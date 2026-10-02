package com.causalops.api.incident;

import com.causalops.api.engine.AiEngineClient;
import com.causalops.api.events.EventBus;
import com.causalops.api.telemetry.TopologyRepository;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.*;

/**
 * Sends an incident's measured telemetry window and discovered topology to the AI engine for
 * root-cause analysis, and stores the result. Triggered explicitly (POST); reading the stored
 * analysis never re-runs it.
 */
@Service
public class IncidentAnalysisService {

    private final JdbcTemplate db;
    private final IncidentRepository incidents;
    private final TopologyRepository topology;
    private final AiEngineClient engine;
    private final EventBus events;
    private final ObjectMapper json;

    public IncidentAnalysisService(JdbcTemplate db, IncidentRepository incidents, TopologyRepository topology,
                                   AiEngineClient engine, EventBus events, ObjectMapper json) {
        this.db = db;
        this.incidents = incidents;
        this.topology = topology;
        this.engine = engine;
        this.events = events;
        this.json = json;
    }

    public Map<String, Object> analyze(UUID incidentId, String mode, int lookbackMinutes) {
        Map<String, Object> incident = incidents.get(incidentId);
        UUID env = (UUID) incident.get("environmentId");
        if (env == null) throw new IllegalArgumentException("Incident " + incidentId + " has no environment");
        Instant opened = ((Timestamp) incident.get("openedAt")).toInstant();
        Instant from = opened.minusSeconds(lookbackMinutes * 60L);

        List<Map<String, Object>> telemetry = db.queryForList("""
                SELECT service_name AS service, captured_at AS timestamp, p50_latency AS "p50Latency",
                       p95_latency AS "p95Latency", p99_latency AS "p99Latency", p99_latency AS latency,
                       error_rate AS "errorRate", request_rate AS "requestRate", pool_utilization AS "poolUtilization",
                       db_latency AS "dbLatency", anomaly_score AS "anomalyScore", anomaly_score AS anomaly
                FROM telemetry_snapshots
                WHERE environment_id = ? AND captured_at >= ?
                ORDER BY captured_at
                """, env, Timestamp.from(from));
        if (telemetry.isEmpty()) throw new IllegalStateException("No telemetry stored for this incident's window");

        List<Map<String, Object>> edges = topology.edges(env, Integer.MAX_VALUE).stream()
                .map(e -> Map.<String, Object>of("source", e.get("source"), "target", e.get("target")))
                .toList();
        Map<String, Object> payload = Map.of(
                "topology", Map.of("edges", edges),
                "telemetry", telemetry,
                "mode", mode == null || mode.isBlank() ? "classical_ml" : mode);

        Map<String, Object> result = engine.post("/analyze/root-cause", payload);
        Object rawCandidates = result.get("candidates");
        if (!(rawCandidates instanceof List<?> candidates) || candidates.isEmpty()) {
            throw new IllegalStateException("AI engine returned no root-cause candidates");
        }
        @SuppressWarnings("unchecked")
        Map<String, Object> top = (Map<String, Object>) candidates.get(0);
        UUID analysisId = db.queryForObject("""
                INSERT INTO root_cause_analyses (incident_id, methodology, root_cause, confidence, evidence)
                VALUES (?, ?, ?, ?, CAST(? AS jsonb)) RETURNING id
                """, UUID.class, incidentId, String.valueOf(result.get("methodology")), top.get("service"),
                ((Number) top.getOrDefault("confidence", 0)).doubleValue(), write(result.getOrDefault("evidence", List.of())));
        for (Object o : candidates) {
            @SuppressWarnings("unchecked")
            Map<String, Object> c = (Map<String, Object>) o;
            db.update("INSERT INTO root_cause_candidates (analysis_id, service_name, score, signals) VALUES (?, ?, ?, CAST(? AS jsonb))",
                    analysisId, c.get("service"), ((Number) c.getOrDefault("score", 0)).doubleValue(),
                    write(c.getOrDefault("signals", Map.of())));
        }
        incidents.addEvent(incidentId, "RCA_COMPLETED", Map.of("rootCause", String.valueOf(top.get("service")),
                "methodology", String.valueOf(result.get("methodology"))));
        events.emit("rca.completed", incidentId, Map.of("rootCause", String.valueOf(top.get("service"))));
        return stored(incidentId);
    }

    public Map<String, Object> stored(UUID incidentId) {
        var analyses = db.queryForList("""
                SELECT id, incident_id AS "incidentId", completed_at AS "completedAt", methodology,
                       root_cause AS "rootCause", confidence, evidence::text AS evidence
                FROM root_cause_analyses WHERE incident_id = ? ORDER BY completed_at DESC LIMIT 1
                """, incidentId);
        if (analyses.isEmpty()) throw new NoSuchElementException("No root-cause analysis has been run for incident " + incidentId);
        var candidates = db.queryForList("""
                SELECT service_name AS service, score, signals::text AS signals
                FROM root_cause_candidates WHERE analysis_id = ? ORDER BY score DESC
                """, analyses.get(0).get("id"));
        return Map.of("analysis", analyses.get(0), "candidates", candidates);
    }

    private String write(Object o) {
        try {
            return json.writeValueAsString(o);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("Cannot serialize analysis data", e);
        }
    }
}

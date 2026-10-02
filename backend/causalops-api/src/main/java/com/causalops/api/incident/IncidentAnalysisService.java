package com.causalops.api.incident;

import com.causalops.api.engine.AiEngineClient;
import com.causalops.api.environment.Environment;
import com.causalops.api.environment.EnvironmentService;
import com.causalops.api.events.EventBus;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.*;

/**
 * Asks the AI engine to analyse an incident's telemetry window (it reads the stored, measured
 * data itself) and stores the ranked root-cause candidates and the counterfactual it returns.
 * Triggered automatically once an incident has propagated, or explicitly via POST; reading the
 * stored analysis never re-runs it.
 */
@Service
public class IncidentAnalysisService {

    private static final Set<String> CALIBRATED = Set.of("CALIBRATED", "ACTIVE");

    private final JdbcTemplate db;
    private final IncidentRepository incidents;
    private final EnvironmentService environments;
    private final AiEngineClient engine;
    private final EventBus events;
    private final ObjectMapper json;
    private final ApplicationEventPublisher publisher;

    public IncidentAnalysisService(JdbcTemplate db, IncidentRepository incidents, EnvironmentService environments,
                                   AiEngineClient engine, EventBus events, ObjectMapper json,
                                   ApplicationEventPublisher publisher) {
        this.publisher = publisher;
        this.db = db;
        this.incidents = incidents;
        this.environments = environments;
        this.engine = engine;
        this.events = events;
        this.json = json;
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> analyze(UUID incidentId, Integer lookbackMinutes) {
        Map<String, Object> incident = incidents.get(incidentId);
        UUID envId = (UUID) incident.get("environmentId");
        if (envId == null) throw new IllegalArgumentException("Incident " + incidentId + " has no environment");
        Environment env = environments.get(envId);
        if (!CALIBRATED.contains(env.status())) {
            throw new IllegalStateException("Environment '" + env.name() + "' is " + env.status()
                    + "; root-cause analysis needs calibrated models");
        }
        Instant opened = ((Timestamp) incident.get("openedAt")).toInstant();
        Instant resolved = incident.get("resolvedAt") == null ? null : ((Timestamp) incident.get("resolvedAt")).toInstant();
        int lookback = lookbackMinutes == null ? 5 : Math.min(Math.max(lookbackMinutes, 1), 60);
        Instant start = opened.minusSeconds(lookback * 60L);
        Instant end = resolved != null ? resolved : Instant.now();

        Map<String, Object> result = engine.post("/pipeline/rca", Map.of(
                "environment_id", envId.toString(), "incident_id", incidentId.toString(),
                "start", start.toString(), "end", end.toString(), "onset", opened.toString()));

        List<Map<String, Object>> candidates = (List<Map<String, Object>>) result.getOrDefault("candidates", List.of());
        if (candidates.isEmpty()) throw new IllegalStateException("AI engine found no anomalous component in the incident window");
        Map<String, Object> top = candidates.get(0);
        String model = String.valueOf(result.get("model_version"));

        UUID analysisId = db.queryForObject("""
                INSERT INTO root_cause_analyses (incident_id, methodology, root_cause, confidence, evidence,
                                                 model_version, candidate_kind)
                VALUES (?, ?, ?, ?, CAST(? AS jsonb), ?, ?) RETURNING id
                """, UUID.class, incidentId, String.valueOf(result.get("methodology")), String.valueOf(top.get("service")),
                ((Number) top.getOrDefault("confidence", 0)).doubleValue(), write(result.getOrDefault("evidence", List.of())),
                model, String.valueOf(top.getOrDefault("kind", "service")));
        for (Map<String, Object> c : candidates) {
            db.update("INSERT INTO root_cause_candidates (analysis_id, service_name, score, signals) VALUES (?, ?, ?, CAST(? AS jsonb))",
                    analysisId, String.valueOf(c.get("service")), ((Number) c.getOrDefault("score", 0)).doubleValue(), write(c));
        }

        Object counterfactual = result.get("counterfactual");
        if (counterfactual instanceof Map<?, ?> cf) {
            db.update("""
                    INSERT INTO simulations (target, intervention, status, environment_id, incident_id, model_version, result)
                    VALUES (?, CAST(? AS jsonb), 'COMPLETED', ?, ?, ?, CAST(? AS jsonb))
                    """, String.valueOf(top.get("service")), write(cf.get("intervention")), envId, incidentId, model, write(cf));
        }

        // Remediation statuses (REMEDIATING, AWAITING_APPROVAL, ...) are not overwritten by a re-analysis.
        if (Set.of("DETECTED", "RCA_IDENTIFIED").contains(String.valueOf(incident.get("status")))) {
            incidents.setStatus(incidentId, "RCA_IDENTIFIED");
        }
        incidents.addEvent(incidentId, "RCA_COMPLETED", Map.of("rootCause", String.valueOf(top.get("service")),
                "kind", String.valueOf(top.getOrDefault("kind", "service")), "modelVersion", model));
        events.emit("rca.completed", incidentId, Map.of("rootCause", String.valueOf(top.get("service"))));
        if (!"RESOLVED".equals(incident.get("status"))) publisher.publishEvent(new RootCauseIdentified(incidentId, analysisId));
        return stored(incidentId);
    }

    public Map<String, Object> stored(UUID incidentId) {
        var analyses = db.queryForList("""
                SELECT id, incident_id AS "incidentId", completed_at AS "completedAt", methodology,
                       root_cause AS "rootCause", candidate_kind AS "rootCauseKind", confidence,
                       evidence::text AS evidence, model_version AS "modelVersion"
                FROM root_cause_analyses WHERE incident_id = ? ORDER BY completed_at DESC LIMIT 1
                """, incidentId);
        if (analyses.isEmpty()) throw new NoSuchElementException("No root-cause analysis has been run for incident " + incidentId);
        var candidates = db.queryForList("""
                SELECT service_name AS service, score, signals::text AS detail
                FROM root_cause_candidates WHERE analysis_id = ? ORDER BY score DESC
                """, analyses.get(0).get("id"));
        var simulation = db.queryForList("""
                SELECT id, created_at AS "createdAt", model_version AS "modelVersion", result::text AS result
                FROM simulations WHERE incident_id = ? ORDER BY created_at DESC LIMIT 1
                """, incidentId);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("analysis", analyses.get(0));
        out.put("candidates", candidates);
        out.put("counterfactual", simulation.isEmpty() ? null : simulation.get(0));
        return out;
    }

    private String write(Object o) {
        try {
            return json.writeValueAsString(o);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("Cannot serialize analysis data", e);
        }
    }
}

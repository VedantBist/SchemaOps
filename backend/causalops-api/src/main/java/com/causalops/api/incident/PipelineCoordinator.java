package com.causalops.api.incident;

import com.causalops.api.engine.AiEngineClient;
import com.causalops.api.environment.Environment;
import com.causalops.api.telemetry.TelemetryRepository;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Runs the calibrated AI pipeline for an environment after each ingestion cycle and stores
 * what it returns: per-service anomaly scores on the snapshots, failure forecasts as predictions,
 * and gate verdicts for the incident detector. Also starts root-cause analysis for incidents
 * once they have had time to propagate. Environments that are still learning are skipped.
 */
@Component
public class PipelineCoordinator {

    private static final Logger log = LoggerFactory.getLogger(PipelineCoordinator.class);
    private static final Set<String> EVALUATED = Set.of("CALIBRATED", "ACTIVE");
    private static final Duration PREDICTION_INTERVAL = Duration.ofMinutes(1);

    private final AiEngineClient engine;
    private final TelemetryRepository telemetry;
    private final IncidentRepository incidents;
    private final IncidentAnalysisService analysis;
    private final JdbcTemplate db;
    private final ObjectMapper json;
    private final ExecutorService rcaWorkers = Executors.newFixedThreadPool(2);
    private final Set<UUID> rcaInFlight = ConcurrentHashMap.newKeySet();
    /** Last failed RCA attempt per incident; retried at most once a minute. */
    private final Map<UUID, Instant> rcaFailedAt = new ConcurrentHashMap<>();
    private static final Duration RCA_RETRY = Duration.ofMinutes(1);

    /** Last stored prediction per (environment, service, horizon): when and at which risk level. */
    private final Map<String, Object[]> lastPrediction = new ConcurrentHashMap<>();

    public PipelineCoordinator(AiEngineClient engine, TelemetryRepository telemetry, IncidentRepository incidents,
                               IncidentAnalysisService analysis, JdbcTemplate db, ObjectMapper json) {
        this.engine = engine;
        this.telemetry = telemetry;
        this.incidents = incidents;
        this.analysis = analysis;
        this.db = db;
        this.json = json;
    }

    /** Evaluates the latest window; returns per-service gate verdicts (empty while learning or on failure). */
    @SuppressWarnings("unchecked")
    public Map<String, AnomalySignal> evaluate(Environment env, Instant at) {
        if (!EVALUATED.contains(env.status())) return Map.of();
        Map<String, Object> result;
        try {
            result = engine.post("/pipeline/evaluate", Map.of("environment_id", env.id().toString(), "at", at.toString()));
        } catch (AiEngineClient.EngineException e) {
            log.warn("Pipeline evaluation failed for {}: {}", env.name(), e.getMessage());
            return Map.of();
        }
        String model = String.valueOf(result.get("model_version"));

        Map<String, AnomalySignal> out = new LinkedHashMap<>();
        Map<String, Object> services = (Map<String, Object>) result.getOrDefault("services", Map.of());
        services.forEach((name, raw) -> {
            Map<String, Object> s = (Map<String, Object>) raw;
            double anomaly = ((Number) s.getOrDefault("anomaly", 0)).doubleValue();
            boolean flagged = Boolean.TRUE.equals(s.get("flagged"));
            List<Map<String, Object>> signals = (List<Map<String, Object>>) s.getOrDefault("signals", List.of());
            telemetry.setAnomalyScore(env.id(), name, at, anomaly);
            out.put(name, new AnomalySignal(name, anomaly, flagged, signals));
        });

        for (Object raw : (List<Object>) result.getOrDefault("forecasts", List.of())) {
            storePrediction(env, model, (Map<String, Object>) raw, at);
        }
        return out;
    }

    /** Stores a forecast at most once a minute per service and horizon, or immediately when its risk level changes. */
    private void storePrediction(Environment env, String model, Map<String, Object> f, Instant at) {
        String service = String.valueOf(f.get("service"));
        int horizon = ((Number) f.get("horizon_seconds")).intValue();
        String level = String.valueOf(f.get("risk_level"));
        String key = env.id() + "|" + service + "|" + horizon;
        Object[] last = lastPrediction.get(key);
        if (last != null && level.equals(last[1]) && Duration.between((Instant) last[0], at).compareTo(PREDICTION_INTERVAL) < 0) {
            return;
        }
        db.update("""
                INSERT INTO predictions (environment_id, service_name, probability, risk_level, horizon_seconds, factors,
                                         model_version, method, created_at)
                VALUES (?, ?, ?, ?, ?, CAST(? AS jsonb), ?, ?, ?)
                """, env.id(), service, ((Number) f.get("probability")).doubleValue(), level, horizon,
                write(f.getOrDefault("factors", List.of())), model, String.valueOf(f.get("method")), Timestamp.from(at));
        lastPrediction.put(key, new Object[]{at, level});
    }

    /**
     * Starts RCA for open incidents that have had {@code rcaDelaySeconds} to propagate and have
     * no analysis newer than their last change. Runs off the ingestion thread.
     */
    public void scheduleRootCauseAnalysis(Environment env) {
        if (!EVALUATED.contains(env.status())) return;
        int delay = env.config().analysis().rcaDelaySeconds();
        List<Map<String, Object>> due = db.queryForList("""
                SELECT i.id FROM incidents i
                WHERE i.environment_id = ? AND i.status <> 'RESOLVED'
                  AND i.updated_at < now() - make_interval(secs => ?)
                  AND NOT EXISTS (SELECT 1 FROM root_cause_analyses r
                                  WHERE r.incident_id = i.id AND r.completed_at >= i.updated_at)
                """, env.id(), delay);
        for (Map<String, Object> row : due) {
            UUID id = (UUID) row.get("id");
            Instant failed = rcaFailedAt.get(id);
            if (failed != null && Duration.between(failed, Instant.now()).compareTo(RCA_RETRY) < 0) continue;
            if (!rcaInFlight.add(id)) continue;
            rcaWorkers.submit(() -> {
                try {
                    analysis.analyze(id, null);
                    rcaFailedAt.remove(id);
                } catch (RuntimeException e) {
                    rcaFailedAt.put(id, Instant.now());
                    log.warn("Automatic RCA for incident {} failed: {}", id, e.getMessage());
                    incidents.addEvent(id, "RCA_FAILED", Map.of("message", String.valueOf(e.getMessage())));
                } finally {
                    rcaInFlight.remove(id);
                }
            });
        }
    }

    private String write(Object o) {
        try {
            return json.writeValueAsString(o);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("Cannot serialize prediction factors", e);
        }
    }
}

package com.causalops.api.calibration;

import com.causalops.api.engine.AiEngineClient;
import com.causalops.api.environment.Environment;
import com.causalops.api.environment.EnvironmentRepository;
import com.causalops.api.environment.EnvironmentService;
import com.causalops.api.events.EventBus;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.*;

/**
 * Drives each environment through LEARNING -> CALIBRATED/ACTIVE and keeps it current:
 * starts the initial calibration once the learning window has elapsed, applies finished runs
 * (written by the AI engine) to the environment's lifecycle, and starts a retrain when the
 * last calibration is older than the retrain interval.
 */
@Service
public class CalibrationService {

    private static final Logger log = LoggerFactory.getLogger(CalibrationService.class);
    private static final Duration FAILED_RETRY = Duration.ofHours(1);

    private final EnvironmentService environments;
    private final EnvironmentRepository environmentRepository;
    private final AiEngineClient engine;
    private final JdbcTemplate db;
    private final EventBus events;

    public CalibrationService(EnvironmentService environments, EnvironmentRepository environmentRepository,
                              AiEngineClient engine, JdbcTemplate db, EventBus events) {
        this.environments = environments;
        this.environmentRepository = environmentRepository;
        this.engine = engine;
        this.db = db;
        this.events = events;
    }

    @Scheduled(initialDelayString = "${causalops.calibration.initial-delay-ms:30000}",
               fixedDelayString = "${causalops.calibration.check-interval-ms:60000}")
    public void tick() {
        for (Environment env : environments.monitored()) {
            try {
                applyFinishedRuns(env);
                Environment current = environments.get(env.id());
                if (running(current.id()).isPresent() || recentlyFailed(current.id())) continue;
                var cal = current.config().calibration();
                if ("LEARNING".equals(current.status()) && dataMinutes(current.id()) >= cal.learningWindowHours() * 60.0) {
                    start(current, "INITIAL", "learning_window_complete");
                } else if (Set.of("CALIBRATED", "ACTIVE").contains(current.status()) && current.calibratedAt() != null
                        && current.calibratedAt().isBefore(Instant.now().minus(Duration.ofDays(cal.retrainIntervalDays())))) {
                    start(current, "RETRAIN", "scheduled_retrain");
                }
            } catch (RuntimeException e) {
                log.warn("Calibration check failed for {}: {}", env.name(), e.getMessage());
            }
        }
    }

    /** Operator-initiated calibration; allowed once minLearningMinutes of telemetry exist. */
    public Map<String, Object> runNow(UUID environmentId) {
        Environment env = environments.resolve(environmentId);
        if (running(env.id()).isPresent()) throw new IllegalStateException("A calibration run is already in progress");
        double minutes = dataMinutes(env.id());
        int required = env.config().calibration().minLearningMinutes();
        if (minutes < required) {
            throw new IllegalStateException("Calibration needs at least %d minutes of telemetry; %.0f minutes are stored"
                    .formatted(required, minutes));
        }
        return start(env, "LEARNING".equals(env.status()) ? "INITIAL" : "MANUAL", "operator");
    }

    private Map<String, Object> start(Environment env, String mode, String trigger) {
        Map<String, Object> r = engine.post("/calibration/run",
                Map.of("environment_id", env.id().toString(), "mode", mode, "trigger", trigger));
        log.info("Started {} calibration of {} ({}): run {}", mode, env.name(), trigger, r.get("run_id"));
        events.emit("calibration.started", env.id(), Map.of("mode", mode, "trigger", trigger, "runId", String.valueOf(r.get("run_id"))));
        return r;
    }

    /** Moves the environment forward once the engine has finished a run. Idempotent. */
    void applyFinishedRuns(Environment env) {
        List<Map<String, Object>> runs = db.queryForList("""
                SELECT id, status, mode, finished_at, promoted, quality_passed, decision, error, model_version
                FROM calibration_runs
                WHERE environment_id = ? AND status <> 'RUNNING' AND finished_at > COALESCE(CAST(? AS timestamptz), '-infinity'::timestamptz)
                ORDER BY finished_at
                """, env.id(), env.calibratedAt() == null ? null : Timestamp.from(env.calibratedAt()));
        for (Map<String, Object> run : runs) {
            Timestamp finished = (Timestamp) run.get("finished_at");
            if ("FAILED".equals(run.get("status"))) {
                String reason = "Calibration failed: " + run.get("error");
                if (!reason.equals(env.statusReason())) {
                    environmentRepository.updateLifecycle(env.id(), env.status(), null, reason);
                    events.emit("calibration.failed", env.id(), Map.of("reason", reason));
                }
                continue;
            }
            String status = env.status();
            // quality_passed describes the model serving after the run (new or kept), judged on the run's data.
            if (Boolean.TRUE.equals(run.get("promoted")) || !"LEARNING".equals(status)) {
                status = Boolean.TRUE.equals(run.get("quality_passed")) ? "ACTIVE" : "CALIBRATED";
            } else {
                status = "CALIBRATED";  // first model is always kept; quality gates decide ACTIVE
            }
            String reason = String.valueOf(run.get("decision"));
            environmentRepository.updateLifecycle(env.id(), status, finished, reason);
            log.info("Environment {} is {} after {} calibration (model {}): {}", env.name(), status, run.get("mode"),
                    run.get("model_version"), reason);
            events.emit("calibration.completed", env.id(), Map.of("status", status, "modelVersion",
                    String.valueOf(run.get("model_version")), "decision", reason));
            env = environments.get(env.id());
        }
    }

    public Map<String, Object> status(UUID environmentId) {
        Environment env = environments.resolve(environmentId);
        var cal = env.config().calibration();
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("environment", Map.of("id", env.id(), "name", env.name(), "status", env.status()));
        out.put("statusReason", env.statusReason());
        double minutes = dataMinutes(env.id());
        Map<String, Object> learning = new LinkedHashMap<>();
        learning.put("dataMinutes", Math.round(minutes * 10) / 10.0);
        learning.put("learningWindowMinutes", cal.learningWindowHours() * 60);
        learning.put("minLearningMinutes", cal.minLearningMinutes());
        learning.put("progressPct", Math.min(100.0, Math.round(1000.0 * minutes / (cal.learningWindowHours() * 60.0)) / 10.0));
        learning.put("canCalibrateNow", minutes >= cal.minLearningMinutes());
        out.put("learning", learning);
        out.put("calibratedAt", env.calibratedAt() == null ? null : env.calibratedAt().toString());
        out.put("nextRetrainDue", env.calibratedAt() == null ? null
                : env.calibratedAt().plus(Duration.ofDays(cal.retrainIntervalDays())).toString());
        out.put("runs", runs(env.id(), 10));
        out.put("champion", db.queryForList("""
                SELECT version, created_at AS "createdAt", promoted_at AS "promotedAt", data_from AS "dataFrom",
                       data_to AS "dataTo", metrics::text AS metrics
                FROM model_registry WHERE environment_id = ? AND status = 'CHAMPION'
                """, env.id()).stream().findFirst().orElse(null));
        return out;
    }

    public List<Map<String, Object>> runs(UUID env, int limit) {
        return db.queryForList("""
                SELECT id, mode, trigger, status, started_at AS "startedAt", finished_at AS "finishedAt",
                       data_from AS "dataFrom", data_to AS "dataTo", model_version AS "modelVersion", promoted,
                       quality_passed AS "qualityPassed", decision, error, metrics::text AS metrics
                FROM calibration_runs WHERE environment_id = ? ORDER BY started_at DESC LIMIT ?
                """, env, limit);
    }

    public List<Map<String, Object>> models(UUID environmentId) {
        UUID env = environments.resolve(environmentId).id();
        return db.queryForList("""
                SELECT version, status, created_at AS "createdAt", promoted_at AS "promotedAt", retired_at AS "retiredAt",
                       data_from AS "dataFrom", data_to AS "dataTo", checksum, metrics::text AS metrics,
                       components::text AS components
                FROM model_registry WHERE environment_id = ? ORDER BY created_at DESC
                """, env);
    }

    private Optional<Map<String, Object>> running(UUID env) {
        return db.queryForList("SELECT id FROM calibration_runs WHERE environment_id = ? AND status = 'RUNNING'", env)
                .stream().findFirst();
    }

    private boolean recentlyFailed(UUID env) {
        return db.queryForList("""
                SELECT status, finished_at FROM calibration_runs WHERE environment_id = ?
                ORDER BY started_at DESC LIMIT 1
                """, env).stream().anyMatch(r -> "FAILED".equals(r.get("status")) && r.get("finished_at") != null
                && ((Timestamp) r.get("finished_at")).toInstant().isAfter(Instant.now().minus(FAILED_RETRY)));
    }

    /** Minutes of telemetry recorded since learning (re)started, i.e. under the current metric definitions. */
    double dataMinutes(UUID env) {
        Map<String, Object> span = db.queryForMap(
                """
                SELECT min(t.captured_at) AS first, max(t.captured_at) AS last
                FROM telemetry_snapshots t JOIN environments e ON e.id = t.environment_id
                WHERE t.environment_id = ? AND t.captured_at >= e.learning_started_at
                """, env);
        if (span.get("first") == null) return 0;
        return Duration.between(((Timestamp) span.get("first")).toInstant(),
                ((Timestamp) span.get("last")).toInstant()).toSeconds() / 60.0;
    }
}

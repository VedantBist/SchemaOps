package com.causalops.api.system;

import com.causalops.api.engine.AiEngineClient;
import com.causalops.api.environment.Environment;
import com.causalops.api.environment.EnvironmentService;
import com.causalops.api.telemetry.PrometheusClient;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/** Live health of every component the platform depends on, checked on each request. */
@RestController
@RequestMapping("/api/system")
public class SystemStatusController {

    private final JdbcTemplate db;
    private final AiEngineClient engine;
    private final PrometheusClient prometheus;
    private final EnvironmentService environments;
    private final RestClient http;
    private final long ingestIntervalMs;

    public SystemStatusController(JdbcTemplate db, AiEngineClient engine, PrometheusClient prometheus,
                                  EnvironmentService environments, RestClient http,
                                  @Value("${causalops.telemetry.ingest-interval-ms:5000}") long ingestIntervalMs) {
        this.db = db;
        this.engine = engine;
        this.prometheus = prometheus;
        this.environments = environments;
        this.http = http;
        this.ingestIntervalMs = ingestIntervalMs;
    }

    @GetMapping("/status")
    public Map<String, Object> status(@RequestParam(required = false) UUID environmentId) {
        Map<String, Object> components = new LinkedHashMap<>();
        components.put("api", "UP");
        components.put("database", check(() -> db.queryForObject("SELECT 1", Integer.class)));
        components.put("aiEngine", engine.ready() ? "UP" : "DOWN");

        Map<String, Object> out = new LinkedHashMap<>();
        try {
            Environment env = environments.resolve(environmentId);
            var e = env.config().endpoints();
            components.put("prometheus", prometheus.ready(e.prometheusUrl()) ? "UP" : "DOWN");
            components.put("tempo", ping(e.tempoUrl(), "/ready"));
            components.put("loki", ping(e.lokiUrl(), "/ready"));
            components.put("collector", ping(e.collectorHealthUrl(), "/"));

            Timestamp last = db.queryForObject("SELECT max(captured_at) FROM telemetry_snapshots WHERE environment_id = ?",
                    Timestamp.class, env.id());
            Map<String, Object> ingestion = new LinkedHashMap<>();
            ingestion.put("lastSampleAt", last == null ? null : last.toInstant().toString());
            long ageMs = last == null ? -1 : Duration.between(last.toInstant(), Instant.now()).toMillis();
            ingestion.put("ageSeconds", ageMs < 0 ? null : ageMs / 1000.0);
            ingestion.put("status", last == null ? "NO_DATA" : ageMs <= 3 * ingestIntervalMs ? "FRESH" : "STALE");
            components.put("telemetryIngestion", ingestion.get("status").equals("FRESH") ? "UP" : "DOWN");

            Integer nodes = db.queryForObject("SELECT count(*) FROM services WHERE environment_id = ?", Integer.class, env.id());
            Integer edges = db.queryForObject("SELECT count(*) FROM dependencies WHERE environment_id = ?", Integer.class, env.id());
            out.put("environment", Map.of("id", env.id(), "name", env.name(), "status", env.status()));
            out.put("ingestion", ingestion);
            out.put("topology", Map.of("nodes", nodes, "edges", edges));
        } catch (java.util.NoSuchElementException noEnv) {
            out.put("environment", null);
        }

        long down = components.values().stream().filter("DOWN"::equals).count();
        out.put("status", down == 0 ? "UP" : "DOWN".equals(components.get("database")) ? "DOWN" : "DEGRADED");
        out.put("components", components);
        out.put("checkedAt", Instant.now().toString());
        return out;
    }

    private String ping(String base, String path) {
        if (base == null || base.isBlank()) return "NOT_CONFIGURED";
        try {
            http.get().uri(base.replaceAll("/+$", "") + path).retrieve().toBodilessEntity();
            return "UP";
        } catch (RestClientException e) {
            return "DOWN";
        }
    }

    private static String check(Runnable r) {
        try {
            r.run();
            return "UP";
        } catch (RuntimeException e) {
            return "DOWN";
        }
    }
}

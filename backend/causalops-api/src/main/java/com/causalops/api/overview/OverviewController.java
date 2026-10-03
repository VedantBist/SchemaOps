package com.causalops.api.overview;

import com.causalops.api.environment.Environment;
import com.causalops.api.environment.EnvironmentService;
import com.causalops.api.incident.IncidentRepository;
import com.causalops.api.telemetry.TelemetryRepository;
import com.causalops.api.telemetry.TopologyRepository;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.*;

import java.time.Duration;
import java.time.Instant;
import java.util.*;

/** Read models over measured telemetry and discovered topology. */
@RestController
@RequestMapping("/api")
public class OverviewController {

    private final EnvironmentService environments;
    private final TopologyRepository topology;
    private final TelemetryRepository telemetry;
    private final IncidentRepository incidents;
    private final JdbcTemplate db;

    public OverviewController(EnvironmentService environments, TopologyRepository topology, TelemetryRepository telemetry,
                              IncidentRepository incidents, JdbcTemplate db) {
        this.environments = environments;
        this.topology = topology;
        this.telemetry = telemetry;
        this.incidents = incidents;
        this.db = db;
    }

    @GetMapping("/overview")
    public Map<String, Object> overview(@RequestParam(required = false) UUID environmentId) {
        Environment env = environments.resolve(environmentId);
        List<Map<String, Object>> services = services(env);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("environment", Map.of("id", env.id(), "name", env.name(), "status", env.status()));
        out.put("systemStatus", systemStatus(services));
        out.put("services", services);
        out.put("activeIncidents", incidents.list(env.id(), IncidentRepository.Filter.ACTIVE, 50));
        out.put("topology", topology(env.id()));
        return out;
    }

    @GetMapping("/services")
    public List<Map<String, Object>> services(@RequestParam(required = false) UUID environmentId) {
        return services(environments.resolve(environmentId));
    }

    @GetMapping("/services/{name}")
    public Map<String, Object> service(@PathVariable String name, @RequestParam(required = false) UUID environmentId) {
        return services(environments.resolve(environmentId)).stream()
                .filter(s -> name.equals(s.get("name")) || name.equals(String.valueOf(s.get("id"))))
                .findFirst().orElseThrow(() -> new NoSuchElementException("Unknown service " + name));
    }

    @GetMapping("/topology")
    public Map<String, Object> topology(@RequestParam(required = false) UUID environmentId) {
        Environment env = environments.resolve(environmentId);
        return Map.of("nodes", services(env), "edges", topology.edges(env.id(), env.config().telemetry().staleAfterSeconds()));
    }

    /** Stored, measured samples (newest first). */
    @GetMapping("/metrics")
    public Map<String, Object> metrics(@RequestParam(required = false) UUID environmentId,
                                       @RequestParam(required = false) String service,
                                       @RequestParam(defaultValue = "15") int minutes,
                                       @RequestParam(defaultValue = "2000") int limit) {
        Environment env = environments.resolve(environmentId);
        Instant since = Instant.now().minus(Duration.ofMinutes(Math.min(Math.max(minutes, 1), 24 * 60)));
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("service", service);
        out.put("since", since.toString());
        out.put("samples", telemetry.series(env.id(), service, since, Math.min(Math.max(limit, 1), 20_000)));
        return out;
    }

    @GetMapping("/predictions")
    public List<Map<String, Object>> predictions(@RequestParam(required = false) UUID environmentId,
                                                 @RequestParam(required = false) String service) {
        UUID env = environments.resolve(environmentId).id();
        String sql = """
                SELECT id, service_name AS service, probability, risk_level AS "riskLevel", horizon_seconds AS "horizonSeconds",
                       factors::text AS factors, method, model_version AS "modelVersion", created_at AS "createdAt"
                FROM predictions WHERE environment_id = ? %s ORDER BY created_at DESC LIMIT 500
                """;
        return service == null ? db.queryForList(sql.formatted(""), env)
                : db.queryForList(sql.formatted("AND service_name = ?"), env, service);
    }

    @GetMapping("/predictions/{service}")
    public List<Map<String, Object>> predictionsFor(@PathVariable String service, @RequestParam(required = false) UUID environmentId) {
        return predictions(environmentId, service);
    }

    @GetMapping("/simulations/{id}")
    public Map<String, Object> simulation(@PathVariable UUID id) {
        var rows = db.queryForList("""
                SELECT id, target, intervention::text AS intervention, status, created_at AS "createdAt"
                FROM simulations WHERE id = ?
                """, id);
        if (rows.isEmpty()) throw new NoSuchElementException("Unknown simulation " + id);
        Map<String, Object> out = new LinkedHashMap<>(rows.get(0));
        out.put("results", db.queryForList("""
                SELECT service_name AS service, baseline, counterfactual, classification
                FROM simulation_results WHERE simulation_id = ?
                """, id));
        return out;
    }

    private List<Map<String, Object>> services(Environment env) {
        List<Map<String, Object>> rows = topology.services(env.id(), env.config().telemetry().staleAfterSeconds());
        for (Map<String, Object> s : rows) {
            s.put("displayName", s.get("name"));
            Object rps = s.get("requestRate");
            s.put("throughput", rps == null ? null : String.format(Locale.ROOT, "%.2f req/s", ((Number) rps).doubleValue()));
        }
        return rows;
    }

    static String systemStatus(List<Map<String, Object>> services) {
        List<Object> live = services.stream().filter(s -> !Boolean.TRUE.equals(s.get("stale")))
                .map(s -> s.get("status")).filter(st -> !"unknown".equals(st) && !"inactive".equals(st)).toList();
        if (live.isEmpty()) return "No data";
        if (live.contains("critical")) return "Critical";
        if (live.contains("degraded")) return "Degraded";
        return "Operational";
    }
}

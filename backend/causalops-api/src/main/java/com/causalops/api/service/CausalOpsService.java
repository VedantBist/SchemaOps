package com.causalops.api.service;

import com.causalops.api.dto.FaultRequest;
import com.causalops.api.entity.ServiceEntity;
import com.causalops.api.repository.ServiceRepository;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.*;
import org.springframework.beans.factory.annotation.*;
import org.springframework.http.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.sql.Timestamp;
import java.time.*;
import java.util.*;
import java.util.stream.*;

@Service
public class CausalOpsService {

    private static final Logger log = LoggerFactory.getLogger(CausalOpsService.class);

    private final JdbcTemplate db;
    private final ServiceRepository services;
    private final RestClient http;
    private final EventBus events;
    private final ObjectMapper json;
    private final HttpClient javaHttp;

    @Value("${causalops.ai-url}")
    String aiUrl;

    @Value("${causalops.demo-mode}")
    boolean demo;

    @Value("${causalops.service-urls.inventory-service}")
    String inventoryUrl;

    @Value("${causalops.service-urls.order-service}")
    String orderUrl;

    @Value("${causalops.service-urls.payment-service}")
    String paymentUrl;

    @Value("${causalops.service-urls.api-gateway}")
    String gatewayUrl;

    @Value("${causalops.rca.mode:classical_ml}")
    String defaultRcaMode = "classical_ml";

    public CausalOpsService(JdbcTemplate d, ServiceRepository s, RestClient h, EventBus e, ObjectMapper mapper) {
        db = d;
        services = s;
        http = h;
        events = e;
        json = mapper;
        javaHttp = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .version(java.net.http.HttpClient.Version.HTTP_1_1)
                .build();
    }

    // ─── Serialization helpers ────────────────────────────────────────────────

    private String write(Object o) {
        try {
            return json.writeValueAsString(o);
        } catch (Exception e) {
            throw new IllegalStateException("Unable to serialize CausalOps payload", e);
        }
    }

    private <T> T readList(String raw, Class<T> cls) {
        try {
            return json.readValue(raw, cls);
        } catch (Exception e) {
            throw new IllegalStateException("Unable to deserialize AI response: " + raw.substring(0, Math.min(200, raw.length())), e);
        }
    }

    /**
     * Send a JSON POST to the AI engine using the JDK HttpClient.
     * This bypasses Spring RestClient entirely to guarantee that:
     *  1. The body is the serialized JSON string (non-empty, valid JSON).
     *  2. Content-Type: application/json is always set.
     *  3. Diagnostic details are logged on every call.
     */
    @SuppressWarnings("unchecked")
    private <T> T aiPost(String path, Object payload, Class<T> returnType) {
        String url = aiUrl + path;
        String bodyStr = write(payload);

        log.info("[AI-POST] url={} bodyLength={} preview={}", url, bodyStr.length(),
                bodyStr.length() > 120 ? bodyStr.substring(0, 120) + "..." : bodyStr);

        if (bodyStr == null || bodyStr.isBlank() || bodyStr.equals("null")) {
            throw new IllegalStateException("AI request body is empty for path=" + path);
        }

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(url))
                .timeout(Duration.ofSeconds(30))
                .header("Content-Type", "application/json")
                .header("Accept", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(bodyStr))
                .build();

        try {
            HttpResponse<String> response = javaHttp.send(request, HttpResponse.BodyHandlers.ofString());

            log.info("[AI-POST] url={} status={} responseLength={}", url, response.statusCode(),
                    response.body() == null ? 0 : response.body().length());

            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                log.error("[AI-POST] Non-2xx from AI engine: url={} status={} body={}",
                        url, response.statusCode(),
                        response.body() == null ? "<null>" : response.body().substring(0, Math.min(500, response.body().length())));
                throw new IllegalStateException("AI engine returned HTTP " + response.statusCode() + " for " + path);
            }

            return json.readValue(response.body(), json.getTypeFactory().constructType(returnType));
        } catch (IllegalStateException e) {
            throw e;
        } catch (Exception e) {
            throw new IllegalStateException("AI HTTP call failed for " + path + ": " + e.getMessage(), e);
        }
    }

    // ─── Service mapping ──────────────────────────────────────────────────────

    private Map<String, Object> service(ServiceEntity s) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", s.id);
        m.put("name", s.name);
        m.put("displayName", s.name);
        m.put("type", s.type);
        m.put("status", s.status);
        m.put("latencyP99", s.currentLatency);
        m.put("baselineLatency", s.baselineLatency);
        m.put("errorRate", s.errorRate);
        m.put("throughput", "60 req/min");
        m.put("causalRole", s.status.equals("critical") ? "incident_root"
                : s.status.equals("healthy") ? "unaffected" : "affected");
        return m;
    }

    // ─── Public API surface ───────────────────────────────────────────────────

    public List<Map<String, Object>> allServices() {
        return services.findAll().stream().map(this::service).toList();
    }

    public Map<String, Object> topology() {
        var edges = db.queryForList(
                "select source_service as source, target_service as target, direction from dependencies");
        return Map.of(
                "nodes", allServices(),
                "edges", edges.stream().map(e -> Map.of(
                        "id", e.get("source") + "-" + e.get("target"),
                        "source", e.get("source"),
                        "target", e.get("target"),
                        "direction", e.get("direction"),
                        "type", "observed"
                )).toList()
        );
    }

    public Map<String, Object> overview() {
        var active = incidents("where status <> 'RESOLVED'");
        return Map.of(
                "services", allServices(),
                "activeIncidents", active,
                "topology", topology(),
                "systemStatus", active.isEmpty() ? "Operational" : "Degraded"
        );
    }

    public List<Map<String, Object>> incidents(String where) {
        return db.queryForList(
                "select id, incident_key as \"incidentKey\", title, severity, status, " +
                "opened_at as \"openedAt\", summary, " +
                "affected_services::text as \"affectedServices\" " +
                "from incidents " + where + " order by opened_at desc"
        );
    }

    public Map<String, Object> inject(FaultRequest r) {
        if (services.findByName(r.target()).isEmpty())
            throw new NoSuchElementException("Unknown target " + r.target());
        String p = write(r.parameters() == null ? Map.of() : r.parameters());
        UUID id = UUID.randomUUID();
        db.update(
                "insert into fault_injections(id,type,target,severity,duration_seconds,parameters) values(?,?,?,?,?,cast(? as jsonb))",
                id, r.type(), r.target(), r.severity(), r.durationSeconds(), p);
        try {
            applyLiveFault(r, false);
        } catch (RuntimeException e) {
            db.update("update fault_injections set status='FAILED',stopped_at=now() where id=?", id);
            log.error("Live fault {} could not be applied to {}", id, r.target(), e);
            throw e;
        }
        events.emit("fault.started", id, Map.of("target", r.target(), "type", r.type()));
        return fault(id);
    }

    public List<Map<String, Object>> faults() {
        return db.queryForList(
                "select id, type, target, severity, duration_seconds as \"durationSeconds\", " +
                "parameters::text as parameters, status, started_at as \"startedAt\", stopped_at as \"stoppedAt\" " +
                "from fault_injections order by started_at desc");
    }

    public Map<String, Object> fault(UUID id) {
        return faults().stream()
                .filter(x -> id.toString().equals(String.valueOf(x.get("id"))))
                .findFirst().orElseThrow();
    }

    public void stop(UUID id) {
        var f = fault(id);
        db.update("update fault_injections set status='STOPPED',stopped_at=now() where id=?", id);
        applyLiveFault(
                new FaultRequest("SERVICE_LATENCY", String.valueOf(f.get("target")), "LOW", 1, Map.of("latencyMs", 0)),
                true);
        events.emit("fault.stopped", id, Map.of());
    }

    public void clear() {
        List<Map<String, Object>> active = faults().stream()
                .filter(f -> "ACTIVE".equals(f.get("status"))).toList();
        for (var f : active) {
            applyLiveFault(
                    new FaultRequest(String.valueOf(f.get("type")), String.valueOf(f.get("target")),
                            "LOW", 1, Map.of("latencyMs", 0)),
                    true);
        }
        db.update("update fault_injections set status='STOPPED',stopped_at=now() where status='ACTIVE'");
        events.emit("faults.cleared", "all", Map.of());
    }

    private void applyLiveFault(FaultRequest r, boolean stop) {
        String url = switch (r.target()) {
            case "inventory-db", "inventory-service" -> inventoryUrl;
            case "order-service" -> orderUrl;
            case "payment-service" -> paymentUrl;
            case "api-gateway", "auth-gateway" -> gatewayUrl;
            default -> throw new IllegalArgumentException("No live controller for target " + r.target());
        };
        try {
            Object latencyVal = (r.parameters() != null && r.parameters().containsKey("latencyMs"))
                    ? r.parameters().get("latencyMs") : 800;
            http.post()
                    .uri(url + "/internal/fault")
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(Map.of(
                            "type", r.type(),
                            "latencyMs", stop ? 0 : latencyVal,
                            "failure", !stop && "SERVICE_FAILURE".equals(r.type())
                    ))
                    .retrieve()
                    .toBodilessEntity();
        } catch (Exception e) {
            throw new IllegalStateException("Live fault controller failed for " + r.target() + " at " + url, e);
        }
    }

    // ─── Scheduled telemetry collection ───────────────────────────────────────

    @Scheduled(fixedDelayString = "${telemetry.collection-interval-ms:1000}")
    public void collect() {
        if (!demo) return;

        Instant cycleInstant = Instant.now();
        Timestamp cycleTimestamp = Timestamp.from(cycleInstant);

        var active = faults().stream().filter(f -> "ACTIVE".equals(f.get("status"))).toList();

        for (ServiceEntity s : services.findAll()) {
            double latency = s.baselineLatency;
            double err = 0.1;
            for (var f : active) {
                if (affects((String) f.get("target"), s.name)) {
                    double v = number(f.get("parameters"), "latencyMs", 800);
                    int d = distance((String) f.get("target"), s.name);
                    if (((String) f.get("type")).contains("LATENCY")) latency += v * Math.pow(.62, d);
                    if ("SERVICE_FAILURE".equals(f.get("type"))) err += 35 * Math.pow(.7, d);
                    if ("ERROR_RATE".equals(f.get("type"))) err += number(f.get("parameters"), "errorRate", 20) * Math.pow(.7, d);
                }
            }
            s.currentLatency = Math.round(latency);
            s.errorRate = Math.min(100, err);
            s.status = latency > s.baselineLatency * 8 || err > 25 ? "critical"
                    : latency > s.baselineLatency * 2 || err > 5 ? "degraded" : "healthy";
            services.save(s);

            double anomaly = Math.min(1, Math.max(0, (latency / s.baselineLatency - 1) / 4 + (err > 1 ? err / 50.0 : 0)));
            db.update(
                    "insert into telemetry_snapshots(service_name,captured_at,p50_latency,p95_latency,p99_latency," +
                    "error_rate,request_rate,db_latency,pool_utilization,anomaly_score) values(?,?,?,?,?,?,?,?,?,?)",
                    s.name, cycleTimestamp, latency * .6, latency * .85, latency, err, 60,
                    s.name.equals("inventory-db") ? latency : null,
                    Math.min(100, 25 + anomaly * 70), anomaly);
        }

        if (!active.isEmpty()) detectAndAnalyze();
        else resolveRecoveredIncidents();

        events.emit("telemetry.updated", "system", allServices());
    }

    private void resolveRecoveredIncidents() {
        boolean healthy = services.findAll().stream().allMatch(s -> "healthy".equals(s.status));
        if (healthy) {
            int count = db.update(
                    "update incidents set status='RESOLVED',resolved_at=now() " +
                    "where status in ('DETECTED','INVESTIGATING','RCA_IDENTIFIED','ANALYSIS_FAILED')");
            if (count > 0) events.emit("incident.resolved", "recovery", Map.of("count", count));
        }
    }

    private boolean affects(String root, String target) {
        return distance(root, target) < 20;
    }

    private int distance(String root, String target) {
        if (root.equals(target)) return 0;
        if ("auth-gateway".equals(root)) {
            if ("api-gateway".equals(target)) return 1;
            return 20;
        }
        if ("payment-service".equals(root)) {
            if ("order-service".equals(target)) return 1;
            if ("api-gateway".equals(target)) return 2;
            return 20;
        }
        List<String> path = List.of("inventory-db", "inventory-service", "order-service", "api-gateway");
        int a = path.indexOf(root), b = path.indexOf(target);
        return a >= 0 && b >= a ? b - a : 20;
    }

    private double number(Object raw, String key, double fallback) {
        try {
            Map<String, Object> m = json.readValue(String.valueOf(raw), new TypeReference<>() {});
            return ((Number) m.getOrDefault(key, fallback)).doubleValue();
        } catch (Exception e) {
            return fallback;
        }
    }

    // ─── Incident detection & AI analysis ────────────────────────────────────

    private void detectAndAnalyze() {
        // Only open one incident at a time per active fault episode
        if (!incidents("where status <> 'RESOLVED'").isEmpty()) return;

        var hot = services.findAll().stream()
                .filter(s -> s.currentLatency > s.baselineLatency * 2 || s.errorRate > 5)
                .toList();
        if (hot.size() < 2) return;

        String title = "Service Degradation Cascade";
        var activeFaults = faults().stream().filter(f -> "ACTIVE".equals(f.get("status"))).toList();
        if (!activeFaults.isEmpty()) {
            String target = String.valueOf(activeFaults.get(0).get("target"));
            String type = String.valueOf(activeFaults.get(0).get("type"));
            if ("inventory-db".equals(target)) title = "Database Latency Cascade";
            else if ("payment-service".equals(target)) title = "Payment Service Failure";
            else if ("order-service".equals(target)) title = "Order Service Latency Cascade";
            else if ("auth-gateway".equals(target)) title = "Auth Gateway Token Validation Failure";
            else title = target + " " + type.replace('_', ' ');
        }

        UUID id = UUID.randomUUID();
        db.update(
                "insert into incidents(id,incident_key,title,severity,status,summary,affected_services) " +
                "values(?,?,?,?,'INVESTIGATING',?,cast(? as jsonb))",
                id,
                "INC-" + (8900 + new Random().nextInt(99)),
                title,
                "HIGH",
                "Evidence threshold exceeded across dependent services",
                write(hot.stream().map(s -> s.name).toList()));
        events.emit("incident.created", id, Map.of("title", title));
        analyze(id);
    }

    public void analyze(UUID incidentId) {
        try {
            analyze(incidentId, defaultRcaMode);
        } catch (Exception ignored) {
        }
    }

    public Map<String, Object> analyze(UUID incidentId, String mode) {
        String selectedMode = (mode != null && !mode.isBlank()) ? mode : defaultRcaMode;
        try {
            List<Map<String, Object>> telemetry = db.queryForList(
                    "select service_name as service, " +
                    "p50_latency as \"p50Latency\", p95_latency as \"p95Latency\", " +
                    "p99_latency as \"p99Latency\", p99_latency as latency, " +
                    "error_rate as \"errorRate\", request_rate as \"requestRate\", " +
                    "pool_utilization as \"poolUtilization\", db_latency as \"dbLatency\", " +
                    "anomaly_score as \"anomalyScore\", anomaly_score as anomaly, " +
                    "captured_at as timestamp " +
                    "from telemetry_snapshots " +
                    "where captured_at > now() - interval '2 minutes' " +
                    "order by captured_at");

            log.info("[ANALYZE] incidentId={} mode={} telemetryRows={}", incidentId, selectedMode, telemetry.size());

            Map<String, Object> payload = Map.of(
                    "topology", topology(),
                    "telemetry", telemetry,
                    "mode", selectedMode
            );

            @SuppressWarnings("unchecked")
            Map<String, Object> result = aiPost("/analyze/root-cause", payload, Map.class);

            if (result == null || !(result.get("candidates") instanceof List<?> candidates) || candidates.isEmpty()) {
                throw new IllegalStateException("AI RCA returned no candidates");
            }

            @SuppressWarnings("unchecked")
            Map<String, Object> root = (Map<String, Object>) candidates.getFirst();
            UUID a = UUID.randomUUID();
            db.update(
                    "insert into root_cause_analyses(id,incident_id,methodology,root_cause,confidence,evidence) " +
                    "values(?,?,?,?,?,cast(? as jsonb))",
                    a, incidentId, result.get("methodology"), root.get("service"),
                    root.get("confidence"), write(result.get("evidence")));

            for (Object x : candidates) {
                @SuppressWarnings("unchecked")
                Map<String, Object> c = (Map<String, Object>) x;
                db.update(
                        "insert into root_cause_candidates(analysis_id,service_name,score,signals) " +
                        "values(?,?,?,cast(? as jsonb))",
                        a, c.get("service"), c.get("score"), write(c.get("signals")));
            }

            db.update("update incidents set status='RCA_IDENTIFIED' where id=?", incidentId);
            log.info("[ANALYZE] RCA complete: incidentId={} mode={} rootCause={} confidence={}",
                    incidentId, selectedMode, root.get("service"), root.get("confidence"));
            events.emit("rca.completed", incidentId, result);
            predict();
            return result;

        } catch (Exception e) {
            db.update("update incidents set status='ANALYSIS_FAILED' where id=?", incidentId);
            log.error("[ANALYZE] AI RCA failed for incident={} url={}: {}",
                    incidentId, aiUrl + "/analyze/root-cause", e.getMessage(), e);
            events.emit("rca.failed", incidentId, Map.of("message", String.valueOf(e.getMessage())));
            throw new RuntimeException("RCA analysis failed: " + e.getMessage(), e);
        }
    }

    private void predict() {
        try {
            Map<String, Object> payload = Map.of("topology", topology(), "services", allServices());

            @SuppressWarnings("unchecked")
            List<Map<String, Object>> r = aiPost("/predict/failure", payload, List.class);

            if (r == null) throw new IllegalStateException("AI prediction returned an empty body");

            for (Map<String, Object> p : r) {
                db.update(
                        "insert into predictions(service_name,probability,risk_level,horizon_seconds,factors) " +
                        "values(?,?,?,?,cast(? as jsonb))",
                        p.get("service"), p.get("probability"), p.get("riskLevel"),
                        p.get("horizonSeconds"), write(p.get("factors")));
            }
            log.info("[PREDICT] Stored {} predictions", r.size());
            events.emit("predictions.updated", "system", r);

        } catch (Exception e) {
            log.error("[PREDICT] AI prediction failed at {}: {}", aiUrl + "/predict/failure", e.getMessage(), e);
        }
    }

    // ─── Query methods ────────────────────────────────────────────────────────

    public Map<String, Object> rca(UUID incident) {
        var a = db.queryForList(
                "select * from root_cause_analyses where incident_id=? order by completed_at desc limit 1",
                incident);
        if (a.isEmpty()) throw new NoSuchElementException("RCA pending");
        var candidates = db.queryForList(
                "select service_name as service, score, signals::text as signals " +
                "from root_cause_candidates where analysis_id=? order by score desc",
                a.get(0).get("id"));
        return Map.of("analysis", a.get(0), "candidates", candidates);
    }

    public Object prediction(String name) {
        return db.queryForList(
                "select id, service_name as service, probability, risk_level as \"riskLevel\", " +
                "horizon_seconds as \"horizonSeconds\", factors::text as factors, created_at as \"createdAt\" " +
                "from predictions " + (name == null ? "" : "where service_name=? ") +
                "order by created_at desc",
                name == null ? new Object[]{} : new Object[]{name});
    }

    public Map<String, Object> metrics(String service) {
        String sql = "select service_name as service, captured_at as timestamp, " +
                "p50_latency as \"p50Latency\", p95_latency as \"p95Latency\", p99_latency as \"p99Latency\", " +
                "error_rate as \"errorRate\", request_rate as \"requestRate\", db_latency as \"dbLatency\", " +
                "pool_utilization as \"poolUtilization\", anomaly_score as \"anomalyScore\" " +
                "from telemetry_snapshots " + (service == null ? "" : "where service_name=? ") +
                "order by captured_at desc limit 500";
        List<Map<String, Object>> samples = service == null
                ? db.queryForList(sql)
                : db.queryForList(sql, service);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("service", service);
        out.put("samples", samples);
        return out;
    }

    public Map<String, Object> simulate(Map<String, Object> request) {
        String target = String.valueOf(request.getOrDefault("target", "inventory-db"));
        try {
            Map<String, Object> payload = Map.of(
                    "topology", topology(),
                    "services", allServices(),
                    "target", target,
                    "reductionPercent", request.getOrDefault("reductionPercent", 70)
            );

            @SuppressWarnings("unchecked")
            Map<String, Object> r = aiPost("/simulate/counterfactual", payload, Map.class);

            UUID id = UUID.randomUUID();
            db.update(
                    "insert into simulations(id,target,intervention,status) values(?,?,cast(? as jsonb),'COMPLETED')",
                    id, target, write(request));

            @SuppressWarnings("unchecked")
            List<Object> results = (List<Object>) r.get("results");
            for (Object x : results) {
                @SuppressWarnings("unchecked")
                Map<String, Object> row = (Map<String, Object>) x;
                db.update(
                        "insert into simulation_results(simulation_id,service_name,baseline,counterfactual) " +
                        "values(?,?,?,?)",
                        id, row.get("service"), row.get("baseline"), row.get("counterfactual"));
            }
            r.put("id", id);
            events.emit("simulation.completed", id, r);
            return r;
        } catch (Exception e) {
            throw new IllegalStateException("AI engine unavailable: " + e.getMessage(), e);
        }
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> causalCounterfactual(Map<String, Object> body) {
        return aiPost("/causal/counterfactual", body, Map.class);
    }
}

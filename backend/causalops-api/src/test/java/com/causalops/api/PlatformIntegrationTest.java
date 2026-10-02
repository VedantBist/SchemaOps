package com.causalops.api;

import com.causalops.api.engine.AiEngineClient;
import com.causalops.api.environment.Environment;
import com.causalops.api.environment.EnvironmentConfig;
import com.causalops.api.environment.EnvironmentService;
import com.causalops.api.incident.IncidentRepository;
import com.causalops.api.telemetry.PrometheusClient;
import com.causalops.api.telemetry.TelemetryIngestor;
import com.causalops.api.telemetry.TopologySync;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.time.Instant;
import java.util.*;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.options;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

/**
 * Runs the real schema (Flyway V1..V3) on PostgreSQL and drives discovery, ingestion and
 * incident detection with a stubbed Prometheus, so every assertion is about stored data.
 */
@Testcontainers
@SpringBootTest(properties = {
        "causalops.telemetry.initial-delay-ms=3600000",
        "causalops.topology.initial-delay-ms=3600000",
        "causalops.cors.allowed-origins=http://allowed.example",
})
@AutoConfigureMockMvc
class PlatformIntegrationTest {

    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16-alpine");

    @MockitoBean PrometheusClient prometheus;
    @MockitoBean AiEngineClient engine;
    @Autowired EnvironmentService environments;
    @Autowired TopologySync topologySync;
    @Autowired TelemetryIngestor ingestor;
    @Autowired IncidentRepository incidents;
    @Autowired JdbcTemplate db;
    @Autowired MockMvc mvc;
    @Autowired com.causalops.api.incident.IncidentAnalysisService analysis;

    /** Current fake measurements: first key contained in the query wins (insertion order). */
    private final Map<String, List<PrometheusClient.Sample>> answers = new LinkedHashMap<>();

    private static PrometheusClient.Sample s(Map<String, String> labels, double v) {
        return new PrometheusClient.Sample(labels, v);
    }

    @BeforeEach
    void stubPrometheus() {
        db.update("DELETE FROM incidents");
        when(prometheus.query(anyString(), anyString())).thenAnswer(inv -> {
            String q = inv.getArgument(1);
            return answers.entrySet().stream().filter(e -> q.contains(e.getKey()))
                    .findFirst().map(Map.Entry::getValue).orElse(List.of());
        });
        answers.clear();
        answers.put("traces_service_graph_request_total[", List.of(
                s(Map.of("client", "user", "server", "gateway", "connection_type", "virtual_node", "failed", "false"), 5),
                s(Map.of("client", "gateway", "server", "orders", "failed", "false"), 5),
                s(Map.of("client", "orders", "server", "orders-db", "connection_type", "database", "failed", "false"), 5)));
        healthy();
    }

    private void healthy() {
        // The error-rate query also contains the request-rate selector, so its key must come first.
        answers.put("status_code=\"STATUS_CODE_ERROR\"", List.of());
        answers.put("traces_span_metrics_calls_total{span_kind=\"SPAN_KIND_SERVER\"}[", List.of(
                s(Map.of("service_name", "gateway"), 5), s(Map.of("service_name", "orders"), 5)));
        answers.put("histogram_quantile(0.99, sum by (service_name", List.of(
                s(Map.of("service_name", "gateway"), 40), s(Map.of("service_name", "orders"), 30)));
    }

    private void ordersSlow() {
        answers.put("histogram_quantile(0.99, sum by (service_name", List.of(
                s(Map.of("service_name", "gateway"), 900), s(Map.of("service_name", "orders"), 1400)));
    }

    private Environment env() {
        return environments.resolve(null);
    }

    @Test
    void migrationRemovesSyntheticSeedsAndBootstrapsEnvironment() {
        assertEquals(1, environments.all().size());
        Environment env = env();
        assertEquals("reference", env.name());
        assertEquals("LEARNING", env.status());
        Integer seeded = db.queryForObject("SELECT count(*) FROM services WHERE name IN ('auth-gateway','api-gateway')", Integer.class);
        assertEquals(0, seeded, "V1/V2 hand-written services must be gone after V3");
    }

    @Test
    void discoversTopologyAndStoresOnlyMeasuredValues() {
        topologySync.sync(env());
        var nodes = db.queryForList("SELECT name, kind FROM services WHERE environment_id = ? ORDER BY name", env().id());
        assertEquals(List.of("gateway", "orders", "orders-db"), nodes.stream().map(n -> n.get("name")).toList());
        assertEquals("database", nodes.get(2).get("kind"));
        assertEquals(2, db.queryForObject("SELECT count(*) FROM dependencies WHERE environment_id = ?", Integer.class, env().id()));

        ingestor.ingest(env(), Instant.now());
        var gateway = db.queryForMap("""
                SELECT p99_latency, error_rate, request_rate, p50_latency FROM telemetry_snapshots
                WHERE service_name = 'gateway' ORDER BY captured_at DESC LIMIT 1""");
        assertEquals(40.0, gateway.get("p99_latency"));
        assertEquals(0.0, gateway.get("error_rate"));
        assertEquals(5.0, gateway.get("request_rate"));
        assertNull(gateway.get("p50_latency"), "a metric Prometheus did not return is stored as NULL, never invented");
        assertEquals(0, db.queryForObject("SELECT count(*) FROM telemetry_snapshots WHERE service_name = 'orders-db'", Integer.class),
                "no measurement, no row");
    }

    @Test
    void sloBreachOpensThenResolvesIncidentWithSequentialKeys() throws Exception {
        Environment env = env();
        topologySync.sync(env);
        ordersSlow();
        for (int i = 0; i < env.config().detection().breachSamples(); i++) ingestor.ingest(env, Instant.now());

        var open = incidents.list(env.id(), IncidentRepository.Filter.ACTIVE, 10);
        assertEquals(1, open.size());
        Map<String, Object> incident = open.get(0);
        assertTrue(((String) incident.get("incidentKey")).matches("INC-\\d+"));
        assertEquals("CRITICAL", incident.get("severity"), "1400 ms exceeds 2x the 500 ms SLO");
        assertEquals("slo_breach", incident.get("detectionSource"), "a learning environment detects from SLOs only");
        var evidence = new com.fasterxml.jackson.databind.ObjectMapper().readTree((String) incident.get("evidence"));
        assertTrue(java.util.stream.StreamSupport.stream(evidence.spliterator(), false)
                        .anyMatch(e -> "orders".equals(e.path("service").asText()) && e.path("observed").asDouble() == 1400.0),
                "evidence records the measured 1400 ms p99 of orders");
        assertTrue(((String) incident.get("affectedServices")).contains("orders"));

        healthy();
        for (int i = 0; i < env.config().detection().recoverySamples(); i++) ingestor.ingest(env, Instant.now());
        assertTrue(incidents.list(env.id(), IncidentRepository.Filter.ACTIVE, 10).isEmpty());
        var resolved = incidents.list(env.id(), IncidentRepository.Filter.RESOLVED, 10).get(0);
        assertNotNull(resolved.get("resolvedAt"));
        assertEquals(List.of("DETECTED", "RECOVERED"), incidents.timeline((UUID) resolved.get("id")).stream()
                .map(e -> e.get("eventType")).toList());

        Set<String> keys = new HashSet<>();
        for (int i = 0; i < 150; i++) {
            UUID id = incidents.create(env.id(), "t", "HIGH", "RESOLVED", "test", "s", List.of(), List.of());
            assertTrue(keys.add((String) incidents.get(id).get("incidentKey")), "incident keys never collide");
        }
    }

    @Test
    void apiReturnsRealStatusCodesAndRestrictsCors() throws Exception {
        mvc.perform(get("/api/incidents/{id}", UUID.randomUUID()))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.status").value(404));
        mvc.perform(get("/api/incidents").param("state", "bogus")).andExpect(status().isBadRequest());
        mvc.perform(options("/api/services").header("Origin", "http://evil.example")
                        .header("Access-Control-Request-Method", "GET"))
                .andExpect(status().isForbidden());
        mvc.perform(options("/api/services").header("Origin", "http://allowed.example")
                        .header("Access-Control-Request-Method", "GET"))
                .andExpect(header().string("Access-Control-Allow-Origin", "http://allowed.example"));
    }

    @Test
    void calibratedEnvironmentDetectsFromAnomalyGateBeforeAnSloBreach() throws Exception {
        Environment env = env();
        topologySync.sync(env);
        environments.updateStatus(env.id(), "ACTIVE");
        try {
            // The engine flags orders as anomalous against its learned baseline; latency is still within SLO.
            when(engine.post(eq("/pipeline/evaluate"), any())).thenReturn(Map.of(
                    "model_version", "test-model",
                    "services", Map.of(
                            "orders", Map.of("anomaly", 0.97, "flagged", true, "signals", List.of(Map.of(
                                    "variable", "orders|latency", "value", 180.0, "baseline", 30.0, "z", 9.5))),
                            "gateway", Map.of("anomaly", 0.1, "flagged", false, "signals", List.of())),
                    "forecasts", List.of(Map.of("service", "orders", "horizon_seconds", 30, "probability", 0.62,
                            "risk_level", "HIGH", "method", "trend_extrapolation", "factors", List.of()))));
            Instant at = Instant.now();
            ingestor.ingest(environments.get(env.id()), at);

            var open = incidents.list(env.id(), IncidentRepository.Filter.ACTIVE, 10);
            assertEquals(1, open.size());
            assertEquals("anomaly_gate", open.get(0).get("detectionSource"));
            assertEquals("MEDIUM", open.get(0).get("severity"), "an anomaly without an SLO breach is an early warning");
            Double score = db.queryForObject("SELECT anomaly_score FROM telemetry_snapshots WHERE service_name = 'orders' "
                    + "AND captured_at = ?", Double.class, java.sql.Timestamp.from(at));
            assertEquals(0.97, score, 1e-9, "the engine's score is stored on the measured snapshot");
            assertEquals(1, db.queryForObject("SELECT count(*) FROM predictions WHERE method = 'trend_extrapolation' "
                    + "AND model_version = 'test-model'", Integer.class));

            ordersSlow();
            for (int i = 0; i < env.config().detection().breachSamples(); i++) {
                ingestor.ingest(environments.get(env.id()), Instant.now());
            }
            var escalated = incidents.get((UUID) open.get(0).get("id"));
            assertEquals("CRITICAL", escalated.get("severity"), "the SLO breach that follows escalates it");
            var events = incidents.timeline((UUID) open.get(0).get("id")).stream().map(e -> e.get("eventType")).toList();
            assertTrue(events.contains("DETECTED"));
            assertTrue(events.stream().anyMatch(e -> e.equals("SERVICES_AFFECTED") || e.equals("ESCALATED")
                    || e.equals("SLO_BREACH_CONFIRMED")), events.toString());
        } finally {
            environments.updateStatus(env.id(), "LEARNING");
            db.update("DELETE FROM predictions");
        }
    }

    @Test
    void storesEngineRootCauseWithLinkCandidateAndCounterfactual() {
        Environment env = env();
        topologySync.sync(env);
        environments.updateStatus(env.id(), "ACTIVE");
        try {
            UUID id = incidents.create(env.id(), "t", "HIGH", "DETECTED", "slo_breach", "s", List.of("orders"), List.of());
            String methodology = "Topology-constrained lagged SCM residuals + learned baselines + onset order + "
                    + "personalized PageRank (calibrated on this environment)";
            when(engine.post(eq("/pipeline/rca"), any())).thenReturn(Map.of(
                    "model_version", "test-model", "methodology", methodology,
                    "candidates", List.of(
                            Map.of("service", "orders->payments-with-a-long-service-name", "kind", "link",
                                   "target", "payments-with-a-long-service-name", "score", 0.91, "confidence", 0.6),
                            Map.of("service", "orders", "kind", "service", "target", "orders", "score", 0.4, "confidence", 0.3)),
                    "evidence", List.of(Map.of("type", "root_cause")),
                    "counterfactual", Map.of("intervention", Map.of("unit", "orders->payments-with-a-long-service-name"),
                            "validity", Map.of("status", "PASS"))));
            Map<String, Object> stored = analysis.analyze(id, null);
            @SuppressWarnings("unchecked")
            Map<String, Object> a = (Map<String, Object>) stored.get("analysis");
            assertEquals(methodology, a.get("methodology"), "methodology longer than 120 characters is stored intact");
            assertEquals("link", a.get("rootCauseKind"));
            assertNotNull(stored.get("counterfactual"));
            // Remediation planning starts right after the analysis is stored; with no executor enabled
            // in this environment it escalates, so either status is valid here.
            assertTrue(Set.of("RCA_IDENTIFIED", "MANUAL_INTERVENTION").contains(incidents.get(id).get("status")));
            assertTrue(incidents.timeline(id).stream().anyMatch(e -> "RCA_COMPLETED".equals(e.get("eventType"))));
        } finally {
            environments.updateStatus(env.id(), "LEARNING");
        }
    }

    @Test
    void changingMetricDefinitionsRestartsLearningAndLegacyDbTemplateIsUpgraded() throws Exception {
        Environment env = env();
        var cfg = env.config();
        var t = cfg.telemetry();
        var legacy = new java.util.LinkedHashMap<>(t.serviceMetrics());
        var p99 = legacy.remove("dbLatencyP99");
        legacy.put("dbLatencyP95", new EnvironmentConfig.MetricTemplate(
                p99.query().replace("histogram_quantile(0.99", "histogram_quantile(0.95"), p99.label(), p99.scale()));
        db.update("UPDATE environments SET status = 'ACTIVE', learning_started_at = now() - interval '2 days' WHERE id = ?", env.id());
        try {
            // Same definitions: the environment keeps its models.
            assertEquals("ACTIVE", environments.updateConfig(env.id(), cfg).status());

            var oldStyle = new EnvironmentConfig(cfg.endpoints(), new EnvironmentConfig.Telemetry(t.rateWindow(),
                    t.topologyWindow(), t.staleAfterSeconds(), legacy, t.dependencyNodeMetrics(), t.edgeMetrics(),
                    t.topologyQuery()), cfg.slo(), cfg.serviceSlos(), cfg.detection(), cfg.externalNodes(),
                    cfg.calibration(), cfg.analysis());
            db.update("UPDATE environments SET config = CAST(? AS jsonb) WHERE id = ?",
                    new com.fasterxml.jackson.databind.ObjectMapper().writeValueAsString(oldStyle), env.id());
            Environment upgraded = environments.updateConfig(env.id(), oldStyle);
            var metrics = upgraded.config().telemetry().serviceMetrics();
            assertFalse(metrics.containsKey("dbLatencyP95"));
            assertTrue(metrics.get("dbLatencyP99").query().startsWith("histogram_quantile(0.99"),
                    "database latency is measured at the same quantile as service latency");
            // The stored definition was p95, so the change restarts learning.
            assertEquals("LEARNING", upgraded.status());
            assertTrue(upgraded.learningStartedAt().isAfter(java.time.Instant.now().minusSeconds(60)));
        } finally {
            environments.updateConfig(env.id(), cfg);
            db.update("UPDATE environments SET status = 'LEARNING', status_reason = NULL WHERE id = ?", env.id());
        }
    }
}

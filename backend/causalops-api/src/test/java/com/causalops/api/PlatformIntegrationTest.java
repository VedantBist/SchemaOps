package com.causalops.api;

import com.causalops.api.environment.Environment;
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
import static org.mockito.ArgumentMatchers.anyString;
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
    @Autowired EnvironmentService environments;
    @Autowired TopologySync topologySync;
    @Autowired TelemetryIngestor ingestor;
    @Autowired IncidentRepository incidents;
    @Autowired JdbcTemplate db;
    @Autowired MockMvc mvc;

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
}

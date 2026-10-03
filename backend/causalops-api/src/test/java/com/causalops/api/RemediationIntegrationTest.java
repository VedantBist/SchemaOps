package com.causalops.api;

import com.causalops.api.engine.AiEngineClient;
import com.causalops.api.environment.Environment;
import com.causalops.api.environment.EnvironmentService;
import com.causalops.api.incident.IncidentAnalysisService;
import com.causalops.api.incident.IncidentRepository;
import com.causalops.api.remediation.RemediationService;
import com.causalops.api.telemetry.PrometheusClient;
import com.causalops.api.telemetry.SloEvaluator;
import com.causalops.api.telemetry.TelemetryIngestor;
import com.causalops.api.telemetry.TopologySync;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.util.*;
import java.util.function.Supplier;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * The closed remediation loop on the real schema: RCA -> recommendation -> tiered policy ->
 * executor call -> verification on measurements -> rollback -> escalation, with the AI engine
 * (RCA and executors) and Prometheus stubbed.
 */
@Testcontainers
@SpringBootTest(properties = {
        "causalops.telemetry.initial-delay-ms=3600000",
        "causalops.topology.initial-delay-ms=3600000",
})
@AutoConfigureMockMvc
class RemediationIntegrationTest {

    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16-alpine");

    @MockitoBean PrometheusClient prometheus;
    @MockitoBean AiEngineClient engine;
    @Autowired EnvironmentService environments;
    @Autowired TopologySync topologySync;
    @Autowired TelemetryIngestor ingestor;
    @Autowired IncidentRepository incidents;
    @Autowired IncidentAnalysisService analysis;
    @Autowired RemediationService remediation;
    @Autowired JdbcTemplate db;
    @Autowired MockMvc mvc;

    private Environment env;

    private static PrometheusClient.Sample s(Map<String, String> labels, double v) {
        return new PrometheusClient.Sample(labels, v);
    }

    @BeforeEach
    void setUp() {
        db.update("DELETE FROM incidents");
        Map<String, List<PrometheusClient.Sample>> answers = new LinkedHashMap<>();
        answers.put("traces_service_graph_request_total[", List.of(
                s(Map.of("client", "user", "server", "gateway", "connection_type", "virtual_node", "failed", "false"), 5),
                s(Map.of("client", "gateway", "server", "orders", "failed", "false"), 5),
                s(Map.of("client", "orders", "server", "orders-db", "connection_type", "database", "failed", "false"), 5)));
        answers.put("status_code=\"STATUS_CODE_ERROR\"", List.of());
        answers.put("traces_span_metrics_calls_total{span_kind=\"SPAN_KIND_SERVER\"}[", List.of(
                s(Map.of("service_name", "gateway"), 5), s(Map.of("service_name", "orders"), 5)));
        answers.put("histogram_quantile(0.99, sum by (service_name", List.of(
                s(Map.of("service_name", "gateway"), 40), s(Map.of("service_name", "orders"), 30)));
        when(prometheus.query(anyString(), anyString())).thenAnswer(inv -> {
            String q = inv.getArgument(1);
            return answers.entrySet().stream().filter(e -> q.contains(e.getKey())).findFirst()
                    .map(Map.Entry::getValue).orElse(List.of());
        });

        env = environments.resolve(null);
        var r = env.config().remediation()
                .withExecutor("docker", Map.of("enabled", true, "project", "shop"))
                .withAutonomy(1, false, false);
        var fast = new com.causalops.api.environment.EnvironmentConfig.Remediation(false, false, 1, 0.3, 30, 4, 10, 30, 0.5,
                new com.causalops.api.environment.EnvironmentConfig.Verification(0, 60, 2), r.executors(), r.targets(), r.actions());
        environments.updateConfig(env.id(), env.config().withRemediation(fast));
        topologySync.sync(env);
        ingestor.ingestAll();                       // fresh measured telemetry (the policy checks its age)
        environments.updateStatus(env.id(), "ACTIVE");
        env = environments.get(env.id());

        when(engine.post(eq("/pipeline/rca"), any())).thenReturn(Map.of(
                "model_version", "m1", "methodology", "test",
                "candidates", List.of(Map.of("service", "orders", "kind", "service", "target", "orders", "score", 0.9,
                        "confidence", 0.7, "root_variable", "orders|latency",
                        "signals", List.of(Map.of("variable", "orders|latency", "metric", "latency", "max_probability", 1.0)))),
                "evidence", List.of(),
                "counterfactual", Map.of("intervention", Map.of("unit", "orders"),
                        "entry_impact", Map.of("gateway", Map.of("mean_avoided_latency_ms", 300.0, "peak_avoided_latency_ms", 800.0)),
                        "restored_nodes", List.of("gateway", "orders"), "validity", Map.of("status", "PASS"))));
    }

    @AfterEach
    void tearDown() {
        environments.updateStatus(env.id(), "LEARNING");
        reset(engine);
    }

    private UUID incidentWithRootCause() {
        UUID id = incidents.create(env.id(), "t", "HIGH", "DETECTED", "slo_breach", "s", List.of("orders", "gateway"), List.of());
        analysis.analyze(id, null);
        return id;
    }

    private static Map<String, SloEvaluator.Result> levels(SloEvaluator.Level level) {
        var r = new SloEvaluator.Result(level, List.of());
        return Map.of("gateway", r, "orders", r);
    }

    private List<Map<String, Object>> executions(UUID incident) {
        return db.queryForList("SELECT id, status, mode, action_id FROM remediation_executions WHERE incident_id = ? ORDER BY started_at",
                incident);
    }

    private void await(String what, Supplier<Boolean> condition) throws InterruptedException {
        for (int i = 0; i < 100; i++) {
            if (condition.get()) return;
            Thread.sleep(100);
        }
        fail("timed out waiting for " + what + "; events " + db.queryForList(
                "SELECT event_type, payload::text FROM incident_events ORDER BY occurred_at") + "; recommendations " + db.queryForList(
                "SELECT action_id, status, policy->>'summary' AS summary FROM remediation_recommendations ORDER BY created_at"));
    }

    private List<String> timeline(UUID id) {
        return incidents.timeline(id).stream().map(e -> (String) e.get("eventType")).toList();
    }

    @Test
    @SuppressWarnings("unchecked")
    void tierOneActionRunsAutomaticallyAndIsVerifiedOnMeasurements() throws Exception {
        when(engine.postInternal(eq("/executors/execute"), any())).thenReturn(Map.of(
                "executor", "docker", "operation", "restart", "target", "orders", "detail", "restarted 1 container(s)"));
        UUID id = incidentWithRootCause();
        await("execution", () -> executions(id).stream().anyMatch(e -> "VERIFYING".equals(e.get("status"))));

        ArgumentCaptor<Map<String, Object>> body = ArgumentCaptor.forClass(Map.class);
        verify(engine).postInternal(eq("/executors/execute"), body.capture());
        assertEquals("restart", body.getValue().get("operation"));
        assertEquals("orders", body.getValue().get("target"));
        assertEquals(Map.of("enabled", true, "project", "shop"), body.getValue().get("executor_config"));

        remediation.verify(env, levels(SloEvaluator.Level.CRITICAL), Map.of());   // still bad: streak resets
        remediation.verify(env, levels(SloEvaluator.Level.HEALTHY), Map.of());
        assertEquals("VERIFYING", executions(id).get(0).get("status"));
        remediation.verify(env, levels(SloEvaluator.Level.HEALTHY), Map.of());
        assertEquals("VERIFIED", executions(id).get(0).get("status"));
        assertEquals("AUTO", executions(id).get(0).get("mode"));
        assertEquals("MITIGATED", incidents.get(id).get("status"));
        Map<String, Object> change = db.queryForMap("SELECT kind, target, ended_at FROM change_events WHERE reference_id = ?",
                executions(id).get(0).get("id"));
        assertEquals("REMEDIATION", change.get("kind"));
        assertNotNull(change.get("ended_at"), "the change window closes with the verdict");
        assertTrue(timeline(id).containsAll(List.of("RCA_COMPLETED", "REMEDIATION_RECOMMENDED", "REMEDIATION_EXECUTING",
                "REMEDIATION_EXECUTED", "REMEDIATION_VERIFIED")), timeline(id).toString());

        List<String> audit = db.queryForList("SELECT action FROM audit_log WHERE incident_id = ? ORDER BY id", String.class, id);
        assertTrue(audit.containsAll(List.of("POLICY_EVALUATED", "EXECUTION_STARTED", "EXECUTED", "VERIFIED")), audit.toString());
        assertThrows(Exception.class, () -> db.update("DELETE FROM audit_log"), "the audit log is append-only");

        mvc.perform(get("/api/remediation/executions").param("incidentId", id.toString()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].status").value("VERIFIED"))
                .andExpect(jsonPath("$[0].verification.healthyStreak").value(2));
        mvc.perform(get("/api/remediation/metrics"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.entryServices[0]").value("gateway"))
                .andExpect(jsonPath("$.incidents[0].handling").value("AUTO_REMEDIATED"))
                .andExpect(jsonPath("$.incidents[0].timeToMitigationSeconds").isNumber());
        // The engine's executor endpoints are not reachable through the generic proxy.
        mvc.perform(post("/api/engine/executors/execute").contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isForbidden());
    }

    @Test
    void failedVerificationRollsBackAsksForApprovalAndFinallyEscalates() throws Exception {
        when(engine.postInternal(eq("/executors/execute"), any())).thenAnswer(inv -> {
            @SuppressWarnings("unchecked") Map<String, Object> b = (Map<String, Object>) inv.getArgument(1);
            return "update_resources".equals(b.get("operation"))
                    ? Map.of("detail", "raised limits", "rollback_state", Map.of("limits", Map.of("abc", Map.of("NanoCpus", 1))))
                    : Map.of("detail", "restarted");
        });
        when(engine.postInternal(eq("/executors/rollback"), any())).thenReturn(Map.of("detail", "restored limits"));
        UUID id = incidentWithRootCause();
        await("restart", () -> executions(id).stream().anyMatch(e -> "VERIFYING".equals(e.get("status"))));

        // The restart does not help before the deadline: nothing to roll back, the next action needs approval (tier 2).
        db.update("UPDATE remediation_executions SET verify_deadline = now() - interval '1 second' WHERE incident_id = ?", id);
        remediation.verify(env, levels(SloEvaluator.Level.CRITICAL), Map.of());
        await("approval request", () -> "AWAITING_APPROVAL".equals(incidents.get(id).get("status")));
        Map<String, Object> pending = db.queryForMap("""
                SELECT id, action_id FROM remediation_recommendations WHERE incident_id = ? AND status = 'AWAITING_APPROVAL'
                """, id);
        assertEquals("docker-raise-limits", pending.get("action_id"));
        assertTrue(timeline(id).contains("ROLLBACK_NOT_APPLICABLE"));

        mvc.perform(post("/api/remediation/recommendations/" + pending.get("id") + "/approve")
                        .contentType(MediaType.APPLICATION_JSON).content("{\"by\":\"oncall@example.com\",\"reason\":\"try it\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.mode").value("APPROVED"))
                .andExpect(jsonPath("$.approvedBy").value("oncall@example.com"));
        await("approved execution", () -> executions(id).stream().anyMatch(e -> "VERIFYING".equals(e.get("status"))));

        // It does not help either: the reversible change is rolled back and, with nothing left, a human takes over.
        db.update("UPDATE remediation_executions SET verify_deadline = now() - interval '1 second' WHERE incident_id = ? AND status = 'VERIFYING'", id);
        remediation.verify(env, levels(SloEvaluator.Level.CRITICAL), Map.of());
        await("escalation", () -> "MANUAL_INTERVENTION".equals(incidents.get(id).get("status")));
        verify(engine).postInternal(eq("/executors/rollback"), any());
        assertEquals(List.of("FAILED", "ROLLED_BACK"), executions(id).stream().map(e -> (String) e.get("status")).toList());
        assertTrue(timeline(id).containsAll(List.of("REMEDIATION_AWAITING_APPROVAL", "REMEDIATION_APPROVED",
                "REMEDIATION_ROLLED_BACK", "REMEDIATION_BLOCKED")), timeline(id).toString());
        assertEquals(1, db.queryForObject("""
                SELECT count(*) FROM remediation_approvals a JOIN remediation_recommendations r ON r.id = a.recommendation_id
                WHERE r.incident_id = ? AND a.consumed_at IS NOT NULL
                """, Integer.class, id));
    }

    @Test
    void killSwitchBlocksEverythingAndIsAudited() throws Exception {
        mvc.perform(put("/api/remediation/autonomy").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"killSwitch\":true,\"changedBy\":\"sre-lead\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.settings.killSwitch").value(true));
        env = environments.get(env.id());
        UUID id = incidentWithRootCause();
        await("escalation", () -> "MANUAL_INTERVENTION".equals(incidents.get(id).get("status")));
        verify(engine, never()).postInternal(anyString(), any());
        assertEquals(0, executions(id).size());
        String policy = db.queryForObject("SELECT policy::text FROM remediation_recommendations WHERE incident_id = ? AND rank = 1",
                String.class, id);
        assertTrue(policy.contains("KILL_SWITCH_OFF") && policy.contains("BLOCKED"), policy);
        assertEquals(1, db.queryForObject("SELECT count(*) FROM audit_log WHERE action = 'AUTONOMY_CHANGED' AND actor = 'sre-lead'",
                Integer.class));
        mvc.perform(put("/api/remediation/autonomy").contentType(MediaType.APPLICATION_JSON)
                .content("{\"killSwitch\":false,\"changedBy\":\"sre-lead\"}")).andExpect(status().isOk());
    }
}

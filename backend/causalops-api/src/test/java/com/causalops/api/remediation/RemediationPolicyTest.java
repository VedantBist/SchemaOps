package com.causalops.api.remediation;

import com.causalops.api.environment.EnvironmentConfig;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.time.Instant;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;

class RemediationPolicyTest {

    private static final Instant NOW = Instant.parse("2026-10-02T12:00:00Z");

    private static Recommender.Proposal proposal(EnvironmentConfig.Action action, double confidence, String validity) {
        var c = new Recommender.Candidate("orders", "service", "orders", confidence, Set.of("latency"), "latency");
        return new Recommender.Proposal(c, action, "orders", action.operation().equals("update_resources"),
                action.operation().equals("restart"), new Recommender.Benefit(300.0, 600.0, null, 2, validity),
                new Recommender.Effectiveness(0, 0), 1, 0.3, "");
    }

    private static RemediationPolicy.Context ctx(Recommender.Proposal p, String status, boolean approved) {
        return new RemediationPolicy.Context(Fixtures.config(1, Fixtures.dockerOnly()), false, status, p, NOW, NOW, 5.0,
                false, false, null, 0, 0.25, approved);
    }

    private static Set<String> failed(RemediationPolicy.Decision d) {
        return Set.copyOf(d.rules().stream().filter(r -> !r.passed()).map(RemediationPolicy.Rule::id).toList());
    }

    @Test
    void tierOneActionRunsAutomaticallyWhenEveryRulePasses() {
        var d = RemediationPolicy.decide(ctx(proposal(Fixtures.RESTART, 0.6, "PASS"), "ACTIVE", false));
        assertEquals(RemediationPolicy.Mode.AUTO, d.mode(), d.summary());
        assertEquals(15, d.rules().size());
    }

    @Test
    void higherTierOrWeakEvidenceAsksForApproval() {
        assertEquals(Set.of("TIER_WITHIN_AUTONOMY"),
                failed(RemediationPolicy.decide(ctx(proposal(Fixtures.LIMITS, 0.6, "PASS"), "ACTIVE", false))));
        var weak = RemediationPolicy.decide(ctx(proposal(Fixtures.RESTART, 0.1, "WARN"), "CALIBRATED", false));
        assertEquals(RemediationPolicy.Mode.APPROVAL, weak.mode());
        assertEquals(Set.of("RCA_CONFIDENCE", "COUNTERFACTUAL_VALID", "ENVIRONMENT_ACTIVE"), failed(weak));
        // An operator's approval takes the autonomy decision; the safety rules still apply.
        assertEquals(RemediationPolicy.Mode.AUTO,
                RemediationPolicy.decide(ctx(proposal(Fixtures.LIMITS, 0.1, "WARN"), "CALIBRATED", true)).mode());
    }

    @Test
    void autonomyRulesCoverStaleTelemetryCooldownBudgetAndBlastRadius() {
        var p = proposal(Fixtures.RESTART, 0.6, "PASS");
        var c = new RemediationPolicy.Context(Fixtures.config(1, Fixtures.dockerOnly()), false, "ACTIVE", p, NOW, NOW, 120.0,
                false, false, NOW.minus(Duration.ofMinutes(3)), 4, 0.75, false);
        var d = RemediationPolicy.decide(c);
        assertEquals(RemediationPolicy.Mode.APPROVAL, d.mode());
        assertEquals(Set.of("TELEMETRY_FRESH", "TARGET_COOLDOWN", "AUTO_BUDGET", "BLAST_RADIUS"), failed(d));
    }

    @Test
    void safetyRulesBlockEvenWithApproval() {
        var p = proposal(Fixtures.RESTART, 0.9, "PASS");
        var killed = new RemediationPolicy.Context(Fixtures.config(3, Fixtures.dockerOnly()), true, "ACTIVE", p, NOW, NOW, 5.0,
                false, false, null, 0, 0.0, true);
        assertEquals(RemediationPolicy.Mode.BLOCKED, RemediationPolicy.decide(killed).mode());

        var busyAndRetried = new RemediationPolicy.Context(Fixtures.config(3, Fixtures.dockerOnly()), false, "ACTIVE", p,
                NOW.minus(Duration.ofHours(1)), NOW, 5.0, true, true, null, 0, 0.0, true);
        var d = RemediationPolicy.decide(busyAndRetried);
        assertEquals(RemediationPolicy.Mode.BLOCKED, d.mode());
        assertEquals(Set.of("RECOMMENDATION_FRESH", "NOT_ALREADY_TRIED", "TARGET_NOT_BUSY"), failed(d));

        var learning = RemediationPolicy.decide(ctx(p, "LEARNING", true));
        assertEquals(RemediationPolicy.Mode.BLOCKED, learning.mode());
        assertTrue(failed(learning).contains("ENVIRONMENT_CALIBRATED"));

        var k8sOff = RemediationPolicy.decide(ctx(proposal(Fixtures.K8S_SCALE, 0.9, "PASS"), "ACTIVE", true));
        assertEquals(RemediationPolicy.Mode.BLOCKED, k8sOff.mode());
        assertTrue(failed(k8sOff).contains("EXECUTOR_ENABLED"));
    }
}

package com.causalops.api.remediation;

import com.causalops.api.environment.EnvironmentConfig;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/**
 * Decides whether a proposed action may run, and whether it needs a human. Pure function.
 *
 * <p><b>Safety rules</b> must pass for any execution, approved or not (a failure blocks it).
 * <b>Autonomy rules</b> decide between running automatically and asking for approval; with an
 * approval they are reported but do not block, because the operator has taken the decision.
 */
public final class RemediationPolicy {

    public enum Mode { AUTO, APPROVAL, BLOCKED }

    public record Rule(String id, boolean safety, boolean passed, String detail) {
    }

    public record Decision(Mode mode, List<Rule> rules) {
        public String summary() {
            List<String> failed = rules.stream().filter(r -> !r.passed()).map(Rule::id).toList();
            return switch (mode) {
                case AUTO -> "all rules passed: executing automatically";
                case APPROVAL -> "needs approval: " + String.join(", ", failed);
                case BLOCKED -> "blocked: " + String.join(", ", rules.stream().filter(r -> r.safety() && !r.passed()).map(Rule::id).toList());
            };
        }
    }

    /** Everything the rules look at, gathered by the caller from the database. */
    public record Context(EnvironmentConfig.Remediation config, boolean globalKillSwitch, String environmentStatus,
                          Recommender.Proposal proposal, Instant recommendationCreatedAt, Instant now,
                          Double telemetryAgeSeconds, boolean alreadyTriedForIncident, boolean targetBusy,
                          Instant lastChangeOnTarget, int autoExecutionsLastHour, Double blastRadius,
                          boolean approved) {
    }

    private static final Set<String> CALIBRATED = Set.of("CALIBRATED", "ACTIVE");

    private RemediationPolicy() {
    }

    public static Decision decide(Context c) {
        var cfg = c.config();
        var p = c.proposal();
        List<Rule> rules = new ArrayList<>();

        // Safety rules.
        boolean killed = c.globalKillSwitch() || cfg.killSwitch();
        rules.add(new Rule("KILL_SWITCH_OFF", true, !killed,
                killed ? (c.globalKillSwitch() ? "global kill switch is on" : "environment kill switch is on") : "off"));
        rules.add(new Rule("ENVIRONMENT_CALIBRATED", true, CALIBRATED.contains(c.environmentStatus()),
                "environment is " + c.environmentStatus()));
        rules.add(new Rule("EXECUTOR_ENABLED", true, cfg.executorEnabled(p.action().executor()),
                p.action().executor() + (cfg.executorEnabled(p.action().executor()) ? " enabled" : " not enabled")));
        rules.add(new Rule("CHANGE_IS_UNDOABLE_OR_STATELESS", true,
                p.reversible() || p.stateless() || "webhook".equals(p.action().executor()),
                p.reversible() ? "rollback restores the previous state" : p.stateless()
                        ? "changes no persistent state" : "runbook decides its own rollback"));
        Duration age = Duration.between(c.recommendationCreatedAt(), c.now());
        boolean fresh = age.toMinutes() < cfg.recommendationTtlMinutes();
        rules.add(new Rule("RECOMMENDATION_FRESH", true, fresh,
                "created %ds ago (ttl %d min)".formatted(age.toSeconds(), cfg.recommendationTtlMinutes())));
        rules.add(new Rule("NOT_ALREADY_TRIED", true, !c.alreadyTriedForIncident(),
                c.alreadyTriedForIncident() ? "this action was already executed or rejected for this incident" : "first attempt"));
        rules.add(new Rule("TARGET_NOT_BUSY", true, !c.targetBusy(),
                c.targetBusy() ? "another change on this target is still executing or being verified" : "no change in flight"));

        // Autonomy rules.
        int tier = p.action().tier();
        rules.add(new Rule("TIER_WITHIN_AUTONOMY", false, tier <= cfg.autoExecuteMaxTier(),
                "tier %d, autonomous up to tier %d".formatted(tier, cfg.autoExecuteMaxTier())));
        rules.add(new Rule("ENVIRONMENT_ACTIVE", false, "ACTIVE".equals(c.environmentStatus()),
                "ACTIVE".equals(c.environmentStatus()) ? "models passed the quality gates"
                        : "models have not passed the quality gates yet (" + c.environmentStatus() + ")"));
        double conf = p.candidate().confidence();
        rules.add(new Rule("RCA_CONFIDENCE", false, conf >= cfg.minRcaConfidence(),
                "%.2f (minimum %.2f)".formatted(conf, cfg.minRcaConfidence())));
        boolean telemetryFresh = c.telemetryAgeSeconds() != null && c.telemetryAgeSeconds() <= cfg.maxTelemetryAgeSeconds();
        rules.add(new Rule("TELEMETRY_FRESH", false, telemetryFresh, c.telemetryAgeSeconds() == null ? "no telemetry"
                : "last sample %.0fs old (max %ds)".formatted(c.telemetryAgeSeconds(), cfg.maxTelemetryAgeSeconds())));
        String validity = p.benefit().validity();
        rules.add(new Rule("COUNTERFACTUAL_VALID", false, "PASS".equals(validity), "counterfactual validity " + validity));
        boolean cooled = c.lastChangeOnTarget() == null
                || Duration.between(c.lastChangeOnTarget(), c.now()).toMinutes() >= cfg.cooldownMinutes();
        rules.add(new Rule("TARGET_COOLDOWN", false, cooled, c.lastChangeOnTarget() == null ? "no recent change on this target"
                : "last change %s (cooldown %d min)".formatted(c.lastChangeOnTarget(), cfg.cooldownMinutes())));
        rules.add(new Rule("AUTO_BUDGET", false, c.autoExecutionsLastHour() < cfg.maxAutoActionsPerHour(),
                "%d automatic actions in the last hour (max %d)".formatted(c.autoExecutionsLastHour(), cfg.maxAutoActionsPerHour())));
        boolean contained = c.blastRadius() != null && c.blastRadius() <= cfg.maxAutoBlastRadius();
        rules.add(new Rule("BLAST_RADIUS", false, contained, c.blastRadius() == null
                ? "no current telemetry to judge what depends on the target"
                : "%.0f%% of monitored components depend on the target and are still healthy (max %.0f%%)".formatted(
                c.blastRadius() * 100, cfg.maxAutoBlastRadius() * 100)));

        Mode mode;
        if (rules.stream().anyMatch(r -> r.safety() && !r.passed())) mode = Mode.BLOCKED;
        else if (c.approved() || rules.stream().allMatch(Rule::passed)) mode = Mode.AUTO;
        else mode = Mode.APPROVAL;
        return new Decision(mode, rules);
    }
}

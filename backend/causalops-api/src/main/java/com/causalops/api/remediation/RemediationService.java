package com.causalops.api.remediation;

import com.causalops.api.changes.ChangeEventRepository;
import com.causalops.api.engine.AiEngineClient;
import com.causalops.api.environment.Environment;
import com.causalops.api.environment.EnvironmentConfig;
import com.causalops.api.environment.EnvironmentService;
import com.causalops.api.events.EventBus;
import com.causalops.api.incident.AnomalySignal;
import com.causalops.api.incident.IncidentRepository;
import com.causalops.api.incident.RootCauseIdentified;
import com.causalops.api.telemetry.SloEvaluator;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.event.EventListener;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Closed-loop remediation: once an incident's root cause is known, propose actions, let the
 * tiered policy decide (automatic, approval, blocked), execute through the AI engine's executor
 * plugins, verify recovery on the next real measurements, roll back reversible changes that did
 * not help, and escalate to a human when nothing safe is left to try. Every step is written to
 * the incident timeline, the audit log and the event stream.
 */
@Service
public class RemediationService {

    private static final Logger log = LoggerFactory.getLogger(RemediationService.class);
    static final String AUTONOMY = "causalops-autonomy";
    private static final int CANDIDATES_CONSIDERED = 2;

    private final RemediationRepository repo;
    private final IncidentRepository incidents;
    private final EnvironmentService environments;
    private final AiEngineClient engine;
    private final EventBus events;
    private final JdbcTemplate db;
    private final ChangeEventRepository changes;
    private final boolean globalKillSwitch;
    private final ExecutorService workers = Executors.newFixedThreadPool(2);

    public RemediationService(RemediationRepository repo, IncidentRepository incidents, EnvironmentService environments,
                              AiEngineClient engine, EventBus events, JdbcTemplate db, ChangeEventRepository changes,
                              @Value("${causalops.remediation.kill-switch:false}") boolean globalKillSwitch) {
        this.changes = changes;
        this.repo = repo;
        this.incidents = incidents;
        this.environments = environments;
        this.engine = engine;
        this.events = events;
        this.db = db;
        this.globalKillSwitch = globalKillSwitch;
    }

    public boolean globalKillSwitch() {
        return globalKillSwitch;
    }

    @EventListener
    public void onRootCause(RootCauseIdentified e) {
        workers.submit(() -> {
            try {
                plan(e.incidentId(), "rca_completed");
            } catch (RuntimeException ex) {
                log.warn("Remediation planning for incident {} failed: {}", e.incidentId(), ex.getMessage());
                incidents.addEvent(e.incidentId(), "REMEDIATION_PLANNING_FAILED", Map.of("message", String.valueOf(ex.getMessage())));
            }
        });
    }

    // ── planning ────────────────────────────────────────────────────────────────

    /** Proposes actions for the incident's current root cause and acts on the best one the policy allows. */
    public synchronized List<Map<String, Object>> plan(UUID incidentId, String trigger) {
        Map<String, Object> incident = incidents.get(incidentId);
        if ("RESOLVED".equals(incident.get("status"))) return List.of();
        if (repo.incidentHasChangeInFlight(incidentId)) return List.of();
        Environment env = environments.get((UUID) incident.get("environmentId"));
        var cfg = env.config().remediation();

        Map<String, Object> analysis = latestAnalysis(incidentId);
        if (analysis == null) throw new IllegalStateException("Incident has no root-cause analysis yet");
        List<Recommender.Candidate> candidates = candidates((UUID) analysis.get("id"));
        Map<String, Recommender.Benefit> benefits = benefits(env, incident, candidates);
        List<Recommender.Proposal> proposals = Recommender.propose(candidates, cfg, benefits,
                (action, metric) -> repo.effectiveness(env.id(), action, metric));

        repo.supersedePending(incidentId);
        Instant now = Instant.now();
        Instant expires = now.plus(Duration.ofMinutes(cfg.recommendationTtlMinutes()));
        List<UUID> ids = new ArrayList<>();
        for (int i = 0; i < proposals.size(); i++) {
            ids.add(repo.insertRecommendation(env.id(), incidentId, (UUID) analysis.get("id"), i + 1, proposals.get(i),
                    "PROPOSED", Map.of(), expires));
        }
        if (proposals.isEmpty()) {
            String why = "No enabled action addresses " + candidates.stream().map(c -> c.unit() + " (" + c.kind() + ")").toList()
                    + ". Enable an executor or add a runbook for this kind of component.";
            escalate(env, incidentId, "REMEDIATION_UNAVAILABLE", why, Map.of("candidates", candidates.stream().map(Recommender.Candidate::unit).toList()));
            return List.of();
        }
        incidents.addEvent(incidentId, "REMEDIATION_RECOMMENDED", Map.of("trigger", trigger, "count", proposals.size(),
                "top", proposals.get(0).action().id() + " on " + proposals.get(0).candidate().unit()));
        events.emit("remediation.recommended", incidentId, Map.of("count", proposals.size()));

        for (int i = 0; i < proposals.size(); i++) {
            Recommender.Proposal p = proposals.get(i);
            UUID recId = ids.get(i);
            RemediationPolicy.Decision d = RemediationPolicy.decide(context(env, incidentId, p, now, now, false));
            Map<String, Object> policy = policyJson(d);
            repo.audit(env.id(), incidentId, AUTONOMY, "POLICY_EVALUATED", "recommendation", recId, policy);
            switch (d.mode()) {
                case BLOCKED -> repo.setRecommendationStatus(recId, "BLOCKED", policy);
                case AUTO -> {
                    repo.setRecommendationStatus(recId, "PROPOSED", policy);
                    startExecution(env, repo.recommendation(recId), p.candidate().rootMetric(), "AUTO", null);
                    return repo.recommendations(env.id(), incidentId, null, 50);
                }
                case APPROVAL -> {
                    repo.setRecommendationStatus(recId, "AWAITING_APPROVAL", policy);
                    incidents.setStatus(incidentId, "AWAITING_APPROVAL");
                    incidents.addEvent(incidentId, "REMEDIATION_AWAITING_APPROVAL", Map.of("recommendationId", recId.toString(),
                            "action", p.action().id(), "target", p.candidate().unit(), "reason", d.summary()));
                    events.emit("remediation.approval_required", incidentId, Map.of("recommendationId", recId,
                            "action", p.action().name(), "target", p.candidate().unit(), "reason", d.summary()));
                    return repo.recommendations(env.id(), incidentId, null, 50);
                }
            }
        }
        escalate(env, incidentId, "REMEDIATION_BLOCKED", "Every proposed action is blocked by a safety rule", Map.of());
        return repo.recommendations(env.id(), incidentId, null, 50);
    }

    private RemediationPolicy.Context context(Environment env, UUID incidentId, Recommender.Proposal p,
                                              Instant createdAt, Instant now, boolean approved) {
        String target = p.candidate().unit();
        return new RemediationPolicy.Context(env.config().remediation(), globalKillSwitch, env.status(), p, createdAt, now,
                repo.telemetryAgeSeconds(env.id()), repo.alreadyTried(incidentId, p.action().id(), target),
                repo.targetBusy(env.id(), target), repo.lastChange(env.id(), target), repo.autoExecutionsLastHour(env.id()),
                blastRadius(env, incidentId, p.candidate().target()), approved, changeInProgress(env));
    }

    /** A deployment or maintenance window that is open (or just ended): automation waits for a human. */
    private String changeInProgress(Environment env) {
        return changes.active(env.id(), 60, false).stream().findFirst()
                .map(c -> c.get("kind") + " " + c.get("target") + " (" + c.get("source") + ")").orElse(null);
    }

    /**
     * Marginal blast radius: the share of monitored nodes that depend on the target (transitively,
     * the target included) and are still healthy, i.e. not already part of the incident. Acting on
     * a root cause whose callers are already degraded disrupts nothing new; touching a healthy
     * component that many others depend on does.
     */
    Double blastRadius(Environment env, UUID incidentId, String node) {
        Set<String> monitored = new HashSet<>(repo.latestRequestRates(env.id()).keySet());
        monitored.removeAll(env.config().externalNodes());
        if (monitored.isEmpty()) return null;
        Map<String, Set<String>> callers = new HashMap<>();
        for (String[] e : repo.edges(env.id())) callers.computeIfAbsent(e[1], k -> new HashSet<>()).add(e[0]);
        Set<String> dependents = new HashSet<>();
        Deque<String> queue = new ArrayDeque<>(List.of(node));
        while (!queue.isEmpty()) {
            String n = queue.pop();
            if (!dependents.add(n)) continue;
            queue.addAll(callers.getOrDefault(n, Set.of()));
        }
        dependents.retainAll(monitored);
        Set<String> affected = Set.of(incidents.read((String) incidents.get(incidentId).get("affectedServices"), String[].class));
        dependents.removeAll(affected);
        return (double) dependents.size() / monitored.size();
    }

    /** Pending proposals expire at their TTL, or as soon as their incident has recovered. */
    @org.springframework.scheduling.annotation.Scheduled(fixedDelay = 30000, initialDelay = 30000)
    public void expireStale() {
        int n = db.update("""
                UPDATE remediation_recommendations r SET status = 'EXPIRED'
                WHERE r.status IN ('PROPOSED', 'AWAITING_APPROVAL', 'BLOCKED')
                  AND (r.expires_at < now() OR EXISTS (SELECT 1 FROM incidents i WHERE i.id = r.incident_id AND i.status = 'RESOLVED'))
                """);
        if (n > 0) log.info("Expired {} pending remediation proposals (TTL passed or incident recovered)", n);
    }

    // ── approvals ───────────────────────────────────────────────────────────────

    public Map<String, Object> approve(UUID recommendationId, String by, String reason) {
        requireActor(by);
        Map<String, Object> rec = repo.recommendation(recommendationId);
        if (!List.of("AWAITING_APPROVAL", "PROPOSED").contains(rec.get("status"))) {
            throw new IllegalStateException("Recommendation is " + rec.get("status") + "; only pending recommendations can be approved");
        }
        UUID incidentId = (UUID) rec.get("incidentId");
        if ("RESOLVED".equals(incidents.get(incidentId).get("status"))) {
            repo.setRecommendationStatus(recommendationId, "EXPIRED", null);
            throw new IllegalStateException("The incident has already recovered; the recommendation expired");
        }
        if (repo.incidentHasChangeInFlight(incidentId)) {
            throw new IllegalStateException("Another change for this incident is executing or being verified; wait for its verdict");
        }
        Environment env = environments.get((UUID) rec.get("environmentId"));
        Recommender.Proposal p = proposalOf(env, rec);
        RemediationPolicy.Decision d = RemediationPolicy.decide(context(env, incidentId, p,
                ((Timestamp) rec.get("createdAt")).toInstant(), Instant.now(), true));
        Map<String, Object> policy = policyJson(d);
        if (d.mode() == RemediationPolicy.Mode.BLOCKED) {
            repo.setRecommendationStatus(recommendationId, "BLOCKED", policy);
            repo.audit(env.id(), incidentId, by, "APPROVAL_BLOCKED", "recommendation", recommendationId, policy);
            throw new PolicyViolation(d.summary(), policy);
        }
        repo.insertDecision(recommendationId, "APPROVED", by, reason);
        repo.audit(env.id(), incidentId, by, "APPROVED", "recommendation", recommendationId,
                Map.of("reason", String.valueOf(reason), "policy", policy));
        incidents.addEvent(incidentId, "REMEDIATION_APPROVED", Map.of("by", by, "action", rec.get("actionId")));
        repo.setRecommendationStatus(recommendationId, "APPROVED", policy);
        UUID execution = startExecution(env, repo.recommendation(recommendationId), p.candidate().rootMetric(), "APPROVED", by);
        return repo.execution(execution);
    }

    public Map<String, Object> reject(UUID recommendationId, String by, String reason) {
        requireActor(by);
        Map<String, Object> rec = repo.recommendation(recommendationId);
        if (!List.of("AWAITING_APPROVAL", "PROPOSED", "BLOCKED").contains(rec.get("status"))) {
            throw new IllegalStateException("Recommendation is " + rec.get("status") + "; it can no longer be rejected");
        }
        UUID incidentId = (UUID) rec.get("incidentId");
        repo.insertDecision(recommendationId, "REJECTED", by, reason);
        repo.setRecommendationStatus(recommendationId, "REJECTED", null);
        repo.audit((UUID) rec.get("environmentId"), incidentId, by, "REJECTED", "recommendation", recommendationId,
                Map.of("reason", String.valueOf(reason)));
        incidents.addEvent(incidentId, "REMEDIATION_REJECTED", Map.of("by", by, "action", rec.get("actionId")));
        workers.submit(() -> replanQuietly(incidentId, "rejected"));
        return repo.recommendation(recommendationId);
    }

    private static void requireActor(String by) {
        if (by == null || by.isBlank() || by.length() > 120) throw new IllegalArgumentException("approvedBy/rejectedBy is required (max 120 chars)");
    }

    // ── execution ───────────────────────────────────────────────────────────────

    UUID startExecution(Environment env, Map<String, Object> rec, String rootMetric, String mode, String approvedBy) {
        UUID incidentId = (UUID) rec.get("incidentId");
        UUID recId = (UUID) rec.get("id");
        var cfg = env.config().remediation();
        UUID id;
        try {
            id = repo.insertExecution(rec, rootMetric, mode, approvedBy, cfg.dryRun());
        } catch (DuplicateKeyException e) {
            throw new IllegalStateException("Another change on " + rec.get("targetNode") + " is still executing or being verified");
        }
        // One change at a time per incident: other pending proposals are re-planned after this one's verdict.
        repo.supersedePending(incidentId);
        repo.setRecommendationStatus(recId, "EXECUTED", null);
        if (approvedBy != null) repo.consumeApproval(recId);
        String actor = approvedBy == null ? AUTONOMY : approvedBy;
        repo.audit(env.id(), incidentId, actor, "EXECUTION_STARTED", "execution", id,
                Map.of("action", rec.get("actionId"), "executor", rec.get("executor"), "operation", rec.get("operation"),
                        "target", rec.get("binding"), "mode", mode, "dryRun", cfg.dryRun()));
        incidents.setStatus(incidentId, "REMEDIATING");
        incidents.addEvent(incidentId, "REMEDIATION_EXECUTING", Map.of("executionId", id.toString(), "action", rec.get("actionId"),
                "target", rec.get("targetNode"), "mode", mode, "dryRun", cfg.dryRun()));
        events.emit("remediation.executing", incidentId, Map.of("executionId", id, "action", rec.get("actionName")));
        workers.submit(() -> runExecutor(env, rec, id, actor));
        return id;
    }

    @SuppressWarnings("unchecked")
    private void runExecutor(Environment env, Map<String, Object> rec, UUID executionId, String actor) {
        UUID incidentId = (UUID) rec.get("incidentId");
        var cfg = env.config().remediation();
        String executor = (String) rec.get("executor");
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("execution_id", executionId.toString());
        body.put("executor", executor);
        body.put("executor_config", cfg.executors().getOrDefault(executor, Map.of()));
        body.put("operation", rec.get("operation"));
        body.put("target", rec.get("binding"));
        body.put("params", repo.read((String) rec.get("params"), Map.class));
        body.put("dry_run", cfg.dryRun());
        body.put("context", Map.of("incident_id", incidentId.toString(), "environment", env.name(),
                "action_id", rec.get("actionId"), "node", rec.get("targetNode")));
        try {
            Map<String, Object> result = engine.postInternal("/executors/execute", body);
            if (cfg.dryRun()) {
                repo.finish(executionId, "DRY_RUN", result);
                repo.audit(env.id(), incidentId, actor, "DRY_RUN", "execution", executionId, result);
                incidents.addEvent(incidentId, "REMEDIATION_DRY_RUN", Map.of("executionId", executionId.toString(),
                        "detail", String.valueOf(result.get("detail"))));
                events.emit("remediation.dry_run", incidentId, Map.of("executionId", executionId));
                return;
            }
            Instant now = Instant.now();
            var v = cfg.verification();
            repo.executed(executionId, result, result.get("rollback_state"), now.plusSeconds(v.settleSeconds()),
                    now.plusSeconds(v.windowSeconds()));
            repo.audit(env.id(), incidentId, actor, "EXECUTED", "execution", executionId, result);
            // The action itself disturbs telemetry; calibration keeps this window out of "normal" data.
            changes.record(env.id(), "REMEDIATION", (String) rec.get("targetNode"), now, null, actor,
                    rec.get("actionId") + ": " + result.get("detail"), executionId);
            incidents.addEvent(incidentId, "REMEDIATION_EXECUTED", Map.of("executionId", executionId.toString(),
                    "detail", String.valueOf(result.get("detail")), "verifyWithinSeconds", v.windowSeconds()));
            events.emit("remediation.executed", incidentId, Map.of("executionId", executionId, "detail", String.valueOf(result.get("detail"))));
            log.info("Remediation {} executed: {}", executionId, result.get("detail"));
        } catch (RuntimeException e) {
            repo.finish(executionId, "ERROR", Map.of("error", String.valueOf(e.getMessage())));
            repo.audit(env.id(), incidentId, actor, "EXECUTION_ERROR", "execution", executionId, Map.of("error", String.valueOf(e.getMessage())));
            incidents.addEvent(incidentId, "REMEDIATION_FAILED", Map.of("executionId", executionId.toString(),
                    "stage", "execute", "message", String.valueOf(e.getMessage())));
            events.emit("remediation.failed", incidentId, Map.of("executionId", executionId, "message", String.valueOf(e.getMessage())));
            log.warn("Remediation {} failed to execute: {}", executionId, e.getMessage());
            replanQuietly(incidentId, "execution_error");
        }
    }

    // ── verification ────────────────────────────────────────────────────────────

    /**
     * Called after every ingestion cycle with that cycle's SLO evaluation and anomaly verdicts.
     * A change is verified once every watched service has been healthy (within SLO, not flagged)
     * for {@code healthySamples} consecutive cycles; past the deadline it has failed.
     */
    public void verify(Environment env, Map<String, SloEvaluator.Result> slo, Map<String, AnomalySignal> anomalies) {
        for (Map<String, Object> ex : repo.verifying(env.id())) {
            UUID id = (UUID) ex.get("id");
            UUID incidentId = (UUID) ex.get("incidentId");
            Set<String> watched = watched(ex, incidentId, slo.keySet());
            // Judged on the signal that declared the incident (as the detector resolves it): SLOs when a
            // breach confirmed it, otherwise the anomaly gate too.
            boolean sloIncident = sloConfirmed(incidentId);
            Map<String, Object> states = new TreeMap<>();
            boolean healthy = !watched.isEmpty();
            for (String s : watched) {
                SloEvaluator.Result r = slo.get(s);
                boolean flagged = anomalies.containsKey(s) && anomalies.get(s).flagged();
                String state = r == null ? "NO_DATA" : r.level() != SloEvaluator.Level.HEALTHY ? r.level().name()
                        : flagged ? (sloIncident ? "HEALTHY_ABOVE_BASELINE" : "ANOMALOUS") : "HEALTHY";
                states.put(s, state);
                healthy &= state.startsWith("HEALTHY");
            }
            int streak = healthy ? ((Number) ex.get("healthyStreak")).intValue() + 1 : 0;
            int required = env.config().remediation().verification().healthySamples();
            Map<String, Object> verification = new LinkedHashMap<>();
            verification.put("watched", states);
            verification.put("healthyStreak", streak);
            verification.put("requiredHealthySamples", required);
            verification.put("checkedAt", Instant.now().toString());
            if (streak >= required) {
                repo.verified(id, verification);
                changes.close(id);
                repo.audit(env.id(), incidentId, AUTONOMY, "VERIFIED", "execution", id, verification);
                incidents.setStatus(incidentId, "MITIGATED");
                incidents.addEvent(incidentId, "REMEDIATION_VERIFIED", Map.of("executionId", id.toString(), "watched", states));
                events.emit("remediation.verified", incidentId, Map.of("executionId", id));
                log.info("Remediation {} verified: {}", id, states);
            } else if (Instant.now().isAfter(((Timestamp) ex.get("verifyDeadline")).toInstant())) {
                repo.verificationFailed(id, verification);
                repo.audit(env.id(), incidentId, AUTONOMY, "VERIFICATION_FAILED", "execution", id, verification);
                incidents.addEvent(incidentId, "REMEDIATION_FAILED", Map.of("executionId", id.toString(), "stage", "verify",
                        "watched", states));
                events.emit("remediation.failed", incidentId, Map.of("executionId", id, "stage", "verify"));
                log.info("Remediation {} did not restore health in time: {}", id, states);
                workers.submit(() -> {
                    rollbackIfPossible(env, repo.execution(id), AUTONOMY, "verification failed");
                    changes.close(id);
                    replanQuietly(incidentId, "verification_failed");
                });
            } else {
                repo.verificationProgress(id, streak, verification);
            }
        }
    }

    private boolean sloConfirmed(UUID incidentId) {
        Map<String, Object> incident = incidents.get(incidentId);
        return Arrays.stream(incidents.read((String) incident.get("evidence"), Object[].class))
                .anyMatch(e -> e instanceof Map<?, ?> m && "slo_breach".equals(m.get("source")));
    }

    /** The target (when it is a monitored service) plus every service the incident affects. */
    private Set<String> watched(Map<String, Object> ex, UUID incidentId, Set<String> measured) {
        Set<String> out = new TreeSet<>();
        String target = (String) ex.get("targetNode");
        if (measured.contains(target)) out.add(target);
        Map<String, Object> incident = incidents.get(incidentId);
        for (String s : incidents.read((String) incident.get("affectedServices"), String[].class)) {
            if (measured.contains(s)) out.add(s);
        }
        return out;
    }

    // ── rollback ────────────────────────────────────────────────────────────────

    public Map<String, Object> manualRollback(UUID executionId, String by, String reason) {
        requireActor(by);
        Map<String, Object> ex = repo.execution(executionId);
        if (!List.of("VERIFIED", "FAILED", "VERIFYING").contains(ex.get("status"))) {
            throw new IllegalStateException("Execution is " + ex.get("status") + "; nothing to roll back");
        }
        if (ex.get("rollbackState") == null) {
            throw new IllegalStateException("'" + ex.get("operation") + "' changed no persistent state; there is nothing to roll back");
        }
        Environment env = environments.get((UUID) ex.get("environmentId"));
        repo.audit(env.id(), (UUID) ex.get("incidentId"), by, "ROLLBACK_REQUESTED", "execution", executionId,
                Map.of("reason", String.valueOf(reason)));
        rollbackIfPossible(env, ex, by, reason);
        changes.record(env.id(), "REMEDIATION", (String) ex.get("targetNode"), java.time.Instant.now(), java.time.Instant.now(),
                by, "manual rollback of " + ex.get("actionId"), null);
        return repo.execution(executionId);
    }

    @SuppressWarnings("unchecked")
    private void rollbackIfPossible(Environment env, Map<String, Object> ex, String actor, String reason) {
        UUID id = (UUID) ex.get("id");
        UUID incidentId = (UUID) ex.get("incidentId");
        if (ex.get("rollbackState") == null) {
            incidents.addEvent(incidentId, "ROLLBACK_NOT_APPLICABLE", Map.of("executionId", id.toString(),
                    "reason", ex.get("operation") + " changed no persistent state"));
            return;
        }
        String executor = (String) ex.get("executor");
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("execution_id", id.toString());
        body.put("executor", executor);
        body.put("executor_config", env.config().remediation().executors().getOrDefault(executor, Map.of()));
        body.put("operation", ex.get("operation"));
        body.put("target", ex.get("binding"));
        body.put("rollback_state", repo.read((String) ex.get("rollbackState"), Map.class));
        body.put("context", Map.of("incident_id", incidentId.toString(), "reason", reason));
        try {
            Map<String, Object> result = engine.postInternal("/executors/rollback", body);
            repo.rolledBack(id, "ROLLED_BACK", result);
            repo.audit(env.id(), incidentId, actor, "ROLLED_BACK", "execution", id, result);
            incidents.addEvent(incidentId, "REMEDIATION_ROLLED_BACK", Map.of("executionId", id.toString(),
                    "detail", String.valueOf(result.get("detail")), "reason", reason));
            events.emit("remediation.rolled_back", incidentId, Map.of("executionId", id));
        } catch (RuntimeException e) {
            repo.rolledBack(id, "ROLLBACK_FAILED", Map.of("error", String.valueOf(e.getMessage())));
            repo.audit(env.id(), incidentId, actor, "ROLLBACK_FAILED", "execution", id, Map.of("error", String.valueOf(e.getMessage())));
            escalate(env, incidentId, "ROLLBACK_FAILED", "Rolling back " + ex.get("actionId") + " failed: " + e.getMessage(), Map.of());
        }
    }

    // ── escalation ──────────────────────────────────────────────────────────────

    private void replanQuietly(UUID incidentId, String trigger) {
        try {
            plan(incidentId, trigger);
        } catch (RuntimeException e) {
            log.warn("Re-planning incident {} failed: {}", incidentId, e.getMessage());
        }
    }

    private void escalate(Environment env, UUID incidentId, String event, String message, Map<String, Object> detail) {
        Map<String, Object> incident = incidents.get(incidentId);
        if ("RESOLVED".equals(incident.get("status"))) return;
        incidents.setStatus(incidentId, "MANUAL_INTERVENTION");
        Map<String, Object> payload = new LinkedHashMap<>(detail);
        payload.put("message", message);
        incidents.addEvent(incidentId, event, payload);
        repo.audit(env.id(), incidentId, AUTONOMY, "ESCALATED", "incident", incidentId, payload);
        events.emit("remediation.escalated", incidentId, payload);
        log.info("Incident {} needs manual intervention: {}", incident.get("incidentKey"), message);
    }

    // ── inputs ──────────────────────────────────────────────────────────────────

    private Map<String, Object> latestAnalysis(UUID incidentId) {
        return db.queryForList("SELECT id FROM root_cause_analyses WHERE incident_id = ? ORDER BY completed_at DESC LIMIT 1",
                incidentId).stream().findFirst().orElse(null);
    }

    @SuppressWarnings("unchecked")
    List<Recommender.Candidate> candidates(UUID analysisId) {
        List<Recommender.Candidate> out = new ArrayList<>();
        for (Map<String, Object> row : db.queryForList(
                "SELECT signals::text AS detail FROM root_cause_candidates WHERE analysis_id = ? ORDER BY score DESC LIMIT ?",
                analysisId, CANDIDATES_CONSIDERED)) {
            Map<String, Object> c = repo.read((String) row.get("detail"), Map.class);
            out.add(candidate(c, out.size() + 1));
        }
        return out;
    }

    @SuppressWarnings("unchecked")
    static Recommender.Candidate candidate(Map<String, Object> c, int rank) {
        Set<String> metrics = new TreeSet<>();
        for (Map<String, Object> s : (List<Map<String, Object>>) c.getOrDefault("signals", List.of())) {
            Object prob = s.get("max_probability");
            if (prob instanceof Number n && n.doubleValue() >= 0.5) metrics.add(String.valueOf(s.get("metric")));
        }
        String rootVariable = String.valueOf(c.getOrDefault("root_variable", ""));
        String rootMetric = rootVariable.contains("|") ? rootVariable.substring(rootVariable.indexOf('|') + 1) : null;
        String unit = String.valueOf(c.get("service"));
        return new Recommender.Candidate(unit, String.valueOf(c.getOrDefault("kind", "service")),
                String.valueOf(c.getOrDefault("target", unit)),
                ((Number) c.getOrDefault("confidence", 0)).doubleValue(), metrics, rootMetric, rank);
    }

    /**
     * Counterfactual benefit per candidate, all computed over the same window so they are
     * comparable. The counterfactual stored with the RCA is only a fallback for the top cause.
     */
    @SuppressWarnings("unchecked")
    private Map<String, Recommender.Benefit> benefits(Environment env, Map<String, Object> incident,
                                                      List<Recommender.Candidate> candidates) {
        Map<String, Recommender.Benefit> out = new HashMap<>();
        UUID incidentId = (UUID) incident.get("id");
        Instant opened = ((Timestamp) incident.get("openedAt")).toInstant();
        String start = opened.minus(Duration.ofMinutes(5)).toString();
        String end = Instant.now().toString();
        for (Recommender.Candidate c : candidates) {
            try {
                Map<String, Object> cf = engine.post("/counterfactual", Map.of("environment_id", env.id().toString(),
                        "start", start, "end", end, "unit", c.unit()));
                // A response without validity or impact data is no estimate at all.
                if (cf != null && cf.containsKey("validity") && cf.containsKey("entry_impact")) out.put(c.unit(), benefit(cf));
            } catch (RuntimeException e) {
                log.debug("No counterfactual for {}: {}", c.unit(), e.getMessage());
            }
        }
        var stored = db.queryForList("SELECT target, result::text AS result FROM simulations WHERE incident_id = ? ORDER BY created_at DESC LIMIT 1",
                incidentId);
        if (!stored.isEmpty()) {
            out.putIfAbsent((String) stored.get(0).get("target"), benefit(repo.read((String) stored.get(0).get("result"), Map.class)));
        }
        return out;
    }

    @SuppressWarnings("unchecked")
    static Recommender.Benefit benefit(Map<String, Object> cf) {
        Map<String, Map<String, Object>> impact = (Map<String, Map<String, Object>>) cf.getOrDefault("entry_impact", Map.of());
        Double mean = null, peak = null, err = null;
        for (Map<String, Object> i : impact.values()) {
            mean = add(mean, i.get("mean_avoided_latency_ms"));
            peak = max(peak, i.get("peak_avoided_latency_ms"));
            err = max(err, i.get("peak_avoided_error_pct"));
        }
        List<?> restored = (List<?>) cf.getOrDefault("restored_nodes", List.of());
        Map<String, Object> validity = (Map<String, Object>) cf.getOrDefault("validity", Map.of());
        return new Recommender.Benefit(mean, peak, err, restored.size(), String.valueOf(validity.getOrDefault("status", "UNKNOWN")));
    }

    private static Double add(Double acc, Object v) {
        return v instanceof Number n ? (acc == null ? 0 : acc) + n.doubleValue() : acc;
    }

    private static Double max(Double acc, Object v) {
        return v instanceof Number n ? (acc == null ? n.doubleValue() : Math.max(acc, n.doubleValue())) : acc;
    }

    /** Rebuilds the proposal behind a stored recommendation (for re-evaluating the policy at approval time). */
    @SuppressWarnings("unchecked")
    private Recommender.Proposal proposalOf(Environment env, Map<String, Object> rec) {
        var action = env.config().remediation().actions().stream().filter(a -> a.id().equals(rec.get("actionId"))).findFirst()
                .orElseThrow(() -> new IllegalStateException("Action " + rec.get("actionId") + " is no longer in the catalog"));
        Map<String, Object> b = repo.read((String) rec.get("expectedBenefit"), Map.class);
        Map<String, Object> e = repo.read((String) rec.get("effectiveness"), Map.class);
        String kind = (String) rec.get("targetKind");
        String unit = (String) rec.get("targetNode");
        String target = "link".equals(kind) && unit.contains("->") ? unit.substring(unit.indexOf("->") + 2) : unit;
        var original = rec.get("analysisId") == null ? null : candidates((UUID) rec.get("analysisId")).stream()
                .filter(c -> c.unit().equals(unit)).findFirst().orElse(null);
        var candidate = new Recommender.Candidate(unit, kind, target, ((Number) rec.get("rcaConfidence")).doubleValue(),
                Set.of(), original == null ? null : original.rootMetric(), original == null ? CANDIDATES_CONSIDERED : original.rank());
        var benefit = new Recommender.Benefit(num(b.get("meanAvoidedLatencyMs")), num(b.get("peakAvoidedLatencyMs")),
                num(b.get("peakAvoidedErrorPct")), ((Number) b.getOrDefault("restoredNodes", 0)).intValue(),
                String.valueOf(b.get("validity")));
        var eff = new Recommender.Effectiveness(((Number) e.get("successes")).intValue(), ((Number) e.get("attempts")).intValue());
        return new Recommender.Proposal(candidate, action, (String) rec.get("binding"), (Boolean) rec.get("reversible"),
                EnvironmentConfig.STATELESS_OPERATIONS.contains(action.operation()), benefit, eff, 0,
                ((Number) rec.get("score")).doubleValue(), (String) rec.get("rationale"));
    }

    private static Double num(Object o) {
        return o instanceof Number n ? n.doubleValue() : null;
    }

    static Map<String, Object> policyJson(RemediationPolicy.Decision d) {
        return Map.of("mode", d.mode().name(), "summary", d.summary(), "rules", d.rules().stream().map(r -> Map.of(
                "id", r.id(), "safety", r.safety(), "passed", r.passed(), "detail", r.detail())).toList());
    }

    /** Raised when the policy blocks an approved action; carries the rule evaluation. */
    public static class PolicyViolation extends RuntimeException {
        private final Map<String, Object> policy;

        public PolicyViolation(String message, Map<String, Object> policy) {
            super(message);
            this.policy = policy;
        }

        public Map<String, Object> policy() {
            return policy;
        }
    }
}

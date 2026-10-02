package com.causalops.api.remediation;

import com.causalops.api.environment.Environment;
import com.causalops.api.environment.EnvironmentService;
import org.springframework.web.bind.annotation.*;

import java.util.*;

/**
 * Remediation API: recommendations with their policy evaluation, approvals, executions with
 * verification evidence, manual rollback, autonomy settings, the audit trail and outcome metrics.
 */
@RestController
@RequestMapping("/api/remediation")
public class RemediationController {

    private static final List<String> JSON_FIELDS = List.of("params", "expectedBenefit", "effectiveness", "policy",
            "result", "rollbackState", "verification", "rollbackResult", "detail");

    private final RemediationService remediation;
    private final RemediationRepository repo;
    private final RemediationMetrics metrics;
    private final EnvironmentService environments;

    public RemediationController(RemediationService remediation, RemediationRepository repo, RemediationMetrics metrics,
                                 EnvironmentService environments) {
        this.remediation = remediation;
        this.repo = repo;
        this.metrics = metrics;
        this.environments = environments;
    }

    public record Decision(String by, String reason) {
    }

    public record Autonomy(Integer autoExecuteMaxTier, Boolean killSwitch, Boolean dryRun, String changedBy) {
    }

    @GetMapping("/recommendations")
    public List<Map<String, Object>> recommendations(@RequestParam(required = false) UUID environmentId,
                                                     @RequestParam(required = false) UUID incidentId,
                                                     @RequestParam(required = false) String status,
                                                     @RequestParam(defaultValue = "100") int limit) {
        Environment env = environments.resolve(environmentId);
        return parsed(repo.recommendations(env.id(), incidentId, status, Math.min(Math.max(limit, 1), 500)));
    }

    @GetMapping("/recommendations/{id}")
    public Map<String, Object> recommendation(@PathVariable UUID id) {
        return parsed(repo.recommendation(id));
    }

    /** Approves a pending recommendation; the safety rules are re-checked and the action runs. */
    @PostMapping("/recommendations/{id}/approve")
    public Map<String, Object> approve(@PathVariable UUID id, @RequestBody Decision body) {
        return parsed(remediation.approve(id, body.by(), body.reason()));
    }

    @PostMapping("/recommendations/{id}/reject")
    public Map<String, Object> reject(@PathVariable UUID id, @RequestBody Decision body) {
        return parsed(remediation.reject(id, body.by(), body.reason()));
    }

    /** Re-plans an incident now, e.g. after enabling an executor or changing the autonomy settings. */
    @PostMapping("/incidents/{incidentId}/plan")
    public List<Map<String, Object>> plan(@PathVariable UUID incidentId) {
        return parsed(remediation.plan(incidentId, "operator"));
    }

    @GetMapping("/executions")
    public List<Map<String, Object>> executions(@RequestParam(required = false) UUID environmentId,
                                                @RequestParam(required = false) UUID incidentId,
                                                @RequestParam(defaultValue = "100") int limit) {
        Environment env = environments.resolve(environmentId);
        return parsed(repo.executions(env.id(), incidentId, Math.min(Math.max(limit, 1), 500)));
    }

    @GetMapping("/executions/{id}")
    public Map<String, Object> execution(@PathVariable UUID id) {
        return parsed(repo.execution(id));
    }

    @PostMapping("/executions/{id}/rollback")
    public Map<String, Object> rollback(@PathVariable UUID id, @RequestBody Decision body) {
        return parsed(remediation.manualRollback(id, body.by(), body.reason()));
    }

    /** Effective autonomy settings, the action catalog and which executors are enabled. */
    @GetMapping("/policy")
    public Map<String, Object> policy(@RequestParam(required = false) UUID environmentId) {
        Environment env = environments.resolve(environmentId);
        var r = env.config().remediation();
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("environmentId", env.id());
        out.put("environmentStatus", env.status());
        out.put("globalKillSwitch", remediation.globalKillSwitch());
        out.put("settings", r);
        out.put("enabledExecutors", r.executors().keySet().stream().filter(r::executorEnabled).sorted().toList());
        return out;
    }

    /** Changes the autonomy level, kill switch or dry-run flag; recorded in the audit log. */
    @PutMapping("/autonomy")
    public Map<String, Object> autonomy(@RequestParam(required = false) UUID environmentId, @RequestBody Autonomy body) {
        if (body.changedBy() == null || body.changedBy().isBlank()) throw new IllegalArgumentException("changedBy is required");
        Environment env = environments.resolve(environmentId);
        var r = env.config().remediation();
        var updated = r.withAutonomy(body.autoExecuteMaxTier() == null ? r.autoExecuteMaxTier() : body.autoExecuteMaxTier(),
                body.killSwitch() == null ? r.killSwitch() : body.killSwitch(),
                body.dryRun() == null ? r.dryRun() : body.dryRun());
        environments.updateConfig(env.id(), env.config().withRemediation(updated));
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("before", Map.of("autoExecuteMaxTier", r.autoExecuteMaxTier(), "killSwitch", r.killSwitch(), "dryRun", r.dryRun()));
        detail.put("after", Map.of("autoExecuteMaxTier", updated.autoExecuteMaxTier(), "killSwitch", updated.killSwitch(),
                "dryRun", updated.dryRun()));
        repo.audit(env.id(), null, body.changedBy(), "AUTONOMY_CHANGED", "environment", env.id(), detail);
        return policy(env.id());
    }

    @GetMapping("/audit")
    public List<Map<String, Object>> audit(@RequestParam(required = false) UUID environmentId,
                                           @RequestParam(required = false) UUID incidentId,
                                           @RequestParam(defaultValue = "200") int limit) {
        Environment env = environments.resolve(environmentId);
        return parsed(repo.auditTrail(env.id(), incidentId, Math.min(Math.max(limit, 1), 1000)));
    }

    /** MTTD, time to mitigation, MTTR and downtime per incident and per way it was handled. */
    @GetMapping("/metrics")
    public Map<String, Object> metrics(@RequestParam(required = false) UUID environmentId,
                                       @RequestParam(defaultValue = "200") int limit) {
        return metrics.compute(environments.resolve(environmentId), Math.min(Math.max(limit, 1), 1000));
    }

    private List<Map<String, Object>> parsed(List<Map<String, Object>> rows) {
        return rows.stream().map(this::parsed).toList();
    }

    private Map<String, Object> parsed(Map<String, Object> row) {
        Map<String, Object> out = new LinkedHashMap<>(row);
        for (String f : JSON_FIELDS) {
            if (out.get(f) instanceof String s) out.put(f, repo.read(s, Object.class));
        }
        return out;
    }
}

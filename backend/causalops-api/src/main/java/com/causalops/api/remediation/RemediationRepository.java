package com.causalops.api.remediation;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.*;

/** Recommendations, approvals, executions and the audit log. Plain JDBC, parameterized SQL. */
@Repository
public class RemediationRepository {

    static final List<String> IN_FLIGHT = List.of("EXECUTING", "VERIFYING");
    static final List<String> FINISHED = List.of("VERIFIED", "FAILED", "ROLLED_BACK", "ROLLBACK_FAILED");

    private static final String REC_SELECT = """
            SELECT id, environment_id AS "environmentId", incident_id AS "incidentId", analysis_id AS "analysisId", rank,
                   action_id AS "actionId", action_name AS "actionName", executor, operation, target_node AS "targetNode",
                   target_kind AS "targetKind", binding, tier, reversible, params::text AS params,
                   rca_confidence AS "rcaConfidence", expected_benefit::text AS "expectedBenefit",
                   effectiveness::text AS effectiveness, score, rationale, status, policy::text AS policy,
                   created_at AS "createdAt", expires_at AS "expiresAt"
            FROM remediation_recommendations
            """;

    private static final String EXEC_SELECT = """
            SELECT id, environment_id AS "environmentId", incident_id AS "incidentId", recommendation_id AS "recommendationId",
                   action_id AS "actionId", executor, operation, target_node AS "targetNode", binding,
                   root_metric AS "rootMetric", mode, approved_by AS "approvedBy", dry_run AS "dryRun", status,
                   started_at AS "startedAt", executed_at AS "executedAt", verify_after AS "verifyAfter",
                   verify_deadline AS "verifyDeadline", healthy_streak AS "healthyStreak", finished_at AS "finishedAt",
                   result::text AS result, rollback_state::text AS "rollbackState", verification::text AS verification,
                   rollback_result::text AS "rollbackResult"
            FROM remediation_executions
            """;

    private final JdbcTemplate db;
    private final ObjectMapper json;

    public RemediationRepository(JdbcTemplate db, ObjectMapper json) {
        this.db = db;
        this.json = json;
    }

    // ── recommendations ────────────────────────────────────────────────────────
    public UUID insertRecommendation(UUID env, UUID incident, UUID analysis, int rank, Recommender.Proposal p,
                                     String status, Object policy, Instant expiresAt) {
        var a = p.action();
        return db.queryForObject("""
                INSERT INTO remediation_recommendations (environment_id, incident_id, analysis_id, rank, action_id, action_name,
                    executor, operation, target_node, target_kind, binding, tier, reversible, params, rca_confidence,
                    expected_benefit, effectiveness, score, rationale, status, policy, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS jsonb), ?, CAST(? AS jsonb), CAST(? AS jsonb), ?, ?, ?,
                        CAST(? AS jsonb), ?)
                RETURNING id
                """, UUID.class, env, incident, analysis, rank, a.id(), a.name(), a.executor(), a.operation(),
                p.candidate().unit(), p.candidate().kind(), p.binding(), a.tier(), p.reversible(), write(a.params()),
                p.candidate().confidence(), write(p.benefit()),
                write(Map.of("successes", p.effectiveness().successes(), "attempts", p.effectiveness().attempts(),
                        "rate", p.effectiveness().rate())), p.score(), p.rationale(), status, write(policy),
                Timestamp.from(expiresAt));
    }

    public void setRecommendationStatus(UUID id, String status, Object policy) {
        if (policy == null) db.update("UPDATE remediation_recommendations SET status = ? WHERE id = ?", status, id);
        else db.update("UPDATE remediation_recommendations SET status = ?, policy = CAST(? AS jsonb) WHERE id = ?",
                status, write(policy), id);
    }

    /** Pending proposals of an incident are replaced when its root cause is re-analysed. */
    public int supersedePending(UUID incident) {
        return db.update("""
                UPDATE remediation_recommendations SET status = 'SUPERSEDED'
                WHERE incident_id = ? AND status IN ('PROPOSED', 'AWAITING_APPROVAL', 'BLOCKED')
                """, incident);
    }

    public Map<String, Object> recommendation(UUID id) {
        return db.queryForList(REC_SELECT + " WHERE id = ?", id).stream().findFirst()
                .orElseThrow(() -> new NoSuchElementException("Unknown recommendation " + id));
    }

    public List<Map<String, Object>> recommendations(UUID env, UUID incident, String status, int limit) {
        StringBuilder sql = new StringBuilder(REC_SELECT).append(" WHERE environment_id = ?");
        List<Object> args = new ArrayList<>(List.of(env));
        if (incident != null) { sql.append(" AND incident_id = ?"); args.add(incident); }
        if (status != null) { sql.append(" AND status = ?"); args.add(status); }
        sql.append(" ORDER BY created_at DESC, rank LIMIT ?");
        args.add(limit);
        return db.queryForList(sql.toString(), args.toArray());
    }

    /** Actions already executed or rejected for this incident, on this target. */
    public boolean alreadyTried(UUID incident, String actionId, String target) {
        return Boolean.TRUE.equals(db.queryForObject("""
                SELECT EXISTS (SELECT 1 FROM remediation_recommendations
                               WHERE incident_id = ? AND action_id = ? AND target_node = ? AND status IN ('EXECUTED', 'REJECTED'))
                """, Boolean.class, incident, actionId, target));
    }

    // ── approvals ───────────────────────────────────────────────────────────────
    public void insertDecision(UUID recommendation, String decision, String by, String reason) {
        db.update("INSERT INTO remediation_approvals (recommendation_id, decision, decided_by, reason) VALUES (?, ?, ?, ?)",
                recommendation, decision, by, reason);
    }

    public void consumeApproval(UUID recommendation) {
        db.update("UPDATE remediation_approvals SET consumed_at = now() WHERE recommendation_id = ? AND decision = 'APPROVED'",
                recommendation);
    }

    // ── executions ──────────────────────────────────────────────────────────────
    public UUID insertExecution(Map<String, Object> rec, String rootMetric, String mode, String approvedBy, boolean dryRun) {
        return db.queryForObject("""
                INSERT INTO remediation_executions (environment_id, incident_id, recommendation_id, action_id, executor, operation,
                    target_node, binding, root_metric, mode, approved_by, dry_run, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'EXECUTING') RETURNING id
                """, UUID.class, rec.get("environmentId"), rec.get("incidentId"), rec.get("id"), rec.get("actionId"),
                rec.get("executor"), rec.get("operation"), rec.get("targetNode"), rec.get("binding"), rootMetric, mode,
                approvedBy, dryRun);
    }

    public void executed(UUID id, Map<String, Object> result, Object rollbackState, Instant verifyAfter, Instant deadline) {
        db.update("""
                UPDATE remediation_executions SET status = 'VERIFYING', executed_at = now(), result = CAST(? AS jsonb),
                    rollback_state = CAST(? AS jsonb), verify_after = ?, verify_deadline = ?
                WHERE id = ?
                """, write(result), rollbackState == null ? null : write(rollbackState), Timestamp.from(verifyAfter),
                Timestamp.from(deadline), id);
    }

    public void finish(UUID id, String status, Map<String, Object> result) {
        db.update("UPDATE remediation_executions SET status = ?, finished_at = now(), result = result || CAST(? AS jsonb) WHERE id = ?",
                status, write(result), id);
    }

    public void verificationProgress(UUID id, int streak, Map<String, Object> verification) {
        db.update("UPDATE remediation_executions SET healthy_streak = ?, verification = CAST(? AS jsonb) WHERE id = ?",
                streak, write(verification), id);
    }

    public void verified(UUID id, Map<String, Object> verification) {
        db.update("""
                UPDATE remediation_executions SET status = 'VERIFIED', finished_at = now(), verification = CAST(? AS jsonb)
                WHERE id = ?
                """, write(verification), id);
    }

    public void verificationFailed(UUID id, Map<String, Object> verification) {
        db.update("""
                UPDATE remediation_executions SET status = 'FAILED', finished_at = now(), verification = CAST(? AS jsonb)
                WHERE id = ?
                """, write(verification), id);
    }

    public void rolledBack(UUID id, String status, Map<String, Object> rollbackResult) {
        db.update("UPDATE remediation_executions SET status = ?, rollback_result = CAST(? AS jsonb) WHERE id = ?",
                status, write(rollbackResult), id);
    }

    public Map<String, Object> execution(UUID id) {
        return db.queryForList(EXEC_SELECT + " WHERE id = ?", id).stream().findFirst()
                .orElseThrow(() -> new NoSuchElementException("Unknown execution " + id));
    }

    public List<Map<String, Object>> executions(UUID env, UUID incident, int limit) {
        if (incident != null) {
            return db.queryForList(EXEC_SELECT + " WHERE environment_id = ? AND incident_id = ? ORDER BY started_at DESC LIMIT ?",
                    env, incident, limit);
        }
        return db.queryForList(EXEC_SELECT + " WHERE environment_id = ? ORDER BY started_at DESC LIMIT ?", env, limit);
    }

    public List<Map<String, Object>> verifying(UUID env) {
        return db.queryForList(EXEC_SELECT + " WHERE environment_id = ? AND status = 'VERIFYING' AND verify_after <= now()", env);
    }

    public boolean incidentHasChangeInFlight(UUID incident) {
        return Boolean.TRUE.equals(db.queryForObject(
                "SELECT EXISTS (SELECT 1 FROM remediation_executions WHERE incident_id = ? AND status IN ('EXECUTING', 'VERIFYING'))",
                Boolean.class, incident));
    }

    public boolean targetBusy(UUID env, String target) {
        return Boolean.TRUE.equals(db.queryForObject(
                "SELECT EXISTS (SELECT 1 FROM remediation_executions WHERE environment_id = ? AND target_node = ? AND status IN ('EXECUTING', 'VERIFYING'))",
                Boolean.class, env, target));
    }

    public Instant lastChange(UUID env, String target) {
        Timestamp t = db.queryForObject("""
                SELECT max(executed_at) FROM remediation_executions
                WHERE environment_id = ? AND target_node = ? AND NOT dry_run AND executed_at IS NOT NULL
                """, Timestamp.class, env, target);
        return t == null ? null : t.toInstant();
    }

    public int autoExecutionsLastHour(UUID env) {
        Integer n = db.queryForObject("""
                SELECT count(*) FROM remediation_executions
                WHERE environment_id = ? AND mode = 'AUTO' AND NOT dry_run AND started_at > now() - interval '1 hour'
                """, Integer.class, env);
        return n == null ? 0 : n;
    }

    /** Verified vs. finished real executions of an action for one kind of deviation in this environment. */
    public Recommender.Effectiveness effectiveness(UUID env, String actionId, String rootMetric) {
        return db.queryForObject("""
                SELECT count(*) FILTER (WHERE status = 'VERIFIED') AS s, count(*) AS n FROM remediation_executions
                WHERE environment_id = ? AND action_id = ? AND root_metric IS NOT DISTINCT FROM ? AND NOT dry_run
                  AND status IN ('VERIFIED', 'FAILED', 'ROLLED_BACK', 'ROLLBACK_FAILED')
                """, (rs, i) -> new Recommender.Effectiveness(rs.getInt("s"), rs.getInt("n")), env, actionId, rootMetric);
    }

    // ── audit ───────────────────────────────────────────────────────────────────
    public void audit(UUID env, UUID incident, String actor, String action, String entityType, UUID entityId, Object detail) {
        db.update("""
                INSERT INTO audit_log (environment_id, incident_id, actor, action, entity_type, entity_id, detail)
                VALUES (?, ?, ?, ?, ?, ?, CAST(? AS jsonb))
                """, env, incident, actor, action, entityType, entityId, write(detail));
    }

    public List<Map<String, Object>> auditTrail(UUID env, UUID incident, int limit) {
        String select = """
                SELECT id, at, environment_id AS "environmentId", incident_id AS "incidentId", actor, action,
                       entity_type AS "entityType", entity_id AS "entityId", detail::text AS detail FROM audit_log
                """;
        if (incident != null) return db.queryForList(select + " WHERE incident_id = ? ORDER BY at, id LIMIT ?", incident, limit);
        return db.queryForList(select + " WHERE environment_id = ? ORDER BY at DESC, id DESC LIMIT ?", env, limit);
    }

    // ── measurements used by the policy ─────────────────────────────────────────
    public Double telemetryAgeSeconds(UUID env) {
        return db.queryForObject("SELECT extract(epoch FROM now() - max(captured_at)) FROM telemetry_snapshots WHERE environment_id = ?",
                Double.class, env);
    }

    /** Latest measured request rate per node (last minute). */
    public Map<String, Double> latestRequestRates(UUID env) {
        Map<String, Double> out = new HashMap<>();
        db.query("""
                SELECT DISTINCT ON (service_name) service_name, request_rate FROM telemetry_snapshots
                WHERE environment_id = ? AND captured_at > now() - interval '60 seconds' AND request_rate IS NOT NULL
                ORDER BY service_name, captured_at DESC
                """, rs -> { out.put(rs.getString(1), rs.getDouble(2)); }, env);
        return out;
    }

    public List<String[]> edges(UUID env) {
        return db.query("SELECT source_service, target_service FROM dependencies WHERE environment_id = ?",
                (rs, i) -> new String[]{rs.getString(1), rs.getString(2)}, env);
    }

    String write(Object o) {
        try {
            return json.writeValueAsString(o);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("Cannot serialize remediation data", e);
        }
    }

    <T> T read(String raw, Class<T> type) {
        try {
            return raw == null ? null : json.readValue(raw, type);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("Stored remediation data is not valid JSON", e);
        }
    }
}

package com.causalops.api.changes;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * Changes made to a monitored system: deployments, restarts, configuration, maintenance and
 * remediation actions. Calibration keeps their windows out of "normal" behaviour and out of the
 * false-positive evaluation; they are not labelled incidents.
 */
@Repository
public class ChangeEventRepository {

    public static final Set<String> KINDS = Set.of("DEPLOYMENT", "RESTART", "CONFIG", "MAINTENANCE", "REMEDIATION", "OTHER");

    private final JdbcTemplate db;

    public ChangeEventRepository(JdbcTemplate db) {
        this.db = db;
    }

    public UUID record(UUID env, String kind, String target, Instant startedAt, Instant endedAt, String source,
                       String description, UUID referenceId) {
        if (!KINDS.contains(kind)) throw new IllegalArgumentException("kind must be one of " + KINDS);
        if (startedAt == null) throw new IllegalArgumentException("startedAt is required");
        if (endedAt != null && endedAt.isBefore(startedAt)) throw new IllegalArgumentException("endedAt is before startedAt");
        if (source == null || source.isBlank() || description == null || description.isBlank()) {
            throw new IllegalArgumentException("source and description are required");
        }
        return db.queryForObject("""
                INSERT INTO change_events (environment_id, kind, target, started_at, ended_at, source, description, reference_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
                """, UUID.class, env, kind, target, Timestamp.from(startedAt), endedAt == null ? null : Timestamp.from(endedAt),
                source, description, referenceId);
    }

    /** Closes the open change recorded for a reference (e.g. a remediation execution). */
    public void close(UUID referenceId) {
        db.update("UPDATE change_events SET ended_at = now() WHERE reference_id = ? AND ended_at IS NULL", referenceId);
    }

    /**
     * Changes in progress (or ended less than {@code settleSeconds} ago). An open-ended change
     * counts for 10 minutes. Remediation changes are excluded when {@code includeRemediation} is false.
     */
    public List<Map<String, Object>> active(UUID env, int settleSeconds, boolean includeRemediation) {
        return db.queryForList("""
                SELECT id, kind, target, started_at AS "startedAt", ended_at AS "endedAt", source, description
                FROM change_events
                WHERE environment_id = ? AND started_at <= now()
                  AND coalesce(ended_at, started_at + interval '10 minutes') >= now() - make_interval(secs => ?)
                  AND (? OR kind <> 'REMEDIATION')
                ORDER BY started_at DESC
                """, env, settleSeconds, includeRemediation);
    }

    public List<Map<String, Object>> list(UUID env, Instant from, Instant to, int limit) {
        return db.queryForList("""
                SELECT id, kind, target, started_at AS "startedAt", ended_at AS "endedAt", source, description,
                       reference_id AS "referenceId", created_at AS "createdAt"
                FROM change_events
                WHERE environment_id = ? AND started_at <= ? AND coalesce(ended_at, started_at) >= ?
                ORDER BY started_at DESC LIMIT ?
                """, env, Timestamp.from(to), Timestamp.from(from), limit);
    }
}

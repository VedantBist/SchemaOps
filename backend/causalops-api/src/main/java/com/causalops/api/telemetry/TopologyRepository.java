package com.causalops.api.telemetry;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Map;
import java.util.UUID;

/** Services (nodes) and dependencies (edges) of an environment. */
@Repository
public class TopologyRepository {

    private final JdbcTemplate db;

    public TopologyRepository(JdbcTemplate db) {
        this.db = db;
    }

    public record Node(String name, String kind, String source) {
    }

    public List<Node> nodes(UUID env) {
        return db.query("SELECT name, kind, source FROM services WHERE environment_id = ? ORDER BY name",
                (rs, i) -> new Node(rs.getString("name"), rs.getString("kind"), rs.getString("source")), env);
    }

    /** Inserts a discovered node or refreshes last_seen_at; manual nodes keep their declared kind. Returns true if new. */
    public boolean upsertDiscoveredNode(UUID env, String name, String kind) {
        Boolean inserted = db.queryForObject("""
                INSERT INTO services (environment_id, name, type, kind, source, status, last_seen_at)
                VALUES (?, ?, ?, ?, 'discovered', 'unknown', now())
                ON CONFLICT (environment_id, name) DO UPDATE SET
                    last_seen_at = now(),
                    kind = CASE WHEN services.source = 'manual' THEN services.kind ELSE EXCLUDED.kind END,
                    type = CASE WHEN services.source = 'manual' THEN services.type ELSE EXCLUDED.type END
                RETURNING (xmax = 0)
                """, Boolean.class, env, name, kind, kind);
        return Boolean.TRUE.equals(inserted);
    }

    /** Inserts or refreshes a discovered call edge with its observed traffic. Returns true if new. */
    public boolean upsertDiscoveredEdge(UUID env, String client, String server, String connectionType,
                                        double callRate, double failedRate) {
        Boolean inserted = db.queryForObject("""
                INSERT INTO dependencies (environment_id, source_service, target_service, direction, source,
                                          connection_type, call_rate, failed_rate, last_seen_at)
                VALUES (?, ?, ?, 'CALLS', 'discovered', ?, ?, ?, now())
                ON CONFLICT (environment_id, source_service, target_service) DO UPDATE SET
                    connection_type = EXCLUDED.connection_type,
                    call_rate = EXCLUDED.call_rate,
                    failed_rate = EXCLUDED.failed_rate,
                    last_seen_at = now()
                RETURNING (xmax = 0)
                """, Boolean.class, env, client, server, connectionType, callRate, failedRate);
        return Boolean.TRUE.equals(inserted);
    }

    /** Discovered nodes not seen within the stale window are marked inactive (kept for history). */
    public int markStaleNodes(UUID env, int staleAfterSeconds) {
        return db.update("""
                UPDATE services SET status = 'inactive', updated_at = now()
                WHERE environment_id = ? AND source = 'discovered' AND status <> 'inactive'
                  AND last_seen_at < now() - make_interval(secs => ?)
                """, env, staleAfterSeconds);
    }

    public List<Map<String, Object>> services(UUID env, int staleAfterSeconds) {
        return db.queryForList("""
                SELECT id, name, kind, type, source, status, current_latency AS "latencyP99", error_rate AS "errorRate",
                       request_rate AS "requestRate", baseline_latency AS "baselineLatency",
                       first_seen_at AS "firstSeenAt", last_seen_at AS "lastSeenAt", last_sample_at AS "lastSampleAt",
                       (last_seen_at IS NULL OR last_seen_at < now() - make_interval(secs => ?)) AS stale
                FROM services WHERE environment_id = ? ORDER BY name
                """, staleAfterSeconds, env);
    }

    public List<Map<String, Object>> edges(UUID env, int staleAfterSeconds) {
        return db.queryForList("""
                SELECT source_service AS source, target_service AS target, direction, source AS origin,
                       connection_type AS "connectionType", call_rate AS "callRate", failed_rate AS "failedRate",
                       first_seen_at AS "firstSeenAt", last_seen_at AS "lastSeenAt",
                       (last_seen_at IS NULL OR last_seen_at < now() - make_interval(secs => ?)) AS stale
                FROM dependencies WHERE environment_id = ? ORDER BY source_service, target_service
                """, staleAfterSeconds, env);
    }

    public void addManualEdge(UUID env, String client, String server) {
        db.update("""
                INSERT INTO dependencies (environment_id, source_service, target_service, direction, source)
                VALUES (?, ?, ?, 'CALLS', 'manual')
                ON CONFLICT (environment_id, source_service, target_service) DO UPDATE SET source = 'manual'
                """, env, client, server);
    }

    public void addManualNode(UUID env, String name, String kind) {
        db.update("""
                INSERT INTO services (environment_id, name, type, kind, source, status)
                VALUES (?, ?, ?, ?, 'manual', 'unknown')
                ON CONFLICT (environment_id, name) DO UPDATE SET source = 'manual', kind = EXCLUDED.kind, type = EXCLUDED.type
                """, env, name, kind, kind);
    }
}

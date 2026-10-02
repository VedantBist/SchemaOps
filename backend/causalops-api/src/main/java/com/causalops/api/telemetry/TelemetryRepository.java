package com.causalops.api.telemetry;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Repository
public class TelemetryRepository {

    private final JdbcTemplate db;

    public TelemetryRepository(JdbcTemplate db) {
        this.db = db;
    }

    public void insertSnapshot(UUID env, Instant at, NodeMeasurement m) {
        db.update("""
                INSERT INTO telemetry_snapshots (environment_id, service_name, captured_at, p50_latency, p95_latency,
                    p99_latency, error_rate, request_rate, db_latency, pool_utilization, pool_pending)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, env, m.name(), Timestamp.from(at), m.get("latencyP50"), m.get("latencyP95"), m.get("latencyP99"),
                m.get("errorRatePct"), m.get("requestRate"), m.get("dbLatencyP95"),
                m.get("poolUtilizationPct"), m.get("poolPending"));
    }

    public void updateServiceState(UUID env, String name, NodeMeasurement m, String status, Instant at) {
        db.update("""
                UPDATE services SET current_latency = ?, error_rate = ?, request_rate = ?, status = ?,
                                    last_sample_at = ?, updated_at = now()
                WHERE environment_id = ? AND name = ?
                """, m.get("latencyP99"), m.get("errorRatePct"), m.get("requestRate"), status,
                Timestamp.from(at), env, name);
    }

    /** Baseline = median of observed p99 over the trailing window, once enough samples exist. */
    public void refreshObservedBaselines(UUID env, int windowHours, int minSamples) {
        db.update("""
                UPDATE services s SET baseline_latency = b.median_p99
                FROM (SELECT service_name, percentile_cont(0.5) WITHIN GROUP (ORDER BY p99_latency) AS median_p99
                      FROM telemetry_snapshots
                      WHERE environment_id = ? AND p99_latency IS NOT NULL
                        AND captured_at > now() - make_interval(hours => ?)
                      GROUP BY service_name HAVING count(*) >= ?) b
                WHERE s.environment_id = ? AND s.name = b.service_name
                """, env, windowHours, minSamples, env);
    }

    public List<Map<String, Object>> series(UUID env, String service, Instant since, int limit) {
        String sql = """
                SELECT service_name AS service, captured_at AS timestamp, p50_latency AS "p50Latency",
                       p95_latency AS "p95Latency", p99_latency AS "p99Latency", error_rate AS "errorRate",
                       request_rate AS "requestRate", db_latency AS "dbLatency", pool_utilization AS "poolUtilization",
                       pool_pending AS "poolPending", anomaly_score AS "anomalyScore"
                FROM telemetry_snapshots
                WHERE environment_id = ? AND captured_at >= ? %s
                ORDER BY captured_at DESC LIMIT ?
                """;
        return service == null
                ? db.queryForList(sql.formatted(""), env, Timestamp.from(since), limit)
                : db.queryForList(sql.formatted("AND service_name = ?"), env, Timestamp.from(since), service, limit);
    }

    /** Deletes raw snapshots older than the retention period. */
    public int purgeOlderThan(int days) {
        return db.update("DELETE FROM telemetry_snapshots WHERE captured_at < now() - make_interval(days => ?)", days);
    }
}

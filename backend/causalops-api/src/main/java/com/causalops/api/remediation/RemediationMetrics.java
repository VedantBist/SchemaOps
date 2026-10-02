package com.causalops.api.remediation;

import com.causalops.api.environment.Environment;
import com.causalops.api.telemetry.SloEvaluator;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.*;

/**
 * Outcome metrics from real timestamps and stored telemetry, per incident and per way it was handled:
 * <ul>
 *   <li><b>MTTD</b>: incident opened minus fault start, only where the fault start is known (an injected fault);</li>
 *   <li><b>time to mitigation</b>: first verified remediation minus incident opened;</li>
 *   <li><b>MTTR</b>: incident resolved (recovery confirmed by the detector) minus opened;</li>
 *   <li><b>downtime</b>: seconds during the incident in which an entry service (where user traffic
 *       arrives) violated its SLO, from the stored samples.</li>
 * </ul>
 */
@Service
public class RemediationMetrics {

    private final JdbcTemplate db;
    private final RemediationRepository repo;
    private final double sampleSeconds;

    public RemediationMetrics(JdbcTemplate db, RemediationRepository repo,
                              @Value("${causalops.telemetry.ingest-interval-ms:5000}") long ingestIntervalMs) {
        this.db = db;
        this.repo = repo;
        this.sampleSeconds = ingestIntervalMs / 1000.0;
    }

    public Map<String, Object> compute(Environment env, int limit) {
        List<Map<String, Object>> rows = db.queryForList("""
                SELECT i.id, i.incident_key AS "incidentKey", i.opened_at AS "openedAt", i.resolved_at AS "resolvedAt",
                       i.status, i.detection_source AS "detectionSource",
                       (SELECT min(f.started_at) FROM fault_injections f
                         WHERE f.environment_id = i.environment_id
                           AND f.started_at BETWEEN i.opened_at - interval '10 minutes' AND i.opened_at) AS "faultStartedAt",
                       (SELECT e.mode FROM remediation_executions e WHERE e.incident_id = i.id AND e.status = 'VERIFIED'
                           AND NOT e.dry_run ORDER BY e.finished_at LIMIT 1) AS "verifiedMode",
                       (SELECT min(e.finished_at) FROM remediation_executions e WHERE e.incident_id = i.id
                           AND e.status = 'VERIFIED' AND NOT e.dry_run) AS "mitigatedAt",
                       (SELECT count(*) FROM remediation_executions e WHERE e.incident_id = i.id AND NOT e.dry_run) AS actions
                FROM incidents i WHERE i.environment_id = ? ORDER BY i.opened_at DESC LIMIT ?
                """, env.id(), limit);
        List<String> entries = entryServices(env);
        List<Map<String, Object>> incidents = new ArrayList<>();
        Map<String, List<Map<String, Object>>> byHandling = new TreeMap<>();
        for (Map<String, Object> r : rows) {
            Instant opened = ts(r.get("openedAt"));
            Instant resolved = ts(r.get("resolvedAt"));
            Instant fault = ts(r.get("faultStartedAt"));
            Instant mitigated = ts(r.get("mitigatedAt"));
            String handling = r.get("verifiedMode") == null
                    ? (((Number) r.get("actions")).intValue() > 0 ? "REMEDIATION_UNSUCCESSFUL" : "NOT_REMEDIATED")
                    : "AUTO".equals(r.get("verifiedMode")) ? "AUTO_REMEDIATED" : "APPROVED_REMEDIATION";
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("incidentId", r.get("id"));
            out.put("incidentKey", r.get("incidentKey"));
            out.put("status", r.get("status"));
            out.put("handling", handling);
            out.put("openedAt", opened.toString());
            out.put("resolvedAt", resolved == null ? null : resolved.toString());
            out.put("mttdSeconds", fault == null ? null : seconds(fault, opened));
            out.put("mttdReference", fault == null ? null : "injected fault start");
            out.put("timeToMitigationSeconds", mitigated == null ? null : seconds(opened, mitigated));
            out.put("mttrSeconds", resolved == null ? null : seconds(opened, resolved));
            out.put("downtimeSeconds", downtime(env, entries, opened, resolved == null ? Instant.now() : resolved));
            incidents.add(out);
            if (resolved != null) byHandling.computeIfAbsent(handling, k -> new ArrayList<>()).add(out);
        }
        Map<String, Object> summary = new LinkedHashMap<>();
        byHandling.forEach((handling, list) -> {
            Map<String, Object> s = new LinkedHashMap<>();
            s.put("resolvedIncidents", list.size());
            s.put("medianMttrSeconds", median(list, "mttrSeconds"));
            s.put("meanMttrSeconds", mean(list, "mttrSeconds"));
            s.put("medianDowntimeSeconds", median(list, "downtimeSeconds"));
            s.put("medianTimeToMitigationSeconds", median(list, "timeToMitigationSeconds"));
            s.put("medianMttdSeconds", median(list, "mttdSeconds"));
            summary.put(handling, s);
        });
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("environmentId", env.id());
        result.put("entryServices", entries);
        result.put("sampleSeconds", sampleSeconds);
        result.put("summary", summary);
        result.put("incidents", incidents);
        return result;
    }

    /** Services that receive user traffic: monitored services no other monitored service calls. */
    List<String> entryServices(Environment env) {
        Set<String> external = new HashSet<>(env.config().externalNodes());
        List<String> services = db.queryForList("""
                SELECT DISTINCT service_name FROM telemetry_snapshots
                WHERE environment_id = ? AND captured_at > now() - interval '1 day' AND request_rate IS NOT NULL
                """, String.class, env.id());
        Set<String> called = new HashSet<>();
        for (String[] e : repo.edges(env.id())) {
            if (!external.contains(e[0]) && services.contains(e[0])) called.add(e[1]);
        }
        return services.stream().filter(s -> !called.contains(s) && !external.contains(s)).sorted().toList();
    }

    private double downtime(Environment env, List<String> entries, Instant from, Instant to) {
        if (entries.isEmpty()) return 0;
        Set<Instant> violating = new HashSet<>();
        for (String s : entries) {
            var slo = SloEvaluator.sloFor(env.config(), s);
            db.query("""
                    SELECT captured_at FROM telemetry_snapshots
                    WHERE environment_id = ? AND service_name = ? AND captured_at BETWEEN ? AND ?
                      AND (p99_latency > ? OR error_rate > ?)
                    """, rs -> { violating.add(rs.getTimestamp(1).toInstant()); }, env.id(), s, Timestamp.from(from),
                    Timestamp.from(to), slo.latencyP99Ms(), slo.errorRatePct());
        }
        return Math.round(violating.size() * sampleSeconds * 10) / 10.0;
    }

    private static Instant ts(Object o) {
        return o == null ? null : ((Timestamp) o).toInstant();
    }

    private static double seconds(Instant a, Instant b) {
        return Math.round(Duration.between(a, b).toMillis() / 100.0) / 10.0;
    }

    private static Double median(List<Map<String, Object>> rows, String key) {
        double[] v = rows.stream().map(r -> r.get(key)).filter(Objects::nonNull).mapToDouble(o -> ((Number) o).doubleValue()).sorted().toArray();
        if (v.length == 0) return null;
        return v.length % 2 == 1 ? v[v.length / 2] : (v[v.length / 2 - 1] + v[v.length / 2]) / 2;
    }

    private static Double mean(List<Map<String, Object>> rows, String key) {
        var s = rows.stream().map(r -> r.get(key)).filter(Objects::nonNull).mapToDouble(o -> ((Number) o).doubleValue()).summaryStatistics();
        return s.getCount() == 0 ? null : Math.round(s.getAverage() * 10) / 10.0;
    }
}

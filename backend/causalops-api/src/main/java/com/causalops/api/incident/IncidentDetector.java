package com.causalops.api.incident;

import com.causalops.api.changes.ChangeEventRepository;
import com.causalops.api.environment.Environment;
import com.causalops.api.events.EventBus;
import com.causalops.api.telemetry.SloEvaluator;
import com.causalops.api.telemetry.SloEvaluator.Breach;
import com.causalops.api.telemetry.SloEvaluator.Level;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.stream.Collectors;

/**
 * Opens, extends, escalates and resolves incidents from two measured signals:
 * <ul>
 *   <li><b>SLO breach</b>: a service violates its SLO for {@code detection.breachSamples}
 *       consecutive samples. Works from the first minute, including while learning.</li>
 *   <li><b>Anomaly gate</b>: the calibrated AI engine flags a service as anomalous against its
 *       learned baseline (only for calibrated environments). Fires before an SLO is crossed.</li>
 * </ul>
 * Services that turn bad later join the open incident. An incident is closed on the signal that
 * declared it: once an SLO breach confirmed it, it resolves when every affected service has been
 * within SLO for {@code detection.recoverySamples} consecutive samples (the usual definition of
 * restored service; a restart's JVM warm-up can stay above the learned baseline while within SLO).
 * An incident only the anomaly gate saw resolves when the affected services are also no longer flagged.
 */
@Component
public class IncidentDetector {

    private static final Logger log = LoggerFactory.getLogger(IncidentDetector.class);
    static final String SLO_SOURCE = "slo_breach";
    static final String ANOMALY_SOURCE = "anomaly_gate";
    private static final List<String> SEVERITY_ORDER = List.of("MEDIUM", "HIGH", "CRITICAL");

    private final IncidentRepository incidents;
    private final EventBus events;
    private final ChangeEventRepository changes;

    /** environment -> service -> streak counters. Only the ingestion thread writes them. */
    private final Map<UUID, Map<String, Streak>> streaks = new ConcurrentHashMap<>();

    private static final class Streak {
        int breaching;
        int healthy;      // within SLO and not flagged by the gate
        int withinSlo;    // within SLO
        Breach worst;
    }

    /** One service that is currently bad, and why. */
    record Issue(String service, String source, String severity, Map<String, Object> evidence, String summary) {
    }

    public IncidentDetector(IncidentRepository incidents, EventBus events, ChangeEventRepository changes) {
        this.changes = changes;
        this.incidents = incidents;
        this.events = events;
    }

    public void evaluate(Environment env, Map<String, SloEvaluator.Result> results,
                         Map<String, AnomalySignal> anomalies, Instant at) {
        var detection = env.config().detection();
        Map<String, Streak> envStreaks = streaks.computeIfAbsent(env.id(), k -> new ConcurrentHashMap<>());

        results.forEach((service, r) -> {
            Streak s = envStreaks.computeIfAbsent(service, k -> new Streak());
            boolean flagged = anomalies.containsKey(service) && anomalies.get(service).flagged();
            s.withinSlo = r.level() == Level.HEALTHY ? s.withinSlo + 1 : 0;
            if (!r.breaches().isEmpty()) {
                s.breaching++;
                s.healthy = 0;
                s.worst = r.breaches().stream().max(Comparator.comparing(Breach::level)
                        .thenComparing(b -> b.observed() / b.threshold())).orElseThrow();
            } else if (r.level() == Level.HEALTHY && !flagged) {
                s.healthy++;
                s.breaching = 0;
            } else if (flagged) {
                s.healthy = 0;
                s.breaching = 0;
            }
        });

        Map<String, Issue> issues = new TreeMap<>();
        envStreaks.forEach((service, s) -> {
            if (s.breaching >= detection.breachSamples() && s.worst != null) {
                issues.put(service, sloIssue(s.worst, at));
            }
        });
        anomalies.values().stream().filter(AnomalySignal::flagged)
                .forEach(a -> issues.putIfAbsent(a.service(), anomalyIssue(a, at)));

        Optional<Map<String, Object>> open = incidents.openFor(env.id());
        if (open.isEmpty()) {
            if (!issues.isEmpty()) openIncident(env, new ArrayList<>(issues.values()), at);
            return;
        }

        Map<String, Object> incident = open.get();
        UUID id = (UUID) incident.get("id");
        List<String> affected = new ArrayList<>(Arrays.asList(
                incidents.read((String) incident.get("affectedServices"), String[].class)));
        List<Object> evidence = new ArrayList<>(Arrays.asList(
                incidents.read((String) incident.get("evidence"), Object[].class)));

        List<Issue> joined = issues.values().stream().filter(i -> !affected.contains(i.service())).toList();
        String current = (String) incident.get("severity");
        String worst = maxSeverity(issues.values());
        boolean escalate = worst != null && SEVERITY_ORDER.indexOf(worst) > SEVERITY_ORDER.indexOf(current);
        boolean sloNowConfirmed = issues.values().stream().anyMatch(i -> SLO_SOURCE.equals(i.source()))
                && evidence.stream().noneMatch(e -> e instanceof Map<?, ?> m && SLO_SOURCE.equals(m.get("source")));
        if (!joined.isEmpty() || escalate || sloNowConfirmed) {
            for (Issue i : joined) affected.add(i.service());
            issues.values().stream()
                    .filter(i -> joined.contains(i) || (sloNowConfirmed && SLO_SOURCE.equals(i.source())))
                    .forEach(i -> evidence.add(i.evidence()));
            String severity = escalate ? worst : current;
            incidents.update(id, title(affected), severity, summary(issues.values()), affected, evidence);
            incidents.addEvent(id, !joined.isEmpty() ? "SERVICES_AFFECTED" : escalate ? "ESCALATED" : "SLO_BREACH_CONFIRMED",
                    Map.of("services", joined.stream().map(Issue::service).toList(), "severity", severity));
            events.emit("incident.updated", id, Map.of("affectedServices", affected, "severity", severity));
        }

        boolean sloIncident = evidence.stream().anyMatch(e -> e instanceof Map<?, ?> m && SLO_SOURCE.equals(m.get("source")));
        boolean recovered = sloIncident
                ? issues.values().stream().noneMatch(i -> SLO_SOURCE.equals(i.source())) && affected.stream().allMatch(svc -> {
                    Streak s = envStreaks.get(svc);
                    return s != null && s.withinSlo >= detection.recoverySamples();
                })
                : issues.isEmpty() && affected.stream().allMatch(svc -> {
                    Streak s = envStreaks.get(svc);
                    return s != null && s.healthy >= detection.recoverySamples();
                });
        if (recovered) {
            incidents.setStatus(id, "RESOLVED");
            incidents.addEvent(id, "RECOVERED", Map.of("at", at.toString(), "recoverySamples", detection.recoverySamples()));
            events.emit("incident.resolved", id, Map.of("incidentKey", incident.get("incidentKey")));
            log.info("Incident {} resolved: all affected services healthy for {} samples",
                    incident.get("incidentKey"), detection.recoverySamples());
        }
    }

    private void openIncident(Environment env, List<Issue> issues, Instant at) {
        List<String> affected = issues.stream().map(Issue::service).distinct().toList();
        String source = issues.stream().anyMatch(i -> SLO_SOURCE.equals(i.source())) ? SLO_SOURCE : ANOMALY_SOURCE;
        String severity = maxSeverity(issues);
        List<Object> evidence = issues.stream().map(i -> (Object) i.evidence()).toList();
        UUID id = incidents.create(env.id(), title(affected), severity, "DETECTED", source, summary(issues),
                affected, evidence);
        incidents.addEvent(id, "DETECTED", Map.of("source", source, "services", affected, "at", at.toString()));
        // Change correlation: most incidents follow a change; say which one was in progress.
        var recent = changes.active(env.id(), 300, true);
        if (!recent.isEmpty()) {
            incidents.addEvent(id, "CHANGE_CORRELATED", Map.of("changes", recent.stream().map(c -> Map.of(
                    "kind", c.get("kind"), "target", String.valueOf(c.get("target")), "source", c.get("source"),
                    "description", c.get("description"), "startedAt", String.valueOf(c.get("startedAt")))).toList()));
        }
        Map<String, Object> created = incidents.get(id);
        events.emit("incident.created", id, created);
        log.info("Incident {} opened by {}: {}", created.get("incidentKey"), source, created.get("title"));
    }

    private static Issue sloIssue(Breach b, Instant at) {
        Map<String, Object> e = new LinkedHashMap<>();
        e.put("timestamp", at.toString());
        e.put("service", b.service());
        e.put("metric", b.metric());
        e.put("observed", round(b.observed()));
        e.put("threshold", b.threshold());
        e.put("level", b.level().name());
        e.put("source", SLO_SOURCE);
        String text = "%s %s %.1f %s (SLO %.1f)".formatted(b.service(),
                "latencyP99".equals(b.metric()) ? "p99 latency" : "error rate", b.observed(),
                "latencyP99".equals(b.metric()) ? "ms" : "%", b.threshold());
        return new Issue(b.service(), SLO_SOURCE, b.level() == Level.CRITICAL ? "CRITICAL" : "HIGH", e, text);
    }

    @SuppressWarnings("unchecked")
    private static Issue anomalyIssue(AnomalySignal a, Instant at) {
        Map<String, Object> top = a.signals().isEmpty() ? Map.of() : a.signals().get(0);
        Map<String, Object> e = new LinkedHashMap<>();
        e.put("timestamp", at.toString());
        e.put("service", a.service());
        e.put("metric", "anomaly");
        e.put("variable", top.get("variable"));
        e.put("observed", top.get("value"));
        e.put("baseline", top.get("baseline"));
        e.put("z", top.get("z"));
        e.put("anomalyScore", round(a.anomaly()));
        e.put("source", ANOMALY_SOURCE);
        String text = "%s %s deviates from its learned baseline (z=%s)".formatted(a.service(),
                top.getOrDefault("variable", "telemetry"), top.getOrDefault("z", "?"));
        return new Issue(a.service(), ANOMALY_SOURCE, "MEDIUM", e, text);
    }

    private static String maxSeverity(Collection<Issue> issues) {
        return issues.stream().map(Issue::severity).max(Comparator.comparingInt(SEVERITY_ORDER::indexOf)).orElse(null);
    }

    static String title(List<String> affected) {
        return "Degradation: " + String.join(", ", affected);
    }

    static String summary(Collection<Issue> issues) {
        if (issues.isEmpty()) return "Affected services are back to normal.";
        return issues.stream().map(Issue::summary).collect(Collectors.joining("; "));
    }

    private static double round(double v) {
        return Math.round(v * 100.0) / 100.0;
    }
}

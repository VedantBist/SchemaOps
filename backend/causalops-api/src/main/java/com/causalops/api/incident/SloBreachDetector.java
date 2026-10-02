package com.causalops.api.incident;

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
 * Opens, extends and resolves incidents from SLO breaches in measured telemetry.
 *
 * <p>An incident opens when a service breaches its SLO for {@code detection.breachSamples}
 * consecutive samples; services that breach later join the open incident; it resolves once every
 * affected service has met its SLO for {@code detection.recoverySamples} consecutive samples.
 * This is the detector that runs while an environment is still learning its baselines; the
 * calibrated anomaly gate (Phase 3) adds detection before an SLO is crossed.
 */
@Component
public class SloBreachDetector {

    private static final Logger log = LoggerFactory.getLogger(SloBreachDetector.class);
    static final String SOURCE = "slo_breach";

    private final IncidentRepository incidents;
    private final EventBus events;

    /** environment -> service -> streak counters. Only this scheduler thread writes them. */
    private final Map<UUID, Map<String, Streak>> streaks = new ConcurrentHashMap<>();

    private static final class Streak {
        int breaching;
        int healthy;
        Breach worst;
    }

    public SloBreachDetector(IncidentRepository incidents, EventBus events) {
        this.incidents = incidents;
        this.events = events;
    }

    public void evaluate(Environment env, Map<String, SloEvaluator.Result> results, Instant at) {
        var detection = env.config().detection();
        Map<String, Streak> envStreaks = streaks.computeIfAbsent(env.id(), k -> new ConcurrentHashMap<>());

        results.forEach((service, r) -> {
            Streak s = envStreaks.computeIfAbsent(service, k -> new Streak());
            if (!r.breaches().isEmpty()) {
                s.breaching++;
                s.healthy = 0;
                s.worst = r.breaches().stream().max(Comparator.comparing(Breach::level)
                        .thenComparing(b -> b.observed() / b.threshold())).orElseThrow();
            } else if (r.level() == Level.HEALTHY) {
                s.healthy++;
                s.breaching = 0;
            }
        });

        List<Breach> confirmed = envStreaks.values().stream()
                .filter(s -> s.breaching >= detection.breachSamples() && s.worst != null)
                .map(s -> s.worst).sorted(Comparator.comparing(Breach::service)).toList();

        Optional<Map<String, Object>> open = incidents.openFor(env.id());
        if (open.isEmpty()) {
            if (!confirmed.isEmpty()) openIncident(env, confirmed, at);
            return;
        }

        Map<String, Object> incident = open.get();
        UUID id = (UUID) incident.get("id");
        List<String> affected = new ArrayList<>(Arrays.asList(
                incidents.read((String) incident.get("affectedServices"), String[].class)));
        List<Object> evidence = new ArrayList<>(Arrays.asList(
                incidents.read((String) incident.get("evidence"), Object[].class)));

        List<Breach> joined = confirmed.stream().filter(b -> !affected.contains(b.service())).toList();
        boolean escalate = "HIGH".equals(incident.get("severity"))
                && confirmed.stream().anyMatch(b -> b.level() == Level.CRITICAL);
        if (!joined.isEmpty() || escalate) {
            joined.forEach(b -> {
                affected.add(b.service());
                evidence.add(evidenceOf(b, at));
            });
            String severity = escalate ? "CRITICAL" : (String) incident.get("severity");
            incidents.update(id, title(affected), severity, summary(confirmed), affected, evidence);
            incidents.addEvent(id, joined.isEmpty() ? "ESCALATED" : "SERVICES_AFFECTED",
                    Map.of("services", joined.stream().map(Breach::service).toList(), "severity", severity));
            events.emit("incident.updated", id, Map.of("affectedServices", affected, "severity", severity));
        }

        boolean recovered = confirmed.isEmpty() && affected.stream().allMatch(svc -> {
            Streak s = envStreaks.get(svc);
            return s != null && s.healthy >= detection.recoverySamples();
        });
        if (recovered && SOURCE.equals(incident.get("detectionSource"))) {
            incidents.setStatus(id, "RESOLVED");
            incidents.addEvent(id, "RECOVERED", Map.of("at", at.toString(),
                    "recoverySamples", detection.recoverySamples()));
            events.emit("incident.resolved", id, Map.of("incidentKey", incident.get("incidentKey")));
            log.info("Incident {} resolved: all affected services met their SLOs", incident.get("incidentKey"));
        }
    }

    private void openIncident(Environment env, List<Breach> confirmed, Instant at) {
        List<String> affected = confirmed.stream().map(Breach::service).distinct().toList();
        String severity = confirmed.stream().anyMatch(b -> b.level() == Level.CRITICAL) ? "CRITICAL" : "HIGH";
        List<Object> evidence = confirmed.stream().map(b -> (Object) evidenceOf(b, at)).toList();
        UUID id = incidents.create(env.id(), title(affected), severity, "DETECTED", SOURCE, summary(confirmed),
                affected, evidence);
        incidents.addEvent(id, "DETECTED", Map.of("source", SOURCE, "services", affected, "at", at.toString()));
        Map<String, Object> created = incidents.get(id);
        events.emit("incident.created", id, created);
        log.info("Incident {} opened: {}", created.get("incidentKey"), created.get("title"));
    }

    private static Map<String, Object> evidenceOf(Breach b, Instant at) {
        Map<String, Object> e = new LinkedHashMap<>();
        e.put("timestamp", at.toString());
        e.put("service", b.service());
        e.put("metric", b.metric());
        e.put("observed", Math.round(b.observed() * 100.0) / 100.0);
        e.put("threshold", b.threshold());
        e.put("level", b.level().name());
        e.put("source", "prometheus");
        return e;
    }

    static String title(List<String> affected) {
        return "SLO breach: " + String.join(", ", affected);
    }

    static String summary(List<Breach> breaches) {
        if (breaches.isEmpty()) return "Affected services are back within their SLOs.";
        return breaches.stream().map(b -> "%s %s %.1f %s (SLO %.1f)".formatted(
                        b.service(), "latencyP99".equals(b.metric()) ? "p99 latency" : "error rate",
                        b.observed(), "latencyP99".equals(b.metric()) ? "ms" : "%", b.threshold()))
                .collect(Collectors.joining("; "));
    }
}

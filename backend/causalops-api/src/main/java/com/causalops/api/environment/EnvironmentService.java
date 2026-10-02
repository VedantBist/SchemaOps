package com.causalops.api.environment;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.event.EventListener;
import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.io.InputStream;
import java.util.List;
import java.util.NoSuchElementException;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;

@Service
public class EnvironmentService {

    private static final Logger log = LoggerFactory.getLogger(EnvironmentService.class);
    private static final Pattern PROM_DURATION = Pattern.compile("\\d+(ms|s|m|h)");
    private static final Set<String> STATUSES = Set.of("LEARNING", "CALIBRATED", "ACTIVE", "DISABLED");

    private final EnvironmentRepository environments;
    private final ObjectMapper json;
    private final BootstrapProperties bootstrap;

    public EnvironmentService(EnvironmentRepository environments, ObjectMapper json, BootstrapProperties bootstrap) {
        this.environments = environments;
        this.json = json;
        this.bootstrap = bootstrap;
    }

    /** Creates the first environment from application properties when none exists yet. */
    @EventListener(ApplicationReadyEvent.class)
    public void bootstrapIfEmpty() {
        if (!bootstrap.enabled() || !environments.findAll().isEmpty()) return;
        EnvironmentConfig defaults = defaults();
        EnvironmentConfig config = new EnvironmentConfig(
                new EnvironmentConfig.Endpoints(bootstrap.prometheusUrl(), bootstrap.tempoUrl(),
                        bootstrap.lokiUrl(), bootstrap.collectorHealthUrl()),
                defaults.telemetry(), defaults.slo(), defaults.serviceSlos(), defaults.detection(),
                defaults.externalNodes(), defaults.calibration());
        validate(config);
        Environment env = environments.create(bootstrap.name(), config);
        log.info("Bootstrapped environment '{}' ({}) reading telemetry from {}", env.name(), env.id(), bootstrap.prometheusUrl());
    }

    public List<Environment> all() {
        return environments.findAll();
    }

    public List<Environment> monitored() {
        return environments.findMonitored();
    }

    public Environment get(UUID id) {
        return environments.findById(id).orElseThrow(() -> new NoSuchElementException("Unknown environment " + id));
    }

    /** The environment an API call refers to: the given id, or the only/first monitored one. */
    public Environment resolve(UUID id) {
        if (id != null) return get(id);
        return environments.findMonitored().stream().findFirst()
                .orElseThrow(() -> new NoSuchElementException("No environment is configured yet"));
    }

    public Environment create(String name, EnvironmentConfig config) {
        if (name == null || !name.matches("[a-z0-9][a-z0-9-]{1,78}")) {
            throw new IllegalArgumentException("name must be lowercase letters, digits and dashes (2-79 chars)");
        }
        EnvironmentConfig merged = withDefaults(config);
        validate(merged);
        return environments.create(name, merged);
    }

    public Environment updateConfig(UUID id, EnvironmentConfig config) {
        get(id);
        EnvironmentConfig merged = withDefaults(config);
        validate(merged);
        return environments.updateConfig(id, merged);
    }

    public Environment updateStatus(UUID id, String status) {
        if (!STATUSES.contains(status)) throw new IllegalArgumentException("status must be one of " + STATUSES);
        return environments.updateStatus(id, status);
    }

    public EnvironmentConfig defaults() {
        try (InputStream in = new ClassPathResource("environment-defaults.json").getInputStream()) {
            return json.readValue(in, EnvironmentConfig.class);
        } catch (IOException e) {
            throw new IllegalStateException("environment-defaults.json is missing or invalid", e);
        }
    }

    /** Sections omitted by the caller fall back to the defaults. */
    private EnvironmentConfig withDefaults(EnvironmentConfig c) {
        if (c == null) throw new IllegalArgumentException("config is required");
        EnvironmentConfig d = defaults();
        return new EnvironmentConfig(
                c.endpoints(),
                c.telemetry() != null ? c.telemetry() : d.telemetry(),
                c.slo() != null ? c.slo() : d.slo(),
                c.serviceSlos(),
                c.detection() != null ? c.detection() : d.detection(),
                c.externalNodes().isEmpty() ? d.externalNodes() : c.externalNodes(),
                c.calibration() != null ? c.calibration() : d.calibration());
    }

    static void validate(EnvironmentConfig c) {
        var e = c.endpoints();
        if (e == null || blank(e.prometheusUrl())) throw new IllegalArgumentException("endpoints.prometheusUrl is required");
        var t = c.telemetry();
        if (t == null || blank(t.topologyQuery())) throw new IllegalArgumentException("telemetry.topologyQuery is required");
        if (!PROM_DURATION.matcher(String.valueOf(t.rateWindow())).matches()
                || !PROM_DURATION.matcher(String.valueOf(t.topologyWindow())).matches()) {
            throw new IllegalArgumentException("telemetry.rateWindow and topologyWindow must be Prometheus durations such as 30s or 5m");
        }
        if (t.staleAfterSeconds() < 10) throw new IllegalArgumentException("telemetry.staleAfterSeconds must be at least 10");
        if (t.serviceMetrics() == null || !t.serviceMetrics().containsKey("requestRate")) {
            throw new IllegalArgumentException("telemetry.serviceMetrics.requestRate is required");
        }
        for (var group : List.of(t.serviceMetrics(), t.dependencyNodeMetrics() == null ? java.util.Map.<String, EnvironmentConfig.MetricTemplate>of() : t.dependencyNodeMetrics())) {
            group.forEach((metric, tpl) -> {
                if (!EnvironmentConfig.METRICS.contains(metric)) {
                    throw new IllegalArgumentException("Unknown metric '" + metric + "'; allowed: " + EnvironmentConfig.METRICS);
                }
                if (tpl == null || blank(tpl.query()) || blank(tpl.label())) {
                    throw new IllegalArgumentException("Metric template '" + metric + "' needs a query and a label");
                }
            });
        }
        var slo = c.slo();
        if (slo == null || slo.latencyP99Ms() <= 0 || slo.errorRatePct() <= 0 || slo.criticalMultiplier() < 1) {
            throw new IllegalArgumentException("slo needs latencyP99Ms > 0, errorRatePct > 0 and criticalMultiplier >= 1");
        }
        var d = c.detection();
        if (d == null || d.breachSamples() < 1 || d.recoverySamples() < 1) {
            throw new IllegalArgumentException("detection.breachSamples and recoverySamples must be at least 1");
        }
        var cal = c.calibration();
        if (cal == null || cal.learningWindowHours() < 1 || cal.retrainIntervalDays() < 1) {
            throw new IllegalArgumentException("calibration.learningWindowHours and retrainIntervalDays must be at least 1");
        }
    }

    private static boolean blank(String s) {
        return s == null || s.isBlank();
    }

    /** First environment created on a fresh install. */
    @ConfigurationProperties(prefix = "causalops.environment.bootstrap")
    public record BootstrapProperties(boolean enabled, String name, String prometheusUrl, String tempoUrl,
                                      String lokiUrl, String collectorHealthUrl) {
    }
}

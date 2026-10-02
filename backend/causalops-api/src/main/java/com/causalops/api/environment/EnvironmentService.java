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
        upgradeStoredConfigs();
        if (!bootstrap.enabled() || !environments.findAll().isEmpty()) return;
        EnvironmentConfig defaults = defaults();
        EnvironmentConfig config = new EnvironmentConfig(
                new EnvironmentConfig.Endpoints(bootstrap.prometheusUrl(), bootstrap.tempoUrl(),
                        bootstrap.lokiUrl(), bootstrap.collectorHealthUrl()),
                defaults.telemetry(), defaults.slo(), defaults.serviceSlos(), defaults.detection(),
                defaults.externalNodes(), defaults.calibration(), defaults.analysis())
                .withRemediation(bootstrapRemediation(defaults.remediation()));
        validate(config);
        Environment env = environments.create(bootstrap.name(), config);
        log.info("Bootstrapped environment '{}' ({}) reading telemetry from {}", env.name(), env.id(), bootstrap.prometheusUrl());
    }

    /** Fills config sections added by newer releases from the defaults; values already set are kept. */
    void upgradeStoredConfigs() {
        for (Environment env : environments.findAll()) {
            EnvironmentConfig upgraded = withDefaults(env.config());
            if (env.config().remediation() == null && env.name().equals(bootstrap.name())) {
                upgraded = upgraded.withRemediation(bootstrapRemediation(upgraded.remediation()));
            }
            if (!upgraded.equals(env.config())) {
                validate(upgraded);
                environments.updateConfig(env.id(), upgraded);
                log.info("Upgraded stored config of environment '{}' with new default sections", env.name());
                restartLearningIfMeasurementChanged(env, upgraded);
            }
        }
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
        Environment current = get(id);
        EnvironmentConfig merged = withDefaults(config);
        validate(merged);
        Environment updated = environments.updateConfig(id, merged);
        return restartLearningIfMeasurementChanged(current, merged) ? get(id) : updated;
    }

    /**
     * Models learned from one metric definition are not valid for another (a p95 is not a p99), so
     * changing what is measured restarts learning: evaluation pauses, and calibration uses only
     * telemetry recorded from now on.
     */
    private boolean restartLearningIfMeasurementChanged(Environment before, EnvironmentConfig after) {
        var a = before.config().telemetry();
        var b = after.telemetry();
        boolean same = a != null && java.util.Objects.equals(a.rateWindow(), b.rateWindow())
                && java.util.Objects.equals(a.serviceMetrics(), b.serviceMetrics())
                && java.util.Objects.equals(a.dependencyNodeMetrics(), b.dependencyNodeMetrics())
                && java.util.Objects.equals(a.edgeMetrics(), b.edgeMetrics());
        if (same || "DISABLED".equals(before.status())) return false;
        String reason = "metric definitions changed; learning restarted so models are trained on the new measurements";
        environments.restartLearning(before.id(), reason);
        log.info("Environment '{}': {}", before.name(), reason);
        return true;
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

    private static final String LEGACY_DB_LATENCY = "dbLatencyP95";

    /** Sections omitted by the caller fall back to the defaults. */
    private EnvironmentConfig withDefaults(EnvironmentConfig c) {
        if (c == null) throw new IllegalArgumentException("config is required");
        EnvironmentConfig d = defaults();
        var t = c.telemetry();
        if (t != null && t.serviceMetrics() != null && t.serviceMetrics().containsKey(LEGACY_DB_LATENCY)) {
            // Earlier releases measured database latency at p95 against service latency at p99, which
            // makes the service appear to degrade before its database. An unmodified default mapping
            // adopts the p99 template; a custom query keeps its definition under the new key.
            var metrics = new java.util.LinkedHashMap<>(t.serviceMetrics());
            var legacy = metrics.remove(LEGACY_DB_LATENCY);
            metrics.put("dbLatencyP99", d.telemetry().serviceMetrics().get("dbLatencyP99"));
            if (!metrics.equals(d.telemetry().serviceMetrics())) {
                metrics.put("dbLatencyP99", legacy);
                log.warn("Custom {} template moved to dbLatencyP99; measure it at the same quantile as latencyP99", LEGACY_DB_LATENCY);
            }
            t = new EnvironmentConfig.Telemetry(t.rateWindow(), t.topologyWindow(), t.staleAfterSeconds(),
                    metrics, t.dependencyNodeMetrics(), t.edgeMetrics(), t.topologyQuery());
        }
        if (t != null && t.edgeMetrics() == null) {
            t = new EnvironmentConfig.Telemetry(t.rateWindow(), t.topologyWindow(), t.staleAfterSeconds(),
                    t.serviceMetrics(), t.dependencyNodeMetrics(), d.telemetry().edgeMetrics(), t.topologyQuery());
        }
        var cal = c.calibration();
        if (cal != null && cal.minLearningMinutes() == 0) {
            cal = new EnvironmentConfig.Calibration(cal.learningWindowHours(), cal.retrainIntervalDays(),
                    d.calibration().minLearningMinutes());
        }
        return new EnvironmentConfig(
                c.endpoints(),
                t != null ? t : d.telemetry(),
                c.slo() != null ? c.slo() : d.slo(),
                c.serviceSlos(),
                c.detection() != null ? c.detection() : d.detection(),
                c.externalNodes().isEmpty() ? d.externalNodes() : c.externalNodes(),
                cal != null ? cal : d.calibration(),
                c.analysis() != null ? c.analysis() : d.analysis(),
                c.remediation() != null ? c.remediation() : d.remediation());
    }

    /**
     * The bootstrapped environment is the stack CausalOps was deployed with; when the deployment
     * names its compose project, the Docker executor may manage that project's containers.
     */
    private EnvironmentConfig.Remediation bootstrapRemediation(EnvironmentConfig.Remediation r) {
        if (blank(bootstrap.dockerProject())) return r;
        return r.withExecutor("docker", java.util.Map.of("enabled", true, "project", bootstrap.dockerProject()));
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
        validateTemplates(t.serviceMetrics(), EnvironmentConfig.METRICS, true);
        validateTemplates(t.dependencyNodeMetrics(), EnvironmentConfig.METRICS, true);
        validateTemplates(t.edgeMetrics(), EnvironmentConfig.EDGE_METRICS, false);
        var slo = c.slo();
        if (slo == null || slo.latencyP99Ms() <= 0 || slo.errorRatePct() <= 0 || slo.criticalMultiplier() < 1) {
            throw new IllegalArgumentException("slo needs latencyP99Ms > 0, errorRatePct > 0 and criticalMultiplier >= 1");
        }
        var d = c.detection();
        if (d == null || d.breachSamples() < 1 || d.recoverySamples() < 1) {
            throw new IllegalArgumentException("detection.breachSamples and recoverySamples must be at least 1");
        }
        var cal = c.calibration();
        if (cal == null || cal.learningWindowHours() < 1 || cal.retrainIntervalDays() < 1 || cal.minLearningMinutes() < 10) {
            throw new IllegalArgumentException(
                    "calibration needs learningWindowHours >= 1, retrainIntervalDays >= 1 and minLearningMinutes >= 10");
        }
        var a = c.analysis();
        if (a == null || a.forecastHorizonsSeconds() == null || a.forecastHorizonsSeconds().isEmpty()
                || a.forecastHorizonsSeconds().stream().anyMatch(h -> h == null || h < 5 || h > 3600)
                || a.anomalyZFloor() < 2 || a.gateConsecutiveSamples() < 1 || a.lagOrder() < 1 || a.lagOrder() > 12
                || a.ridgeAlpha() <= 0 || a.bootstrapModels() < 1 || a.bootstrapModels() > 100
                || a.maxGateFalsePositiveRate() <= 0 || a.maxGateFalsePositiveRate() >= 1 || a.rcaDelaySeconds() < 0
                || a.rcaWeights() == null || a.rcaWeights().isEmpty()) {
            throw new IllegalArgumentException("analysis settings are out of range; see GET /api/environments/defaults");
        }
        validateRemediation(c.remediation());
    }

    static void validateRemediation(EnvironmentConfig.Remediation r) {
        if (r == null) throw new IllegalArgumentException("remediation section is required");
        var v = r.verification();
        if (r.autoExecuteMaxTier() < 0 || r.autoExecuteMaxTier() > 3 || r.minRcaConfidence() < 0 || r.minRcaConfidence() > 1
                || r.maxTelemetryAgeSeconds() < 5 || r.maxAutoActionsPerHour() < 0 || r.cooldownMinutes() < 0
                || r.recommendationTtlMinutes() < 1 || r.maxAutoBlastRadius() <= 0 || r.maxAutoBlastRadius() > 1
                || v == null || v.settleSeconds() < 0 || v.healthySamples() < 1 || v.windowSeconds() < 30
                || v.windowSeconds() <= v.settleSeconds()) {
            throw new IllegalArgumentException("remediation settings are out of range; see GET /api/environments/defaults");
        }
        for (var kind : r.executors().keySet()) {
            if (!EnvironmentConfig.EXECUTOR_OPERATIONS.containsKey(kind)) {
                throw new IllegalArgumentException("Unknown executor '" + kind + "'; allowed: " + EnvironmentConfig.EXECUTOR_OPERATIONS.keySet());
            }
        }
        if (r.executorEnabled("docker") && blank((String) r.executors().get("docker").get("project"))) {
            throw new IllegalArgumentException("remediation.executors.docker.project is required when docker is enabled");
        }
        if (r.executorEnabled("kubernetes") && blank((String) r.executors().get("kubernetes").get("namespace"))) {
            throw new IllegalArgumentException("remediation.executors.kubernetes.namespace is required when kubernetes is enabled");
        }
        if (r.executorEnabled("webhook") && blank((String) r.executors().get("webhook").get("url"))) {
            throw new IllegalArgumentException("remediation.executors.webhook.url is required when webhook is enabled");
        }
        var ids = new java.util.HashSet<String>();
        for (var a : r.actions()) {
            if (a == null || blank(a.id()) || !ids.add(a.id())) {
                throw new IllegalArgumentException("every remediation action needs a unique id");
            }
            var ops = EnvironmentConfig.EXECUTOR_OPERATIONS.get(a.executor());
            if (ops == null || !ops.contains(a.operation())) {
                throw new IllegalArgumentException("action '" + a.id() + "': executor/operation must be one of " + EnvironmentConfig.EXECUTOR_OPERATIONS);
            }
            if (a.tier() < 1 || a.tier() > 3 || a.appliesTo().isEmpty()
                    || !List.of("service", "database", "link").containsAll(a.appliesTo())) {
                throw new IllegalArgumentException("action '" + a.id() + "' needs tier 1-3 and appliesTo within [service, database, link]");
            }
        }
    }

    private static void validateTemplates(java.util.Map<String, EnvironmentConfig.MetricTemplate> templates,
                                          List<String> allowed, boolean needsLabel) {
        if (templates == null) return;
        templates.forEach((metric, tpl) -> {
            if (!allowed.contains(metric)) {
                throw new IllegalArgumentException("Unknown metric '" + metric + "'; allowed: " + allowed);
            }
            if (tpl == null || blank(tpl.query()) || (needsLabel && blank(tpl.label()))) {
                throw new IllegalArgumentException("Metric template '" + metric + "' needs a query" + (needsLabel ? " and a label" : ""));
            }
        });
    }

    private static boolean blank(String s) {
        return s == null || s.isBlank();
    }

    /** First environment created on a fresh install. */
    @ConfigurationProperties(prefix = "causalops.environment.bootstrap")
    public record BootstrapProperties(boolean enabled, String name, String prometheusUrl, String tempoUrl,
                                      String lokiUrl, String collectorHealthUrl, String dockerProject) {
    }
}

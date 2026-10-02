package com.causalops.api.environment;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import java.util.List;
import java.util.Map;

/**
 * Everything CausalOps needs to know about one monitored system. Stored as JSON in
 * {@code environments.config} and edited through the API (and, in Phase 5, the setup wizard).
 * Defaults target any system that sends OpenTelemetry to the bundled collector.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record EnvironmentConfig(
        Endpoints endpoints,
        Telemetry telemetry,
        Slo slo,
        Map<String, Slo> serviceSlos,
        Detection detection,
        List<String> externalNodes,
        Calibration calibration,
        Analysis analysis,
        Remediation remediation) {

    public EnvironmentConfig {
        serviceSlos = serviceSlos == null ? Map.of() : Map.copyOf(serviceSlos);
        externalNodes = externalNodes == null ? List.of() : List.copyOf(externalNodes);
    }

    /** A configuration without a remediation section (it is filled from the defaults). */
    public EnvironmentConfig(Endpoints endpoints, Telemetry telemetry, Slo slo, Map<String, Slo> serviceSlos,
                             Detection detection, List<String> externalNodes, Calibration calibration, Analysis analysis) {
        this(endpoints, telemetry, slo, serviceSlos, detection, externalNodes, calibration, analysis, null);
    }

    public EnvironmentConfig withRemediation(Remediation r) {
        return new EnvironmentConfig(endpoints, telemetry, slo, serviceSlos, detection, externalNodes, calibration, analysis, r);
    }

    /** Where this environment's telemetry lives. */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Endpoints(String prometheusUrl, String tempoUrl, String lokiUrl, String collectorHealthUrl) {
    }

    /**
     * How measurements are read. Each template is a PromQL query returning one series per
     * node, keyed by {@code label}; {@code ${window}} is replaced by {@code rateWindow}
     * ({@code topologyWindow} for the topology query) and the value is multiplied by
     * {@code scale} when set (for example seconds to milliseconds).
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Telemetry(String rateWindow, String topologyWindow, int staleAfterSeconds,
                            Map<String, MetricTemplate> serviceMetrics,
                            Map<String, MetricTemplate> dependencyNodeMetrics,
                            Map<String, MetricTemplate> edgeMetrics,
                            String topologyQuery) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record MetricTemplate(String query, String label, Double scale) {
    }

    /** Service-level objectives; a breach is a candidate incident. */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Slo(double latencyP99Ms, double errorRatePct, double criticalMultiplier) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Detection(int breachSamples, int recoverySamples) {
    }

    /**
     * Learning and retraining schedule. Calibration starts automatically once
     * {@code learningWindowHours} of telemetry exist; an operator may start it earlier
     * once {@code minLearningMinutes} of telemetry exist.
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Calibration(int learningWindowHours, int retrainIntervalDays, int minLearningMinutes) {
    }

    /** Settings the AI engine uses when it calibrates and evaluates this environment. */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Analysis(List<Integer> forecastHorizonsSeconds, double anomalyZFloor, int gateConsecutiveSamples,
                           int lagOrder, double ridgeAlpha, int bootstrapModels,
                           double maxGateFalsePositiveRate, int rcaDelaySeconds, Map<String, Double> rcaWeights) {
    }

    /**
     * Tiered auto-remediation. Actions with {@code tier <= autoExecuteMaxTier} run without a human
     * when every policy rule passes; higher tiers wait for an approval. {@code killSwitch} stops all
     * execution, {@code dryRun} records what would be done without changing anything.
     * {@code executors} holds each executor's settings ({@code enabled} plus its own keys) and is
     * passed to the AI engine as is. {@code targets} maps a topology node to the name an executor
     * knows it by ({@code {"payments": {"kubernetes": "payments-v2"}}}); unmapped nodes use their own name.
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Remediation(boolean dryRun, boolean killSwitch, int autoExecuteMaxTier, double minRcaConfidence,
                              int maxTelemetryAgeSeconds, int maxAutoActionsPerHour, int cooldownMinutes,
                              int recommendationTtlMinutes, double maxAutoBlastRadius, Verification verification,
                              Map<String, Map<String, Object>> executors,
                              Map<String, Map<String, String>> targets,
                              List<Action> actions) {
        public Remediation {
            executors = executors == null ? Map.of() : executors;
            targets = targets == null ? Map.of() : targets;
            actions = actions == null ? List.of() : List.copyOf(actions);
        }

        public boolean executorEnabled(String kind) {
            Map<String, Object> c = executors.get(kind);
            return c != null && Boolean.TRUE.equals(c.get("enabled"));
        }

        public String binding(String node, String executor) {
            return targets.getOrDefault(node, Map.of()).getOrDefault(executor, node);
        }

        public Remediation withExecutor(String kind, Map<String, Object> config) {
            var copy = new java.util.LinkedHashMap<>(executors);
            copy.put(kind, config);
            return new Remediation(dryRun, killSwitch, autoExecuteMaxTier, minRcaConfidence, maxTelemetryAgeSeconds,
                    maxAutoActionsPerHour, cooldownMinutes, recommendationTtlMinutes, maxAutoBlastRadius, verification,
                    copy, targets, actions);
        }

        public Remediation withAutonomy(int maxTier, boolean kill, boolean dry) {
            return new Remediation(dry, kill, maxTier, minRcaConfidence, maxTelemetryAgeSeconds, maxAutoActionsPerHour,
                    cooldownMinutes, recommendationTtlMinutes, maxAutoBlastRadius, verification, executors, targets, actions);
        }
    }

    /**
     * After an action, wait {@code settleSeconds}, then require {@code healthySamples} consecutive
     * healthy samples (within SLO and not flagged by the anomaly gate) before {@code windowSeconds}.
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Verification(int settleSeconds, int windowSeconds, int healthySamples) {
    }

    /**
     * One entry of the action catalog. {@code appliesTo}: unit kinds it can fix (service, database,
     * link). {@code signals}: metrics whose deviation it addresses (latency, error, pool_util, ...).
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Action(String id, String name, String executor, String operation, int tier,
                         List<String> appliesTo, List<String> signals, Map<String, Object> params, String description) {
        public Action {
            appliesTo = appliesTo == null ? List.of() : List.copyOf(appliesTo);
            signals = signals == null ? List.of() : List.copyOf(signals);
            params = params == null ? Map.of() : params;
        }
    }

    /** Executor kinds and the operations whose changes can be rolled back. */
    public static final Map<String, List<String>> EXECUTOR_OPERATIONS = Map.of(
            "docker", List.of("restart", "update_resources"),
            "kubernetes", List.of("rollout_restart", "scale", "update_resources"),
            "webhook", List.of("runbook"));
    public static final List<String> REVERSIBLE_OPERATIONS = List.of("update_resources", "scale");
    /** Restarts change no persistent state: there is nothing to roll back, and nothing left changed. */
    public static final List<String> STATELESS_OPERATIONS = List.of("restart", "rollout_restart");

    /** Metric names a service template map may define. */
    public static final List<String> METRICS = List.of(
            "requestRate", "errorRatePct", "latencyP50", "latencyP95", "latencyP99",
            "dbLatencyP99", "poolUtilizationPct", "poolPending");

    /** Metric names an edge template map may define (series are keyed by client and server labels). */
    public static final List<String> EDGE_METRICS = List.of(
            "requestRate", "errorRatePct", "clientLatencyP95", "serverLatencyP95");
}

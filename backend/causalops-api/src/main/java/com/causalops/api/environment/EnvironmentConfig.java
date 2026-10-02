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
        Calibration calibration) {

    public EnvironmentConfig {
        serviceSlos = serviceSlos == null ? Map.of() : Map.copyOf(serviceSlos);
        externalNodes = externalNodes == null ? List.of() : List.copyOf(externalNodes);
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

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Calibration(int learningWindowHours, int retrainIntervalDays) {
    }

    /** Metric names a service template map may define. */
    public static final List<String> METRICS = List.of(
            "requestRate", "errorRatePct", "latencyP50", "latencyP95", "latencyP99",
            "dbLatencyP95", "poolUtilizationPct", "poolPending");
}

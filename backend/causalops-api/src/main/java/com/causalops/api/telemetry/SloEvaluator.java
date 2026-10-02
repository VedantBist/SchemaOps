package com.causalops.api.telemetry;

import com.causalops.api.environment.EnvironmentConfig;

import java.util.ArrayList;
import java.util.List;

/** Compares a measurement with the environment's SLOs. Pure function, no I/O. */
public final class SloEvaluator {

    public enum Level { UNKNOWN, HEALTHY, DEGRADED, CRITICAL }

    /** One objective that a measurement violated. */
    public record Breach(String service, String metric, double observed, double threshold, Level level) {
    }

    public record Result(Level level, List<Breach> breaches) {
        public String status() {
            return level.name().toLowerCase();
        }
    }

    private SloEvaluator() {
    }

    public static EnvironmentConfig.Slo sloFor(EnvironmentConfig config, String service) {
        return config.serviceSlos().getOrDefault(service, config.slo());
    }

    public static Result evaluate(NodeMeasurement m, EnvironmentConfig.Slo slo) {
        Double p99 = m.get("latencyP99");
        Double err = m.get("errorRatePct");
        if (p99 == null && err == null) return new Result(Level.UNKNOWN, List.of());
        List<Breach> breaches = new ArrayList<>();
        check(m.name(), "latencyP99", p99, slo.latencyP99Ms(), slo.criticalMultiplier(), breaches);
        check(m.name(), "errorRatePct", err, slo.errorRatePct(), slo.criticalMultiplier(), breaches);
        Level level = breaches.stream().map(Breach::level).max(Enum::compareTo).orElse(Level.HEALTHY);
        return new Result(level, breaches);
    }

    private static void check(String service, String metric, Double observed, double threshold,
                              double criticalMultiplier, List<Breach> out) {
        if (observed == null || observed <= threshold) return;
        Level level = observed > threshold * criticalMultiplier ? Level.CRITICAL : Level.DEGRADED;
        out.add(new Breach(service, metric, observed, threshold, level));
    }
}

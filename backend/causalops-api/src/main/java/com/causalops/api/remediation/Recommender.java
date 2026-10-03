package com.causalops.api.remediation;

import com.causalops.api.environment.EnvironmentConfig;
import com.causalops.api.environment.EnvironmentConfig.Action;

import java.util.*;
import java.util.function.BiFunction;

/**
 * Turns ranked root-cause candidates into ranked remediation proposals. Pure function, no I/O.
 *
 * <p>An action is proposed for a candidate when its executor is enabled, it applies to the
 * candidate's kind (service, database, link) and it addresses at least one of the metrics that
 * actually deviated. Score = RCA confidence x learned success rate x expected benefit, where
 * <ul>
 *   <li>the success rate is the Laplace-smoothed share of past executions of this action, for
 *       this kind of deviation in this environment, that passed verification ((s+1)/(n+2));</li>
 *   <li>the expected benefit is the SCM counterfactual of restoring the candidate to its baseline:
 *       the entry-point latency/errors it would avoid, relative to the best candidate.</li>
 * </ul>
 * Ties prefer the lower risk tier.
 */
public final class Recommender {

    private Recommender() {
    }

    /** {@code rank}: position in the RCA ranking (1 = most likely root cause). */
    public record Candidate(String unit, String kind, String target, double confidence, Set<String> metrics,
                            String rootMetric, int rank) {
    }

    /** Counterfactual impact of restoring a unit; null fields when it could not be computed. */
    public record Benefit(Double meanAvoidedLatencyMs, Double peakAvoidedLatencyMs, Double peakAvoidedErrorPct,
                          int restoredNodes, String validity) {
        static final Benefit UNKNOWN = new Benefit(null, null, null, 0, "UNAVAILABLE");
    }

    public record Effectiveness(int successes, int attempts) {
        public double rate() {
            return (successes + 1.0) / (attempts + 2.0);
        }
    }

    public record Proposal(Candidate candidate, Action action, String binding, boolean reversible, boolean stateless,
                           Benefit benefit, Effectiveness effectiveness, double benefitShare, double score,
                           String rationale) {
    }

    public static List<Proposal> propose(List<Candidate> candidates, EnvironmentConfig.Remediation config,
                                         Map<String, Benefit> benefits,
                                         BiFunction<String, String, Effectiveness> effectiveness) {
        double maxLatency = benefits.values().stream().map(Benefit::meanAvoidedLatencyMs).filter(Objects::nonNull)
                .mapToDouble(Double::doubleValue).max().orElse(0);
        double maxError = benefits.values().stream().map(Benefit::peakAvoidedErrorPct).filter(Objects::nonNull)
                .mapToDouble(Double::doubleValue).max().orElse(0);

        List<Proposal> out = new ArrayList<>();
        for (Candidate c : candidates) {
            Benefit b = benefits.getOrDefault(c.unit(), Benefit.UNKNOWN);
            double share = benefitShare(b, maxLatency, maxError);
            for (Action a : config.actions()) {
                if (!config.executorEnabled(a.executor()) || !a.appliesTo().contains(c.kind())) continue;
                if (a.signals().stream().noneMatch(c.metrics()::contains)) continue;
                Effectiveness e = effectiveness.apply(a.id(), c.rootMetric());
                double score = c.confidence() * e.rate() * share;
                boolean reversible = EnvironmentConfig.REVERSIBLE_OPERATIONS.contains(a.operation());
                boolean stateless = EnvironmentConfig.STATELESS_OPERATIONS.contains(a.operation());
                out.add(new Proposal(c, a, config.binding(c.target(), a.executor()), reversible, stateless, b, e, share,
                        score, rationale(c, a, b, e)));
            }
        }
        out.sort(Comparator.comparingDouble(Proposal::score).reversed()
                .thenComparingInt(p -> p.action().tier()));
        return out;
    }

    /** Relative expected benefit in [0.1, 1]; unknown benefit counts as half. */
    static double benefitShare(Benefit b, double maxLatency, double maxError) {
        List<Double> parts = new ArrayList<>();
        if (b.meanAvoidedLatencyMs() != null && maxLatency > 0) parts.add(Math.max(0, b.meanAvoidedLatencyMs()) / maxLatency);
        if (b.peakAvoidedErrorPct() != null && maxError > 0) parts.add(Math.max(0, b.peakAvoidedErrorPct()) / maxError);
        if (parts.isEmpty()) return 0.5;
        double share = parts.stream().mapToDouble(Double::doubleValue).max().orElse(0);
        return Math.max(0.1, Math.min(1.0, share));
    }

    private static String rationale(Candidate c, Action a, Benefit b, Effectiveness e) {
        StringBuilder s = new StringBuilder();
        s.append("%s (%s) is the likely root cause with %.0f%% of the RCA confidence; deviating: %s. "
                .formatted(c.unit(), c.kind(), c.confidence() * 100, String.join(", ", new TreeSet<>(c.metrics()))));
        if (b.meanAvoidedLatencyMs() != null) {
            s.append("Restoring it would avoid about %.0f ms of entry-point latency on average (peak %.0f ms) per the SCM counterfactual (%s). "
                    .formatted(b.meanAvoidedLatencyMs(), b.peakAvoidedLatencyMs() == null ? 0 : b.peakAvoidedLatencyMs(), b.validity()));
        } else {
            s.append("No counterfactual estimate was available. ");
        }
        s.append(e.attempts() == 0
                ? "'%s' has not been tried here before (success prior 50%%).".formatted(a.name())
                : "'%s' passed verification %d of %d times here for %s deviations.".formatted(a.name(), e.successes(), e.attempts(),
                c.rootMetric()));
        return s.toString();
    }
}

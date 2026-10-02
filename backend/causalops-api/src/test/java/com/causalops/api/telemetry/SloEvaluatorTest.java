package com.causalops.api.telemetry;

import com.causalops.api.environment.EnvironmentConfig;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

class SloEvaluatorTest {

    private static final EnvironmentConfig.Slo SLO = new EnvironmentConfig.Slo(500, 5, 2);

    private static NodeMeasurement m(Double p99, Double err) {
        var metrics = new java.util.HashMap<String, Double>();
        if (p99 != null) metrics.put("latencyP99", p99);
        if (err != null) metrics.put("errorRatePct", err);
        return new NodeMeasurement("svc", "service", Map.copyOf(metrics));
    }

    @Test
    void noMeasurementIsUnknownNotHealthy() {
        assertEquals(SloEvaluator.Level.UNKNOWN, SloEvaluator.evaluate(m(null, null), SLO).level());
    }

    @Test
    void withinSloIsHealthy() {
        var r = SloEvaluator.evaluate(m(120.0, 0.5), SLO);
        assertEquals(SloEvaluator.Level.HEALTHY, r.level());
        assertTrue(r.breaches().isEmpty());
    }

    @Test
    void breachAndCriticalMultiplier() {
        assertEquals(SloEvaluator.Level.DEGRADED, SloEvaluator.evaluate(m(700.0, 0.0), SLO).level());
        var critical = SloEvaluator.evaluate(m(1200.0, 12.0), SLO);
        assertEquals(SloEvaluator.Level.CRITICAL, critical.level());
        assertEquals(2, critical.breaches().size());
    }
}

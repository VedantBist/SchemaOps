package com.causalops.api.telemetry;

import java.util.Map;

/**
 * Values measured for one service or dependency node in one ingestion cycle.
 * A metric is absent from {@code metrics} when Prometheus had no data for it.
 */
public record NodeMeasurement(String name, String kind, Map<String, Double> metrics) {

    public Double get(String metric) {
        return metrics.get(metric);
    }

    public boolean hasTraffic() {
        Double rps = metrics.get("requestRate");
        return rps != null && rps > 0;
    }
}

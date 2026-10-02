package com.causalops.api.telemetry;

import java.util.Map;

/** Values measured for one call edge (client to server) in one ingestion cycle. */
public record EdgeMeasurement(String client, String server, Map<String, Double> metrics) {

    public Double get(String metric) {
        return metrics.get(metric);
    }
}

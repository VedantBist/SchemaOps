package com.causalops.api.telemetry;

import com.causalops.api.environment.EnvironmentConfig;
import com.causalops.api.environment.EnvironmentConfig.MetricTemplate;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Turns an environment's PromQL templates into per-node measurements. One query per metric,
 * grouped by node label, so the cost does not grow with the number of services.
 */
@Component
public class MeasurementCollector {

    private static final Logger log = LoggerFactory.getLogger(MeasurementCollector.class);

    private final PrometheusClient prometheus;

    public MeasurementCollector(PrometheusClient prometheus) {
        this.prometheus = prometheus;
    }

    /**
     * @param nodes node name to kind; services use {@code serviceMetrics}, other kinds use
     *              {@code dependencyNodeMetrics} (measured from the callers' side).
     */
    public Map<String, NodeMeasurement> collect(EnvironmentConfig config, Map<String, String> nodes) {
        String base = config.endpoints().prometheusUrl();
        var telemetry = config.telemetry();
        Map<String, Map<String, Double>> serviceValues = run(base, telemetry.serviceMetrics(), telemetry.rateWindow());
        Map<String, Map<String, Double>> nodeValues = telemetry.dependencyNodeMetrics() == null ? Map.of()
                : run(base, telemetry.dependencyNodeMetrics(), telemetry.rateWindow());

        Map<String, NodeMeasurement> out = new LinkedHashMap<>();
        nodes.forEach((name, kind) -> {
            Map<String, Map<String, Double>> source = "service".equals(kind) ? serviceValues : nodeValues;
            Map<String, Double> metrics = new HashMap<>();
            source.forEach((metric, byNode) -> {
                Double v = byNode.get(name);
                if (v != null) metrics.put(metric, v);
            });
            // Traffic observed but no error series means no errors were recorded in the window.
            if (metrics.containsKey("requestRate") && !metrics.containsKey("errorRatePct") && source.containsKey("errorRatePct")) {
                metrics.put("errorRatePct", 0.0);
            }
            out.put(name, new NodeMeasurement(name, kind, Map.copyOf(metrics)));
        });
        return out;
    }

    /** metric -> (node label value -> value). A failing template is logged and skipped, not fatal. */
    private Map<String, Map<String, Double>> run(String base, Map<String, MetricTemplate> templates, String window) {
        Map<String, Map<String, Double>> result = new HashMap<>();
        templates.forEach((metric, tpl) -> {
            try {
                List<PrometheusClient.Sample> samples = prometheus.query(base, render(tpl.query(), window));
                Map<String, Double> byNode = new HashMap<>();
                double scale = tpl.scale() == null ? 1.0 : tpl.scale();
                for (var s : samples) {
                    String node = s.labels().get(tpl.label());
                    if (node != null && !node.isBlank()) byNode.put(node, s.value() * scale);
                }
                result.put(metric, byNode);
            } catch (PrometheusClient.PrometheusException e) {
                log.warn("Metric '{}' could not be read: {}", metric, e.getMessage());
            }
        });
        return result;
    }

    static String render(String template, String window) {
        return template.replace("${window}", window);
    }
}

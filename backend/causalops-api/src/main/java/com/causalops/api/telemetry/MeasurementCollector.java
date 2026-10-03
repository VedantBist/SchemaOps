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

    /**
     * Per-edge measurements from the edge templates (series keyed by {@code client} and
     * {@code server} labels). Edges touching an external pseudo-node are skipped.
     */
    public List<EdgeMeasurement> collectEdges(EnvironmentConfig config) {
        var templates = config.telemetry().edgeMetrics();
        if (templates == null || templates.isEmpty()) return List.of();
        String base = config.endpoints().prometheusUrl();
        var external = java.util.Set.copyOf(config.externalNodes());
        Map<String, Map<String, Double>> byEdge = new LinkedHashMap<>();
        boolean errorQueryRan = false;
        for (var entry : templates.entrySet()) {
            String metric = entry.getKey();
            MetricTemplate tpl = entry.getValue();
            List<PrometheusClient.Sample> samples;
            try {
                samples = prometheus.query(base, render(tpl.query(), config.telemetry().rateWindow()));
            } catch (PrometheusClient.PrometheusException e) {
                log.warn("Edge metric '{}' could not be read: {}", metric, e.getMessage());
                continue;
            }
            if ("errorRatePct".equals(metric)) errorQueryRan = true;
            double scale = tpl.scale() == null ? 1.0 : tpl.scale();
            for (var s : samples) {
                String client = s.labels().get("client");
                String server = s.labels().get("server");
                if (client == null || server == null || external.contains(client) || external.contains(server)) continue;
                byEdge.computeIfAbsent(client + '\u0000' + server, k -> new HashMap<>()).put(metric, s.value() * scale);
            }
        }
        List<EdgeMeasurement> out = new java.util.ArrayList<>();
        for (var e : byEdge.entrySet()) {
            String[] parts = e.getKey().split("\u0000", 2);
            Map<String, Double> metrics = e.getValue();
            if (errorQueryRan && metrics.containsKey("requestRate") && !metrics.containsKey("errorRatePct")) {
                metrics.put("errorRatePct", 0.0);
            }
            out.add(new EdgeMeasurement(parts[0], parts[1], Map.copyOf(metrics)));
        }
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

    public static String render(String template, String window) {
        return template.replace("${window}", window);
    }
}

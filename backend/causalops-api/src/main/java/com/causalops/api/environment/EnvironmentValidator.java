package com.causalops.api.environment;

import com.causalops.api.telemetry.MeasurementCollector;
import com.causalops.api.telemetry.PrometheusClient;
import com.causalops.api.telemetry.TopologyDiscovery;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

import java.util.*;

/**
 * Tries a proposed environment configuration against the real backends before it is saved:
 * reachability of Prometheus, Tempo, Loki and the collector, how many series every metric
 * template returns (and for which nodes), and the topology the service-graph query discovers.
 */
@Service
public class EnvironmentValidator {

    private final EnvironmentService environments;
    private final PrometheusClient prometheus;
    private final RestClient http;

    public EnvironmentValidator(EnvironmentService environments, PrometheusClient prometheus, RestClient http) {
        this.environments = environments;
        this.prometheus = prometheus;
        this.http = http;
    }

    public Map<String, Object> validate(EnvironmentConfig proposed) {
        Map<String, Object> out = new LinkedHashMap<>();
        EnvironmentConfig cfg;
        try {
            cfg = environments.preview(proposed);
        } catch (IllegalArgumentException e) {
            out.put("valid", false);
            out.put("configError", e.getMessage());
            return out;
        }
        var e = cfg.endpoints();
        var t = cfg.telemetry();
        Map<String, Object> endpoints = new LinkedHashMap<>();
        endpoints.put("prometheus", prometheus.ready(e.prometheusUrl()) ? "UP" : "DOWN");
        endpoints.put("tempo", ping(e.tempoUrl(), "/ready"));
        endpoints.put("loki", ping(e.lokiUrl(), "/ready"));
        endpoints.put("collector", ping(e.collectorHealthUrl(), "/"));
        out.put("endpoints", endpoints);

        Map<String, Object> metrics = new LinkedHashMap<>();
        if ("UP".equals(endpoints.get("prometheus"))) {
            checkTemplates(e.prometheusUrl(), t.serviceMetrics(), t.rateWindow(), "service", metrics);
            checkTemplates(e.prometheusUrl(), t.dependencyNodeMetrics(), t.rateWindow(), "dependency", metrics);
            checkTemplates(e.prometheusUrl(), t.edgeMetrics(), t.rateWindow(), "edge", metrics);
            try {
                var graph = prometheus.query(e.prometheusUrl(), MeasurementCollector.render(t.topologyQuery(), t.topologyWindow()));
                var found = TopologyDiscovery.discover(graph, cfg.externalNodes(), List.of());
                out.put("topology", Map.of("nodes", found.nodes(), "edges", found.edges().stream().map(x -> Map.of(
                        "client", x.client(), "server", x.server(), "callRate", x.callRate(),
                        "connectionType", String.valueOf(x.connectionType()))).toList()));
            } catch (RuntimeException ex) {
                out.put("topology", Map.of("error", String.valueOf(ex.getMessage())));
            }
        }
        out.put("metrics", metrics);
        boolean hasRate = metrics.get("service.requestRate") instanceof Map<?, ?> m && ((Number) m.get("series")).intValue() > 0;
        out.put("valid", "UP".equals(endpoints.get("prometheus")) && hasRate);
        out.put("config", cfg);
        return out;
    }

    private void checkTemplates(String base, Map<String, EnvironmentConfig.MetricTemplate> templates, String window, String group,
                                Map<String, Object> out) {
        if (templates == null) return;
        templates.forEach((name, tpl) -> {
            Map<String, Object> r = new LinkedHashMap<>();
            try {
                var samples = prometheus.query(base, MeasurementCollector.render(tpl.query(), window));
                r.put("series", samples.size());
                String label = tpl.label() == null ? "server" : tpl.label();
                r.put("nodes", samples.stream().map(s -> s.labels().getOrDefault(label, s.labels().getOrDefault("client", "?")))
                        .distinct().sorted().limit(20).toList());
            } catch (RuntimeException ex) {
                r.put("series", 0);
                r.put("error", String.valueOf(ex.getMessage()));
            }
            out.put(group + "." + name, r);
        });
    }

    private String ping(String base, String path) {
        if (base == null || base.isBlank()) return "NOT_CONFIGURED";
        try {
            http.get().uri(base.replaceAll("/+$", "") + path).retrieve().toBodilessEntity();
            return "UP";
        } catch (RuntimeException ex) {
            return "DOWN";
        }
    }
}

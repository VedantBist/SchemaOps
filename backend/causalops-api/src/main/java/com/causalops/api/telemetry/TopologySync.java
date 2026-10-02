package com.causalops.api.telemetry;

import com.causalops.api.environment.Environment;
import com.causalops.api.environment.EnvironmentService;
import com.causalops.api.events.EventBus;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/** Keeps each environment's services and dependencies in step with the observed call graph. */
@Component
public class TopologySync {

    private static final Logger log = LoggerFactory.getLogger(TopologySync.class);

    private final EnvironmentService environments;
    private final PrometheusClient prometheus;
    private final TopologyRepository topology;
    private final TelemetryRepository telemetry;
    private final EventBus events;

    public TopologySync(EnvironmentService environments, PrometheusClient prometheus, TopologyRepository topology,
                        TelemetryRepository telemetry, EventBus events) {
        this.environments = environments;
        this.prometheus = prometheus;
        this.topology = topology;
        this.telemetry = telemetry;
        this.events = events;
    }

    @Scheduled(initialDelayString = "${causalops.topology.initial-delay-ms:5000}",
               fixedDelayString = "${causalops.topology.sync-interval-ms:15000}")
    public void syncAll() {
        for (Environment env : environments.monitored()) {
            try {
                sync(env);
            } catch (RuntimeException e) {
                log.warn("Topology sync failed for environment {}: {}", env.name(), e.getMessage());
            }
        }
    }

    public TopologyDiscovery.Result sync(Environment env) {
        var cfg = env.config();
        var t = cfg.telemetry();
        String base = cfg.endpoints().prometheusUrl();

        var graph = prometheus.query(base, MeasurementCollector.render(t.topologyQuery(), t.topologyWindow()));
        Set<String> withTraffic = new HashSet<>();
        var rpsTemplate = t.serviceMetrics().get("requestRate");
        for (var s : prometheus.query(base, MeasurementCollector.render(rpsTemplate.query(), t.topologyWindow()))) {
            String name = s.labels().get(rpsTemplate.label());
            if (name != null && s.value() > 0) withTraffic.add(name);
        }

        TopologyDiscovery.Result found = TopologyDiscovery.discover(graph, cfg.externalNodes(), withTraffic);
        int newNodes = 0;
        int newEdges = 0;
        for (Map.Entry<String, String> n : found.nodes().entrySet()) {
            if (topology.upsertDiscoveredNode(env.id(), n.getKey(), n.getValue())) newNodes++;
        }
        for (var e : found.edges()) {
            if (topology.upsertDiscoveredEdge(env.id(), e.client(), e.server(), e.connectionType(), e.callRate(), e.failedRate())) newEdges++;
        }
        int stale = topology.markStaleNodes(env.id(), t.staleAfterSeconds());
        telemetry.refreshObservedBaselines(env.id(), cfg.calibration().learningWindowHours(), 12);

        if (newNodes > 0 || newEdges > 0 || stale > 0) {
            log.info("Topology of {}: +{} nodes, +{} edges, {} went stale", env.name(), newNodes, newEdges, stale);
            events.emit("topology.changed", env.id(),
                    Map.of("newNodes", newNodes, "newEdges", newEdges, "staleNodes", stale));
        }
        return found;
    }
}

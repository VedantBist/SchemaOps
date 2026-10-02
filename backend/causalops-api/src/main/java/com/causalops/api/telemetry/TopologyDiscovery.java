package com.causalops.api.telemetry;

import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Builds nodes and edges from service-graph samples
 * ({@code client}, {@code server}, {@code connection_type}, {@code failed} labels).
 * Pure function: no I/O, so it is unit-tested directly.
 */
public final class TopologyDiscovery {

    public record Edge(String client, String server, String connectionType, double callRate, double failedRate) {
    }

    public record Result(Map<String, String> nodes, List<Edge> edges) {
    }

    private TopologyDiscovery() {
    }

    /**
     * @param externalNodes pseudo-nodes the collector uses for callers it cannot identify
     *                      (for example "user" for untraced clients); they never become services.
     * @param servicesWithTraffic services seen emitting server spans, even if they have no edges yet.
     */
    public static Result discover(List<PrometheusClient.Sample> samples, Collection<String> externalNodes,
                                  Collection<String> servicesWithTraffic) {
        Set<String> external = Set.copyOf(externalNodes);
        Map<String, String> nodes = new LinkedHashMap<>();
        Map<String, double[]> rates = new LinkedHashMap<>();   // key "client\u0000server" -> [total, failed]
        Map<String, String> types = new LinkedHashMap<>();

        for (var s : samples) {
            String client = s.labels().get("client");
            String server = s.labels().get("server");
            if (client == null || server == null || s.value() <= 0) continue;
            String type = s.labels().getOrDefault("connection_type", "");
            boolean clientExternal = external.contains(client);
            boolean serverExternal = external.contains(server);
            if (serverExternal) continue;

            nodes.put(server, kindOf(type, clientExternal));
            if (clientExternal) continue;
            nodes.putIfAbsent(client, "service");

            String key = client + '\u0000' + server;
            double[] r = rates.computeIfAbsent(key, k -> new double[2]);
            r[0] += s.value();
            if ("true".equals(s.labels().get("failed"))) r[1] += s.value();
            types.put(key, type.isEmpty() ? null : type);
        }
        for (String svc : servicesWithTraffic) {
            if (!external.contains(svc)) nodes.putIfAbsent(svc, "service");
        }

        List<Edge> edges = rates.entrySet().stream().map(e -> {
            String[] parts = e.getKey().split("\u0000", 2);
            return new Edge(parts[0], parts[1], types.get(e.getKey()), e.getValue()[0], e.getValue()[1]);
        }).toList();
        return new Result(nodes, edges);
    }

    /**
     * The collector marks a database call with connection_type=database and an uninstrumented
     * peer with virtual_node. A virtual_node edge whose client is external means the server is
     * a real, instrumented service receiving untraced traffic.
     */
    static String kindOf(String connectionType, boolean clientExternal) {
        return switch (connectionType) {
            case "database" -> "database";
            case "messaging_system" -> "messaging";
            case "virtual_node" -> clientExternal ? "service" : "external";
            default -> "service";
        };
    }
}

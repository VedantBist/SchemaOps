package com.causalops.api.telemetry;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

class TopologyDiscoveryTest {

    private static PrometheusClient.Sample edge(String client, String server, String type, String failed, double rate) {
        var labels = new java.util.HashMap<String, String>();
        labels.put("client", client);
        labels.put("server", server);
        labels.put("failed", failed);
        if (type != null) labels.put("connection_type", type);
        return new PrometheusClient.Sample(labels, rate);
    }

    @Test
    void buildsNodesAndEdgesFromServiceGraph() {
        var result = TopologyDiscovery.discover(List.of(
                edge("user", "api-gateway", "virtual_node", "false", 5.0),
                edge("api-gateway", "order-service", null, "false", 4.0),
                edge("api-gateway", "order-service", null, "true", 1.0),
                edge("inventory-service", "inventory-db", "database", "false", 5.0)
        ), List.of("user", "unknown"), List.of());

        assertEquals(Map.of("api-gateway", "service", "order-service", "service",
                "inventory-service", "service", "inventory-db", "database"), result.nodes());
        assertEquals(2, result.edges().size(), "external caller 'user' must not become an edge");
        var gw = result.edges().stream().filter(e -> e.client().equals("api-gateway")).findFirst().orElseThrow();
        assertEquals(5.0, gw.callRate(), 1e-9, "failed and successful calls are summed");
        assertEquals(1.0, gw.failedRate(), 1e-9);
    }

    @Test
    void ignoresIdleEdgesAndExternalServers() {
        var result = TopologyDiscovery.discover(List.of(
                edge("api-gateway", "order-service", null, "false", 0.0),
                edge("order-service", "unknown", null, "true", 2.0)
        ), List.of("user", "unknown"), List.of());
        assertTrue(result.edges().isEmpty());
        assertTrue(result.nodes().isEmpty());
    }

    @Test
    void addsServicesThatOnlyEmitServerSpans() {
        var result = TopologyDiscovery.discover(List.of(), List.of("user"), List.of("lonely-service", "user"));
        assertEquals(Map.of("lonely-service", "service"), result.nodes());
    }

    @Test
    void uninstrumentedPeerIsExternal() {
        assertEquals("external", TopologyDiscovery.kindOf("virtual_node", false));
        assertEquals("service", TopologyDiscovery.kindOf("virtual_node", true));
        assertEquals("messaging", TopologyDiscovery.kindOf("messaging_system", false));
    }
}

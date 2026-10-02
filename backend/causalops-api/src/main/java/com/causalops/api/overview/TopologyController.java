package com.causalops.api.overview;

import com.causalops.api.environment.EnvironmentService;
import com.causalops.api.telemetry.TopologyRepository;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.Set;
import java.util.UUID;

/**
 * Manual additions to the discovered topology, for parts of a system that emit no traces
 * (for example a legacy dependency). Discovery never overwrites a manual entry.
 */
@RestController
@RequestMapping("/api/topology")
public class TopologyController {

    private static final Set<String> KINDS = Set.of("service", "database", "messaging", "external");

    private final EnvironmentService environments;
    private final TopologyRepository topology;

    public TopologyController(EnvironmentService environments, TopologyRepository topology) {
        this.environments = environments;
        this.topology = topology;
    }

    public record ManualNode(String name, String kind) {
    }

    public record ManualEdge(String source, String target) {
    }

    @PostMapping("/nodes")
    @ResponseStatus(HttpStatus.CREATED)
    public void addNode(@RequestParam(required = false) UUID environmentId, @RequestBody ManualNode node) {
        if (node.name() == null || node.name().isBlank()) throw new IllegalArgumentException("name is required");
        String kind = node.kind() == null ? "service" : node.kind();
        if (!KINDS.contains(kind)) throw new IllegalArgumentException("kind must be one of " + KINDS);
        topology.addManualNode(environments.resolve(environmentId).id(), node.name().trim(), kind);
    }

    @PostMapping("/edges")
    @ResponseStatus(HttpStatus.CREATED)
    public void addEdge(@RequestParam(required = false) UUID environmentId, @RequestBody ManualEdge edge) {
        if (edge.source() == null || edge.target() == null || edge.source().equals(edge.target())) {
            throw new IllegalArgumentException("source and target are required and must differ");
        }
        UUID env = environments.resolve(environmentId).id();
        var known = topology.nodes(env).stream().map(TopologyRepository.Node::name).toList();
        if (!known.contains(edge.source()) || !known.contains(edge.target())) {
            throw new IllegalArgumentException("Both ends must be existing nodes; add missing nodes first");
        }
        topology.addManualEdge(env, edge.source(), edge.target());
    }
}

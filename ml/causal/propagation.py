"""
Causal Propagation Path Analysis for CausalOps SCM.

Traces directed sequences of stable causal edges from a suspected root-cause source
to downstream customer-facing symptoms (e.g. api-gateway SLA metrics).
Computes hop count, cumulative attenuation/transmission effect, and cumulative lag.
"""

from typing import Dict, List, Tuple, Any, Optional
from .edges import CausalEdge, CausalGraph


class PropagationPath:
    """Represents a directed causal pathway through the microservice variable graph."""

    def __init__(self, edges: List[CausalEdge]):
        self.edges = edges
        self.hop_count = len(edges)
        self.total_lag_seconds = sum(e.lag for e in edges)

        # Cumulative transmission effect along the linear path: product of coefficients
        cum_eff = 1.0
        for e in edges:
            cum_eff *= e.coefficient
        self.cumulative_effect = cum_eff
        self.absolute_effect = abs(cum_eff)

    @property
    def source_node(self) -> str:
        return self.edges[0].source_node if self.edges else ""

    @property
    def source_variable(self) -> str:
        return self.edges[0].source_variable if self.edges else ""

    @property
    def target_node(self) -> str:
        return self.edges[-1].target_node if self.edges else ""

    @property
    def target_variable(self) -> str:
        return self.edges[-1].target_variable if self.edges else ""

    def to_dict(self) -> Dict[str, Any]:
        path_str_elements = []
        for i, e in enumerate(self.edges):
            if i == 0:
                path_str_elements.append(f"{e.source_node}.{e.source_variable}")
            path_str_elements.append(f"--(lag {e.lag}s, coef {e.coefficient:+.3f})--> {e.target_node}.{e.target_variable}")

        return {
            "source": f"{self.source_node}.{self.source_variable}",
            "target": f"{self.target_node}.{self.target_variable}",
            "hop_count": self.hop_count,
            "total_lag_seconds": self.total_lag_seconds,
            "cumulative_effect": round(self.cumulative_effect, 6),
            "absolute_effect": round(self.absolute_effect, 6),
            "path_description": " ".join(path_str_elements),
            "edges": [e.to_dict() for e in self.edges],
        }


def find_propagation_paths(
    graph: CausalGraph,
    source_node: str,
    target_node: str = "api-gateway",
    source_variable: Optional[str] = None,
    target_variable: Optional[str] = None,
    max_depth: int = 6,
) -> List[PropagationPath]:
    """
    Finds all directed propagation paths in the graph connecting source_node to target_node.
    Paths are ranked in descending order of absolute cumulative effect.
    """
    discovered_paths: List[PropagationPath] = []

    # Map outgoing edges for fast traversal: (node, var) -> List[CausalEdge]
    adj: Dict[Tuple[str, str], List[CausalEdge]] = {}
    for edge in graph.edges:
        key = (edge.source_node, edge.source_variable)
        if key not in adj:
            adj[key] = []
        adj[key].append(edge)

    def dfs(
        curr_node: str,
        curr_var: str,
        current_path: List[CausalEdge],
        visited_nodes: set,
    ):
        if len(current_path) >= max_depth:
            return

        # Check if we reached the target node
        if curr_node == target_node:
            if target_variable is None or curr_var == target_variable:
                if current_path:
                    discovered_paths.append(PropagationPath(list(current_path)))
            # If target reached, we stop traversing further downstream from gateway
            return

        key = (curr_node, curr_var)
        for edge in adj.get(key, []):
            next_node = edge.target_node
            next_var = edge.target_variable

            # Avoid cyclic visits to the same service to maintain DAG path semantics
            if next_node in visited_nodes and next_node != curr_node:
                continue

            current_path.append(edge)
            new_visited = set(visited_nodes)
            new_visited.add(next_node)

            dfs(next_node, next_var, current_path, new_visited)
            current_path.pop()

    # Find starting nodes
    start_keys = [
        k for k in adj.keys()
        if k[0] == source_node and (source_variable is None or k[1] == source_variable)
    ]

    for s_node, s_var in start_keys:
        dfs(s_node, s_var, [], {s_node})

    # Sort paths by absolute cumulative effect descending
    discovered_paths.sort(key=lambda p: p.absolute_effect, reverse=True)
    return discovered_paths


def summarize_node_propagation(
    graph: CausalGraph,
    source_node: str,
    target_node: str = "api-gateway",
) -> Dict[str, Any]:
    """
    Summarizes all propagation paths from source_node to target_node.
    """
    paths = find_propagation_paths(graph, source_node, target_node)
    top_path = paths[0].to_dict() if paths else None

    return {
        "source_node": source_node,
        "target_node": target_node,
        "total_paths_found": len(paths),
        "primary_path": top_path,
        "all_paths": [p.to_dict() for p in paths[:5]],  # Top 5
    }

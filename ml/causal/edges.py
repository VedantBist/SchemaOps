"""
Causal Edge & Graph Representation for CausalOps SCM.

Represents learned candidate edges, stable edges, and propagation networks.
Provides serialization, filtering, and graph traversal utilities.
"""

from dataclasses import dataclass, asdict
from typing import Dict, List, Any, Optional


@dataclass
class CausalEdge:
    """Represents a single directed lagged causal edge between microservice telemetry variables."""
    source_node: str
    source_variable: str
    target_node: str
    target_variable: str
    lag: int
    coefficient: float
    standardized_effect: float
    absolute_effect: float
    sign: str
    confidence: float
    allowed_by_topology: bool
    retained: bool
    selection_frequency: Optional[float] = None
    coefficient_std: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["coefficient"] = round(float(self.coefficient), 6)
        d["standardized_effect"] = round(float(self.standardized_effect), 6)
        d["absolute_effect"] = round(float(self.absolute_effect), 6)
        d["confidence"] = round(float(self.confidence), 4)
        if self.selection_frequency is not None:
            d["selection_frequency"] = round(float(self.selection_frequency), 4)
        if self.coefficient_std is not None:
            d["coefficient_std"] = round(float(self.coefficient_std), 6)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CausalEdge":
        return cls(**data)


class CausalGraph:
    """Represents a set of candidate or stable causal edges across the microservice topology."""

    def __init__(self, edges: Optional[List[CausalEdge]] = None, graph_name: str = "CausalGraph"):
        self.edges: List[CausalEdge] = edges or []
        self.graph_name = graph_name

    def add_edge(self, edge: CausalEdge) -> None:
        self.edges.append(edge)

    def filter_retained(self) -> "CausalGraph":
        return CausalGraph(
            [e for e in self.edges if e.retained],
            graph_name=f"{self.graph_name}_retained"
        )

    def filter_stable(
        self,
        min_frequency: float = 0.60,
        min_magnitude: float = 0.05,
    ) -> "CausalGraph":
        """
        Filters edges satisfying:
        1. allowed_by_topology == True
        2. selection_frequency >= min_frequency
        3. absolute_effect >= min_magnitude
        """
        stable_edges = [
            e for e in self.edges
            if e.allowed_by_topology
            and (e.selection_frequency is None or e.selection_frequency >= min_frequency)
            and e.absolute_effect >= min_magnitude
        ]
        return CausalGraph(stable_edges, graph_name=f"{self.graph_name}_stable")

    def get_outgoing_edges(
        self,
        source_node: str,
        source_variable: Optional[str] = None,
    ) -> List[CausalEdge]:
        out = []
        for e in self.edges:
            if e.source_node == source_node:
                if source_variable is None or e.source_variable == source_variable:
                    out.append(e)
        return out

    def get_incoming_edges(
        self,
        target_node: str,
        target_variable: Optional[str] = None,
    ) -> List[CausalEdge]:
        inc = []
        for e in self.edges:
            if e.target_node == target_node:
                if target_variable is None or e.target_variable == target_variable:
                    inc.append(e)
        return inc

    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_name": self.graph_name,
            "edge_count": len(self.edges),
            "edges": [e.to_dict() for e in self.edges],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CausalGraph":
        edges = [CausalEdge.from_dict(d) for d in data.get("edges", [])]
        return cls(edges=edges, graph_name=data.get("graph_name", "CausalGraph"))

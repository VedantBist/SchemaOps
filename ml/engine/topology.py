"""The call graph of one environment, as discovered by the platform."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Topology:
    """Nodes (name -> kind) and directed call edges (client, server)."""

    nodes: dict[str, str]
    edges: tuple[tuple[str, str], ...]
    _callees: dict[str, tuple[str, ...]] = field(default_factory=dict, compare=False, repr=False)
    _callers: dict[str, tuple[str, ...]] = field(default_factory=dict, compare=False, repr=False)

    @staticmethod
    def build(nodes: dict[str, str], edges) -> "Topology":
        edges = tuple(sorted({(c, s) for c, s in edges if c in nodes and s in nodes and c != s}))
        callees = {n: tuple(sorted(s for c, s in edges if c == n)) for n in nodes}
        callers = {n: tuple(sorted(c for c, s in edges if s == n)) for n in nodes}
        return Topology(dict(sorted(nodes.items())), edges, callees, callers)

    @staticmethod
    def from_rows(node_rows: list[dict], edge_rows: list[dict]) -> "Topology":
        return Topology.build({r["name"]: r["kind"] for r in node_rows},
                              [(r["client"], r["server"]) for r in edge_rows])

    def callees(self, node: str) -> tuple[str, ...]:
        return self._callees.get(node, ())

    def callers(self, node: str) -> tuple[str, ...]:
        return self._callers.get(node, ())

    def entry_nodes(self) -> list[str]:
        """Nodes nothing else in the graph calls: where users experience the system."""
        entries = [n for n, kind in self.nodes.items() if kind == "service" and not self.callers(n)]
        return entries or [n for n in self.nodes if self.nodes[n] == "service"]

    def descendants_impacted(self, root: str) -> set[str]:
        """Nodes whose behaviour can be affected by root: its callers, transitively (impact flows up the call chain)."""
        seen, stack = set(), [root]
        while stack:
            n = stack.pop()
            for c in self.callers(n):
                if c not in seen:
                    seen.add(c)
                    stack.append(c)
        return seen

    def to_dict(self) -> dict:
        return {"nodes": self.nodes, "edges": [list(e) for e in self.edges]}

    @staticmethod
    def from_dict(d: dict) -> "Topology":
        return Topology.build(d["nodes"], [tuple(e) for e in d["edges"]])

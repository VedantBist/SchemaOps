"""Explainable RCA baseline; it is not a causal-discovery or ML model.

API edges are caller -> dependency.  For fault reasoning they are reversed:
upstream dependency -> downstream caller.
"""
from collections import defaultdict
from datetime import datetime, timezone
import networkx as nx

WEIGHTS = {"anomaly": .30, "temporalPrecedence": .25, "dependency": .20,
           "propagationConsistency": .15, "metricCorrelation": .10}
THRESHOLD = .20


def _time(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if not value:
        return datetime.max.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _first(rows):
    return min((_time(row.get("timestamp")) for row in rows
                if float(row.get("anomaly") or 0) >= THRESHOLD), default=None)


def _temporal(service, firsts, graph):
    """Candidates preceding anomalous causal descendants score highest; ties are neutral."""
    first = firsts[service]
    if first is None:
        return 0.
    downstream = [firsts[node] for node in nx.descendants(graph, service) if firsts.get(node)]
    if not downstream:
        return .5
    return (sum(first < item for item in downstream) +
            .5 * sum(first == item for item in downstream)) / len(downstream)


def score(topology, telemetry):
    graph = nx.DiGraph()
    for edge in topology.get("edges", []):
        graph.add_edge(edge["target"], edge["source"])
    by_service = defaultdict(list)
    for row in telemetry:
        by_service[row["service"]].append(row)
        graph.add_node(row["service"])
    for rows in by_service.values():
        rows.sort(key=lambda row: _time(row.get("timestamp")))
    firsts = {service: _first(rows) for service, rows in by_service.items()}
    candidates, evidence = [], []
    for service, rows in list(by_service.items()):
        latest = rows[-1]
        anomaly = float(latest.get("anomaly") or 0)
        descendants = list(nx.descendants(graph, service))
        affected = [node for node in descendants if by_service.get(node) and float(by_service[node][-1].get("anomaly") or 0) >= THRESHOLD]
        if anomaly < THRESHOLD:
            dependency = 0.
            temporal = 0.
            propagation = 0.
        else:
            dependency = len(affected) / max(1, len(descendants))
            temporal = _temporal(service, firsts, graph)
            propagation = temporal * dependency if descendants else anomaly * temporal
        latency, errors = float(latest.get("latency") or 0), float(latest.get("errorRate") or 0)
        correlation = min(1., .7 * anomaly + .2 * min(1., latency / 1000) + .1 * min(1., errors / 100))
        signals = {"anomaly": round(anomaly, 4), "temporalPrecedence": round(temporal, 4),
                   "dependency": round(dependency, 4), "propagationConsistency": round(propagation, 4),
                   "metricCorrelation": round(correlation, 4)}
        value = sum(WEIGHTS[key] * value for key, value in signals.items())
        candidates.append({"service": service, "score": round(value, 4),
                           "confidence": round(min(.99, value + .12), 4), "signals": signals})
        if anomaly >= THRESHOLD:
            evidence.append({"timestamp": latest.get("timestamp"), "source": "telemetry", "service": service,
                             "metric": "p99_latency", "observedValue": latest.get("latency"), "baseline": None,
                             "relationship": "observed anomalous telemetry", "classification": "OBSERVED"})
    candidates.sort(key=lambda row: (-row["score"], row["service"]))
    return {"methodology": "HEURISTIC BASELINE: weighted anomaly, timestamp precedence, and dependency propagation",
            "weights": WEIGHTS, "candidates": candidates,
            "evidence": sorted(evidence, key=lambda row: _time(row["timestamp"]))}

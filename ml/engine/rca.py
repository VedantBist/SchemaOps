"""Root-cause ranking over services, databases and call links.

For each candidate unit (a node, or a link between two nodes) these measured signals are combined:

  counterfactual  the share of the whole incident's anomalous deviation that disappears when the
              SCM is rolled forward with this unit restored to its baseline. The origin explains
              itself and everything downstream; a symptom only itself and what sits above it.
  residual    the share of a unit's deviation around its onset that its causal parents in the SCM
              cannot explain (energy of the positive residual over energy of the deviation),
              scaled so the least-explained unit scores 1. A caller slowed by its callee is
              explained by the callee; the origin is not.
  anomaly     how far the unit left its learned baseline.
  precedence  how early it deviated relative to the other anomalous units.
  graph       personalized PageRank walking from symptoms toward their dependencies.

The weights come from the environment's configuration and are evaluated against labelled
incidents during calibration.
"""
from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
import numpy as np
import pandas as pd

from .anomaly import AnomalyModel
from .scm import LaggedSCM
from .topology import Topology
from .window import Frame, parse

ONSET_STEPS = 6   # causes and effects are told apart around onset; afterwards persistence explains both
ONSET_TIE_STEPS = 1.5   # a new Prometheus series needs two scrapes before rate() reports it, so
                        # onsets within about one sampling step are treated as simultaneous


@dataclass
class Unit:
    name: str          # node name, or "client->server" for a link
    kind: str          # node kind, or "link"
    target: str        # the node the unit belongs to (a link's server)
    variables: list[str]


def units(topology: Topology, columns: list[str]) -> list[Unit]:
    """Groups variables into the components a responder can act on.

    A service's ``db_latency`` is the client-side duration of its calls to its database, i.e. a
    measurement of that database; it belongs to the database's unit when there is exactly one.
    """
    out: dict[str, Unit] = {}
    for c in columns:
        v = parse(c)
        if v.kind == "node" and v.metric == "db_latency":
            dbs = [d for d in topology.callees(v.node) if topology.nodes.get(d) == "database"]
            if len(dbs) == 1:
                out.setdefault(dbs[0], Unit(dbs[0], "database", dbs[0], [])).variables.append(c)
                continue
        if v.kind == "link":
            name = f"{v.client}->{v.node}"
            out.setdefault(name, Unit(name, "link", v.node, [])).variables.append(c)
        else:
            out.setdefault(v.node, Unit(v.node, topology.nodes.get(v.node, "service"), v.node, [])).variables.append(c)
    return list(out.values())


def rank(frame: Frame, anomaly: AnomalyModel, scm: LaggedSCM, topology: Topology, weights: dict[str, float],
         analysis_start: pd.Timestamp | None = None) -> dict:
    data = frame.data
    z, p = anomaly.variable_scores(data)
    # Deviation and unexplained residual in the same standardized units (baseline median/scale).
    dev = scm.standardized(data).reindex(columns=p.columns)
    raw = scm.residuals(frame).reindex(index=data.index, columns=p.columns)
    resid = scm.standardized_residuals(frame).reindex(index=data.index, columns=p.columns)
    if analysis_start is not None:
        mask = data.index >= analysis_start
        z, p, resid, dev, raw = z[mask], p[mask], resid[mask], dev[mask], raw[mask]

    cols = [c for c in p.columns]
    rows = []
    for u in units(topology, cols):
        vs = [c for c in u.variables if c in p.columns]
        if not vs:
            continue
        pu = p[vs]
        unexplained = 0.0
        for c in vs:
            hot_steps = np.flatnonzero(pu[c].to_numpy() >= 0.5)[:ONSET_STEPS]
            if len(hot_steps) == 0:
                continue
            d = np.nan_to_num(dev[c].to_numpy()[hot_steps])
            r = np.nan_to_num(raw[c].to_numpy()[hot_steps])
            if parse(c).upward:
                r = np.clip(r, 0.0, None)
            energy = float(np.sum(d ** 2))
            if energy > 1e-9:
                share = min(1.0, float(np.sum(r ** 2)) / energy)
                unexplained = max(unexplained, share * float(pu[c].to_numpy()[hot_steps].mean()))
        anomalous = float(pu.max().max())
        hot = (pu.max(axis=1) >= 0.5).astype(int)
        sustained = hot & hot.shift(-1, fill_value=0)
        onset = pu.index[sustained.to_numpy().astype(bool)].min() if sustained.any() else None
        top_var = max(vs, key=lambda c: float(pu[c].max()))
        rows.append({"unit": u, "residual": float(unexplained), "anomaly": anomalous, "onset": onset,
                     "signals": [{"variable": c, "metric": parse(c).metric,
                                  "max_z": round(float(z[c].max()), 2) if c in z else None,
                                  "max_residual_sigma": round(float(resid[c].max()), 2) if resid[c].notna().any() else None,
                                  "max_probability": round(float(pu[c].max()), 3)}
                                 for c in sorted(vs, key=lambda c: float(pu[c].max()), reverse=True)],
                     "top_variable": top_var})

    seen = [r["onset"] for r in rows if r["onset"] is not None]
    if seen:
        first, last = min(seen), max(seen)
        tie = pd.Timedelta(seconds=ONSET_TIE_STEPS * frame.step_seconds)
        span = max((last - first) - tie, pd.Timedelta(0))
    for r in rows:
        if r["onset"] is None:
            r["precedence"] = 0.0
        else:
            lag = max(r["onset"] - first - tie, pd.Timedelta(0))
            r["precedence"] = 1.0 if span == pd.Timedelta(0) else 1.0 - lag / span
    top_unexplained = max((r["residual"] for r in rows), default=0.0)
    for r in rows:
        r["residual"] = r["residual"] / top_unexplained if top_unexplained > 1e-12 else 0.0
    _counterfactual_attribution(rows, frame, scm, p, analysis_start)

    graph = _pagerank(topology, {r["unit"].name: r["anomaly"] for r in rows})
    for r in rows:
        r["graph"] = graph.get(r["unit"].name, 0.0)
        r["score"] = sum(weights.get(k, 0.0) * r[k] for k in COMPONENTS)

    candidates = [r for r in rows if r["anomaly"] >= 0.5] or rows
    candidates.sort(key=lambda r: r["score"], reverse=True)
    total = sum(r["score"] for r in candidates) or 1.0
    ranked = [{
        "service": r["unit"].name, "kind": r["unit"].kind, "target": r["unit"].target,
        "score": round(r["score"], 4), "confidence": round(r["score"] / total, 4),
        "components": {k: round(r[k], 4) for k in COMPONENTS},
        "onset": r["onset"].isoformat() if r["onset"] is not None else None,
        "explains": r.get("explains", {}),
        "signals": r["signals"][:4], "root_variable": r["top_variable"],
    } for r in candidates]
    return {"candidates": ranked, "evidence": _evidence(ranked, resid, p, scm)}


COMPONENTS = ("counterfactual", "residual", "anomaly", "precedence", "graph")


def _positive_energy(z: pd.DataFrame, cols: list[str]) -> np.ndarray:
    """Per-variable energy of the deviation in the 'bad' direction (both directions for request rate)."""
    out = []
    for c in cols:
        v = np.nan_to_num(z[c].to_numpy())
        v = np.clip(v, 0.0, None) if parse(c).upward else np.abs(v)
        out.append(float(np.sum(v ** 2)))
    return np.array(out)


def _counterfactual_attribution(rows: list[dict], frame: Frame, scm: LaggedSCM, p: pd.DataFrame,
                                analysis_start: pd.Timestamp | None) -> None:
    """Pairwise explanation by intervention.

    E[X][Y] is the share of unit Y's anomalous deviation removed when the SCM is rolled forward
    with unit X restored to baseline. The origin explains the others and is explained by none;
    a symptom is explained by its cause. Score = (energy-weighted share of the other anomalous
    units that X explains) x (1 - the most any other unit explains X).
    """
    t0 = 0 if analysis_start is None else int(frame.data.index.searchsorted(analysis_start))
    t0 = max(t0, scm.lags)
    for r in rows:
        r["counterfactual"] = 0.0
    candidates = [r for r in rows if r["anomaly"] >= 0.5]
    if not candidates or t0 >= len(frame.data):
        return
    hot = {r["unit"].name: [c for c in r["unit"].variables if c in scm.variables and float(p[c].max()) >= 0.5]
           for r in candidates}
    hot = {u: cs for u, cs in hot.items() if cs}
    if not hot:
        return
    observed = scm.standardized(frame.data).iloc[t0:]
    total = {u: float(_positive_energy(observed, cs).sum()) for u, cs in hot.items()}
    explain: dict[str, dict[str, float]] = {}
    for x, own in hot.items():
        cf = scm.standardized(scm.rollout(frame, {c: 1.0 for c in own}, t0)).iloc[t0:]
        explain[x] = {}
        for y, cs in hot.items():
            if y == x or total[y] <= 1e-12:
                continue
            remaining = float(_positive_energy(cf, cs).sum())
            explain[x][y] = float(np.clip(1.0 - remaining / total[y], 0.0, 1.0))
    for r in candidates:
        x = r["unit"].name
        if x not in hot:
            continue
        others = [y for y in hot if y != x and total[y] > 1e-12]
        if not others:
            r["counterfactual"] = 1.0
            continue
        weight = sum(total[y] for y in others)
        explains = sum(total[y] * explain[x].get(y, 0.0) for y in others) / weight
        explained = max(explain[y].get(x, 0.0) for y in others)
        r["counterfactual"] = explains * (1.0 - explained)
        r["explains"] = {y: round(explain[x].get(y, 0.0), 3) for y in others}
    top = max(r["counterfactual"] for r in rows)
    for r in rows:
        r["counterfactual"] = r["counterfactual"] / top if top > 1e-12 else 0.0


def _pagerank(topology: Topology, personalization: dict[str, float]) -> dict[str, float]:
    """Walk from anomalous callers toward their dependencies (and through link units)."""
    g = nx.DiGraph()
    g.add_nodes_from(topology.nodes)
    for c, s in topology.edges:
        link = f"{c}->{s}"
        if link in personalization:
            g.add_edge(c, link)
            g.add_edge(link, s)
        else:
            g.add_edge(c, s)
    for n in personalization:
        g.add_node(n)
    pers = {n: max(personalization.get(n, 0.0), 1e-6) for n in g.nodes}
    pr = nx.pagerank(g, alpha=0.85, personalization=pers)
    top = max(pr.values()) or 1.0
    return {n: v / top for n, v in pr.items()}


def _evidence(ranked: list[dict], resid: pd.DataFrame, p: pd.DataFrame, scm: LaggedSCM) -> list[dict]:
    """Plain statements a responder can check against the dashboards."""
    if not ranked:
        return []
    top = ranked[0]
    out = [{"type": "root_cause", "unit": top["service"], "kind": top["kind"],
            "statement": f"{top['service']} deviated first and its deviation is not explained by its dependencies "
                         f"(unexplained score {top['components']['residual']:.2f}, onset {top['onset']})",
            "signals": top["signals"]}]
    for r in ranked[1:4]:
        v = r["root_variable"]
        mres = resid[v].max() if v in resid and resid[v].notna().any() else float("nan")
        explained_by = [pa for pa, k in scm.equations[v].parents if parse(pa).node != parse(v).node] if v in scm.equations else []
        out.append({"type": "symptom", "unit": r["service"],
                    "statement": f"{r['service']} is anomalous but largely explained by its dependencies "
                                 f"(max unexplained deviation {mres:.1f} sigma)",
                    "explained_by": sorted({parse(x).node for x in explained_by})})
    return out

"""What would have happened without the root cause?

Runs the SCM rollout under an intervention for the point model and for every bootstrap
ensemble member, then summarizes the effect where users feel it (the entry services) and
checks the result instead of asserting it is valid.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .anomaly import AnomalyModel
from .scm import LaggedSCM
from .topology import Topology
from .window import Frame, parse, var_name


def simulate(scm: LaggedSCM, anomaly: AnomalyModel, frame: Frame, topology: Topology,
             unit: str, target: str, variables: list[str], t0: int, magnitude: float = 1.0) -> dict:
    variables = [v for v in variables if v in scm.variables]
    if not variables:
        raise ValueError(f"No modelled variable to intervene on for {unit}")
    if not 0.0 < magnitude <= 1.0:
        raise ValueError("magnitude must be in (0, 1]")
    interventions = {v: magnitude for v in variables}
    observed = frame.data.reindex(columns=scm.variables)
    point = scm.rollout(frame, interventions, t0)
    clamped = scm.last_clamped
    members = [scm.rollout(frame, interventions, t0, member=m) for m in range(scm.ensemble_size())]

    impacted = topology.descendants_impacted(target) | {target}
    entries = topology.entry_nodes()
    idx = frame.data.index

    def series(df: pd.DataFrame, col: str) -> list:
        return [None if pd.isna(v) else round(float(v), 3) for v in df[col].to_numpy()] if col in df else []

    def band(col: str, q: float) -> list:
        if not members or col not in point:
            return []
        stack = np.vstack([m[col].to_numpy() for m in members])
        return [round(float(v), 3) for v in np.nanquantile(stack, q, axis=0)]

    nodes = {}
    for n in topology.nodes:
        entry = {}
        for metric in ("latency", "error"):
            col = var_name(n, metric)
            if col in point:
                entry[metric] = {"observed": series(observed, col), "counterfactual": series(point, col),
                                 "low": band(col, 0.05), "high": band(col, 0.95)}
        if entry:
            nodes[n] = entry

    impact = {}
    for n in entries:
        col = var_name(n, "latency")
        if col not in point:
            continue
        avoided = (observed[col] - point[col]).iloc[t0:]
        ecol = var_name(n, "error")
        avoided_err = (observed[ecol] - point[ecol]).iloc[t0:] if ecol in point else pd.Series(dtype=float)
        impact[n] = {"peak_avoided_latency_ms": round(float(avoided.max()), 2),
                     "mean_avoided_latency_ms": round(float(avoided.mean()), 2),
                     "peak_avoided_error_pct": round(float(avoided_err.max()), 3) if len(avoided_err) else None}

    restored = []
    z_cf, p_cf = anomaly.variable_scores(point.iloc[t0:]) if t0 < len(point) else (None, None)
    if p_cf is not None:
        for n in topology.nodes:
            cols = [c for c in p_cf.columns if parse(c).node == n]
            if cols and float(p_cf[cols].iloc[-min(6, len(p_cf)):].max().max()) < 0.5:
                restored.append(n)

    return {"intervention": {"unit": unit, "target": target, "variables": variables, "magnitude": magnitude,
                             "start": idx[t0].isoformat() if t0 < len(idx) else None,
                             "semantics": "do(variable := baseline + (1 - magnitude) * observed deviation)"},
            "timestamps": [t.isoformat() for t in idx],
            "nodes": nodes, "entry_impact": impact, "restored_nodes": restored,
            "ensemble_members": len(members),
            "validity": {**_validity(scm, observed, point, frame, t0, impacted, variables),
                         "physical_bound_clamps": clamped}}


def _validity(scm: LaggedSCM, observed: pd.DataFrame, point: pd.DataFrame, frame: Frame, t0: int,
              impacted: set[str], variables: list[str]) -> dict:
    pre = (point.iloc[:t0] - observed.iloc[:t0]).abs().max().max() if t0 > 0 else 0.0
    untouched = [c for c in scm.variables if parse(c).node not in impacted and c not in variables]
    leak = (point[untouched].iloc[t0:] - observed[untouched].iloc[t0:]).abs().max().max() if untouched else 0.0
    out_of_range = 0
    for c in scm.variables:
        # Every measured quantity is non-negative; above the observed maximum means extrapolation.
        hi = observed[c].max() + 3 * scm.scale[c] if observed[c].notna().any() else np.inf
        vals = point[c].iloc[t0:].dropna()
        out_of_range += int((vals > hi).sum())
    affected = [c for c in scm.variables if parse(c).node in impacted]
    r2 = [scm.equations[c].r2_holdout for c in affected if scm.equations[c].r2_holdout is not None]
    median_r2 = float(np.median(r2)) if r2 else None
    warnings = []
    pre = 0.0 if pd.isna(pre) else float(pre)
    leak = 0.0 if pd.isna(leak) else float(leak)
    if pre > 1e-6:
        warnings.append(f"values before the intervention differ from the observation (max {pre:.3g})")
    if leak > 1e-6:
        warnings.append(f"components the root cause cannot reach changed (max {leak:.3g})")
    if out_of_range:
        warnings.append(f"{out_of_range} counterfactual values fall outside the range seen in training")
    if median_r2 is not None and median_r2 < 0.3:
        warnings.append(f"equations on the propagation path fit held-out data poorly (median R2 {median_r2:.2f})")
    return {"status": "PASS" if not warnings else "WARN", "pre_intervention_max_diff": round(pre, 6),
            "unreachable_max_diff": round(leak, 6), "out_of_range_values": out_of_range,
            "propagation_median_holdout_r2": None if median_r2 is None else round(median_r2, 3),
            "warnings": warnings}

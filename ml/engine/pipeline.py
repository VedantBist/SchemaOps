"""Serving: evaluate the latest telemetry and analyse incidents with an environment's champion model."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from . import rca as rca_mod
from .counterfactual import simulate
from .model import EnvironmentModel, load
from .store import Store
from .window import Frame, build_frame

EVALUATION_WINDOW = timedelta(minutes=10)


class NoModelError(LookupError):
    """The environment has no calibrated champion model yet."""


def champion(store: Store, environment_id: str) -> EnvironmentModel:
    row = store.champion(environment_id)
    if row is None:
        raise NoModelError("This environment has no calibrated model yet")
    return load(environment_id, row["version"], row["artifact_path"], row["checksum"])


def _frame(store: Store, model: EnvironmentModel, environment_id: str, start: datetime, end: datetime) -> Frame:
    frame = build_frame(store.telemetry(environment_id, start, end), store.edge_telemetry(environment_id, start, end),
                        model.topology)
    data = frame.data.reindex(columns=model.columns)
    return Frame(data, frame.slo_latency, frame.slo_error, frame.segment, frame.step_seconds)


def evaluate(store: Store, environment_id: str, at: datetime) -> dict:
    """Anomaly scores, gate verdicts and forecasts for the sample at `at` (past data only)."""
    model = champion(store, environment_id)
    frame = _frame(store, model, environment_id, at - EVALUATION_WINDOW, at)
    scores = model.anomaly.node_scores(frame.data)
    flagged = model.anomaly.flagged(frame.data)
    services = {}
    for node in model.topology.nodes:
        if node not in scores:
            continue
        services[node] = {
            "anomaly": round(float(scores[node].iloc[-1]), 4),
            "flagged": bool(flagged[node].iloc[-1]) if node in flagged else False,
            "signals": model.anomaly.explain(frame.data, node),
            "multivariate_outlier": model.anomaly.multivariate_outlier(frame.data, node),
        }
    forecasts = model.forecaster.predict(frame, model.anomaly, model.topology, model.slo, model.service_slos)
    flagged_now = sorted(n for n, s in services.items() if s["flagged"])
    return {"model_version": model.version, "evaluated_at": frame.data.index[-1].isoformat(),
            "services": services, "forecasts": forecasts,
            "gate": {"incident": bool(flagged_now), "services": flagged_now,
                     "max_anomaly": max((s["anomaly"] for s in services.values()), default=0.0)}}


def analyse(store: Store, environment_id: str, start: datetime, end: datetime, onset: datetime | None = None) -> dict:
    """Ranks root-cause candidates in [start, end] and simulates removing the top one."""
    model = champion(store, environment_id)
    lag_pad = timedelta(seconds=model.step_seconds * (model.scm.lags + 2))
    frame = _frame(store, model, environment_id, start - lag_pad, end)
    if len(frame) < model.scm.lags + 3:
        raise ValueError("Not enough telemetry in the incident window")
    weights = model.analysis["rcaWeights"]
    ranked = rca_mod.rank(frame, model.anomaly, model.scm, model.topology, weights, analysis_start=pd.Timestamp(start))
    out = {"model_version": model.version,
           "methodology": "Topology-constrained lagged SCM residuals + learned baselines + onset order + "
                          "personalized PageRank (calibrated on this environment)",
           "window": {"start": start.isoformat(), "end": end.isoformat()},
           "weights": weights, **ranked, "counterfactual": None}
    if ranked["candidates"]:
        top = ranked["candidates"][0]
        variables = [s["variable"] for s in top["signals"] if s["max_probability"] >= 0.5] or [top["root_variable"]]
        t0 = _first_index(frame, top.get("onset") or (onset.isoformat() if onset else None))
        try:
            out["counterfactual"] = simulate(model.scm, model.anomaly, frame, model.topology, top["service"],
                                             top["target"], variables, t0)
        except ValueError as e:
            out["counterfactual"] = {"error": str(e)}
    return out


def counterfactual(store: Store, environment_id: str, start: datetime, end: datetime, unit: str,
                   magnitude: float, from_time: datetime | None = None) -> dict:
    """Simulates removing (part of) a chosen unit's deviation: a service, database or 'client->server' link."""
    model = champion(store, environment_id)
    lag_pad = timedelta(seconds=model.step_seconds * (model.scm.lags + 2))
    frame = _frame(store, model, environment_id, start - lag_pad, end)
    units = {u.name: u for u in rca_mod.units(model.topology, model.columns)}
    if unit not in units:
        raise ValueError(f"Unknown unit '{unit}'; known: {sorted(units)}")
    u = units[unit]
    _, p = model.anomaly.variable_scores(frame.data)
    hot = [c for c in u.variables if c in p and float(p[c].max()) >= 0.5]
    t0 = _first_index(frame, (from_time or start).isoformat())
    return simulate(model.scm, model.anomaly, frame, model.topology, u.name, u.target, hot or u.variables, t0, magnitude)


def _first_index(frame: Frame, when: str | None) -> int:
    if when is None:
        return max(frame.data.index.size // 3, 1)
    pos = frame.data.index.searchsorted(pd.Timestamp(when))
    return int(min(max(pos, 1), len(frame.data) - 1))

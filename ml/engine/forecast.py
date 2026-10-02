"""Failure forecasting: will a service breach its SLO within the next H seconds?

Features are computed from the past only and do not depend on which service or how many
services exist, so one pooled model serves any topology:
  own z-scores and their recent trend, the worst callee/link state and trend, the request-rate
  deviation, and how close latency and errors already are to the SLO.
There is no window-length or onset-position feature.

Labels come from the environment's own history: y(t) = 1 when the service is within its SLO
at t and breaches it within (t, t + H]. With too few such onsets to learn from, a transparent
trend extrapolation is used instead and reported as such.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, f1_score, roc_auc_score
from sklearn.model_selection import GroupKFold

from .anomaly import AnomalyModel
from .topology import Topology
from .window import Frame, link_name, slo_breach, var_name

TREND_STEPS = 6
MIN_POSITIVES = 10
MIN_SKILL_AUC = 0.7   # a learned forecaster is only used if it beats chance clearly on held-out incidents
FEATURES = ["z_latency", "z_error", "z_pool", "z_pending", "z_db", "s_latency", "s_error", "s_pending",
            "callee_max_z", "callee_max_slope", "link_max_z", "rps_z", "latency_to_slo", "error_to_slo"]
LEVELS = [(0.8, "CRITICAL"), (0.5, "HIGH"), (0.3, "ELEVATED"), (0.0, "LOW")]


def risk_level(p: float) -> str:
    return next(name for floor, name in LEVELS if p >= floor)


def node_features(frame: Frame, z: pd.DataFrame, topology: Topology, node: str, slo: dict) -> pd.DataFrame:
    def col(metric):
        c = var_name(node, metric)
        return z[c] if c in z else pd.Series(0.0, index=z.index)

    def slope(s):
        return (s - s.shift(TREND_STEPS)) / TREND_STEPS

    f = pd.DataFrame(index=z.index)
    f["z_latency"], f["z_error"], f["z_pool"] = col("latency"), col("error"), col("pool_util")
    f["z_pending"], f["z_db"] = col("pool_pending"), col("db_latency")
    f["s_latency"], f["s_error"], f["s_pending"] = slope(f["z_latency"]), slope(f["z_error"]), slope(f["z_pending"])
    callee_z = [z[var_name(d, m)] for d in topology.callees(node) for m in ("latency", "error") if var_name(d, m) in z]
    f["callee_max_z"] = pd.concat(callee_z, axis=1).max(axis=1) if callee_z else 0.0
    f["callee_max_slope"] = slope(f["callee_max_z"]) if callee_z else 0.0
    links = [z[link_name(node, d)] for d in topology.callees(node) if link_name(node, d) in z]
    f["link_max_z"] = pd.concat(links, axis=1).max(axis=1) if links else 0.0
    rps = var_name(node, "rps")
    f["rps_z"] = z[rps] if rps in z else 0.0
    lat = frame.slo_latency[node] if node in frame.slo_latency else pd.Series(np.nan, index=z.index)
    err = frame.slo_error[node] if node in frame.slo_error else pd.Series(np.nan, index=z.index)
    f["latency_to_slo"] = (lat / float(slo["latencyP99Ms"])).reindex(z.index)
    f["error_to_slo"] = (err / float(slo["errorRatePct"])).reindex(z.index)
    return f[FEATURES].fillna(0.0)


def labels(breach: pd.Series, horizon_steps: int) -> pd.Series:
    """1 if within SLO now and breaching within the next horizon_steps samples."""
    future = pd.concat([breach.shift(-k) for k in range(1, horizon_steps + 1)], axis=1).max(axis=1)
    y = ((~breach) & future.astype(float).fillna(0.0).astype(bool)).astype(float)
    y[breach] = np.nan  # already failing: nothing to forecast
    y.iloc[-horizon_steps:] = np.nan  # future unknown
    return y


@dataclass
class HorizonModel:
    horizon_seconds: int
    method: str                       # "logistic_regression" or "trend_extrapolation"
    model: LogisticRegression | None = None
    metrics: dict = field(default_factory=dict)


@dataclass
class Forecaster:
    horizons: list[HorizonModel]
    step_seconds: float

    @staticmethod
    def fit(frame: Frame, anomaly: AnomalyModel, topology: Topology, slo: dict, service_slos: dict,
            horizons: list[int], groups: pd.Series) -> "Forecaster":
        z, _ = anomaly.variable_scores(frame.data)
        breach = slo_breach(frame, slo, service_slos)
        services = [n for n, k in topology.nodes.items() if k == "service" and n in breach.columns]
        out = []
        for h in horizons:
            steps = max(1, int(round(h / frame.step_seconds)))
            X, y, g = [], [], []
            for n in services:
                f = node_features(frame, z, topology, n, service_slos.get(n, slo))
                yl = labels(breach[n], steps)
                keep = yl.notna()
                X.append(f[keep].to_numpy())
                y.append(yl[keep].to_numpy())
                g.append(groups.reindex(f.index)[keep].fillna(-1).to_numpy())
            X, y, g = np.vstack(X), np.concatenate(y), np.concatenate(g)
            positives = int(y.sum())
            hm = HorizonModel(h, "trend_extrapolation", metrics={"samples": int(len(y)), "positives": positives})
            if positives >= MIN_POSITIVES and len(np.unique(g)) >= 3:
                cv = _cross_validate(X, y, g)
                hm.metrics["learned_cv"] = cv
                if cv.get("auc", 0.0) >= MIN_SKILL_AUC:
                    hm.model = _model().fit(X, y)
                    hm.method = "logistic_regression"
                    hm.metrics["coefficients"] = {f: round(float(c), 4) for f, c in zip(FEATURES, hm.model.coef_[0])}
                else:
                    hm.metrics["note"] = (f"a learned forecaster had no useful skill on held-out incidents "
                                          f"(AUC {cv.get('auc')}; need {MIN_SKILL_AUC}): failures here start abruptly, "
                                          "so trend extrapolation is used")
            else:
                hm.metrics["note"] = (f"{positives} SLO-breach onsets in the learning data (need {MIN_POSITIVES}); "
                                      "using trend extrapolation until more history exists")
            out.append(hm)
        return Forecaster(out, frame.step_seconds)

    def predict(self, frame: Frame, anomaly: AnomalyModel, topology: Topology, slo: dict, service_slos: dict) -> list[dict]:
        """Forecast for the latest row of the frame."""
        z, _ = anomaly.variable_scores(frame.data)
        breach = slo_breach(frame, slo, service_slos)
        results = []
        for n, kind in topology.nodes.items():
            if kind != "service" or n not in breach.columns:
                continue
            s = service_slos.get(n, slo)
            f = node_features(frame, z, topology, n, s).iloc[[-1]]
            breaching = bool(breach[n].iloc[-1])
            for hm in self.horizons:
                if breaching:
                    p, method, factors = 1.0, "observed_breach", [{"name": "slo_breached_now", "value": 1.0}]
                elif hm.method == "logistic_regression":
                    p = float(hm.model.predict_proba(f.to_numpy())[0, 1])
                    contrib = hm.model.coef_[0] * f.to_numpy()[0]
                    order = np.argsort(-np.abs(contrib))[:3]
                    factors = [{"name": FEATURES[i], "value": round(float(f.iloc[0, i]), 3),
                                "contribution": round(float(contrib[i]), 3)} for i in order]
                    method = hm.method
                else:
                    p, factors = _trend(frame, n, s, hm.horizon_seconds, self.step_seconds)
                    method = hm.method
                results.append({"service": n, "horizon_seconds": hm.horizon_seconds, "probability": round(p, 4),
                                "risk_level": risk_level(p), "method": method, "factors": factors})
        return results

    def summary(self) -> dict:
        return {str(h.horizon_seconds): {"method": h.method, **{k: v for k, v in h.metrics.items() if k != "coefficients"}}
                for h in self.horizons}


def _trend(frame: Frame, node: str, slo: dict, horizon_s: int, step_s: float) -> tuple[float, list[dict]]:
    """Projects p99 latency and error rate linearly over the horizon and compares them with the SLO."""
    probs, factors = [], []
    for series, limit, name in ((frame.slo_latency.get(node), float(slo["latencyP99Ms"]), "latency_p99"),
                                (frame.slo_error.get(node), float(slo["errorRatePct"]), "error_rate")):
        if series is None:
            continue
        s = series.dropna()
        if len(s) <= TREND_STEPS:
            continue
        slope = (s.iloc[-1] - s.iloc[-1 - TREND_STEPS]) / TREND_STEPS
        projected = s.iloc[-1] + max(slope, 0.0) * (horizon_s / step_s)
        probs.append(float(1 / (1 + np.exp(-(projected - limit) / (0.1 * limit)))))
        factors.append({"name": f"{name}_projected", "value": round(float(projected), 2), "slo": limit})
    return (max(probs) if probs else 0.0), factors


def _model() -> LogisticRegression:
    # No class re-weighting: probabilities must stay calibrated to the real base rate,
    # otherwise rare positives inflate every prediction into a false alarm.
    return LogisticRegression(C=0.5, max_iter=2000)


def _cross_validate(X: np.ndarray, y: np.ndarray, groups: np.ndarray) -> dict:
    """Grouped cross-validation: incidents never appear in both the training and the test fold."""
    k = min(5, len(np.unique(groups)))
    proba = np.full(len(y), np.nan)
    for tr, te in GroupKFold(n_splits=k).split(X, y, groups):
        if y[tr].sum() == 0 or y[tr].sum() == len(tr):
            continue
        m = _model().fit(X[tr], y[tr])
        proba[te] = m.predict_proba(X[te])[:, 1]
    ok = ~np.isnan(proba)
    yt, pt = y[ok], proba[ok]
    out = {"cv_folds": k, "cv_samples": int(ok.sum())}
    if len(np.unique(yt)) == 2:
        out["auc"] = round(float(roc_auc_score(yt, pt)), 4)
        out["f1_at_0_5"] = round(float(f1_score(yt, pt >= 0.5)), 4)
        out["brier"] = round(float(brier_score_loss(yt, pt)), 4)
        neg = yt == 0
        out["false_positive_rate"] = round(float((pt[neg] >= 0.5).mean()), 4)
    return out

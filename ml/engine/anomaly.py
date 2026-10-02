"""Anomaly gate: decides when a service has left its learned normal behaviour.

Per variable, the threshold is calibrated on the environment's own incident-free history so
that, at most, ``max_fpr`` of normal samples would cross it (never below ``z_floor``). A node
is anomalous when any of its variables crosses its threshold; it is *flagged* when that holds
for ``consecutive`` samples in a row. An Isolation Forest per node adds a multivariate check
reported alongside (it corroborates; it does not open incidents on its own).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from .baselines import Baseline, fit_baselines, zscores
from .window import parse

SLOPE = 2.0   # steepness of the z -> probability map around the threshold


@dataclass
class AnomalyModel:
    baselines: dict[str, Baseline]
    thresholds: dict[str, float]
    consecutive: int
    forests: dict[str, tuple[list[str], IsolationForest, np.ndarray]] = field(default_factory=dict)

    @staticmethod
    def fit(clean: pd.DataFrame, z_floor: float, max_fpr: float, consecutive: int, seed: int = 0) -> "AnomalyModel":
        baselines = fit_baselines(clean)
        z = zscores(clean, baselines)
        thresholds = {}
        for col in z.columns:
            q = float(np.nanquantile(z[col].to_numpy(), 1 - max_fpr)) if z[col].notna().any() else z_floor
            thresholds[col] = max(z_floor, q)
        forests = {}
        by_node: dict[str, list[str]] = {}
        for col in z.columns:
            by_node.setdefault(parse(col).node, []).append(col)
        for node, cols in by_node.items():
            m = z[cols].dropna()
            if len(m) < 50:
                continue
            forest = IsolationForest(n_estimators=100, random_state=seed).fit(m.to_numpy())
            forests[node] = (cols, forest, np.sort(forest.score_samples(m.to_numpy())))
        return AnomalyModel(baselines, thresholds, consecutive, forests)

    def variable_scores(self, data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        """(z-scores, anomaly probabilities in [0, 1]) per variable and step."""
        z = zscores(data[[c for c in data.columns if c in self.baselines]], self.baselines)
        thr = pd.Series(self.thresholds).reindex(z.columns)
        p = 1.0 / (1.0 + np.exp(-SLOPE * (z - thr)))
        return z, p.fillna(0.0)

    def node_scores(self, data: pd.DataFrame) -> pd.DataFrame:
        """Anomaly probability per node and step: the worst of the node's own variables and inbound links."""
        _, p = self.variable_scores(data)
        nodes: dict[str, list[str]] = {}
        for col in p.columns:
            nodes.setdefault(parse(col).node, []).append(col)
        return pd.DataFrame({n: p[cols].max(axis=1) for n, cols in nodes.items()}, index=p.index)

    def flagged(self, data: pd.DataFrame) -> pd.DataFrame:
        """True where a node has been anomalous for the last `consecutive` samples."""
        bad = (self.node_scores(data) >= 0.5).astype(int)
        run = bad.rolling(self.consecutive, min_periods=self.consecutive).sum()
        return (run >= self.consecutive).fillna(False)

    def explain(self, data: pd.DataFrame, node: str, top: int = 3) -> list[dict]:
        """The variables that drive a node's latest score, in natural units."""
        z, p = self.variable_scores(data.tail(1))
        cols = [c for c in p.columns if parse(c).node == node]
        ranked = sorted(cols, key=lambda c: float(p[c].iloc[-1]), reverse=True)[:top]
        hours = data.index[-1:].hour
        out = []
        for c in ranked:
            b = self.baselines[c]
            out.append({"variable": c, "metric": parse(c).metric,
                        "value": round(float(data[c].iloc[-1]), 3) if pd.notna(data[c].iloc[-1]) else None,
                        "baseline": round(float(np.atleast_1d(b.center(hours))[0]), 3),
                        "z": round(float(z[c].iloc[-1]), 2), "threshold": round(self.thresholds[c], 2),
                        "probability": round(float(p[c].iloc[-1]), 3)})
        return out

    def multivariate_outlier(self, data: pd.DataFrame, node: str) -> float | None:
        """Share of training samples that looked more normal than the latest one (Isolation Forest)."""
        if node not in self.forests:
            return None
        cols, forest, train_scores = self.forests[node]
        z, _ = self.variable_scores(data.tail(1))
        row = z.reindex(columns=cols).fillna(0.0).to_numpy()
        score = float(forest.score_samples(row)[0])
        # Lower Isolation Forest scores are more anomalous; report the share of training samples above this one.
        return float(1.0 - np.searchsorted(train_scores, score) / len(train_scores))

"""Robust per-variable baselines learned from an environment's own telemetry.

Median and MAD resist the incidents that inevitably occur during a learning window. When
enough history exists, the median and scale are learned per hour of day so a daily traffic
pattern is not mistaken for an anomaly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .window import parse

MAD_TO_SIGMA = 1.4826
MIN_PER_HOUR = 60          # samples needed before an hour-of-day profile is trusted
HOURLY_MIN_SPAN_H = 24.0   # hour-of-day profiles need at least a full day of history

# Smallest meaningful deviation per metric, in natural units, so a perfectly flat series
# (scale ~ 0) does not turn measurement noise into huge z-scores.
ABSOLUTE_FLOOR = {"latency": 2.0, "db_latency": 2.0, "gap": 2.0, "error": 0.5,
                  "rps": 0.2, "pool_util": 2.0, "pool_pending": 0.5}
RELATIVE_FLOOR = 0.05


@dataclass
class Baseline:
    median: float
    scale: float
    hourly_median: dict[int, float] = field(default_factory=dict)
    hourly_scale: dict[int, float] = field(default_factory=dict)
    lo: float = 0.0     # training range, used to flag extrapolation
    hi: float = 0.0

    def center(self, hours: np.ndarray | None = None) -> np.ndarray | float:
        if hours is None or not self.hourly_median:
            return self.median
        return np.array([self.hourly_median.get(int(h), self.median) for h in hours])

    def spread(self, hours: np.ndarray | None = None) -> np.ndarray | float:
        if hours is None or not self.hourly_scale:
            return self.scale
        return np.array([self.hourly_scale.get(int(h), self.scale) for h in hours])


def _scale(values: np.ndarray, median: float, metric: str) -> float:
    mad = float(np.median(np.abs(values - median))) * MAD_TO_SIGMA
    floor = max(ABSOLUTE_FLOOR.get(metric, 1.0), RELATIVE_FLOOR * abs(median))
    return max(mad, floor)


def fit_baselines(data: pd.DataFrame) -> dict[str, Baseline]:
    span_h = (data.index.max() - data.index.min()).total_seconds() / 3600 if len(data) else 0
    hours = data.index.hour if span_h >= HOURLY_MIN_SPAN_H else None
    out = {}
    for col in data.columns:
        metric = parse(col).metric
        s = data[col].dropna()
        if s.empty:
            continue
        v = s.to_numpy()
        med = float(np.median(v))
        b = Baseline(median=med, scale=_scale(v, med, metric), lo=float(np.min(v)), hi=float(np.max(v)))
        if hours is not None:
            h = s.index.hour
            for hour in range(24):
                hv = v[h == hour]
                if len(hv) >= MIN_PER_HOUR:
                    hm = float(np.median(hv))
                    b.hourly_median[hour] = hm
                    b.hourly_scale[hour] = _scale(hv, hm, metric)
        out[col] = b
    return out


def zscores(data: pd.DataFrame, baselines: dict[str, Baseline]) -> pd.DataFrame:
    """Signed robust z-score per variable; upward-only metrics are clipped at 0 below."""
    hours = data.index.hour
    out = {}
    for col in data.columns:
        b = baselines.get(col)
        if b is None:
            continue
        z = (data[col].to_numpy() - b.center(hours)) / b.spread(hours)
        if parse(col).upward:
            z = np.maximum(z, 0.0)
        else:
            z = np.abs(z)
        out[col] = z
    return pd.DataFrame(out, index=data.index)

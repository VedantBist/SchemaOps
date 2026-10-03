"""Self-calibrating log-quality gate (the CausalOps anomaly-gate method applied to parsing quality).

Per source, three series are learned from its own quiet history (bucketed, default 20 s):
  fill        share of the pack's expected OCSF attributes present        → a drop means parser drift
  normalized  share of events fully normalized                            → a drop means parser drift
  volume      events per bucket                                           → zero runs mean a silent source
Baselines are robust (median and MAD × 1.4826, with floors so a perfectly flat series does not turn noise
into huge z-scores). The drift threshold is calibrated so that at most `max_fpr` of the source's own normal
buckets would cross it, never below `z_floor`; an alarm needs `consecutive` buckets in a row.
Silence uses the learned rate: k empty buckets in a row are flagged when a Poisson source with that rate
would produce them with probability below 1e-4 (k = ceil(9.21 / rate), at least 3).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

MAD_TO_SIGMA = 1.4826
FLOOR = {"fill": 0.02, "normalized": 0.02, "volume": 1.0}
SILENCE_LOG_P = math.log(1e-4)


@dataclass
class Baseline:
    metric: str
    median: float
    scale: float
    threshold: float
    samples: int


def median(values: list[float]) -> float:
    v = sorted(values)
    n = len(v)
    if not n:
        return 0.0
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def quantile(values: list[float], q: float) -> float:
    v = sorted(values)
    if not v:
        return 0.0
    pos = q * (len(v) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def fit(metric: str, values: list[float], z_floor: float, max_fpr: float) -> Baseline:
    med = median(values)
    mad = median([abs(x - med) for x in values]) * MAD_TO_SIGMA
    floor = FLOOR[metric] if metric != "volume" else max(FLOOR["volume"], 0.1 * med)
    scale = max(mad, floor)
    drops = [(med - x) / scale for x in values]
    threshold = max(z_floor, quantile(drops, 1 - max_fpr)) if values else z_floor
    return Baseline(metric, med, scale, threshold, len(values))


def drop_z(b: Baseline, value: float) -> float:
    """How far below normal the value is, in robust standard deviations (positive = worse)."""
    return (b.median - value) / b.scale


def silence_buckets(rate_per_bucket: float) -> int:
    if rate_per_bucket <= 0:
        return 10 ** 9
    return max(3, math.ceil(-SILENCE_LOG_P / rate_per_bucket))


def drifting(b_fill: Baseline | None, b_norm: Baseline | None, recent: list[dict], consecutive: int,
             min_events: int = 3) -> tuple[bool, list[dict]]:
    """recent: newest-last completed buckets with keys events, fill, normalized_ratio."""
    window = [r for r in recent if r["events"] >= min_events][-consecutive:]
    if len(window) < consecutive:
        return False, []
    flags = []
    for r in window:
        z_fill = drop_z(b_fill, r["fill"]) if b_fill is not None and r["fill"] is not None else 0.0
        z_norm = drop_z(b_norm, r["normalized_ratio"]) if b_norm is not None else 0.0
        flags.append({"bucket": r["bucket"], "events": r["events"], "fill": r["fill"],
                      "normalizedRatio": r["normalized_ratio"], "zFill": round(z_fill, 2), "zNormalized": round(z_norm, 2),
                      "bad": (b_fill is not None and z_fill > b_fill.threshold) or (b_norm is not None and z_norm > b_norm.threshold)})
    return all(f["bad"] for f in flags), flags


def healthy(b_fill: Baseline | None, b_norm: Baseline | None, rows: list[dict], needed: int, min_events: int = 3) -> bool:
    window = [r for r in rows if r["events"] >= min_events][-needed:]
    if len(window) < needed:
        return False
    for r in window:
        if b_fill is not None and r["fill"] is not None and drop_z(b_fill, r["fill"]) > b_fill.threshold:
            return False
        if b_norm is not None and drop_z(b_norm, r["normalized_ratio"]) > b_norm.threshold:
            return False
    return True

"""Aligns stored measurements into one time-indexed matrix of variables.

Each ingestion cycle stores every service with the same timestamp, so rows align exactly.
Variables are named ``<node>|<metric>`` for a service or database and
``<client>-><server>|gap`` for a call link (client-side minus server-side p95 latency: the time
spent on the network or queueing between the two). The same layout is used for calibration
and live evaluation, so training and serving see identical features.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .topology import Topology

# metric name -> source column in telemetry_snapshots
NODE_METRICS = {
    "latency": "p95_latency",
    "error": "error_rate",
    "rps": "request_rate",
    "db_latency": "db_latency",
    "pool_util": "pool_utilization",
    "pool_pending": "pool_pending",
}
# metrics where only an increase indicates a problem; request rate can be abnormal both ways
UPWARD = {"latency", "error", "db_latency", "pool_util", "pool_pending", "gap"}
MAX_FILL_STEPS = 3


@dataclass(frozen=True)
class Variable:
    name: str
    node: str          # the node the variable belongs to (a link's server for gap variables)
    metric: str
    kind: str          # "node" or "link"
    client: str | None = None

    @property
    def upward(self) -> bool:
        return self.metric in UPWARD


def var_name(node: str, metric: str) -> str:
    return f"{node}|{metric}"


def link_name(client: str, server: str) -> str:
    return f"{client}->{server}|gap"


def parse(name: str) -> Variable:
    left, metric = name.rsplit("|", 1)
    if "->" in left:
        client, server = left.split("->", 1)
        return Variable(name, server, metric, "link", client)
    return Variable(name, left, metric, "node")


@dataclass
class Frame:
    """Measured variables over time plus the SLO signals (p99 latency, error rate) per node."""
    data: pd.DataFrame          # index: timestamps, columns: variable names, natural units
    slo_latency: pd.DataFrame   # node -> p99 latency (ms)
    slo_error: pd.DataFrame     # node -> error rate (%)
    segment: pd.Series          # contiguous-ingestion segment id per row
    step_seconds: float

    @property
    def variables(self) -> list[Variable]:
        return [parse(c) for c in self.data.columns]

    def __len__(self) -> int:
        return len(self.data)


def build_frame(telemetry: pd.DataFrame, edges: pd.DataFrame, topology: Topology) -> Frame:
    if telemetry.empty:
        raise ValueError("No telemetry in the requested window")
    t = telemetry[telemetry["service_name"].isin(topology.nodes.keys())]
    columns: dict[str, pd.Series] = {}
    for metric, source in NODE_METRICS.items():
        wide = t.pivot_table(index="captured_at", columns="service_name", values=source, aggfunc="last")
        for node in wide.columns:
            columns[var_name(node, metric)] = wide[node]
    if edges is not None and not edges.empty:
        e = edges[edges["client"].isin(topology.nodes) & edges["server"].isin(topology.nodes)].copy()
        # A gap needs a server-side span: only instrumented services report one.
        e = e[e["server"].map(lambda s: topology.nodes.get(s) == "service")]
        e["gap"] = (e["client_p95"] - e["server_p95"]).clip(lower=0)
        wide = e.pivot_table(index="captured_at", columns=["client", "server"], values="gap", aggfunc="last")
        for (client, server) in wide.columns:
            columns[link_name(client, server)] = wide[(client, server)]

    data = pd.DataFrame(columns).sort_index()
    data = data.ffill(limit=MAX_FILL_STEPS)
    data = data.dropna(how="all")
    p99 = t.pivot_table(index="captured_at", columns="service_name", values="p99_latency", aggfunc="last").reindex(data.index)
    err = t.pivot_table(index="captured_at", columns="service_name", values="error_rate", aggfunc="last").reindex(data.index)

    diffs = data.index.to_series().diff().dt.total_seconds()
    step = float(diffs.median()) if len(diffs.dropna()) else 5.0
    breaks = (diffs > 2.5 * step).fillna(False)
    segment = breaks.cumsum().astype(int)
    return Frame(data.astype(float), p99.astype(float), err.astype(float), segment, step)


def usable_columns(frame: Frame, min_coverage: float = 0.8, min_samples: int = 120) -> list[str]:
    """Variables observed often enough to model.

    Coverage is measured from the first time a variable was collected, so a signal the
    platform started collecting later (for example a new link) is not discarded for the
    period before it existed.
    """
    out = []
    for c in frame.data.columns:
        s = frame.data[c]
        first = s.first_valid_index()
        if first is None:
            continue
        since = s.loc[first:]
        if since.notna().sum() >= min_samples and since.notna().mean() >= min_coverage and since.std(skipna=True) > 1e-9:
            out.append(c)
    return out


def slo_breach(frame: Frame, slo: dict, service_slos: dict) -> pd.DataFrame:
    """Boolean matrix (time x node): node violates its SLO at that step."""
    out = {}
    for node in frame.slo_latency.columns:
        s = service_slos.get(node, slo)
        lat = frame.slo_latency[node] > float(s["latencyP99Ms"])
        err = frame.slo_error[node] > float(s["errorRatePct"]) if node in frame.slo_error else False
        out[node] = (lat | err).fillna(False)
    return pd.DataFrame(out, index=frame.data.index)


def lag_valid(frame: Frame, lags: int) -> np.ndarray:
    """True where the previous `lags` rows belong to the same ingestion segment."""
    seg = frame.segment.to_numpy()
    ok = np.ones(len(seg), dtype=bool)
    ok[:lags] = False
    for k in range(1, lags + 1):
        ok[k:] &= seg[k:] == seg[:-k]
    return ok

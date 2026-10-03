"""Labelled incidents (episodes) built from recorded fault injections.

A ramp (the same fault re-injected at rising strength) becomes one episode. The expected
root-cause answer follows the fault mechanism: a network fault on a link into X is answered
by that link; every other fault by its target node. Episodes that overlap another fault have
more than one root cause; they are marked concurrent so single-root accuracy is not scored on them.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .topology import Topology

MERGE_GAP_S = 30
SETTLE_S = 150
OVERLAP_MARGIN_S = 60


@dataclass(frozen=True)
class Episode:
    type: str
    target: str
    start: pd.Timestamp
    end: pd.Timestamp
    concurrent: bool = False

    def expected_units(self, topology: Topology, link_units: set[str]) -> set[str]:
        if self.type == "NETWORK_LATENCY":
            links = {f"{c}->{self.target}" for c in topology.callers(self.target)} & link_units
            return links or {self.target}
        return {self.target}


def from_faults(faults: list[dict]) -> list[Episode]:
    eps: list[Episode] = []
    for f in sorted(faults, key=lambda r: r["started_at"]):
        start = pd.Timestamp(f["started_at"])
        end = pd.Timestamp(f["stopped_at"]) if f.get("stopped_at") else start + pd.Timedelta(seconds=int(f["duration_seconds"]))
        if eps and eps[-1].type == f["type"] and eps[-1].target == f["target"] \
                and (start - eps[-1].end).total_seconds() <= MERGE_GAP_S:
            eps[-1] = Episode(f["type"], f["target"], eps[-1].start, max(end, eps[-1].end))
        else:
            eps.append(Episode(f["type"], f["target"], start, end))
    margin = pd.Timedelta(seconds=OVERLAP_MARGIN_S)
    marked = []
    for i, e in enumerate(eps):
        overlaps = any(j != i and o.start <= e.end + margin and e.start <= o.end + margin for j, o in enumerate(eps))
        marked.append(Episode(e.type, e.target, e.start, e.end, overlaps))
    return marked


def disturbed_mask(index: pd.DatetimeIndex, episodes: list[Episode], settle_s: int = SETTLE_S) -> pd.Series:
    """True for samples inside an episode or its recovery period."""
    m = pd.Series(False, index=index)
    for e in episodes:
        m |= (index >= e.start) & (index <= e.end + pd.Timedelta(seconds=settle_s))
    return m


def change_mask(index: pd.DatetimeIndex, changes: list[dict], settle_s: int = SETTLE_S) -> pd.Series:
    """True for samples during a recorded change (deploy, restart, maintenance, remediation) or its settling time.
    Such samples are neither normal behaviour nor labelled incidents."""
    m = pd.Series(False, index=index)
    for c in changes:
        start, end = pd.Timestamp(c["started_at"]), pd.Timestamp(c["ended_at"])
        m |= (index >= start) & (index <= end + pd.Timedelta(seconds=settle_s))
    return m


def groups(index: pd.DatetimeIndex, episodes: list[Episode], block_minutes: int = 10) -> pd.Series:
    """Cross-validation groups: each episode (with margins) is one group, quiet time is cut into blocks."""
    g = pd.Series((index.asi8 // (block_minutes * 60 * 10**9)) + 10**6, index=index)
    for i, e in enumerate(episodes):
        g[(index >= e.start - pd.Timedelta(minutes=2)) & (index <= e.end + pd.Timedelta(minutes=3))] = i
    return g

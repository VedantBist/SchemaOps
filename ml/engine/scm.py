"""Topology-constrained lagged structural causal model for any call graph.

Each variable is a linear function of its allowed parents plus its own exogenous noise:

    x_v(t) = b_v + sum_{(p, k) in parents(v)} a_{v,p,k} * x_p(t - k) + u_v(t)

Parents are derived from the discovered topology, never from a fixed service list:
  * a caller's latency/errors depend on its callees' latency/errors and on the link delay
    to each callee (same step and lagged),
  * a callee's request rate depends on its callers' request rates,
  * inside a service, latency depends on its connection pool and database latency, and the
    pool depends on load and database latency,
  * every variable depends on its own recent past.
Same-step (lag 0) parents always point down the call chain, so they form a DAG and the model
can be rolled forward in causal order. Every coefficient is constrained to be non-negative:
a slower or failing dependency can never make its caller faster or healthier, and the
constraint keeps rare-event extrapolation physically sensible.

Variables are standardized with the environment's baselines (median, robust scale). The
counterfactual follows Pearl's three steps: abduction (recover u from the observed window),
action (fix the intervened variables), prediction (recompute everything downstream in causal
order, reusing the abducted noise).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from .baselines import Baseline
from .topology import Topology
from .window import Frame, lag_valid, link_name, parse, var_name

Parent = tuple[str, int]   # (variable name, lag in steps; 0 = same step)


def candidate_parents(variables: set[str], topology: Topology, lags: int) -> dict[str, list[Parent]]:
    """Allowed parents per variable, restricted to variables that exist."""
    def span(name: str, from_lag: int = 0) -> list[Parent]:
        return [(name, k) for k in range(from_lag, lags + 1)] if name in variables else []

    parents: dict[str, list[Parent]] = {}
    for v in variables:
        var = parse(v)
        n, m = var.node, var.metric
        ps: list[Parent] = [(v, k) for k in range(1, lags + 1)]
        if var.kind == "link":
            ps += span(var_name(var.client, "rps"))
        else:
            callees, callers = topology.callees(n), topology.callers(n)
            if m == "latency":
                for d in callees:
                    ps += span(var_name(d, "latency")) + span(link_name(n, d))
                for c in callers:
                    ps += span(var_name(c, "rps"), from_lag=1)
                for intra in ("pool_pending", "pool_util", "db_latency", "rps"):
                    ps += span(var_name(n, intra))
            elif m == "error":
                for d in callees:
                    ps += span(var_name(d, "error")) + span(var_name(d, "latency"))
                ps += span(var_name(n, "pool_pending"))
            elif m == "rps":
                for c in callers:
                    ps += span(var_name(c, "rps"))
            elif m == "pool_pending":
                ps += span(var_name(n, "pool_util")) + span(var_name(n, "rps"))
            elif m == "pool_util":
                ps += span(var_name(n, "rps")) + span(var_name(n, "db_latency"))
            elif m == "db_latency":
                for d in callees:
                    if topology.nodes.get(d) == "database":
                        ps += span(var_name(d, "latency"))
                ps += span(var_name(n, "rps"))
        parents[v] = sorted(set(ps))
    return _break_same_step_cycles(parents)


def _break_same_step_cycles(parents: dict[str, list[Parent]]) -> dict[str, list[Parent]]:
    """Keep same-step parents acyclic (needed to roll forward); lagged copies remain."""
    g = nx.DiGraph()
    g.add_nodes_from(parents)
    for v, ps in parents.items():
        for p, k in ps:
            if k == 0:
                g.add_edge(p, v)
    while True:
        try:
            cycle = nx.find_cycle(g)
        except nx.NetworkXNoCycle:
            break
        a, b = cycle[0][:2]
        g.remove_edge(a, b)
        parents[b] = [(p, k) for p, k in parents[b] if not (p == a and k == 0)]
    return parents


@dataclass
class Equation:
    parents: list[Parent]
    coef: np.ndarray
    intercept: float
    resid_std: float
    r2_holdout: float | None
    ensemble: list[tuple[np.ndarray, float]] = field(default_factory=list)


@dataclass
class LaggedSCM:
    variables: list[str]
    lags: int
    center: dict[str, float]
    scale: dict[str, float]
    equations: dict[str, Equation]
    order: list[str]
    alpha: float

    # ── fitting ──────────────────────────────────────────────────────────────
    @staticmethod
    def fit(frame: Frame, columns: list[str], baselines: dict[str, Baseline], topology: Topology,
            lags: int, alpha: float, bootstrap: int = 0, block: int = 60, seed: int = 0) -> "LaggedSCM":
        center = {c: baselines[c].median for c in columns}
        scale = {c: baselines[c].scale for c in columns}
        z = standardize(frame.data[columns], center, scale)
        parents = candidate_parents(set(columns), topology, lags)
        valid = lag_valid(frame, lags)
        rng = np.random.default_rng(seed)
        equations = {}
        for v in columns:
            X, y, rows = design(z, v, parents[v], valid)
            if len(y) < max(30, 3 * X.shape[1] if X.size else 30):
                continue
            r2 = blocked_cv_r2(X, y, alpha, gap=lags)
            model = _ridge(alpha).fit(X, y)
            resid = y - model.predict(X)
            eq = Equation(parents[v], model.coef_.copy(), float(model.intercept_), float(max(np.std(resid), 1e-3)), r2)
            for _ in range(bootstrap):
                idx = block_bootstrap(len(y), block, rng)
                b = _ridge(alpha).fit(X[idx], y[idx])
                eq.ensemble.append((b.coef_.copy(), float(b.intercept_)))
            equations[v] = eq
        fitted = [v for v in columns if v in equations]
        g = nx.DiGraph()
        g.add_nodes_from(fitted)
        for v in fitted:
            for p, k in equations[v].parents:
                if k == 0 and p in equations:
                    g.add_edge(p, v)
        return LaggedSCM(fitted, lags, {c: center[c] for c in fitted}, {c: scale[c] for c in fitted},
                         equations, list(nx.topological_sort(g)), alpha)

    # ── abduction ────────────────────────────────────────────────────────────
    def residuals(self, frame: Frame, member: int | None = None) -> pd.DataFrame:
        """Abducted noise u_v(t) in standardized units (NaN where it cannot be computed)."""
        z = self.standardized(frame.data)
        x = z.to_numpy()
        valid = lag_valid(frame, self.lags)
        out = np.full_like(x, np.nan)
        col = {v: i for i, v in enumerate(self.variables)}
        for v in self.variables:
            coef, b = self._params(v, member)
            pred = np.full(len(x), b)
            for (p, k), a in zip(self.equations[v].parents, coef):
                shifted = np.full(len(x), np.nan)
                shifted[k:] = x[: len(x) - k, col[p]] if k else x[:, col[p]]
                pred = pred + a * shifted
            r = x[:, col[v]] - pred
            r[~valid] = np.nan
            out[:, col[v]] = r
        return pd.DataFrame(out, index=z.index, columns=self.variables)

    def standardized_residuals(self, frame: Frame) -> pd.DataFrame:
        u = self.residuals(frame)
        return u / pd.Series({v: self.equations[v].resid_std for v in self.variables})

    # ── action + prediction ──────────────────────────────────────────────────
    def rollout(self, frame: Frame, interventions: dict[str, float], t0: int, member: int | None = None) -> pd.DataFrame:
        """Counterfactual values (natural units) under do(v := (1 - magnitude) * observed deviation) from row t0.

        ``interventions`` maps a variable to the fraction of its deviation from baseline that is
        removed (1.0 restores the baseline). Rows before t0 equal the observation exactly.
        """
        z_obs = self.standardized(frame.data)
        x = z_obs.to_numpy().copy()
        u = self.residuals(frame, member).to_numpy()
        col = {v: i for i, v in enumerate(self.variables)}
        params = {v: self._params(v, member) for v in self.variables}
        # Every measured quantity is non-negative: the standardized value of 0.
        floor = {v: -self.center[v] / self.scale[v] for v in self.variables}
        self.last_clamped = 0
        for t in range(max(t0, self.lags), len(x)):
            for v in self.order:
                i = col[v]
                if v in interventions:
                    observed = z_obs.iat[t, i]
                    if np.isnan(observed):
                        continue
                    removed = interventions[v] * max(observed, 0.0) if parse(v).upward else interventions[v] * observed
                    x[t, i] = observed - removed
                    continue
                if np.isnan(u[t, i]):
                    continue  # no observation to anchor this step; keep it as observed
                coef, b = params[v]
                val = b + u[t, i]
                for (p, k), a in zip(self.equations[v].parents, coef):
                    val += a * x[t - k, col[p]]
                if val < floor[v]:
                    val = floor[v]
                    self.last_clamped += 1
                x[t, i] = val
        out = pd.DataFrame(x, index=z_obs.index, columns=self.variables)
        return self.destandardized(out)

    # ── helpers ──────────────────────────────────────────────────────────────
    def _params(self, v: str, member: int | None) -> tuple[np.ndarray, float]:
        eq = self.equations[v]
        if member is None or not eq.ensemble:
            return eq.coef, eq.intercept
        return eq.ensemble[member % len(eq.ensemble)]

    def ensemble_size(self) -> int:
        return max((len(e.ensemble) for e in self.equations.values()), default=0)

    def standardized(self, data: pd.DataFrame) -> pd.DataFrame:
        return standardize(data.reindex(columns=self.variables), self.center, self.scale)

    def destandardized(self, z: pd.DataFrame) -> pd.DataFrame:
        return z * pd.Series(self.scale) + pd.Series(self.center)

    def zeroed(self) -> "LaggedSCM":
        """A copy with every coefficient and intercept set to zero (used to prove the rollout uses them)."""
        eqs = {v: Equation(e.parents, np.zeros_like(e.coef), 0.0, e.resid_std, e.r2_holdout) for v, e in self.equations.items()}
        return LaggedSCM(self.variables, self.lags, self.center, self.scale, eqs, self.order, self.alpha)

    def summary(self) -> dict:
        r2 = [e.r2_holdout for e in self.equations.values() if e.r2_holdout is not None]
        return {"variables": len(self.variables), "lags": self.lags, "ridge_alpha": self.alpha,
                "parents_total": int(sum(len(e.parents) for e in self.equations.values())),
                "ensemble_members": self.ensemble_size(),
                "median_holdout_r2": round(float(np.median(r2)), 3) if r2 else None,
                "r2_method": "blocked 5-fold cross-validation in time order"}


def blocked_cv_r2(X: np.ndarray, y: np.ndarray, alpha: float, folds: int = 5, gap: int = 3) -> float | None:
    """Out-of-sample R2 from blocked cross-validation in time order.

    Each contiguous block is predicted by a model fitted on the rest (minus ``gap`` rows on each
    side, so lagged rows of the block do not leak into training). Residual and total sums are
    pooled over all blocks. A single "last 20%" holdout is not used: when that slice happens to be
    quiet, its variance is near zero and R2 says nothing about how the equation explains incidents.
    """
    n = len(y)
    if n < folds * 20:
        return None
    ss_res = ss_tot = 0.0
    mean = float(y.mean())
    for block in np.array_split(np.arange(n), folds):
        lo, hi = max(block[0] - gap, 0), min(block[-1] + gap + 1, n)
        train = np.r_[0:lo, hi:n]
        m = _ridge(alpha).fit(X[train], y[train])
        ss_res += float(np.sum((y[block] - m.predict(X[block])) ** 2))
        ss_tot += float(np.sum((y[block] - mean) ** 2))
    return 1 - ss_res / ss_tot if ss_tot > 1e-9 * n else None


def _ridge(alpha: float) -> Ridge:
    return Ridge(alpha=alpha, positive=True)


def standardize(data: pd.DataFrame, center: dict[str, float], scale: dict[str, float]) -> pd.DataFrame:
    return (data - pd.Series(center)) / pd.Series(scale)


def design(z: pd.DataFrame, v: str, parents: list[Parent], valid: np.ndarray):
    x = z.to_numpy()
    col = {c: i for i, c in enumerate(z.columns)}
    feats = []
    for p, k in parents:
        shifted = np.full(len(x), np.nan)
        shifted[k:] = x[: len(x) - k, col[p]] if k else x[:, col[p]]
        feats.append(shifted)
    X = np.column_stack(feats) if feats else np.zeros((len(x), 0))
    y = x[:, col[v]]
    ok = valid & ~np.isnan(y) & ~np.isnan(X).any(axis=1)
    return X[ok], y[ok], np.flatnonzero(ok)


def block_bootstrap(n: int, block: int, rng: np.random.Generator) -> np.ndarray:
    block = max(1, min(block, n))
    starts = rng.integers(0, n - block + 1, size=int(np.ceil(n / block)))
    return np.concatenate([np.arange(s, s + block) for s in starts])[:n]

"""Properties of the environment-agnostic engine, checked on a small simulated system.

The fixture below is a test double only: a linear cascade with known structure so each
property (identity, containment, root-cause ranking, no window-length leakage) has a known
right answer. Runtime code never generates telemetry.
"""
import numpy as np
import pandas as pd
import pytest

from ml.engine.anomaly import AnomalyModel
from ml.engine.counterfactual import simulate
from ml.engine.episodes import Episode, disturbed_mask, from_faults
from ml.engine.forecast import Forecaster
from ml.engine.rca import rank
from ml.engine.scm import LaggedSCM
from ml.engine.topology import Topology
from ml.engine.window import Frame, build_frame, usable_columns

STEP = 5
T = 1600
FAULT = (1200, 1260)
NODES = {"gateway": "service", "orders": "service", "stock": "service", "payments": "service", "stock-db": "database"}
EDGES = [("gateway", "orders"), ("orders", "stock"), ("orders", "payments"), ("stock", "stock-db")]


def simulated(db_fault=0.0, link_fault=0.0, seed=1):
    """Telemetry rows shaped like telemetry_snapshots / edge_snapshots."""
    rng = np.random.default_rng(seed)
    t = pd.date_range("2026-01-01", periods=T, freq=f"{STEP}s", tz="UTC")
    on = np.zeros(T)
    on[FAULT[0]:FAULT[1]] = 1.0
    db = 4 + rng.normal(0, 0.4, T) + db_fault * on
    stock = 3 + db + rng.normal(0, 0.5, T)
    pay = 2 + rng.normal(0, 0.3, T)
    gap = 0.5 + np.abs(rng.normal(0, 0.2, T)) + link_fault * on
    orders = 5 + stock + 0.8 * (pay + gap) + rng.normal(0, 0.6, T)
    gw = 6 + orders + rng.normal(0, 0.7, T)
    rps = 5 + rng.normal(0, 0.2, T)
    rows = []
    for name, lat in (("gateway", gw), ("orders", orders), ("stock", stock), ("payments", pay), ("stock-db", db)):
        for i in range(T):
            rows.append({"captured_at": t[i], "service_name": name, "p50_latency": lat[i] * 0.6, "p95_latency": lat[i],
                         "p99_latency": lat[i] * 1.2, "error_rate": abs(rng.normal(0, 0.05)), "request_rate": rps[i],
                         "db_latency": db[i] if name == "stock" else None, "pool_utilization": None, "pool_pending": None})
    edges = []
    for i in range(T):
        edges.append({"captured_at": t[i], "client": "orders", "server": "payments",
                      "client_p95": pay[i] + gap[i], "server_p95": pay[i], "request_rate": 5.0, "error_rate": 0.0})
    return pd.DataFrame(rows), pd.DataFrame(edges), t


@pytest.fixture(scope="module")
def topology():
    return Topology.build(NODES, EDGES)


def fitted(topology, db_fault=0.0, link_fault=0.0):
    tele, edges, t = simulated(db_fault, link_fault)
    frame = build_frame(tele, edges, topology)
    cols = usable_columns(frame)
    data = frame.data[cols]
    episodes = [Episode("X", "x", t[FAULT[0]], t[FAULT[1] - 1])] if (db_fault or link_fault) else []
    quiet = ~disturbed_mask(data.index, episodes)
    anomaly = AnomalyModel.fit(data[quiet], z_floor=4.0, max_fpr=0.01, consecutive=3)
    f = Frame(data, frame.slo_latency, frame.slo_error, frame.segment, frame.step_seconds)
    scm = LaggedSCM.fit(f, cols, anomaly.baselines, topology, lags=2, alpha=1.0, bootstrap=5)
    return f, cols, anomaly, scm, t


def test_frame_layout_is_topology_driven(topology):
    tele, edges, _ = simulated()
    frame = build_frame(tele, edges, topology)
    assert "stock-db|latency" in frame.data.columns
    assert "orders->payments|gap" in frame.data.columns
    assert "gateway->orders|gap" not in frame.data.columns  # no edge telemetry for it: nothing invented
    assert frame.step_seconds == STEP


def test_rollout_without_intervention_reproduces_observation(topology):
    f, cols, anomaly, scm, _ = fitted(topology, db_fault=200)
    cf = scm.rollout(f, {}, t0=10)
    obs = f.data.reindex(columns=scm.variables)
    assert np.nanmax(np.abs(cf.to_numpy() - obs.to_numpy())) < 1e-6


def test_counterfactual_uses_fitted_coefficients(topology):
    """Regression for audit finding C1: the old engine ignored the SCM entirely."""
    f, cols, anomaly, scm, _ = fitted(topology, db_fault=200)
    fix = {"stock-db|latency": 1.0}
    real = scm.rollout(f, fix, t0=FAULT[0] - 5)
    zero = scm.zeroed().rollout(f, fix, t0=FAULT[0] - 5)
    gw = "gateway|latency"
    assert not np.allclose(real[gw].to_numpy()[FAULT[0]:FAULT[1]], zero[gw].to_numpy()[FAULT[0]:FAULT[1]])


def test_intervention_heals_callers_and_leaves_unreachable_nodes(topology):
    f, cols, anomaly, scm, t = fitted(topology, db_fault=200)
    out = simulate(scm, anomaly, f, topology, "stock-db", "stock-db", ["stock-db|latency"], t0=FAULT[0] - 5)
    assert out["validity"]["pre_intervention_max_diff"] == 0
    assert out["validity"]["unreachable_max_diff"] < 1e-6        # payments cannot be affected by the database
    assert out["entry_impact"]["gateway"]["peak_avoided_latency_ms"] > 150  # most of the 200 ms fault is removed
    assert out["ensemble_members"] == 5


def test_rca_names_the_database_not_its_callers(topology):
    f, cols, anomaly, scm, t = fitted(topology, db_fault=200)
    sel = slice(FAULT[0] - 24, FAULT[0] + 18)
    sub = Frame(f.data.iloc[sel], f.slo_latency.iloc[sel], f.slo_error.iloc[sel], f.segment.iloc[sel], STEP)
    ranked = rank(sub, anomaly, scm, topology, {"counterfactual": 0.6, "residual": 0.05, "anomaly": 0.1, "precedence": 0.1, "graph": 0.15},
                  analysis_start=t[FAULT[0] - 6])
    assert ranked["candidates"][0]["service"] == "stock-db"
    assert ranked["evidence"][0]["type"] == "root_cause"


def test_rca_names_a_slow_link(topology):
    f, cols, anomaly, scm, t = fitted(topology, link_fault=150)
    sel = slice(FAULT[0] - 24, FAULT[0] + 18)
    sub = Frame(f.data.iloc[sel], f.slo_latency.iloc[sel], f.slo_error.iloc[sel], f.segment.iloc[sel], STEP)
    ranked = rank(sub, anomaly, scm, topology, {"counterfactual": 0.6, "residual": 0.05, "anomaly": 0.1, "precedence": 0.1, "graph": 0.15},
                  analysis_start=t[FAULT[0] - 6])
    top = ranked["candidates"][0]
    assert top["service"] == "orders->payments" and top["kind"] == "link" and top["target"] == "payments"


def test_gate_fires_during_fault_and_stays_quiet_on_healthy_data(topology):
    f, cols, anomaly, scm, _ = fitted(topology, db_fault=200)
    flagged = anomaly.flagged(f.data).any(axis=1).to_numpy()
    assert flagged[FAULT[0] + 5: FAULT[1]].mean() > 0.9
    healthy, _, _ = simulated(seed=99)
    tele, edges, _ = simulated(seed=99)
    clean = build_frame(tele, edges, topology).data.reindex(columns=cols)
    assert anomaly.flagged(clean).any(axis=1).mean() < 0.02


def test_short_healthy_window_is_not_forecast_to_fail(topology):
    """Regression for audit finding C3: healthy runs cut to a few steps were predicted as failures."""
    f, cols, anomaly, scm, t = fitted(topology, db_fault=200)
    groups = pd.Series(np.arange(len(f.data)) // 120, index=f.data.index)
    slo = {"latencyP99Ms": 60.0, "errorRatePct": 5.0, "criticalMultiplier": 2}
    forecaster = Forecaster.fit(f, anomaly, topology, slo, {}, [30], groups)
    for n in (5, 8, 12):
        sel = slice(100, 100 + n)
        short = Frame(f.data.iloc[sel], f.slo_latency.iloc[sel], f.slo_error.iloc[sel], f.segment.iloc[sel], STEP)
        preds = forecaster.predict(short, anomaly, topology, slo, {})
        assert max(p["probability"] for p in preds) < 0.5, preds


def test_ramped_faults_become_one_episode():
    t0 = pd.Timestamp("2026-01-01T00:00:00Z")
    faults = [{"type": "SERVICE_LATENCY", "target": "orders", "started_at": t0 + pd.Timedelta(seconds=15 * i),
               "stopped_at": t0 + pd.Timedelta(seconds=15 * i + 14), "duration_seconds": 30} for i in range(6)]
    faults.append({"type": "ERROR_RATE", "target": "payments", "started_at": t0 + pd.Timedelta(minutes=10),
                   "stopped_at": t0 + pd.Timedelta(minutes=11), "duration_seconds": 60})
    eps = from_faults(faults)
    assert [(e.type, e.target) for e in eps] == [("SERVICE_LATENCY", "orders"), ("ERROR_RATE", "payments")]
    assert (eps[0].end - eps[0].start).total_seconds() == 89


def test_overlapping_faults_are_marked_concurrent():
    t0 = pd.Timestamp("2026-01-01T00:00:00Z")
    faults = [
        {"type": "SERVICE_FAILURE", "target": "payments", "started_at": t0, "stopped_at": t0 + pd.Timedelta(seconds=90),
         "duration_seconds": 90},
        {"type": "SERVICE_LATENCY", "target": "gateway", "started_at": t0 + pd.Timedelta(seconds=75),
         "stopped_at": t0 + pd.Timedelta(seconds=165), "duration_seconds": 90},
        {"type": "ERROR_RATE", "target": "stock", "started_at": t0 + pd.Timedelta(minutes=10),
         "stopped_at": t0 + pd.Timedelta(minutes=11), "duration_seconds": 60},
    ]
    eps = from_faults(faults)
    assert [e.concurrent for e in eps] == [True, True, False]


def test_fit_is_scored_across_incidents_not_on_a_quiet_tail():
    """A model that explains an incident must not be judged on a near-constant final slice."""
    from ml.engine.scm import blocked_cv_r2
    rng = np.random.default_rng(3)
    n = 600
    x = np.where((np.arange(n) > 100) & (np.arange(n) < 200), 50.0, 0.0) + rng.normal(0, 0.05, n)
    y = 2.0 * x + rng.normal(0, 0.5, n)
    X = x.reshape(-1, 1)
    r2 = blocked_cv_r2(X, y, alpha=1.0)
    assert r2 is not None and r2 > 0.95
    # The old "last 20%" holdout would see only noise around zero here.
    tail = slice(int(n * 0.8), n)
    assert np.var(y[tail]) < 1.0 < np.var(y)

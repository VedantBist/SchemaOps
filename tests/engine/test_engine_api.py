"""Calibration, model registry and HTTP contract of the engine, against an in-memory store."""
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ai-engine"))

from ml.engine import calibrate, pipeline  # noqa: E402
from ml.engine.store import EnvironmentRecord  # noqa: E402
from tests.engine.test_engine_core import EDGES, FAULT, NODES, simulated  # noqa: E402

ENV = "00000000-0000-0000-0000-000000000001"
CONFIG = {
    "slo": {"latencyP99Ms": 60.0, "errorRatePct": 5.0, "criticalMultiplier": 2},
    "serviceSlos": {},
    "calibration": {"learningWindowHours": 24, "retrainIntervalDays": 7, "minLearningMinutes": 30},
    "analysis": {"forecastHorizonsSeconds": [30], "anomalyZFloor": 4.0, "gateConsecutiveSamples": 3, "lagOrder": 2,
                 "ridgeAlpha": 1.0, "bootstrapModels": 3, "maxGateFalsePositiveRate": 0.01, "rcaDelaySeconds": 30,
                 "rcaWeights": {"counterfactual": 0.6, "residual": 0.05, "anomaly": 0.1, "precedence": 0.1, "graph": 0.15}},
}


class FakeStore:
    """Implements the Store methods the engine uses, over simulated telemetry with three labelled faults."""

    def __init__(self):
        tele, edges, t = simulated(db_fault=200)
        self.tele, self.edges, self.t = tele, edges, t
        start, end = t[FAULT[0]], t[FAULT[1] - 1]
        self.fault_rows = [{"id": "f1", "type": "DB_LATENCY", "target": "stock-db", "parameters": {}, "status": "STOPPED",
                            "started_at": start.to_pydatetime(), "stopped_at": end.to_pydatetime(), "duration_seconds": 300}]
        self.runs_by_id, self.registry = {}, []
        self.learning_started_at = self.t[0].to_pydatetime()

    def environment(self, env_id):
        if env_id != ENV:
            raise LookupError(f"Unknown environment {env_id}")
        return EnvironmentRecord(ENV, "test", "LEARNING", CONFIG, self.learning_started_at)

    def topology(self, env_id):
        return ([{"name": n, "kind": k} for n, k in NODES.items()],
                [{"client": c, "server": s, "connection_type": None} for c, s in EDGES])

    def telemetry(self, env_id, start, end):
        m = (self.tele.captured_at >= pd.Timestamp(start)) & (self.tele.captured_at <= pd.Timestamp(end))
        return self.tele[m]

    def edge_telemetry(self, env_id, start, end):
        m = (self.edges.captured_at >= pd.Timestamp(start)) & (self.edges.captured_at <= pd.Timestamp(end))
        return self.edges[m]

    def data_span(self, env_id):
        return self.t[0].to_pydatetime(), self.t[-1].to_pydatetime()

    def faults(self, env_id, start, end):
        return self.fault_rows

    def has_running_calibration(self, env_id):
        return any(r["status"] == "RUNNING" for r in self.runs_by_id.values())

    def start_run(self, env_id, mode, trigger):
        rid = f"run-{len(self.runs_by_id) + 1}"
        self.runs_by_id[rid] = {"status": "RUNNING", "mode": mode}
        return rid

    def finish_run(self, run_id, **kw):
        self.runs_by_id[run_id].update(kw)

    def runs(self, env_id, limit=20):
        return list(self.runs_by_id.values())[:limit]

    def champion(self, env_id):
        champs = [r for r in self.registry if r["status"] == "CHAMPION"]
        return champs[-1] if champs else None

    def register_model(self, env_id, **kw):
        if kw["status"] == "CHAMPION":
            for r in self.registry:
                if r["status"] == "CHAMPION":
                    r["status"] = "RETIRED"
        self.registry.append(kw)

    def models(self, env_id):
        return self.registry


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGINE_MODEL_DIR", str(tmp_path))
    return FakeStore()


def test_calibration_registers_a_validated_champion_and_serves_it(store):
    run = store.start_run(ENV, "INITIAL", "test")
    result = calibrate.calibrate(store, ENV, run)
    assert result["promoted"] is True
    m = result["metrics"]
    assert m["data"]["labelled_episodes"] == 1
    assert m["episodes"]["method"] == "leave-one-episode-out"
    assert m["episodes"]["per_episode"][0]["top1"] is True          # the database is named
    assert m["episodes"]["detection_recall"] == 1.0
    assert m["gate"]["heldout_false_positive_rate"] <= 0.01
    assert result["quality_passed"] is False                          # one labelled incident is not enough for ACTIVE
    assert "at least 3 labelled single-root incidents" in result["decision"]
    assert store.champion(ENV)["version"] == result["model_version"]

    at = store.t[FAULT[0] + 20].to_pydatetime()
    live = pipeline.evaluate(store, ENV, at)
    assert live["gate"]["incident"] is True and "stock-db" in live["gate"]["services"]
    assert {f["service"] for f in live["forecasts"]} == {"gateway", "orders", "stock", "payments"}

    rca = pipeline.analyse(store, ENV, store.t[FAULT[0] - 24].to_pydatetime(), at)
    assert rca["candidates"][0]["service"] == "stock-db"
    cf = rca["counterfactual"]
    assert cf["validity"]["unreachable_max_diff"] < 1e-6
    assert cf["entry_impact"]["gateway"]["peak_avoided_latency_ms"] > 100


def test_challenger_is_kept_only_if_not_worse(store, monkeypatch):
    calibrate.calibrate(store, ENV, store.start_run(ENV, "INITIAL", "test"))
    first = store.champion(ENV)["version"]
    # A degraded challenger: its RCA misses every episode. calibrate() evaluates the challenger
    # first and then the champion on the same episodes; only the challenger's result is degraded.
    real = calibrate.evaluate_episodes
    calls = []

    def worse(*a, **k):
        r = real(*a, **k)
        calls.append(1)
        return dict(r, rca_top1_accuracy=0.0, detection_recall=0.0) if len(calls) == 1 else r
    monkeypatch.setattr(calibrate, "evaluate_episodes", worse)
    import time
    time.sleep(1.1)  # versions are timestamped to the second
    result = calibrate.calibrate(store, ENV, store.start_run(ENV, "RETRAIN", "test"))
    assert result["promoted"] is False and "champion kept" in result["decision"]
    assert store.champion(ENV)["version"] == first


def test_champion_from_before_a_learning_restart_is_replaced(store, monkeypatch):
    calibrate.calibrate(store, ENV, store.start_run(ENV, "INITIAL", "test"))
    first = store.champion(ENV)["version"]
    # The operator changed metric definitions after the champion's data window ended.
    store.learning_started_at = (pd.Timestamp(store.champion(ENV)["data_to"]) + timedelta(seconds=1)).to_pydatetime()
    monkeypatch.setattr(calibrate, "evaluate_episodes",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("a stale champion must not be scored")))
    promote, decision = calibrate.champion_challenger(store, ENV, None, None, None, [], {})
    assert promote is True and "before learning restarted" in decision and first in decision


def test_http_contract(store, monkeypatch):
    from fastapi.testclient import TestClient
    from app import engine_api
    from app.main import app
    monkeypatch.setattr(engine_api, "_store", store)
    c = TestClient(app)
    at = store.t[100].isoformat()
    assert c.post("/pipeline/evaluate", json={"environment_id": "nope", "at": at}).status_code == 404
    assert c.post("/pipeline/evaluate", json={"environment_id": ENV, "at": at}).status_code == 409   # no model yet
    assert c.post("/pipeline/rca", json={"environment_id": ENV, "start": at, "end": at}).status_code == 422
    assert c.post("/calibration/run", json={"environment_id": ENV, "mode": "WRONG"}).status_code == 422
    monkeypatch.setattr(calibrate, "start", lambda *a: "run-x")
    r = c.post("/calibration/run", json={"environment_id": ENV, "mode": "MANUAL"})
    assert r.status_code == 202 and r.json()["run_id"] == "run-x"
    calibrate.calibrate(store, ENV, store.start_run(ENV, "INITIAL", "test"))
    ok = c.post("/pipeline/evaluate", json={"environment_id": ENV, "at": store.t[FAULT[0] + 20].isoformat()})
    assert ok.status_code == 200 and ok.json()["gate"]["incident"] is True
    bad_unit = c.post("/counterfactual", json={"environment_id": ENV, "start": store.t[FAULT[0] - 20].isoformat(),
                                               "end": store.t[FAULT[0] + 20].isoformat(), "unit": "nothing"})
    assert bad_unit.status_code == 422

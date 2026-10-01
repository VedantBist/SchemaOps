"""Unit, integration, and offline-vs-live consistency tests for CausalOps Classical ML RCA Serving.

Covers:
1. Model artifact loading
2. Feature schema matching (exact 214 features)
3. Feature ordering consistency
4. No NaN/Inf values in feature vectors
5. Model prediction shape and probability sum == 1.0
6. Valid root-cause labels
7. Leakage protection audit
8. Missing telemetry handling
9. Invalid feature schema handling
10. FastAPI ML RCA endpoint (/rca/ml and /analyze/root-cause)
11. Heuristic fallback execution on failure
12. Offline vs live feature and prediction consistency across frozen experiments
"""

import os
import sys
import json
import math
import pytest
import numpy as np

# Ensure root is on path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

AI_ENGINE_PATH = os.path.join(REPO_ROOT, "ai-engine")
if AI_ENGINE_PATH not in sys.path:
    sys.path.insert(0, AI_ENGINE_PATH)

from ml.schema import ROOT_CAUSE_TARGETS, get_feature_definitions
from ml.feature_extraction import extract_experiment_features
from app.rca.classifier import load_model_and_metadata, predict_root_cause
from app.rca.scorer import score
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture(scope="module")
def model_and_metadata():
    model, metadata = load_model_and_metadata()
    return model, metadata


@pytest.fixture(scope="module")
def api_client():
    return TestClient(app)


def test_01_model_artifact_loading(model_and_metadata):
    """Test 1: Model artifact exists, loads cleanly, and matches expected type."""
    model, metadata = model_and_metadata
    assert model is not None
    assert metadata is not None
    assert metadata["model_version"] == "classical_rca_rf_v1"
    assert metadata["model_name"] == "Random Forest"
    assert hasattr(model, "predict_proba")
    assert hasattr(model, "classes_")


def test_02_feature_schema_matching(model_and_metadata):
    """Test 2: Feature count matches exact 214 features in schema catalog."""
    _, metadata = model_and_metadata
    catalog = get_feature_definitions()
    assert len(catalog) == 214
    assert metadata["feature_count"] == 214
    assert len(metadata["features"]) == 214
    assert set(metadata["features"]) == set(catalog.keys())


def test_03_feature_ordering_consistency(model_and_metadata):
    """Test 3: Feature ordering is strictly sorted and canonical."""
    _, metadata = model_and_metadata
    catalog = get_feature_definitions()
    expected_order = sorted(list(catalog.keys()))
    assert metadata["features"] == expected_order, "Metadata feature list does not match canonical sort order"


def test_04_no_nan_or_inf_in_inference():
    """Test 4: Extracted features never contain NaN or Inf even with zero or aberrant inputs."""
    with open("dataset/experiments/EXP-001/topology.json") as f:
        topology = json.load(f)

    # Empty telemetry
    feats = extract_experiment_features([], topology)
    for fn, val in feats.items():
        assert not math.isnan(val), f"NaN found in feature {fn}"
        assert not math.isinf(val), f"Inf found in feature {fn}"

    # Telemetry with None and extreme values
    messy_telemetry = [
        {"service": "inventory-db", "latency": None, "errorRate": None, "anomaly": 9999.0, "timestamp": "2026-01-01T00:00:00Z"},
        {"service": "api-gateway", "latency": 100000.0, "errorRate": 100.0, "anomaly": 0.0, "timestamp": "2026-01-01T00:00:01Z"}
    ]
    feats2 = extract_experiment_features(messy_telemetry, topology)
    for fn, val in feats2.items():
        assert not math.isnan(val), f"NaN found in feature {fn}"
        assert not math.isinf(val), f"Inf found in feature {fn}"


def test_05_prediction_shape_and_probability_sum(model_and_metadata):
    """Test 5: Model prediction produces probabilities summing to 1.0."""
    with open("dataset/experiments/EXP-015/metrics.json") as f:
        metrics = json.load(f)
    with open("dataset/experiments/EXP-015/topology.json") as f:
        topology = json.load(f)

    res = predict_root_cause(topology, metrics["samples"])
    candidates = res["candidates"]
    assert len(candidates) == 4, f"Expected 4 candidates, got {len(candidates)}"

    prob_sum = sum(c["probability"] for c in candidates)
    assert abs(prob_sum - 1.0) < 1e-3, f"Probabilities do not sum to 1.0 (got {prob_sum})"
    assert res["confidence"] == candidates[0]["probability"]


def test_06_valid_root_cause_labels(model_and_metadata):
    """Test 6: Predicted root cause is strictly one of the 4 defined microservices."""
    with open("dataset/experiments/EXP-015/metrics.json") as f:
        metrics = json.load(f)
    with open("dataset/experiments/EXP-015/topology.json") as f:
        topology = json.load(f)

    res = predict_root_cause(topology, metrics["samples"])
    assert res["root_cause"] in ROOT_CAUSE_TARGETS
    for c in res["candidates"]:
        assert c["service"] in ROOT_CAUSE_TARGETS


def test_07_leakage_protection_audit():
    """Test 7: Confirm inference input dictionary does NOT use label/ground truth keys."""
    with open("dataset/experiments/EXP-015/metrics.json") as f:
        metrics = json.load(f)
    with open("dataset/experiments/EXP-015/topology.json") as f:
        topology = json.load(f)

    # Poison input with forbidden keys that might cause label leakage
    forbidden_keys = [
        "fault_target", "fault_type", "ground_truth", "detected_root_cause",
        "rca_match", "target", "label"
    ]
    poisoned_samples = []
    for s in metrics["samples"]:
        s_copy = dict(s)
        for k in forbidden_keys:
            s_copy[k] = "LEAKED_VALUE_SHOULD_BE_IGNORED"
        poisoned_samples.append(s_copy)

    # Inference must succeed and produce exact same result as clean samples
    res_clean = predict_root_cause(topology, metrics["samples"])
    res_poison = predict_root_cause(topology, poisoned_samples)

    assert res_clean["root_cause"] == res_poison["root_cause"]
    assert res_clean["confidence"] == res_poison["confidence"]
    assert res_clean["candidates"] == res_poison["candidates"]


def test_08_missing_telemetry_graceful_handling():
    """Test 8: System handles completely empty or single-service telemetry without crashing."""
    with open("dataset/experiments/EXP-001/topology.json") as f:
        topology = json.load(f)

    # Empty telemetry
    res_empty = predict_root_cause(topology, [])
    assert res_empty["root_cause"] in ROOT_CAUSE_TARGETS
    assert len(res_empty["candidates"]) == 4

    # Single snapshot
    single_snapshot = [{"service": "inventory-db", "latency": 50.0, "anomaly": 0.0, "timestamp": "2026-01-01T00:00:00Z"}]
    res_single = predict_root_cause(topology, single_snapshot)
    assert res_single["root_cause"] in ROOT_CAUSE_TARGETS


def test_09_fastapi_endpoints(api_client):
    """Test 9: FastAPI exposes /rca/ml and /analyze/root-cause with classical_ml mode."""
    with open("dataset/experiments/EXP-015/metrics.json") as f:
        metrics = json.load(f)
    with open("dataset/experiments/EXP-015/topology.json") as f:
        topology = json.load(f)

    # Dedicated /rca/ml endpoint
    resp_ml = api_client.post("/rca/ml", json={"topology": topology, "telemetry": metrics["samples"]})
    assert resp_ml.status_code == 200
    data_ml = resp_ml.json()
    assert data_ml["rca_method"] == "classical_ml"
    assert data_ml["model"] == "classical_rca_rf_v1"
    assert data_ml["root_cause"] == "inventory-db"
    assert data_ml["confidence"] >= 0.90

    # /analyze/root-cause default mode (classical_ml)
    resp_def = api_client.post("/analyze/root-cause", json={"topology": topology, "telemetry": metrics["samples"]})
    assert resp_def.status_code == 200
    data_def = resp_def.json()
    assert data_def["rca_method"] == "classical_ml"
    assert data_def["model"] == "classical_rca_rf_v1"
    assert data_def["root_cause"] == "inventory-db"

    # /analyze/root-cause explicit heuristic mode
    resp_heur = api_client.post("/analyze/root-cause", json={"topology": topology, "telemetry": metrics["samples"], "mode": "heuristic"})
    assert resp_heur.status_code == 200
    data_heur = resp_heur.json()
    assert data_heur["rca_method"] == "heuristic"
    assert "model" not in data_heur  # Never report ML model for heuristic


def test_10_heuristic_fallback(api_client, monkeypatch):
    """Test 10: If ML inference fails, fallback to heuristic baseline with explicit metadata."""
    from app.rca import classifier

    def broken_predict(*args, **kwargs):
        raise RuntimeError("Simulated ML engine crash")

    monkeypatch.setattr(classifier, "predict_root_cause", broken_predict)

    with open("dataset/experiments/EXP-015/metrics.json") as f:
        metrics = json.load(f)
    with open("dataset/experiments/EXP-015/topology.json") as f:
        topology = json.load(f)

    resp = api_client.post("/analyze/root-cause", json={"topology": topology, "telemetry": metrics["samples"], "mode": "classical_ml"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["rca_method"] == "heuristic_fallback"
    assert "HEURISTIC FALLBACK" in data["methodology"]
    assert "model" not in data  # Never report classical_rca_rf_v1 when fallback was used


def test_11_offline_vs_live_consistency(model_and_metadata):
    """
    Test 11: OFFLINE VS LIVE CONSISTENCY TEST.
    For selected test set experiments across all targets:
    Compare offline feature extraction and model prediction against live predict_root_cause.
    Assert exact feature vector equality and identical predicted root cause and probabilities.
    """
    model, metadata = model_and_metadata
    feature_names = metadata["features"]

    # Select representative experiments from different targets
    test_experiments = [
        ("EXP-015", "inventory-db"),
        ("EXP-032", "inventory-service"),
        ("EXP-050", "order-service"),
        ("EXP-064", "payment-service")
    ]

    for exp_id, expected_target in test_experiments:
        metrics_path = f"dataset/experiments/{exp_id}/metrics.json"
        topo_path = f"dataset/experiments/{exp_id}/topology.json"
        if not (os.path.exists(metrics_path) and os.path.exists(topo_path)):
            continue

        with open(metrics_path) as f:
            metrics = json.load(f)
        with open(topo_path) as f:
            topology = json.load(f)

        # OFFLINE: compute feature dict from metrics dict
        offline_feat_dict = extract_experiment_features(metrics, topology)
        offline_vector = [offline_feat_dict[fn] for fn in feature_names]
        offline_probs = model.predict_proba([offline_vector])[0]
        offline_pred = model.classes_[np.argmax(offline_probs)]

        # LIVE: compute via classifier.predict_root_cause with samples list
        live_res = predict_root_cause(topology, metrics["samples"])

        # Compare feature vectors
        live_feat_dict = extract_experiment_features(metrics["samples"], topology)
        live_vector = [live_feat_dict[fn] for fn in feature_names]

        # Vector numerical check
        np.testing.assert_allclose(
            offline_vector, live_vector, rtol=1e-5, atol=1e-5,
            err_msg=f"Feature vector mismatch between offline and live for {exp_id}"
        )

        # Prediction and confidence match
        assert live_res["root_cause"] == offline_pred, f"Prediction mismatch for {exp_id}"
        assert abs(live_res["confidence"] - float(np.max(offline_probs))) < 1e-4, f"Probability mismatch for {exp_id}"
        assert live_res["root_cause"] == expected_target, f"Expected {expected_target}, got {live_res['root_cause']}"

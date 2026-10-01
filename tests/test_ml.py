"""Unit tests for CausalOps ML pipeline: feature extraction, split leakage, and integrity."""

import os
import json
import pytest
import numpy as np
import pandas as pd

from ml.schema import (
    SERVICES,
    ROOT_CAUSE_TARGETS,
    get_feature_definitions,
    GROUP_A_TELEMETRY,
    GROUP_B_TEMPORAL,
    GROUP_C_GRAPH
)
from ml.feature_extraction import extract_experiment_features
from ml.split import create_experiment_splits

def test_feature_extraction_completeness_and_validity():
    """Verify that feature extraction yields all 214 catalog features with 0 NaNs/Infs."""
    exp_path = "dataset/experiments/EXP-011"
    with open(os.path.join(exp_path, "metrics.json")) as f:
        metrics = json.load(f)
    with open(os.path.join(exp_path, "topology.json")) as f:
        topology = json.load(f)

    feats = extract_experiment_features(metrics, topology, "EXP-011")
    catalog = get_feature_definitions()

    assert len(feats) == len(catalog), f"Expected {len(catalog)} features, got {len(feats)}"
    assert set(feats.keys()) == set(catalog.keys()), "Extracted feature keys do not match catalog!"

    for k, v in feats.items():
        assert not np.isnan(v), f"NaN detected in feature {k}"
        assert not np.isinf(v), f"Inf detected in feature {k}"
        assert isinstance(v, (int, float, np.floating)), f"Non-numeric value in feature {k}: {type(v)}"

def test_missing_telemetry_handling():
    """Verify graceful handling and numerical default return when telemetry is partial or empty."""
    empty_metrics = {"service": None, "samples": []}
    mock_topology = {
        "nodes": [{"id": "1", "name": s} for s in SERVICES],
        "edges": [{"source": "api-gateway", "target": "order-service"}]
    }

    feats = extract_experiment_features(empty_metrics, mock_topology, "EXP-EMPTY")
    catalog = get_feature_definitions()

    assert len(feats) == len(catalog)
    for k, v in feats.items():
        assert not np.isnan(v), f"NaN detected in empty telemetry handling for {k}"
        assert not np.isinf(v), f"Inf detected in empty telemetry handling for {k}"

def test_split_leakage_detection():
    """Verify zero overlap between train, validation, and test sets and complete coverage."""
    splits_file = "dataset/ml_v1/splits.json"
    assert os.path.exists(splits_file), f"Missing {splits_file}"

    with open(splits_file) as f:
        splits = json.load(f)

    train_set = set(splits["train_ids"])
    val_set = set(splits["validation_ids"])
    test_set = set(splits["test_ids"])

    # Disjointness checks
    assert len(train_set.intersection(val_set)) == 0, "Leakage: Train and Validation sets overlap!"
    assert len(val_set.intersection(test_set)) == 0, "Leakage: Validation and Test sets overlap!"
    assert len(train_set.intersection(test_set)) == 0, "Leakage: Train and Test sets overlap!"

    # Completeness checks
    all_assigned = train_set.union(val_set).union(test_set)
    expected_all = {f"EXP-{i:03d}" for i in range(1, 81)}
    assert all_assigned == expected_all, f"Mismatch in split experiment coverage: {expected_all - all_assigned}"

    # Target counts check
    assert len(train_set) == 56
    assert len(val_set) == 12
    assert len(test_set) == 12

def test_label_integrity():
    """Verify ground truth label distributions across all 80 experiments."""
    labels_file = "dataset/ml_v1/labels.csv"
    assert os.path.exists(labels_file), f"Missing {labels_file}"

    df = pd.read_csv(labels_file)
    assert len(df) == 80, f"Expected 80 experiment labels, got {len(df)}"

    counts = df["fault_target"].value_counts().to_dict()
    assert counts.get("inventory-db") == 18, f"Unexpected inventory-db count: {counts.get('inventory-db')}"
    assert counts.get("payment-service") == 18, f"Unexpected payment-service count: {counts.get('payment-service')}"
    assert counts.get("inventory-service") == 17, f"Unexpected inventory-service count: {counts.get('inventory-service')}"
    assert counts.get("order-service") == 17, f"Unexpected order-service count: {counts.get('order-service')}"
    assert counts.get("NO_FAULT") == 10, f"Unexpected NO_FAULT count: {counts.get('NO_FAULT')}"

    # Fault counts check
    assert df["is_fault"].sum() == 70
    assert (~df["is_fault"]).sum() == 10

def test_deterministic_feature_generation():
    """Verify that multiple extractions produce bit-for-bit identical feature values."""
    exp_path = "dataset/experiments/EXP-020"
    with open(os.path.join(exp_path, "metrics.json")) as f:
        metrics = json.load(f)
    with open(os.path.join(exp_path, "topology.json")) as f:
        topology = json.load(f)

    feats_run1 = extract_experiment_features(metrics, topology, "EXP-020")
    feats_run2 = extract_experiment_features(metrics, topology, "EXP-020")

    assert feats_run1 == feats_run2, "Feature extraction is not deterministic across runs!"

def test_zero_target_leakage_in_features():
    """Verify that feature columns do not include heuristic RCA predictions or ground-truth meta."""
    features_file = "dataset/ml_v1/features.csv"
    assert os.path.exists(features_file), f"Missing {features_file}"

    df = pd.read_csv(features_file)
    feature_cols = [c for c in df.columns if c not in ("experiment_id", "target")]

    forbidden_substrings = [
        "heuristic",
        "detected_root_cause",
        "rca_match",
        "ground_truth",
        "fault_type",
        "fault_target",
        "confidence"
    ]

    for col in feature_cols:
        for forbidden in forbidden_substrings:
            assert forbidden not in col.lower(), f"Potential target leakage in feature name: {col}"

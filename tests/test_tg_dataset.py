"""Comprehensive test suite for CausalOps Temporal Graph Dataset v1 (dataset/tg_v1).

Validates:
1. Schema definitions & graph topology
2. Graph construction & deterministic node ordering
3. Temporal sorting & monotonicity
4. Sequence dimensions, padding, & temporal mask
5. Label handling & NO_FAULT control semantics
6. Leakage prevention
7. Split integrity & disjointness
8. Serialization, deserialization, & loader API
9. Automated audit execution
10. Source dataset immutability
"""

import os
import sys
import json
import pytest
import numpy as np

# Ensure root on sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dataset.tg_v1.schema import (
    NODE_ORDER,
    NUM_NODES,
    EDGES,
    EDGE_INDEX,
    NUM_EDGES,
    NODE_FEATURE_NAMES,
    NUM_NODE_FEATURES,
    ROOT_CAUSE_SERVICES,
    TARGET_TO_INDEX,
    NO_FAULT_LABEL,
    NO_FAULT_TARGET_INDEX,
    NO_FAULT_NODE_INDEX,
    MAX_SEQUENCE_LENGTH,
    get_graph_schema
)
from dataset.tg_v1.loader import TemporalGraphDataset, TemporalGraphSample
from dataset.tg_v1.validate import run_temporal_graph_audit


@pytest.fixture(scope="module")
def tg_dataset():
    return TemporalGraphDataset(dataset_dir="dataset/tg_v1")


def test_01_schema_definitions():
    """Test 1: Schema specifies exact 5 nodes, 4 directed edges, 10 features, and 4 target classes."""
    schema = get_graph_schema()
    assert schema["node_count"] == 5
    assert schema["node_order"] == ["api-gateway", "order-service", "inventory-service", "payment-service", "inventory-db"]
    assert schema["edge_count"] == 4
    assert schema["feature_count"] == 10
    assert len(schema["node_feature_names"]) == 10
    assert len(schema["label_definitions"]["root_cause_classes"]) == 4
    assert schema["sequence_policy"]["max_observed_length"] == 40
    assert schema["sequence_policy"]["padded_length"] == 40


def test_02_graph_construction(tg_dataset):
    """Test 2: All 80 samples have identical 5 nodes and 4 directed call edges."""
    assert len(tg_dataset) == 80
    expected_edge_index = np.array(EDGE_INDEX, dtype=np.int64)

    for sample in tg_dataset:
        assert sample.node_names == NODE_ORDER
        assert np.array_equal(sample.edge_index, expected_edge_index)


def test_03_temporal_sorting_and_monotonicity(tg_dataset):
    """Test 3: Timestamps are unique, chronologically ordered, and relative time is strictly increasing."""
    for sample in tg_dataset:
        ts = sample.timestamps
        assert len(ts) == len(set(ts)), f"Duplicate timestamps in {sample.experiment_id}"

        rel_t = sample.relative_time_sec
        assert rel_t[0] == 0.0, f"Relative time does not start at 0.0 in {sample.experiment_id}"
        assert np.all(np.diff(rel_t) > 0), f"Non-monotonic timestamps in {sample.experiment_id}"


def test_04_sequence_dimensions_and_padding(tg_dataset):
    """Test 4: Tensor shapes adhere strictly to [T, 5, 10] unpadded and [40, 5, 10] padded with valid mask."""
    for sample in tg_dataset:
        T = sample.sequence_length
        assert 38 <= T <= 40

        # Unpadded tensor
        assert sample.x.shape == (T, NUM_NODES, NUM_NODE_FEATURES)
        assert sample.x.dtype == np.float32

        # Padded tensor
        assert sample.x_padded.shape == (MAX_SEQUENCE_LENGTH, NUM_NODES, NUM_NODE_FEATURES)
        assert sample.x_padded.dtype == np.float32

        # Mask
        assert sample.temporal_mask.shape == (MAX_SEQUENCE_LENGTH,)
        assert sample.temporal_mask.dtype == bool
        assert int(sample.temporal_mask.sum()) == T
        assert np.all(sample.temporal_mask[:T])
        if T < MAX_SEQUENCE_LENGTH:
            assert not np.any(sample.temporal_mask[T:])
            assert np.all(sample.x_padded[T:, :, :] == 0.0)


def test_05_label_handling_and_no_fault(tg_dataset):
    """Test 5: Fault experiments have valid 4-class labels, controls are explicitly marked."""
    fault_count = 0
    control_count = 0

    for sample in tg_dataset:
        if sample.is_fault:
            fault_count += 1
            assert sample.label in ROOT_CAUSE_SERVICES
            assert sample.label_type == "ROOT_CAUSE"
            assert sample.target_class in [0, 1, 2, 3]
            assert sample.target_class == TARGET_TO_INDEX[sample.label]
            assert sample.node_label_index == NODE_ORDER.index(sample.label)
        else:
            control_count += 1
            assert sample.label == NO_FAULT_LABEL
            assert sample.label_type == "NO_FAULT"
            assert sample.target_class == NO_FAULT_TARGET_INDEX
            assert sample.node_label_index == NO_FAULT_NODE_INDEX

    assert fault_count == 70
    assert control_count == 10


def test_06_leakage_prevention(tg_dataset):
    """Test 6: Node features contain zero label, fault type, or prediction metadata."""
    forbidden_tokens = ["fault", "target", "ground_truth", "detected", "prediction", "rca_match"]
    for fn in NODE_FEATURE_NAMES:
        for tok in forbidden_tokens:
            assert tok not in fn.lower(), f"Forbidden token '{tok}' found in feature '{fn}'"

    # Verify no NaN or Inf in any sample
    for sample in tg_dataset:
        assert not np.isnan(sample.x).any(), f"NaN in {sample.experiment_id}"
        assert not np.isinf(sample.x).any(), f"Inf in {sample.experiment_id}"


def test_07_split_integrity(tg_dataset):
    """Test 7: Train (56), Validation (12), and Test (12) are strictly disjoint and match splits.json."""
    with open("dataset/ml_v1/splits.json") as f:
        splits = json.load(f)

    train_ids = set(splits["train_ids"])
    val_ids = set(splits["validation_ids"])
    test_ids = set(splits["test_ids"])

    train_ds = tg_dataset.get_split("train")
    val_ds = tg_dataset.get_split("validation")
    test_ds = tg_dataset.get_split("test")

    assert len(train_ds) == 56
    assert len(val_ds) == 12
    assert len(test_ds) == 12

    obs_train = {s.experiment_id for s in train_ds}
    obs_val = {s.experiment_id for s in val_ds}
    obs_test = {s.experiment_id for s in test_ds}

    assert obs_train == train_ids
    assert obs_val == val_ids
    assert obs_test == test_ids

    # Pairwise disjoint
    assert len(obs_train.intersection(obs_val)) == 0
    assert len(obs_train.intersection(obs_test)) == 0
    assert len(obs_val.intersection(obs_test)) == 0


def test_08_loader_and_sample_interface(tg_dataset):
    """Test 8: Loader API supports item access, ID lookup, fault filtering, and property aliases."""
    sample = tg_dataset.get("EXP-015")
    assert isinstance(sample, TemporalGraphSample)
    assert sample.experiment_id == "EXP-015"
    assert sample.label == "inventory-db"
    assert np.array_equal(sample.node_features, sample.x)
    assert np.array_equal(sample.node_features_padded, sample.x_padded)

    # Fault-only filtering
    fault_ds = TemporalGraphDataset(dataset_dir="dataset/tg_v1", fault_only=True)
    assert len(fault_ds) == 70
    assert all(s.is_fault for s in fault_ds)


def test_09_automated_validation_report():
    """Test 9: Automated validation suite executes and returns status PASS."""
    report = run_temporal_graph_audit(
        dataset_dir="dataset/tg_v1",
        source_splits_path="dataset/ml_v1/splits.json"
    )
    assert report["status"] == "PASS"
    assert report["experiments_count"] == 80
    assert report["fault_experiments_count"] == 70
    assert report["control_experiments_count"] == 10
    assert report["integrity_metrics"]["nan_count"] == 0
    assert report["integrity_metrics"]["inf_count"] == 0
    assert report["integrity_metrics"]["split_violations"] == 0
    assert report["integrity_metrics"]["leakage_violations"] == 0


def test_10_source_immutability():
    """Test 10: Raw frozen experiments remain completely pristine and unmodified."""
    import subprocess
    res = subprocess.run(
        ["git", "status", "--porcelain", "dataset/experiments"],
        capture_output=True,
        text=True,
        check=True
    )
    assert res.stdout.strip() == "", f"Frozen experiments directory has git modifications: {res.stdout}"

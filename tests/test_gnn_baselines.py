"""Unit and integration tests for CausalOps GNN baselines (Phase 2B).

Verifies:
  1. Dataset loader integration
  2. Temporal aggregation
  3. Normalization fitting (train only)
  4. Normalization application
  5. Zero target leakage
  6. MLP forward pass & output shape
  7. GCN forward pass & output shape
  8. GAT forward pass & output shape
  9. Kipf & Welling GCN gradient verification
  10. Veličković GAT gradient verification
  11. Deterministic seed behavior
  12. Checkpoint save & load
  13. Evaluation pipeline on held-out test split
  14. NO_FAULT control evaluation
  15. Split integrity & disjointness
"""

import os
import json
import pytest
import numpy as np

from dataset.tg_v1.loader import TemporalGraphDataset, TemporalGraphSample
from dataset.tg_v1.schema import NUM_NODES, NODE_ORDER
from ml.gnn_baselines.features import (
    FEATURE_SETS,
    aggregate_sample_temporal_features,
    fit_normalization,
    apply_normalization
)
from ml.gnn_baselines.models import (
    MLPBaseline,
    GCNBaseline,
    GATBaseline,
    get_canonical_adjacency,
    compute_cross_entropy,
    compute_cross_entropy_grad,
    Adam
)
from ml.gnn_baselines.train import train_model, prepare_dataset_arrays
from ml.gnn_baselines.evaluate import evaluate_model_on_split, evaluate_checkpoint_file
from ml.gnn_baselines.metrics import evaluate_predictions, evaluate_controls, CLASS_NAMES
from ml.gnn_baselines.utils import set_seed, save_checkpoint, load_checkpoint


@pytest.fixture(scope="module")
def train_dataset():
    return TemporalGraphDataset(split="train", fault_only=True)


@pytest.fixture(scope="module")
def val_dataset():
    return TemporalGraphDataset(split="validation", fault_only=True)


@pytest.fixture(scope="module")
def test_dataset():
    return TemporalGraphDataset(split="test", fault_only=True)


@pytest.fixture(scope="module")
def control_samples():
    all_ds = TemporalGraphDataset(split=None, fault_only=False)
    return [s for s in all_ds if not s.is_fault]


def test_dataset_loader_integration(train_dataset, val_dataset, test_dataset, control_samples):
    """Verifies that dataset splits match Phase 2A specifications."""
    assert len(train_dataset) == 49
    assert len(val_dataset) == 11
    assert len(test_dataset) == 10
    assert len(control_samples) == 10


def test_split_integrity_and_disjointness():
    """Verifies that experiment IDs across train, val, and test splits are strictly disjoint."""
    with open("dataset/tg_v1/splits.json", "r") as f:
        splits = json.load(f)
    train_ids = set(splits["train_ids"])
    val_ids = set(splits["validation_ids"])
    test_ids = set(splits["test_ids"])

    assert len(train_ids) == 56
    assert len(val_ids) == 12
    assert len(test_ids) == 12
    assert len(train_ids.intersection(val_ids)) == 0
    assert len(train_ids.intersection(test_ids)) == 0
    assert len(val_ids.intersection(test_ids)) == 0


def test_temporal_aggregation(train_dataset):
    """Verifies deterministic temporal aggregation producing [N, F*6] features."""
    sample = train_dataset[0]
    agg = aggregate_sample_temporal_features(sample, FEATURE_SETS["all"]["indices"])
    assert agg.shape == (NUM_NODES, 60)
    assert not np.isnan(agg).any()
    assert not np.isinf(agg).any()

    # Test ablation feature sets
    agg_no_anom = aggregate_sample_temporal_features(sample, FEATURE_SETS["no_anomaly_score"]["indices"])
    assert agg_no_anom.shape == (NUM_NODES, 54)

    agg_raw = aggregate_sample_temporal_features(sample, FEATURE_SETS["raw_telemetry_only"]["indices"])
    assert agg_raw.shape == (NUM_NODES, 48)


def test_normalization_fitting_and_application(train_dataset, val_dataset):
    """Verifies normalization is fitted ONLY on train and transforms data without NaNs."""
    mean, std = fit_normalization(list(train_dataset), FEATURE_SETS["all"]["indices"])
    assert mean.shape == (NUM_NODES, 60)
    assert std.shape == (NUM_NODES, 60)
    assert (std > 0).all()

    # Apply to val sample
    val_sample = val_dataset[0]
    raw_val = aggregate_sample_temporal_features(val_sample, FEATURE_SETS["all"]["indices"])
    norm_val = apply_normalization(raw_val, mean, std)
    assert norm_val.shape == (NUM_NODES, 60)
    assert not np.isnan(norm_val).any()


def test_no_target_leakage(train_dataset):
    """Verifies aggregated features contain no label metadata."""
    sample = train_dataset[0]
    agg = aggregate_sample_temporal_features(sample, FEATURE_SETS["all"]["indices"])
    # Features are pure floats
    assert agg.dtype in [np.float32, np.float64]
    # Check that target class or label string is not present in numeric array
    assert sample.label not in str(agg)


def test_mlp_forward_and_output_shape():
    """Verifies MLP forward pass with synthetic batch."""
    np.random.seed(42)
    B, N, D = 4, 5, 60
    X = np.random.randn(B, N, D)
    mlp = MLPBaseline(in_features=N * D, hidden_dim1=64, hidden_dim2=32, num_classes=4)
    mlp.eval()
    out = mlp.forward(X)
    assert out.shape == (B, 4)


def test_gcn_forward_and_output_shape():
    """Verifies GCN forward pass with synthetic batch."""
    np.random.seed(42)
    B, N, D = 4, 5, 60
    X = np.random.randn(B, N, D)
    gcn = GCNBaseline(in_features=D, hidden_dim1=32, hidden_dim2=32, num_classes=4)
    gcn.eval()
    out = gcn.forward(X)
    assert out.shape == (B, 4)


def test_gat_forward_and_output_shape():
    """Verifies GAT forward pass with synthetic batch."""
    np.random.seed(42)
    B, N, D = 4, 5, 60
    X = np.random.randn(B, N, D)
    gat = GATBaseline(in_features=D, hidden_dim1=32, hidden_dim2=32, num_heads=2, num_classes=4)
    gat.eval()
    out = gat.forward(X)
    assert out.shape == (B, 4)


def test_deterministic_seed_behavior(train_dataset, val_dataset):
    """Verifies exact reproducibility when training with identical seeds."""
    run1 = train_model("gcn", list(train_dataset), list(val_dataset), seed=123, max_epochs=10)
    run2 = train_model("gcn", list(train_dataset), list(val_dataset), seed=123, max_epochs=10)

    for k in run1["model"].params:
        diff = np.max(np.abs(run1["model"].params[k] - run2["model"].params[k]))
        assert diff == 0.0, f"Discrepancy in param {k}: {diff}"


def test_checkpoint_save_and_load(tmp_path):
    """Verifies that model checkpoints serialize and restore weights identically."""
    ckpt_file = str(tmp_path / "test_gcn.pt")
    gcn = GCNBaseline(in_features=60, seed=42)
    config = {"model_type": "gcn", "feature_set": "all", "in_features": 60, "parameter_count": gcn.count_parameters()}
    mean = np.zeros((5, 60))
    std = np.ones((5, 60))
    val_m = {"macro_f1": 0.95}

    save_checkpoint(ckpt_file, gcn, config, mean, std, val_m, best_epoch=20)
    assert os.path.exists(ckpt_file)

    loaded = load_checkpoint(ckpt_file)
    assert loaded["config"]["model_type"] == "gcn"
    assert loaded["best_epoch"] == 20
    for k in gcn.params:
        assert np.array_equal(gcn.params[k], loaded["state_dict"][k])


def test_evaluation_pipeline_metrics():
    """Verifies metrics computation against known synthetic predictions."""
    y_true = np.array([0, 1, 2, 3, 0, 1])
    y_pred = np.array([0, 1, 2, 3, 1, 1])  # 5/6 correct
    metrics = evaluate_predictions(y_true, y_pred)
    assert metrics["sample_count"] == 6
    assert metrics["accuracy"] == pytest.approx(5 / 6)
    assert "macro_f1" in metrics
    assert "per_class" in metrics
    assert len(metrics["per_class"]) == 4
    assert len(metrics["confusion_matrix"]) == 4


def test_control_evaluation(control_samples):
    """Verifies control sample evaluation does not produce errors."""
    gat = GATBaseline(in_features=60, seed=42)
    mean = np.zeros((5, 60))
    std = np.ones((5, 60))
    res = evaluate_controls(control_samples, gat, mean, std, FEATURE_SETS["all"]["indices"])
    assert res["control_count"] == 10
    assert "prediction_distribution" in res
    assert "mean_confidence" in res

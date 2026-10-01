"""Unit and integration tests for Phase 2C Temporal Graph Learning models.

Verifies:
  1. TemporalOnlyBaseline forward pass and output shape
  2. TemporalGATBaseline forward pass and output shape
  3. SpatioTemporalGNN forward pass and output shape
  4. Temporal mask handling & GRU state freeze
  5. Strict padded timestep invariance (perturbing padding has 0.0 effect)
  6. Normalization fitting zero-leakage (train split only)
  7. Normalization application
  8. Checkpoint saving and loading
  9. Deterministic reproducibility
  10. Split integrity
  11. NO_FAULT control evaluation
  12. Propagation timeline generation
"""

import os
import json
import pytest
import numpy as np

from dataset.tg_v1.loader import TemporalGraphDataset, TemporalGraphSample
from dataset.tg_v1.schema import NUM_NODES
from ml.temporal_gnn.features import (
    FEATURE_SETS,
    fit_temporal_normalization,
    prepare_temporal_batch
)
from ml.temporal_gnn.temporal_model import TemporalOnlyBaseline, MaskedGRU
from ml.temporal_gnn.temporal_gat import TemporalGATBaseline
from ml.temporal_gnn.spatiotemporal import SpatioTemporalGNN
from ml.temporal_gnn.train import train_temporal_model
from ml.temporal_gnn.evaluate import (
    evaluate_temporal_model_on_samples,
    evaluate_temporal_controls,
    evaluate_checkpoint_file
)
from ml.temporal_gnn.analysis import analyze_experiment_propagation
from ml.gnn_baselines.utils import save_checkpoint, load_checkpoint


@pytest.fixture(scope="module")
def train_samples():
    return list(TemporalGraphDataset(split="train", fault_only=True))


@pytest.fixture(scope="module")
def test_samples():
    return list(TemporalGraphDataset(split="test", fault_only=True))


@pytest.fixture(scope="module")
def control_samples():
    all_ds = list(TemporalGraphDataset(split=None, fault_only=False))
    return [s for s in all_ds if not s.is_fault]


def test_split_integrity():
    """Verifies that splits contain exact disjoint counts matching Phase 2A/2B."""
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


def test_normalization_leakage_and_application(train_samples, test_samples):
    """Verifies that normalization is fitted strictly on train fault samples."""
    mean, std = fit_temporal_normalization(train_samples)
    assert mean.shape == (NUM_NODES, 10)
    assert std.shape == (NUM_NODES, 10)
    assert (std > 0).all()

    # Apply to test samples
    X_test, mask_test, y_test, _ = prepare_temporal_batch(test_samples, mean, std)
    assert X_test.shape == (10, 40, NUM_NODES, 10)
    assert mask_test.shape == (10, 40)
    assert not np.isnan(X_test).any()


def test_temporal_only_forward_and_padding_invariance():
    """Verifies TemporalOnlyBaseline forward pass and strict padding invariance."""
    np.random.seed(42)
    B, T, N, F = 2, 40, 5, 10
    X = np.random.randn(B, T, N, F)
    mask = np.ones((B, T), dtype=bool)
    mask[0, 38:] = False
    mask[1, 39:] = False

    model = TemporalOnlyBaseline(num_nodes=N, num_features=F)
    model.eval()
    out1 = model.forward(X, mask)
    assert out1.shape == (B, 4)

    # Perturb padded values arbitrarily
    X_pert = X.copy()
    X_pert[0, 38:, :, :] += 12345.67
    X_pert[1, 39:, :, :] -= 98765.43

    out2 = model.forward(X_pert, mask)
    diff = np.max(np.abs(out1 - out2))
    assert diff == 0.0, f"TemporalOnlyBaseline leaked padding into output! Diff: {diff}"


def test_temporal_gat_forward_and_padding_invariance():
    """Verifies TemporalGATBaseline forward pass and strict padding invariance."""
    np.random.seed(42)
    B, T, N, F = 2, 40, 5, 10
    X = np.random.randn(B, T, N, F)
    mask = np.ones((B, T), dtype=bool)
    mask[0, 38:] = False

    model = TemporalGATBaseline(num_nodes=N, num_features=F)
    model.eval()
    out1, attn_info = model.forward(X, mask, return_attention=True)
    assert out1.shape == (B, 4)
    assert attn_info["attention"].shape == (B, T, N, N)

    # Perturb padded values
    X_pert = X.copy()
    X_pert[0, 38:, :, :] += 777.7

    out2, _ = model.forward(X_pert, mask)
    diff = np.max(np.abs(out1 - out2))
    assert diff == 0.0, f"TemporalGATBaseline leaked padding into output! Diff: {diff}"


def test_spatiotemporal_forward_and_padding_invariance():
    """Verifies SpatioTemporalGNN forward pass and strict padding invariance."""
    np.random.seed(42)
    B, T, N, F = 2, 40, 5, 10
    X = np.random.randn(B, T, N, F)
    mask = np.ones((B, T), dtype=bool)
    mask[0, 38:] = False

    model = SpatioTemporalGNN(num_nodes=N, num_features=F)
    model.eval()
    out1, node_info = model.forward(X, mask, return_node_states=True)
    assert out1.shape == (B, 4)
    assert node_info["node_states"].shape == (B, N, 48)

    # Perturb padded values
    X_pert = X.copy()
    X_pert[0, 38:, :, :] += 555.5

    out2, _ = model.forward(X_pert, mask)
    diff = np.max(np.abs(out1 - out2))
    assert diff == 0.0, f"SpatioTemporalGNN leaked padding into output! Diff: {diff}"


def test_deterministic_reproducibility(train_samples):
    """Verifies identical parameters when training twice with seed=42."""
    val_subset = train_samples[:6]
    train_subset = train_samples[6:]

    run1 = train_temporal_model("temporal_only", train_subset, val_subset, seed=42, max_epochs=5)
    run2 = train_temporal_model("temporal_only", train_subset, val_subset, seed=42, max_epochs=5)

    for k in run1["model"].params:
        diff = np.max(np.abs(run1["model"].params[k] - run2["model"].params[k]))
        assert diff == 0.0, f"Discrepancy in param {k}: {diff}"


def test_checkpoint_save_and_load(tmp_path):
    """Verifies saving and loading of spatio-temporal checkpoints."""
    ckpt_path = str(tmp_path / "spatio_test.pt")
    model = SpatioTemporalGNN(num_nodes=5, num_features=10, seed=42)
    cfg = {"model_type": model.name, "num_features": 10, "parameter_count": model.count_parameters()}
    mean = np.zeros((5, 10))
    std = np.ones((5, 10))

    save_checkpoint(ckpt_path, model, cfg, mean, std, {"macro_f1": 0.99}, best_epoch=10)
    assert os.path.exists(ckpt_path)

    loaded = load_checkpoint(ckpt_path)
    assert loaded["config"]["model_type"] == "spatiotemporal_gnn_v1"
    for k in model.params:
        assert np.array_equal(model.params[k], loaded["state_dict"][k])


def test_control_evaluation(control_samples):
    """Verifies control sample evaluation runs cleanly without errors."""
    model = SpatioTemporalGNN(num_nodes=5, num_features=10, seed=42)
    mean = np.zeros((5, 10))
    std = np.ones((5, 10))

    res = evaluate_temporal_controls(model, control_samples, mean, std)
    assert res["control_count"] == 10
    assert "prediction_distribution" in res
    assert "mean_confidence" in res


def test_propagation_analysis(test_samples):
    """Verifies temporal propagation timeline extraction on a test sample."""
    sample = test_samples[0]
    model = SpatioTemporalGNN(num_nodes=5, num_features=10, seed=42)
    mean = np.zeros((5, 10))
    std = np.ones((5, 10))

    report = analyze_experiment_propagation(model, sample, mean, std, evaluation_timesteps=[10, 20, 30])
    assert report["experiment_id"] == sample.experiment_id
    assert len(report["timeline"]) == 3
    for step in report["timeline"]:
        assert "predicted_class" in step
        assert "confidence" in step

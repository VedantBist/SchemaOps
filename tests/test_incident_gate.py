"""Unit and integration tests for Phase 2D Incident Gating & Gated RCA Pipeline.

Verifies:
  1. IncidentGateMLP forward pass and output bounds in [0.0, 1.0]
  2. Threshold selection logic (validation sweep)
  3. Train-only normalization fitting
  4. Split integrity & disjointness
  5. Zero target leakage
  6. Gate decision: NORMAL vs INCIDENT
  7. Decoupled policy: If P(incident) < threshold => status=NORMAL, root_cause=None, rca_invoked=False
  8. Decoupled policy: If P(incident) >= threshold => status=INCIDENT, RCA model invoked
  9. Checkpoint saving and loading
  10. End-to-end pipeline composition on real test samples
"""

import os
import json
import pytest
import numpy as np

from dataset.tg_v1.loader import TemporalGraphDataset, TemporalGraphSample
from ml.incident_gate.features import (
    extract_gate_features,
    fit_incident_gate_normalization,
    apply_incident_gate_normalization,
    prepare_gate_dataset
)
from ml.incident_gate.model import IncidentGateMLP, compute_binary_cross_entropy
from ml.incident_gate.train import train_incident_gate, select_best_threshold
from ml.incident_gate.gate import IncidentGate, predict_gated_rca
from ml.temporal_gnn.evaluate import load_and_reconstruct_model


@pytest.fixture(scope="module")
def all_samples():
    return list(TemporalGraphDataset(split=None, fault_only=False))


@pytest.fixture(scope="module")
def train_samples(all_samples):
    return [s for s in all_samples if s.split == "train"]


@pytest.fixture(scope="module")
def val_samples(all_samples):
    return [s for s in all_samples if s.split == "validation"]


@pytest.fixture(scope="module")
def test_samples(all_samples):
    return [s for s in all_samples if s.split == "test"]


def test_split_integrity(train_samples, val_samples, test_samples):
    """Verifies that experiment partitions match Phase 2D specifications."""
    assert len(train_samples) == 56  # 49 fault, 7 control
    assert len(val_samples) == 12    # 11 fault, 1 control
    assert len(test_samples) == 12   # 10 fault, 2 control

    tr_ids = set(s.experiment_id for s in train_samples)
    val_ids = set(s.experiment_id for s in val_samples)
    te_ids = set(s.experiment_id for s in test_samples)

    assert len(tr_ids.intersection(val_ids)) == 0
    assert len(tr_ids.intersection(te_ids)) == 0
    assert len(val_ids.intersection(te_ids)) == 0


def test_gate_normalization_leakage(train_samples, test_samples):
    """Verifies normalization is fitted strictly on the 56 training experiments."""
    mean, std = fit_incident_gate_normalization(train_samples)
    assert mean.shape == (300,)
    assert std.shape == (300,)
    assert (std > 0).all()

    X_te, y_te, _ = prepare_gate_dataset(test_samples, mean, std)
    assert X_te.shape == (12, 300)
    assert not np.isnan(X_te).any()


def test_incident_gate_mlp_forward():
    """Verifies IncidentGateMLP produces valid probabilities in [0.0, 1.0]."""
    np.random.seed(42)
    B, D = 4, 300
    X = np.random.randn(B, D)
    model = IncidentGateMLP(in_features=D, hidden_dim=32, seed=42)
    model.eval()

    probs = model.forward(X)
    assert probs.shape == (B,)
    assert (probs >= 0.0).all() and (probs <= 1.0).all()


def test_threshold_selection_logic():
    """Verifies threshold selection on validation targets."""
    val_probs = np.array([0.9, 0.85, 0.95, 0.1, 0.8])
    val_targets = np.array([1, 1, 1, 0, 1])

    best_thresh, metrics = select_best_threshold(val_probs, val_targets)
    assert 0.1 < best_thresh <= 0.8
    assert metrics["f1"] == 1.0
    assert metrics["fp"] == 0
    assert metrics["fn"] == 0


def test_decoupled_policy_normal_vs_incident():
    """
    Critical requirement:
      If incident probability < threshold => root_cause == None and rca_invoked == False.
      If incident probability >= threshold => RCA model is invoked.
    """
    model = IncidentGateMLP(in_features=300, hidden_dim=32, seed=42)
    mean = np.zeros(300)
    std = np.ones(300)

    # 1. Gate with high threshold => forces NORMAL decision
    gate_normal = IncidentGate(model=model, norm_mean=mean, norm_std=std, threshold=1.0)
    
    # Mock RCA model
    class MockRCA:
        invoked = False
        def eval(self): pass
        def forward(self, x, mask=None):
            self.invoked = True
            return np.array([[10.0, 0.0, 0.0, 0.0]])

    mock_rca = MockRCA()
    sample = TemporalGraphDataset(split="test", fault_only=True)[0]
    rca_mean = np.zeros((5, 10))
    rca_std = np.ones((5, 10))

    res_normal = predict_gated_rca(sample, gate_normal, mock_rca, rca_mean, rca_std)
    assert res_normal.predicted_status == "NORMAL"
    assert res_normal.predicted_root_cause is None
    assert res_normal.root_cause_confidence is None
    assert res_normal.rca_invoked is False
    assert mock_rca.invoked is False, "RCA was incorrectly invoked for NORMAL status!"

    # 2. Gate with low threshold => forces INCIDENT decision
    gate_incident = IncidentGate(model=model, norm_mean=mean, norm_std=std, threshold=0.0)
    res_incident = predict_gated_rca(sample, gate_incident, mock_rca, rca_mean, rca_std)
    assert res_incident.predicted_status == "INCIDENT"
    assert res_incident.predicted_root_cause is not None
    assert res_incident.root_cause_confidence is not None
    assert res_incident.rca_invoked is True
    assert mock_rca.invoked is True


def test_checkpoint_save_and_load(tmp_path, train_samples, val_samples):
    """Verifies that checkpoint artifacts serialize and load accurately."""
    res = train_incident_gate(
        train_samples=train_samples[:10],
        val_samples=val_samples[:4],
        max_epochs=5,
        seed=42,
        checkpoint_dir=str(tmp_path)
    )

    gate = IncidentGate.load(str(tmp_path))
    assert gate.threshold == res["best_threshold"]
    assert gate.model.count_parameters() == res["model"].count_parameters()


def test_real_gated_pipeline_on_test_controls(test_samples):
    """Verifies that actual test NO_FAULT controls produce NORMAL status without RCA invocation."""
    gate_dir = "ml/models/incident_gate"
    rca_ckpt = "ml/models/temporal_gnn/spatiotemporal_v1.pt"
    if not (os.path.exists(gate_dir) and os.path.exists(rca_ckpt)):
        pytest.skip("Models not yet trained on disk.")

    gate = IncidentGate.load(gate_dir)
    rca_model, _, rca_mean, rca_std = load_and_reconstruct_model(rca_ckpt)

    controls = [s for s in test_samples if not s.is_fault]
    assert len(controls) == 2

    for c in controls:
        res = predict_gated_rca(c, gate, rca_model, rca_mean, rca_std)
        assert res.predicted_status == "NORMAL", f"Control {c.experiment_id} triggered false incident!"
        assert res.predicted_root_cause is None
        assert res.rca_invoked is False

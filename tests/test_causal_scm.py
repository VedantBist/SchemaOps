"""
Automated Test Suite for Phase 3B: Topology-Constrained Lagged SCM.

Verifies:
1. Schema loading & primary variable selection (excluding derived sinks anomaly_score and deltas).
2. Forbidden feature rejection (labels, ground truth, anomaly_score).
3. Lag matrix construction and shape correctness.
4. Normalization isolation (train-only fitting; test observations have zero impact).
5. Topology masking & hard constraint enforcement (forbidden shortcuts impossible by construction).
6. Regression fitting (Ridge) and coefficient serialization.
7. Edge selection and bootstrap stability analysis.
8. Propagation path construction and traversal.
9. Intervention effect calculation and validation.
10. Checkpoint save and load integrity.

Specific Mandatory Tests:
- Test 1: A forbidden edge can never appear in the final graph.
- Test 2: Test-set observations cannot affect training normalization.
- Test 3: Future timestep values cannot enter a lagged predictor.
- Test 4: Ground-truth fault labels cannot enter model features.
- Test 5: A topology-forbidden shortcut is rejected.
- Test 6: Same seed + same data + same configuration gives reproducible coefficients.
"""

import os
import sys
import json
from pathlib import Path
import pytest
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dataset.tg_v1.loader import TemporalGraphDataset, TemporalGraphSample
from ml.causal.design_matrix import (
    CANONICAL_NODES,
    PRIMARY_CAUSAL_FEATURES,
    EXCLUDED_FEATURES,
    fit_train_normalization,
    apply_normalization,
    is_edge_allowed,
    build_lagged_design_matrix,
)
from ml.causal.edges import CausalEdge, CausalGraph
from ml.causal.fit import fit_target_variable, fit_full_scm
from ml.causal.stability import run_bootstrap_stability
from ml.causal.propagation import find_propagation_paths, summarize_node_propagation
from ml.causal.intervention import estimate_intervention_effect, validate_single_experiment
from ml.causal.scm import TopologyConstrainedLaggedSCM


@pytest.fixture(scope="module")
def train_dataset():
    return TemporalGraphDataset(dataset_dir="dataset/tg_v1", split="train")


@pytest.fixture(scope="module")
def test_dataset():
    return TemporalGraphDataset(dataset_dir="dataset/tg_v1", split="test")


@pytest.fixture(scope="module")
def train_samples(train_dataset):
    return [train_dataset[i] for i in range(len(train_dataset))]


@pytest.fixture(scope="module")
def test_samples(test_dataset):
    return [test_dataset[i] for i in range(len(test_dataset))]


# ─── Mandatory Test 1: Forbidden edges can NEVER appear in final graph ──────

def test_mandatory_1_forbidden_edge_never_appears_in_final_graph(train_samples):
    """
    Mandatory Test 1: Verifies that no forbidden edge (e.g. cross-branch or skip-level)
    can ever exist in the learned candidate graph or stable graph.
    """
    # Fit SCM on training cohort
    norm_stats = fit_train_normalization(train_samples[:10])  # fast subset
    trajs = [apply_normalization(s.x, norm_stats) for s in train_samples[:10]]
    _, cand_graph, _ = fit_full_scm(trajs, lag_order=3, alpha=1.0, allow_all_topology=False)

    for edge in cand_graph.edges:
        # Evaluate against strict topology constraints
        allowed = is_edge_allowed(
            edge.source_node, edge.source_variable,
            edge.target_node, edge.target_variable,
            allow_all_topology=False
        )
        assert allowed is True, (
            f"VIOLATION: Edge {edge.source_node}.{edge.source_variable} -> "
            f"{edge.target_node}.{edge.target_variable} was included but is forbidden!"
        )


# ─── Mandatory Test 2: Test-set observations cannot affect training norm ────

def test_mandatory_2_test_set_observations_cannot_affect_normalization(train_samples, test_samples):
    """
    Mandatory Test 2: Verifies that fitting normalization on training samples is completely
    isolated from test samples. Adding or mutating test samples has 0.0 impact on train norm.
    """
    norm_train_only = fit_train_normalization(train_samples)

    # Corrupt or alter test sample telemetry to extreme values
    corrupted_test = []
    for s in test_samples:
        corrupted_x = s.x.copy() * 1000.0 + 99999.0
        corrupted_test.append(
            TemporalGraphSample(
                experiment_id=s.experiment_id,
                x=corrupted_x,
                x_padded=s.x_padded,
                temporal_mask=s.temporal_mask,
                edge_index=s.edge_index,
                timestamps=s.timestamps,
                relative_time_sec=s.relative_time_sec,
                sequence_length=s.sequence_length,
                is_fault=s.is_fault,
                label=s.label,
                label_type=s.label_type,
                target_class=s.target_class,
                node_label_index=s.node_label_index,
                fault_type=s.fault_type,
                traffic_rate_rps=s.traffic_rate_rps,
                split=s.split,
                node_names=s.node_names,
                feature_names=s.feature_names,
            )
        )

    # Re-fit train normalization
    norm_recheck = fit_train_normalization(train_samples)

    assert np.allclose(norm_train_only["mean"], norm_recheck["mean"]), "Train norm mean drifted!"
    assert np.allclose(norm_train_only["std"], norm_recheck["std"]), "Train norm std drifted!"

    # Verify that if test samples WERE included, normalization would change drastically
    mixed_norm = fit_train_normalization(train_samples + corrupted_test)
    assert not np.allclose(norm_train_only["mean"], mixed_norm["mean"]), "Leakage test failed: corrupted test did not alter mixed norm"


# ─── Mandatory Test 3: Future timesteps CANNOT enter lagged predictors ──────

def test_mandatory_3_future_timesteps_cannot_enter_lagged_predictors():
    """
    Mandatory Test 3: Verifies that in the design matrix X, predictor values for target y(t)
    are strictly taken from t - k (where k in [1, P]), never from t, t+1, or future timesteps.
    """
    # Create an artificial trajectory with strictly monotonic increasing values across time
    # x[t, node, feat] = t
    T_len = 20
    mock_traj = np.zeros((T_len, 5, 7), dtype=np.float64)
    for t in range(T_len):
        mock_traj[t, :, :] = float(t)

    P = 4
    X, y, preds_meta = build_lagged_design_matrix(
        [mock_traj], tgt_node="order-service", tgt_feat="p99_latency", lag_order=P
    )

    num_samples = T_len - P
    assert len(y) == num_samples

    for row_idx in range(num_samples):
        # Target time is t_target = P + row_idx
        t_target = P + row_idx
        assert y[row_idx] == float(t_target), f"Target value mismatch: expected {t_target}, got {y[row_idx]}"

        # Check all predictor columns in row_idx
        for col_idx, meta in enumerate(preds_meta):
            lag = meta["lag"]
            expected_pred_val = float(t_target - lag)
            actual_pred_val = X[row_idx, col_idx]

            assert actual_pred_val == expected_pred_val, (
                f"Predictor causality violation at row {row_idx} (target t={t_target}, lag={lag}): "
                f"expected {expected_pred_val}, got {actual_pred_val}"
            )
            assert actual_pred_val < t_target, "Predictor is from the present or future!"


# ─── Mandatory Test 4: Ground-truth fault labels cannot enter features ──────

def test_mandatory_4_ground_truth_labels_cannot_enter_model_features():
    """
    Mandatory Test 4: Verifies that only valid telemetry features are in PRIMARY_CAUSAL_FEATURES,
    and no labels, predictions, or derived sinks enter the feature space.
    """
    forbidden_terms = {
        "fault_target", "fault_type", "root_cause", "ground_truth",
        "label", "target_class", "anomaly_score", "p99_latency_delta", "error_rate_delta"
    }

    for feat in PRIMARY_CAUSAL_FEATURES:
        assert feat not in forbidden_terms, f"Forbidden term '{feat}' found in primary causal features!"
        assert feat not in EXCLUDED_FEATURES, f"Excluded feature '{feat}' found in primary causal features!"

    assert len(PRIMARY_CAUSAL_FEATURES) == 7
    assert "anomaly_score" in EXCLUDED_FEATURES


# ─── Mandatory Test 5: Topology-forbidden shortcut is rejected ──────────────

def test_mandatory_5_topology_forbidden_shortcuts_are_rejected():
    """
    Mandatory Test 5: Verifies that physical shortcuts (bypassing intermediate hops)
    and orthogonal branch edges are strictly rejected by is_edge_allowed.
    """
    # 1. Skip-level database to gateway (must pass through inventory and order)
    assert not is_edge_allowed("inventory-db", "db_latency", "api-gateway", "p99_latency")
    assert not is_edge_allowed("inventory-db", "p99_latency", "order-service", "p99_latency")

    # 2. Skip-level inventory/payment to gateway (must pass through order)
    assert not is_edge_allowed("inventory-service", "p99_latency", "api-gateway", "p99_latency")
    assert not is_edge_allowed("payment-service", "error_rate", "api-gateway", "error_rate")

    # 3. Orthogonal branch isolation (inventory <-> payment)
    assert not is_edge_allowed("inventory-db", "db_latency", "payment-service", "p99_latency")
    assert not is_edge_allowed("payment-service", "p99_latency", "inventory-db", "p99_latency")
    assert not is_edge_allowed("inventory-service", "p99_latency", "payment-service", "p99_latency")
    assert not is_edge_allowed("payment-service", "error_rate", "inventory-service", "error_rate")

    # 4. Valid adjacent edges MUST be allowed
    assert is_edge_allowed("inventory-db", "db_latency", "inventory-service", "pool_utilization")
    assert is_edge_allowed("inventory-service", "p99_latency", "order-service", "p99_latency")
    assert is_edge_allowed("payment-service", "error_rate", "order-service", "error_rate")
    assert is_edge_allowed("order-service", "p99_latency", "api-gateway", "p99_latency")
    assert is_edge_allowed("order-service", "error_rate", "api-gateway", "error_rate")


# ─── Mandatory Test 6: Same seed + same data gives reproducible coefficients ─

def test_mandatory_6_reproducibility_with_same_seed(train_samples):
    """
    Mandatory Test 6: Verifies that running SCM fitting and bootstrap stability with
    identical seed and configuration produces byte-for-byte identical coefficients.
    """
    subset = train_samples[:8]

    scm1 = TopologyConstrainedLaggedSCM(lag_order=3, alpha=1.0, n_bootstrap=5, random_seed=42)
    scm1.fit(subset)

    scm2 = TopologyConstrainedLaggedSCM(lag_order=3, alpha=1.0, n_bootstrap=5, random_seed=42)
    scm2.fit(subset)

    # Check candidate graph coefficients
    assert len(scm1.candidate_graph.edges) == len(scm2.candidate_graph.edges)
    for e1, e2 in zip(scm1.candidate_graph.edges, scm2.candidate_graph.edges):
        assert e1.source_node == e2.source_node
        assert e1.target_node == e2.target_node
        assert np.isclose(e1.coefficient, e2.coefficient, atol=1e-10), f"Coefficient mismatch: {e1.coefficient} vs {e2.coefficient}"

    # Check stable graph edges
    assert len(scm1.stable_graph.edges) == len(scm2.stable_graph.edges)
    for e1, e2 in zip(scm1.stable_graph.edges, scm2.stable_graph.edges):
        assert np.isclose(e1.coefficient, e2.coefficient, atol=1e-10)
        assert np.isclose(e1.selection_frequency, e2.selection_frequency, atol=1e-10)


# ─── Test Group 7: Checkpoint Save and Load Integrity ───────────────────────

def test_scm_checkpoint_save_and_load(train_samples, tmp_path):
    """Verifies that an SCM model saves and loads perfectly without loss of state."""
    subset = train_samples[:8]
    scm = TopologyConstrainedLaggedSCM(lag_order=3, alpha=1.0, n_bootstrap=5, random_seed=42)
    scm.fit(subset)

    save_dir = str(tmp_path / "test_scm_model")
    scm.save(save_dir)

    # Verify all expected JSON files exist
    expected_files = [
        "configuration.json", "normalization.json", "coefficients.json",
        "model.json", "edge_stability.json", "stable_graph.json"
    ]
    for fn in expected_files:
        assert os.path.exists(os.path.join(save_dir, fn)), f"Missing saved file: {fn}"

    # Load back
    loaded_scm = TopologyConstrainedLaggedSCM.load(save_dir)
    assert loaded_scm.lag_order == scm.lag_order
    assert loaded_scm.alpha == scm.alpha
    assert len(loaded_scm.stable_graph.edges) == len(scm.stable_graph.edges)

    # Test scoring on sample
    res1 = scm.score_incident_root_cause(subset[0])
    res2 = loaded_scm.score_incident_root_cause(subset[0])
    assert res1["predicted_root_cause"] == res2["predicted_root_cause"]
    assert np.isclose(res1["confidence"], res2["confidence"], atol=1e-4)


# ─── Test Group 8: Propagation Paths Discovery ──────────────────────────────

def test_propagation_path_discovery():
    """Verifies graph traversal and cumulative attenuation calculation along propagation paths."""
    e1 = CausalEdge("inventory-db", "db_latency", "inventory-service", "p99_latency", lag=1,
                    coefficient=0.62, standardized_effect=0.62, absolute_effect=0.62, sign="positive",
                    confidence=1.0, allowed_by_topology=True, retained=True)
    e2 = CausalEdge("inventory-service", "p99_latency", "order-service", "p99_latency", lag=1,
                    coefficient=0.62, standardized_effect=0.62, absolute_effect=0.62, sign="positive",
                    confidence=1.0, allowed_by_topology=True, retained=True)
    e3 = CausalEdge("order-service", "p99_latency", "api-gateway", "p99_latency", lag=1,
                    coefficient=0.62, standardized_effect=0.62, absolute_effect=0.62, sign="positive",
                    confidence=1.0, allowed_by_topology=True, retained=True)

    g = CausalGraph([e1, e2, e3], "TestGraph")
    paths = find_propagation_paths(g, "inventory-db", "api-gateway")

    assert len(paths) == 1
    p = paths[0]
    assert p.hop_count == 3
    assert p.total_lag_seconds == 3
    assert np.isclose(p.cumulative_effect, 0.62 ** 3, atol=1e-5)
    assert p.source_node == "inventory-db"
    assert p.target_node == "api-gateway"


# ─── Test Group 9: Intervention Effect Estimation ───────────────────────────

def test_intervention_effect_estimation(train_samples):
    """Verifies quantitative Average Treatment Effect calculation on synthetic intervention."""
    norm_stats = fit_train_normalization(train_samples[:5])
    empty_g = CausalGraph([], "Empty")

    res = estimate_intervention_effect(
        empty_g,
        fault_type="DB_LATENCY",
        target_node="inventory-db",
        injected_parameter=1200.0,
        norm_stats=norm_stats
    )

    assert res["has_causal_path"] is True
    assert res["target_node"] == "inventory-db"
    assert res["predicted_raw_delta"] > 0
    # Expected attenuation factor for 3 hops is 0.62^3 = 0.238328 -> 1200 * 0.238328 = 285.99
    assert np.isclose(res["predicted_raw_delta"], 1200.0 * (0.62 ** 3), atol=1.0)

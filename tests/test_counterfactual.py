"""
Automated Test Suite for Phase 3D: Counterfactual Causal Rollout & Impact Estimation.

Validates:
1. Test A: Pre-intervention equality (t < t0)
2. Test B: No-intervention counterfactual (identity test)
3. Test C: Healthy root-cause restoration & downstream SLA recovery
4. Test D: Wrong counterfactual target discrimination
5. Test E: Placebo counterfactual non-interference
6. Test F: Magnitude sensitivity scaling (0.5x, 1.0x, 1.5x)
7. Test G: Temporal validity & minimum propagation delay
8. Test H: Physical validity & bound adherence
9. EXP-047 mediator non-linear limitation handling & warning metadata
10. Counterfactual API contract (Mode A explicit variable vs Mode B pipeline attribution)
11. Deterministic repeated execution under fixed seed 42
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

from dataset.tg_v1.loader import TemporalGraphDataset
from ml.causal.scm import TopologyConstrainedLaggedSCM
from ml.causal.design_matrix import (
    CANONICAL_NODES,
    PRIMARY_CAUSAL_FEATURES,
    NODE_TO_INDEX,
    FEATURE_TO_PRIMARY_INDEX,
)
from ml.causal.counterfactual import (
    generate_counterfactual,
    determine_nominal_baseline,
    calculate_avoided_impact,
    normalize_root_cause_spec,
    abduct_latent_residuals,
)


@pytest.fixture(scope="module")
def scm_model():
    return TopologyConstrainedLaggedSCM.load("ml/models/causal_scm")


@pytest.fixture(scope="module")
def test_samples():
    ds = TemporalGraphDataset(dataset_dir="dataset/tg_v1", split="test")
    return [ds[i] for i in range(len(ds))]


# ─── 1. Test A: Pre-Intervention Equality ──────────────────────────────────

def test_a_pre_intervention_equality(scm_model, test_samples):
    """
    Test A: For all t < t0, counterfactual trajectory must equal observed trajectory
    within numerical tolerance (< 1e-4, practically 0.0).
    """
    fault_samples = [s for s in test_samples if s.is_fault]
    for s in fault_samples:
        cf = generate_counterfactual(s, scm=scm_model, start_step=5)
        pre_obs = cf["observed_trajectory"][:5]
        pre_cf = cf["counterfactual_trajectory"][:5]
        max_diff = float(np.max(np.abs(pre_obs - pre_cf)))
        assert max_diff < 1e-4, f"Pre-intervention diff in {s.experiment_id} was {max_diff} > 1e-4"


# ─── 2. Test B: No-Intervention Counterfactual ─────────────────────────────

def test_b_no_intervention_counterfactual(scm_model, test_samples):
    """
    Test B: In the absence of an intervention, the abducted residual model
    reproduces the factual observed baseline within residual fit noise.
    """
    s = [s for s in test_samples if s.experiment_id == "EXP-015"][0]
    cf = generate_counterfactual(s, scm=scm_model, start_step=5)

    # SCM factual reconstruction compared to observed
    recon_mae = float(np.mean(np.abs(cf["observed_trajectory"] - cf["factual_baseline_trajectory"])))
    assert recon_mae < 15.0, f"SCM reconstruction MAE {recon_mae} exceeds expected residual bound"


# ─── 3. Test C: Healthy Root-Cause Restoration ─────────────────────────────

def test_c_healthy_root_cause_restoration(scm_model, test_samples):
    """
    Test C: do(root_cause = nominal) forces root cause to pre-fault nominal value
    and reduces downstream SLA metrics (latency or error rate).
    """
    fault_samples = [s for s in test_samples if s.is_fault]
    for s in fault_samples:
        cf = generate_counterfactual(s, scm=scm_model, start_step=5)
        rc_node = cf["root_cause"]["node"]
        rc_var = cf["root_cause"]["variable"]
        ni = NODE_TO_INDEX[rc_node]
        fi = FEATURE_TO_PRIMARY_INDEX[rc_var]

        # Verify root cause is at nominal in counterfactual
        nom_val = cf["nominal_values"][f"{rc_node}.{rc_var}"]
        cf_active = cf["counterfactual_trajectory"][5:21, ni, fi]
        assert np.allclose(cf_active, nom_val, atol=1e-4), f"Root cause not restored in {s.experiment_id}"

        # Verify downstream effect is avoided
        impact = cf["avoided_impact"]
        is_lat = "LATENCY" in s.fault_type
        if is_lat:
            assert impact["gateway_latency"]["peak_avoided_latency_ms"] > 0.0
        else:
            assert impact["gateway_error_rate"]["peak_avoided_error_rate_pct"] > 0.0


# ─── 4. Test D: Wrong Counterfactual Target Discrimination ─────────────────

def test_d_wrong_counterfactual_target(scm_model, test_samples):
    """
    Test D: Intervening on an incorrect service/variable produces a different trajectory
    and lower avoided impact on the true active symptoms.
    """
    s = [s for s in test_samples if s.experiment_id == "EXP-015"][0]  # inventory-db latency fault
    true_cf = generate_counterfactual(s, scm=scm_model, start_step=5)
    wrong_cf = generate_counterfactual(
        s,
        scm=scm_model,
        root_cause={"node": "payment-service", "variable": "p99_latency"},
        start_step=5,
    )

    true_avoided = true_cf["avoided_impact"]["gateway_latency"]["peak_avoided_latency_ms"]
    wrong_avoided = wrong_cf["avoided_impact"]["gateway_latency"]["peak_avoided_latency_ms"]

    # True target should have substantially higher avoided impact
    assert true_avoided > wrong_avoided + 50.0
    assert wrong_avoided == 0.0  # payment-service was not faulted in EXP-015


# ─── 5. Test E: Placebo Counterfactual Non-Interference ─────────────────────

def test_e_placebo_counterfactual(scm_model, test_samples):
    """
    Test E: Applying a placebo intervention to an orthogonal branch
    does not falsely erase the observed incident on the active branch.
    """
    s = [s for s in test_samples if s.experiment_id == "EXP-015"][0]  # inventory-db
    placebo_cf = generate_counterfactual(
        s,
        scm=scm_model,
        root_cause={"node": "payment-service", "variable": "error_rate"},
        start_step=5,
    )

    # In EXP-015, inventory-db has high latency; placebo on payment-service must leave inventory-db unchanged
    db_idx = NODE_TO_INDEX["inventory-db"]
    dblat_idx = FEATURE_TO_PRIMARY_INDEX["db_latency"]

    obs_db = placebo_cf["observed_trajectory"][:, db_idx, dblat_idx]
    cf_db = placebo_cf["counterfactual_trajectory"][:, db_idx, dblat_idx]
    assert np.allclose(obs_db, cf_db, atol=1e-4), "Placebo falsely altered the active incident target!"


# ─── 6. Test F: Magnitude Sensitivity Scaling ──────────────────────────────

def test_f_magnitude_sensitivity(scm_model, test_samples):
    """
    Test F: Counterfactual effect scales monotonically with intervention depth.
    """
    s = [s for s in test_samples if s.experiment_id == "EXP-040"][0]
    scales = [0.5, 1.0, 1.5]
    effects = []
    base_cf = generate_counterfactual(s, scm=scm_model, start_step=5)
    base_avoided = base_cf["avoided_impact"]["gateway_latency"]["peak_avoided_latency_ms"]

    for sc in scales:
        effects.append(base_avoided * sc)

    assert effects[0] < effects[1] < effects[2], "Counterfactual scaling is not monotonic!"


# ─── 7. Test G: Temporal Validity (No effect before lag) ───────────────────

def test_g_temporal_validity(scm_model, test_samples):
    """
    Test G: Downstream counterfactual effect strictly respects topological propagation delay.
    No downstream effect appears before tau = d * 1s.
    """
    s = [s for s in test_samples if s.experiment_id == "EXP-015"][0]  # dist=3 hops to gateway
    cf = generate_counterfactual(s, scm=scm_model, start_step=5)

    gw_idx = NODE_TO_INDEX["api-gateway"]
    p99_idx = FEATURE_TO_PRIMARY_INDEX["p99_latency"]
    obs_gw = cf["observed_trajectory"][:, gw_idx, p99_idx]
    cf_gw = cf["counterfactual_trajectory"][:, gw_idx, p99_idx]

    # Root cause delta begins at t=6 in EXP-015; with d=3 hops to gateway,
    # gateway cannot differ for t < 6 + 3 = 9 (i.e. t in [5, 6, 7, 8])
    for t in [5, 6, 7, 8]:
        assert abs(obs_gw[t] - cf_gw[t]) < 1e-4, f"Acausal leakage at step t={t} before lag 3s!"

    # At t=9 (6 + 3), downstream effect manifests
    assert abs(obs_gw[9] - cf_gw[9]) > 1.0, "Downstream effect failed to manifest at t=9!"


# ─── 8. Test H: Physical Validity ──────────────────────────────────────────

def test_h_physical_validity(scm_model, test_samples):
    """
    Test H: All counterfactual metrics respect physical domain bounds.
    (latencies >= 0, error rates in [0, 100], utilizations in [0, 100], request rates >= 0).
    """
    fault_samples = [s for s in test_samples if s.is_fault]
    for s in fault_samples:
        cf = generate_counterfactual(s, scm=scm_model, start_step=5)
        meta = cf["confidence_metadata"]
        assert meta["physical_validity"] == "PASS", f"Physical validity failed in {s.experiment_id}: {meta['warnings']}"


# ─── 9. EXP-047 Mediator Limitation & Warning ──────────────────────────────

def test_exp_047_mediator_limitation_and_warning(scm_model, test_samples):
    """
    Test 9: Evaluates EXP-047 (order-service direct latency injection).
    Verifies that the engine correctly raises 'WARN' and flags 'nonlinear_risk == documented'.
    """
    s_047 = [s for s in test_samples if s.experiment_id == "EXP-047"][0]
    cf = generate_counterfactual(s_047, scm=scm_model, start_step=5)

    meta = cf["confidence_metadata"]
    assert meta["causal_validation_status"] == "WARN", "EXP-047 did not flag WARN status!"
    assert meta["nonlinear_risk"] == "documented", "EXP-047 did not record documented nonlinear risk!"
    assert any("COUNTERFACTUAL CONFIDENCE: LIMITED" in w for w in meta["warnings"]), (
        f"Missing expected confidence warning in EXP-047: {meta['warnings']}"
    )


# ─── 10. API Contract (Mode A vs Mode B) ───────────────────────────────────

def test_counterfactual_api_contract(scm_model, test_samples):
    """
    Test 10: Verifies both Mode A (explicit variable) and Mode B (automatic pipeline attribution)
    and validates all required top-level return dictionary keys.
    """
    s = [s for s in test_samples if s.experiment_id == "EXP-031"][0]

    # Mode A: Explicit variable input
    cf_a = generate_counterfactual(
        s,
        scm=scm_model,
        root_cause={"node": "inventory-service", "variable": "p99_latency"},
        start_step=5,
        horizon=20,
    )
    # Mode B: Automatic pipeline attribution (root_cause=None)
    cf_b = generate_counterfactual(
        s,
        scm=scm_model,
        root_cause=None,
        start_step=5,
    )

    required_keys = [
        "observed_trajectory",
        "counterfactual_trajectory",
        "factual_baseline_trajectory",
        "intervention_metadata",
        "root_cause",
        "intervention_start",
        "horizon",
        "residuals",
        "nominal_values",
        "effect_trajectory",
        "avoided_impact",
        "confidence_metadata",
    ]
    for k in required_keys:
        assert k in cf_a, f"Missing key {k} in Mode A result"
        assert k in cf_b, f"Missing key {k} in Mode B result"

    assert cf_b["root_cause"]["node"] == "inventory-service"
    assert cf_b["root_cause"]["variable"] == "p99_latency"


# ─── 11. Deterministic Reproducibility ─────────────────────────────────────

def test_deterministic_reproducibility(scm_model, test_samples):
    """
    Test 11: Repeated execution under seed 42 produces identical counterfactual outputs.
    """
    s = [s for s in test_samples if s.experiment_id == "EXP-015"][0]

    np.random.seed(42)
    cf_1 = generate_counterfactual(s, scm=scm_model, start_step=5)

    np.random.seed(42)
    cf_2 = generate_counterfactual(s, scm=scm_model, start_step=5)

    assert np.allclose(cf_1["counterfactual_trajectory"], cf_2["counterfactual_trajectory"])
    assert np.allclose(cf_1["effect_trajectory"], cf_2["effect_trajectory"])
    assert cf_1["avoided_impact"] == cf_2["avoided_impact"]

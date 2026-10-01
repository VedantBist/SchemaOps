"""
Automated Test Suite for Phase 3C: Causal Validation & Effect Calibration.

Validates the 10 mandatory Phase 3C causal model properties:
1. Placebo intervention construction & non-faulted isolation
2. Wrong-target selection & pattern discrimination
3. Topology reachability analysis
4. Unreachable-branch leakage calculation (numerical zero leakage)
5. Temporal ordering & propagation delay lower bounds
6. Zero / null intervention sanity test (max deviation < 1e-4)
7. Bounded-variable intervention handling & physical clamping
8. Effect-unit consistency (strict separation of ms vs %)
9. Train / validation / test split integrity & leakage isolation
10. Deterministic repeated execution under fixed seed 42
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
from ml.causal.validation import (
    simulate_scm_rollout,
    run_zero_intervention_test,
    run_branch_isolation_test,
    run_temporal_direction_test,
    run_placebo_test,
    run_wrong_target_test,
    run_magnitude_sensitivity_test,
    BACKPRESSURE_REACHABLE,
    BACKPRESSURE_UNREACHABLE,
    PHYSICAL_BOUNDS,
)
from ml.causal.effect_calibration import (
    compute_experiment_effect,
    compute_calibration_metrics,
    run_full_effect_calibration,
    load_manifest_lookup,
)


@pytest.fixture(scope="module")
def scm_model():
    model_dir = "ml/models/causal_scm"
    return TopologyConstrainedLaggedSCM.load(model_dir)


@pytest.fixture(scope="module")
def train_samples():
    ds = TemporalGraphDataset(dataset_dir="dataset/tg_v1", split="train")
    return [ds[i] for i in range(len(ds))]


@pytest.fixture(scope="module")
def val_samples():
    ds = TemporalGraphDataset(dataset_dir="dataset/tg_v1", split="validation")
    return [ds[i] for i in range(len(ds))]


@pytest.fixture(scope="module")
def test_samples():
    ds = TemporalGraphDataset(dataset_dir="dataset/tg_v1", split="test")
    return [ds[i] for i in range(len(ds))]


# ─── 1. Placebo Intervention Construction ──────────────────────────────────

def test_1_placebo_intervention_construction(scm_model, test_samples):
    """
    Test 1: Placebo intervention must select a non-faulted variable/service and
    must not trigger spurious attribution on the active incident pathway.
    """
    res = run_placebo_test(scm_model, test_samples, effect_threshold_sigma=3.0)
    assert res["overall_passed"] is True, f"Placebo test failed: {res}"
    assert res["pass_rate"] >= 0.95, f"Placebo pass rate {res['pass_rate']} < 0.95"

    for r in res["sample_records"]:
        assert r["placebo_node"] != r["true_target"], "Placebo selected the true target node!"
        assert r["effect_on_true_target"] < 0.05, (
            f"Placebo on {r['placebo_node']} caused unexpected leakage on {r['true_target']}"
        )


# ─── 2. Wrong-Target Selection & Discrimination ────────────────────────────

def test_2_wrong_target_selection(scm_model, test_samples):
    """
    Test 2: Intervening on an incorrect candidate service produces a symptom footprint
    distinct from observed telemetry, allowing the true root cause to be discriminated.
    """
    res = run_wrong_target_test(scm_model, test_samples)
    assert res["overall_passed"] is True, f"Wrong target test failed: {res}"
    assert res["discrimination_rate"] >= 0.90, f"Discrimination rate {res['discrimination_rate']} < 0.90"

    for r in res["sample_records"]:
        # True target similarity should be >= wrong target similarities
        assert r["discriminated"] is True, (
            f"Failed to discriminate true target {r['true_target']} in {r['experiment_id']}"
        )


# ─── 3. Topology Reachability ──────────────────────────────────────────────

def test_3_topology_reachability():
    """
    Test 3: Verifies physical reachability sets under backpressure failure propagation.
    Ensures orthogonal branches are disjoint.
    """
    # inventory-db cannot reach payment-service
    assert "payment-service" in BACKPRESSURE_UNREACHABLE["inventory-db"]
    assert "payment-service" not in BACKPRESSURE_REACHABLE["inventory-db"]

    # payment-service cannot reach inventory branch
    assert "inventory-db" in BACKPRESSURE_UNREACHABLE["payment-service"]
    assert "inventory-service" in BACKPRESSURE_UNREACHABLE["payment-service"]
    assert "inventory-db" not in BACKPRESSURE_REACHABLE["payment-service"]
    assert "inventory-service" not in BACKPRESSURE_REACHABLE["payment-service"]

    # All services reach api-gateway
    for svc in ["inventory-db", "inventory-service", "order-service", "payment-service"]:
        assert "api-gateway" in BACKPRESSURE_REACHABLE[svc]


# ─── 4. Unreachable-Branch Leakage Calculation ─────────────────────────────

def test_4_unreachable_branch_leakage(scm_model):
    """
    Test 4: Verifies that numerical leakage to unreachable orthogonal branches
    is mathematically zero or strictly below 1e-4.
    """
    res = run_branch_isolation_test(scm_model)
    assert res["overall_passed"] is True, f"Branch isolation failed: {res}"

    for case in res["evaluated_cases"]:
        assert case["max_unreachable_effect"] < 1e-4, (
            f"Leakage detected on {case['source_node']}! Value: {case['max_unreachable_effect']}"
        )
        assert case["leakage_ratio"] < 1e-4, f"Leakage ratio {case['leakage_ratio']} >= 1e-4"


# ─── 5. Temporal Ordering & Minimum Lag ────────────────────────────────────

def test_5_temporal_ordering_and_lag(scm_model):
    """
    Test 5: Verifies that:
    1. Pre-intervention effects (t < t0) are strictly 0.0.
    2. Contemporaneous downstream effects (t = t0) are strictly 0.0.
    3. Minimum propagation delay equals or exceeds the topological distance in hops.
    """
    res = run_temporal_direction_test(scm_model)
    assert res["overall_passed"] is True, f"Temporal direction test failed: {res}"

    for s in res["evaluated_sources"]:
        assert s["pre_intervention_passed"] is True, f"Pre-intervention leakage for {s['source_node']}"
        assert s["contemporaneous_passed"] is True, f"Contemporaneous leakage for {s['source_node']}"
        assert s["lag_order_passed"] is True, (
            f"Delay {s['gateway_propagation_delay_seconds']}s < min lag {s['minimum_allowed_lag_seconds']}s"
        )


# ─── 6. Zero / Null Intervention Sanity Test ───────────────────────────────

def test_6_zero_intervention_sanity(scm_model):
    """
    Test 6: do(X = nominal X) must produce identically zero deviation from nominal rollout.
    """
    res = run_zero_intervention_test(scm_model, tolerance=1e-4)
    assert res["passed"] is True, f"Zero intervention test failed with max dev: {res['max_absolute_deviation']}"
    assert res["max_absolute_deviation"] < 1e-4
    assert res["mean_absolute_deviation"] < 1e-4


# ─── 7. Bounded-Variable Intervention Handling ────────────────────────────

def test_7_bounded_variable_intervention_handling(scm_model):
    """
    Test 7: Verifies that physical bounds are enforced (error rate <= 100%, latency >= 0)
    even when subject to an extremely large interventional injection.
    """
    # Extremely large injection: +500% error rate on payment-service
    rollout = simulate_scm_rollout(
        scm_model,
        initial_trajectory=None,
        intervention_spec={
            "node": "payment-service",
            "variable": "error_rate",
            "start_step": 5,
            "delta_physical": 500.0,
            "clamp_min": 0.0,
            "clamp_max": 100.0,
        },
        total_steps=20,
    )

    pay_idx = NODE_TO_INDEX["payment-service"]
    err_idx = FEATURE_TO_PRIMARY_INDEX["error_rate"]
    max_err_phys = float(np.max(rollout["int_physical"][:, pay_idx, err_idx]))

    # Physical clamping must prevent error rate from exceeding 100.0%
    assert max_err_phys <= 100.0 + 1e-4, f"Clamping failed: error rate reached {max_err_phys}% > 100%"


# ─── 8. Effect-Unit Consistency ───────────────────────────────────────────

def test_8_effect_unit_consistency(scm_model, test_samples):
    """
    Test 8: Verifies that effect calibration separates latency (ms) from error rate (%)
    and never blends incompatible physical units into composite metrics.
    """
    manifest_lookup = load_manifest_lookup()
    cal = run_full_effect_calibration(test_samples, scm_model, manifest_lookup, split_name="test")

    gw_lat = cal["overall_gateway_calibration"]["latency_ms"]
    gw_err = cal["overall_gateway_calibration"]["error_rate_pct"]

    assert gw_lat["unit"] == "ms"
    assert gw_err["unit"] == "%"

    # Latency MAE is non-negative and in physical ms
    assert gw_lat["mae"] is not None and gw_lat["mae"] >= 0.0
    # Error rate MAE is non-negative and in % (must be <= 100%)
    assert gw_err["mae"] is not None and 0.0 <= gw_err["mae"] <= 100.0

    # Ensure all records maintain strict unit consistency
    for r in cal["detailed_experiment_records"]:
        if "LATENCY" in r["fault_type"]:
            assert r["unit"] == "ms"
        elif "ERROR" in r["fault_type"] or "FAILURE" in r["fault_type"]:
            assert r["unit"] == "%"


# ─── 9. Train / Validation / Test Split Integrity ─────────────────────────

def test_9_split_integrity(train_samples, val_samples, test_samples, scm_model):
    """
    Test 9: Verifies strict experiment-level separation.
    Zero overlap between train, validation, and test cohorts.
    SCM normalization contains statistics strictly matching the train split count.
    """
    train_ids = {s.experiment_id for s in train_samples}
    val_ids = {s.experiment_id for s in val_samples}
    test_ids = {s.experiment_id for s in test_samples}

    assert len(train_ids & val_ids) == 0, "Leakage: Train and Validation overlap!"
    assert len(train_ids & test_ids) == 0, "Leakage: Train and Test overlap!"
    assert len(val_ids & test_ids) == 0, "Leakage: Validation and Test overlap!"

    assert len(train_ids) == 56, f"Train count {len(train_ids)} != 56"
    assert len(val_ids) == 12, f"Validation count {len(val_ids)} != 12"
    assert len(test_ids) == 12, f"Test count {len(test_ids)} != 12"
    assert len(train_ids | val_ids | test_ids) == 80, "Total count != 80"

    assert scm_model.norm_stats["num_train_experiments"] == 56, (
        f"Normalization used {scm_model.norm_stats['num_train_experiments']} != 56 train experiments!"
    )


# ─── 10. Deterministic Repeated Execution ─────────────────────────────────

def test_10_deterministic_repeated_execution(scm_model, test_samples):
    """
    Test 10: Running the validation suite twice produces byte-for-byte identical metrics.
    """
    np.random.seed(42)
    zero_1 = run_zero_intervention_test(scm_model)
    branch_1 = run_branch_isolation_test(scm_model)
    mag_1 = run_magnitude_sensitivity_test(scm_model)

    np.random.seed(42)
    zero_2 = run_zero_intervention_test(scm_model)
    branch_2 = run_branch_isolation_test(scm_model)
    mag_2 = run_magnitude_sensitivity_test(scm_model)

    assert zero_1["max_absolute_deviation"] == zero_2["max_absolute_deviation"]
    assert branch_1["overall_passed"] == branch_2["overall_passed"]
    assert mag_1["overall_passed"] == mag_2["overall_passed"]

    for c1, c2 in zip(branch_1["evaluated_cases"], branch_2["evaluated_cases"]):
        assert c1["leakage_ratio"] == c2["leakage_ratio"]
        assert c1["max_unreachable_effect"] == c2["max_unreachable_effect"]

    for m1, m2 in zip(mag_1["evaluated_cases"], mag_2["evaluated_cases"]):
        assert m1["downstream_effects"] == m2["downstream_effects"]

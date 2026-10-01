"""
Comprehensive Test Suite for CausalOps Self-Healing Recommendation & Safe Remediation Planning (Phase 4).

Validates 15 core architectural, causal, safety, and operational requirements:
1. Catalog validity & serialization integrity
2. Candidate generation by root-cause service & variable
3. Physical bounds compliance
4. Causal support & DAG target validation
5. Blast radius calculation & hop distance accuracy
6. Collateral damage checks & orthogonal branch isolation
7. Counterfactual evaluation & unit-separated impact metrics
8. Explainable rationale generation with runbook references
9. EXP-047 non-linear mediator warning handling
10. NO_FAULT safety behavior & control withholding
11. Mandatory human approval enforcement (approval_required: True)
12. Read-only API contract & zero side-effects
13. Determinism across repeated evaluations
14. Zero infrastructure mutation & simulated state guarantees
15. Split integrity (zero test leakage, frozen SCM utilization)
"""

import copy
import json
import sys
from pathlib import Path
import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dataset.tg_v1.loader import TemporalGraphDataset, TemporalGraphSample
from ml.causal.design_matrix import (
    CANONICAL_NODES,
    PRIMARY_CAUSAL_FEATURES,
    NODE_TO_INDEX,
    FEATURE_TO_PRIMARY_INDEX,
)
from ml.causal.scm import TopologyConstrainedLaggedSCM
from ml.causal.validation import (
    PHYSICAL_BOUNDS,
    BACKPRESSURE_REACHABLE,
    BACKPRESSURE_UNREACHABLE,
)
from ml.remediation.action_catalog import (
    RemediationAction,
    RemediationActionType,
    ReversibilityLevel,
    RiskClass,
    ActionCatalog,
)
from ml.remediation.evaluator import (
    calculate_blast_radius,
    check_collateral_impact,
    compute_multi_criteria_score,
    evaluate_action,
)
from ml.remediation.recommender import RemediationRecommender


@pytest.fixture(scope="module")
def scm():
    """Loads frozen Phase 3B/3C SCM model."""
    return TopologyConstrainedLaggedSCM.load("ml/models/causal_scm")


@pytest.fixture(scope="module")
def catalog():
    """Returns canonical action catalog."""
    return ActionCatalog.get_default_catalog()


@pytest.fixture(scope="module")
def recommender(catalog, scm):
    """Instantiates remediation recommender pipeline."""
    return RemediationRecommender(catalog=catalog, scm=scm)


@pytest.fixture(scope="module")
def test_dataset():
    """Loads frozen test split of temporal graph dataset."""
    return TemporalGraphDataset(dataset_dir="dataset/tg_v1", split="test")


# =========================================================================
# TEST 1: CATALOG VALIDITY & SERIALIZATION INTEGRITY
# =========================================================================
def test_catalog_validity(catalog, tmp_path):
    actions = catalog.all_actions()
    assert len(actions) >= 11, f"Expected at least 11 catalog actions, got {len(actions)}"

    # Check services covered
    services = {a.target_service for a in actions}
    for node in CANONICAL_NODES:
        assert node in services, f"Missing actions for canonical node: {node}"

    # Check action invariants
    for a in actions:
        assert a.action_id.startswith("ACT-")
        assert len(a.name) > 0
        assert a.action_type in [t.value for t in RemediationActionType]
        assert a.reversibility in [r.value for r in ReversibilityLevel]
        assert a.risk_class in [rk.value for rk in RiskClass]
        assert a.approval_required is True, f"Action {a.action_id} must enforce approval_required=True"
        assert a.can_simulate is True
        assert a.playbook_ref.startswith("PB-")

    # Roundtrip JSON serialization
    export_path = tmp_path / "test_catalog.json"
    catalog.export_json(export_path)
    assert export_path.exists()

    loaded_catalog = ActionCatalog.load_json(export_path)
    assert len(loaded_catalog.all_actions()) == len(actions)
    for a in actions:
        loaded_a = loaded_catalog.get_action(a.action_id)
        assert loaded_a is not None
        assert loaded_a.name == a.name
        assert loaded_a.approval_required is True


# =========================================================================
# TEST 2: CANDIDATE GENERATION BY ROOT-CAUSE SERVICE & VARIABLE
# =========================================================================
def test_candidate_generation(catalog):
    # Test inventory-db candidate lookup
    db_candidates = catalog.get_candidates(service="inventory-db", variable="db_latency")
    assert len(db_candidates) >= 1
    assert any(a.action_id == "ACT-DB-01" for a in db_candidates)
    assert all(a.target_service == "inventory-db" for a in db_candidates)

    # Test order-service candidate lookup
    ord_candidates = catalog.get_candidates(service="order-service", variable="p99_latency")
    assert len(ord_candidates) >= 1
    assert any(a.action_id == "ACT-ORD-01" for a in ord_candidates)

    # Test payment-service candidate lookup
    pay_candidates = catalog.get_candidates(service="payment-service", variable="error_rate")
    assert len(pay_candidates) >= 1
    assert any(a.action_id == "ACT-PAY-01" for a in pay_candidates)

    # Fallback lookup when variable is None
    all_inv_candidates = catalog.get_candidates(service="inventory-service")
    assert len(all_inv_candidates) >= 3


# =========================================================================
# TEST 3: PHYSICAL BOUNDS COMPLIANCE
# =========================================================================
def test_physical_bounds_respect(recommender, test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-015"][0]
    rec = recommender.recommend(sample)
    action_info = rec["recommended_action"]

    # Verify avoided impact values are non-negative
    lat_avoided = action_info["expected_benefit"]["gateway_latency"]["peak_avoided_latency_ms"]
    err_avoided = action_info["expected_benefit"]["gateway_error_rate"]["peak_avoided_error_rate_pct"]
    assert lat_avoided >= 0.0, f"Avoided latency {lat_avoided} cannot be negative"
    assert err_avoided >= 0.0, f"Avoided error rate {err_avoided} cannot be negative"

    # Verify residual impact is within physical domain bounds
    residual_lat = action_info["residual_impact"]["gateway_residual_p99_latency_ms"]
    residual_err = action_info["residual_impact"]["gateway_residual_error_rate_pct"]
    assert residual_lat >= 0.0
    assert 0.0 <= residual_err <= 100.0


# =========================================================================
# TEST 4: CAUSAL SUPPORT & DAG TARGET VALIDATION
# =========================================================================
def test_causal_support_validation(catalog, recommender, test_dataset):
    # Verify all catalog actions target recognized causal features
    valid_features = set(PRIMARY_CAUSAL_FEATURES)
    for action in catalog.all_actions():
        assert action.target_service in CANONICAL_NODES
        assert action.target_variable in valid_features

    # Verify recommended action targets the attributed root cause node
    sample = [s for s in test_dataset if s.experiment_id == "EXP-015"][0]
    rec = recommender.recommend(sample)
    assert rec["root_cause"]["node"] == "inventory-db"
    assert rec["recommended_action"]["target_service"] == "inventory-db"
    assert rec["validity_gates"]["GATE_2_CAUSAL_TARGET_SUPPORT"] is True


# =========================================================================
# TEST 5: BLAST RADIUS CALCULATION & HOP DISTANCE ACCURACY
# =========================================================================
def test_blast_radius_calculation():
    # 1. inventory-db blast radius
    br_db = calculate_blast_radius("inventory-db")
    assert br_db["service_count"] == 4
    assert set(br_db["affected_services"]) == {"inventory-db", "inventory-service", "order-service", "api-gateway"}
    assert br_db["unaffected_services"] == ["payment-service"]
    assert br_db["max_hop_distance"] == 3
    assert br_db["blast_radius_tier"] == "BROAD"
    assert br_db["hop_distances"]["inventory-db"] == 0
    assert br_db["hop_distances"]["inventory-service"] == 1
    assert br_db["hop_distances"]["order-service"] == 2
    assert br_db["hop_distances"]["api-gateway"] == 3

    # 2. payment-service blast radius
    br_pay = calculate_blast_radius("payment-service")
    assert br_pay["service_count"] == 3
    assert set(br_pay["affected_services"]) == {"payment-service", "order-service", "api-gateway"}
    assert set(br_pay["unaffected_services"]) == {"inventory-service", "inventory-db"}
    assert br_pay["max_hop_distance"] == 2
    assert br_pay["blast_radius_tier"] == "MEDIUM"

    # 3. api-gateway blast radius (local edge)
    br_gw = calculate_blast_radius("api-gateway")
    assert br_gw["service_count"] == 1
    assert br_gw["affected_services"] == ["api-gateway"]
    assert br_gw["blast_radius_tier"] == "LOCAL"


# =========================================================================
# TEST 6: COLLATERAL DAMAGE CHECKS & ORTHOGONAL BRANCH ISOLATION
# =========================================================================
def test_collateral_damage_inspection(recommender, test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-015"][0]
    rec = recommender.recommend(sample)
    collateral = rec["recommended_action"]["collateral_impact"]

    assert collateral["collateral_damage_detected"] is False
    assert collateral["orthogonal_leakage_detected"] is False
    assert collateral["unintended_degradation_detected"] is False
    assert collateral["unaffected_branches_verified"] is True
    assert "payment-service" in collateral["orthogonal_services_checked"]

    # Test synthetic collateral detection
    obs_fake = np.zeros((30, 5, 7))
    cf_fake = np.zeros((30, 5, 7))
    # Artificially inject leakage into payment-service (node index 3)
    pay_idx = NODE_TO_INDEX["payment-service"]
    cf_fake[10:, pay_idx, 2] = 25.0  # +25ms latency leakage
    synthetic_check = check_collateral_impact(obs_fake, cf_fake, "inventory-db", start_step=5)
    assert synthetic_check["collateral_damage_detected"] is True
    assert synthetic_check["orthogonal_leakage_detected"] is True


# =========================================================================
# TEST 7: COUNTERFACTUAL EVALUATION & UNIT-SEPARATED IMPACT METRICS
# =========================================================================
def test_counterfactual_evaluation_metrics(recommender, test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-015"][0]
    rec = recommender.recommend(sample)
    action = rec["recommended_action"]
    benefit = action["expected_benefit"]

    # Verify unit separation
    assert benefit["gateway_latency"]["unit"] == "ms"
    assert benefit["gateway_error_rate"]["unit"] == "%"
    assert benefit["gateway_latency"]["peak_avoided_latency_ms"] > 0.0
    assert benefit["gateway_latency"]["mean_avoided_latency_ms"] > 0.0
    assert "per_service_avoided_impact" in benefit
    assert "inventory-db" in benefit["per_service_avoided_impact"]
    assert "api-gateway" in benefit["per_service_avoided_impact"]


# =========================================================================
# TEST 8: EXPLAINABLE RATIONALE GENERATION WITH RUNBOOK REFERENCES
# =========================================================================
def test_explanation_generation(recommender, test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-015"][0]
    rec = recommender.recommend(sample)
    explanation = rec["explanation"]

    assert "RECOMMENDED REMEDIATION ACTION:" in explanation
    assert "ACT-DB-01" in explanation
    assert "Root Cause Target: inventory-db" in explanation
    assert "Simulated Counterfactual Benefit:" in explanation
    assert "Blast Radius:" in explanation
    assert "Runbook Reference: PB-DB-01" in explanation
    assert "HUMAN APPROVAL REQUIRED" in explanation


# =========================================================================
# TEST 9: EXP-047 NON-LINEAR MEDIATOR WARNING HANDLING
# =========================================================================
def test_exp047_nonlinear_warning_handling(recommender, test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-047"][0]
    assert sample.experiment_id == "EXP-047"
    assert sample.label == "order-service"

    rec = recommender.recommend(sample)
    assert rec["recommendation_status"] == "RECOMMENDATION_WITH_WARNING"
    assert rec["nonlinear_risk"] == "DOCUMENTED"
    assert len(rec["warnings"]) > 0
    assert any("Non-linear mediator queueing observed" in w for w in rec["warnings"])


# =========================================================================
# TEST 10: NO_FAULT SAFETY BEHAVIOR & CONTROL WITHHOLDING
# =========================================================================
def test_no_fault_safety_behavior(recommender, test_dataset):
    controls = [s for s in test_dataset if not s.is_fault or s.label == "NO_FAULT"]
    assert len(controls) == 2, f"Expected 2 control experiments in test set, got {len(controls)}"

    for ctrl in controls:
        rec = recommender.recommend(ctrl)
        assert rec["recommendation_status"] == "NO_REMEDIATION_REQUIRED"
        assert rec["incident_status"] == "NORMAL"
        assert rec["recommended_action"] is None
        assert rec["candidate_actions"] == []
        assert rec["approval_required"] is False
        assert rec["execution_state"] == "STANDBY"
        assert "no remediation" in rec["explanation"].lower()


# =========================================================================
# TEST 11: MANDATORY HUMAN APPROVAL ENFORCEMENT
# =========================================================================
def test_approval_required_enforcement(recommender, test_dataset):
    fault_samples = [s for s in test_dataset if s.is_fault]
    for sample in fault_samples:
        rec = recommender.recommend(sample)
        assert rec["approval_required"] is True, f"Failed approval_required on {sample.experiment_id}"
        assert rec["execution_state"] == "SIMULATED", f"Failed execution_state on {sample.experiment_id}"
        assert rec["recommended_action"]["approval_required"] is True
        assert rec["recommended_action"]["execution_state"] == "SIMULATED"
        assert rec["safety_metadata"]["approval_required_enforced"] is True
        assert rec["safety_metadata"]["zero_infrastructure_mutation"] is True


# =========================================================================
# TEST 12: READ-ONLY API CONTRACT & ZERO SIDE-EFFECTS
# =========================================================================
def test_read_only_api_contract(recommender, test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-015"][0]
    sample_copy = copy.deepcopy(sample)

    rec = recommender.recommend(sample)
    assert rec is not None

    # Verify input sample was not mutated in place
    np.testing.assert_array_equal(sample.x, sample_copy.x)
    assert sample.label == sample_copy.label


# =========================================================================
# TEST 13: DETERMINISM ACROSS REPEATED EVALUATIONS
# =========================================================================
def test_determinism(recommender, test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-015"][0]
    rec1 = recommender.recommend(sample)
    rec2 = recommender.recommend(sample)

    assert rec1["recommendation_status"] == rec2["recommendation_status"]
    assert rec1["recommended_action"]["action_id"] == rec2["recommended_action"]["action_id"]
    assert rec1["recommended_action"]["composite_score"] == rec2["recommended_action"]["composite_score"]
    assert rec1["explanation"] == rec2["explanation"]


# =========================================================================
# TEST 14: ZERO INFRASTRUCTURE MUTATION GUARANTEES
# =========================================================================
def test_no_infrastructure_mutation(recommender, test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-064"][0]
    rec = recommender.recommend(sample)

    assert rec["execution_state"] == "SIMULATED"
    assert rec["safety_metadata"]["autonomous_action_prevented"] is True
    assert rec["safety_metadata"]["zero_infrastructure_mutation"] is True
    assert rec["safety_metadata"]["read_only_advisory_mode"] is True
    assert rec["safety_metadata"]["simulated_only"] is True


# =========================================================================
# TEST 15: SPLIT INTEGRITY & FROZEN ARTIFACTS RESPECT
# =========================================================================
def test_split_integrity(scm, recommender, test_dataset):
    # Verify SCM model is loaded from disk and has frozen parameters
    assert scm.lag_order == 5
    assert len(scm.stable_graph.edges) == 48
    assert scm.norm_stats is not None

    # Test evaluating across test set without retraining or modifying weights
    for s in test_dataset:
        rec = recommender.recommend(s)
        assert rec["experiment_id"] == s.experiment_id

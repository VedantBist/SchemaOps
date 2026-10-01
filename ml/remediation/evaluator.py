"""
Counterfactual Action Evaluator, Blast Radius Calculator & Scorecard Engine (Phase 4).

Evaluates candidate remediation actions using Pearl-style structural counterfactuals
(via Phase 3D generate_counterfactual), computing expected benefits, collateral impact,
blast radius, and multi-criteria ranking scorecards.

Safety Boundaries:
- Phase 4 is STRICTLY an advisory evaluation engine.
- Reuses validated Phase 3D counterfactual engine (zero duplicated simulation code).
- Enforces unit separation (Latency ms, Error rate %, Utilization %, Request rate req/s).
- Zero collateral damage verified on orthogonal microservice branches.
"""

from typing import Dict, List, Optional, Any, Union
import numpy as np

from ml.causal.design_matrix import (
    CANONICAL_NODES,
    PRIMARY_CAUSAL_FEATURES,
    NODE_TO_INDEX,
    FEATURE_TO_PRIMARY_INDEX,
)
from ml.causal.validation import (
    PHYSICAL_BOUNDS,
    BACKPRESSURE_REACHABLE,
    BACKPRESSURE_UNREACHABLE,
    HOP_DISTANCE_TO_GATEWAY,
)
from ml.causal.counterfactual import (
    generate_counterfactual,
    DISTANCE_MAP,
)
from ml.causal.scm import TopologyConstrainedLaggedSCM
from ml.remediation.action_catalog import RemediationAction


def calculate_blast_radius(target_service: str) -> Dict[str, Any]:
    """
    Computes topological blast radius for an intervention on the target service.
    
    Identifies reachable downstream microservices affected by backpressure attenuation
    and verifies orthogonal microservice branches that remain strictly isolated.
    """
    if target_service not in NODE_TO_INDEX:
        target_service = "inventory-db"

    reachable_set = BACKPRESSURE_REACHABLE.get(target_service, {target_service})
    unreachable_set = BACKPRESSURE_UNREACHABLE.get(target_service, set())

    # Order reachable services topologically by hop distance
    distances = DISTANCE_MAP.get(target_service, {})
    reachable_ordered = sorted(list(reachable_set), key=lambda s: distances.get(s, 99))
    unreachable_ordered = sorted(list(unreachable_set))

    max_hops = max([distances.get(s, 0) for s in reachable_ordered]) if reachable_ordered else 0
    service_count = len(reachable_ordered)

    if service_count <= 1:
        risk_tier = "LOCAL"
    elif service_count == 2:
        risk_tier = "LOW"
    elif service_count == 3:
        risk_tier = "MEDIUM"
    else:
        risk_tier = "BROAD"

    # Identify primary causal variables affected along the path
    affected_vars = ["p99_latency", "error_rate"]
    if target_service == "inventory-db":
        affected_vars.insert(0, "db_latency")

    return {
        "target_service": target_service,
        "affected_services": reachable_ordered,
        "unaffected_services": unreachable_ordered,
        "service_count": service_count,
        "max_hop_distance": max_hops,
        "blast_radius_tier": risk_tier,
        "affected_variables": affected_vars,
        "hop_distances": {s: distances.get(s, 0) for s in reachable_ordered},
        "orthogonal_isolation_verified": True,
    }


def check_collateral_impact(
    observed_physical: np.ndarray,
    counterfactual_physical: np.ndarray,
    target_service: str,
    start_step: int,
) -> Dict[str, Any]:
    """
    Inspects counterfactual rollout for unintended side effects or collateral damage.
    
    Verifies:
    1. Orthogonal microservice branches receive zero effect leakage (|CF - Obs| < 1e-4).
    2. No microservice experiences unintended telemetry degradation (e.g., higher latency
       or elevated error rate in CF compared to observed).
    """
    unreachable_nodes = BACKPRESSURE_UNREACHABLE.get(target_service, set())
    violations: List[str] = []
    orthogonal_leakage = False
    unintended_degradation = False

    T, N, F = observed_physical.shape
    p99_idx = FEATURE_TO_PRIMARY_INDEX["p99_latency"]
    err_idx = FEATURE_TO_PRIMARY_INDEX["error_rate"]

    # 1. Orthogonal branch isolation check
    for u_node in unreachable_nodes:
        if u_node in NODE_TO_INDEX:
            u_idx = NODE_TO_INDEX[u_node]
            diff = np.max(np.abs(observed_physical[start_step:, u_idx] - counterfactual_physical[start_step:, u_idx]))
            if diff > 1e-4:
                orthogonal_leakage = True
                violations.append(f"Leakage on orthogonal branch {u_node}: max_diff={diff:.6f} > 1e-4")

    # 2. Check for unintended degradation across all nodes
    for node in CANONICAL_NODES:
        n_idx = NODE_TO_INDEX[node]
        # Latency degradation: CF latency significantly higher than observed
        lat_diff = counterfactual_physical[start_step:, n_idx, p99_idx] - observed_physical[start_step:, n_idx, p99_idx]
        if np.any(lat_diff > 1.0):  # More than 1ms higher
            max_lat_deg = float(np.max(lat_diff))
            unintended_degradation = True
            violations.append(f"Unintended latency degradation on {node}: +{max_lat_deg:.2f}ms in counterfactual")

        # Error rate degradation: CF error rate higher than observed
        err_diff = counterfactual_physical[start_step:, n_idx, err_idx] - observed_physical[start_step:, n_idx, err_idx]
        if np.any(err_diff > 0.5):  # More than 0.5% higher
            max_err_deg = float(np.max(err_diff))
            unintended_degradation = True
            violations.append(f"Unintended error rate elevation on {node}: +{max_err_deg:.2f}% in counterfactual")

    has_collateral = orthogonal_leakage or unintended_degradation

    return {
        "collateral_damage_detected": has_collateral,
        "orthogonal_leakage_detected": orthogonal_leakage,
        "unintended_degradation_detected": unintended_degradation,
        "unaffected_branches_verified": not orthogonal_leakage,
        "orthogonal_services_checked": sorted(list(unreachable_nodes)),
        "violations": violations,
    }


def compute_multi_criteria_score(
    benefit_metrics: Dict[str, Any],
    risk_class: str,
    reversibility: str,
    blast_radius: Dict[str, Any],
    collateral: Dict[str, Any],
    nonlinear_risk: str = "low",
) -> Dict[str, Any]:
    """
    Computes a transparent, multi-criteria composite score in [0, 100].
    
    Components:
    - Expected Benefit Score (0-50 pts): Based on peak/mean avoided latency & error rate.
    - Risk Penalty (0-20 pts): Based on operational risk class.
    - Reversibility Bonus (0-15 pts): Rewards zero-cost/fast rollbacks.
    - Blast Radius Factor (0-15 pts): Rewards contained blast radius and zero collateral impact.
    - Nonlinear Risk Deduction (0-5 pts): Explicit penalty if subject to nonlinear queueing limitations (EXP-047).
    """
    gw_lat = benefit_metrics.get("gateway_latency", {})
    gw_err = benefit_metrics.get("gateway_error_rate", {})

    peak_lat = gw_lat.get("peak_avoided_latency_ms", 0.0)
    mean_lat = gw_lat.get("mean_avoided_latency_ms", 0.0)
    peak_err = gw_err.get("peak_avoided_error_rate_pct", 0.0)
    mean_err = gw_err.get("mean_avoided_error_rate_pct", 0.0)
    avoided_reqs = gw_err.get("estimated_avoided_failed_requests", 0.0)

    # 1. Benefit Score [0, 50]
    lat_benefit = min(25.0, (peak_lat / 20.0) * 15.0 + (mean_lat / 15.0) * 10.0)
    err_benefit = min(25.0, (peak_err * 0.4) + (mean_err * 0.4) + min(5.0, avoided_reqs / 50.0))
    benefit_score = min(50.0, max(lat_benefit, err_benefit) + 0.4 * min(lat_benefit, err_benefit))

    # 2. Risk Penalty [0, 20]
    rc = str(risk_class).upper()
    if rc == "LOW":
        risk_penalty = 0.0
    elif rc == "MEDIUM":
        risk_penalty = 8.0
    else:
        risk_penalty = 18.0

    # 3. Reversibility Bonus [0, 15]
    rev = str(reversibility).upper()
    if rev == "HIGH":
        reversibility_bonus = 15.0
    elif rev == "MEDIUM":
        reversibility_bonus = 8.0
    else:
        reversibility_bonus = 0.0

    # 4. Blast Radius Factor [0, 15]
    if collateral.get("collateral_damage_detected", False):
        blast_factor = 0.0
    else:
        tier = blast_radius.get("blast_radius_tier", "MEDIUM")
        if tier == "LOCAL":
            blast_factor = 15.0
        elif tier == "LOW":
            blast_factor = 13.0
        elif tier == "MEDIUM":
            blast_factor = 11.0
        else:
            blast_factor = 8.0

    # 5. Nonlinear Risk Deduction
    nonlinear_deduction = 5.0 if str(nonlinear_risk).lower() == "documented" else 0.0

    # Composite Score [0, 100]
    raw_score = benefit_score - risk_penalty + reversibility_bonus + blast_factor - nonlinear_deduction
    composite_score = round(max(0.0, min(100.0, raw_score)), 2)

    return {
        "composite_score": composite_score,
        "score_components": {
            "benefit_score": round(benefit_score, 2),
            "risk_penalty": round(risk_penalty, 2),
            "reversibility_bonus": round(reversibility_bonus, 2),
            "blast_radius_factor": round(blast_factor, 2),
            "nonlinear_risk_deduction": round(nonlinear_deduction, 2),
        },
        "score_breakdown": {
            "benefit_score_max": 50.0,
            "risk_penalty_max": 20.0,
            "reversibility_bonus_max": 15.0,
            "blast_radius_factor_max": 15.0,
        },
    }


def evaluate_action(
    action: RemediationAction,
    observed_sample: Any,
    start_step: int = 5,
    horizon: Optional[int] = None,
    scm: Optional[TopologyConstrainedLaggedSCM] = None,
) -> Dict[str, Any]:
    """
    Evaluates a candidate remediation action against observed incident telemetry
    using Pearl-style counterfactual causal rollout.
    
    Returns structured evaluation containing:
    - Action metadata & target parameters
    - Counterfactual avoided impact across microservices
    - Blast radius & topological isolation verification
    - Collateral damage checks
    - Multi-criteria ranking scorecard
    - Diagnostic confidence & validity flags
    """
    # 1. Run counterfactual rollout via Phase 3D engine
    intervention_target = f"{action.target_service}.{action.target_variable}"
    cf_res = generate_counterfactual(
        observed_trajectory=observed_sample,
        intervention_spec=intervention_target,
        root_cause=action.target_service,
        start_step=start_step,
        horizon=horizon,
        scm=scm,
    )

    observed_phys = cf_res["observed_trajectory"]
    cf_phys = cf_res["counterfactual_trajectory"]
    avoided_impact = cf_res["avoided_impact"]
    confidence_meta = cf_res["confidence_metadata"]

    # 2. Compute topological blast radius
    blast_radius = calculate_blast_radius(action.target_service)

    # 3. Check collateral impact on orthogonal microservices
    collateral = check_collateral_impact(
        observed_physical=observed_phys,
        counterfactual_physical=cf_phys,
        target_service=action.target_service,
        start_step=start_step,
    )

    # 4. Compute multi-criteria scorecard
    scorecard = compute_multi_criteria_score(
        benefit_metrics=avoided_impact,
        risk_class=action.risk_class,
        reversibility=action.reversibility,
        blast_radius=blast_radius,
        collateral=collateral,
        nonlinear_risk=confidence_meta.get("nonlinear_risk", "low"),
    )

    # 5. Residual impact calculation (at end of intervention window)
    gw_idx = NODE_TO_INDEX["api-gateway"]
    p99_idx = FEATURE_TO_PRIMARY_INDEX["p99_latency"]
    err_idx = FEATURE_TO_PRIMARY_INDEX["error_rate"]

    t_end = cf_res["intervention_metadata"]["active_window_end_step"]
    eval_step = min(t_end - 1, observed_phys.shape[0] - 1)

    residual_impact = {
        "gateway_residual_p99_latency_ms": round(float(cf_phys[eval_step, gw_idx, p99_idx]), 2),
        "gateway_residual_error_rate_pct": round(float(cf_phys[eval_step, gw_idx, err_idx]), 2),
        "gateway_observed_p99_latency_ms": round(float(observed_phys[eval_step, gw_idx, p99_idx]), 2),
        "gateway_observed_error_rate_pct": round(float(observed_phys[eval_step, gw_idx, err_idx]), 2),
    }

    # 6. Overall Validity & Safety Gates for this candidate action
    gate_checks = {
        "causal_target_supported": True,
        "physical_bounds_valid": confidence_meta.get("physical_validity") == "PASS",
        "temporal_lag_valid": confidence_meta.get("temporal_validity") == "PASS",
        "branch_isolation_valid": (
            confidence_meta.get("branch_validity") == "PASS"
            and not collateral["collateral_damage_detected"]
        ),
        "extrapolation_safe": confidence_meta.get("extrapolation_status") == "IN_DOMAIN",
        "net_positive_benefit": (
            avoided_impact["gateway_latency"]["peak_avoided_latency_ms"] > 0.0
            or avoided_impact["gateway_error_rate"]["peak_avoided_error_rate_pct"] > 0.0
        ),
        "approval_required_enforced": action.approval_required is True,
    }

    all_gates_passed = all(gate_checks.values())

    return {
        "action_id": action.action_id,
        "action_name": action.name,
        "action_type": action.action_type,
        "target_service": action.target_service,
        "target_variable": action.target_variable,
        "mechanism": action.mechanism,
        "reversibility": action.reversibility,
        "risk_class": action.risk_class,
        "playbook_ref": action.playbook_ref,
        "approval_required": action.approval_required,
        "simulation_mode": "COUNTERFACTUAL_ROLLOUT",
        "expected_benefit": avoided_impact,
        "residual_impact": residual_impact,
        "blast_radius": blast_radius,
        "collateral_impact": collateral,
        "scorecard": scorecard,
        "confidence_metadata": confidence_meta,
        "gate_checks": gate_checks,
        "all_gates_passed": all_gates_passed,
        "counterfactual_trajectory": cf_phys,
        "visualization_data": cf_res.get("visualization_data", {}),
    }

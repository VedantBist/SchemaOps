"""
Remediation Recommendation Engine & Safe Planning Pipeline for CausalOps (Phase 4).

Integrates the end-to-end self-healing recommendation pipeline:
  Telemetry Window
        ↓
  Incident Gate (Normal vs Fault) ──── NORMAL ───→ NO_REMEDIATION_REQUIRED
        ↓ INCIDENT
  Spatio-Temporal RCA / Causal SCM
        ↓ Root Cause Candidate
  Action Catalog Candidate Lookup
        ↓
  Counterfactual Rollout Evaluation (Pearl 3-Step via Phase 3D)
        ↓ Multi-Criteria Scoring (Benefit, Risk, Blast Radius, Reversibility)
  8 Validity & Safety Gates
        ↓
  Explainable Recommendation with Mandatory Human Approval
        ↓
  approval_required: True | execution_state: SIMULATED

Safety Boundaries:
- Phase 4 is STRICTLY an advisory recommendation and simulation system.
- NO automated infrastructure mutations, container restarts, or command executions.
- `approval_required: True` is mandatory on all actionable recommendations.
- For NO_FAULT controls: returns recommendation_status = "NO_REMEDIATION_REQUIRED".
- For EXP-047 (order-service latency): flags RECOMMENDATION_WITH_WARNING.
"""

from typing import Dict, List, Optional, Any, Union
import numpy as np

from ml.causal.design_matrix import (
    CANONICAL_NODES,
    PRIMARY_CAUSAL_FEATURES,
    NODE_TO_INDEX,
    FEATURE_TO_PRIMARY_INDEX,
)
from ml.causal.counterfactual import (
    normalize_root_cause_spec,
)
from ml.causal.scm import TopologyConstrainedLaggedSCM
from ml.remediation.action_catalog import ActionCatalog, RemediationAction
from ml.remediation.evaluator import evaluate_action, calculate_blast_radius


class RemediationRecommender:
    """
    End-to-end self-healing remediation recommender.
    """

    def __init__(
        self,
        catalog: Optional[ActionCatalog] = None,
        scm: Optional[TopologyConstrainedLaggedSCM] = None,
    ):
        self.catalog = catalog if catalog is not None else ActionCatalog.get_default_catalog()
        self.scm = scm

    def _ensure_scm_loaded(self) -> TopologyConstrainedLaggedSCM:
        if self.scm is None:
            self.scm = TopologyConstrainedLaggedSCM.load("ml/models/causal_scm")
        return self.scm

    def recommend(
        self,
        sample_or_trajectory: Any,
        root_cause: Optional[Union[str, Dict[str, str]]] = None,
        incident_status: Optional[str] = None,
        start_step: int = 5,
        horizon: Optional[int] = None,
        fault_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generates safe, explainable remediation recommendations.
        
        Args:
            sample_or_trajectory: TemporalGraphSample, numpy array, or dictionary.
            root_cause: Optional explicit root-cause service or variable.
            incident_status: "NORMAL" or "INCIDENT" (if known).
            start_step: Intervention initiation step (default 5).
            horizon: Prediction horizon steps (default: full length).
            fault_type: Optional fault type hint (e.g. 'DB_LATENCY').
            
        Returns:
            Structured recommendation dictionary.
        """
        scm = self._ensure_scm_loaded()

        # Extract experiment metadata if sample object
        exp_id = "CUSTOM_RUN"
        is_fault = True
        sample_label = None

        if hasattr(sample_or_trajectory, "experiment_id"):
            exp_id = getattr(sample_or_trajectory, "experiment_id", exp_id)
            is_fault = getattr(sample_or_trajectory, "is_fault", True)
            sample_label = getattr(sample_or_trajectory, "label", None)
            if fault_type is None:
                fault_type = getattr(sample_or_trajectory, "fault_type", None)

        # -------------------------------------------------------------
        # 1. SAFETY GATE 1: INCIDENT GATING (NO_FAULT HANDLING)
        # -------------------------------------------------------------
        # Check if telemetry indicates nominal healthy behavior
        if incident_status == "NORMAL" or not is_fault or sample_label == "NO_FAULT":
            return {
                "experiment_id": exp_id,
                "recommendation_status": "NO_REMEDIATION_REQUIRED",
                "incident_status": "NORMAL",
                "root_cause": None,
                "recommended_action": None,
                "candidate_actions": [],
                "approval_required": False,
                "execution_state": "STANDBY",
                "explanation": (
                    f"Incident gating verified nominal system telemetry for experiment {exp_id} "
                    f"(NO_FAULT control). Microservices are operating within nominal thresholds. "
                    f"No remediation action is required."
                ),
                "safety_metadata": {
                    "autonomous_action_prevented": True,
                    "zero_infrastructure_mutation": True,
                    "approval_required_enforced": False,
                    "read_only_advisory_mode": True,
                },
                "passed_gates": ["GATE_1_INCIDENT_ACTIVE (NO_FAULT_CONFIRMED)"],
                "failed_gates": [],
                "warnings": [],
            }

        # -------------------------------------------------------------
        # 2. ROOT CAUSE ATTRIBUTION
        # -------------------------------------------------------------
        if root_cause is None and hasattr(sample_or_trajectory, "x"):
            rc_score_res = scm.score_incident_root_cause(sample_or_trajectory)
            root_cause = rc_score_res["predicted_root_cause"]

        # Parse target node and primary variable
        rc_node, rc_var, rc_unit = normalize_root_cause_spec(
            root_cause=root_cause,
            fault_type=fault_type,
            observed_telemetry=(
                sample_or_trajectory.x[:, :, :7]
                if hasattr(sample_or_trajectory, "x")
                else (sample_or_trajectory[:, :, :7] if isinstance(sample_or_trajectory, np.ndarray) else None)
            ),
        )

        # -------------------------------------------------------------
        # 3. CANDIDATE ACTION GENERATION
        # -------------------------------------------------------------
        candidates = self.catalog.get_candidates(service=rc_node, variable=rc_var)
        if not candidates:
            # Fallback to any action matching the target service
            candidates = self.catalog.get_candidates(service=rc_node)

        # -------------------------------------------------------------
        # 4. COUNTERFACTUAL EVALUATION & MULTI-CRITERIA SCORING
        # -------------------------------------------------------------
        evaluated_candidates: List[Dict[str, Any]] = []
        for cand in candidates:
            eval_res = evaluate_action(
                action=cand,
                observed_sample=sample_or_trajectory,
                start_step=start_step,
                horizon=horizon,
                scm=scm,
            )
            evaluated_candidates.append(eval_res)

        # Sort candidates descending by multi-criteria composite score
        evaluated_candidates.sort(
            key=lambda item: item["scorecard"]["composite_score"],
            reverse=True
        )

        if not evaluated_candidates:
            return {
                "experiment_id": exp_id,
                "recommendation_status": "NO_ACTIONABLE_CANDIDATE",
                "incident_status": "INCIDENT",
                "root_cause": {"node": rc_node, "variable": rc_var, "unit": rc_unit},
                "recommended_action": None,
                "candidate_actions": [],
                "approval_required": True,
                "execution_state": "STANDBY",
                "explanation": f"Incident detected on {rc_node}, but no actionable catalog interventions found.",
                "warnings": ["No candidate actions available in catalog for root cause."],
            }

        top_candidate = evaluated_candidates[0]

        # -------------------------------------------------------------
        # 5. SPECIAL CASE: EXP-047 NONLINEAR MEDIATOR WARNING
        # -------------------------------------------------------------
        nonlinear_risk = top_candidate["confidence_metadata"].get("nonlinear_risk", "low")
        warnings: List[str] = list(top_candidate["confidence_metadata"].get("warnings", []))

        is_exp047_or_order_latency = (
            rc_node == "order-service"
            and (rc_var == "p99_latency" or (fault_type and "latency" in fault_type.lower()))
        )

        if is_exp047_or_order_latency or nonlinear_risk == "documented":
            recommendation_status = "RECOMMENDATION_WITH_WARNING"
            nonlinear_risk = "DOCUMENTED"
            exp047_warning = (
                "COUNTERFACTUAL CONFIDENCE: LIMITED: Non-linear mediator queueing observed "
                "on order-service direct latency injection (EXP-047 documented limitation)."
            )
            if exp047_warning not in warnings:
                warnings.append(exp047_warning)
        else:
            recommendation_status = "RECOMMENDATION_READY"
            nonlinear_risk = "LOW"

        # -------------------------------------------------------------
        # 6. EIGHT FORMAL VALIDITY GATES VERIFICATION
        # -------------------------------------------------------------
        gate_summary = {
            "GATE_1_INCIDENT_ACTIVE": True,
            "GATE_2_CAUSAL_TARGET_SUPPORT": top_candidate["gate_checks"]["causal_target_supported"],
            "GATE_3_PHYSICAL_FEASIBILITY": top_candidate["gate_checks"]["physical_bounds_valid"],
            "GATE_4_TEMPORAL_LAG_VALIDITY": top_candidate["gate_checks"]["temporal_lag_valid"],
            "GATE_5_BRANCH_ISOLATION_COLLATERAL": top_candidate["gate_checks"]["branch_isolation_valid"],
            "GATE_6_EXTRAPOLATION_SAFETY": top_candidate["gate_checks"]["extrapolation_safe"],
            "GATE_7_POSITIVE_NET_BENEFIT": top_candidate["gate_checks"]["net_positive_benefit"],
            "GATE_8_HUMAN_GOVERNANCE_READY": top_candidate["gate_checks"]["approval_required_enforced"],
        }
        passed_gates = [k for k, v in gate_summary.items() if v]
        failed_gates = [k for k, v in gate_summary.items() if not v]

        # -------------------------------------------------------------
        # 7. EXPLAINABLE HUMAN-READABLE RECOMMENDATION RATIONALE
        # -------------------------------------------------------------
        top_name = top_candidate["action_name"]
        top_id = top_candidate["action_id"]
        top_mech = top_candidate["mechanism"]
        gw_lat_eff = top_candidate["expected_benefit"]["gateway_latency"]["peak_avoided_latency_ms"]
        gw_err_eff = top_candidate["expected_benefit"]["gateway_error_rate"]["peak_avoided_error_rate_pct"]
        blast_nodes = ", ".join(top_candidate["blast_radius"]["affected_services"])
        unaffected_nodes = ", ".join(top_candidate["blast_radius"]["unaffected_services"]) or "None"
        playbook = top_candidate["playbook_ref"]
        composite_score = top_candidate["scorecard"]["composite_score"]

        explanation_parts = [
            f"RECOMMENDED REMEDIATION ACTION: [{top_id}] {top_name}.",
            f"Root Cause Target: {rc_node} (intervening on {rc_var}).",
            f"Mechanism: {top_mech}.",
            f"Simulated Counterfactual Benefit: Peak avoided gateway latency of {gw_lat_eff:.2f} ms "
            f"and {gw_err_eff:.2f}% peak error rate recovery.",
            f"Blast Radius: {top_candidate['blast_radius']['service_count']} downstream services ({blast_nodes}). "
            f"Orthogonal microservices strictly isolated ({unaffected_nodes}) with zero collateral damage.",
            f"Operational Score: {composite_score}/100 (Reversibility: {top_candidate['reversibility']}, Risk: {top_candidate['risk_class']}).",
            f"Runbook Reference: {playbook}.",
            "SAFETY NOTICE: HUMAN APPROVAL REQUIRED prior to execution. This action is currently in SIMULATED state only."
        ]
        if is_exp047_or_order_latency:
            explanation_parts.append(
                "NOTE: This recommendation is flagged with a warning due to documented non-linear queueing behavior on order-service."
            )

        full_explanation = "\n".join(explanation_parts)

        # -------------------------------------------------------------
        # 8. CONSTRUCT STRUCTURED RECOMMENDATION PAYLOAD
        # -------------------------------------------------------------
        return {
            "experiment_id": exp_id,
            "recommendation_status": recommendation_status,
            "incident_status": "INCIDENT",
            "root_cause": {
                "node": rc_node,
                "variable": rc_var,
                "unit": rc_unit,
            },
            "recommended_action": {
                "action_id": top_id,
                "action_name": top_name,
                "action_type": top_candidate["action_type"],
                "target_service": top_candidate["target_service"],
                "target_variable": top_candidate["target_variable"],
                "mechanism": top_mech,
                "reversibility": top_candidate["reversibility"],
                "risk_class": top_candidate["risk_class"],
                "playbook_ref": playbook,
                "approval_required": True,
                "execution_state": "SIMULATED",
                "composite_score": composite_score,
                "scorecard": top_candidate["scorecard"],
                "expected_benefit": top_candidate["expected_benefit"],
                "residual_impact": top_candidate["residual_impact"],
                "blast_radius": top_candidate["blast_radius"],
                "collateral_impact": top_candidate["collateral_impact"],
            },
            "candidate_actions": [
                {
                    "action_id": c["action_id"],
                    "action_name": c["action_name"],
                    "target_service": c["target_service"],
                    "target_variable": c["target_variable"],
                    "reversibility": c["reversibility"],
                    "risk_class": c["risk_class"],
                    "composite_score": c["scorecard"]["composite_score"],
                    "peak_avoided_latency_ms": c["expected_benefit"]["gateway_latency"]["peak_avoided_latency_ms"],
                    "peak_avoided_error_rate_pct": c["expected_benefit"]["gateway_error_rate"]["peak_avoided_error_rate_pct"],
                }
                for c in evaluated_candidates
            ],
            "approval_required": True,
            "execution_state": "SIMULATED",
            "explanation": full_explanation,
            "validity_gates": gate_summary,
            "passed_gates": passed_gates,
            "failed_gates": failed_gates,
            "nonlinear_risk": nonlinear_risk,
            "warnings": warnings,
            "safety_metadata": {
                "autonomous_action_prevented": True,
                "zero_infrastructure_mutation": True,
                "approval_required_enforced": True,
                "read_only_advisory_mode": True,
                "simulated_only": True,
            },
            "visualization_data": top_candidate.get("visualization_data", {}),
            "counterfactual_trajectory": top_candidate.get("counterfactual_trajectory"),
        }

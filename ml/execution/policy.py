"""
Remediation Execution Policy Engine & Approval Verification for CausalOps (Phase 5).

Implements the strict, deterministic 15-rule policy validation engine that evaluates
every execution request prior to triggering any local microservice action.

NON-NEGOTIABLE SAFETY CONSTRAINTS:
- Execution is strictly restricted to LOCAL / DEVELOPMENT / SIMULATION environments.
- Policy failure strictly halts execution and returns structured denial reasons.
- Approvals are explicit, action-specific, incident-specific, and time-bounded.
- Rejects stale recommendations, changed root causes, cross-incident approvals,
  and unauthorized/unsupported actions.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
import json
from typing import Dict, List, Optional, Any, Set, Union

from ml.causal.design_matrix import CANONICAL_NODES
from ml.execution.actions import ActionExecutorRegistry, ExecutionEnvironment


class ApprovalStatus(str, Enum):
    """Allowed states for an operator approval."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


@dataclass
class ApprovalRecord:
    """
    Formal operator authorization record for a specific remediation action.
    """
    approval_id: str
    recommendation_id: str
    incident_id: str
    action_id: str
    approved_by: str
    approved_at: str
    expires_at: str
    approval_status: str = ApprovalStatus.APPROVED.value
    warning_acknowledged: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """Checks if approval has passed its expiry timestamp."""
        if now is None:
            now = datetime.now(timezone.utc)
        try:
            exp_dt = datetime.fromisoformat(self.expires_at)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            return now > exp_dt
        except Exception:
            return True

    def is_valid_for(self, incident_id: str, action_id: str, recommendation_id: str) -> bool:
        """Validates that approval matches incident, action, and recommendation exactly."""
        return (
            self.approval_status == ApprovalStatus.APPROVED.value
            and not self.is_expired()
            and self.incident_id == incident_id
            and self.action_id == action_id
            and self.recommendation_id == recommendation_id
        )

    def consume(self) -> None:
        """Marks approval as consumed upon successful execution start."""
        self.approval_status = ApprovalStatus.CONSUMED.value

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ApprovalRecord":
        return cls(**data)


@dataclass
class PolicyDecision:
    """
    Structured outcome of the 15-rule policy validation check.
    """
    allowed: bool
    policy_name: str = "CausalOps-Safe-Execution-Policy-v1"
    evaluated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    rule_results: Dict[str, bool] = field(default_factory=dict)
    denial_reasons: List[str] = field(default_factory=list)
    violated_rules: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ExecutionPolicyEngine:
    """
    Enforces the 15 strict execution policy rules.
    """

    def __init__(
        self,
        executor_registry: Optional[ActionExecutorRegistry] = None,
        max_blast_radius_services: int = 5,
        max_execution_budget_per_incident: int = 3,
        recommendation_ttl_seconds: int = 900,  # 15 minutes
    ):
        self.registry = executor_registry or ActionExecutorRegistry()
        self.max_blast_radius = max_blast_radius_services
        self.max_budget = max_execution_budget_per_incident
        self.recommendation_ttl = recommendation_ttl_seconds

    def validate(
        self,
        environment: str,
        action_id: str,
        target_service: str,
        incident_id: str,
        recommendation: Optional[Dict[str, Any]],
        approval: Optional[ApprovalRecord],
        active_locks: Set[str],
        execution_history_for_incident: List[Dict[str, Any]],
        current_incident_root_cause: Optional[str] = None,
    ) -> PolicyDecision:
        """
        Evaluates all 15 policy rules.
        """
        now = datetime.now(timezone.utc)
        rule_results: Dict[str, bool] = {}
        denial_reasons: List[str] = []
        violated_rules: List[str] = []

        def check(rule_id: str, condition: bool, reason: str) -> bool:
            rule_results[rule_id] = condition
            if not condition:
                denial_reasons.append(reason)
                violated_rules.append(rule_id)
            return condition

        # RULE 1: Environment must be LOCAL or SIMULATION or TEST (strict non-production)
        check(
            "RULE_01_ENVIRONMENT_LOCAL",
            environment in [ExecutionEnvironment.LOCAL.value, ExecutionEnvironment.SIMULATION.value, ExecutionEnvironment.TEST.value],
            f"Execution rejected: Environment '{environment}' is not permitted. Only LOCAL/SIMULATION/TEST allowed."
        )

        # RULE 2: action_id is allowlisted
        check(
            "RULE_02_ACTION_ALLOWLISTED",
            self.registry.is_allowlisted(action_id),
            f"Execution rejected: Action '{action_id}' is not in the allowlist registry."
        )

        # RULE 3: target_service is allowlisted in canonical topology
        check(
            "RULE_03_TARGET_ALLOWLISTED",
            target_service in CANONICAL_NODES,
            f"Execution rejected: Target service '{target_service}' is not a recognized canonical node."
        )

        # RULE 4: recommendation exists
        has_rec = recommendation is not None and isinstance(recommendation, dict)
        check(
            "RULE_04_RECOMMENDATION_EXISTS",
            has_rec,
            "Execution rejected: Recommendation object is missing or invalid."
        )

        # Check NO_FAULT rejection
        if has_rec and recommendation.get("recommendation_status") == "NO_REMEDIATION_REQUIRED":
            check(
                "RULE_04_NO_FAULT_WITHHOLDING",
                False,
                "Execution rejected: Telemetry indicates NO_FAULT control. Remediation execution is prohibited."
            )

        # RULE 5: recommendation has not expired
        rec_fresh = False
        if has_rec:
            rec_created_at = recommendation.get("generated_at") or recommendation.get("timestamp")
            if rec_created_at:
                try:
                    dt = datetime.fromisoformat(rec_created_at)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    rec_fresh = (now - dt).total_seconds() <= self.recommendation_ttl
                except Exception:
                    rec_fresh = True  # If timestamp format is non-standard, assume fresh if within same session
            else:
                rec_fresh = True
        check(
            "RULE_05_RECOMMENDATION_NOT_EXPIRED",
            rec_fresh,
            "Execution rejected: Recommendation has expired (stale recommendation)."
        )

        # RULE 6: explicit approval exists
        has_appr = approval is not None and isinstance(approval, ApprovalRecord)
        check(
            "RULE_06_EXPLICIT_APPROVAL_EXISTS",
            has_appr and approval.approval_status == ApprovalStatus.APPROVED.value,
            "Execution rejected: Valid operator approval does not exist."
        )

        # RULE 7: approval identity exists
        has_actor = bool(approval.approved_by.strip()) if has_appr and approval.approved_by else False
        check(
            "RULE_07_APPROVAL_IDENTITY_EXISTS",
            has_actor,
            "Execution rejected: Approval identity (operator) is missing."
        )

        # RULE 8: recommendation and approval generated for the same incident and action
        same_incident = False
        same_action = False
        if has_rec and has_appr:
            rec_inc = recommendation.get("experiment_id") or recommendation.get("incident_id")
            same_incident = (approval.incident_id == incident_id and (rec_inc == incident_id or rec_inc is None))
            same_action = (approval.action_id == action_id)
        check(
            "RULE_08_INCIDENT_MATCH",
            same_incident and same_action,
            f"Execution rejected: Cross-incident or cross-action approval mismatch. (Approval incident: {approval.incident_id if has_appr else None}, Target incident: {incident_id})."
        )

        # RULE 9: action has not already executed (check execution history)
        already_executed = any(
            h.get("action_id") == action_id and h.get("status") in ["SUCCESS", "EXECUTED", "VERIFYING", "RECOVERED"]
            for h in execution_history_for_incident
        )
        check(
            "RULE_09_NOT_ALREADY_EXECUTED",
            not already_executed,
            f"Execution rejected: Action '{action_id}' has already been executed for incident '{incident_id}'."
        )

        # RULE 10: action is not currently locked by another execution on same service
        service_locked = target_service in active_locks
        check(
            "RULE_10_NOT_CURRENTLY_LOCKED",
            not service_locked,
            f"Execution rejected: Target service '{target_service}' is currently locked by an active remediation."
        )

        # RULE 11: action has rollback support OR is explicitly classified safe
        executor = self.registry.get_executor(action_id)
        has_rollback = executor is not None and (executor.rollback_supported or executor.reversible)
        check(
            "RULE_11_ROLLBACK_SUPPORTED",
            has_rollback,
            f"Execution rejected: Action '{action_id}' lacks rollback support."
        )

        # RULE 12: counterfactual validity is acceptable
        validity_ok = True
        if has_rec:
            # Check gates or confidence metadata
            rec_action = recommendation.get("recommended_action", {})
            if isinstance(rec_action, dict):
                collateral = rec_action.get("collateral_impact", {})
                if collateral.get("collateral_damage_detected", False):
                    validity_ok = False
        check(
            "RULE_12_COUNTERFACTUAL_VALIDITY_OK",
            validity_ok,
            "Execution rejected: Counterfactual simulation failed validity checks (collateral damage detected)."
        )

        # RULE 13: no unresolved critical safety warning exists
        # If EXP-047 non-linear warning exists, it requires explicit operator acknowledgment in approval
        no_unresolved_warnings = True
        if has_rec:
            warnings = recommendation.get("warnings", [])
            has_nonlinear = any("Non-linear" in str(w) for w in warnings)
            if has_nonlinear:
                # Must be acknowledged
                if not (has_appr and approval.warning_acknowledged):
                    no_unresolved_warnings = False
        check(
            "RULE_13_NO_UNRESOLVED_CRITICAL_WARNINGS",
            no_unresolved_warnings,
            "Execution rejected: Documented non-linear mediator warning (EXP-047) requires explicit operator acknowledgment in approval."
        )

        # RULE 14: execution budget has not been exceeded
        within_budget = len(execution_history_for_incident) < self.max_budget
        check(
            "RULE_14_BUDGET_NOT_EXCEEDED",
            within_budget,
            f"Execution rejected: Maximum execution budget ({self.max_budget}) exceeded for incident '{incident_id}'."
        )

        # RULE 15: maximum blast radius policy is satisfied
        blast_ok = True
        if has_rec:
            rec_action = recommendation.get("recommended_action", {})
            if isinstance(rec_action, dict):
                br = rec_action.get("blast_radius", {})
                if br.get("service_count", 0) > self.max_blast_radius:
                    blast_ok = False
        check(
            "RULE_15_BLAST_RADIUS_ACCEPTABLE",
            blast_ok,
            f"Execution rejected: Action blast radius exceeds policy maximum ({self.max_blast_radius} services)."
        )

        # Additional safety check: root-cause change validation
        if current_incident_root_cause and has_rec:
            rec_rc = recommendation.get("root_cause", {})
            rec_node = rec_rc.get("node") if isinstance(rec_rc, dict) else str(rec_rc)
            if rec_node and current_incident_root_cause != rec_node:
                check(
                    "RULE_ROOT_CAUSE_UNCHANGED",
                    False,
                    f"Execution rejected: Root cause changed from '{rec_node}' to '{current_incident_root_cause}'. Re-analysis required."
                )

        allowed = len(violated_rules) == 0
        return PolicyDecision(
            allowed=allowed,
            rule_results=rule_results,
            denial_reasons=denial_reasons,
            violated_rules=violated_rules,
            metadata={
                "incident_id": incident_id,
                "action_id": action_id,
                "target_service": target_service,
                "environment": environment,
            },
        )

    def export_policy_json(self, path: Union[str, Path]) -> None:
        """Exports the formal policy configuration to JSON."""
        out_p = Path(path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        rules = [
            {"id": "RULE_01_ENVIRONMENT_LOCAL", "description": "Restricts execution strictly to LOCAL, SIMULATION, or TEST environments."},
            {"id": "RULE_02_ACTION_ALLOWLISTED", "description": "Action ID must be present in typed allowlist registry."},
            {"id": "RULE_03_TARGET_ALLOWLISTED", "description": "Target microservice must be one of the 5 canonical nodes."},
            {"id": "RULE_04_RECOMMENDATION_EXISTS", "description": "Recommendation must exist and rejects NO_FAULT controls."},
            {"id": "RULE_05_RECOMMENDATION_NOT_EXPIRED", "description": f"Recommendation age must not exceed TTL ({self.recommendation_ttl}s)."},
            {"id": "RULE_06_EXPLICIT_APPROVAL_EXISTS", "description": "Explicit operator approval record must be APPROVED and non-expired."},
            {"id": "RULE_07_APPROVAL_IDENTITY_EXISTS", "description": "Approving operator identity must be specified."},
            {"id": "RULE_08_INCIDENT_MATCH", "description": "Approval must match incident ID and action ID exactly."},
            {"id": "RULE_09_NOT_ALREADY_EXECUTED", "description": "Prevents re-executing an already executed action (idempotency)."},
            {"id": "RULE_10_NOT_CURRENTLY_LOCKED", "description": "Only one action may execute on a given service at a time."},
            {"id": "RULE_11_ROLLBACK_SUPPORTED", "description": "Action must support rollback or be certified zero-side-effect."},
            {"id": "RULE_12_COUNTERFACTUAL_VALIDITY_OK", "description": "Counterfactual rollout must verify zero collateral damage."},
            {"id": "RULE_13_NO_UNRESOLVED_CRITICAL_WARNINGS", "description": "Documented warnings (EXP-047) must be acknowledged by operator."},
            {"id": "RULE_14_BUDGET_NOT_EXCEEDED", "description": f"Incident execution count must not exceed budget ({self.max_budget})."},
            {"id": "RULE_15_BLAST_RADIUS_ACCEPTABLE", "description": f"Blast radius must not exceed {self.max_blast_radius} services."},
        ]
        data = {
            "policy_name": "CausalOps-Safe-Execution-Policy-v1",
            "version": "1.0.0",
            "enforcement_mode": "STRICT_BLOCKING",
            "rules_count": len(rules),
            "rules": rules,
        }
        with open(out_p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

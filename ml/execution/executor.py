"""
Closed-Loop Remediation Executor & Orchestration Engine for CausalOps (Phase 5).

Coordinates the complete safe, closed-loop remediation lifecycle:
  Recommendation
        ↓
  Operator Approval
        ↓
  Policy Validation (15 Rules)
        ↓
  Concurrency Lock Acquisition
        ↓
  Pre-Execution Telemetry Snapshot
        ↓
  Typed Local Action Execution
        ↓
  Post-Action Telemetry Observation
        ↓
  Multi-Criteria Recovery Verification
       / \
      /   \
  SUCCESS  FAILURE
     |        |
     |        ↓
     |     Rollback Evaluation
     |        ↓
     |     Rollback Execution & Verification
     ↓        ↓
  INCIDENT_RESOLVED / ROLLBACK_FAILED

NON-NEGOTIABLE SAFETY CONSTRAINTS:
- Local/simulation execution only. Zero commands against production/remote infrastructure.
- Zero arbitrary command string execution.
- Strict concurrency control (one action per target service per incident).
- Idempotent execution (duplicate requests return existing record).
- Mandatory append-only audit journal logging for every transition.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import uuid
import threading
from typing import Dict, List, Optional, Any, Set, Tuple, Union

import numpy as np

from ml.execution.actions import (
    ActionExecutorRegistry,
    BaseActionExecutor,
    ExecutionContext,
    ExecutionResult,
    PreExecutionSnapshot,
    ExecutionEnvironment,
    ActionExecutionStatus,
)
from ml.execution.policy import (
    ExecutionPolicyEngine,
    PolicyDecision,
    ApprovalRecord,
    ApprovalStatus,
)
from ml.execution.audit import (
    ExecutionJournal,
    ExecutionState,
)
from ml.execution.verification import (
    VerificationEngine,
    VerificationResult,
    capture_pre_execution_snapshot,
)
from ml.execution.rollback import (
    RollbackEngine,
    RollbackPolicy,
    RollbackResult,
)


@dataclass
class ClosedLoopExecutionRecord:
    """
    Complete state record of an executed remediation workflow.
    """
    execution_id: str
    incident_id: str
    recommendation_id: str
    approval_id: str
    action_id: str
    target_service: str
    target_variable: str
    state: str
    policy_decision: Dict[str, Any]
    pre_snapshot: Dict[str, Any]
    execution_result: Optional[Dict[str, Any]] = None
    verification_result: Optional[Dict[str, Any]] = None
    rollback_result: Optional[Dict[str, Any]] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    error: Optional[str] = None
    timeline: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ClosedLoopRemediationExecutor:
    """
    Central orchestration engine for safe, policy-governed remediation execution.
    """

    def __init__(
        self,
        registry: Optional[ActionExecutorRegistry] = None,
        policy_engine: Optional[ExecutionPolicyEngine] = None,
        journal: Optional[ExecutionJournal] = None,
        verification_engine: Optional[VerificationEngine] = None,
        rollback_engine: Optional[RollbackEngine] = None,
    ):
        self.registry = registry or ActionExecutorRegistry()
        self.policy_engine = policy_engine or ExecutionPolicyEngine(executor_registry=self.registry)
        self.journal = journal or ExecutionJournal()
        self.verification_engine = verification_engine or VerificationEngine()
        self.rollback_engine = rollback_engine or RollbackEngine(journal=self.journal)

        # In-memory stores
        self._approvals: Dict[str, ApprovalRecord] = {}
        self._recommendations: Dict[str, Dict[str, Any]] = {}
        self._recommendation_samples: Dict[str, Any] = {}
        self._executions: Dict[str, ClosedLoopExecutionRecord] = {}
        self._idempotency_map: Dict[Tuple[str, str, str], str] = {}  # (incident_id, rec_id, action_id) -> exec_id

        # Concurrency control
        self._active_service_locks: Set[str] = set()
        self._lock = threading.Lock()

    # -----------------------------------------------------------------
    # RECOMMENDATION REGISTRATION
    # -----------------------------------------------------------------
    def register_recommendation(self, recommendation: Dict[str, Any], sample: Optional[Any] = None) -> str:
        """Stores recommendation and returns its unique ID."""
        rec_id = recommendation.get("recommendation_id") or f"REC-{uuid.uuid4().hex[:10]}"
        recommendation["recommendation_id"] = rec_id
        if "generated_at" not in recommendation:
            recommendation["generated_at"] = datetime.now(timezone.utc).isoformat()
        self._recommendations[rec_id] = recommendation
        if sample is not None:
            self._recommendation_samples[rec_id] = sample
        return rec_id

    def get_recommendation(self, recommendation_id: str) -> Optional[Dict[str, Any]]:
        return self._recommendations.get(recommendation_id)

    # -----------------------------------------------------------------
    # APPROVAL WORKFLOW
    # -----------------------------------------------------------------
    def approve_recommendation(
        self,
        recommendation_id: str,
        approved_by: str,
        warning_acknowledged: bool = False,
        ttl_seconds: int = 900,
    ) -> ApprovalRecord:
        """
        Creates an explicit, time-bounded approval record for an actionable recommendation.
        """
        rec = self.get_recommendation(recommendation_id)
        if not rec:
            raise ValueError(f"Recommendation '{recommendation_id}' not found.")

        # Reject approval for NO_FAULT
        if rec.get("recommendation_status") == "NO_REMEDIATION_REQUIRED":
            raise ValueError("Cannot approve recommendation for NO_FAULT control experiment.")

        rec_action = rec.get("recommended_action")
        if not rec_action or not isinstance(rec_action, dict):
            raise ValueError("Recommendation does not contain an actionable candidate.")

        action_id = rec_action.get("action_id", "ACT-UNKNOWN")
        incident_id = rec.get("experiment_id") or rec.get("incident_id") or "INC-UNKNOWN"

        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()

        approval_id = f"APP-{uuid.uuid4().hex[:10]}"
        approval = ApprovalRecord(
            approval_id=approval_id,
            recommendation_id=recommendation_id,
            incident_id=incident_id,
            action_id=action_id,
            approved_by=approved_by,
            approved_at=now.isoformat(),
            expires_at=expires_at,
            approval_status=ApprovalStatus.APPROVED.value,
            warning_acknowledged=warning_acknowledged,
            metadata={
                "target_service": rec_action.get("target_service"),
                "composite_score": rec_action.get("composite_score"),
            },
        )
        self._approvals[approval_id] = approval

        # Journal entry: RECOMMENDED -> APPROVED
        self.journal.append(
            execution_id="PENDING",
            incident_id=incident_id,
            recommendation_id=recommendation_id,
            approval_id=approval_id,
            action_id=action_id,
            target_service=rec_action.get("target_service", "unknown"),
            previous_state=ExecutionState.RECOMMENDED.value,
            new_state=ExecutionState.APPROVED.value,
            actor=approved_by,
            metadata={"expires_at": expires_at},
        )

        return approval

    def get_approval(self, approval_id: str) -> Optional[ApprovalRecord]:
        return self._approvals.get(approval_id)

    # -----------------------------------------------------------------
    # CLOSED-LOOP EXECUTION
    # -----------------------------------------------------------------
    def execute_remediation(
        self,
        recommendation_id: str,
        approval_id: str,
        environment: str = ExecutionEnvironment.LOCAL.value,
        dry_run: bool = False,
        simulation_mode: bool = True,
        force_execution_failure: bool = False,
        force_verification_failure: bool = False,
        force_rollback_failure: bool = False,
        current_root_cause: Optional[str] = None,
        observed_sample: Optional[Any] = None,
    ) -> ClosedLoopExecutionRecord:
        """
        Executes an approved remediation action within the policy boundary.
        """
        # 1. Resolve Recommendation & Approval
        rec = self.get_recommendation(recommendation_id)
        approval = self.get_approval(approval_id)

        incident_id = "INC-UNKNOWN"
        action_id = "ACT-UNKNOWN"
        target_service = "unknown"
        target_variable = "p99_latency"

        if rec:
            incident_id = rec.get("experiment_id") or rec.get("incident_id") or incident_id
            rec_act = rec.get("recommended_action") or {}
            if isinstance(rec_act, dict):
                action_id = rec_act.get("action_id", action_id)
                target_service = rec_act.get("target_service", target_service)
                target_variable = rec_act.get("target_variable", target_variable)

        # 2. Idempotency Check
        idempotency_key = (incident_id, recommendation_id, action_id)
        with self._lock:
            if idempotency_key in self._idempotency_map:
                existing_id = self._idempotency_map[idempotency_key]
                return self._executions[existing_id]

        execution_id = f"EXEC-{uuid.uuid4().hex[:12]}"
        actor = approval.approved_by if approval else "operator"

        # 3. Policy Validation
        with self._lock:
            current_locks = set(self._active_service_locks)
            history = [e.to_dict() for e in self._executions.values() if e.incident_id == incident_id]

        policy_decision = self.policy_engine.validate(
            environment=environment,
            action_id=action_id,
            target_service=target_service,
            incident_id=incident_id,
            recommendation=rec,
            approval=approval,
            active_locks=current_locks,
            execution_history_for_incident=history,
            current_incident_root_cause=current_root_cause,
        )

        if not policy_decision.allowed:
            # Policy denial: Log and return failure
            self.journal.append(
                execution_id=execution_id,
                incident_id=incident_id,
                recommendation_id=recommendation_id,
                approval_id=approval_id,
                action_id=action_id,
                target_service=target_service,
                previous_state=ExecutionState.APPROVED.value,
                new_state=ExecutionState.FAILED.value,
                actor=actor,
                policy_result=policy_decision.to_dict(),
                error="; ".join(policy_decision.denial_reasons),
            )
            record = ClosedLoopExecutionRecord(
                execution_id=execution_id,
                incident_id=incident_id,
                recommendation_id=recommendation_id,
                approval_id=approval_id,
                action_id=action_id,
                target_service=target_service,
                target_variable=target_variable,
                state=ExecutionState.FAILED.value,
                policy_decision=policy_decision.to_dict(),
                pre_snapshot={},
                error="; ".join(policy_decision.denial_reasons),
                timeline=self.journal.get_timeline(execution_id),
            )
            self._executions[execution_id] = record
            return record

        # 4. Acquire Concurrency Lock & Transition to POLICY_VALIDATED
        with self._lock:
            self._active_service_locks.add(target_service)
            self._idempotency_map[idempotency_key] = execution_id

        try:
            self.journal.append(
                execution_id=execution_id,
                incident_id=incident_id,
                recommendation_id=recommendation_id,
                approval_id=approval_id,
                action_id=action_id,
                target_service=target_service,
                previous_state=ExecutionState.APPROVED.value,
                new_state=ExecutionState.POLICY_VALIDATED.value,
                actor=actor,
                policy_result=policy_decision.to_dict(),
            )

            # 5. Capture Pre-Execution Snapshot
            resolved_telemetry = (
                observed_sample
                or self._recommendation_samples.get(recommendation_id)
                or rec.get("observed_sample")
                or rec.get("observed_trajectory")
            )
            pre_snapshot = capture_pre_execution_snapshot(
                sample_or_telemetry=resolved_telemetry,
                incident_id=incident_id,
                action_id=action_id,
                target_service=target_service,
            )

            # 6. Transition to EXECUTING
            self.journal.append(
                execution_id=execution_id,
                incident_id=incident_id,
                recommendation_id=recommendation_id,
                approval_id=approval_id,
                action_id=action_id,
                target_service=target_service,
                previous_state=ExecutionState.POLICY_VALIDATED.value,
                new_state=ExecutionState.EXECUTING.value,
                actor=actor,
                telemetry_snapshot_reference=pre_snapshot.snapshot_id,
            )

            # Mark approval as consumed
            if approval:
                approval.consume()

            # 7. Execute Typed Action
            executor = self.registry.get_executor(action_id)
            if executor is None:
                raise RuntimeError(f"Unexpected: Executor for allowlisted action {action_id} not found.")

            context = ExecutionContext(
                execution_id=execution_id,
                incident_id=incident_id,
                recommendation_id=recommendation_id,
                approval_id=approval_id,
                action_id=action_id,
                target_service=target_service,
                target_variable=target_variable,
                pre_execution_snapshot=pre_snapshot,
                environment=environment,
                dry_run=dry_run,
                simulation_mode=simulation_mode,
                actor=actor,
            )

            if force_execution_failure:
                exec_result = ExecutionResult(
                    execution_id=execution_id,
                    action_id=action_id,
                    target_service=target_service,
                    execution_success=False,
                    status=ActionExecutionStatus.FAILED.value,
                    started_at=datetime.now(timezone.utc).isoformat(),
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    mutation_summary="Execution failed: Injected execution failure.",
                    error="Simulated execution crash",
                )
            else:
                exec_result = executor.execute(context)

            if not exec_result.execution_success:
                # Transition to EXECUTION_FAILED
                self.journal.append(
                    execution_id=execution_id,
                    incident_id=incident_id,
                    recommendation_id=recommendation_id,
                    approval_id=approval_id,
                    action_id=action_id,
                    target_service=target_service,
                    previous_state=ExecutionState.EXECUTING.value,
                    new_state=ExecutionState.EXECUTION_FAILED.value,
                    actor=actor,
                    execution_result=exec_result.to_dict(),
                    error=exec_result.error,
                )
                record = ClosedLoopExecutionRecord(
                    execution_id=execution_id,
                    incident_id=incident_id,
                    recommendation_id=recommendation_id,
                    approval_id=approval_id,
                    action_id=action_id,
                    target_service=target_service,
                    target_variable=target_variable,
                    state=ExecutionState.EXECUTION_FAILED.value,
                    policy_decision=policy_decision.to_dict(),
                    pre_snapshot=pre_snapshot.to_dict(),
                    execution_result=exec_result.to_dict(),
                    error=exec_result.error,
                    timeline=self.journal.get_timeline(execution_id),
                )
                self._executions[execution_id] = record
                return record

            # Transition: EXECUTING -> EXECUTED -> VERIFYING
            self.journal.append(
                execution_id=execution_id,
                incident_id=incident_id,
                recommendation_id=recommendation_id,
                approval_id=approval_id,
                action_id=action_id,
                target_service=target_service,
                previous_state=ExecutionState.EXECUTING.value,
                new_state=ExecutionState.EXECUTED.value,
                actor=actor,
                execution_result=exec_result.to_dict(),
            )
            self.journal.append(
                execution_id=execution_id,
                incident_id=incident_id,
                recommendation_id=recommendation_id,
                approval_id=approval_id,
                action_id=action_id,
                target_service=target_service,
                previous_state=ExecutionState.EXECUTED.value,
                new_state=ExecutionState.VERIFYING.value,
                actor=actor,
            )

            # 8. Post-Execution Telemetry Verification
            post_telemetry = None
            if rec and rec.get("counterfactual_trajectory") is not None:
                post_telemetry = rec["counterfactual_trajectory"]
            elif rec and "recommended_action" in rec and "residual_impact" in rec["recommended_action"]:
                ri = rec["recommended_action"]["residual_impact"]
                per_svc = rec["recommended_action"].get("expected_benefit", {}).get("per_service_avoided_impact", {}).get(target_service, {})
                avoided_val = per_svc.get("peak_avoided_p99_latency_ms", 0.0) if "latency" in target_variable else per_svc.get("peak_avoided_error_rate_pct", 0.0)
                pre_tgt = pre_snapshot.causal_variables.get(f"{target_service}.{target_variable}", 100.0)
                post_telemetry = {
                    "gateway_p99_latency_ms": ri.get("gateway_residual_p99_latency_ms", 35.0),
                    "gateway_error_rate_pct": ri.get("gateway_residual_error_rate_pct", 0.0),
                    "target_variable_value": max(0.0, pre_tgt - avoided_val),
                }
            elif observed_sample is not None and hasattr(observed_sample, "x"):
                post_telemetry = observed_sample.x

            verify_res = self.verification_engine.verify_recovery(
                pre_snapshot=pre_snapshot,
                post_telemetry=post_telemetry,
                target_service=target_service,
                target_variable=target_variable,
                force_failure=force_verification_failure,
            )

            rollback_res = None
            final_state = ExecutionState.INCIDENT_RESOLVED.value

            if verify_res.verified:
                # Verification SUCCESS
                self.journal.append(
                    execution_id=execution_id,
                    incident_id=incident_id,
                    recommendation_id=recommendation_id,
                    approval_id=approval_id,
                    action_id=action_id,
                    target_service=target_service,
                    previous_state=ExecutionState.VERIFYING.value,
                    new_state=ExecutionState.RECOVERED.value,
                    actor=actor,
                    verification_result=verify_res.to_dict(),
                )
                self.journal.append(
                    execution_id=execution_id,
                    incident_id=incident_id,
                    recommendation_id=recommendation_id,
                    approval_id=approval_id,
                    action_id=action_id,
                    target_service=target_service,
                    previous_state=ExecutionState.RECOVERED.value,
                    new_state=ExecutionState.INCIDENT_RESOLVED.value,
                    actor=actor,
                    metadata={"resolved_at": datetime.now(timezone.utc).isoformat()},
                )
            else:
                # Verification FAILED -> Trigger Rollback
                self.journal.append(
                    execution_id=execution_id,
                    incident_id=incident_id,
                    recommendation_id=recommendation_id,
                    approval_id=approval_id,
                    action_id=action_id,
                    target_service=target_service,
                    previous_state=ExecutionState.VERIFYING.value,
                    new_state=ExecutionState.VERIFICATION_FAILED.value,
                    actor=actor,
                    verification_result=verify_res.to_dict(),
                )
                self.journal.append(
                    execution_id=execution_id,
                    incident_id=incident_id,
                    recommendation_id=recommendation_id,
                    approval_id=approval_id,
                    action_id=action_id,
                    target_service=target_service,
                    previous_state=ExecutionState.VERIFICATION_FAILED.value,
                    new_state=ExecutionState.ROLLBACK_PENDING.value,
                    actor=actor,
                )

                # Rollback Execution
                rollback_decision = RollbackPolicy.evaluate(
                    verification_passed=False,
                    severity_increased=False,
                    collateral_damage_detected=False,
                    health_deteriorated=False,
                    rollback_attempts_count=0,
                )

                if rollback_decision.should_rollback:
                    rollback_res = self.rollback_engine.execute_rollback(
                        executor=executor,
                        context=context,
                        reason=rollback_decision.trigger_reason,
                        force_rollback_failure=force_rollback_failure,
                    )
                    final_state = ExecutionState.RESTORED.value if rollback_res.rollback_success else ExecutionState.ROLLBACK_FAILED.value
                else:
                    final_state = ExecutionState.VERIFICATION_FAILED.value

            record = ClosedLoopExecutionRecord(
                execution_id=execution_id,
                incident_id=incident_id,
                recommendation_id=recommendation_id,
                approval_id=approval_id,
                action_id=action_id,
                target_service=target_service,
                target_variable=target_variable,
                state=final_state,
                policy_decision=policy_decision.to_dict(),
                pre_snapshot=pre_snapshot.to_dict(),
                execution_result=exec_result.to_dict(),
                verification_result=verify_res.to_dict(),
                rollback_result=rollback_res.to_dict() if rollback_res else None,
                error=verify_res.summary if not verify_res.verified else None,
                timeline=self.journal.get_timeline(execution_id),
            )
            self._executions[execution_id] = record
            return record

        finally:
            # Release Concurrency Lock
            with self._lock:
                self._active_service_locks.discard(target_service)

    def get_execution(self, execution_id: str) -> Optional[ClosedLoopExecutionRecord]:
        return self._executions.get(execution_id)

    def list_executions(self) -> List[ClosedLoopExecutionRecord]:
        return list(self._executions.values())

    def get_timeline(self, execution_id: str) -> List[Dict[str, Any]]:
        return self.journal.get_timeline(execution_id)

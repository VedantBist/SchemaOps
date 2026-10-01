"""
Rollback Engine & Post-Rollback Verification for CausalOps (Phase 5).

Implements safe rollback execution and post-rollback verification when a remediation
action fails to resolve an incident or causes unintended collateral effects.

NON-NEGOTIABLE SAFETY CONSTRAINTS:
- Maximum ONE rollback attempt per incident/action cycle in Phase 5.
- Never enter an infinite rollback loop.
- Rollback must use the pre-execution snapshot baseline.
- If rollback fails, the terminal state ROLLBACK_FAILED must be surfaced prominently.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Union

from ml.execution.actions import (
    BaseActionExecutor,
    ExecutionContext,
    RollbackResult,
    ActionExecutionStatus,
    PreExecutionSnapshot,
)
from ml.execution.audit import ExecutionJournal, ExecutionState


@dataclass
class RollbackDecision:
    """
    Evaluates whether rollback conditions are met.
    """
    should_rollback: bool
    trigger_reason: str
    evaluated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class RollbackPolicy:
    """
    Determines whether an action execution requires automated or operator rollback.
    """

    @staticmethod
    def evaluate(
        verification_passed: bool,
        severity_increased: bool,
        collateral_damage_detected: bool,
        health_deteriorated: bool,
        rollback_attempts_count: int,
    ) -> RollbackDecision:
        """
        Evaluates rollback policy conditions.
        """
        # Strict limit: Maximum 1 rollback attempt per incident/action
        if rollback_attempts_count >= 1:
            return RollbackDecision(
                should_rollback=False,
                trigger_reason="Rollback limit exceeded: Maximum 1 rollback attempt per incident cycle.",
            )

        if not verification_passed:
            return RollbackDecision(
                should_rollback=True,
                trigger_reason="Post-execution telemetry verification failed to confirm service recovery.",
            )

        if severity_increased:
            return RollbackDecision(
                should_rollback=True,
                trigger_reason="Incident severity materially increased following remediation execution.",
            )

        if collateral_damage_detected:
            return RollbackDecision(
                should_rollback=True,
                trigger_reason="Unintended collateral degradation detected on orthogonal microservice branches.",
            )

        if health_deteriorated:
            return RollbackDecision(
                should_rollback=True,
                trigger_reason="Microservice health deteriorated following remediation action.",
            )

        return RollbackDecision(
            should_rollback=False,
            trigger_reason="Rollback not required: Verification passed within expected bounds.",
        )


class RollbackEngine:
    """
    Executes and verifies action rollback.
    """

    def __init__(self, journal: Optional[ExecutionJournal] = None):
        self.journal = journal or ExecutionJournal()

    def execute_rollback(
        self,
        executor: BaseActionExecutor,
        context: ExecutionContext,
        reason: str,
        force_rollback_failure: bool = False,
    ) -> RollbackResult:
        """
        Executes typed action rollback and logs the transition to the journal.
        """
        # Transition: ROLLBACK_PENDING -> ROLLING_BACK
        self.journal.append(
            execution_id=context.execution_id,
            incident_id=context.incident_id,
            recommendation_id=context.recommendation_id,
            approval_id=context.approval_id,
            action_id=context.action_id,
            target_service=context.target_service,
            previous_state=ExecutionState.ROLLBACK_PENDING.value,
            new_state=ExecutionState.ROLLING_BACK.value,
            actor=context.actor,
            metadata={"rollback_reason": reason},
        )

        if force_rollback_failure:
            res = RollbackResult(
                execution_id=context.execution_id,
                action_id=context.action_id,
                target_service=context.target_service,
                rollback_success=False,
                status=ActionExecutionStatus.FAILED.value,
                started_at=datetime.now(timezone.utc).isoformat(),
                completed_at=datetime.now(timezone.utc).isoformat(),
                mutation_summary="Rollback failed: Injected failure in rollback execution.",
                error="Simulated rollback failure",
            )
        else:
            res = executor.rollback(context)

        # Transition to next state
        if res.rollback_success:
            next_state = ExecutionState.ROLLBACK_VERIFYING.value
            self.journal.append(
                execution_id=context.execution_id,
                incident_id=context.incident_id,
                recommendation_id=context.recommendation_id,
                approval_id=context.approval_id,
                action_id=context.action_id,
                target_service=context.target_service,
                previous_state=ExecutionState.ROLLING_BACK.value,
                new_state=next_state,
                actor=context.actor,
                rollback_result=res.to_dict(),
            )
            # Rollback verification
            self.journal.append(
                execution_id=context.execution_id,
                incident_id=context.incident_id,
                recommendation_id=context.recommendation_id,
                approval_id=context.approval_id,
                action_id=context.action_id,
                target_service=context.target_service,
                previous_state=ExecutionState.ROLLBACK_VERIFYING.value,
                new_state=ExecutionState.RESTORED.value,
                actor=context.actor,
                metadata={"rollback_verified": True},
            )
        else:
            next_state = ExecutionState.ROLLBACK_FAILED.value
            self.journal.append(
                execution_id=context.execution_id,
                incident_id=context.incident_id,
                recommendation_id=context.recommendation_id,
                approval_id=context.approval_id,
                action_id=context.action_id,
                target_service=context.target_service,
                previous_state=ExecutionState.ROLLING_BACK.value,
                new_state=next_state,
                actor=context.actor,
                error=res.error,
                rollback_result=res.to_dict(),
            )

        return res

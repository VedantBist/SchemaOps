"""
Test Suite for CausalOps Remediation Rollback Engine (Phase 5).

Validates:
1. Rollback policy triggers on verification failure, severity increase, or collateral damage
2. Strict limit of maximum ONE rollback attempt per incident/action
3. Successful rollback execution and transition to RESTORED
4. Rollback failure transition to ROLLBACK_FAILED
"""

import sys
from pathlib import Path
from datetime import datetime, timezone
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.execution.actions import (
    ActionExecutorRegistry,
    ExecutionContext,
    PreExecutionSnapshot,
    ExecutionEnvironment,
)
from ml.execution.audit import ExecutionJournal, ExecutionState
from ml.execution.rollback import RollbackEngine, RollbackPolicy


@pytest.fixture
def journal(tmp_path):
    return ExecutionJournal(journal_path=tmp_path / "test_journal.jsonl")


@pytest.fixture
def rollback_engine(journal):
    return RollbackEngine(journal=journal)


@pytest.fixture
def sample_context():
    snap = PreExecutionSnapshot(
        snapshot_id="SNAP-001",
        incident_id="EXP-015",
        action_id="ACT-DB-01",
        target_service="inventory-db",
        captured_at=datetime.now(timezone.utc).isoformat(),
        service_health={"inventory-db": "degraded"},
        metrics={"inventory-db.db_latency": 1500.0},
        causal_variables={"inventory-db.db_latency": 1500.0},
        gateway_p99_latency_ms=280.0,
        gateway_error_rate_pct=0.0,
        active_faults_count=1,
    )
    return ExecutionContext(
        execution_id="EXEC-TEST-001",
        incident_id="EXP-015",
        recommendation_id="REC-001",
        approval_id="APP-001",
        action_id="ACT-DB-01",
        target_service="inventory-db",
        target_variable="db_latency",
        pre_execution_snapshot=snap,
        environment=ExecutionEnvironment.LOCAL.value,
    )


def test_rollback_policy_evaluation():
    # 1. Triggered on verification failure
    d1 = RollbackPolicy.evaluate(
        verification_passed=False,
        severity_increased=False,
        collateral_damage_detected=False,
        health_deteriorated=False,
        rollback_attempts_count=0,
    )
    assert d1.should_rollback is True
    assert "verification failed" in d1.trigger_reason.lower()

    # 2. Triggered on severity increase
    d2 = RollbackPolicy.evaluate(
        verification_passed=True,
        severity_increased=True,
        collateral_damage_detected=False,
        health_deteriorated=False,
        rollback_attempts_count=0,
    )
    assert d2.should_rollback is True
    assert "severity materially increased" in d2.trigger_reason.lower()

    # 3. Maximum 1 attempt enforced
    d3 = RollbackPolicy.evaluate(
        verification_passed=False,
        severity_increased=False,
        collateral_damage_detected=False,
        health_deteriorated=False,
        rollback_attempts_count=1,
    )
    assert d3.should_rollback is False
    assert "limit exceeded" in d3.trigger_reason.lower()


def test_rollback_execution_success(rollback_engine, sample_context, journal):
    registry = ActionExecutorRegistry()
    executor = registry.get_executor("ACT-DB-01")

    # Seed initial journal state
    journal.append(
        execution_id=sample_context.execution_id,
        incident_id=sample_context.incident_id,
        recommendation_id=sample_context.recommendation_id,
        approval_id=sample_context.approval_id,
        action_id=sample_context.action_id,
        target_service=sample_context.target_service,
        previous_state=ExecutionState.VERIFICATION_FAILED.value,
        new_state=ExecutionState.ROLLBACK_PENDING.value,
    )

    result = rollback_engine.execute_rollback(
        executor=executor,
        context=sample_context,
        reason="Verification failure",
        force_rollback_failure=False,
    )

    assert result.rollback_success is True
    timeline = journal.get_timeline(sample_context.execution_id)
    states = [t["new_state"] for t in timeline]
    assert ExecutionState.ROLLING_BACK.value in states
    assert ExecutionState.RESTORED.value in states


def test_rollback_execution_failure(rollback_engine, sample_context, journal):
    registry = ActionExecutorRegistry()
    executor = registry.get_executor("ACT-DB-01")

    # Seed initial journal state
    journal.append(
        execution_id=sample_context.execution_id,
        incident_id=sample_context.incident_id,
        recommendation_id=sample_context.recommendation_id,
        approval_id=sample_context.approval_id,
        action_id=sample_context.action_id,
        target_service=sample_context.target_service,
        previous_state=ExecutionState.VERIFICATION_FAILED.value,
        new_state=ExecutionState.ROLLBACK_PENDING.value,
    )

    result = rollback_engine.execute_rollback(
        executor=executor,
        context=sample_context,
        reason="Verification failure",
        force_rollback_failure=True,  # Simulate failure during rollback
    )

    assert result.rollback_success is False
    timeline = journal.get_timeline(sample_context.execution_id)
    states = [t["new_state"] for t in timeline]
    assert ExecutionState.ROLLBACK_FAILED.value in states

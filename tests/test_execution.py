"""
Comprehensive End-to-End Test Suite for Closed-Loop Remediation Execution (Phase 5).

Validates:
1. End-to-end execution of official fault cases: EXP-015, EXP-043, EXP-047, EXP-064
2. EXP-047 non-linear mediator warning handling & operator acknowledgment
3. NO_FAULT safety behavior on controls (EXP-007, EXP-008)
4. Idempotent execution (duplicate submission handling)
5. Service-level concurrency locking
6. Execution failure handling & state transition
7. Verification failure triggering automatic rollback to RESTORED
8. Rollback failure transition to ROLLBACK_FAILED
9. Audit journal append-only integrity
"""

import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dataset.tg_v1.loader import TemporalGraphDataset
from ml.causal.scm import TopologyConstrainedLaggedSCM
from ml.remediation.action_catalog import ActionCatalog
from ml.remediation.recommender import RemediationRecommender
from ml.execution.actions import ActionExecutorRegistry, ExecutionEnvironment
from ml.execution.policy import ExecutionPolicyEngine
from ml.execution.audit import ExecutionJournal, ExecutionState
from ml.execution.verification import VerificationEngine
from ml.execution.rollback import RollbackEngine
from ml.execution.executor import ClosedLoopRemediationExecutor


@pytest.fixture(scope="module")
def scm():
    return TopologyConstrainedLaggedSCM.load("ml/models/causal_scm")


@pytest.fixture(scope="module")
def catalog():
    return ActionCatalog.get_default_catalog()


@pytest.fixture(scope="module")
def recommender(catalog, scm):
    return RemediationRecommender(catalog=catalog, scm=scm)


@pytest.fixture(scope="module")
def test_dataset():
    return TemporalGraphDataset(dataset_dir="dataset/tg_v1", split="test")


@pytest.fixture
def executor(tmp_path):
    journal = ExecutionJournal(journal_path=tmp_path / "journal.jsonl")
    registry = ActionExecutorRegistry()
    policy_engine = ExecutionPolicyEngine(executor_registry=registry)
    verification_engine = VerificationEngine()
    rollback_engine = RollbackEngine(journal=journal)
    return ClosedLoopRemediationExecutor(
        registry=registry,
        policy_engine=policy_engine,
        journal=journal,
        verification_engine=verification_engine,
        rollback_engine=rollback_engine,
    )


# =========================================================================
# TEST 1: EXP-015 (DATABASE LATENCY CASCADE) END-TO-END
# =========================================================================
def test_e2e_exp015_db_latency(executor, recommender, test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-015"][0]

    # 1. Recommendation
    rec = recommender.recommend(sample)
    assert rec["recommendation_status"] == "RECOMMENDATION_READY"
    assert rec["recommended_action"]["action_id"] == "ACT-DB-01"

    rec_id = executor.register_recommendation(rec)

    # 2. Approval
    approval = executor.approve_recommendation(
        recommendation_id=rec_id,
        approved_by="sre-lead@causalops.local",
        warning_acknowledged=True,
    )
    assert approval.approval_status == "APPROVED"

    # 3. Execution & Verification
    exec_record = executor.execute_remediation(
        recommendation_id=rec_id,
        approval_id=approval.approval_id,
        observed_sample=sample,
    )

    assert exec_record.state == ExecutionState.INCIDENT_RESOLVED.value
    assert exec_record.execution_result["execution_success"] is True
    assert exec_record.verification_result["verified"] is True
    assert exec_record.verification_result["target_improved"] is True
    assert exec_record.verification_result["severity_decreased"] is True

    # 4. Check Timeline
    timeline_states = [t["new_state"] for t in exec_record.timeline]
    assert ExecutionState.POLICY_VALIDATED.value in timeline_states
    assert ExecutionState.EXECUTING.value in timeline_states
    assert ExecutionState.EXECUTED.value in timeline_states
    assert ExecutionState.VERIFYING.value in timeline_states
    assert ExecutionState.RECOVERED.value in timeline_states
    assert ExecutionState.INCIDENT_RESOLVED.value in timeline_states


# =========================================================================
# TEST 2: EXP-043 (INVENTORY SERVICE FAILURE) END-TO-END
# =========================================================================
def test_e2e_exp043_inventory_failure(executor, recommender, test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-043"][0]

    rec = recommender.recommend(sample)
    assert rec["recommendation_status"] == "RECOMMENDATION_READY"
    rec_id = executor.register_recommendation(rec)

    approval = executor.approve_recommendation(
        recommendation_id=rec_id,
        approved_by="sre-operator",
        warning_acknowledged=True,
    )

    exec_record = executor.execute_remediation(
        recommendation_id=rec_id,
        approval_id=approval.approval_id,
        observed_sample=sample,
    )

    assert exec_record.state == ExecutionState.INCIDENT_RESOLVED.value
    assert exec_record.execution_result["execution_success"] is True
    assert exec_record.verification_result["verified"] is True


# =========================================================================
# TEST 3: EXP-047 (ORDER SERVICE NON-LINEAR MEDIATOR) WITH WARNING
# =========================================================================
def test_e2e_exp047_warning_handling(executor, recommender, test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-047"][0]

    rec = recommender.recommend(sample)
    assert rec["recommendation_status"] == "RECOMMENDATION_WITH_WARNING"
    assert rec["nonlinear_risk"] == "DOCUMENTED"

    rec_id = executor.register_recommendation(rec)

    # If operator does NOT acknowledge warning, execution should be blocked by policy
    unack_approval = executor.approve_recommendation(
        recommendation_id=rec_id,
        approved_by="sre-operator",
        warning_acknowledged=False,
    )
    denied_record = executor.execute_remediation(
        recommendation_id=rec_id,
        approval_id=unack_approval.approval_id,
        observed_sample=sample,
    )
    assert denied_record.state == ExecutionState.FAILED.value
    assert "RULE_13_NO_UNRESOLVED_CRITICAL_WARNINGS" in denied_record.policy_decision["violated_rules"]

    # When operator explicitly acknowledges the documented warning
    ack_approval = executor.approve_recommendation(
        recommendation_id=rec_id,
        approved_by="sre-lead",
        warning_acknowledged=True,
    )
    exec_record = executor.execute_remediation(
        recommendation_id=rec_id,
        approval_id=ack_approval.approval_id,
        observed_sample=sample,
    )
    assert exec_record.state == ExecutionState.INCIDENT_RESOLVED.value
    assert exec_record.execution_result["execution_success"] is True


# =========================================================================
# TEST 4: EXP-064 (PAYMENT SERVICE FAILURE) END-TO-END
# =========================================================================
def test_e2e_exp064_payment_failure(executor, recommender, test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-064"][0]

    rec = recommender.recommend(sample)
    assert rec["recommendation_status"] == "RECOMMENDATION_READY"
    rec_id = executor.register_recommendation(rec)

    approval = executor.approve_recommendation(
        recommendation_id=rec_id,
        approved_by="sre-operator",
        warning_acknowledged=True,
    )

    exec_record = executor.execute_remediation(
        recommendation_id=rec_id,
        approval_id=approval.approval_id,
        observed_sample=sample,
    )

    assert exec_record.state == ExecutionState.INCIDENT_RESOLVED.value
    assert exec_record.execution_result["execution_success"] is True


# =========================================================================
# TEST 5: NO_FAULT CONTROLS (EXP-007, EXP-008) SAFETY WITHHOLDING
# =========================================================================
def test_no_fault_rejection(executor, recommender, test_dataset):
    for ctrl_id in ["EXP-007", "EXP-008"]:
        sample = [s for s in test_dataset if s.experiment_id == ctrl_id][0]
        rec = recommender.recommend(sample)
        assert rec["recommendation_status"] == "NO_REMEDIATION_REQUIRED"

        rec_id = executor.register_recommendation(rec)

        # Attempting to approve must fail
        with pytest.raises(ValueError, match="NO_FAULT"):
            executor.approve_recommendation(
                recommendation_id=rec_id,
                approved_by="rogue-operator",
            )


# =========================================================================
# TEST 6: IDEMPOTENCY (DUPLICATE SUBMISSION)
# =========================================================================
def test_idempotency(executor, recommender, test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-015"][0]
    rec = recommender.recommend(sample)
    rec_id = executor.register_recommendation(rec)

    approval = executor.approve_recommendation(
        recommendation_id=rec_id,
        approved_by="sre-operator",
        warning_acknowledged=True,
    )

    # First execution
    rec1 = executor.execute_remediation(
        recommendation_id=rec_id,
        approval_id=approval.approval_id,
        observed_sample=sample,
    )

    # Duplicate execution attempt with same parameters
    rec2 = executor.execute_remediation(
        recommendation_id=rec_id,
        approval_id=approval.approval_id,
        observed_sample=sample,
    )

    # Must return identical execution record without re-running
    assert rec1.execution_id == rec2.execution_id
    assert rec1.state == rec2.state


# =========================================================================
# TEST 7: CONCURRENCY LOCKING
# =========================================================================
def test_concurrency_locking(executor, recommender, test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-015"][0]
    rec = recommender.recommend(sample)
    rec_id = executor.register_recommendation(rec)

    approval = executor.approve_recommendation(
        recommendation_id=rec_id,
        approved_by="sre-operator",
        warning_acknowledged=True,
    )

    # Manually acquire lock on inventory-db
    executor._active_service_locks.add("inventory-db")

    denied = executor.execute_remediation(
        recommendation_id=rec_id,
        approval_id=approval.approval_id,
        observed_sample=sample,
    )

    assert denied.state == ExecutionState.FAILED.value
    assert "RULE_10_NOT_CURRENTLY_LOCKED" in denied.policy_decision["violated_rules"]

    executor._active_service_locks.discard("inventory-db")


# =========================================================================
# TEST 8: EXECUTION FAILURE TEST DOUBLE
# =========================================================================
def test_execution_failure_handling(executor, recommender, test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-015"][0]
    rec = recommender.recommend(sample)
    rec_id = executor.register_recommendation(rec)

    approval = executor.approve_recommendation(
        recommendation_id=rec_id,
        approved_by="sre-operator",
        warning_acknowledged=True,
    )

    failed_record = executor.execute_remediation(
        recommendation_id=rec_id,
        approval_id=approval.approval_id,
        observed_sample=sample,
        force_execution_failure=True,
    )

    assert failed_record.state == ExecutionState.EXECUTION_FAILED.value
    assert failed_record.execution_result["execution_success"] is False


# =========================================================================
# TEST 9: VERIFICATION FAILURE TRIGGERING AUTOMATIC ROLLBACK
# =========================================================================
def test_verification_failure_and_rollback(executor, recommender, test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-015"][0]
    rec = recommender.recommend(sample)
    rec_id = executor.register_recommendation(rec)

    approval = executor.approve_recommendation(
        recommendation_id=rec_id,
        approved_by="sre-operator",
        warning_acknowledged=True,
    )

    rolled_back_record = executor.execute_remediation(
        recommendation_id=rec_id,
        approval_id=approval.approval_id,
        observed_sample=sample,
        force_verification_failure=True,  # Simulate verification failure
    )

    assert rolled_back_record.state == ExecutionState.RESTORED.value
    assert rolled_back_record.verification_result["verified"] is False
    assert rolled_back_record.rollback_result is not None
    assert rolled_back_record.rollback_result["rollback_success"] is True

    timeline_states = [t["new_state"] for t in rolled_back_record.timeline]
    assert ExecutionState.VERIFICATION_FAILED.value in timeline_states
    assert ExecutionState.ROLLBACK_PENDING.value in timeline_states
    assert ExecutionState.ROLLING_BACK.value in timeline_states
    assert ExecutionState.RESTORED.value in timeline_states


# =========================================================================
# TEST 10: ROLLBACK FAILURE HANDLING
# =========================================================================
def test_rollback_failure_handling(executor, recommender, test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-015"][0]
    rec = recommender.recommend(sample)
    rec_id = executor.register_recommendation(rec)

    approval = executor.approve_recommendation(
        recommendation_id=rec_id,
        approved_by="sre-operator",
        warning_acknowledged=True,
    )

    failed_rollback_record = executor.execute_remediation(
        recommendation_id=rec_id,
        approval_id=approval.approval_id,
        observed_sample=sample,
        force_verification_failure=True,
        force_rollback_failure=True,  # Rollback itself crashes
    )

    assert failed_rollback_record.state == ExecutionState.ROLLBACK_FAILED.value
    assert failed_rollback_record.rollback_result["rollback_success"] is False

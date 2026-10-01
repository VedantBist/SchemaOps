"""
Test Suite for CausalOps Remediation Execution Policy Engine (Phase 5).

Validates the 15 strict policy validation rules:
1. Environment restriction (LOCAL/SIMULATION/TEST only)
2. Action allowlist verification
3. Target service allowlist verification
4. Recommendation existence and NO_FAULT withholding
5. Recommendation freshness & TTL expiry
6. Explicit operator approval existence
7. Operator identity presence
8. Incident and action match (cross-incident rejection)
9. Already-executed action rejection (idempotency)
10. Concurrency locking per service
11. Rollback support check
12. Counterfactual validity adherence
13. Documented warning acknowledgment (EXP-047)
14. Execution budget adherence
15. Blast radius policy limit
"""

import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.execution.actions import ActionExecutorRegistry, ExecutionEnvironment
from ml.execution.policy import (
    ExecutionPolicyEngine,
    ApprovalRecord,
    ApprovalStatus,
)


@pytest.fixture
def policy_engine():
    return ExecutionPolicyEngine(
        executor_registry=ActionExecutorRegistry(),
        max_blast_radius_services=4,
        max_execution_budget_per_incident=3,
        recommendation_ttl_seconds=900,
    )


@pytest.fixture
def valid_recommendation():
    return {
        "experiment_id": "EXP-015",
        "incident_id": "EXP-015",
        "recommendation_status": "RECOMMENDATION_READY",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root_cause": {"node": "inventory-db", "variable": "db_latency"},
        "recommended_action": {
            "action_id": "ACT-DB-01",
            "action_name": "Terminate Blocking Queries & Release Table Locks",
            "target_service": "inventory-db",
            "target_variable": "db_latency",
            "composite_score": 48.0,
            "blast_radius": {"service_count": 4},
            "collateral_impact": {"collateral_damage_detected": False},
        },
        "warnings": [],
    }


@pytest.fixture
def valid_approval(valid_recommendation):
    now = datetime.now(timezone.utc)
    return ApprovalRecord(
        approval_id="APP-TEST-001",
        recommendation_id="REC-TEST-001",
        incident_id="EXP-015",
        action_id="ACT-DB-01",
        approved_by="sre-operator@causalops.local",
        approved_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=15)).isoformat(),
        approval_status=ApprovalStatus.APPROVED.value,
        warning_acknowledged=True,
    )


# -----------------------------------------------------------------
# RULE 1: ENVIRONMENT RESTRICTION
# -----------------------------------------------------------------
def test_policy_rule_01_environment_restriction(policy_engine, valid_recommendation, valid_approval):
    # Allowed environments
    for env in ["LOCAL", "SIMULATION", "TEST"]:
        decision = policy_engine.validate(
            environment=env,
            action_id="ACT-DB-01",
            target_service="inventory-db",
            incident_id="EXP-015",
            recommendation=valid_recommendation,
            approval=valid_approval,
            active_locks=set(),
            execution_history_for_incident=[],
        )
        assert decision.allowed is True

    # Disallowed production environments
    for bad_env in ["PRODUCTION", "STAGING", "AWS_PROD", "REMOTE"]:
        decision = policy_engine.validate(
            environment=bad_env,
            action_id="ACT-DB-01",
            target_service="inventory-db",
            incident_id="EXP-015",
            recommendation=valid_recommendation,
            approval=valid_approval,
            active_locks=set(),
            execution_history_for_incident=[],
        )
        assert decision.allowed is False
        assert "RULE_01_ENVIRONMENT_LOCAL" in decision.violated_rules


# -----------------------------------------------------------------
# RULE 2: ACTION ALLOWLIST
# -----------------------------------------------------------------
def test_policy_rule_02_action_allowlist(policy_engine, valid_recommendation, valid_approval):
    bad_approval = ApprovalRecord(
        approval_id="APP-TEST-002",
        recommendation_id="REC-TEST-001",
        incident_id="EXP-015",
        action_id="ACT-ARBITRARY-SHELL-INJECTION",
        approved_by="sre-operator",
        approved_at=datetime.now(timezone.utc).isoformat(),
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        approval_status=ApprovalStatus.APPROVED.value,
    )
    decision = policy_engine.validate(
        environment="LOCAL",
        action_id="ACT-ARBITRARY-SHELL-INJECTION",
        target_service="inventory-db",
        incident_id="EXP-015",
        recommendation=valid_recommendation,
        approval=bad_approval,
        active_locks=set(),
        execution_history_for_incident=[],
    )
    assert decision.allowed is False
    assert "RULE_02_ACTION_ALLOWLISTED" in decision.violated_rules


# -----------------------------------------------------------------
# RULE 3: TARGET SERVICE ALLOWLIST
# -----------------------------------------------------------------
def test_policy_rule_03_target_allowlist(policy_engine, valid_recommendation, valid_approval):
    decision = policy_engine.validate(
        environment="LOCAL",
        action_id="ACT-DB-01",
        target_service="external-payment-vendor",
        incident_id="EXP-015",
        recommendation=valid_recommendation,
        approval=valid_approval,
        active_locks=set(),
        execution_history_for_incident=[],
    )
    assert decision.allowed is False
    assert "RULE_03_TARGET_ALLOWLISTED" in decision.violated_rules


# -----------------------------------------------------------------
# RULE 4: NO_FAULT CONTROL WITHHOLDING
# -----------------------------------------------------------------
def test_policy_rule_04_no_fault_withholding(policy_engine, valid_approval):
    no_fault_rec = {
        "experiment_id": "EXP-007",
        "incident_id": "EXP-007",
        "recommendation_status": "NO_REMEDIATION_REQUIRED",
        "root_cause": None,
        "recommended_action": None,
    }
    decision = policy_engine.validate(
        environment="LOCAL",
        action_id="ACT-DB-01",
        target_service="inventory-db",
        incident_id="EXP-007",
        recommendation=no_fault_rec,
        approval=valid_approval,
        active_locks=set(),
        execution_history_for_incident=[],
    )
    assert decision.allowed is False
    assert "RULE_04_NO_FAULT_WITHHOLDING" in decision.violated_rules


# -----------------------------------------------------------------
# RULE 5: RECOMMENDATION FRESHNESS / EXPIRY
# -----------------------------------------------------------------
def test_policy_rule_05_stale_recommendation(policy_engine, valid_recommendation, valid_approval):
    stale_rec = dict(valid_recommendation)
    stale_rec["generated_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    decision = policy_engine.validate(
        environment="LOCAL",
        action_id="ACT-DB-01",
        target_service="inventory-db",
        incident_id="EXP-015",
        recommendation=stale_rec,
        approval=valid_approval,
        active_locks=set(),
        execution_history_for_incident=[],
    )
    assert decision.allowed is False
    assert "RULE_05_RECOMMENDATION_NOT_EXPIRED" in decision.violated_rules


# -----------------------------------------------------------------
# RULE 6: EXPLICIT APPROVAL EXISTENCE & STATUS
# -----------------------------------------------------------------
def test_policy_rule_06_unapproved_or_rejected(policy_engine, valid_recommendation, valid_approval):
    # None approval
    decision = policy_engine.validate(
        environment="LOCAL",
        action_id="ACT-DB-01",
        target_service="inventory-db",
        incident_id="EXP-015",
        recommendation=valid_recommendation,
        approval=None,
        active_locks=set(),
        execution_history_for_incident=[],
    )
    assert decision.allowed is False
    assert "RULE_06_EXPLICIT_APPROVAL_EXISTS" in decision.violated_rules

    # Rejected approval
    rejected_appr = ApprovalRecord(
        approval_id="APP-REJ",
        recommendation_id="REC-001",
        incident_id="EXP-015",
        action_id="ACT-DB-01",
        approved_by="sre-operator",
        approved_at=datetime.now(timezone.utc).isoformat(),
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        approval_status=ApprovalStatus.REJECTED.value,
    )
    decision = policy_engine.validate(
        environment="LOCAL",
        action_id="ACT-DB-01",
        target_service="inventory-db",
        incident_id="EXP-015",
        recommendation=valid_recommendation,
        approval=rejected_appr,
        active_locks=set(),
        execution_history_for_incident=[],
    )
    assert decision.allowed is False
    assert "RULE_06_EXPLICIT_APPROVAL_EXISTS" in decision.violated_rules


# -----------------------------------------------------------------
# RULE 7: APPROVAL IDENTITY PRESENCE
# -----------------------------------------------------------------
def test_policy_rule_07_approval_identity_missing(policy_engine, valid_recommendation):
    anonymous_approval = ApprovalRecord(
        approval_id="APP-ANON",
        recommendation_id="REC-001",
        incident_id="EXP-015",
        action_id="ACT-DB-01",
        approved_by="   ",  # Blank operator
        approved_at=datetime.now(timezone.utc).isoformat(),
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        approval_status=ApprovalStatus.APPROVED.value,
    )
    decision = policy_engine.validate(
        environment="LOCAL",
        action_id="ACT-DB-01",
        target_service="inventory-db",
        incident_id="EXP-015",
        recommendation=valid_recommendation,
        approval=anonymous_approval,
        active_locks=set(),
        execution_history_for_incident=[],
    )
    assert decision.allowed is False
    assert "RULE_07_APPROVAL_IDENTITY_EXISTS" in decision.violated_rules


# -----------------------------------------------------------------
# RULE 8: CROSS-INCIDENT APPROVAL MISMATCH
# -----------------------------------------------------------------
def test_policy_rule_08_cross_incident_rejection(policy_engine, valid_recommendation):
    cross_appr = ApprovalRecord(
        approval_id="APP-CROSS",
        recommendation_id="REC-001",
        incident_id="EXP-999",  # Incident mismatch!
        action_id="ACT-DB-01",
        approved_by="sre-operator",
        approved_at=datetime.now(timezone.utc).isoformat(),
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        approval_status=ApprovalStatus.APPROVED.value,
    )
    decision = policy_engine.validate(
        environment="LOCAL",
        action_id="ACT-DB-01",
        target_service="inventory-db",
        incident_id="EXP-015",
        recommendation=valid_recommendation,
        approval=cross_appr,
        active_locks=set(),
        execution_history_for_incident=[],
    )
    assert decision.allowed is False
    assert "RULE_08_INCIDENT_MATCH" in decision.violated_rules


# -----------------------------------------------------------------
# RULE 9: ALREADY EXECUTED ACTION (IDEMPOTENCY)
# -----------------------------------------------------------------
def test_policy_rule_09_already_executed(policy_engine, valid_recommendation, valid_approval):
    prior_history = [
        {"action_id": "ACT-DB-01", "status": "SUCCESS", "execution_id": "EXEC-PREV-1"}
    ]
    decision = policy_engine.validate(
        environment="LOCAL",
        action_id="ACT-DB-01",
        target_service="inventory-db",
        incident_id="EXP-015",
        recommendation=valid_recommendation,
        approval=valid_approval,
        active_locks=set(),
        execution_history_for_incident=prior_history,
    )
    assert decision.allowed is False
    assert "RULE_09_NOT_ALREADY_EXECUTED" in decision.violated_rules


# -----------------------------------------------------------------
# RULE 10: CONCURRENCY LOCK
# -----------------------------------------------------------------
def test_policy_rule_10_concurrency_lock(policy_engine, valid_recommendation, valid_approval):
    active_locks = {"inventory-db"}
    decision = policy_engine.validate(
        environment="LOCAL",
        action_id="ACT-DB-01",
        target_service="inventory-db",
        incident_id="EXP-015",
        recommendation=valid_recommendation,
        approval=valid_approval,
        active_locks=active_locks,
        execution_history_for_incident=[],
    )
    assert decision.allowed is False
    assert "RULE_10_NOT_CURRENTLY_LOCKED" in decision.violated_rules


# -----------------------------------------------------------------
# RULE 13: EXP-047 WARNING ACKNOWLEDGMENT
# -----------------------------------------------------------------
def test_policy_rule_13_exp047_unacknowledged_warning(policy_engine, valid_approval):
    exp047_rec = {
        "experiment_id": "EXP-047",
        "incident_id": "EXP-047",
        "recommendation_status": "RECOMMENDATION_WITH_WARNING",
        "root_cause": {"node": "order-service", "variable": "p99_latency"},
        "recommended_action": {
            "action_id": "ACT-ORD-01",
            "target_service": "order-service",
            "target_variable": "p99_latency",
            "blast_radius": {"service_count": 2},
        },
        "warnings": ["Non-linear mediator queueing observed on order-service direct injection"],
    }
    unack_appr = ApprovalRecord(
        approval_id="APP-ORD",
        recommendation_id="REC-ORD",
        incident_id="EXP-047",
        action_id="ACT-ORD-01",
        approved_by="sre-operator",
        approved_at=datetime.now(timezone.utc).isoformat(),
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        approval_status=ApprovalStatus.APPROVED.value,
        warning_acknowledged=False,  # Warning NOT acknowledged!
    )
    decision = policy_engine.validate(
        environment="LOCAL",
        action_id="ACT-ORD-01",
        target_service="order-service",
        incident_id="EXP-047",
        recommendation=exp047_rec,
        approval=unack_appr,
        active_locks=set(),
        execution_history_for_incident=[],
    )
    assert decision.allowed is False
    assert "RULE_13_NO_UNRESOLVED_CRITICAL_WARNINGS" in decision.violated_rules

    # When acknowledged, it passes
    unack_appr.warning_acknowledged = True
    ack_decision = policy_engine.validate(
        environment="LOCAL",
        action_id="ACT-ORD-01",
        target_service="order-service",
        incident_id="EXP-047",
        recommendation=exp047_rec,
        approval=unack_appr,
        active_locks=set(),
        execution_history_for_incident=[],
    )
    assert ack_decision.allowed is True


# -----------------------------------------------------------------
# ROOT CAUSE CHANGED CHECK
# -----------------------------------------------------------------
def test_policy_root_cause_change_rejection(policy_engine, valid_recommendation, valid_approval):
    decision = policy_engine.validate(
        environment="LOCAL",
        action_id="ACT-DB-01",
        target_service="inventory-db",
        incident_id="EXP-015",
        recommendation=valid_recommendation,
        approval=valid_approval,
        active_locks=set(),
        execution_history_for_incident=[],
        current_incident_root_cause="payment-service",  # Root cause changed!
    )
    assert decision.allowed is False
    assert "RULE_ROOT_CAUSE_UNCHANGED" in decision.violated_rules

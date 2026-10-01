"""
Test Suite for Remediation Conflict Detection Engine (Phase 6).

Validates:
1. Target Service Collision: Two remediations targeting the same service are blocked.
2. Active Rollback Collision: Concurrent action during active rollback is blocked.
3. Variable Dependency Collision: Mutual interference between variables is blocked.
4. Blast Radius Budget Exceeded: Cumulative concurrent blast radius > 4 is blocked.
5. Non-conflicting independent actions evaluate to clean pass.
"""

import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.orchestration.conflict import ConflictDetector, ConflictType


def test_target_service_collision():
    """Confirms two actions targeting the same service are blocked."""
    active = [{
        "incident_id": "INC-01",
        "action_id": "ACT-DB-01",
        "target_service": "inventory-db",
        "target_variable": "db_latency",
        "blast_radius_size": 2,
    }]

    conflict = ConflictDetector.evaluate_conflict(
        candidate_incident_id="INC-02",
        candidate_action_id="ACT-DB-02",
        candidate_target_service="inventory-db",
        candidate_target_variable="db_latency",
        candidate_blast_radius_size=2,
        active_remediations=active,
    )

    assert conflict.has_conflict is True
    assert conflict.conflict_type == ConflictType.TARGET_SERVICE_COLLISION.value
    assert "inventory-db" in conflict.affected_resource
    assert "ACT-DB-02" in conflict.blocked_action_ids


def test_active_rollback_collision():
    """Confirms active rollback blocks concurrent action on overlapping service."""
    active = [{
        "incident_id": "INC-01",
        "action_id": "ACT-INV-01",
        "target_service": "inventory-service",
        "target_variable": "p99_latency",
        "is_rollback": True,
        "blast_radius_services": ["inventory-service", "order-service"],
    }]

    conflict = ConflictDetector.evaluate_conflict(
        candidate_incident_id="INC-02",
        candidate_action_id="ACT-ORD-01",
        candidate_target_service="order-service",
        candidate_target_variable="p99_latency",
        candidate_blast_radius_size=1,
        active_remediations=active,
    )

    assert conflict.has_conflict is True
    assert conflict.conflict_type == ConflictType.ACTIVE_ROLLBACK_COLLISION.value


def test_variable_dependency_collision():
    """Confirms mutual interference between dependent variables is blocked."""
    active = [{
        "incident_id": "INC-01",
        "action_id": "ACT-INV-03",
        "target_service": "inventory-service",
        "target_variable": "error_rate",
        "blast_radius_size": 1,
    }]

    conflict = ConflictDetector.evaluate_conflict(
        candidate_incident_id="INC-02",
        candidate_action_id="ACT-ORD-01",
        candidate_target_service="order-service",
        candidate_target_variable="p99_latency",
        candidate_blast_radius_size=1,
        active_remediations=active,
    )

    assert conflict.has_conflict is True
    assert conflict.conflict_type == ConflictType.VARIABLE_DEPENDENCY_COLLISION.value


def test_blast_radius_budget_exceeded():
    """Confirms cumulative blast radius > 4 services is blocked."""
    active = [{
        "incident_id": "INC-01",
        "action_id": "ACT-DB-01",
        "target_service": "inventory-db",
        "target_variable": "db_latency",
        "blast_radius_size": 3,
    }]

    conflict = ConflictDetector.evaluate_conflict(
        candidate_incident_id="INC-02",
        candidate_action_id="ACT-PAY-01",
        candidate_target_service="payment-service",
        candidate_target_variable="error_rate",
        candidate_blast_radius_size=2,  # 3 + 2 = 5 > 4
        active_remediations=active,
    )

    assert conflict.has_conflict is True
    assert conflict.conflict_type == ConflictType.BLAST_RADIUS_BUDGET_EXCEEDED.value


def test_non_conflicting_actions_pass():
    """Confirms independent, non-interfering actions evaluate to clean pass."""
    active = [{
        "incident_id": "INC-01",
        "action_id": "ACT-DB-01",
        "target_service": "inventory-db",
        "target_variable": "db_latency",
        "blast_radius_size": 2,
    }]

    conflict = ConflictDetector.evaluate_conflict(
        candidate_incident_id="INC-02",
        candidate_action_id="ACT-PAY-01",
        candidate_target_service="payment-service",
        candidate_target_variable="error_rate",
        candidate_blast_radius_size=1,  # 2 + 1 = 3 <= 4
        active_remediations=active,
    )

    assert conflict.has_conflict is False
    assert conflict.conflict_type is None

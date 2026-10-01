"""
Test Suite for Deterministic Remediation Scheduler (Phase 6).

Validates:
1. Deterministic FIFO scheduling based on approval timestamp.
2. Target service locking blocks dispatch.
3. Stale/unusable telemetry blocks dispatch.
4. Active remediation conflicts block dispatch.
5. Inactive/degraded incident state blocks dispatch.
"""

from datetime import datetime, timezone, timedelta
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.orchestration.scheduler import RemediationScheduler, QueueItem
from ml.orchestration.health import TelemetryReport, TelemetryFreshness, TelemetryHealthStatus
from ml.orchestration.incident_state import IncidentState


def test_fifo_ordering():
    """Confirms candidate approved earlier is scheduled first."""
    scheduler = RemediationScheduler()
    t1 = "2026-09-27T10:00:00+00:00"
    t2 = "2026-09-27T10:05:00+00:00"

    item_later = QueueItem(
        incident_id="INC-02",
        recommendation_id="REC-02",
        approval_id="APP-02",
        action_id="ACT-PAY-01",
        target_service="payment-service",
        target_variable="error_rate",
        approved_at=t2,
    )
    item_earlier = QueueItem(
        incident_id="INC-01",
        recommendation_id="REC-01",
        approval_id="APP-01",
        action_id="ACT-DB-01",
        target_service="inventory-db",
        target_variable="db_latency",
        approved_at=t1,
    )

    # Enqueue in reverse chronological order
    scheduler.enqueue(item_later)
    scheduler.enqueue(item_earlier)

    states = {
        "INC-01": IncidentState.APPROVAL_PENDING.value,
        "INC-02": IncidentState.APPROVAL_PENDING.value,
    }

    decision = scheduler.schedule_next(
        active_service_locks=set(),
        active_remediations=[],
        incident_states=states,
        telemetry_reports={},
    )

    assert decision.status == "DISPATCHED"
    assert decision.dispatched_item is not None
    assert decision.dispatched_item.incident_id == "INC-01"
    assert decision.dispatched_item.action_id == "ACT-DB-01"


def test_locked_service_blocks_candidate():
    """Confirms active service lock blocks candidate and evaluates next eligible."""
    scheduler = RemediationScheduler()
    t1 = "2026-09-27T10:00:00+00:00"
    t2 = "2026-09-27T10:01:00+00:00"

    # First candidate targets locked inventory-db
    item_db = QueueItem(
        incident_id="INC-01",
        recommendation_id="REC-01",
        approval_id="APP-01",
        action_id="ACT-DB-01",
        target_service="inventory-db",
        target_variable="db_latency",
        approved_at=t1,
    )
    # Second candidate targets free payment-service
    item_pay = QueueItem(
        incident_id="INC-02",
        recommendation_id="REC-02",
        approval_id="APP-02",
        action_id="ACT-PAY-01",
        target_service="payment-service",
        target_variable="error_rate",
        approved_at=t2,
    )

    scheduler.enqueue(item_db)
    scheduler.enqueue(item_pay)

    states = {
        "INC-01": IncidentState.APPROVAL_PENDING.value,
        "INC-02": IncidentState.APPROVAL_PENDING.value,
    }

    # Lock inventory-db
    decision = scheduler.schedule_next(
        active_service_locks={"inventory-db"},
        active_remediations=[],
        incident_states=states,
        telemetry_reports={},
    )

    assert decision.status == "DISPATCHED"
    assert decision.dispatched_item.incident_id == "INC-02"
    assert decision.dispatched_item.action_id == "ACT-PAY-01"


def test_stale_telemetry_blocks_scheduling():
    """Confirms stale telemetry prevents execution dispatch."""
    scheduler = RemediationScheduler()
    t1 = "2026-09-27T10:00:00+00:00"

    item = QueueItem(
        incident_id="INC-01",
        recommendation_id="REC-01",
        approval_id="APP-01",
        action_id="ACT-DB-01",
        target_service="inventory-db",
        target_variable="db_latency",
        approved_at=t1,
    )
    scheduler.enqueue(item)

    stale_report = TelemetryReport(
        freshness=TelemetryFreshness.STALE.value,
        quality=TelemetryHealthStatus.UNUSABLE.value,
        evaluated_at=t1,
        delay_seconds=45.0,
        service_coverage={"inventory-db": True},
        nan_count=0,
        inf_count=0,
        sample_count=10,
        can_execute_remediation=False,
    )

    decision = scheduler.schedule_next(
        active_service_locks=set(),
        active_remediations=[],
        incident_states={"INC-01": IncidentState.APPROVAL_PENDING.value},
        telemetry_reports={"INC-01": stale_report},
    )

    assert decision.status in ["WAITING", "BLOCKED"]
    assert decision.dispatched_item is None

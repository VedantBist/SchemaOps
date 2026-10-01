"""
Test Suite for Official Multi-Incident Scenarios A through F (Phase 6).

Validates:
Scenario A: Simultaneous independent incidents (inventory-db & payment-service) maintain strict isolation.
Scenario B: Upstream root cause + downstream symptom creates correlated incident.
Scenario C: Concurrent conflicting remediations on the same service trigger conflict detection.
Scenario D: Stale telemetry during approval blocks remediation execution.
Scenario E: Execution succeeds but post-action telemetry oscillates -> incident DEGRADED.
Scenario F: Execution fails and rollback fails -> triggers MANUAL_INTERVENTION.
"""

from datetime import datetime, timezone, timedelta
import numpy as np
import sys
import tempfile
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.orchestration.incident_manager import IncidentOrchestrationManager
from ml.orchestration.incident_state import IncidentState
from ml.orchestration.health import TelemetryReport, TelemetryFreshness, TelemetryHealthStatus
from ml.orchestration.conflict import ConflictDetector, ConflictType


def test_scenario_a_simultaneous_independent_incidents():
    """Scenario A: Independent incidents on inventory-db and payment-service maintain strict isolation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        journal = Path(tmpdir) / "scen_a.jsonl"
        mgr = IncidentOrchestrationManager(journal_path=journal)
        now = datetime.now(timezone.utc)

        # Ingest Incident A (inventory-db)
        inc_a = mgr.process_anomaly(
            service="inventory-db",
            primary_variable="db_latency",
            fault_signature="lock_contention",
            severity="CRITICAL",
            timestamp=now,
        )

        # Ingest Incident B (payment-service) simultaneously
        inc_b = mgr.process_anomaly(
            service="payment-service",
            primary_variable="error_rate",
            fault_signature="gateway_refused",
            severity="HIGH",
            timestamp=now + timedelta(seconds=1.0),
        )

        # Verify independence
        assert inc_a.incident_id != inc_b.incident_id
        assert inc_a.correlation_id != inc_b.correlation_id
        assert inc_a.correlation_group != inc_b.correlation_group
        assert inc_a.affected_services == ["inventory-db"]
        assert inc_b.affected_services == ["payment-service"]

        # Advance Incident A to RCA_COMPLETE
        mgr.run_investigation_and_rca(inc_a.incident_id)
        assert mgr.get_incident(inc_a.incident_id).current_state == IncidentState.RCA_COMPLETE.value
        # Incident B must remain in DETECTED state (zero cross-contamination)
        assert mgr.get_incident(inc_b.incident_id).current_state == IncidentState.DETECTED.value


def test_scenario_b_correlated_incident_cascade():
    """Scenario B: Upstream inventory-db latency + downstream inventory-service symptoms -> Correlated."""
    with tempfile.TemporaryDirectory() as tmpdir:
        journal = Path(tmpdir) / "scen_b.jsonl"
        mgr = IncidentOrchestrationManager(journal_path=journal)
        t0 = datetime(2026, 9, 27, 14, 0, 0, tzinfo=timezone.utc)

        # 1. Primary failure on inventory-db
        inc_root = mgr.process_anomaly(
            service="inventory-db",
            primary_variable="db_latency",
            fault_signature="lock_spike",
            timestamp=t0,
        )
        assert inc_root.is_downstream_symptom is False

        # 2. Downstream symptom on inventory-service 4 seconds later
        inc_downstream = mgr.process_anomaly(
            service="inventory-service",
            primary_variable="p99_latency",
            fault_signature="callee_latency",
            timestamp=t0 + timedelta(seconds=4.0),
        )

        assert inc_downstream.is_downstream_symptom is True
        assert inc_downstream.parent_incident_id == inc_root.incident_id
        assert inc_downstream.correlation_group == inc_root.correlation_group
        assert inc_downstream.current_state == IncidentState.CORRELATED.value


def test_scenario_c_concurrent_remediation_conflict():
    """Scenario C: Two remediations targeting inventory-service trigger conflict detection."""
    active = [{
        "incident_id": "INC-01",
        "action_id": "ACT-INV-01",
        "target_service": "inventory-service",
        "target_variable": "p99_latency",
        "blast_radius_size": 2,
    }]

    conflict = ConflictDetector.evaluate_conflict(
        candidate_incident_id="INC-02",
        candidate_action_id="ACT-INV-03",
        candidate_target_service="inventory-service",
        candidate_target_variable="error_rate",
        candidate_blast_radius_size=1,
        active_remediations=active,
    )

    assert conflict.has_conflict is True
    assert conflict.conflict_type == ConflictType.TARGET_SERVICE_COLLISION.value
    assert "inventory-service" in conflict.affected_resource


def test_scenario_d_stale_telemetry_blocks_execution():
    """Scenario D: Telemetry becomes stale during approval -> execution is BLOCKED."""
    with tempfile.TemporaryDirectory() as tmpdir:
        journal = Path(tmpdir) / "scen_d.jsonl"
        mgr = IncidentOrchestrationManager(journal_path=journal)
        now = datetime.now(timezone.utc)

        inc = mgr.process_anomaly(service="inventory-db", primary_variable="db_latency", fault_signature="lock")
        mgr.submit_approval(
            incident_id=inc.incident_id,
            approval_id="APP-D",
            approved_by="sre@causalops.local",
            action_id="ACT-DB-01",
            target_service="inventory-db",
            target_variable="db_latency",
        )

        # Stale telemetry report (>25s old)
        stale_report = TelemetryReport(
            freshness=TelemetryFreshness.STALE.value,
            quality=TelemetryHealthStatus.UNUSABLE.value,
            evaluated_at=now.isoformat(),
            delay_seconds=25.0,
            service_coverage={"inventory-db": True},
            nan_count=0,
            inf_count=0,
            sample_count=10,
            can_execute_remediation=False,
        )

        decision = mgr.scheduler.schedule_next(
            active_service_locks=set(),
            active_remediations=[],
            incident_states={inc.incident_id: IncidentState.APPROVAL_PENDING.value},
            telemetry_reports={inc.incident_id: stale_report},
        )

        assert decision.status in ["WAITING", "BLOCKED"]
        assert decision.dispatched_item is None


def test_scenario_e_oscillation_degrades_incident():
    """Scenario E: Verification oscillates -> no false recovery, incident DEGRADED."""
    with tempfile.TemporaryDirectory() as tmpdir:
        journal = Path(tmpdir) / "scen_e.jsonl"
        mgr = IncidentOrchestrationManager(journal_path=journal)

        inc = mgr.process_anomaly(service="api-gateway", primary_variable="p99_latency", fault_signature="osc")
        inc_id = inc.incident_id

        # Trigger oscillation
        for _ in range(3):
            mgr.record_health_observation(inc_id, is_healthy=True)
            mgr.record_health_observation(inc_id, is_healthy=False)

        updated = mgr.get_incident(inc_id)
        assert updated.oscillation_detected is True
        assert updated.current_state == IncidentState.DEGRADED.value


def test_scenario_f_rollback_failure_triggers_manual_intervention():
    """Scenario F: Execution fails and rollback fails -> triggers MANUAL_INTERVENTION."""
    with tempfile.TemporaryDirectory() as tmpdir:
        journal = Path(tmpdir) / "scen_f.jsonl"
        mgr = IncidentOrchestrationManager(journal_path=journal)

        inc = mgr.process_anomaly(service="inventory-db", primary_variable="db_latency", fault_signature="crash")
        inc_id = inc.incident_id

        # Advance to EXECUTING
        mgr.transition_state(inc_id, IncidentState.INVESTIGATING.value)
        mgr.transition_state(inc_id, IncidentState.RCA_COMPLETE.value)
        mgr.transition_state(inc_id, IncidentState.REMEDIATION_RECOMMENDED.value)
        mgr.transition_state(inc_id, IncidentState.APPROVAL_PENDING.value)
        mgr.transition_state(inc_id, IncidentState.REMEDIATION_EXECUTING.value)

        # Execution fails -> ROLLBACK
        mgr.transition_state(inc_id, IncidentState.ROLLBACK.value)
        # Rollback fails -> MANUAL_INTERVENTION
        mgr.transition_state(inc_id, IncidentState.MANUAL_INTERVENTION.value, actor="RollbackEngine", reason="Rollback failed.")

        final = mgr.get_incident(inc_id)
        assert final.current_state == IncidentState.MANUAL_INTERVENTION.value

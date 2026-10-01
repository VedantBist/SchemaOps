"""
Test Suite for End-to-End Multi-Incident Orchestration Manager (Phase 6).

Validates:
1. End-to-end incident orchestration lifecycle.
2. SRE operator acknowledgment functionality.
3. Oscillation / flapping detection marking incident DEGRADED.
4. Remediation budget enforcement triggering MANUAL_INTERVENTION.
5. Observability metrics counters increment accurately.
"""

from datetime import datetime, timezone
import sys
import tempfile
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.orchestration.incident_manager import IncidentOrchestrationManager
from ml.orchestration.incident_state import IncidentState


def test_orchestrator_lifecycle_and_acknowledgment():
    """Validates complete incident flow from ingestion to acknowledgment and recovery."""
    with tempfile.TemporaryDirectory() as tmpdir:
        journal = Path(tmpdir) / "test_orch.jsonl"
        mgr = IncidentOrchestrationManager(journal_path=journal)

        # 1. Ingest Anomaly
        inc = mgr.process_anomaly(
            service="inventory-db",
            primary_variable="db_latency",
            fault_signature="lock_contention",
            severity="CRITICAL",
        )
        assert inc.current_state == IncidentState.DETECTED.value
        assert inc.correlation_id.startswith("COR-")

        # 2. Operator Acknowledges
        ack_inc = mgr.acknowledge_incident(inc.incident_id, "oncall-sre@causalops.local")
        assert ack_inc.acknowledged_by == "oncall-sre@causalops.local"
        assert ack_inc.acknowledged_at is not None

        # 3. Investigation & RCA
        rca_inc = mgr.run_investigation_and_rca(inc.incident_id)
        assert rca_inc.current_state == IncidentState.RCA_COMPLETE.value

        # 4. Recommendation & Approval
        rec_inc = mgr.register_recommendation(
            incident_id=inc.incident_id,
            recommendation_id="REC-100",
            recommended_action_id="ACT-DB-01",
            target_service="inventory-db",
        )
        assert rec_inc.current_state == IncidentState.REMEDIATION_RECOMMENDED.value

        appr_inc = mgr.submit_approval(
            incident_id=inc.incident_id,
            approval_id="APP-100",
            approved_by="lead-sre@causalops.local",
            action_id="ACT-DB-01",
            target_service="inventory-db",
            target_variable="db_latency",
        )
        assert appr_inc.current_state == IncidentState.APPROVAL_PENDING.value
        assert len(mgr.scheduler.get_queued_items()) == 1

        # 5. Recovery
        recov_inc = mgr.transition_state(inc.incident_id, IncidentState.REMEDIATION_EXECUTING.value)
        recov_inc = mgr.transition_state(inc.incident_id, IncidentState.VERIFYING.value)
        recov_inc = mgr.transition_state(inc.incident_id, IncidentState.RECOVERED.value)
        assert recov_inc.current_state == IncidentState.RECOVERED.value

        metrics = mgr.get_metrics()
        assert metrics["incidents_created_total"] == 1
        assert metrics["incidents_recovered_total"] == 1


def test_oscillation_detection():
    """Validates that rapid flapping triggers DEGRADED state and halts automation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        journal = Path(tmpdir) / "test_osc.jsonl"
        mgr = IncidentOrchestrationManager(journal_path=journal)

        inc = mgr.process_anomaly(service="order-service", primary_variable="p99_latency", fault_signature="jitter")
        inc_id = inc.incident_id

        # Rapidly flip health status 4 times
        mgr.record_health_observation(inc_id, is_healthy=True)
        mgr.record_health_observation(inc_id, is_healthy=False)
        mgr.record_health_observation(inc_id, is_healthy=True)
        mgr.record_health_observation(inc_id, is_healthy=False)

        updated = mgr.get_incident(inc_id)
        assert updated.oscillation_detected is True
        assert updated.current_state == IncidentState.DEGRADED.value


def test_budget_exceeded_triggers_manual_intervention():
    """Validates that exceeding execution or rollback limits transitions to MANUAL_INTERVENTION."""
    with tempfile.TemporaryDirectory() as tmpdir:
        journal = Path(tmpdir) / "test_budget.jsonl"
        mgr = IncidentOrchestrationManager(journal_path=journal)

        inc = mgr.process_anomaly(service="payment-service", primary_variable="error_rate", fault_signature="500s")
        inc_id = inc.incident_id

        # 3 allowed executions
        for _ in range(3):
            allowed, _ = mgr.check_and_increment_budget(inc_id, is_execution=True)
            assert allowed is True

        # 4th execution exceeds budget!
        allowed, reason = mgr.check_and_increment_budget(inc_id, is_execution=True)
        assert allowed is False
        assert "Execution limit exceeded" in reason

        updated = mgr.get_incident(inc_id)
        assert updated.current_state == IncidentState.MANUAL_INTERVENTION.value

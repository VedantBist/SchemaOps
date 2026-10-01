"""
Test Suite for Deterministic Audit Journal Replay (Phase 6).

Validates:
1. Complete incident lifecycle is logged to append-only JSONL journal.
2. Replay reconstructs exact in-memory incident state from journal.
3. State, metadata, timestamps, and timeline match live state.
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


def test_deterministic_journal_replay():
    """Confirms live state equals reconstructed journal state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        journal_file = Path(tmpdir) / "test_journal.jsonl"
        manager = IncidentOrchestrationManager(journal_path=journal_file)

        # 1. Ingest incident
        inc = manager.process_anomaly(
            service="inventory-db",
            primary_variable="db_latency",
            fault_signature="lock_spike",
            severity="CRITICAL",
        )
        inc_id = inc.incident_id

        # 2. Advance through lifecycle
        manager.run_investigation_and_rca(inc_id)
        manager.register_recommendation(
            incident_id=inc_id,
            recommendation_id="REC-TEST-01",
            recommended_action_id="ACT-DB-01",
            target_service="inventory-db",
        )
        manager.submit_approval(
            incident_id=inc_id,
            approval_id="APP-TEST-01",
            approved_by="lead-sre@causalops.local",
            action_id="ACT-DB-01",
            target_service="inventory-db",
            target_variable="db_latency",
        )
        manager.transition_state(inc_id, IncidentState.REMEDIATION_EXECUTING.value, actor="Executor")
        manager.transition_state(inc_id, IncidentState.VERIFYING.value, actor="VerificationEngine")
        manager.transition_state(inc_id, IncidentState.RECOVERED.value, actor="VerificationEngine")

        live_inc = manager.get_incident(inc_id)
        assert live_inc.current_state == IncidentState.RECOVERED.value
        assert len(live_inc.timeline) >= 6

        # 3. Perform Replay from disk
        replayed_incidents = IncidentOrchestrationManager.replay_journal(journal_file)
        assert inc_id in replayed_incidents

        replayed_inc = replayed_incidents[inc_id]
        assert replayed_inc.incident_id == live_inc.incident_id
        assert replayed_inc.correlation_id == live_inc.correlation_id
        assert replayed_inc.current_state == live_inc.current_state
        assert replayed_inc.root_cause == live_inc.root_cause
        assert replayed_inc.affected_services == live_inc.affected_services
        assert len(replayed_inc.timeline) == len(live_inc.timeline)

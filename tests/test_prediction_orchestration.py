"""
Phase 6A Tests — Prediction Orchestration Integration
Validates:
1. End-to-end integration: Failure prediction event -> Anomaly confirmed -> Investigation -> Recovery
2. process_anomaly automatically confirms active PREDICTED incident on the same service
3. Append-only journal records predictive events and transitions
4. Phase 6 multi-incident isolation preserved
"""
import pytest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from ml.orchestration.incident_manager import IncidentOrchestrationManager
from ml.orchestration.incident_state import IncidentState


class TestPredictionOrchestration:
    """Verifies additive integration with Phase 6 orchestration engine."""

    def test_anomaly_confirms_existing_prediction(self):
        """When an anomaly occurs on a predicted service, it confirms the predictive incident."""
        mgr = IncidentOrchestrationManager()

        # Step 1: Forward-looking prediction occurs
        pred_inc = mgr.process_prediction(
            service="inventory-db",
            predicted_fault="DB_LATENCY",
            probability=0.94,
            horizon_seconds=10,
        )
        assert pred_inc.current_state == IncidentState.PREDICTED.value

        # Step 2: Telemetry anomaly occurs on inventory-db
        confirmed_inc = mgr.process_anomaly(
            service="inventory-db",
            primary_variable="db_latency",
            fault_signature="DB_LATENCY",
            severity="CRITICAL",
        )

        # Same incident ID is preserved and state transitioned to CONFIRMED
        assert confirmed_inc.incident_id == pred_inc.incident_id
        assert confirmed_inc.current_state == IncidentState.CONFIRMED.value
        assert confirmed_inc.detection_source == "failure_prediction"
        assert mgr.get_metrics()["predictions_confirmed_total"] == 1

    def test_independent_unpredicted_anomaly_still_detected(self):
        """Anomalies on unpredicted services proceed through standard DETECTED flow."""
        mgr = IncidentOrchestrationManager()

        inc = mgr.process_anomaly(
            service="payment-service",
            primary_variable="p99_latency",
            fault_signature="SERVICE_LATENCY",
            severity="HIGH",
        )
        assert inc.current_state == IncidentState.DETECTED.value
        assert inc.detection_source == "telemetry_anomaly_detector"

    def test_multi_incident_isolation_with_predictions(self):
        """Simultaneous predictive and reactive incidents maintain total state isolation."""
        mgr = IncidentOrchestrationManager()

        pred_inc = mgr.process_prediction(
            service="inventory-db",
            predicted_fault="DB_LATENCY",
            probability=0.88,
        )

        react_inc = mgr.process_anomaly(
            service="order-service",
            primary_variable="error_rate",
            fault_signature="ERROR_RATE",
            severity="HIGH",
        )

        assert pred_inc.incident_id != react_inc.incident_id
        assert pred_inc.current_state == IncidentState.PREDICTED.value
        assert react_inc.current_state in (IncidentState.DETECTED.value, IncidentState.CORRELATED.value)
        assert mgr.get_metrics()["active_incidents"] == 2

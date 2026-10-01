"""
Phase 6A Tests — Predictive Incident Lifecycle
Validates:
1. Ingestion of predictive failure events into PREDICTED state
2. Detection source strictly set to "failure_prediction"
3. Valid state transitions: PREDICTED -> CONFIRMED, PREDICTED -> EXPIRED, PREDICTED -> CANCELLED
4. Distinction from reactive incidents (is_predictive flag, detection_source)
5. Rejection of direct remediation execution from PREDICTED state
"""
import pytest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from ml.orchestration.incident_manager import IncidentOrchestrationManager
from ml.orchestration.incident_state import (
    IncidentState,
    validate_incident_transition,
    InvalidIncidentTransitionError,
)


class TestPredictiveIncidentLifecycle:
    """Tests the state lifecycle and isolation of predictive incidents."""

    def test_create_predictive_incident(self):
        """Creates an incident in PREDICTED state with failure_prediction source."""
        mgr = IncidentOrchestrationManager()
        inc = mgr.process_prediction(
            service="payment-service",
            predicted_fault="SERVICE_FAILURE",
            horizon_seconds=10,
            probability=0.91,
            lead_time_seconds=8.5,
        )

        assert inc.current_state == IncidentState.PREDICTED.value
        assert inc.detection_source == "failure_prediction"
        assert inc.affected_services == ["payment-service"]
        assert inc.metadata.get("is_predictive") is True
        assert inc.metadata["prediction"]["probability"] == 0.91
        assert inc.metadata["prediction"]["horizon_seconds"] == 10

    def test_transition_predicted_to_confirmed(self):
        """Confirms a predictive incident when an anomaly occurs."""
        mgr = IncidentOrchestrationManager()
        inc = mgr.process_prediction(
            service="order-service",
            predicted_fault="SERVICE_LATENCY",
            probability=0.88,
        )
        assert inc.current_state == IncidentState.PREDICTED.value

        confirmed = mgr.confirm_prediction(inc.incident_id)
        assert confirmed.current_state == IncidentState.CONFIRMED.value
        assert mgr.get_metrics()["predictions_confirmed_total"] >= 1

    def test_transition_predicted_to_cancelled(self):
        """Cancels a predictive incident on operator intervention."""
        mgr = IncidentOrchestrationManager()
        inc = mgr.process_prediction(
            service="inventory-db",
            predicted_fault="DB_LATENCY",
            probability=0.72,
        )
        cancelled = mgr.cancel_prediction(inc.incident_id, cancelled_by="operator@causalops.local")
        assert cancelled.current_state == IncidentState.CANCELLED.value
        assert mgr.get_metrics()["predictions_cancelled_total"] >= 1

    def test_transition_predicted_to_expired(self):
        """Expires a predictive incident when the prediction horizon passes safely."""
        mgr = IncidentOrchestrationManager()
        inc = mgr.process_prediction(
            service="api-gateway",
            predicted_fault="ERROR_RATE",
            probability=0.65,
        )
        expired = mgr.expire_prediction(inc.incident_id)
        assert expired.current_state == IncidentState.EXPIRED.value
        assert mgr.get_metrics()["predictions_expired_total"] >= 1

    def test_cannot_execute_remediation_directly_from_predicted(self):
        """Predictive incident cannot bypass safety gates to execute remediation."""
        mgr = IncidentOrchestrationManager()
        inc = mgr.process_prediction(
            service="payment-service",
            predicted_fault="SERVICE_FAILURE",
            probability=0.99,
        )
        with pytest.raises(InvalidIncidentTransitionError):
            validate_incident_transition(
                from_state=inc.current_state,
                to_state=IncidentState.REMEDIATION_EXECUTING.value,
                incident_id=inc.incident_id,
            )

    def test_deduplication_of_repeated_predictions(self):
        """Repeated prediction on same service updates existing PREDICTED incident."""
        mgr = IncidentOrchestrationManager()
        inc1 = mgr.process_prediction(
            service="inventory-service",
            predicted_fault="SERVICE_LATENCY",
            probability=0.85,
        )
        inc2 = mgr.process_prediction(
            service="inventory-service",
            predicted_fault="SERVICE_LATENCY",
            probability=0.92,
        )
        assert inc1.incident_id == inc2.incident_id
        assert inc2.repetition_count == 2

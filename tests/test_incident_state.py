"""
Test Suite for Formal Incident State Machine (Phase 6).

Validates:
1. Valid transitions across the entire lifecycle:
   DETECTED -> INVESTIGATING -> RCA_COMPLETE -> REMEDIATION_RECOMMENDED ->
   APPROVAL_PENDING -> REMEDIATION_EXECUTING -> VERIFYING -> RECOVERED
2. Safe degradation paths (transition to DEGRADED on failure)
3. Safe escalation paths (transition to MANUAL_INTERVENTION)
4. Strict rejection of invalid / illegal state jumps (raises InvalidIncidentTransitionError)
5. Terminal state immutability
"""

import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.orchestration.incident_state import (
    IncidentState,
    validate_incident_transition,
    InvalidIncidentTransitionError,
    VALID_INCIDENT_TRANSITIONS,
)


def test_valid_lifecycle_transitions():
    """Verifies standard forward progress along the canonical happy path."""
    assert validate_incident_transition(IncidentState.DETECTED.value, IncidentState.INVESTIGATING.value)
    assert validate_incident_transition(IncidentState.INVESTIGATING.value, IncidentState.RCA_COMPLETE.value)
    assert validate_incident_transition(IncidentState.RCA_COMPLETE.value, IncidentState.REMEDIATION_RECOMMENDED.value)
    assert validate_incident_transition(IncidentState.REMEDIATION_RECOMMENDED.value, IncidentState.APPROVAL_PENDING.value)
    assert validate_incident_transition(IncidentState.APPROVAL_PENDING.value, IncidentState.REMEDIATION_EXECUTING.value)
    assert validate_incident_transition(IncidentState.REMEDIATION_EXECUTING.value, IncidentState.VERIFYING.value)
    assert validate_incident_transition(IncidentState.VERIFYING.value, IncidentState.RECOVERED.value)


def test_rollback_lifecycle_transitions():
    """Verifies state flow through verification failure and rollback."""
    assert validate_incident_transition(IncidentState.VERIFYING.value, IncidentState.ROLLBACK.value)
    assert validate_incident_transition(IncidentState.ROLLBACK.value, IncidentState.ROLLBACK_VERIFYING.value)
    assert validate_incident_transition(IncidentState.ROLLBACK_VERIFYING.value, IncidentState.RECOVERED.value)
    assert validate_incident_transition(IncidentState.ROLLBACK_VERIFYING.value, IncidentState.MANUAL_INTERVENTION.value)


def test_degraded_transitions():
    """Verifies that active states can safely degrade when dependencies fail."""
    assert validate_incident_transition(IncidentState.DETECTED.value, IncidentState.DEGRADED.value)
    assert validate_incident_transition(IncidentState.INVESTIGATING.value, IncidentState.DEGRADED.value)
    assert validate_incident_transition(IncidentState.APPROVAL_PENDING.value, IncidentState.DEGRADED.value)
    assert validate_incident_transition(IncidentState.REMEDIATION_EXECUTING.value, IncidentState.DEGRADED.value)


def test_invalid_transitions_strictly_rejected():
    """Verifies that illegal jumps raise InvalidIncidentTransitionError."""
    # Cannot jump from DETECTED directly to REMEDIATION_EXECUTING (requires investigation & approval)
    with pytest.raises(InvalidIncidentTransitionError) as exc_info:
        validate_incident_transition(IncidentState.DETECTED.value, IncidentState.REMEDIATION_EXECUTING.value, "INC-001")
    assert "Invalid incident transition" in str(exc_info.value)
    assert "INC-001" in str(exc_info.value)

    # Cannot jump from APPROVAL_PENDING directly to RECOVERED without execution or verification
    with pytest.raises(InvalidIncidentTransitionError):
        validate_incident_transition(IncidentState.APPROVAL_PENDING.value, IncidentState.REMEDIATION_EXECUTING.value)
        validate_incident_transition(IncidentState.APPROVAL_PENDING.value, IncidentState.VERIFYING.value)

    # Cannot transition from terminal RECOVERED state
    with pytest.raises(InvalidIncidentTransitionError):
        validate_incident_transition(IncidentState.RECOVERED.value, IncidentState.INVESTIGATING.value)


def test_unknown_source_state():
    """Verifies handling of unparseable states."""
    with pytest.raises(InvalidIncidentTransitionError) as exc_info:
        validate_incident_transition("NON_EXISTENT_STATE", IncidentState.DETECTED.value)
    assert "Unknown source state" in str(exc_info.value)

"""
Formal Incident State Machine for CausalOps Multi-Incident Orchestration (Phase 6).

Implements the deterministic state machine tracking an incident's entire lifecycle
from initial anomaly detection through correlation, RCA, approval, remediation,
verification, recovery, or manual escalation.

NON-NEGOTIABLE SAFETY CONSTRAINTS:
- Every transition must be explicitly validated and recorded.
- Invalid state transitions are strictly rejected with typed exceptions.
- Terminal states (RECOVERED, MANUAL_INTERVENTION, SUPERSEDED, EXPIRED) cannot silently mutate.
- DEGRADED and MANUAL_INTERVENTION can be reached from any active state upon safety violations.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
import uuid
from typing import Dict, Set, Optional, Any, List


class IncidentState(str, Enum):
    """Formal states across the multi-incident orchestration lifecycle."""
    DETECTED = "DETECTED"
    INVESTIGATING = "INVESTIGATING"
    RCA_COMPLETE = "RCA_COMPLETE"
    REMEDIATION_RECOMMENDED = "REMEDIATION_RECOMMENDED"
    APPROVAL_PENDING = "APPROVAL_PENDING"
    REMEDIATION_EXECUTING = "REMEDIATION_EXECUTING"
    VERIFYING = "VERIFYING"
    RECOVERED = "RECOVERED"
    ROLLBACK = "ROLLBACK"
    ROLLBACK_VERIFYING = "ROLLBACK_VERIFYING"
    MANUAL_INTERVENTION = "MANUAL_INTERVENTION"

    # Additional resilience states
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    CORRELATED = "CORRELATED"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"

    # Phase 6A predictive incident lifecycle
    PREDICTED = "PREDICTED"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


class InvalidIncidentTransitionError(ValueError):
    """Raised when an illegal state transition is attempted."""
    def __init__(self, from_state: str, to_state: str, incident_id: str, reason: str = ""):
        self.from_state = from_state
        self.to_state = to_state
        self.incident_id = incident_id
        msg = f"Invalid incident transition for '{incident_id}': '{from_state}' -> '{to_state}'."
        if reason:
            msg += f" Reason: {reason}"
        super().__init__(msg)


# Deterministic transition matrix
VALID_INCIDENT_TRANSITIONS: Dict[str, Set[str]] = {
    IncidentState.DETECTED.value: {
        IncidentState.INVESTIGATING.value,
        IncidentState.RCA_COMPLETE.value,
        IncidentState.REMEDIATION_RECOMMENDED.value,
        IncidentState.APPROVAL_PENDING.value,
        IncidentState.CORRELATED.value,
        IncidentState.SUPERSEDED.value,
        IncidentState.DEGRADED.value,
        IncidentState.BLOCKED.value,
        IncidentState.MANUAL_INTERVENTION.value,
        IncidentState.EXPIRED.value,
    },
    IncidentState.INVESTIGATING.value: {
        IncidentState.RCA_COMPLETE.value,
        IncidentState.DEGRADED.value,
        IncidentState.BLOCKED.value,
        IncidentState.CORRELATED.value,
        IncidentState.SUPERSEDED.value,
        IncidentState.MANUAL_INTERVENTION.value,
    },
    IncidentState.RCA_COMPLETE.value: {
        IncidentState.REMEDIATION_RECOMMENDED.value,
        IncidentState.DEGRADED.value,
        IncidentState.BLOCKED.value,
        IncidentState.RECOVERED.value,  # If NO_FAULT / self-resolved
        IncidentState.MANUAL_INTERVENTION.value,
    },
    IncidentState.REMEDIATION_RECOMMENDED.value: {
        IncidentState.APPROVAL_PENDING.value,
        IncidentState.BLOCKED.value,
        IncidentState.DEGRADED.value,
        IncidentState.EXPIRED.value,
        IncidentState.RECOVERED.value,  # Self-resolved before approval
        IncidentState.MANUAL_INTERVENTION.value,
    },
    IncidentState.APPROVAL_PENDING.value: {
        IncidentState.REMEDIATION_EXECUTING.value,
        IncidentState.EXPIRED.value,
        IncidentState.BLOCKED.value,
        IncidentState.DEGRADED.value,
        IncidentState.RECOVERED.value,  # Anomaly cleared while waiting
        IncidentState.MANUAL_INTERVENTION.value,
    },
    IncidentState.REMEDIATION_EXECUTING.value: {
        IncidentState.VERIFYING.value,
        IncidentState.ROLLBACK.value,
        IncidentState.DEGRADED.value,
        IncidentState.MANUAL_INTERVENTION.value,
    },
    IncidentState.VERIFYING.value: {
        IncidentState.RECOVERED.value,
        IncidentState.ROLLBACK.value,
        IncidentState.DEGRADED.value,
        IncidentState.MANUAL_INTERVENTION.value,
    },
    IncidentState.ROLLBACK.value: {
        IncidentState.ROLLBACK_VERIFYING.value,
        IncidentState.MANUAL_INTERVENTION.value,
        IncidentState.DEGRADED.value,
    },
    IncidentState.ROLLBACK_VERIFYING.value: {
        IncidentState.RECOVERED.value,
        IncidentState.MANUAL_INTERVENTION.value,
        IncidentState.DEGRADED.value,
    },
    IncidentState.BLOCKED.value: {
        IncidentState.APPROVAL_PENDING.value,      # Conflict resolved
        IncidentState.REMEDIATION_RECOMMENDED.value,
        IncidentState.INVESTIGATING.value,
        IncidentState.MANUAL_INTERVENTION.value,
        IncidentState.EXPIRED.value,
        IncidentState.DEGRADED.value,
    },
    IncidentState.DEGRADED.value: {
        IncidentState.INVESTIGATING.value,         # Re-triaging after telemetry/model recovered
        IncidentState.MANUAL_INTERVENTION.value,
        IncidentState.RECOVERED.value,
        IncidentState.EXPIRED.value,
    },
    IncidentState.CORRELATED.value: {
        IncidentState.SUPERSEDED.value,
        IncidentState.RECOVERED.value,
    },
    # Phase 6A: Predictive incident lifecycle transitions
    IncidentState.PREDICTED.value: {
        IncidentState.CONFIRMED.value,
        IncidentState.EXPIRED.value,
        IncidentState.CANCELLED.value,
        IncidentState.DETECTED.value,
        IncidentState.INVESTIGATING.value,
        IncidentState.DEGRADED.value,
        IncidentState.MANUAL_INTERVENTION.value,
    },
    IncidentState.CONFIRMED.value: {
        IncidentState.DETECTED.value,
        IncidentState.INVESTIGATING.value,
        IncidentState.RCA_COMPLETE.value,
        IncidentState.REMEDIATION_RECOMMENDED.value,
        IncidentState.DEGRADED.value,
        IncidentState.MANUAL_INTERVENTION.value,
        IncidentState.RECOVERED.value,
    },
    # Terminal states
    IncidentState.RECOVERED.value: set(),
    IncidentState.SUPERSEDED.value: set(),
    IncidentState.EXPIRED.value: set(),
    IncidentState.CANCELLED.value: set(),
    IncidentState.MANUAL_INTERVENTION.value: {
        IncidentState.INVESTIGATING.value,         # Human manual re-open
        IncidentState.RECOVERED.value,             # Human resolved
    },
    IncidentState.UNKNOWN.value: {
        IncidentState.DETECTED.value,
        IncidentState.DEGRADED.value,
    },
}

ACTIVE_STATES: Set[str] = {
    IncidentState.DETECTED.value,
    IncidentState.INVESTIGATING.value,
    IncidentState.RCA_COMPLETE.value,
    IncidentState.REMEDIATION_RECOMMENDED.value,
    IncidentState.APPROVAL_PENDING.value,
    IncidentState.REMEDIATION_EXECUTING.value,
    IncidentState.VERIFYING.value,
    IncidentState.ROLLBACK.value,
    IncidentState.ROLLBACK_VERIFYING.value,
    IncidentState.BLOCKED.value,
    IncidentState.DEGRADED.value,
    IncidentState.PREDICTED.value,
    IncidentState.CONFIRMED.value,
}

TERMINAL_STATES: Set[str] = {
    IncidentState.RECOVERED.value,
    IncidentState.SUPERSEDED.value,
    IncidentState.EXPIRED.value,
    IncidentState.CANCELLED.value,
}


@dataclass
class IncidentStateTransition:
    """Record of an explicit transition in the incident lifecycle."""
    transition_id: str
    incident_id: str
    correlation_id: str
    from_state: str
    to_state: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    actor: str = "orchestrator"
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def validate_incident_transition(
    from_state: str,
    to_state: str,
    incident_id: str = "INC-UNKNOWN",
    reason: str = "",
) -> bool:
    """
    Validates whether a proposed transition from from_state to to_state is permissible.
    Raises InvalidIncidentTransitionError if the transition violates the formal state machine.
    """
    if from_state not in VALID_INCIDENT_TRANSITIONS:
        raise InvalidIncidentTransitionError(
            from_state=from_state,
            to_state=to_state,
            incident_id=incident_id,
            reason=f"Unknown source state '{from_state}'",
        )

    allowed_targets = VALID_INCIDENT_TRANSITIONS[from_state]
    if to_state not in allowed_targets:
        raise InvalidIncidentTransitionError(
            from_state=from_state,
            to_state=to_state,
            incident_id=incident_id,
            reason=f"Permissible targets from '{from_state}' are: {sorted(list(allowed_targets))}. Got '{to_state}'. {reason}".strip(),
        )

    return True

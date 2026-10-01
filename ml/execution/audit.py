"""
Append-Only Execution Journal & State Machine Engine for CausalOps (Phase 5).

Implements the deterministic closed-loop state machine and immutable append-only
audit journal tracking every lifecycle transition from recommendation to resolution.

NON-NEGOTIABLE SAFETY CONSTRAINTS:
- Never overwrite previous events. The journal is the authoritative audit history.
- Invalid state transitions are strictly rejected.
- Failures are explicitly logged and never silently converted to successes.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import json
import uuid
from typing import Dict, List, Optional, Any, Set, Union


class ExecutionState(str, Enum):
    """Deterministic closed-loop remediation states."""
    RECOMMENDED = "RECOMMENDED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    POLICY_VALIDATED = "POLICY_VALIDATED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    VERIFYING = "VERIFYING"
    RECOVERED = "RECOVERED"
    FAILED = "FAILED"
    ROLLBACK_PENDING = "ROLLBACK_PENDING"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLBACK_VERIFYING = "ROLLBACK_VERIFYING"
    RESTORED = "RESTORED"
    INCIDENT_RESOLVED = "INCIDENT_RESOLVED"

    # Terminal failure states
    EXECUTION_FAILED = "EXECUTION_FAILED"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


# Valid state transitions matrix
VALID_TRANSITIONS: Dict[str, Set[str]] = {
    ExecutionState.RECOMMENDED.value: {
        ExecutionState.PENDING_APPROVAL.value,
        ExecutionState.APPROVED.value,
    },
    ExecutionState.PENDING_APPROVAL.value: {
        ExecutionState.APPROVED.value,
        ExecutionState.FAILED.value,
    },
    ExecutionState.APPROVED.value: {
        ExecutionState.POLICY_VALIDATED.value,
        ExecutionState.FAILED.value,
    },
    ExecutionState.POLICY_VALIDATED.value: {
        ExecutionState.EXECUTING.value,
        ExecutionState.EXECUTION_FAILED.value,
    },
    ExecutionState.EXECUTING.value: {
        ExecutionState.EXECUTED.value,
        ExecutionState.EXECUTION_FAILED.value,
        ExecutionState.FAILED.value,
    },
    ExecutionState.EXECUTED.value: {
        ExecutionState.VERIFYING.value,
        ExecutionState.ROLLBACK_PENDING.value,
    },
    ExecutionState.VERIFYING.value: {
        ExecutionState.RECOVERED.value,
        ExecutionState.VERIFICATION_FAILED.value,
        ExecutionState.FAILED.value,
        ExecutionState.ROLLBACK_PENDING.value,
    },
    ExecutionState.RECOVERED.value: {
        ExecutionState.INCIDENT_RESOLVED.value,
    },
    ExecutionState.FAILED.value: {
        ExecutionState.ROLLBACK_PENDING.value,
        ExecutionState.EXECUTION_FAILED.value,
    },
    ExecutionState.VERIFICATION_FAILED.value: {
        ExecutionState.ROLLBACK_PENDING.value,
    },
    ExecutionState.ROLLBACK_PENDING.value: {
        ExecutionState.ROLLING_BACK.value,
    },
    ExecutionState.ROLLING_BACK.value: {
        ExecutionState.ROLLBACK_VERIFYING.value,
        ExecutionState.ROLLBACK_FAILED.value,
    },
    ExecutionState.ROLLBACK_VERIFYING.value: {
        ExecutionState.RESTORED.value,
        ExecutionState.ROLLBACK_FAILED.value,
    },
    ExecutionState.RESTORED.value: {
        ExecutionState.INCIDENT_RESOLVED.value,
    },
    ExecutionState.INCIDENT_RESOLVED.value: set(),
    ExecutionState.EXECUTION_FAILED.value: set(),
    ExecutionState.ROLLBACK_FAILED.value: set(),
}


@dataclass
class JournalEntry:
    """
    Immutable audit record representing a single state transition in the lifecycle.
    """
    entry_id: str
    execution_id: str
    incident_id: str
    recommendation_id: str
    approval_id: str
    action_id: str
    target_service: str
    previous_state: str
    new_state: str
    timestamp: str
    actor: str = "system"
    policy_result: Optional[Dict[str, Any]] = None
    execution_result: Optional[Dict[str, Any]] = None
    verification_result: Optional[Dict[str, Any]] = None
    rollback_result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    telemetry_snapshot_reference: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ExecutionJournal:
    """
    Append-only audit log storing all remediation events in JSON Lines format.
    """

    def __init__(self, journal_path: Union[str, Path] = "ml/models/execution/execution_journal.jsonl"):
        self.journal_path = Path(journal_path)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: List[JournalEntry] = []
        self._load_existing()

    def _load_existing(self) -> None:
        """Loads existing entries into memory cache if file exists."""
        if self.journal_path.exists():
            with open(self.journal_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            d = json.loads(line)
                            self._entries.append(JournalEntry(**d))
                        except Exception:
                            continue

    def append(
        self,
        execution_id: str,
        incident_id: str,
        recommendation_id: str,
        approval_id: str,
        action_id: str,
        target_service: str,
        previous_state: str,
        new_state: str,
        actor: str = "system",
        policy_result: Optional[Dict[str, Any]] = None,
        execution_result: Optional[Dict[str, Any]] = None,
        verification_result: Optional[Dict[str, Any]] = None,
        rollback_result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        telemetry_snapshot_reference: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> JournalEntry:
        """
        Validates transition and appends an immutable entry to the journal.
        """
        # Validate state transition if previous_state is known
        if previous_state and previous_state in VALID_TRANSITIONS:
            valid_next = VALID_TRANSITIONS[previous_state]
            if new_state not in valid_next and new_state != previous_state:
                raise ValueError(
                    f"Invalid state transition from '{previous_state}' to '{new_state}'. "
                    f"Allowed transitions: {valid_next}"
                )

        entry = JournalEntry(
            entry_id=f"JRN-{uuid.uuid4().hex[:12]}",
            execution_id=execution_id,
            incident_id=incident_id,
            recommendation_id=recommendation_id,
            approval_id=approval_id,
            action_id=action_id,
            target_service=target_service,
            previous_state=previous_state,
            new_state=new_state,
            timestamp=datetime.now(timezone.utc).isoformat(),
            actor=actor,
            policy_result=policy_result,
            execution_result=execution_result,
            verification_result=verification_result,
            rollback_result=rollback_result,
            error=error,
            telemetry_snapshot_reference=telemetry_snapshot_reference,
            metadata=metadata or {},
        )

        self._entries.append(entry)

        # Atomic append to file
        with open(self.journal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict()) + "\n")

        return entry

    def get_timeline(self, execution_id: str) -> List[Dict[str, Any]]:
        """Returns ordered timeline of events for an execution."""
        return [
            e.to_dict() for e in self._entries
            if e.execution_id == execution_id
        ]

    def get_entries_for_incident(self, incident_id: str) -> List[JournalEntry]:
        """Returns all entries associated with an incident."""
        return [e for e in self._entries if e.incident_id == incident_id]

    def all_entries(self) -> List[JournalEntry]:
        """Returns all entries."""
        return list(self._entries)

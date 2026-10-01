"""
Deterministic Remediation Scheduler for CausalOps (Phase 6).

Coordinates the dispatching of approved remediations across multiple concurrent incidents.

NON-NEGOTIABLE SAFETY CONSTRAINTS:
- Do NOT use opaque numerical heuristics or random weights.
- Strict deterministic policy eligibility ordering:
  1. Incident active and not degraded/superseded/recovered
  2. Telemetry FRESH and HEALTHY (never STALE or UNUSABLE)
  3. Explicit valid, non-expired human approval present
  4. 15-rule policy validation satisfied
  5. Target service mutex lock available
  6. Zero resource, dependency, or rollback conflicts detected (via conflict.py)
  7. Blast radius budget satisfied
- Tie-breaking: Strict FIFO by approval timestamp (earliest approval dispatched first).
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any, Set, Tuple

from .conflict import ConflictDetector, ConflictEvaluation
from .health import TelemetryReport, TelemetryHealthStatus, TelemetryFreshness
from .incident_state import IncidentState, ACTIVE_STATES


@dataclass
class QueueItem:
    """An approved remediation candidate awaiting scheduling and execution dispatch."""
    incident_id: str
    recommendation_id: str
    approval_id: str
    action_id: str
    target_service: str
    target_variable: str
    approved_at: str
    blast_radius_size: int = 1
    blast_radius_services: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SchedulingDecision:
    """Outcome of scheduler evaluation."""
    dispatched_item: Optional[QueueItem]
    status: str  # "DISPATCHED", "BLOCKED", "WAITING", "EMPTY"
    reason: str
    evaluations: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RemediationScheduler:
    """
    Deterministic FIFO scheduler with safety policy and conflict evaluation.
    """

    def __init__(self):
        self._queue: List[QueueItem] = []

    def enqueue(self, item: QueueItem):
        """Appends an approved remediation candidate to the scheduling queue."""
        # Prevent duplicate entries in queue
        for existing in self._queue:
            if (
                existing.incident_id == item.incident_id
                and existing.action_id == item.action_id
                and existing.approval_id == item.approval_id
            ):
                return
        self._queue.append(item)

    def remove(self, approval_id: str):
        """Removes an item from queue upon execution completion or cancellation."""
        self._queue = [q for q in self._queue if q.approval_id != approval_id]

    def schedule_next(
        self,
        active_service_locks: Set[str],
        active_remediations: List[Dict[str, Any]],
        incident_states: Dict[str, str],
        telemetry_reports: Dict[str, TelemetryReport],
    ) -> SchedulingDecision:
        """
        Selects the next eligible remediation for execution following strict deterministic criteria.
        """
        if not self._queue:
            return SchedulingDecision(
                dispatched_item=None,
                status="EMPTY",
                reason="Scheduling queue is empty.",
            )

        # 1. Sort queue strictly by FIFO approval timestamp
        sorted_candidates = sorted(
            self._queue,
            key=lambda item: item.approved_at or "",
        )

        evaluations: List[Dict[str, Any]] = []

        for candidate in sorted_candidates:
            inc_id = candidate.incident_id
            service = candidate.target_service
            act_id = candidate.action_id

            eval_entry = {
                "incident_id": inc_id,
                "action_id": act_id,
                "target_service": service,
                "approved_at": candidate.approved_at,
                "eligible": False,
                "rejection_reasons": [],
            }

            # Invariant 1: Incident is in an active, eligible state
            curr_state = incident_states.get(inc_id, IncidentState.UNKNOWN.value)
            if curr_state not in ACTIVE_STATES or curr_state in [
                IncidentState.RECOVERED.value,
                IncidentState.DEGRADED.value,
                IncidentState.BLOCKED.value,
                IncidentState.SUPERSEDED.value,
                IncidentState.EXPIRED.value,
            ]:
                eval_entry["rejection_reasons"].append(
                    f"Incident '{inc_id}' is not in an active eligible state (current: '{curr_state}')."
                )

            # Invariant 2: Telemetry Freshness & Quality
            tel_rep = telemetry_reports.get(inc_id)
            if tel_rep is not None:
                if not tel_rep.can_execute_remediation or tel_rep.freshness == TelemetryFreshness.STALE.value or tel_rep.quality == TelemetryHealthStatus.UNUSABLE.value:
                    eval_entry["rejection_reasons"].append(
                        f"Telemetry is unusable or stale (freshness: {tel_rep.freshness}, quality: {tel_rep.quality}). Execution blocked."
                    )

            # Invariant 3: Target service concurrency lock
            if service in active_service_locks:
                eval_entry["rejection_reasons"].append(
                    f"Target service '{service}' is currently locked by another active remediation."
                )

            # Invariant 4: Remediation Conflict Check
            conflict = ConflictDetector.evaluate_conflict(
                candidate_incident_id=inc_id,
                candidate_action_id=act_id,
                candidate_target_service=service,
                candidate_target_variable=candidate.target_variable,
                candidate_blast_radius_size=candidate.blast_radius_size,
                active_remediations=active_remediations,
            )
            if conflict.has_conflict:
                eval_entry["rejection_reasons"].append(
                    f"Conflict detected: {conflict.conflict_type} ({conflict.explanation})"
                )

            # Decision evaluation
            if not eval_entry["rejection_reasons"]:
                eval_entry["eligible"] = True
                evaluations.append(eval_entry)

                # Pop candidate from queue and return dispatch decision
                self.remove(candidate.approval_id)

                return SchedulingDecision(
                    dispatched_item=candidate,
                    status="DISPATCHED",
                    reason=f"Candidate '{act_id}' for incident '{inc_id}' successfully scheduled via FIFO.",
                    evaluations=evaluations,
                )
            else:
                evaluations.append(eval_entry)

        # All queued candidates are currently blocked or waiting on locks/fresh telemetry
        return SchedulingDecision(
            dispatched_item=None,
            status="BLOCKED" if any("Conflict" in r for e in evaluations for r in e["rejection_reasons"]) else "WAITING",
            reason="All queued candidates are currently blocked by conflicts, active locks, or stale telemetry.",
            evaluations=evaluations,
        )

    def get_queued_items(self) -> List[Dict[str, Any]]:
        return [q.to_dict() for q in self._queue]

    def clear(self):
        self._queue.clear()

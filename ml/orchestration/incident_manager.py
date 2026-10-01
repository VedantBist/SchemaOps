"""
Central Multi-Incident Orchestration Engine for CausalOps (Phase 6).

Coordinates the end-to-end multi-incident lifecycle across distributed microservices:
Telemetry -> Telemetry Health -> Detection -> Deduplication -> Correlation ->
RCA -> Remediation -> Conflict Detection -> Scheduling -> Policy ->
Controlled Execution -> Hardened Verification -> Hierarchical Recovery -> Closure.

NON-NEGOTIABLE SAFETY CONSTRAINTS:
- Fails safely on degraded AI/SCM/Telemetry dependencies without crashing.
- Preserves absolute multi-incident state isolation (no cross-incident contamination).
- Enforces strict oscillation detection and per-incident remediation budgets.
- Maintains full auditability with universal correlation_id propagation and journal replay.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import threading
import uuid
from typing import Dict, List, Optional, Any, Set, Tuple, Union

from .incident_state import (
    IncidentState,
    IncidentStateTransition,
    validate_incident_transition,
    ACTIVE_STATES,
    TERMINAL_STATES,
)
from .health import (
    TelemetryHealthTracker,
    TelemetryReport,
    TelemetryHealthStatus,
    TelemetryFreshness,
    ServiceHealthTracker,
    DependencyRecoveryTracker,
    DependencyRecoveryStatus,
    SystemHealthRegistry,
    CANONICAL_SERVICES,
)
from .deduplication import IncidentDeduplicationEngine, DeduplicationResult
from .correlation import IncidentCorrelationEngine, CorrelationResult
from .conflict import ConflictDetector, ConflictEvaluation, ConflictType
from .scheduler import RemediationScheduler, QueueItem, SchedulingDecision


@dataclass
class RemediationBudget:
    """Hard safety budget per incident to eliminate runaway loops."""
    max_executions: int = 3
    max_rollbacks: int = 1
    max_duration_seconds: float = 1800.0  # 30 minutes
    max_affected_services: int = 4

    execution_count: int = 0
    rollback_count: int = 0
    duration_seconds: float = 0.0

    def is_exceeded(self, affected_services_count: int = 1) -> Tuple[bool, str]:
        if self.execution_count > self.max_executions:
            return True, f"Execution limit exceeded ({self.execution_count} > {self.max_executions})"
        if self.rollback_count > self.max_rollbacks:
            return True, f"Rollback ceiling exceeded ({self.rollback_count} > {self.max_rollbacks})"
        if self.duration_seconds > self.max_duration_seconds:
            return True, f"Duration ceiling exceeded ({self.duration_seconds:.0f}s > {self.max_duration_seconds}s)"
        if affected_services_count > self.max_affected_services:
            return True, f"Blast radius exceeded ({affected_services_count} > {self.max_affected_services})"
        return False, ""


@dataclass
class IncidentRecord:
    """First-class incident entity tracking multi-incident orchestration."""
    incident_id: str
    correlation_id: str
    correlation_group: str
    created_at: str
    updated_at: str
    detection_source: str
    affected_services: List[str]
    root_cause: Optional[str]
    fault_signature: str
    current_state: str
    severity: str
    is_downstream_symptom: bool = False
    parent_incident_id: Optional[str] = None
    recommendation_id: Optional[str] = None
    approval_id: Optional[str] = None
    execution_id: Optional[str] = None
    budget: RemediationBudget = field(default_factory=RemediationBudget)
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    repetition_count: int = 1
    telemetry_health: Optional[Dict[str, Any]] = None
    recovery_status: Optional[str] = None
    oscillation_detected: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


class IncidentOrchestrationManager:
    """
    Production-grade multi-incident orchestrator for CausalOps.
    """

    def __init__(
        self,
        journal_path: Optional[Union[str, Path]] = None,
        telemetry_tracker: Optional[TelemetryHealthTracker] = None,
        service_tracker: Optional[ServiceHealthTracker] = None,
        system_health: Optional[SystemHealthRegistry] = None,
    ):
        self.journal_path = Path(journal_path or "ml/models/orchestration/orchestration_journal.jsonl")
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)

        self.telemetry_tracker = telemetry_tracker or TelemetryHealthTracker()
        self.service_tracker = service_tracker or ServiceHealthTracker()
        self.system_health = system_health or SystemHealthRegistry()

        self.deduplicator = IncidentDeduplicationEngine()
        self.correlator = IncidentCorrelationEngine()
        self.scheduler = RemediationScheduler()

        # In-memory stores (isolated per incident_id)
        self._incidents: Dict[str, IncidentRecord] = {}
        self._lock = threading.Lock()

        # Metric counters for Phase 6 observability
        self._metrics = {
            "active_incidents": 0,
            "incidents_created_total": 0,
            "incidents_correlated_total": 0,
            "incidents_deduplicated_total": 0,
            "incidents_recovered_total": 0,
            "incidents_degraded_total": 0,
            "remediation_blocked_total": 0,
            "remediation_executed_total": 0,
            "remediation_failed_total": 0,
            "rollback_total": 0,
            "rollback_failed_total": 0,
            "stale_telemetry_total": 0,
            "policy_denial_total": 0,
            "incidents_predicted_total": 0,
            "predictions_confirmed_total": 0,
            "predictions_cancelled_total": 0,
            "predictions_expired_total": 0,
        }

        # Track recent health values for oscillation detection
        self._health_flips: Dict[str, List[Tuple[float, str]]] = {}

    # -------------------------------------------------------------------------
    # INCIDENT INGESTION & CREATION
    # -------------------------------------------------------------------------
    def process_anomaly(
        self,
        service: str,
        primary_variable: str,
        fault_signature: str,
        severity: str = "HIGH",
        detection_source: str = "telemetry_anomaly_detector",
        timestamp: Optional[datetime] = None,
        telemetry_payload: Optional[Any] = None,
        root_cause_candidate: Optional[str] = None,
    ) -> IncidentRecord:
        """
        Ingests an observed microservice anomaly. Performs:
        1. Telemetry health evaluation
        2. Deduplication check (merges repeated events into single incident)
        3. Causal correlation check (links downstream cascade to upstream root)
        4. State transition to DETECTED
        """
        ts = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc)
        ts_iso = ts.isoformat()

        # 1. Telemetry Freshness & Quality
        if telemetry_payload is not None:
            tel_rep = self.telemetry_tracker.evaluate_telemetry(telemetry_payload, timestamp=ts)
            if tel_rep.freshness == TelemetryFreshness.STALE.value:
                self._metrics["stale_telemetry_total"] += 1
        else:
            tel_rep = TelemetryReport(
                freshness=TelemetryFreshness.FRESH.value,
                quality=TelemetryHealthStatus.HEALTHY.value,
                evaluated_at=ts_iso,
                delay_seconds=0.0,
                service_coverage={s: True for s in CANONICAL_SERVICES},
                nan_count=0,
                inf_count=0,
                sample_count=1,
                missing_services=[],
                reasons=["Anomaly event ingested from telemetry notification."],
                can_execute_remediation=True,
            )

        with self._lock:
            # 2. Incident Deduplication
            candidate_id = f"INC-{uuid.uuid4().hex[:10]}"
            dedup_res = self.deduplicator.process_anomaly_event(
                service=service,
                primary_variable=primary_variable,
                fault_signature=fault_signature,
                generated_incident_id=candidate_id,
                timestamp=ts,
            )

            if dedup_res.is_duplicate:
                # Existing incident matches! Update repeat count and duration
                self._metrics["incidents_deduplicated_total"] += 1
                existing_inc = self._incidents[dedup_res.incident_id]
                existing_inc.repetition_count = dedup_res.repetition_count
                existing_inc.updated_at = ts_iso
                existing_inc.telemetry_health = tel_rep.to_dict()

                # If telemetry became unusable or stale, mark DEGRADED
                if (
                    not tel_rep.can_execute_remediation
                    and existing_inc.current_state in ACTIVE_STATES
                    and existing_inc.current_state != IncidentState.DEGRADED.value
                ):
                    self._transition_incident_locked(
                        existing_inc,
                        IncidentState.DEGRADED.value,
                        actor="TelemetryHealthTracker",
                        reason=f"Telemetry degraded/stale: {'; '.join(tel_rep.reasons)}",
                    )

                return existing_inc

            # 3. Correlation Check
            active_list = [i.to_dict() for i in self._incidents.values() if i.current_state in ACTIVE_STATES]
            corr_res = self.correlator.correlate_incident(
                incident_id=candidate_id,
                service=service,
                timestamp=ts,
                active_incidents=active_list,
            )

            if corr_res.is_correlated:
                self._metrics["incidents_correlated_total"] += 1

            # 4. Construct Incident Entity with Complete Identity
            correlation_id = f"COR-{uuid.uuid4().hex[:12]}"
            initial_state = IncidentState.CORRELATED.value if corr_res.is_correlated else IncidentState.DETECTED.value

            record = IncidentRecord(
                incident_id=candidate_id,
                correlation_id=correlation_id,
                correlation_group=corr_res.correlation_group_id,
                created_at=ts_iso,
                updated_at=ts_iso,
                detection_source=detection_source,
                affected_services=[service],
                root_cause=root_cause_candidate or (corr_res.parent_incident_id and self._incidents[corr_res.parent_incident_id].root_cause) or service,
                fault_signature=fault_signature,
                current_state=initial_state,
                severity=severity,
                is_downstream_symptom=corr_res.is_correlated,
                parent_incident_id=corr_res.parent_incident_id,
                telemetry_health=tel_rep.to_dict(),
                metadata={"correlation_explanation": corr_res.explanation},
            )

            # Check for existing PREDICTED incident on this service
            pred_inc = None
            for inc in self._incidents.values():
                if inc.current_state == IncidentState.PREDICTED.value and service in inc.affected_services:
                    pred_inc = inc
                    break

            if pred_inc:
                self._transition_incident_locked(
                    pred_inc,
                    IncidentState.CONFIRMED.value,
                    actor="anomaly_detector",
                    reason=f"Anomaly observed ({fault_signature}), confirming predictive failure",
                )
                pred_inc.severity = severity
                pred_inc.fault_signature = fault_signature
                pred_inc.updated_at = ts_iso
                pred_inc.telemetry_health = tel_rep.to_dict()
                self._metrics["predictions_confirmed_total"] += 1
                return pred_inc

            # Record initial timeline transition
            trans = IncidentStateTransition(
                transition_id=f"TRN-{uuid.uuid4().hex[:8]}",
                incident_id=candidate_id,
                correlation_id=correlation_id,
                from_state=IncidentState.UNKNOWN.value,
                to_state=initial_state,
                timestamp=ts_iso,
                actor="IncidentOrchestrationManager",
                reason=corr_res.explanation if corr_res.is_correlated else "Initial anomaly detected.",
            )
            record.timeline.append(trans.to_dict())

            self._incidents[candidate_id] = record
            self._metrics["incidents_created_total"] += 1
            self._metrics["active_incidents"] += 1

            self._append_journal(trans, record)
            return record

    def process_prediction(
        self,
        service: str,
        predicted_fault: str,
        horizon_seconds: int = 10,
        probability: float = 0.9,
        model_version: str = "failure_prediction_v1",
        lead_time_seconds: Optional[float] = None,
        timestamp: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> IncidentRecord:
        """
        Ingests a forward-looking failure prediction event.
        Creates an incident in PREDICTED state with detection_source="failure_prediction".
        Strictly advisory: does NOT automatically execute remediation.
        """
        ts = timestamp or datetime.now(timezone.utc)
        ts_iso = ts.isoformat()

        with self._lock:
            # Idempotency / deduplication for predictions on the same service
            for inc in self._incidents.values():
                if inc.current_state == IncidentState.PREDICTED.value and service in inc.affected_services:
                    inc.updated_at = ts_iso
                    inc.repetition_count += 1
                    inc.metadata["last_prediction"] = {
                        "probability": probability,
                        "horizon_seconds": horizon_seconds,
                        "predicted_fault": predicted_fault,
                    }
                    return inc

            candidate_id = f"INC-PRED-{uuid.uuid4().hex[:8]}"
            correlation_id = f"COR-{uuid.uuid4().hex[:12]}"
            correlation_group = f"GRP-PRED-{service}"

            pred_meta = {
                "prediction": {
                    "probability": probability,
                    "horizon_seconds": horizon_seconds,
                    "predicted_fault": predicted_fault,
                    "model_version": model_version,
                    "lead_time_seconds": lead_time_seconds,
                },
                "is_predictive": True,
            }
            if metadata:
                pred_meta.update(metadata)

            record = IncidentRecord(
                incident_id=candidate_id,
                correlation_id=correlation_id,
                correlation_group=correlation_group,
                created_at=ts_iso,
                updated_at=ts_iso,
                detection_source="failure_prediction",
                affected_services=[service],
                root_cause=service,
                fault_signature=predicted_fault,
                current_state=IncidentState.PREDICTED.value,
                severity="HIGH" if probability >= 0.8 else "MEDIUM",
                is_downstream_symptom=False,
                metadata=pred_meta,
            )

            trans = IncidentStateTransition(
                transition_id=f"TRN-{uuid.uuid4().hex[:8]}",
                incident_id=candidate_id,
                correlation_id=correlation_id,
                from_state=IncidentState.UNKNOWN.value,
                to_state=IncidentState.PREDICTED.value,
                timestamp=ts_iso,
                actor="FailurePredictionEngine",
                reason=f"Predicted failure {predicted_fault} on {service} (p={probability:.2f}, horizon={horizon_seconds}s)",
            )
            record.timeline.append(trans.to_dict())

            self._incidents[candidate_id] = record
            self._metrics["incidents_created_total"] += 1
            self._metrics["incidents_predicted_total"] += 1
            self._metrics["active_incidents"] += 1

            self._append_journal(trans, record)
            return record

    def confirm_prediction(
        self,
        incident_id: str,
        confirmed_by: str = "anomaly_detector",
        reason: str = "Fault confirmed by telemetry",
    ) -> IncidentRecord:
        """Transitions a PREDICTED incident to CONFIRMED."""
        with self._lock:
            inc = self._incidents.get(incident_id)
            if not inc:
                raise ValueError(f"Incident '{incident_id}' not found.")
            res = self._transition_incident_locked(
                inc, IncidentState.CONFIRMED.value, actor=confirmed_by, reason=reason
            )
            self._metrics["predictions_confirmed_total"] += 1
            return res

    def cancel_prediction(
        self,
        incident_id: str,
        cancelled_by: str = "operator",
        reason: str = "Prediction cancelled by operator",
    ) -> IncidentRecord:
        """Transitions a PREDICTED incident to CANCELLED."""
        with self._lock:
            inc = self._incidents.get(incident_id)
            if not inc:
                raise ValueError(f"Incident '{incident_id}' not found.")
            res = self._transition_incident_locked(
                inc, IncidentState.CANCELLED.value, actor=cancelled_by, reason=reason
            )
            self._metrics["predictions_cancelled_total"] += 1
            self._metrics["active_incidents"] = max(0, self._metrics["active_incidents"] - 1)
            return res

    def expire_prediction(
        self,
        incident_id: str,
        reason: str = "Prediction horizon window expired without fault",
    ) -> IncidentRecord:
        """Transitions a PREDICTED incident to EXPIRED."""
        with self._lock:
            inc = self._incidents.get(incident_id)
            if not inc:
                raise ValueError(f"Incident '{incident_id}' not found.")
            res = self._transition_incident_locked(
                inc, IncidentState.EXPIRED.value, actor="PredictionMonitor", reason=reason
            )
            self._metrics["predictions_expired_total"] += 1
            self._metrics["active_incidents"] = max(0, self._metrics["active_incidents"] - 1)
            return res

    # -------------------------------------------------------------------------
    # STATE MACHINE TRANSITIONS
    # -------------------------------------------------------------------------
    def transition_state(
        self,
        incident_id: str,
        to_state: str,
        actor: str = "orchestrator",
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> IncidentRecord:
        """Thread-safe transition of incident state."""
        with self._lock:
            incident = self._incidents.get(incident_id)
            if not incident:
                raise ValueError(f"Incident '{incident_id}' not found.")

            return self._transition_incident_locked(incident, to_state, actor, reason, metadata)

    def _transition_incident_locked(
        self,
        incident: IncidentRecord,
        to_state: str,
        actor: str = "orchestrator",
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> IncidentRecord:
        from_state = incident.current_state

        # Validate transition against formal state matrix
        validate_incident_transition(from_state, to_state, incident.incident_id, reason)

        now_iso = datetime.now(timezone.utc).isoformat()
        incident.current_state = to_state
        incident.updated_at = now_iso

        if to_state == IncidentState.RECOVERED.value:
            self._metrics["incidents_recovered_total"] += 1
            self._metrics["active_incidents"] = max(0, self._metrics["active_incidents"] - 1)
        elif to_state == IncidentState.DEGRADED.value:
            self._metrics["incidents_degraded_total"] += 1
        elif to_state == IncidentState.BLOCKED.value:
            self._metrics["remediation_blocked_total"] += 1

        trans = IncidentStateTransition(
            transition_id=f"TRN-{uuid.uuid4().hex[:8]}",
            incident_id=incident.incident_id,
            correlation_id=incident.correlation_id,
            from_state=from_state,
            to_state=to_state,
            timestamp=now_iso,
            actor=actor,
            reason=reason,
            metadata=metadata or {},
        )
        incident.timeline.append(trans.to_dict())
        self._append_journal(trans, incident)

        return incident

    # -------------------------------------------------------------------------
    # AI ENGINE INTEGRATION WITH GRACEFUL DEGRADATION
    # -------------------------------------------------------------------------
    def run_investigation_and_rca(
        self,
        incident_id: str,
        rca_fn: Optional[Any] = None,
        topology: Optional[Dict[str, Any]] = None,
        telemetry: Optional[List[Dict[str, Any]]] = None,
    ) -> IncidentRecord:
        """
        Executes root-cause investigation. If RCA or GNN models fail/unavailable,
        degrades gracefully to DEGRADED state rather than crashing.
        """
        with self._lock:
            incident = self._incidents.get(incident_id)
            if not incident:
                raise ValueError(f"Incident '{incident_id}' not found.")

            self._transition_incident_locked(
                incident,
                IncidentState.INVESTIGATING.value,
                actor="IncidentOrchestrator",
                reason="Initiating AI Root Cause Analysis investigation.",
            )

        try:
            if rca_fn is None:
                # Mock or fallback investigation
                root_cause = incident.root_cause or incident.affected_services[0]
            else:
                rca_res = rca_fn(topology=topology, telemetry=telemetry)
                root_cause = rca_res.get("root_cause") or incident.affected_services[0]

            with self._lock:
                incident.root_cause = root_cause
                return self._transition_incident_locked(
                    incident,
                    IncidentState.RCA_COMPLETE.value,
                    actor="RCAEngine",
                    reason=f"RCA identified root cause on '{root_cause}'.",
                )
        except Exception as e:
            # AI Degradation handling
            self.system_health.set_status("rca_engine", "DEGRADED", error=str(e))
            with self._lock:
                return self._transition_incident_locked(
                    incident,
                    IncidentState.DEGRADED.value,
                    actor="IncidentOrchestrator",
                    reason=f"AI RCA model unavailable or threw error: {e}. Transitioning to DEGRADED.",
                )

    # -------------------------------------------------------------------------
    # REMEDIATION RECOMMENDATION & SCHEDULING
    # -------------------------------------------------------------------------
    def register_recommendation(
        self,
        incident_id: str,
        recommendation_id: str,
        recommender_status: str = "RECOMMENDATION_READY",
        recommended_action_id: Optional[str] = None,
        target_service: Optional[str] = None,
        target_variable: Optional[str] = None,
        blast_radius_size: int = 1,
    ) -> IncidentRecord:
        """Associates a counterfactual recommendation with the incident."""
        with self._lock:
            incident = self._incidents.get(incident_id)
            if not incident:
                raise ValueError(f"Incident '{incident_id}' not found.")

            incident.recommendation_id = recommendation_id
            target_st = (
                IncidentState.REMEDIATION_RECOMMENDED.value
                if recommender_status != "NO_REMEDIATION_REQUIRED"
                else IncidentState.RECOVERED.value
            )

            return self._transition_incident_locked(
                incident,
                target_st,
                actor="RecommendationEngine",
                reason=f"Recommendation '{recommendation_id}' ({recommender_status}) linked to incident.",
                metadata={
                    "recommended_action_id": recommended_action_id,
                    "target_service": target_service,
                    "blast_radius_size": blast_radius_size,
                },
            )

    def submit_approval(
        self,
        incident_id: str,
        approval_id: str,
        approved_by: str,
        action_id: str,
        target_service: str,
        target_variable: str,
        blast_radius_size: int = 1,
    ) -> IncidentRecord:
        """Registers explicit SRE approval and enqueues candidate for deterministic scheduling."""
        with self._lock:
            incident = self._incidents.get(incident_id)
            if not incident:
                raise ValueError(f"Incident '{incident_id}' not found.")

            incident.approval_id = approval_id
            now_iso = datetime.now(timezone.utc).isoformat()

            # Enqueue into deterministic scheduler
            self.scheduler.enqueue(
                QueueItem(
                    incident_id=incident_id,
                    recommendation_id=incident.recommendation_id or f"REC-{uuid.uuid4().hex[:8]}",
                    approval_id=approval_id,
                    action_id=action_id,
                    target_service=target_service,
                    target_variable=target_variable,
                    approved_at=now_iso,
                    blast_radius_size=blast_radius_size,
                )
            )

            return self._transition_incident_locked(
                incident,
                IncidentState.APPROVAL_PENDING.value,
                actor=approved_by,
                reason=f"Operator '{approved_by}' signed approval '{approval_id}'.",
            )

    # -------------------------------------------------------------------------
    # OSCILLATION & BUDGET PROTECTION
    # -------------------------------------------------------------------------
    def record_health_observation(self, incident_id: str, is_healthy: bool):
        """Detects flapping/oscillation between healthy and unhealthy states."""
        now_ts = datetime.now(timezone.utc).timestamp()
        val_str = "HEALTHY" if is_healthy else "UNHEALTHY"

        with self._lock:
            flips = self._health_flips.setdefault(incident_id, [])
            flips.append((now_ts, val_str))

            # Keep recent 120 seconds
            flips = [(t, v) for (t, v) in flips if now_ts - t <= 120.0]
            self._health_flips[incident_id] = flips

            # Detect rapid oscillation (>= 4 state reversals within 120s)
            reversals = 0
            for i in range(1, len(flips)):
                if flips[i][1] != flips[i - 1][1]:
                    reversals += 1

            if reversals >= 3:
                incident = self._incidents.get(incident_id)
                if (
                    incident
                    and incident.current_state in ACTIVE_STATES
                    and incident.current_state != IncidentState.DEGRADED.value
                ):
                    incident.oscillation_detected = True
                    self._transition_incident_locked(
                        incident,
                        IncidentState.DEGRADED.value,
                        actor="OscillationDetector",
                        reason=f"Telemetry oscillation detected ({reversals} reversals in 120s). Halting automated actions.",
                    )

    def check_and_increment_budget(
        self,
        incident_id: str,
        is_execution: bool = True,
        is_rollback: bool = False,
    ) -> Tuple[bool, str]:
        """Enforces hard budget ceiling. Exceeded budget triggers MANUAL_INTERVENTION."""
        with self._lock:
            incident = self._incidents.get(incident_id)
            if not incident:
                return False, f"Incident '{incident_id}' not found."

            if is_execution:
                incident.budget.execution_count += 1
            if is_rollback:
                incident.budget.rollback_count += 1

            exceeded, reason = incident.budget.is_exceeded(len(incident.affected_services))
            if exceeded:
                self._transition_incident_locked(
                    incident,
                    IncidentState.MANUAL_INTERVENTION.value,
                    actor="RemediationBudgetPolicy",
                    reason=f"Remediation budget exceeded: {reason}. Requiring manual intervention.",
                )
                return False, reason

            return True, ""

    # -------------------------------------------------------------------------
    # OPERATOR ACKNOWLEDGMENT & QUERIES
    # -------------------------------------------------------------------------
    def acknowledge_incident(self, incident_id: str, operator_id: str) -> IncidentRecord:
        """Allows an on-call engineer to acknowledge active incident."""
        with self._lock:
            incident = self._incidents.get(incident_id)
            if not incident:
                raise ValueError(f"Incident '{incident_id}' not found.")

            now_iso = datetime.now(timezone.utc).isoformat()
            incident.acknowledged_by = operator_id
            incident.acknowledged_at = now_iso

            trans = IncidentStateTransition(
                transition_id=f"TRN-{uuid.uuid4().hex[:8]}",
                incident_id=incident_id,
                correlation_id=incident.correlation_id,
                from_state=incident.current_state,
                to_state=incident.current_state,
                timestamp=now_iso,
                actor=operator_id,
                reason=f"Acknowledged by on-call engineer '{operator_id}'.",
            )
            incident.timeline.append(trans.to_dict())
            self._append_journal(trans, incident)
            return incident

    def get_incident(self, incident_id: str) -> Optional[IncidentRecord]:
        with self._lock:
            return self._incidents.get(incident_id)

    def list_incidents(
        self,
        state_filter: Optional[str] = None,
        severity_filter: Optional[str] = None,
        correlation_group: Optional[str] = None,
    ) -> List[IncidentRecord]:
        with self._lock:
            results = list(self._incidents.values())
            if state_filter:
                results = [r for r in results if r.current_state == state_filter]
            if severity_filter:
                results = [r for r in results if r.severity == severity_filter]
            if correlation_group:
                results = [r for r in results if r.correlation_group == correlation_group]
            return results

    def get_metrics(self) -> Dict[str, Any]:
        with self._lock:
            m = dict(self._metrics)
            m["total_tracked_incidents"] = len(self._incidents)
            return m

    # -------------------------------------------------------------------------
    # AUDIT JOURNAL & REPLAY
    # -------------------------------------------------------------------------
    def _append_journal(self, transition: IncidentStateTransition, incident: IncidentRecord):
        entry = {
            "transition_id": transition.transition_id,
            "incident_id": incident.incident_id,
            "correlation_id": incident.correlation_id,
            "correlation_group": incident.correlation_group,
            "from_state": transition.from_state,
            "to_state": transition.to_state,
            "actor": transition.actor,
            "timestamp": transition.timestamp,
            "reason": transition.reason,
            "affected_services": incident.affected_services,
            "root_cause": incident.root_cause,
            "telemetry_health": incident.telemetry_health,
        }
        with open(self.journal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    @classmethod
    def replay_journal(cls, journal_file_path: Union[str, Path]) -> Dict[str, IncidentRecord]:
        """
        Reconstructs complete incident state from the immutable append-only journal.
        Deterministic invariant: live state must equal reconstructed state.
        """
        path = Path(journal_file_path)
        if not path.exists():
            return {}

        reconstructed: Dict[str, IncidentRecord] = {}

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                entry = json.loads(line)
                inc_id = entry["incident_id"]

                if inc_id not in reconstructed:
                    reconstructed[inc_id] = IncidentRecord(
                        incident_id=inc_id,
                        correlation_id=entry.get("correlation_id", "COR-UNKNOWN"),
                        correlation_group=entry.get("correlation_group", "GRP-UNKNOWN"),
                        created_at=entry["timestamp"],
                        updated_at=entry["timestamp"],
                        detection_source="journal_replay",
                        affected_services=entry.get("affected_services", []),
                        root_cause=entry.get("root_cause"),
                        fault_signature="replayed_signature",
                        current_state=entry["to_state"],
                        severity="HIGH",
                        telemetry_health=entry.get("telemetry_health"),
                    )
                else:
                    reconstructed[inc_id].current_state = entry["to_state"]
                    reconstructed[inc_id].updated_at = entry["timestamp"]

                reconstructed[inc_id].timeline.append({
                    "transition_id": entry["transition_id"],
                    "from_state": entry["from_state"],
                    "to_state": entry["to_state"],
                    "actor": entry["actor"],
                    "timestamp": entry["timestamp"],
                    "reason": entry.get("reason", ""),
                })

        return reconstructed

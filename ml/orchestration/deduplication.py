"""
Deterministic Incident Deduplication Engine for CausalOps (Phase 6).

Implements deterministic fingerprinting to aggregate repeated anomaly events
into a single authoritative incident record.

NON-NEGOTIABLE REQUIREMENTS:
- Do NOT use ground-truth experiment labels (must run purely on observed telemetry/signals).
- Fingerprint based on: affected service, anomaly variable, fault signature,
  temporal proximity window, and topological neighborhood.
- Repetition invariant: The same underlying fault repeated 10 times produces
  EXACTLY ONE active incident, incrementing observation count and extending duration.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
from typing import Dict, Optional, Any, List, Set, Tuple


@dataclass
class DeduplicationResult:
    """Result of incident deduplication check."""
    is_duplicate: bool
    incident_id: str
    fingerprint: str
    repetition_count: int
    first_seen: str
    last_seen: str
    suppressed_event_count: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class IncidentDeduplicationEngine:
    """
    Constructs deterministic telemetry fingerprints and aggregates duplicate incident alerts.
    """

    # Neighborhood mapping for topological clustering
    TOPOLOGY_NEIGHBORS = {
        "inventory-db": ["inventory-service"],
        "inventory-service": ["inventory-db", "order-service"],
        "order-service": ["inventory-service", "payment-service", "api-gateway"],
        "payment-service": ["order-service"],
        "api-gateway": ["order-service"],
    }

    def __init__(self, time_window_seconds: float = 60.0):
        self.time_window = time_window_seconds
        # fingerprint -> incident record tracking
        self._fingerprint_to_incident: Dict[str, str] = {}
        self._fingerprint_state: Dict[str, Dict[str, Any]] = {}

    def compute_fingerprint(
        self,
        service: str,
        primary_variable: str,
        fault_signature: str,
        timestamp: Optional[datetime] = None,
    ) -> str:
        """
        Constructs a deterministic hash from service, variable, fault signature,
        topology neighborhood, and time-window bucket.
        """
        ts = timestamp or datetime.now(timezone.utc)
        # Bucket by time_window
        epoch_seconds = ts.timestamp()
        time_bucket = int(epoch_seconds // self.time_window)

        # Topological cluster
        neighbors = sorted(self.TOPOLOGY_NEIGHBORS.get(service, []))
        cluster_str = f"{service}:{','.join(neighbors)}"

        raw_payload = f"{service}|{primary_variable}|{fault_signature}|{cluster_str}|{time_bucket}"
        return hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()[:16]

    def process_anomaly_event(
        self,
        service: str,
        primary_variable: str,
        fault_signature: str,
        generated_incident_id: str,
        timestamp: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DeduplicationResult:
        """
        Evaluates an incoming anomaly event against active fingerprints.
        If an active matching fingerprint is found within the window,
        suppresses creation of a new incident and returns the existing incident ID.
        """
        ts = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc)
        ts_iso = ts.isoformat()

        # Compute bucketed fingerprint
        fp = self.compute_fingerprint(service, primary_variable, fault_signature, ts)

        # Also check previous bucket for boundary continuity
        prev_ts = ts - timedelta(seconds=self.time_window * 0.5)
        prev_fp = self.compute_fingerprint(service, primary_variable, fault_signature, prev_ts)

        active_fp = fp if fp in self._fingerprint_to_incident else (prev_fp if prev_fp in self._fingerprint_to_incident else fp)

        if active_fp in self._fingerprint_to_incident:
            # Duplicate detected!
            existing_id = self._fingerprint_to_incident[active_fp]
            state = self._fingerprint_state[active_fp]
            state["repetition_count"] += 1
            state["last_seen"] = ts_iso
            state["suppressed_count"] += 1

            return DeduplicationResult(
                is_duplicate=True,
                incident_id=existing_id,
                fingerprint=active_fp,
                repetition_count=state["repetition_count"],
                first_seen=state["first_seen"],
                last_seen=ts_iso,
                suppressed_event_count=state["suppressed_count"],
                metadata=metadata or {},
            )
        else:
            # New distinct incident
            self._fingerprint_to_incident[active_fp] = generated_incident_id
            self._fingerprint_state[active_fp] = {
                "incident_id": generated_incident_id,
                "first_seen": ts_iso,
                "last_seen": ts_iso,
                "repetition_count": 1,
                "suppressed_count": 0,
            }

            return DeduplicationResult(
                is_duplicate=False,
                incident_id=generated_incident_id,
                fingerprint=active_fp,
                repetition_count=1,
                first_seen=ts_iso,
                last_seen=ts_iso,
                suppressed_event_count=0,
                metadata=metadata or {},
            )

    def clear(self):
        """Clears all deduplication state."""
        self._fingerprint_to_incident.clear()
        self._fingerprint_state.clear()

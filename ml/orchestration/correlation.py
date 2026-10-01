"""
Topological & Causal-Constrained Incident Correlation Engine (Phase 6).

Distinguishes primary root-cause failure origins from secondary downstream propagation
symptoms using microservice dependency topology and temporal propagation lags.

NON-NEGOTIABLE REQUIREMENTS:
- Use physical causal topology:
    inventory-db -> inventory-service -> order-service -> api-gateway
    order-service -> payment-service
- Distinguish root-cause incident from downstream symptom.
- Do NOT infer correlation from ground-truth labels.
- Groups downstream cascade into a unified correlation group to prevent
  redundant, conflicting, or circular remediation attempts.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import uuid
from typing import Dict, List, Optional, Any, Set, Tuple


@dataclass
class CorrelationResult:
    """Result of topological and temporal incident correlation."""
    is_correlated: bool
    incident_id: str
    parent_incident_id: Optional[str]
    correlation_group_id: str
    role: str  # "ROOT_CAUSE" or "DOWNSTREAM_SYMPTOM"
    topological_distance: int
    causal_propagation_path: List[str]
    explanation: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class IncidentCorrelationEngine:
    """
    Evaluates microservice incidents for causal propagation correlation.
    """

    # Directed dependency topology in CausalOps:
    # A -> B means A is called by B (or B depends on A).
    # Traffic flows Gateway -> Order -> Inventory -> DB;
    # Errors and latency propagate DB -> Inventory -> Order -> Gateway.
    PROPAGATION_DAG = {
        "inventory-db": ["inventory-service", "order-service", "api-gateway"],
        "inventory-service": ["order-service", "api-gateway"],
        "order-service": ["api-gateway"],
        "payment-service": ["order-service", "api-gateway"],
        "api-gateway": [],
    }

    # Maximum temporal lag between upstream root cause and downstream symptom (seconds)
    MAX_PROPAGATION_LAG_SECONDS = 30.0

    def __init__(self, max_lag_seconds: float = 30.0):
        self.max_lag = max_lag_seconds
        # correlation_group_id -> dict of incident records
        self._groups: Dict[str, Dict[str, Any]] = {}
        # incident_id -> correlation_group_id
        self._incident_to_group: Dict[str, str] = {}

    def correlate_incident(
        self,
        incident_id: str,
        service: str,
        timestamp: datetime,
        active_incidents: List[Dict[str, Any]],
    ) -> CorrelationResult:
        """
        Determines if an incident on `service` is a downstream symptom of an existing
        active upstream incident, or if it represents an independent root-cause incident.
        """
        ts = timestamp.astimezone(timezone.utc) if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)

        # Check existing active incidents for an upstream root cause
        for parent in active_incidents:
            parent_id = parent["incident_id"]
            if parent_id == incident_id:
                continue

            parent_service = parent.get("affected_service") or parent.get("root_cause")
            if not parent_service:
                continue

            parent_ts = parent.get("created_at")
            if isinstance(parent_ts, str):
                p_dt = datetime.fromisoformat(parent_ts).astimezone(timezone.utc)
            elif isinstance(parent_ts, datetime):
                p_dt = parent_ts.astimezone(timezone.utc)
            else:
                p_dt = ts

            # Temporal ordering: Downstream symptom occurs at or after upstream root cause
            lag = (ts - p_dt).total_seconds()
            if lag < -2.0 or lag > self.max_lag:
                continue

            # Topological check: Is `service` downstream of `parent_service`?
            allowed_downstream = self.PROPAGATION_DAG.get(parent_service, [])
            if service in allowed_downstream:
                # Correlated downstream symptom!
                group_id = self._incident_to_group.get(parent_id) or f"GRP-{uuid.uuid4().hex[:10]}"
                self._incident_to_group[parent_id] = group_id
                self._incident_to_group[incident_id] = group_id

                path = [parent_service]
                if parent_service == "inventory-db" and service == "order-service":
                    path.append("inventory-service")
                elif parent_service == "inventory-db" and service == "api-gateway":
                    path.extend(["inventory-service", "order-service"])
                elif parent_service == "inventory-service" and service == "api-gateway":
                    path.append("order-service")
                path.append(service)

                distance = len(path) - 1

                explanation = (
                    f"Incident on '{service}' is correlated as a downstream symptom of root-cause "
                    f"incident '{parent_id}' on '{parent_service}' (propagation path: {' -> '.join(path)}, lag: {lag:.1f}s)."
                )

                return CorrelationResult(
                    is_correlated=True,
                    incident_id=incident_id,
                    parent_incident_id=parent_id,
                    correlation_group_id=group_id,
                    role="DOWNSTREAM_SYMPTOM",
                    topological_distance=distance,
                    causal_propagation_path=path,
                    explanation=explanation,
                )

        # No upstream parent found: This is an independent root cause
        new_group_id = f"GRP-{uuid.uuid4().hex[:10]}"
        self._incident_to_group[incident_id] = new_group_id

        return CorrelationResult(
            is_correlated=False,
            incident_id=incident_id,
            parent_incident_id=None,
            correlation_group_id=new_group_id,
            role="ROOT_CAUSE",
            topological_distance=0,
            causal_propagation_path=[service],
            explanation=f"Incident on '{service}' is an independent root cause origin.",
        )

    def get_group_for_incident(self, incident_id: str) -> Optional[str]:
        return self._incident_to_group.get(incident_id)

    def clear(self):
        self._groups.clear()
        self._incident_to_group.clear()

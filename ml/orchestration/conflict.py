"""
Remediation Conflict Detection Engine for CausalOps (Phase 6).

Evaluates concurrent, overlapping, or dependent remediation actions to identify
and prevent race conditions, resource contention, and cascading interference.

NON-NEGOTIABLE SAFETY CONSTRAINTS:
- If any conflict exists, execution must be strictly BLOCKED.
- Do NOT execute conflicting actions automatically.
- Expose typed conflict diagnostics:
    conflict_type, incident_ids, action_ids, affected_resource, explanation.
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional, Any, Set, Tuple


class ConflictType(str, Enum):
    """Categorical classification of remediation conflicts."""
    TARGET_SERVICE_COLLISION = "TARGET_SERVICE_COLLISION"
    VARIABLE_DEPENDENCY_COLLISION = "VARIABLE_DEPENDENCY_COLLISION"
    ACTIVE_ROLLBACK_COLLISION = "ACTIVE_ROLLBACK_COLLISION"
    SHARED_RESOURCE_CONTENTION = "SHARED_RESOURCE_CONTENTION"
    BLAST_RADIUS_BUDGET_EXCEEDED = "BLAST_RADIUS_BUDGET_EXCEEDED"


@dataclass
class ConflictEvaluation:
    """Detailed diagnostic of detected remediation conflicts."""
    has_conflict: bool
    conflict_type: Optional[str]
    incident_ids: List[str]
    action_ids: List[str]
    affected_resource: str
    explanation: str
    blocked_action_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ConflictDetector:
    """
    Evaluates proposed remediation executions against currently executing or scheduled actions.
    """

    MAX_CUMULATIVE_BLAST_RADIUS = 4

    # Inter-variable mutual interference matrix:
    # If Action 1 modifies Var A, Action 2 modifying Var B may conflict.
    MUTUAL_EXCLUSION_VARIABLES = {
        ("inventory-service.error_rate", "order-service.p99_latency"),
        ("inventory-db.db_latency", "inventory-service.p99_latency"),
    }

    @classmethod
    def evaluate_conflict(
        cls,
        candidate_incident_id: str,
        candidate_action_id: str,
        candidate_target_service: str,
        candidate_target_variable: str,
        candidate_blast_radius_size: int,
        active_remediations: List[Dict[str, Any]],
    ) -> ConflictEvaluation:
        """
        Evaluates candidate action against all currently executing or pending remediations.
        """
        for active in active_remediations:
            active_inc_id = active.get("incident_id", "")
            active_act_id = active.get("action_id", "")
            active_service = active.get("target_service", "")
            active_variable = active.get("target_variable", "")
            is_rollback = active.get("is_rollback", False)
            active_blast_size = active.get("blast_radius_size", 1)

            # 1. Target Service Collision (Two actions target the same microservice)
            if candidate_target_service == active_service:
                return ConflictEvaluation(
                    has_conflict=True,
                    conflict_type=ConflictType.TARGET_SERVICE_COLLISION.value,
                    incident_ids=[active_inc_id, candidate_incident_id],
                    action_ids=[active_act_id, candidate_action_id],
                    affected_resource=f"service:{candidate_target_service}",
                    explanation=(
                        f"Action '{candidate_action_id}' (incident '{candidate_incident_id}') targets service "
                        f"'{candidate_target_service}' which is currently locked by active remediation '{active_act_id}'."
                    ),
                    blocked_action_ids=[candidate_action_id],
                )

            # 2. Active Rollback Collision
            if is_rollback and (
                candidate_target_service == active_service
                or candidate_target_service in active.get("blast_radius_services", [])
            ):
                return ConflictEvaluation(
                    has_conflict=True,
                    conflict_type=ConflictType.ACTIVE_ROLLBACK_COLLISION.value,
                    incident_ids=[active_inc_id, candidate_incident_id],
                    action_ids=[active_act_id, candidate_action_id],
                    affected_resource=f"rollback:{active_service}",
                    explanation=(
                        f"Active rollback of '{active_act_id}' on '{active_service}' conflicts with "
                        f"candidate action '{candidate_action_id}' on overlapping dependency path."
                    ),
                    blocked_action_ids=[candidate_action_id],
                )

            # 3. Variable Dependency Interference
            pair1 = (f"{active_service}.{active_variable}", f"{candidate_target_service}.{candidate_target_variable}")
            pair2 = (f"{candidate_target_service}.{candidate_target_variable}", f"{active_service}.{active_variable}")
            if pair1 in cls.MUTUAL_EXCLUSION_VARIABLES or pair2 in cls.MUTUAL_EXCLUSION_VARIABLES:
                return ConflictEvaluation(
                    has_conflict=True,
                    conflict_type=ConflictType.VARIABLE_DEPENDENCY_COLLISION.value,
                    incident_ids=[active_inc_id, candidate_incident_id],
                    action_ids=[active_act_id, candidate_action_id],
                    affected_resource=f"variable:{active_variable} <-> {candidate_target_variable}",
                    explanation=(
                        f"Remediation '{candidate_action_id}' mutates '{candidate_target_variable}' which interferes "
                        f"with active action '{active_act_id}' controlling '{active_variable}'."
                    ),
                    blocked_action_ids=[candidate_action_id],
                )

            # 4. Cumulative Blast Radius Budget Exceeded
            cumulative_blast = candidate_blast_radius_size + active_blast_size
            if cumulative_blast > cls.MAX_CUMULATIVE_BLAST_RADIUS:
                return ConflictEvaluation(
                    has_conflict=True,
                    conflict_type=ConflictType.BLAST_RADIUS_BUDGET_EXCEEDED.value,
                    incident_ids=[active_inc_id, candidate_incident_id],
                    action_ids=[active_act_id, candidate_action_id],
                    affected_resource="cluster:blast_radius_budget",
                    explanation=(
                        f"Cumulative blast radius of concurrent actions ({cumulative_blast} services) "
                        f"exceeds maximum cluster safety budget of {cls.MAX_CUMULATIVE_BLAST_RADIUS} services."
                    ),
                    blocked_action_ids=[candidate_action_id],
                )

        # No conflicts detected
        return ConflictEvaluation(
            has_conflict=False,
            conflict_type=None,
            incident_ids=[candidate_incident_id],
            action_ids=[candidate_action_id],
            affected_resource="none",
            explanation="No concurrent remediation or dependency conflicts detected.",
            blocked_action_ids=[],
        )

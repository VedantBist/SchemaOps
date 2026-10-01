"""
Remediation Action Catalog & Microservice Action Ontology for CausalOps (Phase 4).

Defines the formal action ontology, physical parameters, reversibility classes,
risk categories, and action catalog registry for safe self-healing recommendations.

Safety Boundaries:
- Phase 4 is STRICTLY an advisory recommendation and simulation system.
- NO automated infrastructure mutations, container restarts, or command executions.
- `approval_required: True` is mandatory on all actionable remediation candidates.
- Actions are mapped directly to physical variables in the Causal SCM.
"""

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union


class RemediationActionType(str, Enum):
    """Supported remediation action archetypes mapped to causal variables."""
    REDUCE_DB_LATENCY = "REDUCE_DB_LATENCY"
    REDUCE_SERVICE_LATENCY = "REDUCE_SERVICE_LATENCY"
    RESTORE_ERROR_RATE = "RESTORE_ERROR_RATE"
    RESTORE_SERVICE_HEALTH = "RESTORE_SERVICE_HEALTH"
    RESTORE_POOL_UTILIZATION = "RESTORE_POOL_UTILIZATION"


class ReversibilityLevel(str, Enum):
    """Reversibility classification indicating operational rollback complexity."""
    HIGH = "HIGH"      # Immediate zero-cost rollback (e.g., rate limit release, connection reset)
    MEDIUM = "MEDIUM"  # Rollback takes seconds/minutes or temporary cache warm-up needed
    LOW = "LOW"        # Involves permanent state change or extensive teardown


class RiskClass(str, Enum):
    """Operational blast radius and service-level risk rating."""
    LOW = "LOW"        # Negligible risk of service degradation
    MEDIUM = "MEDIUM"  # Potential temporary queue shedding or cache miss latency
    HIGH = "HIGH"      # Pod teardown or upstream caller disruption potential


@dataclass
class RemediationAction:
    """
    Formal representation of a candidate remediation action.
    
    Attributes:
        action_id: Unique canonical identifier (e.g. ACT-DB-01)
        name: Human-readable action name
        action_type: RemediationActionType enum string
        target_service: Microservice node name in canonical topology
        target_variable: Primary physical variable intervened upon
        secondary_variables: Optional secondary variables affected
        mechanism: Detailed description of the operational intervention
        description: High-level overview of action purpose
        reversibility: ReversibilityLevel enum string
        risk_class: RiskClass enum string
        complexity: Operational execution complexity ("LOW", "MEDIUM", "HIGH")
        playbook_ref: Canonical runbook/playbook documentation identifier
        approval_required: Must ALWAYS be True for Phase 4 recommendations
        can_simulate: Flag indicating counterfactual simulation support (True)
        physical_bounds: Physical range bounds (min, max) for target variable
        nominal_target_hint: Optional predefined healthy target value
    """
    action_id: str
    name: str
    action_type: str
    target_service: str
    target_variable: str
    mechanism: str
    description: str
    reversibility: str
    risk_class: str
    complexity: str
    playbook_ref: str
    secondary_variables: List[str] = field(default_factory=list)
    approval_required: bool = True
    can_simulate: bool = True
    physical_bounds: Dict[str, Tuple[Optional[float], Optional[float]]] = field(
        default_factory=lambda: {"p99_latency": (0.0, None), "error_rate": (0.0, 100.0)}
    )
    nominal_target_hint: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serializes action definition to dictionary."""
        d = asdict(self)
        # Ensure enums are serialized as strings
        if isinstance(d.get("action_type"), Enum):
            d["action_type"] = d["action_type"].value
        if isinstance(d.get("reversibility"), Enum):
            d["reversibility"] = d["reversibility"].value
        if isinstance(d.get("risk_class"), Enum):
            d["risk_class"] = d["risk_class"].value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RemediationAction":
        """Instantiates RemediationAction from dictionary."""
        data_clean = dict(data)
        # Guarantee approval_required is True
        data_clean["approval_required"] = True
        return cls(**data_clean)


class ActionCatalog:
    """
    Catalog registry of remediation actions indexed by service, variable, and action ID.
    """

    def __init__(self, actions: Optional[List[RemediationAction]] = None):
        self._actions_by_id: Dict[str, RemediationAction] = {}
        self._actions_by_service: Dict[str, List[RemediationAction]] = {}
        if actions:
            for action in actions:
                self.register(action)

    def register(self, action: RemediationAction) -> None:
        """Registers a remediation action in the catalog."""
        # Enforce safety constraint: approval_required must be True
        action.approval_required = True
        self._actions_by_id[action.action_id] = action
        if action.target_service not in self._actions_by_service:
            self._actions_by_service[action.target_service] = []
        self._actions_by_service[action.target_service].append(action)

    def get_action(self, action_id: str) -> Optional[RemediationAction]:
        """Retrieves action by its canonical ID."""
        return self._actions_by_id.get(action_id)

    def get_candidates(
        self,
        service: str,
        variable: Optional[str] = None
    ) -> List[RemediationAction]:
        """
        Retrieves candidate actions for a given target service and optional variable.
        """
        candidates = self._actions_by_service.get(service, [])
        if variable is None:
            return list(candidates)

        # Filter or rank candidates by variable match
        matched = [a for a in candidates if a.target_variable == variable or variable in a.secondary_variables]
        return matched if matched else list(candidates)

    def all_actions(self) -> List[RemediationAction]:
        """Returns all registered actions."""
        return list(self._actions_by_id.values())

    def to_dict(self) -> Dict[str, Any]:
        """Serializes entire catalog to dictionary."""
        return {
            "version": "1.0.0",
            "count": len(self._actions_by_id),
            "safety_policy": {
                "autonomous_mutation_enabled": False,
                "approval_required_enforced": True,
                "read_only_simulation_mode": True,
            },
            "actions": [a.to_dict() for a in self.all_actions()],
        }

    def export_json(self, path: Union[str, Path]) -> None:
        """Exports catalog to a JSON file."""
        out_p = Path(path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load_json(cls, path: Union[str, Path]) -> "ActionCatalog":
        """Loads action catalog from a JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        actions = [RemediationAction.from_dict(item) for item in data.get("actions", [])]
        return cls(actions)

    @classmethod
    def get_default_catalog(cls) -> "ActionCatalog":
        """
        Creates and populates the canonical CausalOps microservice remediation catalog.
        """
        catalog = cls()

        # -------------------------------------------------------------
        # 1. INVENTORY-DB ACTIONS
        # -------------------------------------------------------------
        catalog.register(RemediationAction(
            action_id="ACT-DB-01",
            name="Terminate Blocking Queries & Release Table Locks",
            action_type=RemediationActionType.REDUCE_DB_LATENCY.value,
            target_service="inventory-db",
            target_variable="db_latency",
            secondary_variables=["pool_utilization"],
            mechanism="Issues pg_cancel_backend / pg_terminate_backend on blocked transactions holding ExclusiveLock on inventory tables",
            description="Eliminates database thread contention and queue stalling by clearing hung queries causing backpressure.",
            reversibility=ReversibilityLevel.HIGH.value,
            risk_class=RiskClass.LOW.value,
            complexity="LOW",
            playbook_ref="PB-DB-01: Postgres Lock Contention & Slow Query Recovery",
            physical_bounds={"db_latency": (0.0, None), "pool_utilization": (0.0, 100.0)},
        ))

        catalog.register(RemediationAction(
            action_id="ACT-DB-02",
            name="Drain & Re-initialize Database Connection Pool",
            action_type=RemediationActionType.RESTORE_POOL_UTILIZATION.value,
            target_service="inventory-db",
            target_variable="pool_utilization",
            secondary_variables=["db_latency"],
            mechanism="Signals connection pool proxy to gracefully evict idle/leaked connections and reset connection pool headroom",
            description="Restores database connection availability when pool slots are exhausted.",
            reversibility=ReversibilityLevel.HIGH.value,
            risk_class=RiskClass.LOW.value,
            complexity="LOW",
            playbook_ref="PB-DB-02: Connection Pool Exhaustion Mitigation",
            physical_bounds={"pool_utilization": (0.0, 100.0), "db_latency": (0.0, None)},
        ))

        # -------------------------------------------------------------
        # 2. INVENTORY-SERVICE ACTIONS
        # -------------------------------------------------------------
        catalog.register(RemediationAction(
            action_id="ACT-INV-01",
            name="Scale Inventory Worker Replicas & Clear RPC Backlog",
            action_type=RemediationActionType.REDUCE_SERVICE_LATENCY.value,
            target_service="inventory-service",
            target_variable="p99_latency",
            secondary_variables=["pool_utilization"],
            mechanism="Horizontally autoscales inventory-service pods (+2 replicas) and sheds saturated worker queues",
            description="Absorbs high service latency and worker queue backlog by expanding horizontal capacity.",
            reversibility=ReversibilityLevel.HIGH.value,
            risk_class=RiskClass.LOW.value,
            complexity="LOW",
            playbook_ref="PB-INV-01: Inventory Worker Saturation Recovery",
            physical_bounds={"p99_latency": (0.0, None)},
        ))

        catalog.register(RemediationAction(
            action_id="ACT-INV-02",
            name="Engage Circuit Breaker & Fallback to Local Redis Cache",
            action_type=RemediationActionType.RESTORE_SERVICE_HEALTH.value,
            target_service="inventory-service",
            target_variable="p99_latency",
            secondary_variables=["error_rate"],
            mechanism="Trips downstream DB circuit breaker to serve cached inventory availability, isolating callers from database wait",
            description="Protects upstream order processing by falling back to cached stock counts.",
            reversibility=ReversibilityLevel.HIGH.value,
            risk_class=RiskClass.MEDIUM.value,
            complexity="MEDIUM",
            playbook_ref="PB-INV-02: Inventory Circuit Breaker Trip",
            physical_bounds={"p99_latency": (0.0, None), "error_rate": (0.0, 100.0)},
        ))

        catalog.register(RemediationAction(
            action_id="ACT-INV-03",
            name="Rolling Restart of Deadlocked Inventory Pods",
            action_type=RemediationActionType.RESTORE_ERROR_RATE.value,
            target_service="inventory-service",
            target_variable="error_rate",
            secondary_variables=["p99_latency"],
            mechanism="Performs phased rolling restart of degraded inventory pods to clear unhandled deadlock/crash state",
            description="Restores service reliability after unrecoverable application error cascade.",
            reversibility=ReversibilityLevel.MEDIUM.value,
            risk_class=RiskClass.MEDIUM.value,
            complexity="LOW",
            playbook_ref="PB-INV-03: Inventory Worker Rolling Restart",
            physical_bounds={"error_rate": (0.0, 100.0), "p99_latency": (0.0, None)},
        ))

        # -------------------------------------------------------------
        # 3. ORDER-SERVICE ACTIONS
        # -------------------------------------------------------------
        catalog.register(RemediationAction(
            action_id="ACT-ORD-01",
            name="Apply Ingress Rate Shedding & Flush Pending Order Queue",
            action_type=RemediationActionType.REDUCE_SERVICE_LATENCY.value,
            target_service="order-service",
            target_variable="p99_latency",
            secondary_variables=["error_rate"],
            mechanism="Enforces token-bucket admission control to discard queue overflow and relieve mediator thread pool",
            description="Relieves mediator queueing saturation and restores checkout pipeline throughput.",
            reversibility=ReversibilityLevel.HIGH.value,
            risk_class=RiskClass.MEDIUM.value,
            complexity="MEDIUM",
            playbook_ref="PB-ORD-01: Order Ingress Shedding",
            physical_bounds={"p99_latency": (0.0, None), "error_rate": (0.0, 100.0)},
        ))

        catalog.register(RemediationAction(
            action_id="ACT-ORD-02",
            name="Increase RPC Retry Backoff & Clear Deadlocked Requests",
            action_type=RemediationActionType.RESTORE_ERROR_RATE.value,
            target_service="order-service",
            target_variable="error_rate",
            secondary_variables=["p99_latency"],
            mechanism="Applies jittered exponential backoff and cancels hung outgoing client requests to break retry loops",
            description="Dissolves retry storm between order-service and downstream dependencies.",
            reversibility=ReversibilityLevel.HIGH.value,
            risk_class=RiskClass.LOW.value,
            complexity="LOW",
            playbook_ref="PB-ORD-02: Retry Storm Mitigation",
            physical_bounds={"error_rate": (0.0, 100.0), "p99_latency": (0.0, None)},
        ))

        # -------------------------------------------------------------
        # 4. PAYMENT-SERVICE ACTIONS
        # -------------------------------------------------------------
        catalog.register(RemediationAction(
            action_id="ACT-PAY-01",
            name="Failover Payment Route to Standby Acquirer Gateway",
            action_type=RemediationActionType.RESTORE_ERROR_RATE.value,
            target_service="payment-service",
            target_variable="error_rate",
            secondary_variables=["p99_latency"],
            mechanism="Switches outgoing payment gateway upstream DNS/VIP route to standby acquirer partner",
            description="Bypasses third-party gateway connection timeouts and payment validation failures.",
            reversibility=ReversibilityLevel.HIGH.value,
            risk_class=RiskClass.LOW.value,
            complexity="LOW",
            playbook_ref="PB-PAY-01: Payment Gateway Fallback Routing",
            physical_bounds={"error_rate": (0.0, 100.0), "p99_latency": (0.0, None)},
        ))

        catalog.register(RemediationAction(
            action_id="ACT-PAY-02",
            name="Graceful Container Restart & Refresh Auth/JWKS Cache",
            action_type=RemediationActionType.RESTORE_SERVICE_HEALTH.value,
            target_service="payment-service",
            target_variable="error_rate",
            secondary_variables=["p99_latency"],
            mechanism="Triggers graceful rolling restart of payment pods and evicts expired security token keys",
            description="Restores payment verification pipeline after internal auth crash or deadlock.",
            reversibility=ReversibilityLevel.MEDIUM.value,
            risk_class=RiskClass.MEDIUM.value,
            complexity="LOW",
            playbook_ref="PB-PAY-02: Payment Worker Pod Graceful Restart",
            physical_bounds={"error_rate": (0.0, 100.0), "p99_latency": (0.0, None)},
        ))

        catalog.register(RemediationAction(
            action_id="ACT-PAY-03",
            name="Autoscale Payment Service Replicas",
            action_type=RemediationActionType.REDUCE_SERVICE_LATENCY.value,
            target_service="payment-service",
            target_variable="p99_latency",
            secondary_variables=["error_rate"],
            mechanism="Adds worker pods to payment deployment to absorb external network latency delay",
            description="Mitigates queue build-up when external payment provider responses are sluggish.",
            reversibility=ReversibilityLevel.HIGH.value,
            risk_class=RiskClass.LOW.value,
            complexity="LOW",
            playbook_ref="PB-PAY-03: Payment Service Horizontal Scaling",
            physical_bounds={"p99_latency": (0.0, None), "error_rate": (0.0, 100.0)},
        ))

        # -------------------------------------------------------------
        # 5. API-GATEWAY ACTIONS
        # -------------------------------------------------------------
        catalog.register(RemediationAction(
            action_id="ACT-GW-01",
            name="Engage Ingress Rate Limiting & Shed 504 Timeouts",
            action_type=RemediationActionType.RESTORE_ERROR_RATE.value,
            target_service="api-gateway",
            target_variable="error_rate",
            secondary_variables=["p99_latency"],
            mechanism="Applies token bucket rate limiter at ingress edge, returning 429 to non-critical traffic to protect core routing",
            description="Protects API Gateway from cascade failure during widespread downstream timeouts.",
            reversibility=ReversibilityLevel.HIGH.value,
            risk_class=RiskClass.LOW.value,
            complexity="LOW",
            playbook_ref="PB-GW-01: Gateway Ingress Throttling",
            physical_bounds={"error_rate": (0.0, 100.0), "p99_latency": (0.0, None)},
        ))

        return catalog

"""
CausalOps Multi-Incident Orchestration & Systems Hardening Module (Phase 6).

Exports:
- IncidentState, IncidentStateTransition, validate_incident_transition
- TelemetryHealthTracker, ServiceHealthTracker, DependencyRecoveryTracker, SystemHealthRegistry
- IncidentDeduplicationEngine, DeduplicationResult
- IncidentCorrelationEngine, CorrelationResult
- ConflictDetector, ConflictEvaluation, ConflictType
- RemediationScheduler, QueueItem, SchedulingDecision
- IncidentOrchestrationManager, IncidentRecord, RemediationBudget
"""

from .incident_state import (
    IncidentState,
    IncidentStateTransition,
    InvalidIncidentTransitionError,
    validate_incident_transition,
    VALID_INCIDENT_TRANSITIONS,
    ACTIVE_STATES,
    TERMINAL_STATES,
)

from .health import (
    TelemetryFreshness,
    TelemetryHealthStatus,
    ServiceHealthStatus,
    DependencyRecoveryStatus,
    TelemetryReport,
    TelemetryHealthTracker,
    ServiceHealthTracker,
    DependencyRecoveryTracker,
    SystemHealthRegistry,
    CANONICAL_SERVICES,
)

from .deduplication import (
    IncidentDeduplicationEngine,
    DeduplicationResult,
)

from .correlation import (
    IncidentCorrelationEngine,
    CorrelationResult,
)

from .conflict import (
    ConflictType,
    ConflictEvaluation,
    ConflictDetector,
)

from .scheduler import (
    QueueItem,
    SchedulingDecision,
    RemediationScheduler,
)

from .incident_manager import (
    IncidentRecord,
    RemediationBudget,
    IncidentOrchestrationManager,
)

__all__ = [
    "IncidentState",
    "IncidentStateTransition",
    "InvalidIncidentTransitionError",
    "validate_incident_transition",
    "VALID_INCIDENT_TRANSITIONS",
    "ACTIVE_STATES",
    "TERMINAL_STATES",
    "TelemetryFreshness",
    "TelemetryHealthStatus",
    "ServiceHealthStatus",
    "DependencyRecoveryStatus",
    "TelemetryReport",
    "TelemetryHealthTracker",
    "ServiceHealthTracker",
    "DependencyRecoveryTracker",
    "SystemHealthRegistry",
    "CANONICAL_SERVICES",
    "IncidentDeduplicationEngine",
    "DeduplicationResult",
    "IncidentCorrelationEngine",
    "CorrelationResult",
    "ConflictType",
    "ConflictEvaluation",
    "ConflictDetector",
    "QueueItem",
    "SchedulingDecision",
    "RemediationScheduler",
    "IncidentRecord",
    "RemediationBudget",
    "IncidentOrchestrationManager",
]

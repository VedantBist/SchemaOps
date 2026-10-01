"""
CausalOps Controlled Remediation Execution & Closed-Loop Self-Healing Module (Phase 5).
"""

from .actions import (
    BaseActionExecutor,
    ActionExecutorRegistry,
    ExecutionContext,
    ExecutionResult,
    RollbackResult,
    PreExecutionSnapshot,
    ExecutionEnvironment,
    ActionExecutionStatus,
)
from .policy import (
    ExecutionPolicyEngine,
    PolicyDecision,
    ApprovalRecord,
    ApprovalStatus,
)
from .audit import (
    ExecutionJournal,
    JournalEntry,
    ExecutionState,
)
from .verification import (
    VerificationEngine,
    VerificationResult,
    capture_pre_execution_snapshot,
)
from .rollback import (
    RollbackEngine,
    RollbackPolicy,
    RollbackDecision,
)
from .executor import (
    ClosedLoopRemediationExecutor,
    ClosedLoopExecutionRecord,
)

__all__ = [
    "BaseActionExecutor",
    "ActionExecutorRegistry",
    "ExecutionContext",
    "ExecutionResult",
    "RollbackResult",
    "PreExecutionSnapshot",
    "ExecutionEnvironment",
    "ActionExecutionStatus",
    "ExecutionPolicyEngine",
    "PolicyDecision",
    "ApprovalRecord",
    "ApprovalStatus",
    "ExecutionJournal",
    "JournalEntry",
    "ExecutionState",
    "VerificationEngine",
    "VerificationResult",
    "capture_pre_execution_snapshot",
    "RollbackEngine",
    "RollbackPolicy",
    "RollbackDecision",
    "ClosedLoopRemediationExecutor",
    "ClosedLoopExecutionRecord",
]

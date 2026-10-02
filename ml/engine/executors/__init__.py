"""Executor plugins: carry out a remediation operation on the monitored environment and undo it.

Every executor works on an explicit target binding (a compose service, a Kubernetes deployment,
a runbook) and refuses anything outside its configured scope. ``execute`` returns the state
needed to roll the change back; ``rollback`` restores it. Operations that change no persistent
state (restarts) return ``rollback_state=None`` and cannot be rolled back.
"""
from .base import ExecutionResult, ExecutorError, OperationNotSupported, TargetNotFound
from .registry import execute, rollback, describe

__all__ = ["ExecutionResult", "ExecutorError", "OperationNotSupported", "TargetNotFound",
           "execute", "rollback", "describe"]

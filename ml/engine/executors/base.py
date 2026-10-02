from __future__ import annotations

from dataclasses import dataclass, field


class ExecutorError(Exception):
    """The executor could not carry out the operation (reported to the caller as HTTP 502)."""


class TargetNotFound(ExecutorError):
    """The binding does not resolve to anything inside the executor's allowed scope (HTTP 404)."""


class OperationNotSupported(ExecutorError):
    """The executor does not implement this operation, or its parameters are invalid (HTTP 422)."""


@dataclass
class ExecutionResult:
    executor: str
    operation: str
    target: str
    detail: str
    rollback_state: dict | None = None     # None: nothing to restore
    observed: dict = field(default_factory=dict)   # what the executor saw before/after (for the audit log)
    dry_run: bool = False

    def to_dict(self) -> dict:
        return {"executor": self.executor, "operation": self.operation, "target": self.target,
                "detail": self.detail, "rollback_state": self.rollback_state, "observed": self.observed,
                "dry_run": self.dry_run, "reversible": self.rollback_state is not None}


def positive_factor(params: dict, key: str, default: float) -> float:
    try:
        f = float(params.get(key, default))
    except (TypeError, ValueError):
        raise OperationNotSupported(f"{key} must be a number")
    if not 1.0 < f <= 4.0:
        raise OperationNotSupported(f"{key} must be in (1, 4]; got {f}")
    return f

"""Executor endpoints. They change the monitored environment, so only the CausalOps API may call
them: every request must carry ``X-Engine-Token`` equal to ``ENGINE_INTERNAL_TOKEN``. Without a
configured token the executors stay disabled."""
from __future__ import annotations

import hmac
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from ml.engine import executors
from ml.engine.executors import ExecutorError, OperationNotSupported, TargetNotFound

log = logging.getLogger("causalops.executors")


def _authorize(token: Optional[str]) -> None:
    expected = os.environ.get("ENGINE_INTERNAL_TOKEN", "")
    if len(expected) < 16:
        raise HTTPException(status_code=503, detail="executors are disabled: ENGINE_INTERNAL_TOKEN is not configured")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="missing or invalid X-Engine-Token")


def require_token(x_engine_token: Optional[str] = Header(None)) -> None:
    _authorize(x_engine_token)


# The token is checked before anything else, including request-body validation.
router = APIRouter(prefix="/executors", dependencies=[Depends(require_token)])


class ExecuteRequest(BaseModel):
    execution_id: str
    executor: str = Field(pattern="^(docker|kubernetes|webhook)$")
    executor_config: dict
    operation: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=200)
    params: dict = {}
    dry_run: bool = False
    context: dict = {}


class RollbackRequest(BaseModel):
    execution_id: str
    executor: str = Field(pattern="^(docker|kubernetes|webhook)$")
    executor_config: dict
    operation: str
    target: str
    rollback_state: Optional[dict] = None
    context: dict = {}


def _run(fn, *args):
    try:
        return fn(*args).to_dict()
    except TargetNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except OperationNotSupported as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ExecutorError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("")
def describe():
    return executors.describe()


@router.post("/execute")
def execute(body: ExecuteRequest):
    log.info("execution %s: %s %s on %s (dry_run=%s)", body.execution_id, body.executor, body.operation, body.target, body.dry_run)
    return _run(executors.execute, body.executor, body.executor_config, body.operation, body.target, body.params,
                body.dry_run, {"execution_id": body.execution_id, **body.context})


@router.post("/rollback")
def rollback(body: RollbackRequest):
    log.info("rollback %s: %s %s on %s", body.execution_id, body.executor, body.operation, body.target)
    return _run(executors.rollback, body.executor, body.executor_config, body.operation, body.target,
                body.rollback_state, {"execution_id": body.execution_id, **body.context})

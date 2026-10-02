"""Signed webhook executor: hands an operation to the team's own runbook automation.

The request body is JSON; ``X-CausalOps-Signature: sha256=<hex>`` is the HMAC-SHA256 of
``<X-CausalOps-Timestamp>.<body>`` with the shared secret, so the receiver can reject forged or
replayed calls. Any 2xx response is success. A JSON response may carry ``rollback_state``; the
operation is then reversible and rollback POSTs the same envelope with ``phase: "rollback"``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

import httpx

from .base import ExecutionResult, ExecutorError, OperationNotSupported


def client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(transport=transport, timeout=30.0)


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


class WebhookExecutor:
    name = "webhook"
    operations = ("runbook",)

    def __init__(self, config: dict, http: httpx.Client):
        self.url = config.get("url")
        if not self.url:
            raise OperationNotSupported("webhook executor needs 'url'")
        secret_env = config.get("secretEnv") or "CAUSALOPS_WEBHOOK_SECRET"
        self.secret = os.environ.get(secret_env, "")
        if len(self.secret) < 16:
            raise OperationNotSupported(f"webhook secret ${secret_env} is not set (or shorter than 16 characters)")
        self.http = http

    def execute(self, operation: str, target: str, params: dict, dry_run: bool, context: dict | None = None) -> ExecutionResult:
        if operation != "runbook":
            raise OperationNotSupported(f"webhook executor does not support '{operation}'")
        envelope = {"phase": "execute", "operation": operation, "target": target, "params": params,
                    "dry_run": dry_run, **(context or {})}
        reply = self._post(envelope)
        state = reply.get("rollback_state") if isinstance(reply, dict) else None
        return ExecutionResult(self.name, operation, target, str(reply.get("detail", "runbook accepted")) if isinstance(reply, dict)
                               else "runbook accepted", state, {"response": reply}, dry_run)

    def rollback(self, operation: str, target: str, state: dict | None, context: dict | None = None) -> ExecutionResult:
        if not state:
            raise OperationNotSupported("the runbook returned no rollback_state; there is nothing to roll back")
        reply = self._post({"phase": "rollback", "operation": operation, "target": target, "rollback_state": state,
                            **(context or {})})
        return ExecutionResult(self.name, "rollback:" + operation, target, "runbook rollback accepted", None, {"response": reply})

    def _post(self, envelope: dict):
        body = json.dumps(envelope, sort_keys=True, default=str).encode()
        ts = str(int(time.time()))
        headers = {"Content-Type": "application/json", "X-CausalOps-Timestamp": ts,
                   "X-CausalOps-Signature": sign(self.secret, ts, body)}
        try:
            r = self.http.post(self.url, content=body, headers=headers)
        except httpx.HTTPError as e:
            raise ExecutorError(f"runbook endpoint unreachable: {e}") from e
        if not 200 <= r.status_code < 300:
            raise ExecutorError(f"runbook endpoint returned {r.status_code}: {r.text.strip()[:300]}")
        try:
            return r.json()
        except ValueError:
            return {"detail": r.text.strip()[:300] or "accepted"}

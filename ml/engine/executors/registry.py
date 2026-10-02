"""Builds the executor named in a request from the environment's executor configuration."""
from __future__ import annotations

import os

import httpx

from . import docker, kubernetes, webhook
from .base import ExecutionResult, OperationNotSupported

# Tests inject fake transports here; production uses the real socket / API server / network.
TRANSPORTS: dict[str, httpx.BaseTransport | None] = {"docker": None, "kubernetes": None, "webhook": None}


def _build(kind: str, config: dict):
    if not config.get("enabled", False):
        raise OperationNotSupported(f"executor '{kind}' is not enabled for this environment")
    if kind == "docker":
        sock = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
        return docker.DockerExecutor(config, docker.client(sock, TRANSPORTS["docker"]))
    if kind == "kubernetes":
        return kubernetes.KubernetesExecutor(config, kubernetes.client(config, TRANSPORTS["kubernetes"]))
    if kind == "webhook":
        return webhook.WebhookExecutor(config, webhook.client(TRANSPORTS["webhook"]))
    raise OperationNotSupported(f"unknown executor '{kind}'")


def execute(kind: str, config: dict, operation: str, target: str, params: dict, dry_run: bool,
            context: dict | None = None) -> ExecutionResult:
    ex = _build(kind, config)
    try:
        if kind == "webhook":
            return ex.execute(operation, target, params, dry_run, context)
        return ex.execute(operation, target, params, dry_run)
    finally:
        ex.http.close()


def rollback(kind: str, config: dict, operation: str, target: str, state: dict | None,
             context: dict | None = None) -> ExecutionResult:
    ex = _build(kind, config)
    try:
        if kind == "webhook":
            return ex.rollback(operation, target, state, context)
        return ex.rollback(operation, target, state)
    finally:
        ex.http.close()


def describe() -> dict:
    return {"docker": list(docker.DockerExecutor.operations),
            "kubernetes": list(kubernetes.KubernetesExecutor.operations),
            "webhook": list(webhook.WebhookExecutor.operations)}

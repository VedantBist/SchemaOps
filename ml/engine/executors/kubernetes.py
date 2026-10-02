"""Kubernetes API executor (in-cluster ServiceAccount, or an explicit API URL and token).

Scope: deployments in the configured namespace only. A target is a deployment name.

Operations
  rollout_restart   same as ``kubectl rollout restart`` (no persistent spec change to undo)
  scale             add ``delta`` replicas, capped at ``maxReplicas``; rollback restores the count
  update_resources  raise every container's CPU / memory limits (and requests) by a factor;
                    rollback restores the previous resources
"""
from __future__ import annotations

import copy
import os
from datetime import datetime, timezone

import httpx

from .base import ExecutionResult, ExecutorError, OperationNotSupported, TargetNotFound, positive_factor

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
MERGE = {"Content-Type": "application/merge-patch+json"}


def client(config: dict, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    url = config.get("apiUrl") or "https://kubernetes.default.svc"
    token = os.environ.get(config.get("tokenEnv") or "", "") or _read(f"{SA_DIR}/token")
    verify: bool | str = config.get("caFile") or (f"{SA_DIR}/ca.crt" if os.path.exists(f"{SA_DIR}/ca.crt") else True)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=url, headers=headers, verify=verify, transport=transport, timeout=30.0)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


class KubernetesExecutor:
    name = "kubernetes"
    operations = ("rollout_restart", "scale", "update_resources")

    def __init__(self, config: dict, http: httpx.Client):
        self.namespace = config.get("namespace")
        if not self.namespace:
            raise OperationNotSupported("kubernetes executor needs 'namespace'")
        self.max_replicas = int(config.get("maxReplicas", 10))
        self.http = http

    def _path(self, deployment: str, sub: str = "") -> str:
        return f"/apis/apps/v1/namespaces/{self.namespace}/deployments/{deployment}{sub}"

    def execute(self, operation: str, target: str, params: dict, dry_run: bool) -> ExecutionResult:
        dep = self._call("GET", self._path(target)).json()
        if operation == "rollout_restart":
            stamp = datetime.now(timezone.utc).isoformat()
            if not dry_run:
                self._call("PATCH", self._path(target), headers=MERGE, json={"spec": {"template": {"metadata": {
                    "annotations": {"kubectl.kubernetes.io/restartedAt": stamp}}}}})
            return ExecutionResult(self.name, operation, target, f"rollout restart of {self.namespace}/{target}",
                                   None, {"restartedAt": stamp, "generation": dep["metadata"].get("generation")}, dry_run)
        if operation == "scale":
            delta = int(params.get("delta", 1))
            if delta < 1:
                raise OperationNotSupported("scale delta must be >= 1")
            current = int(self._call("GET", self._path(target, "/scale")).json()["spec"].get("replicas", 0))
            wanted = min(current + delta, self.max_replicas)
            if wanted <= current:
                raise OperationNotSupported(f"{target} already runs {current} replicas (maxReplicas {self.max_replicas})")
            if not dry_run:
                self._set_replicas(target, wanted)
            return ExecutionResult(self.name, operation, target, f"scaled {self.namespace}/{target} {current} -> {wanted}",
                                   {"replicas": current}, {"before": current, "after": wanted}, dry_run)
        if operation == "update_resources":
            cpu_f, mem_f = positive_factor(params, "cpuFactor", 1.5), positive_factor(params, "memoryFactor", 1.5)
            containers = dep["spec"]["template"]["spec"]["containers"]
            before = {c["name"]: copy.deepcopy(c.get("resources") or {}) for c in containers}
            after = {}
            for c in containers:
                res = copy.deepcopy(c.get("resources") or {})
                if not res.get("limits"):
                    raise OperationNotSupported(f"container {c['name']} of {target} has no limits to raise")
                for section in ("limits", "requests"):
                    for key, factor in (("cpu", cpu_f), ("memory", mem_f)):
                        if key in res.get(section, {}):
                            res[section][key] = scale_quantity(res[section][key], factor)
                after[c["name"]] = res
            if not dry_run:
                self._patch_resources(target, after)
            return ExecutionResult(self.name, operation, target, f"raised resources of {self.namespace}/{target}",
                                   {"resources": before}, {"before": before, "after": after}, dry_run)
        raise OperationNotSupported(f"kubernetes executor does not support '{operation}'")

    def rollback(self, operation: str, target: str, state: dict | None) -> ExecutionResult:
        if operation == "scale" and state:
            self._set_replicas(target, int(state["replicas"]))
            return ExecutionResult(self.name, "rollback:scale", target, f"restored {state['replicas']} replicas", None, state)
        if operation == "update_resources" and state:
            self._patch_resources(target, state["resources"])
            return ExecutionResult(self.name, "rollback:update_resources", target, "restored container resources", None, state)
        raise OperationNotSupported(f"'{operation}' changes no persistent state; there is nothing to roll back")

    def _set_replicas(self, target: str, replicas: int) -> None:
        self._call("PATCH", self._path(target, "/scale"), headers=MERGE, json={"spec": {"replicas": replicas}})

    def _patch_resources(self, target: str, resources: dict[str, dict]) -> None:
        # Strategic merge patch merges containers by name.
        patch = {"spec": {"template": {"spec": {"containers": [{"name": n, "resources": r} for n, r in resources.items()]}}}}
        self._call("PATCH", self._path(target), headers={"Content-Type": "application/strategic-merge-patch+json"}, json=patch)

    def _call(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = self.http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise ExecutorError(f"Kubernetes API unreachable: {e}") from e
        if r.status_code == 404:
            raise TargetNotFound(f"deployment not found: {path}")
        if r.status_code >= 400:
            raise ExecutorError(f"Kubernetes API {method} {path} returned {r.status_code}: {r.text.strip()[:300]}")
        return r


_FINER = {"Ti": ("Gi", 1024), "Gi": ("Mi", 1024), "Mi": ("Ki", 1024), "Ki": ("", 1024),
          "G": ("M", 1000), "M": ("k", 1000), "k": ("", 1000), "": ("m", 1000)}


def scale_quantity(q: str, factor: float) -> str:
    """Multiplies a Kubernetes quantity ("500m", "256Mi", "1") exactly, moving to a finer unit
    when the result is fractional (1Gi x 1.5 = 1536Mi, 1 x 1.5 = 1500m)."""
    q = str(q)
    unit = next((u for u in ("Ti", "Gi", "Mi", "Ki", "m", "k", "M", "G") if q.endswith(u)), "")
    value = float(q[: len(q) - len(unit)]) * factor
    while abs(value - round(value)) > 1e-9 and unit in _FINER:
        unit, mult = _FINER[unit]
        value *= mult
    return f"{int(round(value))}{unit}"

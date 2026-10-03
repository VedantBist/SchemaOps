"""Docker Engine API executor (over the mounted Docker socket).

Scope: only containers labelled with the configured compose project. A target is a compose
service name; every container (replica) of that service is acted on.

Operations
  restart           restart the service's containers (no persistent change; nothing to roll back)
  update_resources  raise CPU / memory limits by a factor; rollback restores the previous limits
  start_stopped     start the service's replicas that exited or crashed (running ones are untouched)
"""
from __future__ import annotations

import json

import httpx

from .base import ExecutionResult, ExecutorError, OperationNotSupported, TargetNotFound, positive_factor

# Unversioned paths: the daemon answers with its own API version, so a daemon that has dropped an
# older pinned version still works. The fields used here are stable across versions.
API = "http://docker"
STOP_TIMEOUT_S = 10


def client(socket_path: str = "/var/run/docker.sock", transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(transport=transport or httpx.HTTPTransport(uds=socket_path), timeout=60.0)


class DockerExecutor:
    name = "docker"
    operations = ("restart", "update_resources", "start_stopped")

    def __init__(self, config: dict, http: httpx.Client):
        self.project = config.get("project")
        if not self.project:
            raise OperationNotSupported("docker executor needs 'project' (the compose project whose containers it may manage)")
        self.http = http

    def containers(self, service: str, include_stopped: bool = False) -> list[dict]:
        filters = {"label": [f"com.docker.compose.project={self.project}", f"com.docker.compose.service={service}"]}
        r = self._call("GET", "/containers/json", params={"all": str(include_stopped).lower(), "filters": json.dumps(filters)})
        found = r.json()
        if not found:
            raise TargetNotFound(f"no {'' if include_stopped else 'running '}container of compose service '{service}' "
                                 f"in project '{self.project}'")
        return found

    def execute(self, operation: str, target: str, params: dict, dry_run: bool) -> ExecutionResult:
        if operation == "start_stopped":
            stopped = [c for c in self.containers(target, include_stopped=True) if c.get("State") != "running"]
            ids = [c["Id"][:12] for c in stopped]
            if not dry_run:
                for c in stopped:
                    self._call("POST", f"/containers/{c['Id']}/start")
            return ExecutionResult(self.name, operation, target,
                                   f"started {len(ids)} stopped container(s) {ids}" if ids else "no stopped container",
                                   None, {"containers": ids, "states": {c["Id"][:12]: c.get("State") for c in stopped}}, dry_run)
        found = self.containers(target)
        ids = [c["Id"][:12] for c in found]
        if operation == "restart":
            if not dry_run:
                for c in found:
                    self._call("POST", f"/containers/{c['Id']}/restart", params={"t": STOP_TIMEOUT_S})
            return ExecutionResult(self.name, operation, target, f"restarted {len(ids)} container(s) {ids}",
                                   None, {"containers": ids}, dry_run)
        if operation == "update_resources":
            cpu_f, mem_f = positive_factor(params, "cpuFactor", 1.5), positive_factor(params, "memoryFactor", 1.5)
            before, after = {}, {}
            for c in found:
                host = self._call("GET", f"/containers/{c['Id']}/json").json()["HostConfig"]
                cpus, mem = int(host.get("NanoCpus") or 0), int(host.get("Memory") or 0)
                if cpus == 0 and mem == 0:
                    raise OperationNotSupported(f"container {c['Id'][:12]} has no CPU or memory limit to raise; "
                                                "set limits on the service first")
                # Only limits that are set are raised (0 means unlimited and stays unlimited).
                before[c["Id"]] = {"NanoCpus": cpus, "Memory": mem, "MemorySwap": int(host.get("MemorySwap") or 0)}
                after[c["Id"]] = {"NanoCpus": int(cpus * cpu_f), "Memory": int(mem * mem_f), "MemorySwap": -1 if mem else 0}
            if not dry_run:
                for cid, limits in after.items():
                    self._call("POST", f"/containers/{cid}/update", json=limits)
            return ExecutionResult(self.name, operation, target,
                                   f"raised limits of {len(ids)} container(s) by cpu x{cpu_f}, memory x{mem_f}",
                                   {"limits": before}, {"before": before, "after": after}, dry_run)
        raise OperationNotSupported(f"docker executor does not support '{operation}'")

    def rollback(self, operation: str, target: str, state: dict | None) -> ExecutionResult:
        if operation != "update_resources" or not state:
            raise OperationNotSupported(f"'{operation}' changes no persistent state; there is nothing to roll back")
        allowed = {c["Id"] for c in self.containers(target)}
        restored = []
        for cid, limits in state["limits"].items():
            if cid not in allowed:
                raise TargetNotFound(f"container {cid[:12]} is no longer part of service '{target}'")
            body = dict(limits)
            if body.get("MemorySwap", 0) == 0:
                body["MemorySwap"] = -1
            self._call("POST", f"/containers/{cid}/update", json=body)
            restored.append(cid[:12])
        return ExecutionResult(self.name, "rollback:" + operation, target, f"restored limits of {restored}",
                               None, {"restored": state["limits"]})

    def _call(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = self.http.request(method, API + path, **kw)
        except httpx.HTTPError as e:
            raise ExecutorError(f"Docker Engine API unreachable: {e}") from e
        if r.status_code == 404:
            raise TargetNotFound(f"Docker: {r.text.strip()}")
        if r.status_code >= 400:
            raise ExecutorError(f"Docker Engine API {method} {path} returned {r.status_code}: {r.text.strip()}")
        return r

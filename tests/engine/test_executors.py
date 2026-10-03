"""Executor plugins against in-process fakes of the Docker Engine API, the Kubernetes API and a
runbook receiver (httpx.MockTransport): requests, scope checks, rollback and error mapping."""
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ai-engine"))

from ml.engine.executors import registry, webhook  # noqa: E402
from ml.engine.executors.base import OperationNotSupported, TargetNotFound  # noqa: E402
from ml.engine.executors.kubernetes import scale_quantity  # noqa: E402


class FakeDocker:
    def __init__(self):
        self.containers = {
            "aaa111": {"project": "shop", "service": "payments", "NanoCpus": 1_000_000_000, "Memory": 512 * 2**20, "MemorySwap": 0},
            "bbb222": {"project": "other", "service": "payments", "NanoCpus": 1_000_000_000, "Memory": 512 * 2**20, "MemorySwap": 0},
            "ccc333": {"project": "shop", "service": "nolimits", "NanoCpus": 0, "Memory": 0, "MemorySwap": 0},
            "ddd444": {"project": "shop", "service": "memonly", "NanoCpus": 0, "Memory": 512 * 2**20, "MemorySwap": 0},
        }
        self.restarts = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if req.method == "GET" and path == "/containers/json":
            labels = json.loads(parse_qs(urlparse(str(req.url)).query)["filters"][0])["label"]
            want = dict(lbl.split("=", 1) for lbl in labels)
            hits = [{"Id": cid} for cid, c in self.containers.items()
                    if c["project"] == want["com.docker.compose.project"] and c["service"] == want["com.docker.compose.service"]]
            return httpx.Response(200, json=hits)
        cid = path.split("/")[2]
        if cid not in self.containers:
            return httpx.Response(404, text="No such container")
        if path.endswith("/restart"):
            self.restarts.append(cid)
            return httpx.Response(204)
        if path.endswith("/json"):
            c = self.containers[cid]
            return httpx.Response(200, json={"HostConfig": {k: c[k] for k in ("NanoCpus", "Memory", "MemorySwap")}})
        if path.endswith("/update"):
            self.containers[cid].update({k: v for k, v in json.loads(req.content).items()})
            return httpx.Response(200, json={"Warnings": []})
        return httpx.Response(400, text="unexpected")


class FakeKube:
    def __init__(self):
        self.replicas = 2
        self.resources = {"limits": {"cpu": "500m", "memory": "256Mi"}, "requests": {"cpu": "250m", "memory": "128Mi"}}
        self.annotations = {}
        self.auth = None

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.auth = req.headers.get("Authorization")
        p = req.url.path
        if not p.startswith("/apis/apps/v1/namespaces/prod/deployments/payments"):
            return httpx.Response(404, json={"message": "not found"})
        body = json.loads(req.content) if req.content else None
        if p.endswith("/scale"):
            if req.method == "PATCH":
                self.replicas = body["spec"]["replicas"]
            return httpx.Response(200, json={"spec": {"replicas": self.replicas}})
        if req.method == "PATCH":
            tmpl = body["spec"]["template"]
            self.annotations.update(tmpl.get("metadata", {}).get("annotations", {}))
            for c in tmpl.get("spec", {}).get("containers", []):
                self.resources = c["resources"]
        return httpx.Response(200, json={"metadata": {"generation": 3}, "spec": {"template": {"spec": {
            "containers": [{"name": "app", "resources": self.resources}]}}}})


@pytest.fixture
def docker_fake(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setitem(registry.TRANSPORTS, "docker", httpx.MockTransport(fake))
    return fake


@pytest.fixture
def kube_fake(monkeypatch):
    fake = FakeKube()
    monkeypatch.setitem(registry.TRANSPORTS, "kubernetes", httpx.MockTransport(fake))
    monkeypatch.setenv("KUBE_TOKEN", "t0k3n")
    return fake


DOCKER = {"enabled": True, "project": "shop"}
KUBE = {"enabled": True, "namespace": "prod", "apiUrl": "https://k8s.example", "tokenEnv": "KUBE_TOKEN", "maxReplicas": 3}


def test_docker_restart_only_touches_the_configured_project(docker_fake):
    r = registry.execute("docker", DOCKER, "restart", "payments", {}, False)
    assert docker_fake.restarts == ["aaa111"]          # bbb222 has the same service name in another project
    assert r.rollback_state is None and not r.to_dict()["reversible"]
    with pytest.raises(OperationNotSupported):
        registry.rollback("docker", DOCKER, "restart", "payments", None)


def test_docker_dry_run_changes_nothing(docker_fake):
    r = registry.execute("docker", DOCKER, "restart", "payments", {}, True)
    assert docker_fake.restarts == [] and r.dry_run


def test_docker_resources_raised_then_restored(docker_fake):
    r = registry.execute("docker", DOCKER, "update_resources", "payments", {"cpuFactor": 2, "memoryFactor": 1.5}, False)
    c = docker_fake.containers["aaa111"]
    assert c["NanoCpus"] == 2_000_000_000 and c["Memory"] == 768 * 2**20
    registry.rollback("docker", DOCKER, "update_resources", "payments", r.rollback_state)
    assert c["NanoCpus"] == 1_000_000_000 and c["Memory"] == 512 * 2**20
    assert docker_fake.containers["bbb222"]["NanoCpus"] == 1_000_000_000      # other project untouched


def test_docker_raises_only_the_limits_that_are_set(docker_fake):
    r = registry.execute("docker", DOCKER, "update_resources", "memonly", {"cpuFactor": 2, "memoryFactor": 1.5}, False)
    c = docker_fake.containers["ddd444"]
    assert c["NanoCpus"] == 0 and c["Memory"] == 768 * 2**20       # CPU stays unlimited
    registry.rollback("docker", DOCKER, "update_resources", "memonly", r.rollback_state)
    assert c["Memory"] == 512 * 2**20


def test_docker_errors(docker_fake):
    with pytest.raises(TargetNotFound):
        registry.execute("docker", DOCKER, "restart", "unknown", {}, False)
    with pytest.raises(OperationNotSupported):
        registry.execute("docker", DOCKER, "update_resources", "nolimits", {}, False)
    with pytest.raises(OperationNotSupported):
        registry.execute("docker", DOCKER, "update_resources", "payments", {"cpuFactor": 9}, False)
    with pytest.raises(OperationNotSupported):
        registry.execute("docker", {"enabled": False, "project": "shop"}, "restart", "payments", {}, False)


def test_kubernetes_scale_and_rollback(kube_fake):
    r = registry.execute("kubernetes", KUBE, "scale", "payments", {"delta": 5}, False)
    assert kube_fake.replicas == 3 and r.rollback_state == {"replicas": 2}      # capped at maxReplicas
    assert kube_fake.auth == "Bearer t0k3n"
    registry.rollback("kubernetes", KUBE, "scale", "payments", r.rollback_state)
    assert kube_fake.replicas == 2


def test_kubernetes_rollout_restart_and_resources(kube_fake):
    registry.execute("kubernetes", KUBE, "rollout_restart", "payments", {}, False)
    assert "kubectl.kubernetes.io/restartedAt" in kube_fake.annotations
    r = registry.execute("kubernetes", KUBE, "update_resources", "payments", {"cpuFactor": 2, "memoryFactor": 2}, False)
    assert kube_fake.resources["limits"] == {"cpu": "1000m", "memory": "512Mi"}
    registry.rollback("kubernetes", KUBE, "update_resources", "payments", r.rollback_state)
    assert kube_fake.resources["limits"] == {"cpu": "500m", "memory": "256Mi"}
    with pytest.raises(TargetNotFound):
        registry.execute("kubernetes", KUBE, "scale", "missing", {}, False)


def test_quantity_scaling():
    assert scale_quantity("500m", 1.5) == "750m"
    assert scale_quantity("1", 2) == "2"
    assert scale_quantity("1Gi", 1.5) == "1536Mi"
    assert scale_quantity("1", 1.5) == "1500m"
    assert scale_quantity("0.5", 2) == "1"


def test_webhook_is_signed_and_reversible(monkeypatch):
    secret = "s" * 24
    monkeypatch.setenv("RUNBOOK_SECRET", secret)
    seen = []

    def receiver(req: httpx.Request) -> httpx.Response:
        ts, sig = req.headers["X-CausalOps-Timestamp"], req.headers["X-CausalOps-Signature"]
        if sig != webhook.sign(secret, ts, req.content):
            return httpx.Response(401, text="bad signature")
        body = json.loads(req.content)
        seen.append(body)
        return httpx.Response(200, json={"detail": "failover started", "rollback_state": {"primary": "db-a"}}
                              if body["phase"] == "execute" else {"detail": "failed back"})

    monkeypatch.setitem(registry.TRANSPORTS, "webhook", httpx.MockTransport(receiver))
    cfg = {"enabled": True, "url": "https://runbooks.example/hook", "secretEnv": "RUNBOOK_SECRET"}
    r = registry.execute("webhook", cfg, "runbook", "orders-db", {"runbook": "db-failover"}, False, {"execution_id": "e1"})
    assert r.detail == "failover started" and r.rollback_state == {"primary": "db-a"}
    registry.rollback("webhook", cfg, "runbook", "orders-db", r.rollback_state, {"execution_id": "e1"})
    assert [b["phase"] for b in seen] == ["execute", "rollback"] and seen[0]["execution_id"] == "e1"

    monkeypatch.setenv("RUNBOOK_SECRET", "short")
    with pytest.raises(OperationNotSupported):
        registry.execute("webhook", cfg, "runbook", "orders-db", {}, False)

"""The engine serves only environment-aware endpoints; executor endpoints need the internal token."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.parametrize("method,path", [
    ("get", "/incidents"), ("get", "/system/health"), ("get", "/observability/metrics"),
    ("post", "/remediation/approve"), ("post", "/remediation/execute"), ("get", "/remediation/executions"),
])
def test_legacy_journal_endpoints_are_gone(client, method, path):
    assert getattr(client, method)(path).status_code in (404, 405)


def test_executors_disabled_without_configured_token(client, monkeypatch):
    monkeypatch.delenv("ENGINE_INTERNAL_TOKEN", raising=False)
    assert client.get("/executors").status_code == 503


def test_executors_reject_wrong_token(client, monkeypatch):
    monkeypatch.setenv("ENGINE_INTERNAL_TOKEN", "x" * 32)
    assert client.get("/executors").status_code == 401
    assert client.get("/executors", headers={"X-Engine-Token": "y" * 32}).status_code == 401
    ok = client.get("/executors", headers={"X-Engine-Token": "x" * 32})
    assert ok.status_code == 200 and "restart" in ok.json()["docker"]


def test_token_is_checked_before_the_request_body(client, monkeypatch):
    monkeypatch.setenv("ENGINE_INTERNAL_TOKEN", "x" * 32)
    assert client.post("/executors/execute", json={}).status_code == 401

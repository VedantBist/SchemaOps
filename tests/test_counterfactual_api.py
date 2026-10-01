"""
Phase 3D / Section 10: Counterfactual Simulation API Integration Tests
Tests:
1. Counterfactual API route exists on FastAPI application.
2. Valid request returns HTTP 200 OK.
3. Invalid request returns structured 4xx.
4. Returned trajectory is non-empty with matching dimensions.
5. AI-engine health endpoint returns UP and counterfactual_engine HEALTHY.
6. Backend handles bad experiment gracefully with structured error.
7. Configurable intervention magnitude produces scaled counterfactual trajectory.
"""

import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
if str(repo_root / "ai-engine") not in sys.path:
    sys.path.insert(0, str(repo_root / "ai-engine"))

from app.main import app

client = TestClient(app)


def test_01_counterfactual_route_exists():
    """Verify POST /causal/counterfactual route is registered on FastAPI app."""
    routes = [r.path for r in app.routes if hasattr(r, "path")]
    assert "/causal/counterfactual" in routes


def test_02_valid_request_returns_200():
    """Verify a valid counterfactual simulation request returns HTTP 200."""
    payload = {
        "experiment_id": "EXP-015",
        "root_cause": "inventory-db",
        "intervention": "reduce-latency-70",
        "intervention_magnitude": 0.7,
        "simulation_resolution": 1.0,
    }
    response = client.post("/causal/counterfactual", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["experiment_id"] == "EXP-015"
    assert data["root_cause"]["node"] == "inventory-db"


def test_03_invalid_request_returns_structured_4xx():
    """Verify invalid request body returns structured 4xx (422 Unprocessable Entity)."""
    # Send invalid type for payload
    response = client.post("/causal/counterfactual", content="invalid json", headers={"Content-Type": "application/json"})
    assert response.status_code in [400, 422]


def test_04_returned_trajectory_is_non_empty():
    """Verify returned counterfactual trajectory and timeline frames are non-empty."""
    payload = {
        "experiment_id": "EXP-015",
        "root_cause": "inventory-db",
        "intervention_magnitude": 0.7,
    }
    response = client.post("/causal/counterfactual", json=payload)
    assert response.status_code == 200
    data = response.json()

    # Trajectory arrays must be populated
    assert len(data["counterfactual_trajectory"]) > 0
    assert len(data["observed_trajectory"]) > 0
    assert len(data["timeline"]) == data["total_horizon_seconds"]
    assert len(data["visualization_data"]["timesteps"]) == len(data["timeline"])

    # Ensure frames contain services
    first_frame = data["timeline"][0]
    assert "services" in first_frame
    assert "inventory-db" in first_frame["services"]
    assert "api-gateway" in first_frame["services"]


def test_05_health_check_reports_counterfactual_engine():
    """Verify /health diagnostic endpoint returns status UP and counterfactual_engine HEALTHY."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "UP"
    assert data["counterfactual_engine"] == "HEALTHY"


def test_06_cors_headers_present():
    """Verify CORS preflight and headers are returned for frontend origin."""
    response = client.options(
        "/causal/counterfactual",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers


def test_07_intervention_magnitude_scaling():
    """Verify intervention magnitude scales the counterfactual effect delta."""
    # Run with 50% magnitude
    resp_50 = client.post("/causal/counterfactual", json={
        "experiment_id": "EXP-015",
        "root_cause": "inventory-db",
        "intervention_magnitude": 0.5,
    })
    # Run with 90% magnitude
    resp_90 = client.post("/causal/counterfactual", json={
        "experiment_id": "EXP-015",
        "root_cause": "inventory-db",
        "intervention_magnitude": 0.9,
    })
    assert resp_50.status_code == 200
    lat_50 = resp_50.json()["avoided_impact"]["gateway_latency"]["peak_avoided_latency_ms"]
    lat_90 = resp_90.json()["avoided_impact"]["gateway_latency"]["peak_avoided_latency_ms"]
    assert lat_90 > lat_50

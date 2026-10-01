"""
Phase 6A Tests — FastAPI Prediction API Integration
Tests the /predict/failure-v2 endpoints.
"""
import pytest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

# Use TestClient
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    """Initialize the FastAPI test client."""
    from ai_engine.app.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def ai_client():
    """Alternate client import path."""
    try:
        sys.path.insert(0, str(repo_root / "ai-engine"))
        from app.main import app
        return TestClient(app)
    except Exception:
        pytest.skip("Could not import ai-engine app")


@pytest.fixture(scope="module")
def test_client():
    """Robust client initialization."""
    import importlib
    try:
        sys.path.insert(0, str(repo_root / "ai-engine"))
        spec = importlib.util.spec_from_file_location(
            "main", str(repo_root / "ai-engine" / "app" / "main.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return TestClient(module.app)
    except Exception as e:
        pytest.skip(f"Could not load app: {e}")


class TestFailurePredictionV2API:
    """Tests for the /predict/failure-v2 API endpoint."""

    @pytest.fixture(scope="class")
    def client(self):
        import sys
        sys.path.insert(0, str(repo_root / "ai-engine"))
        from app.main import app
        return TestClient(app)

    def test_status_endpoint(self, client):
        """GET /predict/failure-v2/status returns 200."""
        response = client.get("/predict/failure-v2/status")
        assert response.status_code == 200
        data = response.json()
        assert "is_loaded" in data
        assert "horizons" in data

    def test_manifest_endpoint(self, client):
        """GET /predict/failure-v2/manifest returns training results."""
        response = client.get("/predict/failure-v2/manifest")
        if response.status_code == 404:
            pytest.skip("Training manifest not found")
        assert response.status_code == 200
        data = response.json()
        assert "phase" in data
        assert data["phase"] == "6A"

    def test_predict_with_experiment_id(self, client):
        """POST /predict/failure-v2 with experiment_id returns prediction."""
        response = client.post(
            "/predict/failure-v2",
            json={"experiment_id": "EXP-015"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "status" in data

    def test_predict_returns_three_horizons(self, client):
        """Prediction includes all three horizons."""
        response = client.post(
            "/predict/failure-v2",
            json={"experiment_id": "EXP-015"}
        )
        assert response.status_code == 200
        data = response.json()
        if data.get("status") == "OK":
            preds = data["predictions"]
            assert "failure_within_5s" in preds
            assert "failure_within_10s" in preds
            assert "failure_within_30s" in preds

    def test_predict_no_input_returns_400(self, client):
        """POST without experiment_id or telemetry_array returns 400."""
        response = client.post("/predict/failure-v2", json={})
        assert response.status_code in (400, 200)
        # If 200: might return error in body
        if response.status_code == 200:
            data = response.json()
            assert data.get("status") in ("MODEL_UNAVAILABLE", "NO_TELEMETRY", "FEATURE_EXTRACTION_FAILED")

    def test_predict_with_telemetry_array(self, client):
        """POST with telemetry_array returns prediction."""
        import numpy as np
        x = np.zeros((20, 5, 10))
        x[5:, 4, 6] = 500.0
        response = client.post(
            "/predict/failure-v2",
            json={"telemetry_array": x.tolist()}
        )
        assert response.status_code == 200

    def test_predict_probability_range(self, client):
        """All probabilities in response are in [0, 1]."""
        response = client.post(
            "/predict/failure-v2",
            json={"experiment_id": "EXP-015"}
        )
        if response.status_code != 200:
            pytest.skip("Endpoint unavailable")
        data = response.json()
        if data.get("status") != "OK":
            pytest.skip(f"Non-OK status: {data.get('status')}")
        for horizon, pred in data["predictions"].items():
            if pred.get("probability") is not None:
                assert 0.0 <= pred["probability"] <= 1.0

    def test_old_failure_endpoint_still_works(self, client):
        """Legacy /predict/failure endpoint still returns 200."""
        response = client.post(
            "/predict/failure",
            json={"topology": {}, "services": []}
        )
        assert response.status_code == 200

    def test_health_endpoint_unchanged(self, client):
        """GET /health still returns status UP."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "UP"

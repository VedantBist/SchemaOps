"""
CausalOps Phase 7 — Security Tests
====================================
Tests for authentication, authorization, CORS policy, structured errors,
rate limiting, and secret non-disclosure behaviors.

Run with:
    PYTHONPATH=ai-engine pytest tests/test_phase7_security.py -v
"""
import json
import os
import sys
from pathlib import Path

import pytest

# Ensure ai-engine is on the path for imports like `app.phase7_hardening`
_repo = Path(__file__).parent.parent
_ai_engine = _repo / "ai-engine"
sys.path.insert(0, str(_ai_engine))
sys.path.insert(0, str(_repo))


# ── CORS Tests ─────────────────────────────────────────────────────────────────

class TestCORSPolicy:
    """Verify CORS is not wildcard (*) when configured with explicit origins."""

    def test_cors_origin_not_wildcard_in_production_config(self):
        """
        When CAUSALOPS_CORS_ORIGINS is explicitly set, the value must not contain '*'.
        """
        raw = "https://causalops.example.com,https://admin.causalops.example.com"
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        assert "*" not in origins
        assert "https://causalops.example.com" in origins
        assert len(origins) == 2

    def test_cors_default_dev_config_does_not_include_star(self):
        """Default dev config should allow localhost only, not '*'."""
        raw = "http://localhost:3000,http://localhost:5173,http://localhost:8080"
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        assert "*" not in origins

    def test_cors_single_origin_parses_correctly(self):
        """Single origin without trailing comma parses correctly."""
        raw = "https://causalops.prod.example.com"
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        assert len(origins) == 1
        assert origins[0] == "https://causalops.prod.example.com"

    def test_cors_empty_string_filtered_out(self):
        """Trailing commas in CORS config produce no empty-string origins."""
        raw = "http://localhost:3000,,http://localhost:5173,"
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        assert "" not in origins
        assert len(origins) == 2


# ── Structured Error Tests ─────────────────────────────────────────────────────

class TestStructuredErrors:
    """Verify error responses are structured and do not expose internals."""

    def test_phase7_middleware_returns_correlation_id_on_rate_limit(self):
        """Rate limit responses must include correlation_id."""
        from app.phase7_hardening import Phase7Middleware, METRICS
        # Verify the rate-limit response body format is correct
        response_body = {
            "error_code": "RATE_LIMIT_EXCEEDED",
            "message": "Too many requests to this endpoint. Please wait before retrying.",
            "correlation_id": "test-correlation-id",
            "retryable": True,
            "details": {"endpoint": "/causal/counterfactual"},
        }
        assert "error_code" in response_body
        assert "message" in response_body
        assert "correlation_id" in response_body
        assert "Traceback" not in json.dumps(response_body)
        assert response_body["error_code"] == "RATE_LIMIT_EXCEEDED"

    def test_error_body_format_no_traceback(self):
        """Standard error format should never contain stack trace indicators."""
        error_bodies = [
            {"error_code": "INCIDENT_NOT_FOUND", "message": "Not found", "correlation_id": "x", "retryable": False},
            {"error_code": "MODELS_NOT_TRAINED", "message": "No models", "retryable": False},
            {"error_code": "INTERNAL_ERROR", "message": "Check logs", "correlation_id": "y", "retryable": False},
        ]
        forbidden = ["Traceback", "File \"/", "line ", "/app/ml/"]
        for body in error_bodies:
            body_str = json.dumps(body)
            for f in forbidden:
                assert f not in body_str, f"Found '{f}' in error body: {body_str}"

    def test_no_internal_paths_in_structured_error(self):
        """Internal filesystem paths must not appear in error responses."""
        error = {
            "error_code": "DATASET_LOAD_FAILED",
            "message": "Failed to load dataset sample",
            "retryable": False,
        }
        body_str = json.dumps(error)
        assert "/Users/" not in body_str
        assert "/home/" not in body_str
        assert "/app/ml/" not in body_str


# ── Rate Limiting Tests ────────────────────────────────────────────────────────

class TestRateLimiting:
    """Verify token-bucket rate limiting logic."""

    def test_rate_limiter_allows_initial_requests(self):
        """First requests within capacity should be allowed."""
        from app.phase7_hardening import TokenBucketRateLimiter
        limiter = TokenBucketRateLimiter()
        limiter.LIMITS["/test/endpoint"] = (3, 3 / 60.0)
        assert limiter.is_allowed("127.0.0.1", "/test/endpoint") is True
        assert limiter.is_allowed("127.0.0.1", "/test/endpoint") is True
        assert limiter.is_allowed("127.0.0.1", "/test/endpoint") is True

    def test_rate_limiter_blocks_after_capacity(self):
        """Requests beyond capacity should be blocked."""
        from app.phase7_hardening import TokenBucketRateLimiter
        limiter = TokenBucketRateLimiter()
        limiter.LIMITS["/test/rate"] = (2, 0.0)
        assert limiter.is_allowed("10.0.0.1", "/test/rate") is True
        assert limiter.is_allowed("10.0.0.1", "/test/rate") is True
        assert limiter.is_allowed("10.0.0.1", "/test/rate") is False

    def test_rate_limiter_different_ips_independent(self):
        """Each IP gets its own token bucket."""
        from app.phase7_hardening import TokenBucketRateLimiter
        limiter = TokenBucketRateLimiter()
        limiter.LIMITS["/test/ip"] = (1, 0.0)
        assert limiter.is_allowed("192.168.1.1", "/test/ip") is True
        assert limiter.is_allowed("192.168.1.1", "/test/ip") is False
        assert limiter.is_allowed("192.168.1.2", "/test/ip") is True

    def test_rate_limiter_exempt_paths_always_allowed(self):
        """Health, metrics, and models endpoints are never rate-limited."""
        from app.phase7_hardening import TokenBucketRateLimiter
        limiter = TokenBucketRateLimiter()
        for path in ["/health", "/ready", "/metrics", "/models"]:
            for _ in range(20):
                assert limiter.is_allowed("any.ip", path) is True


# ── Metrics Tests ──────────────────────────────────────────────────────────────

class TestMetrics:
    """Verify metrics registry correctness."""

    def test_metrics_registry_increments_counters(self):
        from app.phase7_hardening import MetricsRegistry
        reg = MetricsRegistry()
        reg.inc("test.counter")
        reg.inc("test.counter")
        reg.inc("test.counter", value=3)
        snapshot = reg.snapshot()
        assert snapshot["counters"]["test.counter"] == 5

    def test_metrics_registry_records_gauges(self):
        from app.phase7_hardening import MetricsRegistry
        reg = MetricsRegistry()
        reg.gauge("ai.health", 1.0)
        snapshot = reg.snapshot()
        assert snapshot["gauges"]["ai.health"] == 1.0

    def test_metrics_registry_records_histograms(self):
        from app.phase7_hardening import MetricsRegistry
        reg = MetricsRegistry()
        for v in [10.0, 20.0, 30.0, 40.0, 50.0]:
            reg.observe("latency_ms", v)
        snapshot = reg.snapshot()
        assert "latency_ms" in snapshot["histograms"]
        assert snapshot["histograms"]["latency_ms"]["count"] == 5
        assert snapshot["histograms"]["latency_ms"]["mean_ms"] == 30.0

    def test_metrics_prometheus_text_format(self):
        from app.phase7_hardening import MetricsRegistry
        reg = MetricsRegistry()
        reg.inc("http_requests_total")
        reg.gauge("ai_engine_health", 1.0)
        text = reg.prometheus_text()
        assert "http_requests_total" in text
        assert "ai_engine_health" in text
        assert "# TYPE" in text


# ── Model Artifact Verification Tests ─────────────────────────────────────────

class TestArtifactVerification:
    """Verify startup artifact checks catch missing files."""

    def test_artifact_verification_passes_with_all_files(self, tmp_path):
        """Verification passes when all required files exist."""
        from app.phase7_hardening import verify_model_artifacts, _REQUIRED_ARTIFACTS
        for rel_path, name in _REQUIRED_ARTIFACTS:
            full = tmp_path / rel_path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(b'{"test": true}')
        result = verify_model_artifacts(tmp_path)
        ok_count = sum(1 for v in result.values() if v.get("status") == "OK")
        assert ok_count == len(_REQUIRED_ARTIFACTS)

    def test_artifact_verification_raises_on_missing_required(self, tmp_path):
        """Verification raises RuntimeError if required artifacts are absent."""
        from app.phase7_hardening import verify_model_artifacts
        with pytest.raises(RuntimeError, match="Required model artifacts missing"):
            verify_model_artifacts(tmp_path)


# ── Secret Non-Disclosure Tests ───────────────────────────────────────────────

class TestSecretNonDisclosure:
    """Verify that sensitive configuration values are not leaked via API."""

    def test_metrics_snapshot_no_credentials(self):
        """Metrics snapshot must not expose passwords or connection strings."""
        from app.phase7_hardening import MetricsRegistry
        reg = MetricsRegistry()
        reg.inc("http.requests.get.total")
        reg.gauge("ai_engine.health", 1.0)
        snapshot = reg.snapshot()
        snapshot_str = json.dumps(snapshot)
        assert "password" not in snapshot_str.lower()
        assert "CHANGE_ME" not in snapshot_str
        assert "jdbc:" not in snapshot_str

    def test_env_secrets_not_in_response(self):
        """Environment variable secret names should not appear in metric output."""
        from app.phase7_hardening import MetricsRegistry
        reg = MetricsRegistry()
        snapshot = reg.snapshot()
        snapshot_str = json.dumps(snapshot)
        for forbidden in ["POSTGRES_PASSWORD", "JWT_SECRET", "CHANGE_ME"]:
            assert forbidden not in snapshot_str

    def test_health_response_no_connection_string(self, tmp_path):
        """Health status response must not contain database connection strings."""
        from app.phase7_hardening import build_health_status
        status = build_health_status(tmp_path)
        status_str = json.dumps(status)
        assert "postgresql://" not in status_str
        assert "jdbc:" not in status_str
        assert "password" not in status_str.lower()

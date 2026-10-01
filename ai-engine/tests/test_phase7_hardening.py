"""
CausalOps Phase 7 — Hardening Unit Tests
==========================================
Tests for the phase7_hardening module components.
These tests do NOT require running Docker or the AI engine.

Run with:
    PYTHONPATH=ai-engine pytest tests/test_phase7_hardening.py -v
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure ai-engine is on path
_repo = Path(__file__).parent.parent
sys.path.insert(0, str(_repo))
sys.path.insert(0, str(_repo / "ai-engine"))


class TestMetricsRegistry:
    """Tests for the in-process metrics registry."""

    def setup_method(self):
        from app.phase7_hardening import MetricsRegistry
        self.reg = MetricsRegistry()

    def test_counter_starts_at_zero(self):
        snap = self.reg.snapshot()
        assert snap["counters"] == {}

    def test_inc_single(self):
        self.reg.inc("http.total")
        assert self.reg.snapshot()["counters"]["http.total"] == 1

    def test_inc_accumulates(self):
        for _ in range(5):
            self.reg.inc("reqs")
        assert self.reg.snapshot()["counters"]["reqs"] == 5

    def test_gauge(self):
        self.reg.gauge("health", 1.0)
        self.reg.gauge("health", 0.0)
        assert self.reg.snapshot()["gauges"]["health"] == 0.0

    def test_histogram_mean(self):
        self.reg.observe("latency", 100.0)
        self.reg.observe("latency", 200.0)
        snap = self.reg.snapshot()
        assert snap["histograms"]["latency"]["mean_ms"] == 150.0

    def test_prometheus_text_contains_counters(self):
        self.reg.inc("http_req_total")
        text = self.reg.prometheus_text()
        assert "http_req_total" in text
        assert "# TYPE" in text


class TestRateLimiter:
    """Tests for the token bucket rate limiter."""

    def setup_method(self):
        from app.phase7_hardening import TokenBucketRateLimiter
        self.limiter = TokenBucketRateLimiter()
        # Add a test-only limit
        self.limiter.LIMITS["/test/limited"] = (2, 0.0)  # capacity=2, no refill

    def test_allows_within_capacity(self):
        assert self.limiter.is_allowed("1.1.1.1", "/test/limited") is True
        assert self.limiter.is_allowed("1.1.1.1", "/test/limited") is True

    def test_blocks_over_capacity(self):
        self.limiter.is_allowed("2.2.2.2", "/test/limited")
        self.limiter.is_allowed("2.2.2.2", "/test/limited")
        assert self.limiter.is_allowed("2.2.2.2", "/test/limited") is False

    def test_independent_per_ip(self):
        self.limiter.is_allowed("3.3.3.3", "/test/limited")
        self.limiter.is_allowed("3.3.3.3", "/test/limited")
        assert self.limiter.is_allowed("3.3.3.3", "/test/limited") is False
        # Different IP still allowed
        assert self.limiter.is_allowed("4.4.4.4", "/test/limited") is True

    def test_unknown_paths_not_limited(self):
        for _ in range(100):
            assert self.limiter.is_allowed("5.5.5.5", "/unknown/path") is True


class TestArtifactVerification:
    """Tests for startup artifact verification."""

    def test_passes_with_all_required_files(self, tmp_path):
        from app.phase7_hardening import verify_model_artifacts, _REQUIRED_ARTIFACTS
        for rel, _ in _REQUIRED_ARTIFACTS:
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'{"ok": true}')
        result = verify_model_artifacts(tmp_path)
        ok_count = sum(1 for v in result.values() if v.get("status") == "OK")
        assert ok_count == len(_REQUIRED_ARTIFACTS)

    def test_fails_with_missing_required_artifact(self, tmp_path):
        from app.phase7_hardening import verify_model_artifacts
        with pytest.raises(RuntimeError, match="Required model artifacts missing"):
            verify_model_artifacts(tmp_path)

    def test_checksum_prefix_in_result(self, tmp_path):
        from app.phase7_hardening import verify_model_artifacts, _REQUIRED_ARTIFACTS
        for rel, _ in _REQUIRED_ARTIFACTS:
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'{"ok": true}')
        result = verify_model_artifacts(tmp_path)
        # Required artifacts should have checksum prefixes
        for name, info in result.items():
            if info.get("status") == "OK":
                assert info.get("checksum_prefix") is not None
                assert len(info["checksum_prefix"]) == 16


class TestHealthStatus:
    """Tests for health status builder."""

    def test_up_status_when_all_artifacts_present(self, tmp_path):
        from app.phase7_hardening import build_health_status
        # Create required artifacts
        scm = tmp_path / "ml" / "models" / "causal_scm"
        scm.mkdir(parents=True)
        (scm / "model.json").write_bytes(b'{}')
        (scm / "coefficients.json").write_bytes(b'{}')
        fp = tmp_path / "ml" / "models" / "failure_prediction"
        fp.mkdir(parents=True)
        (fp / "manifest.json").write_bytes(b'{}')
        (tmp_path / "ml" / "models" / "classical_rca_rf_v1.joblib").write_bytes(b'joblib')

        status = build_health_status(tmp_path)
        assert status["status"] == "UP"

    def test_degraded_status_when_artifacts_missing(self, tmp_path):
        from app.phase7_hardening import build_health_status
        status = build_health_status(tmp_path)
        assert status["status"] == "DEGRADED"
        assert status["components"]["causal_scm"] == "UNAVAILABLE"

    def test_readiness_false_when_critical_artifacts_missing(self, tmp_path):
        from app.phase7_hardening import build_readiness_status
        is_ready, status = build_readiness_status(tmp_path)
        assert is_ready is False
        assert status["ready"] is False
        assert len(status["failed_components"]) > 0


class TestModelRegistry:
    """Tests for model registry builder."""

    def test_registry_returns_list(self, tmp_path):
        from app.phase7_hardening import build_model_registry
        registry = build_model_registry(tmp_path)
        assert isinstance(registry, list)
        assert len(registry) >= 2

    def test_registry_has_required_fields(self, tmp_path):
        from app.phase7_hardening import build_model_registry
        registry = build_model_registry(tmp_path)
        required_fields = {"model_name", "version", "type", "artifact_path", "artifact_present"}
        for model in registry:
            for field in required_fields:
                assert field in model, f"Missing field '{field}' in {model.get('model_name')}"

    def test_registry_includes_causal_scm(self, tmp_path):
        from app.phase7_hardening import build_model_registry
        registry = build_model_registry(tmp_path)
        names = [m["model_name"] for m in registry]
        assert "causal_scm_v1" in names

    def test_registry_includes_failure_prediction(self, tmp_path):
        from app.phase7_hardening import build_model_registry
        registry = build_model_registry(tmp_path)
        names = [m["model_name"] for m in registry]
        assert "failure_prediction_v1" in names

    def test_failure_prediction_documents_limitations(self, tmp_path):
        from app.phase7_hardening import build_model_registry
        registry = build_model_registry(tmp_path)
        fp = next(m for m in registry if m["model_name"] == "failure_prediction_v1")
        assert "known_limitations" in fp
        limitations = fp["known_limitations"]
        assert len(limitations) > 0
        # Must document the 30% target-service accuracy limitation
        combined = " ".join(limitations)
        assert "30%" in combined


class TestStructuredLogging:
    """Tests for the JSON structured log formatter."""

    def test_formatter_produces_valid_json(self):
        import logging
        from app.phase7_hardening import StructuredFormatter
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="test.py", lineno=1,
            msg="Test message", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "Test message"
        assert parsed["severity"] == "INFO"
        assert parsed["service"] == "causalops-ai-engine"
        assert "timestamp" in parsed

    def test_formatter_no_secret_leakage(self):
        import logging
        from app.phase7_hardening import StructuredFormatter
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test", level=logging.WARNING,
            pathname="test.py", lineno=1,
            msg="Connection to postgres using password=secret123",
            args=(), exc_info=None,
        )
        output = formatter.format(record)
        # The formatter should not alter messages; it's the responsibility of
        # callers to not log secrets. But the formatter itself should not ADD secrets.
        assert "POSTGRES_PASSWORD" not in output
        assert "JWT_SECRET" not in output

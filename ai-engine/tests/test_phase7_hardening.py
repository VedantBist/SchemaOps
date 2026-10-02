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


class TestHealthStatus:
    """Health reports the engine's real dependencies: database and model store."""

    def test_up_when_database_and_model_store_are_available(self, tmp_path, monkeypatch):
        from app import phase7_hardening as h
        monkeypatch.setenv("ENGINE_MODEL_DIR", str(tmp_path))
        monkeypatch.setattr(h, "_database_ok", lambda: True)
        status = h.build_health_status()
        assert status["status"] == "UP"
        assert status["components"] == {"database": "HEALTHY", "model_store": "HEALTHY"}

    def test_degraded_and_not_ready_without_database(self, tmp_path, monkeypatch):
        from app import phase7_hardening as h
        monkeypatch.setenv("ENGINE_MODEL_DIR", str(tmp_path))
        monkeypatch.setattr(h, "_database_ok", lambda: False)
        assert h.build_health_status()["status"] == "DEGRADED"
        ready, body = h.build_readiness_status()
        assert ready is False and body["failed_components"] == ["database"]

    def test_unwritable_model_store_is_reported(self, tmp_path, monkeypatch):
        from app import phase7_hardening as h
        blocker = tmp_path / "file"
        blocker.write_text("x")
        monkeypatch.setenv("ENGINE_MODEL_DIR", str(blocker / "models"))
        monkeypatch.setattr(h, "_database_ok", lambda: True)
        assert h.build_health_status()["components"]["model_store"] == "UNAVAILABLE"



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

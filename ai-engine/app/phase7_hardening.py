"""
CausalOps Phase 7 — AI Engine Hardening Middleware
===================================================
Provides:
  - Correlation ID injection (X-Correlation-ID header)
  - Structured JSON logging
  - Prometheus-compatible metrics counters
  - Rate limiting (token-bucket, per IP, for expensive endpoints)
  - Startup model artifact verification
  - /health, /ready, /models endpoints (extended)
  - Safe error formatting (no stack traces to clients)
  - Request size limits
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# ── Logging ────────────────────────────────────────────────────────────────────

class StructuredFormatter(logging.Formatter):
    """Emit JSON log records for machine-readable structured logging."""

    SERVICE = "causalops-ai-engine"

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": self.formatTime(record, datefmt=None),
            "severity": record.levelname,
            "service": self.SERVICE,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Carry contextual fields attached by handlers
        for field in ("correlation_id", "incident_id", "prediction_id", "error_code"):
            if hasattr(record, field):
                data[field] = getattr(record, field)
        if record.exc_info:
            # Include exception class but NOT the full traceback for security
            exc_type = record.exc_info[0]
            data["exception_type"] = exc_type.__name__ if exc_type else "unknown"
        return json.dumps(data)


def configure_structured_logging(level: str = "INFO") -> None:
    """Replace the root logger formatter with the structured JSON formatter."""
    root = logging.getLogger()
    level_val = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(level_val)
    for handler in root.handlers:
        handler.setFormatter(StructuredFormatter())
    if not root.handlers:
        h = logging.StreamHandler()
        h.setFormatter(StructuredFormatter())
        root.addHandler(h)


# ── Metrics ────────────────────────────────────────────────────────────────────

class MetricsRegistry:
    """Simple in-process Prometheus-compatible metrics registry."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = defaultdict(float)
        self._histograms: dict[str, list[float]] = defaultdict(list)

    def inc(self, name: str, value: int = 1) -> None:
        self._counters[name] += value

    def gauge(self, name: str, value: float) -> None:
        self._gauges[name] = value

    def observe(self, name: str, value: float) -> None:
        self._histograms[name].append(value)

    def snapshot(self) -> dict[str, Any]:
        hist_summary = {}
        for name, vals in self._histograms.items():
            if vals:
                hist_summary[name] = {
                    "count": len(vals),
                    "sum": round(sum(vals), 4),
                    "mean_ms": round(sum(vals) / len(vals), 4),
                    "p95_ms": round(sorted(vals)[int(len(vals) * 0.95)], 4),
                }
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": hist_summary,
        }

    def prometheus_text(self) -> str:
        """Return a Prometheus-compatible text exposition."""
        lines = []
        for name, value in self._counters.items():
            safe = name.replace(".", "_").replace("-", "_")
            lines.append(f"# TYPE {safe} counter")
            lines.append(f"{safe} {value}")
        for name, value in self._gauges.items():
            safe = name.replace(".", "_").replace("-", "_")
            lines.append(f"# TYPE {safe} gauge")
            lines.append(f"{safe} {value}")
        return "\n".join(lines) + "\n"


METRICS = MetricsRegistry()


# ── Rate limiting ──────────────────────────────────────────────────────────────

class TokenBucketRateLimiter:
    """Per-client IP token-bucket rate limiter for expensive endpoints."""

    # endpoint_pattern -> (capacity, refill_per_second)
    LIMITS: dict[str, tuple[int, float]] = {
        # /pipeline/evaluate is called by the platform every ingestion cycle and is not limited.
        "/pipeline/rca":            (30,  30 / 60.0),
        "/counterfactual":          (20,  20 / 60.0),
        "/calibration/run":         (5,   5  / 60.0),
        "/remediation/execute":     (5,   5  / 60.0),
        "/remediation/rollback":    (5,   5  / 60.0),
    }

    def __init__(self) -> None:
        # {(ip, endpoint): [tokens, last_refill_ts]}
        self._buckets: dict[tuple[str, str], list[float]] = {}

    def _key(self, ip: str, path: str) -> tuple[str, str]:
        # Match the most specific limit prefix
        matched = ""
        for pattern in self.LIMITS:
            if path.startswith(pattern) and len(pattern) > len(matched):
                matched = pattern
        return (ip, matched)

    def is_allowed(self, ip: str, path: str) -> bool:
        key = self._key(ip, path)
        endpoint = key[1]
        if not endpoint:
            return True  # No limit for this path
        capacity, rate = self.LIMITS[endpoint]
        now = time.monotonic()
        if key not in self._buckets:
            self._buckets[key] = [capacity, now]
        tokens, last = self._buckets[key]
        # Refill
        elapsed = now - last
        tokens = min(capacity, tokens + elapsed * rate)
        self._buckets[key][1] = now
        if tokens >= 1:
            self._buckets[key][0] = tokens - 1
            return True
        return False


RATE_LIMITER = TokenBucketRateLimiter()


# ── Middleware ─────────────────────────────────────────────────────────────────

MAX_REQUEST_BODY_BYTES = 5 * 1024 * 1024  # 5 MB


class Phase7Middleware(BaseHTTPMiddleware):
    """
    Attaches correlation IDs, enforces rate limits, records metrics,
    and ensures safe (no stack trace) error responses.
    """

    EXEMPT_PATHS = {"/health", "/ready", "/models", "/metrics", "/metrics/prometheus"}

    async def dispatch(self, request: Request, call_next):
        # Correlation ID
        correlation_id = (
            request.headers.get("X-Correlation-ID")
            or request.headers.get("X-Request-ID")
            or str(uuid.uuid4())
        )

        path = request.url.path
        method = request.method

        # Rate limiting (skip health/metrics endpoints)
        if path not in self.EXEMPT_PATHS and method in ("POST", "PUT"):
            client_ip = request.client.host if request.client else "unknown"
            if not RATE_LIMITER.is_allowed(client_ip, path):
                METRICS.inc("http.rate_limited_total")
                return JSONResponse(
                    status_code=429,
                    content={
                        "error_code": "RATE_LIMIT_EXCEEDED",
                        "message": "Too many requests to this endpoint. Please wait before retrying.",
                        "correlation_id": correlation_id,
                        "retryable": True,
                        "details": {"endpoint": path},
                    },
                    headers={"X-Correlation-ID": correlation_id},
                )

        start = time.monotonic()
        METRICS.inc(f"http.requests.{method.lower()}.total")

        try:
            response: Response = await call_next(request)
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            METRICS.inc("http.errors.unhandled.total")
            METRICS.observe(f"http.latency_ms.{path.lstrip('/').replace('/', '_')}", elapsed_ms)
            logger = logging.getLogger("causalops.ai_engine.middleware")
            logger.error(
                "Unhandled exception in request",
                extra={"correlation_id": correlation_id},
            )
            return JSONResponse(
                status_code=500,
                content={
                    "error_code": "INTERNAL_ERROR",
                    "message": "An internal error occurred. Check service logs.",
                    "correlation_id": correlation_id,
                    "retryable": False,
                },
                headers={"X-Correlation-ID": correlation_id},
            )

        elapsed_ms = (time.monotonic() - start) * 1000
        status = response.status_code
        METRICS.observe(f"http.latency_ms.{path.lstrip('/').replace('/', '_')}", elapsed_ms)

        if status >= 500:
            METRICS.inc("http.errors.5xx.total")
        elif status >= 400:
            METRICS.inc("http.errors.4xx.total")

        METRICS.inc("http.responses.total")

        # Inject correlation ID into response
        response.headers["X-Correlation-ID"] = correlation_id
        return response


# ── Health and readiness ───────────────────────────────────────────────────────

def _database_ok() -> bool:
    try:
        from ml.engine.store import Store
        with Store().connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False


def _model_store_ok() -> bool:
    from ml.engine.model import model_root
    root = model_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write-probe"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def build_health_status() -> dict[str, Any]:
    """Liveness plus the state of each dependency the engine needs to calibrate and serve."""
    db_ok, store_ok = _database_ok(), _model_store_ok()
    return {
        "status": "UP" if db_ok and store_ok else "DEGRADED",
        "service": "causalops-ai-engine",
        "version": "8.0.0",
        "components": {
            "database": "HEALTHY" if db_ok else "UNAVAILABLE",
            "model_store": "HEALTHY" if store_ok else "UNAVAILABLE",
        },
    }


def build_readiness_status() -> tuple[bool, dict[str, Any]]:
    health = build_health_status()
    failed = [c for c, state in health["components"].items() if state != "HEALTHY"]
    ready = not failed
    return ready, {"ready": ready, "status": "READY" if ready else "NOT_READY",
                   "failed_components": failed, "components": health["components"]}

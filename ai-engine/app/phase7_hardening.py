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
        "/causal/counterfactual":    (10,  10 / 60.0),
        "/causal/recommendation":   (15,  15 / 60.0),
        "/predict/failure-v2":      (30,  30 / 60.0),
        "/analyze/root-cause":      (30,  30 / 60.0),
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


# ── Model artifact verification ────────────────────────────────────────────────

_REQUIRED_ARTIFACTS: list[tuple[str, str]] = [
    # (relative path from repo_root, friendly name)
    ("ml/models/causal_scm/model.json",        "Causal SCM topology"),
    ("ml/models/causal_scm/coefficients.json", "Causal SCM coefficients"),
    ("ml/models/failure_prediction/manifest.json", "Failure prediction manifest"),
]

_RECOMMENDED_ARTIFACTS: list[tuple[str, str]] = [
    ("ml/models/failure_prediction/rf_within_5s.pkl",  "RF failure_within_5s"),
    ("ml/models/failure_prediction/rf_within_10s.pkl", "RF failure_within_10s"),
    ("ml/models/failure_prediction/rf_within_30s.pkl", "RF failure_within_30s"),
    ("ml/models/failure_prediction/lr_within_5s.pkl",  "LR failure_within_5s"),
    ("ml/models/failure_prediction/lr_within_10s.pkl", "LR failure_within_10s"),
    ("ml/models/failure_prediction/lr_within_30s.pkl", "LR failure_within_30s"),
    ("ml/models/classical_rca_rf_v1.joblib",           "Classical RCA Random Forest"),
]


def verify_model_artifacts(repo_root: Path) -> dict[str, Any]:
    """
    Verifies required model artifacts exist at startup.
    Returns a status dict; raises RuntimeError if any REQUIRED artifact is missing.
    """
    results: dict[str, dict] = {}
    missing_required: list[str] = []

    for rel_path, name in _REQUIRED_ARTIFACTS:
        full = repo_root / rel_path
        exists = full.exists()
        checksum = None
        if exists:
            try:
                checksum = hashlib.sha256(full.read_bytes()).hexdigest()[:16]
            except Exception:
                checksum = "unreadable"
        results[name] = {"path": rel_path, "status": "OK" if exists else "MISSING", "checksum_prefix": checksum}
        if not exists:
            missing_required.append(name)

    for rel_path, name in _RECOMMENDED_ARTIFACTS:
        full = repo_root / rel_path
        exists = full.exists()
        results[name] = {"path": rel_path, "status": "OK" if exists else "OPTIONAL_MISSING"}

    if missing_required:
        raise RuntimeError(
            f"CRITICAL: Required model artifacts missing — {missing_required}. "
            "Do NOT retrain in production. Restore frozen artifacts and restart."
        )

    return results


# ── Model version registry ─────────────────────────────────────────────────────

def build_model_registry(repo_root: Path) -> list[dict[str, Any]]:
    """Returns structured model version info for GET /models endpoint."""
    registry = []

    # Classical RCA
    rca_path = repo_root / "ml" / "models" / "classical_rca_rf_v1.joblib"
    registry.append({
        "model_name": "classical_rca_rf_v1",
        "version": "1.0.0",
        "type": "RCA",
        "algorithm": "Random Forest",
        "dataset_version": "ml_v1",
        "feature_schema_version": "ml_v1",
        "artifact_path": "ml/models/classical_rca_rf_v1.joblib",
        "artifact_present": rca_path.exists(),
        "artifact_checksum_prefix": _file_checksum(rca_path),
        "description": "Topology-aware Random Forest for root-cause classification (Phase 2)",
    })

    # Causal SCM
    scm_model = repo_root / "ml" / "models" / "causal_scm" / "model.json"
    scm_coef = repo_root / "ml" / "models" / "causal_scm" / "coefficients.json"
    scm_present = scm_model.exists() and scm_coef.exists()
    scm_config_path = repo_root / "ml" / "causal" / "config.json"
    scm_config: dict = {}
    if scm_config_path.exists():
        try:
            scm_config = json.loads(scm_config_path.read_text())
        except Exception:
            pass
    registry.append({
        "model_name": "causal_scm_v1",
        "version": "1.0.0",
        "type": "CausalSCM",
        "algorithm": "Topology-Constrained Lagged Ridge SCM",
        "dataset_version": "tg_v1",
        "lag_order": scm_config.get("lag_order", 5),
        "ridge_alpha": scm_config.get("ridge_alpha", 1.0),
        "bootstrap_resamples": scm_config.get("n_bootstrap", 50),
        "stable_edges": 48,
        "artifact_path": "ml/models/causal_scm/",
        "artifact_present": scm_present,
        "artifact_checksum_prefix": _file_checksum(scm_model),
        "description": "Phase 3B Topology-Constrained Lagged SCM for counterfactual simulation",
    })

    # Failure prediction
    fp_manifest_path = repo_root / "ml" / "models" / "failure_prediction" / "manifest.json"
    fp_manifest: dict = {}
    if fp_manifest_path.exists():
        try:
            fp_manifest = json.loads(fp_manifest_path.read_text())
        except Exception:
            pass
    registry.append({
        "model_name": "failure_prediction_v1",
        "version": "1.0.0",
        "type": "FailurePrediction",
        "algorithm": "Random Forest + Logistic Regression (multi-horizon)",
        "dataset_version": "failure_prediction_v1",
        "horizons": ["within_5s", "within_10s", "within_30s"],
        "training_seed": 42,
        "artifact_path": "ml/models/failure_prediction/",
        "artifact_present": fp_manifest_path.exists(),
        "evaluation_summary": fp_manifest.get("evaluation_summary", {}),
        "description": "Phase 6A pre-failure prediction engine (VALIDATED_WITH_LIMITATIONS)",
        "known_limitations": [
            "30% target-service pre-onset accuracy",
            "40% fault-type pre-onset accuracy",
            "4-5s realized lead time on tg_v1 benchmark",
            "Fixed rolling buffer required in production streaming",
        ],
    })

    # Temporal GNN
    gnn_results = repo_root / "ml" / "temporal_gnn" / "results.json"
    registry.append({
        "model_name": "temporal_gnn_v1",
        "version": "1.0.0",
        "type": "TemporalGNN",
        "algorithm": "Spatio-Temporal Graph Neural Network",
        "dataset_version": "tg_v1",
        "artifact_path": "ml/models/temporal_gnn/",
        "artifact_present": (repo_root / "ml" / "models" / "temporal_gnn").exists(),
        "description": "Phase 2D Temporal GNN for spatio-temporal RCA",
    })

    return registry


def _file_checksum(path: Path) -> Optional[str]:
    if not path or not path.exists():
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except Exception:
        return None


# ── Health status builder ──────────────────────────────────────────────────────

def build_health_status(repo_root: Path) -> dict[str, Any]:
    """Build a detailed health/liveness status for GET /health."""
    scm_path = repo_root / "ml" / "models" / "causal_scm"
    scm_ok = (scm_path / "model.json").exists() and (scm_path / "coefficients.json").exists()

    fp_manifest = repo_root / "ml" / "models" / "failure_prediction" / "manifest.json"
    fp_ok = fp_manifest.exists()

    rca_ok = (repo_root / "ml" / "models" / "classical_rca_rf_v1.joblib").exists()

    dataset_ok = (repo_root / "dataset" / "tg_v1").exists()

    overall = "UP" if (scm_ok and fp_ok and rca_ok) else "DEGRADED"
    cf_status = "HEALTHY" if scm_ok else "UNAVAILABLE"

    return {
        "status": overall,
        # Top-level counterfactual_engine for backwards compatibility with existing tests
        "counterfactual_engine": cf_status,
        "service": "causalops-ai-engine",
        "version": "7.0.0",
        "components": {
            "rca_model":             "HEALTHY" if rca_ok else "UNAVAILABLE",
            "causal_scm":            "HEALTHY" if scm_ok else "UNAVAILABLE",
            "counterfactual_engine": cf_status,
            "failure_prediction":    "HEALTHY" if fp_ok else "UNAVAILABLE",
            "dataset":               "HEALTHY" if dataset_ok else "UNAVAILABLE",
        },
    }


def build_readiness_status(repo_root: Path) -> tuple[bool, dict[str, Any]]:
    """
    Build readiness check. Returns (is_ready, response_dict).
    Service is NOT ready if any critical component is missing.
    """
    health = build_health_status(repo_root)
    comps = health["components"]
    critical = ["rca_model", "causal_scm", "counterfactual_engine"]
    failed = [c for c in critical if comps.get(c) != "HEALTHY"]
    ready = len(failed) == 0
    return ready, {
        "ready": ready,
        "status": "READY" if ready else "NOT_READY",
        "failed_components": failed,
        "components": comps,
    }

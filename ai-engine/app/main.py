"""CausalOps AI engine.

Serves the environment-agnostic engine (ml/engine): calibration, anomaly gate, forecasting,
root-cause analysis and counterfactual simulation, each learned per environment from the
telemetry the platform API stores in PostgreSQL, plus the remediation executors (Docker,
Kubernetes, signed webhook) that the platform API drives. The archived tg_v1 research models
and the journal-based legacy remediation/orchestration endpoints are no longer served.
"""
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

# Repository root: /app in Docker (ml/ is mounted there), the checkout root locally.
_this_file = Path(__file__).resolve()
repo_root = next((p for p in (_this_file.parent.parent, _this_file.parent.parent.parent) if (p / "ml").exists()),
                 _this_file.parent.parent)
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from .phase7_hardening import (  # noqa: E402
    Phase7Middleware,
    METRICS,
    configure_structured_logging,
    build_health_status,
    build_readiness_status,
)
from .engine_api import router as engine_router  # noqa: E402
from .executor_api import router as executor_router  # noqa: E402

configure_structured_logging(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("causalops.ai_engine")

_cors_origins = [o.strip() for o in os.environ.get(
    "CAUSALOPS_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173,http://localhost:8080").split(",") if o.strip()]

app = FastAPI(title="CausalOps AI Engine", version="8.0.0", docs_url="/docs", redoc_url="/redoc")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Correlation-ID", "X-Request-ID", "Authorization", "X-Engine-Token"],
)
app.add_middleware(Phase7Middleware)
app.include_router(engine_router)
app.include_router(executor_router)


@app.get('/health')
def health():
    """Liveness: the process runs; components report whether they are usable."""
    status = build_health_status()
    METRICS.gauge("ai_engine.health", 1.0 if status["status"] == "UP" else 0.0)
    return status


@app.get('/ready')
def ready():
    """Readiness: database reachable and model store writable."""
    is_ready, status = build_readiness_status()
    METRICS.gauge("ai_engine.ready", 1.0 if is_ready else 0.0)
    return status if is_ready else JSONResponse(status_code=503, content=status)


@app.get('/metrics')
def metrics_json():
    return METRICS.snapshot()


@app.get('/metrics/prometheus', response_class=PlainTextResponse)
def metrics_prometheus():
    return METRICS.prometheus_text()

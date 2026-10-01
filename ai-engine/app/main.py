import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional, List
from fastapi import Body, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

# Resolve repo root: works both locally (3 levels up from ai-engine/app/main.py)
# and in Docker (WORKDIR=/app so ml/ and dataset/ are at /app/ml and /app/dataset)
_this_file = Path(__file__).resolve()
# Try the parent-of-parent-of-parent (local dev layout: causalops/ai-engine/app/main.py)
_candidate_local = _this_file.parent.parent.parent
# Try the parent-of-parent (Docker layout: /app/app/main.py -> /app)
_candidate_docker = _this_file.parent.parent

# Pick whichever candidate has ml/ subdirectory
if (_candidate_docker / "ml").exists():
    repo_root = _candidate_docker
elif (_candidate_local / "ml").exists():
    repo_root = _candidate_local
else:
    # Last resort: use the Docker candidate (WORKDIR is /app)
    repo_root = _candidate_docker

if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from .rca.scorer import score
from .rca import classifier
from .prediction.baseline import predict
from .simulation.propagate import simulate
from .phase7_hardening import (
    Phase7Middleware,
    METRICS,
    configure_structured_logging,
    verify_model_artifacts,
    build_health_status,
    build_readiness_status,
    build_model_registry,
)

# ── Structured logging (Phase 7) ──────────────────────────────────────────────
configure_structured_logging(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("causalops.ai_engine")

# ── Startup artifact verification (Phase 7) ───────────────────────────────────
try:
    _artifact_status = verify_model_artifacts(repo_root)
    logger.info("Model artifact verification passed: %d artifacts checked", len(_artifact_status))
except RuntimeError as _artifact_err:
    logger.critical("STARTUP FAILED: %s", _artifact_err)
    raise

# ── CORS (Phase 7: environment-configured origins) ────────────────────────────
_cors_raw = os.environ.get(
    "CAUSALOPS_CORS_ORIGINS",
    "http://localhost:3000,http://localhost:5173,http://localhost:8080",
)
_cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()]

app = FastAPI(
    title="CausalOps Explainable AI Engine",
    version="7.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Correlation-ID", "X-Request-ID", "Authorization"],
)

# Phase 7 hardening middleware (correlation IDs, rate limiting, metrics)
app.add_middleware(Phase7Middleware)


# ── Phase 7: Extended health / readiness / model-version / metrics endpoints ──

@app.get('/health')
def health():
    """
    Liveness probe. Returns UP if the process is running and required artifacts
    are mounted. Components individually report HEALTHY or UNAVAILABLE.
    """
    status = build_health_status(repo_root)
    METRICS.gauge("ai_engine.health", 1.0 if status["status"] == "UP" else 0.0)
    return status


@app.get('/ready')
def ready():
    """
    Readiness probe. Returns 200/READY only when all critical components are
    available. Returns 503/NOT_READY if any required model artifact is missing.
    """
    is_ready, status = build_readiness_status(repo_root)
    METRICS.gauge("ai_engine.ready", 1.0 if is_ready else 0.0)
    if not is_ready:
        return JSONResponse(status_code=503, content=status)
    return status


@app.get('/models')
def model_registry():
    """
    Returns the full model registry: name, version, dataset version,
    artifact presence, checksum prefix, and known limitations.
    """
    return {
        "service": "causalops-ai-engine",
        "version": "7.0.0",
        "models": build_model_registry(repo_root),
    }


@app.get('/metrics')
def metrics_json():
    """Returns operational metrics as JSON (counters, latency histograms)."""
    return METRICS.snapshot()


@app.get('/metrics/prometheus', response_class=PlainTextResponse)
def metrics_prometheus():
    """Returns Prometheus text-format metrics for scraping."""
    return METRICS.prometheus_text()

class Analysis(BaseModel):
    topology: dict
    telemetry: list[dict]
    mode: Optional[str] = "classical_ml"


class Prediction(BaseModel):
    topology: dict
    services: list[dict]


class Simulation(BaseModel):
    topology: dict
    services: list[dict]
    target: str
    reductionPercent: float = 70.0


@app.post('/analyze/root-cause')
def rca(body: Analysis = Body(...)):
    mode = (body.mode or "classical_ml").lower()

    if mode == "heuristic":
        res = score(body.topology, body.telemetry)
        res["rca_method"] = "heuristic"
        return res

    # Classical ML RCA with controlled heuristic fallback
    try:
        return classifier.predict_root_cause(body.topology, body.telemetry)
    except Exception as e:
        logger.error(f"[RCA] Classical ML inference failed ({e}), invoking heuristic fallback", exc_info=True)
        res = score(body.topology, body.telemetry)
        res["rca_method"] = "heuristic_fallback"
        res["methodology"] = f"HEURISTIC FALLBACK (ML error: {type(e).__name__}): " + res.get("methodology", "")
        # Explicitly do NOT set 'model': 'classical_rca_rf_v1'
        return res


@app.post('/rca/ml')
def rca_ml(body: Analysis = Body(...)):
    """Dedicated endpoint for classical ML RCA inference."""
    return classifier.predict_root_cause(body.topology, body.telemetry)


@app.post('/predict/failure')
def failure(body: Prediction = Body(...)):
    return predict(body.services)


@app.post('/simulate/counterfactual')
def counterfactual(body: Simulation = Body(...)):
    return simulate(body.topology, body.services, body.target, body.reductionPercent)


class CausalCounterfactualRequest(BaseModel):
    experiment_id: Optional[str] = None
    incident_id: Optional[str] = None
    root_cause: Optional[Any] = None
    intervention: Optional[Any] = None
    intervention_spec: Optional[Any] = None
    intervention_magnitude: Optional[float] = None
    start_step: Optional[int] = 5
    horizon: Optional[int] = None
    simulation_resolution: Optional[float] = 1.0
    telemetry_array: Optional[list] = None


@app.post('/causal/counterfactual')
def causal_counterfactual(body: CausalCounterfactualRequest = Body(...)):
    """
    Executes Pearl 3-step counterfactual causal simulation under do(root_cause = nominal).
    Returns factual, counterfactual, effect trajectories, avoided impact, validity metadata, and frame-by-frame timeline.
    """

    from ml.causal.counterfactual import generate_counterfactual
    from ml.causal.design_matrix import NODE_TO_INDEX, FEATURE_TO_PRIMARY_INDEX
    from dataset.tg_v1.loader import TemporalGraphDataset

    target_exp = body.experiment_id or (body.incident_id if body.incident_id and body.incident_id.startswith("EXP-") else None)

    sample = None
    if target_exp:
        for split in ["test", "validation", "train"]:
            try:
                ds = TemporalGraphDataset(dataset_dir="dataset/tg_v1", split=split)
                for s in ds:
                    if s.experiment_id == target_exp:
                        sample = s
                        break
                if sample is not None:
                    break
            except Exception:
                continue

    if sample is None and body.telemetry_array:
        import numpy as np
        sample = np.array(body.telemetry_array, dtype=np.float64)

    if sample is None:
        # Default fallback to test sample EXP-015 (canonical DB_LATENCY incident)
        try:
            ds = TemporalGraphDataset(dataset_dir="dataset/tg_v1", split="test")
            # Look for EXP-015 or first fault sample
            for s in ds:
                if s.experiment_id == "EXP-015" or s.is_fault:
                    sample = s
                    break
            if sample is None:
                sample = ds[0]
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={
                    "error_code": "DATASET_LOAD_FAILED",
                    "message": f"Failed to load dataset sample: {e}",
                    "retryable": False,
                },
            )

    try:
        res = generate_counterfactual(
            observed_trajectory=sample,
            root_cause=body.root_cause,
            intervention_spec=body.intervention_spec or body.intervention,
            start_step=body.start_step or 5,
            horizon=body.horizon,
            intervention_magnitude=body.intervention_magnitude,
        )

        T = len(res["visualization_data"]["timesteps"])
        obs_traj = res["observed_trajectory"]
        cf_traj = res["counterfactual_trajectory"]
        eff_traj = res["effect_trajectory"]

        timeline_frames = []
        for t in range(T):
            services_data = {}
            for node, n_i in NODE_TO_INDEX.items():
                p99 = float(cf_traj[t, n_i, FEATURE_TO_PRIMARY_INDEX["p99_latency"]])
                err = float(cf_traj[t, n_i, FEATURE_TO_PRIMARY_INDEX["error_rate"]])
                db_lat = float(cf_traj[t, n_i, FEATURE_TO_PRIMARY_INDEX["db_latency"]]) if "db_latency" in FEATURE_TO_PRIMARY_INDEX else 0.0

                if p99 > 800.0 or err > 5.0 or db_lat > 500.0:
                    state = "CRITICAL"
                elif p99 > 200.0 or err > 1.0 or db_lat > 100.0:
                    state = "DEGRADED"
                else:
                    state = "OPERATIONAL"

                services_data[node] = {
                    "observed": {feat: round(float(obs_traj[t, n_i, f_i]), 2) for feat, f_i in FEATURE_TO_PRIMARY_INDEX.items()},
                    "counterfactual": {feat: round(float(cf_traj[t, n_i, f_i]), 2) for feat, f_i in FEATURE_TO_PRIMARY_INDEX.items()},
                    "effect_delta": {feat: round(float(eff_traj[t, n_i, f_i]), 2) for feat, f_i in FEATURE_TO_PRIMARY_INDEX.items()},
                    "state": state,
                }

            timeline_frames.append({
                "time_seconds": t,
                "step": t,
                "is_intervened": (t >= res["intervention_metadata"]["intervention_start_step"]),
                "root_cause_observed": float(res["visualization_data"]["root_cause_series"]["observed"][t]),
                "root_cause_counterfactual": float(res["visualization_data"]["root_cause_series"]["counterfactual"][t]),
                "gateway_latency_observed": float(res["visualization_data"]["gateway_latency_series"]["observed"][t]),
                "gateway_latency_counterfactual": float(res["visualization_data"]["gateway_latency_series"]["counterfactual"][t]),
                "gateway_error_rate_observed": float(res["visualization_data"]["gateway_error_rate_series"]["observed"][t]),
                "gateway_error_rate_counterfactual": float(res["visualization_data"]["gateway_error_rate_series"]["counterfactual"][t]),
                "services": services_data,
            })

        return {
            "experiment_id": res["experiment_id"],
            "root_cause": res["root_cause"],
            "intervention_metadata": res["intervention_metadata"],
            "avoided_impact": res["avoided_impact"],
            "validity_metadata": res["confidence_metadata"],
            "warnings": res["confidence_metadata"]["warnings"],
            "counterfactual_trajectory": cf_traj.tolist(),
            "observed_trajectory": obs_traj.tolist(),
            "effect_trajectory": eff_traj.tolist(),
            "visualization_data": res["visualization_data"],
            "simulation_resolution": body.simulation_resolution or 1.0,
            "total_horizon_seconds": T,
            "timeline": timeline_frames,
        }
    except Exception as e:
        logger.exception("Counterfactual simulation error: %s", e)
        return JSONResponse(
            status_code=400,
            content={
                "error_code": "COUNTERFACTUAL_SIMULATION_ERROR",
                "message": str(e),
                "retryable": False,
            },
        )


class CausalRecommendationRequest(BaseModel):
    experiment_id: Optional[str] = None
    root_cause: Optional[Any] = None
    incident_status: Optional[str] = None
    start_step: Optional[int] = 5
    horizon: Optional[int] = 20
    telemetry_array: Optional[list] = None
    fault_type: Optional[str] = None


@app.post('/causal/recommendation')
def causal_recommendation(body: CausalRecommendationRequest = Body(...)):
    """
    Generates safe, explainable remediation recommendations with mandatory human approval.
    Evaluates candidate actions using Pearl 3-step counterfactual causal simulation.
    """

    from ml.remediation.action_catalog import ActionCatalog
    from ml.remediation.recommender import RemediationRecommender
    from ml.causal.scm import TopologyConstrainedLaggedSCM
    from dataset.tg_v1.loader import TemporalGraphDataset

    sample = None
    if body.experiment_id:
        for split in ["test", "validation", "train"]:
            try:
                ds = TemporalGraphDataset(dataset_dir="dataset/tg_v1", split=split)
                for s in ds:
                    if s.experiment_id == body.experiment_id:
                        sample = s
                        break
                if sample is not None:
                    break
            except Exception:
                continue

    if sample is None and body.telemetry_array:
        import numpy as np
        sample = np.array(body.telemetry_array, dtype=np.float64)

    if sample is None:
        try:
            ds = TemporalGraphDataset(dataset_dir="dataset/tg_v1", split="test")
            sample = ds[0]
        except Exception as e:
            return {"error": f"Failed to load sample: {e}"}

    scm = TopologyConstrainedLaggedSCM.load("ml/models/causal_scm")
    catalog = ActionCatalog.get_default_catalog()
    recommender = RemediationRecommender(catalog=catalog, scm=scm)

    rec = recommender.recommend(
        sample_or_trajectory=sample,
        root_cause=body.root_cause,
        incident_status=body.incident_status,
        start_step=body.start_step or 5,
        horizon=body.horizon,
        fault_type=body.fault_type,
    )

    # Register recommendation in execution engine
    executor = get_executor()
    if rec.get("recommendation_status") != "NO_REMEDIATION_REQUIRED":
        rec_id = executor.register_recommendation(rec, sample=sample)
        rec["recommendation_id"] = rec_id

    if "counterfactual_trajectory" in rec and rec["counterfactual_trajectory"] is not None:
        import numpy as np
        if isinstance(rec["counterfactual_trajectory"], np.ndarray):
            rec["counterfactual_trajectory"] = rec["counterfactual_trajectory"].tolist()

    return rec


# =========================================================================
# PHASE 5: CONTROLLED REMEDIATION EXECUTION & CLOSED-LOOP API
# =========================================================================

_executor_instance = None


def get_executor():
    global _executor_instance
    if _executor_instance is None:
        import sys
        from pathlib import Path
        # repo_root is module-level (handles both Docker and local paths)
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from ml.execution.executor import ClosedLoopRemediationExecutor
        _executor_instance = ClosedLoopRemediationExecutor()
    return _executor_instance


class RemediationApproveRequest(BaseModel):
    recommendation_id: str
    approved_by: str
    warning_acknowledged: Optional[bool] = False
    ttl_seconds: Optional[int] = 900


@app.post('/remediation/approve')
def approve_remediation(body: RemediationApproveRequest = Body(...)):
    """
    Creates an explicit, time-bounded approval record for an actionable recommendation.
    """
    executor = get_executor()
    try:
        appr = executor.approve_recommendation(
            recommendation_id=body.recommendation_id,
            approved_by=body.approved_by,
            warning_acknowledged=body.warning_acknowledged or False,
            ttl_seconds=body.ttl_seconds or 900,
        )
        return appr.to_dict()
    except Exception as e:
        return {"error": str(e), "approval_status": "REJECTED"}


class RemediationExecuteRequest(BaseModel):
    recommendation_id: str
    approval_id: str
    environment: Optional[str] = "LOCAL"
    dry_run: Optional[bool] = False
    simulation_mode: Optional[bool] = True


@app.post('/remediation/execute')
def execute_remediation(body: RemediationExecuteRequest = Body(...)):
    """
    Executes an approved remediation action within the policy boundary.
    """
    executor = get_executor()
    try:
        record = executor.execute_remediation(
            recommendation_id=body.recommendation_id,
            approval_id=body.approval_id,
            environment=body.environment or "LOCAL",
            dry_run=body.dry_run or False,
            simulation_mode=body.simulation_mode or True,
        )
        return record.to_dict()
    except Exception as e:
        return {"error": str(e), "state": "EXECUTION_FAILED"}


class RemediationVerifyRequest(BaseModel):
    execution_id: str


@app.post('/remediation/verify')
def verify_remediation(body: RemediationVerifyRequest = Body(...)):
    """
    Returns post-execution telemetry verification results.
    """
    executor = get_executor()
    record = executor.get_execution(body.execution_id)
    if not record:
        return {"error": f"Execution '{body.execution_id}' not found"}
    return {
        "execution_id": body.execution_id,
        "state": record.state,
        "verification_result": record.verification_result,
    }


class RemediationRollbackRequest(BaseModel):
    execution_id: str
    reason: Optional[str] = "Operator rollback requested"


@app.post('/remediation/rollback')
def rollback_remediation(body: RemediationRollbackRequest = Body(...)):
    """
    Returns rollback status for an execution.
    """
    executor = get_executor()
    record = executor.get_execution(body.execution_id)
    if not record:
        return {"error": f"Execution '{body.execution_id}' not found"}
    return {
        "execution_id": body.execution_id,
        "state": record.state,
        "rollback_result": record.rollback_result,
    }


@app.get('/remediation/executions/{execution_id}')
def get_execution_detail(execution_id: str):
    """
    Inspects details and chronological timeline for a specific remediation execution.
    """
    executor = get_executor()
    record = executor.get_execution(execution_id)
    if not record:
        return {"error": f"Execution '{execution_id}' not found"}
    return record.to_dict()


@app.get('/remediation/executions')
def list_executions():
    """
    Lists all closed-loop remediation executions.
    """
    executor = get_executor()
    return [e.to_dict() for e in executor.list_executions()]


# =========================================================================
# PHASE 6: PRODUCTION-GRADE HARDENING & MULTI-INCIDENT ORCHESTRATION API
# =========================================================================

_incident_manager_instance = None


def get_incident_manager():
    global _incident_manager_instance
    if _incident_manager_instance is None:
        import sys
        from pathlib import Path
        # repo_root is module-level (handles both Docker and local paths)
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from ml.orchestration.incident_manager import IncidentOrchestrationManager
        _incident_manager_instance = IncidentOrchestrationManager()
        # Seed an initial incident if empty
        if not _incident_manager_instance.list_incidents():
            _incident_manager_instance.process_anomaly(
                service="inventory-db",
                primary_variable="db_latency",
                fault_signature="lock_contention",
                severity="CRITICAL",
                detection_source="telemetry_anomaly_detector",
                root_cause_candidate="inventory-db",
            )
    return _incident_manager_instance


class AcknowledgeRequest(BaseModel):
    operator_id: Optional[str] = "oncall-sre@causalops.local"


@app.get('/incidents')
def list_incidents(
    state: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    correlation_group: Optional[str] = Query(None),
):
    """
    Lists active and historical incidents with optional state, severity, or correlation filtering.
    """
    mgr = get_incident_manager()
    incidents = mgr.list_incidents(
        state_filter=state,
        severity_filter=severity,
        correlation_group=correlation_group,
    )
    return [i.to_dict() for i in incidents]


@app.get('/incidents/{incident_id}')
def get_incident(incident_id: str):
    """
    Returns complete incident entity and state machine identity.
    """
    mgr = get_incident_manager()
    inc = mgr.get_incident(incident_id)
    if not inc:
        return JSONResponse(
            status_code=404,
            content={
                "error_code": "INCIDENT_NOT_FOUND",
                "message": f"Incident '{incident_id}' not found.",
                "incident_id": incident_id,
                "correlation_id": "UNKNOWN",
                "retryable": False,
            },
        )
    return inc.to_dict()


@app.get('/incidents/{incident_id}/timeline')
def get_incident_timeline(incident_id: str):
    """
    Returns chronological state transitions and audit timeline for an incident.
    """
    mgr = get_incident_manager()
    inc = mgr.get_incident(incident_id)
    if not inc:
        return JSONResponse(
            status_code=404,
            content={
                "error_code": "INCIDENT_NOT_FOUND",
                "message": f"Incident '{incident_id}' not found.",
                "incident_id": incident_id,
                "correlation_id": "UNKNOWN",
                "retryable": False,
            },
        )
    return inc.timeline


@app.get('/incidents/{incident_id}/health')
def get_incident_health(incident_id: str):
    """
    Returns telemetry health and canonical service health for an incident.
    """
    mgr = get_incident_manager()
    inc = mgr.get_incident(incident_id)
    if not inc:
        return JSONResponse(
            status_code=404,
            content={
                "error_code": "INCIDENT_NOT_FOUND",
                "message": f"Incident '{incident_id}' not found.",
                "incident_id": incident_id,
                "correlation_id": "UNKNOWN",
                "retryable": False,
            },
        )
    return {
        "incident_id": incident_id,
        "telemetry_health": inc.telemetry_health,
        "services_health": mgr.service_tracker.get_all_health(),
    }


@app.get('/incidents/{incident_id}/conflicts')
def get_incident_conflicts(incident_id: str):
    """
    Evaluates potential resource or dependency conflicts for the incident's remediation.
    """
    mgr = get_incident_manager()
    inc = mgr.get_incident(incident_id)
    if not inc:
        return JSONResponse(
            status_code=404,
            content={
                "error_code": "INCIDENT_NOT_FOUND",
                "message": f"Incident '{incident_id}' not found.",
                "incident_id": incident_id,
                "correlation_id": "UNKNOWN",
                "retryable": False,
            },
        )

    executor = get_executor()
    active_rems = []
    for exec_record in executor.list_executions():
        if exec_record.state in ["EXECUTING", "POLICY_VALIDATED"]:
            active_rems.append({
                "incident_id": exec_record.incident_id,
                "action_id": exec_record.action_id,
                "target_service": exec_record.target_service,
                "target_variable": exec_record.target_variable,
                "blast_radius_size": 2,
            })

    from ml.orchestration.conflict import ConflictDetector
    conflict = ConflictDetector.evaluate_conflict(
        candidate_incident_id=incident_id,
        candidate_action_id=inc.metadata.get("recommended_action_id", "ACT-UNKNOWN"),
        candidate_target_service=inc.root_cause or inc.affected_services[0],
        candidate_target_variable=inc.fault_signature,
        candidate_blast_radius_size=1,
        active_remediations=active_rems,
    )
    return conflict.to_dict()


@app.post('/incidents/{incident_id}/acknowledge')
def acknowledge_incident(incident_id: str, body: AcknowledgeRequest = Body(...)):
    """
    Records human on-call engineer acknowledgment for an active incident.
    """
    mgr = get_incident_manager()
    try:
        inc = mgr.acknowledge_incident(incident_id, operator_id=body.operator_id or "oncall-sre@causalops.local")
        return inc.to_dict()
    except ValueError as e:
        return JSONResponse(
            status_code=404,
            content={
                "error_code": "INCIDENT_NOT_FOUND",
                "message": str(e),
                "incident_id": incident_id,
                "correlation_id": "UNKNOWN",
                "retryable": False,
            },
        )


@app.get('/system/health')
def system_health():
    """
    Returns health status of all CausalOps components (Telemetry, AI, SCM, Execution).
    """
    mgr = get_incident_manager()
    return mgr.system_health.to_dict()


@app.get('/observability/metrics')
def observability_metrics():
    """
    Returns Phase 6 operational metrics counters.
    """
    mgr = get_incident_manager()
    return mgr.get_metrics()


# =========================================================================
# PHASE 6A: REAL FAILURE PREDICTION ENGINE API
# =========================================================================

_failure_predictor_instance = None


def get_failure_predictor():
    global _failure_predictor_instance
    if _failure_predictor_instance is None:
        from ml.failure_prediction.predictor import FailurePredictionService
        _failure_predictor_instance = FailurePredictionService(
            model_dir=str(repo_root / "ml" / "models" / "failure_prediction")
        )
    return _failure_predictor_instance


class FailurePredictionRequest(BaseModel):
    experiment_id: Optional[str] = None
    telemetry_array: Optional[list] = None
    fault_onset_step: Optional[int] = None


@app.post('/predict/failure-v2')
def predict_failure_v2(body: FailurePredictionRequest = Body(...)):
    """
    Phase 6A real failure prediction using trained LR/RF models.
    Returns multi-horizon failure probabilities (within_5s, within_10s, within_30s).
    Trained on the frozen tg_v1 experiment corpus (TRAIN=56, VAL=12, TEST=12).
    """
    import numpy as np
    from dataset.tg_v1.loader import TemporalGraphDataset

    predictor = get_failure_predictor()

    if not predictor.is_available():
        return JSONResponse(
            status_code=503,
            content={
                "error_code": "MODELS_NOT_TRAINED",
                "message": "Failure prediction models not available. Run the training pipeline first.",
                "retryable": False,
            }
        )

    # Resolve telemetry array
    x = None
    if body.telemetry_array:
        try:
            x = np.array(body.telemetry_array, dtype=np.float64)
        except Exception as e:
            return JSONResponse(
                status_code=400,
                content={"error_code": "INVALID_TELEMETRY", "message": str(e)}
            )

    if x is None and body.experiment_id:
        for split in ["test", "validation", "train"]:
            try:
                ds = TemporalGraphDataset(dataset_dir="dataset/tg_v1", split=split)
                for s in ds:
                    if s.experiment_id == body.experiment_id:
                        x = s.x
                        break
                if x is not None:
                    break
            except Exception:
                continue

    if x is None:
        return JSONResponse(
            status_code=400,
            content={
                "error_code": "NO_TELEMETRY",
                "message": "Provide either experiment_id or telemetry_array.",
            }
        )

    result = predictor.predict(
        x=x,
        fault_onset_step=body.fault_onset_step,
        experiment_id=body.experiment_id,
    )
    return result


@app.get('/predict/failure-v2/status')
def failure_prediction_status():
    """
    Returns Phase 6A model registry — which models are loaded and available.
    """
    predictor = get_failure_predictor()
    return predictor.get_model_registry()


@app.get('/predict/failure-v2/manifest')
def failure_prediction_manifest():
    """
    Returns the training manifest with evaluation results from the last training run.
    """
    import json
    manifest_path = repo_root / "ml" / "models" / "failure_prediction" / "manifest.json"
    if not manifest_path.exists():
        return JSONResponse(
            status_code=404,
            content={
                "error_code": "MANIFEST_NOT_FOUND",
                "message": "Training manifest not found. Run the training pipeline first.",
            }
        )
    with open(manifest_path) as f:
        return json.load(f)

    from ml.remediation.action_catalog import ActionCatalog
    from ml.remediation.recommender import RemediationRecommender
    from ml.causal.scm import TopologyConstrainedLaggedSCM
    from dataset.tg_v1.loader import TemporalGraphDataset

    sample = None
    if body.experiment_id:
        for split in ["test", "validation", "train"]:
            try:
                ds = TemporalGraphDataset(dataset_dir="dataset/tg_v1", split=split)
                for s in ds:
                    if s.experiment_id == body.experiment_id:
                        sample = s
                        break
                if sample is not None:
                    break
            except Exception:
                continue

    if sample is None and body.telemetry_array:
        import numpy as np
        sample = np.array(body.telemetry_array, dtype=np.float64)

    if sample is None:
        try:
            ds = TemporalGraphDataset(dataset_dir="dataset/tg_v1", split="test")
            sample = ds[0]
        except Exception as e:
            return {"error": f"Failed to load sample: {e}"}

    scm = TopologyConstrainedLaggedSCM.load("ml/models/causal_scm")
    catalog = ActionCatalog.get_default_catalog()
    recommender = RemediationRecommender(catalog=catalog, scm=scm)

    rec = recommender.recommend(
        sample_or_trajectory=sample,
        root_cause=body.root_cause,
        incident_status=body.incident_status,
        start_step=body.start_step or 5,
        horizon=body.horizon,
        fault_type=body.fault_type,
    )

    # Register recommendation in execution engine
    executor = get_executor()
    if rec.get("recommendation_status") != "NO_REMEDIATION_REQUIRED":
        rec_id = executor.register_recommendation(rec, sample=sample)
        rec["recommendation_id"] = rec_id

    if "counterfactual_trajectory" in rec and rec["counterfactual_trajectory"] is not None:
        import numpy as np
        if isinstance(rec["counterfactual_trajectory"], np.ndarray):
            rec["counterfactual_trajectory"] = rec["counterfactual_trajectory"].tolist()

    return rec


# =========================================================================
# PHASE 5: CONTROLLED REMEDIATION EXECUTION & CLOSED-LOOP API
# =========================================================================

_executor_instance = None


def get_executor():
    global _executor_instance
    if _executor_instance is None:
        import sys
        from pathlib import Path
        # repo_root is module-level (handles both Docker and local paths)
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from ml.execution.executor import ClosedLoopRemediationExecutor
        _executor_instance = ClosedLoopRemediationExecutor()
    return _executor_instance


class RemediationApproveRequest(BaseModel):
    recommendation_id: str
    approved_by: str
    warning_acknowledged: Optional[bool] = False
    ttl_seconds: Optional[int] = 900



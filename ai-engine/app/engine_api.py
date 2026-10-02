"""HTTP API of the environment-agnostic engine. Errors use real status codes."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ml.engine import calibrate, pipeline
from ml.engine.store import Store

router = APIRouter()
_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def _environment(environment_id: str):
    try:
        return store().environment(environment_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── calibration ───────────────────────────────────────────────────────────────
class CalibrationRequest(BaseModel):
    environment_id: str
    mode: str = Field(pattern="^(INITIAL|RETRAIN|MANUAL)$")
    trigger: str = "operator"


@router.post("/calibration/run", status_code=202)
def run_calibration(body: CalibrationRequest):
    _environment(body.environment_id)
    try:
        run_id = calibrate.start(store(), body.environment_id, body.mode, body.trigger)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"run_id": run_id, "status": "RUNNING"}


@router.get("/calibration/runs")
def calibration_runs(environment_id: str = Query(...), limit: int = Query(20, ge=1, le=200)):
    _environment(environment_id)
    return store().runs(environment_id, limit)


@router.get("/models")
def models(environment_id: str = Query(...)):
    _environment(environment_id)
    return store().models(environment_id)


# ── live pipeline ─────────────────────────────────────────────────────────────
class EvaluateRequest(BaseModel):
    environment_id: str
    at: datetime


@router.post("/pipeline/evaluate")
def evaluate(body: EvaluateRequest):
    _environment(body.environment_id)
    try:
        return pipeline.evaluate(store(), body.environment_id, body.at)
    except pipeline.NoModelError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


class AnalyseRequest(BaseModel):
    environment_id: str
    start: datetime
    end: datetime
    onset: Optional[datetime] = None
    incident_id: Optional[str] = None


@router.post("/pipeline/rca")
def analyse(body: AnalyseRequest):
    _environment(body.environment_id)
    if body.end <= body.start:
        raise HTTPException(status_code=422, detail="end must be after start")
    try:
        return pipeline.analyse(store(), body.environment_id, body.start, body.end, body.onset)
    except pipeline.NoModelError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


class CounterfactualRequest(BaseModel):
    environment_id: str
    start: datetime
    end: datetime
    unit: str = Field(description="service, database, or 'client->server' link to correct")
    magnitude: float = Field(1.0, gt=0.0, le=1.0, description="share of the deviation removed (1 = back to baseline)")
    intervene_from: Optional[datetime] = None


@router.post("/counterfactual")
def counterfactual(body: CounterfactualRequest):
    _environment(body.environment_id)
    if body.end <= body.start:
        raise HTTPException(status_code=422, detail="end must be after start")
    try:
        return pipeline.counterfactual(store(), body.environment_id, body.start, body.end, body.unit,
                                       body.magnitude, body.intervene_from)
    except pipeline.NoModelError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

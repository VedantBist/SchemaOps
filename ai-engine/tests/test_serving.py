"""AI Engine serving tests for Classical ML RCA."""

import os
import json
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.rca.classifier import load_model_and_metadata, predict_root_cause


@pytest.fixture
def client():
    return TestClient(app)


def test_model_loading():
    model, metadata = load_model_and_metadata()
    assert model is not None
    assert metadata["model_version"] == "classical_rca_rf_v1"
    assert len(metadata["features"]) == 214


def test_rca_endpoint_ml(client):
    topology = {"edges": [{"source": "inventory-service", "target": "inventory-db"}]}
    telemetry = [
        {"service": "inventory-db", "latency": 1500, "anomaly": 1.0, "timestamp": "2026-01-01T00:00:01Z"},
        {"service": "inventory-service", "latency": 500, "anomaly": 0.5, "timestamp": "2026-01-01T00:00:02Z"}
    ]
    resp = client.post("/rca/ml", json={"topology": topology, "telemetry": telemetry})
    assert resp.status_code == 200
    data = resp.json()
    assert data["rca_method"] == "classical_ml"
    assert data["model"] == "classical_rca_rf_v1"
    assert data["root_cause"] in ["inventory-db", "inventory-service", "order-service", "payment-service"]
    assert len(data["candidates"]) == 4


def test_analyze_root_cause_default_ml(client):
    topology = {"edges": [{"source": "inventory-service", "target": "inventory-db"}]}
    telemetry = [
        {"service": "inventory-db", "latency": 1500, "anomaly": 1.0, "timestamp": "2026-01-01T00:00:01Z"}
    ]
    resp = client.post("/analyze/root-cause", json={"topology": topology, "telemetry": telemetry})
    assert resp.status_code == 200
    data = resp.json()
    assert data["rca_method"] == "classical_ml"
    assert data["model"] == "classical_rca_rf_v1"


def test_analyze_root_cause_heuristic_mode(client):
    topology = {"edges": [{"source": "inventory-service", "target": "inventory-db"}]}
    telemetry = [
        {"service": "inventory-db", "latency": 1500, "anomaly": 1.0, "timestamp": "2026-01-01T00:00:01Z"}
    ]
    resp = client.post("/analyze/root-cause", json={"topology": topology, "telemetry": telemetry, "mode": "heuristic"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["rca_method"] == "heuristic"
    assert "model" not in data


def test_causal_counterfactual_endpoint(client):
    resp = client.post("/causal/counterfactual", json={
        "experiment_id": "EXP-015",
        "root_cause": {"node": "inventory-db", "variable": "db_latency"},
        "start_step": 5,
        "horizon": 20
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["experiment_id"] == "EXP-015"
    assert data["root_cause"]["node"] == "inventory-db"
    assert data["root_cause"]["variable"] == "db_latency"
    assert "avoided_impact" in data
    assert "validity_metadata" in data
    assert "counterfactual_trajectory" in data
    assert data["avoided_impact"]["gateway_latency"]["peak_avoided_latency_ms"] > 0.0


def test_causal_recommendation_endpoint_fault(client):
    resp = client.post("/causal/recommendation", json={
        "experiment_id": "EXP-015",
        "root_cause": "inventory-db",
        "start_step": 5,
        "horizon": 20
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["experiment_id"] == "EXP-015"
    assert data["recommendation_status"] == "RECOMMENDATION_READY"
    assert data["approval_required"] is True
    assert data["execution_state"] == "SIMULATED"
    assert data["recommended_action"] is not None
    assert data["recommended_action"]["action_id"] == "ACT-DB-01"
    assert data["recommended_action"]["approval_required"] is True
    assert "blast_radius" in data["recommended_action"]
    assert "collateral_impact" in data["recommended_action"]
    assert data["recommended_action"]["collateral_impact"]["collateral_damage_detected"] is False
    assert len(data["candidate_actions"]) >= 2
    assert "HUMAN APPROVAL REQUIRED" in data["explanation"]


def test_causal_recommendation_endpoint_control(client):
    resp = client.post("/causal/recommendation", json={
        "experiment_id": "EXP-007",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["experiment_id"] == "EXP-007"
    assert data["recommendation_status"] == "NO_REMEDIATION_REQUIRED"
    assert data["recommended_action"] is None
    assert data["approval_required"] is False
    assert "nominal" in data["explanation"].lower() or "no_fault" in data["explanation"].lower()


def test_remediation_approval_and_execution_lifecycle(client):
    # 1. Generate recommendation
    rec_resp = client.post("/causal/recommendation", json={
        "experiment_id": "EXP-015",
        "root_cause": "inventory-db",
        "start_step": 5,
        "horizon": 20
    })
    assert rec_resp.status_code == 200
    rec_data = rec_resp.json()
    rec_id = rec_data.get("recommendation_id")
    assert rec_id is not None

    # 2. Approve recommendation
    appr_resp = client.post("/remediation/approve", json={
        "recommendation_id": rec_id,
        "approved_by": "lead-sre@causalops.local",
        "warning_acknowledged": True,
    })
    assert appr_resp.status_code == 200
    appr_data = appr_resp.json()
    assert appr_data["approval_status"] == "APPROVED"
    approval_id = appr_data["approval_id"]

    # 3. Execute approved remediation
    exec_resp = client.post("/remediation/execute", json={
        "recommendation_id": rec_id,
        "approval_id": approval_id,
        "environment": "LOCAL",
    })
    assert exec_resp.status_code == 200
    exec_data = exec_resp.json()
    assert exec_data["state"] == "INCIDENT_RESOLVED"
    assert exec_data["execution_result"]["execution_success"] is True
    assert exec_data["verification_result"]["verified"] is True
    execution_id = exec_data["execution_id"]

    # 4. Inspect execution detail
    detail_resp = client.get(f"/remediation/executions/{execution_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["execution_id"] == execution_id
    assert len(detail["timeline"]) >= 4

    # 5. List executions
    list_resp = client.get("/remediation/executions")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) >= 1


def test_phase_6_incident_orchestration_endpoints(client):
    # 1. List incidents
    resp = client.get("/incidents")
    assert resp.status_code == 200
    incidents = resp.json()
    assert isinstance(incidents, list)
    assert len(incidents) >= 1
    inc_id = incidents[0]["incident_id"]

    # 2. Get incident details
    inc_resp = client.get(f"/incidents/{inc_id}")
    assert inc_resp.status_code == 200
    inc_data = inc_resp.json()
    assert inc_data["incident_id"] == inc_id
    assert "correlation_id" in inc_data
    assert "current_state" in inc_data

    # 3. Get timeline
    timeline_resp = client.get(f"/incidents/{inc_id}/timeline")
    assert timeline_resp.status_code == 200
    timeline = timeline_resp.json()
    assert isinstance(timeline, list)
    assert len(timeline) >= 1

    # 4. Get health
    health_resp = client.get(f"/incidents/{inc_id}/health")
    assert health_resp.status_code == 200
    health_data = health_resp.json()
    assert "services_health" in health_data

    # 5. Get conflicts
    conf_resp = client.get(f"/incidents/{inc_id}/conflicts")
    assert conf_resp.status_code == 200
    conf_data = conf_resp.json()
    assert "has_conflict" in conf_data

    # 6. Acknowledge incident
    ack_resp = client.post(f"/incidents/{inc_id}/acknowledge", json={
        "operator_id": "test-sre@causalops.local"
    })
    assert ack_resp.status_code == 200
    ack_data = ack_resp.json()
    assert ack_data["acknowledged_by"] == "test-sre@causalops.local"

    # 7. System health
    sys_resp = client.get("/system/health")
    assert sys_resp.status_code == 200
    assert "overall_status" in sys_resp.json()

    # 8. Observability metrics
    metrics_resp = client.get("/observability/metrics")
    assert metrics_resp.status_code == 200
    metrics = metrics_resp.json()
    assert "active_incidents" in metrics
    assert "incidents_created_total" in metrics


def test_structured_error_responses(client):
    # Non-existent incident must return structured 404 error
    resp = client.get("/incidents/INC-NON-EXISTENT-999")
    assert resp.status_code == 404
    data = resp.json()
    assert data["error_code"] == "INCIDENT_NOT_FOUND"
    assert "retryable" in data
    assert data["retryable"] is False




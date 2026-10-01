"""
Test Suite for Post-Execution Telemetry Verification Engine (Phase 5).

Validates:
1. Multi-criteria verification (target improvement + gateway recovery + service health)
2. Target metric reduction verification
3. Gateway severity reduction verification
4. Zero downstream regression check
5. Verification failure detection when telemetry remains degraded
6. Pre-execution snapshot capture integrity
"""

import sys
from pathlib import Path
import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dataset.tg_v1.loader import TemporalGraphDataset
from ml.execution.verification import (
    VerificationEngine,
    VerificationResult,
    capture_pre_execution_snapshot,
)


@pytest.fixture(scope="module")
def test_dataset():
    return TemporalGraphDataset(dataset_dir="dataset/tg_v1", split="test")


def test_pre_execution_snapshot_capture(test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-015"][0]
    snap = capture_pre_execution_snapshot(
        sample_or_telemetry=sample,
        incident_id="EXP-015",
        action_id="ACT-DB-01",
        target_service="inventory-db",
        step=5,
    )
    assert snap.incident_id == "EXP-015"
    assert snap.action_id == "ACT-DB-01"
    assert snap.target_service == "inventory-db"
    assert "inventory-db" in snap.service_health
    assert "inventory-db.db_latency" in snap.causal_variables
    assert snap.gateway_p99_latency_ms >= 0.0


def test_verification_success(test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-015"][0]
    snap = capture_pre_execution_snapshot(
        sample_or_telemetry=sample,
        incident_id="EXP-015",
        action_id="ACT-DB-01",
        target_service="inventory-db",
        step=5,
    )

    engine = VerificationEngine(min_latency_improvement_pct=20.0)

    # Simulated recovered telemetry
    post_telemetry = {
        "gateway_p99_latency_ms": 32.5,
        "gateway_error_rate_pct": 0.0,
        "target_variable_value": 15.0,
    }

    result = engine.verify_recovery(
        pre_snapshot=snap,
        post_telemetry=post_telemetry,
        target_service="inventory-db",
        target_variable="db_latency",
    )

    assert result.verified is True
    assert result.target_improved is True
    assert result.severity_decreased is True
    assert result.health_restored is True
    assert result.no_downstream_regression is True
    assert "Recovery Verified" in result.summary


def test_verification_failure_unimproved_target(test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-015"][0]
    snap = capture_pre_execution_snapshot(
        sample_or_telemetry=sample,
        incident_id="EXP-015",
        action_id="ACT-DB-01",
        target_service="inventory-db",
        step=5,
    )

    engine = VerificationEngine(min_latency_improvement_pct=20.0)

    # Post telemetry still exhibits heavy database latency and high gateway latency
    post_telemetry = {
        "gateway_p99_latency_ms": snap.gateway_p99_latency_ms + 50.0,
        "gateway_error_rate_pct": snap.gateway_error_rate_pct + 5.0,
        "target_variable_value": snap.causal_variables["inventory-db.db_latency"] + 100.0,
    }

    result = engine.verify_recovery(
        pre_snapshot=snap,
        post_telemetry=post_telemetry,
        target_service="inventory-db",
        target_variable="db_latency",
    )

    assert result.verified is False
    assert result.target_improved is False
    assert result.severity_decreased is False
    assert "Recovery Verification Failed" in result.summary


def test_verification_forced_failure(test_dataset):
    sample = [s for s in test_dataset if s.experiment_id == "EXP-015"][0]
    snap = capture_pre_execution_snapshot(
        sample_or_telemetry=sample,
        incident_id="EXP-015",
        action_id="ACT-DB-01",
        target_service="inventory-db",
        step=5,
    )
    engine = VerificationEngine()
    result = engine.verify_recovery(
        pre_snapshot=snap,
        post_telemetry={},
        target_service="inventory-db",
        target_variable="db_latency",
        force_failure=True,
    )
    assert result.verified is False

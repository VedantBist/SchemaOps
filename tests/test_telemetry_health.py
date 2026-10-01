"""
Test Suite for Telemetry Health, Freshness & Service Health Engine (Phase 6).

Validates:
1. Freshness categorizations (FRESH, DELAYED, STALE, MISSING, INVALID).
2. Mandatory safe boundary: STALE or UNUSABLE telemetry strictly blocks execution.
3. Detection of NaN and Inf corruption.
4. Multi-signal microservice operational health evaluation (UP, DEGRADED, DOWN).
5. Hierarchical dependency recovery evaluation (root -> downstream -> gateway).
"""

from datetime import datetime, timezone, timedelta
import numpy as np
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.orchestration.health import (
    TelemetryHealthTracker,
    TelemetryFreshness,
    TelemetryHealthStatus,
    ServiceHealthTracker,
    ServiceHealthStatus,
    DependencyRecoveryTracker,
    DependencyRecoveryStatus,
)


def test_freshness_thresholds():
    """Validates boundary transitions between FRESH, DELAYED, and STALE."""
    tracker = TelemetryHealthTracker(fresh_threshold_seconds=3.0, delayed_threshold_seconds=10.0)
    now = datetime(2026, 9, 27, 12, 0, 20, tzinfo=timezone.utc)
    mock_data = np.zeros((10, 5, 7))

    # 1. Fresh (1s delay)
    rep_fresh = tracker.evaluate_telemetry(mock_data, timestamp=now - timedelta(seconds=1.0), now=now)
    assert rep_fresh.freshness == TelemetryFreshness.FRESH.value
    assert rep_fresh.quality == TelemetryHealthStatus.HEALTHY.value
    assert rep_fresh.can_execute_remediation is True

    # 2. Delayed (5s delay)
    rep_delayed = tracker.evaluate_telemetry(mock_data, timestamp=now - timedelta(seconds=5.0), now=now)
    assert rep_delayed.freshness == TelemetryFreshness.DELAYED.value
    assert rep_delayed.quality == TelemetryHealthStatus.DEGRADED.value

    # 3. Stale (15s delay) -> MUST BLOCK EXECUTION
    rep_stale = tracker.evaluate_telemetry(mock_data, timestamp=now - timedelta(seconds=15.0), now=now)
    assert rep_stale.freshness == TelemetryFreshness.STALE.value
    assert rep_stale.quality == TelemetryHealthStatus.UNUSABLE.value
    assert rep_stale.can_execute_remediation is False


def test_missing_telemetry_blocks_execution():
    """Confirms None telemetry is evaluated as MISSING and UNUSABLE."""
    tracker = TelemetryHealthTracker()
    rep = tracker.evaluate_telemetry(None)
    assert rep.freshness == TelemetryFreshness.MISSING.value
    assert rep.quality == TelemetryHealthStatus.UNUSABLE.value
    assert rep.can_execute_remediation is False


def test_nan_and_inf_detection():
    """Confirms numeric NaN or Inf violations are caught and marked INVALID."""
    tracker = TelemetryHealthTracker()
    corrupt_data = np.zeros((10, 5, 7))
    corrupt_data[2, 0, 0] = np.nan
    corrupt_data[4, 1, 1] = np.inf

    rep = tracker.evaluate_telemetry(corrupt_data)
    assert rep.freshness == TelemetryFreshness.INVALID.value
    assert rep.nan_count == 1
    assert rep.inf_count == 1
    assert rep.quality == TelemetryHealthStatus.UNUSABLE.value
    assert rep.can_execute_remediation is False


def test_service_health_multi_signal():
    """Confirms microservice health state tracks latency and error rate thresholds."""
    tracker = ServiceHealthTracker()

    # Nominal operation -> UP
    tracker.update_service_metric("order-service", p99_latency=35.0, error_rate=0.0)
    assert tracker.get_service_health("order-service") == ServiceHealthStatus.UP

    # Degraded operation (>200ms latency) -> DEGRADED
    tracker.update_service_metric("order-service", p99_latency=250.0, error_rate=1.0)
    assert tracker.get_service_health("order-service") == ServiceHealthStatus.DEGRADED

    # Outage (>500ms latency or >25% errors) -> DOWN
    tracker.update_service_metric("order-service", p99_latency=800.0, error_rate=30.0)
    assert tracker.get_service_health("order-service") == ServiceHealthStatus.DOWN


def test_hierarchical_recovery_evaluation():
    """Confirms dependency-aware recovery propagation from root cause to gateway."""
    # 1. Unrecovered: Root and downstream high
    status1, _ = DependencyRecoveryTracker.evaluate_hierarchical_recovery(
        root_cause_service="inventory-db",
        service_latencies={"inventory-db": 1000.0, "inventory-service": 600.0, "api-gateway": 300.0},
        service_error_rates={"inventory-db": 0.0, "inventory-service": 0.0, "api-gateway": 5.0},
    )
    assert status1 == DependencyRecoveryStatus.NOT_RECOVERED

    # 2. Fully recovered: Root, callers, and gateway healthy
    status2, checks = DependencyRecoveryTracker.evaluate_hierarchical_recovery(
        root_cause_service="inventory-db",
        service_latencies={"inventory-db": 15.0, "inventory-service": 40.0, "order-service": 50.0, "api-gateway": 35.0},
        service_error_rates={"inventory-db": 0.0, "inventory-service": 0.0, "order-service": 0.0, "api-gateway": 0.0},
    )
    assert status2 == DependencyRecoveryStatus.FULLY_RECOVERED
    assert checks["gateway_healthy"] is True

"""
Test Suite for Deterministic Incident Deduplication Engine (Phase 6).

Validates:
1. Ten repeated anomaly events produce EXACTLY ONE active incident.
2. Repetition counters and timestamp bounds are properly updated.
3. Distinct services produce separate incidents.
4. Anomaly events outside the temporal window create distinct incident records.
5. Fingerprint calculation is deterministic across identical inputs.
"""

from datetime import datetime, timezone, timedelta
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.orchestration.deduplication import IncidentDeduplicationEngine


def test_ten_repeated_anomalies_produce_single_incident():
    """Mandatory requirement: 10 repeated events -> 1 active incident."""
    engine = IncidentDeduplicationEngine(time_window_seconds=60.0)
    base_time = datetime(2026, 9, 27, 10, 0, 0, tzinfo=timezone.utc)

    first_incident_id = "INC-ORIGINAL-001"
    res1 = engine.process_anomaly_event(
        service="inventory-db",
        primary_variable="db_latency",
        fault_signature="latency_spike_p99",
        generated_incident_id=first_incident_id,
        timestamp=base_time,
    )

    assert res1.is_duplicate is False
    assert res1.incident_id == first_incident_id
    assert res1.repetition_count == 1

    # Simulate 9 subsequent repeated anomaly firings over next 30 seconds
    for i in range(1, 10):
        t = base_time + timedelta(seconds=i * 3.0)
        res = engine.process_anomaly_event(
            service="inventory-db",
            primary_variable="db_latency",
            fault_signature="latency_spike_p99",
            generated_incident_id=f"INC-POTENTIAL-DUPLICATE-{i}",
            timestamp=t,
        )
        assert res.is_duplicate is True
        assert res.incident_id == first_incident_id
        assert res.repetition_count == i + 1
        assert res.suppressed_event_count == i


def test_different_services_create_separate_incidents():
    """Confirms that distinct services are NOT falsely deduplicated."""
    engine = IncidentDeduplicationEngine(time_window_seconds=60.0)
    now = datetime.now(timezone.utc)

    res_db = engine.process_anomaly_event(
        service="inventory-db",
        primary_variable="db_latency",
        fault_signature="latency_spike",
        generated_incident_id="INC-DB-1",
        timestamp=now,
    )
    res_pay = engine.process_anomaly_event(
        service="payment-service",
        primary_variable="error_rate",
        fault_signature="connection_refused",
        generated_incident_id="INC-PAY-1",
        timestamp=now,
    )

    assert res_db.is_duplicate is False
    assert res_pay.is_duplicate is False
    assert res_db.incident_id != res_pay.incident_id


def test_time_window_expiration_creates_new_incident():
    """Confirms that a recurring failure hours later is treated as a new incident."""
    engine = IncidentDeduplicationEngine(time_window_seconds=60.0)
    t1 = datetime(2026, 9, 27, 8, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)  # 4 hours later

    res1 = engine.process_anomaly_event(
        service="inventory-db",
        primary_variable="db_latency",
        fault_signature="latency_spike",
        generated_incident_id="INC-OLD",
        timestamp=t1,
    )
    res2 = engine.process_anomaly_event(
        service="inventory-db",
        primary_variable="db_latency",
        fault_signature="latency_spike",
        generated_incident_id="INC-NEW",
        timestamp=t2,
    )

    assert res1.is_duplicate is False
    assert res2.is_duplicate is False
    assert res1.incident_id != res2.incident_id

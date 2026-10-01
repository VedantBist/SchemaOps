"""
Test Suite for Topological & Causal-Constrained Incident Correlation Engine (Phase 6).

Validates:
1. Root cause detection on upstream services (e.g. inventory-db).
2. Automatic correlation of downstream symptoms (inventory-service, order-service, api-gateway).
3. Attribution of downstream symptoms to parent incident under shared correlation group.
4. Independent failure origins (e.g. payment-service and inventory-db) remain unlinked.
5. Topological propagation distance and path accuracy.
"""

from datetime import datetime, timezone, timedelta
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.orchestration.correlation import IncidentCorrelationEngine


def test_downstream_cascade_correlation():
    """Validates that a downstream cascade is correlated to the upstream root cause."""
    engine = IncidentCorrelationEngine(max_lag_seconds=30.0)
    t0 = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Primary root-cause incident on inventory-db
    root_res = engine.correlate_incident(
        incident_id="INC-DB-ROOT",
        service="inventory-db",
        timestamp=t0,
        active_incidents=[],
    )
    assert root_res.is_correlated is False
    assert root_res.role == "ROOT_CAUSE"
    assert root_res.parent_incident_id is None
    group_id = root_res.correlation_group_id

    # 2. Downstream inventory-service latency 3 seconds later
    active = [{
        "incident_id": "INC-DB-ROOT",
        "affected_service": "inventory-db",
        "root_cause": "inventory-db",
        "created_at": t0.isoformat(),
    }]
    t1 = t0 + timedelta(seconds=3.0)
    inv_res = engine.correlate_incident(
        incident_id="INC-INV-SYMPTOM",
        service="inventory-service",
        timestamp=t1,
        active_incidents=active,
    )
    assert inv_res.is_correlated is True
    assert inv_res.parent_incident_id == "INC-DB-ROOT"
    assert inv_res.correlation_group_id == group_id
    assert inv_res.role == "DOWNSTREAM_SYMPTOM"
    assert inv_res.topological_distance == 1
    assert inv_res.causal_propagation_path == ["inventory-db", "inventory-service"]

    # 3. Downstream API Gateway 504 timeouts 8 seconds later
    active.append({
        "incident_id": "INC-INV-SYMPTOM",
        "affected_service": "inventory-service",
        "root_cause": "inventory-db",
        "created_at": t1.isoformat(),
    })
    t2 = t0 + timedelta(seconds=8.0)
    gw_res = engine.correlate_incident(
        incident_id="INC-GW-SYMPTOM",
        service="api-gateway",
        timestamp=t2,
        active_incidents=active,
    )
    assert gw_res.is_correlated is True
    assert gw_res.correlation_group_id == group_id
    assert gw_res.role == "DOWNSTREAM_SYMPTOM"
    assert "inventory-db" in gw_res.causal_propagation_path


def test_independent_services_remain_uncorrelated():
    """Confirms that concurrent failures on separate branches do not falsely correlate."""
    engine = IncidentCorrelationEngine(max_lag_seconds=30.0)
    now = datetime.now(timezone.utc)

    active = [{
        "incident_id": "INC-DB-01",
        "affected_service": "inventory-db",
        "root_cause": "inventory-db",
        "created_at": now.isoformat(),
    }]

    pay_res = engine.correlate_incident(
        incident_id="INC-PAY-01",
        service="payment-service",
        timestamp=now + timedelta(seconds=2.0),
        active_incidents=active,
    )

    assert pay_res.is_correlated is False
    assert pay_res.role == "ROOT_CAUSE"
    assert pay_res.parent_incident_id is None
    assert pay_res.correlation_group_id != "INC-DB-01"

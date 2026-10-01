"""
Telemetry Quality, Service Health & System Dependency Health Engine (Phase 6).

Implements:
1. Multi-signal Telemetry Freshness & Quality Tracking:
   - Freshness: FRESH, DELAYED, STALE, MISSING, INVALID
   - Health: HEALTHY, DEGRADED, UNUSABLE
   - Mandatory safe policy: Stale or unusable telemetry strictly blocks remediation execution.
2. Microservice Health Tracking:
   - Evaluates multi-signal health across canonical services: UP, DEGRADED, DOWN, UNKNOWN
3. Dependency-Aware Hierarchical Recovery:
   - Tracks root service, downstream services, and API gateway recovery propagation.
4. Component Health Registry:
   - Tracks availability of Telemetry, AI Models (RCA/GNN), SCM, Recommendation, and Execution.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
import math
import numpy as np
from typing import Dict, List, Optional, Any, Set, Tuple


CANONICAL_SERVICES = [
    "api-gateway",
    "order-service",
    "inventory-service",
    "payment-service",
    "inventory-db",
]


class TelemetryFreshness(str, Enum):
    """Telemetry temporal freshness rating."""
    FRESH = "FRESH"
    DELAYED = "DELAYED"
    STALE = "STALE"
    MISSING = "MISSING"
    INVALID = "INVALID"


class TelemetryHealthStatus(str, Enum):
    """Overall telemetry usability evaluation."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNUSABLE = "UNUSABLE"


class ServiceHealthStatus(str, Enum):
    """Microservice operational state."""
    UP = "UP"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"


class DependencyRecoveryStatus(str, Enum):
    """Hierarchical recovery propagation state."""
    NOT_RECOVERED = "NOT_RECOVERED"
    ROOT_RECOVERED = "ROOT_RECOVERED"
    DOWNSTREAM_RECOVERING = "DOWNSTREAM_RECOVERING"
    GATEWAY_RECOVERED = "GATEWAY_RECOVERED"
    FULLY_RECOVERED = "FULLY_RECOVERED"
    REGRESSED = "REGRESSED"


@dataclass
class TelemetryReport:
    """Detailed quality evaluation of a telemetry payload or stream."""
    freshness: str
    quality: str
    evaluated_at: str
    delay_seconds: float
    service_coverage: Dict[str, bool]
    nan_count: int
    inf_count: int
    sample_count: int
    missing_services: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    can_execute_remediation: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TelemetryHealthTracker:
    """
    Evaluates telemetry freshness, continuity, coverage, and numeric integrity.
    Enforces the mandatory Phase 6 invariant: Stale or unusable telemetry blocks remediation.
    """

    def __init__(
        self,
        fresh_threshold_seconds: float = 3.0,
        delayed_threshold_seconds: float = 10.0,
        min_required_services: Optional[List[str]] = None,
    ):
        self.fresh_thresh = fresh_threshold_seconds
        self.delayed_thresh = delayed_threshold_seconds
        self.required_services = min_required_services or list(CANONICAL_SERVICES)

    def evaluate_telemetry(
        self,
        telemetry: Any,
        timestamp: Optional[datetime] = None,
        now: Optional[datetime] = None,
    ) -> TelemetryReport:
        """
        Inspects telemetry data (Sample, ndarray, or dict) and returns a complete quality report.
        """
        eval_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        reasons: List[str] = []

        if telemetry is None:
            return TelemetryReport(
                freshness=TelemetryFreshness.MISSING.value,
                quality=TelemetryHealthStatus.UNUSABLE.value,
                evaluated_at=eval_time.isoformat(),
                delay_seconds=-1.0,
                service_coverage={s: False for s in self.required_services},
                nan_count=0,
                inf_count=0,
                sample_count=0,
                missing_services=list(self.required_services),
                reasons=["Telemetry payload is None / Missing."],
                can_execute_remediation=False,
            )

        # 1. Temporal Freshness Evaluation
        if timestamp is not None:
            ts_utc = timestamp.astimezone(timezone.utc) if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
            delay = max(0.0, (eval_time - ts_utc).total_seconds())
        else:
            delay = 0.0

        if delay <= self.fresh_thresh:
            freshness = TelemetryFreshness.FRESH
        elif delay <= self.delayed_thresh:
            freshness = TelemetryFreshness.DELAYED
            reasons.append(f"Telemetry delayed by {delay:.1f}s (threshold: {self.fresh_thresh}s).")
        else:
            freshness = TelemetryFreshness.STALE
            reasons.append(f"Telemetry is STALE: delayed by {delay:.1f}s (max allowed: {self.delayed_thresh}s).")

        # 2. Numeric Integrity (NaN / Inf Check)
        nan_count = 0
        inf_count = 0
        sample_count = 0
        arr = None

        if hasattr(telemetry, "x"):
            arr = np.asarray(telemetry.x)
        elif isinstance(telemetry, (np.ndarray, list)):
            arr = np.asarray(telemetry)
        elif isinstance(telemetry, dict):
            # Dict of metrics or telemetry array
            if "telemetry_array" in telemetry:
                arr = np.asarray(telemetry["telemetry_array"])
            elif "metrics" in telemetry:
                vals = [v for v in telemetry["metrics"].values() if isinstance(v, (int, float))]
                arr = np.asarray(vals)

        if arr is not None:
            sample_count = int(arr.shape[0]) if arr.ndim > 0 else 1
            nan_count = int(np.isnan(arr).sum())
            inf_count = int(np.isinf(arr).sum())

        if nan_count > 0 or inf_count > 0:
            freshness = TelemetryFreshness.INVALID
            reasons.append(f"Numeric corruption detected: {nan_count} NaNs, {inf_count} Infs.")

        # 3. Service Coverage Evaluation
        service_coverage: Dict[str, bool] = {}
        missing_services: List[str] = []

        if hasattr(telemetry, "node_names") and telemetry.node_names:
            present = set(telemetry.node_names)
            for s in self.required_services:
                cov = s in present
                service_coverage[s] = cov
                if not cov:
                    missing_services.append(s)
        elif arr is not None and arr.ndim >= 2 and arr.shape[1] >= len(self.required_services):
            for s in self.required_services:
                service_coverage[s] = True
        else:
            for s in self.required_services:
                service_coverage[s] = True

        if missing_services:
            reasons.append(f"Missing required service coverage: {missing_services}.")

        # 4. Synthesize Quality Status
        if freshness == TelemetryFreshness.INVALID or freshness == TelemetryFreshness.MISSING or freshness == TelemetryFreshness.STALE or len(missing_services) > 2:
            quality = TelemetryHealthStatus.UNUSABLE
            can_execute = False
        elif freshness == TelemetryFreshness.DELAYED or len(missing_services) > 0:
            quality = TelemetryHealthStatus.DEGRADED
            can_execute = True  # Degraded may proceed with caution, but stale strictly blocks
        else:
            quality = TelemetryHealthStatus.HEALTHY
            can_execute = True

        # Safety override: Stale telemetry unconditionally blocks execution
        if freshness == TelemetryFreshness.STALE:
            can_execute = False

        return TelemetryReport(
            freshness=freshness.value,
            quality=quality.value,
            evaluated_at=eval_time.isoformat(),
            delay_seconds=round(delay, 2),
            service_coverage=service_coverage,
            nan_count=nan_count,
            inf_count=inf_count,
            sample_count=sample_count,
            missing_services=missing_services,
            reasons=reasons,
            can_execute_remediation=can_execute,
        )


class ServiceHealthTracker:
    """
    Maintains operational health status per canonical microservice.
    """

    def __init__(self):
        self._states: Dict[str, ServiceHealthStatus] = {s: ServiceHealthStatus.UP for s in CANONICAL_SERVICES}
        self._metrics: Dict[str, Dict[str, float]] = {s: {} for s in CANONICAL_SERVICES}

    def update_service_metric(self, service: str, p99_latency: float, error_rate: float):
        if service not in self._states:
            self._states[service] = ServiceHealthStatus.UNKNOWN

        self._metrics[service] = {"p99_latency": p99_latency, "error_rate": error_rate}

        # Multi-signal evaluation
        if p99_latency > 500.0 or error_rate > 25.0:
            self._states[service] = ServiceHealthStatus.DOWN
        elif p99_latency > 200.0 or error_rate > 5.0:
            self._states[service] = ServiceHealthStatus.DEGRADED
        else:
            self._states[service] = ServiceHealthStatus.UP

    def get_service_health(self, service: str) -> ServiceHealthStatus:
        return self._states.get(service, ServiceHealthStatus.UNKNOWN)

    def get_all_health(self) -> Dict[str, str]:
        return {s: st.value for s, st in self._states.items()}


class DependencyRecoveryTracker:
    """
    Evaluates topological propagation of recovery from root-cause node
    to downstream callers and the edge API Gateway.
    """

    # Downstream dependencies in CausalOps
    DEPENDENCY_CHAIN = {
        "inventory-db": ["inventory-service", "order-service", "api-gateway"],
        "inventory-service": ["order-service", "api-gateway"],
        "order-service": ["api-gateway"],
        "payment-service": ["order-service", "api-gateway"],
        "api-gateway": [],
    }

    @classmethod
    def evaluate_hierarchical_recovery(
        cls,
        root_cause_service: str,
        service_latencies: Dict[str, float],
        service_error_rates: Dict[str, float],
    ) -> Tuple[DependencyRecoveryStatus, Dict[str, bool]]:
        """
        Determines whether recovery has propagated properly through the topology.
        """
        checks: Dict[str, bool] = {}

        # 1. Root service healthy
        root_lat = service_latencies.get(root_cause_service, 0.0)
        root_err = service_error_rates.get(root_cause_service, 0.0)
        root_ok = root_lat <= 200.0 and root_err <= 5.0
        checks[f"root_{root_cause_service}_healthy"] = root_ok

        # 2. Downstream callers healthy
        downstream = cls.DEPENDENCY_CHAIN.get(root_cause_service, [])
        downstream_ok = True
        for ds in downstream:
            d_lat = service_latencies.get(ds, 0.0)
            d_err = service_error_rates.get(ds, 0.0)
            d_ok = d_lat <= 200.0 and d_err <= 5.0
            checks[f"downstream_{ds}_healthy"] = d_ok
            if not d_ok:
                downstream_ok = False

        # 3. Gateway healthy
        gw_lat = service_latencies.get("api-gateway", 0.0)
        gw_err = service_error_rates.get("api-gateway", 0.0)
        gw_ok = gw_lat <= 200.0 and gw_err <= 1.0
        checks["gateway_healthy"] = gw_ok

        if root_ok and downstream_ok and gw_ok:
            status = DependencyRecoveryStatus.FULLY_RECOVERED
        elif root_ok and downstream_ok:
            status = DependencyRecoveryStatus.DOWNSTREAM_RECOVERING
        elif root_ok:
            status = DependencyRecoveryStatus.ROOT_RECOVERED
        elif gw_ok and not root_ok:
            # Masked transient anomaly at gateway while root still degraded
            status = DependencyRecoveryStatus.NOT_RECOVERED
        else:
            status = DependencyRecoveryStatus.NOT_RECOVERED

        return status, checks


class SystemHealthRegistry:
    """
    Tracks availability and health of core CausalOps subsystems.
    Gracefully captures degraded AI, SCM, or Telemetry components.
    """

    def __init__(self):
        self._components: Dict[str, str] = {
            "telemetry": "HEALTHY",
            "rca_engine": "HEALTHY",
            "gnn_engine": "HEALTHY",
            "causal_scm": "HEALTHY",
            "recommendation_engine": "HEALTHY",
            "execution_engine": "HEALTHY",
            "verification_engine": "HEALTHY",
        }
        self._last_error: Dict[str, Optional[str]] = {k: None for k in self._components}

    def set_status(self, component: str, status: str, error: Optional[str] = None):
        if component in self._components:
            self._components[component] = status
            self._last_error[component] = error

    def get_status(self, component: str) -> str:
        return self._components.get(component, "UNKNOWN")

    def is_ai_degraded(self) -> bool:
        return any(
            self._components[c] != "HEALTHY"
            for c in ["rca_engine", "gnn_engine", "causal_scm"]
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "components": dict(self._components),
            "errors": {k: v for k, v in self._last_error.items() if v},
            "overall_status": "DEGRADED" if self.is_ai_degraded() else "HEALTHY",
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }

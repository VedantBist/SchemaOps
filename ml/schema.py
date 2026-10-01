"""Schema definitions and metadata for CausalOps ML feature extraction and modeling."""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional

SERVICES = [
    'api-gateway',
    'order-service',
    'inventory-service',
    'payment-service',
    'inventory-db'
]

ROOT_CAUSE_TARGETS = [
    'inventory-db',
    'inventory-service',
    'order-service',
    'payment-service'
]

FAULT_TYPES = [
    'DB_LATENCY',
    'SERVICE_LATENCY',
    'NETWORK_LATENCY',
    'ERROR_RATE',
    'SERVICE_FAILURE',
    'NO_FAULT'
]

# Feature Groups for Ablation
GROUP_A_TELEMETRY = 'telemetry_only'
GROUP_B_TEMPORAL = 'telemetry_plus_temporal'
GROUP_C_GRAPH = 'telemetry_temporal_graph'

@dataclass
class FeatureDefinition:
    name: str
    service: str
    group: str
    dtype: str
    description: str

def get_feature_definitions() -> Dict[str, FeatureDefinition]:
    """Returns the full catalog of engineered features across all services and groups."""
    defs: Dict[str, FeatureDefinition] = {}

    for svc in SERVICES:
        # ---------------------------------------------------------
        # Group A: Statistical Telemetry Features (Per Service)
        # ---------------------------------------------------------
        defs[f"{svc}__p99_mean"] = FeatureDefinition(
            name=f"{svc}__p99_mean",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Mean P99 latency (ms) for {svc} across window"
        )
        defs[f"{svc}__p99_median"] = FeatureDefinition(
            name=f"{svc}__p99_median",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Median P99 latency (ms) for {svc}"
        )
        defs[f"{svc}__p99_p95"] = FeatureDefinition(
            name=f"{svc}__p99_p95",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"95th percentile of P99 latency (ms) for {svc}"
        )
        defs[f"{svc}__p99_max"] = FeatureDefinition(
            name=f"{svc}__p99_max",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Maximum P99 latency (ms) for {svc}"
        )
        defs[f"{svc}__p99_min"] = FeatureDefinition(
            name=f"{svc}__p99_min",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Minimum P99 latency (ms) for {svc}"
        )
        defs[f"{svc}__p99_std"] = FeatureDefinition(
            name=f"{svc}__p99_std",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Standard deviation of P99 latency for {svc}"
        )
        defs[f"{svc}__p99_delta"] = FeatureDefinition(
            name=f"{svc}__p99_delta",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Latency surge delta (max - baseline) for {svc}"
        )
        defs[f"{svc}__p50_mean"] = FeatureDefinition(
            name=f"{svc}__p50_mean",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Mean P50 latency (ms) for {svc}"
        )
        defs[f"{svc}__error_rate_mean"] = FeatureDefinition(
            name=f"{svc}__error_rate_mean",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Mean HTTP/gRPC error rate (%) for {svc}"
        )
        defs[f"{svc}__error_rate_max"] = FeatureDefinition(
            name=f"{svc}__error_rate_max",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Maximum error rate (%) for {svc}"
        )
        defs[f"{svc}__error_rate_std"] = FeatureDefinition(
            name=f"{svc}__error_rate_std",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Error rate standard deviation for {svc}"
        )
        defs[f"{svc}__error_rate_delta"] = FeatureDefinition(
            name=f"{svc}__error_rate_delta",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Error rate delta (max - baseline) for {svc}"
        )
        defs[f"{svc}__request_rate_mean"] = FeatureDefinition(
            name=f"{svc}__request_rate_mean",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Mean throughput request rate (req/s) for {svc}"
        )
        defs[f"{svc}__pool_util_mean"] = FeatureDefinition(
            name=f"{svc}__pool_util_mean",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Mean resource/connection pool utilization (%) for {svc}"
        )
        defs[f"{svc}__pool_util_max"] = FeatureDefinition(
            name=f"{svc}__pool_util_max",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Peak connection/resource pool utilization (%) for {svc}"
        )
        defs[f"{svc}__pool_util_delta"] = FeatureDefinition(
            name=f"{svc}__pool_util_delta",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Pool utilization surge delta for {svc}"
        )
        defs[f"{svc}__anomaly_score_mean"] = FeatureDefinition(
            name=f"{svc}__anomaly_score_mean",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Mean raw anomaly score for {svc}"
        )
        defs[f"{svc}__anomaly_score_max"] = FeatureDefinition(
            name=f"{svc}__anomaly_score_max",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Peak raw anomaly score for {svc}"
        )
        defs[f"{svc}__anomaly_count"] = FeatureDefinition(
            name=f"{svc}__anomaly_count",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Number of telemetry intervals where {svc} was anomalous"
        )
        defs[f"{svc}__anomaly_fraction"] = FeatureDefinition(
            name=f"{svc}__anomaly_fraction",
            service=svc,
            group=GROUP_A_TELEMETRY,
            dtype="float64",
            description=f"Fraction of window intervals where {svc} was anomalous"
        )

        # ---------------------------------------------------------
        # Group B: Temporal Features (Per Service)
        # ---------------------------------------------------------
        defs[f"{svc}__latency_step_t0"] = FeatureDefinition(
            name=f"{svc}__latency_step_t0",
            service=svc,
            group=GROUP_B_TEMPORAL,
            dtype="float64",
            description=f"Initial baseline latency (mean of first 5 steps) for {svc}"
        )
        defs[f"{svc}__latency_step_t_last"] = FeatureDefinition(
            name=f"{svc}__latency_step_t_last",
            service=svc,
            group=GROUP_B_TEMPORAL,
            dtype="float64",
            description=f"Post-recovery latency (mean of last 5 steps) for {svc}"
        )
        defs[f"{svc}__latency_lag1_diff"] = FeatureDefinition(
            name=f"{svc}__latency_lag1_diff",
            service=svc,
            group=GROUP_B_TEMPORAL,
            dtype="float64",
            description=f"Mean 1-step latency transition rate (x_t - x_t-1) for {svc}"
        )
        defs[f"{svc}__latency_lag2_diff"] = FeatureDefinition(
            name=f"{svc}__latency_lag2_diff",
            service=svc,
            group=GROUP_B_TEMPORAL,
            dtype="float64",
            description=f"Mean 2-step latency transition rate (x_t - x_t-2) for {svc}"
        )
        defs[f"{svc}__latency_rolling_mean_max"] = FeatureDefinition(
            name=f"{svc}__latency_rolling_mean_max",
            service=svc,
            group=GROUP_B_TEMPORAL,
            dtype="float64",
            description=f"Peak 3-step rolling mean latency for {svc}"
        )
        defs[f"{svc}__latency_rolling_std_max"] = FeatureDefinition(
            name=f"{svc}__latency_rolling_std_max",
            service=svc,
            group=GROUP_B_TEMPORAL,
            dtype="float64",
            description=f"Peak 3-step rolling latency standard deviation for {svc}"
        )
        defs[f"{svc}__latency_slope"] = FeatureDefinition(
            name=f"{svc}__latency_slope",
            service=svc,
            group=GROUP_B_TEMPORAL,
            dtype="float64",
            description=f"Linear regression rate-of-change slope of latency over time for {svc}"
        )
        defs[f"{svc}__error_rate_slope"] = FeatureDefinition(
            name=f"{svc}__error_rate_slope",
            service=svc,
            group=GROUP_B_TEMPORAL,
            dtype="float64",
            description=f"Linear regression rate-of-change slope of error rate over time for {svc}"
        )
        defs[f"{svc}__time_to_first_anomaly"] = FeatureDefinition(
            name=f"{svc}__time_to_first_anomaly",
            service=svc,
            group=GROUP_B_TEMPORAL,
            dtype="float64",
            description=f"Elapsed seconds from telemetry window start to first anomaly detection on {svc}"
        )
        defs[f"{svc}__anomaly_duration_sec"] = FeatureDefinition(
            name=f"{svc}__anomaly_duration_sec",
            service=svc,
            group=GROUP_B_TEMPORAL,
            dtype="float64",
            description=f"Total anomalous seconds observed on {svc}"
        )
        defs[f"{svc}__relative_anomaly_rank"] = FeatureDefinition(
            name=f"{svc}__relative_anomaly_rank",
            service=svc,
            group=GROUP_B_TEMPORAL,
            dtype="float64",
            description=f"Temporal onset order rank (1=earliest) of anomaly appearance for {svc}"
        )

        # ---------------------------------------------------------
        # Group C: Graph-Aware Topology Features (Per Service)
        # ---------------------------------------------------------
        defs[f"{svc}__in_degree"] = FeatureDefinition(
            name=f"{svc}__in_degree",
            service=svc,
            group=GROUP_C_GRAPH,
            dtype="float64",
            description=f"Topology graph in-degree for {svc}"
        )
        defs[f"{svc}__out_degree"] = FeatureDefinition(
            name=f"{svc}__out_degree",
            service=svc,
            group=GROUP_C_GRAPH,
            dtype="float64",
            description=f"Topology graph out-degree for {svc}"
        )
        defs[f"{svc}__upstream_count"] = FeatureDefinition(
            name=f"{svc}__upstream_count",
            service=svc,
            group=GROUP_C_GRAPH,
            dtype="float64",
            description=f"Number of ancestor services in DAG upstream of {svc}"
        )
        defs[f"{svc}__downstream_count"] = FeatureDefinition(
            name=f"{svc}__downstream_count",
            service=svc,
            group=GROUP_C_GRAPH,
            dtype="float64",
            description=f"Number of descendant services in DAG downstream of {svc}"
        )
        defs[f"{svc}__upstream_anomaly_count"] = FeatureDefinition(
            name=f"{svc}__upstream_anomaly_count",
            service=svc,
            group=GROUP_C_GRAPH,
            dtype="float64",
            description=f"Count of direct upstream caller nodes exhibiting anomalies for {svc}"
        )
        defs[f"{svc}__downstream_anomaly_count"] = FeatureDefinition(
            name=f"{svc}__downstream_anomaly_count",
            service=svc,
            group=GROUP_C_GRAPH,
            dtype="float64",
            description=f"Count of direct downstream callee nodes exhibiting anomalies for {svc}"
        )
        defs[f"{svc}__upstream_mean_latency"] = FeatureDefinition(
            name=f"{svc}__upstream_mean_latency",
            service=svc,
            group=GROUP_C_GRAPH,
            dtype="float64",
            description=f"Mean latency across direct upstream dependencies for {svc}"
        )
        defs[f"{svc}__downstream_mean_latency"] = FeatureDefinition(
            name=f"{svc}__downstream_mean_latency",
            service=svc,
            group=GROUP_C_GRAPH,
            dtype="float64",
            description=f"Mean latency across direct downstream dependencies for {svc}"
        )
        defs[f"{svc}__dist_to_earliest_anomaly"] = FeatureDefinition(
            name=f"{svc}__dist_to_earliest_anomaly",
            service=svc,
            group=GROUP_C_GRAPH,
            dtype="float64",
            description=f"Graph hop distance between {svc} and the earliest anomalous node"
        )
        defs[f"{svc}__propagation_order_score"] = FeatureDefinition(
            name=f"{svc}__propagation_order_score",
            service=svc,
            group=GROUP_C_GRAPH,
            dtype="float64",
            description=f"Precedence alignment score (+1 if {svc} preceded downstream anomalies, -1 if lagged, 0 if isolated)"
        )
        defs[f"{svc}__downstream_latency_ratio"] = FeatureDefinition(
            name=f"{svc}__downstream_latency_ratio",
            service=svc,
            group=GROUP_C_GRAPH,
            dtype="float64",
            description=f"Ratio of {svc}'s peak latency surge to downstream mean latency surge"
        )

    # Global Cross-Service Features (Group A & C)
    defs["global__total_anomalous_services"] = FeatureDefinition(
        name="global__total_anomalous_services",
        service="global",
        group=GROUP_A_TELEMETRY,
        dtype="float64",
        description="Total number of distinct services reporting anomalyScore > 0.3 in the window"
    )
    defs["global__system_max_p99_latency"] = FeatureDefinition(
        name="global__system_max_p99_latency",
        service="global",
        group=GROUP_A_TELEMETRY,
        dtype="float64",
        description="Peak P99 latency observed across any service in the entire system"
    )
    defs["global__system_max_error_rate"] = FeatureDefinition(
        name="global__system_max_error_rate",
        service="global",
        group=GROUP_A_TELEMETRY,
        dtype="float64",
        description="Peak error rate observed across any service in the entire system"
    )
    defs["global__cascade_diameter"] = FeatureDefinition(
        name="global__cascade_diameter",
        service="global",
        group=GROUP_C_GRAPH,
        dtype="float64",
        description="Maximum topological distance between any two co-anomalous services"
    )

    return defs

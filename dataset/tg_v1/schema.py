"""Authoritative graph topology, node ordering, and feature definitions for CausalOps TG v1.

Defines:
- Canonical 5-node service ordering
- Authoritative 4 directed call edges
- 10 causal observable node features per node timestep
- Target label mappings and NO_FAULT control semantics
- Sequence windowing policy (T=40 max, padded with boolean temporal mask)
"""

from typing import Dict, List, Any, Tuple

DATASET_VERSION = "tg_v1"
SOURCE_DATASET = "CausalOps Frozen ML Dataset v1 (EXP-001 ... EXP-080)"
SOURCE_MANIFEST = "dataset/manifests/ml_dataset_v1.json"
SOURCE_SPLITS = "dataset/ml_v1/splits.json"

# 1. Canonical Node Ordering (Deterministic across all temporal samples)
NODE_ORDER = [
    "api-gateway",        # Index 0
    "order-service",      # Index 1
    "inventory-service",  # Index 2
    "payment-service",    # Index 3
    "inventory-db"        # Index 4
]
NUM_NODES = len(NODE_ORDER)

NODE_NAME_TO_INDEX = {name: idx for idx, name in enumerate(NODE_ORDER)}
INDEX_TO_NODE_NAME = {idx: name for idx, name in enumerate(NODE_ORDER)}

# 2. Authoritative Graph Topology (Microservice call hierarchy)
# Direction: caller -> dependency
EDGES = [
    ("api-gateway", "order-service"),
    ("order-service", "inventory-service"),
    ("order-service", "payment-service"),
    ("inventory-service", "inventory-db")
]
NUM_EDGES = len(EDGES)

# PyG / standard edge_index tensor representation: shape [2, E]
EDGE_INDEX = [
    [NODE_NAME_TO_INDEX[src] for src, _ in EDGES],
    [NODE_NAME_TO_INDEX[tgt] for _, tgt in EDGES]
]

EDGE_DIRECTION = "CALLS (caller -> dependency)"

# 3. Node Features (10 causal, observable telemetry metrics per node timestep)
NODE_FEATURE_NAMES = [
    "p50_latency",
    "p95_latency",
    "p99_latency",
    "error_rate",
    "request_rate",
    "pool_utilization",
    "db_latency",
    "anomaly_score",
    "p99_latency_delta",
    "error_rate_delta"
]
NUM_NODE_FEATURES = len(NODE_FEATURE_NAMES)

FEATURE_DEFINITIONS = {
    "p50_latency": {
        "index": 0,
        "description": "Median request execution latency",
        "unit": "milliseconds",
        "source_field": "p50Latency",
        "derivation": "Observed telemetry snapshot"
    },
    "p95_latency": {
        "index": 1,
        "description": "95th percentile request execution latency",
        "unit": "milliseconds",
        "source_field": "p95Latency",
        "derivation": "Observed telemetry snapshot"
    },
    "p99_latency": {
        "index": 2,
        "description": "99th percentile tail request latency",
        "unit": "milliseconds",
        "source_field": "p99Latency",
        "derivation": "Observed telemetry snapshot"
    },
    "error_rate": {
        "index": 3,
        "description": "HTTP / gRPC failure percentage",
        "unit": "percent",
        "source_field": "errorRate",
        "derivation": "Observed telemetry snapshot"
    },
    "request_rate": {
        "index": 4,
        "description": "Incoming request throughput rate",
        "unit": "requests_per_minute",
        "source_field": "requestRate",
        "derivation": "Observed telemetry snapshot"
    },
    "pool_utilization": {
        "index": 5,
        "description": "Thread / DB connection pool utilization",
        "unit": "percent",
        "source_field": "poolUtilization",
        "derivation": "Observed telemetry snapshot"
    },
    "db_latency": {
        "index": 6,
        "description": "Database relation query latency (0.0 for non-DB nodes)",
        "unit": "milliseconds",
        "source_field": "dbLatency",
        "derivation": "Observed telemetry snapshot (imputed to 0.0 for non-db nodes)"
    },
    "anomaly_score": {
        "index": 7,
        "description": "Statistical telemetry anomaly detector score",
        "unit": "unitless_score [0.0, 1.0]",
        "source_field": "anomalyScore",
        "derivation": "Observed telemetry snapshot"
    },
    "p99_latency_delta": {
        "index": 8,
        "description": "Causal first-difference: p99(t) - p99(t-1)",
        "unit": "milliseconds",
        "source_field": "p99Latency",
        "derivation": "Causal 1-step backward difference (0.0 at t=0)"
    },
    "error_rate_delta": {
        "index": 9,
        "description": "Causal first-difference: err(t) - err(t-1)",
        "unit": "percent",
        "source_field": "errorRate",
        "derivation": "Causal 1-step backward difference (0.0 at t=0)"
    }
}

# 4. Target Classes and Label Mappings
ROOT_CAUSE_SERVICES = [
    "inventory-db",
    "inventory-service",
    "order-service",
    "payment-service"
]
NUM_CLASSES = len(ROOT_CAUSE_SERVICES)

TARGET_TO_INDEX = {name: idx for idx, name in enumerate(ROOT_CAUSE_SERVICES)}
INDEX_TO_TARGET = {idx: name for idx, name in enumerate(ROOT_CAUSE_SERVICES)}

NO_FAULT_LABEL = "NO_FAULT"
NO_FAULT_TARGET_INDEX = -1
NO_FAULT_NODE_INDEX = -1

# 5. Temporal Sequence and Windowing Policy
SAMPLING_INTERVAL_SEC = 1.0
MAX_SEQUENCE_LENGTH = 40
MIN_SEQUENCE_LENGTH = 38
PADDING_VALUE = 0.0
PADDING_POLICY = "post_padding_with_temporal_mask"


def get_graph_schema() -> Dict[str, Any]:
    """Generates the authoritative graph schema metadata."""
    return {
        "dataset_version": DATASET_VERSION,
        "source_dataset": SOURCE_DATASET,
        "source_manifest": SOURCE_MANIFEST,
        "source_splits": SOURCE_SPLITS,
        "temporal_representation": "[T, N, F]",
        "node_count": NUM_NODES,
        "node_order": NODE_ORDER,
        "node_name_to_index": NODE_NAME_TO_INDEX,
        "edge_count": NUM_EDGES,
        "edge_list": [list(e) for e in EDGES],
        "edge_index": EDGE_INDEX,
        "edge_direction": EDGE_DIRECTION,
        "feature_count": NUM_NODE_FEATURES,
        "node_feature_names": NODE_FEATURE_NAMES,
        "feature_definitions": FEATURE_DEFINITIONS,
        "sequence_policy": {
            "sampling_interval_sec": SAMPLING_INTERVAL_SEC,
            "min_observed_length": MIN_SEQUENCE_LENGTH,
            "max_observed_length": MAX_SEQUENCE_LENGTH,
            "padded_length": MAX_SEQUENCE_LENGTH,
            "padding_policy": PADDING_POLICY,
            "padding_value": PADDING_VALUE,
            "temporal_mask_description": "Boolean array of length 40 where True indicates observed step and False indicates padding"
        },
        "label_definitions": {
            "root_cause_classes": ROOT_CAUSE_SERVICES,
            "class_count": NUM_CLASSES,
            "target_to_index": TARGET_TO_INDEX,
            "index_to_target": INDEX_TO_TARGET,
            "node_label_mapping": {
                svc: NODE_NAME_TO_INDEX[svc] for svc in ROOT_CAUSE_SERVICES
            }
        },
        "no_fault_policy": {
            "control_count": 10,
            "label_type": "NO_FAULT",
            "target_class": NO_FAULT_TARGET_INDEX,
            "node_label_index": NO_FAULT_NODE_INDEX,
            "description": "NO_FAULT control experiments are explicitly marked with label_type='NO_FAULT' and target_class=-1, preserving them for false-positive validation while permitting clean exclusion from 4-class root-cause classifier training."
        },
        "leakage_exclusions": [
            "fault_target",
            "fault_type",
            "ground_truth_root_cause",
            "detected_root_cause",
            "detected_confidence",
            "rca_match",
            "heuristic_rca_scores",
            "classical_ml_predictions",
            "classical_ml_probabilities",
            "future_temporal_information"
        ]
    }

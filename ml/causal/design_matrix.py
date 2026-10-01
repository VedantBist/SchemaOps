"""
Lagged Design Matrix Construction & Train-Only Normalization for CausalOps SCM.

Implements:
1. Extraction of canonical causal features (excluding derived sink anomaly_score and deltas).
2. Train-only normalization (fitting mean and std strictly on training experiments).
3. Hard topological feature masking (forbidding physically impossible or shortcut edges by construction).
4. Temporal lag tensor unrolling for order P >= 1 (strictly backward in time: t - k, k in [1, P]).
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
import numpy as np

# Canonical feature ordering in dataset/tg_v1:
# 0: p50_latency, 1: p95_latency, 2: p99_latency, 3: error_rate, 4: request_rate,
# 5: pool_utilization, 6: db_latency, 7: anomaly_score, 8: p99_latency_delta, 9: error_rate_delta

CANONICAL_NODES = [
    "api-gateway",
    "order-service",
    "inventory-service",
    "payment-service",
    "inventory-db",
]

NODE_TO_INDEX = {name: i for i, name in enumerate(CANONICAL_NODES)}

PRIMARY_CAUSAL_FEATURES = [
    "p50_latency",
    "p95_latency",
    "p99_latency",
    "error_rate",
    "request_rate",
    "pool_utilization",
    "db_latency",
]

FEATURE_TO_PRIMARY_INDEX = {name: i for i, name in enumerate(PRIMARY_CAUSAL_FEATURES)}
PRIMARY_INDICES_IN_RAW = [0, 1, 2, 3, 4, 5, 6]

# Forbidden derived features from Phase 3A:
EXCLUDED_FEATURES = {"anomaly_score", "p99_latency_delta", "error_rate_delta"}


def fit_train_normalization(
    train_samples: List[Any],
    primary_indices: List[int] = PRIMARY_INDICES_IN_RAW,
) -> Dict[str, Any]:
    """
    Fits mean and standard deviation strictly on the training experiments.
    Never uses validation or test set samples.

    Returns dictionary serialized for ml/models/causal_scm/normalization.json.
    """
    # Accumulate all valid unpadded timesteps across train samples
    # Raw sample.x shape: [T, N, 10]
    gathered: List[np.ndarray] = []
    for s in train_samples:
        x_raw = s.x[:, :, primary_indices]  # [T, N, 7]
        gathered.append(x_raw)

    concat_x = np.concatenate(gathered, axis=0)  # [Total_T, N, 7]
    mean = np.mean(concat_x, axis=0)             # [N, 7]
    std = np.std(concat_x, axis=0)               # [N, 7]

    # Handle zero-variance features (e.g. db_latency on non-DB nodes is identically 0.0)
    # Epsilon prevents division by zero
    std_safe = np.where(std < 1e-6, 1.0, std)

    norm_stats = {
        "nodes": CANONICAL_NODES,
        "features": PRIMARY_CAUSAL_FEATURES,
        "mean": mean.tolist(),
        "std": std_safe.tolist(),
        "std_raw": std.tolist(),
        "num_train_experiments": len(train_samples),
        "total_timesteps": int(concat_x.shape[0]),
    }
    return norm_stats


def apply_normalization(
    x_raw: np.ndarray,
    norm_stats: Dict[str, Any],
    primary_indices: List[int] = PRIMARY_INDICES_IN_RAW,
) -> np.ndarray:
    """
    Normalizes an unpadded telemetry tensor [T, N, 10] to [T, N, 7] using frozen train stats.
    """
    x_primary = x_raw[:, :, primary_indices].astype(np.float64)  # [T, N, 7]
    mean = np.array(norm_stats["mean"], dtype=np.float64)        # [N, 7]
    std = np.array(norm_stats["std"], dtype=np.float64)          # [N, 7]

    # Where std_raw was effectively 0, normalized value is 0.0
    normalized = (x_primary - mean) / std
    std_raw = np.array(norm_stats.get("std_raw", std), dtype=np.float64)
    normalized = np.where(std_raw < 1e-6, 0.0, normalized)
    return normalized


def is_edge_allowed(
    src_node: str,
    src_feat: str,
    tgt_node: str,
    tgt_feat: str,
    allow_all_topology: bool = False,
) -> bool:
    """
    Evaluates whether directed edge (src_node.src_feat -> tgt_node.tgt_feat) is allowed
    by the CausalOps Phase 3A system-knowledge graph constraints.

    Hard Mask Rules:
    1. If allow_all_topology is True (Ablation mode), allows any edge (except derived features).
    2. Intra-node (src_node == tgt_node):
       - Autoregressive self-lag: (src_feat == tgt_feat) is ALWAYS allowed.
       - Mechanistic intra-node pathways:
         request_rate -> pool_utilization, p99_latency
         p99_latency -> pool_utilization
         p50_latency -> p95_latency, p99_latency
         p95_latency -> p99_latency
         db_latency -> p99_latency (for inventory-db only)
    3. Inter-node Call-Aligned Forward (Load propagation along call graph):
       - api-gateway -> order-service: request_rate -> request_rate
       - order-service -> inventory-service: request_rate -> request_rate
       - order-service -> payment-service: request_rate -> request_rate
       - inventory-service -> inventory-db: request_rate -> request_rate
    4. Inter-node Backpressure Reverse (Latency / Error propagation opposite call graph):
       - inventory-db -> inventory-service:
         db_latency -> p99_latency, pool_utilization
         p99_latency -> p99_latency
       - inventory-service -> order-service:
         p99_latency -> p99_latency
         error_rate -> error_rate
         p99_latency -> pool_utilization
       - payment-service -> order-service:
         p99_latency -> p99_latency
         error_rate -> error_rate
         p99_latency -> pool_utilization
       - order-service -> api-gateway:
         p99_latency -> p99_latency
         error_rate -> error_rate
         p99_latency -> pool_utilization
    5. STRICTLY FORBIDDEN:
       - Any edge between orthogonal branches (inventory-db/inventory-service <-> payment-service).
       - Any skip-level shortcut (inventory-db -> order-service, inventory-db -> api-gateway,
         inventory-service -> api-gateway, payment-service -> api-gateway).
       - Any db_latency source from a non-DB node.
       - Any edge involving anomaly_score or deltas.
    """
    if src_feat in EXCLUDED_FEATURES or tgt_feat in EXCLUDED_FEATURES:
        return False

    if src_feat == "db_latency" and src_node != "inventory-db":
        return False

    if tgt_feat == "db_latency" and tgt_node != "inventory-db":
        return False

    if allow_all_topology:
        return True

    # 1. Intra-node
    if src_node == tgt_node:
        if src_feat == tgt_feat:
            return True  # AR self-lag
        if src_feat == "request_rate" and tgt_feat in ("pool_utilization", "p99_latency"):
            return True
        if src_feat == "p99_latency" and tgt_feat == "pool_utilization":
            return True
        if src_feat == "pool_utilization" and tgt_feat == "p99_latency":
            return True
        if src_feat == "p50_latency" and tgt_feat in ("p95_latency", "p99_latency"):
            return True
        if src_feat == "p95_latency" and tgt_feat == "p99_latency":
            return True
        if src_feat == "db_latency" and tgt_feat in ("p99_latency", "pool_utilization") and src_node == "inventory-db":
            return True
        return False

    # 2. Inter-node forbidden orthogonal checks
    orthogonal_inventory = {"inventory-db", "inventory-service"}
    if (src_node in orthogonal_inventory and tgt_node == "payment-service") or \
       (src_node == "payment-service" and tgt_node in orthogonal_inventory):
        return False

    # 3. Inter-node forbidden skip-level checks
    if src_node == "inventory-db" and tgt_node in ("order-service", "api-gateway"):
        return False
    if src_node in ("inventory-service", "payment-service") and tgt_node == "api-gateway":
        return False
    if src_node == "api-gateway" and tgt_node in ("inventory-service", "payment-service", "inventory-db"):
        return False

    # 4. Inter-node allowed call-aligned forward
    call_pairs = {
        ("api-gateway", "order-service"),
        ("order-service", "inventory-service"),
        ("order-service", "payment-service"),
        ("inventory-service", "inventory-db"),
    }
    if (src_node, tgt_node) in call_pairs:
        if src_feat == "request_rate" and tgt_feat == "request_rate":
            return True
        return False

    # 5. Inter-node allowed backpressure reverse
    if (src_node == "inventory-db" and tgt_node == "inventory-service"):
        if src_feat in ("db_latency", "p99_latency", "pool_utilization") and tgt_feat in ("p99_latency", "pool_utilization"):
            return True
        return False

    if (src_node in ("inventory-service", "payment-service") and tgt_node == "order-service"):
        if src_feat in ("p99_latency", "pool_utilization") and tgt_feat in ("p99_latency", "pool_utilization"):
            return True
        if src_feat == "error_rate" and tgt_feat == "error_rate":
            return True
        return False

    if (src_node == "order-service" and tgt_node == "api-gateway"):
        if src_feat in ("p99_latency", "pool_utilization") and tgt_feat in ("p99_latency", "pool_utilization"):
            return True
        if src_feat == "error_rate" and tgt_feat == "error_rate":
            return True
        return False

    return False


def get_allowed_predictors(
    tgt_node: str,
    tgt_feat: str,
    lag_order: int,
    allow_all_topology: bool = False,
) -> List[Dict[str, Any]]:
    """
    Returns the exact list of allowed predictor specs [(src_node, src_feat, lag), ...]
    for a given target variable.
    """
    predictors: List[Dict[str, Any]] = []
    for lag in range(1, lag_order + 1):
        for src_node in CANONICAL_NODES:
            for src_feat in PRIMARY_CAUSAL_FEATURES:
                if is_edge_allowed(src_node, src_feat, tgt_node, tgt_feat, allow_all_topology):
                    predictors.append({
                        "source_node": src_node,
                        "source_variable": src_feat,
                        "target_node": tgt_node,
                        "target_variable": tgt_feat,
                        "lag": lag,
                    })
    return predictors


def build_lagged_design_matrix(
    normalized_trajectories: List[np.ndarray],
    tgt_node: str,
    tgt_feat: str,
    lag_order: int = 5,
    allow_all_topology: bool = False,
) -> Tuple[np.ndarray, np.ndarray, List[Dict[str, Any]]]:
    """
    Constructs the unrolled design matrix X and target vector Y for target (tgt_node, tgt_feat).

    Args:
        normalized_trajectories: List of [T_e, N, F_primary] normalized arrays.
        tgt_node: Name of target node (e.g. 'order-service').
        tgt_feat: Name of target feature (e.g. 'p99_latency').
        lag_order: P (default 5). Predictors are strictly from t-1 ... t-P.
        allow_all_topology: If True, disables hard topology filtering for ablations.

    Returns:
        X: 2D array [M, K_allowed] of lagged predictors.
        y: 1D array [M] of target values.
        predictors_meta: List of length K_allowed with metadata for each column.
    """
    tgt_node_idx = NODE_TO_INDEX[tgt_node]
    tgt_feat_idx = FEATURE_TO_PRIMARY_INDEX[tgt_feat]

    predictors_meta = get_allowed_predictors(tgt_node, tgt_feat, lag_order, allow_all_topology)
    num_predictors = len(predictors_meta)

    all_y: List[np.ndarray] = []
    all_x: List[np.ndarray] = []

    for traj in normalized_trajectories:
        T, N, F = traj.shape
        if T <= lag_order:
            continue

        num_valid_t = T - lag_order
        y_traj = traj[lag_order:, tgt_node_idx, tgt_feat_idx]  # [num_valid_t]
        all_y.append(y_traj)

        if num_predictors == 0:
            x_traj = np.zeros((num_valid_t, 0), dtype=np.float64)
            all_x.append(x_traj)
            continue

        x_traj = np.empty((num_valid_t, num_predictors), dtype=np.float64)
        for col_idx, pred in enumerate(predictors_meta):
            s_node_idx = NODE_TO_INDEX[pred["source_node"]]
            s_feat_idx = FEATURE_TO_PRIMARY_INDEX[pred["source_variable"]]
            lag = pred["lag"]

            # t ranges from lag_order to T-1
            # predictor is at t - lag
            # When t = lag_order, predictor index is lag_order - lag >= 0 (since lag <= lag_order)
            start_pred = lag_order - lag
            end_pred = T - lag
            x_traj[:, col_idx] = traj[start_pred:end_pred, s_node_idx, s_feat_idx]

        all_x.append(x_traj)

    if not all_y:
        return np.zeros((0, num_predictors)), np.zeros((0,)), predictors_meta

    y = np.concatenate(all_y, axis=0)
    X = np.concatenate(all_x, axis=0) if num_predictors > 0 else np.zeros((len(y), 0))
    return X, y, predictors_meta

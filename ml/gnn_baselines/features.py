"""Temporal feature aggregation and normalization pipeline for GNN baselines.

Deterministic temporal aggregation converts [T, N, F] temporal graphs into
static node feature matrices [N, F * K] using K=6 descriptive statistics:
  1. mean: Average metric level over the incident window
  2. std: Variability / dispersion of the metric
  3. min: Minimum observed level
  4. max: Peak degradation level
  5. final: Last observed snapshot value
  6. max_abs_change: Peak 1-step dynamic transition rate

Strict Zero-Leakage Policy:
- Aggregation is computed strictly from observable sequence metrics.
- Normalization (z-score) is fitted ONLY on the training fault split.
- Frozen parameters are saved and applied to validation and test samples.
"""

import os
import json
from typing import Dict, List, Any, Optional, Tuple, Sequence
import numpy as np

from dataset.tg_v1.schema import (
    NODE_ORDER,
    NODE_FEATURE_NAMES,
    NUM_NODES,
    NUM_NODE_FEATURES
)
from dataset.tg_v1.loader import TemporalGraphSample


# Feature set definitions for ablations
FEATURE_SETS: Dict[str, Dict[str, Any]] = {
    "all": {
        "description": "Full feature set (10 features, including anomaly_score and deltas)",
        "indices": list(range(10)),
        "names": NODE_FEATURE_NAMES,
        "features_per_node": 10 * 6  # 60
    },
    "no_anomaly_score": {
        "description": "Critical Ablation: anomaly_score removed (9 features)",
        "indices": [0, 1, 2, 3, 4, 5, 6, 8, 9],
        "names": [NODE_FEATURE_NAMES[i] for i in [0, 1, 2, 3, 4, 5, 6, 8, 9]],
        "features_per_node": 9 * 6   # 54
    },
    "raw_telemetry_only": {
        "description": "Secondary Ablation: raw telemetry only without delta features (8 features)",
        "indices": [0, 1, 2, 3, 4, 5, 6, 7],
        "names": [NODE_FEATURE_NAMES[i] for i in [0, 1, 2, 3, 4, 5, 6, 7]],
        "features_per_node": 8 * 6   # 48
    },
    "raw_no_anomaly": {
        "description": "Secondary Ablation: raw telemetry without deltas or anomaly_score (7 features)",
        "indices": [0, 1, 2, 3, 4, 5, 6],
        "names": [NODE_FEATURE_NAMES[i] for i in [0, 1, 2, 3, 4, 5, 6]],
        "features_per_node": 7 * 6   # 42
    }
}

STATISTIC_NAMES = ["mean", "std", "min", "max", "final", "max_abs_change"]
NUM_STATISTICS = len(STATISTIC_NAMES)


def aggregate_sample_temporal_features(
    sample: TemporalGraphSample,
    feature_indices: Optional[List[int]] = None
) -> np.ndarray:
    """
    Computes deterministic temporal statistics for a single temporal graph sample.

    Args:
        sample: TemporalGraphSample with x shape [T, N, F]
        feature_indices: Optional list of feature indices to extract.
                         Defaults to all features (0..F-1).

    Returns:
        np.ndarray of shape [N, num_features * 6]
    """
    # Use unpadded sequence to avoid padding artifacts in stats
    x = sample.x  # shape [T, N, F]
    T, N, F = x.shape
    
    if feature_indices is None:
        feature_indices = list(range(F))
    
    num_selected = len(feature_indices)
    out_dim = num_selected * NUM_STATISTICS
    agg_matrix = np.zeros((N, out_dim), dtype=np.float64)

    for n in range(N):
        col_idx = 0
        for f_idx in feature_indices:
            series = x[:, n, f_idx]
            
            # 1. mean
            mean_val = float(np.mean(series))
            # 2. std
            std_val = float(np.std(series))
            # 3. min
            min_val = float(np.min(series))
            # 4. max
            max_val = float(np.max(series))
            # 5. final
            final_val = float(series[-1])
            # 6. max_abs_change
            if T >= 2:
                diffs = np.abs(series[1:] - series[:-1])
                max_change = float(np.max(diffs))
            else:
                max_change = 0.0

            agg_matrix[n, col_idx:col_idx + 6] = [
                mean_val, std_val, min_val, max_val, final_val, max_change
            ]
            col_idx += 6

    return agg_matrix


def fit_normalization(
    train_samples: Sequence[TemporalGraphSample],
    feature_indices: Optional[List[int]] = None
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Fits per-node z-score normalization parameters ONLY on training fault samples.

    Args:
        train_samples: List of training TemporalGraphSample instances (fault only).
        feature_indices: Selected feature indices.

    Returns:
        mean: shape [N, D_agg]
        std: shape [N, D_agg]
    """
    if len(train_samples) == 0:
        raise ValueError("Cannot fit normalization on empty sample list.")

    # Collect aggregated features: list of [N, D_agg]
    matrices = [
        aggregate_sample_temporal_features(s, feature_indices=feature_indices)
        for s in train_samples
    ]
    # Stack along experiment dimension: [B, N, D_agg]
    stacked = np.stack(matrices, axis=0)

    mean = np.mean(stacked, axis=0)  # [N, D_agg]
    std = np.std(stacked, axis=0)    # [N, D_agg]

    # Guard against zero variance
    std = np.where(std < 1e-6, 1.0, std)

    return mean, std


def apply_normalization(
    x_agg: np.ndarray,
    mean: np.ndarray,
    std: np.ndarray
) -> np.ndarray:
    """
    Applies fitted normalization to aggregated node features.

    Args:
        x_agg: Array of shape [N, D_agg] or [B, N, D_agg]
        mean: Mean array of shape [N, D_agg]
        std: Std array of shape [N, D_agg]

    Returns:
        Normalized array of identical shape.
    """
    return (x_agg - mean) / std


def save_normalization_params(
    filepath: str,
    mean: np.ndarray,
    std: np.ndarray,
    feature_set: str,
    train_count: int
) -> None:
    """Saves fitted normalization parameters to JSON file."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    payload = {
        "feature_set": feature_set,
        "feature_names": FEATURE_SETS[feature_set]["names"],
        "statistics": STATISTIC_NAMES,
        "train_samples_fitted": train_count,
        "node_order": NODE_ORDER,
        "mean": mean.tolist(),
        "std": std.tolist()
    }
    with open(filepath, "w") as f:
        json.dump(payload, f, indent=2)


def load_normalization_params(filepath: str) -> Dict[str, Any]:
    """Loads normalization parameters from JSON file."""
    with open(filepath, "r") as f:
        data = json.load(f)
    data["mean"] = np.array(data["mean"], dtype=np.float64)
    data["std"] = np.array(data["std"], dtype=np.float64)
    return data

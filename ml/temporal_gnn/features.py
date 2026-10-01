"""Temporal feature preparation and normalization pipeline for Temporal GNNs.

Extracts continuous temporal graph sequences of shape [B, T, N, F] and temporal masks [B, T].

Strict Zero-Leakage Policy:
- Normalization (per-node per-feature z-score) is fitted ONLY on valid timesteps
  of the 49 training fault experiments.
- Padded timesteps are strictly masked out and set to zero.
- Normalization parameters are saved for frozen deployment to validation and test sets.
"""

import os
import json
from typing import Dict, List, Any, Optional, Tuple, Sequence
import numpy as np

from dataset.tg_v1.schema import NODE_ORDER, NODE_FEATURE_NAMES, NUM_NODES, NUM_NODE_FEATURES
from dataset.tg_v1.loader import TemporalGraphSample

FEATURE_SETS: Dict[str, Dict[str, Any]] = {
    "all": {
        "description": "Full continuous telemetry (10 features including deltas)",
        "indices": list(range(10)),
        "names": NODE_FEATURE_NAMES,
        "num_features": 10
    },
    "no_deltas": {
        "description": "Ablation A: Continuous telemetry without delta features (8 features)",
        "indices": [0, 1, 2, 3, 4, 5, 6, 7],
        "names": [NODE_FEATURE_NAMES[i] for i in [0, 1, 2, 3, 4, 5, 6, 7]],
        "num_features": 8
    }
}


def fit_temporal_normalization(
    train_samples: Sequence[TemporalGraphSample],
    feature_indices: Optional[List[int]] = None
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Fits per-node per-feature z-score normalization strictly on training fault experiments.

    Args:
        train_samples: List of TemporalGraphSample instances (train split, fault only).
        feature_indices: Optional list of selected feature indices.

    Returns:
        mean: shape [N, num_features]
        std: shape [N, num_features]
    """
    if len(train_samples) == 0:
        raise ValueError("Cannot fit normalization on empty sample list.")

    if feature_indices is None:
        feature_indices = list(range(NUM_NODE_FEATURES))

    num_feat = len(feature_indices)
    means = np.zeros((NUM_NODES, num_feat), dtype=np.float64)
    stds = np.zeros((NUM_NODES, num_feat), dtype=np.float64)

    for n in range(NUM_NODES):
        for f_out_idx, f_in_idx in enumerate(feature_indices):
            # Collect all valid timesteps across all training samples
            valid_vals = []
            for s in train_samples:
                # s.x is unpadded [T, N, F]
                vals = s.x[:, n, f_in_idx]
                valid_vals.extend(vals)
            
            arr = np.array(valid_vals, dtype=np.float64)
            m = float(np.mean(arr))
            s_val = float(np.std(arr))
            if s_val < 1e-6:
                s_val = 1.0
            means[n, f_out_idx] = m
            stds[n, f_out_idx] = s_val

    return means, stds


def prepare_temporal_batch(
    samples: Sequence[TemporalGraphSample],
    mean: np.ndarray,
    std: np.ndarray,
    feature_indices: Optional[List[int]] = None
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[str]]:
    """
    Prepares normalized batch tensors from sample objects.

    Args:
        samples: Sequence of TemporalGraphSample instances.
        mean: Normalization mean [N, F]
        std: Normalization std [N, F]
        feature_indices: Feature indices to slice.

    Returns:
        X_norm: shape [B, 40, N, F]
        mask: shape [B, 40] boolean
        targets: shape [B] int
        exp_ids: list of experiment IDs
    """
    if feature_indices is None:
        feature_indices = list(range(mean.shape[1]))

    B = len(samples)
    T = 40
    N = NUM_NODES
    F = len(feature_indices)

    X_norm = np.zeros((B, T, N, F), dtype=np.float64)
    mask = np.zeros((B, T), dtype=bool)
    targets = np.zeros(B, dtype=int)
    exp_ids = []

    for b, s in enumerate(samples):
        exp_ids.append(s.experiment_id)
        targets[b] = s.target_class
        m = s.temporal_mask  # [40]
        mask[b] = m

        # Extract selected features from padded array [40, 5, 10]
        raw_sub = s.x_padded[:, :, feature_indices]  # [40, 5, F]

        # Apply per-node normalization
        norm_sub = (raw_sub - mean) / std

        # Zero out padded timesteps
        norm_sub[~m, :, :] = 0.0
        X_norm[b] = norm_sub

    return X_norm, mask, targets, exp_ids


def save_temporal_normalization(
    filepath: str,
    mean: np.ndarray,
    std: np.ndarray,
    feature_set: str,
    train_count: int
) -> None:
    """Saves normalization configuration and arrays to JSON."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    payload = {
        "feature_set": feature_set,
        "feature_names": FEATURE_SETS[feature_set]["names"],
        "num_features": len(FEATURE_SETS[feature_set]["names"]),
        "train_samples_fitted": train_count,
        "node_order": NODE_ORDER,
        "mean": mean.tolist(),
        "std": std.tolist()
    }
    with open(filepath, "w") as f:
        json.dump(payload, f, indent=2)


def load_temporal_normalization(filepath: str) -> Dict[str, Any]:
    """Loads normalization configuration and arrays from JSON."""
    with open(filepath, "r") as f:
        data = json.load(f)
    data["mean"] = np.array(data["mean"], dtype=np.float64)
    data["std"] = np.array(data["std"], dtype=np.float64)
    return data

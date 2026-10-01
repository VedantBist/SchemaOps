"""Feature extraction and normalization pipeline for the Incident Gate.

Extracts deterministic temporal statistics [N * F * K] for binary incident detection:
  Class 0: NO_FAULT (healthy baseline)
  Class 1: FAULT (incident present)

Strict Zero-Leakage Policy:
- Normalization (mean and std) is fitted strictly on the 56 training experiments
  (49 fault + 7 NO_FAULT controls).
- Validation and test splits are transformed using frozen training parameters.
- For early detection analysis, features are extracted only up to horizon tau,
  strictly preventing any future telemetry leakage.
"""

import os
import json
from typing import Dict, List, Any, Optional, Tuple, Sequence
import numpy as np

from dataset.tg_v1.schema import NODE_ORDER, NODE_FEATURE_NAMES, NUM_NODES, NUM_NODE_FEATURES
from dataset.tg_v1.loader import TemporalGraphSample

STATISTIC_NAMES = ["mean", "std", "min", "max", "final", "max_abs_change"]
NUM_STATS = len(STATISTIC_NAMES)
FEATURE_DIM = NUM_NODES * NUM_NODE_FEATURES * NUM_STATS  # 5 * 10 * 6 = 300


def extract_gate_features(
    sample_or_tensor: Any,
    horizon_cutoff: Optional[int] = None
) -> np.ndarray:
    """
    Computes deterministic temporal summary statistics for binary incident detection.

    Args:
        sample_or_tensor: TemporalGraphSample or raw ndarray of shape [T, N, F]
        horizon_cutoff: Optional prefix timestep cutoff (e.g. t=10).
                        Only timesteps [0:horizon_cutoff] are used.

    Returns:
        Flattened 1D feature array of shape [300]
    """
    if isinstance(sample_or_tensor, TemporalGraphSample):
        # Use unpadded sequence
        x = sample_or_tensor.x  # [T, N, F]
    else:
        x = np.asarray(sample_or_tensor, dtype=np.float64)

    if horizon_cutoff is not None and horizon_cutoff > 0:
        x = x[:horizon_cutoff, :, :]

    T, N, F = x.shape
    out = np.zeros(N * F * NUM_STATS, dtype=np.float64)
    idx = 0

    for n in range(N):
        for f in range(F):
            series = x[:, n, f]
            mean_v = float(np.mean(series))
            std_v = float(np.std(series))
            min_v = float(np.min(series))
            max_v = float(np.max(series))
            final_v = float(series[-1]) if T > 0 else 0.0

            if T >= 2:
                diffs = np.abs(series[1:] - series[:-1])
                change_v = float(np.max(diffs))
            else:
                change_v = 0.0

            out[idx:idx + 6] = [mean_v, std_v, min_v, max_v, final_v, change_v]
            idx += 6

    return out


def fit_incident_gate_normalization(
    train_samples: Sequence[TemporalGraphSample]
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Fits feature normalization strictly on the training split (49 fault + 7 NO_FAULT).

    Args:
        train_samples: All 56 training experiments.

    Returns:
        mean: shape [300]
        std: shape [300]
    """
    if len(train_samples) == 0:
        raise ValueError("Cannot fit normalization on empty sample list.")

    matrix = np.stack([extract_gate_features(s) for s in train_samples], axis=0)  # [56, 300]
    mean = np.mean(matrix, axis=0)
    std = np.std(matrix, axis=0)

    # Protect against zero variance
    std = np.where(std < 1e-6, 1.0, std)
    return mean, std


def apply_incident_gate_normalization(
    features: np.ndarray,
    mean: np.ndarray,
    std: np.ndarray
) -> np.ndarray:
    """Applies fitted normalization parameters to feature vector or batch."""
    return (features - mean) / std


def prepare_gate_dataset(
    samples: Sequence[TemporalGraphSample],
    mean: np.ndarray,
    std: np.ndarray
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """
    Prepares normalized feature matrix, binary targets, and experiment IDs.

    Returns:
        X_norm: shape [B, 300]
        y: shape [B] binary (1 = FAULT, 0 = NO_FAULT)
        exp_ids: list of experiment IDs
    """
    B = len(samples)
    raw = np.stack([extract_gate_features(s) for s in samples], axis=0)
    norm = apply_incident_gate_normalization(raw, mean, std)
    y = np.array([1 if s.is_fault else 0 for s in samples], dtype=int)
    exp_ids = [s.experiment_id for s in samples]
    return norm, y, exp_ids


def save_gate_normalization(
    filepath: str,
    mean: np.ndarray,
    std: np.ndarray,
    train_count: int
) -> None:
    """Saves normalization configuration and arrays to JSON."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    payload = {
        "feature_dim": len(mean),
        "train_samples_fitted": train_count,
        "statistics": STATISTIC_NAMES,
        "node_order": NODE_ORDER,
        "mean": mean.tolist(),
        "std": std.tolist()
    }
    with open(filepath, "w") as f:
        json.dump(payload, f, indent=2)


def load_gate_normalization(filepath: str) -> Dict[str, Any]:
    """Loads normalization configuration and arrays from JSON."""
    with open(filepath, "r") as f:
        data = json.load(f)
    data["mean"] = np.array(data["mean"], dtype=np.float64)
    data["std"] = np.array(data["std"], dtype=np.float64)
    return data

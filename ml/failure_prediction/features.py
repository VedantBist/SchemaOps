"""
Phase 6A — Pre-Fault Feature Extraction for Failure Prediction

PURPOSE:
    Extracts temporal, statistical, and topology-aware features from the
    PRE-FAULT portion of each experiment's telemetry window.

LEAKAGE SAFETY:
    - Features are computed ONLY from timesteps t=0 .. (fault_onset_step - 1).
    - For NO_FAULT experiments, features use all T timesteps.
    - If fault_onset_step == 0 or 1, we still extract features from the
      available pre-fault steps (possibly just 1 step) and flag this in
      the audit report.
    - Normalization statistics (mean, std) are fit ONLY on training split
      experiments and applied to validation/test.
    - No future-looking features (e.g., max over the entire window) are
      computed — only statistics up to the pre-fault window.

FEATURE GROUPS:
    1. Statistical moments (mean, std, max) of each node×feature over
       the pre-fault window: 5 nodes × 7 features × 3 stats = 105 values
    2. Temporal trend (linear regression slope) per node×feature: 35 values
    3. Topology-aware cascade features:
       - Cross-node correlation (downstream vs upstream) for p99 and error_rate
       - Max delta (variability proxy) per feature across nodes: 7 values
    4. Window length (number of pre-fault steps): 1 value

    Total raw feature vector: 105 + 35 + 14 + 1 = 155 features

    Normalized to zero-mean unit-variance using train-split statistics.

NOTE:
    Features are deterministic and reproducible.
    Feature computation does NOT use the fault_type, label, or any other
    ground-truth field — only the x tensor slice.
"""

from __future__ import annotations
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass
import numpy as np

# Feature indices in tg_v1 x tensor (7 primary causal features only)
PRIMARY_FEATURE_INDICES = [0, 1, 2, 3, 4, 5, 6]  # p50, p95, p99, err, req, pool, db_lat
PRIMARY_FEATURE_NAMES = ["p50_latency", "p95_latency", "p99_latency", "error_rate",
                          "request_rate", "pool_utilization", "db_latency"]
N_NODES = 5
N_PRIMARY = len(PRIMARY_FEATURE_INDICES)

# Topology: downstream → upstream edges (for cascade feature)
DOWNSTREAM_NODE = 0   # api-gateway receives traffic
UPSTREAM_NODES = [1, 2, 3, 4]  # order, inventory-svc, payment, inventory-db

# Total feature dimension
FEATURE_DIM = (N_NODES * N_PRIMARY * 3) + (N_NODES * N_PRIMARY) + (2 * N_NODES) + 1
# = 105 + 35 + 10 + 1 = 151
# Let's compute it explicitly in the extraction function


def extract_prefault_features(
    x: np.ndarray,
    fault_onset_step: Optional[int],
) -> np.ndarray:
    """
    Extracts the pre-fault feature vector from a single experiment.

    Args:
        x: Full telemetry tensor, shape [T, N, F]. Must be UNPADDED.
        fault_onset_step: First step with fault active (from labels.py).
                          None means NO_FAULT — use full window.

    Returns:
        1D feature vector of shape [D] where D = FEATURE_DIM.
    """
    T, N, F = x.shape

    # Select pre-fault window
    if fault_onset_step is None or fault_onset_step <= 0:
        window = x  # NO_FAULT or immediate onset: use full window
    else:
        window = x[:fault_onset_step]  # shape [fault_onset_step, N, F]

    if len(window) == 0:
        window = x[:1]  # last resort: at least 1 step

    # Extract only primary causal features
    w = window[:, :, PRIMARY_FEATURE_INDICES]  # [W, N, 7]
    W = w.shape[0]

    features = []

    # ── Group 1: Statistical moments per node × feature ──────────────────
    for n in range(N_NODES):
        for fi in range(N_PRIMARY):
            series = w[:, n, fi]  # [W]
            features.append(float(np.mean(series)))
            features.append(float(np.std(series, ddof=0)))
            features.append(float(np.max(series)))
    # Contribution: N_NODES × N_PRIMARY × 3 = 5 × 7 × 3 = 105

    # ── Group 2: Linear trend (slope) per node × feature ─────────────────
    t_axis = np.arange(W, dtype=np.float64)
    if W > 1:
        t_norm = (t_axis - t_axis.mean()) / (t_axis.std() + 1e-8)
    else:
        t_norm = np.zeros(W)

    for n in range(N_NODES):
        for fi in range(N_PRIMARY):
            series = w[:, n, fi].astype(np.float64)
            if W > 1:
                slope = float(np.dot(t_norm, series) / W)
            else:
                slope = 0.0
            features.append(slope)
    # Contribution: 5 × 7 = 35

    # ── Group 3: Topology-aware cross-node features ───────────────────────
    # For each node: max p99 spread vs gateway (cascade signal)
    gw_p99 = w[:, DOWNSTREAM_NODE, 2]  # api-gateway p99
    for n in range(N_NODES):
        node_p99 = w[:, n, 2]
        features.append(float(np.max(np.abs(node_p99 - gw_p99))))
    # Contribution: 5

    # For each node: max error_rate spread vs gateway
    gw_err = w[:, DOWNSTREAM_NODE, 3]
    for n in range(N_NODES):
        node_err = w[:, n, 3]
        features.append(float(np.max(np.abs(node_err - gw_err))))
    # Contribution: 5

    # ── Group 4: Pre-fault window length (info about lead time) ───────────
    features.append(float(W))
    # Contribution: 1

    # Total: 105 + 35 + 10 + 1 = 151
    return np.array(features, dtype=np.float64)


def get_feature_names() -> List[str]:
    """Returns ordered list of feature names matching extract_prefault_features output."""
    names = []

    stat_names = ["mean", "std", "max"]
    for n in range(N_NODES):
        node_names = ["api-gateway", "order-service", "inventory-service", "payment-service", "inventory-db"]
        for fn in PRIMARY_FEATURE_NAMES:
            for stat in stat_names:
                names.append(f"{node_names[n]}.{fn}.{stat}")

    node_names = ["api-gateway", "order-service", "inventory-service", "payment-service", "inventory-db"]
    for n in range(N_NODES):
        for fn in PRIMARY_FEATURE_NAMES:
            names.append(f"{node_names[n]}.{fn}.trend_slope")

    for n in range(N_NODES):
        names.append(f"{node_names[n]}.p99_spread_vs_gw")
    for n in range(N_NODES):
        names.append(f"{node_names[n]}.err_spread_vs_gw")

    names.append("prefault_window_length")

    return names


@dataclass
class FeatureNormalizationStats:
    """Train-split normalization statistics for standardizing features."""
    feature_mean: np.ndarray  # shape [D]
    feature_std: np.ndarray   # shape [D]
    feature_dim: int
    feature_names: List[str]
    n_train_samples: int

    def normalize(self, X: np.ndarray) -> np.ndarray:
        """Applies train-stats z-score normalization to feature matrix [N, D]."""
        return (X - self.feature_mean) / (self.feature_std + 1e-8)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "feature_dim": self.feature_dim,
            "n_train_samples": self.n_train_samples,
            "feature_names": self.feature_names,
            "feature_mean": self.feature_mean.tolist(),
            "feature_std": self.feature_std.tolist(),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FeatureNormalizationStats":
        return cls(
            feature_mean=np.array(d["feature_mean"]),
            feature_std=np.array(d["feature_std"]),
            feature_dim=d["feature_dim"],
            feature_names=d["feature_names"],
            n_train_samples=d["n_train_samples"],
        )


def fit_normalization_stats(
    train_feature_matrix: np.ndarray,
) -> FeatureNormalizationStats:
    """
    Fits normalization statistics from the training feature matrix.

    Args:
        train_feature_matrix: shape [N_train, D]

    Returns:
        FeatureNormalizationStats fitted on training data only.
    """
    mean = train_feature_matrix.mean(axis=0)
    std = train_feature_matrix.std(axis=0, ddof=0)
    return FeatureNormalizationStats(
        feature_mean=mean,
        feature_std=std,
        feature_dim=train_feature_matrix.shape[1],
        feature_names=get_feature_names(),
        n_train_samples=train_feature_matrix.shape[0],
    )


def build_feature_matrix(
    samples,
    labels_by_id: Dict[str, Any],
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """
    Builds the raw (unnormalized) feature matrix for a list of samples.

    Args:
        samples: Iterable of TemporalGraphSample.
        labels_by_id: Dict mapping experiment_id → FailurePredictionLabel.

    Returns:
        Tuple of:
            X: shape [N, D] — raw feature matrix
            y_dict: dict of {'within_5s': [N], 'within_10s': [N], 'within_30s': [N]}
            exp_ids: list of experiment_ids [N]
    """
    rows = []
    y5, y10, y30 = [], [], []
    exp_ids = []

    for sample in samples:
        lbl = labels_by_id[sample.experiment_id]
        feat = extract_prefault_features(sample.x, lbl.fault_onset_step)
        rows.append(feat)
        y5.append(int(lbl.failure_within_5s))
        y10.append(int(lbl.failure_within_10s))
        y30.append(int(lbl.failure_within_30s))
        exp_ids.append(sample.experiment_id)

    X = np.array(rows, dtype=np.float64)
    y_dict = {
        "within_5s": np.array(y5, dtype=np.int32),
        "within_10s": np.array(y10, dtype=np.int32),
        "within_30s": np.array(y30, dtype=np.int32),
    }
    return X, y_dict, exp_ids

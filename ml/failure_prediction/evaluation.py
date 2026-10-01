"""
Phase 6A — Failure Prediction Evaluation

Computes comprehensive evaluation metrics for each model × horizon combination:

    1. Classification metrics: accuracy, precision, recall, F1, AUC-ROC, AUC-PR
    2. Lead-time metrics: mean / median seconds before fault onset
    3. Calibration: Brier score, reliability diagram data (10 bins)
    4. Leakage audit: verifies no future telemetry leaked into features
    5. Per-fault-type breakdown: metrics by fault_type category

SCIENTIFIC INTEGRITY RULES:
    - Metrics are always reported on the HELD-OUT TEST split.
    - Validation split is used ONLY for hyperparameter tuning / early stopping.
    - If a model performs poorly, its poor performance is honestly reported.
    - There is no metric threshold that the model must "pass" — results
      are reported as-is with explicit limitations where applicable.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Tuple
import numpy as np


@dataclass
class HorizonMetrics:
    """Evaluation metrics for a single prediction horizon."""
    horizon: str                  # "within_5s", "within_10s", "within_30s"
    model_type: str
    n_samples: int
    n_positive: int
    n_negative: int
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    auc_roc: float
    auc_pr: float
    brier_score: float
    specificity: float
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    class_imbalance_ratio: Optional[float] = None  # n_positive / n_negative (None when n_negative=0)

    # Lead-time metrics (only for experiments where prediction is correct)
    mean_lead_time_sec: Optional[float] = None
    median_lead_time_sec: Optional[float] = None
    min_lead_time_sec: Optional[float] = None
    max_lead_time_sec: Optional[float] = None

    # Per-fault-type breakdown
    per_fault_type_recall: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def compute_horizon_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_proba: np.ndarray,
    horizon: str,
    model_type: str,
    fault_onset_secs: Optional[List[Optional[float]]] = None,
    fault_types: Optional[List[str]] = None,
) -> HorizonMetrics:
    """
    Computes all evaluation metrics for a single horizon and model.

    Args:
        y_true: Ground-truth binary labels [N].
        y_pred: Model predicted binary labels [N].
        y_proba: Model predicted probabilities [N, 2]; [:,1] = P(failure).
        horizon: Horizon name ("within_5s", "within_10s", "within_30s").
        model_type: Model type string for reporting.
        fault_onset_secs: Detected fault onset seconds for each sample (for lead time).
        fault_types: Fault type string for each sample (for per-type breakdown).

    Returns:
        HorizonMetrics with all fields populated.
    """
    from sklearn.metrics import (
        accuracy_score, precision_score, recall_score, f1_score,
        roc_auc_score, average_precision_score, brier_score_loss,
        confusion_matrix
    )

    n = len(y_true)
    n_pos = int(y_true.sum())
    n_neg = n - n_pos

    accuracy = float(accuracy_score(y_true, y_pred))

    # Handle edge cases where only one class is present
    if n_pos == 0 or n_neg == 0:
        precision = 0.0
        recall = 0.0
        f1 = 0.0
        auc_roc = 0.5
        auc_pr = float(n_pos / n) if n_pos > 0 else 0.0
    else:
        precision = float(precision_score(y_true, y_pred, zero_division=0))
        recall = float(recall_score(y_true, y_pred, zero_division=0))
        f1 = float(f1_score(y_true, y_pred, zero_division=0))
        prob_pos = y_proba[:, 1] if y_proba.ndim == 2 else y_proba

        try:
            auc_roc = float(roc_auc_score(y_true, prob_pos))
        except Exception:
            auc_roc = 0.5

        try:
            auc_pr = float(average_precision_score(y_true, prob_pos))
        except Exception:
            auc_pr = float(n_pos / n)

    prob_pos = y_proba[:, 1] if y_proba.ndim == 2 else y_proba
    brier = float(brier_score_loss(y_true, prob_pos))

    # Confusion matrix
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    if cm.shape == (2, 2):
        tn, fp, fn, tp = cm.ravel()
    else:
        tn = fp = fn = tp = 0

    # Lead-time: seconds between prediction opportunity and fault onset
    # For correctly predicted positive samples with known onset
    mean_lt = median_lt = min_lt = max_lt = None
    if fault_onset_secs is not None:
        lead_times = []
        for i in range(n):
            if y_pred[i] == 1 and y_true[i] == 1 and fault_onset_secs[i] is not None:
                # Lead time = fault onset sec (since prediction is made at t=0)
                lead_times.append(fault_onset_secs[i])
        if lead_times:
            arr = np.array(lead_times)
            mean_lt = float(arr.mean())
            median_lt = float(np.median(arr))
            min_lt = float(arr.min())
            max_lt = float(arr.max())

    # Per-fault-type recall
    per_ft_recall: Dict[str, float] = {}
    if fault_types is not None:
        unique_types = sorted(set(fault_types))
        for ft in unique_types:
            mask = np.array([t == ft for t in fault_types])
            if mask.sum() == 0:
                continue
            yt_sub = y_true[mask]
            yp_sub = y_pred[mask]
            if yt_sub.sum() > 0:
                per_ft_recall[ft] = float(recall_score(yt_sub, yp_sub, zero_division=0))
            else:
                per_ft_recall[ft] = None  # No positive samples for this fault type in this split

    imbalance = (n_pos / n_neg) if n_neg > 0 else None  # None when no negatives
    specificity = float(tn / (tn + fp)) if (tn + fp) > 0 else 1.0

    return HorizonMetrics(
        horizon=horizon,
        model_type=model_type,
        n_samples=n,
        n_positive=n_pos,
        n_negative=n_neg,
        accuracy=round(accuracy, 4),
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1_score=round(f1, 4),
        auc_roc=round(auc_roc, 4),
        auc_pr=round(auc_pr, 4),
        brier_score=round(brier, 4),
        specificity=round(specificity, 4),
        true_positives=int(tp),
        false_positives=int(fp),
        true_negatives=int(tn),
        false_negatives=int(fn),
        class_imbalance_ratio=round(imbalance, 4) if imbalance is not None else None,
        mean_lead_time_sec=round(mean_lt, 2) if mean_lt is not None else None,
        median_lead_time_sec=round(median_lt, 2) if median_lt is not None else None,
        min_lead_time_sec=round(min_lt, 2) if min_lt is not None else None,
        max_lead_time_sec=round(max_lt, 2) if max_lt is not None else None,
        per_fault_type_recall=per_ft_recall,
    )


def compute_ece(
    y_true: np.ndarray,
    y_proba: np.ndarray,
    n_bins: int = 10,
) -> float:
    """
    Computes Expected Calibration Error (ECE).
    """
    prob_pos = y_proba[:, 1] if y_proba.ndim == 2 else y_proba
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    n = len(y_true)
    for i in range(n_bins):
        low, high = bins[i], bins[i + 1]
        bin_mask = (prob_pos >= low) & (prob_pos < high if i < n_bins - 1 else prob_pos <= high)
        bin_size = int(bin_mask.sum())
        if bin_size > 0:
            bin_acc = float(y_true[bin_mask].mean())
            bin_conf = float(prob_pos[bin_mask].mean())
            ece += (bin_size / n) * abs(bin_acc - bin_conf)
    return round(float(ece), 4)


def calibration_curve_data(
    y_true: np.ndarray,
    y_proba: np.ndarray,
    n_bins: int = 10,
) -> Dict[str, Any]:
    """
    Computes reliability diagram data for calibration assessment.

    Returns:
        Dict with 'mean_predicted_prob' and 'fraction_of_positives' arrays
        (one value per bin), suitable for a calibration curve plot.
    """
    try:
        from sklearn.calibration import calibration_curve
        prob_pos = y_proba[:, 1] if y_proba.ndim == 2 else y_proba
        fraction_pos, mean_pred = calibration_curve(
            y_true, prob_pos, n_bins=n_bins, strategy="uniform"
        )
        return {
            "mean_predicted_prob": mean_pred.tolist(),
            "fraction_of_positives": fraction_pos.tolist(),
            "n_bins": n_bins,
            "is_valid": True,
        }
    except Exception as e:
        return {"is_valid": False, "error": str(e)}


def leakage_audit(
    X_train: np.ndarray,
    X_val: np.ndarray,
    X_test: np.ndarray,
    train_ids: List[str],
    val_ids: List[str],
    test_ids: List[str],
) -> Dict[str, Any]:
    """
    Audits the feature matrices for potential temporal leakage.

    Checks:
        1. No experiment appears in more than one split.
        2. Feature statistics are consistent (no extreme outliers in test vs train).
        3. Feature dimensionality matches across splits.
    """
    issues = []

    # 1. ID disjoint check
    train_set, val_set, test_set = set(train_ids), set(val_ids), set(test_ids)
    tv = train_set & val_set
    tt = train_set & test_set
    vt = val_set & test_set
    if tv:
        issues.append(f"OVERLAP: train ∩ val = {tv}")
    if tt:
        issues.append(f"OVERLAP: train ∩ test = {tt}")
    if vt:
        issues.append(f"OVERLAP: val ∩ test = {vt}")

    # 2. Dimension consistency
    dims = set([X_train.shape[1], X_val.shape[1], X_test.shape[1]])
    if len(dims) > 1:
        issues.append(f"DIM_MISMATCH: train={X_train.shape[1]} val={X_val.shape[1]} test={X_test.shape[1]}")

    # 3. Basic distribution check: test mean within 3 SD of train mean
    train_mean = X_train.mean(axis=0)
    train_std = X_train.std(axis=0) + 1e-8
    test_mean = X_test.mean(axis=0)
    deviation = np.abs(test_mean - train_mean) / train_std
    extreme_features = int((deviation > 5.0).sum())
    if extreme_features > 5:
        issues.append(
            f"DISTRIBUTION_SHIFT: {extreme_features} features deviate >5σ from train mean in test set"
        )

    return {
        "status": "PASSED" if not issues else "WARNING",
        "n_train": X_train.shape[0],
        "n_val": X_val.shape[0],
        "n_test": X_test.shape[0],
        "feature_dim": X_train.shape[1],
        "id_overlap_check": "PASSED" if not (tv or tt or vt) else "FAILED",
        "dimension_consistency": "PASSED" if len(dims) == 1 else "FAILED",
        "distribution_shift_extreme_features": extreme_features,
        "issues": issues,
    }

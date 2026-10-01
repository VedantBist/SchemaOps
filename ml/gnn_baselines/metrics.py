"""Metric calculation and evaluation utilities for GNN baselines.

Implements rigorous multi-class evaluation:
  - Accuracy
  - Macro Precision, Recall, F1
  - Weighted Precision, Recall, F1
  - Per-class Precision, Recall, F1, and Support
  - Confusion Matrix
  - False-positive analysis for NO_FAULT control samples
"""

from typing import Dict, List, Any, Optional
import numpy as np
from sklearn.metrics import (
    accuracy_score,
    precision_recall_fscore_support,
    confusion_matrix
)

# Canonical 4-class target mapping
CLASS_NAMES = [
    "inventory-db",       # 0
    "inventory-service",  # 1
    "order-service",      # 2
    "payment-service"     # 3
]


def evaluate_predictions(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_probs: Optional[np.ndarray] = None
) -> Dict[str, Any]:
    """
    Computes complete classification metrics for 4-class root cause analysis.

    Args:
        y_true: True class indices (0..3) of shape [N]
        y_pred: Predicted class indices (0..3) of shape [N]
        y_probs: Optional predicted probabilities of shape [N, 4]

    Returns:
        Structured dictionary of evaluation metrics.
    """
    y_true = np.asarray(y_true, dtype=int)
    y_pred = np.asarray(y_pred, dtype=int)

    acc = float(accuracy_score(y_true, y_pred))

    # Macro metrics
    p_macro, r_macro, f1_macro, _ = precision_recall_fscore_support(
        y_true, y_pred, average="macro", zero_division=0
    )

    # Weighted metrics
    p_wt, r_wt, f1_wt, _ = precision_recall_fscore_support(
        y_true, y_pred, average="weighted", zero_division=0
    )

    # Per-class metrics
    p_per, r_per, f1_per, sup_per = precision_recall_fscore_support(
        y_true, y_pred, average=None, labels=list(range(len(CLASS_NAMES))), zero_division=0
    )

    per_class = {}
    for idx, cname in enumerate(CLASS_NAMES):
        per_class[cname] = {
            "class_index": idx,
            "precision": float(p_per[idx]),
            "recall": float(r_per[idx]),
            "f1": float(f1_per[idx]),
            "support": int(sup_per[idx])
        }

    # Confusion matrix
    cm = confusion_matrix(y_true, y_pred, labels=list(range(len(CLASS_NAMES))))

    results = {
        "sample_count": len(y_true),
        "accuracy": acc,
        "macro_precision": float(p_macro),
        "macro_recall": float(r_macro),
        "macro_f1": float(f1_macro),
        "weighted_precision": float(p_wt),
        "weighted_recall": float(r_wt),
        "weighted_f1": float(f1_wt),
        "per_class": per_class,
        "confusion_matrix": cm.tolist()
    }

    if y_probs is not None:
        # Confidence statistics
        max_probs = np.max(y_probs, axis=-1)
        results["mean_confidence"] = float(np.mean(max_probs))
        results["min_confidence"] = float(np.min(max_probs))
        results["max_confidence"] = float(np.max(max_probs))

    return results


def evaluate_controls(
    control_samples: List[Any],
    model: Any,
    norm_mean: np.ndarray,
    norm_std: np.ndarray,
    feature_indices: Optional[List[int]] = None
) -> Dict[str, Any]:
    """
    Evaluates model behavior on NO_FAULT control samples to analyze false-positive patterns.

    Args:
        control_samples: List of TemporalGraphSample instances with is_fault=False
        model: Trained model (MLP, GCN, or GAT)
        norm_mean: Fitted normalization mean
        norm_std: Fitted normalization std
        feature_indices: Feature indices used by the model

    Returns:
        Summary of control predictions, predicted class distribution, and confidence.
    """
    from ml.gnn_baselines.features import (
        aggregate_sample_temporal_features,
        apply_normalization
    )

    if not control_samples:
        return {"control_count": 0, "message": "No controls provided"}

    matrices = [
        aggregate_sample_temporal_features(s, feature_indices=feature_indices)
        for s in control_samples
    ]
    stacked = np.stack(matrices, axis=0)
    norm_x = apply_normalization(stacked, norm_mean, norm_std)

    model.eval()
    logits = model.forward(norm_x)
    
    # Softmax probabilities
    max_l = np.max(logits, axis=-1, keepdims=True)
    exp_l = np.exp(logits - max_l)
    probs = exp_l / np.sum(exp_l, axis=-1, keepdims=True)

    preds = np.argmax(probs, axis=-1)
    max_conf = np.max(probs, axis=-1)

    pred_counts = {cname: int(np.sum(preds == idx)) for idx, cname in enumerate(CLASS_NAMES)}

    return {
        "control_count": len(control_samples),
        "description": "Model behavior on un-faulted control runs (false positive root-cause attribution)",
        "prediction_distribution": pred_counts,
        "mean_confidence": float(np.mean(max_conf)),
        "min_confidence": float(np.min(max_conf)),
        "max_confidence": float(np.max(max_conf)),
        "per_sample": [
            {
                "experiment_id": s.experiment_id,
                "predicted_class": CLASS_NAMES[preds[i]],
                "confidence": float(max_conf[i]),
                "probabilities": {CLASS_NAMES[c]: float(probs[i, c]) for c in range(4)}
            }
            for i, s in enumerate(control_samples)
        ]
    }

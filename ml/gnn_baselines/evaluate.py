"""Evaluation pipeline for GNN baselines.

Evaluates trained model checkpoints on the held-out test split and NO_FAULT control samples:
  - Held-out test evaluation on the 10 fault experiments
  - False-positive analysis on all 10 NO_FAULT control experiments
  - Produces complete per-class metrics, confusion matrices, and confidence summaries.
"""

from typing import Dict, List, Any, Optional
import numpy as np

from ml.gnn_baselines.features import (
    FEATURE_SETS,
    aggregate_sample_temporal_features,
    apply_normalization
)
from ml.gnn_baselines.metrics import (
    evaluate_predictions,
    evaluate_controls,
    CLASS_NAMES
)
from ml.gnn_baselines.utils import load_checkpoint
from dataset.tg_v1.loader import TemporalGraphDataset, TemporalGraphSample


def evaluate_model_on_split(
    model: Any,
    samples: List[TemporalGraphSample],
    norm_mean: np.ndarray,
    norm_std: np.ndarray,
    feature_set: str = "all"
) -> Dict[str, Any]:
    """
    Evaluates a trained model on a given list of fault samples.
    """
    if len(samples) == 0:
        return {"error": "Empty sample list"}

    feat_indices = FEATURE_SETS[feature_set]["indices"]

    X_raw = np.stack([
        aggregate_sample_temporal_features(s, feature_indices=feat_indices)
        for s in samples
    ], axis=0)
    X_norm = apply_normalization(X_raw, norm_mean, norm_std)
    y_true = np.array([s.target_class for s in samples], dtype=int)

    model.eval()
    logits = model.forward(X_norm)

    max_l = np.max(logits, axis=-1, keepdims=True)
    exp_l = np.exp(logits - max_l)
    probs = exp_l / np.sum(exp_l, axis=-1, keepdims=True)
    preds = np.argmax(probs, axis=-1)

    metrics = evaluate_predictions(y_true, preds, probs)
    return metrics


def evaluate_checkpoint_file(checkpoint_path: str) -> Dict[str, Any]:
    """
    Loads a checkpoint from disk and performs full evaluation on test split and controls.
    """
    from ml.gnn_baselines.models import MLPBaseline, GCNBaseline, GATBaseline

    checkpoint = load_checkpoint(checkpoint_path)
    cfg = checkpoint["config"]
    model_type = cfg["model_type"]
    feature_set = cfg["feature_set"]
    in_features = cfg["in_features"]
    norm_mean = checkpoint["norm_mean"]
    norm_std = checkpoint["norm_std"]

    # Recreate model
    if model_type == "mlp":
        model = MLPBaseline(
            in_features=5 * in_features,
            dropout_p=0.0
        )
    elif model_type == "gcn":
        model = GCNBaseline(
            in_features=in_features,
            dropout_p=0.0
        )
    elif model_type == "gat":
        model = GATBaseline(
            in_features=in_features,
            num_heads=2,
            dropout_p=0.0
        )
    else:
        raise ValueError(f"Unknown model_type: {model_type}")

    model.load_state_dict(checkpoint["state_dict"])
    model.eval()

    # Load test split
    test_ds = TemporalGraphDataset(split="test", fault_only=True)
    test_metrics = evaluate_model_on_split(
        model, list(test_ds), norm_mean, norm_std, feature_set=feature_set
    )

    # Load control samples across all splits
    all_ds = TemporalGraphDataset(split=None, fault_only=False)
    controls = [s for s in all_ds if not s.is_fault]
    feat_indices = FEATURE_SETS[feature_set]["indices"]
    control_analysis = evaluate_controls(controls, model, norm_mean, norm_std, feat_indices)

    return {
        "model_type": model_type,
        "config": cfg,
        "best_epoch": checkpoint["best_epoch"],
        "val_metrics": checkpoint["val_metrics"],
        "test_metrics": test_metrics,
        "control_analysis": control_analysis
    }

"""Evaluation pipeline for Phase 2C Temporal Graph Learning models.

Evaluates trained checkpoints on:
  - Held-out test split (10 fault experiments)
  - NO_FAULT control samples (10 un-faulted experiments)
  - Temporal propagation timeline analysis across canonical fault types
"""

from typing import Dict, List, Any, Optional, Tuple
import numpy as np

from ml.gnn_baselines.metrics import evaluate_predictions, CLASS_NAMES
from ml.gnn_baselines.utils import load_checkpoint
from ml.temporal_gnn.features import (
    FEATURE_SETS,
    prepare_temporal_batch
)
from ml.temporal_gnn.temporal_model import TemporalOnlyBaseline
from ml.temporal_gnn.temporal_gat import TemporalGATBaseline
from ml.temporal_gnn.spatiotemporal import SpatioTemporalGNN
from ml.temporal_gnn.analysis import analyze_experiment_propagation
from dataset.tg_v1.loader import TemporalGraphDataset, TemporalGraphSample


def evaluate_temporal_model_on_samples(
    model: Any,
    samples: List[TemporalGraphSample],
    norm_mean: np.ndarray,
    norm_std: np.ndarray,
    feature_indices: Optional[List[int]] = None
) -> Dict[str, Any]:
    """Evaluates a temporal model on a batch of samples."""
    if len(samples) == 0:
        return {"error": "Empty sample list"}

    X, mask, targets, _ = prepare_temporal_batch(
        samples, norm_mean, norm_std, feature_indices=feature_indices
    )

    model.eval()
    out = model.forward(X, mask=mask)
    logits = out[0] if isinstance(out, tuple) else out

    max_l = np.max(logits, axis=-1, keepdims=True)
    exp_l = np.exp(logits - max_l)
    probs = exp_l / np.sum(exp_l, axis=-1, keepdims=True)
    preds = np.argmax(probs, axis=-1)

    metrics = evaluate_predictions(targets, preds, probs)
    return metrics


def evaluate_temporal_controls(
    model: Any,
    control_samples: List[TemporalGraphSample],
    norm_mean: np.ndarray,
    norm_std: np.ndarray,
    feature_indices: Optional[List[int]] = None
) -> Dict[str, Any]:
    """Evaluates false-positive attribution on un-faulted NO_FAULT controls."""
    if len(control_samples) == 0:
        return {"control_count": 0}

    X, mask, _, exp_ids = prepare_temporal_batch(
        control_samples, norm_mean, norm_std, feature_indices=feature_indices
    )

    model.eval()
    out = model.forward(X, mask=mask)
    logits = out[0] if isinstance(out, tuple) else out

    max_l = np.max(logits, axis=-1, keepdims=True)
    exp_l = np.exp(logits - max_l)
    probs = exp_l / np.sum(exp_l, axis=-1, keepdims=True)
    preds = np.argmax(probs, axis=-1)
    max_confs = np.max(probs, axis=-1)

    dist = {cname: int(np.sum(preds == idx)) for idx, cname in enumerate(CLASS_NAMES)}

    return {
        "control_count": len(control_samples),
        "prediction_distribution": dist,
        "mean_confidence": round(float(np.mean(max_confs)), 4),
        "min_confidence": round(float(np.min(max_confs)), 4),
        "max_confidence": round(float(np.max(max_confs)), 4),
        "per_sample": [
            {
                "experiment_id": exp_ids[i],
                "predicted_class": CLASS_NAMES[preds[i]],
                "confidence": round(float(max_confs[i]), 4),
                "probabilities": {CLASS_NAMES[c]: round(float(probs[i, c]), 4) for c in range(4)}
            }
            for i in range(len(control_samples))
        ]
    }


def load_and_reconstruct_model(checkpoint_path: str) -> Tuple[Any, Dict[str, Any], np.ndarray, np.ndarray]:
    """Loads checkpoint from disk and reconstructs model."""
    ckpt = load_checkpoint(checkpoint_path)
    cfg = ckpt["config"]
    model_type = cfg["model_type"]
    num_features = cfg.get("num_features", 10)
    norm_mean = ckpt["norm_mean"]
    norm_std = ckpt["norm_std"]

    if model_type in ["temporal_only_v1", "temporal_only"]:
        model = TemporalOnlyBaseline(
            num_nodes=5,
            num_features=num_features,
            gru_hidden_dim=64,
            classifier_hidden_dim=32,
            dropout_p=0.0
        )
    elif model_type in ["temporal_gat_v1", "temporal_gat"]:
        model = TemporalGATBaseline(
            num_nodes=5,
            num_features=num_features,
            gat_hidden_dim=32,
            num_heads=2,
            gru_hidden_dim=64,
            classifier_hidden_dim=32,
            dropout_p=0.0
        )
    elif model_type in ["spatiotemporal_gnn_v1", "spatiotemporal_v1", "spatiotemporal"]:
        model = SpatioTemporalGNN(
            num_nodes=5,
            num_features=num_features,
            gat_hidden_dim=32,
            num_heads=2,
            gru_hidden_dim=48,
            classifier_hidden_dim=32,
            dropout_p=0.0
        )
    else:
        raise ValueError(f"Unknown model_type: {model_type}")

    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model, cfg, norm_mean, norm_std


def evaluate_checkpoint_file(checkpoint_path: str) -> Dict[str, Any]:
    """Evaluates checkpoint file on test split and controls without retraining."""
    model, cfg, norm_mean, norm_std = load_and_reconstruct_model(checkpoint_path)
    feat_set = cfg.get("feature_set", "all")
    feat_indices = FEATURE_SETS[feat_set]["indices"]

    test_ds = list(TemporalGraphDataset(split="test", fault_only=True))
    all_ds = list(TemporalGraphDataset(split=None, fault_only=False))
    controls = [s for s in all_ds if not s.is_fault]

    test_metrics = evaluate_temporal_model_on_samples(
        model, test_ds, norm_mean, norm_std, feature_indices=feat_indices
    )
    control_analysis = evaluate_temporal_controls(
        model, controls, norm_mean, norm_std, feature_indices=feat_indices
    )

    return {
        "model_type": model.name,
        "config": cfg,
        "test_metrics": test_metrics,
        "control_analysis": control_analysis
    }

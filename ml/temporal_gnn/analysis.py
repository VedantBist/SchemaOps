"""Temporal failure propagation analysis for Phase 2C.

Examines model prediction dynamics and confidence evolution over time as faults
propagate across the microservice dependency topology.

Produces prefix timelines:
  t: timestep (e.g. t=5, 10, 15, 20, 25, 30, 35, 40)
  predicted_class: attributed root cause
  confidence: softmax probability
  class_probabilities: distribution across the 4 services
"""

from typing import Dict, List, Any, Optional
import numpy as np

from ml.gnn_baselines.metrics import CLASS_NAMES
from ml.temporal_gnn.features import prepare_temporal_batch
from dataset.tg_v1.loader import TemporalGraphSample


def analyze_experiment_propagation(
    model: Any,
    sample: TemporalGraphSample,
    norm_mean: np.ndarray,
    norm_std: np.ndarray,
    feature_indices: Optional[List[int]] = None,
    evaluation_timesteps: Optional[List[int]] = None
) -> Dict[str, Any]:
    """
    Evaluates model prediction dynamics across increasing temporal observation horizons.

    Args:
        model: Trained temporal or spatio-temporal model
        sample: TemporalGraphSample
        norm_mean: Fitted normalization mean
        norm_std: Fitted normalization std
        feature_indices: Selected feature indices
        evaluation_timesteps: List of timestep cutoffs (default: [5, 10, 15, 20, 25, 30, 35, T])

    Returns:
        Dictionary with experiment metadata, ground truth, and temporal timeline.
    """
    T_valid = sample.sequence_length
    if evaluation_timesteps is None:
        steps = [5, 10, 15, 20, 25, 30, 35]
        evaluation_timesteps = [s for s in steps if s < T_valid] + [T_valid]

    X, full_mask, targets, _ = prepare_temporal_batch(
        [sample], norm_mean, norm_std, feature_indices=feature_indices
    )

    timeline = []
    model.eval()

    for step_cutoff in evaluation_timesteps:
        # Create prefix mask where only steps up to step_cutoff are True
        prefix_mask = np.zeros_like(full_mask)
        prefix_mask[0, :step_cutoff] = True

        out = model.forward(X, mask=prefix_mask)
        logits = out[0] if isinstance(out, tuple) else out

        max_l = np.max(logits, axis=-1, keepdims=True)
        exp_l = np.exp(logits - max_l)
        probs = (exp_l / np.sum(exp_l, axis=-1, keepdims=True))[0]

        pred_idx = int(np.argmax(probs))
        conf = float(probs[pred_idx])

        timeline.append({
            "timestep": step_cutoff,
            "relative_sec": round(step_cutoff * 1.0, 1),
            "predicted_class": CLASS_NAMES[pred_idx],
            "confidence": round(conf, 4),
            "probabilities": {CLASS_NAMES[c]: round(float(probs[c]), 4) for c in range(4)},
            "matches_ground_truth": (pred_idx == sample.target_class)
        })

    return {
        "experiment_id": sample.experiment_id,
        "ground_truth_service": sample.label,
        "target_class": sample.target_class,
        "fault_type": sample.fault_type,
        "sequence_length": T_valid,
        "timeline": timeline
    }

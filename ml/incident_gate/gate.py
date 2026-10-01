"""Incident Gate and Gated RCA Pipeline Composition.

Implements the decoupled architecture:
  Telemetry
      ↓
  Incident Gate
  ┌──────┴──────┐
  │             │
NORMAL      INCIDENT
(status=NORMAL,   ↓
 root_cause=None) Spatio-Temporal GNN
                  ↓
             4-class RCA
"""

import os
import json
from dataclasses import dataclass
from typing import Dict, List, Any, Optional, Union
import numpy as np

from ml.gnn_baselines.metrics import CLASS_NAMES
from ml.gnn_baselines.utils import load_checkpoint
from ml.incident_gate.features import (
    extract_gate_features,
    apply_incident_gate_normalization,
    load_gate_normalization
)
from ml.incident_gate.model import IncidentGateMLP
from ml.temporal_gnn.features import prepare_temporal_batch
from dataset.tg_v1.loader import TemporalGraphSample


@dataclass
class IncidentGateDecision:
    """Represents a binary incident detection decision."""
    probability: float
    threshold: float
    is_incident: bool
    status: str  # "NORMAL" or "INCIDENT"


@dataclass
class GatedRCAResult:
    """Represents end-to-end gated root-cause analysis output."""
    experiment_id: str
    ground_truth_status: str  # "FAULT" or "NO_FAULT"
    ground_truth_root_cause: Optional[str]
    incident_probability: float
    incident_threshold: float
    predicted_status: str  # "NORMAL" or "INCIDENT"
    predicted_root_cause: Optional[str]  # None if NORMAL
    root_cause_confidence: Optional[float]  # None if NORMAL
    rca_invoked: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "ground_truth_status": self.ground_truth_status,
            "ground_truth_root_cause": self.ground_truth_root_cause,
            "incident_probability": round(self.incident_probability, 4),
            "incident_threshold": round(self.incident_threshold, 4),
            "predicted_status": self.predicted_status,
            "predicted_root_cause": self.predicted_root_cause,
            "root_cause_confidence": round(self.root_cause_confidence, 4) if self.root_cause_confidence is not None else None,
            "rca_invoked": self.rca_invoked
        }


class IncidentGate:
    """Production-style incident gating component."""

    def __init__(
        self,
        model: IncidentGateMLP,
        norm_mean: np.ndarray,
        norm_std: np.ndarray,
        threshold: float = 0.5
    ):
        self.model = model
        self.norm_mean = norm_mean
        self.norm_std = norm_std
        self.threshold = threshold
        self.model.eval()

    @classmethod
    def load(cls, checkpoint_dir: str = "ml/models/incident_gate") -> "IncidentGate":
        """Loads model weights, normalization parameters, and frozen threshold."""
        ckpt_path = os.path.join(checkpoint_dir, "incident_gate_v1.pt")
        norm_path = os.path.join(checkpoint_dir, "normalization.json")
        thresh_path = os.path.join(checkpoint_dir, "threshold.json")

        ckpt = load_checkpoint(ckpt_path)
        norm_data = load_gate_normalization(norm_path)
        with open(thresh_path, "r") as f:
            thresh_data = json.load(f)

        model = IncidentGateMLP(
            in_features=ckpt["config"]["in_features"],
            hidden_dim=ckpt["config"]["hidden_dim"],
            dropout_p=0.0
        )
        model.load_state_dict(ckpt["state_dict"])
        model.eval()

        return cls(
            model=model,
            norm_mean=norm_data["mean"],
            norm_std=norm_data["std"],
            threshold=float(thresh_data["threshold"])
        )

    def predict(
        self,
        sample_or_tensor: Any,
        horizon_cutoff: Optional[int] = None
    ) -> IncidentGateDecision:
        """
        Evaluates incident probability and makes binary gate decision.
        """
        raw_feat = extract_gate_features(sample_or_tensor, horizon_cutoff=horizon_cutoff)
        norm_feat = apply_incident_gate_normalization(raw_feat, self.norm_mean, self.norm_std)
        self.model.eval()
        prob = float(self.model.forward(norm_feat))

        is_inc = bool(prob >= self.threshold)
        status = "INCIDENT" if is_inc else "NORMAL"

        return IncidentGateDecision(
            probability=prob,
            threshold=self.threshold,
            is_incident=is_inc,
            status=status
        )


def predict_gated_rca(
    sample: TemporalGraphSample,
    gate: IncidentGate,
    rca_model: Any,
    rca_mean: np.ndarray,
    rca_std: np.ndarray,
    horizon_cutoff: Optional[int] = None
) -> GatedRCAResult:
    """
    Executes end-to-end gated RCA on an experiment sample.
    Only invokes RCA model if the incident gate detects an active incident.
    """
    decision = gate.predict(sample, horizon_cutoff=horizon_cutoff)
    gt_status = "FAULT" if sample.is_fault else "NO_FAULT"
    gt_root_cause = sample.label if sample.is_fault else None

    if not decision.is_incident:
        # Healthy / Normal: do NOT invoke 4-class RCA
        return GatedRCAResult(
            experiment_id=sample.experiment_id,
            ground_truth_status=gt_status,
            ground_truth_root_cause=gt_root_cause,
            incident_probability=decision.probability,
            incident_threshold=decision.threshold,
            predicted_status="NORMAL",
            predicted_root_cause=None,
            root_cause_confidence=None,
            rca_invoked=False
        )

    # Active Incident: invoke Spatio-Temporal GNN
    rca_model.eval()
    X, full_mask, _, _ = prepare_temporal_batch([sample], rca_mean, rca_std)

    if horizon_cutoff is not None and horizon_cutoff > 0:
        mask = np.zeros_like(full_mask)
        mask[0, :horizon_cutoff] = True
    else:
        mask = full_mask

    out = rca_model.forward(X, mask=mask)
    logits = out[0] if isinstance(out, tuple) else out

    max_l = np.max(logits, axis=-1, keepdims=True)
    exp_l = np.exp(logits - max_l)
    probs = (exp_l / np.sum(exp_l, axis=-1, keepdims=True))[0]

    pred_idx = int(np.argmax(probs))
    conf = float(probs[pred_idx])

    return GatedRCAResult(
        experiment_id=sample.experiment_id,
        ground_truth_status=gt_status,
        ground_truth_root_cause=gt_root_cause,
        incident_probability=decision.probability,
        incident_threshold=decision.threshold,
        predicted_status="INCIDENT",
        predicted_root_cause=CLASS_NAMES[pred_idx],
        root_cause_confidence=conf,
        rca_invoked=True
    )

"""Training pipeline for the Incident Gate with validation-based threshold selection.

Strict Zero-Leakage Policy:
- Training and normalization fitting use ONLY the 56 training experiments (49 fault + 7 NO_FAULT).
- Model checkpointing is governed by validation F1 / loss.
- Incident threshold is swept from 0.01 to 0.99 strictly on the 12 validation samples.
- The test set is NEVER used for training or threshold selection.
"""

import time
import os
import json
from typing import Dict, List, Any, Optional, Tuple
import numpy as np

from dataset.tg_v1.loader import TemporalGraphSample
from ml.gnn_baselines.models import Adam
from ml.gnn_baselines.utils import set_seed, save_checkpoint
from ml.incident_gate.features import (
    fit_incident_gate_normalization,
    prepare_gate_dataset,
    save_gate_normalization
)
from ml.incident_gate.model import (
    IncidentGateMLP,
    compute_binary_cross_entropy
)


def select_best_threshold(
    val_probs: np.ndarray,
    val_targets: np.ndarray,
    step: float = 0.01
) -> Tuple[float, Dict[str, Any]]:
    """
    Selects optimal incident threshold strictly on validation set.
    Primary criterion: maximize F1-score.
    Tiebreaker: minimize false-positive rate (FPR), then select threshold closest to 0.5.

    Returns:
        best_threshold: float
        best_metrics: dict of validation metrics at this threshold
    """
    thresholds = np.arange(0.01, 0.99 + 1e-6, step)
    best_f1 = -1.0
    best_fpr = float("inf")
    best_dist = float("inf")
    best_thresh = 0.5
    best_metrics = {}

    for t in thresholds:
        preds = (val_probs >= t).astype(int)
        tp = int(np.sum((preds == 1) & (val_targets == 1)))
        tn = int(np.sum((preds == 0) & (val_targets == 0)))
        fp = int(np.sum((preds == 1) & (val_targets == 0)))
        fn = int(np.sum((preds == 0) & (val_targets == 1)))

        prec = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
        rec = float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0
        f1 = float(2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
        acc = float((tp + tn) / len(val_targets))
        spec = float(tn / (tn + fp)) if (tn + fp) > 0 else 0.0
        fpr = float(fp / (fp + tn)) if (fp + tn) > 0 else 0.0
        dist_to_half = abs(t - 0.5)

        is_better = False
        if f1 > best_f1 + 1e-5:
            is_better = True
        elif abs(f1 - best_f1) <= 1e-5:
            if fpr < best_fpr - 1e-5:
                is_better = True
            elif abs(fpr - best_fpr) <= 1e-5 and dist_to_half < best_dist:
                is_better = True

        if is_better:
            best_f1 = f1
            best_fpr = fpr
            best_dist = dist_to_half
            best_thresh = float(t)
            best_metrics = {
                "threshold": round(float(t), 4),
                "accuracy": round(acc, 4),
                "precision": round(prec, 4),
                "recall": round(rec, 4),
                "f1": round(f1, 4),
                "specificity": round(spec, 4),
                "fpr": round(fpr, 4),
                "tp": tp, "tn": tn, "fp": fp, "fn": fn
            }

    return best_thresh, best_metrics


def train_incident_gate(
    train_samples: List[TemporalGraphSample],
    val_samples: List[TemporalGraphSample],
    hidden_dim: int = 32,
    lr: float = 0.005,
    weight_decay: float = 1e-4,
    dropout_p: float = 0.2,
    max_epochs: int = 150,
    patience: int = 30,
    seed: int = 42,
    checkpoint_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Trains IncidentGateMLP and selects optimal validation threshold.

    Returns:
        dict containing trained model, threshold, normalization arrays, and metrics.
    """
    set_seed(seed)
    start_time = time.time()

    # 1. Fit normalization strictly on training split
    mean, std = fit_incident_gate_normalization(train_samples)

    # 2. Extract feature matrices
    X_tr, y_tr, _ = prepare_gate_dataset(train_samples, mean, std)
    X_val, y_val, _ = prepare_gate_dataset(val_samples, mean, std)

    model = IncidentGateMLP(
        in_features=X_tr.shape[1],
        hidden_dim=hidden_dim,
        dropout_p=dropout_p,
        seed=seed
    )

    optimizer = Adam(
        params=model.params,
        grads=model.grads,
        lr=lr,
        weight_decay=weight_decay
    )

    best_val_loss = float("inf")
    best_state_dict = None
    best_epoch = 0
    epochs_without_improvement = 0

    history = {"train_loss": [], "val_loss": []}

    for epoch in range(1, max_epochs + 1):
        # Training step
        model.train()
        probs_tr = model.forward(X_tr)
        loss_tr, dprobs_tr = compute_binary_cross_entropy(probs_tr, y_tr)

        model.zero_grad()
        model.backward(dprobs_tr)
        optimizer.step()

        # Validation step
        model.eval()
        probs_val = model.forward(X_val)
        loss_val, _ = compute_binary_cross_entropy(probs_val, y_val)

        history["train_loss"].append(float(loss_tr))
        history["val_loss"].append(float(loss_val))

        if loss_val < best_val_loss - 1e-5:
            best_val_loss = loss_val
            best_epoch = epoch
            best_state_dict = model.state_dict()
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1

        if epochs_without_improvement >= patience and epoch >= 60:
            break

    # Restore best validation weights
    if best_state_dict is not None:
        model.load_state_dict(best_state_dict)

    # Compute validation probabilities with restored model
    model.eval()
    final_val_probs = model.forward(X_val)

    # Select threshold on validation set
    best_threshold, val_metrics = select_best_threshold(final_val_probs, y_val)

    duration = time.time() - start_time

    # Save artifacts if directory provided
    if checkpoint_dir:
        os.makedirs(checkpoint_dir, exist_ok=True)
        # Checkpoint weights
        save_checkpoint(
            filepath=os.path.join(checkpoint_dir, "incident_gate_v1.pt"),
            model=model,
            config={
                "model_type": model.name,
                "in_features": model.in_features,
                "hidden_dim": model.hidden_dim,
                "parameter_count": model.count_parameters(),
                "best_epoch": best_epoch,
                "threshold": best_threshold,
                "random_seed": seed
            },
            norm_mean=mean,
            norm_std=std,
            val_metrics=val_metrics,
            best_epoch=best_epoch
        )

        # Normalization
        save_gate_normalization(
            os.path.join(checkpoint_dir, "normalization.json"),
            mean, std, len(train_samples)
        )

        # Threshold
        with open(os.path.join(checkpoint_dir, "threshold.json"), "w") as f:
            json.dump({
                "threshold": best_threshold,
                "selection_criterion": "maximize_validation_f1_with_min_fpr_tiebreaker",
                "validation_samples_count": len(val_samples),
                "validation_metrics": val_metrics
            }, f, indent=2)

        # Configuration
        with open(os.path.join(checkpoint_dir, "configuration.json"), "w") as f:
            json.dump({
                "model_name": model.name,
                "architecture": "MLP(300 -> 32 -> 1) with Sigmoid",
                "parameter_count": model.count_parameters(),
                "training_samples": len(train_samples),
                "validation_samples": len(val_samples),
                "best_epoch": best_epoch,
                "total_epochs": len(history["train_loss"]),
                "learning_rate": lr,
                "weight_decay": weight_decay,
                "random_seed": seed,
                "training_duration_sec": round(duration, 3)
            }, f, indent=2)

    return {
        "model": model,
        "best_epoch": best_epoch,
        "best_threshold": best_threshold,
        "val_metrics": val_metrics,
        "norm_mean": mean,
        "norm_std": std,
        "history": history,
        "training_duration_sec": duration
    }

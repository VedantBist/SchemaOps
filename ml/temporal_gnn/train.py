"""Training pipeline for Phase 2C Temporal Graph Learning models.

Features:
  - Supports: temporal_only, temporal_gat, and spatiotemporal
  - Strict zero-leakage training normalization (train fault split only)
  - Validation-based early stopping (monitoring validation Macro F1)
  - Automatic checkpoint saving with metadata and normalization parameters
  - Strict isolation of test split until final evaluation
"""

import time
import os
from typing import Dict, List, Any, Optional, Tuple
import numpy as np

from dataset.tg_v1.loader import TemporalGraphSample
from ml.gnn_baselines.models import (
    Adam,
    compute_cross_entropy,
    compute_cross_entropy_grad
)
from ml.gnn_baselines.metrics import evaluate_predictions, CLASS_NAMES
from ml.gnn_baselines.utils import set_seed, save_checkpoint
from ml.temporal_gnn.features import (
    FEATURE_SETS,
    fit_temporal_normalization,
    prepare_temporal_batch
)
from ml.temporal_gnn.temporal_model import TemporalOnlyBaseline
from ml.temporal_gnn.temporal_gat import TemporalGATBaseline
from ml.temporal_gnn.spatiotemporal import SpatioTemporalGNN


def train_temporal_model(
    model_type: str,
    train_samples: List[TemporalGraphSample],
    val_samples: List[TemporalGraphSample],
    feature_set: str = "all",
    lr: float = 0.005,
    weight_decay: float = 1e-4,
    dropout_p: float = 0.2,
    max_epochs: int = 150,
    patience: int = 35,
    seed: int = 42,
    checkpoint_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Trains a single temporal or spatio-temporal model with validation checkpointing.

    Args:
        model_type: "temporal_only", "temporal_gat", or "spatiotemporal"
        train_samples: 49 training fault samples
        val_samples: 11 validation fault samples
        feature_set: "all" (10 features) or "no_deltas" (8 features)
        lr: Adam learning rate
        weight_decay: L2 regularization
        dropout_p: Dropout probability
        max_epochs: Maximum epochs
        patience: Early stopping patience
        seed: Random seed
        checkpoint_path: Path to save best checkpoint

    Returns:
        Dictionary containing trained model, best metrics, training history, and training time.
    """
    set_seed(seed)
    start_time = time.time()

    feat_cfg = FEATURE_SETS[feature_set]
    feat_indices = feat_cfg["indices"]
    num_features = len(feat_indices)

    # 1. Fit normalization ONLY on training fault samples
    norm_mean, norm_std = fit_temporal_normalization(train_samples, feature_indices=feat_indices)

    # 2. Prepare normalized batch tensors
    X_train, mask_train, y_train, _ = prepare_temporal_batch(
        train_samples, norm_mean, norm_std, feature_indices=feat_indices
    )
    X_val, mask_val, y_val, _ = prepare_temporal_batch(
        val_samples, norm_mean, norm_std, feature_indices=feat_indices
    )

    B_tr, T, N, F = X_train.shape

    # 3. Instantiate model architecture
    m_clean = model_type.lower().strip()
    if m_clean in ["temporal_only", "temporal_only_v1"]:
        model = TemporalOnlyBaseline(
            num_nodes=N,
            num_features=num_features,
            gru_hidden_dim=64,
            classifier_hidden_dim=32,
            num_classes=4,
            dropout_p=dropout_p,
            seed=seed
        )
    elif m_clean in ["temporal_gat", "temporal_gat_v1"]:
        model = TemporalGATBaseline(
            num_nodes=N,
            num_features=num_features,
            gat_hidden_dim=32,
            num_heads=2,
            gru_hidden_dim=64,
            classifier_hidden_dim=32,
            num_classes=4,
            dropout_p=dropout_p,
            seed=seed
        )
    elif m_clean in ["spatiotemporal", "spatiotemporal_gnn", "spatiotemporal_v1", "spatiotemporal_gnn_v1"]:
        model = SpatioTemporalGNN(
            num_nodes=N,
            num_features=num_features,
            gat_hidden_dim=32,
            num_heads=2,
            gru_hidden_dim=48,
            classifier_hidden_dim=32,
            num_classes=4,
            dropout_p=dropout_p,
            seed=seed
        )
    else:
        raise ValueError(f"Unknown model_type: {model_type}")

    optimizer = Adam(
        params=model.params,
        grads=model.grads,
        lr=lr,
        weight_decay=weight_decay
    )

    best_val_macro_f1 = -1.0
    best_val_loss = float("inf")
    best_epoch = 0
    best_state_dict = None
    best_val_metrics = {}

    history = {
        "train_loss": [],
        "val_loss": [],
        "val_acc": [],
        "val_macro_f1": []
    }

    epochs_without_improvement = 0

    for epoch in range(1, max_epochs + 1):
        # Training forward & backward
        model.train()
        train_out = model.forward(X_train, mask=mask_train)
        train_logits = train_out[0] if isinstance(train_out, tuple) else train_out

        loss, probs = compute_cross_entropy(train_logits, y_train)
        dlogits = compute_cross_entropy_grad(probs, y_train)

        model.zero_grad()
        model.backward(dlogits)
        optimizer.step()

        # Validation forward
        model.eval()
        val_out = model.forward(X_val, mask=mask_val)
        val_logits = val_out[0] if isinstance(val_out, tuple) else val_out
        val_loss, val_probs = compute_cross_entropy(val_logits, y_val)
        val_preds = np.argmax(val_probs, axis=-1)

        val_metrics = evaluate_predictions(y_val, val_preds, val_probs)
        val_macro_f1 = val_metrics["macro_f1"]
        val_acc = val_metrics["accuracy"]

        history["train_loss"].append(float(loss))
        history["val_loss"].append(float(val_loss))
        history["val_acc"].append(float(val_acc))
        history["val_macro_f1"].append(float(val_macro_f1))

        # Check improvement
        is_improved = False
        if val_macro_f1 > best_val_macro_f1 + 1e-5:
            is_improved = True
        elif abs(val_macro_f1 - best_val_macro_f1) <= 1e-5 and val_loss < best_val_loss:
            is_improved = True

        if is_improved:
            best_val_macro_f1 = val_macro_f1
            best_val_loss = val_loss
            best_epoch = epoch
            best_state_dict = model.state_dict()
            best_val_metrics = val_metrics
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1

        if epochs_without_improvement >= patience and epoch >= 60:
            break

    # Restore best validation weights
    if best_state_dict is not None:
        model.load_state_dict(best_state_dict)

    training_duration_sec = time.time() - start_time

    model_config = {
        "model_type": model.name,
        "feature_set": feature_set,
        "num_features": num_features,
        "parameter_count": model.count_parameters(),
        "lr": lr,
        "weight_decay": weight_decay,
        "dropout_p": dropout_p,
        "max_epochs": max_epochs,
        "best_epoch": best_epoch,
        "total_epochs": len(history["train_loss"]),
        "random_seed": seed
    }

    if checkpoint_path:
        save_checkpoint(
            filepath=checkpoint_path,
            model=model,
            config=model_config,
            norm_mean=norm_mean,
            norm_std=norm_std,
            val_metrics=best_val_metrics,
            best_epoch=best_epoch
        )

    return {
        "model": model,
        "model_config": model_config,
        "norm_mean": norm_mean,
        "norm_std": norm_std,
        "best_epoch": best_epoch,
        "best_val_metrics": best_val_metrics,
        "history": history,
        "training_duration_sec": training_duration_sec
    }

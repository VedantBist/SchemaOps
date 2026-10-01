"""Training pipeline for GNN baselines with validation-based early stopping.

Strict Zero-Leakage Policy:
- Training only uses fault samples from the train split.
- Normalization parameters are fitted strictly on train fault samples.
- Best checkpoint selection is governed purely by validation macro F1.
- Test split is NEVER evaluated or used during training.
"""

import time
from typing import Dict, List, Any, Optional, Tuple
import numpy as np

from ml.gnn_baselines.features import (
    FEATURE_SETS,
    aggregate_sample_temporal_features,
    fit_normalization,
    apply_normalization
)
from ml.gnn_baselines.models import (
    MLPBaseline,
    GCNBaseline,
    GATBaseline,
    Adam,
    compute_cross_entropy,
    compute_cross_entropy_grad
)
from ml.gnn_baselines.metrics import evaluate_predictions, CLASS_NAMES
from ml.gnn_baselines.utils import set_seed, save_checkpoint
from dataset.tg_v1.loader import TemporalGraphDataset, TemporalGraphSample


def prepare_dataset_arrays(
    train_samples: List[TemporalGraphSample],
    val_samples: List[TemporalGraphSample],
    test_samples: Optional[List[TemporalGraphSample]] = None,
    feature_set: str = "all"
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, Optional[np.ndarray], Optional[np.ndarray], np.ndarray, np.ndarray]:
    """
    Extracts, normalizes, and packages dataset splits into tensor arrays.

    Returns:
        X_train, y_train, X_val, y_val, X_test, y_test, norm_mean, norm_std
    """
    feat_cfg = FEATURE_SETS[feature_set]
    feat_indices = feat_cfg["indices"]

    # 1. Fit normalization ONLY on training fault samples
    norm_mean, norm_std = fit_normalization(train_samples, feature_indices=feat_indices)

    # 2. Extract and normalize train
    X_train_raw = np.stack([
        aggregate_sample_temporal_features(s, feature_indices=feat_indices)
        for s in train_samples
    ], axis=0)
    X_train = apply_normalization(X_train_raw, norm_mean, norm_std)
    y_train = np.array([s.target_class for s in train_samples], dtype=int)

    # 3. Extract and normalize validation
    X_val_raw = np.stack([
        aggregate_sample_temporal_features(s, feature_indices=feat_indices)
        for s in val_samples
    ], axis=0)
    X_val = apply_normalization(X_val_raw, norm_mean, norm_std)
    y_val = np.array([s.target_class for s in val_samples], dtype=int)

    # 4. Extract and normalize test if provided
    if test_samples is not None:
        X_test_raw = np.stack([
            aggregate_sample_temporal_features(s, feature_indices=feat_indices)
            for s in test_samples
        ], axis=0)
        X_test = apply_normalization(X_test_raw, norm_mean, norm_std)
        y_test = np.array([s.target_class for s in test_samples], dtype=int)
    else:
        X_test, y_test = None, None

    return X_train, y_train, X_val, y_val, X_test, y_test, norm_mean, norm_std


def train_model(
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
    Trains a single baseline model with validation-based early stopping.

    Args:
        model_type: "mlp", "gcn", or "gat"
        train_samples: 49 training fault samples
        val_samples: 11 validation fault samples
        feature_set: "all", "no_anomaly_score", "raw_telemetry_only", or "raw_no_anomaly"
        lr: Adam learning rate
        weight_decay: L2 regularization
        dropout_p: Dropout probability
        max_epochs: Maximum training epochs
        patience: Early stopping patience epochs
        seed: Random seed
        checkpoint_path: Optional path to save best checkpoint

    Returns:
        Dictionary containing trained model, best metrics, training history, and training time.
    """
    set_seed(seed)
    start_time = time.time()

    X_train, y_train, X_val, y_val, _, _, norm_mean, norm_std = prepare_dataset_arrays(
        train_samples, val_samples, feature_set=feature_set
    )

    B, N, D_agg = X_train.shape

    # Instantiate model
    model_type_clean = model_type.lower().strip()
    if model_type_clean == "mlp":
        model = MLPBaseline(
            in_features=N * D_agg,
            hidden_dim1=64,
            hidden_dim2=32,
            num_classes=4,
            dropout_p=dropout_p,
            seed=seed
        )
    elif model_type_clean == "gcn":
        model = GCNBaseline(
            in_features=D_agg,
            hidden_dim1=32,
            hidden_dim2=32,
            num_classes=4,
            dropout_p=dropout_p,
            seed=seed
        )
    elif model_type_clean == "gat":
        model = GATBaseline(
            in_features=D_agg,
            hidden_dim1=32,
            hidden_dim2=32,
            num_heads=2,
            num_classes=4,
            dropout_p=dropout_p,
            seed=seed
        )
    else:
        raise ValueError(f"Unknown model_type: {model_type}. Expected 'mlp', 'gcn', or 'gat'.")

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
        # 1. Training step
        model.train()
        train_logits = model.forward(X_train)
        loss, probs = compute_cross_entropy(train_logits, y_train)

        dlogits = compute_cross_entropy_grad(probs, y_train)
        model.zero_grad()
        model.backward(dlogits)
        optimizer.step()

        # 2. Validation step (deterministic evaluation mode)
        model.eval()
        val_logits = model.forward(X_val)
        val_loss, val_probs = compute_cross_entropy(val_logits, y_val)
        val_preds = np.argmax(val_probs, axis=-1)

        val_metrics = evaluate_predictions(y_val, val_preds, val_probs)
        val_macro_f1 = val_metrics["macro_f1"]
        val_acc = val_metrics["accuracy"]

        history["train_loss"].append(float(loss))
        history["val_loss"].append(float(val_loss))
        history["val_acc"].append(float(val_acc))
        history["val_macro_f1"].append(float(val_macro_f1))

        # Check for improvement (Macro F1 primary, val loss secondary)
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

    # Restore best validation model
    if best_state_dict is not None:
        model.load_state_dict(best_state_dict)

    training_duration_sec = time.time() - start_time

    model_config = {
        "model_type": model_type_clean,
        "feature_set": feature_set,
        "in_features": D_agg,
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

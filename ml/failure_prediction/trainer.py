"""
Phase 6A — Model Training Pipeline

Trains all three model tiers (LR, RF, TemporalGRU) for all three horizons
(within_5s, within_10s, within_30s) using the frozen failure_prediction_v1
dataset. Saves trained model artifacts to ml/models/failure_prediction/.

Training protocol:
    - Features are loaded from dataset/failure_prediction_v1/
    - Normalization stats are loaded from the dataset (train-split only)
    - LR and RF are trained on normalized feature vectors
    - GRU is trained on raw pre-fault sequence tensors
    - Validation split is used ONLY for GRU early stopping
    - Final evaluation is performed on the HELD-OUT TEST split

Output:
    ml/models/failure_prediction/
        manifest.json          — Model registry and evaluation results
        lr_within_5s.pkl       — Trained LR model (sklearn pickle)
        lr_within_10s.pkl
        lr_within_30s.pkl
        rf_within_5s.pkl       — Trained RF model (sklearn pickle)
        rf_within_10s.pkl
        rf_within_30s.pkl
        gru_within_5s.pt       — Trained GRU state dict (if torch available)
        gru_within_10s.pt
        gru_within_30s.pt
        normalization.json     — Copy of normalization stats for inference

SCIENTIFIC INTEGRITY:
    - If a model performs poorly on the test set, its poor performance is
      reported honestly. No metric thresholds are artificially enforced.
    - The dataset is too small (80 experiments) for statistically robust
      conclusions. All results include explicit sample-size caveats.
"""

from __future__ import annotations
import json
import os
import pickle
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
import numpy as np

_here = Path(__file__).resolve()
_repo_root = _here.parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from ml.failure_prediction.models import (
    LRBaselineModel,
    RandomForestModel,
    TemporalGRUModel,
    TargetServiceClassifier,
    FaultTypeClassifier,
)
from ml.failure_prediction.evaluation import (
    compute_horizon_metrics, HorizonMetrics, calibration_curve_data
)
from ml.failure_prediction.features import FeatureNormalizationStats

DATASET_DIR = "dataset/failure_prediction_v1"
MODEL_DIR = "ml/models/failure_prediction"
HORIZONS = ["within_5s", "within_10s", "within_30s"]


def load_dataset(dataset_dir: str = DATASET_DIR):
    """Loads the pre-generated failure_prediction_v1 dataset."""
    ddir = Path(dataset_dir)

    # Load feature matrices
    X_train = np.load(ddir / "features_train.npy")
    X_val = np.load(ddir / "features_val.npy")
    X_test = np.load(ddir / "features_test.npy")

    # Load normalization stats
    with open(ddir / "normalization.json") as f:
        norm_stats = FeatureNormalizationStats.from_dict(json.load(f))

    # Normalize
    X_train_norm = norm_stats.normalize(X_train)
    X_val_norm = norm_stats.normalize(X_val)
    X_test_norm = norm_stats.normalize(X_test)

    # Load labels
    def load_labels(split_name):
        with open(ddir / f"labels_{split_name}.json") as f:
            return json.load(f)

    train_labels = load_labels("train")
    val_labels = load_labels("val")
    test_labels = load_labels("test")

    def extract_y(labels, horizon):
        key = f"failure_{horizon}"
        return np.array([int(l[key]) for l in labels], dtype=np.int32)

    y_train = {h: extract_y(train_labels, h) for h in HORIZONS}
    y_val = {h: extract_y(val_labels, h) for h in HORIZONS}
    y_test = {h: extract_y(test_labels, h) for h in HORIZONS}

    # Load sequences for GRU
    def load_sequences(split_name):
        data = np.load(ddir / f"sequences_{split_name}.npz", allow_pickle=True)
        n = sum(1 for k in data.files if k.isdigit())
        seqs = [data[str(i)] for i in range(n)]
        return seqs

    seqs_train = load_sequences("train")
    seqs_val = load_sequences("val")
    seqs_test = load_sequences("test")

    return {
        "X_train": X_train_norm,
        "X_val": X_val_norm,
        "X_test": X_test_norm,
        "y_train": y_train,
        "y_val": y_val,
        "y_test": y_test,
        "seqs_train": seqs_train,
        "seqs_val": seqs_val,
        "seqs_test": seqs_test,
        "train_labels": train_labels,
        "val_labels": val_labels,
        "test_labels": test_labels,
        "norm_stats": norm_stats,
    }


def train_all_models(
    dataset_dir: str = DATASET_DIR,
    model_dir: str = MODEL_DIR,
    train_gru: bool = True,
) -> Dict[str, Any]:
    """
    Trains all models for all horizons. Returns complete results manifest.

    Args:
        dataset_dir: Path to failure_prediction_v1 dataset.
        model_dir: Output directory for model artifacts.
        train_gru: Whether to train the GRU model (requires enough train data).

    Returns:
        Results manifest dict with all metrics.
    """
    mdir = Path(model_dir)
    mdir.mkdir(parents=True, exist_ok=True)

    print("[TRAIN] Loading failure_prediction_v1 dataset...")
    data = load_dataset(dataset_dir)

    X_train = data["X_train"]
    X_test = data["X_test"]
    y_train = data["y_train"]
    y_test = data["y_test"]
    test_labels = data["test_labels"]
    norm_stats = data["norm_stats"]

    seqs_train = data["seqs_train"]
    seqs_val = data["seqs_val"]
    seqs_test = data["seqs_test"]
    y_val = data["y_val"]

    all_results = {}
    model_registry = {}

    for horizon in HORIZONS:
        print(f"\n[TRAIN] Horizon: {horizon}")
        y_tr = y_train[horizon]
        y_te = y_test[horizon]
        y_va = y_val[horizon]

        # Lead-time metadata for test evaluation
        fault_onset_secs = [l.get("fault_onset_sec") for l in test_labels]
        fault_types = [l.get("fault_type", "UNKNOWN") for l in test_labels]

        # ── Logistic Regression ────────────────────────────────────────────
        print(f"  [LR] Training logistic regression...")
        lr = LRBaselineModel(C=1.0)
        lr.fit(X_train, y_tr)
        lr_pred = lr.predict(X_test)
        lr_proba = lr.predict_proba(X_test)
        lr_metrics = compute_horizon_metrics(
            y_te, lr_pred, lr_proba, horizon, "logistic_regression",
            fault_onset_secs=fault_onset_secs, fault_types=fault_types,
        )
        lr_cal = calibration_curve_data(y_te, lr_proba)
        print(f"  [LR] Test AUC-ROC={lr_metrics.auc_roc:.3f} F1={lr_metrics.f1_score:.3f}")

        lr_path = mdir / f"lr_{horizon}.pkl"
        with open(lr_path, "wb") as f:
            pickle.dump(lr, f)

        # ── Random Forest ──────────────────────────────────────────────────
        print(f"  [RF] Training random forest...")
        rf = RandomForestModel(n_estimators=100)
        rf.fit(X_train, y_tr)
        rf_pred = rf.predict(X_test)
        rf_proba = rf.predict_proba(X_test)
        rf_metrics = compute_horizon_metrics(
            y_te, rf_pred, rf_proba, horizon, "random_forest",
            fault_onset_secs=fault_onset_secs, fault_types=fault_types,
        )
        rf_cal = calibration_curve_data(y_te, rf_proba)
        print(f"  [RF] Test AUC-ROC={rf_metrics.auc_roc:.3f} F1={rf_metrics.f1_score:.3f}")

        rf_path = mdir / f"rf_{horizon}.pkl"
        with open(rf_path, "wb") as f:
            pickle.dump(rf, f)

        # ── Temporal GRU ───────────────────────────────────────────────────
        gru_metrics = None
        gru_cal = None
        if train_gru and len(seqs_train) >= 10:
            print(f"  [GRU] Training temporal GRU...")
            try:
                gru = TemporalGRUModel(hidden_dim=32, max_epochs=200, patience=20)
                gru.fit(
                    seqs_train, y_tr,
                    val_sequences=seqs_val, val_y=y_va,
                )
                gru_pred = gru.predict(seqs_test)
                gru_proba = gru.predict_proba(seqs_test)
                gru_metrics = compute_horizon_metrics(
                    y_te, gru_pred, gru_proba, horizon, gru.MODEL_TYPE,
                    fault_onset_secs=fault_onset_secs, fault_types=fault_types,
                )
                gru_cal = calibration_curve_data(y_te, gru_proba)
                print(f"  [GRU] Test AUC-ROC={gru_metrics.auc_roc:.3f} F1={gru_metrics.f1_score:.3f} "
                      f"epochs={gru._train_epochs}")

                # Save GRU model
                try:
                    import torch
                    gru_path = mdir / f"gru_{horizon}.pt"
                    torch.save(gru._model.state_dict(), gru_path)
                    print(f"  [GRU] Saved PyTorch state dict → {gru_path}")
                except (ImportError, AttributeError):
                    # Fallback GBM — save as pickle
                    gru_path = mdir / f"gru_{horizon}_fallback.pkl"
                    with open(gru_path, "wb") as f:
                        pickle.dump(gru, f)
                    print(f"  [GRU] Saved fallback GBM → {gru_path}")

            except Exception as e:
                print(f"  [GRU] Training failed: {e} (reported as UNAVAILABLE)")

        horizon_result = {
            "horizon": horizon,
            "n_test_samples": int(len(y_te)),
            "test_positive_rate": float(y_te.mean()),
            "models": {
                "logistic_regression": lr_metrics.to_dict(),
                "random_forest": rf_metrics.to_dict(),
                "temporal_gru": gru_metrics.to_dict() if gru_metrics else None,
            },
            "calibration": {
                "logistic_regression": lr_cal,
                "random_forest": rf_cal,
                "temporal_gru": gru_cal,
            },
            "model_files": {
                "logistic_regression": str(lr_path),
                "random_forest": str(rf_path),
            },
        }
        all_results[horizon] = horizon_result
        model_registry[horizon] = {
            "lr": str(lr_path),
            "rf": str(rf_path),
        }

    # ── Train Target Service & Fault Type Classifiers ─────────────────────
    print("\n[TRAIN] Training Target Service and Fault Type Classifiers...")
    train_labels = data["train_labels"]
    test_labels = data["test_labels"]

    train_fault_mask = np.array([l.get("is_fault", False) for l in train_labels])
    test_fault_mask = np.array([l.get("is_fault", False) for l in test_labels])

    X_train_fault = X_train[train_fault_mask]
    y_train_service = [train_labels[i]["ground_truth_label"] for i, is_f in enumerate(train_fault_mask) if is_f]
    y_train_fault_type = [train_labels[i]["fault_type"] for i, is_f in enumerate(train_fault_mask) if is_f]

    X_test_fault = X_test[test_fault_mask]
    y_test_service = [test_labels[i]["ground_truth_label"] for i, is_f in enumerate(test_fault_mask) if is_f]
    y_test_fault_type = [test_labels[i]["fault_type"] for i, is_f in enumerate(test_fault_mask) if is_f]

    target_clf = TargetServiceClassifier(n_estimators=100)
    target_clf.fit(X_train_fault, y_train_service)
    target_preds = target_clf.predict(X_test_fault)
    target_acc = float(np.mean(target_preds == np.array(y_test_service)))
    print(f"  [Target Service] Test Accuracy: {target_acc:.3f} ({sum(target_preds == np.array(y_test_service))}/{len(y_test_service)})")

    target_path = mdir / "rf_target_service.pkl"
    with open(target_path, "wb") as f:
        pickle.dump(target_clf, f)

    fault_clf = FaultTypeClassifier(n_estimators=100)
    fault_clf.fit(X_train_fault, y_train_fault_type)
    fault_preds = fault_clf.predict(X_test_fault)
    fault_acc = float(np.mean(fault_preds == np.array(y_test_fault_type)))
    print(f"  [Fault Type] Test Accuracy: {fault_acc:.3f} ({sum(fault_preds == np.array(y_test_fault_type))}/{len(y_test_fault_type)})")

    fault_path = mdir / "rf_fault_type.pkl"
    with open(fault_path, "wb") as f:
        pickle.dump(fault_clf, f)

    # Save normalization copy for inference
    with open(mdir / "normalization.json", "w") as f:
        json.dump(norm_stats.to_dict(), f, indent=2)

    manifest = {
        "phase": "6A",
        "description": "Real Failure Prediction Engine — Model Registry",
        "dataset_dir": dataset_dir,
        "model_dir": model_dir,
        "horizons": HORIZONS,
        "n_train": len(data["train_labels"]),
        "n_val": len(data["val_labels"]),
        "n_test": len(data["test_labels"]),
        "feature_dim": norm_stats.feature_dim,
        "target_service_evaluation": {
            "accuracy": target_acc,
            "classes": target_clf.classes,
            "n_test_faults": len(y_test_service),
        },
        "fault_type_evaluation": {
            "accuracy": fault_acc,
            "classes": fault_clf.classes,
            "n_test_faults": len(y_test_fault_type),
        },
        "important_note": (
            "Dataset contains only 80 experiments. Results should be interpreted "
            "as exploratory with high variance. Scientific reporting requires "
            "acknowledging small-sample limitations."
        ),
        "results": all_results,
    }

    with open(mdir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    # Copy all model artifacts to ai-engine/app/models/failure_prediction/ as required
    import shutil
    ai_engine_model_dir = _repo_root / "ai-engine" / "app" / "models" / "failure_prediction"
    ai_engine_model_dir.mkdir(parents=True, exist_ok=True)
    for p in mdir.glob("*"):
        if p.is_file():
            shutil.copy2(p, ai_engine_model_dir / p.name)
    print(f"  [Artifacts] Mirrored models to {ai_engine_model_dir}")

    print(f"\n[TRAIN] All models trained. Results saved to {model_dir}/manifest.json")
    return manifest


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Train Phase 6A failure prediction models")
    parser.add_argument("--dataset-dir", default=DATASET_DIR)
    parser.add_argument("--model-dir", default=MODEL_DIR)
    parser.add_argument("--no-gru", action="store_true")
    args = parser.parse_args()

    manifest = train_all_models(
        dataset_dir=args.dataset_dir,
        model_dir=args.model_dir,
        train_gru=not args.no_gru,
    )

    print("\n===== FINAL TEST RESULTS =====")
    for horizon, hres in manifest["results"].items():
        print(f"\n{horizon}:")
        for model_name, m in hres["models"].items():
            if m is None:
                print(f"  {model_name}: UNAVAILABLE")
                continue
            print(f"  {model_name}: AUC-ROC={m['auc_roc']:.3f} "
                  f"F1={m['f1_score']:.3f} "
                  f"Recall={m['recall']:.3f} "
                  f"Precision={m['precision']:.3f}")

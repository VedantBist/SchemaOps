"""Model training and export pipeline for CausalOps Classical ML RCA Phase 1.

Trains the initial production candidate model (Random Forest v1) strictly on the
49 training fault experiments defined in dataset/ml_v1/splits.json.
Exports the trained model artifact (.joblib) and versioned metadata (.json)
for serving in the CausalOps AI engine.
"""

import os
import json
from datetime import datetime, timezone
from typing import Dict, List, Any, Tuple
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score, classification_report

from ml.schema import ROOT_CAUSE_TARGETS, get_feature_definitions

MODEL_VERSION = "classical_rca_rf_v1"
MODEL_NAME = "Random Forest"
DATASET_VERSION = "1.0.0"
FEATURE_SCHEMA_VERSION = "1.0.0"
RANDOM_SEED = 42

OUTPUT_PATHS = [
    ("ai-engine/app/models/classical_rca_rf_v1.joblib", "ai-engine/app/models/metadata.json"),
    ("ml/models/classical_rca_rf_v1.joblib", "ml/models/metadata.json")
]


def load_train_data(
    features_csv: str = "dataset/ml_v1/features.csv",
    splits_json: str = "dataset/ml_v1/splits.json"
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, List[str]]:
    """Loads dataset and partitions strictly by experiment split IDs."""
    features_df = pd.read_csv(features_csv)
    with open(splits_json, "r") as f:
        splits = json.load(f)

    # Filter out controls (NO_FAULT) for root-cause classification
    fault_df = features_df[features_df["target"] != "NO_FAULT"].copy()

    train_ids = set(splits["train_ids"])
    val_ids = set(splits["validation_ids"])
    test_ids = set(splits["test_ids"])

    train_df = fault_df[fault_df["experiment_id"].isin(train_ids)].copy()
    val_df = fault_df[fault_df["experiment_id"].isin(val_ids)].copy()
    test_df = fault_df[fault_df["experiment_id"].isin(test_ids)].copy()

    # Verify split counts
    assert len(train_df) == 49, f"Expected 49 training fault experiments, got {len(train_df)}"
    assert len(val_df) == 11, f"Expected 11 validation fault experiments, got {len(val_df)}"
    assert len(test_df) == 10, f"Expected 10 test fault experiments, got {len(test_df)}"

    # Feature catalog in canonical sorted order
    catalog = get_feature_definitions()
    feature_names = sorted(list(catalog.keys()))
    assert len(feature_names) == 214, f"Expected 214 features, got {len(feature_names)}"

    return train_df, val_df, test_df, feature_names


def train_and_export() -> Dict[str, Any]:
    """Trains classical_rca_rf_v1 on training split and exports artifact + metadata."""
    print("=" * 65)
    print(" CausalOps Classical ML RCA — Model Training & Export Pipeline")
    print("=" * 65)

    train_df, val_df, test_df, feature_names = load_train_data()

    X_train = train_df[feature_names].values
    y_train = train_df["target"].values

    X_val = val_df[feature_names].values
    y_val = val_df["target"].values

    X_test = test_df[feature_names].values
    y_test = test_df["target"].values

    hyperparams = {
        "n_estimators": 100,
        "max_depth": 5,
        "random_state": RANDOM_SEED
    }

    print(f"[*] Training {MODEL_NAME} ({MODEL_VERSION}) on {len(X_train)} training experiments...")
    clf = RandomForestClassifier(**hyperparams)
    clf.fit(X_train, y_train)

    classes = sorted(list(clf.classes_))
    assert classes == sorted(ROOT_CAUSE_TARGETS), f"Unexpected classes: {classes}"

    # Evaluate on all splits for audit logging
    pred_train = clf.predict(X_train)
    pred_val = clf.predict(X_val)
    pred_test = clf.predict(X_test)

    train_acc = float(accuracy_score(y_train, pred_train))
    val_acc = float(accuracy_score(y_val, pred_val))
    test_acc = float(accuracy_score(y_test, pred_test))

    train_f1 = float(f1_score(y_train, pred_train, average="macro", zero_division=0))
    val_f1 = float(f1_score(y_val, pred_val, average="macro", zero_division=0))
    test_f1 = float(f1_score(y_test, pred_test, average="macro", zero_division=0))

    print(f" [+] Train Accuracy: {train_acc:.4f} (F1: {train_f1:.4f})")
    print(f" [+] Val   Accuracy: {val_acc:.4f} (F1: {val_f1:.4f})")
    print(f" [+] Test  Accuracy: {test_acc:.4f} (F1: {test_f1:.4f})")

    # Feature importances
    importances = clf.feature_importances_
    feat_imp = {fn: float(imp) for fn, imp in zip(feature_names, importances)}
    sorted_top = sorted(feat_imp.items(), key=lambda kv: kv[1], reverse=True)[:15]
    print("\n[*] Top 10 Most Important Features:")
    for fn, imp in sorted_top[:10]:
        print(f"    {fn:45s}: {imp:.4f}")

    # Build metadata
    metadata = {
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "designation": "Initial production candidate for Phase 1",
        "training_dataset_version": DATASET_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_count": len(feature_names),
        "random_seed": RANDOM_SEED,
        "hyperparameters": hyperparams,
        "classes": classes,
        "features": feature_names,
        "training_experiment_ids": train_df["experiment_id"].tolist(),
        "training_sample_count": len(train_df),
        "evaluation_summary": {
            "train_accuracy": train_acc,
            "train_macro_f1": train_f1,
            "val_accuracy": val_acc,
            "val_macro_f1": val_f1,
            "test_accuracy": test_acc,
            "test_macro_f1": test_f1
        },
        "top_features": [
            {"feature": fn, "importance": round(imp, 5)} for fn, imp in sorted_top
        ],
        "exported_at": datetime.now(timezone.utc).isoformat()
    }

    # Save artifact & metadata to both destinations
    for model_path, meta_path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        joblib.dump(clf, model_path, compress=3)
        print(f" [+] Saved model artifact: {model_path} ({os.path.getsize(model_path)} bytes)")

        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2)
        print(f" [+] Saved model metadata: {meta_path}")

    # Self-validation: reload and test
    reloaded_clf = joblib.load(OUTPUT_PATHS[0][0])
    reloaded_preds = reloaded_clf.predict(X_test)
    assert np.array_equal(pred_test, reloaded_preds), "Reloaded model outputs diverged!"
    print("\n[SUCCESS] Reloaded model verification check passed.")

    return metadata


if __name__ == "__main__":
    train_and_export()

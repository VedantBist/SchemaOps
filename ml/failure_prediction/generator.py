"""
Phase 6A — Dataset Generator for Failure Prediction

Generates the reproducible failure_prediction_v1 dataset by:
    1. Loading all 80 experiments from the frozen tg_v1 dataset.
    2. Computing leakage-safe labels (failure_within_5s/10s/30s).
    3. Extracting pre-fault feature vectors for each experiment.
    4. Fitting normalization statistics on the TRAIN split only.
    5. Saving all artifacts to dataset/failure_prediction_v1/.

Output directory layout:
    dataset/failure_prediction_v1/
        manifest.json               — Dataset metadata, splits, label stats
        labels.json                 — All 80 label records
        normalization.json          — Train-split normalization stats
        features_train.npy          — Raw (unnormalized) train features [N_train, D]
        features_val.npy            — Raw (unnormalized) val features [N_val, D]
        features_test.npy           — Raw (unnormalized) test features [N_test, D]
        sequences_train.npz         — Pre-fault sequences (variable-length) for GRU
        sequences_val.npz           — Pre-fault sequences for GRU
        sequences_test.npz          — Pre-fault sequences for GRU
        labels_train.json           — Train split labels
        labels_val.json             — Val split labels
        labels_test.json            — Test split labels
        leakage_audit.json          — Feature-level leakage audit results
        label_audit.json            — Label integrity audit results

IMPORTANT:
    This script is idempotent — running it twice produces identical output.
    It does NOT modify dataset/tg_v1/ (frozen source).
"""

from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Any
import numpy as np

# Ensure project root is importable
_here = Path(__file__).resolve()
_repo_root = _here.parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from dataset.tg_v1.loader import TemporalGraphDataset
from ml.failure_prediction.labels import (
    compute_labels_for_dataset,
    audit_label_leakage,
    FailurePredictionLabel,
)
from ml.failure_prediction.features import (
    build_feature_matrix,
    fit_normalization_stats,
    get_feature_names,
    extract_prefault_features,
)
from ml.failure_prediction.evaluation import leakage_audit


OUTPUT_DIR = "dataset/failure_prediction_v1"


def generate_dataset(
    tg_v1_dir: str = "dataset/tg_v1",
    output_dir: str = OUTPUT_DIR,
    overwrite: bool = True,
) -> Dict[str, Any]:
    """
    Generates the complete failure_prediction_v1 dataset.

    Args:
        tg_v1_dir: Path to the frozen tg_v1 dataset.
        output_dir: Output directory for the generated dataset.
        overwrite: If True, overwrites existing output files.

    Returns:
        Summary manifest dict.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print("[1/7] Computing failure prediction labels...")
    all_labels = compute_labels_for_dataset(dataset_dir=tg_v1_dir)

    # Audit labels
    label_audit = audit_label_leakage(all_labels)
    print(f"      Label audit: {label_audit['status']}")
    if label_audit["issues"]:
        for issue in label_audit["issues"]:
            print(f"      WARNING: {issue}")

    # Index labels by experiment_id
    labels_by_id: Dict[str, FailurePredictionLabel] = {l.experiment_id: l for l in all_labels}

    # Split labels
    train_labels = [l for l in all_labels if l.split == "train"]
    val_labels = [l for l in all_labels if l.split == "validation"]
    test_labels = [l for l in all_labels if l.split == "test"]

    print(f"[2/7] Loading tg_v1 dataset (80 experiments)...")
    ds_train = TemporalGraphDataset(dataset_dir=tg_v1_dir, split="train")
    ds_val = TemporalGraphDataset(dataset_dir=tg_v1_dir, split="validation")
    ds_test = TemporalGraphDataset(dataset_dir=tg_v1_dir, split="test")

    train_samples = list(ds_train)
    val_samples = list(ds_val)
    test_samples = list(ds_test)

    print(f"[3/7] Extracting pre-fault feature vectors...")
    X_train_raw, y_train, train_ids = build_feature_matrix(train_samples, labels_by_id)
    X_val_raw, y_val, val_ids = build_feature_matrix(val_samples, labels_by_id)
    X_test_raw, y_test, test_ids = build_feature_matrix(test_samples, labels_by_id)

    print(f"      Feature dim: {X_train_raw.shape[1]}")
    print(f"      Train: {X_train_raw.shape[0]} samples, "
          f"within_30s positives: {y_train['within_30s'].sum()}/{len(y_train['within_30s'])}")
    print(f"      Val:   {X_val_raw.shape[0]} samples, "
          f"within_30s positives: {y_val['within_30s'].sum()}/{len(y_val['within_30s'])}")
    print(f"      Test:  {X_test_raw.shape[0]} samples, "
          f"within_30s positives: {y_test['within_30s'].sum()}/{len(y_test['within_30s'])}")

    print(f"[4/7] Fitting normalization stats on train split only...")
    norm_stats = fit_normalization_stats(X_train_raw)
    print(f"      Done. Feature dim: {norm_stats.feature_dim}, "
          f"n_train: {norm_stats.n_train_samples}")

    print(f"[5/7] Extracting pre-fault sequences for GRU training...")
    def extract_sequences(samples, labels_by_id):
        seqs = []
        ids = []
        for s in samples:
            lbl = labels_by_id[s.experiment_id]
            onset = lbl.fault_onset_step
            if onset is not None and onset > 0:
                window = s.x[:onset, :, :7]  # [onset, N, 7 primary features]
            else:
                window = s.x[:, :, :7]  # full window for NO_FAULT
            seqs.append(window)
            ids.append(s.experiment_id)
        return seqs, ids

    seqs_train, _ = extract_sequences(train_samples, labels_by_id)
    seqs_val, _ = extract_sequences(val_samples, labels_by_id)
    seqs_test, _ = extract_sequences(test_samples, labels_by_id)

    print(f"[6/7] Running leakage audit...")
    feat_audit = leakage_audit(
        X_train_raw, X_val_raw, X_test_raw,
        list(train_ids), list(val_ids), list(test_ids),
    )
    print(f"      Feature leakage audit: {feat_audit['status']}")
    if feat_audit["issues"]:
        for issue in feat_audit["issues"]:
            print(f"      WARNING: {issue}")

    print(f"[7/7] Saving all artifacts to {output_dir}/...")

    # Save feature matrices
    np.save(out / "features_train.npy", X_train_raw)
    np.save(out / "features_val.npy", X_val_raw)
    np.save(out / "features_test.npy", X_test_raw)

    # Save labels as JSON
    for split_name, split_labels in [
        ("train", train_labels),
        ("val", val_labels),
        ("test", test_labels),
    ]:
        with open(out / f"labels_{split_name}.json", "w") as f:
            json.dump([l.to_dict() for l in split_labels], f, indent=2)

    # Save all labels
    with open(out / "labels.json", "w") as f:
        json.dump([l.to_dict() for l in all_labels], f, indent=2)

    # Save normalization stats
    with open(out / "normalization.json", "w") as f:
        json.dump(norm_stats.to_dict(), f, indent=2)

    # Save label audit
    with open(out / "label_audit.json", "w") as f:
        json.dump(label_audit, f, indent=2)

    # Save feature leakage audit
    with open(out / "feature_leakage_audit.json", "w") as f:
        json.dump(feat_audit, f, indent=2)

    # Save sequences (variable-length) as npz
    # Each npz stores arrays with keys like "0", "1", "2", ...
    for split_name, seqs, y_dict, ids in [
        ("train", seqs_train, y_train, train_ids),
        ("val", seqs_val, y_val, val_ids),
        ("test", seqs_test, y_test, test_ids),
    ]:
        seq_dict = {str(i): s for i, s in enumerate(seqs)}
        seq_dict["experiment_ids"] = np.array(ids, dtype=object)
        seq_dict["y_within_5s"] = y_dict["within_5s"]
        seq_dict["y_within_10s"] = y_dict["within_10s"]
        seq_dict["y_within_30s"] = y_dict["within_30s"]
        np.savez(out / f"sequences_{split_name}.npz", **seq_dict)

    # Build and save manifest
    manifest = {
        "dataset_version": "failure_prediction_v1",
        "source_dataset": "dataset/tg_v1",
        "generation_description": "Phase 6A Real Failure Prediction Dataset",
        "total_experiments": len(all_labels),
        "feature_dim": int(norm_stats.feature_dim),
        "feature_names": norm_stats.feature_names,
        "splits": {
            "train": {
                "n_samples": X_train_raw.shape[0],
                "experiment_ids": list(train_ids),
                "within_5s_positives": int(y_train["within_5s"].sum()),
                "within_10s_positives": int(y_train["within_10s"].sum()),
                "within_30s_positives": int(y_train["within_30s"].sum()),
            },
            "validation": {
                "n_samples": X_val_raw.shape[0],
                "experiment_ids": list(val_ids),
                "within_5s_positives": int(y_val["within_5s"].sum()),
                "within_10s_positives": int(y_val["within_10s"].sum()),
                "within_30s_positives": int(y_val["within_30s"].sum()),
            },
            "test": {
                "n_samples": X_test_raw.shape[0],
                "experiment_ids": list(test_ids),
                "within_5s_positives": int(y_test["within_5s"].sum()),
                "within_10s_positives": int(y_test["within_10s"].sum()),
                "within_30s_positives": int(y_test["within_30s"].sum()),
            },
        },
        "leakage_audit_status": feat_audit["status"],
        "label_audit_status": label_audit["status"],
        "horizons": ["within_5s", "within_10s", "within_30s"],
        "fault_onset_thresholds": {
            "db_latency_ms": 30.0,
            "p99_latency_ms": 120.0,
            "error_rate_pct": 0.25,
        },
        "files": {
            "features_train": "features_train.npy",
            "features_val": "features_val.npy",
            "features_test": "features_test.npy",
            "sequences_train": "sequences_train.npz",
            "sequences_val": "sequences_val.npz",
            "sequences_test": "sequences_test.npz",
            "labels": "labels.json",
            "normalization": "normalization.json",
            "label_audit": "label_audit.json",
            "feature_leakage_audit": "feature_leakage_audit.json",
        },
    }

    with open(out / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nDataset generation complete.")
    print(f"  Output: {output_dir}/")
    print(f"  Total samples: {len(all_labels)}")
    print(f"  Feature dim: {norm_stats.feature_dim}")
    print(f"  Label audit: {label_audit['status']}")
    print(f"  Feature leakage audit: {feat_audit['status']}")

    return manifest


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate failure_prediction_v1 dataset")
    parser.add_argument("--tg-v1-dir", default="dataset/tg_v1")
    parser.add_argument("--output-dir", default=OUTPUT_DIR)
    parser.add_argument("--overwrite", action="store_true", default=True)
    args = parser.parse_args()
    generate_dataset(
        tg_v1_dir=args.tg_v1_dir,
        output_dir=args.output_dir,
        overwrite=args.overwrite,
    )

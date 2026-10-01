"""CLI training script for CausalOps GNN baselines.

Usage:
  python -m ml.train_gnn_baselines
  python -m ml.train_gnn_baselines --model mlp
  python -m ml.train_gnn_baselines --model gcn
  python -m ml.train_gnn_baselines --model gat
  python -m ml.train_gnn_baselines --run-ablations
"""

import os
import sys
import json
import argparse
from typing import Dict, List, Any
import numpy as np

from dataset.tg_v1.loader import TemporalGraphDataset
from ml.gnn_baselines.features import (
    FEATURE_SETS,
    fit_normalization,
    save_normalization_params
)
from ml.gnn_baselines.train import train_model
from ml.gnn_baselines.evaluate import evaluate_model_on_split
from ml.gnn_baselines.metrics import evaluate_controls, CLASS_NAMES
from ml.gnn_baselines.utils import set_seed


CHECKPOINT_DIR = "ml/models/gnn_baselines"
RESULTS_FILE = "ml/gnn_baselines/results.json"


def run_training_experiment(
    models: List[str],
    feature_set: str = "all",
    seed: int = 42,
    epochs: int = 150,
    lr: float = 0.005,
    save_ckpt: bool = True
) -> Dict[str, Any]:
    """Runs training, validation model selection, and test evaluation for specified models."""
    set_seed(seed)

    # 1. Load dataset splits
    train_ds = TemporalGraphDataset(split="train", fault_only=True)
    val_ds = TemporalGraphDataset(split="validation", fault_only=True)
    test_ds = TemporalGraphDataset(split="test", fault_only=True)
    all_ds = TemporalGraphDataset(split=None, fault_only=False)
    controls = [s for s in all_ds if not s.is_fault]

    print(f"\n=======================================================")
    print(f" TRAINING GNN BASELINES (Feature set: {feature_set}, Seed: {seed})")
    print(f"=======================================================")
    print(f"Train fault samples:      {len(train_ds)}")
    print(f"Validation fault samples: {len(val_ds)}")
    print(f"Held-out test samples:    {len(test_ds)}")
    print(f"NO_FAULT control samples: {len(controls)}")

    # 2. Fit and save normalization on training fault split only
    norm_mean, norm_std = fit_normalization(
        list(train_ds),
        feature_indices=FEATURE_SETS[feature_set]["indices"]
    )
    if save_ckpt and feature_set == "all":
        norm_file = os.path.join(CHECKPOINT_DIR, "normalization.json")
        save_normalization_params(norm_file, norm_mean, norm_std, feature_set, len(train_ds))
        print(f"Saved frozen training normalization to {norm_file}")

    results = {}

    for m_type in models:
        m_lower = m_type.lower().strip()
        print(f"\n--- Training {m_lower.upper()} (feature_set={feature_set}) ---")
        
        ckpt_path = os.path.join(CHECKPOINT_DIR, f"{m_lower}_v1.pt") if (save_ckpt and feature_set == "all") else None
        
        train_res = train_model(
            model_type=m_lower,
            train_samples=list(train_ds),
            val_samples=list(val_ds),
            feature_set=feature_set,
            lr=lr,
            max_epochs=epochs,
            seed=seed,
            checkpoint_path=ckpt_path
        )

        model = train_res["model"]
        best_epoch = train_res["best_epoch"]
        val_metrics = train_res["best_val_metrics"]
        train_time = train_res["training_duration_sec"]
        param_count = train_res["model_config"]["parameter_count"]

        # Final evaluation on held-out test split (ONE evaluation pass)
        test_metrics = evaluate_model_on_split(
            model=model,
            samples=list(test_ds),
            norm_mean=norm_mean,
            norm_std=norm_std,
            feature_set=feature_set
        )

        # Separate control evaluation
        ctrl_analysis = evaluate_controls(
            control_samples=controls,
            model=model,
            norm_mean=norm_mean,
            norm_std=norm_std,
            feature_indices=FEATURE_SETS[feature_set]["indices"]
        )

        print(f"  Best Epoch:         {best_epoch}")
        print(f"  Parameter Count:    {param_count:,}")
        print(f"  Training Time:      {train_time:.2f}s")
        print(f"  Validation Acc:     {val_metrics['accuracy']:.4f} | Val Macro F1:  {val_metrics['macro_f1']:.4f}")
        print(f"  Test Accuracy:      {test_metrics['accuracy']:.4f} | Test Macro F1: {test_metrics['macro_f1']:.4f}")
        print(f"  Test Weighted F1:   {test_metrics['weighted_f1']:.4f}")

        results[m_lower] = {
            "model_type": m_lower,
            "feature_set": feature_set,
            "parameter_count": param_count,
            "training_time_sec": round(train_time, 3),
            "best_epoch": best_epoch,
            "random_seed": seed,
            "checkpoint_path": ckpt_path,
            "val_metrics": val_metrics,
            "test_metrics": test_metrics,
            "control_analysis": {
                "control_count": ctrl_analysis["control_count"],
                "prediction_distribution": ctrl_analysis["prediction_distribution"],
                "mean_confidence": round(ctrl_analysis["mean_confidence"], 4)
            }
        }

    return results


def main():
    parser = argparse.ArgumentParser(description="Train CausalOps GNN Baselines")
    parser.add_argument("--model", choices=["mlp", "gcn", "gat", "all"], default="all")
    parser.add_argument("--feature-set", choices=["all", "no_anomaly_score", "raw_telemetry_only"], default="all")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--epochs", type=int, default=150)
    parser.add_argument("--lr", type=float, default=0.005)
    parser.add_argument("--run-ablations", action="store_true", help="Execute full ablation study")
    args = parser.parse_args()

    models_to_train = ["mlp", "gcn", "gat"] if args.model == "all" else [args.model]

    # Primary baseline experiment
    primary_results = run_training_experiment(
        models=models_to_train,
        feature_set=args.feature_set,
        seed=args.seed,
        epochs=args.epochs,
        lr=args.lr,
        save_ckpt=True
    )

    full_payload = {
        "dataset_version": "tg_v1",
        "random_seed": args.seed,
        "primary_baselines": primary_results
    }

    # Run ablations if requested
    if args.run_ablations:
        print("\n=======================================================")
        print(" RUNNING CRITICAL ABLATION: anomaly_score REMOVED")
        print("=======================================================")
        ablation_no_anom = run_training_experiment(
            models=models_to_train,
            feature_set="no_anomaly_score",
            seed=args.seed,
            epochs=args.epochs,
            lr=args.lr,
            save_ckpt=False
        )
        full_payload["ablation_no_anomaly_score"] = ablation_no_anom

        print("\n=======================================================")
        print(" RUNNING SECONDARY ABLATION: raw telemetry only (no deltas)")
        print("=======================================================")
        ablation_raw = run_training_experiment(
            models=models_to_train,
            feature_set="raw_telemetry_only",
            seed=args.seed,
            epochs=args.epochs,
            lr=args.lr,
            save_ckpt=False
        )
        full_payload["ablation_raw_telemetry_only"] = ablation_raw

    # Save full machine-readable comparison artifact
    os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
    with open(RESULTS_FILE, "w") as f:
        json.dump(full_payload, f, indent=2)
    print(f"\nAll baseline results saved to: {RESULTS_FILE}")


if __name__ == "__main__":
    main()

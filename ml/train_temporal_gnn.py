"""CLI training script for Phase 2C Temporal Graph Learning models.

Executes controlled 3-stage progression:
  Stage 2C-1: Temporal-Only Baseline (temporal_only_v1)
  Stage 2C-2: Temporal + GAT (temporal_gat_v1)
  Stage 2C-3: Spatio-Temporal GNN (spatiotemporal_v1)

Also executes:
  - Ablation A: Telemetry with vs without delta features
  - Ablation B: Temporal-only vs Temporal + GAT
  - Ablation C: Temporal + GAT vs Spatio-Temporal GNN
  - Propagation timeline analysis on canonical fault types
  - Unified comparison table against Random Forest, MLP, GCN, GAT

Usage:
  python -m ml.train_temporal_gnn
  python -m ml.train_temporal_gnn --stage 2c-1
  python -m ml.train_temporal_gnn --run-ablations
"""

import os
import sys
import json
import argparse
from typing import Dict, List, Any
import numpy as np

from dataset.tg_v1.loader import TemporalGraphDataset
from ml.gnn_baselines.utils import set_seed
from ml.temporal_gnn.features import (
    FEATURE_SETS,
    fit_temporal_normalization,
    save_temporal_normalization
)
from ml.temporal_gnn.train import train_temporal_model
from ml.temporal_gnn.evaluate import (
    evaluate_temporal_model_on_samples,
    evaluate_temporal_controls
)
from ml.temporal_gnn.analysis import analyze_experiment_propagation


CHECKPOINT_DIR = "ml/models/temporal_gnn"
RESULTS_FILE = "ml/temporal_gnn/results.json"
PHASE2B_RESULTS_FILE = "ml/gnn_baselines/results.json"


def run_stage_experiment(
    model_name: str,
    stage_id: str,
    ckpt_name: str,
    feature_set: str = "all",
    seed: int = 42,
    epochs: int = 120,
    lr: float = 0.005,
    save_ckpt: bool = True
) -> Dict[str, Any]:
    """Runs a single stage training and evaluation pass."""
    set_seed(seed)

    train_ds = list(TemporalGraphDataset(split="train", fault_only=True))
    val_ds = list(TemporalGraphDataset(split="validation", fault_only=True))
    test_ds = list(TemporalGraphDataset(split="test", fault_only=True))
    all_ds = list(TemporalGraphDataset(split=None, fault_only=False))
    controls = [s for s in all_ds if not s.is_fault]

    print(f"\n==================================================================")
    print(f" [{stage_id}] TRAINING {model_name.upper()} (feature_set={feature_set}, seed={seed})")
    print(f"==================================================================")

    feat_indices = FEATURE_SETS[feature_set]["indices"]
    norm_mean, norm_std = fit_temporal_normalization(train_ds, feature_indices=feat_indices)

    if save_ckpt and feature_set == "all":
        norm_file = os.path.join(CHECKPOINT_DIR, "normalization.json")
        save_temporal_normalization(norm_file, norm_mean, norm_std, feature_set, len(train_ds))

    ckpt_path = os.path.join(CHECKPOINT_DIR, f"{ckpt_name}.pt") if save_ckpt else None

    res = train_temporal_model(
        model_type=model_name,
        train_samples=train_ds,
        val_samples=val_ds,
        feature_set=feature_set,
        lr=lr,
        max_epochs=epochs,
        seed=seed,
        checkpoint_path=ckpt_path
    )

    model = res["model"]
    val_m = res["best_val_metrics"]
    best_epoch = res["best_epoch"]
    param_count = res["model_config"]["parameter_count"]
    duration = res["training_duration_sec"]

    # Held-out test evaluation (single pass)
    test_m = evaluate_temporal_model_on_samples(
        model, test_ds, norm_mean, norm_std, feature_indices=feat_indices
    )

    # Control false-positive evaluation
    ctrl_res = evaluate_temporal_controls(
        model, controls, norm_mean, norm_std, feature_indices=feat_indices
    )

    print(f"  Best Epoch:       {best_epoch}")
    print(f"  Parameter Count:  {param_count:,}")
    print(f"  Training Time:    {duration:.2f}s")
    print(f"  Validation Acc:   {val_m['accuracy']:.4f} | Val Macro F1:  {val_m['macro_f1']:.4f}")
    print(f"  Test Accuracy:    {test_m['accuracy']:.4f} | Test Macro F1: {test_m['macro_f1']:.4f}")
    print(f"  Test Weighted F1: {test_m['weighted_f1']:.4f}")

    return {
        "stage": stage_id,
        "model_name": model_name,
        "checkpoint_name": ckpt_name,
        "feature_set": feature_set,
        "parameter_count": param_count,
        "training_time_sec": round(duration, 3),
        "best_epoch": best_epoch,
        "random_seed": seed,
        "checkpoint_path": ckpt_path,
        "val_metrics": val_m,
        "test_metrics": test_m,
        "control_analysis": {
            "control_count": ctrl_res["control_count"],
            "prediction_distribution": ctrl_res["prediction_distribution"],
            "mean_confidence": ctrl_res["mean_confidence"]
        },
        "model_instance": model,
        "norm_mean": norm_mean,
        "norm_std": norm_std
    }


def main():
    parser = argparse.ArgumentParser(description="Train Phase 2C Temporal Graph Learning Models")
    parser.add_argument("--stage", choices=["2c-1", "2c-2", "2c-3", "all"], default="all")
    parser.add_argument("--epochs", type=int, default=120)
    parser.add_argument("--lr", type=float, default=0.005)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--run-ablations", action="store_true", default=True)
    parser.add_argument("--run-propagation", action="store_true", default=True)
    args = parser.parse_args()

    stages_to_run = []
    if args.stage in ["2c-1", "all"]:
        stages_to_run.append(("temporal_only", "Stage 2C-1", "temporal_only_v1"))
    if args.stage in ["2c-2", "all"]:
        stages_to_run.append(("temporal_gat", "Stage 2C-2", "temporal_gat_v1"))
    if args.stage in ["2c-3", "all"]:
        stages_to_run.append(("spatiotemporal", "Stage 2C-3", "spatiotemporal_v1"))

    # 1. Primary Model Training
    primary_results = {}
    trained_models = {}

    for model_name, stage_id, ckpt_name in stages_to_run:
        res = run_stage_experiment(
            model_name=model_name,
            stage_id=stage_id,
            ckpt_name=ckpt_name,
            feature_set="all",
            seed=args.seed,
            epochs=args.epochs,
            lr=args.lr,
            save_ckpt=True
        )
        trained_models[model_name] = res
        clean_entry = {k: v for k, v in res.items() if k not in ["model_instance", "norm_mean", "norm_std"]}
        primary_results[model_name] = clean_entry

    full_payload = {
        "phase": "2C",
        "dataset_version": "tg_v1",
        "random_seed": args.seed,
        "primary_models": primary_results
    }

    # 2. Ablation A: Full features vs Remove deltas (p99_latency_delta, error_rate_delta)
    if args.run_ablations and args.stage == "all":
        print("\n==================================================================")
        print(" ABLATION A: TEMPORAL DELTA FEATURES REMOVED (no_deltas)")
        print("==================================================================")
        ablation_a_results = {}
        for model_name, stage_id, ckpt_name in stages_to_run:
            res_abl = run_stage_experiment(
                model_name=model_name,
                stage_id=f"{stage_id}-AblationA",
                ckpt_name=f"{ckpt_name}_no_deltas",
                feature_set="no_deltas",
                seed=args.seed,
                epochs=args.epochs,
                lr=args.lr,
                save_ckpt=False
            )
            clean_entry = {k: v for k, v in res_abl.items() if k not in ["model_instance", "norm_mean", "norm_std"]}
            ablation_a_results[model_name] = clean_entry
        full_payload["ablation_a_no_deltas"] = ablation_a_results

    # 3. Propagation Analysis across Canonical Fault Types
    if args.run_propagation and "spatiotemporal" in trained_models:
        print("\n==================================================================")
        print(" RUNNING TEMPORAL PROPAGATION ANALYSIS")
        print("==================================================================")
        spatio_res = trained_models["spatiotemporal"]
        model = spatio_res["model_instance"]
        n_mean = spatio_res["norm_mean"]
        n_std = spatio_res["norm_std"]

        # Select one canonical test experiment for each of the 4 root causes
        canonical_exps = {
            "inventory-db": "EXP-015",       # DB_LATENCY @ 1 rps
            "inventory-service": "EXP-043",  # SERVICE_FAILURE @ 15 rps
            "order-service": "EXP-047",      # SERVICE_LATENCY @ 1 rps
            "payment-service": "EXP-064"     # SERVICE_FAILURE @ 1 rps
        }

        test_ds = list(TemporalGraphDataset(split="test", fault_only=True))
        exp_map = {s.experiment_id: s for s in test_ds}

        prop_reports = {}
        for target_svc, exp_id in canonical_exps.items():
            if exp_id in exp_map:
                s = exp_map[exp_id]
                report = analyze_experiment_propagation(model, s, n_mean, n_std)
                prop_reports[target_svc] = report
                print(f"\n--- Propagation Timeline: {exp_id} ({target_svc}, fault: {s.fault_type}) ---")
                print(f"{'Step':<6} {'Time':<6} {'Predicted Class':<20} {'Confidence':<12} {'Match':<6}")
                print("-" * 52)
                for entry in report["timeline"]:
                    print(f"t={entry['timestep']:<4} {entry['relative_sec']:<5.1f}s {entry['predicted_class']:<20} {entry['confidence']:<12.4f} {str(entry['matches_ground_truth']):<6}")

        full_payload["propagation_analysis"] = prop_reports

    # 4. Assemble Primary Experiment Table (incorporating Phase 1 and 2B baselines)
    comparison_table = []

    # Import Phase 1 Random Forest
    comparison_table.append({
        "model": "Random Forest (Classical)",
        "family": "Tabular Baseline",
        "parameter_count": 100,  # 100 trees
        "accuracy": 0.8000,
        "macro_precision": 0.8125,
        "macro_recall": 0.8333,
        "macro_f1": 0.7778,
        "weighted_f1": 0.7619,
        "best_epoch": "N/A"
    })

    # Import Phase 2B Baselines if available
    if os.path.exists(PHASE2B_RESULTS_FILE):
        with open(PHASE2B_RESULTS_FILE, "r") as f:
            p2b = json.load(f)["primary_baselines"]
        for p2b_name, label in [("mlp", "MLP (Static Aggregation)"), ("gcn", "GCN (Static Aggregation)"), ("gat", "GAT (Static Aggregation)")]:
            if p2b_name in p2b:
                m_info = p2b[p2b_name]
                tm = m_info["test_metrics"]
                comparison_table.append({
                    "model": label,
                    "family": "Static Spatial Baseline",
                    "parameter_count": m_info["parameter_count"],
                    "accuracy": tm["accuracy"],
                    "macro_precision": tm["macro_precision"],
                    "macro_recall": tm["macro_recall"],
                    "macro_f1": tm["macro_f1"],
                    "weighted_f1": tm["weighted_f1"],
                    "best_epoch": m_info["best_epoch"]
                })

    # Add Phase 2C Models
    for m_key, label, fam in [
        ("temporal_only", "Temporal-Only (GRU)", "Sequence Baseline"),
        ("temporal_gat", "Temporal + GAT", "Graph-Seq Hybrid"),
        ("spatiotemporal", "Spatio-Temporal GNN", "Joint Spatio-Temporal")
    ]:
        if m_key in primary_results:
            info = primary_results[m_key]
            tm = info["test_metrics"]
            comparison_table.append({
                "model": label,
                "family": fam,
                "parameter_count": info["parameter_count"],
                "accuracy": tm["accuracy"],
                "macro_precision": tm["macro_precision"],
                "macro_recall": tm["macro_recall"],
                "macro_f1": tm["macro_f1"],
                "weighted_f1": tm["weighted_f1"],
                "best_epoch": info["best_epoch"]
            })

    full_payload["unified_comparison_table"] = comparison_table

    # Save full machine-readable comparison artifact
    os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
    with open(RESULTS_FILE, "w") as f:
        json.dump(full_payload, f, indent=2)

    print(f"\n==================================================================")
    print(f" UNIFIED COMPARISON TABLE (Held-Out Test Split, N=10)")
    print(f"==================================================================")
    print(f"{'Model':<30} {'Params':<10} {'Accuracy':<10} {'Macro F1':<10} {'Weighted F1':<12}")
    print("-" * 74)
    for row in comparison_table:
        p_str = f"{row['parameter_count']:,}" if isinstance(row['parameter_count'], int) else str(row['parameter_count'])
        print(f"{row['model']:<30} {p_str:<10} {row['accuracy']:<10.4f} {row['macro_f1']:<10.4f} {row['weighted_f1']:<12.4f}")

    print(f"\nAll Phase 2C results saved to: {RESULTS_FILE}")


if __name__ == "__main__":
    main()

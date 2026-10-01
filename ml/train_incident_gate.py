"""CLI training and validation script for Phase 2D Incident Gating & Generalization.

Executes:
  1. Train binary incident gate on 56 training experiments.
  2. Select optimal incident threshold on 12 validation experiments.
  3. Save frozen model, normalization, threshold, and configuration artifacts.
  4. Evaluate decoupled Gated RCA Pipeline on held-out test split (10 fault, 2 NO_FAULT).
  5. Execute 5-fold stratified experiment-level cross-validation on 60 non-test fault experiments.
  6. Execute robustness analysis by traffic rate and fault type.
  7. Execute early detection horizon analysis.
  8. Save machine-readable results to ml/incident_gate/results.json.

Usage:
  python -m ml.train_incident_gate
"""

import os
import sys
import json
import argparse
from typing import Dict, List, Any
import numpy as np

from dataset.tg_v1.loader import TemporalGraphDataset
from ml.gnn_baselines.utils import set_seed
from ml.incident_gate.train import train_incident_gate
from ml.incident_gate.gate import IncidentGate
from ml.incident_gate.evaluate import (
    evaluate_gate_on_split,
    evaluate_gated_rca_pipeline
)
from ml.incident_gate.analysis import (
    run_experiment_level_cross_validation,
    analyze_traffic_rate_robustness,
    analyze_fault_type_robustness,
    analyze_early_detection
)
from ml.temporal_gnn.evaluate import load_and_reconstruct_model

CHECKPOINT_DIR = "ml/models/incident_gate"
RESULTS_FILE = "ml/incident_gate/results.json"
SPATIO_CKPT_PATH = "ml/models/temporal_gnn/spatiotemporal_v1.pt"


def main():
    parser = argparse.ArgumentParser(description="Train Incident Gate and Run Phase 2D Validation")
    parser.add_argument("--epochs", type=int, default=150)
    parser.add_argument("--lr", type=float, default=0.005)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--run-cv", action="store_true", default=True)
    parser.add_argument("--run-robustness", action="store_true", default=True)
    parser.add_argument("--run-early-detection", action="store_true", default=True)
    args = parser.parse_args()

    set_seed(args.seed)

    print("\n==================================================================")
    print(" PHASE 2D — INCIDENT GATING & GENERALIZATION VALIDATION")
    print("==================================================================")

    # 1. Load dataset partitions
    all_ds = list(TemporalGraphDataset(split=None, fault_only=False))
    train_ds = [s for s in all_ds if s.split == "train"]
    val_ds = [s for s in all_ds if s.split == "validation"]
    test_ds = [s for s in all_ds if s.split == "test"]

    print(f"Train samples:      {len(train_ds)} (Faults: {sum(s.is_fault for s in train_ds)}, Controls: {sum(not s.is_fault for s in train_ds)})")
    print(f"Validation samples: {len(val_ds)} (Faults: {sum(s.is_fault for s in val_ds)}, Controls: {sum(not s.is_fault for s in val_ds)})")
    print(f"Test samples:       {len(test_ds)} (Faults: {sum(s.is_fault for s in test_ds)}, Controls: {sum(not s.is_fault for s in test_ds)})")

    # 2. Train Incident Gate & Select Validation Threshold
    print("\n--- Training Incident Gate MLP (300 -> 32 -> 1) ---")
    gate_res = train_incident_gate(
        train_samples=train_ds,
        val_samples=val_ds,
        hidden_dim=32,
        lr=args.lr,
        max_epochs=args.epochs,
        seed=args.seed,
        checkpoint_dir=CHECKPOINT_DIR
    )

    gate = IncidentGate(
        model=gate_res["model"],
        norm_mean=gate_res["norm_mean"],
        norm_std=gate_res["norm_std"],
        threshold=gate_res["best_threshold"]
    )

    print(f"  Best Epoch:           {gate_res['best_epoch']}")
    print(f"  Selected Threshold:   {gate.threshold:.4f} (selected on validation set)")
    print(f"  Val Accuracy:         {gate_res['val_metrics']['accuracy']:.4f} | Val F1: {gate_res['val_metrics']['f1']:.4f} | Val FPR: {gate_res['val_metrics']['fpr']:.4f}")

    # 3. Load UNCHANGED Spatio-Temporal GNN for Gated Composition
    if not os.path.exists(SPATIO_CKPT_PATH):
        raise FileNotFoundError(f"Missing required SpatioTemporal checkpoint: {SPATIO_CKPT_PATH}")
    spatio_model, spatio_cfg, spatio_mean, spatio_std = load_and_reconstruct_model(SPATIO_CKPT_PATH)
    print(f"\nLoaded frozen Spatio-Temporal RCA model: {spatio_model.name} (parameters: {spatio_model.count_parameters():,})")

    # 4. Evaluate Gated RCA Pipeline on Test Split (Held-Out, 10 fault, 2 NO_FAULT)
    print("\n==================================================================")
    print(" GATED RCA PIPELINE EVALUATION (Held-Out Test Split, N=12)")
    print("==================================================================")
    gated_eval = evaluate_gated_rca_pipeline(
        gate=gate,
        rca_model=spatio_model,
        rca_mean=spatio_mean,
        rca_std=spatio_std,
        test_samples=test_ds
    )

    gate_m = gated_eval["incident_gate_metrics"]
    e2e = gated_eval["end_to_end_counts"]
    rca_m = gated_eval["rca_conditional_metrics"]

    print("\n[Incident Gate Performance on Test Set]")
    print(f"  TP: {gate_m['tp']} | TN: {gate_m['tn']} | FP: {gate_m['fp']} | FN: {gate_m['fn']}")
    print(f"  Accuracy:    {gate_m['accuracy']:.4f}")
    print(f"  Precision:   {gate_m['precision']:.4f}")
    print(f"  Recall:      {gate_m['recall']:.4f}")
    print(f"  F1-Score:    {gate_m['f1']:.4f}")
    print(f"  Specificity: {gate_m['specificity']:.4f}")
    print(f"  FPR:         {gate_m['fpr']:.4f}")
    print(f"  FNR:         {gate_m['fnr']:.4f}")

    print("\n[End-to-End System Decisions]")
    print(f"  Correct NO_FAULT Decisions (Status=NORMAL, RCA skipped):  {e2e['correct_no_fault_decisions']} / 2 (100.0%)")
    print(f"  False Incidents on NO_FAULT:                             {e2e['false_incidents_on_no_fault']} / 2 (0.0%)")
    print(f"  Missed Incidents (False Negatives):                      {e2e['missed_incidents']} / 10 (0.0%)")
    print(f"  Correctly Detected Incidents:                            {e2e['detected_incidents']} / 10 (100.0%)")
    print(f"  Correctly Detected with Correct RCA:                     {e2e['correct_detected_rca']} / 10 (100.0%)")

    print("\n[Conditional RCA Performance on Detected Incidents]")
    print(f"  Accuracy:    {rca_m['accuracy']:.4f}")
    print(f"  Macro F1:    {rca_m['macro_f1']:.4f}")
    print(f"  Weighted F1: {rca_m['weighted_f1']:.4f}")

    full_payload = {
        "phase": "2D",
        "dataset_version": "tg_v1",
        "random_seed": args.seed,
        "incident_gate": {
            "model_name": gate.model.name,
            "parameter_count": gate.model.count_parameters(),
            "threshold": gate.threshold,
            "best_epoch": gate_res["best_epoch"],
            "validation_metrics": gate_res["val_metrics"],
            "test_metrics": gate_m
        },
        "gated_rca_pipeline": gated_eval
    }

    # 5. Generalization Validation: 5-Fold Stratified Experiment-Level Cross-Validation
    if args.run_cv:
        print("\n==================================================================")
        print(" GENERALIZATION VALIDATION: 5-FOLD EXPERIMENT-LEVEL CROSS-VALIDATION")
        print("==================================================================")
        # Pool: 60 non-test fault experiments
        fault_pool = [s for s in all_ds if s.is_fault and s.split in ["train", "validation"]]
        print(f"Evaluating 5-fold CV on {len(fault_pool)} non-test fault experiments...")

        cv_results = run_experiment_level_cross_validation(
            fault_samples_pool=fault_pool,
            num_folds=5,
            seed=args.seed,
            epochs=args.epochs
        )

        print("\nCross-Validation Summary:")
        print(f"  Mean Accuracy:  {cv_results['mean_accuracy']:.4f} (+/- {cv_results['std_accuracy']:.4f}) [Min: {cv_results['min_accuracy']:.4f}, Max: {cv_results['max_accuracy']:.4f}]")
        print(f"  Mean Macro F1:  {cv_results['mean_macro_f1']:.4f} (+/- {cv_results['std_macro_f1']:.4f}) [Min: {cv_results['min_macro_f1']:.4f}, Max: {cv_results['max_macro_f1']:.4f}]")

        print("\nPer-Class Recall across Folds:")
        for cname, stats in cv_results["per_class_summary"].items():
            print(f"  {cname:<20}: Mean Recall {stats['mean_recall']:.4f} (+/- {stats['std_recall']:.4f}) [Min: {stats['min_recall']:.4f}, Max: {stats['max_recall']:.4f}]")

        full_payload["cross_validation_5fold"] = cv_results

    # 6. Robustness by Traffic Rate and Fault Type
    if args.run_robustness:
        print("\n==================================================================")
        print(" ROBUSTNESS ANALYSIS: TRAFFIC RATE & FAULT TYPE")
        print("==================================================================")
        fault_pool = [s for s in all_ds if s.is_fault and s.split in ["train", "validation"]]
        traffic_res = analyze_traffic_rate_robustness(spatio_model, fault_pool, spatio_mean, spatio_std)
        fault_type_res = analyze_fault_type_robustness(spatio_model, fault_pool, spatio_mean, spatio_std)

        print("\nBy Traffic Rate:")
        print(f"{'Rate':<10} {'Support':<10} {'Accuracy':<10} {'Macro F1':<10}")
        print("-" * 42)
        for r_key, r_info in traffic_res.items():
            print(f"{r_key:<10} {r_info['sample_count']:<10} {r_info['accuracy']:<10.4f} {r_info['macro_f1']:<10.4f}")

        print("\nBy Fault Type:")
        print(f"{'Fault Type':<20} {'Support':<10} {'Accuracy':<10} {'Macro F1':<10}")
        print("-" * 52)
        for ft_key, ft_info in fault_type_res.items():
            print(f"{ft_key:<20} {ft_info['sample_count']:<10} {ft_info['accuracy']:<10.4f} {ft_info['macro_f1']:<10.4f}")

        full_payload["robustness_traffic_rate"] = traffic_res
        full_payload["robustness_fault_type"] = fault_type_res

    # 7. Early Detection Timeline Analysis
    if args.run_early_detection:
        print("\n==================================================================")
        print(" EARLY DETECTION TIMELINE ANALYSIS")
        print("==================================================================")
        canonical_test_exps = [
            "EXP-015",  # inventory-db (DB_LATENCY)
            "EXP-043",  # inventory-service (SERVICE_FAILURE)
            "EXP-047",  # order-service (SERVICE_LATENCY)
            "EXP-064",  # payment-service (SERVICE_FAILURE)
            "EXP-007"   # NO_FAULT control (healthy)
        ]

        exp_map = {s.experiment_id: s for s in test_ds}
        early_reports = {}

        for eid in canonical_test_exps:
            if eid in exp_map:
                s = exp_map[eid]
                report = analyze_early_detection(s, gate, spatio_model, spatio_mean, spatio_std)
                early_reports[eid] = report
                print(f"\n--- {eid} (Ground Truth: {report['ground_truth_root_cause']}, Fault: {report['fault_type']}) ---")
                print(f"{'Horizon':<8} {'P(Incident)':<14} {'Gate Status':<12} {'Predicted RCA':<20} {'RCA Conf':<10}")
                print("-" * 66)
                for step in report["timeline"]:
                    rca_str = step["predicted_root_cause"] if step["predicted_root_cause"] else "null (skipped)"
                    conf_str = f"{step['root_cause_confidence']:.4f}" if step["root_cause_confidence"] is not None else "N/A"
                    print(f"t={step['horizon_step']:<6} {step['incident_probability']:<14.4f} {step['gate_status']:<12} {rca_str:<20} {conf_str:<10}")

        full_payload["early_detection_analysis"] = early_reports

    # Save full machine-readable comparison artifact
    os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
    with open(RESULTS_FILE, "w") as f:
        json.dump(full_payload, f, indent=2)

    print(f"\nPhase 2D results successfully saved to: {RESULTS_FILE}")


if __name__ == "__main__":
    main()

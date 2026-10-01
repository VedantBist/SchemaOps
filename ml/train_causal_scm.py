"""
Training & Calibration Pipeline for CausalOps Topology-Constrained Lagged SCM (Phase 3B).

Usage:
    python -m ml.train_causal_scm
    python -m ml.train_causal_scm --lag-order 5 --alpha 1.0 --bootstrap-count 50 --run-ablations
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Any

# Ensure project root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dataset.tg_v1.loader import TemporalGraphDataset
from ml.causal.scm import TopologyConstrainedLaggedSCM
from ml.causal.evaluate import evaluate_scm_on_samples
from ml.causal.analysis import analyze_correlation_vs_causality
from ml.causal.design_matrix import apply_normalization


def run_ablation_studies(
    train_samples: List[Any],
    val_samples: List[Any],
    alpha: float = 1.0,
    solver: str = "ridge",
    random_seed: int = 42,
) -> Dict[str, Any]:
    """
    Executes controlled ablations:
    A. No topology constraints (Unconstrained Lagged Regression)
    B. Topology constraints enabled (Primary SCM, P=5)
    C. Lag P = 1
    D. Lag P = 3
    E. Lag P = 5 (Identical to B)
    F. Lag P = 10
    """
    print("\nRunning controlled ablation studies on validation split...")
    ablation_results = {}

    configurations = [
        ("Ablation_A_NoTopologyConstraints", {"lag_order": 5, "allow_all_topology": True}),
        ("Ablation_B_TopologyConstrained_P5", {"lag_order": 5, "allow_all_topology": False}),
        ("Ablation_C_Lag_P1", {"lag_order": 1, "allow_all_topology": False}),
        ("Ablation_D_Lag_P3", {"lag_order": 3, "allow_all_topology": False}),
        ("Ablation_E_Lag_P5", {"lag_order": 5, "allow_all_topology": False}),
        ("Ablation_F_Lag_P10", {"lag_order": 10, "allow_all_topology": False}),
    ]

    for name, cfg in configurations:
        scm_abl = TopologyConstrainedLaggedSCM(
            lag_order=cfg["lag_order"],
            alpha=alpha,
            solver=solver,
            edge_threshold=0.05,
            stability_threshold=0.60,
            n_bootstrap=10,  # Fast bootstrap for ablations
            random_seed=random_seed,
            allow_all_topology=cfg["allow_all_topology"],
        )
        scm_abl.fit(train_samples)
        val_eval = evaluate_scm_on_samples(scm_abl, val_samples, split_name="validation")

        # Count forbidden edges learned
        forbidden_count = 0
        if cfg["allow_all_topology"] and scm_abl.stable_graph:
            from ml.causal.design_matrix import is_edge_allowed
            for e in scm_abl.stable_graph.edges:
                if not is_edge_allowed(e.source_node, e.source_variable, e.target_node, e.target_variable, allow_all_topology=False):
                    forbidden_count += 1

        ablation_results[name] = {
            "config": cfg,
            "mean_r2": scm_abl.fit_summary.get("mean_r2", 0.0),
            "candidate_edges_count": len(scm_abl.candidate_graph.edges) if scm_abl.candidate_graph else 0,
            "stable_edges_count": len(scm_abl.stable_graph.edges) if scm_abl.stable_graph else 0,
            "forbidden_stable_edges_count": forbidden_count,
            "val_top1_accuracy": val_eval["root_cause_attribution"]["top1_exact_match_accuracy"],
            "val_top2_recall": val_eval["root_cause_attribution"]["top2_recall"],
            "val_sign_agreement": val_eval["intervention_validation"]["sign_agreement_rate"],
            "val_mean_abs_error": val_eval["intervention_validation"]["mean_absolute_error"],
        }
        print(f"  [{name}] Stable Edges: {ablation_results[name]['stable_edges_count']:<3} | "
              f"Forbidden Edges: {forbidden_count:<2} | "
              f"Val Top-1 Acc: {ablation_results[name]['val_top1_accuracy']:.2%} | "
              f"Val Top-2 Recall: {ablation_results[name]['val_top2_recall']:.2%}")

    return ablation_results


def main():
    parser = argparse.ArgumentParser(description="Train Topology-Constrained Lagged SCM (Phase 3B)")
    parser.add_argument("--dataset-dir", type=str, default="dataset/tg_v1", help="Path to tg_v1 dataset")
    parser.add_argument("--lag-order", type=int, default=5, help="Autoregressive lag order P (default 5)")
    parser.add_argument("--alpha", type=float, default=1.0, help="Ridge regularization parameter")
    parser.add_argument("--solver", type=str, default="ridge", choices=["ridge", "lasso"])
    parser.add_argument("--edge-threshold", type=float, default=0.05, help="Edge retention threshold")
    parser.add_argument("--stability-threshold", type=float, default=0.60, help="Bootstrap frequency threshold")
    parser.add_argument("--bootstrap-count", type=int, default=50, help="Number of bootstrap iterations")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--model-dir", type=str, default="ml/models/causal_scm", help="Output model directory")
    parser.add_argument("--run-ablations", action="store_true", help="Run controlled ablation studies")
    args = parser.parse_args()

    print("================================================================================")
    print(" PHASE 3B — TOPOLOGY-CONSTRAINED LAGGED SCM TRAINING & VALIDATION")
    print("================================================================================")
    print(f"Configuration: Lag P={args.lag_order} | Alpha={args.alpha} | Solver={args.solver}")
    print(f"Bootstrap Resamples: {args.bootstrap_count} | Stability Threshold: {args.stability_threshold:.2f}")
    print(f"Seed: {args.seed} | Model Output: {args.model_dir}")

    # Load splits (Train: 56 experiments, Validation: 12 experiments)
    print(f"\nLoading dataset from {args.dataset_dir}...")
    train_ds = TemporalGraphDataset(dataset_dir=args.dataset_dir, split="train")
    val_ds = TemporalGraphDataset(dataset_dir=args.dataset_dir, split="validation")

    train_samples = [train_ds[i] for i in range(len(train_ds))]
    val_samples = [val_ds[i] for i in range(len(val_ds))]

    print(f"Train Cohort: {len(train_samples)} experiments ({sum(1 for s in train_samples if s.is_fault)} fault, {sum(1 for s in train_samples if not s.is_fault)} controls)")
    print(f"Val Cohort:   {len(val_samples)} experiments ({sum(1 for s in val_samples if s.is_fault)} fault, {sum(1 for s in val_samples if not s.is_fault)} controls)")

    # Instantiate and Fit SCM
    print("\nFitting Topology-Constrained Lagged SCM on training cohort...")
    scm = TopologyConstrainedLaggedSCM(
        lag_order=args.lag_order,
        alpha=args.alpha,
        solver=args.solver,
        edge_threshold=args.edge_threshold,
        stability_threshold=args.stability_threshold,
        n_bootstrap=args.bootstrap_count,
        random_seed=args.seed,
        allow_all_topology=False,
    )
    scm.fit(train_samples)

    print("\nTraining Fit Results:")
    print(f"  Total Structural Equations: {scm.fit_summary['total_targets']}")
    print(f"  Mean Equation R^2:          {scm.fit_summary['mean_r2']:.4f}")
    print(f"  Median Equation R^2:        {scm.fit_summary['median_r2']:.4f}")
    print(f"  Candidate Edges (Allowed):  {len(scm.candidate_graph.edges)}")
    print(f"  Retained Candidate Edges:   {scm.fit_summary['retained_candidate_edges']}")
    print(f"  Bootstrap Stable Edges:     {len(scm.stable_graph.edges)} (freq >= {args.stability_threshold:.2f})")

    # Evaluate on Validation Split (Never test set!)
    print("\nEvaluating SCM on Validation Split (12 experiments)...")
    val_eval = evaluate_scm_on_samples(scm, val_samples, split_name="validation")
    rc_m = val_eval["root_cause_attribution"]
    int_m = val_eval["intervention_validation"]

    print(f"  Val Top-1 Root Cause Accuracy: {rc_m['top1_exact_match_accuracy']:.2%} ({rc_m['exact_matches_count']}/{rc_m['support']})")
    print(f"  Val Top-2 Recall:             {rc_m['top2_recall']:.2%} ({rc_m['top2_matches_count']}/{rc_m['support']})")
    print(f"  Intervention Sign Agreement:  {int_m['sign_agreement_rate']:.2%}")
    print(f"  Direction Agreement Rate:     {int_m['direction_agreement_rate']:.2%}")
    print(f"  Mean Treatment Effect Error:  {int_m['mean_absolute_error']:.2f}")

    # Correlation vs Causality Analysis
    print("\nRunning Associational Baseline (Pearson correlation vs SCM)...")
    norm_trajs = [apply_normalization(s.x, scm.norm_stats) for s in train_samples]
    corr_analysis = analyze_correlation_vs_causality(norm_trajs, scm.stable_graph)

    # Optional Ablations
    ablation_results = None
    if args.run_ablations:
        ablation_results = run_ablation_studies(
            train_samples, val_samples, alpha=args.alpha, solver=args.solver, random_seed=args.seed
        )

    # Save Model Artifacts
    print(f"\nSaving model checkpoints and JSON results to {args.model_dir}...")
    scm.save(args.model_dir)

    results_payload = {
        "training_summary": scm.fit_summary,
        "validation_evaluation": val_eval,
        "correlation_analysis": corr_analysis,
        "ablations": ablation_results,
    }
    with open(Path(args.model_dir) / "results.json", "w", encoding="utf-8") as f:
        json.dump(results_payload, f, indent=2)

    print(f"Model and results successfully saved to {args.model_dir}/")
    print("================================================================================")


if __name__ == "__main__":
    main()

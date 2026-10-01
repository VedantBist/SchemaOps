"""
Official Held-Out Test Evaluation Pipeline for CausalOps SCM (Phase 3B).

Evaluates the frozen, pre-calibrated SCM once on the held-out test split (12 experiments:
10 fault experiments + 2 NO_FAULT controls).
Compares causal attribution against Phase 2 SpatioTemporal GNN predictions.

Usage:
    python -m ml.evaluate_causal_scm
    python -m ml.evaluate_causal_scm --model-dir ml/models/causal_scm
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dataset.tg_v1.loader import TemporalGraphDataset
from ml.causal.scm import TopologyConstrainedLaggedSCM
from ml.causal.evaluate import evaluate_scm_on_samples, compare_with_gnn_baseline


def main():
    parser = argparse.ArgumentParser(description="Evaluate CausalOps SCM on Official Held-Out Test Split")
    parser.add_argument("--dataset-dir", type=str, default="dataset/tg_v1", help="Path to tg_v1 dataset")
    parser.add_argument("--model-dir", type=str, default="ml/models/causal_scm", help="Path to trained SCM artifacts")
    parser.add_argument("--gnn-checkpoint", type=str, default="ml/models/temporal_gnn/spatiotemporal_v1.pt", help="Path to GNN checkpoint")
    args = parser.parse_args()

    print("================================================================================")
    print(" PHASE 3B — OFFICIAL HELD-OUT TEST EVALUATION REPORT")
    print("================================================================================")

    # 1. Load fitted model
    print(f"Loading trained SCM from {args.model_dir}...")
    scm = TopologyConstrainedLaggedSCM.load(args.model_dir)

    # 2. Load held-out test split
    print(f"Loading held-out test split from {args.dataset_dir}...")
    test_ds = TemporalGraphDataset(dataset_dir=args.dataset_dir, split="test")
    test_samples = [test_ds[i] for i in range(len(test_ds))]

    fault_samples = [s for s in test_samples if s.is_fault]
    control_samples = [s for s in test_samples if not s.is_fault]
    print(f"Test Cohort: {len(test_samples)} total experiments ({len(fault_samples)} fault, {len(control_samples)} controls)\n")

    # 3. Evaluate SCM metrics
    test_results = evaluate_scm_on_samples(scm, test_samples, split_name="test")
    rc_m = test_results["root_cause_attribution"]
    int_m = test_results["intervention_validation"]

    print("--------------------------------------------------------------------------------")
    print(" 1. ROOT-CAUSE ATTRIBUTION ACCURACY (INDEPENDENT CAUSAL EVIDENCE)")
    print("--------------------------------------------------------------------------------")
    print(f"Top-1 Exact Match Accuracy: {rc_m['top1_exact_match_accuracy']:.2%} ({rc_m['exact_matches_count']}/{rc_m['support']})")
    print(f"Top-2 Candidate Recall:     {rc_m['top2_recall']:.2%} ({rc_m['top2_matches_count']}/{rc_m['support']})")
    print("\nPer-Class Attribution Breakdown:")
    print(f"{'Target Service':<22} {'Recall':<10} {'Correct':<10} {'Support':<8}")
    print("-" * 52)
    for cname, stats in rc_m["per_class_recall"].items():
        print(f"{cname:<22} {stats['recall']:<10.2%} {stats['correct']:<10} {stats['support']:<8}")

    print("\n--------------------------------------------------------------------------------")
    print(" 2. INTERVENTION VALIDATION & TREATMENT EFFECT ESTIMATION")
    print("--------------------------------------------------------------------------------")
    print(f"Sign Agreement Rate:       {int_m['sign_agreement_rate']:.2%}")
    print(f"Direction Agreement Rate:  {int_m['direction_agreement_rate']:.2%}")
    print(f"Mean Absolute ATE Error:   {int_m['mean_absolute_error']:.2f}")
    print(f"Median Absolute ATE Error: {int_m['median_absolute_error']:.2f}")

    print("\n--------------------------------------------------------------------------------")
    print(" 3. COMPARATIVE BENCHMARK: CAUSAL SCM VS. SPATIO-TEMPORAL GNN")
    print("--------------------------------------------------------------------------------")
    gnn_comparison = compare_with_gnn_baseline(
        test_results, test_samples, gnn_checkpoint_path=args.gnn_checkpoint
    )

    print(f"{'Exp ID':<9} {'Fault Type':<16} {'Actual Target':<18} {'GNN Candidate':<18} {'Causal SCM':<18} {'Match?':<6}")
    print("-" * 88)
    for comp in gnn_comparison:
        match_str = "YES" if comp["agreement"] else "DIFF"
        print(f"{comp['experiment_id']:<9} {comp['fault_type']:<16} {comp['actual_target']:<18} "
              f"{comp['gnn_candidate']:<18} {comp['causal_candidate']:<18} {match_str:<6}")

    # 4. Save results to test_results.json
    out_file = Path(args.model_dir) / "test_results.json"
    full_output = {
        "test_metrics": test_results,
        "gnn_comparison": gnn_comparison,
    }
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(full_output, f, indent=2)

    print("\n================================================================================")
    print(f"Official test evaluation saved to {out_file}")
    print("================================================================================")


if __name__ == "__main__":
    main()

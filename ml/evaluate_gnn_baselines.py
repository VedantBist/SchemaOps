"""CLI evaluation script for CausalOps GNN baselines.

Loads saved model checkpoints from disk and evaluates them on the held-out test split
and NO_FAULT control samples without retraining.

Usage:
  python -m ml.evaluate_gnn_baselines
  python -m ml.evaluate_gnn_baselines --checkpoint ml/models/gnn_baselines/gcn_v1.pt
"""

import os
import sys
import json
import argparse
from typing import Dict, List, Any

from ml.gnn_baselines.evaluate import evaluate_checkpoint_file
from ml.gnn_baselines.metrics import CLASS_NAMES


DEFAULT_CHECKPOINTS = [
    ("MLP", "ml/models/gnn_baselines/mlp_v1.pt"),
    ("GCN", "ml/models/gnn_baselines/gcn_v1.pt"),
    ("GAT", "ml/models/gnn_baselines/gat_v1.pt")
]


def print_evaluation_summary(name: str, res: Dict[str, Any]) -> None:
    cfg = res["config"]
    test_m = res["test_metrics"]
    val_m = res["val_metrics"]
    ctrl = res["control_analysis"]

    print(f"\n=======================================================")
    print(f" EVALUATION REPORT: {name.upper()}")
    print(f"=======================================================")
    print(f"Architecture:        {cfg['model_type'].upper()}")
    print(f"Feature Set:         {cfg['feature_set']}")
    print(f"Parameter Count:     {cfg['parameter_count']:,}")
    print(f"Best Training Epoch: {res['best_epoch']}")
    print(f"Validation Acc:      {val_m['accuracy']:.4f} | Val Macro F1:  {val_m['macro_f1']:.4f}")
    print(f"Test Accuracy:       {test_m['accuracy']:.4f} | Test Macro F1: {test_m['macro_f1']:.4f}")
    print(f"Test Weighted F1:    {test_m['weighted_f1']:.4f}")
    
    print("\nPer-Class Test Metrics:")
    print(f"{'Class':<20} {'Precision':<10} {'Recall':<10} {'F1-Score':<10} {'Support':<8}")
    print("-" * 58)
    for cname in CLASS_NAMES:
        pcm = test_m["per_class"][cname]
        print(f"{cname:<20} {pcm['precision']:<10.4f} {pcm['recall']:<10.4f} {pcm['f1']:<10.4f} {pcm['support']:<8}")

    print("\nConfusion Matrix (Rows=True, Cols=Predicted):")
    print("      " + " ".join(f"{c[:7]:>7}" for c in CLASS_NAMES))
    cm = test_m["confusion_matrix"]
    for idx, row in enumerate(cm):
        print(f"{CLASS_NAMES[idx][:5]:>5} " + " ".join(f"{val:>7}" for val in row))

    print(f"\nNO_FAULT Controls Analysis (N={ctrl['control_count']}):")
    print(f"  Mean Confidence:          {ctrl['mean_confidence']:.4f}")
    print(f"  Prediction Distribution:  {ctrl['prediction_distribution']}")


def main():
    parser = argparse.ArgumentParser(description="Evaluate CausalOps GNN Baselines")
    parser.add_argument("--checkpoint", type=str, default=None, help="Path to specific checkpoint file")
    args = parser.parse_args()

    if args.checkpoint:
        if not os.path.exists(args.checkpoint):
            print(f"Error: Checkpoint file not found: {args.checkpoint}")
            sys.exit(1)
        name = os.path.basename(args.checkpoint).split("_")[0]
        res = evaluate_checkpoint_file(args.checkpoint)
        print_evaluation_summary(name, res)
    else:
        for name, ckpt_path in DEFAULT_CHECKPOINTS:
            if not os.path.exists(ckpt_path):
                print(f"Warning: Checkpoint not found: {ckpt_path}. Run training first.")
                continue
            res = evaluate_checkpoint_file(ckpt_path)
            print_evaluation_summary(name, res)


if __name__ == "__main__":
    main()

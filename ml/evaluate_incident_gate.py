"""CLI evaluation script for Phase 2D Incident Gating & Gated RCA.

Loads saved incident gate and RCA checkpoints and evaluates the complete decoupled
pipeline on the held-out test split without retraining.

Usage:
  python -m ml.evaluate_incident_gate
"""

import os
import sys
import argparse

from ml.incident_gate.gate import IncidentGate
from ml.incident_gate.evaluate import evaluate_gated_rca_pipeline
from ml.temporal_gnn.evaluate import load_and_reconstruct_model
from dataset.tg_v1.loader import TemporalGraphDataset

GATE_DIR = "ml/models/incident_gate"
RCA_CKPT = "ml/models/temporal_gnn/spatiotemporal_v1.pt"


def main():
    parser = argparse.ArgumentParser(description="Evaluate Incident Gate and Gated RCA Pipeline")
    parser.add_argument("--gate-dir", type=str, default=GATE_DIR)
    parser.add_argument("--rca-checkpoint", type=str, default=RCA_CKPT)
    args = parser.parse_args()

    if not os.path.exists(args.gate_dir):
        print(f"Error: Gate checkpoint directory not found: {args.gate_dir}. Run training first.")
        sys.exit(1)
    if not os.path.exists(args.rca_checkpoint):
        print(f"Error: RCA checkpoint not found: {args.rca_checkpoint}.")
        sys.exit(1)

    gate = IncidentGate.load(args.gate_dir)
    rca_model, rca_cfg, rca_mean, rca_std = load_and_reconstruct_model(args.rca_checkpoint)

    test_ds = [s for s in TemporalGraphDataset(split=None, fault_only=False) if s.split == "test"]

    res = evaluate_gated_rca_pipeline(
        gate=gate,
        rca_model=rca_model,
        rca_mean=rca_mean,
        rca_std=rca_std,
        test_samples=test_ds
    )

    gm = res["incident_gate_metrics"]
    e2e = res["end_to_end_counts"]
    rca_m = res["rca_conditional_metrics"]

    print("\n=======================================================")
    print(" GATED RCA PIPELINE EVALUATION (Held-Out Test Split)")
    print("=======================================================")
    print(f"Total Test Samples:   {res['total_test_samples']} (10 Faults, 2 NO_FAULT Controls)")
    print(f"Incident Threshold:   {gm['threshold']:.4f}")

    print("\nIncident Gate Performance:")
    print(f"  TP: {gm['tp']} | TN: {gm['tn']} | FP: {gm['fp']} | FN: {gm['fn']}")
    print(f"  Accuracy:    {gm['accuracy']:.4f}")
    print(f"  Precision:   {gm['precision']:.4f}")
    print(f"  Recall:      {gm['recall']:.4f}")
    print(f"  F1-Score:    {gm['f1']:.4f}")
    print(f"  Specificity: {gm['specificity']:.4f}")
    print(f"  FPR:         {gm['fpr']:.4f}")
    print(f"  FNR:         {gm['fnr']:.4f}")

    print("\nEnd-to-End Decision Breakdown:")
    print(f"  Correct NO_FAULT Decisions (Status=NORMAL, RCA skipped):  {e2e['correct_no_fault_decisions']} / 2")
    print(f"  False Incidents on NO_FAULT:                             {e2e['false_incidents_on_no_fault']} / 2")
    print(f"  Missed Incidents (False Negatives):                      {e2e['missed_incidents']} / 10")
    print(f"  Correctly Detected Incidents:                            {e2e['detected_incidents']} / 10")
    print(f"  Correctly Detected with Correct RCA:                     {e2e['correct_detected_rca']} / 10")

    print("\nConditional RCA on Detected Faults:")
    print(f"  Accuracy:    {rca_m['accuracy']:.4f}")
    print(f"  Macro F1:    {rca_m['macro_f1']:.4f}")
    print(f"  Weighted F1: {rca_m['weighted_f1']:.4f}")


if __name__ == "__main__":
    main()

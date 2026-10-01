"""Evaluation pipeline for the Incident Gate and Gated RCA Pipeline.

Evaluates:
  1. Binary incident detection metrics on test split (10 fault, 2 NO_FAULT).
  2. Root-cause classification metrics on detected faults.
  3. End-to-end system composition performance.
"""

from typing import Dict, List, Any, Optional
import numpy as np

from ml.gnn_baselines.metrics import evaluate_predictions, CLASS_NAMES
from ml.incident_gate.features import prepare_gate_dataset
from ml.incident_gate.gate import IncidentGate, predict_gated_rca, GatedRCAResult
from ml.temporal_gnn.evaluate import load_and_reconstruct_model
from dataset.tg_v1.loader import TemporalGraphDataset, TemporalGraphSample


def evaluate_gate_on_split(
    gate: IncidentGate,
    samples: List[TemporalGraphSample]
) -> Dict[str, Any]:
    """
    Evaluates binary incident detection metrics on a sample partition.
    """
    X, targets, exp_ids = prepare_gate_dataset(samples, gate.norm_mean, gate.norm_std)
    probs = gate.model.forward(X)
    preds = (probs >= gate.threshold).astype(int)

    tp = int(np.sum((preds == 1) & (targets == 1)))
    tn = int(np.sum((preds == 0) & (targets == 0)))
    fp = int(np.sum((preds == 1) & (targets == 0)))
    fn = int(np.sum((preds == 0) & (targets == 1)))

    prec = float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    rec = float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0
    f1 = float(2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
    acc = float((tp + tn) / len(targets))
    spec = float(tn / (tn + fp)) if (tn + fp) > 0 else 0.0
    fpr = float(fp / (fp + tn)) if (fp + tn) > 0 else 0.0
    fnr = float(fn / (fn + tp)) if (fn + tp) > 0 else 0.0

    return {
        "sample_count": len(samples),
        "fault_count": int(np.sum(targets == 1)),
        "control_count": int(np.sum(targets == 0)),
        "threshold": round(gate.threshold, 4),
        "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "accuracy": round(acc, 4),
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1": round(f1, 4),
        "specificity": round(spec, 4),
        "fpr": round(fpr, 4),
        "fnr": round(fnr, 4),
        "confusion_matrix": [[tn, fp], [fn, tp]],
        "predictions": [
            {
                "experiment_id": exp_ids[i],
                "ground_truth": "FAULT" if targets[i] == 1 else "NO_FAULT",
                "probability": round(float(probs[i]), 4),
                "predicted": "FAULT" if preds[i] == 1 else "NO_FAULT"
            }
            for i in range(len(samples))
        ]
    }


def evaluate_gated_rca_pipeline(
    gate: IncidentGate,
    rca_model: Any,
    rca_mean: np.ndarray,
    rca_std: np.ndarray,
    test_samples: List[TemporalGraphSample]
) -> Dict[str, Any]:
    """
    Evaluates the complete decoupled pipeline: Incident Gate -> Spatio-Temporal GNN.
    """
    gated_results: List[GatedRCAResult] = [
        predict_gated_rca(s, gate, rca_model, rca_mean, rca_std)
        for s in test_samples
    ]

    # Partition by outcome
    correct_no_fault = sum(1 for r in gated_results if r.ground_truth_status == "NO_FAULT" and r.predicted_status == "NORMAL")
    false_incidents = sum(1 for r in gated_results if r.ground_truth_status == "NO_FAULT" and r.predicted_status == "INCIDENT")
    missed_incidents = sum(1 for r in gated_results if r.ground_truth_status == "FAULT" and r.predicted_status == "NORMAL")
    detected_incidents = sum(1 for r in gated_results if r.ground_truth_status == "FAULT" and r.predicted_status == "INCIDENT")

    # Evaluate RCA strictly among actual fault experiments where RCA was invoked
    fault_results = [r for r in gated_results if r.ground_truth_status == "FAULT"]
    correctly_identified_root_causes = sum(
        1 for r in fault_results if r.rca_invoked and r.predicted_root_cause == r.ground_truth_root_cause
    )

    y_true_rca = []
    y_pred_rca = []
    for r in fault_results:
        if r.rca_invoked and r.predicted_root_cause in CLASS_NAMES:
            y_true_rca.append(CLASS_NAMES.index(r.ground_truth_root_cause))
            y_pred_rca.append(CLASS_NAMES.index(r.predicted_root_cause))

    if len(y_true_rca) > 0:
        rca_metrics = evaluate_predictions(np.array(y_true_rca), np.array(y_pred_rca))
    else:
        rca_metrics = {"accuracy": 0.0, "macro_f1": 0.0}

    return {
        "total_test_samples": len(test_samples),
        "incident_gate_metrics": evaluate_gate_on_split(gate, test_samples),
        "end_to_end_counts": {
            "correct_no_fault_decisions": correct_no_fault,
            "false_incidents_on_no_fault": false_incidents,
            "missed_incidents": missed_incidents,
            "detected_incidents": detected_incidents,
            "correct_detected_rca": correctly_identified_root_causes
        },
        "rca_conditional_metrics": rca_metrics,
        "sample_records": [r.to_dict() for r in gated_results]
    }

"""
Comprehensive Evaluation Pipeline for CausalOps SCM (Phase 3B).

Evaluates:
1. Root-cause attribution accuracy (Top-1 Exact Match & Top-2 Recall) derived independently from SCM.
2. Intervention validation: sign agreement, direction agreement, treatment effect error.
3. Comparative analysis against Phase 2 SpatioTemporal GNN baseline.
4. Clean separation of metrics with exact support counts.
"""

from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
import numpy as np

from .scm import TopologyConstrainedLaggedSCM
from .intervention import validate_all_experiments


ROOT_CAUSE_CLASSES = [
    "inventory-db",
    "inventory-service",
    "order-service",
    "payment-service",
]


def evaluate_scm_on_samples(
    scm: TopologyConstrainedLaggedSCM,
    samples: List[Any],
    split_name: str = "test",
) -> Dict[str, Any]:
    """
    Evaluates the fitted SCM on a list of samples (e.g. held-out test split).
    Produces exact support counts, per-class metrics, and intervention validation.
    """
    fault_samples = [s for s in samples if s.is_fault]
    control_samples = [s for s in samples if not s.is_fault]

    exact_matches = 0
    top2_matches = 0

    per_class_total: Dict[str, int] = {c: 0 for c in ROOT_CAUSE_CLASSES}
    per_class_correct: Dict[str, int] = {c: 0 for c in ROOT_CAUSE_CLASSES}

    experiment_results: List[Dict[str, Any]] = []

    for s in fault_samples:
        score_res = scm.score_incident_root_cause(s, candidate_services=ROOT_CAUSE_CLASSES)
        pred_top1 = score_res["predicted_root_cause"]
        ranked = [c["service"] for c in score_res["ranked_candidates"]]
        top2 = ranked[:2]
        true_target = s.label

        is_exact = (pred_top1 == true_target)
        is_top2 = (true_target in top2)

        if is_exact:
            exact_matches += 1
        if is_top2:
            top2_matches += 1

        if true_target in per_class_total:
            per_class_total[true_target] += 1
            if is_exact:
                per_class_correct[true_target] += 1

        rec = {
            "experiment_id": s.experiment_id,
            "fault_type": s.fault_type,
            "actual_injected_target": true_target,
            "causal_candidate": pred_top1,
            "causal_confidence": score_res["confidence"],
            "exact_match": is_exact,
            "in_top_2": is_top2,
            "ranked_candidates": score_res["ranked_candidates"],
            "supporting_propagation_path": score_res["supporting_evidence"]["primary_propagation_path"],
        }
        experiment_results.append(rec)

    # Controls evaluation: check false positive root cause identification
    control_results = []
    for s in control_samples:
        score_res = scm.score_incident_root_cause(s, candidate_services=ROOT_CAUSE_CLASSES)
        control_results.append({
            "experiment_id": s.experiment_id,
            "is_fault": False,
            "max_confidence": score_res["confidence"],
        })

    # Intervention effect validation
    intervention_records, intervention_summary = validate_all_experiments(
        samples, scm.stable_graph, scm.norm_stats
    )

    n_faults = max(1, len(fault_samples))
    top1_accuracy = exact_matches / float(n_faults)
    top2_recall = top2_matches / float(n_faults)

    per_class_recall = {}
    for c in ROOT_CAUSE_CLASSES:
        tot = per_class_total[c]
        corr = per_class_correct[c]
        per_class_recall[c] = {
            "support": tot,
            "correct": corr,
            "recall": round(corr / float(tot), 4) if tot > 0 else 0.0,
        }

    return {
        "split_name": split_name,
        "total_samples": len(samples),
        "fault_count": len(fault_samples),
        "control_count": len(control_samples),
        "root_cause_attribution": {
            "top1_exact_match_accuracy": round(top1_accuracy, 4),
            "top2_recall": round(top2_recall, 4),
            "exact_matches_count": exact_matches,
            "top2_matches_count": top2_matches,
            "support": len(fault_samples),
            "per_class_recall": per_class_recall,
        },
        "intervention_validation": intervention_summary,
        "experiment_breakdown": experiment_results,
        "control_breakdown": control_results,
    }


def compare_with_gnn_baseline(
    scm_results: Dict[str, Any],
    samples: List[Any],
    gnn_checkpoint_path: str = "ml/models/temporal_gnn/spatiotemporal_v1.pt",
) -> List[Dict[str, Any]]:
    """
    Compares SCM causal root-cause predictions with Phase 2 SpatioTemporal GNN predictions
    on the exact same samples.
    """
    comparisons = []
    chk_file = Path(gnn_checkpoint_path)

    # Map SCM results by experiment_id
    scm_map = {r["experiment_id"]: r for r in scm_results.get("experiment_breakdown", [])}

    if not chk_file.exists():
        # Fallback if checkpoint file missing
        for s in samples:
            if not s.is_fault:
                continue
            scm_res = scm_map.get(s.experiment_id, {})
            comparisons.append({
                "experiment_id": s.experiment_id,
                "fault_type": s.fault_type,
                "actual_target": s.label,
                "gnn_candidate": "N/A (checkpoint missing)",
                "causal_candidate": scm_res.get("causal_candidate"),
                "causal_confidence": scm_res.get("causal_confidence"),
                "gnn_accuracy": None,
                "causal_accuracy": scm_res.get("exact_match"),
                "agreement": None,
                "supporting_propagation_path": scm_res.get("supporting_propagation_path"),
            })
        return comparisons

    try:
        from ml.temporal_gnn.evaluate import load_and_reconstruct_model
        from ml.temporal_gnn.features import prepare_temporal_batch, FEATURE_SETS
        from ml.gnn_baselines.metrics import CLASS_NAMES

        model, cfg, norm_mean, norm_std = load_and_reconstruct_model(str(chk_file))
        feat_set = cfg.get("feature_set", "all")
        feat_indices = FEATURE_SETS[feat_set]["indices"]

        X, mask, targets, _ = prepare_temporal_batch(
            samples, norm_mean, norm_std, feature_indices=feat_indices
        )

        model.eval()
        out = model.forward(X, mask=mask)
        logits = out[0] if isinstance(out, tuple) else out

        max_l = np.max(logits, axis=-1, keepdims=True)
        exp_l = np.exp(logits - max_l)
        probs = exp_l / np.sum(exp_l, axis=-1, keepdims=True)
        preds = np.argmax(probs, axis=-1)

        for i, s in enumerate(samples):
            if not s.is_fault:
                continue
            gnn_pred_class = CLASS_NAMES[preds[i]]
            gnn_conf = float(probs[i, preds[i]])
            scm_res = scm_map.get(s.experiment_id, {})

            comparisons.append({
                "experiment_id": s.experiment_id,
                "fault_type": s.fault_type,
                "actual_target": s.label,
                "gnn_candidate": gnn_pred_class,
                "gnn_confidence": round(gnn_conf, 4),
                "causal_candidate": scm_res.get("causal_candidate"),
                "causal_confidence": scm_res.get("causal_confidence"),
                "gnn_accuracy": bool(gnn_pred_class == s.label),
                "causal_accuracy": scm_res.get("exact_match"),
                "agreement": bool(gnn_pred_class == scm_res.get("causal_candidate")),
                "supporting_propagation_path": scm_res.get("supporting_propagation_path"),
            })
    except Exception as e:
        # Graceful fallback on import or torch error
        for s in samples:
            if not s.is_fault:
                continue
            scm_res = scm_map.get(s.experiment_id, {})
            comparisons.append({
                "experiment_id": s.experiment_id,
                "fault_type": s.fault_type,
                "actual_target": s.label,
                "gnn_candidate": "N/A (eval error)",
                "causal_candidate": scm_res.get("causal_candidate"),
                "causal_confidence": scm_res.get("causal_confidence"),
                "gnn_accuracy": None,
                "causal_accuracy": scm_res.get("exact_match"),
                "agreement": None,
                "supporting_propagation_path": scm_res.get("supporting_propagation_path"),
            })

    return comparisons

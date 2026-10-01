"""Generalization validation, cross-validation, and early detection analysis for Phase 2D.

Contains:
  1. 5-Fold Stratified Experiment-Level Cross-Validation on the 60 non-test fault experiments.
  2. Robustness breakdowns by Root-Cause Class, Traffic Rate (1, 5, 15 rps), and Fault Type.
  3. Early Detection Timeline Analysis combining Incident Gating and Spatio-Temporal RCA.
"""

from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict
import numpy as np

from dataset.tg_v1.loader import TemporalGraphSample
from ml.gnn_baselines.metrics import evaluate_predictions, CLASS_NAMES
from ml.temporal_gnn.features import fit_temporal_normalization
from ml.temporal_gnn.train import train_temporal_model
from ml.temporal_gnn.evaluate import evaluate_temporal_model_on_samples
from ml.incident_gate.gate import IncidentGate, predict_gated_rca


def run_experiment_level_cross_validation(
    fault_samples_pool: List[TemporalGraphSample],
    num_folds: int = 5,
    seed: int = 42,
    epochs: int = 60
) -> Dict[str, Any]:
    """
    Executes 5-fold stratified experiment-level cross-validation on non-test fault experiments.
    Strictly splits by experiment ID (zero timestep or window leakage).
    """
    rng = np.random.RandomState(seed)

    # Group by target class to ensure balanced stratification
    by_class: Dict[int, List[TemporalGraphSample]] = defaultdict(list)
    for s in fault_samples_pool:
        by_class[s.target_class].append(s)

    # Shuffle each class group with fixed seed
    for c in by_class:
        rng.shuffle(by_class[c])

    # Form folds by round-robin distribution
    folds: List[List[TemporalGraphSample]] = [[] for _ in range(num_folds)]
    for c in sorted(by_class.keys()):
        for idx, s in enumerate(by_class[c]):
            folds[idx % num_folds].append(s)

    fold_results = []
    class_recalls: Dict[str, List[float]] = {cname: [] for cname in CLASS_NAMES}

    for k in range(num_folds):
        val_fold = folds[k]
        train_fold = []
        for j in range(num_folds):
            if j != k:
                train_fold.extend(folds[j])

        # Verify disjointness
        train_ids = set(s.experiment_id for s in train_fold)
        val_ids = set(s.experiment_id for s in val_fold)
        assert len(train_ids.intersection(val_ids)) == 0, f"Leakage detected in fold {k+1}!"

        # Fit normalization ONLY on train_fold
        f_mean, f_std = fit_temporal_normalization(train_fold)

        # Train SpatioTemporalGNN on this fold
        res = train_temporal_model(
            model_type="spatiotemporal",
            train_samples=train_fold,
            val_samples=val_fold,
            lr=0.005,
            max_epochs=epochs,
            seed=seed + k,
            checkpoint_path=None
        )

        model = res["model"]
        m = evaluate_temporal_model_on_samples(model, val_fold, f_mean, f_std)

        fold_results.append({
            "fold": k + 1,
            "train_count": len(train_fold),
            "val_count": len(val_fold),
            "accuracy": round(m["accuracy"], 4),
            "macro_f1": round(m["macro_f1"], 4),
            "weighted_f1": round(m["weighted_f1"], 4)
        })

        for cname in CLASS_NAMES:
            class_recalls[cname].append(m["per_class"][cname]["recall"])

    accs = [f["accuracy"] for f in fold_results]
    f1s = [f["macro_f1"] for f in fold_results]

    class_summary = {}
    for cname in CLASS_NAMES:
        r_list = class_recalls[cname]
        class_summary[cname] = {
            "mean_recall": round(float(np.mean(r_list)), 4),
            "std_recall": round(float(np.std(r_list)), 4),
            "min_recall": round(float(np.min(r_list)), 4),
            "max_recall": round(float(np.max(r_list)), 4)
        }

    return {
        "num_folds": num_folds,
        "total_experiments_evaluated": len(fault_samples_pool),
        "mean_accuracy": round(float(np.mean(accs)), 4),
        "std_accuracy": round(float(np.std(accs)), 4),
        "min_accuracy": round(float(np.min(accs)), 4),
        "max_accuracy": round(float(np.max(accs)), 4),
        "mean_macro_f1": round(float(np.mean(f1s)), 4),
        "std_macro_f1": round(float(np.std(f1s)), 4),
        "min_macro_f1": round(float(np.min(f1s)), 4),
        "max_macro_f1": round(float(np.max(f1s)), 4),
        "folds": fold_results,
        "per_class_summary": class_summary
    }


def analyze_traffic_rate_robustness(
    model: Any,
    samples: List[TemporalGraphSample],
    norm_mean: np.ndarray,
    norm_std: np.ndarray
) -> Dict[str, Any]:
    """Evaluates RCA model accuracy across traffic generation rates (1, 5, 15 req/s)."""
    by_rate: Dict[int, List[TemporalGraphSample]] = defaultdict(list)
    for s in samples:
        by_rate[s.traffic_rate_rps].append(s)

    results = {}
    for rate in sorted(by_rate.keys()):
        rate_samples = by_rate[rate]
        m = evaluate_temporal_model_on_samples(model, rate_samples, norm_mean, norm_std)
        results[f"{rate}_rps"] = {
            "rate_rps": rate,
            "sample_count": len(rate_samples),
            "accuracy": round(m["accuracy"], 4),
            "macro_f1": round(m["macro_f1"], 4),
            "weighted_f1": round(m["weighted_f1"], 4)
        }
    return results


def analyze_fault_type_robustness(
    model: Any,
    samples: List[TemporalGraphSample],
    norm_mean: np.ndarray,
    norm_std: np.ndarray
) -> Dict[str, Any]:
    """Evaluates RCA model accuracy across the 5 supported fault types."""
    by_type: Dict[str, List[TemporalGraphSample]] = defaultdict(list)
    for s in samples:
        by_type[s.fault_type].append(s)

    results = {}
    for ftype in sorted(by_type.keys()):
        ftype_samples = by_type[ftype]
        m = evaluate_temporal_model_on_samples(model, ftype_samples, norm_mean, norm_std)
        results[ftype] = {
            "fault_type": ftype,
            "sample_count": len(ftype_samples),
            "accuracy": round(m["accuracy"], 4),
            "macro_f1": round(m["macro_f1"], 4),
            "weighted_f1": round(m["weighted_f1"], 4)
        }
    return results


def analyze_early_detection(
    sample: TemporalGraphSample,
    gate: IncidentGate,
    rca_model: Any,
    rca_mean: np.ndarray,
    rca_std: np.ndarray,
    observation_horizons: Optional[List[int]] = None
) -> Dict[str, Any]:
    """
    Evaluates early detection timeline for a canonical experiment.
    Evaluates expanding observation windows: 5s, 10s, 15s, 20s, 30s, final.
    """
    T_valid = sample.sequence_length
    if observation_horizons is None:
        steps = [5, 10, 15, 20, 30]
        observation_horizons = [s for s in steps if s < T_valid] + [T_valid]

    timeline = []
    earliest_gate_step = None
    earliest_correct_rca_step = None

    for h in observation_horizons:
        res = predict_gated_rca(sample, gate, rca_model, rca_mean, rca_std, horizon_cutoff=h)
        
        is_gate_detected = (res.predicted_status == "INCIDENT")
        is_rca_correct = (res.predicted_root_cause == sample.label) if sample.is_fault else (res.predicted_root_cause is None)

        if is_gate_detected and earliest_gate_step is None:
            earliest_gate_step = h
        if is_rca_correct and earliest_correct_rca_step is None and (is_gate_detected or not sample.is_fault):
            earliest_correct_rca_step = h

        timeline.append({
            "horizon_step": h,
            "relative_sec": round(float(h), 1),
            "incident_probability": round(res.incident_probability, 4),
            "gate_status": res.predicted_status,
            "predicted_root_cause": res.predicted_root_cause,
            "root_cause_confidence": round(res.root_cause_confidence, 4) if res.root_cause_confidence is not None else None,
            "rca_invoked": res.rca_invoked,
            "matches_ground_truth": is_rca_correct
        })

    return {
        "experiment_id": sample.experiment_id,
        "is_fault": sample.is_fault,
        "fault_type": sample.fault_type,
        "ground_truth_root_cause": sample.label if sample.is_fault else "NO_FAULT",
        "earliest_gate_detected_sec": float(earliest_gate_step) if earliest_gate_step is not None else None,
        "earliest_correct_rca_sec": float(earliest_correct_rca_step) if earliest_correct_rca_step is not None else None,
        "timeline": timeline
    }

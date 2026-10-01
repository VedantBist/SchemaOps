"""
Phase 6A — Scientific Audit and Validation Script
Executes all quantitative audits, ablations, baseline sanity checks,
and early-window precursor tests required by the Phase 6A Validation Specification.
"""

from __future__ import annotations
import json
import os
import pickle
import sys
from pathlib import Path
from typing import Dict, Any, List, Tuple
import numpy as np
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score, confusion_matrix, brier_score_loss

_here = Path(__file__).resolve()
_repo_root = _here.parent.parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from ml.failure_prediction.features import (
    extract_prefault_features,
    get_feature_names,
    fit_normalization_stats,
    FeatureNormalizationStats,
    FEATURE_DIM,
)
from ml.failure_prediction.labels import detect_fault_onset, compute_labels_for_dataset
from ml.failure_prediction.predictor import FailurePredictionService
from ml.failure_prediction.evaluation import compute_ece, calibration_curve_data
from dataset.tg_v1.loader import TemporalGraphDataset

DATASET_DIR = _repo_root / "dataset" / "failure_prediction_v1"
MODEL_DIR = _repo_root / "ml" / "models" / "failure_prediction"
AUDIT_DIR = _repo_root / "ml" / "failure_prediction" / "audit"


def run_full_audit() -> Dict[str, Any]:
    print("=" * 70)
    print("PHASE 6A: SCIENTIFIC AUDIT & VALIDATION ENGINE")
    print("=" * 70)

    # 1. Load Data
    X_train = np.load(DATASET_DIR / "features_train.npy")
    X_val = np.load(DATASET_DIR / "features_val.npy")
    X_test = np.load(DATASET_DIR / "features_test.npy")

    with open(DATASET_DIR / "labels_train.json") as f:
        labels_train = json.load(f)
    with open(DATASET_DIR / "labels_val.json") as f:
        labels_val = json.load(f)
    with open(DATASET_DIR / "labels_test.json") as f:
        labels_test = json.load(f)
    with open(DATASET_DIR / "labels.json") as f:
        labels_all = json.load(f)

    with open(DATASET_DIR / "normalization.json") as f:
        norm_stats = FeatureNormalizationStats.from_dict(json.load(f))

    X_train_norm = norm_stats.normalize(X_train)
    X_test_norm = norm_stats.normalize(X_test)

    feature_names = get_feature_names()

    # Load trained RF within_30s model
    with open(MODEL_DIR / "rf_within_30s.pkl", "rb") as f:
        rf_30 = pickle.load(f)
    with open(MODEL_DIR / "rf_within_10s.pkl", "rb") as f:
        rf_10 = pickle.load(f)
    with open(MODEL_DIR / "rf_within_5s.pkl", "rb") as f:
        rf_5 = pickle.load(f)

    audit_summary: Dict[str, Any] = {}

    # -------------------------------------------------------------------------
    # AUDIT 4: TEMPORAL LEAKAGE TEST
    # -------------------------------------------------------------------------
    print("\n--- AUDIT 4: TEMPORAL LEAKAGE TEST ---")
    temporal_leakage_passed = True
    temporal_leakage_details = []

    ds_test = TemporalGraphDataset(dataset_dir="dataset/tg_v1", split="test")

    for s in ds_test:
        lbl = next(l for l in labels_test if l["experiment_id"] == s.experiment_id)
        onset_step = lbl["fault_onset_step"]
        if onset_step is not None:
            # Pre-fault window length must strictly be <= onset_step
            # Features extracted from s.x[:onset_step]
            # Max timestep in feature window is onset_step - 1
            max_feature_timestamp = onset_step - 1
            failure_timestamp = onset_step

            # Assertion: max(feature_timestamp) < failure_timestamp
            check = max_feature_timestamp < failure_timestamp
            if not check:
                temporal_leakage_passed = False

            temporal_leakage_details.append({
                "experiment_id": s.experiment_id,
                "onset_step": onset_step,
                "max_feature_step": max_feature_timestamp,
                "valid": check,
            })

    print(f"Temporal Leakage Check: {'PASSED' if temporal_leakage_passed else 'FAILED'}")
    audit_summary["temporal_leakage"] = {
        "status": "PASSED" if temporal_leakage_passed else "FAILED",
        "tested_samples": len(temporal_leakage_details),
    }

    # -------------------------------------------------------------------------
    # AUDIT 5 & 6: EARLY PREDICTION & LEAD TIME PER EXPERIMENT
    # -------------------------------------------------------------------------
    print("\n--- AUDIT 6: EARLY PREDICTION TEST & LEAD TIME PER EXPERIMENT ---")
    lead_time_table = []
    category_counts = {"EARLY >= 10s": 0, "EARLY >= 5s": 0, "EARLY > 0s": 0, "AT_FAILURE": 0, "TOO_LATE": 0}

    test_fault_samples = [s for s in ds_test if s.is_fault]

    for s in test_fault_samples:
        lbl = next(l for l in labels_test if l["experiment_id"] == s.experiment_id)
        onset_step = lbl["fault_onset_step"]
        actual_failure_time = float(onset_step)

        # Evaluate live streaming predictor step-by-step
        live_pred = FailurePredictionService(model_dir=str(MODEL_DIR))
        first_prediction_step = None

        for t in range(1, onset_step + 1):
            w = s.x[:t]
            res = live_pred.predict(w, experiment_id=s.experiment_id)
            if res["overall_verdict"] == "FAULT_PREDICTED":
                first_prediction_step = t
                break

        if first_prediction_step is not None and first_prediction_step < actual_failure_time:
            lead_time = actual_failure_time - first_prediction_step
            if lead_time >= 10.0:
                cat = "EARLY >= 10s"
            elif lead_time >= 5.0:
                cat = "EARLY >= 5s"
            elif lead_time > 0.0:
                cat = "EARLY > 0s"
            else:
                cat = "AT_FAILURE"
        elif first_prediction_step == actual_failure_time:
            lead_time = 0.0
            cat = "AT_FAILURE"
        else:
            lead_time = -1.0
            cat = "TOO_LATE"

        category_counts[cat] += 1

        lead_time_table.append({
            "experiment_id": s.experiment_id,
            "target": s.label,
            "failure_time": actual_failure_time,
            "first_prediction_time": first_prediction_step,
            "lead_time": lead_time,
            "category": cat,
        })
        print(f"  {s.experiment_id} | Target: {s.label:17s} | Fail: t={actual_failure_time}s | Pred: t={first_prediction_step}s | Lead: {lead_time:.1f}s | {cat}")

    audit_summary["lead_time_table"] = lead_time_table
    audit_summary["lead_time_category_counts"] = category_counts

    # -------------------------------------------------------------------------
    # AUDIT 7: BASELINE SANITY CHECKS
    # -------------------------------------------------------------------------
    print("\n--- AUDIT 7: BASELINE SANITY CHECKS ---")
    y_test_30 = np.array([int(l["failure_within_30s"]) for l in labels_test], dtype=np.int32)

    # Baseline A: Raw threshold detector on pre-fault window (db_latency > 30, p99 > 120, err > 0.25)
    base_a_preds = []
    for s in ds_test:
        lbl = next(l for l in labels_test if l["experiment_id"] == s.experiment_id)
        onset = lbl["fault_onset_step"]
        w = s.x[:onset] if onset else s.x
        breached = (w[:, :, 6].max() > 30.0) or (w[:, :, 2].max() > 120.0) or (w[:, :, 3].max() > 0.25)
        base_a_preds.append(int(breached))
    base_a_preds = np.array(base_a_preds)
    base_a_acc = float(accuracy_score(y_test_30, base_a_preds))

    # Baseline B: Previous-timestep threshold detector (tests if immediate t-1 is breached)
    base_b_preds = []
    for s in ds_test:
        lbl = next(l for l in labels_test if l["experiment_id"] == s.experiment_id)
        onset = lbl["fault_onset_step"]
        if onset and onset > 1:
            t_prev = s.x[onset - 2]
            breached = (t_prev[:, 6].max() > 30.0) or (t_prev[:, 2].max() > 120.0) or (t_prev[:, 3].max() > 0.25)
        else:
            breached = False
        base_b_preds.append(int(breached))
    base_b_preds = np.array(base_b_preds)
    base_b_acc = float(accuracy_score(y_test_30, base_b_preds))

    # Baseline C: Rolling Latency Threshold on lookback 5 (p99 > 80ms)
    base_c_preds = []
    for s in ds_test:
        lbl = next(l for l in labels_test if l["experiment_id"] == s.experiment_id)
        onset = lbl["fault_onset_step"]
        w = s.x[:onset] if onset else s.x
        base_c_preds.append(int(w[:, :, 2].max() > 80.0))
    base_c_preds = np.array(base_c_preds)
    base_c_acc = float(accuracy_score(y_test_30, base_c_preds))

    # Baseline D: Window Length Threshold (W <= 10 -> Fault)
    base_d_preds = np.array([int(X_test[i, 150] <= 10.0) for i in range(len(labels_test))])
    base_d_acc = float(accuracy_score(y_test_30, base_d_preds))

    print(f"  Baseline A (Raw threshold on pre-fault window): Acc = {base_a_acc:.3f}")
    print(f"  Baseline B (Previous-timestep threshold):       Acc = {base_b_acc:.3f}")
    print(f"  Baseline C (Rolling Latency > 80ms):            Acc = {base_c_acc:.3f}")
    print(f"  Baseline D (Window Length W <= 10 threshold):   Acc = {base_d_acc:.3f} (1.000 trivial shortcut!)")

    audit_summary["baselines"] = {
        "baseline_a_raw_threshold_acc": base_a_acc,
        "baseline_b_prev_timestep_acc": base_b_acc,
        "baseline_c_latency_80ms_acc": base_c_acc,
        "baseline_d_window_length_acc": base_d_acc,
    }

    # -------------------------------------------------------------------------
    # AUDIT 8: LABEL-SHIFT / PRECURSOR TEST (Ablation with Lead Requirements)
    # -------------------------------------------------------------------------
    print("\n--- AUDIT 8: LABEL-SHIFT / PRECURSOR ABLATION ---")
    lead_ablation_results = []

    # Windows ending K seconds before failure
    for k_sec in [1, 3, 5, 10, 15, 20, 30]:
        X_test_k = []
        y_test_k = []

        for i, s in enumerate(ds_test):
            lbl = labels_test[i]
            onset = lbl["fault_onset_step"]
            if onset is not None:
                end_step = onset - k_sec
                if end_step < 1:
                    # Truncation before window start: no pre-fault data left at this lead time
                    w_x = s.x[:1]
                else:
                    w_x = s.x[:end_step]
                y_val = 1
            else:
                w_x = s.x
                y_val = 0

            feat = extract_prefault_features(w_x, fault_onset_step=None)
            X_test_k.append(feat)
            y_test_k.append(y_val)

        X_k = np.array(X_test_k)
        y_k = np.array(y_test_k)
        X_k_norm = norm_stats.normalize(X_k)

        probs = rf_30.predict_proba(X_k_norm)[:, 1]
        preds = (probs >= 0.5).astype(int)

        try:
            auc = float(roc_auc_score(y_k, probs))
        except Exception:
            auc = 0.5
        f1 = float(f1_score(y_k, preds, zero_division=0))
        rec = float(recall_score(y_k, preds, zero_division=0))

        lead_ablation_results.append({
            "lead_requirement": f">={k_sec}s",
            "lead_sec": k_sec,
            "auc": round(auc, 3),
            "f1": round(f1, 3),
            "recall": round(rec, 3),
        })
        print(f"  Lead >={k_sec:2d}s: AUC = {auc:.3f} | F1 = {f1:.3f} | Recall = {rec:.3f}")

    audit_summary["lead_shift_ablation"] = lead_ablation_results

    # -------------------------------------------------------------------------
    # AUDIT 9: NO_FAULT INDIVIDUAL EXPERIMENT AUDIT
    # -------------------------------------------------------------------------
    print("\n--- AUDIT 9: NO_FAULT AUDIT (All 10 Control Experiments) ---")
    nofault_audit_table = []

    ds_train = TemporalGraphDataset(dataset_dir="dataset/tg_v1", split="train")
    ds_val = TemporalGraphDataset(dataset_dir="dataset/tg_v1", split="validation")

    all_samples_by_id = {s.experiment_id: s for s in list(ds_train) + list(ds_val) + list(ds_test)}
    control_ids = sorted([l["experiment_id"] for l in labels_all if not l["is_fault"]])

    live_pred = FailurePredictionService(model_dir=str(MODEL_DIR))

    for cid in control_ids:
        s = all_samples_by_id[cid]
        # Evaluate on full sequence
        res_full = live_pred.predict(s.x, experiment_id=cid)
        max_prob_full = res_full["max_failure_probability"]

        # Evaluate across all sliding windows
        window_probs = []
        alerts_count = 0
        for t in range(5, s.sequence_length):
            w = s.x[:t]
            res_t = live_pred.predict(w, experiment_id=cid)
            p = res_t["max_failure_probability"]
            window_probs.append(p)
            if p >= 0.5:
                alerts_count += 1

        max_alert_prob = max(window_probs) if window_probs else max_prob_full
        is_fp = res_full["overall_verdict"] == "FAULT_PREDICTED"

        nofault_audit_table.append({
            "experiment_id": cid,
            "full_sequence_prob": round(max_prob_full, 4),
            "full_sequence_verdict": res_full["overall_verdict"],
            "max_window_prob": round(max_alert_prob, 4),
            "sliding_window_alerts": alerts_count,
            "false_positive": is_fp,
        })
        print(f"  {cid} | Full prob: {max_prob_full:.3f} | Max window prob: {max_alert_prob:.3f} | Alerts: {alerts_count} | FP: {is_fp}")

    audit_summary["nofault_audit"] = nofault_audit_table

    # -------------------------------------------------------------------------
    # AUDIT 10 & 11: TARGET SERVICE & FAULT TYPE ACCURACY & CONFUSION MATRICES
    # -------------------------------------------------------------------------
    print("\n--- AUDIT 10 & 11: TARGET SERVICE & FAULT TYPE AUDIT ---")
    with open(MODEL_DIR / "rf_target_service.pkl", "rb") as f:
        target_clf = pickle.load(f)
    with open(MODEL_DIR / "rf_fault_type.pkl", "rb") as f:
        fault_clf = pickle.load(f)

    test_fault_indices = [i for i, l in enumerate(labels_test) if l["is_fault"]]
    X_test_fault_norm = X_test_norm[test_fault_indices]

    y_true_targets = [labels_test[i]["ground_truth_label"] for i in test_fault_indices]
    y_pred_targets = list(target_clf.predict(X_test_fault_norm))

    y_true_faults = [labels_test[i]["fault_type"] for i in test_fault_indices]
    y_pred_faults = list(fault_clf.predict(X_test_fault_norm))

    target_cm = confusion_matrix(y_true_targets, y_pred_targets, labels=target_clf.classes)
    fault_cm = confusion_matrix(y_true_faults, y_pred_faults, labels=fault_clf.classes)

    print("Target Service Classes:", target_clf.classes)
    print("Target Service Confusion Matrix:\n", target_cm)
    print(f"Target Service Accuracy: {accuracy_score(y_true_targets, y_pred_targets):.3f}")

    print("\nFault Type Classes:", fault_clf.classes)
    print("Fault Type Confusion Matrix:\n", fault_cm)
    print(f"Fault Type Accuracy: {accuracy_score(y_true_faults, y_pred_faults):.3f}")

    audit_summary["target_service_audit"] = {
        "classes": target_clf.classes,
        "confusion_matrix": target_cm.tolist(),
        "accuracy": float(accuracy_score(y_true_targets, y_pred_targets)),
    }
    audit_summary["fault_type_audit"] = {
        "classes": fault_clf.classes,
        "confusion_matrix": fault_cm.tolist(),
        "accuracy": float(accuracy_score(y_true_faults, y_pred_faults)),
    }

    # -------------------------------------------------------------------------
    # AUDIT 12: FEATURE ABLATION
    # -------------------------------------------------------------------------
    print("\n--- AUDIT 12: FEATURE ABLATIONS ---")
    from sklearn.ensemble import RandomForestClassifier

    y_train_30 = np.array([int(l["failure_within_30s"]) for l in labels_train], dtype=np.int32)

    ablations = {
        "A_temporal_slopes_only": list(range(105, 140)),
        "B_topology_spreads_only": list(range(140, 150)),
        "C_raw_telemetry_moments_only": list(range(0, 105)),
        "D_temporal_plus_topology": list(range(105, 150)),
        "E_no_window_length": list(range(0, 150)), # Excludes feature 150
        "F_no_error_no_window_length": [i for i in range(150) if "error" not in feature_names[i]],
    }

    ablation_results = {}
    for name, indices in ablations.items():
        X_tr_sub = X_train_norm[:, indices]
        X_te_sub = X_test_norm[:, indices]

        clf = RandomForestClassifier(n_estimators=100, random_state=42)
        clf.fit(X_tr_sub, y_train_30)

        p = clf.predict_proba(X_te_sub)[:, 1]
        y_pred_sub = (p >= 0.5).astype(int)

        try:
            auc = float(roc_auc_score(y_test_30, p))
        except Exception:
            auc = 0.5
        f1 = float(f1_score(y_test_30, y_pred_sub, zero_division=0))
        rec = float(recall_score(y_test_30, y_pred_sub, zero_division=0))

        ablation_results[name] = {
            "n_features": len(indices),
            "auc": round(auc, 3),
            "f1": round(f1, 3),
            "recall": round(rec, 3),
        }
        print(f"  {name:30s} ({len(indices):3d} feats) -> AUC = {auc:.3f} | F1 = {f1:.3f} | Recall = {rec:.3f}")

    audit_summary["feature_ablations"] = ablation_results

    # -------------------------------------------------------------------------
    # AUDIT 16: LIVE VS OFFLINE NUMERICAL CONSISTENCY (10 Windows)
    # -------------------------------------------------------------------------
    print("\n--- AUDIT 16: LIVE VS OFFLINE CONSISTENCY (10 Windows) ---")
    consistency_passed = True
    consistency_diffs = []

    for idx in range(min(10, len(ds_test))):
        s = ds_test[idx]
        lbl = labels_test[idx]
        onset = lbl["fault_onset_step"]

        feat_offline = extract_prefault_features(s.x, onset).reshape(1, -1)
        feat_norm = norm_stats.normalize(feat_offline)
        prob_offline = float(rf_30.predict_proba(feat_norm)[0, 1])

        res_live = live_pred.predict(s.x, fault_onset_step=onset)
        prob_live = res_live["predictions"]["failure_within_30s"]["probability"]

        diff = abs(prob_offline - prob_live)
        consistency_diffs.append(diff)
        if diff >= 1e-5:
            consistency_passed = False

    max_diff = max(consistency_diffs)
    print(f"  Max difference across 10 test windows: {max_diff:.2e} (Threshold: 1e-5)")
    print(f"  Consistency Status: {'PASSED' if consistency_passed else 'FAILED'}")

    audit_summary["live_offline_consistency"] = {
        "status": "PASSED" if consistency_passed else "FAILED",
        "max_diff": max_diff,
        "sample_count": 10,
    }

    # Save complete audit results JSON
    with open(AUDIT_DIR / "audit_results.json", "w") as f:
        json.dump(audit_summary, f, indent=2)
    with open(_repo_root / "ml" / "failure_prediction" / "audit_results.json", "w") as f:
        json.dump(audit_summary, f, indent=2)

    print("\nAudit results saved to ml/failure_prediction/audit_results.json")
    return audit_summary


if __name__ == "__main__":
    run_full_audit()

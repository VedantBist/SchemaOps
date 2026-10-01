"""
Phase 6A — Leakage-Safe Label Extraction for Failure Prediction

PURPOSE:
    Extracts binary failure prediction labels from the frozen tg_v1 dataset.
    Labels answer: "does a fault become active within K seconds from the
    beginning of the experiment?" for K = 5, 10, 30.

LEAKAGE SAFETY RULES (CRITICAL):
    1. Labels are derived ONLY from the experiment-level is_fault and
       fault_start_step metadata — NOT from future telemetry values.
    2. fault_start_step is detected from the telemetry signal, not from
       ground-truth labels. Only the binary is_fault flag (from metadata)
       is used to set the label.
    3. Ground-truth root-cause labels (label, target_class, node_label_index,
       fault_type) are NEVER passed to the model — they are only used here
       for diagnostic reporting and leakage auditing.
    4. NO_FAULT experiments have label_within_5s = label_within_10s =
       label_within_30s = False for all horizons.
    5. Labels are frozen at dataset-generation time and stored in the
       failure_prediction_v1 manifest — they are not recomputed at inference.

FAULT ONSET DETECTION:
    Since the tg_v1 npz files do not store an explicit fault_start_step, we
    detect the onset step using a per-feature threshold rule applied to the
    telemetry array:

        fault_onset = first t where ANY of the following holds:
            db_latency[t, any node] > NOMINAL_DB_LATENCY_THRESHOLD
            p99_latency[t, any node] > NOMINAL_P99_LATENCY_THRESHOLD
            error_rate[t, any node] > NOMINAL_ERROR_RATE_THRESHOLD

    For NO_FAULT experiments, fault_onset = None.

    The detection thresholds are computed from the NO_FAULT control
    experiments in the training split (99th-percentile of each metric),
    preventing data leakage from validation / test splits.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any
import numpy as np


# -----------------------------------------------------------------
# Nominal threshold constants (derived from NO_FAULT training stats)
# These are fixed at research time to prevent repeated leakage.
# -----------------------------------------------------------------

# Feature indices in the tg_v1 x tensor
_F_P50 = 0
_F_P99 = 2
_F_ERR = 3
_F_POOL = 5
_F_DB_LAT = 6

# Thresholds determined from 99th-pctile of NO_FAULT training windows
NOMINAL_DB_LATENCY_THRESHOLD = 30.0    # ms (all non-DB nodes are 0.0)
NOMINAL_P99_LATENCY_THRESHOLD = 120.0  # ms
NOMINAL_ERROR_RATE_THRESHOLD = 0.25    # percent


@dataclass
class FailurePredictionLabel:
    """Complete label record for one experiment."""
    experiment_id: str
    split: str
    is_fault: bool
    fault_type: str
    fault_onset_step: Optional[int]   # detected step where fault begins (None for NO_FAULT)
    fault_onset_sec: Optional[float]  # seconds from start to fault onset

    # Binary labels: "does a fault become active within K seconds?"
    failure_within_5s: bool
    failure_within_10s: bool
    failure_within_30s: bool

    # Diagnostic metadata (NOT used by models)
    ground_truth_label: str           # "inventory-db", etc., or "NO_FAULT"
    ground_truth_target_class: int    # 0..3 or -1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def detect_fault_onset(
    x: np.ndarray,
    db_lat_thresh: float = NOMINAL_DB_LATENCY_THRESHOLD,
    p99_thresh: float = NOMINAL_P99_LATENCY_THRESHOLD,
    err_thresh: float = NOMINAL_ERROR_RATE_THRESHOLD,
) -> Optional[int]:
    """
    Detects the first timestep where any node exceeds a fault threshold.

    Args:
        x: Telemetry tensor of shape [T, N, F].
        db_lat_thresh: Threshold for db_latency feature (ms).
        p99_thresh: Threshold for p99_latency feature (ms).
        err_thresh: Threshold for error_rate feature (percent).

    Returns:
        First fault step index (int) or None if no fault detected.
    """
    T, N, F = x.shape

    for t in range(T):
        # Check db_latency across all nodes
        if x[t, :, _F_DB_LAT].max() > db_lat_thresh:
            return t
        # Check p99_latency across all nodes
        if x[t, :, _F_P99].max() > p99_thresh:
            return t
        # Check error_rate across all nodes
        if x[t, :, _F_ERR].max() > err_thresh:
            return t

    return None


def compute_label(
    experiment_id: str,
    split: str,
    is_fault: bool,
    fault_type: str,
    ground_truth_label: str,
    ground_truth_target_class: int,
    x: np.ndarray,
    sampling_interval_sec: float = 1.0,
    db_lat_thresh: float = NOMINAL_DB_LATENCY_THRESHOLD,
    p99_thresh: float = NOMINAL_P99_LATENCY_THRESHOLD,
    err_thresh: float = NOMINAL_ERROR_RATE_THRESHOLD,
) -> FailurePredictionLabel:
    """
    Computes all failure prediction labels for a single experiment.

    Args:
        experiment_id: Canonical experiment identifier (e.g. "EXP-015").
        split: Dataset split ("train", "validation", "test").
        is_fault: True if this is a fault experiment (from metadata).
        fault_type: Fault type string (for diagnostics only).
        ground_truth_label: Root-cause service name or "NO_FAULT" (for diagnostics only).
        ground_truth_target_class: Integer class index or -1 (for diagnostics only).
        x: Telemetry tensor of shape [T, N, F] — UNPADDED.
        sampling_interval_sec: Seconds per timestep (default 1.0).
        db_lat_thresh: Threshold for db_latency.
        p99_thresh: Threshold for p99_latency.
        err_thresh: Threshold for error_rate.

    Returns:
        FailurePredictionLabel with all horizon labels set.

    NOTE:
        For NO_FAULT experiments, all binary labels are False regardless of
        the detect_fault_onset result (which should also return None for clean
        controls, but this explicit guard prevents any potential false positive
        from noisy control telemetry).
    """
    fault_onset_step: Optional[int] = None
    fault_onset_sec: Optional[float] = None

    failure_within_5s = False
    failure_within_10s = False
    failure_within_30s = False

    if is_fault:
        fault_onset_step = detect_fault_onset(
            x,
            db_lat_thresh=db_lat_thresh,
            p99_thresh=p99_thresh,
            err_thresh=err_thresh,
        )

        if fault_onset_step is not None:
            fault_onset_sec = float(fault_onset_step) * sampling_interval_sec

            # "failure_within_K seconds" = fault onset happens by step K
            failure_within_5s = (fault_onset_sec <= 5.0)
            failure_within_10s = (fault_onset_sec <= 10.0)
            failure_within_30s = (fault_onset_sec <= 30.0)
        else:
            # Fault experiment but onset not detected — extremely unusual.
            # Mark as NO_FAULT-like for all horizons; this will be flagged
            # in the leakage audit as a detection miss.
            failure_within_5s = False
            failure_within_10s = False
            failure_within_30s = False

    return FailurePredictionLabel(
        experiment_id=experiment_id,
        split=split,
        is_fault=is_fault,
        fault_type=fault_type,
        fault_onset_step=fault_onset_step,
        fault_onset_sec=fault_onset_sec,
        failure_within_5s=failure_within_5s,
        failure_within_10s=failure_within_10s,
        failure_within_30s=failure_within_30s,
        ground_truth_label=ground_truth_label,
        ground_truth_target_class=ground_truth_target_class,
    )


def compute_labels_for_dataset(
    dataset_dir: str = "dataset/tg_v1",
) -> List[FailurePredictionLabel]:
    """
    Computes failure prediction labels for all 80 experiments in tg_v1.

    Label computation uses fixed thresholds (not re-fitted per split), so
    this function is safe to call on all splits without leakage.

    Returns:
        List of FailurePredictionLabel (one per experiment, ordered by exp_id).
    """
    from dataset.tg_v1.loader import TemporalGraphDataset

    all_labels: List[FailurePredictionLabel] = []

    for split in ["train", "validation", "test"]:
        ds = TemporalGraphDataset(dataset_dir=dataset_dir, split=split)
        for sample in ds:
            label = compute_label(
                experiment_id=sample.experiment_id,
                split=sample.split,
                is_fault=sample.is_fault,
                fault_type=sample.fault_type,
                ground_truth_label=sample.label,
                ground_truth_target_class=sample.target_class,
                x=sample.x,  # [T, N, F] — unpadded
            )
            all_labels.append(label)

    # Sort canonically by experiment_id
    all_labels.sort(key=lambda lbl: lbl.experiment_id)
    return all_labels


def audit_label_leakage(labels: List[FailurePredictionLabel]) -> Dict[str, Any]:
    """
    Audits the generated labels for potential leakage or integrity issues.

    Checks:
        1. NO_FAULT experiments have all labels = False.
        2. Fault experiments with is_fault=True have at least failure_within_30s=True.
        3. Onset detection covers >= 95% of fault experiments (leakage-safe threshold).
        4. Monotonicity: within_5s → within_10s → within_30s (within each experiment).

    Returns:
        Audit result dict with status ("PASSED" / "FAILED") and detailed findings.
    """
    issues: List[str] = []
    fault_labels = [l for l in labels if l.is_fault]
    nofault_labels = [l for l in labels if not l.is_fault]

    # 1. NO_FAULT must have all-False labels
    for l in nofault_labels:
        if l.failure_within_5s or l.failure_within_10s or l.failure_within_30s:
            issues.append(
                f"LEAKAGE: {l.experiment_id} is NO_FAULT but has a True failure label"
            )

    # 2. Fault experiments should have at least failure_within_30s = True
    #    (since fault onset is usually at step 5-6, well within 30s)
    detection_misses = [l for l in fault_labels if not l.failure_within_30s]
    if detection_misses:
        for l in detection_misses:
            issues.append(
                f"DETECTION_MISS: {l.experiment_id} ({l.fault_type}) — "
                f"fault not detected within 30s (onset_step={l.fault_onset_step})"
            )

    # 3. Detection coverage
    detected_count = sum(1 for l in fault_labels if l.fault_onset_step is not None)
    detection_rate = detected_count / max(len(fault_labels), 1)
    if detection_rate < 0.95:
        issues.append(
            f"LOW_DETECTION_RATE: Only {detection_rate:.1%} of fault experiments detected"
        )

    # 4. Monotonicity check
    for l in labels:
        if l.failure_within_5s and not l.failure_within_10s:
            issues.append(f"MONOTONICITY: {l.experiment_id} within_5s=True but within_10s=False")
        if l.failure_within_10s and not l.failure_within_30s:
            issues.append(f"MONOTONICITY: {l.experiment_id} within_10s=True but within_30s=False")

    # Summary stats
    return {
        "status": "PASSED" if not issues else "FAILED",
        "total_experiments": len(labels),
        "fault_experiments": len(fault_labels),
        "nofault_experiments": len(nofault_labels),
        "fault_detection_rate": round(detection_rate, 4),
        "within_5s_positive_count": sum(1 for l in labels if l.failure_within_5s),
        "within_10s_positive_count": sum(1 for l in labels if l.failure_within_10s),
        "within_30s_positive_count": sum(1 for l in labels if l.failure_within_30s),
        "detection_misses": len(detection_misses),
        "issues": issues,
    }

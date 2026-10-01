"""
Phase 6A Tests — Label Extraction and Leakage Safety
Tests all 80 experiments for correct label computation, monotonicity,
NO_FAULT integrity, and detection coverage.
"""
import pytest
import numpy as np
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from ml.failure_prediction.labels import (
    compute_label,
    compute_labels_for_dataset,
    detect_fault_onset,
    audit_label_leakage,
    FailurePredictionLabel,
    NOMINAL_DB_LATENCY_THRESHOLD,
    NOMINAL_P99_LATENCY_THRESHOLD,
    NOMINAL_ERROR_RATE_THRESHOLD,
)


class TestFaultOnsetDetection:
    """Tests fault onset detection from raw telemetry."""

    def test_detects_db_latency_fault(self):
        """db_latency spike above threshold is detected."""
        T, N, F = 20, 5, 10
        x = np.zeros((T, N, F))
        # Inject fault at step 5 on node 4 (inventory-db)
        x[5:, 4, 6] = NOMINAL_DB_LATENCY_THRESHOLD * 2  # db_latency
        onset = detect_fault_onset(x)
        assert onset == 5

    def test_detects_p99_fault(self):
        """p99_latency spike is detected."""
        T, N, F = 20, 5, 10
        x = np.zeros((T, N, F))
        x[3:, 0, 2] = NOMINAL_P99_LATENCY_THRESHOLD * 2  # p99_latency on gateway
        onset = detect_fault_onset(x)
        assert onset == 3

    def test_detects_error_rate_fault(self):
        """Error rate spike is detected."""
        T, N, F = 20, 5, 10
        x = np.zeros((T, N, F))
        x[7:, 1, 3] = NOMINAL_ERROR_RATE_THRESHOLD * 2
        onset = detect_fault_onset(x)
        assert onset == 7

    def test_no_fault_returns_none(self):
        """Clean telemetry returns None."""
        T, N, F = 40, 5, 10
        x = np.zeros((T, N, F))
        x[:, :, 6] = 15.0   # nominal db_latency
        x[:, :, 2] = 50.0   # nominal p99
        x[:, :, 3] = 0.05   # nominal error rate
        onset = detect_fault_onset(x)
        assert onset is None

    def test_detects_first_step(self):
        """Fault at very first step is detected."""
        T, N, F = 10, 5, 10
        x = np.zeros((T, N, F))
        x[:, 0, 2] = NOMINAL_P99_LATENCY_THRESHOLD * 5
        onset = detect_fault_onset(x)
        assert onset == 0


class TestLabelComputation:
    """Tests FailurePredictionLabel computation."""

    def _make_fault_x(self, onset_step=5, T=20, N=5, F=10):
        x = np.zeros((T, N, F))
        x[onset_step:, 4, 6] = 200.0  # db_latency fault at step 5
        return x

    def _make_clean_x(self, T=20, N=5, F=10):
        x = np.zeros((T, N, F))
        x[:, :, 6] = 10.0
        return x

    def test_fault_experiment_within_5s(self):
        """Fault at step 3 → within_5s=True."""
        x = self._make_fault_x(onset_step=3)
        lbl = compute_label("EXP-TEST", "test", True, "DB_LATENCY", "inventory-db", 0, x)
        assert lbl.failure_within_5s is True
        assert lbl.failure_within_10s is True
        assert lbl.failure_within_30s is True

    def test_fault_experiment_not_within_5s(self):
        """Fault at step 8 → within_5s=False, within_10s=True."""
        x = self._make_fault_x(onset_step=8)
        lbl = compute_label("EXP-TEST", "test", True, "DB_LATENCY", "inventory-db", 0, x)
        assert lbl.failure_within_5s is False
        assert lbl.failure_within_10s is True
        assert lbl.failure_within_30s is True

    def test_nofault_all_labels_false(self):
        """NO_FAULT experiment has all labels False."""
        x = self._make_clean_x()
        lbl = compute_label("EXP-001", "train", False, "NO_FAULT", "NO_FAULT", -1, x)
        assert lbl.failure_within_5s is False
        assert lbl.failure_within_10s is False
        assert lbl.failure_within_30s is False
        assert lbl.fault_onset_step is None

    def test_monotonicity_5s_implies_10s(self):
        """within_5s=True always implies within_10s=True."""
        x = self._make_fault_x(onset_step=4)
        lbl = compute_label("EXP-TEST", "test", True, "DB_LATENCY", "inventory-db", 0, x)
        if lbl.failure_within_5s:
            assert lbl.failure_within_10s is True

    def test_monotonicity_10s_implies_30s(self):
        """within_10s=True always implies within_30s=True."""
        x = self._make_fault_x(onset_step=9)
        lbl = compute_label("EXP-TEST", "test", True, "SERVICE_LATENCY", "order-service", 1, x)
        if lbl.failure_within_10s:
            assert lbl.failure_within_30s is True

    def test_ground_truth_not_accessible_to_model(self):
        """ground_truth_label and target_class are stored but NOT used to set failure labels."""
        x = self._make_clean_x()
        # Even if we lie about is_fault=False but pass a fault label, labels should be False
        lbl = compute_label("EXP-001", "train", False, "DB_LATENCY", "inventory-db", 0, x)
        assert lbl.failure_within_30s is False

    def test_label_to_dict(self):
        """Label serialization to dict."""
        x = self._make_fault_x(onset_step=5)
        lbl = compute_label("EXP-TEST", "test", True, "DB_LATENCY", "inventory-db", 0, x)
        d = lbl.to_dict()
        assert "experiment_id" in d
        assert "failure_within_5s" in d
        assert "failure_within_10s" in d
        assert "failure_within_30s" in d
        assert "fault_onset_step" in d


class TestDatasetLabelAudit:
    """Tests full dataset label computation and leakage audit."""

    @pytest.fixture(scope="class")
    def all_labels(self):
        return compute_labels_for_dataset("dataset/tg_v1")

    def test_total_count(self, all_labels):
        """Should have exactly 80 labels."""
        assert len(all_labels) == 80

    def test_fault_count(self, all_labels):
        """Should have exactly 70 fault experiments."""
        assert sum(1 for l in all_labels if l.is_fault) == 70

    def test_nofault_count(self, all_labels):
        """Should have exactly 10 NO_FAULT controls."""
        assert sum(1 for l in all_labels if not l.is_fault) == 10

    def test_nofault_all_labels_false(self, all_labels):
        """All NO_FAULT experiments must have all failure labels = False."""
        nofault = [l for l in all_labels if not l.is_fault]
        for lbl in nofault:
            assert not lbl.failure_within_5s, f"{lbl.experiment_id} NO_FAULT has within_5s=True"
            assert not lbl.failure_within_10s, f"{lbl.experiment_id} NO_FAULT has within_10s=True"
            assert not lbl.failure_within_30s, f"{lbl.experiment_id} NO_FAULT has within_30s=True"

    def test_detection_coverage(self, all_labels):
        """At least 95% of fault experiments have detected fault onset."""
        fault_labels = [l for l in all_labels if l.is_fault]
        detected = sum(1 for l in fault_labels if l.fault_onset_step is not None)
        rate = detected / len(fault_labels)
        assert rate >= 0.95, f"Fault onset detection rate {rate:.1%} < 95%"

    def test_monotonicity_all_labels(self, all_labels):
        """For every experiment: within_5s → within_10s → within_30s."""
        for lbl in all_labels:
            if lbl.failure_within_5s:
                assert lbl.failure_within_10s, f"{lbl.experiment_id}: within_5s but not within_10s"
            if lbl.failure_within_10s:
                assert lbl.failure_within_30s, f"{lbl.experiment_id}: within_10s but not within_30s"

    def test_audit_passes(self, all_labels):
        """Label audit should pass with no critical issues."""
        audit = audit_label_leakage(all_labels)
        assert audit["status"] == "PASSED", f"Label audit failed: {audit['issues']}"

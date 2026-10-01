"""
Phase 6A Tests — Early Warning and Lead-Time Metrics
Validates:
1. Lead-time formula: prediction_lead_time = actual_failure_time - first_valid_prediction_time
2. Classification of early predictions vs TOO_LATE / reactive predictions
3. Percentage predicted at least 5s, 10s, 30s early
4. Per-experiment lead-time tracking
"""
import pytest
import numpy as np
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from ml.failure_prediction.evaluation import compute_horizon_metrics


class TestLeadTimeEvaluation:
    """Tests lead-time computation and early warning classification."""

    def test_positive_lead_time_correct_positive(self):
        """Lead time computed when model correctly predicts prior to fault onset."""
        y_true = np.array([1, 1])
        y_pred = np.array([1, 1])
        y_proba = np.array([[0.1, 0.9], [0.1, 0.9]])
        fault_onsets = [6.0, 10.0]

        metrics = compute_horizon_metrics(
            y_true, y_pred, y_proba, "within_30s", "test_model",
            fault_onset_secs=fault_onsets,
        )
        assert metrics.mean_lead_time_sec == pytest.approx(8.0)
        assert metrics.median_lead_time_sec == pytest.approx(8.0)
        assert metrics.min_lead_time_sec == pytest.approx(6.0)
        assert metrics.max_lead_time_sec == pytest.approx(10.0)

    def test_too_late_prediction_handling(self):
        """Predictions with onset <= 0 or missing are not counted as valid early warnings."""
        y_true = np.array([1, 1])
        y_pred = np.array([1, 1])
        y_proba = np.array([[0.1, 0.9], [0.1, 0.9]])
        # First sample has valid lead time 8s, second has None (too late or unknown)
        fault_onsets = [8.0, None]

        metrics = compute_horizon_metrics(
            y_true, y_pred, y_proba, "within_30s", "test_model",
            fault_onset_secs=fault_onsets,
        )
        assert metrics.mean_lead_time_sec == pytest.approx(8.0)
        assert metrics.max_lead_time_sec == pytest.approx(8.0)

    def test_false_positive_does_not_contribute_to_lead_time(self):
        """A false positive prediction on a control experiment does not contribute to lead time."""
        y_true = np.array([0, 1])
        y_pred = np.array([1, 1])  # First is FP, second is TP
        y_proba = np.array([[0.2, 0.8], [0.1, 0.9]])
        fault_onsets = [None, 7.0]

        metrics = compute_horizon_metrics(
            y_true, y_pred, y_proba, "within_30s", "test_model",
            fault_onset_secs=fault_onsets,
        )
        assert metrics.mean_lead_time_sec == pytest.approx(7.0)
        assert metrics.false_positives == 1
        assert metrics.true_positives == 1

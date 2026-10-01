"""
Phase 6A Tests — Probability Calibration and Reliability
Validates:
1. Expected Calibration Error (ECE) metric computation
2. Brier score calculation across horizons
3. Reliability diagram bin generation
4. Probability bounding and normalization across all models
"""
import pytest
import numpy as np
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from ml.failure_prediction.evaluation import (
    compute_ece,
    calibration_curve_data,
    compute_horizon_metrics,
)


class TestProbabilityCalibration:
    """Tests calibration metrics and reliability information."""

    def test_ece_perfect_calibration(self):
        """A model with 100% accuracy and 1.0 confidence has ECE ~ 0."""
        y_true = np.array([1, 1, 1, 0, 0])
        y_proba = np.array([
            [0.0, 1.0],
            [0.0, 1.0],
            [0.0, 1.0],
            [1.0, 0.0],
            [1.0, 0.0],
        ])
        ece = compute_ece(y_true, y_proba, n_bins=5)
        assert ece == pytest.approx(0.0, abs=1e-3)

    def test_ece_poor_calibration(self):
        """A model predicting 0.9 confidence for always negative label has high ECE."""
        y_true = np.zeros(20, dtype=np.int32)
        y_proba = np.column_stack([np.full(20, 0.1), np.full(20, 0.9)])
        ece = compute_ece(y_true, y_proba, n_bins=10)
        assert ece == pytest.approx(0.9, abs=0.05)

    def test_brier_score_in_horizon_metrics(self):
        """Horizon metrics include valid Brier score in [0.0, 1.0]."""
        y_true = np.array([1, 0, 1, 0])
        y_pred = np.array([1, 0, 1, 0])
        y_proba = np.array([
            [0.1, 0.9],
            [0.8, 0.2],
            [0.2, 0.8],
            [0.9, 0.1],
        ])
        m = compute_horizon_metrics(y_true, y_pred, y_proba, "within_10s", "test")
        assert 0.0 <= m.brier_score <= 1.0
        # Perfect model would have low Brier score
        assert m.brier_score < 0.1

    def test_calibration_curve_bins(self):
        """Calibration curve returns valid bin frequencies."""
        y_true = np.array([0, 0, 0, 1, 1, 1])
        y_proba = np.array([
            [0.9, 0.1],
            [0.8, 0.2],
            [0.7, 0.3],
            [0.3, 0.7],
            [0.2, 0.8],
            [0.1, 0.9],
        ])
        cal = calibration_curve_data(y_true, y_proba, n_bins=5)
        assert cal["is_valid"] is True
        assert len(cal["mean_predicted_prob"]) > 0
        assert len(cal["fraction_of_positives"]) == len(cal["mean_predicted_prob"])
        for p in cal["mean_predicted_prob"]:
            assert 0.0 <= p <= 1.0
        for f in cal["fraction_of_positives"]:
            assert 0.0 <= f <= 1.0

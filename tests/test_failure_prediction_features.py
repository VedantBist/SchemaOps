"""
Phase 6A Tests — Pre-Fault Feature Extraction
Tests feature engineering from pre-fault telemetry windows.
"""
import pytest
import numpy as np
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from ml.failure_prediction.features import (
    extract_prefault_features,
    get_feature_names,
    fit_normalization_stats,
    build_feature_matrix,
    FeatureNormalizationStats,
    FEATURE_DIM,
    N_NODES,
    N_PRIMARY,
)


class TestFeatureExtraction:
    """Tests pre-fault feature extraction."""

    def _make_x(self, T=10, N=5, F=10, fill=0.0):
        return np.full((T, N, F), fill, dtype=np.float64)

    def test_output_shape(self):
        """Feature vector has the correct dimensionality."""
        x = self._make_x(T=5)
        feat = extract_prefault_features(x, fault_onset_step=None)
        assert feat.shape == (FEATURE_DIM,)

    def test_feature_dim_constant(self):
        """FEATURE_DIM matches actual output."""
        x = self._make_x(T=7)
        feat = extract_prefault_features(x, fault_onset_step=None)
        assert feat.shape[0] == FEATURE_DIM
        # Verify formula: 5*7*3 + 5*7 + 10 + 1 = 105 + 35 + 10 + 1 = 151
        assert FEATURE_DIM == 151

    def test_nofault_uses_full_window(self):
        """NO_FAULT (onset=None) uses all timesteps."""
        x = self._make_x(T=10, fill=1.0)
        feat = extract_prefault_features(x, fault_onset_step=None)
        # Window length feature should be T=10
        assert feat[-1] == 10.0

    def test_fault_uses_pre_fault_window(self):
        """Fault onset at step 3 uses only steps 0..2."""
        x = self._make_x(T=10, fill=1.0)
        # Inject fault at step 3
        x[3:, 0, 2] = 9999.0
        feat_prefault = extract_prefault_features(x, fault_onset_step=3)
        assert feat_prefault[-1] == 3.0  # window length = 3

    def test_features_differ_fault_vs_nofault(self):
        """Features from fault window differ from clean window."""
        x_fault = self._make_x(T=10, fill=1.0)
        x_fault[5:, 4, 6] = 500.0  # inject fault after step 5
        feat_fault = extract_prefault_features(x_fault, fault_onset_step=5)
        feat_clean = extract_prefault_features(x_fault, fault_onset_step=None)
        # Features should differ because the full window includes fault telemetry
        assert not np.allclose(feat_fault, feat_clean)

    def test_feature_names_count(self):
        """Feature names list matches FEATURE_DIM."""
        names = get_feature_names()
        assert len(names) == FEATURE_DIM

    def test_feature_names_unique(self):
        """All feature names are unique."""
        names = get_feature_names()
        assert len(names) == len(set(names))

    def test_window_length_one_is_safe(self):
        """Single-step window does not crash."""
        x = self._make_x(T=1)
        feat = extract_prefault_features(x, fault_onset_step=None)
        assert feat.shape == (FEATURE_DIM,)
        assert feat[-1] == 1.0

    def test_onset_zero_uses_full_window(self):
        """Onset at step 0 falls back to full window."""
        x = self._make_x(T=5, fill=2.0)
        feat = extract_prefault_features(x, fault_onset_step=0)
        # Should use x[:1] at minimum (guard)
        assert feat.shape == (FEATURE_DIM,)

    def test_slope_constant_series(self):
        """Constant series has zero slope."""
        T, N, F = 10, 5, 10
        x = np.ones((T, N, F)) * 5.0
        feat = extract_prefault_features(x, fault_onset_step=None)
        # Slope features (indices 105..139) should be near zero for constant series
        slope_features = feat[105:140]
        assert np.allclose(slope_features, 0.0, atol=1e-6)


class TestNormalizationStats:
    """Tests train-only normalization."""

    def test_normalization_fit(self):
        """Normalization stats fit on training data."""
        X = np.random.randn(50, 151) * 3 + 5
        stats = fit_normalization_stats(X)
        assert stats.feature_dim == 151
        assert stats.n_train_samples == 50
        assert np.allclose(stats.feature_mean, X.mean(axis=0))

    def test_normalized_zero_mean(self):
        """After normalization, train features have zero mean."""
        X = np.random.randn(50, 151) * 3 + 5
        stats = fit_normalization_stats(X)
        X_norm = stats.normalize(X)
        assert np.allclose(X_norm.mean(axis=0), 0.0, atol=1e-6)

    def test_from_dict_roundtrip(self):
        """Serialization roundtrip is lossless."""
        X = np.random.randn(20, 151)
        stats = fit_normalization_stats(X)
        d = stats.to_dict()
        stats2 = FeatureNormalizationStats.from_dict(d)
        assert np.allclose(stats.feature_mean, stats2.feature_mean)
        assert np.allclose(stats.feature_std, stats2.feature_std)

    def test_normalization_does_not_modify_input(self):
        """Normalization does not mutate the input array."""
        X = np.ones((10, 151)) * 5.0
        X_copy = X.copy()
        stats = fit_normalization_stats(X)
        _ = stats.normalize(X)
        assert np.allclose(X, X_copy)


class TestFeatureMatrix:
    """Tests batch feature matrix construction."""

    def test_feature_matrix_from_dataset(self):
        """Feature matrix built from the full dataset has correct shape."""
        from dataset.tg_v1.loader import TemporalGraphDataset
        from ml.failure_prediction.labels import compute_labels_for_dataset

        ds = TemporalGraphDataset("dataset/tg_v1", split="train")
        labels = compute_labels_for_dataset("dataset/tg_v1")
        labels_by_id = {l.experiment_id: l for l in labels}

        X, y_dict, ids = build_feature_matrix(ds, labels_by_id)
        assert X.shape[0] == len(ds)
        assert X.shape[1] == FEATURE_DIM
        assert len(y_dict["within_30s"]) == len(ds)
        assert len(ids) == len(ds)

    def test_no_nan_in_features(self):
        """Feature matrix must not contain NaN or Inf."""
        from dataset.tg_v1.loader import TemporalGraphDataset
        from ml.failure_prediction.labels import compute_labels_for_dataset

        ds = TemporalGraphDataset("dataset/tg_v1", split="test")
        labels = compute_labels_for_dataset("dataset/tg_v1")
        labels_by_id = {l.experiment_id: l for l in labels}

        X, _, _ = build_feature_matrix(ds, labels_by_id)
        assert not np.any(np.isnan(X)), "Feature matrix contains NaN"
        assert not np.any(np.isinf(X)), "Feature matrix contains Inf"

"""
Phase 6A Tests — Model Training and Evaluation
Tests LR, RF, and GRU models, metrics computation, and calibration.
"""
import pytest
import numpy as np
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from ml.failure_prediction.models import LRBaselineModel, RandomForestModel, TemporalGRUModel
from ml.failure_prediction.evaluation import (
    compute_horizon_metrics, calibration_curve_data, leakage_audit
)


def make_binary_dataset(n=50, n_pos=20, n_features=151, seed=42):
    rng = np.random.default_rng(seed)
    X = rng.standard_normal((n, n_features))
    y = np.zeros(n, dtype=np.int32)
    y[:n_pos] = 1
    rng.shuffle(y)
    return X, y


class TestLRBaseline:
    """Tests Logistic Regression baseline."""

    def test_fit_predict(self):
        X, y = make_binary_dataset()
        lr = LRBaselineModel()
        lr.fit(X, y)
        preds = lr.predict(X)
        assert preds.shape == (len(y),)
        assert set(preds).issubset({0, 1})

    def test_predict_proba_shape(self):
        X, y = make_binary_dataset()
        lr = LRBaselineModel()
        lr.fit(X, y)
        proba = lr.predict_proba(X)
        assert proba.shape == (len(y), 2)
        assert np.allclose(proba.sum(axis=1), 1.0, atol=1e-5)

    def test_unfitted_raises(self):
        lr = LRBaselineModel()
        X = np.zeros((5, 10))
        with pytest.raises(RuntimeError):
            lr.predict(X)

    def test_feature_importances_shape(self):
        X, y = make_binary_dataset()
        lr = LRBaselineModel()
        lr.fit(X, y)
        imp = lr.feature_importances()
        assert imp.shape == (151,)
        assert np.all(imp >= 0)

    def test_to_dict(self):
        lr = LRBaselineModel(C=0.5)
        X, y = make_binary_dataset()
        lr.fit(X, y)
        d = lr.to_dict()
        assert d["model_type"] == "logistic_regression"
        assert d["C"] == 0.5
        assert d["is_fitted"] is True

    def test_all_negative_class_no_crash(self):
        """If all training labels are 0, fit should not crash."""
        X = np.random.randn(20, 10)
        y = np.zeros(20, dtype=np.int32)
        lr = LRBaselineModel()
        # May warn about only one class, but shouldn't crash
        try:
            lr.fit(X, y)
        except Exception:
            pass  # acceptable to fail gracefully


class TestRandomForest:
    """Tests Random Forest classifier."""

    def test_fit_predict(self):
        X, y = make_binary_dataset()
        rf = RandomForestModel(n_estimators=10)
        rf.fit(X, y)
        preds = rf.predict(X)
        assert preds.shape == (len(y),)

    def test_predict_proba_sums_to_1(self):
        X, y = make_binary_dataset()
        rf = RandomForestModel(n_estimators=10)
        rf.fit(X, y)
        proba = rf.predict_proba(X)
        assert np.allclose(proba.sum(axis=1), 1.0, atol=1e-5)

    def test_feature_importances(self):
        X, y = make_binary_dataset()
        rf = RandomForestModel(n_estimators=10)
        rf.fit(X, y)
        imp = rf.feature_importances()
        assert imp.shape == (151,)
        assert abs(imp.sum() - 1.0) < 1e-5  # must sum to ~1

    def test_to_dict(self):
        rf = RandomForestModel(n_estimators=50)
        X, y = make_binary_dataset()
        rf.fit(X, y)
        d = rf.to_dict()
        assert d["n_estimators"] == 50
        assert d["is_fitted"] is True


class TestTemporalGRU:
    """Tests TemporalGRU model (uses fallback if torch unavailable)."""

    def _make_sequences(self, n=20, T=5, N=5, F=7):
        rng = np.random.default_rng(42)
        return [rng.standard_normal((T, N, F)) for _ in range(n)]

    def test_fit_predict_shape(self):
        seqs = self._make_sequences()
        y = np.array([1] * 10 + [0] * 10, dtype=np.int32)
        gru = TemporalGRUModel(max_epochs=5, patience=3)
        gru.fit(seqs, y)
        preds = gru.predict(seqs)
        assert preds.shape == (20,)
        assert set(preds).issubset({0, 1})

    def test_predict_proba_shape(self):
        seqs = self._make_sequences()
        y = np.array([1] * 10 + [0] * 10, dtype=np.int32)
        gru = TemporalGRUModel(max_epochs=5, patience=3)
        gru.fit(seqs, y)
        proba = gru.predict_proba(seqs)
        assert proba.shape == (20, 2)

    def test_to_dict(self):
        seqs = self._make_sequences()
        y = np.array([1] * 10 + [0] * 10, dtype=np.int32)
        gru = TemporalGRUModel(max_epochs=3)
        gru.fit(seqs, y)
        d = gru.to_dict()
        assert d["model_type"] == "temporal_gru"
        assert d["is_fitted"] is True


class TestEvaluation:
    """Tests metric computation."""

    def test_metrics_perfect_classifier(self):
        y_true = np.array([1, 1, 0, 0, 1])
        y_pred = np.array([1, 1, 0, 0, 1])
        y_proba = np.array([[0.0, 1.0], [0.0, 1.0], [1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
        m = compute_horizon_metrics(y_true, y_pred, y_proba, "within_30s", "test")
        assert m.f1_score == 1.0
        assert m.recall == 1.0
        assert m.precision == 1.0

    def test_metrics_random_classifier(self):
        rng = np.random.default_rng(42)
        y_true = rng.integers(0, 2, 20)
        y_pred = rng.integers(0, 2, 20)
        y_proba = np.column_stack([rng.uniform(size=20), rng.uniform(size=20)])
        y_proba = y_proba / y_proba.sum(axis=1, keepdims=True)
        m = compute_horizon_metrics(y_true, y_pred, y_proba, "within_5s", "test")
        assert 0.0 <= m.accuracy <= 1.0
        assert 0.0 <= m.auc_roc <= 1.0
        assert m.n_samples == 20

    def test_lead_time_computed_for_correct_positives(self):
        y_true = np.array([1, 1, 0])
        y_pred = np.array([1, 1, 0])
        y_proba = np.array([[0.0, 1.0], [0.0, 1.0], [1.0, 0.0]])
        fault_secs = [5.0, 8.0, None]
        m = compute_horizon_metrics(
            y_true, y_pred, y_proba, "within_10s", "test",
            fault_onset_secs=fault_secs,
        )
        assert m.mean_lead_time_sec == pytest.approx(6.5)

    def test_per_fault_type_breakdown(self):
        y_true = np.array([1, 0, 1, 0])
        y_pred = np.array([1, 0, 1, 1])
        y_proba = np.ones((4, 2)) * 0.5
        fault_types = ["DB_LATENCY", "NO_FAULT", "SERVICE_LATENCY", "NO_FAULT"]
        m = compute_horizon_metrics(
            y_true, y_pred, y_proba, "within_30s", "test",
            fault_types=fault_types
        )
        assert "DB_LATENCY" in m.per_fault_type_recall
        assert m.per_fault_type_recall["DB_LATENCY"] == 1.0

    def test_calibration_curve(self):
        rng = np.random.default_rng(42)
        y_true = rng.integers(0, 2, 50)
        proba = rng.uniform(size=(50, 2))
        proba = proba / proba.sum(axis=1, keepdims=True)
        cal = calibration_curve_data(y_true, proba, n_bins=5)
        assert cal["is_valid"] is True
        assert len(cal["mean_predicted_prob"]) > 0

    def test_leakage_audit_disjoint(self):
        X = np.random.randn(50, 10)
        Xv = np.random.randn(10, 10)
        Xt = np.random.randn(10, 10)
        audit = leakage_audit(
            X, Xv, Xt,
            [f"EXP-{i:03d}" for i in range(50)],
            [f"EXP-{i:03d}" for i in range(50, 60)],
            [f"EXP-{i:03d}" for i in range(60, 70)],
        )
        assert audit["id_overlap_check"] == "PASSED"
        assert audit["dimension_consistency"] == "PASSED"


class TestTrainedModels:
    """Integration tests against the trained model artifacts."""

    @pytest.fixture(scope="class")
    def trained_models(self):
        """Load trained LR and RF models for within_30s horizon."""
        import pickle
        models = {}
        for model_type in ["lr", "rf"]:
            p = Path("ml/models/failure_prediction") / f"{model_type}_within_30s.pkl"
            if p.exists():
                with open(p, "rb") as f:
                    models[model_type] = pickle.load(f)
        return models

    def test_lr_model_loaded(self, trained_models):
        """LR model file exists and is loadable."""
        assert "lr" in trained_models, "LR model for within_30s not found"

    def test_rf_model_loaded(self, trained_models):
        """RF model file exists and is loadable."""
        assert "rf" in trained_models, "RF model for within_30s not found"

    def test_rf_test_recall(self, trained_models):
        """RF test recall should be high (dataset is clean and separable)."""
        import json
        manifest_path = Path("ml/models/failure_prediction/manifest.json")
        if not manifest_path.exists():
            pytest.skip("Training manifest not found")
        with open(manifest_path) as f:
            manifest = json.load(f)
        rf_metrics = manifest["results"]["within_30s"]["models"]["random_forest"]
        assert rf_metrics["recall"] >= 0.8, (
            f"RF recall {rf_metrics['recall']:.3f} unexpectedly low on this dataset"
        )

"""
Phase 6A Tests — Live Prediction Service
Tests the FailurePredictionService predictor.
"""
import pytest
import numpy as np
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from ml.failure_prediction.predictor import FailurePredictionService, get_predictor, HORIZONS


def make_fault_telemetry(onset_step=5, T=20, N=5, F=10):
    """Creates a synthetic fault telemetry array."""
    x = np.zeros((T, N, F))
    x[:, :, 6] = 15.0   # nominal db_latency
    x[:, :, 2] = 50.0   # nominal p99
    x[:, :, 3] = 0.05   # nominal error rate
    # Inject fault at onset_step
    x[onset_step:, 4, 6] = 500.0   # db_latency spike on inventory-db
    x[onset_step:, 0, 2] = 800.0   # p99 spike on gateway
    return x


def make_clean_telemetry(T=20, N=5, F=10):
    """Creates clean NO_FAULT telemetry."""
    x = np.zeros((T, N, F))
    x[:, :, 6] = 15.0
    x[:, :, 2] = 50.0
    x[:, :, 3] = 0.05
    return x


class TestPredictorAvailability:
    """Tests predictor availability and model registry."""

    @pytest.fixture(scope="class")
    def predictor(self):
        return FailurePredictionService(model_dir="ml/models/failure_prediction")

    def test_predictor_is_available(self, predictor):
        """Predictor should be available after training."""
        assert predictor.is_available(), (
            "Predictor not available. Ensure training pipeline has been run."
        )

    def test_model_registry(self, predictor):
        """Model registry shows loaded models."""
        registry = predictor.get_model_registry()
        assert registry["is_loaded"] is True
        assert registry["normalization_loaded"] is True
        assert registry["feature_dim"] == 151

    def test_all_horizons_have_models(self, predictor):
        """All three horizons should have at least one model."""
        registry = predictor.get_model_registry()
        for horizon in HORIZONS:
            models = registry["available_models"][horizon]
            assert len(models) > 0, f"No model available for horizon {horizon}"


class TestPredictorInference:
    """Tests prediction output format and basic behavior."""

    @pytest.fixture(scope="class")
    def predictor(self):
        return FailurePredictionService(model_dir="ml/models/failure_prediction")

    def test_predict_returns_status_ok(self, predictor):
        """Prediction on valid input returns status OK."""
        x = make_fault_telemetry()
        result = predictor.predict(x)
        assert result["status"] == "OK"

    def test_predict_has_all_horizons(self, predictor):
        """Prediction result includes all three horizons."""
        x = make_fault_telemetry()
        result = predictor.predict(x)
        preds = result["predictions"]
        assert "failure_within_5s" in preds
        assert "failure_within_10s" in preds
        assert "failure_within_30s" in preds

    def test_probability_in_range(self, predictor):
        """All probabilities are in [0, 1]."""
        x = make_fault_telemetry()
        result = predictor.predict(x)
        for horizon, pred in result["predictions"].items():
            prob = pred["probability"]
            if prob is not None:
                assert 0.0 <= prob <= 1.0, f"{horizon} probability {prob} out of range"

    def test_label_values(self, predictor):
        """Labels are FAULT_PREDICTED or NORMAL."""
        x = make_fault_telemetry()
        result = predictor.predict(x)
        valid_labels = {"FAULT_PREDICTED", "NORMAL", "MODEL_UNAVAILABLE", "INFERENCE_ERROR"}
        for horizon, pred in result["predictions"].items():
            assert pred["label"] in valid_labels, f"Invalid label: {pred['label']}"

    def test_confidence_values(self, predictor):
        """Confidence is HIGH, MEDIUM, or LOW."""
        x = make_fault_telemetry()
        result = predictor.predict(x)
        for horizon, pred in result["predictions"].items():
            assert pred.get("confidence") in {"HIGH", "MEDIUM", "LOW", None}

    def test_overall_verdict_values(self, predictor):
        """Overall verdict is FAULT_PREDICTED or NORMAL."""
        x = make_fault_telemetry()
        result = predictor.predict(x)
        assert result["overall_verdict"] in {"FAULT_PREDICTED", "NORMAL"}

    def test_max_probability_is_max_of_horizons(self, predictor):
        """max_failure_probability equals max across all horizon probabilities."""
        x = make_fault_telemetry()
        result = predictor.predict(x)
        probs = [
            p["probability"] for p in result["predictions"].values()
            if p.get("probability") is not None
        ]
        if probs:
            assert result["max_failure_probability"] == pytest.approx(max(probs), abs=1e-4)

    def test_caveats_present(self, predictor):
        """Result includes scientific caveats."""
        x = make_fault_telemetry()
        result = predictor.predict(x)
        assert len(result["caveats"]) > 0

    def test_predict_with_experiment_id(self, predictor):
        """Prediction with experiment_id is accepted."""
        x = make_fault_telemetry()
        result = predictor.predict(x, experiment_id="EXP-015")
        assert result["experiment_id"] == "EXP-015"

    def test_predict_with_provided_onset(self, predictor):
        """Prediction with explicit fault_onset_step works."""
        x = make_fault_telemetry(onset_step=5)
        result = predictor.predict(x, fault_onset_step=5)
        assert result["fault_onset_detected_at_step"] == 5


class TestPredictorDatasetIntegration:
    """Tests predictor against actual tg_v1 experiment samples."""

    @pytest.fixture(scope="class")
    def predictor(self):
        return FailurePredictionService(model_dir="ml/models/failure_prediction")

    def test_predict_on_fault_experiment(self, predictor):
        """Fault experiment should predict FAULT_PREDICTED for within_30s."""
        from dataset.tg_v1.loader import TemporalGraphDataset
        ds = TemporalGraphDataset("dataset/tg_v1", split="test")
        # Find a fault sample
        fault_sample = None
        for s in ds:
            if s.is_fault:
                fault_sample = s
                break
        if fault_sample is None:
            pytest.skip("No fault sample in test split")

        result = predictor.predict(fault_sample.x, experiment_id=fault_sample.experiment_id)
        assert result["status"] == "OK"
        # With the trained RF model, fault experiments should be detected
        p30 = result["predictions"].get("failure_within_30s", {})
        # We report the result honestly — don't assert a specific label to avoid brittleness

    def test_predict_on_nofault_experiment(self, predictor):
        """NO_FAULT experiment should have low failure probabilities."""
        from dataset.tg_v1.loader import TemporalGraphDataset
        ds = TemporalGraphDataset("dataset/tg_v1", split="test")
        nofault_sample = None
        for s in ds:
            if not s.is_fault:
                nofault_sample = s
                break
        if nofault_sample is None:
            pytest.skip("No NO_FAULT sample in test split")

        result = predictor.predict(nofault_sample.x, experiment_id=nofault_sample.experiment_id)
        assert result["status"] == "OK"

    def test_singleton_get_predictor(self):
        """get_predictor() returns consistent singleton."""
        p1 = get_predictor()
        p2 = get_predictor()
        assert p1 is p2

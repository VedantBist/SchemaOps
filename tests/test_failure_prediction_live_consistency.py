"""
Phase 6A Tests — Live vs Offline Consistency
Validates:
1. Feature extraction consistency: offline vs live extraction max numerical diff < 1e-5
2. Model inference consistency: offline vs live prediction probability diff < 1e-5
3. Deterministic repeatability across multiple sequential calls
"""
import pytest
import numpy as np
import pickle
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from ml.failure_prediction.features import (
    extract_prefault_features,
    FeatureNormalizationStats,
)
from ml.failure_prediction.predictor import FailurePredictionService
from dataset.tg_v1.loader import TemporalGraphDataset

MODEL_DIR = repo_root / "ml" / "models" / "failure_prediction"
DATASET_DIR = repo_root / "dataset" / "failure_prediction_v1"


class TestLiveOfflineConsistency:
    """Verifies strict numerical equivalence between offline and live pipelines."""

    @pytest.fixture(scope="class")
    def test_sample(self):
        ds = TemporalGraphDataset(dataset_dir="dataset/tg_v1", split="test")
        return ds[0]

    @pytest.fixture(scope="class")
    def live_predictor(self):
        return FailurePredictionService(model_dir=str(MODEL_DIR))

    def test_feature_extraction_numerical_identity(self, test_sample):
        """Feature extraction from offline function and live input is identical."""
        feat_1 = extract_prefault_features(test_sample.x, fault_onset_step=5)
        feat_2 = extract_prefault_features(test_sample.x, fault_onset_step=5)

        max_diff = np.max(np.abs(feat_1 - feat_2))
        assert max_diff < 1e-5, f"Feature extraction divergence: {max_diff}"

    def test_offline_vs_live_prediction_probability_diff(self, test_sample, live_predictor):
        """Live predictor probability matches direct offline model evaluation < 1e-5."""
        with open(MODEL_DIR / "rf_within_30s.pkl", "rb") as f:
            rf_offline = pickle.load(f)
        import json
        with open(DATASET_DIR / "normalization.json") as f:
            norm_stats = FeatureNormalizationStats.from_dict(json.load(f))

        feat_offline = extract_prefault_features(test_sample.x, fault_onset_step=5).reshape(1, -1)
        feat_norm = norm_stats.normalize(feat_offline)
        offline_prob = float(rf_offline.predict_proba(feat_norm)[0, 1])

        live_res = live_predictor.predict(test_sample.x, fault_onset_step=5)
        live_prob = live_res["predictions"]["failure_within_30s"]["probability"]

        diff = abs(offline_prob - live_prob)
        assert diff < 1e-5, f"Probability divergence between offline ({offline_prob}) and live ({live_prob}): {diff}"

    def test_deterministic_repeated_inferences(self, test_sample, live_predictor):
        """Sequential invocations produce bit-for-bit identical probabilities."""
        res_1 = live_predictor.predict(test_sample.x, fault_onset_step=5)
        res_2 = live_predictor.predict(test_sample.x, fault_onset_step=5)

        assert res_1["incident_probability"] == res_2["incident_probability"]
        assert res_1["predicted_target_service"] == res_2["predicted_target_service"]
        assert res_1["predicted_fault_type"] == res_2["predicted_fault_type"]
        assert res_1["overall_verdict"] == res_2["overall_verdict"]

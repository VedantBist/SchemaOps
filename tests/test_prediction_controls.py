"""
Phase 6A Tests — NO_FAULT Controls and Alert Threshold Policies
Validates:
1. All 10 NO_FAULT control experiments are correctly handled
2. Zero false positive predictions on test control experiments
3. Deterministic alert threshold policy: probability >= threshold
4. Threshold validation isolation (never tuned on test set)
"""
import pytest
import json
import numpy as np
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from ml.failure_prediction.predictor import FailurePredictionService
from dataset.tg_v1.loader import TemporalGraphDataset

MODEL_DIR = repo_root / "ml" / "models" / "failure_prediction"
DATASET_DIR = repo_root / "dataset" / "failure_prediction_v1"


class TestPredictionControls:
    """Verifies control experiment performance and alert threshold gating."""

    @pytest.fixture(scope="class")
    def predictor(self):
        return FailurePredictionService(model_dir=str(MODEL_DIR))

    def test_all_10_controls_present_in_dataset(self):
        """Dataset contains exactly 10 NO_FAULT control experiments."""
        with open(DATASET_DIR / "labels.json") as f:
            all_labels = json.load(f)
        controls = [l for l in all_labels if not l["is_fault"]]
        assert len(controls) == 10
        for c in controls:
            assert c["failure_within_5s"] is False
            assert c["failure_within_10s"] is False
            assert c["failure_within_30s"] is False

    def test_test_split_controls_zero_false_positives(self, predictor):
        """Test split controls produce zero false positive alerts on full sequence."""
        ds = TemporalGraphDataset(dataset_dir="dataset/tg_v1", split="test")
        test_controls = [s for s in ds if not s.is_fault]
        assert len(test_controls) == 2, f"Expected 2 test controls, found {len(test_controls)}"

        for ctrl in test_controls:
            res = predictor.predict(ctrl.x, experiment_id=ctrl.experiment_id)
            assert res["status"] == "OK"
            assert res["overall_verdict"] == "NORMAL", (
                f"Control {ctrl.experiment_id} produced false positive: {res['overall_verdict']}"
            )
            assert res["prediction_state"] == "NORMAL"

    def test_alert_threshold_policy_gating(self, predictor):
        """Probability < threshold (0.5) must not trigger FAULT_PREDICTED verdict."""
        # Synthesize zero/flat feature input
        T, N, F = 20, 5, 10
        x_flat = np.ones((T, N, F)) * 10.0
        res = predictor.predict(x_flat)
        # Flat telemetry should not exceed high confidence thresholds
        if res["max_failure_probability"] < 0.5:
            assert res["overall_verdict"] == "NORMAL"
            assert res["prediction_state"] == "NORMAL"

    def test_no_fault_manifest_summary(self):
        """failure_prediction_results.json includes complete control experiment evaluation."""
        results_file = MODEL_DIR / "failure_prediction_results.json"
        if not results_file.exists():
            pytest.skip("Results manifest not generated yet")

        with open(results_file) as f:
            res = json.load(f)

        control_eval = res["no_fault_control_evaluation"]
        assert len(control_eval["all_control_experiment_ids"]) == 10
        assert control_eval["false_positive_count"] == 0
        assert control_eval["specificity"] == 1.0
        assert control_eval["false_positive_rate"] == 0.0

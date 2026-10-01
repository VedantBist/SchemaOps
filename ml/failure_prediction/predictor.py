"""
Phase 6A — Live Failure Prediction Service

Provides real-time failure probability inference for Phase 6 orchestration.
Loads trained models from ml/models/failure_prediction/ and produces
multi-horizon predictions from live or experimental telemetry.

Interface:
    predictor = FailurePredictionService()
    result = predictor.predict(telemetry_array)  # [T, N, F] numpy array
    # Returns: {
    #     "failure_within_5s": {"probability": 0.87, "label": "FAULT_PREDICTED", ...},
    #     "failure_within_10s": {...},
    #     "failure_within_30s": {...},
    #     "model_used": "random_forest",
    #     "prediction_timestamp": "2026-09-27T...",
    #     "warnings": [...],
    # }

Integration with Phase 6 orchestration:
    The predictor is called by the incident manager during the
    MONITORING → PREDICTED state transition. It does NOT trigger
    automatic remediation — it provides input to the recommendation engine.

MODEL LOADING:
    The service loads the best-available model for each horizon:
        1. Random Forest (preferred — most reliable on this dataset size)
        2. Logistic Regression (fallback if RF unavailable)
    GRU is not used for live inference in Phase 6A due to its need for
    variable-length sequences at runtime.
"""

from __future__ import annotations
import json
import os
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
import numpy as np

_here = Path(__file__).resolve()
_repo_root = _here.parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from ml.failure_prediction.features import (
    FeatureNormalizationStats, extract_prefault_features
)
from ml.failure_prediction.labels import detect_fault_onset

MODEL_DIR = "ml/models/failure_prediction"
HORIZONS = ["within_5s", "within_10s", "within_30s"]


class FailurePredictionService:
    """
    Live inference service for multi-horizon failure prediction.

    This service is stateless between calls. It loads trained model
    artifacts once on construction and caches them in memory.
    """

    POSITIVE_THRESHOLD = 0.5   # Probability above which label = FAULT_PREDICTED
    HIGH_CONFIDENCE_THRESHOLD = 0.8

    def __init__(self, model_dir: str = MODEL_DIR):
        self.model_dir = Path(model_dir)
        self._norm_stats: Optional[FeatureNormalizationStats] = None
        self._models: Dict[str, Dict[str, Any]] = {}  # horizon → {model_type → model}
        self._target_service_model = None
        self._fault_type_model = None
        self._is_loaded = False
        self._load_warnings: List[str] = []

        try:
            self._load_models()
        except Exception as e:
            self._load_warnings.append(f"Model loading failed: {e}")

    def _load_models(self) -> None:
        """Loads normalization stats and all available trained models."""
        norm_path = self.model_dir / "normalization.json"
        if not norm_path.exists():
            raise FileNotFoundError(
                f"Normalization stats not found at {norm_path}. "
                "Run ml/failure_prediction/trainer.py first."
            )

        with open(norm_path) as f:
            self._norm_stats = FeatureNormalizationStats.from_dict(json.load(f))

        for horizon in HORIZONS:
            self._models[horizon] = {}

            # Try RF first (preferred)
            rf_path = self.model_dir / f"rf_{horizon}.pkl"
            if rf_path.exists():
                with open(rf_path, "rb") as f:
                    self._models[horizon]["random_forest"] = pickle.load(f)

            # Try LR as fallback
            lr_path = self.model_dir / f"lr_{horizon}.pkl"
            if lr_path.exists():
                with open(lr_path, "rb") as f:
                    self._models[horizon]["logistic_regression"] = pickle.load(f)

        target_path = self.model_dir / "rf_target_service.pkl"
        if target_path.exists():
            with open(target_path, "rb") as f:
                self._target_service_model = pickle.load(f)

        fault_path = self.model_dir / "rf_fault_type.pkl"
        if fault_path.exists():
            with open(fault_path, "rb") as f:
                self._fault_type_model = pickle.load(f)

        self._is_loaded = True

    def _select_model(self, horizon: str):
        """Returns the best available model for the given horizon."""
        available = self._models.get(horizon, {})
        return (
            available.get("random_forest")
            or available.get("logistic_regression")
        )

    def _get_model_type(self, horizon: str) -> str:
        available = self._models.get(horizon, {})
        if "random_forest" in available:
            return "random_forest"
        if "logistic_regression" in available:
            return "logistic_regression"
        return "unavailable"

    def predict(
        self,
        x: np.ndarray,
        fault_onset_step: Optional[int] = None,
        experiment_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Produces multi-horizon failure predictions from a telemetry array.

        Args:
            x: Telemetry array of shape [T, N, F] (7+ features per node).
               Must use the canonical tg_v1 feature ordering.
            fault_onset_step: If known, use this as the pre-fault window end.
                              If None, auto-detect using threshold rules.
            experiment_id: Optional experiment ID for logging.

        Returns:
            Prediction result dict with per-horizon probabilities, labels,
            confidence levels, and metadata.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        warnings: List[str] = list(self._load_warnings)

        if not self._is_loaded:
            return {
                "status": "MODEL_UNAVAILABLE",
                "message": "Failure prediction models not loaded. Train models first.",
                "warnings": warnings,
                "timestamp": timestamp,
            }

        # Auto-detect fault onset if not provided
        if fault_onset_step is None:
            fault_onset_step = detect_fault_onset(x)

        # Extract features from pre-fault window
        try:
            features_raw = extract_prefault_features(x, fault_onset_step)
            features_raw = features_raw.reshape(1, -1)  # [1, D]
            features_norm = self._norm_stats.normalize(features_raw)
        except Exception as e:
            return {
                "status": "FEATURE_EXTRACTION_FAILED",
                "message": str(e),
                "warnings": warnings,
                "timestamp": timestamp,
            }

        # Predict for each horizon
        horizon_results = {}
        model_types_used = set()

        for horizon in HORIZONS:
            model = self._select_model(horizon)
            model_type = self._get_model_type(horizon)

            if model is None:
                horizon_results[f"failure_{horizon}"] = {
                    "probability": None,
                    "label": "MODEL_UNAVAILABLE",
                    "confidence": "LOW",
                    "model_type": "unavailable",
                }
                warnings.append(f"No model available for horizon {horizon}")
                continue

            try:
                proba = model.predict_proba(features_norm)  # [1, 2]
                prob_positive = float(proba[0, 1])
                label = "FAULT_PREDICTED" if prob_positive >= self.POSITIVE_THRESHOLD else "NORMAL"
                confidence = (
                    "HIGH" if abs(prob_positive - 0.5) > (self.HIGH_CONFIDENCE_THRESHOLD - 0.5)
                    else "MEDIUM" if abs(prob_positive - 0.5) > 0.2
                    else "LOW"
                )

                horizon_results[f"failure_{horizon}"] = {
                    "probability": round(prob_positive, 6),
                    "label": label,
                    "confidence": confidence,
                    "threshold_used": self.POSITIVE_THRESHOLD,
                    "model_type": model_type,
                }
                model_types_used.add(model_type)

            except Exception as e:
                horizon_results[f"failure_{horizon}"] = {
                    "probability": None,
                    "label": "INFERENCE_ERROR",
                    "confidence": "LOW",
                    "model_type": model_type,
                    "error": str(e),
                }
                warnings.append(f"Inference failed for horizon {horizon}: {e}")

        # Aggregate verdict
        any_fault_predicted = any(
            r.get("label") == "FAULT_PREDICTED"
            for r in horizon_results.values()
        )
        max_prob = max(
            (r.get("probability") or 0.0 for r in horizon_results.values()),
            default=0.0,
        )

        # Determine target service and fault type predictions
        predicted_target_service = None
        target_probability = 0.0
        predicted_fault_type = "NO_FAULT"
        fault_type_probability = round(1.0 - max_prob, 4)

        if self._target_service_model is not None:
            try:
                target_probs = self._target_service_model.predict_proba(features_norm)[0]
                target_classes = self._target_service_model.classes
                best_idx = int(np.argmax(target_probs))
                predicted_target_service = target_classes[best_idx]
                target_probability = round(float(target_probs[best_idx]), 4)
            except Exception as e:
                warnings.append(f"Target service prediction error: {e}")

        if self._fault_type_model is not None:
            try:
                fault_probs = self._fault_type_model.predict_proba(features_norm)[0]
                fault_classes = self._fault_type_model.classes
                best_f_idx = int(np.argmax(fault_probs))
                if any_fault_predicted:
                    predicted_fault_type = fault_classes[best_f_idx]
                    fault_type_probability = round(float(fault_probs[best_f_idx]), 4)
            except Exception as e:
                warnings.append(f"Fault type prediction error: {e}")

        # Determine triggering horizon (lowest horizon that predicted fault or default 10)
        horizon_seconds = 10
        if horizon_results.get("failure_within_5s", {}).get("label") == "FAULT_PREDICTED":
            horizon_seconds = 5
        elif horizon_results.get("failure_within_10s", {}).get("label") == "FAULT_PREDICTED":
            horizon_seconds = 10
        elif horizon_results.get("failure_within_30s", {}).get("label") == "FAULT_PREDICTED":
            horizon_seconds = 30

        return {
            "status": "OK",
            "experiment_id": experiment_id,
            "prediction_timestamp": timestamp,
            "fault_onset_detected_at_step": fault_onset_step,
            "prefault_window_steps": fault_onset_step if fault_onset_step else x.shape[0],
            "max_failure_probability": round(max_prob, 6),
            "overall_verdict": "FAULT_PREDICTED" if any_fault_predicted else "NORMAL",
            "model_used": list(model_types_used)[0] if len(model_types_used) == 1
                          else ", ".join(sorted(model_types_used)),
            # Contract fields required by Phase 6A:
            "incident_probability": round(max_prob, 6),
            "horizon_seconds": horizon_seconds,
            "predicted_target_service": predicted_target_service,
            "target_probability": target_probability,
            "predicted_fault_type": predicted_fault_type,
            "fault_type_probability": fault_type_probability,
            "model_version": "failure_prediction_v1",
            "prediction_state": "PREDICTED" if any_fault_predicted else "NORMAL",
            # Multi-horizon details
            "predictions": horizon_results,
            "warnings": warnings,
            "caveats": [
                "Models trained on 56 experiments — results may not generalize.",
                "Predictions are for informational use only.",
                "Human review required before acting on predictions.",
            ],
        }

    def is_available(self) -> bool:
        """Returns True if at least one model is loaded for at least one horizon."""
        return self._is_loaded and any(
            bool(self._models.get(h)) for h in HORIZONS
        )

    def get_model_registry(self) -> Dict[str, Any]:
        """Returns metadata about loaded models."""
        return {
            "is_loaded": self._is_loaded,
            "model_dir": str(self.model_dir),
            "horizons": HORIZONS,
            "available_models": {
                horizon: list(self._models.get(horizon, {}).keys())
                for horizon in HORIZONS
            },
            "normalization_loaded": self._norm_stats is not None,
            "feature_dim": self._norm_stats.feature_dim if self._norm_stats else None,
            "load_warnings": self._load_warnings,
        }


# Module-level singleton for use by the FastAPI routes
_predictor_instance: Optional[FailurePredictionService] = None


def get_predictor() -> FailurePredictionService:
    """Returns (or initializes) the module-level predictor singleton."""
    global _predictor_instance
    if _predictor_instance is None:
        _predictor_instance = FailurePredictionService()
    return _predictor_instance

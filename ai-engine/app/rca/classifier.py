"""Classical ML Root Cause Classifier for CausalOps Phase 1.

Serves the trained Random Forest model (classical_rca_rf_v1) for live inference.
Reuses ml.feature_extraction.extract_experiment_features to ensure 100% feature
definition and ordering consistency with offline training.
"""

import os
import sys
import json
import logging
import math
from typing import Dict, List, Any, Optional, Tuple
import joblib
import numpy as np

# Ensure causalops repository root is available on sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from ml.feature_extraction import extract_experiment_features
from ml.schema import ROOT_CAUSE_TARGETS, get_feature_definitions

logger = logging.getLogger("causalops.rca.classifier")

# Primary and fallback model artifact search locations
MODEL_PATHS = [
    os.path.join(os.path.dirname(__file__), "..", "models", "classical_rca_rf_v1.joblib"),
    os.path.join(REPO_ROOT, "ai-engine", "app", "models", "classical_rca_rf_v1.joblib"),
    os.path.join(REPO_ROOT, "ml", "models", "classical_rca_rf_v1.joblib")
]
METADATA_PATHS = [
    os.path.join(os.path.dirname(__file__), "..", "models", "metadata.json"),
    os.path.join(REPO_ROOT, "ai-engine", "app", "models", "metadata.json"),
    os.path.join(REPO_ROOT, "ml", "models", "metadata.json")
]

_model = None
_metadata = None


def load_model_and_metadata(force_reload: bool = False) -> Tuple[Any, Dict[str, Any]]:
    """Loads and caches the model artifact and metadata."""
    global _model, _metadata
    if _model is not None and _metadata is not None and not force_reload:
        return _model, _metadata

    model_file = None
    for p in MODEL_PATHS:
        if os.path.exists(p):
            model_file = p
            break

    if not model_file:
        raise FileNotFoundError(
            f"Classical ML RCA model artifact not found. Checked: {MODEL_PATHS}. "
            "Please run 'PYTHONPATH=. python3 ml/train_and_export.py' to generate the artifact."
        )

    metadata_file = None
    for p in METADATA_PATHS:
        if os.path.exists(p):
            metadata_file = p
            break

    if not metadata_file:
        raise FileNotFoundError(
            f"Classical ML RCA metadata not found. Checked: {METADATA_PATHS}."
        )

    logger.info(f"Loading ML RCA model from {model_file}")
    _model = joblib.load(model_file)

    with open(metadata_file, "r") as f:
        _metadata = json.load(f)

    # Validate feature count
    expected_count = _metadata.get("feature_count", 214)
    features_list = _metadata.get("features", [])
    if len(features_list) != expected_count:
        raise ValueError(
            f"Metadata feature list length ({len(features_list)}) does not match feature_count ({expected_count})"
        )

    logger.info(
        f"Loaded {_metadata.get('model_name')} ({_metadata.get('model_version')}) "
        f"with {len(features_list)} features and classes: {_metadata.get('classes')}"
    )

    return _model, _metadata


def predict_root_cause(
    topology: Dict[str, Any],
    telemetry: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Performs real-time root-cause inference using the trained Random Forest model.

    Pipeline:
    1. Extract 214 features using shared extract_experiment_features
    2. Validate feature schema and ordering against metadata
    3. Run model.predict_proba()
    4. Rank candidates by probability
    5. Generate explainability evidence from top feature attributions
    """
    model, metadata = load_model_and_metadata()
    feature_names = metadata["features"]
    feature_schema_version = metadata.get("feature_schema_version", "1.0.0")

    # 1. Feature extraction (leakage-free)
    feat_dict = extract_experiment_features(telemetry, topology)

    # 2. Build feature vector in exact canonical order
    vector: List[float] = []
    for fn in feature_names:
        val = feat_dict.get(fn, 0.0)
        if math.isnan(val) or math.isinf(val):
            val = 0.0
        vector.append(float(val))

    if len(vector) != len(feature_names):
        raise ValueError(
            f"Feature vector length mismatch: expected {len(feature_names)}, got {len(vector)}"
        )

    X = np.array([vector], dtype=np.float64)

    # 3. Model inference
    probs = model.predict_proba(X)[0]
    classes = list(model.classes_)

    # 4. Candidate ranking
    candidate_list = []
    for cls_name, prob in zip(classes, probs):
        candidate_list.append({
            "service": cls_name,
            "probability": float(prob),
            "score": float(prob)
        })

    candidate_list.sort(key=lambda x: -x["probability"])

    for rank_idx, cand in enumerate(candidate_list, start=1):
        p = round(cand["probability"], 4)
        cand["signals"] = {
            "modelProbability": p,
            "rank": rank_idx
        }
        cand["score"] = p
        cand["confidence"] = p

    root_cause = candidate_list[0]["service"]
    confidence = round(float(candidate_list[0]["probability"]), 4)

    # 5. Explainability & Top Feature Attributions
    top_features_meta = metadata.get("top_features", [])
    feat_catalog = get_feature_definitions()

    # Filter features that have non-zero or notable values in this inference
    # and sort by global importance * relevance
    explained_features = []
    for tf in top_features_meta:
        fn = tf["feature"]
        imp = tf["importance"]
        val = feat_dict.get(fn, 0.0)
        svc = fn.split("__")[0]
        desc = feat_catalog[fn].description if fn in feat_catalog else fn
        explained_features.append({
            "feature": fn,
            "service": svc,
            "value": round(float(val), 4),
            "importance": round(float(imp), 4),
            "description": desc
        })

    # Evidence items formatted for both storage and frontend display
    evidence = []
    for idx, ef in enumerate(explained_features[:5], start=1):
        evidence.append({
            "step": idx,
            "claim": f"{ef['service']}: {ef['feature'].split('__')[-1].replace('_', ' ')}",
            "timestamp": telemetry[-1].get("timestamp") if telemetry else None,
            "detail": f"{ef['description']} (observed value: {ef['value']}, feature importance: {ef['importance']:.3f})",
            "state": "OBSERVED" if ef["value"] > 0 else "BASELINE",
            "feature": ef["feature"],
            "value": ef["value"],
            "importance": ef["importance"]
        })

    return {
        "methodology": f"CLASSICAL ML BASELINE: Random Forest ({metadata.get('model_version', 'classical_rca_rf_v1')})",
        "rca_method": "classical_ml",
        "model": metadata.get("model_version", "classical_rca_rf_v1"),
        "root_cause": root_cause,
        "confidence": confidence,
        "candidates": candidate_list,
        "feature_schema_version": feature_schema_version,
        "explanation": {
            "top_features": explained_features
        },
        "evidence": evidence
    }

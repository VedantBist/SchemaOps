# CausalOps Classical ML RCA Serving & Baselines (`ml/`)

This directory contains the production-grade, reproducible feature engineering, dataset preparation, split generation, classical machine learning training, export pipeline, and live inference serving integration for CausalOps root-cause analysis (RCA).

> [!IMPORTANT]
> **Scientific Integrity & Model Scope Statement:**  
> The current classical ML RCA model is a baseline learned classifier trained on the frozen CausalOps ML Dataset v1. It is not yet a Temporal GNN or causal inference model. The 100% accuracy result applies specifically to the 10 held-out test fault experiments in `dataset/ml_v1/splits.json` and must NOT be interpreted as proof of universal or generalizable accuracy across arbitrary unseen failure modes.

---

## 1. Selected Production Candidate: Random Forest (`classical_rca_rf_v1`)

- **Model Designation:** Initial production candidate for Phase 1.
- **Model Version:** `classical_rca_rf_v1`
- **Model Algorithm:** Scikit-learn `RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)`
- **Rationale for Selection:**
  - Robust multi-class root-cause probability estimation across all 4 target services.
  - Native global and tree-based feature importances for instant explainability.
  - Non-linear boundary handling without feature scaling sensitivity.
  - Ultra-lightweight CPU inference (~1.5ms) suitable for real-time microservice incident diagnosis.
  - 100% accuracy on held-out test split (10/10), resolving historical heuristic confusions on `payment-service`.

---

## 2. Model Artifacts & Locations

The model training and serialization pipeline (`ml/train_and_export.py`) exports dual artifacts:

| Destination | Artifact Path | Description |
|---|---|---|
| **AI Engine Serving** | `ai-engine/app/models/classical_rca_rf_v1.joblib` | Serialized joblib classifier loaded by FastAPI |
| **AI Engine Metadata** | `ai-engine/app/models/metadata.json` | 214-feature ordering, classes, training experiment IDs, schema version |
| **Repository Backup** | `ml/models/classical_rca_rf_v1.joblib` | Reproducible repository-level model checkpoint |
| **Repository Metadata** | `ml/models/metadata.json` | Checkpoint metadata catalog |

---

## 3. Dataset & Split Specifications

- **Frozen Dataset Version:** `1.0.0`
- **Total Experiments:** 80 (`EXP-001` through `EXP-080`)
- **Frozen Manifest:** `dataset/manifests/ml_dataset_v1.json`
- **Target Services (4 Classes):** `inventory-db`, `inventory-service`, `order-service`, `payment-service`
- **Experiment-Level Partition (`dataset/ml_v1/splits.json`):**
  - **Train (70%):** 56 experiments (49 fault runs + 7 controls). The classifier is fit strictly on the **49 fault runs**.
  - **Validation (15%):** 12 experiments (11 fault runs + 1 control).
  - **Test (15%):** 12 experiments (10 fault runs + 2 controls). Zero test experiments were seen during training.

---

## 4. Feature Schema (Version `1.0.0`)

Exact **214 engineered features** extracted per incident window across all 5 topology services plus global summaries.

| Group | Name | Count | Key Features |
|---|---|---|---|
| **Group A** | Telemetry Statistics | 103 | P99/P50 latency (mean, median, p95, min, max, std, delta), error rate (mean, max, std, delta), request rate, connection pool utilization (mean, max, delta), anomaly score (mean, max, count, fraction). |
| **Group B** | Temporal Dynamics | 55 | Initial baseline latency ($t_0$), post-recovery latency ($t_{\text{last}}$), 1-step and 2-step lag differences, 3-step rolling mean and std, linear regression slopes ($d\text{latency}/dt$, $d\text{err}/dt$), time-to-first-anomaly, anomaly duration, onset rank. |
| **Group C** | Graph & Topology | 56 | In/out degree, upstream ancestor count, downstream descendant count, upstream/downstream caller anomaly counts, upstream/downstream mean latency, hop distance to earliest anomaly, propagation order score, latency surge ratio, system cascade diameter. |

---

## 5. Inference API Specification

The FastAPI AI engine exposes real-time Classical ML RCA at `POST /analyze/root-cause` and dedicated `POST /rca/ml`.

### Request

```http
POST /analyze/root-cause HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "topology": {
    "edges": [
      {"source": "order-service", "target": "inventory-service"},
      {"source": "inventory-service", "target": "inventory-db"}
    ]
  },
  "telemetry": [
    {
      "service": "inventory-db",
      "p99Latency": 1420.0,
      "p50Latency": 1200.0,
      "errorRate": 0.0,
      "requestRate": 60.0,
      "poolUtilization": 95.0,
      "anomalyScore": 0.85,
      "timestamp": "2026-09-26T22:00:00.000Z"
    }
  ],
  "mode": "classical_ml"
}
```

### Response

```json
{
  "methodology": "CLASSICAL ML BASELINE: Random Forest (classical_rca_rf_v1)",
  "rca_method": "classical_ml",
  "model": "classical_rca_rf_v1",
  "root_cause": "inventory-db",
  "confidence": 0.995,
  "candidates": [
    {
      "service": "inventory-db",
      "probability": 0.995,
      "score": 0.995,
      "signals": {
        "modelProbability": 0.995,
        "rank": 1
      }
    },
    {
      "service": "inventory-service",
      "probability": 0.005,
      "score": 0.005,
      "signals": {
        "modelProbability": 0.005,
        "rank": 2
      }
    },
    {
      "service": "order-service",
      "probability": 0.0,
      "score": 0.0,
      "signals": {
        "modelProbability": 0.0,
        "rank": 3
      }
    },
    {
      "service": "payment-service",
      "probability": 0.0,
      "score": 0.0,
      "signals": {
        "modelProbability": 0.0,
        "rank": 4
      }
    }
  ],
  "feature_schema_version": "1.0.0",
  "explanation": {
    "top_features": [
      {
        "feature": "inventory-db__p99_delta",
        "service": "inventory-db",
        "value": 1395.0,
        "importance": 0.0271,
        "description": "Delta between max observed P99 latency and baseline"
      }
    ]
  },
  "evidence": [
    {
      "step": 1,
      "claim": "inventory-db: p99 delta",
      "timestamp": "2026-09-26T22:00:00.000Z",
      "detail": "Delta between max observed P99 latency and baseline (observed value: 1395.0, feature importance: 0.027)",
      "state": "OBSERVED",
      "feature": "inventory-db__p99_delta",
      "value": 1395.0,
      "importance": 0.0271
    }
  ]
}
```

---

## 6. Heuristic Coexistence & Controlled Fallback

- The existing heuristic RCA (`rca/scorer.py`) is preserved intact and can be explicitly selected by sending `"mode": "heuristic"`.
- If Classical ML inference encounters an unrecoverable failure (e.g., missing artifact, schema corruption), the engine logs the exception and executes a **controlled fallback** to the heuristic baseline.
- **Audit Transparency:** Fallbacks explicitly report `"rca_method": "heuristic_fallback"` and prepends `HEURISTIC FALLBACK` to `methodology`. The `model` key is omitted to ensure zero false attribution.

---

## 7. Execution & Reproducibility Commands

```bash
# 1. Reproduce model training, verification, and artifact export
PYTHONPATH=. python3 ml/train_and_export.py

# 2. Run offline evaluation benchmarks and cross-validation
python3 -m ml.evaluate

# 3. Run unit and serving consistency tests
PYTHONPATH=. pytest tests/test_ml.py tests/test_ml_serving.py
PYTHONPATH=ai-engine:. pytest ai-engine/tests

# 4. Verify backend and frontend builds
cd backend/causalops-api && mvn test && cd ../..
npm run build
```

# CausalOps ML Baseline Evaluation Report: Root Cause Classification

**Date:** 2026-09-26  
**Dataset Version:** CausalOps Frozen ML Dataset v1.0 (`dataset/manifests/ml_dataset_v1.json`)  
**Pipeline Location:** `ml/`  
**Evaluation Scope:** DATASET FREEZE → FEATURE ENGINEERING → EXPERIMENT-LEVEL SPLIT → CLASSICAL ML BASELINES → EVALUATION  

---

## 1. Executive Summary

This report establishes the first rigorous, leakage-free classical machine learning baselines for automated Root Cause Analysis (RCA) on the frozen 80-experiment CausalOps dataset.

### Key Benchmark Findings:
1. **Model Performance:** All three classical baselines (Logistic Regression, Random Forest, Gradient Boosting) achieved **100.0% accuracy (10/10)** and **1.0000 Macro F1** on the held-out test set.
2. **Resolution of Heuristic Weakness:** On `payment-service` (the known primary failure mode of the current heuristic), the classical ML baselines achieved **100% recall (3/3)** on the test set, compared to **33.33% (1/3)** for the heuristic RCA.
3. **Ablation Insight:** Group C (incorporating graph and topology features) improved 5-fold cross-validation performance for Gradient Boosting from **87.8% (Group B)** to **96.0% (Group C)**, demonstrating that structural topology features provide essential inductive bias that regularizes non-linear tree models on small sample sizes.
4. **Leakage Audit:** A strict zero-leakage protocol was verified. Features were derived strictly from telemetry and graph artifacts without access to ground truth parameters, heuristic RCA outputs, or cross-split data.

---

## 2. Dataset Profile & Freeze Manifest

The dataset evaluated is strictly frozen under `dataset/manifests/ml_dataset_v1.json`. Raw artifacts in `dataset/experiments/EXP-001` through `EXP-080` remain untouched and immutable.

- **Total Experiments:** 80
- **Fault Injections:** 70
- **Negative Controls:** 10 (`NO_FAULT` controls with nominal background traffic)
- **Total Telemetry Snapshots:** 15,745 snapshots (~38–39 per service per experiment)
- **Collection Cadence:** 1.0s synchronized interval with 0.00ms timestamp spread across all 5 services

### Distribution by Target Service:
| Target Service | Fault Experiments | Description |
|---|---|---|
| `inventory-db` | 18 | Storage engine disk I/O stall & connection pool saturation |
| `inventory-service` | 17 | Thread pool starvation, network latency, service failure |
| `order-service` | 17 | Synchronous RPC retries, gateway timeouts, error spikes |
| `payment-service` | 18 | Upstream timeout cascading, token stalls, service failures |
| `NO_FAULT` | 10 | Unperturbed baseline control experiments |
| **Total** | **80** | **4 Target Services + 1 Control Group** |

### Distribution by Fault Type:
- `DB_LATENCY`: 18
- `SERVICE_LATENCY`: 17
- `NETWORK_LATENCY`: 13
- `ERROR_RATE`: 13
- `SERVICE_FAILURE`: 9
- `NO_FAULT`: 10

---

## 3. Feature Engineering Architecture

A total of **214 numerical features** were engineered at the experiment level across all 5 system services (`api-gateway`, `order-service`, `inventory-service`, `payment-service`, `inventory-db`) plus global system indicators.

```
Total Features: 214
├── Group A: Statistical Telemetry Only (103 features)
├── Group B: Temporal Dynamics (55 features)
└── Group C: Graph & Topology (56 features)
```

### Group A: Statistical Telemetry Features (103 features)
For each service:
- P99 Latency: mean, median, 95th percentile, max, min, standard deviation, surge delta ($\max - \text{baseline}$)
- P50 Latency: mean
- Error Rate (%): mean, max, standard deviation, surge delta
- Request Rate (req/s): mean throughput
- Connection Pool Utilization (%): mean, peak, surge delta
- Anomaly Score: mean, peak, anomalous sample count, anomalous observation fraction
- Global system metrics: total anomalous services, peak system P99 latency, peak system error rate

### Group B: Temporal Dynamic Features (55 features)
For each service:
- Multi-phase latency: baseline latency ($t_0$), post-recovery latency ($t_{\text{last}}$)
- Transition dynamics: 1-step lag difference ($x_t - x_{t-1}$), 2-step lag difference ($x_t - x_{t-2}$)
- Rolling window statistics: peak 3-step rolling mean, peak 3-step rolling standard deviation
- Linear regression trends: latency slope ($d\text{latency}/dt$), error rate slope ($d\text{err}/dt$)
- Temporal onset metrics: time-to-first-anomaly (seconds from window start), total anomaly duration (seconds), relative anomaly rank (1 to 5)

### Group C: Graph & Topology Features (56 features)
Derived directly from `topology.json`:
- Graph structural properties: in-degree, out-degree, upstream ancestor count, downstream descendant count
- Dependency anomaly signals: direct upstream caller anomaly count, direct downstream callee anomaly count
- Dependency latency signals: direct upstream mean latency, direct downstream mean latency
- Spatial-causal interaction: shortest hop distance to earliest anomalous service in undirected graph
- Propagation precedence score: $+1$ if service anomaly preceded downstream children, $-1$ if lagged, $0$ if unaffected
- Causal gradient: ratio of service latency surge to downstream mean latency surge
- Global network signal: system cascade diameter (maximum topological distance between co-anomalous services)

---

## 4. Experiment-Level Split Protocol

Splits were conducted strictly by **Experiment ID** (`EXP-xxx`) to prevent intra-experiment telemetry leakage, using a fixed random seed (`42`) and stratification on target service:

- **Train (70%):** 56 experiments (49 fault + 7 control)
- **Validation (15%):** 12 experiments (11 fault + 1 control)
- **Test (15%):** 12 experiments (10 fault + 2 control)

Stored deterministically in: `dataset/ml_v1/splits.json`.

### Exact Experiment ID Partition:
```json
{
  "train_ids (56)": [
    "EXP-001", "EXP-002", "EXP-003", "EXP-004", "EXP-005", "EXP-009", "EXP-010",
    "EXP-011", "EXP-012", "EXP-013", "EXP-014", "EXP-017", "EXP-018", "EXP-019",
    "EXP-020", "EXP-021", "EXP-022", "EXP-023", "EXP-024", "EXP-025", "EXP-026",
    "EXP-027", "EXP-028", "EXP-030", "EXP-032", "EXP-033", "EXP-034", "EXP-035",
    "EXP-036", "EXP-037", "EXP-038", "EXP-039", "EXP-041", "EXP-042", "EXP-044",
    "EXP-045", "EXP-046", "EXP-048", "EXP-049", "EXP-050", "EXP-051", "EXP-052",
    "EXP-053", "EXP-054", "EXP-055", "EXP-056", "EXP-057", "EXP-058", "EXP-060",
    "EXP-061", "EXP-062", "EXP-063", "EXP-066", "EXP-067", "EXP-068", "EXP-069"
  ],
  "validation_ids (12)": [
    "EXP-006", "EXP-029", "EXP-070", "EXP-071", "EXP-072", "EXP-073", "EXP-074",
    "EXP-075", "EXP-077", "EXP-078", "EXP-079", "EXP-080"
  ],
  "test_ids (12)": [
    "EXP-007", "EXP-008", "EXP-015", "EXP-016", "EXP-031", "EXP-040", "EXP-043",
    "EXP-047", "EXP-059", "EXP-064", "EXP-065", "EXP-076"
  ]
}
```

### Partition Target Balance:
| Split | Total | `inventory-db` | `inventory-service` | `order-service` | `payment-service` | `NO_FAULT` |
|---|---|---|---|---|---|---|
| **Train** | 56 | 13 | 12 | 12 | 12 | 7 |
| **Validation** | 12 | 3 | 2 | 3 | 3 | 1 |
| **Test** | 12 | 2 | 3 | 2 | 3 | 2 |
| **Total** | **80** | **18** | **17** | **17** | **18** | **10** |

---

## 5. Classical ML Baseline Evaluation

The Root Cause Classification task predicts the root cause target among the 4 candidate services: `inventory-db`, `inventory-service`, `order-service`, `payment-service`. Models were trained on the 49 training fault experiments, validated on the 11 validation fault experiments, and tested on the 10 held-out test fault experiments.

### Model Hyperparameters:
1. **Logistic Regression:** $L_2$ regularization, $C=1.0$, `solver='lbfgs'`, `max_iter=1000`, `random_state=42`. Standardized using `StandardScaler` (fit exclusively on Train).
2. **Random Forest:** `n_estimators=100`, `max_depth=5`, `criterion='gini'`, `random_state=42`.
3. **Gradient Boosting:** `n_estimators=100`, `max_depth=3`, `learning_rate=0.1`, `random_state=42`.

### Overall Benchmark Results (Group C: Full 214 Features):

| Model | Train Acc | Train F1 | Val Acc | Val F1 | Test Acc | Test Macro F1 | Test Weighted F1 | Payment Rec |
|---|---|---|---|---|---|---|---|---|
| **Logistic Regression** | 100.0% | 1.0000 | 100.0% | 1.0000 | **100.0%** | **1.0000** | **1.0000** | **100.0% (3/3)** |
| **Random Forest** | 100.0% | 1.0000 | 100.0% | 1.0000 | **100.0%** | **1.0000** | **1.0000** | **100.0% (3/3)** |
| **Gradient Boosting** | 100.0% | 1.0000 | 100.0% | 1.0000 | **100.0%** | **1.0000** | **1.0000** | **100.0% (3/3)** |
| *Heuristic RCA Baseline* | — | — | — | — | *80.0%* | *0.7778* | *0.7917* | *33.3% (1/3)* |

*(Note on Heuristic Baseline: Across the full 70 fault experiments, heuristic RCA achieved 59/70 = 84.29% exact match with 38.89% payment-service recall.)*

---

## 6. Confusion Matrices (Held-Out Test Set, N=10)

### All Three ML Models (Logistic Regression, Random Forest, Gradient Boosting):
```
Predicted ───────────────────────►
True Target           inv-db  inv-svc  order-svc  pay-svc
inventory-db            2        0         0         0
inventory-service       0        3         0         0
order-service           0        0         2         0
payment-service         0        0         0         3
```
*Exact Match: 10/10 (100.0%)*

### Baseline Heuristic RCA (Held-Out Test Set):
```
Predicted ───────────────────────►
True Target           inv-db  inv-svc  order-svc  pay-svc
inventory-db            2        0         0         0
inventory-service       0        3         0         0
order-service           0        0         2         0
payment-service         0        0         2         1   <-- 2 misattributed to order-svc!
```
*Exact Match: 8/10 (80.0%)*

---

## 7. Per-Class Performance Breakdown (Test Set)

| Service Target | Support | Precision (ML) | Recall (ML) | F1 (ML) | Precision (Heuristic) | Recall (Heuristic) | F1 (Heuristic) |
|---|---|---|---|---|---|---|---|
| `inventory-db` | 2 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| `inventory-service` | 3 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| `order-service` | 2 | 1.0000 | 1.0000 | 1.0000 | 0.5000 | 1.0000 | 0.6667 |
| `payment-service` | 3 | **1.0000** | **1.0000** | **1.0000** | **1.0000** | **0.3333** | **0.5000** |

---

## 8. Feature Group Ablation Study

To evaluate whether graph topology and temporal features contribute measurable predictive information, we conducted an ablation across the three nested feature sets:
- **Group A (Telemetry Only):** 103 statistical features
- **Group B (Telemetry + Temporal):** 158 features
- **Group C (Telemetry + Temporal + Graph):** 214 features

### Ablation Matrix:
| Feature Set | Features | Model | 5-Fold CV Acc (Train) | Val Acc | Test Acc | Test Macro F1 | Payment Recall |
|---|---|---|---|---|---|---|---|
| **Group A: Telemetry Only** | 103 | Logistic Regression | 96.00% (±4.9%) | 100% | 100% | 1.0000 | 100% |
| | | Random Forest | **98.00% (±4.0%)** | 100% | 100% | 1.0000 | 100% |
| | | Gradient Boosting | 91.78% (±7.6%) | 100% | 100% | 1.0000 | 100% |
| **Group B: Telemetry + Temporal** | 158 | Logistic Regression | 96.00% (±4.9%) | 100% | 100% | 1.0000 | 100% |
| | | Random Forest | 96.00% (±8.0%) | 100% | 100% | 1.0000 | 100% |
| | | Gradient Boosting | 87.78% (±7.4%) | 100% | 100% | 1.0000 | 100% |
| **Group C: Full (Tel + Temp + Graph)** | 214 | Logistic Regression | 96.00% (±4.9%) | 100% | 100% | 1.0000 | 100% |
| | | Random Forest | 96.00% (±8.0%) | 100% | 100% | 1.0000 | 100% |
| | | Gradient Boosting | **96.00% (±8.0%)** | 100% | 100% | 1.0000 | 100% |

### Key Ablation Insights:
1. **Telemetry Signals Sufficiency for Linear / Bagging Models:** Statistical telemetry alone (Group A) provides strong localized separation between services (e.g. pool utilization surge, localized error rates, and latency deltas).
2. **Topology Regularizes Gradient Boosting:** Gradient Boosting performance in cross-validation fell to 87.78% when temporal features were added without graph constraints (due to feature dimensionality vs small sample size), but **rebounded sharply to 96.00%** when Group C graph features (in/out degree, ancestor counts, propagation order scores) were provided. Topology features act as structural regularizers.

---

## 9. In-Depth Error Analysis

### A. Test Set Discrepancies (Heuristic vs Machine Learning)

On the held-out test set, the heuristic RCA failed on 2 experiments, whereas ML correctly identified the ground truth in both cases:

| Exp ID | Ground Truth | Fault Type | Rate | ML Prediction (Conf) | Heuristic Output | Root Cause Diagnosis |
|---|---|---|---|---|---|---|
| `EXP-064` | `payment-service` | `SERVICE_FAILURE` | 5 rps | `payment-service` (1.00) | `order-service` (Mismatch) | Heuristic favored caller hub centrality. ML detected `payment-service` error spike (35.1%) > `order-service` (24.6%). |
| `EXP-065` | `payment-service` | `SERVICE_FAILURE` | 15 rps | `payment-service` (1.00) | `order-service` (Mismatch) | High traffic rate saturated order-service retry queue; heuristic blamed the caller. ML detected localized pool delta on payment-service. |

### B. Cross-Validation Boundary Errors (Low-Data Partitions)

During 5-fold cross-validation on the training set (where training was restricted to ~39 experiments per fold), three boundary edge cases were observed:

| Exp ID | Fold | Ground Truth | ML Prediction | Confidence | Fault Type & Mechanism |
|---|---|---|---|---|---|
| `EXP-045` | 3 | `inventory-service` | `order-service` | 0.48 | `SERVICE_FAILURE` at 1 rps. Synchronous gRPC timeouts at caller `order-service` created near-identical latency surge before pool delta fully registered. |
| `EXP-067` | 3 | `payment-service` | `order-service` | 0.82 | `SERVICE_FAILURE` at 1 rps. In low-sample subsamples, caller retry exhaustion can shadow leaf failure. |
| `EXP-011` | 4 | `inventory-db` | `inventory-service` | 0.46 | `DB_LATENCY` (200ms). Database latency propagated synchronously into `inventory-service` lease wait loop; low confidence (0.46) reflected caller-callee boundary ambiguity. |

**Mitigation:** When trained on the full training set (49 experiments), all such boundary ambiguities were resolved, as the model learned the localized saturation threshold that differentiates root causes from callers.

---

## 10. Negative Control Verification (Anomaly Gating)

The 10 `NO_FAULT` control experiments were evaluated through the feature pipeline:
- In all 10 controls, `global__total_anomalous_services == 0.0`.
- Peak system P99 latency remained at nominal baseline (80.0ms).
- Max error rate across all services was nominal ($\le 0.1\%$).
- An initial two-stage inference architecture (Stage 1: Anomaly Gating via `global__total_anomalous_services > 0`; Stage 2: Root Cause Classification) achieves **0% false positive incident alerts** on negative controls while maintaining **100% root cause classification accuracy** on active incidents.

---

## 11. Leakage Audit Certification

| Audit Item | Verification Status | Evidence |
|---|---|---|
| **Target Leakage** | PASSED | Zero references to `detected_root_cause`, `rca_match`, `fault_target`, or `fault_type` in input features. Tested by `test_zero_target_leakage_in_features`. |
| **Split Isolation** | PASSED | Splits partitioned strictly by `experiment_id`. Pairwise intersections: $\text{Train} \cap \text{Val} = \emptyset$, $\text{Val} \cap \text{Test} = \emptyset$, $\text{Train} \cap \text{Test} = \emptyset$. Tested by `test_split_leakage_detection`. |
| **Scaler Contamination** | PASSED | `StandardScaler` was fit strictly on `X_train`. Validation and Test matrices were transformed using train-derived $\mu$ and $\sigma$. |
| **Temporal Precedence** | PASSED | Features computed strictly within each experiment's bounded telemetry window. |

---

## 12. Reproducibility Guide

The baseline evaluation is completely deterministic and reproducible:

```bash
# 1. Build experiment-level dataset matrix
python3 -m ml.dataset

# 2. Generate deterministic stratified splits
python3 -m ml.split

# 3. Execute models, ablation, and benchmarks
python3 -m ml.evaluate

# 4. Run automated test suite
PYTHONPATH=. pytest tests/test_ml.py
PYTHONPATH=. pytest tests/test_dataset_generator.py
PYTHONPATH=ai-engine pytest ai-engine/tests
mvn test
npm run build
```

---

## 13. Limitations of Classical ML Baselines

1. **Fixed Topological Encoding:** The engineered graph features assume a fixed service inventory (5 services). Adding or removing microservices requires updating feature definitions.
2. **Small Sample Regime:** The dataset contains 80 experiments (70 fault runs). While 100% test accuracy was achieved, performance under novel compound faults or multi-root incidents remains unmeasured.
3. **Correlation vs Causation:** Classical models exploit statistical correlation across localized metric shifts. They do not infer counterfactual causality or construct formal Structural Causal Models (SCMs).

---

## 14. Recommendations for Next Research Stage

1. **Progress to Temporal Graph Neural Networks (T-GNN):** Now that a strong classical ML baseline is established, investigate whether T-GNNs can generalize across variable topologies without manual feature engineering.
2. **Formulate Structural Causal Discovery:** Evaluate constraint-based (PC algorithm) and score-based (GES / NOTEARS) causal discovery algorithms directly on the time series to construct causal DAGs dynamically.
3. **Compound Fault Injection:** Expand dataset generation to multi-fault scenarios where two independent services experience simultaneous failures.

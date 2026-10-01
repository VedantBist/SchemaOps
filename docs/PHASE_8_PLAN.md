# CausalOps Phase 8: End-to-End Scientific Validation, Benchmarking & Final Evidence Plan

## A. End-to-End Validation Objective

The objective of Phase 8 is to provide rigorous, reproducible, scientific evidence that the complete CausalOps end-to-end operational pipeline functions seamlessly as an integrated system on actual telemetry from cloud microservices:

$$\text{Telemetry} \longrightarrow \text{Incident Gate} \longrightarrow \text{Failure Prediction} \longrightarrow \text{RCA} \longrightarrow \text{Topology SCM} \longrightarrow \text{Counterfactual} \longrightarrow \text{Recommendation} \longrightarrow \text{Approval} \longrightarrow \text{Execution} \longrightarrow \text{Verification} \longrightarrow \text{Resolution}$$

This phase does **not** introduce new models, alter mathematical equations, retrain classifiers, weaken safety policies, or modify frozen baseline datasets. Instead, it systematically benchmarks each subsystem against held-out ground truth, measures execution latencies, quantifies prediction lead times, verifies fail-safe operational behavior, and packages reproducible evidence for thesis and publication defense.

---

## B. Frozen Artifacts & Immutable Baselines

The following artifacts were established, validated, and frozen in Phases 1 through 7 and must remain strictly unmodified:

1. **Benchmark Datasets**:
   - `dataset/tg_v1/`: 80 multi-service temporal graph experiments (56 train, 12 val, 12 test; 70 fault injections, 10 NO_FAULT controls).
   - `dataset/ml_v1/`: Pre-computed tabular feature matrices and canonical experiment splits.
   - `dataset/failure_prediction_v1/`: Time-series failure prediction feature tables across 5s, 10s, and 30s horizons.

2. **Model Checkpoints**:
   - `ml/models/classical_rca_rf_v1.joblib`: Phase 2 Random Forest root cause classifier.
   - `ml/models/causal_scm/`: Phase 3B topology-constrained lagged Structural Causal Model ($P=5$, $\alpha=1.0$, 48 stable edges).
   - `ml/models/failure_prediction/`: Phase 6A multi-horizon Random Forest and Logistic Regression models.
   - `ml/models/temporal_gnn/`: Spatio-temporal graph neural network checkpoints.

3. **Safety & Orchestration Logic**:
   - `ml/execution/`: Phase 5 closed-loop remediation executor, 15 safety policy rules, typed allowlists, rollback engine.
   - `ml/orchestration/`: Phase 6 multi-incident state machine, deduplication, conflict resolver, deterministic scheduler.

Integrity is verified against the SHA-256 signatures in `ml/failure_prediction/audit/checksums.json`.

---

## C. Evaluation Experiments & Representative Scenarios

The validation suite executes real data through real code across 10 canonical scenarios:

| # | Scenario ID | Experiment / Scenario | Description & Physical Ground Truth |
|---|---|---|---|
| 1 | `SCEN-01` | `EXP-001` / `EXP-002` | **NO_FAULT Healthy Control**: Validates zero false alarms; incident gate remains closed; no remediation triggered. |
| 2 | `SCEN-02` | `EXP-015` | **Database Latency Injection**: Downstream database bottleneck on `inventory-db` propagating upstream to `order-service` and `api-gateway`. |
| 3 | `SCEN-03` | `EXP-020` | **Inventory Service Fault**: High CPU / execution delay localized on `inventory-service`. |
| 4 | `SCEN-04` | `EXP-014` | **Order Service Fault**: Internal processing delay / thread contention on `order-service`. |
| 5 | `SCEN-05` | `EXP-011` | **Payment Service Fault**: Downstream payment processing delay isolating payment branch. |
| 6 | `SCEN-06` | `EXP-034` / `EXP-037` | **Network Degradation / Packet Loss**: Inter-service communication latency degradation. |
| 7 | `SCEN-07` | `EXP-022` | **Service Crash / Pod Failure**: Hard service disruption triggering cascade failure. |
| 8 | `SCEN-08` | `EXP-029` | **Error Rate Surge**: High HTTP 5xx rate propagating across API gateway. |
| 9 | `SCEN-09` | `EXP-047` | **Nonlinear Queueing / Thread Starvation**: Extreme queue saturation boundary condition validating documented SCM linear approximation limits. |
| 10 | `SCEN-10` | Multi-Incident Cascade | **Simultaneous & Correlated Incidents**: Parallel incidents exercising correlation, conflict detection, lock management, and prioritized scheduling. |

---

## D. Evaluated Metrics & Evaluation Criteria

### 1. Failure Prediction Engine
- **Classification Performance**: ROC-AUC, PR-AUC, Macro F1, Precision, Recall, Specificity, False Positive Rate (FPR) on NO_FAULT controls.
- **Probabilistic Calibration**: Brier Score Loss, Expected Calibration Error (ECE).
- **Temporal Warning Lead Time**: Mean, Median, Min, Max lead time ($t_{\text{onset}} - t_{\text{alert}}$), and percentage of faults predicted prior to physical degradation.

### 2. Root Cause Analysis (RCA)
- **Attribution Accuracy**: Top-1 and Top-2 accuracy across heuristic, classical RF, temporal GRU, and spatio-temporal GNN models.
- **Service Confusion**: Full confusion matrix analyzing localization accuracy across `inventory-db`, `inventory-service`, `order-service`, and `payment-service`.

### 3. Structural Causal Model & Counterfactual Simulation
- **Ablation & Calibration**: Mean Absolute Error (MAE), Root Mean Square Error (RMSE), Bias, Relative Error, Pearson $r$, and sign agreement between counterfactual rollout and empirical observations.
- **Axiomatic Consistency**: Identity under zero intervention, pre-intervention invariance ($t < t_{\text{intervention}}$), monotonic effect scaling with intervention magnitude, orthogonal branch isolation, and physical boundary enforcement.

### 4. Closed-Loop Remediation & Policy Safety
- **Policy Enforcement**: 15/15 rules verified (allowlisting, authorization tokens, TTL expiration, rollback capability, blast radius gating, budget enforcement).
- **Execution & Rollback**: Success rate, idempotency under repeat executions, verification latency, and state restoration on failed verification.

### 5. Multi-Incident Orchestration
- **Deduplication & Correlation**: Duplicate incident suppression, correlation of cascade events, lock serialization on shared dependencies, and deterministic scheduling.

### 6. Production Hardening & Operational Resilience
- **Platform Resilience**: Recovery under AI engine failure, database disconnection, stale telemetry, unauthorized token submission, and container restarts.
- **API Performance**: Latency percentiles ($p_{50}, p_{95}, p_{99}$), CPU and memory consumption under local Docker workloads.

---

## E. Acceptance Criteria

Phase 8 will be accepted as `COMPLETE` if and only if all following criteria are satisfied:

1. **Frozen Artifact Integrity**: 100% of immutable datasets, model weights, and causal matrices match recorded baseline signatures.
2. **Zero Mock Data in E2E Pipeline**: Every pipeline step from telemetry ingestion to rollback verification runs against real backend code and datasets.
3. **Control Safety**: NO_FAULT control experiments produce zero false alerts, zero incident tickets, and zero remediation actions.
4. **Transparent Limitation Disclosure**: The documented limitations (approximately 4–5 seconds realized lead time; 30% pre-onset target accuracy; 40% pre-onset fault-type accuracy; linear queueing approximations) are prominently preserved in all reports and artifacts.
5. **Deterministic Multi-Incident Scheduling**: Orchestration scenarios execute with 100% deterministic state transitions and zero deadlock.
6. **Full Regression Suite Green**:
   - `pytest tests/`: $\ge 349/349$ passed.
   - `PYTHONPATH=ai-engine pytest ai-engine/tests/`: $\ge 34/34$ passed.
   - `npm run test:frontend`: $22/22$ passed.
   - `npm run build`: Clean build with zero TypeScript/Vite compilation errors.
   - `phase7_smoke_test.py`: 31/31 passed.
   - `phase7_failure_tests.py`: 8/8 passed.
7. **Traceability**: All numbers in summary tables trace directly to raw JSON/CSV data files in `artifacts/phase8/`.

---

## F. Reproducibility Procedure

The validation framework is fully automated via executable scripts:

```bash
# 1. Verify frozen artifact signatures and environment
python3 scripts/phase8_reproduce.py --step verify

# 2. Run end-to-end validation pipeline across all 10 canonical scenarios
python3 scripts/phase8_e2e_validation.py

# 3. Execute quantitative benchmarks (Prediction, RCA, SCM, Counterfactual, Remediation, Orchestration)
python3 scripts/phase8_reproduce.py --step benchmarks

# 4. Execute performance and local load tests
python3 scripts/phase8_load_test.py --concurrency 10,50,100

# 5. Execute full regression suite and generate final manifest
python3 scripts/phase8_reproduce.py --step all
```

---

## G. Expected Outputs & Generated Evidence

```
artifacts/phase8/
├── manifest.json                  # Environment, checksums, git hash, test summaries
├── e2e_results.json               # Full lifecycle records for all 10 canonical scenarios
├── failure_prediction_benchmark.json # AUC, F1, lead time, breakdown by traffic/fault
├── rca_benchmark.json             # Cross-model accuracy, precision, recall
├── rca_confusion_matrix.csv       # Multi-class confusion matrix across 4 microservices
├── causal_validation_results.json # SCM mathematical validation & ablation metrics
├── counterfactual_results.json    # Pearl 3-step rollout trajectories & validation
├── remediation_benchmark.json     # Policy checks, safety gates, execution records
├── orchestration_results.json     # Multi-incident deduplication & scheduling logs
└── performance_results.json       # Latency percentiles, CPU, memory, throughput

docs/
├── PHASE_8_PLAN.md                # This master scientific plan
├── PHASE_8_RESULTS.md             # Consolidated data tables with artifact citations
├── PHASE_8_FAILURE_MATRIX.md      # Matrix of 14 failure modes & fail-safe behaviors
├── PHASE_8_REPORT.md              # Final executive validation report
└── diagrams/
    ├── e2e_pipeline.mermaid       # End-to-end AI decision lifecycle diagram
    ├── causal_scm_flow.mermaid    # Pearl SCM counterfactual propagation diagram
    ├── multi_incident.mermaid     # Orchestration state machine & conflict graph
    └── production_arch.mermaid    # Docker deployment & network isolation topology
```

---

## H. Known Scientific Limitations & Threats to Validity

1. **Benchmark Size**: Evaluated on 80 controlled experiments in `dataset/tg_v1`.
2. **Fixed Topology**: Validated on a canonical 5-node cloud e-commerce topology (`api-gateway`, `order-service`, `inventory-service`, `payment-service`, `inventory-db`).
3. **Realized Warning Lead Time**: Approximately 4–5 seconds of lead time prior to physical telemetry breach; 5s/10s/30s horizons are evaluation targets, not realized alert times.
4. **Pre-Onset Localization**: 30% pre-onset target service attribution and 40% fault-type classification accuracy; post-onset GNN/SCM should be used for conclusive localization.
5. **Linear SCM Approximation**: SCM Ridge regression ($\alpha=1.0$) models linear multi-lag cross-node relationships; extreme queue saturation (e.g., `EXP-047`) exhibits nonlinear queue buildup that deviates from linear attenuation.
6. **Local Concurrency**: Performance benchmarks reflect a single-node local Docker Compose environment rather than a multi-region distributed cloud cluster.

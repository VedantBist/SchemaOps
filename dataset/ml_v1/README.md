# CausalOps ML Dataset v1 (`dataset/ml_v1`)

## Overview
This directory contains the frozen, experiment-level feature matrix and labels for classical machine learning baselines on CausalOps root-cause classification.

- **Experiments:** EXP-001 through EXP-080 (70 fault experiments + 10 NO_FAULT controls)
- **Feature Matrix Size:** 80 rows × 216 columns (1 `experiment_id` + 1 `target` + 214 features)
- **Features Count:** 214 engineered features
  - **Group A (Telemetry-only):** 103 features (statistical distributions of latency, error rate, throughput, pool saturation, anomaly scores)
  - **Group B (Temporal):** 55 features (step baselines, lag differences, rolling statistics, regression slopes, time-to-first-anomaly, duration, onset rank)
  - **Group C (Graph-aware):** 56 features (in/out degree, ancestor/descendant counts, upstream/downstream anomalies and latency, hop distance to earliest anomaly, propagation order score, cascade diameter)

## Files
1. `features.csv`: Tabular matrix of experiment-level features and ground-truth target.
2. `labels.csv`: Metadata and ground-truth labels for each experiment.
3. `feature_schema.json`: Complete catalog with descriptions, types, and leakage audit certification.
4. `splits.json`: Deterministic 70/15/15 train/val/test split IDs (seed=42).

## Leakage Prevention Guarantee
- Feature extraction strictly processes `metrics.json` and `topology.json`.
- Zero access to `detected_root_cause`, `detected_confidence`, `rca_match`, or ground truth parameters.
- Standard scalers and transformers must be fit solely on the training split.

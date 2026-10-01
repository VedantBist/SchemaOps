"""
Phase 6A — Real Failure Prediction Engine

Provides reproducible, scientifically-sound failure prediction for CausalOps
microservice telemetry using the frozen tg_v1 dataset.

Components:
    labels    — Leakage-safe binary label extraction (5s / 10s / 30s horizons)
    features  — Pre-fault feature engineering from pre-fault telemetry windows
    models    — LR baseline, Random Forest, Temporal GRU
    evaluation — Lead-time metrics, leakage audit, calibration
    predictor — Live inference service for Phase 6 orchestration
    generator — Reproducible dataset generation pipeline
"""

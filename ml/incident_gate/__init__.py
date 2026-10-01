"""CausalOps Incident Gating and Generalization Validation (Phase 2D).

Decouples incident detection from root-cause attribution:
  1. IncidentGateMLP: Binary incident detector (NORMAL vs INCIDENT)
  2. IncidentGate: Production-style gating component
  3. Gated RCA Pipeline: Composes Incident Gate with Spatio-Temporal GNN
  4. Generalization Validation: 5-fold cross-validation, traffic rate, and fault type robustness
"""

from ml.incident_gate.features import (
    extract_gate_features,
    fit_incident_gate_normalization,
    apply_incident_gate_normalization,
    prepare_gate_dataset,
    save_gate_normalization,
    load_gate_normalization
)
from ml.incident_gate.model import IncidentGateMLP, compute_binary_cross_entropy
from ml.incident_gate.train import train_incident_gate, select_best_threshold
from ml.incident_gate.gate import (
    IncidentGate,
    IncidentGateDecision,
    GatedRCAResult,
    predict_gated_rca
)
from ml.incident_gate.evaluate import (
    evaluate_gate_on_split,
    evaluate_gated_rca_pipeline
)
from ml.incident_gate.analysis import (
    run_experiment_level_cross_validation,
    analyze_traffic_rate_robustness,
    analyze_fault_type_robustness,
    analyze_early_detection
)

__all__ = [
    "extract_gate_features",
    "fit_incident_gate_normalization",
    "apply_incident_gate_normalization",
    "prepare_gate_dataset",
    "save_gate_normalization",
    "load_gate_normalization",
    "IncidentGateMLP",
    "compute_binary_cross_entropy",
    "train_incident_gate",
    "select_best_threshold",
    "IncidentGate",
    "IncidentGateDecision",
    "GatedRCAResult",
    "predict_gated_rca",
    "evaluate_gate_on_split",
    "evaluate_gated_rca_pipeline",
    "run_experiment_level_cross_validation",
    "analyze_traffic_rate_robustness",
    "analyze_fault_type_robustness",
    "analyze_early_detection"
]

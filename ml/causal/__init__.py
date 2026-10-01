"""
CausalOps Causal Inference Specification (Phase 3A).

This package defines the formal causal inference framework, variable schema,
intervention semantics, system-knowledge constraints, and evaluation metrics
for AI-based root-cause analysis and counterfactual validation in CausalOps.
"""

from pathlib import Path

CAUSAL_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = CAUSAL_DIR / "causal_schema.json"
INTERVENTIONS_PATH = CAUSAL_DIR / "interventions.json"
CONSTRAINTS_PATH = CAUSAL_DIR / "graph_constraints.json"
GROUND_TRUTH_SPEC_PATH = CAUSAL_DIR / "ground_truth_spec.json"
VARIABLE_CATALOG_PATH = CAUSAL_DIR / "variable_catalog.json"
CAUSAL_QUERIES_PATH = CAUSAL_DIR / "causal_queries.json"
METHOD_COMPARISON_PATH = CAUSAL_DIR / "method_comparison.json"

__version__ = "1.0.0"
__all__ = [
    "CAUSAL_DIR",
    "SCHEMA_PATH",
    "INTERVENTIONS_PATH",
    "CONSTRAINTS_PATH",
    "GROUND_TRUTH_SPEC_PATH",
    "VARIABLE_CATALOG_PATH",
    "CAUSAL_QUERIES_PATH",
    "METHOD_COMPARISON_PATH",
]

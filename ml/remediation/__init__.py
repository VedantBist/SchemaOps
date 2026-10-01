"""
CausalOps Self-Healing Recommendation & Safe Remediation Planning Module (Phase 4).
"""

from .action_catalog import (
    RemediationAction,
    RemediationActionType,
    ReversibilityLevel,
    RiskClass,
    ActionCatalog,
)
from .evaluator import (
    calculate_blast_radius,
    check_collateral_impact,
    compute_multi_criteria_score,
    evaluate_action,
)
from .recommender import (
    RemediationRecommender,
)

__all__ = [
    "RemediationAction",
    "RemediationActionType",
    "ReversibilityLevel",
    "RiskClass",
    "ActionCatalog",
    "calculate_blast_radius",
    "check_collateral_impact",
    "compute_multi_criteria_score",
    "evaluate_action",
    "RemediationRecommender",
]

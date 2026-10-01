"""
Structural Equation Fitting & Coefficient Estimation for CausalOps SCM.

Implements Ridge (and Lasso) regression over allowed lagged predictors
to learn structural coefficients A_ij^(k) and B_i.
"""

from typing import Dict, List, Tuple, Any, Optional
import numpy as np
from sklearn.linear_model import Ridge, Lasso
from sklearn.metrics import r2_score, mean_squared_error

from .design_matrix import (
    CANONICAL_NODES,
    PRIMARY_CAUSAL_FEATURES,
    build_lagged_design_matrix,
)
from .edges import CausalEdge, CausalGraph


def fit_target_variable(
    X: np.ndarray,
    y: np.ndarray,
    predictors_meta: List[Dict[str, Any]],
    alpha: float = 1.0,
    solver: str = "ridge",
    fit_intercept: bool = True,
    edge_threshold: float = 0.05,
) -> Tuple[Dict[str, Any], List[CausalEdge]]:
    """
    Fits regularized regression for a single target variable:
        y(t) = sum_k W_k * X_k(t - lag) + intercept + epsilon

    Returns:
        fit_metrics: Dictionary containing r2, mse, residual_std, intercept.
        edges: List of CausalEdge objects for each candidate predictor.
    """
    if X.shape[1] == 0 or len(y) == 0:
        # No predictors allowed (e.g. non-db node db_latency)
        fit_metrics = {
            "r2": 1.0 if np.allclose(y, 0.0) else 0.0,
            "mse": 0.0,
            "residual_std": 0.0,
            "intercept": 0.0,
            "n_samples": len(y),
            "n_features": 0,
        }
        return fit_metrics, []

    if solver == "lasso":
        model = Lasso(alpha=alpha, fit_intercept=fit_intercept, max_iter=2000, random_state=42)
    else:
        model = Ridge(alpha=alpha, fit_intercept=fit_intercept, random_state=42)

    model.fit(X, y)
    y_pred = model.predict(X)

    r2 = float(r2_score(y, y_pred)) if np.var(y) > 1e-8 else 1.0
    mse = float(mean_squared_error(y, y_pred))
    residuals = y - y_pred
    res_std = float(np.std(residuals))
    intercept = float(model.intercept_) if fit_intercept else 0.0

    fit_metrics = {
        "r2": r2,
        "mse": mse,
        "residual_std": res_std,
        "intercept": intercept,
        "n_samples": len(y),
        "n_features": X.shape[1],
        "coefficients": [float(c) for c in model.coef_],
    }

    edges: List[CausalEdge] = []
    for col_idx, meta in enumerate(predictors_meta):
        coef = float(model.coef_[col_idx])
        abs_coef = abs(coef)
        sign = "positive" if coef >= 0 else "negative"
        retained = abs_coef >= edge_threshold

        edge = CausalEdge(
            source_node=meta["source_node"],
            source_variable=meta["source_variable"],
            target_node=meta["target_node"],
            target_variable=meta["target_variable"],
            lag=meta["lag"],
            coefficient=coef,
            standardized_effect=coef,  # Data is pre-standardized
            absolute_effect=abs_coef,
            sign=sign,
            confidence=1.0 / (1.0 + np.exp(-abs_coef * 5.0)),  # Sigmoid confidence metric
            allowed_by_topology=True,
            retained=retained,
        )
        edges.append(edge)

    return fit_metrics, edges


def fit_full_scm(
    normalized_trajectories: List[np.ndarray],
    lag_order: int = 5,
    alpha: float = 1.0,
    solver: str = "ridge",
    fit_intercept: bool = True,
    edge_threshold: float = 0.05,
    allow_all_topology: bool = False,
) -> Tuple[Dict[str, Any], CausalGraph, Dict[str, Any]]:
    """
    Fits the complete Topology-Constrained Lagged Structural Causal Model
    across all 5 nodes and 7 primary features (35 structural equations).

    Returns:
        models_dict: Detailed fit metrics and coefficients per target variable.
        candidate_graph: CausalGraph containing all candidate edges.
        overall_summary: Aggregated fit statistics (mean R^2, total edges, etc.).
    """
    models_dict: Dict[str, Any] = {}
    all_edges: List[CausalEdge] = []
    r2_scores: List[float] = []
    mse_scores: List[float] = []

    for tgt_node in CANONICAL_NODES:
        for tgt_feat in PRIMARY_CAUSAL_FEATURES:
            var_name = f"{tgt_node}.{tgt_feat}"
            X, y, preds_meta = build_lagged_design_matrix(
                normalized_trajectories,
                tgt_node=tgt_node,
                tgt_feat=tgt_feat,
                lag_order=lag_order,
                allow_all_topology=allow_all_topology,
            )

            fit_meta, edges = fit_target_variable(
                X,
                y,
                preds_meta,
                alpha=alpha,
                solver=solver,
                fit_intercept=fit_intercept,
                edge_threshold=edge_threshold,
            )

            models_dict[var_name] = {
                "node": tgt_node,
                "feature": tgt_feat,
                "predictors_count": len(preds_meta),
                "predictors": preds_meta,
                "fit_metrics": fit_meta,
            }
            all_edges.extend(edges)
            r2_scores.append(fit_meta["r2"])
            mse_scores.append(fit_meta["mse"])

    candidate_graph = CausalGraph(all_edges, graph_name="CandidateCausalGraph")
    retained_graph = candidate_graph.filter_retained()

    overall_summary = {
        "lag_order": lag_order,
        "alpha": alpha,
        "solver": solver,
        "total_targets": len(models_dict),
        "mean_r2": float(np.mean(r2_scores)),
        "median_r2": float(np.median(r2_scores)),
        "mean_mse": float(np.mean(mse_scores)),
        "total_candidate_edges": len(candidate_graph.edges),
        "retained_candidate_edges": len(retained_graph.edges),
        "allow_all_topology": allow_all_topology,
    }

    return models_dict, candidate_graph, overall_summary

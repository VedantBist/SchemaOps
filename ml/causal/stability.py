"""
Bootstrap Stability Analysis for CausalOps SCM.

Implements experiment-level bootstrap resampling to compute edge selection frequencies,
mean coefficients, and empirical standard errors, producing the validated Stable Causal Graph.
"""

from typing import Dict, List, Tuple, Any, Optional
import numpy as np

from .edges import CausalEdge, CausalGraph
from .fit import fit_full_scm


def run_bootstrap_stability(
    train_trajectories: List[np.ndarray],
    base_candidate_graph: CausalGraph,
    lag_order: int = 5,
    alpha: float = 1.0,
    solver: str = "ridge",
    edge_threshold: float = 0.05,
    stability_threshold: float = 0.60,
    n_bootstrap: int = 50,
    random_seed: int = 42,
    allow_all_topology: bool = False,
) -> Tuple[Dict[str, Any], CausalGraph, CausalGraph]:
    """
    Performs experiment-level bootstrap resampling.

    Args:
        train_trajectories: List of [T_e, N, F] normalized arrays for training experiments.
        base_candidate_graph: The baseline candidate graph fitted on full training data.
        lag_order: Lag order P (default 5).
        alpha: Regularization penalty.
        solver: Regression solver ("ridge").
        edge_threshold: Minimum absolute effect to count an edge as active.
        stability_threshold: Minimum frequency (e.g. 0.60) across resamples to retain edge.
        n_bootstrap: Number of bootstrap resamples (default 50).
        random_seed: Reproducibility seed.
        allow_all_topology: If True, disables topology masking for ablation analysis.

    Returns:
        stability_records: Dictionary containing stability stats for each candidate edge.
        updated_candidate_graph: Base candidate graph annotated with stability metrics.
        stable_graph: Filtered graph containing only stable, high-confidence edges.
    """
    rng = np.random.RandomState(random_seed)
    n_train = len(train_trajectories)

    def edge_key(edge: CausalEdge) -> str:
        return f"{edge.source_node}.{edge.source_variable}->{edge.target_node}.{edge.target_variable}:lag{edge.lag}"

    # Track coefficients across bootstrap iterations
    all_keys = [edge_key(e) for e in base_candidate_graph.edges]
    edge_coefs: Dict[str, List[float]] = {k: [] for k in all_keys}
    edge_selected: Dict[str, int] = {k: 0 for k in all_keys}

    for b in range(n_bootstrap):
        # Experiment-level sampling with replacement
        boot_indices = rng.choice(n_train, size=n_train, replace=True)
        boot_trajectories = [train_trajectories[i] for i in boot_indices]

        # Fit SCM on resampled cohort
        _, boot_graph, _ = fit_full_scm(
            boot_trajectories,
            lag_order=lag_order,
            alpha=alpha,
            solver=solver,
            edge_threshold=edge_threshold,
            allow_all_topology=allow_all_topology,
        )

        for edge in boot_graph.edges:
            k = edge_key(edge)
            if k in edge_coefs:
                edge_coefs[k].append(edge.coefficient)
                if abs(edge.coefficient) >= edge_threshold:
                    edge_selected[k] += 1

    # Compute aggregate stability statistics
    stability_records: Dict[str, Any] = {}
    updated_edges: List[CausalEdge] = []
    stable_edges: List[CausalEdge] = []

    for base_edge in base_candidate_graph.edges:
        k = edge_key(base_edge)
        coefs = edge_coefs.get(k, [base_edge.coefficient])
        freq = edge_selected.get(k, 0) / float(n_bootstrap)
        mean_c = float(np.mean(coefs)) if coefs else base_edge.coefficient
        std_c = float(np.std(coefs)) if coefs else 0.0

        is_stable = (
            base_edge.allowed_by_topology
            and freq >= stability_threshold
            and abs(mean_c) >= edge_threshold
        )

        rec = {
            "edge_key": k,
            "source_node": base_edge.source_node,
            "source_variable": base_edge.source_variable,
            "target_node": base_edge.target_node,
            "target_variable": base_edge.target_variable,
            "lag": base_edge.lag,
            "base_coefficient": base_edge.coefficient,
            "mean_coefficient": round(mean_c, 6),
            "std_coefficient": round(std_c, 6),
            "selection_frequency": round(freq, 4),
            "stable": is_stable,
            "allowed_by_topology": base_edge.allowed_by_topology,
        }
        stability_records[k] = rec

        updated_edge = CausalEdge(
            source_node=base_edge.source_node,
            source_variable=base_edge.source_variable,
            target_node=base_edge.target_node,
            target_variable=base_edge.target_variable,
            lag=base_edge.lag,
            coefficient=mean_c,
            standardized_effect=mean_c,
            absolute_effect=abs(mean_c),
            sign="positive" if mean_c >= 0 else "negative",
            confidence=freq,
            allowed_by_topology=base_edge.allowed_by_topology,
            retained=abs(mean_c) >= edge_threshold,
            selection_frequency=freq,
            coefficient_std=std_c,
        )
        updated_edges.append(updated_edge)

        if is_stable:
            stable_edges.append(updated_edge)

    updated_candidate_graph = CausalGraph(updated_edges, graph_name="AnnotatedCandidateCausalGraph")
    stable_graph = CausalGraph(stable_edges, graph_name="StableCausalGraph")

    return stability_records, updated_candidate_graph, stable_graph

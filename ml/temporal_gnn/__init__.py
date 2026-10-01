"""CausalOps Temporal Graph Learning (Phase 2C).

Implements sequential temporal and spatio-temporal architectures for root cause analysis:
  - Stage 2C-1: TemporalOnlyBaseline (temporal modeling without graph edges)
  - Stage 2C-2: TemporalGATBaseline (graph attention per timestep + temporal GRU)
  - Stage 2C-3: SpatioTemporalGNN (joint node-level spatio-temporal sequence modeling)
"""

from ml.temporal_gnn.features import (
    FEATURE_SETS,
    fit_temporal_normalization,
    prepare_temporal_batch,
    save_temporal_normalization,
    load_temporal_normalization
)
from ml.temporal_gnn.temporal_model import TemporalOnlyBaseline, MaskedGRU
from ml.temporal_gnn.temporal_gat import TemporalGATBaseline
from ml.temporal_gnn.spatiotemporal import SpatioTemporalGNN
from ml.temporal_gnn.train import train_temporal_model
from ml.temporal_gnn.evaluate import (
    evaluate_temporal_model_on_samples,
    evaluate_temporal_controls,
    evaluate_checkpoint_file,
    load_and_reconstruct_model
)
from ml.temporal_gnn.analysis import analyze_experiment_propagation

__all__ = [
    "FEATURE_SETS",
    "fit_temporal_normalization",
    "prepare_temporal_batch",
    "save_temporal_normalization",
    "load_temporal_normalization",
    "TemporalOnlyBaseline",
    "MaskedGRU",
    "TemporalGATBaseline",
    "SpatioTemporalGNN",
    "train_temporal_model",
    "evaluate_temporal_model_on_samples",
    "evaluate_temporal_controls",
    "evaluate_checkpoint_file",
    "load_and_reconstruct_model",
    "analyze_experiment_propagation"
]

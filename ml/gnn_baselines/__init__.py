"""CausalOps Graph Neural Network Baselines (Phase 2B).

Provides MLP, GCN, and GAT baselines operating on the CausalOps Temporal Graph Dataset (dataset/tg_v1/).
"""

from ml.gnn_baselines.features import (
    FEATURE_SETS,
    aggregate_sample_temporal_features,
    fit_normalization,
    apply_normalization,
    save_normalization_params,
    load_normalization_params
)
from ml.gnn_baselines.models import (
    MLPBaseline,
    GCNBaseline,
    GATBaseline,
    compute_cross_entropy,
    compute_cross_entropy_grad,
    Adam
)
from ml.gnn_baselines.metrics import (
    evaluate_predictions,
    evaluate_controls,
    CLASS_NAMES
)
from ml.gnn_baselines.train import train_model, prepare_dataset_arrays
from ml.gnn_baselines.evaluate import evaluate_model_on_split, evaluate_checkpoint_file
from ml.gnn_baselines.utils import set_seed, save_checkpoint, load_checkpoint

__all__ = [
    "MLPBaseline",
    "GCNBaseline",
    "GATBaseline",
    "FEATURE_SETS",
    "aggregate_sample_temporal_features",
    "fit_normalization",
    "apply_normalization",
    "save_normalization_params",
    "load_normalization_params",
    "compute_cross_entropy",
    "compute_cross_entropy_grad",
    "Adam",
    "evaluate_predictions",
    "evaluate_controls",
    "CLASS_NAMES",
    "train_model",
    "prepare_dataset_arrays",
    "evaluate_model_on_split",
    "evaluate_checkpoint_file",
    "set_seed",
    "save_checkpoint",
    "load_checkpoint"
]

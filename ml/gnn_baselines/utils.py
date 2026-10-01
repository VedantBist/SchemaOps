"""Utility functions for reproducibility, checkpointing, and reporting."""

import os
import random
import json
import pickle
from typing import Dict, Any, Optional
import numpy as np


def set_seed(seed: int = 42) -> None:
    """Sets random seeds for reproducibility across random, numpy."""
    random.seed(seed)
    np.random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)


def save_checkpoint(
    filepath: str,
    model: Any,
    config: Dict[str, Any],
    norm_mean: np.ndarray,
    norm_std: np.ndarray,
    val_metrics: Dict[str, Any],
    best_epoch: int
) -> None:
    """
    Saves model weights, architecture configuration, normalization parameters, and metrics.
    """
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    checkpoint = {
        "model_name": model.name,
        "config": config,
        "state_dict": model.state_dict(),
        "norm_mean": norm_mean,
        "norm_std": norm_std,
        "val_metrics": val_metrics,
        "best_epoch": best_epoch
    }
    with open(filepath, "wb") as f:
        pickle.dump(checkpoint, f)


def load_checkpoint(filepath: str) -> Dict[str, Any]:
    """Loads checkpoint from disk."""
    with open(filepath, "rb") as f:
        checkpoint = pickle.load(f)
    return checkpoint

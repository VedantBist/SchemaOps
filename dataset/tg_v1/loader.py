"""Loader and dataset representation API for CausalOps Temporal Graph Dataset v1.

Provides a clean, framework-neutral interface to load and iterate over temporal graph samples:
- Framework-agnostic numpy representation ([T, 5, 10] and [40, 5, 10])
- Seamless split filtering ('train', 'validation', 'test')
- Control filtering ('fault_only=True' for 4-class supervised GNN training)
- Optional PyTorch / PyTorch Geometric conversion helpers
"""

import os
import json
from dataclasses import dataclass
from typing import Dict, List, Any, Optional, Iterator, Union
import numpy as np

from dataset.tg_v1.schema import NODE_ORDER, NODE_FEATURE_NAMES, NUM_NODES, NUM_NODE_FEATURES


@dataclass
class TemporalGraphSample:
    """Represents a single temporal graph experiment sample."""
    experiment_id: str
    x: np.ndarray                  # Unpadded temporal feature tensor: shape [T, N, F]
    x_padded: np.ndarray           # Uniformly padded tensor: shape [40, N, F]
    temporal_mask: np.ndarray      # Boolean mask of valid timesteps: shape [40]
    edge_index: np.ndarray         # Directed graph edge indices: shape [2, E]
    timestamps: List[str]          # Original ISO timestamp strings: length T
    relative_time_sec: np.ndarray  # Chronological relative time in seconds: shape [T]
    sequence_length: int           # Original sequence length T (in [38, 40])
    is_fault: bool                 # True for fault experiments, False for controls
    label: str                     # Target service name or "NO_FAULT"
    label_type: str                # "ROOT_CAUSE" or "NO_FAULT"
    target_class: int              # Target class index 0..3 (-1 for NO_FAULT)
    node_label_index: int          # Target graph node index 0..4 (-1 for NO_FAULT)
    fault_type: str                # Specific injected fault type or "NO_FAULT"
    traffic_rate_rps: int          # Injected traffic rate in requests/sec
    split: str                     # Dataset partition: "train", "validation", "test"
    node_names: List[str]          # Canonical node names
    feature_names: List[str]       # Canonical feature names

    @property
    def node_features(self) -> np.ndarray:
        """Alias for unpadded node features tensor [T, N, F]."""
        return self.x

    @property
    def node_features_padded(self) -> np.ndarray:
        """Alias for padded node features tensor [40, N, F]."""
        return self.x_padded

    def to_dict(self) -> Dict[str, Any]:
        """Serializes sample metadata to dictionary."""
        return {
            "experiment_id": self.experiment_id,
            "sequence_length": self.sequence_length,
            "is_fault": self.is_fault,
            "label": self.label,
            "label_type": self.label_type,
            "target_class": self.target_class,
            "node_label_index": self.node_label_index,
            "fault_type": self.fault_type,
            "traffic_rate_rps": self.traffic_rate_rps,
            "split": self.split,
            "t0": self.timestamps[0] if self.timestamps else None,
            "t_last": self.timestamps[-1] if self.timestamps else None
        }

    def to_torch(self):
        """
        Converts arrays to PyTorch tensors if PyTorch is installed.
        Returns a dict of tensors without hard dependency on torch.
        """
        try:
            import torch
            return {
                "experiment_id": self.experiment_id,
                "x": torch.from_numpy(self.x),
                "x_padded": torch.from_numpy(self.x_padded),
                "temporal_mask": torch.from_numpy(self.temporal_mask),
                "edge_index": torch.from_numpy(self.edge_index),
                "relative_time_sec": torch.from_numpy(self.relative_time_sec),
                "target_class": torch.tensor(self.target_class, dtype=torch.long),
                "node_label_index": torch.tensor(self.node_label_index, dtype=torch.long),
                "is_fault": torch.tensor(self.is_fault, dtype=torch.bool),
                "split": self.split,
                "label": self.label
            }
        except ImportError:
            raise ImportError("PyTorch is not installed. Use sample.x and sample.edge_index directly as NumPy arrays.")


class TemporalGraphDataset:
    """Dataset reader and iterator for CausalOps Temporal Graph Dataset v1."""

    def __init__(
        self,
        dataset_dir: str = "dataset/tg_v1",
        split: Optional[str] = None,
        fault_only: bool = False
    ):
        self.dataset_dir = dataset_dir
        self.split = split
        self.fault_only = fault_only

        manifest_path = os.path.join(dataset_dir, "graph_dataset_manifest.json")
        if not os.path.exists(manifest_path):
            raise FileNotFoundError(
                f"Manifest not found at {manifest_path}. "
                "Please run 'python3 -m dataset.tg_v1.builder' first."
            )

        with open(manifest_path, "r") as f:
            self.manifest = json.load(f)

        self.samples_dir = os.path.join(dataset_dir, "samples")
        self.samples_meta = self.manifest["samples"]

        # Filter by split if requested
        if split is not None:
            valid_splits = {"train", "validation", "test"}
            if split not in valid_splits:
                raise ValueError(f"Invalid split '{split}'. Must be one of {valid_splits}")
            self.samples_meta = [m for m in self.samples_meta if m["split"] == split]

        # Filter fault-only if requested (exclude controls)
        if fault_only:
            self.samples_meta = [m for m in self.samples_meta if m["is_fault"]]

        self.by_id: Dict[str, Dict[str, Any]] = {m["experiment_id"]: m for m in self.samples_meta}

    def __len__(self) -> int:
        return len(self.samples_meta)

    def __iter__(self) -> Iterator[TemporalGraphSample]:
        for idx in range(len(self)):
            yield self[idx]

    def __getitem__(self, idx: int) -> TemporalGraphSample:
        if idx < 0 or idx >= len(self.samples_meta):
            raise IndexError(f"Index {idx} out of range for dataset with {len(self.samples_meta)} samples")

        meta = self.samples_meta[idx]
        exp_id = meta["experiment_id"]
        return self.get(exp_id)

    def get(self, exp_id: str) -> TemporalGraphSample:
        """Loads a specific experiment by ID."""
        if exp_id not in self.by_id:
            raise KeyError(f"Experiment {exp_id} not in current dataset selection")

        meta = self.by_id[exp_id]
        npz_path = os.path.join(self.dataset_dir, meta["npz_file"])

        if not os.path.exists(npz_path):
            raise FileNotFoundError(f"Sample file not found: {npz_path}")

        data = np.load(npz_path, allow_pickle=True)

        return TemporalGraphSample(
            experiment_id=exp_id,
            x=data["x"],
            x_padded=data["x_padded"],
            temporal_mask=data["temporal_mask"],
            edge_index=data["edge_index"],
            timestamps=list(data["timestamps"]),
            relative_time_sec=data["relative_time_sec"],
            sequence_length=int(data["sequence_length"]),
            is_fault=bool(data["is_fault"]),
            label=meta["label"],
            label_type=meta["label_type"],
            target_class=int(data["target_class"]),
            node_label_index=int(data["node_label_index"]),
            fault_type=meta["fault_type"],
            traffic_rate_rps=meta["traffic_rate_rps"],
            split=meta["split"],
            node_names=list(NODE_ORDER),
            feature_names=list(NODE_FEATURE_NAMES)
        )

    def get_split(self, split_name: str, fault_only: bool = False) -> "TemporalGraphDataset":
        """Returns a new TemporalGraphDataset filtered to the specified split."""
        return TemporalGraphDataset(
            dataset_dir=self.dataset_dir,
            split=split_name,
            fault_only=fault_only
        )

    def summary(self) -> Dict[str, Any]:
        """Returns high-level summary of current dataset partition."""
        return {
            "total_samples": len(self),
            "split": self.split,
            "fault_only": self.fault_only,
            "fault_count": sum(1 for m in self.samples_meta if m["is_fault"]),
            "control_count": sum(1 for m in self.samples_meta if not m["is_fault"]),
            "target_breakdown": {
                target: sum(1 for m in self.samples_meta if m["label"] == target)
                for target in ["inventory-db", "inventory-service", "order-service", "payment-service", "NO_FAULT"]
            }
        }

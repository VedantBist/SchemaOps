"""Lightweight CLI inspection utility for CausalOps Temporal Graph Dataset v1.

Prints a human-readable summary of any experiment in tg_v1:
- Experiment ID, ground truth root cause, label type, split, fault type
- Graph topology edges & canonical node ordering
- Matrix dimensions [T, 5, 10] and [40, 5, 10]
- Node feature names
- Sample snapshot slices across key timesteps (t=0, t=onset, t=end)

Usage:
    python3 -m dataset.tg_v1.inspect --exp EXP-015
    python3 -m dataset.tg_v1.inspect --exp EXP-001
"""

import argparse
import numpy as np
from dataset.tg_v1.loader import TemporalGraphDataset
from dataset.tg_v1.schema import EDGES


def inspect_experiment(exp_id: str = "EXP-015", step: int = 15):
    """Prints detailed human-readable representation of one temporal graph sample."""
    ds = TemporalGraphDataset()
    try:
        sample = ds.get(exp_id)
    except KeyError:
        print(f"Error: Experiment {exp_id} not found in tg_v1 dataset.")
        return

    print("=" * 70)
    print(f" TEMPORAL GRAPH INSPECTOR — {sample.experiment_id}")
    print("=" * 70)
    print(f"Root cause:        {sample.label} (type: {sample.label_type})")
    print(f"Target class index:{sample.target_class}")
    print(f"Node label index:  {sample.node_label_index}")
    print(f"Split partition:   {sample.split}")
    print(f"Injected fault:    {sample.fault_type} @ {sample.traffic_rate_rps} req/s")
    print(f"Sequence length:   {sample.sequence_length} timesteps (interval: 1.0s)")
    print(f"Total duration:    {sample.relative_time_sec[-1]:.2f} seconds")
    print()
    print("Graph Structure:")
    print(f"  Nodes ({len(sample.node_names)}): {sample.node_names}")
    print(f"  Edges ({len(EDGES)}): {[' -> '.join(e) for e in EDGES]}")
    print(f"  Edge Index:\n{sample.edge_index}")
    print()
    print(f"Feature Matrix Dimensions:")
    print(f"  Unpadded [T, N, F]: {sample.x.shape}")
    print(f"  Padded   [40, N, F]:{sample.x_padded.shape}")
    print(f"  Temporal Mask:      {sample.temporal_mask.shape} (valid steps: {sample.temporal_mask.sum()})")
    print()
    print("Feature Names (F=10):")
    for idx, fn in enumerate(sample.feature_names):
        print(f"  [{idx}] {fn}")
    print()

    # Show temporal snapshot at requested step
    s_idx = min(step, sample.sequence_length - 1)
    ts = sample.timestamps[s_idx]
    rel_t = sample.relative_time_sec[s_idx]

    print(f"--- Snapshot at step t={s_idx} (rel_t={rel_t:.1f}s, ISO={ts}) ---")
    header = f"{'Node':20s} | " + " | ".join(f"{fn[:6]:>6s}" for fn in sample.feature_names)
    print(header)
    print("-" * len(header))

    x_step = sample.x[s_idx]  # [5, 10]
    for n_idx, node_name in enumerate(sample.node_names):
        vals = " | ".join(f"{v:6.1f}" for v in x_step[n_idx])
        is_target_marker = " * " if sample.label == node_name else "   "
        print(f"{is_target_marker}{node_name:17s} | {vals}")

    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inspect a CausalOps temporal graph sample.")
    parser.add_argument("--exp", type=str, default="EXP-015", help="Experiment ID to inspect (e.g. EXP-015)")
    parser.add_argument("--step", type=int, default=15, help="Time step index to display (0..T-1)")
    args = parser.parse_args()
    inspect_experiment(args.exp, args.step)

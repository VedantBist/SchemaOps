"""Deterministic, stratified experiment-level split for CausalOps ML v1."""

import os
import json
from typing import Dict, List, Any, Tuple
import pandas as pd
from sklearn.model_selection import train_test_split

DEFAULT_SEED = 42

def create_experiment_splits(
    labels_csv_path: str = "dataset/ml_v1/labels.csv",
    output_path: str = "dataset/ml_v1/splits.json",
    random_seed: int = DEFAULT_SEED
) -> Dict[str, Any]:
    """
    Splits experiments deterministically by experiment ID:
    - 70% Train (~56 experiments)
    - 15% Validation (~12 experiments)
    - 15% Test (~12 experiments)
    
    Stratified by ground-truth target (with NO_FAULT controls kept separate).
    """
    df = pd.read_csv(labels_csv_path)
    total_exp = len(df)
    assert total_exp == 80, f"Expected 80 experiments, got {total_exp}"

    # We stratify on fault_target to ensure balanced representation of all 4 services + controls
    strat_key = df['fault_target'].astype(str)
    all_ids = df['experiment_id'].tolist()

    # Step 1: Split 70% train (56) vs 30% temp (24)
    train_ids, temp_ids, _, y_temp = train_test_split(
        all_ids,
        strat_key,
        test_size=24,
        stratify=strat_key,
        random_state=random_seed
    )

    # Step 2: Split temp 50/50 into validation (12) and test (12)
    val_ids, test_ids, _, _ = train_test_split(
        temp_ids,
        y_temp,
        test_size=12,
        stratify=y_temp,
        random_state=random_seed
    )

    train_ids = sorted(train_ids)
    val_ids = sorted(val_ids)
    test_ids = sorted(test_ids)

    # Integrity verification: zero overlap, complete partition
    set_train = set(train_ids)
    set_val = set(val_ids)
    set_test = set(test_ids)

    assert len(set_train) == 56, f"Train set size is {len(set_train)}, expected 56"
    assert len(set_val) == 12, f"Val set size is {len(set_val)}, expected 12"
    assert len(set_test) == 12, f"Test set size is {len(set_test)}, expected 12"

    assert len(set_train.intersection(set_val)) == 0, "Train and Val overlap detected!"
    assert len(set_val.intersection(set_test)) == 0, "Val and Test overlap detected!"
    assert len(set_train.intersection(set_test)) == 0, "Train and Test overlap detected!"
    assert len(set_train.union(set_val).union(set_test)) == 80, "Partition incomplete!"

    # Target counts breakdown
    df_by_id = df.set_index('experiment_id')
    train_targets = df_by_id.loc[train_ids, 'fault_target'].value_counts().to_dict()
    val_targets = df_by_id.loc[val_ids, 'fault_target'].value_counts().to_dict()
    test_targets = df_by_id.loc[test_ids, 'fault_target'].value_counts().to_dict()

    train_fault_count = sum(v for k, v in train_targets.items() if k != 'NO_FAULT')
    val_fault_count = sum(v for k, v in val_targets.items() if k != 'NO_FAULT')
    test_fault_count = sum(v for k, v in test_targets.items() if k != 'NO_FAULT')

    splits_data = {
        "dataset_version": "1.0.0",
        "random_seed": random_seed,
        "split_ratio": {
            "train": 0.70,
            "validation": 0.15,
            "test": 0.15
        },
        "counts": {
            "total_experiments": 80,
            "train_total": len(train_ids),
            "train_fault_count": train_fault_count,
            "train_control_count": train_targets.get('NO_FAULT', 0),
            "val_total": len(val_ids),
            "val_fault_count": val_fault_count,
            "val_control_count": val_targets.get('NO_FAULT', 0),
            "test_total": len(test_ids),
            "test_fault_count": test_fault_count,
            "test_control_count": test_targets.get('NO_FAULT', 0)
        },
        "breakdown_by_target": {
            "train": train_targets,
            "validation": val_targets,
            "test": test_targets
        },
        "train_ids": train_ids,
        "validation_ids": val_ids,
        "test_ids": test_ids,
        "leakage_audit": {
            "disjoint_check": "PASSED (zero overlap between train, val, and test)",
            "coverage_check": "PASSED (exact 80/80 partition)",
            "unit_of_split": "EXPERIMENT_ID (no row-level temporal leakage)"
        }
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(splits_data, f, indent=2)

    print(f" [+] Wrote {output_path}")
    print(f"     Train: {len(train_ids)} ({train_fault_count} fault + {train_targets.get('NO_FAULT', 0)} control)")
    print(f"     Val:   {len(val_ids)} ({val_fault_count} fault + {val_targets.get('NO_FAULT', 0)} control)")
    print(f"     Test:  {len(test_ids)} ({test_fault_count} fault + {test_targets.get('NO_FAULT', 0)} control)")

    return splits_data

if __name__ == "__main__":
    create_experiment_splits()

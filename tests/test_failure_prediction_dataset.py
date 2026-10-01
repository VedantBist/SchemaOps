"""
Phase 6A Tests — Dataset Generator and Manifest Integrity
Tests the failure_prediction_v1 dataset generation pipeline.
"""
import pytest
import json
import numpy as np
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))


DATASET_DIR = Path("dataset/failure_prediction_v1")


class TestDatasetManifest:
    """Tests the generated dataset manifest and integrity."""

    @pytest.fixture(scope="class")
    def manifest(self):
        p = DATASET_DIR / "manifest.json"
        if not p.exists():
            pytest.skip("Dataset not generated yet")
        with open(p) as f:
            return json.load(f)

    def test_manifest_version(self, manifest):
        assert manifest["dataset_version"] == "failure_prediction_v1"

    def test_manifest_total_experiments(self, manifest):
        assert manifest["total_experiments"] == 80

    def test_manifest_feature_dim(self, manifest):
        assert manifest["feature_dim"] == 151

    def test_manifest_splits_count(self, manifest):
        splits = manifest["splits"]
        total = sum(s["n_samples"] for s in splits.values())
        assert total == 80

    def test_manifest_train_split(self, manifest):
        train = manifest["splits"]["train"]
        assert train["n_samples"] == 56

    def test_manifest_val_split(self, manifest):
        val = manifest["splits"]["validation"]
        assert val["n_samples"] == 12

    def test_manifest_test_split(self, manifest):
        test = manifest["splits"]["test"]
        assert test["n_samples"] == 12

    def test_manifest_audits_passed(self, manifest):
        assert manifest["leakage_audit_status"] == "PASSED"
        assert manifest["label_audit_status"] == "PASSED"

    def test_manifest_horizons(self, manifest):
        assert set(manifest["horizons"]) == {"within_5s", "within_10s", "within_30s"}


class TestDatasetFiles:
    """Tests that all expected dataset files exist and have correct shapes."""

    def test_features_train_shape(self):
        p = DATASET_DIR / "features_train.npy"
        if not p.exists():
            pytest.skip("Dataset not generated yet")
        X = np.load(p)
        assert X.shape == (56, 151)

    def test_features_val_shape(self):
        p = DATASET_DIR / "features_val.npy"
        if not p.exists():
            pytest.skip("Dataset not generated yet")
        X = np.load(p)
        assert X.shape == (12, 151)

    def test_features_test_shape(self):
        p = DATASET_DIR / "features_test.npy"
        if not p.exists():
            pytest.skip("Dataset not generated yet")
        X = np.load(p)
        assert X.shape == (12, 151)

    def test_no_nan_features(self):
        for split in ["train", "val", "test"]:
            p = DATASET_DIR / f"features_{split}.npy"
            if not p.exists():
                pytest.skip("Dataset not generated yet")
            X = np.load(p)
            assert not np.any(np.isnan(X)), f"{split} features contain NaN"
            assert not np.any(np.isinf(X)), f"{split} features contain Inf"

    def test_labels_file_exists(self):
        p = DATASET_DIR / "labels.json"
        if not p.exists():
            pytest.skip("Dataset not generated yet")
        with open(p) as f:
            labels = json.load(f)
        assert len(labels) == 80

    def test_normalization_file_exists(self):
        p = DATASET_DIR / "normalization.json"
        if not p.exists():
            pytest.skip("Dataset not generated yet")
        with open(p) as f:
            norm = json.load(f)
        assert len(norm["feature_mean"]) == 151
        assert len(norm["feature_std"]) == 151

    def test_sequences_npz_exists(self):
        for split in ["train", "val", "test"]:
            p = DATASET_DIR / f"sequences_{split}.npz"
            assert p.exists(), f"sequences_{split}.npz not found"

    def test_label_audit_exists(self):
        p = DATASET_DIR / "label_audit.json"
        if not p.exists():
            pytest.skip("Dataset not generated yet")
        with open(p) as f:
            audit = json.load(f)
        assert audit["status"] == "PASSED"

    def test_feature_leakage_audit_exists(self):
        p = DATASET_DIR / "feature_leakage_audit.json"
        if not p.exists():
            pytest.skip("Dataset not generated yet")
        with open(p) as f:
            audit = json.load(f)
        assert audit["id_overlap_check"] == "PASSED"


class TestDatasetReproducibility:
    """Tests that dataset generation is reproducible (deterministic)."""

    def test_features_deterministic(self, tmp_path):
        """Regenerating features produces identical output."""
        from ml.failure_prediction.features import (
            build_feature_matrix, fit_normalization_stats
        )
        from ml.failure_prediction.labels import compute_labels_for_dataset
        from dataset.tg_v1.loader import TemporalGraphDataset

        labels = compute_labels_for_dataset("dataset/tg_v1")
        labels_by_id = {l.experiment_id: l for l in labels}
        ds = TemporalGraphDataset("dataset/tg_v1", split="train")

        X1, _, _ = build_feature_matrix(ds, labels_by_id)
        X2, _, _ = build_feature_matrix(ds, labels_by_id)
        assert np.allclose(X1, X2), "Feature extraction is not deterministic"

    def test_split_ids_match_manifest(self):
        """Experiment IDs in the manifest match the tg_v1 splits."""
        manifest_path = DATASET_DIR / "manifest.json"
        if not manifest_path.exists():
            pytest.skip("Dataset not generated yet")
        with open(manifest_path) as f:
            manifest = json.load(f)

        from dataset.tg_v1.loader import TemporalGraphDataset
        for split_name, split_key in [("train", "train"), ("validation", "validation"), ("test", "test")]:
            ds = TemporalGraphDataset("dataset/tg_v1", split=split_name)
            ds_ids = sorted([ds[i].experiment_id for i in range(len(ds))])
            manifest_key = "validation" if split_name == "validation" else split_name
            if manifest_key in manifest["splits"]:
                manifest_ids = sorted(manifest["splits"][manifest_key].get("experiment_ids", []))
                if manifest_ids:
                    assert ds_ids == manifest_ids, f"ID mismatch for {split_name}"

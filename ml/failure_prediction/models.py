"""
Phase 6A — Failure Prediction Models

Implements three model tiers for multi-horizon failure prediction:

    1. LRBaseline     — Logistic Regression (scikit-learn), L2 regularized.
                        Serves as the interpretable minimum viable predictor.

    2. RandomForest   — Random Forest Classifier (scikit-learn).
                        Ensemble method capturing non-linear interactions.

    3. TemporalGRU    — GRU-based sequence classifier operating on the
                        pre-fault telemetry window directly, without any
                        feature engineering. Uses only the primary 7 features
                        from the tg_v1 x tensor.

All models implement the same interface:
    model.fit(X_train, y_train) → fitted model
    model.predict(X) → np.ndarray of 0/1 labels
    model.predict_proba(X) → np.ndarray of [N, 2] probabilities
    model.to_dict() → serializable metadata dict

Model selection is horizon-specific: a separate model is trained for
each of {within_5s, within_10s, within_30s}.

NOTES:
    - TemporalGRU takes raw x tensors (NOT feature-engineered vectors).
    - LR and RF take the engineered feature vectors from features.py.
    - All model hyperparameters are fixed for reproducibility.
    - The TemporalGRU is trained to convergence with early stopping but
      without GPU requirement (runs on CPU in ~30s for this dataset size).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
import json

# ─────────────────────────────────────────────────────────
# Scikit-learn models
# ─────────────────────────────────────────────────────────

class LRBaselineModel:
    """
    Logistic Regression baseline with L2 regularization.
    Trained on engineered feature vectors from features.py.
    """
    MODEL_TYPE = "logistic_regression"
    RANDOM_STATE = 42

    def __init__(self, C: float = 1.0, max_iter: int = 1000):
        self.C = C
        self.max_iter = max_iter
        self._clf = None
        self._is_fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> "LRBaselineModel":
        from sklearn.linear_model import LogisticRegression
        self._clf = LogisticRegression(
            C=self.C,
            max_iter=self.max_iter,
            random_state=self.RANDOM_STATE,
            class_weight="balanced",
            solver="lbfgs",
        )
        self._clf.fit(X, y)
        self._is_fitted = True
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self._is_fitted:
            raise RuntimeError("Model not fitted.")
        return self._clf.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self._is_fitted:
            raise RuntimeError("Model not fitted.")
        return self._clf.predict_proba(X)

    def feature_importances(self) -> np.ndarray:
        """Returns absolute coefficient magnitudes (proxy for importance)."""
        if not self._is_fitted:
            raise RuntimeError("Model not fitted.")
        return np.abs(self._clf.coef_[0])

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "model_type": self.MODEL_TYPE,
            "C": self.C,
            "max_iter": self.max_iter,
            "is_fitted": self._is_fitted,
        }
        if self._is_fitted:
            d["n_features"] = self._clf.n_features_in_
            d["classes"] = self._clf.classes_.tolist()
        return d


class RandomForestModel:
    """
    Random Forest classifier for failure prediction.
    Trained on engineered feature vectors from features.py.
    """
    MODEL_TYPE = "random_forest"
    RANDOM_STATE = 42

    def __init__(self, n_estimators: int = 100, max_depth: Optional[int] = None):
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self._clf = None
        self._is_fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> "RandomForestModel":
        from sklearn.ensemble import RandomForestClassifier
        self._clf = RandomForestClassifier(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            random_state=self.RANDOM_STATE,
            class_weight="balanced",
            n_jobs=1,
        )
        self._clf.fit(X, y)
        self._is_fitted = True
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self._is_fitted:
            raise RuntimeError("Model not fitted.")
        return self._clf.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self._is_fitted:
            raise RuntimeError("Model not fitted.")
        return self._clf.predict_proba(X)

    def feature_importances(self) -> np.ndarray:
        if not self._is_fitted:
            raise RuntimeError("Model not fitted.")
        return self._clf.feature_importances_

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "model_type": self.MODEL_TYPE,
            "n_estimators": self.n_estimators,
            "max_depth": self.max_depth,
            "is_fitted": self._is_fitted,
        }
        if self._is_fitted:
            d["n_features"] = self._clf.n_features_in_
            d["classes"] = self._clf.classes_.tolist()
        return d


# ─────────────────────────────────────────────────────────
# Temporal GRU model (pure NumPy / minimal dependency)
# ─────────────────────────────────────────────────────────

class TemporalGRUModel:
    """
    GRU-based failure predictor operating directly on pre-fault telemetry sequences.

    Architecture:
        Input: [W, N, 7] pre-fault telemetry → flatten spatial dim → [W, N*7=35]
        GRU(hidden_dim=32, 1 layer) → take final hidden state → [32]
        Linear(32, 2) → softmax → failure probability

    Training:
        - Adam optimizer, lr=1e-3, weight_decay=1e-4
        - CrossEntropy loss with class weighting for imbalance
        - Early stopping (patience=20 epochs, val loss)
        - Maximum 200 epochs

    Inference:
        - Accepts variable-length sequences (padded internally)
        - Returns probability of failure [0, 1]

    NOTE:
        If PyTorch is not available, falls back to scikit-learn GradientBoosting
        on flattened sequence statistics. This fallback is clearly flagged in
        the model metadata.
    """
    MODEL_TYPE = "temporal_gru"
    RANDOM_STATE = 42
    N_FEATURES_PER_NODE = 7
    N_NODES = 5
    INPUT_DIM = N_FEATURES_PER_NODE * N_NODES  # 35

    def __init__(
        self,
        hidden_dim: int = 32,
        n_layers: int = 1,
        lr: float = 1e-3,
        weight_decay: float = 1e-4,
        max_epochs: int = 200,
        patience: int = 20,
        batch_size: int = 16,
    ):
        self.hidden_dim = hidden_dim
        self.n_layers = n_layers
        self.lr = lr
        self.weight_decay = weight_decay
        self.max_epochs = max_epochs
        self.patience = patience
        self.batch_size = batch_size
        self._use_torch = False
        self._model = None
        self._fallback_clf = None
        self._is_fitted = False
        self._train_epochs = 0
        self._best_val_loss = float("inf")

    def _build_model(self):
        """Builds the PyTorch GRU model."""
        import torch
        import torch.nn as nn

        class GRUClassifier(nn.Module):
            def __init__(self, input_dim, hidden_dim, n_layers):
                super().__init__()
                self.gru = nn.GRU(
                    input_dim, hidden_dim, n_layers,
                    batch_first=True, dropout=0.0
                )
                self.classifier = nn.Sequential(
                    nn.Linear(hidden_dim, 16),
                    nn.ReLU(),
                    nn.Linear(16, 2),
                )

            def forward(self, x, lengths):
                # x: [B, T_max, input_dim]
                # Pack padded
                packed = torch.nn.utils.rnn.pack_padded_sequence(
                    x, lengths.cpu(), batch_first=True, enforce_sorted=False
                )
                _, h_n = self.gru(packed)
                last_hidden = h_n[-1]  # [B, hidden_dim]
                return self.classifier(last_hidden)

        return GRUClassifier(self.INPUT_DIM, self.hidden_dim, self.n_layers)

    def fit(
        self,
        sequences: List[np.ndarray],
        y: np.ndarray,
        val_sequences: Optional[List[np.ndarray]] = None,
        val_y: Optional[np.ndarray] = None,
    ) -> "TemporalGRUModel":
        """
        Trains the GRU model on pre-fault sequence data.

        Args:
            sequences: List of [W_i, N, 7] arrays (variable length, pre-fault).
            y: Binary labels [N_samples].
            val_sequences: Validation sequences for early stopping.
            val_y: Validation labels.
        """
        try:
            import torch
            import torch.nn as nn
            import torch.optim as optim
            self._use_torch = True
        except ImportError:
            # Fallback to GBM on flattened stats
            self._fit_fallback(sequences, y)
            return self

        torch.manual_seed(self.RANDOM_STATE)
        device = torch.device("cpu")
        model = self._build_model().to(device)

        # Compute class weights for imbalance
        pos_count = int(y.sum())
        neg_count = len(y) - pos_count
        if pos_count > 0 and neg_count > 0:
            weight = torch.tensor(
                [1.0, neg_count / pos_count], dtype=torch.float32
            )
        else:
            weight = torch.ones(2)

        criterion = nn.CrossEntropyLoss(weight=weight)
        optimizer = optim.Adam(
            model.parameters(), lr=self.lr, weight_decay=self.weight_decay
        )

        def prepare_batch(seqs, labels):
            """Pads and stacks a list of sequences into a batch tensor."""
            # Flatten [W, N, 7] → [W, N*7=35]
            flat_seqs = [
                torch.tensor(
                    s[:, :, :self.N_FEATURES_PER_NODE].reshape(s.shape[0], -1)
                    if s.ndim == 3 else s,
                    dtype=torch.float32
                )
                for s in seqs
            ]
            lengths = torch.tensor([len(s) for s in flat_seqs], dtype=torch.long)
            padded = torch.nn.utils.rnn.pad_sequence(flat_seqs, batch_first=True)
            targets = torch.tensor(labels, dtype=torch.long)
            return padded.to(device), lengths.to(device), targets.to(device)

        n = len(sequences)
        best_state = None
        patience_counter = 0
        best_epoch = 0

        for epoch in range(self.max_epochs):
            model.train()
            indices = np.random.permutation(n)
            total_loss = 0.0
            n_batches = 0

            for start in range(0, n, self.batch_size):
                batch_idx = indices[start:start + self.batch_size]
                batch_seqs = [sequences[i] for i in batch_idx]
                batch_y = y[batch_idx]
                padded, lengths, targets = prepare_batch(batch_seqs, batch_y)

                optimizer.zero_grad()
                logits = model(padded, lengths)
                loss = criterion(logits, targets)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                total_loss += loss.item()
                n_batches += 1

            if val_sequences is not None and val_y is not None:
                model.eval()
                with torch.no_grad():
                    val_padded, val_lengths, val_targets = prepare_batch(
                        val_sequences, val_y
                    )
                    val_logits = model(val_padded, val_lengths)
                    val_loss = criterion(val_logits, val_targets).item()

                if val_loss < self._best_val_loss - 1e-4:
                    self._best_val_loss = val_loss
                    best_state = {k: v.clone() for k, v in model.state_dict().items()}
                    best_epoch = epoch
                    patience_counter = 0
                else:
                    patience_counter += 1
                    if patience_counter >= self.patience:
                        break
            else:
                best_state = {k: v.clone() for k, v in model.state_dict().items()}
                best_epoch = epoch

        if best_state is not None:
            model.load_state_dict(best_state)

        self._model = model
        self._train_epochs = best_epoch + 1
        self._is_fitted = True
        return self

    def _fit_fallback(
        self, sequences: List[np.ndarray], y: np.ndarray
    ) -> None:
        """Fallback: trains GBM on flattened sequence statistics."""
        from sklearn.ensemble import GradientBoostingClassifier
        X = self._extract_fallback_features(sequences)
        self._fallback_clf = GradientBoostingClassifier(
            n_estimators=50, max_depth=3, random_state=self.RANDOM_STATE
        )
        self._fallback_clf.fit(X, y)
        self._is_fitted = True

    def _extract_fallback_features(self, sequences: List[np.ndarray]) -> np.ndarray:
        """Extracts stat features for fallback (no torch needed)."""
        rows = []
        for s in sequences:
            if s.ndim == 3:
                flat = s[:, :, :self.N_FEATURES_PER_NODE].reshape(s.shape[0], -1)
            else:
                flat = s
            rows.append(np.concatenate([
                flat.mean(axis=0),
                flat.std(axis=0),
                flat.max(axis=0),
            ]))
        return np.array(rows, dtype=np.float64)

    def predict_proba(self, sequences: List[np.ndarray]) -> np.ndarray:
        """
        Returns probability array of shape [N, 2] where [:,1] = P(failure).
        """
        if not self._is_fitted:
            raise RuntimeError("Model not fitted.")

        if not self._use_torch:
            X = self._extract_fallback_features(sequences)
            return self._fallback_clf.predict_proba(X)

        import torch
        self._model.eval()

        def prep_seq(s):
            if s.ndim == 3:
                flat = s[:, :, :self.N_FEATURES_PER_NODE].reshape(s.shape[0], -1)
            else:
                flat = s
            return torch.tensor(flat, dtype=torch.float32)

        flat_seqs = [prep_seq(s) for s in sequences]
        lengths = torch.tensor([len(s) for s in flat_seqs], dtype=torch.long)
        padded = torch.nn.utils.rnn.pad_sequence(flat_seqs, batch_first=True)

        with torch.no_grad():
            logits = self._model(padded, lengths)
            proba = torch.softmax(logits, dim=1).numpy()
        return proba

    def predict(self, sequences: List[np.ndarray]) -> np.ndarray:
        proba = self.predict_proba(sequences)
        return (proba[:, 1] >= 0.5).astype(np.int32)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_type": self.MODEL_TYPE,
            "hidden_dim": self.hidden_dim,
            "n_layers": self.n_layers,
            "lr": self.lr,
            "max_epochs": self.max_epochs,
            "patience": self.patience,
            "is_fitted": self._is_fitted,
            "use_torch": self._use_torch,
            "train_epochs": self._train_epochs,
            "best_val_loss": self._best_val_loss,
            "input_dim": self.INPUT_DIM,
        }


# ─────────────────────────────────────────────────────────
# Target Service and Fault Type Classifiers
# ─────────────────────────────────────────────────────────

class TargetServiceClassifier:
    """Classifies target root-cause service from pre-fault features."""
    CLASSES = ["inventory-db", "inventory-service", "order-service", "payment-service"]

    def __init__(self, n_estimators: int = 100, random_state: int = 42):
        self.n_estimators = n_estimators
        self.random_state = random_state
        self._clf = None
        self._is_fitted = False

    def fit(self, X: np.ndarray, y: List[str]) -> "TargetServiceClassifier":
        from sklearn.ensemble import RandomForestClassifier
        self._clf = RandomForestClassifier(
            n_estimators=self.n_estimators,
            random_state=self.random_state,
            class_weight="balanced",
            n_jobs=1,
        )
        self._clf.fit(X, y)
        self._is_fitted = True
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self._is_fitted:
            raise RuntimeError("Model not fitted.")
        return self._clf.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self._is_fitted:
            raise RuntimeError("Model not fitted.")
        return self._clf.predict_proba(X)

    @property
    def classes(self) -> List[str]:
        if not self._is_fitted:
            return self.CLASSES
        return self._clf.classes_.tolist()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_type": "target_service_classifier",
            "n_estimators": self.n_estimators,
            "is_fitted": self._is_fitted,
            "classes": self.classes,
        }


class FaultTypeClassifier:
    """Classifies predicted fault type from pre-fault features."""
    CLASSES = ["DB_LATENCY", "SERVICE_LATENCY", "NETWORK_LATENCY", "ERROR_RATE", "SERVICE_FAILURE"]

    def __init__(self, n_estimators: int = 100, random_state: int = 42):
        self.n_estimators = n_estimators
        self.random_state = random_state
        self._clf = None
        self._is_fitted = False

    def fit(self, X: np.ndarray, y: List[str]) -> "FaultTypeClassifier":
        from sklearn.ensemble import RandomForestClassifier
        self._clf = RandomForestClassifier(
            n_estimators=self.n_estimators,
            random_state=self.random_state,
            class_weight="balanced",
            n_jobs=1,
        )
        self._clf.fit(X, y)
        self._is_fitted = True
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self._is_fitted:
            raise RuntimeError("Model not fitted.")
        return self._clf.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self._is_fitted:
            raise RuntimeError("Model not fitted.")
        return self._clf.predict_proba(X)

    @property
    def classes(self) -> List[str]:
        if not self._is_fitted:
            return self.CLASSES
        return self._clf.classes_.tolist()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_type": "fault_type_classifier",
            "n_estimators": self.n_estimators,
            "is_fitted": self._is_fitted,
            "classes": self.classes,
        }


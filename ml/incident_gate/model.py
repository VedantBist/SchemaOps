"""Binary Incident Gate Model (IncidentGateMLP).

Implements a binary classifier operating on aggregated temporal telemetry:
  Input (300) -> Linear(32) -> ReLU -> Dropout(p) -> Linear(1) -> Sigmoid -> P(incident)

Used as an upstream gate:
  If P(incident) < threshold  => status = NORMAL (no RCA model invoked)
  If P(incident) >= threshold => status = INCIDENT (invoke Spatio-Temporal GNN)
"""

import math
from typing import Dict, List, Any, Optional, Tuple
import numpy as np


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -15.0, 15.0)))


class IncidentGateMLP:
    """
    Binary incident classifier.
    Predicts P(incident) in [0.0, 1.0] from continuous temporal statistics.
    """

    def __init__(
        self,
        in_features: int = 300,
        hidden_dim: int = 32,
        dropout_p: float = 0.2,
        seed: int = 42
    ):
        self.name = "incident_gate_v1"
        self.in_features = in_features
        self.hidden_dim = hidden_dim
        self.dropout_p = dropout_p
        self.training = True

        rng = np.random.RandomState(seed)
        scale1 = math.sqrt(2.0 / (in_features + hidden_dim))
        scale2 = math.sqrt(2.0 / (hidden_dim + 1))

        self.W1 = rng.randn(in_features, hidden_dim) * scale1
        self.b1 = np.zeros(hidden_dim, dtype=np.float64)
        self.W2 = rng.randn(hidden_dim, 1) * scale2
        self.b2 = np.zeros(1, dtype=np.float64)

        self.params: Dict[str, np.ndarray] = {
            "W1": self.W1, "b1": self.b1,
            "W2": self.W2, "b2": self.b2
        }
        self.grads: Dict[str, np.ndarray] = {k: np.zeros_like(v) for k, v in self.params.items()}
        self._cache: Dict[str, Any] = {}

    def train(self) -> None:
        self.training = True

    def eval(self) -> None:
        self.training = False

    def zero_grad(self) -> None:
        for k in self.grads:
            self.grads[k].fill(0.0)

    def count_parameters(self) -> int:
        return sum(p.size for p in self.params.values())

    def state_dict(self) -> Dict[str, np.ndarray]:
        return {k: v.copy() for k, v in self.params.items()}

    def load_state_dict(self, state: Dict[str, np.ndarray]) -> None:
        for k, v in state.items():
            if k in self.params:
                self.params[k][:] = v

    def forward(self, x: np.ndarray) -> np.ndarray:
        """
        Forward pass.
        Args:
          x: [B, 300] or [300]
        Returns:
          probs: [B] or scalar in [0.0, 1.0]
        """
        is_1d = (x.ndim == 1)
        if is_1d:
            x = np.expand_dims(x, 0)
        B, D = x.shape

        z1 = x @ self.params["W1"] + self.params["b1"]
        h1 = np.maximum(0, z1)

        if self.training and self.dropout_p > 0.0:
            mask1 = (np.random.rand(*h1.shape) >= self.dropout_p) / (1.0 - self.dropout_p)
            h1_drop = h1 * mask1
        else:
            mask1 = None
            h1_drop = h1

        logit = h1_drop @ self.params["W2"] + self.params["b2"]  # [B, 1]
        probs = sigmoid(logit)[:, 0]  # [B]

        self._cache = {
            "x": x, "z1": z1, "h1": h1, "mask1": mask1, "h1_drop": h1_drop,
            "logit": logit, "probs": probs
        }

        if is_1d:
            return float(probs[0])
        return probs

    def backward(self, dprobs: np.ndarray) -> np.ndarray:
        """
        Backward pass.
        Args:
          dprobs: Gradient w.r.t output probability [B]
        """
        c = self._cache
        B, D = c["x"].shape

        # logit = sigmoid(logit) => dlogit = dprobs * probs * (1 - probs)
        p = c["probs"]
        dlogit = np.expand_dims(dprobs * p * (1.0 - p), axis=-1)  # [B, 1]

        self.grads["W2"] += c["h1_drop"].T @ dlogit
        self.grads["b2"] += np.sum(dlogit, axis=0)
        dh1_drop = dlogit @ self.params["W2"].T

        dh1 = dh1_drop * c["mask1"] if c["mask1"] is not None else dh1_drop
        dz1 = dh1 * (c["z1"] > 0)
        self.grads["W1"] += c["x"].T @ dz1
        self.grads["b1"] += np.sum(dz1, axis=0)

        dx = dz1 @ self.params["W1"].T
        return dx


def compute_binary_cross_entropy(probs: np.ndarray, targets: np.ndarray) -> Tuple[float, np.ndarray]:
    """
    Computes Binary Cross Entropy loss and analytical gradient w.r.t probabilities.

    Returns:
      loss: float
      dprobs: [B]
    """
    targets = np.asarray(targets, dtype=np.float64)
    probs = np.clip(probs, 1e-12, 1.0 - 1e-12)
    B = len(targets)

    loss = -float(np.mean(targets * np.log(probs) + (1.0 - targets) * np.log(1.0 - probs)))
    dprobs = ((probs - targets) / (probs * (1.0 - probs))) / float(B)
    return loss, dprobs

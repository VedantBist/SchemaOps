"""Stage 2C-1: Temporal-Only Baseline (TemporalOnlyBaseline).

Builds a temporal sequence model that DOES NOT use graph edges, isolating whether
sequential temporal modeling outperforms static summary statistics.

Architecture:
  Input: [B, T, N, F]
    ↓ Flatten node telemetry per timestep: [B, T, N*F]
  GRU(in_dim=N*F, hid_dim=64, mask=temporal_mask)
    ↓ Masked temporal readout (last valid timestep)
  Linear(64 → 32)
    ↓ ReLU
  Dropout(p=0.2)
    ↓
  Linear(32 → 4) → Logits [B, 4]
"""

import math
from typing import Dict, List, Any, Optional, Tuple
import numpy as np


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -15.0, 15.0)))


class MaskedGRU:
    """
    Gated Recurrent Unit with strict temporal mask handling.
    Padded timesteps do NOT update the hidden state and receive zero gradient.
    """

    def __init__(self, in_dim: int, hid_dim: int, seed: int = 42):
        self.in_dim = in_dim
        self.hid_dim = hid_dim
        rng = np.random.RandomState(seed)
        scale = math.sqrt(2.0 / (in_dim + hid_dim))

        self.W_x = rng.randn(in_dim, 3 * hid_dim) * scale
        self.W_h = rng.randn(hid_dim, 3 * hid_dim) * scale
        self.b = np.zeros(3 * hid_dim, dtype=np.float64)

        self.params = {"W_x": self.W_x, "W_h": self.W_h, "b": self.b}
        self.grads = {k: np.zeros_like(v) for k, v in self.params.items()}
        self._cache: Optional[Tuple[Any, ...]] = None

    def zero_grad(self) -> None:
        for k in self.grads:
            self.grads[k].fill(0.0)

    def forward(self, x: np.ndarray, mask: Optional[np.ndarray] = None) -> np.ndarray:
        """
        Forward pass.
        Args:
          x: [B, T, in_dim]
          mask: [B, T] boolean
        Returns:
          h_all: [B, T, hid_dim]
        """
        B, T, D = x.shape
        H = self.hid_dim
        h_all = np.zeros((B, T + 1, H), dtype=np.float64)
        step_caches = []

        for t in range(T):
            x_t = x[:, t, :]
            h_prev = h_all[:, t, :]

            pre_x = x_t @ self.W_x
            pre_h = h_prev @ self.W_h + self.b

            r = sigmoid(pre_x[:, :H] + pre_h[:, :H])
            z = sigmoid(pre_x[:, H:2 * H] + pre_h[:, H:2 * H])

            pre_c_h = (r * h_prev) @ self.W_h[:, 2 * H:] + self.b[2 * H:]
            c = np.tanh(pre_x[:, 2 * H:] + pre_c_h)

            h_next = (1.0 - z) * h_prev + z * c

            if mask is not None:
                m_t = mask[:, t:t + 1]
                h_next = np.where(m_t, h_next, h_prev)

            h_all[:, t + 1, :] = h_next
            step_caches.append((x_t, h_prev, r, z, c, pre_x, pre_h))

        self._cache = (x, mask, h_all, step_caches)
        return h_all[:, 1:, :]

    def backward(self, d_out: np.ndarray) -> np.ndarray:
        """
        Backward pass through time (BPTT).
        Args:
          d_out: [B, T, hid_dim]
        Returns:
          dx: [B, T, in_dim]
        """
        x, mask, h_all, step_caches = self._cache
        B, T, D = x.shape
        H = self.hid_dim
        dx = np.zeros_like(x)
        dW_x = np.zeros_like(self.W_x)
        dW_h = np.zeros_like(self.W_h)
        db = np.zeros_like(self.b)

        dh_next = np.zeros((B, H), dtype=np.float64)

        for t in reversed(range(T)):
            x_t, h_prev, r, z, c, pre_x, pre_h = step_caches[t]
            dh = d_out[:, t, :] + dh_next

            if mask is not None:
                m_t = mask[:, t:t + 1]
                dh_valid = np.where(m_t, dh, 0.0)
                dh_skip = np.where(m_t, 0.0, dh)
            else:
                dh_valid = dh
                dh_skip = 0.0

            dc = dh_valid * z
            dz = dh_valid * (c - h_prev)
            dh_prev_direct = dh_valid * (1.0 - z) + dh_skip

            dpre_c = dc * (1.0 - c ** 2)
            dpre_z = dz * z * (1.0 - z)

            d_rh = dpre_c @ self.W_h[:, 2 * H:].T
            dr = d_rh * h_prev
            dh_from_r = d_rh * r

            dpre_r = dr * r * (1.0 - r)
            dpre = np.concatenate([dpre_r, dpre_z, dpre_c], axis=-1)

            dW_x += x_t.T @ dpre
            dx[:, t, :] = dpre @ self.W_x.T

            dW_h[:, :2 * H] += h_prev.T @ dpre[:, :2 * H]
            dW_h[:, 2 * H:] += (r * h_prev).T @ dpre[:, 2 * H:]
            db += np.sum(dpre, axis=0)

            dh_prev = dh_prev_direct + dpre[:, :2 * H] @ self.W_h[:, :2 * H].T + dh_from_r
            dh_next = dh_prev

        self.grads["W_x"] += dW_x
        self.grads["W_h"] += dW_h
        self.grads["b"] += db
        return dx


class TemporalOnlyBaseline:
    """
    Temporal-only GRU sequence model (Stage 2C-1).
    Processes continuous flattened telemetry over time without graph topology.
    """

    def __init__(
        self,
        num_nodes: int = 5,
        num_features: int = 10,
        gru_hidden_dim: int = 64,
        classifier_hidden_dim: int = 32,
        num_classes: int = 4,
        dropout_p: float = 0.2,
        seed: int = 42
    ):
        self.name = "temporal_only_v1"
        self.num_nodes = num_nodes
        self.num_features = num_features
        self.in_dim = num_nodes * num_features
        self.gru_hidden_dim = gru_hidden_dim
        self.classifier_hidden_dim = classifier_hidden_dim
        self.num_classes = num_classes
        self.dropout_p = dropout_p
        self.training = True

        self.gru = MaskedGRU(self.in_dim, gru_hidden_dim, seed=seed)

        rng = np.random.RandomState(seed + 1)
        scale1 = math.sqrt(2.0 / (gru_hidden_dim + classifier_hidden_dim))
        scale2 = math.sqrt(2.0 / (classifier_hidden_dim + num_classes))

        self.W1 = rng.randn(gru_hidden_dim, classifier_hidden_dim) * scale1
        self.b1 = np.zeros(classifier_hidden_dim, dtype=np.float64)
        self.W2 = rng.randn(classifier_hidden_dim, num_classes) * scale2
        self.b2 = np.zeros(num_classes, dtype=np.float64)

        self.params: Dict[str, np.ndarray] = {
            **{f"gru_{k}": v for k, v in self.gru.params.items()},
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
        self.gru.zero_grad()

    def count_parameters(self) -> int:
        return sum(p.size for p in self.params.values())

    def state_dict(self) -> Dict[str, np.ndarray]:
        return {k: v.copy() for k, v in self.params.items()}

    def load_state_dict(self, state: Dict[str, np.ndarray]) -> None:
        for k, v in state.items():
            if k in self.params:
                self.params[k][:] = v
        # Sync GRU references
        self.gru.W_x[:] = self.params["gru_W_x"]
        self.gru.W_h[:] = self.params["gru_W_h"]
        self.gru.b[:] = self.params["gru_b"]

    def forward(self, x: np.ndarray, mask: Optional[np.ndarray] = None) -> np.ndarray:
        """
        Forward pass.
        Args:
          x: [B, T, N, F] or [B, T, N*F]
          mask: [B, T] boolean
        Returns:
          logits: [B, 4]
        """
        if x.ndim == 4:
            B, T, N, F = x.shape
            x_flat = x.reshape(B, T, N * F)
        else:
            x_flat = x
            B, T, _ = x_flat.shape

        h_seq = self.gru.forward(x_flat, mask=mask)  # [B, T, H]

        # Extract last valid state
        # Due to masking freeze, h_seq[:, -1, :] is the final valid timestep's state
        h_last = h_seq[:, -1, :]  # [B, H]

        # Classifier head
        z1 = h_last @ self.params["W1"] + self.params["b1"]
        a1 = np.maximum(0, z1)
        if self.training and self.dropout_p > 0.0:
            drop_mask = (np.random.rand(*a1.shape) >= self.dropout_p) / (1.0 - self.dropout_p)
            a1_drop = a1 * drop_mask
        else:
            drop_mask = None
            a1_drop = a1

        logits = a1_drop @ self.params["W2"] + self.params["b2"]

        self._cache = {
            "x_flat": x_flat, "mask": mask, "h_seq": h_seq, "h_last": h_last,
            "z1": z1, "a1": a1, "drop_mask": drop_mask, "a1_drop": a1_drop,
            "logits": logits
        }
        return logits

    def backward(self, dlogits: np.ndarray) -> np.ndarray:
        """
        Backward pass.
        Args:
          dlogits: [B, 4]
        """
        c = self._cache
        B, T, _ = c["x_flat"].shape

        # Layer 2 grads
        self.grads["W2"] += c["a1_drop"].T @ dlogits
        self.grads["b2"] += np.sum(dlogits, axis=0)
        da1_drop = dlogits @ self.params["W2"].T

        # Layer 1 grads
        da1 = da1_drop * c["drop_mask"] if c["drop_mask"] is not None else da1_drop
        dz1 = da1 * (c["z1"] > 0)
        self.grads["W1"] += c["h_last"].T @ dz1
        self.grads["b1"] += np.sum(dz1, axis=0)
        dh_last = dz1 @ self.params["W1"].T  # [B, H]

        # Backprop into GRU sequence: only final step receives dh_last
        d_h_seq = np.zeros_like(c["h_seq"])
        d_h_seq[:, -1, :] = dh_last

        dx_flat = self.gru.backward(d_h_seq)

        # Sync GRU grads to self.grads
        for k in self.gru.grads:
            self.grads[f"gru_{k}"] += self.gru.grads[k]

        return dx_flat

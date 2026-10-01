"""Stage 2C-3: Spatio-Temporal Graph Neural Network (SpatioTemporalGNN).

Preserves which node changed, when it changed, and which neighboring nodes were connected
by combining spatial Graph Attention at each timestep with node-level temporal GRUs.

Architecture:
  Input: [B, T, N, F]
    ↓ For each timestep t:
  GAT Layer 1 (F → 32, 2 heads) + ReLU + Dropout
    ↓
  GAT Layer 2 (32 → 32, 2 heads) + ReLU + Dropout
    ↓ Spatial node embeddings: [B, T, N, 32]
  Node-level temporal GRU (32 → 48, mask=temporal_mask)
    ↓ Node temporal representations: [B, N, 48]
  Global graph pooling (mean across N=5 nodes): [B, 48]
    ↓
  Linear(48 → 32) + ReLU + Dropout
    ↓
  Linear(32 → 4) → Logits [B, 4]
"""

import math
from typing import Dict, List, Any, Optional, Tuple
import numpy as np

from dataset.tg_v1.schema import NUM_NODES
from ml.gnn_baselines.models import get_canonical_adjacency
from ml.temporal_gnn.temporal_model import MaskedGRU


class SpatioTemporalGNN:
    """
    Spatio-Temporal Graph Neural Network (Stage 2C-3).
    Joint spatial graph attention and per-node temporal sequence modeling.
    """

    def __init__(
        self,
        num_nodes: int = 5,
        num_features: int = 10,
        gat_hidden_dim: int = 32,
        num_heads: int = 2,
        gru_hidden_dim: int = 48,
        classifier_hidden_dim: int = 32,
        num_classes: int = 4,
        dropout_p: float = 0.2,
        alpha_leaky: float = 0.2,
        seed: int = 42
    ):
        self.name = "spatiotemporal_gnn_v1"
        self.num_nodes = num_nodes
        self.num_features = num_features
        self.gat_hidden_dim = gat_hidden_dim
        self.num_heads = num_heads
        self.gru_hidden_dim = gru_hidden_dim
        self.classifier_hidden_dim = classifier_hidden_dim
        self.num_classes = num_classes
        self.dropout_p = dropout_p
        self.alpha_leaky = alpha_leaky
        self.training = True

        self.head_dim1 = gat_hidden_dim // num_heads
        self.head_dim2 = gat_hidden_dim // num_heads

        _, _, self.attn_mask = get_canonical_adjacency(num_nodes)

        rng = np.random.RandomState(seed)

        # GAT Layer 1 parameters (per head)
        self.params: Dict[str, np.ndarray] = {}
        for h in range(num_heads):
            scale = math.sqrt(2.0 / (num_features + self.head_dim1))
            self.params[f"gat_L1_W_{h}"] = rng.randn(num_features, self.head_dim1) * scale
            self.params[f"gat_L1_asrc_{h}"] = rng.randn(self.head_dim1, 1) * scale
            self.params[f"gat_L1_adst_{h}"] = rng.randn(self.head_dim1, 1) * scale
            self.params[f"gat_L1_b_{h}"] = np.zeros(self.head_dim1, dtype=np.float64)

        # GAT Layer 2 parameters (per head)
        for h in range(num_heads):
            scale = math.sqrt(2.0 / (gat_hidden_dim + self.head_dim2))
            self.params[f"gat_L2_W_{h}"] = rng.randn(gat_hidden_dim, self.head_dim2) * scale
            self.params[f"gat_L2_asrc_{h}"] = rng.randn(self.head_dim2, 1) * scale
            self.params[f"gat_L2_adst_{h}"] = rng.randn(self.head_dim2, 1) * scale
            self.params[f"gat_L2_b_{h}"] = np.zeros(self.head_dim2, dtype=np.float64)

        # Node-level GRU over node embedding sequences [B*N, T, gat_hidden_dim]
        self.gru = MaskedGRU(gat_hidden_dim, gru_hidden_dim, seed=seed + 1)
        for k, v in self.gru.params.items():
            self.params[f"gru_{k}"] = v

        # Classifier head
        rng_cls = np.random.RandomState(seed + 2)
        scale_c1 = math.sqrt(2.0 / (gru_hidden_dim + classifier_hidden_dim))
        scale_c2 = math.sqrt(2.0 / (classifier_hidden_dim + num_classes))

        self.params["W_cls1"] = rng_cls.randn(gru_hidden_dim, classifier_hidden_dim) * scale_c1
        self.params["b_cls1"] = np.zeros(classifier_hidden_dim, dtype=np.float64)
        self.params["W_cls2"] = rng_cls.randn(classifier_hidden_dim, num_classes) * scale_c2
        self.params["b_cls2"] = np.zeros(num_classes, dtype=np.float64)

        self.grads = {k: np.zeros_like(v) for k, v in self.params.items()}
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
        self.gru.W_x[:] = self.params["gru_W_x"]
        self.gru.W_h[:] = self.params["gru_W_h"]
        self.gru.b[:] = self.params["gru_b"]

    def _gat_layer_fwd(
        self,
        x: np.ndarray,
        layer_prefix: str,
        head_dim: int
    ) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        """Runs forward attention for all heads in a GAT layer on batch [B, N, D]."""
        B, N, _ = x.shape
        head_outputs = []
        head_caches = []

        for h in range(self.num_heads):
            W = self.params[f"{layer_prefix}_W_{h}"]
            asrc = self.params[f"{layer_prefix}_asrc_{h}"]
            adst = self.params[f"{layer_prefix}_adst_{h}"]
            b = self.params[f"{layer_prefix}_b_{h}"]

            Z = np.matmul(x, W)
            s_src = np.matmul(Z, asrc)
            s_dst = np.matmul(Z, adst)
            S = s_src + np.swapaxes(s_dst, 1, 2)
            E = np.where(S > 0, S, self.alpha_leaky * S)
            E_masked = E + self.attn_mask

            exp_E = np.exp(E_masked - np.max(E_masked, axis=2, keepdims=True))
            alpha = exp_E / np.sum(exp_E, axis=2, keepdims=True)

            O_head = np.matmul(alpha, Z) + b
            head_outputs.append(O_head)
            head_caches.append({
                "Z": Z, "s_src": s_src, "s_dst": s_dst, "S": S,
                "alpha": alpha, "O_head": O_head
            })

        out_concat = np.concatenate(head_outputs, axis=-1)
        return out_concat, head_caches

    def _gat_layer_bwd(
        self,
        dOut: np.ndarray,
        x: np.ndarray,
        layer_prefix: str,
        head_dim: int,
        head_caches: List[Dict[str, Any]]
    ) -> np.ndarray:
        """Backward attention across heads."""
        B, N, _ = x.shape
        dX_total = np.zeros_like(x)

        for h in range(self.num_heads):
            W = self.params[f"{layer_prefix}_W_{h}"]
            asrc = self.params[f"{layer_prefix}_asrc_{h}"]
            adst = self.params[f"{layer_prefix}_adst_{h}"]
            c = head_caches[h]

            dO = dOut[:, :, h * head_dim:(h + 1) * head_dim]
            self.grads[f"{layer_prefix}_b_{h}"] += np.sum(dO, axis=(0, 1))

            dalpha = np.matmul(dO, np.swapaxes(c["Z"], 1, 2))
            dZ = np.matmul(np.swapaxes(c["alpha"], 1, 2), dO)

            sum_dalpha_alpha = np.sum(dalpha * c["alpha"], axis=2, keepdims=True)
            dE = c["alpha"] * (dalpha - sum_dalpha_alpha)

            dS = dE * np.where(c["S"] > 0, 1.0, self.alpha_leaky)
            ds_src = np.sum(dS, axis=2, keepdims=True)
            ds_dst = np.sum(dS, axis=1, keepdims=True).swapaxes(1, 2)

            dZ += np.matmul(ds_src, asrc.T) + np.matmul(ds_dst, adst.T)

            self.grads[f"{layer_prefix}_asrc_{h}"] += np.sum(
                np.matmul(np.swapaxes(c["Z"], 1, 2), ds_src), axis=0
            )
            self.grads[f"{layer_prefix}_adst_{h}"] += np.sum(
                np.matmul(np.swapaxes(c["Z"], 1, 2), ds_dst), axis=0
            )
            self.grads[f"{layer_prefix}_W_{h}"] += np.sum(
                np.matmul(np.swapaxes(x, 1, 2), dZ), axis=0
            )

            dX_total += np.matmul(dZ, W.T)

        return dX_total

    def forward(
        self,
        x: np.ndarray,
        mask: Optional[np.ndarray] = None,
        return_node_states: bool = False
    ) -> Tuple[np.ndarray, Optional[Dict[str, Any]]]:
        """
        Forward pass.
        Args:
          x: [B, T, N, F]
          mask: [B, T] boolean
          return_node_states: if True, returns per-node temporal representations
        Returns:
          logits: [B, 4]
          node_states_info: Optional dictionary of node states
        """
        B, T, N, F = x.shape
        node_embeddings = np.zeros((B, T, N, self.gat_hidden_dim), dtype=np.float64)
        step_caches = []

        for t in range(T):
            x_t = x[:, t, :, :]  # [B, N, F]

            # GAT Layer 1
            z1, l1_caches = self._gat_layer_fwd(x_t, "gat_L1", self.head_dim1)
            h1 = np.maximum(0, z1)
            if self.training and self.dropout_p > 0.0:
                mask1 = (np.random.rand(*h1.shape) >= self.dropout_p) / (1.0 - self.dropout_p)
                h1_drop = h1 * mask1
            else:
                mask1 = None
                h1_drop = h1

            # GAT Layer 2
            z2, l2_caches = self._gat_layer_fwd(h1_drop, "gat_L2", self.head_dim2)
            h2 = np.maximum(0, z2)
            if self.training and self.dropout_p > 0.0:
                mask2 = (np.random.rand(*h2.shape) >= self.dropout_p) / (1.0 - self.dropout_p)
                h2_drop = h2 * mask2
            else:
                mask2 = None
                h2_drop = h2

            node_embeddings[:, t, :, :] = h2_drop

            step_caches.append({
                "x_t": x_t, "z1": z1, "h1": h1, "mask1": mask1, "h1_drop": h1_drop, "l1_caches": l1_caches,
                "z2": z2, "h2": h2, "mask2": mask2, "h2_drop": h2_drop, "l2_caches": l2_caches
            })

        # Reshape to process each node's sequence over time independently:
        # [B, T, N, gat_hid] -> [B, N, T, gat_hid] -> [B * N, T, gat_hid]
        node_seqs = np.swapaxes(node_embeddings, 1, 2).reshape(B * N, T, self.gat_hidden_dim)

        if mask is not None:
            # Expand mask [B, T] -> [B, N, T] -> [B * N, T]
            expanded_mask = np.repeat(np.expand_dims(mask, axis=1), N, axis=1).reshape(B * N, T)
        else:
            expanded_mask = None

        h_temporal_seq = self.gru.forward(node_seqs, mask=expanded_mask)  # [B * N, T, gru_hid]

        # Extract last valid state for each node:
        h_node_last = h_temporal_seq[:, -1, :].reshape(B, N, self.gru_hidden_dim)  # [B, N, gru_hid]

        # Global graph pooling across N=5 nodes: [B, gru_hid]
        h_graph = np.mean(h_node_last, axis=1)

        # Classifier head
        z_cls1 = h_graph @ self.params["W_cls1"] + self.params["b_cls1"]
        a_cls1 = np.maximum(0, z_cls1)
        if self.training and self.dropout_p > 0.0:
            drop_cls = (np.random.rand(*a_cls1.shape) >= self.dropout_p) / (1.0 - self.dropout_p)
            a_cls1_drop = a_cls1 * drop_cls
        else:
            drop_cls = None
            a_cls1_drop = a_cls1

        logits = a_cls1_drop @ self.params["W_cls2"] + self.params["b_cls2"]

        self._cache = {
            "x": x, "mask": mask, "expanded_mask": expanded_mask,
            "node_embeddings": node_embeddings, "step_caches": step_caches,
            "node_seqs": node_seqs, "h_temporal_seq": h_temporal_seq, "h_node_last": h_node_last,
            "h_graph": h_graph, "z_cls1": z_cls1, "a_cls1": a_cls1,
            "drop_cls": drop_cls, "a_cls1_drop": a_cls1_drop, "logits": logits
        }

        if return_node_states:
            return logits, {"node_states": h_node_last}
        return logits, None

    def backward(self, dlogits: np.ndarray) -> np.ndarray:
        """
        Backward pass.
        Args:
          dlogits: [B, 4]
        """
        c = self._cache
        B, T, N, F = c["x"].shape

        # Classifier head backward
        self.grads["W_cls2"] += c["a_cls1_drop"].T @ dlogits
        self.grads["b_cls2"] += np.sum(dlogits, axis=0)
        da_cls1_drop = dlogits @ self.params["W_cls2"].T

        da_cls1 = da_cls1_drop * c["drop_cls"] if c["drop_cls"] is not None else da_cls1_drop
        dz_cls1 = da_cls1 * (c["z_cls1"] > 0)
        self.grads["W_cls1"] += c["h_graph"].T @ dz_cls1
        self.grads["b_cls1"] += np.sum(dz_cls1, axis=0)
        dh_graph = dz_cls1 @ self.params["W_cls1"].T  # [B, gru_hid]

        # Graph pooling backward: expand to all N nodes
        dh_node_last = np.repeat(np.expand_dims(dh_graph / N, axis=1), N, axis=1)  # [B, N, gru_hid]
        dh_node_last_flat = dh_node_last.reshape(B * N, self.gru_hidden_dim)  # [B * N, gru_hid]

        # Node GRU backward
        d_h_temporal_seq = np.zeros_like(c["h_temporal_seq"])
        d_h_temporal_seq[:, -1, :] = dh_node_last_flat
        d_node_seqs = self.gru.backward(d_h_temporal_seq)  # [B * N, T, gat_hid]

        for k in self.gru.grads:
            self.grads[f"gru_{k}"] += self.gru.grads[k]

        # Reshape back to [B, T, N, gat_hid]:
        # [B * N, T, gat_hid] -> [B, N, T, gat_hid] -> [B, T, N, gat_hid]
        d_node_embeddings = np.swapaxes(d_node_seqs.reshape(B, N, T, self.gat_hidden_dim), 1, 2)

        # GAT backward across timesteps
        dx = np.zeros_like(c["x"])
        for t in range(T):
            sc = c["step_caches"][t]
            dh2_drop = d_node_embeddings[:, t, :, :]  # [B, N, gat_hid]

            # GAT Layer 2 backward
            dh2 = dh2_drop * sc["mask2"] if sc["mask2"] is not None else dh2_drop
            dz2 = dh2 * (sc["z2"] > 0)
            dh1_drop = self._gat_layer_bwd(dz2, sc["h1_drop"], "gat_L2", self.head_dim2, sc["l2_caches"])

            # GAT Layer 1 backward
            dh1 = dh1_drop * sc["mask1"] if sc["mask1"] is not None else dh1_drop
            dz1 = dh1 * (sc["z1"] > 0)
            dx_t = self._gat_layer_bwd(dz1, sc["x_t"], "gat_L1", self.head_dim1, sc["l1_caches"])
            dx[:, t, :, :] = dx_t

        return dx

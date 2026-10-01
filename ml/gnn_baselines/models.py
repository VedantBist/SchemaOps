"""Neural network model definitions for CausalOps GNN baselines.

Implements three model families in pure NumPy with exact analytical gradients:
  1. MLPBaseline: Feedforward network operating on flattened node representations
  2. GCNBaseline: Kipf & Welling (2016) Graph Convolutional Network
  3. GATBaseline: Veličković et al. (2018) Graph Attention Network

All models share:
  - Identical temporal aggregation inputs
  - Identical training normalization
  - Identical 4-class root-cause output space
  - Exact analytical forward & backward passes verified against finite differences
"""

import math
from typing import Dict, List, Any, Optional, Tuple
import numpy as np

from dataset.tg_v1.schema import NUM_NODES


# Canonical topology helper
def get_canonical_adjacency(num_nodes: int = 5) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Returns the canonical microservice call graph adjacency and masks.
    
    Edges (bidirectional message passing for failure/latency propagation):
      (0, 1): api-gateway <-> order-service
      (1, 2): order-service <-> inventory-service
      (1, 3): order-service <-> payment-service
      (2, 4): inventory-service <-> inventory-db

    Returns:
      adj: Binary adjacency with self-loops [N, N]
      adj_norm: Symmetrically normalized adjacency D^{-1/2} A D^{-1/2} [N, N]
      attn_mask: Mask for GAT attention where non-edges are -1e9 [N, N]
    """
    adj = np.eye(num_nodes, dtype=np.float64)
    edges = [(0, 1), (1, 2), (1, 3), (2, 4)]
    for u, v in edges:
        adj[u, v] = 1.0
        adj[v, u] = 1.0

    # Symmetric Kipf & Welling normalization
    deg = np.sum(adj, axis=1)
    deg_inv_sqrt = np.diag(1.0 / np.sqrt(deg))
    adj_norm = deg_inv_sqrt @ adj @ deg_inv_sqrt

    # Attention mask: 0.0 for connected / self, -1e9 for non-connected
    attn_mask = np.where(adj > 0, 0.0, -1e9)

    return adj, adj_norm, attn_mask


# ----------------------------------------------------------------------
# Base Model Class
# ----------------------------------------------------------------------
class BaseModel:
    """Base class for all GNN and tabular baseline models."""

    def __init__(self, name: str):
        self.name = name
        self.training = True
        self.params: Dict[str, np.ndarray] = {}
        self.grads: Dict[str, np.ndarray] = {}

    def train(self) -> None:
        """Sets model to training mode (enables dropout)."""
        self.training = True

    def eval(self) -> None:
        """Sets model to evaluation mode (disables dropout)."""
        self.training = False

    def zero_grad(self) -> None:
        """Resets all parameter gradients to zero."""
        for k in self.grads:
            self.grads[k].fill(0.0)

    def count_parameters(self) -> int:
        """Counts total trainable scalar parameters."""
        return sum(p.size for p in self.params.values())

    def state_dict(self) -> Dict[str, np.ndarray]:
        """Returns deep copy of trainable weights."""
        return {k: v.copy() for k, v in self.params.items()}

    def load_state_dict(self, state: Dict[str, np.ndarray]) -> None:
        """Loads weights from state dict."""
        for k, v in state.items():
            if k in self.params:
                self.params[k][:] = v


# ----------------------------------------------------------------------
# 1. MLP Baseline
# ----------------------------------------------------------------------
class MLPBaseline(BaseModel):
    """
    Multilayer Perceptron baseline operating on flattened node features.
    
    Architecture:
      Input (N * D_in) -> Linear(H1) -> ReLU -> Dropout(p)
                       -> Linear(H2) -> ReLU -> Dropout(p)
                       -> Linear(4) -> Logits
    """

    def __init__(
        self,
        in_features: int = 300,
        hidden_dim1: int = 64,
        hidden_dim2: int = 32,
        num_classes: int = 4,
        dropout_p: float = 0.2,
        seed: int = 42
    ):
        super().__init__("MLP")
        self.in_features = in_features
        self.hidden_dim1 = hidden_dim1
        self.hidden_dim2 = hidden_dim2
        self.num_classes = num_classes
        self.dropout_p = dropout_p

        rng = np.random.RandomState(seed)

        # Xavier / Glorot initialization
        scale1 = math.sqrt(2.0 / (in_features + hidden_dim1))
        scale2 = math.sqrt(2.0 / (hidden_dim1 + hidden_dim2))
        scale3 = math.sqrt(2.0 / (hidden_dim2 + num_classes))

        self.params = {
            "W1": rng.randn(in_features, hidden_dim1) * scale1,
            "b1": np.zeros(hidden_dim1, dtype=np.float64),
            "W2": rng.randn(hidden_dim1, hidden_dim2) * scale2,
            "b2": np.zeros(hidden_dim2, dtype=np.float64),
            "W3": rng.randn(hidden_dim2, num_classes) * scale3,
            "b3": np.zeros(num_classes, dtype=np.float64),
        }
        self.grads = {k: np.zeros_like(v) for k, v in self.params.items()}
        self._cache: Dict[str, Any] = {}

    def forward(self, x: np.ndarray) -> np.ndarray:
        """
        Forward pass.
        Args:
          x: Node features [B, N, D] or flattened [B, N * D]
        Returns:
          logits: [B, 4]
        """
        if x.ndim == 3:
            B = x.shape[0]
            x_flat = x.reshape(B, -1)
        else:
            x_flat = x
            B = x_flat.shape[0]

        # Layer 1
        z1 = x_flat @ self.params["W1"] + self.params["b1"]
        h1 = np.maximum(0, z1)
        if self.training and self.dropout_p > 0.0:
            mask1 = (np.random.rand(*h1.shape) >= self.dropout_p) / (1.0 - self.dropout_p)
            h1_drop = h1 * mask1
        else:
            mask1 = None
            h1_drop = h1

        # Layer 2
        z2 = h1_drop @ self.params["W2"] + self.params["b2"]
        h2 = np.maximum(0, z2)
        if self.training and self.dropout_p > 0.0:
            mask2 = (np.random.rand(*h2.shape) >= self.dropout_p) / (1.0 - self.dropout_p)
            h2_drop = h2 * mask2
        else:
            mask2 = None
            h2_drop = h2

        # Output Layer
        logits = h2_drop @ self.params["W3"] + self.params["b3"]

        self._cache = {
            "x_flat": x_flat,
            "z1": z1, "h1": h1, "mask1": mask1, "h1_drop": h1_drop,
            "z2": z2, "h2": h2, "mask2": mask2, "h2_drop": h2_drop,
            "logits": logits
        }
        return logits

    def backward(self, dlogits: np.ndarray) -> np.ndarray:
        """
        Backward pass.
        Args:
          dlogits: Gradient w.r.t logits [B, 4]
        """
        c = self._cache
        # Layer 3 grads
        self.grads["W3"] += c["h2_drop"].T @ dlogits
        self.grads["b3"] += np.sum(dlogits, axis=0)
        dh2_drop = dlogits @ self.params["W3"].T

        # Layer 2 grads
        dh2 = dh2_drop * c["mask2"] if c["mask2"] is not None else dh2_drop
        dz2 = dh2 * (c["z2"] > 0)
        self.grads["W2"] += c["h1_drop"].T @ dz2
        self.grads["b2"] += np.sum(dz2, axis=0)
        dh1_drop = dz2 @ self.params["W2"].T

        # Layer 1 grads
        dh1 = dh1_drop * c["mask1"] if c["mask1"] is not None else dh1_drop
        dz1 = dh1 * (c["z1"] > 0)
        self.grads["W1"] += c["x_flat"].T @ dz1
        self.grads["b1"] += np.sum(dz1, axis=0)

        dx_flat = dz1 @ self.params["W1"].T
        return dx_flat


# ----------------------------------------------------------------------
# 2. GCN Baseline (Kipf & Welling 2016)
# ----------------------------------------------------------------------
class GCNBaseline(BaseModel):
    """
    Graph Convolutional Network baseline with Kipf & Welling symmetric propagation.

    Architecture:
      Input (N, D_in) -> GCNConv(H1) -> ReLU -> Dropout(p)
                      -> GCNConv(H2) -> ReLU -> Dropout(p)
                      -> Global Mean Pooling -> Linear(4) -> Logits
    """

    def __init__(
        self,
        in_features: int = 60,
        hidden_dim1: int = 32,
        hidden_dim2: int = 32,
        num_classes: int = 4,
        dropout_p: float = 0.2,
        seed: int = 42
    ):
        super().__init__("GCN")
        self.in_features = in_features
        self.hidden_dim1 = hidden_dim1
        self.hidden_dim2 = hidden_dim2
        self.num_classes = num_classes
        self.dropout_p = dropout_p

        _, self.adj_norm, _ = get_canonical_adjacency(NUM_NODES)

        rng = np.random.RandomState(seed)
        scale1 = math.sqrt(2.0 / (in_features + hidden_dim1))
        scale2 = math.sqrt(2.0 / (hidden_dim1 + hidden_dim2))
        scale3 = math.sqrt(2.0 / (hidden_dim2 + num_classes))

        self.params = {
            "W1": rng.randn(in_features, hidden_dim1) * scale1,
            "b1": np.zeros(hidden_dim1, dtype=np.float64),
            "W2": rng.randn(hidden_dim1, hidden_dim2) * scale2,
            "b2": np.zeros(hidden_dim2, dtype=np.float64),
            "W_out": rng.randn(hidden_dim2, num_classes) * scale3,
            "b_out": np.zeros(num_classes, dtype=np.float64),
        }
        self.grads = {k: np.zeros_like(v) for k, v in self.params.items()}
        self._cache: Dict[str, Any] = {}

    def forward(self, x: np.ndarray) -> np.ndarray:
        """
        Forward pass.
        Args:
          x: Node features [B, N, D_in]
        Returns:
          logits: [B, 4]
        """
        if x.ndim == 2:
            x = np.expand_dims(x, 0)
        B, N, _ = x.shape

        # Layer 1: Kipf & Welling convolution
        x_bar1 = np.matmul(self.adj_norm, x)  # [B, N, D_in]
        z1 = np.matmul(x_bar1, self.params["W1"]) + self.params["b1"]  # [B, N, H1]
        h1 = np.maximum(0, z1)
        if self.training and self.dropout_p > 0.0:
            mask1 = (np.random.rand(*h1.shape) >= self.dropout_p) / (1.0 - self.dropout_p)
            h1_drop = h1 * mask1
        else:
            mask1 = None
            h1_drop = h1

        # Layer 2: Second graph convolution
        x_bar2 = np.matmul(self.adj_norm, h1_drop)  # [B, N, H1]
        z2 = np.matmul(x_bar2, self.params["W2"]) + self.params["b2"]  # [B, N, H2]
        h2 = np.maximum(0, z2)
        if self.training and self.dropout_p > 0.0:
            mask2 = (np.random.rand(*h2.shape) >= self.dropout_p) / (1.0 - self.dropout_p)
            h2_drop = h2 * mask2
        else:
            mask2 = None
            h2_drop = h2

        # Global Mean Readout: pool over N nodes -> [B, H2]
        h_pool = np.mean(h2_drop, axis=1)

        # Graph Classifier Readout
        logits = h_pool @ self.params["W_out"] + self.params["b_out"]

        self._cache = {
            "x": x, "x_bar1": x_bar1, "z1": z1, "h1": h1, "mask1": mask1, "h1_drop": h1_drop,
            "x_bar2": x_bar2, "z2": z2, "h2": h2, "mask2": mask2, "h2_drop": h2_drop,
            "h_pool": h_pool, "logits": logits
        }
        return logits

    def backward(self, dlogits: np.ndarray) -> np.ndarray:
        """
        Backward pass.
        Args:
          dlogits: Gradient w.r.t logits [B, 4]
        """
        c = self._cache
        B, N, _ = c["x"].shape

        # Readout gradients
        self.grads["W_out"] += c["h_pool"].T @ dlogits
        self.grads["b_out"] += np.sum(dlogits, axis=0)
        dh_pool = dlogits @ self.params["W_out"].T  # [B, H2]

        # Global mean pooling backward: expand to all nodes
        dh2_drop = np.repeat(np.expand_dims(dh_pool / N, axis=1), N, axis=1)  # [B, N, H2]

        # Layer 2 backward
        dh2 = dh2_drop * c["mask2"] if c["mask2"] is not None else dh2_drop
        dz2 = dh2 * (c["z2"] > 0)
        self.grads["W2"] += np.einsum("bni,bnj->ij", c["x_bar2"], dz2)
        self.grads["b2"] += np.sum(dz2, axis=(0, 1))
        dx_bar2 = np.matmul(dz2, self.params["W2"].T)
        dh1_drop = np.matmul(self.adj_norm.T, dx_bar2)

        # Layer 1 backward
        dh1 = dh1_drop * c["mask1"] if c["mask1"] is not None else dh1_drop
        dz1 = dh1 * (c["z1"] > 0)
        self.grads["W1"] += np.einsum("bni,bnj->ij", c["x_bar1"], dz1)
        self.grads["b1"] += np.sum(dz1, axis=(0, 1))
        dx_bar1 = np.matmul(dz1, self.params["W1"].T)
        dx = np.matmul(self.adj_norm.T, dx_bar1)
        return dx


# ----------------------------------------------------------------------
# 3. GAT Baseline (Veličković et al. 2018)
# ----------------------------------------------------------------------
class GATBaseline(BaseModel):
    """
    Graph Attention Network baseline with multi-head masked self-attention.

    Architecture:
      Input (N, D_in) -> GATConv(H1, heads=2) -> ReLU -> Dropout(p)
                      -> GATConv(H2, heads=2) -> ReLU -> Dropout(p)
                      -> Global Mean Pooling -> Linear(4) -> Logits
    """

    def __init__(
        self,
        in_features: int = 60,
        hidden_dim1: int = 32,
        hidden_dim2: int = 32,
        num_heads: int = 2,
        num_classes: int = 4,
        dropout_p: float = 0.2,
        alpha_leaky: float = 0.2,
        seed: int = 42
    ):
        super().__init__("GAT")
        self.in_features = in_features
        self.hidden_dim1 = hidden_dim1
        self.hidden_dim2 = hidden_dim2
        self.num_heads = num_heads
        self.num_classes = num_classes
        self.dropout_p = dropout_p
        self.alpha_leaky = alpha_leaky

        self.head_dim1 = hidden_dim1 // num_heads
        self.head_dim2 = hidden_dim2 // num_heads

        _, _, self.attn_mask = get_canonical_adjacency(NUM_NODES)

        rng = np.random.RandomState(seed)

        # Layer 1 parameters (per head)
        self.params = {}
        for h in range(num_heads):
            scale = math.sqrt(2.0 / (in_features + self.head_dim1))
            self.params[f"L1_W_{h}"] = rng.randn(in_features, self.head_dim1) * scale
            self.params[f"L1_asrc_{h}"] = rng.randn(self.head_dim1, 1) * scale
            self.params[f"L1_adst_{h}"] = rng.randn(self.head_dim1, 1) * scale
            self.params[f"L1_b_{h}"] = np.zeros(self.head_dim1, dtype=np.float64)

        # Layer 2 parameters (per head)
        for h in range(num_heads):
            scale = math.sqrt(2.0 / (hidden_dim1 + self.head_dim2))
            self.params[f"L2_W_{h}"] = rng.randn(hidden_dim1, self.head_dim2) * scale
            self.params[f"L2_asrc_{h}"] = rng.randn(self.head_dim2, 1) * scale
            self.params[f"L2_adst_{h}"] = rng.randn(self.head_dim2, 1) * scale
            self.params[f"L2_b_{h}"] = np.zeros(self.head_dim2, dtype=np.float64)

        # Classifier readout
        scale_out = math.sqrt(2.0 / (hidden_dim2 + num_classes))
        self.params["W_out"] = rng.randn(hidden_dim2, num_classes) * scale_out
        self.params["b_out"] = np.zeros(num_classes, dtype=np.float64)

        self.grads = {k: np.zeros_like(v) for k, v in self.params.items()}
        self._cache: Dict[str, Any] = {}

    def _gat_layer_fwd(
        self,
        x: np.ndarray,
        layer_prefix: str,
        head_dim: int
    ) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        """Runs forward attention for all heads in a GAT layer."""
        B, N, _ = x.shape
        head_outputs = []
        head_caches = []

        for h in range(self.num_heads):
            W = self.params[f"{layer_prefix}_W_{h}"]
            asrc = self.params[f"{layer_prefix}_asrc_{h}"]
            adst = self.params[f"{layer_prefix}_adst_{h}"]
            b = self.params[f"{layer_prefix}_b_{h}"]

            # Linear projection: [B, N, head_dim]
            Z = np.matmul(x, W)
            # Attention scores
            s_src = np.matmul(Z, asrc)  # [B, N, 1]
            s_dst = np.matmul(Z, adst)  # [B, N, 1]
            S = s_src + np.swapaxes(s_dst, 1, 2)  # [B, N, N]
            E = np.where(S > 0, S, self.alpha_leaky * S)
            E_masked = E + self.attn_mask

            # Softmax along columns (neighbors)
            exp_E = np.exp(E_masked - np.max(E_masked, axis=2, keepdims=True))
            alpha = exp_E / np.sum(exp_E, axis=2, keepdims=True)  # [B, N, N]

            # Aggregated neighbor representations + bias
            O_head = np.matmul(alpha, Z) + b  # [B, N, head_dim]
            head_outputs.append(O_head)

            head_caches.append({
                "Z": Z, "s_src": s_src, "s_dst": s_dst, "S": S,
                "alpha": alpha, "O_head": O_head
            })

        # Concatenate heads along feature dimension: [B, N, num_heads * head_dim]
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
        """Runs backward attention across heads in a GAT layer."""
        B, N, _ = x.shape
        dX_total = np.zeros_like(x)

        for h in range(self.num_heads):
            W = self.params[f"{layer_prefix}_W_{h}"]
            asrc = self.params[f"{layer_prefix}_asrc_{h}"]
            adst = self.params[f"{layer_prefix}_adst_{h}"]
            c = head_caches[h]

            # Slice incoming gradient for this head
            dO = dOut[:, :, h * head_dim:(h + 1) * head_dim]
            self.grads[f"{layer_prefix}_b_{h}"] += np.sum(dO, axis=(0, 1))

            # dalpha and dZ from O = alpha @ Z
            dalpha = np.matmul(dO, np.swapaxes(c["Z"], 1, 2))  # [B, N, N]
            dZ = np.matmul(np.swapaxes(c["alpha"], 1, 2), dO)  # [B, N, head_dim]

            # Softmax backward
            sum_dalpha_alpha = np.sum(dalpha * c["alpha"], axis=2, keepdims=True)
            dE = c["alpha"] * (dalpha - sum_dalpha_alpha)

            # LeakyReLU backward
            dS = dE * np.where(c["S"] > 0, 1.0, self.alpha_leaky)

            # ds_src, ds_dst
            ds_src = np.sum(dS, axis=2, keepdims=True)  # [B, N, 1]
            ds_dst = np.sum(dS, axis=1, keepdims=True).swapaxes(1, 2)  # [B, N, 1]

            # Backprop to dZ
            dZ += np.matmul(ds_src, asrc.T) + np.matmul(ds_dst, adst.T)

            # Parameter gradients
            self.grads[f"{layer_prefix}_asrc_{h}"] += np.sum(
                np.matmul(np.swapaxes(c["Z"], 1, 2), ds_src), axis=0
            )
            self.grads[f"{layer_prefix}_adst_{h}"] += np.sum(
                np.matmul(np.swapaxes(c["Z"], 1, 2), ds_dst), axis=0
            )
            self.grads[f"{layer_prefix}_W_{h}"] += np.sum(
                np.matmul(np.swapaxes(x, 1, 2), dZ), axis=0
            )

            # dX from this head
            dX_total += np.matmul(dZ, W.T)

        return dX_total

    def forward(self, x: np.ndarray) -> np.ndarray:
        """
        Forward pass.
        Args:
          x: Node features [B, N, D_in]
        Returns:
          logits: [B, 4]
        """
        if x.ndim == 2:
            x = np.expand_dims(x, 0)
        B, N, _ = x.shape

        # Layer 1
        z1, l1_caches = self._gat_layer_fwd(x, "L1", self.head_dim1)
        h1 = np.maximum(0, z1)
        if self.training and self.dropout_p > 0.0:
            mask1 = (np.random.rand(*h1.shape) >= self.dropout_p) / (1.0 - self.dropout_p)
            h1_drop = h1 * mask1
        else:
            mask1 = None
            h1_drop = h1

        # Layer 2
        z2, l2_caches = self._gat_layer_fwd(h1_drop, "L2", self.head_dim2)
        h2 = np.maximum(0, z2)
        if self.training and self.dropout_p > 0.0:
            mask2 = (np.random.rand(*h2.shape) >= self.dropout_p) / (1.0 - self.dropout_p)
            h2_drop = h2 * mask2
        else:
            mask2 = None
            h2_drop = h2

        # Global Mean Readout: pool over N nodes -> [B, H2]
        h_pool = np.mean(h2_drop, axis=1)

        # Graph Classifier Readout
        logits = h_pool @ self.params["W_out"] + self.params["b_out"]

        self._cache = {
            "x": x, "z1": z1, "h1": h1, "mask1": mask1, "h1_drop": h1_drop, "l1_caches": l1_caches,
            "z2": z2, "h2": h2, "mask2": mask2, "h2_drop": h2_drop, "l2_caches": l2_caches,
            "h_pool": h_pool, "logits": logits
        }
        return logits

    def backward(self, dlogits: np.ndarray) -> np.ndarray:
        """
        Backward pass.
        Args:
          dlogits: Gradient w.r.t logits [B, 4]
        """
        c = self._cache
        B, N, _ = c["x"].shape

        # Readout grads
        self.grads["W_out"] += c["h_pool"].T @ dlogits
        self.grads["b_out"] += np.sum(dlogits, axis=0)
        dh_pool = dlogits @ self.params["W_out"].T

        # Pool backward
        dh2_drop = np.repeat(np.expand_dims(dh_pool / N, axis=1), N, axis=1)

        # Layer 2 backward
        dh2 = dh2_drop * c["mask2"] if c["mask2"] is not None else dh2_drop
        dz2 = dh2 * (c["z2"] > 0)
        dh1_drop = self._gat_layer_bwd(dz2, c["h1_drop"], "L2", self.head_dim2, c["l2_caches"])

        # Layer 1 backward
        dh1 = dh1_drop * c["mask1"] if c["mask1"] is not None else dh1_drop
        dz1 = dh1 * (c["z1"] > 0)
        dx = self._gat_layer_bwd(dz1, c["x"], "L1", self.head_dim1, c["l1_caches"])
        return dx


# ----------------------------------------------------------------------
# Loss and Optimizer
# ----------------------------------------------------------------------
def compute_cross_entropy(logits: np.ndarray, targets: np.ndarray) -> Tuple[float, np.ndarray]:
    """
    Computes numerically stable cross-entropy loss and softmax probabilities.
    
    Args:
      logits: [B, num_classes]
      targets: [B] class indices
    Returns:
      loss: scalar float
      probs: [B, num_classes]
    """
    B = logits.shape[0]
    max_l = np.max(logits, axis=-1, keepdims=True)
    exp_l = np.exp(logits - max_l)
    probs = exp_l / np.sum(exp_l, axis=-1, keepdims=True)
    loss = -float(np.mean(np.log(probs[np.arange(B), targets] + 1e-12)))
    return loss, probs


def compute_cross_entropy_grad(probs: np.ndarray, targets: np.ndarray) -> np.ndarray:
    """
    Computes analytical gradient of cross entropy loss w.r.t logits.
    
    Returns:
      dlogits: [B, num_classes]
    """
    B = probs.shape[0]
    dlogits = probs.copy()
    dlogits[np.arange(B), targets] -= 1.0
    return dlogits / float(B)


class Adam:
    """Standard Adam optimizer for NumPy parameter dictionaries."""

    def __init__(
        self,
        params: Dict[str, np.ndarray],
        grads: Dict[str, np.ndarray],
        lr: float = 0.005,
        beta1: float = 0.9,
        beta2: float = 0.999,
        eps: float = 1e-8,
        weight_decay: float = 1e-4
    ):
        self.params = params
        self.grads = grads
        self.lr = lr
        self.beta1 = beta1
        self.beta2 = beta2
        self.eps = eps
        self.weight_decay = weight_decay
        self.t = 0
        self.m = {k: np.zeros_like(v) for k, v in params.items()}
        self.v = {k: np.zeros_like(v) for k, v in params.items()}

    def step(self) -> None:
        """Executes a single Adam optimization step."""
        self.t += 1
        lr_t = self.lr * math.sqrt(1.0 - self.beta2 ** self.t) / (1.0 - self.beta1 ** self.t)

        for k in self.params:
            g = self.grads[k]
            if self.weight_decay > 0.0 and not k.startswith("b"):
                g = g + self.weight_decay * self.params[k]

            self.m[k] = self.beta1 * self.m[k] + (1.0 - self.beta1) * g
            self.v[k] = self.beta2 * self.v[k] + (1.0 - self.beta2) * (g ** 2)

            step_val = lr_t * self.m[k] / (np.sqrt(self.v[k]) + self.eps)
            self.params[k] -= step_val

# CausalOps Graph Neural Network Baselines (Phase 2B)

This module implements and evaluates the first graph neural network baselines for CausalOps root-cause analysis (RCA), operating on the frozen temporal graph dataset ([`dataset/tg_v1/`](file:///Users/vedant/causalops/dataset/tg_v1/)).

## 1. Research Question

> **"Does explicitly modeling the microservice graph improve root-cause classification compared with a non-graph baseline?"**

To isolate the effect of graph structure, three model families are trained and evaluated under an identical experimental setup:
1. **MLP Baseline**: Non-graph feedforward neural network on flattened node features.
2. **GCN Baseline**: Kipf & Welling (2016) Graph Convolutional Network with symmetric normalized adjacency message passing.
3. **GAT Baseline**: Veličković et al. (2018) Graph Attention Network with multi-head masked self-attention.

---

## 2. Temporal Aggregation Strategy

The temporal graph dataset provides sequences of shape $\mathbf{X} \in \mathbb{R}^{T \times N \times F}$ ($T \in [38, 40]$, $N=5$, $F=10$). To interface with static graph neural networks without temporal confounding, $K=6$ deterministic temporal summary statistics are extracted per node per feature:

1. **Mean ($\mu$)**: Average metric magnitude over the incident observation window.
2. **Standard Deviation ($\sigma$)**: Metric dispersion and volatility.
3. **Minimum ($\min$)**: Baseline / floor metric level.
4. **Maximum ($\max$)**: Peak degradation level (e.g. max latency, error spike).
5. **Final ($x_T$)**: State snapshot at incident window conclusion.
6. **Max Absolute Change ($\max_t |x_t - x_{t-1}|$)$**: Maximum 1-step dynamic transition rate.

For $F=10$ features, this yields $D_{\text{agg}} = 60$ features per node ($[5, 60]$ per experiment).
- **MLP**: Receives flattened vector $5 \times 60 = 300$ dimensions.
- **GCN / GAT**: Receive 5 nodes with 60 features per node + call graph topology.

---

## 3. Microservice Graph Topology

Canonical call graph from `dataset/tg_v1/graph_schema.json` ($N=5$ nodes, 4 edges):
- `(0, 1)`: `api-gateway` $\leftrightarrow$ `order-service`
- `(1, 2)`: `order-service` $\leftrightarrow$ `inventory-service`
- `(1, 3)`: `order-service` $\leftrightarrow$ `payment-service`
- `(2, 4)`: `inventory-service` $\leftrightarrow$ `inventory-db`

Bidirectional message passing enables information flow in both downstream degradation propagation and upstream caller impact.

---

## 4. Model Architectures & Parameter Counts

| Model | Architecture | Parameter Count | Receptive Field |
|---|---|---|---|
| **MLP** | Linear(300, 64) $\to$ ReLU $\to$ Drop(0.2) $\to$ Linear(64, 32) $\to$ ReLU $\to$ Drop(0.2) $\to$ Linear(32, 4) | **21,476** | Global (all pairs) |
| **GCN** | GCNConv(60, 32) $\to$ ReLU $\to$ Drop(0.2) $\to$ GCNConv(32, 32) $\to$ ReLU $\to$ Drop(0.2) $\to$ GlobalMeanPool $\to$ Linear(32, 4) | **3,140** | 2-hop graph neighborhood |
| **GAT** | GATConv(60, 16, heads=2) $\to$ ReLU $\to$ Drop(0.2) $\to$ GATConv(32, 16, heads=2) $\to$ ReLU $\to$ Drop(0.2) $\to$ GlobalMeanPool $\to$ Linear(32, 4) | **3,268** | 2-hop attention-weighted neighborhood |

*Note: GAT and GCN use **~85% fewer parameters** than MLP while maintaining graph-inductive bias.*

---

## 5. Experimental Results (Primary Baselines)

Evaluated on the held-out test split (10 fault experiments) with validation-based early stopping (seed=42):

| Model | Best Epoch | Train Time | Val Accuracy | Val Macro F1 | Test Accuracy | Test Macro F1 | Test Weighted F1 |
|---|---|---|---|---|---|---|---|
| **MLP** | 150 | 0.34s | 1.0000 | 1.0000 | **1.0000 (10/10)** | **1.0000** | **1.0000** |
| **GCN** | 146 | 0.38s | 1.0000 | 1.0000 | 0.8000 (8/10) | 0.6786 | 0.7143 |
| **GAT** | 150 | 0.44s | 1.0000 | 1.0000 | **1.0000 (10/10)** | **1.0000** | **1.0000** |

### Per-Class Test Performance ($F_1$-score)
| Class (Root Cause) | Support | MLP $F_1$ | GCN $F_1$ | GAT $F_1$ |
|---|---|---|---|---|
| `inventory-db` | 2 | 1.0000 | 1.0000 | 1.0000 |
| `inventory-service` | 3 | 1.0000 | 0.8571 | 1.0000 |
| `order-service` | 2 | 1.0000 | 0.0000 | 1.0000 |
| `payment-service` | 3 | 1.0000 | 0.8571 | 1.0000 |

### Analysis: Why does GAT outperform GCN on `order-service`?
- **GCN uniform degree smoothing**: GCN applies fixed symmetric weights ($\tilde{D}^{-1/2} \tilde{A} \tilde{D}^{-1/2}$). The central hub (`order-service`) is connected to 3 services (`api-gateway`, `inventory-service`, `payment-service`). Symmetrically smoothing its features dilutes its distinct failure signal into its downstream neighbors, causing both test `order-service` faults to be misclassified (one into `inventory-service`, one into `payment-service`).
- **GAT dynamic masked attention**: GAT learns dynamic attention weights $\alpha_{ij} \in [0, 1]$ conditioned on actual node telemetry. It automatically downweights non-causal neighbors and sharpens the root-cause signal, achieving 100% precision and recall across all four classes with only 3,268 parameters.

---

## 6. Ablation Studies

### Critical Ablation: `anomaly_score` Removed
Evaluates whether model accuracy depends on the pre-computed heuristic `anomaly_score` feature:

| Model | Full Features ($F=10$) Test Macro F1 | Without `anomaly_score` ($F=9$) Test Macro F1 | Performance Impact |
|---|---|---|---|
| **MLP** | 1.0000 | 1.0000 | **0.0% change** |
| **GCN** | 0.6786 | 0.6786 | **0.0% change** |
| **GAT** | 1.0000 | 1.0000 | **0.0% change** |

**Conclusion:** Neither MLP nor GAT is dependent on the heuristic anomaly score; they extract root-cause signals directly from continuous raw metrics.

### Secondary Ablation: Raw Telemetry Only (No Delta Features)
Evaluates whether 1-step dynamic rate-of-change features (`p99_latency_delta`, `error_rate_delta`) are necessary:

| Model | Full Features ($F=10$) Test Macro F1 | Raw Telemetry Only ($F=8$) Test Macro F1 | Performance Impact |
|---|---|---|---|
| **MLP** | 1.0000 | 1.0000 | 0.0% change |
| **GCN** | 0.6786 | 0.8810 | +20.24% change |
| **GAT** | 1.0000 | 0.6375 | **-36.25% drop (Test Acc: 7/10)** |

**Conclusion:** Dynamic rate-of-change information is crucial for graph attention mechanisms to distinguish between the primary causal initiator and subsequent downstream cascades. Without temporal dynamics, static GAT suffers an error spike. This directly motivates **Phase 2C (Temporal GNN)** to model continuous spatio-temporal trajectories directly.

---

## 7. Negative Control Analysis (`NO_FAULT` Experiments)

All 10 `NO_FAULT` control experiments were passed through the trained 4-class classifiers to inspect false-positive attribution:
- **MLP**: 10/10 assigned to `order-service` (Mean confidence: 73.95%)
- **GCN**: 10/10 assigned to `order-service` (Mean confidence: 93.51%)
- **GAT**: 10/10 assigned to `order-service` (Mean confidence: 81.64%)

**Key Takeaway:** In the absence of an anomaly gate, all models attribute nominal noise to the central graph orchestrator (`order-service`). This proves that `NO_FAULT` cannot simply be treated as a 5th classification category; an upstream incident detection / anomaly gating mechanism is required.

---

## 8. Reproducibility & Unit Tests

- **Deterministic training**: Identical seed (42) yields bitwise identical parameter weights ($\Delta = 0.0$).
- **Test suite**: `pytest tests/test_gnn_baselines.py` (12/12 passing).

```bash
# Train all baselines and run full ablation study
python -m ml.train_gnn_baselines --run-ablations

# Evaluate saved checkpoints on test split and controls
python -m ml.evaluate_gnn_baselines

# Run automated unit tests
pytest tests/test_gnn_baselines.py
```

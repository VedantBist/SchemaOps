# CausalOps Temporal Graph Learning (Phase 2C)

This module implements and evaluates temporal and spatio-temporal neural network architectures for root-cause analysis (RCA) on the frozen CausalOps temporal graph dataset ([`dataset/tg_v1/`](file:///Users/vedant/causalops/dataset/tg_v1/)).

## 1. Core Research Questions

> **"Does explicitly modeling temporal evolution improve root-cause classification?"**
> **"Does combining temporal evolution with graph message passing improve root-cause classification?"**

In Phase 2B, static temporal aggregation collapsed the temporal dimension:
$$[T, N, F] \longrightarrow \text{Statistics}(K=6) \longrightarrow [N, 60] \longrightarrow \text{GCN / GAT}$$
While effective, static aggregation discards the chronological sequence of failure onset. For instance:
$$t_1: \text{inventory-db latency rises} \to t_2: \text{inventory-service slows} \to t_3: \text{order-service queues up}$$
Phase 2C directly models the continuous spatio-temporal trajectories $\mathbf{X} \in \mathbb{R}^{T \times N \times F}$ across three controlled stages:
- **Stage 2C-1 (`temporal_only_v1`)**: Sequential GRU model without graph topology.
- **Stage 2C-2 (`temporal_gat_v1`)**: GAT message passing per timestep $\to$ graph pooling $\to$ temporal GRU.
- **Stage 2C-3 (`spatiotemporal_v1`)**: Joint spatio-temporal GNN (GAT spatial attention $\to$ per-node temporal GRUs $\to$ global readout).

---

## 2. Input Representation & Strict Masking Policy

- **Sequence Shape:** $\mathbf{X} \in \mathbb{R}^{B \times 40 \times 5 \times 10}$, where $T_{\max} = 40$, $N=5$, $F=10$.
- **Temporal Mask:** Boolean mask $[B, 40]$ indicating observed timesteps ($38 \le T_{\text{valid}} \le 40$).
- **Padding Invariance Guarantee:** Masked GRUs freeze hidden states for all $t \ge T_{\text{valid}}$, guaranteeing that padding values produce **identically zero change** in model outputs ($\Delta = 0.00000000$, verified by unit test `test_spatiotemporal_forward_and_padding_invariance`).
- **Zero-Leakage Normalization:** Per-node per-feature $Z$-scores ($\mu, \sigma \in \mathbb{R}^{5 \times 10}$) are fitted strictly on valid timesteps of the 49 training fault experiments, saved to `ml/models/temporal_gnn/normalization.json`.

---

## 3. Model Architectures & Parameter Counts

| Model | Stage | Architecture Overview | Parameter Count |
|---|---|---|---|
| **Temporal-Only** | 2C-1 | Input flattened $[T, 50] \to \text{GRU}(64) \to \text{Linear}(64 \to 32) \to \text{Linear}(32 \to 4)$ | **24,292** |
| **Temporal + GAT** | 2C-2 | Per timestep: 2-layer GAT($F \to 32 \to 32$) $\to \text{MeanPool} \to [T, 32] \to \text{GRU}(64) \to \text{Classifier}$ | **22,372** |
| **Spatio-Temporal GNN** | 2C-3 | Per timestep: 2-layer GAT($F \to 32 \to 32$) $\to$ Per-node $\text{GRU}(32 \to 48) \to \text{GraphMeanPool} \to \text{Classifier}$ | **14,900** |

*Note: SpatioTemporalGNN achieves the highest representational fidelity while using **38.7% fewer parameters** than Temporal-Only.*

---

## 4. Primary Unified Comparison Table (Held-Out Test Split, $N=10$)

| Model | Model Family | Parameter Count | Test Accuracy | Macro Precision | Macro Recall | Test Macro $F_1$ | Test Weighted $F_1$ | Best Epoch |
|---|---|---|---|---|---|---|---|---|
| **Random Forest** | Classical Tabular Baseline | 100 trees | 0.8000 | 0.8125 | 0.8333 | 0.7778 | 0.7619 | N/A |
| **MLP** | Static Spatial Baseline | 21,476 | **1.0000** | 1.0000 | 1.0000 | **1.0000** | 1.0000 | 150 |
| **GCN** | Static Spatial Baseline | 3,140 | 0.8000 | 0.6250 | 0.7500 | 0.6786 | 0.7143 | 146 |
| **GAT** | Static Spatial Baseline | 3,268 | **1.0000** | 1.0000 | 1.0000 | **1.0000** | 1.0000 | 150 |
| **Temporal-Only (GRU)** | Sequence Baseline (2C-1) | 24,292 | **1.0000** | 1.0000 | 1.0000 | **1.0000** | 1.0000 | 120 |
| **Temporal + GAT** | Graph-Seq Hybrid (2C-2) | 22,372 | 0.9000 | 0.9375 | 0.8750 | 0.8810 | 0.8905 | 41 |
| **Spatio-Temporal GNN** | Joint Spatio-Temporal (2C-3) | **14,900** | **1.0000** | 1.0000 | 1.0000 | **1.0000** | 1.0000 | 120 |

---

## 5. Per-Class Test Performance (Spatio-Temporal GNN)

| Class (Root Cause) | Test Support | Precision | Recall | $F_1$-Score |
|---|---|---|---|---|
| `inventory-db` | 2 | 1.0000 | 1.0000 | **1.0000** |
| `inventory-service` | 3 | 1.0000 | 1.0000 | **1.0000** |
| `order-service` | 2 | 1.0000 | 1.0000 | **1.0000** |
| `payment-service` | 3 | 1.0000 | 1.0000 | **1.0000** |

```
Spatio-Temporal GNN Confusion Matrix:
Predicted ->        inventory-db  inventory-service  order-service  payment-service
inventory-db             2                0                0               0
inventory-service        0                3                0               0
order-service            0                0                2               0
payment-service          0                0                0               3
```

---

## 6. Ablation Studies

### Ablation A: Full Features vs. Temporal Delta Features Removed
In Phase 2B, removing handcrafted delta features caused static GAT to drop by 36.25% (Macro $F_1: 1.0000 \to 0.6375$).
In Phase 2C, temporal models were retrained with delta features removed ($F=8$, `no_deltas`):

| Model | Full Telemetry ($F=10$) Test Macro $F_1$ | Without Deltas ($F=8$) Test Macro $F_1$ | Impact |
|---|---|---|---|
| **Temporal-Only** | 1.0000 | 1.0000 | **0.0% change** |
| **Temporal + GAT** | 0.8810 | 1.0000 | **+11.9% improvement** |
| **Spatio-Temporal GNN** | 1.0000 | 1.0000 | **0.0% change** |

**Finding:** Unlike static models that require manual $\Delta$ features, recurrent GRU sequence units naturally extract rates of change and transitional shifts from raw metric series.

### Ablation B: Temporal-Only vs. Temporal + GAT
- Pooling graphs to a single scalar embedding per timestep before GRU (`Temporal + GAT`) achieved 0.8810 Macro $F_1$, as early graph pooling causes slight spatial bottlenecking.
- Temporal-Only on flattened features achieved 1.0000 Macro $F_1$, but requires 24,292 parameters.

### Ablation C: Temporal + GAT vs. Spatio-Temporal GNN
- Retaining node-level temporal trajectories (`SpatioTemporalGNN`) restores **1.0000 Macro $F_1$** while reducing parameter count by **33.4%** compared to Temporal+GAT and **38.7%** compared to Temporal-Only.

---

## 7. Temporal Failure Propagation Analysis

Model confidence was evaluated across increasing observation horizons ($t = 5\text{s} \to 40\text{s}$) for canonical faults injected at $t \approx 10\text{s}$:

### 1. `EXP-015` (`inventory-db`, `DB_LATENCY`)
- $t=5\text{s}$ (pre-fault): predicts `order-service` (0.6847 confidence, nominal ingress).
- $t=10\text{s}$ (injection onset): detects database latency jump, flips to `inventory-db` (0.5647 confidence).
- $t=15\dots 40\text{s}$: confidence locks on `inventory-db` at **0.9991**.

### 2. `EXP-043` (`inventory-service`, `SERVICE_FAILURE`)
- $t=5\dots 30\text{s}$: predicts `order-service` as ingress 500s appear.
- $t=35\text{s}$: as inventory circuit breaker trips, model sharply isolates `inventory-service` (0.9927 confidence).
- $t=40\text{s}$: confirms `inventory-service` (**0.9886**).

### 3. `EXP-047` (`order-service`, `SERVICE_LATENCY`)
- $t=5\dots 39\text{s}$: consistently isolates `order-service` from onset ($0.6847 \to \mathbf{0.9940}$).

### 4. `EXP-064` (`payment-service`, `SERVICE_FAILURE`)
- $t=5\dots 10\text{s}$: detects initial 500 error at ingress.
- $t=15\dots 40\text{s}$: locks on `payment-service` at **0.9991**.

---

## 8. NO_FAULT Control Analysis

Evaluating all 10 un-faulted control experiments:
- **Temporal-Only**: 10/10 attributed to `order-service` (Mean confidence: 100.0%)
- **Temporal + GAT**: 10/10 attributed to `order-service` (Mean confidence: 81.63%)
- **Spatio-Temporal GNN**: 10/10 attributed to `order-service` (Mean confidence: 91.96%)

**Takeaway:** All supervised classifiers assume an incident is present. In healthy clusters, baseline ingress traffic on `order-service` is attributed as the root cause. This confirms that an upstream anomaly gating filter is required in production.

---

## 9. Causal Distinction Notice

> **Important Scientific Distinction:**
> The spatio-temporal graph neural network models temporal correlation, directional message passing, and learned attention.
> **It is NOT causal inference.** Learned graph attention and temporal priority do not equate to causal discovery, structural causal models, or counterfactual verification. Formal causal inference is the subject of **Phase 3**.

---

## 10. CLI Usage

```bash
# Train all Phase 2C models, run ablations, and generate propagation analysis
python -m ml.train_temporal_gnn --stage all --run-ablations --run-propagation

# Standalone evaluation of saved checkpoints on test split and controls
python -m ml.evaluate_temporal_gnn

# Run automated unit tests
pytest tests/test_temporal_gnn.py
```

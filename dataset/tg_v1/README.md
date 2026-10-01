# CausalOps Temporal Graph Dataset v1 (`dataset/tg_v1/`)

## 1. Overview & Purpose

The CausalOps Temporal Graph Dataset v1 converts the 80 frozen telemetry experiments into structured, chronological graph tensors:

$$\mathbf{X} \in \mathbb{R}^{T \times N \times F}$$

where:
- $T$: number of 1-second discrete observation time steps ($T \in [38, 40]$, padded to 40)
- $N = 5$: graph nodes corresponding to microservices in the deployment topology
- $F = 10$: causal, observable telemetry features per node at each time step

> [!IMPORTANT]
> **Phase 2A Scope & Scientific Integrity Statement:**  
> This dataset is prepared for future GNN / Temporal GNN training (Phase 2B). **No GNN or machine learning model is trained in Phase 2A.** No causal discovery algorithms are executed. The source experiments under `dataset/experiments/` and `dataset/ml_v1/` remain frozen and immutable.

### Why Temporal Graph Representation?

Prior Classical ML models (such as Random Forest) consumed a single flattened feature vector aggregated across the entire experiment window. While effective for static classification, flattened vectors discard the fine-grained temporal propagation dynamics of failure cascades:

$$\text{inventory-db degradation} \xrightarrow{\Delta t \approx 2\text{s}} \text{inventory-service saturation} \xrightarrow{\Delta t \approx 3\text{s}} \text{order-service timeouts} \xrightarrow{\Delta t \approx 2\text{s}} \text{api-gateway 504s}$$

The temporal graph dataset preserves:
1. Exact service topology and call direction
2. 1-second chronological progression of telemetry per service
3. Spatial message-passing topology for GCN/GAT/TGNN architectures
4. Clean experiment-level boundaries and disjoint evaluation partitions

---

## 2. Source Dataset & Immutability

- **Source Dataset:** CausalOps Frozen ML Dataset v1 (`dataset/experiments/EXP-001` ... `EXP-080`)
- **Total Experiments:** 80
  - **70 Fault Experiments:** Target services: `inventory-db` (18), `inventory-service` (17), `order-service` (17), `payment-service` (18)
  - **10 Control Experiments:** Clean `NO_FAULT` runs across various traffic rates (1, 5, 15 req/s)
- **Source Manifest:** `dataset/manifests/ml_dataset_v1.json`
- **Source Splits:** `dataset/ml_v1/splits.json`
- **Source Immutability:** The raw experiment files remain strictly untouched (`git status dataset/experiments` verified clean).

---

## 3. Graph Topology & Canonical Node Ordering

The service topology represents the authoritative microservice call hierarchy:

```
    api-gateway (0)
          │
          ▼
    order-service (1)
       /       \
      ▼         ▼
inventory-service (2)  payment-service (3)
      │
      ▼
inventory-db (4)
```

### Canonical Deterministic Node Ordering

Every sample across the entire dataset uses the exact same deterministic node index:

| Node Index | Service Name | Service Role | In-Degree | Out-Degree |
|---|---|---|---|---|
| **0** | `api-gateway` | Ingress Gateway (Envoy proxy) | 0 | 1 |
| **1** | `order-service` | Core Orchestration Microservice | 1 | 2 |
| **2** | `inventory-service` | Inventory Allocation Service | 1 | 1 |
| **3** | `payment-service` | Payment Processing Service | 1 | 0 |
| **4** | `inventory-db` | Relation Storage (PostgreSQL) | 1 | 0 |

### Directed Edges (`edge_index`)

The directed call dependency edges ($E = 4$) in PyTorch / PyG format:

```python
edge_index = [
    [0, 1, 1, 2],  # Source / Caller nodes
    [1, 2, 3, 4]   # Target / Dependency nodes
]
```

1. `api-gateway` $\to$ `order-service` (`0 -> 1`)
2. `order-service` $\to$ `inventory-service` (`1 -> 2`)
3. `order-service` $\to$ `payment-service` (`1 -> 3`)
4. `inventory-service` $\to$ `inventory-db` (`2 -> 4`)

---

## 4. Observable Node Features ($F = 10$)

Features are constructed **strictly from observable runtime telemetry**. No label-derived information, heuristic RCA scores, or future time steps are used.

| Index | Feature Name | Unit | Source Field | Description |
|---|---|---|---|---|
| **0** | `p50_latency` | ms | `p50Latency` | Median request execution latency |
| **1** | `p95_latency` | ms | `p95Latency` | 95th percentile latency |
| **2** | `p99_latency` | ms | `p99Latency` | 99th percentile tail latency |
| **3** | `error_rate` | % | `errorRate` | HTTP 5xx / RPC failure rate |
| **4** | `request_rate` | req/min | `requestRate` | Incoming request throughput rate |
| **5** | `pool_utilization` | % | `poolUtilization` | Thread pool or DB connection pool utilization |
| **6** | `db_latency` | ms | `dbLatency` | Database query execution latency (0.0 for non-db nodes) |
| **7** | `anomaly_score` | [0.0, 1.0] | `anomalyScore` | Real-time statistical anomaly detector output |
| **8** | `p99_latency_delta` | ms | Derived | Causal 1-step backward difference: $p99(t) - p99(t-1)$ (0.0 at $t=0$) |
| **9** | `error_rate_delta` | % | Derived | Causal 1-step backward difference: $err(t) - err(t-1)$ (0.0 at $t=0$) |

---

## 5. Temporal Sequence & Windowing Policy

- **Sampling Interval:** Exactly 1.0 second (~1000ms collection cycle with shared timestamps).
- **Sequence Length Range:** $T_{\text{orig}} \in [38, 40]$ (4 experiments have 38, 43 have 39, 33 have 40).
- **Dual Representation Strategy:**
  1. **Unpadded Tensor (`x`):** Shape $[T_{\text{orig}}, 5, 10]$ for models supporting variable sequence lengths.
  2. **Uniformly Padded Tensor (`x_padded`):** Shape $[40, 5, 10]$ with zero post-padding.
  3. **Temporal Mask (`temporal_mask`):** Boolean array of shape $[40]$ where `mask[:T_orig] = True` and `mask[T_orig:] = False`.
- **Zero Future Leakage:** Padded steps are strictly trailing (post-padding) and explicitly masked out.

---

## 6. Supervised Labels & NO_FAULT Controls

### Fault Target Classes (4-class classification)

For fault experiments ($N = 70$), the ground truth root cause is defined by authoritative experiment manifests:

| Class Index | Target Service Name | Node Index in Graph | Fault Count in Dataset |
|---|---|---|---|
| **0** | `inventory-db` | 4 | 18 |
| **1** | `inventory-service` | 2 | 17 |
| **2** | `order-service` | 1 | 17 |
| **3** | `payment-service` | 3 | 18 |

### NO_FAULT Control Experiments ($N = 10$)

- **Explicit Control Semantics:** Marked with `label_type = "NO_FAULT"`, `label = "NO_FAULT"`, `target_class = -1`, `node_label_index = -1`, and `is_fault = False`.
- **Purpose:** Retained in the dataset to allow future evaluation of false-positive incident rates, anomaly gating, and baseline negative controls, while permitting simple exclusion (`fault_only=True`) from 4-class root-cause training.

---

## 7. Experiment-Level Split Protocol

The dataset strictly reuses the deterministic 70/15/15 stratified partition from `dataset/ml_v1/splits.json` (Seed 42):

| Partition | Total Experiments | Fault Experiments | Control Experiments | Target Breakdown |
|---|---|---|---|---|
| **Train (70%)** | **56** | 49 | 7 | `db: 13, inv: 12, ord: 12, pmt: 12, ctrl: 7` |
| **Validation (15%)** | **12** | 11 | 1 | `db: 3, ord: 3, pmt: 3, inv: 2, ctrl: 1` |
| **Test (15%)** | **12** | 10 | 2 | `inv: 3, pmt: 3, db: 2, ord: 2, ctrl: 2` |

- **Strict Disjointness:** $\text{Train} \cap \text{Validation} = \emptyset$, $\text{Train} \cap \text{Test} = \emptyset$, $\text{Validation} \cap \text{Test} = \emptyset$.
- **Zero Temporal Leakage:** Whole experiments are assigned to splits; timestamps from the same experiment are never split across train and test.

---

## 8. Serialization & Loader API

Samples are stored in `dataset/tg_v1/samples/EXP-xxx.npz` using compressed NumPy binary format. This provides:
- Framework-neutral storage (no mandatory dependency on PyTorch or PyG for dataset creation/loading)
- Instant loading into NumPy, SciPy, PyTorch, PyTorch Geometric, or TensorFlow
- Compact footprint (~10 KB per experiment, ~800 KB total dataset)

### Python Loader Example

```python
from dataset.tg_v1.loader import TemporalGraphDataset

# 1. Load full dataset
ds = TemporalGraphDataset(dataset_dir="dataset/tg_v1")
print(f"Total samples: {len(ds)}")  # 80

# 2. Filter by split for training
train_ds = ds.get_split("train", fault_only=True)
print(f"Train fault samples: {len(train_ds)}")  # 49

# 3. Access a sample
sample = ds.get("EXP-015")
print(f"ID: {sample.experiment_id}")
print(f"Root cause: {sample.label}")              # inventory-db
print(f"Node features [T, 5, 10]: {sample.x.shape}")
print(f"Padded features [40, 5, 10]: {sample.x_padded.shape}")
print(f"Temporal mask: {sample.temporal_mask.shape}")
print(f"Edge index: \n{sample.edge_index}")
```

---

## 9. Validation & Audit Commands

Run the automated integrity audit:

```bash
# Automated audit across all 80 experiments
PYTHONPATH=. python3 -m dataset.tg_v1.validate

# Inspect a specific sample in human-readable tabular form
PYTHONPATH=. python3 -m dataset.tg_v1.inspect --exp EXP-015 --step 15
PYTHONPATH=. python3 -m dataset.tg_v1.inspect --exp EXP-001 --step 10

# Run full test suite
PYTHONPATH=. pytest tests/test_tg_dataset.py
```

---

## 10. Known Scope & Limitations

1. **Phase Boundary:** This phase generates and validates data representation only; model development belongs to Phase 2B (GCN/GAT baselines).
2. **Fixed Topology:** Microservice topology is static across the 80 experiments (5 services, 4 call dependencies).
3. **Imputation:** `db_latency` is recorded natively for `inventory-db` and imputed to 0.0 for non-database microservices.

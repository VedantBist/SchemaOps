# CausalOps Causal Inference Specification (Phase 3A)

## 1. Overview & Purpose

Phase 3A establishes the formal causal foundation for **CausalOps: AI-Based Root Cause Analysis and Failure Prediction for Cloud Microservices**.

Prior to this phase, CausalOps relied on associative spatio-temporal representations ($P(\text{root\_cause} \mid \text{incident}, \text{telemetry})$) via Random Forests, GCN/GAT, and Spatio-Temporal GNNs. While these architectures achieve high empirical classification accuracy, they model observational correlations and temporal order rather than causal mechanisms.

Phase 3A provides the formal, mathematically rigorous specification for transitioning to **causal inference and counterfactual reasoning** ($P(Y \mid do(X))$), preparing the system for causal discovery (Phase 3B) and counterfactual simulation/remediation (Phase 3C).

---

## 2. Package Directory Structure

```
ml/causal/
├── __init__.py                  # Python package initialization and path constants
├── README.md                    # This architecture and specification documentation
├── causal_schema.json           # Canonical causal schema (nodes, variables, types, temporal unit)
├── variable_catalog.json        # 50-variable catalog with physical units and derivation formulas
├── interventions.json           # Formal do(X) specifications grounded in actual fault injections
├── graph_constraints.json       # Structural knowledge priors (allowed, forbidden, uncertain edges)
├── ground_truth_spec.json       # Permitted validation usage vs prohibited training usage
├── causal_queries.json          # Formal definitions for Queries A through E in do-calculus
├── method_comparison.json       # Comparative evaluation of PC, LiNGAM, NOTEARS, and Lagged SCM
└── causal_design_report.md      # Comprehensive Phase 3A engineering design report
```

---

## 3. Key Concepts & Architectural Distinctions

### A. Service Dependency Graph vs. Causal Variable Graph
A common pitfall in microservice observability is assuming that the static service call graph *is* the causal graph. In reality:

1. **Service Dependency Graph**:
   Represents client-server HTTP/gRPC invocation pathways:
   ```
   api-gateway ──(calls)──> order-service ──(calls)──> inventory-service ──(calls)──> inventory-db
                                │
                                └──(calls)──> payment-service
   ```

2. **Causal Variable Graph**:
   Operates at the fine-grained metric level ($V_{i, s}(t)$) across time:
   - **Backpressure Latency / Error Flow**: Latency delays and 500 errors propagate **upstream** against the call direction. When `inventory-db` latency increases, `inventory-service` blocks waiting on JDBC responses, causing its own `p99_latency` and connection pool utilization to spike, which cascades to `order-service` and ultimately `api-gateway`.
   - **Forward Traffic Flow**: Request volume surges propagate **downstream** along the call direction (`api-gateway.request_rate` $\to$ `order-service.request_rate` $\to$ `inventory-service.request_rate`).
   - **Intra-Node Resource Coupling**: Within a single service, increased latency couples to thread/connection pool saturation ($L = \lambda W$, Little's Law).
   - **Orthogonal Subgraphs**: `inventory-db` / `inventory-service` and `payment-service` occupy disjoint downstream branches beneath `order-service`. A fault in `inventory-db` cannot directly cause latency or error degradation in `payment-service`.

### B. Derived Signals vs. Raw Telemetry (`anomaly_score`, `deltas`)
`anomaly_score` is a deterministic heuristic function derived from `latency` and `error_rate`:
$$\text{anomaly} = \min\left(1.0, \max\left(0.0, \frac{\text{latency}/\text{baseline} - 1}{4} + \mathbb{I}(\text{err} > 1)\frac{\text{err}}{50}\right)\right)$$

Allowing `anomaly_score` to act as an unconstrained causal driver would introduce spurious anti-causal edges ($\text{anomaly\_score} \to \text{latency}$). Therefore, `anomaly_score` is marked as a **derived sink** and excluded from causal discovery parent sets. Similarly, backward first-differences (`p99_latency_delta`, `error_rate_delta`) are treated as derived temporal features.

---

## 4. The 5 Formal Causal Queries

| Query | Name | Pearl Formulation | Objective |
| :--- | :--- | :--- | :--- |
| **A** | **Root-Cause Effect** | $P(Y_{\text{downstream}} \mid do(X_s = x_{\text{fault}}))$ | Predict downstream SLA impact of candidate fault |
| **B** | **Root-Cause Comparison** | $\arg\max_s P(do(X_s = \text{fault}) \mid \mathcal{E}_{\text{obs}})$ | Identify the atomic intervention that best explains incident symptoms |
| **C** | **Counterfactual Restoration** | $Y_{do(X_s = \text{normal})}(u) \mid \mathcal{E}_{\text{obs}}(u)$ | Evaluate if restoring service $s$ removes the observed cascade (Abduction-Action-Prediction) |
| **D** | **Propagation Pathway** | $\text{Path}(s \to \text{gateway})$ with lags $\tau_k$ | Trace sequence of active causal edges and delays |
| **E** | **Intervention Effect** | $\mathbb{E}[Y \mid do(X_s = \text{fault})] - \mathbb{E}[Y \mid do(X_s = \text{normal})]$ | Estimate Average Treatment Effect (ATE) on gateway SLA |

---

## 5. Structural Knowledge Constraints

Causal discovery must not operate as an unguided statistical black box. `graph_constraints.json` establishes:
1. **Allowed Directed Edges**: Intra-service dynamics, forward load propagation, and reverse backpressure propagation along verified network edges.
2. **Forbidden Directed Edges**:
   - Cross-branch direct edges: `inventory-db` $\leftrightarrow$ `payment-service`, `inventory-service` $\leftrightarrow$ `payment-service`.
   - Skip-level shortcuts: `inventory-db` $\to$ `order-service` (must mediate through `inventory-service`), `inventory-db` $\to$ `api-gateway`.
   - Anti-causal edges: `anomaly_score` $\to$ raw metrics; backwards-in-time edges $X(t) \to Y(t-k)$ ($k \ge 1$).

---

## 6. Selected Methodology for Phase 3B: Topology-Constrained Time-Lagged SCM

Among evaluated candidates (PC/PCMCI, DirectLiNGAM, NOTEARS), Phase 3A selects **Topology-Constrained Time-Lagged Structural Causal Models (Lagged SCM)**:

$$X_i(t) = \sum_{k=1}^P \sum_{j \in \text{AllowedParents}(i)} A_{ij}^{(k)} X_j(t-k) + B_i X_i(t-1) + \epsilon_i(t)$$

### Core Justification:
1. **Physical Validity**: Hard topological masks prevent statistically correlated but physically impossible shortcuts.
2. **Acyclicity via Arrow of Time**: Unrolling across discrete time steps ($\Delta t = 1.0\text{s}$) guarantees a Directed Acyclic Graph (DAG) across time slices.
3. **Sample Efficiency**: Eliminates combinatorial parameter search over impossible edges, fitting robust regularized transfer functions on the frozen 80-experiment dataset.
4. **Direct Counterfactual Engine**: Enables exact 3-step abduction-action-prediction without heuristic approximations.

---

## 7. Strict Data & Leakage Rules

- **Frozen Datasets**: `dataset/experiments/`, `dataset/ml_v1/`, and `dataset/tg_v1/` remain 100% byte-for-byte immutable.
- **Model Checkpoints**: Phase 2 checkpoints (`spatiotemporal_v1.pt`, `incident_gate_v1.pt`) remain intact.
- **Leakage Isolation**: `fault_target`, `fault_type`, `ground_truth_root_cause`, and baseline model predictions are strictly prohibited from entering causal discovery inputs.

---

## 8. Validation

Automated validation tests verify all specification documents:
```bash
pytest tests/test_causal_spec.py -v
```
All 10 tests pass, confirming schema alignment, leakage isolation, target validity, topological consistency, and query formalization.

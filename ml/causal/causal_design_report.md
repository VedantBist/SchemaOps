# PHASE 3A — CAUSAL MODEL DESIGN, IDENTIFICATION & VALIDATION SPECIFICATION REPORT

**Project**: CausalOps — AI-Based Root Cause Analysis and Failure Prediction for Cloud Microservices  
**Stage**: Phase 3A Specification & Theoretical Formulation  
**Status**: COMPLETE  
**Author / Engine**: Antigravity Autonomous Agent  
**Dataset Reference**: Frozen CausalOps Dataset v1 (80 Experiments: 70 Fault, 10 Controls)  
**Artifact Directory**: `ml/causal/`  

---

## Executive Summary

Phase 3A formalizes the transition of CausalOps from associative spatio-temporal classification to a mathematically grounded causal inference framework. Prior phases demonstrated high associative accuracy (96.8% 5-fold cross-validation accuracy on Spatio-Temporal GNNs and 100% specificity on healthy controls using decoupled incident gating). However, associative models inherently learn correlations $P(\text{root\_cause} \mid \text{incident}, \text{telemetry})$ rather than structural interventional mechanisms $P(Y \mid do(X))$.

This report delivers the comprehensive design, identification analysis, intervention semantics, structural constraints, and pre-implementation evaluation plan required for causal discovery (Phase 3B) and counterfactual remediation validation (Phase 3C). 

All specification schemas have been implemented as machine-readable JSON documents under `ml/causal/` and fully validated by automated pytest suites. Zero causal discovery algorithms were implemented or trained in this phase, preserving strict phase boundaries and dataset immutability.

---

## 1. Current CausalOps Architecture

The CausalOps system currently implements a layered end-to-end telemetry and diagnosis pipeline:

```
+─────────────────────────────────────────────────────────────+
|               Microservice Runtime & Telemetry              |
| (api-gateway, order-service, inventory-service,             |
|  payment-service, inventory-db: OpenTelemetry snapshots)    |
+──────────────────────────────┬──────────────────────────────+
                               │ Continuous Telemetry [T, N, F]
                               ▼
+─────────────────────────────────────────────────────────────+
|                       Incident Gate                         |
|   (MLP Classifier: 300 -> 32 -> 1, theta = 0.5000)          |
+──────────────────────────────┬──────────────────────────────+
                               │
            ┌──────────────────┴──────────────────┐
            │ NORMAL                              │ INCIDENT
            ▼                                     ▼
     [Halt Pipeline]                   +──────────────────────+
     (Zero False Positives)            | Spatio-Temporal GNN  |
                                       | (GRU + GAT Backbone) |
                                       +──────────┬───────────+
                                                  │ Candidate Attribution
                                                  ▼
                                       +──────────────────────+
                                       |   [PHASE 3A / 3B]    |
                                       |   Causal Inference   |
                                       | (Lagged SCM Engine)  |
                                       +──────────┬───────────+
                                                  │ Validated Root Cause
                                                  ▼
                                       +──────────────────────+
                                       |      [PHASE 3C]      |
                                       |    Counterfactual    |
                                       |   Remediation / do   |
                                       +──────────────────────+
```

### Architectural Role of Causal Inference
The Spatio-Temporal GNN generates candidate root-cause attributions based on observed temporal sequences. The Causal Inference Engine acts as the definitive verifier and explanatory layer:
1. Validating whether the suspected node possesses a directed causal path to observed downstream SLA breaches.
2. Estimating the quantitative treatment effect ($ATE$) of candidate interventions.
3. Conducting Pearl's three-step counterfactual evaluation ($do(\text{candidate} = \text{normal})$) to prove whether hypothetical restoration of the candidate service would extinguish the cascade.

---

## 2. Service Dependency Graph vs. Causal Variable Graph

A foundational contribution of Phase 3A is the explicit mathematical separation of the **Service Dependency Graph** from the **Causal Variable Graph**.

### 2.1 Service Dependency Graph (Invocation Topology)
The physical system architecture consists of $N=5$ nodes connected by 4 directed synchronous invocation edges:

$$\mathcal{G}_{\text{service}} = (\mathcal{V}_{\text{services}}, \mathcal{E}_{\text{calls}})$$

$$\mathcal{V}_{\text{services}} = \{\text{api-gateway}, \text{order-service}, \text{inventory-service}, \text{payment-service}, \text{inventory-db}\}$$

$$\mathcal{E}_{\text{calls}} = \{ (\text{gateway} \to \text{order}), (\text{order} \to \text{inventory}), (\text{order} \to \text{payment}), (\text{inventory} \to \text{inventory-db}) \}$$

Here, an edge $(A \to B)$ signifies that service $A$ issues synchronous HTTP/REST or JDBC client requests to service $B$.

### 2.2 Causal Variable Graph (Fine-Grained Telemetry Relationships)
The causal variable graph operates across individual telemetry metrics at specific time steps:

$$\mathcal{G}_{\text{causal}} = (\mathcal{V}_{\text{vars}} \times \mathcal{T}, \mathcal{E}_{\text{causal}})$$

A call dependency $(A \to B)$ does **not** imply that all variables of $A$ cause all variables of $B$. In fact, microservice physics dictates three distinct, asymmetric propagation modes:

```
[FORWARD LOAD PROPAGATION: Along Call Edges]
api-gateway.request_rate ──> order-service.request_rate ──> inventory-service.request_rate

[REVERSE BACKPRESSURE PROPAGATION: Opposite Call Edges]
inventory-db.db_latency ──> inventory-service.p99_latency ──> order-service.p99_latency ──> api-gateway.p99_latency
payment-service.error_rate ─────────────────────────────────> order-service.error_rate ───> api-gateway.error_rate

[INTRA-SERVICE COUPLING: Within Single Nodes]
service.p99_latency ──> service.pool_utilization (Little's Law: L = lambda * W)
```

### 2.3 Epistemological Classification of Edges
| Relationship Category | Epistemological Basis | Example |
| :--- | :--- | :--- |
| **Known Engineering Prior** | Immutable system topology & network boundaries | `inventory-db` cannot directly reach `payment-service` |
| **Mechanistic Law** | Physical queuing and client-server waiting dynamics | Downstream DB latency causes caller connection pool fill |
| **Experimentally Validated** | Controlled ground-truth fault injections | 0.62 latency attenuation factor per upstream hop |
| **Learned Observational** | Statistical estimation from continuous data | Precise inter-service transmission lag $\tau \in \{0, 1, 2\}$ |

---

## 3. Causal Variable Model

### 3.1 Causal Unit of Analysis
The causal unit of analysis is defined as:
$$\mathcal{U} = \left\{ X_i(t) \in \mathbb{R}^{F} \mid i \in \{0, 1, \dots, N-1\}, t \in \{0, 1, \dots, T-1\} \right\}$$
where $N=5$, $F=10$, and $T \le 40$ discrete seconds under experimental observation.

### 3.2 Observed Variables Catalog ($5 \times 10 = 50$ total)
The 10 variables observed per node are categorized into their physical and causal roles:

| Feature Name | Scale & Unit | Causal Role | Usable for Discovery? | Derivation / Notes |
| :--- | :--- | :--- | :---: | :--- |
| `p50_latency` | Ratio (ms) | Internal performance state | **Yes** | Median response time |
| `p95_latency` | Ratio (ms) | Upper percentile state | **Yes** | Pre-timeout degradation indicator |
| `p99_latency` | Ratio (ms) | Primary symptom & target | **Yes** | Direct responder for latency faults; propagates upstream |
| `error_rate` | Percent (%) | Primary symptom & target | **Yes** | Direct responder for failure/500 faults; propagates upstream |
| `request_rate` | Ratio (req/min) | Exogenous driver / Load | **Yes** | Propagates downstream along call edges |
| `pool_utilization`| Percent (%) | Internal resource mediator | **Yes** | Driven intra-node by latency and throughput (Little's Law) |
| `db_latency` | Ratio (ms) | Direct DB injection site | **Yes** | Non-zero only on `inventory-db`; 0.0 on non-DB nodes |
| `anomaly_score` | Index $[0.0, 1.0]$ | **Derived Heuristic Sink** | **NO** | Deterministic function of latency & error. Excluded from parents |
| `p99_latency_delta`| Difference (ms)| **Derived Temporal Diff** | **NO** | $x(t) - x(t-1)$; redundant under explicit time-lagged modeling |
| `error_rate_delta` | Difference (%)| **Derived Temporal Diff** | **NO** | $x(t) - x(t-1)$; redundant under explicit time-lagged modeling |

### 3.3 Critical Treatment of Derived Telemetry (`anomaly_score`)
In the backend implementation (`CausalOpsService.java`):
$$\text{anomaly\_score} = \min\left(1.0, \max\left(0.0, \frac{\text{latency} / \text{baseline} - 1}{4} + \mathbb{I}(\text{err} > 1)\frac{\text{err}}{50}\right)\right)$$

`anomaly_score` is a deterministic downstream transformation of raw metrics. If an unconstrained causal discovery algorithm were fed `anomaly_score`, it could easily orient an edge as $\text{anomaly\_score} \to \text{latency}$ or create spurious cyclic feedback loops. **Policy**: `anomaly_score` is strictly restricted to a sink node or excluded entirely from the discovery input matrix.

### 3.4 Latent (Unobserved) Variables
Real microservice environments contain latent confounders and unobserved mechanisms:
- Kernel TCP socket buffer exhaustion and retransmission delays
- JVM garbage collection pause durations (Stop-The-World events)
- PostgreSQL row lock acquisition wait queues
- Client-side HTTP connection pool timeout queues
- External client load surges not captured in steady 1.0s windowing

Under the Phase 3A specification, unobserved variables are formally modeled as exogenous error terms $\epsilon_i(t)$ in the Structural Causal Model.

---

## 4. Temporal Semantics

Telemetry is collected at fixed 1.0-second intervals ($\Delta t = 1.0\text{s}$):
$$t \in \{0, 1, 2, \dots, T-1\}, \quad T \in [38, 40]$$

### 4.1 Arrow of Time & Temporal Ordering
Causal influence strictly respects the thermodynamic and physical arrow of time:
$$\text{Cause}(t_1) \implies \text{Effect}(t_2) \quad \text{requires } t_2 \ge t_1$$
No edge may exist from future time step $t+k$ ($k \ge 1$) to past time step $t$.

### 4.2 Distinguishing Temporal Precedence from Causality
Temporal precedence is a *necessary* condition for lagged causality, but not a *sufficient* condition (the *Post hoc ergo propter hoc* fallacy). Two variables $A(t)$ and $B(t+1)$ may be correlated with $A$ preceding $B$ due to:
1. Genuine causal propagation: $A(t) \to B(t+1)$
2. Confounded common shock: An unobserved shock $Z(t-1)$ directly causes $A(t)$ with lag 1 and $B(t+1)$ with lag 2
3. Asymmetric sampling artifacts: Difference in internal buffer flush intervals

Therefore, temporal precedence is used solely to construct candidate lagged parent sets, which are then pruned using topological constraints and regularized regression.

### 4.3 Intra-Slice vs. Inter-Slice Dynamics
- **Inter-Slice ($\tau \ge 1$)**: Captures network transmission delays, database disk I/O wait times, and cascading thread pool saturation.
- **Intra-Slice ($\tau = 0$)**: Fast sub-second propagation (e.g. in-process memory calls taking 20ms) appears instantaneous at the 1.0s sampling cadence. Intra-slice dependencies are modeled as a Directed Acyclic Graph within slice $t$.

---

## 5. Intervention Semantics Grounded in Actual Fault Mechanisms

Interventions $do(I)$ are derived strictly from the actual execution code in `CausalOpsService.java` and `dataset_generator/`:

| Fault Type | Target Service | Parameter | Mathematical Operator | Primary Responders | Invariant Non-Responders |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`DB_LATENCY`** | `inventory-db` | `latencyMs`: 1200..1800 | $do(\text{db\_latency} += \Delta)$ | `inventory-db.db_latency`, `inventory-service.p99_latency` (0.62 atten), `order-service.p99` (0.38 atten), `gateway.p99` (0.24 atten) | `payment-service.*` (orthogonal branch), `*.error_rate` (no exceptions thrown) |
| **`SERVICE_LATENCY`** | `order-service` | `latencyMs`: 800..1200 | $do(\text{svc\_latency} += \Delta)$ | `order-service.p99_latency`, `order-service.pool_utilization`, `api-gateway.p99_latency` (0.62 atten) | `inventory-db.db_latency` (DB engine not slowed), `payment-service.p99_latency`, `*.error_rate` |
| **`NETWORK_LATENCY`** | `inventory-service` / `payment-service` | `latencyMs`: 800..950 | $do(\text{link\_delay} = \Delta)$ | Caller-perceived `order-service.p99_latency`, propagating to `api-gateway.p99_latency` | Sibling branch (e.g. payment unaffected by inventory link delay), `db_latency` |
| **`ERROR_RATE`** | `payment-service` / `order-service` | `errorRate`: 20..30% | $do(\text{err\_rate} += \Delta)$ | `target.error_rate`, `order-service.error_rate` (0.70 atten), `api-gateway.error_rate` (0.49 atten) | `inventory-db.db_latency`, sibling branch, `p99_latency` (fast-failing errors return instantly) |
| **`SERVICE_FAILURE`** | `payment-service` | N/A (crash simulation) | $do(\text{err\_rate} += 35\%)$ | `payment-service.error_rate`, `order-service.error_rate` (24.5%), `api-gateway.error_rate` (17.15%) | `inventory-service.*`, `inventory-db.*` (completely isolated) |

---

## 6. Formal Causal Queries

### Query A — Root-Cause Forward Effect
- **Formulation**: $P(Y_{\text{downstream}}(t+\Delta) \mid do(X_s = x_{\text{fault}}), X_{\text{pre}} = x_0)$
- **Meaning**: Predict the cascade and SLA violation at `api-gateway` given a specific fault injected at service $s$.
- **Identifiability**: **Identifiable**. The intervention truncates incoming parents to $X_s$, enabling computation via Pearl's G-formula.

### Query B — Root-Cause Comparison (Posterior Attribution)
- **Formulation**: $\arg\max_{s \in \mathcal{S}_{\text{candidates}}} P(do(X_s = \text{fault}) \mid \mathcal{E}_{\text{obs}})$
- **Meaning**: Given observed incident telemetry $\mathcal{E}_{\text{obs}}$, evaluate which single atomic intervention best explains the multi-service degradation pattern.
- **Identifiability**: **Identifiable**. Candidate interventions induce distinct signature vectors (e.g., payment failures do not affect inventory metrics, database latency does not cause payment errors).

### Query C — Counterfactual Restoration
- **Formulation**: $Y_{do(X_s = \text{normal})}(u) \mid \mathcal{E}_{\text{obs}}(u)$
- **Meaning**: If candidate service $s$ had been forcibly held at nominal baseline throughout the incident, would the gateway SLA degradation have vanished?
- **Identifiability**: **Identifiable** under linear-additive structural equations via Pearl's three-step algorithm (Abduction $\to$ Action $\to$ Prediction).

### Query D — Propagation Pathway Identification
- **Formulation**: $\text{Path}(s \to \text{gateway}) = \left[ (V_k \xrightarrow{\tau_k} V_{k+1}) \mid \tau_k \ge 0, w_k > 0 \right]$
- **Meaning**: Trace the exact directed sequence of service metrics and time lags transmitting the fault.
- **Identifiability**: **Identifiable**. System-knowledge graph constraints guarantee path uniqueness across service tiers.

### Query E — Average Treatment Effect ($ATE$)
- **Formulation**: $\text{ATE}_{s \to \text{gateway}} = \mathbb{E}[Y_{\text{gateway}} \mid do(X_s = \text{fault})] - \mathbb{E}[Y_{\text{gateway}} \mid do(X_s = \text{normal})]$
- **Meaning**: Measure the quantitative shift in gateway SLA metrics induced by the fault.
- **Identifiability**: **Identifiable**. Controlled fault injections provide unconfounded randomized experimental interventions.

---

## 7. Candidate Causal Graph Representations

Three primary mathematical representations were analyzed for CausalOps:

1. **Static DAG**: Fails to capture temporal delays and creates false bidirectional cycles under backpressure and load interactions.
2. **Dynamic Bayesian Network (DBN)**: Requires coarse discretization of continuous latency variables, causing severe loss of numerical precision.
3. **Time-Lagged Structural Causal Model (Lagged SCM)**: **Selected Representation**.
   $$X_i(t) = \sum_{k=1}^P \sum_{j \in \text{AllowedParents}(i)} A_{ij}^{(k)} X_j(t-k) + B_i X_i(t-1) + \epsilon_i(t)$$
   Unrolling the SCM across time slices yields an acyclic DAG by construction, natively handles continuous variables, and directly supports matrix-based counterfactual simulation.

---

## 8. Causal Discovery Algorithm Comparison

| Dimension | PC / PCMCI | DirectLiNGAM / VAR-LiNGAM | NOTEARS / DYNOTEARS | Topology-Constrained Lagged SCM (Selected) |
| :--- | :--- | :--- | :--- | :--- |
| **Algorithm Family** | Constraint-based CI tests | Linear Non-Gaussian FCM | Continuous Optimization | Structural Equation Modeling with Hard Priors |
| **Core Assumptions** | Faithfulness, sufficiency, Gaussianity/CI power | Linearity, non-Gaussian noise, sufficiency | Linearity/smoothness, equal noise variance | Topological routing validity, temporal arrow of time |
| **Sample Efficiency** | Extremely Poor ($>10^4$ samples required) | Moderate ($>10^3$ samples) | Moderate (sensitive to noise scales) | **Optimal** (fits convex regressions on sparse allowed parents) |
| **Temporal Data Handling** | Requires PCMCI with long series | Explicit lagged matrices | Explicit $W_0$ and $W_{\text{lag}}$ matrices | **Native** discrete-time lagged autoregression |
| **Physical Priors** | Difficult to constrain cleanly | Post-hoc thresholding only | Soft penalty masks | **Hard binary masking** $M \in \{0, 1\}^{d \times d}$ |
| **Spurious Cross-Branch Edges**| High probability under traffic correlation | High probability without masks | Non-zero probability | **Zero by construction** (forbidden by topology mask) |
| **Counterfactual Capability** | None (CPDAG outputs) | Closed-form linear algebra | Closed-form linear algebra | **Direct 3-step Pearl counterfactual evaluation** |
| **Phase 3B Verdict** | Rejected | Secondary Benchmark | Secondary Benchmark | **PRIMARY SELECTED ARCHITECTURE** |

---

## 9. System-Knowledge Constraints

To prevent statistical learning from generating physically impossible relationships, `graph_constraints.json` establishes strict structural masks:

### Allowed Directed Pathways
1. **Call-Aligned Forward**: `api-gateway` $\to$ `order-service` $\to$ `inventory-service` / `payment-service` $\to$ `inventory-db` (load/request rate propagation).
2. **Backpressure Reverse**: `inventory-db` $\to$ `inventory-service` $\to$ `order-service` $\to$ `api-gateway` (latency and error backpressure).
3. **Intra-Node Dynamics**: `latency` $\to$ `pool_utilization`, `request_rate` $\to$ `pool_utilization`, autoregressive self-loops $X_i(t-1) \to X_i(t)$.

### Forbidden Pathways (Hard Zero Mask $M_{ij} = 0$)
1. **Orthogonal Branches**: Any direct edge between `inventory-db` / `inventory-service` and `payment-service` (completely independent network paths).
2. **Skip-Level Shortcuts**: Any direct edge from `inventory-db` to `order-service` or `api-gateway` (must mediate through `inventory-service`).
3. **Anti-Causal Derived Edges**: Any edge from `anomaly_score` to raw metrics.
4. **Thermodynamic Arrow Violations**: Any edge from $t$ to $t-k$ ($k \ge 1$).

---

## 10. Identifiability Analysis

Under Pearl's graphical criteria (Backdoor Criterion, Frontdoor Criterion, and do-calculus), identifiability is classified as follows:

| Query | Identifiability Status | Theoretical Rationale & Conditions |
| :--- | :--- | :--- |
| **Query A (Forward Effect)** | **Identifiable** | Randomized intervention $do(X_s = x)$ removes all incoming edges to $X_s$, satisfying Backdoor Criterion trivially. |
| **Query B (Attribution)** | **Identifiable** | Microservice topology and fault archetypes induce distinct, non-overlapping downstream symptom vectors. |
| **Query C (Counterfactual)** | **Identifiable** | Holds under additive invertible noise models: $\epsilon_i(t) = X_i(t) - f_i(\text{PA}_i(t))$. |
| **Query D (Pathway)** | **Identifiable** | Tree-structured call graph eliminates multi-path ambiguity; lagged coefficients are uniquely identifiable. |
| **Query E ($ATE$)** | **Identifiable** | Unconfounded experimental randomization between control and fault cohorts guarantees exchangeability. |

---

## 11. Available Causal Ground Truth

The 80-experiment frozen dataset provides rigorous experimental ground truth, subject to strict boundary rules:

```
+─────────────────────────────────────────────────────────────+
|               Experimental Manifest Ground Truth            |
| - experiment_id, fault_target, fault_type                   |
| - fault_start_at, fault_end_at, fault_parameters            |
| - 10 NO_FAULT controls                                      |
+──────────────────────────────┬──────────────────────────────+
                               │
            ┌──────────────────┴──────────────────┐
            │                                     │
            ▼                                     ▼
+──────────────────────────────+       +──────────────────────+
|   ALLOWED EVALUATION USAGE   |       | PROHIBITED TRAINING  |
| - Top-1 Root Cause Accuracy  |       | - NO label inputs    |
| - Top-2 Recall               |       | - NO target columns  |
| - Effect Estimation Error    |       | - NO fault type prior|
| - False Positive Rate on Ctrl|       | - NO step-change flag|
+──────────────────────────────+       +──────────────────────+
```

---

## 12. Pre-Implementation Evaluation Plan

Metrics are formally established prior to algorithm implementation in Phase 3B:

1. **Graph Recovery Metrics**:
   - Structural Hamming Distance: $\text{SHD}(\widehat{\mathcal{G}}, \mathcal{G}^*) = \text{Extra Edges} + \text{Missing Edges} + \text{Reversed Edges}$
   - Directed Edge Precision, Recall, and $F_1$ against validated physical connections.
2. **Root Cause Attribution Metrics**:
   - Exact Match Top-1 Accuracy: $\frac{1}{M}\sum_{m=1}^M \mathbb{I}(\hat{s}_m = s^*_m)$
   - Top-2 Recall: Percentage of experiments where $s^*$ is in the top 2 causal candidates.
3. **Intervention Effect Estimation**:
   - Absolute Treatment Effect Error: $|\widehat{\text{ATE}} - \text{ATE}_{\text{empirical}}|$
4. **Counterfactual Reconstruction Error**:
   - $\text{RMSE}_{\text{CF}}$ evaluated on pre-fault baseline $[0, t_{\text{fault}})$ and post-fault recovery $[t_{\text{end}}, T]$.
5. **Temporal Propagation Delay Error**:
   - $|\hat{\tau}_{\text{hop}} - \tau_{\text{empirical}}|$ in discrete seconds.

---

## 13. Counterfactual Restoration Specification: $do(\text{root\_cause} = \text{normal})$

### What Counterfactual Restoration is NOT
1. It is **NOT** setting telemetry values to zero ($\text{latency} = 0$, $\text{request\_rate} = 0$). Healthy microservices possess positive non-zero baseline latency (~5–15ms), normal throughput (~60 req/min), and positive baseline pool utilization (~25%). Zeroing values represents service annihilation, not health.
2. It is **NOT** deleting the node from the graph. Deleting the node breaks the call chain, artificially driving downstream request rates to zero.

### Formal 3-Step Counterfactual Algorithm
For an observed incident $\mathcal{E}_{\text{obs}}$ and suspected root cause $s$:

1. **Step 1: Abduction (Exogenous Noise Recovery)**
   $$\hat{\epsilon}_i(t) = X_i^{\text{obs}}(t) - \sum_{k=1}^P \sum_{j \in \text{PA}_i} \hat{A}_{ij}^{(k)} X_j^{\text{obs}}(t-k) - \hat{B}_i X_i^{\text{obs}}(t-1)$$
   This recovers the specific operational background state (traffic jitter, background noise) of the actual incident episode.

2. **Step 2: Action (Graph Mutilation)**
   Replace the structural equation for target service $s$ with the healthy baseline condition:
   $$do\left( X_s(t) = X_{s, \text{nominal}}(t) \right)$$
   severing all incoming fault mechanisms.

3. **Step 3: Prediction (Forward Counterfactual Rollout)**
   Propagate through the mutilated SCM for all downstream nodes $j \in \text{Descendants}(s)$:
   $$X_j^{\text{CF}}(t) = \sum_{k=1}^P \sum_{m \in \text{PA}_j} \hat{A}_{jm}^{(k)} X_m^{\text{CF}}(t-k) + \hat{B}_j X_j^{\text{CF}}(t-1) + \hat{\epsilon}_j(t)$$
   If $X_{\text{api-gateway}}^{\text{CF}}(t)$ returns below degraded thresholds, service $s$ is confirmed as the necessary and sufficient causal root cause.

---

## 14. Feedback and Dynamic System Analysis

Cloud microservices exhibit non-linear dynamic feedback loops:
- **Connection Pool Exhaustion**: As latency rises, threads remain blocked, causing rapid non-linear pool saturation.
- **Client Retries & Circuit Breakers**: Upstream retries amplify downstream load during degradation, creating positive feedback loops.
- **Cascading Timeouts**: Once latencies cross the 5000ms socket timeout threshold, latency collapses to timeout duration while error rate surges.

**Modeling Decision**: A standard static DAG cannot model these dynamics. The discrete-time Lagged SCM captures feedback loops across time slices:
$$\text{service}_A(t-1) \xrightarrow{\text{delay}} \text{service}_B(t) \xrightarrow{\text{retry}} \text{service}_A(t+1)$$
Because edges are strictly directed forward across time indices, the unfolded dynamic graph remains acyclic at all times.

---

## 15. Recommended Phase 3B Architecture

Phase 3B should implement the **Topology-Constrained Time-Lagged SCM Engine** structured as follows:

```
ml/causal/
├── scm/
│   ├── __init__.py
│   ├── model.py            # Lagged SCM class with matrix transfer functions
│   ├── constraints.py      # Hard adjacency mask generator from graph_constraints.json
│   ├── discovery.py        # Ridge/LASSO constrained structural coefficient estimator
│   ├── attribution.py      # Query B posterior root-cause attribution ranker
│   └── counterfactual.py   # Pearl 3-step abduction-action-prediction simulator
└── benchmark/
    ├── dynotears_runner.py # Unconstrained DYNOTEARS comparison baseline
    └── lingam_runner.py    # VAR-LiNGAM comparison baseline
```

### Discovery Algorithm Formulation
For each node $i \in \{0, \dots, N-1\}$ and feature $f \in \{0, \dots, F-1\}$:
$$\min_{A_i, B_i} \sum_{t=P}^{T-1} \left\| X_{i, f}(t) - \sum_{k=1}^P \sum_{j \in \text{AllowedParents}(i)} A_{ij}^{(k)} X_j(t-k) - B_i X_i(t-1) \right\|_2^2 + \alpha \|A_i\|_1$$
This is a standard convex lasso/elastic-net optimization solved independently per node in milliseconds, providing massive scalability and numerical stability.

---

## 16. Limitations

1. **Monitored Boundary Sufficiency**: Causal discovery assumes all critical microservices are within the telemetry boundary. External third-party payment gateways or cloud provider network outages are unobserved.
2. **Sampling Cadence Aliasing**: High-frequency sub-second cascades (<100ms) appear instantaneous within the 1.0s telemetry snapshots, requiring intra-slice DAG estimation.
3. **Linearity Approximations**: Linear transfer functions approximate non-linear threshold phenomena (such as connection pool hard limits).

---

## 17. Open Questions for Phase 3B

1. **Optimal Lag Order ($P$)**: Does a lag of $P=1$ second suffice to capture all inter-service propagation, or do deep cascades (`inventory-db` $\to$ `gateway`) require $P=2$ or $P=3$?
2. **Intra-Slice Orientation**: Which statistical criterion best resolves contemporaneous dependencies ($\tau=0$) within the same second without violating acyclicity?
3. **Non-Linear Spline Extensions**: Should piece-wise linear or spline basis expansions be incorporated for connection pool saturation limits?

---

## 18. Verification Summary

All specifications and schemas have been verified using automated regression tests:
- `tests/test_causal_spec.py`: **10 / 10 tests PASSED**
- Zero leakage violations detected across all 50 observed variables.
- Zero modifications to frozen datasets (`dataset/experiments/`, `dataset/ml_v1/`, `dataset/tg_v1/`).
- Zero modifications to Phase 2 checkpoints (`spatiotemporal_v1.pt`, `incident_gate_v1.pt`).
- Critical stop condition strictly enforced: No causal discovery algorithms were implemented or trained.

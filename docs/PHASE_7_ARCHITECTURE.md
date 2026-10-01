# CausalOps Phase 7 Architecture

## System Architecture

CausalOps is a production-style AI-powered site reliability engineering (SRE) platform with causal reasoning, counterfactual simulation, and self-healing remediation capabilities.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          CausalOps — Phase 7 Architecture                    │
└─────────────────────────────────────────────────────────────────────────────┘

  Browser / Operator
       │
       ▼ HTTP/3000 (dev) or HTTPS (prod)
  ┌─────────────┐
  │  Frontend   │  React + Vite + Tailwind
  │  (static)   │  Served via nginx or Vite dev server
  └─────────────┘
       │
       ├─── /api/* ────────────────────────────────────────────────────────────▶
       │                                                                         │
       │                                                              ┌──────────┴──────┐
       │                                                              │ causalops-api   │
       │                                                              │ Spring Boot     │
       │                                                              │ Port 8080       │
       │                                                              └──────────┬──────┘
       │                                                                         │
       └─── /causal/* /remediation/* /health /incidents/* ──────────────────────┤
            /system/* /observability/* /predict/failure-v2                       │
                                                                                 ▼
                                                                       ┌─────────────────┐
                                                                       │  AI Engine      │
                                                                       │  FastAPI/Python │
                                                                       │  Port 8000      │
                                                                       └────────┬────────┘
                                                                                │
                        ┌───────────────────────────────────────────────────────┤
                        │               ML/AI subsystems (frozen)               │
                        │                                                        │
               ┌────────┴───────┐  ┌─────────────┐  ┌─────────────────────┐    │
               │ Classical RCA  │  │ Temporal GNN │  │ Causal SCM (Phase3B)│    │
               │ (RF, v1)       │  │ (v1)         │  │ Ridge, lag=5, P=48  │    │
               └────────────────┘  └─────────────┘  └─────────────────────┘    │
               ┌────────────────┐  ┌────────────────────────────────────────┐   │
               │ Failure Pred   │  │ Counterfactual Engine (Phase 3D)       │   │
               │ (RF+LR, 3     │  │ Pearl Abduction/Action/Prediction       │   │
               │  horizons)     │  └────────────────────────────────────────┘   │
               └────────────────┘                                               │
                                                                                │
                        ┌──────────────────────────────────────────────────────┘
                        ▼
              ┌──────────────────┐
              │   PostgreSQL 16  │  Port 5432 (internal only)
              │   (persistent)   │  Volume: postgres-data
              └──────────────────┘

  ── Monitored Application Tier ────────────────────────────────────────────────
  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐  ┌──────────────┐
  │ api-gateway  │→ │ order-service│→ │ inventory-service  │→ │ inventory-db │
  │ Port 8081    │  │ (internal)   │  │ (internal)         │  │ (postgres)   │
  └──────────────┘  └─────────────┘  └──────┬─────────────┘  └──────────────┘
                    └──────────────────────→ │ payment-service │
                                             └─────────────────┘

  ── Observability Stack ────────────────────────────────────────────────────────
  ┌───────────────┐  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐
  │ OTel Collector│  │ Prometheus  │  │     Loki     │  │    Tempo     │
  │ :4317/:4318   │  │ :9090       │  │ :3100        │  │ :3200        │
  └───────────────┘  └─────────────┘  └──────────────┘  └──────────────┘
```

## Service Inventory

### 1. `postgres` — PostgreSQL 16

| Property | Value |
|---|---|
| Image | `postgres:16-alpine` |
| Port | 5432 (internal only) |
| Volume | `postgres-data` (persistent) |
| Health check | `pg_isready -U $POSTGRES_USER -d $POSTGRES_DB` |
| Dependencies | None |
| Environment | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` |
| Init script | `infrastructure/postgres/init.sql` |
| Restart | `unless-stopped` |

**Persisted data:**
- Service registry (`services`, `dependencies`)
- Application-tier tables (`products`, `inventory`, `orders`, `payments`)
- Incidents (`incidents`)
- Predictions (`predictions`)
- Simulations (`simulations`)
- Remediation approvals (`remediation_approvals`)
- Remediation executions (`remediation_executions`)
- Audit log (`audit_log`)

---

### 2. `ai-engine` — CausalOps AI Engine (FastAPI/Python 3.12)

| Property | Value |
|---|---|
| Build | `./ai-engine` |
| Port | 8000 (internal) |
| Health check | `GET /health` |
| Readiness | `GET /ready` |
| Dependencies | `postgres` (healthy) |
| Volumes | `./ml:/app/ml:ro`, `./dataset:/app/dataset:ro` |
| Environment | `LOG_LEVEL`, `CAUSALOPS_CORS_ORIGINS`, rate limit vars |
| Restart | `unless-stopped` |

**Key API routes:**

| Route | Method | Description |
|---|---|---|
| `/health` | GET | Liveness — checks artifact presence |
| `/ready` | GET | Readiness — fails if critical artifacts missing |
| `/models` | GET | Full model registry with versions and checksums |
| `/metrics` | GET | JSON operational metrics |
| `/metrics/prometheus` | GET | Prometheus text format |
| `/analyze/root-cause` | POST | Classical ML RCA (with heuristic fallback) |
| `/rca/ml` | POST | Dedicated ML RCA endpoint |
| `/predict/failure` | POST | Rolling-heuristic failure prediction (legacy) |
| `/predict/failure-v2` | POST | Phase 6A real failure prediction (frozen RF/LR) |
| `/predict/failure-v2/status` | GET | Prediction model registry |
| `/predict/failure-v2/manifest` | GET | Training manifest with evaluation results |
| `/simulate/counterfactual` | POST | Dependency-attenuation simulation (legacy) |
| `/causal/counterfactual` | POST | Phase 3D Pearl SCM counterfactual simulation |
| `/causal/recommendation` | POST | Phase 4 remediation recommendation |
| `/remediation/approve` | POST | Phase 5 explicit approval (time-bounded) |
| `/remediation/execute` | POST | Phase 5 controlled execution (policy-gated) |
| `/remediation/verify` | POST | Phase 5 telemetry verification |
| `/remediation/rollback` | POST | Phase 5 rollback |
| `/remediation/executions` | GET | List all executions |
| `/remediation/executions/{id}` | GET | Execution detail + timeline |
| `/incidents` | GET | List incidents (filtered) |
| `/incidents/{id}` | GET | Incident detail |
| `/incidents/{id}/timeline` | GET | Chronological audit timeline |
| `/incidents/{id}/health` | GET | Telemetry + service health |
| `/incidents/{id}/conflicts` | GET | Remediation conflict detection |
| `/incidents/{id}/acknowledge` | POST | Operator acknowledgment |
| `/system/health` | GET | Orchestration system health |
| `/observability/metrics` | GET | Phase 6 operational counters |

**Mounted AI/ML artifacts (read-only):**
- `ml/models/classical_rca_rf_v1.joblib` — Classical RCA Random Forest
- `ml/models/causal_scm/` — Topology-Constrained Lagged SCM
- `ml/models/failure_prediction/` — Phase 6A failure prediction models (RF + LR)
- `ml/models/temporal_gnn/` — Temporal GNN checkpoints
- `dataset/tg_v1/` — Frozen temporal graph dataset (91 experiments)

---

### 3. `causalops-api` — Backend Spring Boot API

| Property | Value |
|---|---|
| Build | `./backend/causalops-api` |
| Port | 8080 (external) |
| Health check | `GET /actuator/health` |
| Dependencies | `postgres` (healthy), `ai-engine` (healthy) |
| Environment | DB credentials, service URLs, AI engine URL |
| Restart | `unless-stopped` |

**Role:** Acts as the primary API gateway for frontend requests. Stores operational data in PostgreSQL. Proxies AI/ML requests to the AI engine.

**Configuration (application.yml):**
- Flyway migrations enabled
- Prometheus metrics at `/actuator/prometheus`
- CORS: configurable via `CAUSALOPS_CORS_ORIGINS`
- AI Engine URL: `http://ai-engine:8000`

---

### 4. `inventory-service`, `payment-service`, `order-service`, `api-gateway`

The monitored microservice tier. These are the synthetic application services whose telemetry is analyzed by CausalOps.

| Service | Port | Dependencies |
|---|---|---|
| `inventory-service` | 8080 (internal) | PostgreSQL |
| `payment-service` | 8080 (internal) | None |
| `order-service` | 8080 (internal) | inventory-service, payment-service |
| `api-gateway` | 8081 (external) | order-service |

All built from `./services/<name>` with Maven multi-stage Dockerfiles.

---

### 5. Observability: `otel-collector`, `prometheus`, `loki`, `tempo`

| Service | Image | Port | Data Volume |
|---|---|---|---|
| `otel-collector` | otel/opentelemetry-collector-contrib:0.119.0 | 4317 (gRPC), 4318 (HTTP) | None |
| `prometheus` | prom/prometheus:v3.1.0 | 9090 | `prometheus-data` |
| `loki` | grafana/loki:3.3.2 | 3100 | `loki-data` |
| `tempo` | grafana/tempo:2.6.1 | 3200 | `tempo-data` |

---

## Networks

| Network | Services |
|---|---|
| `causalops-internal` | postgres, ai-engine, causalops-api, all microservices, otel-collector, prometheus |
| `causalops-observability` | otel-collector, prometheus, loki, tempo |

---

## Persistent Volumes

| Volume | Service | Contains |
|---|---|---|
| `postgres-data` | postgres | All operational database state |
| `prometheus-data` | prometheus | 15-day metrics retention |
| `loki-data` | loki | Log storage |
| `tempo-data` | tempo | Distributed trace storage |

---

## Environment Variables

See [`.env.example`](../.env.example) for the complete annotated list.

**Critical variables:**

| Variable | Service | Description |
|---|---|---|
| `POSTGRES_PASSWORD` | postgres, causalops-api, microservices | Database password — NEVER use default |
| `CAUSALOPS_JWT_SECRET` | causalops-api | JWT signing secret — use `openssl rand -hex 32` |
| `CAUSALOPS_CORS_ORIGINS` | ai-engine, causalops-api | Allowed origins — no `*` in production |
| `AI_ENGINE_URL` | causalops-api | AI engine base URL |
| `LOG_LEVEL` | ai-engine | `INFO` (production), `DEBUG` (development) |

---

## Known Phase 6A Validated Limitations

These limitations are documented and must remain visible in production:

1. **Lead time**: ~4–5 seconds on the `tg_v1` benchmark; not 10s or 30s
2. **Target-service accuracy**: 30% pre-onset (use GNN/SCM for post-onset attribution)
3. **Fault-type accuracy**: 40% pre-onset
4. **Rolling buffer**: Production streaming must use fixed 10s lookback window
5. **Dataset topology**: Fixed 5-node synthetic topology

# CausalOps Phase 7 — Deployment Guide

## Prerequisites

- Docker Desktop ≥ 4.26 or Docker Engine ≥ 24.0 with Compose V2
- At least 8 GB RAM allocated to Docker
- At least 20 GB available disk
- `docker compose version` shows ≥ 2.20

## 1. Environment Configuration

```bash
# Clone and enter repository
git clone <repo-url>
cd causalops

# Create environment file from template
cp .env.example .env

# Edit required secrets — NEVER commit this file
nano .env
```

**Minimum required values to change:**
```bash
POSTGRES_PASSWORD=<strong-unique-password>
CAUSALOPS_JWT_SECRET=$(openssl rand -hex 32)
CAUSALOPS_ADMIN_PASSWORD=<strong-admin-password>
```

## 2. Production Startup

```bash
# Pull/build all images
docker compose -f docker-compose.prod.yml build

# Start all services in background
docker compose -f docker-compose.prod.yml up -d

# Watch startup progress
docker compose -f docker-compose.prod.yml logs -f
```

### Expected startup order:
1. `postgres` → healthy (~15s)
2. `ai-engine` → healthy (~30s, verifies model artifacts)
3. `causalops-api` → healthy (~60s, runs Flyway migrations)
4. `inventory-service`, `payment-service` → healthy (~45s)
5. `order-service`, `api-gateway` → healthy (~45s)
6. Observability stack (parallel)

### Health verification:
```bash
# Check all services are healthy
docker compose -f docker-compose.prod.yml ps

# AI Engine liveness
curl http://localhost:8000/health

# AI Engine readiness (returns 503 if models missing)
curl http://localhost:8000/ready

# Model versions
curl http://localhost:8000/models

# Backend API
curl http://localhost:8080/actuator/health

# Prometheus
curl http://localhost:9090/-/healthy
```

## 3. Shutdown

```bash
# Graceful shutdown (preserves volumes)
docker compose -f docker-compose.prod.yml down

# Shutdown and remove volumes (DESTRUCTIVE — loses all data)
docker compose -f docker-compose.prod.yml down -v
```

## 4. Persistence

PostgreSQL data persists in the `postgres-data` named Docker volume.

State that survives restart:
- All incidents (state machine, timeline, metadata)
- All predictions
- All simulations
- All remediation approvals and execution records
- Full audit log
- Service registry and topology

## 5. Backup and Restore

See [`docs/BACKUP_RESTORE.md`](./BACKUP_RESTORE.md) for the full backup/restore procedure.

**Quick backup:**
```bash
docker exec causalops-postgres-1 \
  pg_dump -U causalops causalops \
  | gzip > causalops-backup-$(date +%Y%m%d-%H%M%S).sql.gz
```

## 6. Logs

```bash
# Follow all logs
docker compose -f docker-compose.prod.yml logs -f

# Follow specific service
docker compose -f docker-compose.prod.yml logs -f ai-engine

# View last N lines
docker compose -f docker-compose.prod.yml logs --tail=100 causalops-api
```

Logs are structured JSON from the AI engine. Use `| jq` for pretty-printing:
```bash
docker compose -f docker-compose.prod.yml logs -f ai-engine | jq .
```

## 7. Troubleshooting

### AI Engine fails to start: "STARTUP FAILED: Required model artifacts missing"
The model artifacts must be present in `./ml/models/` before startup.
These are frozen and must be restored from a backup or the repository.
```bash
ls ml/models/causal_scm/model.json
ls ml/models/failure_prediction/manifest.json
ls ml/models/classical_rca_rf_v1.joblib
```

### PostgreSQL connection refused
```bash
# Check postgres health
docker compose -f docker-compose.prod.yml ps postgres
docker compose -f docker-compose.prod.yml logs postgres
```

### API Engine returns 503 on /ready
```bash
curl -v http://localhost:8000/ready
# Check components: {"ready": false, "failed_components": [...]}
```

### Metrics
```bash
# JSON metrics
curl http://localhost:8000/metrics

# Prometheus format
curl http://localhost:8000/metrics/prometheus
```

### Rate limit errors (HTTP 429)
The AI engine enforces per-IP rate limits on expensive endpoints:
- `/causal/counterfactual`: 10 req/min
- `/predict/failure-v2`: 30 req/min
- `/remediation/execute`: 5 req/min

## 8. Authentication & Roles

| Role | Endpoints |
|---|---|
| `VIEWER` | All GET endpoints |
| `OPERATOR` | VIEWER + `/remediation/approve`, `/remediation/execute` |
| `ADMIN` | OPERATOR + configuration endpoints |

Authentication is implemented at the `causalops-api` layer. The AI engine is on an internal Docker network and not directly exposed to end users in production.

**Note:** Phase 5 safety gates (explicit approval, blast-radius, policy validation) remain active regardless of role.

## 9. Model Artifacts

Model artifacts are frozen and read-only mounted into the AI engine:
```
ml/models/
  classical_rca_rf_v1.joblib    — Classical RCA (Phase 2)
  causal_scm/                   — Topology-Constrained SCM (Phase 3B)
  failure_prediction/           — Failure Prediction models (Phase 6A)
  temporal_gnn/                 — Temporal GNN (Phase 2D)
```

**Never retrain models in production.** Retraining requires a controlled offline pipeline.

Model versions are exposed at `GET /models`.

## 10. Telemetry & Observability

| Service | URL | Purpose |
|---|---|---|
| Prometheus | `http://localhost:9090` | Metrics storage and querying |
| Loki | `http://localhost:3100` | Log aggregation |
| Tempo | `http://localhost:3200` | Distributed tracing |

OTel Collector receives traces at `:4317` (gRPC) and `:4318` (HTTP).

## 11. Rollback

If a deployment fails:
```bash
# Stop the failed deployment
docker compose -f docker-compose.prod.yml down

# Restore the previous image (if using a registry)
# Or rebuild from the last working commit
git checkout <last-good-commit>
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d

# Restore data if needed (see BACKUP_RESTORE.md)
```

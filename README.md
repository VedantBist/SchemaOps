# CausalOps

CausalOps is a local, explainable observability and incident-response demo. It combines a React/Vite console, a Spring Boot control API, a small FastAPI analytics engine, and a deliberately simple service topology that can be fault-injected on demand.

The main demonstration is a database-latency incident:

```text
API gateway -> order service -> inventory service -> PostgreSQL
									\-> payment service
```

The platform accepts a fault, applies it to the live demo path, collects the resulting incident state, ranks likely causes, predicts service risk, and evaluates a simulated remediation. The analytics are intentionally transparent heuristics, so this project is useful for demos and local experimentation rather than production diagnosis.

## What is included

- **Frontend:** React 19, TypeScript, Vite, Three.js, and Lucide icons at the repository root.
- **Control API:** Spring Boot 3.4 with PostgreSQL persistence, Flyway migrations, validation, Actuator, and Swagger UI in `backend/causalops-api`.
- **AI engine:** FastAPI endpoints for root-cause scoring, failure prediction, and counterfactual simulation in `ai-engine`.
- **Demo services:** API gateway, order, inventory, and payment services in `services/`.
- **Telemetry infrastructure:** OpenTelemetry Collector, Prometheus, Loki, and Tempo configurations in `infrastructure/`.
- **Database:** PostgreSQL 16 with the demo business schema and CausalOps migrations.

## Quick start

### Prerequisites

For the complete demo, install:

- Docker Desktop with Docker Compose
- Node.js 20 or newer and npm

Java 21, Maven, and Python 3.12 are only needed when running an individual backend or the AI engine outside Docker.

### Start the platform

From the repository root:

```bash
cp .env.example .env
npm install
docker compose up --build
```

The Compose build can take a few minutes the first time. In a second terminal, start the frontend:

```bash
npm run dev
```

Open <http://localhost:3000>. The frontend uses `http://localhost:8080/api` by default. To point it at another API, set `VITE_API_BASE_URL` in `.env` before starting Vite.

### Verify the installation

```bash
curl http://localhost:8080/actuator/health
curl http://localhost:8000/health
curl http://localhost:8081/orders/demo
```

The first two commands should report an `UP` status. The third exercises the business path through the gateway.

## Run the incident demo

1. Make sure the Compose stack and frontend are running.
2. Inject 1.2 seconds of latency into the inventory database path:

	```bash
	curl -X POST http://localhost:8080/api/faults \
	  -H 'Content-Type: application/json' \
	  -d '{
		 "type": "DB_LATENCY",
		 "target": "inventory-db",
		 "severity": "HIGH",
		 "durationSeconds": 60,
		 "parameters": {"latencyMs": 1200}
	  }'
	```

3. Exercise the affected path a few times:

	```bash
	curl http://localhost:8081/orders/demo
	```

4. Wait roughly two telemetry cycles, or about ten seconds, then inspect active incidents:

	```bash
	curl http://localhost:8080/api/incidents/active
	curl http://localhost:8080/api/predictions
	```

5. Take the incident ID from the active-incidents response and request its analysis:

	```bash
	curl http://localhost:8080/api/incidents/<incident-id>/root-cause
	curl http://localhost:8080/api/incidents/<incident-id>/evidence
	```

6. Submit a counterfactual remediation that reduces the target's impact by 70%:

	```bash
	curl -X POST http://localhost:8080/api/simulations \
	  -H 'Content-Type: application/json' \
	  -d '{"target":"inventory-db","reductionPercent":70}'
	```

7. Clear active faults when finished:

	```bash
	curl -X POST http://localhost:8080/api/faults/clear
	```

The same workflow is available through the frontend views for overview, topology, active incidents, root cause, predictions, simulation, metrics, logs, and traces.

## HTTP API

The Spring API is available at `http://localhost:8080`. Its base path is `/api`.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/overview` | Dashboard summary |
| `GET` | `/api/services` | List services |
| `GET` | `/api/services/{id}` | Inspect one service |
| `GET` | `/api/topology` | Service dependency graph |
| `GET` | `/api/incidents` | All incidents |
| `GET` | `/api/incidents/active` | Unresolved incidents |
| `GET` | `/api/incidents/history` | Resolved incidents |
| `GET` | `/api/incidents/{id}/root-cause` | Root-cause analysis |
| `GET` | `/api/incidents/{id}/evidence` | Analysis evidence |
| `GET` | `/api/predictions` | Failure-risk predictions |
| `GET` | `/api/faults` | Active faults |
| `POST` | `/api/faults` | Inject a fault |
| `POST` | `/api/faults/{id}/stop` | Stop one fault |
| `POST` | `/api/faults/clear` | Stop all faults |
| `POST` | `/api/simulations` | Run a counterfactual simulation |
| `GET` | `/api/metrics` | Metric samples, optionally filtered by `service` |
| `GET` | `/api/logs` | Log data |
| `GET` | `/api/traces` | Trace data |
| `GET` | `/api/system/status` | System status |
| `GET` | `/api/events/stream` | Server-sent incident events |

Interactive OpenAPI documentation is available at <http://localhost:8080/swagger-ui/index.html> when the API is running.

The AI engine is available at `http://localhost:8000`:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Health check |
| `GET` | `/models/status` | Describe the active heuristic implementations |
| `POST` | `/analyze/root-cause` | Rank likely causes from topology and telemetry |
| `POST` | `/predict/failure` | Estimate service failure risk |
| `POST` | `/simulate/counterfactual` | Estimate the effect of reducing a target |

## How the analysis works

The analytics engine does not require a GPU, remote model, or Gemini API key. It uses deterministic, inspectable calculations:

- Root cause scoring combines normalized anomaly, temporal precedence, dependency reach, propagation consistency, and metric correlation.
- Prediction uses a rolling latency/error heuristic.
- Counterfactual simulation attenuates impact across the dependency graph.

Counterfactual results are marked as simulated and model-estimated. They should not be interpreted as causal-discovery results or production-grade SRE recommendations. More detail is in [docs/ai-engine.md](docs/ai-engine.md) and [docs/architecture.md](docs/architecture.md).

## Local development

Install frontend dependencies and use the available checks:

```bash
npm install
npm run lint
npm run build
```

Run the AI engine without Docker:

```bash
cd ai-engine
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Run the AI tests from the AI engine directory:

```bash
cd ai-engine
python3 -m pytest tests
```

Run the Spring API locally with Java 21 and Maven after starting PostgreSQL and the AI engine:

```bash
cd backend/causalops-api
mvn spring-boot:run
```

For normal development, Compose is recommended because it supplies the expected service names, database, telemetry components, and environment variables.

## Configuration

`.env.example` contains the main local defaults. The most useful overrides are:

| Variable | Default | Description |
| --- | --- | --- |
| `POSTGRES_DB` | `causalops` | PostgreSQL database name |
| `POSTGRES_USER` | `causalops` | PostgreSQL user |
| `POSTGRES_PASSWORD` | `causalops` | PostgreSQL password |
| `POSTGRES_PORT` | `5432` | Host PostgreSQL port |
| `CAUSALOPS_API_PORT` | `8080` | Host port for the Spring API |
| `AI_ENGINE_URL` | `http://localhost:8000` | AI engine URL for local processes |
| `DEMO_MODE` | `true` | Enables demo behavior in the API |
| `VITE_API_BASE_URL` | `http://localhost:8080/api` | Frontend API base URL |

Compose uses the service-to-service values defined in `docker-compose.yml`, so container names such as `postgres` and `ai-engine` should be used only from inside the Compose network.

## Service and telemetry URLs

| Component | URL |
| --- | --- |
| Frontend | <http://localhost:3000> |
| CausalOps API | <http://localhost:8080> |
| Swagger UI | <http://localhost:8080/swagger-ui/index.html> |
| Business gateway | <http://localhost:8081> |
| AI engine | <http://localhost:8000> |
| Prometheus | <http://localhost:9090> |
| Loki | <http://localhost:3100> |
| Tempo | <http://localhost:3200> |
| OTLP gRPC | `localhost:4317` |
| OTLP HTTP | `localhost:4318` |

## Troubleshooting

- **The API is not ready:** wait for PostgreSQL and the AI engine health checks, then inspect `docker compose logs causalops-api`.
- **Port already in use:** change `POSTGRES_PORT` or `CAUSALOPS_API_PORT` in `.env`; the gateway, frontend, AI engine, and telemetry ports are declared in `docker-compose.yml` and may also need to be adjusted there.
- **The frontend shows API errors:** confirm the API is reachable at `http://localhost:8080/actuator/health` and that `VITE_API_BASE_URL` ends with `/api`.
- **The database schema is stale:** the named `postgres-data` volume preserves data between runs. To recreate the local database, run `docker compose down -v` and then start the stack again. This deletes the Compose PostgreSQL volume.
- **A fault remains active:** call `POST /api/faults/clear` or stop the individual fault with `POST /api/faults/{id}/stop`.

## Repository layout

```text
.
├── src/                         React/Vite frontend
├── ai-engine/                   FastAPI analytics and Python tests
├── backend/causalops-api/       Spring Boot control API
├── services/                    Gateway and business demo services
├── infrastructure/              PostgreSQL, OpenTelemetry, and telemetry configs
├── docs/                        Architecture, API, demo, and AI notes
├── docker-compose.yml           Local multi-service environment
└── package.json                 Frontend scripts and dependencies
```

## Production Deployment (Phase 7 Hardened Architecture)

For production-style environments, CausalOps includes a hardened deployment specification:

```bash
# 1. Populate production environment config (never commit real secrets)
cp .env.example .env
nano .env

# 2. Build and launch hardened services with isolated networks and health checks
docker compose -f docker-compose.prod.yml up --build -d

# 3. Verify deployment health via smoke test
python3 scripts/phase7_smoke_test.py
```

### Phase 7 Production Features
- **Production Compose:** `docker-compose.prod.yml` with explicit dependency ordering, strict healthchecks, restart policies (`unless-stopped`), internal networks, and named volume persistence.
- **Security & Secret Handling:** Strict `.env` segregation, explicit non-wildcard CORS, role-based authorization (Viewer, Operator, Admin), suppressed stack traces, and structured error format (`X-Correlation-ID`).
- **Persistence:** PostgreSQL 16 schema supporting persistent incident states, failure predictions, SCM simulations, and append-only remediation execution audits.
- **Model Governance:** Frozen artifacts (`ml/models/`) mounted read-only, verified on startup, and served with version metadata and documented scientific limitations via `GET /models`.
- **Reliability & Rate Limiting:** Per-IP token-bucket rate limiting on heavy SCM/counterfactual endpoints and Prometheus metrics on `/metrics` and `/metrics/prometheus`.

## Further documentation

- [Phase 7 Deployment Guide](docs/PHASE_7_DEPLOYMENT.md)
- [Phase 7 Production Architecture](docs/PHASE_7_ARCHITECTURE.md)
- [Phase 7 Security Policy](docs/SECURITY.md)
- [Backup and Restore Guide](docs/BACKUP_RESTORE.md)
- [Phase 6A Scientific Validation Report](ml/failure_prediction/PHASE_6A_VALIDATION_REPORT.md)
- [Architecture](docs/architecture.md)
- [API reference](docs/api.md)
- [Demo walkthrough](docs/demo.md)
- [AI engine notes](docs/ai-engine.md)


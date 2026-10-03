# CausalOps

**Causal AIOps for self-healing microservices: from alert to answer to action.**

CausalOps watches a running microservice system through OpenTelemetry and closes the loop that monitoring tools leave open:

1. **Detects** incidents: SLO breaches, plus an anomaly gate calibrated on the system's own quiet behaviour that can fire before an SLO is crossed.
2. **Explains** them: names the root cause (a service, database or call link) with a topology-constrained lagged structural causal model, and gives evidence.
3. **Simulates** the fix: a counterfactual rollout ("what if this component had stayed at baseline?") with ensemble uncertainty and validity checks.
4. **Fixes it safely** with tiered auto-remediation:
   - low-risk, reversible actions run automatically when 17 policy rules pass;
   - everything else waits for approval;
   - every action is verified on real measurements, rolled back if it does not help, and escalated when nothing safe is left;
   - executors: Docker Engine API, Kubernetes API and HMAC-signed runbook webhooks.
5. **Learns each environment by itself:** topology is discovered from traces, models are calibrated within 24 hours and validated on labelled incidents, retrained every 7 days, and a new model is promoted only through champion/challenger.

Every number in the console comes from the API, with no mock data.

## Run the demo in one command

**Requirements:** Docker Desktop (4+ CPUs, 8+ GB RAM), on macOS, Linux or Windows.

```bash
bash scripts/demo/start.sh
```

Then open **http://localhost:3000**.

The repository ships a **demo snapshot** (`demo/`), so a fresh clone starts with the reference environment already **ACTIVE**: calibrated models, incident history and outcome metrics. No learning window is needed.

- **Setup and exact demo script for a Mac:** [docs/MAC_SETUP_AND_DEMO.md](docs/MAC_SETUP_AND_DEMO.md)
- **Shorter demo notes:** [docs/DEMO_GUIDE.md](docs/DEMO_GUIDE.md)
- **Reset between demo runs:** `bash scripts/demo/reset.sh`

| URL | What |
|---|---|
| http://localhost:3000 | Console |
| http://localhost:8080/api | Platform API (`/actuator/health`) |
| http://localhost:8000/docs | AI engine |
| http://localhost:3001 | Grafana |

## Architecture

```
reference system (OTel Java agent) ─► otel-collector ─► Prometheus · Tempo · Loki
   api-gateway → order → inventory → inventory-db                │
                       └→ payment                                ▼
                                    causalops-api (Spring Boot) ◄──► ai-engine (FastAPI)
                                    ingestion · topology · detector   baselines · anomaly gate · SCM
                                    remediation policy · audit · SSE  RCA · counterfactual · executors
                                             │                                │
                                       PostgreSQL ◄──────── model registry ───┘
                                             │
                                    console (React, nginx) :3000
```

| Path | What |
|---|---|
| `backend/causalops-api` | Spring Boot 3.4 platform API: environments, ingestion, topology, incidents, calibration lifecycle, remediation policy, approvals, change events, audit log. Flyway migrations V1–V7. |
| `ai-engine/` + `ml/engine/` | FastAPI service and the environment-agnostic engine: baselines, anomaly gate, lagged SCM, RCA, forecaster, counterfactual, calibration, executors (`ml/engine/executors`). |
| `src/` | React 19 + Vite console (pages, live charts, topology graph, setup wizard). |
| `services/` | Instrumented reference microservices with token-protected fault injection. |
| `infrastructure/` | OTel collector, Prometheus, Tempo, Loki, Grafana, Toxiproxy, load generator, Postgres init, console nginx. |
| `scripts/demo/` | `start.sh`, `reset.sh`, `snapshot.sh`. |
| `scripts/dev/` | `verify_phase1.py` … `verify_phase4.py`: end-to-end checks against the running stack. |
| `scripts/calibration/` | Chaos campaign that produces labelled incidents for calibration. |
| `docs/phases/` | Phase reports with measured results. |
| `ml/models/` | Archived synthetic `tg_v1` research models (not used in serving; see its README). |

## Measured on the reference system

| Measure | Result |
|---|---|
| Detection recall / median delay | 100% / 22 s (18 labelled incidents) |
| Root cause ranked first / in the top two | 83% / 100% |
| Causal-model fit | median cross-validated R² 0.94 |
| Live payment-service failure | detected, root cause named, container restarted automatically, recovery verified, resolved in 2 min 17 s |

These are the platform's own numbers (Calibration page, `scripts/dev/verify_phase3.py`, `verify_phase4.py`).

## Tests

```bash
cd backend/causalops-api && mvn clean test
```
```bash
python3 -m pytest tests/engine ai-engine/tests
```
```bash
npm install && npm run lint && npm run build
```

The backend integration tests use Testcontainers, so Docker must be running.

## Status

| Phase | Status |
|---|---|
| 1 Real telemetry · 2 Ingestion and topology · 3 Self-calibrating ML · 4 Tiered auto-remediation · 5 Live console | Done (see `docs/phases/`) |
| 6 Sign-in and roles, Helm chart, integration guide | Planned (the console runs in a labelled "auth not enabled" mode) |
| 7 Long evidence campaigns and final report | Planned |

## Security notes

- The default tokens in `docker-compose.yml` (`CHAOS_TOKEN`, `ENGINE_INTERNAL_TOKEN`) are for local demos. Set real values in `.env` (see `.env.example`) anywhere else.
- The AI engine mounts the Docker socket for the Docker executor, and only acts on containers of the `causalops` compose project.

## Team

PBL Semester V, T.E. AI & DS (Division A), Thakur College of Engineering and Technology:
- Vedant Bist (14)
- Aayush Gupta (35)
- Mayank Jaiswal (54)

Guide: Ms. Swati Mude, Assistant Professor.

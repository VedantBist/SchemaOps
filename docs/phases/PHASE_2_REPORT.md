# Phase 2 Report: Real Ingestion, Discovered Topology, Environment Model

Date: 2026-10-02 · Status: **complete, awaiting your verification** · Nothing committed or pushed.

## Goal

Make the CausalOps platform itself run on real data. The backend no longer computes any telemetry: it reads measurements from Prometheus, discovers services and dependencies from traces, opens incidents from real SLO breaches, and serves logs and traces live from Loki and Tempo. Every monitored system is an **environment** with its own configuration. This is the foundation for onboarding any system in later phases.

## What changed

### Backend (`backend/causalops-api`), rewritten into focused packages

| Package | Responsibility |
|---|---|
| `environment` | `environments` table plus API. Each environment's JSON config holds endpoints (Prometheus/Tempo/Loki/collector), PromQL metric templates, SLOs, detection thresholds, external pseudo-nodes and calibration settings. Defaults live in `environment-defaults.json` (plain configuration, not code). The first environment bootstraps from env vars on a fresh install. Config is validated on every update. |
| `telemetry` | `TelemetryIngestor` (every 5 s, one PromQL query per metric grouped by service). `TopologySync` (every 15 s, from the collector's service graph). `MeasurementCollector`, `TopologyDiscovery` and `SloEvaluator` are pure logic and unit-tested. A metric Prometheus has no data for is stored as **NULL**; nothing is invented. |
| `incident` | `SloBreachDetector`: opens an incident after N consecutive breaching samples, adds services that breach later, and resolves after M consecutive healthy samples, recording the measured evidence. RCA runs only on an explicit `POST /api/incidents/{id}/rca`, against the incident's stored telemetry window; GET never triggers work. |
| `observability` | `/api/logs` (Loki: filter by service, text or trace id), `/api/traces` and `/api/traces/{id}` (Tempo). |
| `engine` | `/api/engine/**` proxies the AI engine with status codes intact, so the UI (Phase 5) needs only one origin. |
| `faults` | Fault lifecycle (from Phase 1), now per environment. A fault can only target nodes in the discovered topology. |
| `system` | `/api/system/status` checks DB, AI engine, Prometheus, Tempo, Loki, collector and ingestion freshness live. |
| `overview` | Overview, services, topology, metrics (stored series), predictions and simulations read models, plus manual topology additions (`POST /api/topology/nodes`, `POST /api/topology/edges`) for parts that emit no traces. |

**Removed:**
- `CausalOpsService` (the synthetic `collect()`, the hardcoded hop distances, the random incident keys, and the automatic heuristic prediction and simulation calls).
- `ApiController`, the JPA entity/repository, and the tautological test.
- JPA (replaced by JDBC).
- The `DEMO_MODE` flag.

### Database: Flyway `V3__environments_real_telemetry.sql`
- Adds `environments`, and `environment_id` on services, dependencies, telemetry, incidents and faults.
- **Deletes** the hand-seeded services and dependencies and **truncates** formula telemetry.
- Measured columns become nullable.
- Adds discovery metadata (kind, source, first/last seen, call and failure rates).
- Incident keys come from a sequence (`INC-1001`, …), so they can't collide.
- Adds incident evidence and timeline support.
- Drops the never-used `log_events` and `trace_summaries` tables.

The plan listed tables for recommendations, approvals, executions, calibration runs and the model registry under V3. I'm adding each in the phase that first writes to it (3 and 4), so no table exists without a writer.

### Infrastructure fixes found while verifying
- **Loki and Tempo were never "ready"** (their `/ready` returned 503 indefinitely) in Phase 1. The single-binary ingester ring kept flapping. Fixed by pinning the instance address and `min_ready_duration: 0s`. This also fixes the prod-compose health checks, which probe `/ready`.
- **Throughput jitter of up to ±50%.** Agent batching, collector flushing and Prometheus scraping all ran on 5 s cycles, so a 30 s `rate()` window held 5 or 6 counter steps. Fixed by defaulting to a 1 m window and exporting spans every 1 s (`OTEL_BSP_SCHEDULE_DELAY`). Services now agree within about 1%.
- The collector copies `service.name` onto every metric data point. Agent metrics such as OTel `db.client.connections.*` then group by service like span metrics do, for any language, not just Spring/Hikari. The Grafana pool panel now uses these semantic-convention metrics.

Audit items addressed: **C8** (backend), **H6** (CORS from config; no `@CrossOrigin("*")`), **H9** (sequence keys), **M10** (no GET side effects), **M11** (part: predictions are no longer fabricated after RCA), **M18** (backend sends named SSE events), **L3** (all SQL parameterized), **L4** (real `/system/status`, `/simulations/{id}`, throughput).

## Verification

**Backend tests:** `mvn clean test` gives **13/13 pass**. These include `PlatformIntegrationTest` on real PostgreSQL 16 via Testcontainers, which covers:
- migrations V1→V3 removing the seeded nodes;
- environment bootstrap;
- discovery and ingestion storing only measured values (an unobserved metric stays NULL);
- an SLO breach opening a CRITICAL incident with evidence, then resolving it with timeline DETECTED→RECOVERED;
- 150 incidents with unique keys;
- the 404/400 error contract;
- CORS refusing unlisted origins.

**Live stack:** `python scripts/dev/verify_phase2.py --incident` gives **34/34 pass**:

```
1. API edges == Prometheus service graph; no seeded nodes; inventory-db classified as database
2. API p99/rps == direct PromQL (e.g. order-service p99 25.0 vs 25.0 ms, rps 3.80 vs 3.80)
3. api, database, aiEngine, prometheus, tempo, loki, collector, ingestion: all UP (last sample 0.2 s ago)
4. /api/logs (Loki), /api/traces and /api/traces/{id} (Tempo, 8 spans) served live
5. /api/engine/health proxied; engine status codes pass through
6. 404/400 JSON errors; preflight from http://evil.example -> 403; localhost:3000 allowed
7. no Math.pow / 0.62 / "60 req/min" / DEMO_MODE / new Random( in backend source
8. 900 ms fault -> INC-1002 "SLO breach: api-gateway, order-service" with evidence
   latencyP99 observed 987.38 > 500 (prometheus) -> resolved on its own after the fault stopped
```

The Phase 1 checks still pass.

### How to verify yourself

```bash
python scripts/dev/verify_phase2.py --incident
```

```bash
curl -s localhost:8080/api/topology
```

```bash
curl -s localhost:8080/api/system/status
```

To rerun the backend tests (needs the Docker socket for Testcontainers):

```bash
docker run --rm -v //var/run/docker.sock:/var/run/docker.sock -v "E:/causalops/backend/causalops-api:/src" -v causalops-m2:/root/.m2 -w /src -e TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal -e TESTCONTAINERS_RYUK_DISABLED=true maven:3.9-eclipse-temurin-21 mvn -B clean test
```

## API reference (new and changed)

| Method & path | Purpose |
|---|---|
| `GET/POST /api/environments`, `GET /api/environments/{id}`, `GET /api/environments/defaults` | Environments and default config |
| `PUT /api/environments/{id}/config`, `PUT /api/environments/{id}/status` | Change integration config (validated) or lifecycle state |
| `GET /api/overview`, `/api/services`, `/api/services/{name}`, `/api/topology` | Discovered topology with measured values |
| `POST /api/topology/nodes`, `POST /api/topology/edges` | Manual additions; discovery never overwrites them |
| `GET /api/metrics?service=&minutes=` | Stored measured series |
| `GET /api/logs?service=&contains=&traceId=&minutes=` | Live logs from Loki |
| `GET /api/traces?service=&minDurationMs=`, `GET /api/traces/{traceId}` | Live traces from Tempo |
| `GET /api/incidents?state=active\|resolved\|all`, `/api/incidents/{id}`, `/api/incidents/{id}/timeline` | Incidents and timeline |
| `GET /api/incidents/{id}/root-cause`, `POST /api/incidents/{id}/rca` | Stored RCA / run RCA on the stored window |
| `GET /api/system/status` | Live component health and ingestion freshness |
| `ANY /api/engine/**` | AI engine through the platform |
| `GET /api/events/stream` | SSE with named events (`telemetry.ingested`, `topology.changed`, `incident.created`, `incident.updated`, `incident.resolved`, `fault.*`, `rca.completed`) |

All routes accept an optional `environmentId`. Without it they use the single monitored environment.

## Known limitations carried forward

- **The UI still reads mock data** in most views, and the shapes of some fields changed (for example incidents now include `evidence` and `environmentId`, and services include `kind` and `stale`). Phase 5 rewires every view to these endpoints.
- **The AI engine is unchanged.** `POST /api/incidents/{id}/rca` sends real telemetry, but the engine's models were trained on synthetic data and expect an `anomaly` field that is NULL until Phase 3 computes anomaly scores. Phase 3 replaces this.
- Baselines are the median observed p99 over the learning window (a real statistic). Phase 3 replaces them with calibrated per-metric baselines.
- The detector's streak counters are in memory. After an API restart they rebuild over the next few samples; open incidents stay in the database.

## Files

- **Modified:** `docker-compose.yml`, `docker-compose.prod.yml`, `infrastructure/{otel,loki,tempo}/*`, `infrastructure/grafana/dashboards/reference-red.json`, `backend/causalops-api/{Dockerfile,pom.xml}`, `application.yml`, `CausalOpsApplication.java`, `config/WebConfig.java`, `controller/ApiExceptionHandler.java`.
- **Moved (staged with `git mv`):** `EventBus` → `events/`, `FaultInjector` and `FaultRequest` → `faults/`.
- **Deleted (staged with `git rm`):** `ApiController`, `CausalOpsService`, `entity/ServiceEntity`, `repository/ServiceRepository`, `TelemetryIntervalConfigTest`.
- **New:** `engine/`, `environment/`, `events/EventController`, `faults/{FaultController,FaultService}`, `incident/`, `observability/`, `overview/`, `system/`, `telemetry/`, `V3__environments_real_telemetry.sql`, `environment-defaults.json`, `src/test/**`, `scripts/dev/verify_phase2.py`, this report.

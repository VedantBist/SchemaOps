# Phase 1 Report: Real Telemetry from the Reference System

Date: 2026-10-02 · Status: **complete, awaiting your verification** · Nothing committed or pushed.

## Goal

Make the bundled reference system (api-gateway, order-service, inventory-service, payment-service, inventory-db) produce **real** telemetry through the proposed architecture, and make every fault type a **real** fault:

`services (OTel Java agent) → OTel Collector → Prometheus / Tempo / Loki → Grafana`

The backend's synthetic `collect()` is still running. It is replaced in Phase 2, when CausalOps starts ingesting from Prometheus.

## What changed

| Area | Change |
|---|---|
| Reference services | OTel Java agent 2.10 in every image (traces, metrics, logs over OTLP). `micrometer-registry-prometheus` and `/actuator/prometheus`. Code rewritten to be readable, with proper downstream error propagation (502/504) and timeouts. |
| Real faults | New token-protected `/internal/chaos` in each service. `SERVICE_LATENCY` delays real requests. `ERROR_RATE` fails a percentage of requests. `SERVICE_FAILURE` returns 503. `CONNECTION_POOL_SATURATION` holds real Hikari connections on `pg_sleep`. Every fault auto-expires. |
| Network/DB faults | Toxiproxy sits on every real path (edge→gateway, gateway→order, order→inventory, order→payment, inventory→DB). `DB_LATENCY` and `NETWORK_LATENCY` add a real latency toxic to the link. |
| Backend | New `FaultInjector` (config-driven target→mechanism map, no hardcoded switch). Faults expire server-side after `durationSeconds`. Unknown targets and missing parameters return 400. `CausalOpsService` uses it. |
| Database | `init.sql` now only creates the reference system's own database, `inventory-db`. Flyway owns the CausalOps schema; the original startup crash is fixed. |
| Collector | `spanmetrics` + `servicegraph` connectors (language-agnostic RED metrics and call graph from traces), traces → Tempo, logs → Loki (OTLP), `health_check` extension, and a filter that drops monitoring and control spans. |
| Prometheus | Scrapes the collector, the services' Micrometer endpoints, causalops-api and the AI engine. |
| Grafana | New, with provisioned Prometheus/Tempo/Loki datasources (trace↔log links) and a "Service RED and Dependencies" dashboard at http://localhost:3001. |
| Load generator | `infrastructure/loadgen/loadgen.py`: continuous Poisson traffic (default 5 req/s with a slow ±30% wave). Standard library only; runs in `python:3.12-slim`. |
| Compose | Dev and prod updated. Prod fixes: no `wget` health check on the distroless collector; Prometheus, Loki and Tempo no longer published on the host; required secrets (`${VAR:?}`). The api-gateway is no longer published on host port 8081. |
| Hygiene | `.gitattributes` (LF for JSON/py/sh/sql/yml, so artifact checksums survive Windows checkouts). `scripts/dev/reset_db.sh` (asks for confirmation). `.env.example` updated. |

Audit items addressed: **C8** (data part), **H8**, **M14** (pipeline part), **L7**, **L9** (partial), and the Flyway/init.sql startup crash.

## Verification (run on the live stack)

`python scripts/dev/verify_phase1.py --faults`:

```
1. RED metrics per service (from OTel traces)
  [PASS] api-gateway       - rate=3.3 req/s p50=22.1 p95=46.7 p99=49.3 ms err=0.0%
  [PASS] order-service     - rate=3.4 req/s p50=18.4 p95=38.4 p99=47.7 ms err=0.0%
  [PASS] inventory-service - rate=3.4 req/s p50=5.3  p95=9.8  p99=20.4 ms err=0.0%
  [PASS] payment-service   - rate=3.4 req/s p50=3.8  p95=8.5  p99=9.7  ms err=0.0%
2. Service graph discovered from traces
  [PASS] api-gateway -> order-service
  [PASS] inventory-service -> inventory-db
  [PASS] order-service -> inventory-service
  [PASS] order-service -> payment-service
3. Connection pool metrics
  [PASS] hikaricp_connections_max{inventory-service} - max=10.0
4. Real faults through the CausalOps API (45s baseline vs 45s under fault)
  [PASS] DB_LATENCY on inventory-db                     - DB client p95: 1.9 -> 487.5 ms
  [PASS] SERVICE_LATENCY on order-service               - order p95: 23.2 -> 737.5 ms
  [PASS] ERROR_RATE on payment-service                  - payment error %: 0.0 -> 48.1
  [PASS] SERVICE_FAILURE on inventory-service           - order error % (propagated): 0.0 -> 100.0
  [PASS] NETWORK_LATENCY on payment-service             - order p95 (slow link): 17.9 -> 737.5 ms
  [PASS] CONNECTION_POOL_SATURATION on inventory-service - Hikari pending: 0.0 -> 6.0
RESULT: ALL CHECKS PASSED
```

Other checks:
- `causalops-api` starts cleanly: Flyway applied V1 and V2 to a fresh schema.
- Logs from all 4 services are in Loki (`service_name` label), and traces from all 4 are in Tempo.
- Chaos endpoint: no token → 401, wrong token → 401, and it is not reachable from the host.
- An 8 s fault expired after 8.4 s (status `EXPIRED`), and its Toxiproxy toxic was removed (0 toxics on all 5 links).
- Faults on targets with no real mechanism (`auth-gateway`) return 400 instead of being faked.

## How to verify yourself

```bash
docker compose up -d
```

```bash
python scripts/dev/verify_phase1.py --faults
```

Open Grafana at http://localhost:3001 (anonymous viewer, or admin/admin) → Dashboards → CausalOps → "Service RED and Dependencies". Inject a fault and watch it move:

```bash
curl -X POST localhost:8080/api/faults -H "Content-Type: application/json" -d "{\"type\":\"DB_LATENCY\",\"target\":\"inventory-db\",\"severity\":\"HIGH\",\"durationSeconds\":120,\"parameters\":{\"latencyMs\":400}}"
```

## Known limitations carried into Phase 2

- The backend still writes **synthetic** rows to `telemetry_snapshots`, and the UI still reads them. Phase 2 replaces this with Prometheus ingestion.
- The service graph also shows `user` (external callers) and `unknown` (calls that never reached a server, for example during restarts). Phase 2's topology sync treats these as non-service nodes.
- `auth-gateway` (added by migration V2) has no real service behind it. Phase 2 makes the topology come from discovery, so it will disappear unless a real service exists.
- `dataset_generator` still defaults to `localhost:8081` for traffic. Phase 3 rewrites it on top of real telemetry.
- Image builds now use a BuildKit Maven cache, so later rebuilds don't re-download dependencies.

## Files

- **Modified:** `.env.example`, `docker-compose.yml`, `docker-compose.prod.yml`, `infrastructure/{otel,prometheus,tempo,loki,postgres}/*`, `backend/causalops-api/{CausalOpsApplication.java, service/CausalOpsService.java, application.yml}`, and for each of the 4 services `Dockerfile`, `pom.xml` and `DemoApplication.java`.
- **New:** `.gitattributes`, `backend/.../service/FaultInjector.java`, `services/*/src/main/java/com/causalops/demo/{ChaosState,ChaosController,ChaosRequest,ChaosExtension,DownstreamErrors}.java`, `services/inventory-service/.../PoolSaturator.java`, `services/*/src/main/resources/application.yml`, `infrastructure/{grafana,loadgen,toxiproxy}/`, `scripts/dev/{reset_db.sh,verify_phase1.py}`, this report.
- Pre-existing uncommitted changes that are not mine: `.gitignore`, `package.json`, `.vscode/`.

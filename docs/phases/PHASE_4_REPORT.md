# Phase 4 Report: Tiered Auto-Remediation with Real Executors

Date: 2026-10-02 · Status: **complete, awaiting your verification** · Nothing committed or pushed.

## Goal

Close the loop. Once an incident's root cause is known, CausalOps proposes fixes and lets a tiered policy decide whether each runs on its own, waits for a human, or is blocked. It executes the fix through real executors (Docker Engine API, Kubernetes API, signed webhooks) and verifies recovery on the measurements that follow. A reversible change that did not help is rolled back, and the incident escalates to a human when nothing safe is left. MTTR and downtime are measured from real timestamps.

## How the loop works

```
RCA stored ─► RootCauseIdentified ─► Recommender ─► RemediationPolicy ─┬─ AUTO ──────► executor ─► VERIFYING
                                     (top-2 causes x                   ├─ APPROVAL ──► operator approves ─┘      │
                                      enabled catalog)                 └─ BLOCKED ───► next proposal / escalate  │
                                                                                                               ▼
       next ingest cycles: target + affected services within SLO and not flagged by the gate, N samples in a row
            ├─ yes ─► VERIFIED ─► incident MITIGATED ─► detector confirms recovery ─► RESOLVED
            └─ deadline ─► FAILED ─► rollback (if reversible) ─► re-plan: next action, or MANUAL_INTERVENTION
```

### Recommender (`remediation/Recommender`)
Proposals come from the environment's **action catalog** (config, not code). An action qualifies when:
- its executor is enabled;
- it applies to the candidate's kind (service, database or link);
- it addresses a metric that actually deviated.

**Score = RCA confidence × learned success rate × expected benefit.**
- **Success rate:** the Laplace-smoothed share of past executions of that action, for that kind of deviation in this environment, that passed verification. It starts at 50% and learns from outcomes.
- **Expected benefit:** the SCM counterfactual of restoring that component to baseline, i.e. the entry-point latency and errors it would avoid. It is stored with the RCA for the top cause and computed by the engine for the runner-up.

Each proposal carries a plain-language rationale built from these numbers.

### Policy (`remediation/RemediationPolicy`): 15 rules

| Safety rules: block even with an approval | Autonomy rules: decide AUTO vs APPROVAL |
|---|---|
| KILL_SWITCH_OFF (global env var plus per environment) | TIER_WITHIN_AUTONOMY (`autoExecuteMaxTier`, default 1) |
| ENVIRONMENT_CALIBRATED | ENVIRONMENT_ACTIVE (models passed the Phase 3 quality gates) |
| EXECUTOR_ENABLED | RCA_CONFIDENCE (≥ `minRcaConfidence`) |
| CHANGE_IS_UNDOABLE_OR_STATELESS | TELEMETRY_FRESH |
| RECOMMENDATION_FRESH (TTL) | COUNTERFACTUAL_VALID (validity PASS) |
| NOT_ALREADY_TRIED (executed or rejected for this incident) | TARGET_COOLDOWN |
| TARGET_NOT_BUSY (plus a database lock: one change in flight per target) | AUTO_BUDGET (automatic actions per hour) |
| | BLAST_RADIUS (see below) |

Every evaluation is stored with the recommendation and in the audit log.

**Blast radius, measured, with one design change.** The plan sketched blast radius as the share of user traffic through the target. Measured on the reference system, every service carries 100% of the gateway's traffic: it is a chain, and so are many real systems. That rule would demand approval for every action. The rule now measures **marginal** harm: the share of monitored components that depend on the target (transitively, the target included) **and are still healthy**. Restarting a root cause whose callers are already degraded disrupts nothing new; touching a healthy component that much of the system depends on needs a human. Default maximum: 50%.

### Executors (`ml/engine/executors/`, served by the AI engine)

| Executor | Operations | Rollback | Scope enforcement |
|---|---|---|---|
| Docker Engine API (mounted socket, plain HTTP over UDS) | `restart`, `update_resources` (CPU/memory × factor) | `update_resources` restores the previous limits; a restart changes no persistent state | only containers labelled with the configured compose project |
| Kubernetes API (ServiceAccount token or configured URL/token) | `rollout_restart`, `scale` (+delta, capped), `update_resources` | `scale` restores the replica count; `update_resources` restores resources | only deployments in the configured namespace |
| Webhook (runbooks) | `runbook` | if the receiver returns `rollback_state`, rollback posts it back | HMAC-SHA256 over `timestamp.body` (`X-CausalOps-Signature`, `X-CausalOps-Timestamp`) |

- **Access control:** the executor endpoints require `X-Engine-Token`, the shared `ENGINE_INTERNAL_TOKEN`. Without the token they are disabled (503). The generic `/api/engine/**` proxy refuses them (403), so only the remediation service (policy, approvals, audit) can drive them.
- **Dry run:** `dryRun` executes nothing and records what would have happened.

### Persistence (Flyway V6)
- `remediation_recommendations`, `remediation_approvals` and `remediation_executions`, the last with verification evidence and rollback state.
- A unique index allows only one change in flight per target.
- `audit_log` is append-only (a trigger rejects UPDATE/DELETE). It records who did what and why: policy evaluations, approvals and rejections, executions, verdicts, rollbacks, autonomy changes and escalations.

### API (`/api/remediation`)
- `GET recommendations`, `POST recommendations/{id}/approve`, `POST recommendations/{id}/reject`, `POST incidents/{id}/plan`.
- `GET executions`, `GET executions/{id}`, `POST executions/{id}/rollback`.
- `GET policy`, `PUT autonomy` (tier, kill switch, dry run; audited).
- `GET audit`.
- `GET metrics`: per incident MTTD (vs the injected fault start, where known), time to mitigation, MTTR and downtime (seconds in which an entry service violated its SLO, from stored samples), summarised by how each incident was handled.

### Removed
The journal-based legacy endpoints in the AI engine (`/remediation/*`, `/incidents*`, `/system/health`, `/observability/metrics`) are gone. They wrote JSONL to a read-only mount (Phase 3 noted the resulting 500s), returned canned executor strings, and served a seeded fake incident.

### Configuration
- `remediation` section in the environment config. Defaults: autonomy tier 1, all executors **disabled** until configured. Catalog: Docker restart (tier 1) and raise-limits (tier 2); K8s rollout-restart and scale-out (tier 1) and raise-resources (tier 2); database and network runbooks (tier 3).
- The bootstrapped environment enables Docker for `CAUSALOPS_DOCKER_PROJECT` (default `causalops`).
- Compose:
  - the engine gets the Docker socket and the token;
  - the reference services get real CPU/memory limits (1 CPU, 1 GiB, heap 60% of it) so `update_resources` has something real to raise and restore;
  - prod compose requires the token.

Audit items addressed: **H1, H2** (real executors, real verification), **C6** (no seeded/fake remediation state), **M3, M4, M5, M15**, **M2** (remediation).

## Verification

RESULTS_PLACEHOLDER

## Honest limitations

- **Kubernetes and webhook executors are tested only against in-process fakes** of the Kubernetes API and a runbook receiver (requests, scope, rollback, signatures). The reference system runs on Docker, and no cluster was available here.
- **What a restart fixes in the reference system.** The injected service faults live in the service's memory, so a restart genuinely clears them, like a wedged process or leaked connections in real systems. Faults outside the process (Toxiproxy DB and network delay) cannot be fixed by any enabled executor here. Those incidents escalate to a human, which is the correct outcome. Enabling a database/network runbook webhook gives them a path.
- **The control comparison uses a fault that ends after a fixed time** as a stand-in for a human fix, so the absolute MTTR difference depends on that choice. The meaningful number is the auto-remediated MTTR, and the fact that the fault was still scheduled to run when the incident resolved.
- **The success-rate prior is 50%** until real outcomes accumulate. Each environment learns it from its own verified executions.
- **No user authentication yet** (Phase 6). Approvals record the name the caller gives. The audit log is append-only, but identities are not yet authenticated.

## How to verify

```bash
python scripts/dev/verify_phase4.py
```
Optionally add `--database` (a DB fault that ends in escalation).

Tests:
- `mvn clean test` in `backend/causalops-api`.
- `python -m pytest tests ai-engine/tests`.

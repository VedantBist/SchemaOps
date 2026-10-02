# Phase 3 Report: Environment-Agnostic, Self-Calibrating ML Core

Date: 2026-10-02 · Status: **complete, awaiting your verification** · Nothing committed or pushed.

## Goal

Replace every heuristic, replayed or formula-trained model in the serving path with models the platform **learns from the monitored environment's own real telemetry**. This covers anomaly detection, root-cause analysis, failure forecasting and counterfactual simulation. The platform then validates those models against labelled incidents, promotes them through a champion/challenger gate, and retrains them weekly. Nothing here is specific to the reference system: topology, variables and thresholds all come from the environment's discovered graph and stored measurements.

## What changed

### New engine package `ml/engine/` (environment-agnostic)

| Module | Responsibility |
|---|---|
| `store.py` | Postgres access: environment, discovered topology, telemetry, edge telemetry, recorded faults, calibration runs, model registry. |
| `topology.py` | The environment's nodes and edges as loaded from Postgres (any size, any names). |
| `window.py` | Aligns stored measurements into a time-indexed variable matrix (`node\|metric`, `client->server\|gap`). It uses **past-only** windows and the same layout for calibration and live inference. |
| `baselines.py` | Robust per-variable baselines (median/MAD), with hour-of-day profiles once ≥ 24 h exist. |
| `anomaly.py` | Anomaly gate: per-variable thresholds calibrated to a target false-positive rate on undisturbed data, N consecutive samples, and Isolation Forest corroboration. |
| `scm.py` | Topology-constrained lagged structural causal model. Each variable gets Ridge regression with `positive=True` over its allowed parents (callee latency, link gap, own pool/DB/rate, lagged self). It uses a block-bootstrap ensemble and an acyclic same-step structure. |
| `counterfactual.py` | A real abduction → action → prediction rollout. Exogenous noise is abducted from observations, the unit is held at baseline, and the SCM is rolled forward, clamped to physical limits (latency ≥ 0). Uncertainty comes from the ensemble, with validity checks (identical pre-intervention, unreachable nodes unchanged). |
| `rca.py` | Ranks services, databases and call links using five measured signals: counterfactual attribution (how much of the others' deviation disappears when the unit is restored, discounted by how much it is explained by others), unexplained residual energy at onset, anomaly, onset precedence, and personalized PageRank. Weights come from the environment config. |
| `forecast.py` | Failure forecast per service and horizon. The calibrated logistic model is used **only** if its held-out AUC ≥ 0.7; otherwise it falls back to trend extrapolation, and the stored forecast says which method produced it. |
| `episodes.py` | Labelled incidents from recorded `fault_injections`. Ramps are merged, and overlapping (concurrent) episodes are excluded from single-root accuracy. |
| `calibrate.py` | The calibration job. Learning-window checks, leave-one-episode-out RCA evaluation, held-out gate false-positive rate, quality gates, champion/challenger on the same episodes, and a registry entry with a SHA-256 checksum. |
| `pipeline.py` | Live `evaluate` (gate plus forecasts) and incident `analyse` (RCA plus counterfactual) with the current champion. |

### AI engine HTTP (`ai-engine/app/engine_api.py`)
`POST /calibration/run` (202), `GET /calibration/runs`, `GET /models`, `POST /pipeline/evaluate`, `POST /pipeline/rca`, `POST /counterfactual`. Errors use real status codes: 404 unknown environment, 409 no calibrated model yet, 422 invalid input. `/health` and `/ready` check the database and model store.

**Removed from serving:**
- the heuristic `app/prediction/baseline.py`;
- the replayed `app/simulation/propagate.py` (EXP-015 fallback);
- `app/rca/{classifier,scorer}.py`;
- the bundled `tg_v1` model copies under `ai-engine/app/models/`.

The originals in `ml/models/` are untouched and labelled as the archived synthetic benchmark (`ml/models/README.md`).

### Backend (Spring)
- **Flyway V4:** `edge_snapshots`, `calibration_runs`, `model_registry` (at most one champion per environment), lifecycle columns, and model/kind columns on analyses. Pre-Phase-3 synthetic incidents, predictions and simulations are deleted.
- **Flyway V5:** widens `methodology` to text and root-cause names to 200 characters. Link root causes are named `client->server`, and the engine's methodology description exceeded the old 120-character limit.
- **`IncidentDetector`** (replaces `SloBreachDetector`) opens incidents from either an SLO breach or the calibrated anomaly gate. Gate-only incidents start at MEDIUM and escalate on a breach.
- **`PipelineCoordinator`** runs live evaluation every ingest cycle for calibrated environments. It stores anomaly scores and forecasts, then schedules RCA after `rcaDelaySeconds`, retrying with backoff.
- **`IncidentAnalysisService`** stores the engine's ranked candidates, evidence and counterfactual, and sets RCA_IDENTIFIED/RCA_COMPLETED.
- **`CalibrationService`** drives LEARNING → CALIBRATED/ACTIVE. It calibrates when the learning window completes (or on demand once `minLearningMinutes` exist) and retrains every `retrainIntervalDays`. Endpoints: `GET /api/calibration/status`, `POST /api/calibration/run`, `GET /api/models`.
- **Edge telemetry:** per-link request rate, errors and client/server p95 from the service graph.

### Measurement-consistency fix found during verification
The first end-to-end run ranked `inventory-service` above `inventory-db` for an injected DB delay. The raw telemetry showed why. Service latency was measured at **p99** but database latency at **p95**. When a fault starts, the slow tail moves p99 one scrape before p95, so the service *appeared* to degrade 5 s before its database, and the SCM read that artefact as causation.
- The DB template is now `dbLatencyP99`, the same quantile as service latency. Stored configs are migrated: an unmodified default mapping adopts the new template, and a customised one keeps its query under the new key with a warning.
- **Changing any metric definition now restarts learning:** status goes back to LEARNING and `learning_started_at` resets. Calibration and the learning-time counter use only telemetry recorded since then. A champion trained before the restart is replaced, not compared, because models learned on one measurement are not valid for another. This applies to every environment, not just the reference one.

Audit items addressed: **C1, C3, C4** (no heuristic/replayed/formula models in serving), **M1, M2** (engine), **M6, M7, M8, M16, M17**, **H7**, **H12** (validation computed, not asserted).

## Verification

**Backend:** `mvn clean test` gives **16/16** (Testcontainers PostgreSQL). New tests cover anomaly-gate detection, storing engine RCA output (link root cause, long methodology) and the learning restart when metric definitions change.

**Engine:** `pytest tests/engine` gives **14/14**. They cover:
- SCM fit and rollout;
- a counterfactual that changes when the coefficients are zeroed;
- RCA on simulated faults;
- forecaster skill gating;
- concurrent episodes;
- calibration, champion/challenger and stale-champion replacement;
- the HTTP contract.

The full Python suite passes, except one test that needs a `git` binary.

**Live stack:** `python scripts/dev/verify_phase3.py --calibrate --incident` gave 29/30. A follow-up `--calibrate` run after 20 more minutes of quiet telemetry gave **23/23** and set the environment **ACTIVE**.

| Measured on this environment (data since the learning restart, 14:23–16:07 UTC) | Result |
|---|---|
| Labelled single-root incidents (fresh campaign, no overlap) | 17 |
| Detection recall / median detection delay | 1.0 / 21.7 s |
| Gate false-positive rate on held-out quiet data | 0.0 |
| RCA top-1 / top-2 (leave-one-episode-out) | **0.76 / 1.0** |
| top-1 by fault type | error rate 3/3, service failure 2/2, pool saturation 1/1, service latency 4/5, DB latency 2/3, network latency 1/3 |
| Live DB fault (INC-1034): detection / RCA | anomaly gate after 18 s (before any SLO breach); root cause **inventory-db**; counterfactual stored; resolved after the fault stopped |
| Forecaster | no skill on held-out incidents (AUC 0.54 / 0.47), so trend extrapolation is used, and each forecast says so |
| Champion | reference-20261002T160732Z, SHA-256 checksum; weekly retrain scheduled 2026-10-09 |

**Effect of the p95/p99 fix:** before it, the live DB fault was attributed to `inventory-service`. After it, the same check names `inventory-db`, and DB-latency top-1 in calibration went from 1/2 to 2/3.

**One run stayed CALIBRATED for a good reason.** The first calibration after the restart had too few undisturbed samples (it needs 200) for a held-out false-positive estimate. It correctly refused to promote to ACTIVE. Twenty quiet minutes later, the estimate existed (0.0) and the gates passed.

## Honest limitations

- **Open issue: SCM held-out fit got worse after the learning restart.** Median holdout R² was 0.87 on the old (p95) data, 0.62 after the restart, and **-1.7 in the final run**. The counterfactual on the recorded DB fault now reports validity **WARN** ("equations on the propagation path fit held-out data poorly"). Its direction is still right (removing the DB delay lowers gateway latency, and unreachable nodes are unchanged), but its magnitudes should not be trusted until this is fixed.
  - **Likely cause, not yet confirmed:** the holdout is the latest slice of data, and in the final run that slice is the 30 quiet minutes. There R² divides by almost no variance, so small errors give large negative values.
  - **Second contributor:** only about 100 minutes of data exist since the restart.
  - **Gap in the gates:** RCA accuracy and detection passed, but the quality gates do not include SCM fit. That should change.
  - I have not changed anything here yet. It needs your go-ahead (see the chat).
- **Network-latency RCA is weakest** (1/3 top-1). The caller is ranked above the link; the correct link is always second.
- **The forecaster has no predictive skill** on this system's abrupt faults, and says so instead of inventing probabilities. Ramped faults are too few to learn from.
- **The model is linear.** Queueing effects (e.g. pool saturation) are only approximated.
- **About 100 minutes of data** since the restart. The default learning window is 24 h; the reference profile uses `minLearningMinutes: 30` for the demo.

## Deviations from the plan

- **New package instead of refactoring the legacy modules.** The plan said to parameterise the ~25 legacy `ml/causal`, `ml/remediation` and `ml/execution` modules. They are built around the synthetic five-node tensor layout, so I wrote `ml/engine/` against the discovered topology instead. The legacy modules are no longer in the serving path. Phase 4 reuses the policy, recommender and orchestration logic from `ml/execution`/`ml/remediation`, adapted to `ml/engine` units.
- **Spring is the single writer of incidents** (the plan said the engine). Detection had to run on Spring's ingest cycle, and one writer avoids races. The engine is stateless per request and writes only calibration runs and the model registry.
- **Tables are added in the phase that first writes them** (calibration/registry in V4; recommendations/executions in Phase 4).
- **The `tg_v1` artifacts are labelled in place, not moved yet.** About 30 legacy research scripts and checksum audits reference `ml/models/...`. The physical move to `ml/models/archive/tg_v1_synthetic/` happens in Phase 7 together with the rewrite of that evidence.
- **Supervised enhancers (RF/GNN)** are not retrained on real data in this phase. With 16–22 labelled episodes they would overfit. The calibration metrics record the episode count so they can be added once operator-confirmed incidents accumulate.

## A mistake I made during this phase

While building the first calibration dataset, I wrongly believed the first chaos campaign had stopped at episode 10, and I started a second campaign. The first had in fact completed, so for about six minutes the two overlapped (13:45–13:51 UTC) and produced episodes with two simultaneous root causes. I stopped the second campaign and cleared its faults. I then made the problem detectable instead of hiding it: `episodes.py` now marks overlapping episodes **concurrent** and excludes them from single-root accuracy (they were shown as CONCURRENT in the first verify run). That dataset is no longer used. The metric-definition change restarted learning, and the numbers above come from a fresh, non-overlapping campaign.

## Files

Phase 3 adds:
- `ml/engine/` (13 modules);
- `ai-engine/app/engine_api.py`;
- calibration, pipeline and detector classes under `backend/causalops-api/.../{calibration,incident,telemetry}`;
- Flyway `V4__calibration_and_links.sql` and `V5__wider_analysis_columns.sql`;
- `scripts/calibration/` and `scripts/dev/verify_phase3.py`;
- `tests/engine/`;
- `ml/models/README.md`.

**Removed (staged in git):**
- `ai-engine/app/models/*` (copies of the archived `tg_v1` models; the originals in `ml/models/` are untouched);
- `ai-engine/app/{prediction,rca,simulation}`;
- `SloBreachDetector.java`;
- the obsolete tests for the removed endpoints.

**Note:** the working tree also contains the uncommitted, undeployed **Phase 4** code (remediation package, executors, V6, compose changes, `scripts/dev/verify_phase4.py`, `docs/phases/PHASE_4_REPORT.md`). That code is not running in the stack. Leave it out of a Phase 3 commit if you want to commit phase by phase.

## How to verify

```bash
python scripts/dev/verify_phase3.py --incident
```
`--calibrate` additionally triggers a calibration run first. The script checks:
- calibration metrics and quality gates;
- registry and lifecycle;
- live anomaly scores and forecasts;
- counterfactual validity on a recorded fault;
- a real injected DB fault, followed through detection → automatic RCA → resolution;
- that the serving code no longer uses any archived dataset.

Tests:
- `mvn clean test` in `backend/causalops-api`.
- `python -m pytest tests/engine ai-engine/tests tests`.

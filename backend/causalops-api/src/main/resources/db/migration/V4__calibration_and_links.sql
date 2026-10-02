-- Phase 3: per-link telemetry, calibration runs, model registry and engine outputs.

-- ── Results produced before Phase 3 came from synthetic telemetry and the tg_v1 models ──
-- Incidents without an environment predate measured telemetry (Phase 2); predictions and
-- simulations were produced by heuristics. None of them describe the real system.
DELETE FROM incidents WHERE environment_id IS NULL;
DELETE FROM predictions;
DELETE FROM simulations;

-- ── Per-link measurements: client-side vs server-side latency of each call edge ──
-- The gap between them is time spent on the network or queueing between two services,
-- which is how a slow link is told apart from a slow service.
CREATE TABLE edge_snapshots (
    id             bigserial PRIMARY KEY,
    environment_id uuid NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
    client         varchar(80) NOT NULL,
    server         varchar(80) NOT NULL,
    captured_at    timestamptz NOT NULL,
    client_p95     double precision,
    server_p95     double precision,
    request_rate   double precision,
    error_rate     double precision
);
CREATE INDEX edge_snapshots_env_time ON edge_snapshots (environment_id, captured_at DESC);

-- ── Environment lifecycle detail ────────────────────────────────────────────────
ALTER TABLE environments
    ADD COLUMN calibrated_at timestamptz,
    ADD COLUMN status_reason text;

-- ── Calibration runs (written by the AI engine) ─────────────────────────────────
CREATE TABLE calibration_runs (
    id             uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    environment_id uuid NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
    mode           varchar(20) NOT NULL,
    trigger        varchar(40) NOT NULL,
    status         varchar(20) NOT NULL DEFAULT 'RUNNING',
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    data_from      timestamptz,
    data_to        timestamptz,
    model_version  varchar(60),
    promoted       boolean,
    decision       text,
    quality_passed boolean,
    metrics        jsonb NOT NULL DEFAULT '{}'::jsonb,
    error          text,
    CONSTRAINT calibration_runs_mode_check CHECK (mode IN ('INITIAL', 'RETRAIN', 'MANUAL')),
    CONSTRAINT calibration_runs_status_check CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED'))
);
CREATE INDEX calibration_runs_env_time ON calibration_runs (environment_id, started_at DESC);

-- ── Model registry (written by the AI engine): one champion per environment ─────
CREATE TABLE model_registry (
    id                 uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    environment_id     uuid NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
    version            varchar(60) NOT NULL,
    status             varchar(20) NOT NULL,
    calibration_run_id uuid REFERENCES calibration_runs(id) ON DELETE SET NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    promoted_at        timestamptz,
    retired_at         timestamptz,
    data_from          timestamptz,
    data_to            timestamptz,
    artifact_path      text NOT NULL,
    checksum           varchar(64) NOT NULL,
    metrics            jsonb NOT NULL DEFAULT '{}'::jsonb,
    components         jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT model_registry_env_version_key UNIQUE (environment_id, version),
    CONSTRAINT model_registry_status_check CHECK (status IN ('CHAMPION', 'CHALLENGER', 'REJECTED', 'RETIRED'))
);
CREATE UNIQUE INDEX model_registry_one_champion ON model_registry (environment_id) WHERE status = 'CHAMPION';

-- ── Engine outputs stored by the platform API ────────────────────────────────────
ALTER TABLE predictions
    ADD COLUMN environment_id uuid REFERENCES environments(id) ON DELETE CASCADE,
    ADD COLUMN model_version  varchar(60),
    ADD COLUMN method         varchar(40);
CREATE INDEX predictions_env_time ON predictions (environment_id, created_at DESC);

ALTER TABLE simulations
    ADD COLUMN environment_id uuid REFERENCES environments(id) ON DELETE CASCADE,
    ADD COLUMN incident_id    uuid REFERENCES incidents(id) ON DELETE CASCADE,
    ADD COLUMN model_version  varchar(60),
    ADD COLUMN result         jsonb;

ALTER TABLE root_cause_analyses
    ADD COLUMN model_version varchar(60),
    ADD COLUMN candidate_kind varchar(20);

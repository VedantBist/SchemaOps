-- Phase 2: environments, discovered topology and measured telemetry.
--
-- Before this migration, services/dependencies were hand-seeded and telemetry_snapshots
-- was produced by a formula in CausalOpsService.collect(). Those rows describe a
-- system that never existed, so they are removed here. From now on:
--   * services and dependencies are discovered from the OTel service graph (TopologySync),
--   * telemetry_snapshots holds values measured in Prometheus (TelemetryIngestor),
--   * every row belongs to an environment (one per monitored system).

CREATE TABLE environments (
    id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                varchar(80) UNIQUE NOT NULL,
    status              varchar(20) NOT NULL DEFAULT 'LEARNING',
    config              jsonb NOT NULL,
    learning_started_at timestamptz NOT NULL DEFAULT now(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT environments_status_check CHECK (status IN ('LEARNING', 'CALIBRATED', 'ACTIVE', 'DISABLED'))
);

-- ── Remove synthetic seed data and formula telemetry ────────────────────────────
DELETE FROM dependencies;
DELETE FROM services;
TRUNCATE telemetry_snapshots;

-- ── services: discovered per environment, measured values are nullable ─────────
ALTER TABLE services DROP CONSTRAINT services_name_key;
ALTER TABLE services
    ADD COLUMN environment_id uuid NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
    ADD COLUMN kind           varchar(20) NOT NULL DEFAULT 'service',
    ADD COLUMN source         varchar(20) NOT NULL DEFAULT 'discovered',
    ADD COLUMN request_rate   double precision,
    ADD COLUMN first_seen_at  timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN last_seen_at   timestamptz,
    ADD COLUMN last_sample_at timestamptz,
    ADD CONSTRAINT services_env_name_key UNIQUE (environment_id, name),
    ADD CONSTRAINT services_kind_check CHECK (kind IN ('service', 'database', 'messaging', 'external')),
    ADD CONSTRAINT services_source_check CHECK (source IN ('discovered', 'manual'));
ALTER TABLE services
    ALTER COLUMN type SET DEFAULT 'service',
    ALTER COLUMN status SET DEFAULT 'unknown',
    ALTER COLUMN baseline_latency DROP NOT NULL,
    ALTER COLUMN current_latency DROP NOT NULL,
    ALTER COLUMN error_rate DROP NOT NULL,
    ALTER COLUMN error_rate DROP DEFAULT;

-- ── dependencies: discovered call edges with observed traffic ───────────────────
ALTER TABLE dependencies DROP CONSTRAINT dependencies_source_service_target_service_key;
ALTER TABLE dependencies
    ADD COLUMN environment_id  uuid NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
    ADD COLUMN source          varchar(20) NOT NULL DEFAULT 'discovered',
    ADD COLUMN connection_type varchar(20),
    ADD COLUMN call_rate       double precision,
    ADD COLUMN failed_rate     double precision,
    ADD COLUMN first_seen_at   timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN last_seen_at    timestamptz,
    ADD CONSTRAINT dependencies_env_edge_key UNIQUE (environment_id, source_service, target_service),
    ADD CONSTRAINT dependencies_source_check CHECK (source IN ('discovered', 'manual'));

-- ── telemetry_snapshots: measured values; a metric that was not observed stays NULL ─
ALTER TABLE telemetry_snapshots
    ADD COLUMN environment_id uuid NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
    ADD COLUMN pool_pending   double precision,
    ALTER COLUMN p50_latency DROP NOT NULL,
    ALTER COLUMN p95_latency DROP NOT NULL,
    ALTER COLUMN p99_latency DROP NOT NULL,
    ALTER COLUMN error_rate DROP NOT NULL,
    ALTER COLUMN request_rate DROP NOT NULL,
    ALTER COLUMN anomaly_score DROP NOT NULL,
    ALTER COLUMN anomaly_score DROP DEFAULT;
DROP INDEX telemetry_service_time;
CREATE INDEX telemetry_env_service_time ON telemetry_snapshots (environment_id, service_name, captured_at DESC);
CREATE INDEX telemetry_env_time ON telemetry_snapshots (environment_id, captured_at DESC);

-- ── incidents: sequential keys (no random collisions) and recorded evidence ──────
CREATE SEQUENCE incident_seq START WITH 1001;
ALTER TABLE incidents
    ALTER COLUMN incident_key SET DEFAULT ('INC-' || nextval('incident_seq')),
    ADD COLUMN environment_id   uuid REFERENCES environments(id) ON DELETE CASCADE,
    ADD COLUMN detection_source varchar(60),
    ADD COLUMN evidence         jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN updated_at       timestamptz NOT NULL DEFAULT now();
CREATE INDEX incidents_env_status ON incidents (environment_id, status);

-- ── fault injections are recorded per environment too ─────────────────────────
ALTER TABLE fault_injections ADD COLUMN environment_id uuid REFERENCES environments(id) ON DELETE CASCADE;

-- log_events and trace_summaries were never written; logs and traces are read live from Loki and Tempo.
DROP TABLE log_events;
DROP TABLE trace_summaries;

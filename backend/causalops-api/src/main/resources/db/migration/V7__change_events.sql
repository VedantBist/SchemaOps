-- Changes to the monitored system (deployments, restarts, maintenance, remediation actions).
-- They disturb telemetry without being faults: calibration keeps them out of "normal" behaviour and
-- out of the false-positive evaluation, and does not treat them as labelled incidents.
CREATE TABLE change_events (
    id             uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    environment_id uuid NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
    kind           varchar(20) NOT NULL CHECK (kind IN ('DEPLOYMENT', 'RESTART', 'CONFIG', 'MAINTENANCE', 'REMEDIATION', 'OTHER')),
    target         varchar(200),
    started_at     timestamptz NOT NULL,
    ended_at       timestamptz,
    source         varchar(120) NOT NULL,
    description    text NOT NULL,
    reference_id   uuid,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);
CREATE INDEX change_events_env_time ON change_events (environment_id, started_at);

-- ULPF self-calibrating quality: per-source quality series, learned baselines, pipeline incidents with
-- their timeline and actions, re-normalization jobs with the previous versions of every changed event,
-- per-source clock correction, and log-pipeline fault scenarios.

ALTER TABLE ulpf_events ADD COLUMN device_time timestamptz;       -- time stated by the device (before correction)
ALTER TABLE ulpf_events ADD COLUMN revision int NOT NULL DEFAULT 1; -- bumped by every re-normalization
CREATE INDEX ulpf_events_source_status ON ulpf_events (source_id, status, received_at);

ALTER TABLE log_sources ADD COLUMN skew_ms bigint;                -- applied clock correction (received - device time)
ALTER TABLE log_sources ADD COLUMN skew_applied_at timestamptz;

-- One row per source and bucket (default 20 s), aggregated from ulpf_events by the monitor.
CREATE TABLE ulpf_quality (
    source_id     varchar(120) NOT NULL,
    bucket        timestamptz NOT NULL,
    events        int NOT NULL,
    normalized    int NOT NULL,
    partial       int NOT NULL,
    quarantined   int NOT NULL,
    lossless_ok   int NOT NULL,
    fill_avg      real,
    skew_ms       bigint,                 -- median (received - device time) of events carrying a device time
    pack          varchar(100),
    PRIMARY KEY (source_id, bucket)
);
CREATE INDEX ulpf_quality_bucket ON ulpf_quality (bucket DESC);

-- Learned normal behaviour per source and metric (robust median / MAD, calibrated threshold).
CREATE TABLE source_baselines (
    source_id   varchar(120) NOT NULL,
    metric      varchar(20) NOT NULL,      -- fill · normalized · volume
    median      double precision NOT NULL,
    scale       double precision NOT NULL,
    threshold   double precision NOT NULL,
    samples     int NOT NULL,
    hourly      jsonb,                     -- hour-of-day medians once a full day of history exists
    fitted_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, metric)
);

CREATE SEQUENCE ulpf_incident_seq START WITH 1001;
CREATE TABLE ulpf_incidents (
    id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    incident_key  varchar(20) NOT NULL UNIQUE DEFAULT ('LOG-' || nextval('ulpf_incident_seq')),
    kind          varchar(20) NOT NULL CHECK (kind IN ('PARSER_DRIFT', 'SOURCE_SILENT', 'CLOCK_SKEW', 'PIPELINE', 'SECURITY_CORRELATION')),
    source_id     varchar(120),
    severity      varchar(10) NOT NULL CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    status        varchar(12) NOT NULL CHECK (status IN ('OPEN', 'MITIGATING', 'MITIGATED', 'RESOLVED', 'ESCALATED')),
    title         varchar(200) NOT NULL,
    summary       text NOT NULL,
    evidence      jsonb NOT NULL DEFAULT '{}'::jsonb,
    onset_at      timestamptz,             -- first affected event (measured, not the detection time)
    detected_at   timestamptz NOT NULL DEFAULT now(),
    mitigated_at  timestamptz,
    resolved_at   timestamptz,
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ulpf_incidents_open ON ulpf_incidents (status, kind, source_id);

CREATE TABLE ulpf_incident_events (
    id           bigserial PRIMARY KEY,
    incident_id  uuid NOT NULL REFERENCES ulpf_incidents(id) ON DELETE CASCADE,
    at           timestamptz NOT NULL DEFAULT now(),
    type         varchar(40) NOT NULL,
    payload      jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX ulpf_incident_events_incident ON ulpf_incident_events (incident_id, at);

CREATE TABLE ulpf_actions (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    incident_id  uuid REFERENCES ulpf_incidents(id) ON DELETE CASCADE,
    action       varchar(30) NOT NULL,     -- PROMOTE_PACK · ROLLBACK_PACK · RENORMALIZE · CLOCK_CORRECTION · RESTART_WORKER
    tier         int NOT NULL,
    status       varchar(12) NOT NULL CHECK (status IN ('PROPOSED', 'APPROVED', 'EXECUTING', 'VERIFYING', 'VERIFIED',
                                                         'FAILED', 'ROLLED_BACK', 'REJECTED', 'BLOCKED', 'DONE')),
    automatic    boolean NOT NULL,
    params       jsonb NOT NULL DEFAULT '{}'::jsonb,
    policy       jsonb NOT NULL DEFAULT '[]'::jsonb,   -- every rule evaluated, with its result
    result       jsonb,
    created_at   timestamptz NOT NULL DEFAULT now(),
    executed_at  timestamptz,
    finished_at  timestamptz,
    decided_by   varchar(120)
);
CREATE INDEX ulpf_actions_incident ON ulpf_actions (incident_id, created_at);

CREATE TABLE renormalization_jobs (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id    varchar(120) NOT NULL,
    incident_id  uuid REFERENCES ulpf_incidents(id) ON DELETE SET NULL,
    pack         varchar(100),
    window_from  timestamptz,
    window_to    timestamptz,
    only_degraded boolean NOT NULL DEFAULT true,
    status       varchar(10) NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'DONE', 'FAILED')),
    requested_by varchar(120) NOT NULL,
    processed    int NOT NULL DEFAULT 0,
    changed      int NOT NULL DEFAULT 0,
    improved     int NOT NULL DEFAULT 0,
    fields_recovered int NOT NULL DEFAULT 0,
    lossless_ok  int NOT NULL DEFAULT 0,
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    started_at   timestamptz,
    finished_at  timestamptz
);

-- The normalized record as it was before each re-normalization ("as parsed by v1 vs v2").
CREATE TABLE ulpf_event_versions (
    uid        varchar(40) NOT NULL,
    revision   int NOT NULL,
    parser     varchar(80) NOT NULL,
    status     varchar(12) NOT NULL,
    fill       real,
    event      jsonb NOT NULL,
    replaced_at timestamptz NOT NULL DEFAULT now(),
    job_id     uuid,
    PRIMARY KEY (uid, revision)
);

CREATE TABLE ulpf_settings (
    key    varchar(60) PRIMARY KEY,
    value  jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by varchar(120)
);
INSERT INTO ulpf_settings (key, value) VALUES
    ('autoExecuteMaxTier', '1'), ('dryRun', 'false'), ('learningMinutes', '20'), ('bucketSeconds', '20'),
    ('consecutiveBuckets', '2'), ('maxFalsePositiveRate', '0.01'), ('zFloor', '4'), ('promotionCooldownMinutes', '10'),
    ('skewThresholdSeconds', '30');

-- Packs repaired automatically after a format change are recorded as such.
ALTER TABLE parser_packs DROP CONSTRAINT parser_packs_origin_check;
ALTER TABLE parser_packs ADD CONSTRAINT parser_packs_origin_check CHECK (origin IN ('BUILTIN', 'STUDIO', 'DRAIN', 'IMPORT', 'REPAIR'));

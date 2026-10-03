-- ULPF outputs and security analytics: export order, sinks and their measured bytes (SIEM cost tiering),
-- Sigma detections, DPDP token map and its audit, signed bundles, compliance runs, benchmarks.

-- A commit-ordered export position: rows are exported by seq once they are older than a short guard.
ALTER TABLE ulpf_events ADD COLUMN seq bigserial;
ALTER TABLE ulpf_events ADD COLUMN inserted_at timestamptz NOT NULL DEFAULT now();
CREATE UNIQUE INDEX ulpf_events_seq ON ulpf_events (seq);

CREATE TABLE ulpf_sinks (
    name        varchar(60) PRIMARY KEY,
    kind        varchar(20) NOT NULL,           -- lake · jsonl · cef · hec · opensearch · kafka
    tier        varchar(10) NOT NULL CHECK (tier IN ('lake', 'siem', 'all')),
    enabled     boolean NOT NULL DEFAULT true,
    tokenize    boolean NOT NULL DEFAULT false, -- DPDP pseudonymisation of exported records
    config      jsonb NOT NULL DEFAULT '{}'::jsonb,
    cursor_seq  bigint NOT NULL DEFAULT 0,
    exported    bigint NOT NULL DEFAULT 0,
    last_ok     timestamptz,
    last_error  text,
    last_error_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO ulpf_sinks (name, kind, tier, enabled, tokenize, config) VALUES
    ('data-lake', 'lake', 'all', true, false, '{"note": "Parquet on MinIO (S3); full fidelity"}'),
    ('siem-tier', 'jsonl', 'siem', true, true, '{"path": "/var/lib/ulpf/export", "note": "what a SIEM would ingest (stand-in file)"}'),
    ('splunk-hec', 'hec', 'siem', false, false, '{"url": "https://splunk.example.local:8088", "token": ""}'),
    ('siem-cef-syslog', 'cef', 'siem', false, false, '{"host": "siem.example.local", "port": 514, "protocol": "tcp"}'),
    ('opensearch', 'opensearch', 'all', false, false, '{"url": "http://opensearch:9200", "index": "ocsf-events"}'),
    ('kafka', 'kafka', 'all', false, false, '{"bootstrap": "kafka:9092", "topic": "ocsf.events"}');

-- Bytes per sink per day; for the SIEM tier also what the full stream would have cost.
CREATE TABLE sink_stats (
    sink        varchar(60) NOT NULL,
    day         date NOT NULL,
    events_in   bigint NOT NULL DEFAULT 0,
    events_out  bigint NOT NULL DEFAULT 0,
    bytes       bigint NOT NULL DEFAULT 0,
    full_bytes  bigint NOT NULL DEFAULT 0,   -- compact JSON of every input event (what untiered export would send)
    errors      bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (sink, day)
);

CREATE TABLE sigma_rules (
    id          varchar(40) PRIMARY KEY,
    title       varchar(200) NOT NULL,
    level       varchar(12) NOT NULL,
    tags        text[] NOT NULL DEFAULT '{}',
    yaml        text NOT NULL,
    enabled     boolean NOT NULL DEFAULT true,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sigma_hits (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    rule_id     varchar(40) NOT NULL,
    rule_title  varchar(200) NOT NULL,
    level       varchar(12) NOT NULL,
    group_key   varchar(300) NOT NULL,
    count       int NOT NULL,
    distinct_count int,
    sources     text[] NOT NULL,
    vendors     text[] NOT NULL,
    sample_uids text[] NOT NULL,
    first_seen  timestamptz NOT NULL,
    last_seen   timestamptz NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sigma_hits_time ON sigma_hits (last_seen DESC);
CREATE INDEX sigma_hits_rule ON sigma_hits (rule_id, group_key, last_seen DESC);

CREATE TABLE pii_tokens (
    token      varchar(80) PRIMARY KEY,
    kind       varchar(12) NOT NULL,
    value_enc  bytea NOT NULL,              -- AES-GCM; key derived from ULPF_PRIVACY_KEY
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE token_audit (
    id        bigserial PRIMARY KEY,
    at        timestamptz NOT NULL DEFAULT now(),
    actor     varchar(120) NOT NULL,
    token     varchar(80) NOT NULL,
    reason    text NOT NULL,
    granted   boolean NOT NULL
);
CREATE OR REPLACE FUNCTION token_audit_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'token_audit is append-only';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER token_audit_no_update BEFORE UPDATE OR DELETE ON token_audit
    FOR EACH STATEMENT EXECUTE FUNCTION token_audit_append_only();

CREATE TABLE ulpf_bundles (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    direction   varchar(8) NOT NULL CHECK (direction IN ('EXPORT', 'IMPORT')),
    purpose     varchar(40) NOT NULL,          -- DATA_DIODE · CERT_IN_EVIDENCE · TAMPER_TEST
    file        varchar(300),
    bytes       bigint,
    events      int,
    sha256      char(64),
    key_id      varchar(80),
    verified    boolean,
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_by  varchar(120) NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE compliance_runs (
    id        bigserial PRIMARY KEY,
    at        timestamptz NOT NULL DEFAULT now(),
    framework varchar(40) NOT NULL,
    passed    int NOT NULL,
    failed    int NOT NULL,
    result    jsonb NOT NULL
);

CREATE TABLE benchmarks (
    id         bigserial PRIMARY KEY,
    at         timestamptz NOT NULL DEFAULT now(),
    kind       varchar(20) NOT NULL,         -- PROCESSING (in-process) · END_TO_END (through intake)
    result     jsonb NOT NULL
);

INSERT INTO ulpf_settings (key, value) VALUES
    ('siemCostPerGbInr', '3000'), ('retentionDays', '180'), ('privacyOnExport', 'true'),
    ('mandatorySources', '[]'), ('ntpMaxOffsetSeconds', '30');

ALTER TABLE ulpf_incidents DROP CONSTRAINT ulpf_incidents_kind_check;
ALTER TABLE ulpf_incidents ADD CONSTRAINT ulpf_incidents_kind_check
    CHECK (kind IN ('PARSER_DRIFT', 'SOURCE_SILENT', 'CLOCK_SKEW', 'PIPELINE', 'SECURITY_CORRELATION'));

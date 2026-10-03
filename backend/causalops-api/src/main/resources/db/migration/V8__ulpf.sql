-- CausalOps ULPF (Universal Log Pre-processing Framework): normalized events, log sources and the
-- raw-vault index. The raw bytes themselves live in the hash-chained vault (ulpf-vault volume);
-- every row here points at its record (segment + offset) and carries its SHA-256 and chain hash.
CREATE TABLE log_sources (
    id           varchar(120) PRIMARY KEY,
    host         varchar(200),
    vendor       varchar(120),
    product      varchar(120),
    format       varchar(60),
    first_seen   timestamptz NOT NULL,
    last_seen    timestamptz NOT NULL,
    last_peer    varchar(64),
    transport    varchar(10),
    events       bigint NOT NULL DEFAULT 0,
    normalized   bigint NOT NULL DEFAULT 0,
    partial      bigint NOT NULL DEFAULT 0,
    quarantined  bigint NOT NULL DEFAULT 0,
    lossless_ok  bigint NOT NULL DEFAULT 0,
    bytes        bigint NOT NULL DEFAULT 0
);

CREATE TABLE ulpf_events (
    uid           varchar(40) PRIMARY KEY,           -- the stream entry id assigned at intake
    source_id     varchar(120) NOT NULL,
    received_at   timestamptz NOT NULL,
    event_time    timestamptz NOT NULL,
    class_uid     int NOT NULL,
    status        varchar(12) NOT NULL CHECK (status IN ('NORMALIZED', 'PARTIAL', 'QUARANTINED')),
    format        varchar(60) NOT NULL,
    product       varchar(120),
    src_ip        inet,
    dst_ip        inet,
    user_name     varchar(200),
    action        varchar(20),
    message       varchar(500),
    lossless      boolean NOT NULL,
    segment       varchar(120) NOT NULL,
    vault_offset  bigint NOT NULL,
    sha256        char(64) NOT NULL,
    chain         char(64) NOT NULL,
    parser        varchar(80) NOT NULL,
    event         jsonb NOT NULL                       -- the OCSF record with unmapped{}, lineage and skeleton
);
CREATE INDEX ulpf_events_received ON ulpf_events (received_at DESC);
CREATE INDEX ulpf_events_source ON ulpf_events (source_id, received_at DESC);
CREATE INDEX ulpf_events_class ON ulpf_events (class_uid, received_at DESC);
CREATE INDEX ulpf_events_src_ip ON ulpf_events (src_ip);
CREATE INDEX ulpf_events_dst_ip ON ulpf_events (dst_ip);
CREATE INDEX ulpf_events_user ON ulpf_events (user_name) WHERE user_name IS NOT NULL;
CREATE INDEX ulpf_events_status ON ulpf_events (status) WHERE status <> 'NORMALIZED';

CREATE TABLE vault_segments (
    id         varchar(120) PRIMARY KEY,
    writer     varchar(64) NOT NULL,
    prev_head  char(64),
    head       char(64),
    records    bigint NOT NULL DEFAULT 0,
    bytes      bigint NOT NULL DEFAULT 0,
    sealed_at  timestamptz,
    status     varchar(10) NOT NULL CHECK (status IN ('OPEN', 'SEALED'))
);

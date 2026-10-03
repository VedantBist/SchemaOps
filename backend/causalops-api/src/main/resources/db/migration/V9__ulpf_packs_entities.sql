-- ULPF parser packs (versioned YAML, one CHAMPION per pack id), source → pack bindings, the pack
-- fill rate per event, and the cross-vendor entity graph (IPs, hostnames, MACs, users and their links).
CREATE TABLE parser_packs (
    id          varchar(80) NOT NULL,
    version     int NOT NULL,
    status      varchar(10) NOT NULL CHECK (status IN ('CHAMPION', 'SHADOW', 'PROPOSED', 'RETIRED')),
    vendor      varchar(120),
    product     varchar(120),
    yaml        text NOT NULL,
    origin      varchar(12) NOT NULL CHECK (origin IN ('BUILTIN', 'STUDIO', 'DRAIN', 'IMPORT')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  varchar(120),
    notes       text,
    test        jsonb,                         -- test-bench result recorded when the version was saved
    PRIMARY KEY (id, version)
);
CREATE UNIQUE INDEX parser_packs_one_champion ON parser_packs (id) WHERE status = 'CHAMPION';

ALTER TABLE log_sources ADD COLUMN pack_id varchar(80);
ALTER TABLE log_sources ADD COLUMN pack_bound_at timestamptz;
ALTER TABLE log_sources ADD COLUMN fill_sum double precision NOT NULL DEFAULT 0;
ALTER TABLE log_sources ADD COLUMN fill_events bigint NOT NULL DEFAULT 0;
ALTER TABLE ulpf_events ADD COLUMN fill real;

CREATE TABLE entities (
    key         varchar(300) PRIMARY KEY,      -- ip:10.20.1.11 · host:lt-arjun-00 · mac:00:1a:.. · user:arjun.mehta
    kind        varchar(8) NOT NULL CHECK (kind IN ('ip', 'host', 'mac', 'user')),
    value       varchar(290) NOT NULL,
    country     char(2),
    is_private  boolean,
    events      bigint NOT NULL DEFAULT 0,
    sources     text[] NOT NULL DEFAULT '{}',
    first_seen  timestamptz NOT NULL,
    last_seen   timestamptz NOT NULL
);
CREATE INDEX entities_kind ON entities (kind, events DESC);
CREATE INDEX entities_value ON entities (value);

CREATE TABLE entity_links (
    a           varchar(300) NOT NULL,
    b           varchar(300) NOT NULL,
    kind        varchar(12) NOT NULL,          -- ip-mac, ip-host, host-mac (same asset) · user-ip (who used it)
    events      bigint NOT NULL DEFAULT 0,
    sources     text[] NOT NULL DEFAULT '{}',
    first_seen  timestamptz NOT NULL,
    last_seen   timestamptz NOT NULL,
    PRIMARY KEY (a, b)
);
CREATE INDEX entity_links_b ON entity_links (b);

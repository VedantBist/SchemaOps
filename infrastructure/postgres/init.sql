-- ==============================================================================
-- CausalOps Phase 7: Production Database Schema
-- This is managed by Flyway: backend migrations live under
-- backend/causalops-api/src/main/resources/db/migration/
-- This file provides the bootstrap schema for Docker-based cold starts.
-- ==============================================================================

-- ── Application service registry ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS services (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        varchar(120) UNIQUE NOT NULL,
    type        varchar(60)  NOT NULL DEFAULT 'microservice',
    status      varchar(30)  NOT NULL DEFAULT 'healthy',
    current_latency  double precision DEFAULT 0,
    baseline_latency double precision DEFAULT 0,
    error_rate       double precision DEFAULT 0,
    updated_at  timestamptz DEFAULT now()
);

-- ── Service dependency graph ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dependencies (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_service  varchar(120) NOT NULL,
    target_service  varchar(120) NOT NULL,
    direction       varchar(30) DEFAULT 'downstream',
    UNIQUE(source_service, target_service)
);

-- ── Application tier tables (monitored services) ───────────────────────────────
CREATE TABLE IF NOT EXISTS products (
    id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sku  varchar(80) UNIQUE NOT NULL,
    name varchar(120) NOT NULL
);
CREATE TABLE IF NOT EXISTS inventory (
    sku      varchar(80) PRIMARY KEY,
    quantity integer NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    status     varchar(30) NOT NULL,
    created_at timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS payments (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id   uuid,
    status     varchar(30) NOT NULL,
    created_at timestamptz DEFAULT now()
);

-- ── CausalOps operational tables ──────────────────────────────────────────────

-- Incidents
CREATE TABLE IF NOT EXISTS incidents (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_key     varchar(80) UNIQUE NOT NULL,
    title            varchar(255) NOT NULL,
    severity         varchar(30) NOT NULL DEFAULT 'MEDIUM',
    status           varchar(30) NOT NULL DEFAULT 'OPEN',
    detection_source varchar(120),
    affected_services jsonb DEFAULT '[]',
    root_cause       varchar(120),
    fault_signature  varchar(120),
    summary          text,
    opened_at        timestamptz DEFAULT now(),
    acknowledged_at  timestamptz,
    resolved_at      timestamptz,
    updated_at       timestamptz DEFAULT now()
);

-- Predictions
CREATE TABLE IF NOT EXISTS predictions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id     uuid REFERENCES incidents(id) ON DELETE SET NULL,
    experiment_id   varchar(80),
    timestamp       timestamptz DEFAULT now(),
    target_service  varchar(120),
    horizon         varchar(30),
    probability     double precision NOT NULL,
    model_name      varchar(120),
    model_version   varchar(60),
    prediction_state varchar(30) DEFAULT 'ACTIVE',
    metadata        jsonb DEFAULT '{}'
);

-- Simulations
CREATE TABLE IF NOT EXISTS simulations (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id     uuid REFERENCES incidents(id) ON DELETE SET NULL,
    experiment_id   varchar(80),
    root_cause      varchar(120),
    intervention    jsonb,
    result_summary  jsonb,
    created_at      timestamptz DEFAULT now(),
    model_version   varchar(60),
    correlation_id  varchar(80)
);

-- Remediation approvals
CREATE TABLE IF NOT EXISTS remediation_approvals (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_id   varchar(120) NOT NULL,
    incident_id         uuid REFERENCES incidents(id) ON DELETE SET NULL,
    approved_by         varchar(120) NOT NULL,
    approved_at         timestamptz DEFAULT now(),
    expires_at          timestamptz,
    warning_acknowledged boolean DEFAULT false,
    approval_status     varchar(30) DEFAULT 'APPROVED',
    metadata            jsonb DEFAULT '{}'
);

-- Remediation executions (audit trail)
CREATE TABLE IF NOT EXISTS remediation_executions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_id        varchar(120) UNIQUE NOT NULL,
    recommendation_id   varchar(120),
    approval_id         varchar(120),
    incident_id         uuid REFERENCES incidents(id) ON DELETE SET NULL,
    action_id           varchar(120),
    target_service      varchar(120),
    target_variable     varchar(120),
    state               varchar(30) NOT NULL,
    environment         varchar(30),
    dry_run             boolean DEFAULT true,
    simulation_mode     boolean DEFAULT true,
    started_at          timestamptz,
    completed_at        timestamptz,
    verification_result jsonb,
    rollback_result     jsonb,
    audit_timeline      jsonb DEFAULT '[]',
    created_at          timestamptz DEFAULT now()
);

-- System audit log (append-only)
CREATE TABLE IF NOT EXISTS audit_log (
    id              bigserial PRIMARY KEY,
    event_type      varchar(80) NOT NULL,
    entity_type     varchar(60),
    entity_id       varchar(120),
    actor           varchar(120),
    correlation_id  varchar(80),
    event_data      jsonb DEFAULT '{}',
    created_at      timestamptz DEFAULT now()
);

-- ── Seed data ──────────────────────────────────────────────────────────────────
INSERT INTO inventory(sku, quantity) VALUES ('sku-demo', 100) ON CONFLICT (sku) DO NOTHING;

INSERT INTO services(name, type, status, current_latency, baseline_latency, error_rate)
VALUES
    ('api-gateway',      'gateway',      'healthy',  45,  40,  0.1),
    ('order-service',    'microservice', 'healthy',  80,  75,  0.2),
    ('inventory-service','microservice', 'healthy',  60,  55,  0.1),
    ('payment-service',  'microservice', 'healthy',  70,  65,  0.2),
    ('inventory-db',     'database',     'healthy',  10,   8,  0.0)
ON CONFLICT (name) DO NOTHING;

INSERT INTO dependencies(source_service, target_service, direction)
VALUES
    ('api-gateway',       'order-service',     'downstream'),
    ('order-service',     'inventory-service', 'downstream'),
    ('order-service',     'payment-service',   'downstream'),
    ('inventory-service', 'inventory-db',      'downstream')
ON CONFLICT (source_service, target_service) DO NOTHING;

-- ── Indexes ────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_incidents_status     ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_opened_at  ON incidents(opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_ts       ON predictions(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_simulations_ts       ON simulations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_type       ON audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_log_ts         ON audit_log(created_at DESC);

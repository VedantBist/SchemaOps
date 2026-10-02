-- Phase 4: tiered auto-remediation. Spring is the only writer of these tables.

CREATE TABLE remediation_recommendations (
    id               uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    environment_id   uuid NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
    incident_id      uuid NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    analysis_id      uuid REFERENCES root_cause_analyses(id) ON DELETE SET NULL,
    rank             int NOT NULL,
    action_id        varchar(80) NOT NULL,
    action_name      varchar(160) NOT NULL,
    executor         varchar(20) NOT NULL,
    operation        varchar(64) NOT NULL,
    target_node      varchar(200) NOT NULL,
    target_kind      varchar(20) NOT NULL,
    binding          varchar(200) NOT NULL,
    tier             int NOT NULL,
    reversible       boolean NOT NULL,
    params           jsonb NOT NULL DEFAULT '{}'::jsonb,
    rca_confidence   double precision NOT NULL,
    expected_benefit jsonb NOT NULL DEFAULT '{}'::jsonb,
    effectiveness    jsonb NOT NULL DEFAULT '{}'::jsonb,
    score            double precision NOT NULL,
    rationale        text NOT NULL,
    status           varchar(24) NOT NULL,      -- PROPOSED, AWAITING_APPROVAL, APPROVED, REJECTED, EXECUTED,
                                                -- BLOCKED, SUPERSEDED, EXPIRED
    policy           jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz NOT NULL
);
CREATE INDEX remediation_recs_incident ON remediation_recommendations (incident_id, created_at DESC);

CREATE TABLE remediation_approvals (
    id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    recommendation_id uuid NOT NULL REFERENCES remediation_recommendations(id) ON DELETE CASCADE,
    decision          varchar(12) NOT NULL CHECK (decision IN ('APPROVED', 'REJECTED')),
    decided_by        varchar(120) NOT NULL,
    reason            text,
    decided_at        timestamptz NOT NULL DEFAULT now(),
    consumed_at       timestamptz
);
CREATE UNIQUE INDEX remediation_one_decision ON remediation_approvals (recommendation_id);

CREATE TABLE remediation_executions (
    id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    environment_id    uuid NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
    incident_id       uuid NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    recommendation_id uuid NOT NULL REFERENCES remediation_recommendations(id) ON DELETE CASCADE,
    action_id         varchar(80) NOT NULL,
    executor          varchar(20) NOT NULL,
    operation         varchar(64) NOT NULL,
    target_node       varchar(200) NOT NULL,
    binding           varchar(200) NOT NULL,
    root_metric       varchar(40),
    mode              varchar(12) NOT NULL CHECK (mode IN ('AUTO', 'APPROVED')),
    approved_by       varchar(120),
    dry_run           boolean NOT NULL DEFAULT false,
    status            varchar(20) NOT NULL,     -- EXECUTING, VERIFYING, VERIFIED, FAILED, ROLLED_BACK,
                                                -- ROLLBACK_FAILED, ERROR
    started_at        timestamptz NOT NULL DEFAULT now(),
    executed_at       timestamptz,
    verify_after      timestamptz,
    verify_deadline   timestamptz,
    healthy_streak    int NOT NULL DEFAULT 0,
    finished_at       timestamptz,
    result            jsonb NOT NULL DEFAULT '{}'::jsonb,
    rollback_state    jsonb,
    verification      jsonb NOT NULL DEFAULT '{}'::jsonb,
    rollback_result   jsonb
);
CREATE INDEX remediation_exec_env_status ON remediation_executions (environment_id, status);
CREATE INDEX remediation_exec_target ON remediation_executions (environment_id, target_node, started_at DESC);
-- One in-flight change per target at a time.
CREATE UNIQUE INDEX remediation_exec_target_lock ON remediation_executions (environment_id, target_node)
    WHERE status IN ('EXECUTING', 'VERIFYING');

-- Append-only record of every remediation decision and change, by whom and why.
CREATE TABLE audit_log (
    id             bigserial PRIMARY KEY,
    at             timestamptz NOT NULL DEFAULT now(),
    environment_id uuid,               -- no foreign keys: audit entries outlive what they describe
    incident_id    uuid,
    actor          varchar(120) NOT NULL,
    action         varchar(60) NOT NULL,
    entity_type    varchar(40) NOT NULL,
    entity_id      uuid,
    detail         jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX audit_log_incident ON audit_log (incident_id, at);
CREATE INDEX audit_log_env_time ON audit_log (environment_id, at DESC);

CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER audit_log_no_update BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();

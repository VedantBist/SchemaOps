-- Engine methodology descriptions exceed the 120 characters V1 allowed, and root causes can now
-- be call links ("client->server"), which need more room than a service name.
ALTER TABLE root_cause_analyses
    ALTER COLUMN methodology TYPE text,
    ALTER COLUMN root_cause TYPE varchar(200);
ALTER TABLE root_cause_candidates ALTER COLUMN service_name TYPE varchar(200);
ALTER TABLE simulations ALTER COLUMN target TYPE varchar(200);

-- ==============================================================================
-- Reference system bootstrap (Docker cold starts only).
--
-- The inventory-service owns its own database, "inventory-db". OpenTelemetry
-- reports it as db.name, so the collector's service graph shows a real
-- inventory-service -> inventory-db edge without any CausalOps-specific config.
--
-- The CausalOps platform database (POSTGRES_DB, default "causalops") is owned
-- entirely by Flyway: backend/causalops-api/src/main/resources/db/migration/.
-- Do not create CausalOps tables here; Flyway V1 fails if they already exist.
-- ==============================================================================

CREATE DATABASE "inventory-db";

\connect "inventory-db"

CREATE TABLE IF NOT EXISTS products (
    id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sku  varchar(80) UNIQUE NOT NULL,
    name varchar(120) NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory (
    sku      varchar(80) PRIMARY KEY,
    quantity integer NOT NULL
);

INSERT INTO products(sku, name) VALUES ('sku-demo', 'Reference product') ON CONFLICT (sku) DO NOTHING;
INSERT INTO inventory(sku, quantity) VALUES ('sku-demo', 100) ON CONFLICT (sku) DO NOTHING;

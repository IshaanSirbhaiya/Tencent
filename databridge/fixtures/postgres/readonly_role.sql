-- A read-only role for SOURCE connections (09_FINAL_PRD §15.1): sources must use
-- read-only credentials; the connector's privilege probe refuses/flags a role that can
-- write. Connect sources as:
--   postgresql+psycopg2://databridge_ro:databridge_ro@localhost:5432/fintech
CREATE ROLE databridge_ro WITH LOGIN PASSWORD 'databridge_ro';
GRANT CONNECT ON DATABASE fintech TO databridge_ro;
GRANT USAGE ON SCHEMA public TO databridge_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO databridge_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO databridge_ro;
-- explicitly no INSERT/UPDATE/DELETE/CREATE → the probe sees a read-only role.

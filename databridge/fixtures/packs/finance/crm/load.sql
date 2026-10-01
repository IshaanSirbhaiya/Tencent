-- SYNTHETIC DATA - load crm in dependency order.
\set ON_ERROR_STOP on
\i schema.sql

\i seed/01_relationship_manager_01.sql
\i seed/02_account_org_01.sql
\i seed/03_contact_01.sql
\i seed/04_interaction_01.sql
\i seed/04_interaction_02.sql
\i seed/05_opportunity_01.sql

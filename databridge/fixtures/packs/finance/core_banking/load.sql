-- SYNTHETIC DATA - load core_banking in dependency order.
-- psql -v ON_ERROR_STOP=1 -f load.sql
\set ON_ERROR_STOP on
\i schema.sql

\i seed/01_party_01.sql
\i seed/02_party_address_01.sql
\i seed/03_party_contact_01.sql
\i seed/04_account_01.sql
\i seed/05_facility_01.sql
\i seed/06_facility_account_01.sql
\i seed/07_txn_01.sql
\i seed/07_txn_02.sql
\i seed/07_txn_03.sql
\i seed/07_txn_04.sql
\i seed/07_txn_05.sql
\i seed/07_txn_06.sql
\i seed/07_txn_07.sql
\i seed/08_balance_01.sql
\i seed/08_balance_02.sql
\i seed/08_balance_03.sql
\i seed/09_collateral_01.sql
\i seed/10_covenant_01.sql
\i seed/11_covenant_test_01.sql

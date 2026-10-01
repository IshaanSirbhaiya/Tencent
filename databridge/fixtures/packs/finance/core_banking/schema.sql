-- =============================================================================================
-- SYNTHETIC DATA - Walnut finance estate / source: core_banking
-- PostgreSQL 14+ dialect. Every entity below is fictional.
--
-- Deliberate messiness (this is the point of the fixture):
--   * `party_type`, `country_code`, `status` and `currency` carry INCONSISTENT CASING.
--   * `party.notes`, `facility.notes`, `collateral.notes` are free text: PII and planted
--     prompt-injection payloads hide there. See ground_truth/injections.json.
--   * `txn.counterparty_party_id`, `txn.reversal_of`, `collateral.facility_id` and
--     `party.primary_rm_code` are UNDECLARED foreign keys and some of them are ORPHANS -
--     a heuristic FK finder must discover them and a join must survive the dangling rows.
--   * `party` contains NEAR-DUPLICATE rows for the same real-world entity.
--   * empty strings and NULLs are both used for "missing", inconsistently.
--
-- Pinning tier: T1 (monotonic LSN cursor). T0 only where the deployment adds PITR; the
-- retention window must then be declared with the claim. See SOURCES.md.
-- =============================================================================================

DROP SCHEMA IF EXISTS core_banking CASCADE;
CREATE SCHEMA core_banking;

CREATE TABLE core_banking.party (
    party_id            TEXT PRIMARY KEY,
    party_type          TEXT NOT NULL,
    legal_name          TEXT NOT NULL,
    trading_name        TEXT,
    registration_no     TEXT,
    incorporation_date  DATE,
    date_of_birth       DATE,
    country_code        TEXT,
    industry_code       TEXT,
    risk_grade          TEXT,
    relationship_since  DATE,
    primary_rm_code     TEXT,
    kyc_status          TEXT,
    kyc_review_due      DATE,
    source_system       TEXT NOT NULL DEFAULT 'CB-CORE',
    updated_at          TIMESTAMPTZ NOT NULL,
    deleted_at          TIMESTAMPTZ,
    notes               TEXT
);
COMMENT ON TABLE  core_banking.party IS 'Legal and natural persons known to the lending book. Synthetic.';
COMMENT ON COLUMN core_banking.party.primary_rm_code IS 'UNDECLARED cross-source FK -> crm.relationship_manager.rm_code; some values are orphans.';
COMMENT ON COLUMN core_banking.party.date_of_birth IS 'SENSITIVE: personal data, individuals only.';
COMMENT ON COLUMN core_banking.party.notes IS 'SENSITIVE: unstructured operator notes. Contains personal data and untrusted third-party text.';
COMMENT ON COLUMN core_banking.party.deleted_at IS 'Soft delete. Hard deletes are NOT detectable on this source.';

CREATE TABLE core_banking.party_address (
    address_id    TEXT PRIMARY KEY,
    party_id      TEXT NOT NULL REFERENCES core_banking.party(party_id),
    address_type  TEXT,
    line1         TEXT,
    line2         TEXT,
    city          TEXT,
    postal_code   TEXT,
    country_code  TEXT,
    valid_from    DATE,
    valid_to      DATE
);
COMMENT ON COLUMN core_banking.party_address.line1 IS 'SENSITIVE: residential addresses appear for INDIVIDUAL parties.';

CREATE TABLE core_banking.party_contact (
    contact_id   TEXT PRIMARY KEY,
    party_id     TEXT NOT NULL REFERENCES core_banking.party(party_id),
    contact_name TEXT NOT NULL,
    email        TEXT,
    phone        TEXT,
    role_title   TEXT,
    is_primary   BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at   TIMESTAMPTZ NOT NULL
);
COMMENT ON COLUMN core_banking.party_contact.email IS 'SENSITIVE: contact identifier; also the only reliable join key to crm.contact.';

CREATE TABLE core_banking.account (
    account_id        TEXT PRIMARY KEY,
    party_id          TEXT NOT NULL REFERENCES core_banking.party(party_id),
    account_no        TEXT NOT NULL,
    product_code      TEXT NOT NULL,
    currency          TEXT NOT NULL,
    opened_on         DATE NOT NULL,
    closed_on         DATE,
    status            TEXT NOT NULL,
    branch_code       TEXT,
    ledger_balance    NUMERIC(18,2),
    available_balance NUMERIC(18,2),
    updated_at        TIMESTAMPTZ NOT NULL
);
COMMENT ON COLUMN core_banking.account.account_no IS 'Joins to payments.debtor_account_ref / creditor_account_ref (different formatting).';

CREATE TABLE core_banking.facility (
    facility_id     TEXT PRIMARY KEY,
    party_id        TEXT NOT NULL REFERENCES core_banking.party(party_id),
    facility_type   TEXT NOT NULL,
    limit_amount    NUMERIC(18,2) NOT NULL,
    drawn_amount    NUMERIC(18,2),
    currency        TEXT NOT NULL,
    start_date      DATE NOT NULL,
    maturity_date   DATE,
    interest_basis  TEXT,
    margin_bps      INTEGER,
    status          TEXT NOT NULL,
    booking_entity  TEXT,
    review_date     DATE,
    updated_at      TIMESTAMPTZ NOT NULL,
    notes           TEXT
);

CREATE TABLE core_banking.facility_account (
    facility_id TEXT NOT NULL REFERENCES core_banking.facility(facility_id),
    account_id  TEXT NOT NULL REFERENCES core_banking.account(account_id),
    linked_on   DATE,
    PRIMARY KEY (facility_id, account_id)
);

CREATE TABLE core_banking.txn (
    txn_id                BIGINT PRIMARY KEY,
    account_id            TEXT NOT NULL REFERENCES core_banking.account(account_id),
    booking_ts            TIMESTAMPTZ NOT NULL,
    value_date            DATE,
    amount                NUMERIC(18,2) NOT NULL,
    currency              TEXT NOT NULL,
    direction             TEXT NOT NULL,
    txn_code              TEXT NOT NULL,
    counterparty_name     TEXT,
    counterparty_party_id TEXT,
    narrative             TEXT,
    channel               TEXT,
    status                TEXT NOT NULL,
    reversal_of           BIGINT
);
COMMENT ON COLUMN core_banking.txn.counterparty_party_id IS 'UNDECLARED FK -> core_banking.party(party_id). Contains orphans on purpose.';
COMMENT ON COLUMN core_banking.txn.reversal_of IS 'UNDECLARED self-FK -> core_banking.txn(txn_id).';

CREATE TABLE core_banking.account_balance_monthly (
    account_id      TEXT NOT NULL REFERENCES core_banking.account(account_id),
    as_of_date      DATE NOT NULL,
    closing_balance NUMERIC(18,2) NOT NULL,
    currency        TEXT NOT NULL,
    PRIMARY KEY (account_id, as_of_date)
);

CREATE TABLE core_banking.collateral (
    collateral_id      TEXT PRIMARY KEY,
    facility_id        TEXT,
    collateral_type    TEXT NOT NULL,
    description        TEXT,
    valuation_amount   NUMERIC(18,2),
    valuation_currency TEXT,
    valuation_date     DATE,
    ltv_pct            NUMERIC(6,2),
    custodian          TEXT,
    notes              TEXT
);
COMMENT ON COLUMN core_banking.collateral.facility_id IS 'UNDECLARED FK -> core_banking.facility(facility_id). Orphans present.';

CREATE TABLE core_banking.covenant (
    covenant_id     TEXT PRIMARY KEY,
    facility_id     TEXT NOT NULL REFERENCES core_banking.facility(facility_id),
    covenant_type   TEXT NOT NULL,
    comparison_op   TEXT NOT NULL,
    threshold_value NUMERIC(14,4) NOT NULL,
    unit            TEXT,
    test_frequency  TEXT,
    next_test_date  DATE,
    status          TEXT NOT NULL
);

CREATE TABLE core_banking.covenant_test (
    test_id        TEXT PRIMARY KEY,
    covenant_id    TEXT NOT NULL REFERENCES core_banking.covenant(covenant_id),
    test_date      DATE NOT NULL,
    observed_value NUMERIC(14,4),
    breached       BOOLEAN,
    waiver_ref     TEXT,
    recorded_by    TEXT,
    recorded_at    TIMESTAMPTZ
);

CREATE INDEX idx_account_party      ON core_banking.account(party_id);
CREATE INDEX idx_facility_party     ON core_banking.facility(party_id);
CREATE INDEX idx_txn_account_ts     ON core_banking.txn(account_id, booking_ts);
CREATE INDEX idx_txn_value_date     ON core_banking.txn(value_date);
CREATE INDEX idx_covtest_covenant   ON core_banking.covenant_test(covenant_id, test_date);
CREATE INDEX idx_party_updated_at   ON core_banking.party(updated_at);

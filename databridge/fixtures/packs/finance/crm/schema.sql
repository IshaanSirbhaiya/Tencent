-- =============================================================================================
-- SYNTHETIC DATA - Walnut finance estate / source: crm
-- PostgreSQL 14+ dialect. Fictional entities only.
--
-- This source deliberately DOES NOT share identifiers with core_banking. `account_org.external_ref`
-- is the only bridge and it is unreliable by construction:
--     ~30% correct, ~50% NULL, ~20% POINTING AT THE WRONG PARTY.
-- Names differ from core_banking (abbreviations, dropped legal forms, typos), so entity
-- resolution has to work on names, domains, contact emails and addresses - not on an id.
--
-- Pinning tier: T2 (current-addressable). The only watermark is `updated_at`, written by the
-- application clock, so it is NOT monotonic and it skips rows updated inside the same second.
-- Deletes are HARD and leave no tombstone.
-- =============================================================================================

DROP SCHEMA IF EXISTS crm CASCADE;
CREATE SCHEMA crm;

CREATE TABLE crm.relationship_manager (
    rm_code        TEXT PRIMARY KEY,
    full_name      TEXT NOT NULL,
    email          TEXT NOT NULL,
    team           TEXT,
    region         TEXT,
    hire_date      DATE,
    manager_rm_code TEXT,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE
);
COMMENT ON COLUMN crm.relationship_manager.manager_rm_code IS 'UNDECLARED self-FK -> crm.relationship_manager(rm_code).';

CREATE TABLE crm.account_org (
    crm_org_id     TEXT PRIMARY KEY,
    org_name       TEXT NOT NULL,
    website        TEXT,
    sector         TEXT,
    hq_city        TEXT,
    hq_country     TEXT,
    owner_rm_code  TEXT REFERENCES crm.relationship_manager(rm_code),
    external_ref   TEXT,
    lifecycle_stage TEXT,
    annual_revenue_band TEXT,
    created_at     TIMESTAMPTZ NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL
);
COMMENT ON COLUMN crm.account_org.external_ref IS 'UNRELIABLE bridge to core_banking.party(party_id): correct, NULL or wrong. Never trust without corroboration.';

CREATE TABLE crm.contact (
    crm_contact_id TEXT PRIMARY KEY,
    crm_org_id     TEXT NOT NULL REFERENCES crm.account_org(crm_org_id),
    first_name     TEXT,
    last_name      TEXT,
    email          TEXT,
    phone          TEXT,
    job_title      TEXT,
    opted_out      BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at     TIMESTAMPTZ NOT NULL
);
COMMENT ON COLUMN crm.contact.email IS 'SENSITIVE. Role mailboxes (treasury@, ops@) are shared by several people - a join key that is not a key.';

CREATE TABLE crm.interaction (
    interaction_id TEXT PRIMARY KEY,
    crm_org_id     TEXT NOT NULL REFERENCES crm.account_org(crm_org_id),
    crm_contact_id TEXT,
    rm_code        TEXT,
    channel        TEXT,
    occurred_at    TIMESTAMPTZ NOT NULL,
    subject        TEXT,
    summary        TEXT,
    sentiment      TEXT,
    follow_up_date DATE
);
COMMENT ON COLUMN crm.interaction.crm_contact_id IS 'UNDECLARED FK -> crm.contact(crm_contact_id); orphans present after contact deletions.';
COMMENT ON COLUMN crm.interaction.summary IS 'SENSITIVE free text. Contains untrusted third-party content.';

CREATE TABLE crm.opportunity (
    opportunity_id TEXT PRIMARY KEY,
    crm_org_id     TEXT NOT NULL REFERENCES crm.account_org(crm_org_id),
    name           TEXT NOT NULL,
    stage          TEXT NOT NULL,
    amount         NUMERIC(18,2),
    currency       TEXT,
    probability    NUMERIC(5,2),
    expected_close DATE,
    owner_rm_code  TEXT,
    lost_reason    TEXT,
    created_at     TIMESTAMPTZ NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_crm_org_updated  ON crm.account_org(updated_at);
CREATE INDEX idx_crm_inter_org    ON crm.interaction(crm_org_id, occurred_at);
CREATE INDEX idx_crm_contact_org  ON crm.contact(crm_org_id);

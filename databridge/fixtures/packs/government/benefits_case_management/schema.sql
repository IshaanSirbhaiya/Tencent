-- =====================================================================
-- SYNTHETIC FIXTURE -- Walnut `government` pack
-- Source: benefits_case_management
-- System of record: Aldermoor Territory Benefits Administration (ATBA),
--                   "CLAIMLINE" case management system.
--
-- EVERY person, programme, address and place name in this file is
-- INVENTED. No real claimant or payment is represented here.
--
-- Target: PostgreSQL 14+.  Runnable as-is:
--   psql -v ON_ERROR_STOP=1 -f schema.sql
--   psql -v ON_ERROR_STOP=1 -f seed/load_all.sql
--
-- THE ENTITY-RESOLUTION TEST LIVES HERE.  Roughly half of the people in
-- this database are the same human beings as rows in permits.applicants,
-- but CLAIMLINE never adopted the Aldermoor Resident Number: it keys on
-- its own Benefits Reference Number (BRN).  Names, dates of birth and
-- addresses agree only approximately.  The true mapping is in
-- ../GROUND_TRUTH.md and ../entity_resolution_truth.csv -- the fixture
-- ships the answer key so resolution can be SCORED, not eyeballed.
--
-- Pinning tier (D6): T1 (LSN cursor, forward-only).
-- =====================================================================

DROP SCHEMA IF EXISTS benefits CASCADE;
CREATE SCHEMA benefits;
COMMENT ON SCHEMA benefits IS
  'CLAIMLINE benefits case management. SYNTHETIC. Pinning tier T1 (LSN cursor); T0 only with PITR.';

-- ---------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------

CREATE TABLE benefits.benefit_programs (
    program_code           text     PRIMARY KEY,
    name                   text     NOT NULL,
    statutory_reference    text     NOT NULL,
    max_monthly_cents      integer  NOT NULL CHECK (max_monthly_cents > 0),
    means_tested           boolean  NOT NULL,
    review_interval_months smallint NOT NULL CHECK (review_interval_months > 0),
    opened_on              date     NOT NULL,
    closed_on              date
);
COMMENT ON TABLE benefits.benefit_programs IS
  'Programme catalogue. Eligibility rules live in pack YAML, never in code -- adding a programme must be a data change.';

CREATE TABLE benefits.caseworkers (
    caseworker_ref text     PRIMARY KEY,
    display_name   text     NOT NULL,
    team           text     NOT NULL,
    role_code      text     NOT NULL
        CHECK (role_code IN ('CASEWORKER','SENIOR_CASEWORKER','REVIEWER','TEAM_LEAD','FRAUD_OFFICER')),
    work_email     text,
    active         boolean  NOT NULL DEFAULT true,
    started_on     date     NOT NULL,
    ended_on       date
);
COMMENT ON TABLE benefits.caseworkers IS
  'Drives the caseload row filter in ../roles.yaml (claims.caseworker_ref = principal.caseworker_ref).';

-- ---------------------------------------------------------------------
-- Parties
-- ---------------------------------------------------------------------

CREATE TABLE benefits.claimants (
    claimant_id       text        PRIMARY KEY,
    benefits_ref      text        NOT NULL UNIQUE,
    surname           text        NOT NULL,
    forenames         text        NOT NULL,
    date_of_birth     date,
    resident_number   text,
    contact_email     text,
    contact_phone     text,
    address_line      text,
    address_locality  text,
    address_postcode  text,
    household_size    smallint    CHECK (household_size IS NULL OR household_size BETWEEN 1 AND 14),
    registered_on     date        NOT NULL,
    last_contact_on   date,
    deceased_flag     boolean     NOT NULL DEFAULT false,
    correspondence_pref text
        CHECK (correspondence_pref IS NULL OR correspondence_pref IN ('post','email','sms','in_person'))
);
COMMENT ON TABLE benefits.claimants IS
  'Subject kind `claimant` (spine D4b). NEVER_FUZZY_MERGE. Overlaps permits.applicants by person, never by key.';
COMMENT ON COLUMN benefits.claimants.benefits_ref IS
  'BRN -- CLAIMLINE primary external identifier. Unrelated to the Aldermoor Resident Number used by PERMITWORKS.';
COMMENT ON COLUMN benefits.claimants.resident_number IS
  'Optional back-reference to the Aldermoor Resident Number, captured only since 2019 and populated for a minority of rows. Where present it is often a transposed or truncated variant of the value PERMITWORKS holds -- this is the intended trap.';
COMMENT ON COLUMN benefits.claimants.surname IS
  'Recorded surname. Married/changed names were overwritten in place; CLAIMLINE keeps no name history at all.';

CREATE INDEX claimants_surname_idx ON benefits.claimants (lower(surname));
CREATE INDEX claimants_dob_idx     ON benefits.claimants (date_of_birth);
CREATE INDEX claimants_resident_number_idx ON benefits.claimants (resident_number);

-- ---------------------------------------------------------------------
-- Claims and the determination chain
-- ---------------------------------------------------------------------

CREATE TABLE benefits.claims (
    claim_id        text        PRIMARY KEY,
    claimant_id     text        NOT NULL REFERENCES benefits.claimants(claimant_id),
    program_code    text        NOT NULL REFERENCES benefits.benefit_programs(program_code),
    opened_on       date        NOT NULL,
    closed_on       date,
    status          text        NOT NULL
        CHECK (status IN ('NEW','IN_ASSESSMENT','AWAITING_EVIDENCE','ACTIVE','SUSPENDED',
                          'CLOSED','REJECTED','APPEALED','TRANSFERRED')),
    caseworker_ref  text        REFERENCES benefits.caseworkers(caseworker_ref),
    intake_channel  text        NOT NULL
        CHECK (intake_channel IN ('office','online','phone','post','outreach','referral')),
    linked_application_id text,
    priority_flag   boolean     NOT NULL DEFAULT false,
    CONSTRAINT claims_lifecycle CHECK (closed_on IS NULL OR closed_on >= opened_on)
);
COMMENT ON COLUMN benefits.claims.linked_application_id IS
  'Occasionally hand-keyed reference to a PERMITWORKS application_id. Present on a small minority of claims, mistyped on some of those. It is a hint for resolution, never a key.';

CREATE INDEX claims_claimant_idx   ON benefits.claims (claimant_id);
CREATE INDEX claims_program_idx    ON benefits.claims (program_code);
CREATE INDEX claims_caseworker_idx ON benefits.claims (caseworker_ref);

CREATE TABLE benefits.eligibility_determinations (
    determination_id          text        PRIMARY KEY,
    claim_id                  text        NOT NULL REFERENCES benefits.claims(claim_id),
    determined_on             date        NOT NULL,
    outcome                   text        NOT NULL
        CHECK (outcome IN ('ELIGIBLE','INELIGIBLE','PARTIAL','DEFERRED','WITHDRAWN')),
    basis_code                text        NOT NULL,
    monthly_entitlement_cents integer     CHECK (monthly_entitlement_cents IS NULL OR monthly_entitlement_cents >= 0),
    effective_from            date,
    effective_to              date,
    determined_by             text        REFERENCES benefits.caseworkers(caseworker_ref),
    evidence_ref              text,
    supersedes                text        REFERENCES benefits.eligibility_determinations(determination_id),
    CONSTRAINT determination_validity CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from)
);
COMMENT ON COLUMN benefits.eligibility_determinations.basis_code IS
  'Statutory basis. Some codes carry health or household-circumstance meaning and are redacted for the auditor and withheld from the public-records officer -- see ../roles.yaml.';
COMMENT ON COLUMN benefits.eligibility_determinations.evidence_ref IS
  'Points at a case file in ../case_files (scanned, T3) or a legacy record key. Resolution is against the Capture Store, never the live filesystem.';

CREATE INDEX determinations_claim_idx ON benefits.eligibility_determinations (claim_id);

CREATE TABLE benefits.payments (
    payment_id     text        PRIMARY KEY,
    claim_id       text        NOT NULL REFERENCES benefits.claims(claim_id),
    paid_on        date        NOT NULL,
    amount_cents   integer     NOT NULL,
    method         text        NOT NULL
        CHECK (method IN ('bank_transfer','cheque','card_load','cash_office','offset')),
    reference      text,
    period_start   date,
    period_end     date,
    reversal_of    text        REFERENCES benefits.payments(payment_id),
    posted_at      timestamptz NOT NULL
);
COMMENT ON COLUMN benefits.payments.reversal_of IS
  'A reversal is a new negative row referencing the original. Nothing is ever updated or deleted in this table.';
COMMENT ON COLUMN benefits.payments.posted_at IS
  'System time. The adapter watermark column for this table.';

CREATE INDEX payments_claim_idx  ON benefits.payments (claim_id, paid_on);
CREATE INDEX payments_posted_idx ON benefits.payments (posted_at);

CREATE TABLE benefits.reviews (
    review_id     text        PRIMARY KEY,
    claim_id      text        NOT NULL REFERENCES benefits.claims(claim_id),
    review_type   text        NOT NULL
        CHECK (review_type IN ('SCHEDULED','TRIGGERED','APPEAL','COMPLIANCE','DATA_QUALITY')),
    scheduled_for date,
    completed_on  date,
    result        text
        CHECK (result IS NULL OR result IN ('NO_CHANGE','ENTITLEMENT_INCREASED','ENTITLEMENT_REDUCED',
                                            'CLAIM_CLOSED','REFERRED_FOR_INVESTIGATION','NOT_COMPLETED')),
    reviewer_ref  text        REFERENCES benefits.caseworkers(caseworker_ref),
    notes         text
);

CREATE INDEX reviews_claim_idx ON benefits.reviews (claim_id);

CREATE TABLE benefits.claim_notes (
    note_id      text        PRIMARY KEY,
    claim_id     text        NOT NULL REFERENCES benefits.claims(claim_id),
    authored_at  timestamptz NOT NULL,
    author_ref   text,
    sensitivity  text        NOT NULL
        CHECK (sensitivity IN ('routine','restricted','sealed')),
    body         text        NOT NULL,
    source_channel text
);
COMMENT ON TABLE benefits.claim_notes IS
  'Free-text caseworker notes. UNTRUSTED (spine D3) -- some notes quote text supplied by claimants or third parties verbatim, including the injection attempts planted in ../correspondence.';
COMMENT ON COLUMN benefits.claim_notes.sensitivity IS
  'restricted -> redacted for auditor; sealed -> policy_withheld for every role except the originating caseworker. Withheld is a state, not a null (spine D4).';

CREATE INDEX claim_notes_claim_idx ON benefits.claim_notes (claim_id, authored_at);

CREATE TABLE benefits.claim_events (
    event_id     bigint      PRIMARY KEY,
    claim_id     text        NOT NULL REFERENCES benefits.claims(claim_id),
    occurred_at  timestamptz NOT NULL,
    recorded_at  timestamptz NOT NULL,
    event_type   text        NOT NULL,
    actor_ref    text,
    actor_kind   text        NOT NULL
        CHECK (actor_kind IN ('caseworker','claimant','system','external_body','third_party')),
    channel      text,
    detail       text,
    correlation_ref text
);
COMMENT ON TABLE benefits.claim_events IS
  'CLAIMLINE audit trail. Truncated by an unlogged retention job at roughly 7 years, so the earliest claims have decisions with no surviving events -- the adapter must not read absence as never-happened.';

CREATE INDEX claim_events_claim_idx    ON benefits.claim_events (claim_id, occurred_at);
CREATE INDEX claim_events_recorded_idx ON benefits.claim_events (recorded_at);

-- ---------------------------------------------------------------------
-- Disclosable projection (public-records officer; see ../roles.yaml)
-- ---------------------------------------------------------------------

CREATE VIEW benefits.v_program_caseload AS
SELECT c.program_code,
       date_trunc('month', c.opened_on::timestamp)::date AS opened_month,
       count(*)                                          AS claims_opened,
       count(*) FILTER (WHERE c.status = 'ACTIVE')       AS still_active
FROM benefits.claims c
GROUP BY 1, 2;
COMMENT ON VIEW benefits.v_program_caseload IS
  'Aggregate-only disclosure. Small-cell suppression is the policy layer''s job, not the view''s -- the view deliberately does NOT suppress, so the withholding path is exercised.';

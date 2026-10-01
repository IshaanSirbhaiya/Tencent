-- =====================================================================
-- SYNTHETIC FIXTURE -- Walnut `government` pack
-- Source: permits_registry
-- System of record: Aldermoor Territory Department of Community Services
--                   and Permitting (DCSP), "PERMITWORKS" case system.
--
-- EVERY person, agency, address and place name in this file is INVENTED.
-- No real permit, applicant or parcel is represented here.
--
-- Target: PostgreSQL 14+.  Runnable as-is:
--   psql -v ON_ERROR_STOP=1 -f schema.sql
--   psql -v ON_ERROR_STOP=1 -f seed/load_all.sql
--
-- Pinning tier (D6): T1 by default (LSN cursor, forward-only).
-- Promotable to T0 only where the deployment runs PITR / a logical
-- snapshot layer, and then only with the retention window declared.
-- See ../SOURCES.md.
-- =====================================================================

DROP SCHEMA IF EXISTS permits CASCADE;
CREATE SCHEMA permits;
COMMENT ON SCHEMA permits IS
  'PERMITWORKS case system. SYNTHETIC. Pinning tier T1 (LSN cursor); T0 only with PITR.';

-- ---------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------

CREATE TABLE permits.permit_types (
    permit_type_code      text        PRIMARY KEY,
    title                 text        NOT NULL,
    category              text        NOT NULL,
    statutory_reference   text        NOT NULL,
    standard_fee_cents    integer     NOT NULL CHECK (standard_fee_cents >= 0),
    target_decision_days  smallint    NOT NULL CHECK (target_decision_days > 0),
    requires_inspection   boolean     NOT NULL DEFAULT true,
    introduced_on         date        NOT NULL,
    retired_on            date,
    CONSTRAINT permit_types_lifecycle CHECK (retired_on IS NULL OR retired_on > introduced_on)
);
COMMENT ON TABLE  permits.permit_types IS 'Closed vocabulary of permit types. No code branches on these values; rules live in pack YAML.';
COMMENT ON COLUMN permits.permit_types.statutory_reference IS 'Citation into the (fictional) Aldermoor Territory statute book.';

CREATE TABLE permits.officers (
    officer_ref   text        PRIMARY KEY,
    display_name  text        NOT NULL,
    unit          text        NOT NULL,
    role_code     text        NOT NULL
        CHECK (role_code IN ('CASEWORKER','SENIOR_CASEWORKER','INSPECTOR','REGISTRAR','APPEALS_CLERK')),
    work_email    text,
    active        boolean     NOT NULL DEFAULT true,
    started_on    date        NOT NULL,
    ended_on      date
);
COMMENT ON TABLE permits.officers IS 'Agency staff. Drives the caseworker row filter in ../roles.yaml (assigned_officer = principal.officer_ref).';

-- ---------------------------------------------------------------------
-- Parties
-- ---------------------------------------------------------------------

CREATE TABLE permits.applicants (
    applicant_id       text        PRIMARY KEY,
    family_name        text        NOT NULL,
    given_names        text        NOT NULL,
    date_of_birth      date,
    national_id_ref    text,
    primary_email      text,
    primary_phone      text,
    address_line1      text,
    address_locality   text,
    address_postcode   text,
    preferred_language text,
    record_source      text        NOT NULL
        CHECK (record_source IN ('counter','online','phone','post','migrated_1998','migrated_2011')),
    notes              text,
    created_at         timestamptz NOT NULL,
    updated_at         timestamptz NOT NULL,
    CONSTRAINT applicants_updated_after_created CHECK (updated_at >= created_at)
);
COMMENT ON TABLE  permits.applicants IS
  'Subject kind `applicant` (spine D4b). NEVER_FUZZY_MERGE: the resolver may present candidates only.';
COMMENT ON COLUMN permits.applicants.national_id_ref IS
  'Aldermoor Resident Number as keyed at the counter. NULLABLE and frequently wrong: transposed digits, OCR confusions, missing dashes. Not a trustworthy join key.';
COMMENT ON COLUMN permits.applicants.record_source IS
  'migrated_1998 / migrated_2011 rows came from LEGACY-MASTER (see ../legacy_records) and lost their history in the migration.';

CREATE INDEX applicants_family_name_idx ON permits.applicants (lower(family_name));
CREATE INDEX applicants_dob_idx         ON permits.applicants (date_of_birth);

-- ---------------------------------------------------------------------
-- Applications and the decision chain
-- ---------------------------------------------------------------------

CREATE TABLE permits.applications (
    application_id     text        PRIMARY KEY,
    applicant_id       text        NOT NULL REFERENCES permits.applicants(applicant_id),
    permit_type_code   text        NOT NULL REFERENCES permits.permit_types(permit_type_code),
    parcel_id          text,
    submitted_at       timestamptz NOT NULL,
    received_channel   text        NOT NULL
        CHECK (received_channel IN ('counter','online','post','email','phone','agent')),
    status             text        NOT NULL
        CHECK (status IN ('DRAFT','SUBMITTED','UNDER_REVIEW','AWAITING_INFO','REFERRED',
                          'DECIDED','WITHDRAWN','LAPSED','ON_APPEAL','CLOSED')),
    fee_charged_cents  integer     CHECK (fee_charged_cents IS NULL OR fee_charged_cents >= 0),
    fee_paid_cents     integer     CHECK (fee_paid_cents IS NULL OR fee_paid_cents >= 0),
    assigned_officer   text        REFERENCES permits.officers(officer_ref),
    agent_of_record    text,
    site_description   text,
    last_event_at      timestamptz,
    withdrawn_on       date
);
COMMENT ON COLUMN permits.applications.parcel_id IS
  'Joins ../gis_parcels/parcels_*.csv. Deliberately NOT a foreign key: GIS is a different system on a different release train, and a small fraction of parcel ids here do not exist there.';
COMMENT ON COLUMN permits.applications.agent_of_record IS
  'Free-text third party (architect, contractor, advocacy service). Untrusted (spine D3): displayed and cited, never allowed to influence control flow.';

CREATE INDEX applications_applicant_idx ON permits.applications (applicant_id);
CREATE INDEX applications_parcel_idx    ON permits.applications (parcel_id);
CREATE INDEX applications_status_idx    ON permits.applications (status);
CREATE INDEX applications_submitted_idx ON permits.applications (submitted_at);

CREATE TABLE permits.decisions (
    decision_id      text        PRIMARY KEY,
    application_id   text        NOT NULL REFERENCES permits.applications(application_id),
    outcome          text        NOT NULL
        CHECK (outcome IN ('GRANTED','GRANTED_WITH_CONDITIONS','REFUSED','DEEMED_APPROVED','REVOKED')),
    decided_at       timestamptz NOT NULL,
    decided_by       text        REFERENCES permits.officers(officer_ref),
    rationale        text,
    permit_number    text,
    valid_from       date,
    valid_until      date,
    appeal_deadline  date,
    superseded_by    text        REFERENCES permits.decisions(decision_id),
    CONSTRAINT decisions_validity CHECK (valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from)
);
COMMENT ON COLUMN permits.decisions.superseded_by IS
  'Corrections are new rows pointing back, never UPDATEs. The only history this estate keeps natively.';

CREATE UNIQUE INDEX decisions_permit_number_key ON permits.decisions (permit_number) WHERE permit_number IS NOT NULL;
CREATE INDEX decisions_application_idx ON permits.decisions (application_id);

CREATE TABLE permits.permit_conditions (
    condition_id    text        PRIMARY KEY,
    decision_id     text        NOT NULL REFERENCES permits.decisions(decision_id),
    ordinal         smallint    NOT NULL,
    condition_text  text        NOT NULL,
    compliance_due  date,
    discharged_on   date,
    UNIQUE (decision_id, ordinal)
);

CREATE TABLE permits.inspections (
    inspection_id   text        PRIMARY KEY,
    application_id  text        REFERENCES permits.applications(application_id),
    parcel_id       text,
    scheduled_for   timestamptz,
    performed_at    timestamptz,
    inspector_ref   text        REFERENCES permits.officers(officer_ref),
    outcome         text
        CHECK (outcome IS NULL OR outcome IN ('PASS','PASS_WITH_ACTIONS','FAIL','NO_ACCESS','ABANDONED')),
    notes           text,
    photo_batch_ref text
);
COMMENT ON COLUMN permits.inspections.photo_batch_ref IS
  'Joins ../inspection_photos/sidecars/*.json. The images themselves are not in the repo; only OCR sidecars with bbox annotations.';

CREATE INDEX inspections_application_idx ON permits.inspections (application_id);

CREATE TABLE permits.appeals (
    appeal_id     text        PRIMARY KEY,
    decision_id   text        NOT NULL REFERENCES permits.decisions(decision_id),
    lodged_at     timestamptz NOT NULL,
    lodged_by     text,
    grounds       text,
    hearing_date  date,
    outcome       text
        CHECK (outcome IS NULL OR outcome IN ('UPHELD','DISMISSED','REMITTED','WITHDRAWN','STRUCK_OUT')),
    closed_at     timestamptz,
    sealed        boolean     NOT NULL DEFAULT false
);
COMMENT ON COLUMN permits.appeals.sealed IS
  'Sealed appeals are withheld from the public-records officer as policy_withheld (spine D4), never silently absent.';

-- ---------------------------------------------------------------------
-- The event stream (the bulk of the rows)
-- ---------------------------------------------------------------------

CREATE TABLE permits.case_events (
    event_id        bigint      PRIMARY KEY,
    application_id  text        NOT NULL REFERENCES permits.applications(application_id),
    occurred_at     timestamptz NOT NULL,
    recorded_at     timestamptz NOT NULL,
    event_type      text        NOT NULL,
    actor_ref       text,
    actor_kind      text        NOT NULL
        CHECK (actor_kind IN ('officer','applicant','agent','system','external_body')),
    channel         text,
    detail          text,
    correlation_ref text
);
COMMENT ON TABLE  permits.case_events IS 'Append-only in practice but NOT enforced by the source system; there is no tombstone table, so deletes are undetectable. See ../SOURCES.md.';
COMMENT ON COLUMN permits.case_events.occurred_at IS 'Valid time. Frequently BEFORE recorded_at by days -- counter staff key events in batches.';
COMMENT ON COLUMN permits.case_events.recorded_at IS 'System time. The only monotonic column; the adapter watermark uses it.';
COMMENT ON COLUMN permits.case_events.correlation_ref IS 'Loose join to ../correspondence/*.eml Message-ID or to a case file reference. Not enforced.';

CREATE INDEX case_events_application_idx ON permits.case_events (application_id, occurred_at);
CREATE INDEX case_events_recorded_idx    ON permits.case_events (recorded_at);
CREATE INDEX case_events_type_idx        ON permits.case_events (event_type);

-- ---------------------------------------------------------------------
-- Disclosable projection (public-records officer; see ../roles.yaml)
-- ---------------------------------------------------------------------

CREATE VIEW permits.v_public_register AS
SELECT d.permit_number,
       a.application_id,
       a.permit_type_code,
       a.parcel_id,
       a.submitted_at::date  AS submitted_on,
       d.outcome,
       d.decided_at::date    AS decided_on,
       d.valid_from,
       d.valid_until
FROM permits.applications a
JOIN permits.decisions d ON d.application_id = a.application_id
WHERE d.permit_number IS NOT NULL;
COMMENT ON VIEW permits.v_public_register IS
  'The statutory public register. Carries no personal identifiers by construction -- the disclosable subset in ../roles.yaml.';

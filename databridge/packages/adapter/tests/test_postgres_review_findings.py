"""Review `wiki/40-reviews/raw/phase3-adapters-postgres.md` (12 Critical, 2 High): one test per
finding that is actually fixed here, each failing without the fix. Triage: `wiki/40-reviews/R-005
-adapter-fleet-triage.md`. Dispositions, including the findings NOT fixed and why: `wiki/40-reviews
/R-006-postgres-dispositions.md`.

Scope: `databridge_adapter/sources/postgres.py` and `databridge_adapter/sources/contract.py` only.
Findings closed elsewhere (6, in `policy.py`/`taint.py`) are verified here only for postgres.py's
own call sites, not re-implemented.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from databridge_adapter import (
    Budget,
    CapabilityNotAdvertised,
    CapabilityRequest,
    CannotPinPast,
    PolicyEngine,
    PrincipalBinding,
    Principal,
    Projection,
    PurposeBinding,
    Role,
    RoleModel,
    RowRule,
    Subquery,
    UnorderedPointeredRead,
    Verdict,
    cell_value,
)
from databridge_adapter.errors import UntrustedColumnInPolicy
from databridge_adapter.purposes import PurposeDecl, PurposeVocabulary, use_purposes
from databridge_adapter.sources.contract import (
    SourceContract,
    SourceContractError,
)
from databridge_adapter.sources.postgres import PitrWindow, PostgresCore, cell_digest, cell_schema_hash
from databridge_core import SnapshotIdentity, SourcePointer, Trust


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


NOW = utc(2026, 8, 16)


def _minimal_entry(source_id: str, *, trust_class: str = "trusted", **overrides: object) -> dict:
    """The smallest `sources.yaml` entry `describe()` will accept: `pinning_tier` and a non-empty
    `reachable_verdicts` are both required by `SourceDescription`'s own validator."""
    entry: dict = {"id": source_id, "trust_class": trust_class, "pinning_tier": "T2",
                   "reachable_verdicts": ["VERIFIED"]}
    entry.update(overrides)
    return entry


# =======================================================================================
# Finding 1 (Critical) — historical T0 reads returned the live database.
# =======================================================================================

def test_finding_1_a_t0_declared_source_with_a_covering_pitr_still_refuses_as_of(
        finance_engine, policy, sources_yaml):
    """The exact shape the review found: a contract that declares `pinning_tier: T0` and a
    `PitrWindow` that covers the requested instant. The old code let `_check_as_of` return
    without raising in this case, and `_read_transaction` then opened an ORDINARY connection to
    the LIVE engine regardless — silently serving the present under a snapshot that claimed to be
    the past. There is no branch left that can do that: `_check_as_of` now raises unconditionally.
    """
    entry = {"id": "core_banking", "trust_class": "trusted", "pinning_tier": "T0",
             "t0_capable": True}
    contract = SourceContract(entry, path=Path("test://sources.yaml"), estate="test")
    pitr = PitrWindow(mechanism="pitr", retention_days=35, restored_source_id="core_banking_pitr")
    core = PostgresCore(source_id="core_banking", engine=finance_engine, policy=policy,
                        contract=contract, pitr=pitr, now=lambda: NOW)
    assert contract.declared_tier == "T0"
    assert pitr.covers(utc(2026, 8, 1), now=NOW)          # the window DOES cover the instant

    req = CapabilityRequest(
        source_id="core_banking", capability="row_read",
        principal=Principal(actor="orchestrator@walnut", on_behalf_of=None,
                            purpose="finance.graph_build"),
        projection=Projection(entity="party", fields=("party_id",), key=("party_id",)),
        order_by=("party_id",), as_of=utc(2026, 8, 1), limit=5)

    with pytest.raises(CannotPinPast):
        core._check_as_of(req)
    # And the second, independent choke point: even called directly, a non-None as_of is refused.
    with pytest.raises(CannotPinPast):
        core._read_transaction(as_of=utc(2026, 8, 1))


def test_finding_1_ordinary_t2_as_of_is_unaffected(core, party_request):
    """The everyday case — `core_banking` is T2 — still refuses `as_of`, as it always did."""
    with pytest.raises(CannotPinPast):
        core.resolve(party_request(as_of=utc(2020, 1, 1)))


# =======================================================================================
# Finding 2 (Critical) — a missing principal attribute widened an `in_subquery` rule instead
# of matching nothing (SQL `IS NULL`, and the same gap in the local re-evaluation set).
# =======================================================================================

@pytest.fixture
def scratch_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'scratch.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE widget (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL)"))
        conn.execute(text("CREATE TABLE lookup (widget_id TEXT, team TEXT)"))
        conn.execute(text("INSERT INTO widget (id, owner_id) VALUES ('w1', 'A'), ('w2', 'B')"))
        # The row an unbound principal attribute must NOT admit: its `team` is SQL NULL, and
        # `col == None` compiles to `IS NULL`, which this row would satisfy.
        conn.execute(text("INSERT INTO lookup (widget_id, team) VALUES ('w1', NULL)"))
    yield engine
    engine.dispose()


PACK_PURPOSE, ESTATE_PURPOSE, ROLE_NAME, ACTOR = (
    "test.scoped_read", "scoped_read", "scoped_reader", "svc@test")


@pytest.fixture(autouse=False)
def _scoped_purpose_vocabulary():
    vocab = PurposeVocabulary(
        {PACK_PURPOSE: PurposeDecl(id=PACK_PURPOSE, pack="test", description="",
                                   subject_scoped=False)},
        packs=("test",))
    with use_purposes(vocab):
        yield


def _scoped_core(engine, *, row_filter: RowRule) -> PostgresCore:
    role = Role(name=ROLE_NAME, description="t", purposes=frozenset({ESTATE_PURPOSE}),
               egress_ceiling="RESTRICTED_INTERNAL", allow={"scratch": frozenset({"widget"})},
               deny_tables=frozenset(), deny_columns=frozenset(), column_masks={},
               deny_sources=frozenset(), row_filters={"scratch.widget": row_filter},
               purpose_grants={ESTATE_PURPOSE: frozenset({"*"})})
    role_model = RoleModel(estate="scratch_estate", version=1, roles={role.name: role}, masks={},
                           classification={}, estate_purposes={ESTATE_PURPOSE: "e"},
                           path=Path("test://roles.yaml"))
    binding = PurposeBinding(estate="scratch_estate", pack="test",
                             bindings={PACK_PURPOSE: ESTATE_PURPOSE},
                             path=Path("test://purpose_bindings.yaml"))
    directory = {ACTOR: PrincipalBinding(roles=(role.name,))}    # NO attributes bound
    engine_policy = PolicyEngine(role_model=role_model, purpose_binding=binding,
                                 directory=directory, default_budget=Budget(max_rows=100))
    entry = _minimal_entry("scratch")
    contract = SourceContract(entry, path=Path("test://sources.yaml"), estate="test")
    return PostgresCore(source_id="scratch", engine=engine, policy=engine_policy,
                        contract=contract)


def test_finding_2_an_unbound_subquery_attribute_admits_nothing_not_the_null_row(
        scratch_engine, _scoped_purpose_vocabulary):
    sub = Subquery(schema=None, table="lookup", select_column="widget_id",
                   where_column="team", where_principal_attr="team")
    rule = RowRule(target="scratch.widget", role=ROLE_NAME, kind="in_subquery",
                   raw="id IN (SELECT widget_id FROM lookup WHERE team = :principal.team)",
                   column="id", principal_attr=None, subquery=sub)
    core = _scoped_core(scratch_engine, row_filter=rule)
    req = CapabilityRequest(
        source_id="scratch", capability="row_read",
        principal=Principal(actor=ACTOR, on_behalf_of=None, purpose=PACK_PURPOSE),
        projection=Projection(entity="widget", fields=("id", "owner_id"), key=("id",)),
        order_by=("id",), limit=10)

    result = core.resolve(req)
    # Before the fix: `team IS NULL` matched lookup's one row and admitted widget 'w1'. Now an
    # unbound `:principal.team` must match NOTHING - the result is empty, not one row wide.
    assert result.rows.rows == ()


def test_finding_2_a_bound_subquery_attribute_still_works(scratch_engine,
                                                           _scoped_purpose_vocabulary):
    """The control: the identical shape with the attribute actually bound admits the real match."""
    with scratch_engine.begin() as conn:
        conn.execute(text("INSERT INTO lookup (widget_id, team) VALUES ('w2', 'east')"))
    sub = Subquery(schema=None, table="lookup", select_column="widget_id",
                   where_column="team", where_principal_attr="team")
    rule = RowRule(target="scratch.widget", role=ROLE_NAME, kind="in_subquery",
                   raw="id IN (SELECT widget_id FROM lookup WHERE team = :principal.team)",
                   column="id", principal_attr=None, subquery=sub)
    role = Role(name=ROLE_NAME, description="t", purposes=frozenset({ESTATE_PURPOSE}),
               egress_ceiling="RESTRICTED_INTERNAL", allow={"scratch": frozenset({"widget"})},
               deny_tables=frozenset(), deny_columns=frozenset(), column_masks={},
               deny_sources=frozenset(), row_filters={"scratch.widget": rule},
               purpose_grants={ESTATE_PURPOSE: frozenset({"*"})})
    role_model = RoleModel(estate="scratch_estate", version=1, roles={role.name: role}, masks={},
                           classification={}, estate_purposes={ESTATE_PURPOSE: "e"},
                           path=Path("test://roles.yaml"))
    binding = PurposeBinding(estate="scratch_estate", pack="test",
                             bindings={PACK_PURPOSE: ESTATE_PURPOSE},
                             path=Path("test://purpose_bindings.yaml"))
    directory = {ACTOR: PrincipalBinding(roles=(role.name,), attributes=(("team", "east"),))}
    engine_policy = PolicyEngine(role_model=role_model, purpose_binding=binding,
                                 directory=directory, default_budget=Budget(max_rows=100))
    entry = _minimal_entry("scratch")
    contract = SourceContract(entry, path=Path("test://sources.yaml"), estate="test")
    core = PostgresCore(source_id="scratch", engine=scratch_engine, policy=engine_policy,
                        contract=contract)
    req = CapabilityRequest(
        source_id="scratch", capability="row_read",
        principal=Principal(actor=ACTOR, on_behalf_of=None, purpose=PACK_PURPOSE),
        projection=Projection(entity="widget", fields=("id", "owner_id"), key=("id",)),
        order_by=("id",), limit=10)
    result = core.resolve(req)
    ids = {cell_value(row[0]) for row in result.rows.rows}
    assert ids == {"w2"}


# =======================================================================================
# Finding 3 (Critical) — `structural_read` (schema/counts/digests only) could still reach
# `resolve()` and disclose real cell VALUES.
# =======================================================================================

def test_finding_3_resolve_refuses_a_structural_read_capability(core, analyst):
    req = CapabilityRequest(
        source_id="core_banking", capability="structural_read", principal=analyst,
        projection=Projection(entity="party", fields=("party_id",), key=("party_id",)),
        order_by=("party_id",), limit=5)
    with pytest.raises(CapabilityNotAdvertised, match="row_read"):
        core.resolve(req)


def test_finding_3_snapshot_and_plan_still_accept_structural_read(core, analyst):
    """The control: `snapshot()` and `plan()` disclose no cell content, so `structural_read` is
    exactly the capability they exist to serve, and neither is narrowed by this fix."""
    req = CapabilityRequest(
        source_id="core_banking", capability="structural_read", principal=analyst,
        projection=Projection(entity="party", fields=("party_id",), key=("party_id",)),
        order_by=("party_id",), limit=5)
    snap = core.snapshot(req)
    assert snap.source_id == "core_banking"
    plan = core.plan(req)
    assert plan.source_id == "core_banking"


def test_finding_3_an_unadvertised_capability_is_still_caught_before_the_narrower_check(
        core, analyst):
    """Ordering matters: "not advertised at all" must be reported before "wrong verb for THIS
    capability" - see `test_an_unadvertised_capability_is_rejected_and_never_reconsidered`."""
    req = CapabilityRequest(
        source_id="core_banking", capability="bulk_export", principal=analyst,
        projection=Projection(entity="party", fields=("party_id",), key=("party_id",)),
        order_by=("party_id",), limit=5)
    with pytest.raises(CapabilityNotAdvertised, match="advertises"):
        core.resolve(req)


# =======================================================================================
# Finding 4 (Critical) — pushed-down row denials became silent omissions (and, worse, the
# "fix" of counting them accurately would violate `existence_disclosure`: no count, ever).
# =======================================================================================

def test_finding_4_a_scoped_read_carries_the_constant_note_and_never_a_count(
        core, rm, party_request):
    import re

    result = core.resolve(party_request(principal=rm, limit=500))
    assert any("limited to your scope" in w for w in result.warnings)
    for warning in result.warnings:
        assert not re.search(r"\d+\s*row", warning), (
            f"a row-scope warning must never carry a count: {warning!r}")
    assert 0 < len(result.rows.rows) < 100, "RM-007 owns a handful of the 466 parties"


# =======================================================================================
# Finding 5 (Critical) — unclassified fields silently became trusted, and the entity-relative
# wildcard (`"party.*"`) never matched anything.
# =======================================================================================

def test_finding_5_a_source_with_no_trust_class_refuses_at_construction():
    entry = {"id": "no_trust_class_declared"}
    with pytest.raises(SourceContractError, match="trust_class"):
        SourceContract(entry, path=Path("test://sources.yaml"), estate="test")


def test_finding_5_an_untagged_column_defaults_to_the_sources_own_trust_class():
    """`market_data`-shaped case: the source is `trust_class: untrusted` and only ONE column is
    individually listed. Every OTHER column must default to the SOURCE's own declared class, not
    to a hard-coded `Trust.TRUSTED` that ignores what the estate said about the source."""
    entry = {"id": "market_data", "trust_class": "untrusted",
             "untrusted_fields": ["market_data.instruments.issuer_name_as_reported"]}
    contract = SourceContract(entry, path=Path("test://sources.yaml"), estate="test")
    assert contract.trust_of("instruments", "issuer_name_as_reported") is Trust.UNTRUSTED
    # Not individually listed - must still be untrusted, from the SOURCE's own default.
    assert contract.trust_of("instruments", "close_price") is Trust.UNTRUSTED


def test_finding_5_the_entity_relative_wildcard_now_actually_matches():
    entry = {"id": "core_banking", "trust_class": "trusted", "untrusted_fields": ["party.*"]}
    contract = SourceContract(entry, path=Path("test://sources.yaml"), estate="test")
    assert contract.trust_of("party", "notes") is Trust.UNTRUSTED
    assert contract.trust_of("party", "legal_name") is Trust.UNTRUSTED
    # A DIFFERENT entity is untouched by an entity-scoped wildcard.
    assert contract.trust_of("facility", "notes") is Trust.TRUSTED


def test_finding_5_trusted_fields_is_a_targeted_promotion_never_a_blanket_one():
    """`inspection_photos`' actual shape: `trust_class: untrusted`, with named exceptions."""
    entry = {"id": "inspection_photos", "trust_class": "untrusted",
             "trusted_fields": ["inspection_id", "captured_by"]}
    contract = SourceContract(entry, path=Path("test://sources.yaml"), estate="test")
    assert contract.trust_of("photo", "inspection_id") is Trust.TRUSTED
    assert contract.trust_of("photo", "captured_by") is Trust.TRUSTED
    # Not named - stays at the source default.
    assert contract.trust_of("photo", "application_id") is Trust.UNTRUSTED


def test_finding_5_untrusted_wins_over_trusted_on_a_contradictory_declaration():
    entry = {"id": "s", "trust_class": "untrusted", "untrusted_fields": ["t.c"],
             "trusted_fields": ["t.c"]}
    contract = SourceContract(entry, path=Path("test://sources.yaml"), estate="test")
    assert contract.trust_of("t", "c") is Trust.UNTRUSTED


def test_finding_5_the_real_finance_contract_still_resolves_correctly(sources_yaml):
    """Round-trip against the real estate, not a synthetic one: `core_banking` is
    `trust_class: trusted`, `party.notes` is individually untrusted, everything else on `party`
    is trusted."""
    from databridge_adapter.sources.contract import load_source_contract

    contract = load_source_contract("core_banking", sources_yaml=sources_yaml)
    assert contract.trust_of("party", "notes") is Trust.UNTRUSTED
    assert contract.trust_of("party", "legal_name") is Trust.TRUSTED
    assert contract.trust_of("party", "party_id") is Trust.TRUSTED


# =======================================================================================
# Finding 6 (Critical) — CLOSED centrally in `PolicyEngine._compile` (`taint.py`
# `require_trusted_scope_column`). Verified here only for postgres.py's own call sites: a role
# scoping `party` through the contract-declared UNTRUSTED `notes` column must never reach
# `PostgresCore._compile`/`_materialise_subqueries` at all.
# =======================================================================================

def test_finding_6_postgres_never_builds_sql_for_an_untrusted_scope_column(
        finance_engine, sources_yaml, _scoped_purpose_vocabulary):
    role = Role(name=ROLE_NAME, description="t", purposes=frozenset({ESTATE_PURPOSE}),
               egress_ceiling="RESTRICTED_INTERNAL", allow={"core_banking": frozenset({"party"})},
               deny_tables=frozenset(), deny_columns=frozenset(), column_masks={},
               deny_sources=frozenset(),
               row_filters={"core_banking.party": RowRule(
                   target="core_banking.party", role=ROLE_NAME, kind="eq",
                   raw="notes = :principal.attr", column="notes", principal_attr="attr")},
               purpose_grants={ESTATE_PURPOSE: frozenset({"*"})})
    role_model = RoleModel(estate="core_banking_probe", version=1, roles={role.name: role},
                           masks={}, classification={}, estate_purposes={ESTATE_PURPOSE: "e"},
                           path=Path("test://roles.yaml"))
    binding = PurposeBinding(estate="core_banking_probe", pack="test",
                             bindings={PACK_PURPOSE: ESTATE_PURPOSE},
                             path=Path("test://purpose_bindings.yaml"))
    directory = {ACTOR: PrincipalBinding(roles=(role.name,), attributes=(("attr", "x"),))}
    engine_policy = PolicyEngine(role_model=role_model, purpose_binding=binding,
                                 directory=directory, default_budget=Budget(max_rows=1000))
    probe = PostgresCore(source_id="core_banking", engine=finance_engine, policy=engine_policy,
                         sources_yaml=sources_yaml)
    req = CapabilityRequest(
        source_id="core_banking", capability="row_read",
        principal=Principal(actor=ACTOR, on_behalf_of=None, purpose=PACK_PURPOSE),
        projection=Projection(entity="party", fields=("party_id", "legal_name"),
                              key=("party_id",)),
        order_by=("party_id",), limit=5)

    with pytest.raises(UntrustedColumnInPolicy, match="core_banking.party.notes"):
        probe.resolve(req)


# =======================================================================================
# Finding 7 (Critical) — `verify()`'s `FORBIDDEN` check only asked "is the principal still in
# the directory", never "would policy still disclose THIS field of THIS row today".
# =======================================================================================

def test_finding_7_verify_catches_a_row_scope_that_no_longer_covers_this_principal(
        core, rm, party_request):
    """RM-007's own citation, re-verified as RM-013 (a DIFFERENT relationship manager under the
    identical `own_portfolio_only` rule): the row is outside RM-013's book, so policy now denies
    what it once allowed - and the old code had no way to notice, because rm-0099 is very much
    still in the directory."""
    result = core.resolve(party_request(principal=rm, limit=500))
    ptr = result.rows.rows[0][0].pointer
    assert core.verify(ptr, principal=rm) is Verdict.VERIFIED

    other_rm = Principal(actor="orchestrator@walnut", on_behalf_of="rm-0099",
                         purpose="lending.underwriting", grant_id="grant-rm-0099")
    assert core.verify(ptr, principal=other_rm) is Verdict.FORBIDDEN


def test_finding_7_verify_with_no_principal_is_unaffected(core, rm, party_request):
    """The control: `verify(pointer)` with no principal never claims FORBIDDEN - there is nobody
    to deny, exactly as the docstring says."""
    result = core.resolve(party_request(principal=rm, limit=500))
    ptr = result.rows.rows[0][0].pointer
    assert core.verify(ptr) is Verdict.VERIFIED


# =======================================================================================
# Finding 8 (Critical) — composite-key pointers were minted even though neither verify() nor
# resolve-mode can ever address one.
# =======================================================================================

def test_finding_8_a_composite_key_entity_is_refused_before_a_pointer_is_minted(
        core, analyst):
    """`facility_account` has PRIMARY KEY (facility_id, account_id) — a real composite key in the
    finance schema, not a hypothetical one."""
    req = CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=analyst,
        projection=Projection(entity="facility_account", fields=("facility_id", "account_id"),
                              key=("facility_id", "account_id")),
        order_by=("facility_id", "account_id"), limit=5)
    with pytest.raises(UnorderedPointeredRead, match="composite primary key"):
        core._require_total_order(req)


# =======================================================================================
# Finding 9 (Critical) — a `contract` for a DIFFERENT source could be attached to a Core.
# =======================================================================================

def test_finding_9_a_foreign_contract_is_refused_at_construction(finance_engine, policy,
                                                                 sources_yaml):
    from databridge_adapter.sources.contract import load_source_contract

    wrong_contract = load_source_contract("crm", sources_yaml=sources_yaml)
    with pytest.raises(ValueError, match="core_banking"):
        PostgresCore(source_id="core_banking", engine=finance_engine, policy=policy,
                    contract=wrong_contract)


# =======================================================================================
# Finding 10 (Critical) — a `derived` pointer's `verify()` combined PARENT verdicts and never
# replayed the derivation itself: "provenance structurally present" read as "derivation
# verified".
# =======================================================================================

@pytest.fixture
def party_contact_row(finance_engine):
    with finance_engine.connect() as conn:
        row = conn.execute(text(
            "SELECT contact_id, email FROM core_banking.party_contact "
            "WHERE email IS NOT NULL LIMIT 1")).mappings().first()
    assert row is not None
    return dict(row)


def test_finding_10_verify_replays_the_derivation_and_catches_a_stale_digest(
        core, party_contact_row):
    from databridge_adapter.derivations import redact_pii

    raw = party_contact_row["email"]
    contact_schema = cell_schema_hash("party_contact", "email", ("contact_id",),
                                      core._reflected_columns("party_contact"))
    raw_ptr = SourcePointer(
        source_id="core_banking", kind="query_cell", table="core_banking.party_contact",
        column="email", row_id=party_contact_row["contact_id"], row_ordinal=0,
        snapshot=SnapshotIdentity(source_id="core_banking", as_of=NOW,
                                  content_hash=cell_digest(raw), schema_hash=contact_schema,
                                  query_hash="qh"),
        query_hash="qh", trust=Trust.UNTRUSTED)
    produced = redact_pii(raw, "email")
    assert produced != raw, "the fixture row must actually redact for this test to mean anything"

    good_ptr = SourcePointer(
        source_id="core_banking", kind="derived", derivation="redact_pii@v1(column=email)",
        derived_from=[raw_ptr], trust=Trust.UNTRUSTED,
        snapshot=SnapshotIdentity(source_id="core_banking", as_of=NOW,
                                  content_hash=cell_digest(produced), schema_hash="sh",
                                  query_hash="qh"),
        query_hash="qh")
    assert core.verify(good_ptr) is Verdict.VERIFIED

    stale_ptr = SourcePointer(
        source_id="core_banking", kind="derived", derivation="redact_pii@v1(column=email)",
        derived_from=[raw_ptr], trust=Trust.UNTRUSTED,
        snapshot=SnapshotIdentity(source_id="core_banking", as_of=NOW,
                                  content_hash="deliberately-not-what-it-produces",
                                  schema_hash="sh", query_hash="qh"),
        query_hash="qh")
    # Before the fix: every parent (just `raw_ptr`) still verifies, so the old code returned
    # VERIFIED without ever looking at `stale_ptr.snapshot.content_hash` or replaying anything.
    assert core.verify(stale_ptr) is Verdict.DRIFT_MATERIAL


def test_finding_10_an_unregistered_derivation_is_unverifiable_not_verified(
        core, party_contact_row):
    raw = party_contact_row["email"]
    contact_schema = cell_schema_hash("party_contact", "email", ("contact_id",),
                                      core._reflected_columns("party_contact"))
    raw_ptr = SourcePointer(
        source_id="core_banking", kind="query_cell", table="core_banking.party_contact",
        column="email", row_id=party_contact_row["contact_id"], row_ordinal=0,
        snapshot=SnapshotIdentity(source_id="core_banking", as_of=NOW,
                                  content_hash=cell_digest(raw), schema_hash=contact_schema,
                                  query_hash="qh"),
        query_hash="qh", trust=Trust.UNTRUSTED)
    ptr = SourcePointer(
        source_id="core_banking", kind="derived", derivation="invent_a_value@v9(column=email)",
        derived_from=[raw_ptr], trust=Trust.UNTRUSTED,
        snapshot=SnapshotIdentity(source_id="core_banking", as_of=NOW, content_hash="anything",
                                  schema_hash="sh", query_hash="qh"),
        query_hash="qh")
    assert core.verify(ptr) is Verdict.UNVERIFIABLE


# =======================================================================================
# Finding 11 (Critical) — schema drift (type/nullability) was invisible to verify(): the cell
# digest alone cannot see a column that changed shape while the cited value stayed byte-equal.
# =======================================================================================

def test_finding_11_verify_detects_schema_drift_even_when_the_value_is_unchanged(core):
    with core._read_transaction() as conn:
        row = conn.execute(text(
            "SELECT party_id, legal_name FROM core_banking.party LIMIT 1")).mappings().first()
    good_schema = cell_schema_hash("party", "legal_name", ("party_id",),
                                   core._reflected_columns("party"))
    base = dict(source_id="core_banking", kind="query_cell", table="core_banking.party",
               column="legal_name", row_id=row["party_id"], row_ordinal=0, query_hash="qh",
               trust=Trust.TRUSTED)

    good_ptr = SourcePointer(**base, snapshot=SnapshotIdentity(
        source_id="core_banking", as_of=NOW, content_hash=cell_digest(row["legal_name"]),
        schema_hash=good_schema, query_hash="qh"))
    assert core.verify(good_ptr) is Verdict.VERIFIED

    stale_ptr = SourcePointer(**base, snapshot=SnapshotIdentity(
        source_id="core_banking", as_of=NOW, content_hash=cell_digest(row["legal_name"]),
        schema_hash="a-schema-hash-from-before-a-migration", query_hash="qh"))
    # Before the fix: `pointer.snapshot.schema_hash` was never even read inside `_verify`.
    assert core.verify(stale_ptr) is Verdict.DRIFT_MATERIAL


def test_finding_11_emitted_pointers_carry_a_per_cell_not_per_projection_schema_hash(
        core, rm, party_request):
    """Two legitimate reads of the SAME unchanged column must stamp the SAME schema_hash on their
    pointers, even though the two reads ask for different OTHER columns - the read-level
    `snap.schema_hash` (whole projection) would make them disagree for no reason."""
    narrow = core.resolve(party_request(
        principal=rm, limit=5, projection=Projection(entity="party", fields=("party_id",),
                                                      key=("party_id",)),
        order_by=("party_id",)))
    wide = core.resolve(party_request(
        principal=rm, limit=5,
        projection=Projection(entity="party", fields=("party_id", "legal_name", "kyc_status"),
                              key=("party_id",)),
        order_by=("party_id",)))
    narrow_hash = narrow.rows.rows[0][0].pointer.snapshot.schema_hash
    wide_hash = wide.rows.rows[0][0].pointer.snapshot.schema_hash
    assert narrow_hash == wide_hash


# =======================================================================================
# Finding 12 (Critical) — an opaque value's content hash fell back to `sha256(repr(value))`,
# which is not contracted to be deterministic.
# =======================================================================================

def test_finding_12_an_opaque_type_refuses_rather_than_hashing_repr():
    class Opaque:
        """No custom `__repr__` — the default includes the object's memory address."""

    with pytest.raises(ValueError, match="canonical form|canonical_scalar"):
        cell_digest(Opaque())


def test_finding_12_ordinary_scalars_still_digest_fine():
    assert cell_digest("hello") == cell_digest("hello")
    assert cell_digest(b"raw bytes") == cell_digest(b"raw bytes")
    assert cell_digest("a") != cell_digest("b")


# =======================================================================================
# describe() view names (flagged by another agent; folded in here per the task brief).
# =======================================================================================

def test_describe_reflects_views_as_well_as_tables(tmp_path):
    """`get_table_names()` alone misses a view a role might scope through
    (`v_team_parties` et al. in a future estate) - dormant today, but a silent describe() gap the
    moment one exists."""
    engine = create_engine(f"sqlite:///{tmp_path / 'views.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE base (id TEXT PRIMARY KEY, val TEXT)"))
        conn.execute(text("CREATE VIEW base_view AS SELECT id, val FROM base"))
    entry = _minimal_entry("viewy")
    contract = SourceContract(entry, path=Path("test://sources.yaml"), estate="test")
    core = PostgresCore(source_id="viewy", engine=engine, policy=_pe_stub(), contract=contract)
    described = core.describe()
    names = {e.name for e in described.entities}
    assert "base" in names
    assert "base_view" in names


def _pe_stub():
    """A minimal, never-invoked `PolicyEngine` — `describe()` does not touch policy at all, but
    `PostgresCore.__init__` refuses construction without one."""
    role_model = RoleModel(estate="viewy_estate", version=1, roles={}, masks={},
                           classification={}, estate_purposes={}, path=Path("test://roles.yaml"))
    binding = PurposeBinding(estate="viewy_estate", pack="test", bindings={},
                             path=Path("test://purpose_bindings.yaml"))
    return PolicyEngine(role_model=role_model, purpose_binding=binding, directory={},
                        default_budget=Budget(max_rows=10))


# =======================================================================================
# High — `PitrWindow` could not be constructed as declared (`pitr.recovery_url` does not exist
# on the dataclass); tier defaults disagreed between `__init__` and `SourceContract`.
# =======================================================================================

def test_high_a_pitrwindow_no_longer_crashes_construction(finance_engine, policy):
    pitr = PitrWindow(mechanism="pitr", retention_days=35, restored_source_id="x")
    core = PostgresCore(source_id="core_banking", engine=finance_engine, policy=policy,
                        sources_yaml=Path(__file__).resolve().parents[3]
                        / "fixtures" / "packs" / "finance" / "sources.yaml",
                        pitr=pitr)
    assert core.pinning_tier in ("T0", "T1", "T2", "T3")


def test_high_the_instance_tier_agrees_with_the_contracts_own_declared_tier(core):
    assert core.pinning_tier == core._contract.declared_tier
    assert core._contract.declared_tier == "T2"          # core_banking, per the estate


# =======================================================================================
# Hardening beyond the numbered findings — a resolve-mode pointer for a DIFFERENT entity than
# the request's projection had no check that it belonged there at all.
# =======================================================================================

def test_hardening_a_resolve_pointer_for_a_different_entity_is_refused(core, analyst):
    foreign_ptr = SourcePointer(
        source_id="core_banking", kind="query_cell", table="core_banking.facility",
        column="facility_id", row_id="FAC-1", row_ordinal=0, query_hash="qh",
        snapshot=SnapshotIdentity(source_id="core_banking", as_of=NOW, content_hash="x",
                                  schema_hash="sh", query_hash="qh"),
        trust=Trust.TRUSTED)
    req = CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=analyst,
        projection=Projection(entity="party", fields=("party_id",), key=("party_id",)),
        resolve=(foreign_ptr,), limit=5)
    with pytest.raises(UnorderedPointeredRead, match="facility"):
        core.resolve(req)


# =======================================================================================
# Hygiene — `snapshot()`/`plan()` now re-verify the admission commitment, like `resolve()`.
# =======================================================================================

def test_hygiene_snapshot_and_plan_recheck_the_admission_commitment():
    import inspect

    assert ".check()" in inspect.getsource(PostgresCore.snapshot)
    assert ".check()" in inspect.getsource(PostgresCore.plan)
    assert ".check()" in inspect.getsource(PostgresCore.resolve)

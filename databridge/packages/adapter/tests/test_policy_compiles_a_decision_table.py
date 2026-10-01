"""The Policy layer (workstream 3C): five screens, then a compiled decision table (§1, §7).

> "Row-level decisions need raw rows, which never leave Core — so Policy compiles a decision table
> for this (principal, purpose, projection) and hands it down at admission; Core evaluates it
> locally and cannot widen it."

Everything here is deterministic and none of it is reachable by a model. The estate's own
`demonstration_cases` in `roles.yaml` name five places "a system usually fails quietly"; four of
them are relational and are tested against the real estate below, and the fifth
(`injection_across_a_boundary`) has its own file.
"""
from __future__ import annotations

import pytest

from databridge_adapter import (
    Budget,
    BudgetExceeded,
    CapabilityNotAdvertised,
    CapabilityRequest,
    EgressCeilingExceeded,
    PolicyEngine,
    PolicyNotApplied,
    Principal,
    PrincipalBinding,
    Projection,
    PurposeNotBound,
    PurposeNotPermitted,
    RowRuleNotCompilable,
    compile_row_filter,
    load_purpose_bindings,
    load_role_model,
)
from databridge_core import GroundedValue, Trust, Withheld
from databridge_adapter.testing.estate import ROLES_YAML


# =======================================================================================
# The role model, read from the estate's own file.
# =======================================================================================

def test_the_role_model_keeps_tables_columns_and_rows_as_three_decisions(role_model):
    """"Losing any one of them collapses the policy into 'can this person query this database',
    which is not a policy." So they are three structures, not one merged allow-list."""
    rm = role_model.require_role("relationship_manager")
    assert rm.allow and rm.deny_columns and rm.row_filters
    assert rm.grants_entity("core_banking", "party")
    assert not rm.grants_entity("sanctions_watchlist", "anything")
    assert rm.denies_column("core_banking", "party", "notes")
    assert rm.row_rule_for("core_banking", "party") is not None


def test_extends_replaces_a_row_filter_rather_than_unioning_it(role_model):
    """`senior_relationship_manager` widens two filters. Unioning would leave the narrower
    own-book rule in force beside the wider team rule, and the role would silently not do what it
    says."""
    srm = role_model.require_role("senior_relationship_manager")
    rule = srm.row_rule_for("core_banking", "party")
    assert rule.kind == "in_subquery" and rule.subquery.table == "relationship_manager"
    assert srm.grants_entity("core_banking", "party"), "inherited allow-list survived"


def test_an_uncompilable_row_filter_is_recorded_not_dropped():
    """A rule that silently does not run is a policy that permits everything while appearing not
    to. `core_banking.txn`'s three-table JOIN needs the predicate grammar of §6."""
    rule = compile_row_filter(
        "core_banking.txn", "relationship_manager",
        "account_id IN (SELECT account_id FROM core_banking.account a "
        "JOIN core_banking.party p USING (party_id) WHERE p.primary_rm_code = :principal.rm_code)")
    assert rule.kind == "uncompilable" and not rule.compilable
    assert rule.raw


def test_a_role_bound_to_an_undeclared_mask_is_a_load_error(tmp_path):
    bad = tmp_path / "roles.yaml"
    bad.write_text(
        "version: 1\nestate: e\npurposes: [{id: p}]\nmasks: {}\n"
        "roles:\n  r:\n    purposes: [p]\n    column_masks: {'a.b.c': nope}\n", encoding="utf-8")
    with pytest.raises(Exception, match="undeclared mask"):
        load_role_model(bad)


# =======================================================================================
# Screen 1 — capability.
# =======================================================================================

def test_an_unadvertised_capability_is_rejected_and_never_reconsidered(core, analyst):
    """§10: "reject; log; do not escalate to a model.\""""
    req = CapabilityRequest(
        source_id="core_banking", capability="bulk_export", principal=analyst,
        projection=Projection(entity="party", fields=("party_id",), key=("party_id",)),
        order_by=("party_id",), limit=5)
    with pytest.raises(CapabilityNotAdvertised, match="advertises"):
        core.resolve(req)


# =======================================================================================
# Screen 3 — purpose. The estate's `purpose_mismatch` demonstration case.
# =======================================================================================

def test_holding_data_for_one_purpose_is_not_permission_to_use_it_for_another(core, packs_purposes,
                                                                             party_request):
    """> "principal: {role: relationship_manager, purpose: financial_crime_compliance}
    > ask: Screen my portfolio against the watchlist.
    > expect: FORBIDDEN. The role holds no grant for that purpose."
    """
    rm_screening = Principal(actor="orchestrator@walnut", on_behalf_of="rm-0042",
                             purpose="kyc.screening", grant_id="g")
    with pytest.raises(PurposeNotPermitted, match="not permission to use it for another"):
        core.resolve(party_request(principal=rm_screening))


def test_a_pack_purpose_bound_to_no_estate_purpose_is_refused(core, packs_purposes,
                                                              party_request):
    """`finance.graph_build` is deliberately unbound — see `config/purpose_bindings.yaml`. The
    estate has no build purpose, and the two nearest candidates are both wrong in the dangerous
    direction, so the read is a refusal naming both files rather than a silent choice."""
    crawler = Principal(actor="orchestrator@walnut", on_behalf_of="analyst-7",
                        purpose="finance.graph_build")
    with pytest.raises(PurposeNotBound, match="bound to no purpose"):
        core.resolve(party_request(principal=crawler))


def test_an_unknown_principal_is_refused_rather_than_treated_as_role_less(core, packs_purposes,
                                                                         party_request):
    stranger = Principal(actor="somebody@else", on_behalf_of="nobody-we-know",
                         purpose="lending.underwriting", grant_id="g")
    with pytest.raises(PurposeNotPermitted, match="no directory entry"):
        core.resolve(party_request(principal=stranger))


def test_an_expired_role_is_refused_on_the_day(role_model, packs_purposes, core, party_request):
    """`external_auditor` carries `expires_on: 2026-11-30`. An expiry that is not enforced on the
    day is a grant with a comment on it."""
    from datetime import date

    binding = load_purpose_bindings(role_model.estate,
                                    known_estate_purposes=role_model.estate_purposes)
    engine = PolicyEngine(role_model=role_model, purpose_binding=binding,
                          directory={"ext-1": PrincipalBinding(roles=("external_auditor",))},
                          today=date(2026, 12, 1))
    principal = Principal(actor="orchestrator@walnut", on_behalf_of="ext-1",
                          purpose="finance.pointer_verification")
    req = CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=principal,
        projection=Projection(entity="facility", fields=("facility_id", "status"),
                              key=("facility_id",)),
        order_by=("facility_id",), limit=5)
    with pytest.raises(PurposeNotPermitted, match="POLICY_GRANT_EXPIRED"):
        engine.admit(req, description=core.describe())


# =======================================================================================
# Screen 4 — budget. Screen 5 — egress.
# =======================================================================================

def test_the_budget_refuses_before_the_call_not_after(role_model, core, party_request):
    """"After the fact is too late: the cost has already been incurred and, for an egress-classed
    source, the rows have already left.\""""
    binding = load_purpose_bindings(role_model.estate,
                                    known_estate_purposes=role_model.estate_purposes)
    engine = PolicyEngine(role_model=role_model, purpose_binding=binding,
                          directory={"analyst-7": PrincipalBinding(roles=("credit_analyst",))},
                          default_budget=Budget(max_rows=10))
    with pytest.raises(BudgetExceeded, match="refused before it runs"):
        engine.admit(party_request(limit=5000), description=core.describe())


def test_an_aggregate_only_role_may_not_receive_rows(core, packs_purposes, party_request):
    """`portfolio_analyst` is `aggregate_only: true` with `on_small_group: refuse`. A row read
    emits named rows and there is no rounding that makes it an aggregate."""
    quant = Principal(actor="orchestrator@walnut", on_behalf_of="quant-2",
                      purpose="lending.portfolio_reporting")
    with pytest.raises(EgressCeilingExceeded):
        core.resolve(party_request(principal=quant))


def test_the_egress_ceiling_is_checked_against_the_sources_class(core, packs_purposes,
                                                                 party_request):
    """`core_banking` is RESTRICTED_INTERNAL; a role whose ceiling is INTERNAL is refused it.

    **A consequence worth naming, because it looks like a bug and is not.** The estate's
    `evaluation_order` puts the egress ceiling at stage 7 and the column projection at stage 9, so
    the ceiling is compared to the OBJECT's class before anything is known about which columns
    would be disclosed. `auditor` and `platform_operator` both carry an INTERNAL ceiling and
    `allow: {core_banking: ["*"]}`, and both are therefore refused this source outright — even
    though `column_masks: {"*": redact}` means neither would have received a value.

    The softer reading (compare the ceiling to what would actually be disclosed) is tempting and
    is not what the file says. An object-level ceiling that a sufficiently redacted projection can
    slip past is not a ceiling, so the declared order is followed and the consequence recorded.
    """
    assert core.describe().egress_class == "RESTRICTED_INTERNAL"
    auditor = Principal(actor="orchestrator@walnut", on_behalf_of="audit-1",
                        purpose="finance.pointer_verification")
    with pytest.raises(EgressCeilingExceeded, match="POLICY_EGRESS_CEILING"):
        core.resolve(party_request(principal=auditor))


# =======================================================================================
# The compiled table, and what the Core may do with it.
# =======================================================================================

def test_the_table_is_compiled_for_one_principal_purpose_and_projection(core, policy,
                                                                        party_request):
    req = party_request(projection=Projection(
        entity="party", fields=("party_id", "legal_name", "notes"), key=("party_id",)),
        order_by=("party_id",))
    admission = policy.admit(req, description=core.describe())
    table = admission.decisions
    assert table.entity == "party" and table.estate_purpose == "credit_assessment"
    assert {c.column for c in table.columns} == {"party_id", "legal_name", "notes"}
    assert table.rule_for("notes").withheld and not table.rule_for("legal_name").withheld
    assert admission.decisions_digest == table.digest


def test_a_column_outside_the_admission_is_not_permitted_it_is_missed(core, policy,
                                                                      party_request):
    """"Defaulting it to disclosed is how a policy engine quietly stops applying.\""""
    admission = policy.admit(party_request(), description=core.describe())
    with pytest.raises(PolicyNotApplied, match="has not been permitted"):
        admission.decisions.rule_for("date_of_birth")


def test_an_admission_cannot_carry_a_table_it_did_not_commit_to(core, policy, party_request):
    from databridge_adapter import Admission

    admission = policy.admit(party_request(), description=core.describe())
    with pytest.raises(PolicyNotApplied, match="changed after they were admitted"):
        Admission(**{**admission.model_dump(), "decisions": admission.decisions,
                     "decisions_digest": "0" * 64})


def test_a_core_cannot_be_built_without_a_policy_engine(finance_engine, sources_yaml):
    """Appendix A: "a caller must not be able to invoke Core directly and skip Policy; today this
    is stated, not enforced." This is the in-process half of that enforcement."""
    import inspect

    from databridge_adapter.sources.postgres import PostgresCore

    params = inspect.signature(PostgresCore.__init__).parameters
    assert params["policy"].default is inspect.Parameter.empty, (
        "a Core with an optional policy is a Core with no policy")
    for verb in ("describe", "snapshot", "plan", "resolve", "verify"):
        sig = inspect.signature(getattr(PostgresCore, verb))
        assert not any("admission" in p or "decision" in p or "policy" in p
                       for p in sig.parameters), (
            f"{verb} takes something a caller could use to supply a wider decision table")


def test_the_row_rule_narrows_the_read_and_says_so(core, rm, party_request):
    """`roles.yaml`'s `own_portfolio_only`: "Other borrowers are not filtered out silently - the
    response states that N rows were withheld by the portfolio rule.\""""
    result = core.resolve(party_request(principal=rm, limit=500))
    assert result.warnings, "a scoped read that declares nothing looks complete"
    assert any("limited to your scope" in w for w in result.warnings)
    assert 0 < len(result.rows.rows) < 100, "RM-007 owns a handful of the 466 parties"


def test_a_scoped_response_declares_the_scope_and_never_the_count(core, rm, party_request):
    """`existence_disclosure`: "a response... carries a CONSTANT scope declaration... and NEVER a
    count of what was excluded. **A count is cardinality and cardinality is the leak.**"

    An earlier version of this Core reported "N rows withheld by a row-level rule", which is
    exactly the oracle the estate names: repeat the read with a narrowing projection and the count
    enumerates the bank's client list one refusal at a time.
    """
    import re

    result = core.resolve(party_request(principal=rm, limit=500))
    for warning in result.warnings:
        assert not re.search(r"\b\d+\s+row", warning), (
            f"the scope declaration carries a count: {warning!r}")
    # And it is CONSTANT - the same declaration whatever the projection.
    other = core.resolve(party_request(
        principal=rm, limit=500,
        projection=Projection(entity="party", fields=("party_id", "kyc_status"),
                              key=("party_id",)),
        order_by=("party_id",)))
    assert set(result.warnings) == set(other.warnings)


def test_the_row_rule_actually_restricts_the_rows(core, rm, analyst, party_request, finance_engine):
    from sqlalchemy import text

    scoped = core.resolve(party_request(principal=rm, limit=500))
    ids = {row[0].value for row in scoped.rows.rows}
    with finance_engine.connect() as conn:
        expected = {r[0] for r in conn.execute(text(
            "SELECT party_id FROM core_banking.party WHERE primary_rm_code = 'RM-007'"))}
    assert ids == expected and ids
    everyone = {row[0].value for row in core.resolve(
        party_request(principal=analyst, limit=500)).rows.rows}
    assert ids < everyone, "the RM sees strictly fewer parties than the unrestricted analyst"


def test_a_row_rule_the_table_cannot_express_refuses_the_read(core, rm, analyst):
    """Fails CLOSED. `core_banking.txn`'s filter is a three-table join; the read is refused rather
    than served with the rule dropped."""
    req = CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=rm,
        projection=Projection(entity="txn", fields=("txn_id", "amount"), key=("txn_id",)),
        order_by=("txn_id",), limit=5)
    with pytest.raises(RowRuleNotCompilable, match="POLICY_ROW_SCOPE_UNCOMPILABLE"):
        core.resolve(req)


def test_an_allowed_object_with_no_declared_row_scope_fails_closed(core, role_model,
                                                                   packs_purposes, party_request):
    """`row_scope_completeness`: "EVERY object in a role's `allow` must appear in EITHER that
    role's `row_filters` OR its `unscoped_objects` list... There is no default scope, and 'the
    client will join through a filtered parent' is not a policy."

    Built by removing one `unscoped_objects` entry from a real role, so the test exercises the
    refusal rather than a synthetic fixture that happens to be missing a key.
    """
    import dataclasses

    from databridge_adapter.sources.postgres import PostgresCore

    analyst_role = role_model.require_role("credit_analyst")
    stripped = dataclasses.replace(analyst_role, unscoped_objects=frozenset())
    patched = dict(role_model.roles)
    patched["credit_analyst"] = stripped
    from databridge_adapter import RoleModel

    model = RoleModel(estate=role_model.estate, version=role_model.version, roles=patched,
                      masks=role_model.masks, classification=role_model.classification,
                      estate_purposes=role_model.estate_purposes, path=role_model.path,
                      reason_codes=role_model.reason_codes,
                      free_text_exceptions=role_model.free_text_exceptions)
    binding = load_purpose_bindings(model.estate, known_estate_purposes=model.estate_purposes)
    engine = PolicyEngine(role_model=model, purpose_binding=binding,
                          directory={"analyst-7": PrincipalBinding(roles=("credit_analyst",))})
    core2 = PostgresCore(source_id="core_banking", engine=core._engine, policy=engine,
                         contract=core._contract)
    with pytest.raises(RowRuleNotCompilable, match="POLICY_ROW_SCOPE_UNDECLARED"):
        core2.resolve(party_request())


def test_a_cross_source_row_rule_refuses_rather_than_reaching_into_another_source(
        core, role_model, packs_purposes, party_request):
    """`senior_relationship_manager` filters core_banking rows through `crm.relationship_manager`.
    One request reaches one adapter and one adapter holds one source's credentials."""
    binding = load_purpose_bindings(role_model.estate,
                                    known_estate_purposes=role_model.estate_purposes)
    engine = PolicyEngine(
        role_model=role_model, purpose_binding=binding,
        directory={"srm-1": PrincipalBinding(roles=("senior_relationship_manager",),
                                             attributes=(("team", "SG-CORP"),))})
    principal = Principal(actor="orchestrator@walnut", on_behalf_of="srm-1",
                          purpose="lending.underwriting", grant_id="g")
    with pytest.raises(RowRuleNotCompilable, match="another source"):
        engine.admit(party_request(principal=principal), description=core.describe())


def test_an_unbound_principal_attribute_fails_closed(core, role_model, packs_purposes,
                                                     party_request):
    """An RM with no `rm_code` must see NOTHING, not everything. A missing WHERE clause is the
    direction that leaks."""
    binding = load_purpose_bindings(role_model.estate,
                                    known_estate_purposes=role_model.estate_purposes)
    engine = PolicyEngine(role_model=role_model, purpose_binding=binding,
                          directory={"rm-x": PrincipalBinding(roles=("relationship_manager",))})
    from databridge_adapter.sources.postgres import PostgresCore

    core2 = PostgresCore(source_id="core_banking", engine=core._engine, policy=engine,
                         contract=core._contract)
    principal = Principal(actor="orchestrator@walnut", on_behalf_of="rm-x",
                          purpose="lending.underwriting", grant_id="g")
    assert core2.resolve(party_request(principal=principal, limit=50)).rows.rows == ()


# =======================================================================================
# Masks, and the fourth cell state that does not exist.
# =======================================================================================

def test_a_masked_column_is_a_denial_carrying_the_masks_name(core, policy, packs_purposes):
    """Every mask in `roles.yaml` declares `cell_type: Withheld`. Honoured rather than
    reinterpreted: inventing a partial-disclosure cell state (O1b, still open) would put an
    uncitable value behind a pointer.

    The role used here is `auditor`, whose `column_masks: {"*": redact}` is the only mask in the
    estate that is actually REACHABLE — see
    `test_three_declared_masks_in_the_estate_are_unreachable` for the other three and why.
    """
    auditor = Principal(actor="orchestrator@walnut", on_behalf_of="audit-1",
                        purpose="finance.pointer_verification")
    req = CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=auditor,
        projection=Projection(entity="party", fields=("party_id", "legal_name"),
                              key=("party_id",)),
        order_by=("party_id",), limit=5)
    # `core_banking` is RESTRICTED_INTERNAL and the auditor's ceiling is INTERNAL, so the object
    # never reaches stage 9 against the real source. The mask path is exercised against the same
    # description with an INTERNAL egress class - the ONE fact changed, so everything else about
    # the decision is the estate's own.
    internal = core.describe().model_copy(update={"egress_class": "INTERNAL"})
    table = policy.admit(req, description=internal).decisions
    rule = table.rule_for("legal_name")
    assert rule.withheld
    assert rule.reason_code == "POLICY_COLUMN_DENIED"      # the `redact` mask's declared reason
    assert rule.decided_by == "auditor"


def test_no_mask_over_core_banking_is_reachable_in_this_estate(role_model):
    """**A finding about the estate, asserted so it cannot be quietly lost.**

    Every role that carries a `column_masks` entry over `core_banking` also carries an INTERNAL
    egress ceiling, and `core_banking` is RESTRICTED_INTERNAL — so stage 7 refuses the object
    before stage 9 can mask a column of it. The masks that DO cover core_banking
    (`model_developer`'s sixteen `pseudonym_engagement` / `band` / `month_only` bindings, and the
    `redact` wildcards on `auditor` and `platform_operator`) are therefore unreachable against
    this source as the file currently stands.

    That is not an engine bug and it is not fixed here — `roles.yaml` is another workstream's
    file, and the alternative reading of stage 7 is worse (see
    `test_the_egress_ceiling_is_checked_against_the_sources_class`). Asserting it keeps the
    estate's intent and the engine's behaviour from diverging silently.
    """
    masked_over_core_banking = {
        name: role for name, role in role_model.roles.items()
        if any(path.startswith("core_banking.") or path == "*" for path in role.column_masks)
    }
    assert masked_over_core_banking, "the estate does declare masks over core_banking"
    for name, role in masked_over_core_banking.items():
        assert role.egress_ceiling == "INTERNAL", (
            f"{name} masks a core_banking column and could actually reach it; this finding is "
            f"stale and the test should become an end-to-end one")


def test_a_sensitive_column_is_disclosed_through_a_replayable_derivation(core, compliance):
    """The §4a redaction path, end to end: the emitted value is not the cited value, so the
    pointer is `derived`, it cites the raw cell, and its derivation REPLAYS to exactly the value
    that was emitted."""
    from databridge_adapter import assert_derived_from, assert_grounded

    req = CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=compliance,
        projection=Projection(entity="party_contact", fields=("contact_id", "email"),
                              key=("contact_id",)),
        order_by=("contact_id",), limit=10)
    result = core.resolve(req)
    derived = [row[1] for row in result.rows.rows
               if isinstance(row[1], GroundedValue) and row[1].pointer.kind == "derived"]
    assert derived, "party_contact.email is declared sensitive and must be redacted on disclosure"
    cell = derived[0]
    assert cell.value == "<REDACTED:email>"
    raw = cell.pointer.derived_from[0]
    assert raw.kind == "query_cell" and raw.column == "email"
    assert_derived_from(cell.pointer, raw)
    assert cell.pointer.derivation == "redact_pii@v1(column=email)"
    # The wall replays the rule over a real source value and demands the emitted result.
    assert_grounded(cell, fetched="ops@example.test")
    assert cell.trust is raw.trust, "redaction does not launder trust"


def test_the_metadata_only_role_is_refused_the_source_before_it_reaches_a_column(core,
                                                                                packs_purposes,
                                                                                party_request):
    """`platform_operator`: "Row counts and column names are metadata; a single cell value is not."

    Its ceiling is INTERNAL, so stage 7 refuses `core_banking` outright and stage 9's
    `column_masks: {"*": redact}` is never reached. The role IS bounded — more tightly than
    intended, in fact — and the refusal is the estate's declared order rather than a choice this
    engine made. See `test_no_mask_over_core_banking_is_reachable_in_this_estate`.
    """
    ops = Principal(actor="orchestrator@walnut", on_behalf_of="ops-1",
                    purpose="finance.source_health")
    with pytest.raises(EgressCeilingExceeded, match="POLICY_EGRESS_CEILING"):
        core.resolve(party_request(principal=ops, limit=5))


def test_a_metadata_only_role_withholds_every_cell_where_it_can_read_at_all(core, policy,
                                                                           packs_purposes,
                                                                           party_request):
    """The other half: where the ceiling permits the object, every cell still comes back withheld.

    Same single-fact change as the mask test — the description's egress class — so that what is
    being exercised is stage 9 and nothing else.
    """
    ops = Principal(actor="orchestrator@walnut", on_behalf_of="ops-1",
                    purpose="finance.source_health")
    internal = core.describe().model_copy(update={"egress_class": "INTERNAL"})
    table = policy.admit(party_request(principal=ops), description=internal).decisions
    assert table.columns
    for rule in table.columns:
        assert rule.withheld and rule.reason_code == "POLICY_COLUMN_DENIED"


def test_the_stub_decision_table_still_raises(crawler):
    """3A's honest placeholder is kept beside the real engine, because it names the half of
    Appendix A's item that is still missing: cross-process authorisation."""
    from databridge_adapter import NotYetSpecifiedDecisionTable

    with pytest.raises(NotImplementedError, match="Appendix A"):
        NotYetSpecifiedDecisionTable().decide("party", "notes", {}, crawler)

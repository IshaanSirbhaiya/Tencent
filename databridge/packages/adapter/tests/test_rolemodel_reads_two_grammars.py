"""`roles.yaml` is written in TWO grammars, and both have to work — not merely parse.

The government estate's `roles:` block is a LIST of roles, each carrying a list of `grants` with
per-grant `purposes`, `columns`, `withheld`, `masked` and a `row_scope` written against
named `@aliases`. An earlier partial fix made that file *parse* — seven roles loaded — while
every role came back with **zero grants**. It failed closed, which is the safe direction, and it
read as success, which is the dangerous one: nothing downstream can tell an empty role model from a
working one.

These tests pin the three things that were actually wrong:

1. the government estate loads WITH its grants, and every role has some;
2. grant atomicity survives the read — rows and columns are never unioned across grants, and a row
   scope this build cannot compile refuses the read rather than being dropped;
3. the finance estate, in the other grammar, loads exactly as it did.

They also pin the three ideas `rolemodel`'s own docstring says the types exist to preserve, in the
new grammar: tables/columns/rows stay three separate decisions, `Withheld` is not `Absent`, and
free text is classified before it is authorised.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from databridge_adapter import (
    GRAMMAR_GRANT_LIST,
    GRAMMAR_PERMISSION_LISTS,
    RoleModelError,
    load_role_model,
    resolve_scope_aliases,
)

#: packages/adapter/tests/ -> packages/adapter -> packages -> databridge/
DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]
GOVERNMENT_ROLES = DATABRIDGE_ROOT / "fixtures" / "packs" / "government" / "roles.yaml"
FINANCE_ROLES = DATABRIDGE_ROOT / "fixtures" / "packs" / "finance" / "roles.yaml"


@pytest.fixture(scope="module")
def government():
    return load_role_model(GOVERNMENT_ROLES)


@pytest.fixture(scope="module")
def finance():
    return load_role_model(FINANCE_ROLES)


@pytest.fixture(scope="module")
def government_source() -> dict:
    return yaml.safe_load(GOVERNMENT_ROLES.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data: dict, name: str = "roles.yaml") -> Path:
    target = tmp_path / name
    target.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return target


def _minimal(**overrides) -> dict:
    """The smallest `grant_list` file that loads, so a test can break exactly one thing."""
    data = {
        "version": 2,
        "estate": "test-estate",
        "purposes": [{"id": "case_determination", "description": "deciding a case"}],
        "reason_codes": {"POLICY_DATA_MINIMIZATION": "not minimum necessary",
                         "POLICY_ROLE_SCOPE": "outside this role"},
        "scopes": {"@my_caseload": {
            "returns": "application_id",
            "definition": "SELECT application_id FROM permits.applications "
                          "WHERE assigned_officer = :principal.officer_ref"}},
        "roles": [{
            "id": "caseworker",
            "egress_ceiling": "RESTRICTED_INTERNAL",
            "grants": [{
                "object": "permits.applications",
                "purposes": ["case_determination"],
                "row_scope": {"predicate": "assigned_officer = :principal.officer_ref",
                              "outside": "existence_not_disclosed"},
                "columns": ["application_id", "status"],
            }],
        }],
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------------------
# 1. the government estate loads WITH its grants
# ---------------------------------------------------------------------------------------


def test_the_government_estate_is_read_in_the_grant_list_grammar(government):
    assert government.grammar == GRAMMAR_GRANT_LIST
    assert government.estate == "government"
    assert government.bundle_version == "government@v2"


def test_every_government_role_loads_with_a_non_zero_number_of_grants(government,
                                                                    government_source):
    """The bug this file exists for. Seven roles loaded and granted NOTHING."""
    census = dict(government.grant_census())
    assert set(census) == {"permits_caseworker", "permits_inspector", "benefits_caseworker",
                           "benefits_team_lead", "auditor", "public_records_officer",
                           "adapter_service"}
    assert all(count > 0 for count in census.values()), census
    # Derived (HC2, SWEEP #24-34): each role resolves to one grant per distinct object it names in
    # its YAML — its own `grants` and `overrides`, plus everything its `extends` parent resolves to
    # (`combination_semantics.extends`: an override REPLACES the parent's grant for that object).
    # Read from the raw file, not recalled, so a grant added to the estate widens the expectation.
    entries = {role["id"]: role for role in government_source["roles"]}

    def objects(name: str) -> set:
        entry = entries[name]
        inherited = objects(entry["extends"]) if entry.get("extends") else set()
        return inherited | {g["object"] for kind in ("grants", "overrides")
                            for g in (entry.get(kind) or ())}

    declared = {name: len(objects(name)) for name in entries}
    assert any(e.get("extends") for e in entries.values())   # the inheritance branch is exercised
    assert sum(declared.values()) >= len(declared) >= 1                  # non-vacuous
    assert census == declared


def test_a_grant_list_role_that_resolves_to_zero_grants_is_a_LOAD_error(tmp_path):
    """Loud, not empty. "An empty role model that loads cleanly is indistinguishable from a
    working one to everything downstream", which is exactly how this estate went un-enforced.
    """
    data = _minimal()
    data["roles"].append({"id": "ghost", "egress_ceiling": "INTERNAL", "grants": []})
    with pytest.raises(RoleModelError, match="ZERO grants"):
        load_role_model(_write(tmp_path, data))


def test_a_grant_carries_the_whole_tuple_not_a_scattering_of_lists(government):
    """`(object, row_scope, columns, transformations, purposes, egress_ceiling)` on ONE object."""
    grant = government.require_role("permits_caseworker").grant_for("permits", "applications")
    assert grant is not None
    assert grant.object == "permits.applications"
    assert grant.purposes == frozenset({"case_determination", "correspondence",
                                        "service_delivery"})
    assert grant.egress_ceiling == "RESTRICTED_INTERNAL"
    assert "application_id" in grant.columns and "site_description" not in grant.columns
    assert grant.transforms["site_description"] == "classify_only"
    assert grant.row_scope is not None
    assert grant.row_scope.describe() == "assigned_officer = :principal.officer_ref"
    assert grant.outside == "existence_not_disclosed"
    assert grant.outside_reason == "POLICY_SCOPE_UNDISCLOSED"


def test_purpose_is_declared_per_grant_not_per_role(government):
    """R-002 finding C4 / D5: purpose moved role -> grant deliberately.

    `permits.appeals` is reachable under `case_determination` only. Under `service_delivery` —
    which the SAME role holds, and which reaches `permits.applications` — it reaches nothing. The
    role-level union is not a grant.
    """
    role = government.require_role("permits_caseworker")
    assert "service_delivery" in role.purposes and "case_determination" in role.purposes
    assert role.grant_for("permits", "appeals", "case_determination") is not None
    assert role.grant_for("permits", "appeals", "service_delivery") is None
    assert role.grant_for("permits", "applications", "service_delivery") is not None
    assert role.purpose_reaches("case_determination", "permits", "appeals")
    assert not role.purpose_reaches("service_delivery", "permits", "appeals")


def test_a_grant_with_no_purposes_fails_the_loader(tmp_path):
    """"A grant with no `purposes` list is INVALID and must fail the policy loader, not default
    to the role's list." Defaulting is the escalation the move to per-grant purpose prevented."""
    data = _minimal()
    del data["roles"][0]["grants"][0]["purposes"]
    with pytest.raises(RoleModelError, match="declares no `purposes`"):
        load_role_model(_write(tmp_path, data))


# ---------------------------------------------------------------------------------------
# 2. grant atomicity, and row scopes that cannot be compiled
# ---------------------------------------------------------------------------------------


def test_no_role_holds_two_grants_over_one_object(government):
    """Grant atomicity, as an invariant of the loaded model rather than a paragraph.

    Two grants over one object cannot be flattened into the single row scope and single column
    policy this build compiles without pairing grant A's rows with grant B's columns — "there is
    no path by which grant A's rows meet grant B's columns", and a merge would BE that path.
    """
    for name, role in government.roles.items():
        objects = [g.object for g in role.grants]
        assert len(objects) == len(set(objects)), f"{name} declares an object twice"


def test_two_grants_over_one_object_are_refused_at_load(tmp_path):
    data = _minimal()
    data["roles"][0]["grants"].append({
        "object": "permits.applications",
        "purposes": ["case_determination"],
        "row_scope": {"predicate": None, "outside": "n/a"},
        "columns": ["site_description"],
    })
    with pytest.raises(RoleModelError, match="two grants over"):
        load_role_model(_write(tmp_path, data))


def test_rows_and_columns_come_from_the_same_grant_and_are_never_unioned(government):
    """The atomic read and the flat projection `policy.py` compiles must agree, object by object.

    If any object's row scope or column policy had been unioned across two grants, the flattened
    `row_filters[object]` would differ from the one grant's own `row_scope`.
    """
    for role in government.roles.values():
        for grant in role.grants:
            if grant.conditional:
                continue
            flat = role.row_filters.get(grant.object)
            if grant.row_scope is None:
                assert flat is None
                assert grant.object in role.unscoped_objects
            else:
                assert flat is grant.row_scope


def test_a_named_scope_alias_resolves_to_the_estates_own_definition(government):
    """`@permits.my_caseload` is written once in `scopes:` and cannot be forgotten (finding C2)."""
    rule = government.require_role("permits_caseworker").grant_for("permits", "decisions").row_scope
    assert rule.kind == "in_subquery"
    assert rule.subquery.qualified == "permits.applications"
    assert rule.subquery.where_principal_attr == "officer_ref"


def test_a_two_hop_scope_refuses_the_read_rather_than_running_unfiltered(government):
    """`@permits.my_decisions` selects over `@permits.my_caseload` — two hops, which this build's
    compiler cannot express. It compiles to `uncompilable`, which `policy.py` turns into a REFUSAL
    at admission. "A row rule that silently does not run is the exact shape of a policy that
    permits everything while appearing not to."

    The raw text keeps the unexpanded inner alias, so the refusal names the two-hop scope a human
    wrote rather than a machine-generated string nobody would recognise.
    """
    role = government.require_role("permits_caseworker")
    rule = role.grant_for("permits", "appeals").row_scope
    assert not rule.compilable
    assert "@permits.my_caseload" in rule.raw
    assert not role.grant_for("permits", "appeals").compilable
    # And it is NOT quietly turned into "no scope", which would read as "every row".
    assert "permits.appeals" not in role.unscoped_objects
    assert role.row_filters["permits.appeals"] is rule


def test_an_undeclared_scope_alias_is_a_load_error_not_an_uncompilable_rule(tmp_path):
    """A typo and a policy this build cannot express must not look the same: only one of them is
    fixed by editing the file."""
    data = _minimal()
    data["roles"][0]["grants"][0]["row_scope"]["predicate"] = "application_id IN @my_caselaod"
    with pytest.raises(RoleModelError, match="does not declare"):
        load_role_model(_write(tmp_path, data))


def test_resolve_scope_aliases_expands_exactly_one_level(tmp_path):
    scopes = {"@a": {"definition": "SELECT x FROM t WHERE y = :principal.z"},
              "@b": {"definition": "SELECT p FROM q WHERE x IN @a"}}
    one_hop = resolve_scope_aliases("x IN @a", scopes, where="test", path=tmp_path)
    assert one_hop == "x IN (SELECT x FROM t WHERE y = :principal.z)"
    two_hop = resolve_scope_aliases("p IN @b", scopes, where="test", path=tmp_path)
    assert "@a" in two_hop, "the inner alias stays unexpanded, so the rule stays uncompilable"


def test_an_untrusted_field_may_not_decide_access(tmp_path):
    """D3, and the estate's own `enforcement` line: "policy loader rejects any grant whose
    row_scope predicate names one of these"."""
    data = _minimal()
    data["untrusted_fields_forbidden_in_predicates"] = {
        "fields": ["permits.applications.agent_of_record"]}
    data["roles"][0]["grants"][0]["row_scope"]["predicate"] = (
        "agent_of_record = :principal.officer_ref")
    with pytest.raises(RoleModelError, match="forbids"):
        load_role_model(_write(tmp_path, data))


def test_an_untrusted_field_is_caught_inside_a_subquery_too(tmp_path):
    """The forbidden column is checked against every object the predicate READS, not only the
    grant's own — otherwise a subquery selecting from a second table on an attacker-controlled
    column walks straight past the check."""
    data = _minimal()
    data["untrusted_fields_forbidden_in_predicates"] = {
        "fields": ["correspondence.claimed_case_ref"]}
    data["roles"][0]["grants"][0]["row_scope"]["predicate"] = (
        "application_id IN (SELECT claimed_case_ref FROM correspondence "
        "WHERE claimed_case_ref = :principal.officer_ref)")
    with pytest.raises(RoleModelError, match="POLICY_UNTRUSTED_PREDICATE"):
        load_role_model(_write(tmp_path, data))


def test_a_wildcard_projection_gets_no_closed_world_withholdings(government):
    """`columns: "*"` is the estate declaring the whole object readable, with a written
    justification. The closed world has nothing to close, and inventing withholdings there would
    contradict a grant the file wrote deliberately."""
    grant = government.require_role("permits_caseworker").grant_for("permits", "permit_types")
    assert grant.wildcard_columns
    assert grant.wildcard_justification and "reference catalogue" in grant.wildcard_justification
    assert grant.column_disposition("anything_at_all") == ("disclosed", None)
    role = government.require_role("permits_caseworker")
    assert not [k for k in role.column_masks if k.startswith("permits.permit_types.")]


def test_the_government_estate_declares_no_forbidden_predicate(government_source, government):
    """The estate itself is clean, so the check above is a guard and not a description of it."""
    forbidden = set(government_source["untrusted_fields_forbidden_in_predicates"]["fields"])
    for role in government.roles.values():
        for grant in role.grants:
            if grant.row_scope is None:
                continue
            assert not [f for f in forbidden if f in grant.row_scope.raw]


def test_a_conditional_grant_reaches_nothing_until_its_condition_can_be_evaluated(government):
    """Review finding H11. `adapter_service` is a TEMPLATE whose every grant is conditional on a
    `bound_source_id` that comes from the CREDENTIAL: "a grant whose condition is false is not
    merely filtered -- it does not exist for that instance."

    This build cannot evaluate a credential binding, so it cannot find the condition TRUE. The
    grants are read and carried; they reach nothing. A leaked permits credential reads no benefits
    data, and it also reads no permits data, which is the safe way to be wrong.
    """
    role = government.require_role("adapter_service")
    assert role.grant_count == 9
    assert all(g.conditional for g in role.grants)
    assert dict(role.allow) == {}
    assert not role.grants_entity("permits", "applications")
    assert not role.grant_for("permits", "applications", "ingest").authorises(
        source_id="permits", entity="applications", purpose="ingest", column="application_id")


# ---------------------------------------------------------------------------------------
# 3. the three ideas the module exists to preserve
# ---------------------------------------------------------------------------------------


def test_tables_columns_and_rows_stay_three_separate_decisions(government):
    """Never one merged allow-list: the role reaches the TABLE, is refused two COLUMNS of it for
    two different reasons, and is confined to a set of ROWS — three independent structures."""
    role = government.require_role("permits_inspector")
    assert role.grants_entity("permits", "inspections")                       # tables
    assert role.mask_for("permits", "applicants", "national_id_ref") is not None   # columns
    assert role.mask_for("permits", "applicants", "date_of_birth") is not None
    assert role.row_rule_for("permits", "inspections") is not None            # rows
    # ...and the three disagree, which is the point: a granted table with denied columns and a
    # narrow row scope is not expressible as one list.
    assert role.mask_for("permits", "inspections", "inspection_id") is None


def test_withheld_is_not_absent_and_carries_the_estates_own_reason_code(government):
    """A withheld column is a DENIAL WITH A REASON, and the reason comes from the estate's closed
    `reason_codes` set — never from a code this engine invented, which nobody could look up,
    aggregate or appeal against.
    """
    role = government.require_role("permits_caseworker")
    mask = government.require_mask(role.mask_for("correspondence", "email", "from_address"))
    assert mask.cell_type == "Withheld"
    assert mask.reason_code == "POLICY_THIRD_PARTY_DATA"
    assert mask.emit == "", "a withholding emits nothing; it is not a partial disclosure"
    assert government.require_reason(mask.reason_code) == "POLICY_THIRD_PARTY_DATA"


def test_a_conditional_withholding_withholds_unconditionally(government):
    """`{column: lodged_by, when: "sealed = true", reason: POLICY_SEALED_RECORD}`.

    This build has no per-row column engine, so it cannot evaluate `sealed = true`. It withholds
    always. Withholding a cell the estate would have disclosed on some rows is the safe error;
    disclosing a sealed appeal's contents because the condition could not be evaluated is not.
    """
    grant = government.require_role("permits_caseworker").grant_for("permits", "appeals")
    assert grant.withheld_when["lodged_by"] == "sealed = true"
    assert grant.column_disposition("lodged_by") == ("withheld", "POLICY_SEALED_RECORD")
    assert grant.column_disposition("appeal_id") == ("disclosed", None)


def test_every_reason_code_the_file_uses_is_in_its_own_closed_set(government):
    """"A withholding with no code in this list is a bug, not a policy" — checked at LOAD, because
    a code checked only when it fires is a code nobody has checked."""
    declared = set(government.reason_codes)
    assert declared, "the government estate declares a closed reason-code vocabulary"
    for role in government.roles.values():
        for grant in role.grants:
            assert set(grant.withheld.values()) <= declared
            assert grant.default_excluded_reason in declared
            if grant.outside_reason:
                assert grant.outside_reason in declared
        for mask_name in role.column_masks.values():
            assert government.require_mask(mask_name).reason_code in declared


def test_the_closed_world_withholds_a_classified_column_the_grant_did_not_list(government):
    """Stage 7 is `closed_world: true`: a column outside the grant's `columns:` list is Withheld,
    never disclosed by omission.

    `benefits_team_lead` restates the whole `benefits.claims` tuple and drops
    `linked_application_id` — "a hand-keyed hint on ~12% of claims, mistyped on a quarter of
    those". Without the closed world it would come back as a fact on the row.
    """
    role = government.require_role("benefits_team_lead")
    grant = role.grant_for("benefits", "claims")
    assert "linked_application_id" not in grant.columns
    assert grant.column_disposition("linked_application_id") == (
        "withheld", "POLICY_DATA_MINIMIZATION")
    mask = government.require_mask(
        role.mask_for("benefits", "claims", "linked_application_id"))
    assert mask.cell_type == "Withheld"
    assert mask.reason_code == "POLICY_DATA_MINIMIZATION"
    assert role.mask_for("benefits", "claims", "claim_id") is None, "granted: not masked"


def test_free_text_is_classified_before_it_is_authorised(government):
    """"A policy engine must be able to answer 'what IS this column' before it answers 'who may
    see it'." Including the surfaces the estate names at SOURCE level, because the source has
    exactly one of them and the reader's entity split is not the policy author's business.
    """
    assert government.is_free_text("permits", "applicants", "notes")
    assert government.is_free_text("benefits", "claim_notes", "body")
    assert government.is_free_text("case_files", "document", "content")
    assert government.is_free_text("correspondence", "message", "body")
    assert government.is_free_text("inspection_photos", "ocr_block", "text")
    assert not government.is_free_text("permits", "applications", "status")


def test_a_per_grant_free_text_exception_is_carried_but_not_widened(government):
    """v2 writes free-text exceptions per grant and per COLUMN; `free_text_policy.exceptions`,
    which `policy.py` reads, is keyed by role and purpose only. Projecting one into the other
    would hand the whole role every free-text column in the estate, so it is not projected: the
    exception is carried on the grant and the column stays withheld. Narrower than the estate
    wrote, and narrow is the direction that is safe to be wrong in.
    """
    grant = government.require_role("permits_inspector").grant_for("permits", "inspections")
    assert grant.free_text_exception == frozenset({"notes"})
    assert government.free_text_exceptions == ()


# ---------------------------------------------------------------------------------------
# 4. the version marker, the grammar, and the other estate
# ---------------------------------------------------------------------------------------


def test_version_cannot_discriminate_the_grammars_and_is_not_asked_to(government, finance):
    """Both estates declare `version: 2` and mean different things by it — each estate's own
    policy revision number, arrived at independently. A loader branching on it would read one
    estate's file with the other's reader on the strength of a coincidence.
    """
    assert government.version == finance.version == 2
    assert government.grammar != finance.grammar


def test_a_pack_may_declare_its_grammar_explicitly(tmp_path):
    data = _minimal(roles_grammar=GRAMMAR_GRANT_LIST)
    assert load_role_model(_write(tmp_path, data)).grammar == GRAMMAR_GRANT_LIST
    data = _minimal(roles_grammar="esperanto")
    with pytest.raises(RoleModelError, match="known grammars"):
        load_role_model(_write(tmp_path, data, "bad.yaml"))


def test_a_file_that_mixes_the_two_grammars_is_refused_rather_than_half_read(tmp_path):
    """Whichever reader ran, the other grammar's permissions would be dropped — and dropped
    permissions read as a working policy."""
    data = _minimal()
    data["roles"] = {"caseworker": {
        "egress_ceiling": "INTERNAL",
        "allow": {"permits": ["applications"]},
        "purposes": ["case_determination"],
        "grants": [{"object": "permits.applications", "purposes": ["case_determination"],
                    "columns": ["application_id"]}],
    }}
    with pytest.raises(RoleModelError, match="two grammars"):
        load_role_model(_write(tmp_path, data))


def test_the_finance_estate_still_loads_in_the_permission_lists_grammar(finance):
    """The regression pin. Nothing about the finance read may change: it is the grammar
    `policy.py`, the mailbox adapter and the whole finance test suite were built against.
    """
    assert finance.grammar == GRAMMAR_PERMISSION_LISTS
    assert finance.estate == "walnut-finance-synthetic"
    assert finance.bundle_version == "walnut-finance-synthetic@v2"
    assert sorted(finance.roles) == [
        "auditor", "compliance_officer", "credit_analyst", "external_auditor", "model_developer",
        "platform_operator", "portfolio_analyst", "relationship_manager",
        "senior_relationship_manager"]
    assert sorted(finance.masks) == [
        "band", "classify_only", "last4", "month_only", "pseudonym_engagement", "redact",
        "year_only"], "the grant_list reader synthesises masks; it must not do so here"

    rm = finance.require_role("relationship_manager")
    assert rm.grants == (), "a permission_lists role has no single tuple to carry"
    assert rm.purposes == frozenset({"relationship_management", "credit_assessment"})
    assert rm.egress_ceiling == "RESTRICTED_INTERNAL"
    assert rm.grants_entity("core_banking", "party")
    assert not rm.grants_entity("sanctions_watchlist", "anything")
    assert rm.denies_column("core_banking", "party", "notes")
    assert len(rm.deny_columns) == 13, "column denial stays a deny_columns entry here"
    assert rm.row_rule_for("core_banking", "party").kind == "eq"
    assert rm.row_rule_for("core_banking", "txn").kind == "uncompilable"
    assert finance.is_free_text("core_banking", "party", "notes")
    assert not finance.is_free_text("core_banking", "party", "date_of_birth")
    assert len(finance.free_text_exceptions) == 1


def test_the_projection_policy_py_reads_is_complete_for_every_government_role(government):
    """`row_scope_completeness`, checked against the flat vocabulary the engine actually reads.

    `policy.py` stage 8 refuses with POLICY_ROW_SCOPE_UNDECLARED any object a role allows that
    appears in neither `row_filters` nor `unscoped_objects`. Nothing constructs a request here —
    this asserts that the grant_list projection cannot produce that state, so a government read is
    refused for a reason the estate wrote rather than for one the translation left behind.
    """
    for name, role in government.roles.items():
        for grant in role.grants:
            if grant.conditional:
                continue
            assert role.grants_entity(grant.source_id, grant.entity or "any"), (
                f"{name} grants {grant.object} but the projection does not allow it")
            scoped = grant.object in role.row_filters
            unscoped = grant.object in role.unscoped_objects
            assert scoped != unscoped, f"{name}/{grant.object} is scoped both ways or neither"
            assert role.purpose_reaches(sorted(grant.purposes)[0],
                                        grant.source_id, grant.entity or "any")


def test_deny_beats_every_grant_in_the_government_estate(government):
    """"DENY WINS, always, over every grant in every role" — and it is the ONE place roles
    combine, which `policy.py` reads off `deny_sources`/`deny_tables`.
    """
    caseworker = government.require_role("permits_caseworker")
    assert "benefits" in caseworker.deny_sources         # written as `benefits.*`
    assert not caseworker.grants_entity("benefits", "claims")
    assert "legacy_records.LEGACY-MASTER" in caseworker.deny_tables
    officer = government.require_role("public_records_officer")
    assert not officer.grants_entity("benefits", "claimants")
    assert officer.grants_entity("permits", "v_public_register")


def test_the_finance_estate_gains_no_grant_list_behaviour(finance):
    """The two readers are separate paths, and the `grant_list` one leaves no trace here."""
    for role in finance.roles.values():
        assert role.grant_count == 0
        assert role.default_excluded_reason == "POLICY_DATA_MINIMIZATION"
        assert not [m for m in role.column_masks.values() if m.startswith("withheld:")]
        assert not [k for k in role.column_masks if ".*." in k]

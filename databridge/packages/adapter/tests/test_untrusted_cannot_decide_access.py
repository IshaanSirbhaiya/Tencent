"""Compile-time refusal for a policy rule that scopes access through an untrusted column.

Five independent Codex reviews of Phase 3A converged on one root cause, from five different call
sites (`wiki/40-reviews/raw/phase3-adapters-{postgres,document,mailbox,mcp,policy}.md`): trust is
stamped per FIELD when a pointer is EMITTED (`SourceDescription.trust_of`), but nothing checked
trust when a value ENTERED policy evaluation. A row-scope predicate's comparison column, or a
subquery's ``select_column`` / ``where_column``, could name a contract-declared UNTRUSTED column -
OCR text, an email header, a claimed case reference - and that attacker-writable value would then
decide row inclusion.

The fix is `taint.require_trusted_scope_column`, called from `PolicyEngine._compile`
(`policy.py`): refuse to COMPILE the decision table rather than evaluate the rule per row. Every
test below is built to FAIL without that fix.

**Self-contained on purpose.** This does not use the real finance/government estate fixtures
(`estate.py`, `government_estate.py`, `conftest.py`'s shared directory) — another agent is
refactoring those concurrently. Every `SourceDescription` / `Role` / `RoleModel` here is built by
hand, so this file's outcome depends only on `taint.py`, `policy.py` and `rolemodel.py`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pytest

from databridge_adapter.contract import CapabilityRequest, Principal, Projection
from databridge_adapter.describe import (
    CursorDescription,
    EntityDescription,
    FieldDescription,
    SourceDescription,
)
from databridge_adapter.errors import UntrustedColumnInPolicy
from databridge_adapter.policy import PolicyEngine, PrincipalBinding
from databridge_adapter.purposes import PurposeDecl, PurposeVocabulary, use_purposes
from databridge_adapter.results import Budget
from databridge_adapter.rolemodel import PurposeBinding, Role, RoleModel, RowRule, Subquery
from databridge_core import Trust
from databridge_adapter.verdicts import Verdict

PACK_PURPOSE = "test.scoped_read"
ESTATE_PURPOSE = "scoped_read"
ROLE_NAME = "scoped_reader"
ACTOR = "svc@test"


# --- purpose vocabulary, bound explicitly so this file needs no real pack on disk -------

@pytest.fixture(autouse=True)
def _purpose_vocabulary():
    vocab = PurposeVocabulary(
        {PACK_PURPOSE: PurposeDecl(id=PACK_PURPOSE, pack="test", description="",
                                   subject_scoped=False)},
        packs=("test",))
    with use_purposes(vocab):
        yield


# --- builders ---------------------------------------------------------------------------

def make_description(source_id: str, entity_fields: Mapping[str, Mapping[str, Trust]], *,
                     egress_class: str = "INTERNAL") -> SourceDescription:
    """One `SourceDescription` with one entity per key of `entity_fields`.

    Each value maps column name -> declared `Trust` - the SOURCE CONTRACT statement my new check
    is supposed to look up, never infer.
    """
    entities = tuple(
        EntityDescription(
            name=entity,
            fields=tuple(FieldDescription(name=col, type="varchar", trust=trust)
                        for col, trust in fields.items()),
            key=(next(iter(fields)),))
        for entity, fields in entity_fields.items())
    return SourceDescription(
        source_id=source_id, capabilities=frozenset({"row_read"}), entities=entities,
        pinning_tier="T2", cursor=CursorDescription(kind="none"), deletes_detectable="none",
        effective_retention={"default": "unknown"}, capture_required=False,
        egress_class=egress_class,
        reachable_verdicts=frozenset({Verdict.VERIFIED, Verdict.UNVERIFIABLE}))


def make_role(source_id: str, entity: str, rule: RowRule) -> Role:
    return Role(
        name=ROLE_NAME, description="test role", purposes=frozenset({ESTATE_PURPOSE}),
        egress_ceiling="RESTRICTED_INTERNAL",
        allow={source_id: frozenset({entity})},
        deny_tables=frozenset(), deny_columns=frozenset(), column_masks={},
        deny_sources=frozenset(), row_filters={f"{source_id}.{entity}": rule},
        purpose_grants={ESTATE_PURPOSE: frozenset({"*"})})


def make_engine(role: Role, *, estate: str = "test_estate") -> PolicyEngine:
    role_model = RoleModel(
        estate=estate, version=1, roles={role.name: role}, masks={}, classification={},
        estate_purposes={ESTATE_PURPOSE: "estate purpose"}, path=Path("test://roles.yaml"))
    binding = PurposeBinding(estate=estate, pack="test", bindings={PACK_PURPOSE: ESTATE_PURPOSE},
                             path=Path("test://purpose_bindings.yaml"))
    directory = {ACTOR: PrincipalBinding(roles=(role.name,))}
    return PolicyEngine(role_model=role_model, purpose_binding=binding, directory=directory,
                        default_budget=Budget(max_rows=1000))


def make_request(source_id: str, entity: str, fields: tuple[str, ...] = ("id",)
                 ) -> CapabilityRequest:
    return CapabilityRequest(
        source_id=source_id, capability="row_read",
        principal=Principal(actor=ACTOR, on_behalf_of=None, purpose=PACK_PURPOSE),
        projection=Projection(entity=entity, fields=fields), order_by=(fields[0],))


def eq_rule(source_id: str, entity: str, column: str) -> RowRule:
    return RowRule(target=f"{source_id}.{entity}", role=ROLE_NAME, kind="eq",
                   raw=f"{column} = :principal.attr", column=column, principal_attr="attr")


def subquery_rule(source_id: str, entity: str, *, column: str, sub_table: str,
                  select_column: str, where_column: str | None = None) -> RowRule:
    sub = Subquery(schema=None, table=sub_table, select_column=select_column,
                   where_column=where_column,
                   where_principal_attr=("attr" if where_column else None))
    return RowRule(target=f"{source_id}.{entity}", role=ROLE_NAME, kind="in_subquery",
                   raw=f"{column} IN (SELECT {select_column} FROM {sub_table})",
                   column=column, principal_attr=None, subquery=sub)


# =======================================================================================
# 1. Row scope: a RowRule's comparison column, contract-declared UNTRUSTED.
# =======================================================================================

def test_row_scope_through_an_untrusted_column_is_refused_at_compile_time():
    description = make_description(
        "src", {"widget": {"id": Trust.TRUSTED, "owner_claim": Trust.UNTRUSTED}})
    role = make_role("src", "widget", eq_rule("src", "widget", "owner_claim"))
    engine = make_engine(role)
    req = make_request("src", "widget")

    with pytest.raises(UntrustedColumnInPolicy) as exc:
        engine.admit(req, description=description)

    message = str(exc.value)
    assert "src.widget.owner_claim" in message           # names the column
    assert f"{ROLE_NAME}:row:src.widget" in message       # names the rule


def test_row_scope_through_a_trusted_column_compiles_fine():
    """The control: the identical shape, over a column the contract declares TRUSTED, compiles."""
    description = make_description(
        "src", {"widget": {"id": Trust.TRUSTED, "owner_id": Trust.TRUSTED}})
    role = make_role("src", "widget", eq_rule("src", "widget", "owner_id"))
    engine = make_engine(role)
    req = make_request("src", "widget")

    admission = engine.admit(req, description=description)
    assert admission.decisions.rules[0].column == "owner_id"


# =======================================================================================
# 2. Subquery select_column / where_column, contract-declared UNTRUSTED.
# =======================================================================================

def test_subquery_select_column_through_an_untrusted_column_is_refused():
    description = make_description("src", {
        "widget": {"id": Trust.TRUSTED, "owner_id": Trust.TRUSTED},
        "lookup": {"claimed_ref": Trust.UNTRUSTED},
    })
    rule = subquery_rule("src", "widget", column="owner_id", sub_table="lookup",
                         select_column="claimed_ref")
    role = make_role("src", "widget", rule)
    engine = make_engine(role)
    req = make_request("src", "widget")

    with pytest.raises(UntrustedColumnInPolicy, match="src.lookup.claimed_ref"):
        engine.admit(req, description=description)


def test_subquery_where_column_through_an_untrusted_column_is_refused():
    description = make_description("src", {
        "widget": {"id": Trust.TRUSTED, "owner_id": Trust.TRUSTED},
        "lookup": {"ok_id": Trust.TRUSTED, "claimed_team": Trust.UNTRUSTED},
    })
    rule = subquery_rule("src", "widget", column="owner_id", sub_table="lookup",
                         select_column="ok_id", where_column="claimed_team")
    role = make_role("src", "widget", rule)
    engine = make_engine(role)
    req = make_request("src", "widget")

    with pytest.raises(UntrustedColumnInPolicy, match="src.lookup.claimed_team"):
        engine.admit(req, description=description)


def test_subquery_over_trusted_columns_compiles_fine():
    description = make_description("src", {
        "widget": {"id": Trust.TRUSTED, "owner_id": Trust.TRUSTED},
        "lookup": {"ok_id": Trust.TRUSTED, "team": Trust.TRUSTED},
    })
    rule = subquery_rule("src", "widget", column="owner_id", sub_table="lookup",
                         select_column="ok_id", where_column="team")
    role = make_role("src", "widget", rule)
    engine = make_engine(role)
    req = make_request("src", "widget")

    admission = engine.admit(req, description=description)
    assert admission.decisions.rules[0].subquery.select_column == "ok_id"


# =======================================================================================
# 3. Fail closed: a column the contract never classified at all.
# =======================================================================================

def test_an_unclassified_scope_column_fails_closed_as_untrusted():
    """`SourceDescription.trust_of` raises `KeyError` for a column reflection never described.

    That must be treated exactly like an explicit UNTRUSTED declaration, never like TRUSTED:
    "never classified" is not evidence of safety.
    """
    description = make_description("src", {"widget": {"id": Trust.TRUSTED}})   # no "ghost_col"
    role = make_role("src", "widget", eq_rule("src", "widget", "ghost_col"))
    engine = make_engine(role)
    req = make_request("src", "widget")

    with pytest.raises(UntrustedColumnInPolicy, match="src.widget.ghost_col"):
        engine.admit(req, description=description)


# =======================================================================================
# 4. Column decisions: the refusal is ATOMIC. It blocks the whole compile, not just the
#    row rule - no partial table with some columns compiled ever escapes.
# =======================================================================================

def test_the_refusal_blocks_column_decisions_too_not_just_the_row_rule():
    """A projection naming several otherwise-legitimate columns still compiles NOTHING.

    `PolicyEngine._compile` runs row scope (STAGE 8) before column projection (STAGE 9); this
    proves the untrusted-scope refusal fires before STAGE 9 ever runs; `admit()` either returns a
    complete `Admission` or raises - there is no partial return through which some column
    decisions could leak while the row rule is refused.
    """
    description = make_description(
        "src", {"widget": {"id": Trust.TRUSTED, "owner_claim": Trust.UNTRUSTED,
                           "name": Trust.TRUSTED, "amount": Trust.TRUSTED}})
    role = make_role("src", "widget", eq_rule("src", "widget", "owner_claim"))
    engine = make_engine(role)
    req = make_request("src", "widget", fields=("id", "name", "amount"))

    with pytest.raises(UntrustedColumnInPolicy):
        engine.admit(req, description=description)
    # Nothing partial: the exception IS the whole result. There is no admission object, complete
    # or partial, anywhere in scope for a caller to have accidentally captured.


# =======================================================================================
# 5. No per-source branching: the identical rule shape is refused identically for two
#    different sources. The check is a lookup against whichever description it is given, not a
#    hardcoded source name.
# =======================================================================================

@pytest.mark.parametrize("source_id", ["source_alpha", "source_beta"])
def test_the_same_rule_shape_is_refused_identically_for_different_sources(source_id):
    description = make_description(
        source_id, {"widget": {"id": Trust.TRUSTED, "owner_claim": Trust.UNTRUSTED}})
    role = make_role(source_id, "widget", eq_rule(source_id, "widget", "owner_claim"))
    engine = make_engine(role)
    req = make_request(source_id, "widget")

    with pytest.raises(UntrustedColumnInPolicy, match=f"{source_id}.widget.owner_claim"):
        engine.admit(req, description=description)


# =======================================================================================
# 6. The other half of the rule survives: an untrusted pointer may still be re-resolved, and
#    an untrusted value may still be PROJECTED and disclosed. Only using it to SCOPE is refused.
# =======================================================================================

def test_an_untrusted_column_that_is_merely_projected_is_still_disclosed():
    """Quotable, not actionable (D3). Scoping THROUGH `notes` is refused (case 1 above); reading
    and disclosing `notes` as an ordinary projected column is not touched by this change at all.
    """
    description = make_description(
        "src", {"widget": {"id": Trust.TRUSTED, "owner_id": Trust.TRUSTED,
                           "notes": Trust.UNTRUSTED}})
    role = make_role("src", "widget", eq_rule("src", "widget", "owner_id"))
    engine = make_engine(role)
    req = make_request("src", "widget", fields=("id", "notes"))

    admission = engine.admit(req, description=description)
    notes_rule = admission.decisions.rule_for("notes")
    assert notes_rule.withheld is False, (
        "an untrusted column that is only PROJECTED, never used to scope a row, must still be "
        "disclosed - refusing to compile it would break the quotable half of D3")

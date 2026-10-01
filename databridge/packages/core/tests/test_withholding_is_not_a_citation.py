"""Withholding is a third cell state, and the tests are mostly about what it must NOT say.

"Not present in the source" and "present in the source, not permitted to you" are different
facts (spine D4). Collapsing them is a correctness bug in the credibility-critical path — a
renderer that shows them together tells a case officer "the document doesn't say" about a
value the document does say — and downstream it opens a missingness covert channel: if
suppression correlates with the label, the null mask leaks what suppression removed.

The design is defined by three refusals, and each one has a test here:

1. a `WithholdingRecord` carries **no value**;
2. it carries **no SourcePointer** — withholding is not a citation, and a pointer beside a
   withheld field would vouch for something the consumer was never shown;
3. it does **not assert that a value exists** behind the denial. The source may hold NULL.
   Claiming existence is itself a disclosure.

(3) is not enforceable by a type — it is enforced by the *absence* of any field in which such
a claim could be written. The tests below assert that absence, so a future "helpful" addition
like `value_exists: bool` fails here rather than shipping.

**What R-003 findings 4 and 5 added, and what they deliberately did not.**

Finding 4 was that "it does not assert existence" was true of the record's declared fields and
false of everything around them. Three of those channels are in the record's SHAPE, and shape is
something a type can hold, so they are closed here:

* the *reason code* — "sensitive value present" defeats the guarantee in four words, and it is
  the natural thing for a policy author to write. `Denial` refuses a reason code that describes
  the value. The diagnostic code stays on the audit record where the auditor, not the requester,
  reads it;
* the *audit detail* — principal, purpose, policy version and decision time all ride back to the
  requester if the response serialises `WithholdingRecord` directly. `public_denial()` is the
  projection that drops them, so two denials differing only in audit detail are equal objects;
* the *key set* — rule 5 requires a withheld key to be present in `attrs`, which is a disclosure
  if `attrs` was built from what the source happened to hold. `assert_withheld_disjoint` now
  takes `requested` and requires every requested schema key to be present regardless.

The fourth channel is **behavioural** and is not closed, because no type can see it: if the
policy reads the cell before deciding, then whether a denial is produced at all is value-
dependent. That belongs to the policy engine at the D4a enforcement point. The object-level
version of the same problem already has a declared answer in spine O1b — a pack may declare
withheld and absent byte-identical.

Finding 5 said `Withheld` and `Absent` violate the universal provenance claim. The fix was to
the CLAIM, not to the types, and the reasoning is in the module docstring of `provenance.py`:
these two are not values, they are non-answers, so a pointer to a *source* is the wrong proof
for them — and a pointer to the withheld value is precisely what they must never carry. What
they did lack was proof of their own: a denial needs the decision it came out of (now
`decision_id`, joining policy_id/policy_version/principal/purpose/decided_at) and an absence
needs the snapshot it was observed against (now `Absent.snapshot`).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from databridge_core.provenance import (
    Absent,
    Denial,
    SnapshotIdentity,
    GroundedValue,
    MissingReason,
    SourcePointer,
    Trust,
    Withheld,
    WithholdingRecord,
    assert_withheld_disjoint,
)


def _record(**over) -> WithholdingRecord:
    kwargs = dict(field_path="a.b", policy_id="pol-7", reason_code="not_in_grant",
                  principal="agent:caseworker-1")
    kwargs.update(over)
    return WithholdingRecord(**kwargs)


def _pointer() -> SourcePointer:
    return SourcePointer(source_id="s", kind="table_cell", table="t", column="c", row_id="1",
                         trust=Trust.TRUSTED)


# --- refusal 1 and 2: no value, no citation -------------------------------------------------

def test_a_withholding_record_cannot_carry_a_source_pointer():
    """This is the load-bearing one. `extra="forbid"` is what makes it structural."""
    with pytest.raises(ValidationError):
        _record(pointer=_pointer())


def test_a_withholding_record_cannot_carry_a_value():
    with pytest.raises(ValidationError):
        _record(value="the thing you may not see")


def test_a_withheld_cell_cannot_carry_a_value_either():
    with pytest.raises(ValidationError):
        Withheld(record=_record(), value=1)


def test_a_withheld_cell_cannot_carry_a_pointer_either():
    with pytest.raises(ValidationError):
        Withheld(record=_record(), pointer=_pointer())


# --- refusal 3: it must not assert existence --------------------------------------------------

def test_the_record_has_no_field_in_which_to_claim_a_value_exists():
    """Asserting existence is itself a disclosure; the source may simply hold NULL.

    Enforced by absence, so this test names the absence explicitly. If someone adds
    `value_exists`, `has_value`, `is_null` or a `value_hash`, this fails and they have to
    argue for it in review rather than land it as a convenience.
    """
    forbidden = {"value", "value_exists", "has_value", "is_null", "value_hash",
                 "pointer", "source_pointer", "redacted_value"}
    declared = set(WithholdingRecord.model_fields)
    assert not (declared & forbidden), (
        f"WithholdingRecord grew a field that leaks existence: {sorted(declared & forbidden)}")


# --- the record must be answerable -------------------------------------------------------------

def test_the_four_required_identifiers_are_required():
    for field in ("field_path", "policy_id", "reason_code", "principal"):
        kwargs = dict(field_path="a.b", policy_id="p", reason_code="r", principal="who")
        kwargs.pop(field)
        with pytest.raises(ValidationError):
            WithholdingRecord(**kwargs)


@pytest.mark.parametrize("field", ["field_path", "policy_id", "reason_code", "principal"])
def test_a_blank_identifier_is_refused(field):
    """"Decision D was made under policy version 7, denying principal P" must be answerable.
    A record with a blank policy id satisfies the type and answers nothing."""
    with pytest.raises(ValidationError, match="non-blank"):
        _record(**{field: "   "})


def test_the_denial_is_principal_specific_not_global():
    """A denial is a fact about (policy, principal), never about the subject. Two principals
    can legitimately get different answers, and `appealable` is what lets a UI say so."""
    a = _record(principal="agent:caseworker-1", appealable=True)
    b = _record(principal="agent:auditor-9", appealable=False)
    assert a != b


def test_optional_audit_fields_survive_when_supplied():
    rec = _record(policy_version="sha256:beef", purpose="eligibility_determination",
                  decided_at=datetime(2026, 8, 16, tzinfo=timezone.utc), appealable=True)
    assert rec.policy_version == "sha256:beef" and rec.purpose == "eligibility_determination"


def test_records_are_frozen():
    rec = _record()
    with pytest.raises(ValidationError):
        rec.policy_id = "pol-8"


# --- the three states are distinct ---------------------------------------------------------------

def test_withheld_and_absent_are_different_types_not_a_flag():
    """The whole point of D4: they cannot be confused by a consumer that forgets to check."""
    assert not isinstance(Withheld(record=_record()), Absent)
    assert not isinstance(Absent(field_path="a.b"), Withheld)


def test_a_withheld_cell_is_not_a_degenerate_grounded_value():
    """`GroundedValue.pointer` is non-Optional and must stay that way — that is exactly why
    withholding needed a separate type instead of `GroundedValue(value=None, pointer=None)`."""
    with pytest.raises(ValidationError):
        GroundedValue(value=None, pointer=None)


def test_missing_reasons_are_three_distinct_states():
    assert len({MissingReason.NOT_IN_SOURCE,
                MissingReason.GROUNDING_DROPPED,
                MissingReason.POLICY_SUPPRESSED}) == 3


# --- the disjointness rules (SYSTEM_DESIGN 1.6, rules 4 and 5) --------------------------------------

def test_a_key_may_not_be_both_cited_and_withheld():
    with pytest.raises(ValueError, match="not a citation"):
        assert_withheld_disjoint(
            attrs={"a": None},
            attr_sources={"a": _pointer()},
            attr_withheld={"a": _record(field_path="a")})


def test_a_withheld_key_must_already_exist_in_attrs():
    """Otherwise policy has invented a field the record never had."""
    with pytest.raises(ValueError, match="absent from attrs"):
        assert_withheld_disjoint(
            attrs={}, attr_sources={}, attr_withheld={"ghost": _record(field_path="ghost")})


def test_a_withheld_key_must_be_none_in_attrs():
    with pytest.raises(ValueError, match="non-None"):
        assert_withheld_disjoint(
            attrs={"a": "still here"}, attr_sources={},
            attr_withheld={"a": _record(field_path="a")})


def test_a_well_formed_record_passes():
    assert_withheld_disjoint(
        attrs={"a": None, "b": "shown"},
        attr_sources={"b": _pointer()},
        attr_withheld={"a": _record(field_path="a")})


def test_the_helper_says_nothing_about_unresolved():
    """The honest limit: rule 6 — withheld keys are REMOVED from `unresolved` — cannot be
    checked here, because `unresolved` is derived per consumer. Each consumer owes it, and
    the graph schema is where it is enforced for nodes and edges."""
    assert "unresolved" not in assert_withheld_disjoint.__code__.co_varnames


# --- finding 4: the reason code must not describe what it is hiding ---------------------------

@pytest.mark.parametrize("code", [
    "sensitive_value_present",
    "value_exists",
    "non_null_and_restricted",
    "instruction_shaped_value",
    "PII_DETECTED",
    "has_value",
])
def test_a_public_denial_refuses_a_reason_code_that_describes_the_value(code):
    """A denial that explains itself by describing what it is hiding has disclosed that there is
    something to hide. This is the channel the review named with a four-word example."""
    with pytest.raises(ValidationError, match="asserts something about the withheld value"):
        _record(reason_code=code).public_denial()


@pytest.mark.parametrize("code", ["not_in_grant", "purpose_not_permitted", "RESTRICTED",
                                  "RESTRICTED_FIELD", "no_grant_for_principal"])
def test_a_policy_shaped_reason_code_projects_fine(code):
    """The rule must not block the codes a policy legitimately uses, or nobody can deny anything."""
    assert _record(reason_code=code).public_denial().reason_code == code


def test_the_diagnostic_code_may_still_live_on_the_AUDIT_record():
    """"The value looked like an injected instruction" is exactly what an auditor needs to read,
    and exactly what the requester must not be told. Two planes, two types."""
    rec = _record(reason_code="instruction_shaped_value")
    assert rec.reason_code == "instruction_shaped_value"     # the audit plane keeps it
    with pytest.raises(ValidationError):
        rec.public_denial()                                   # the response plane does not


# --- finding 4: the response plane carries no audit detail -------------------------------------

def test_two_denials_differing_only_in_audit_detail_project_to_the_same_thing():
    """The observational-identity property, for the part of it a type can hold. If timing or
    principal or policy version rode back to the requester, each of them would be a channel."""
    a = _record(principal="agent:caseworker-1", purpose="eligibility",
                policy_version="sha256:beef", decided_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
                decision_id="ev-1")
    b = _record(principal="agent:auditor-9", purpose="audit",
                policy_version="sha256:cafe", decided_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
                decision_id="ev-2")
    assert a != b
    assert a.public_denial() == b.public_denial()


def test_a_denial_has_no_field_in_which_audit_detail_could_be_leaked():
    """Enforced by absence, like refusal 3 above, and named so an addition has to argue for
    itself in review."""
    forbidden = {"principal", "purpose", "policy_version", "decided_at", "decision_id",
                 "value", "pointer"}
    assert not (set(Denial.model_fields) & forbidden)


def test_the_withheld_cell_projects_too_so_a_renderer_never_has_to_reach_for_the_record():
    """Making the safe thing the short thing. A renderer that has to write
    `cell.record.public_denial()` will eventually write `cell.record` instead."""
    cell = Withheld(record=_record())
    assert cell.public_denial() == cell.record.public_denial()


# --- finding 4: the key set must not encode source presence ------------------------------------

def test_the_requested_schema_keys_must_all_be_present_regardless_of_what_the_source_held():
    """Rule 5 is a disclosure if `attrs` was built from what the source happened to hold: the
    key set then encodes presence, and a Withheld among keys that only appear when the source
    has them proves the field exists."""
    with pytest.raises(ValueError, match="requested keys missing from attrs"):
        assert_withheld_disjoint(
            attrs={"a": None},                     # "b" absent because the source had nothing
            attr_sources={}, attr_withheld={"a": _record(field_path="a")},
            requested=["a", "b"])


def test_a_denial_for_a_field_nobody_requested_announces_that_the_field_is_there():
    with pytest.raises(ValueError, match="never requested"):
        assert_withheld_disjoint(
            attrs={"a": None, "b": None}, attr_sources={},
            attr_withheld={"b": _record(field_path="b")},
            requested=["a"])


def test_a_fully_populated_schema_passes():
    assert_withheld_disjoint(
        attrs={"a": None, "b": None, "c": "shown"},
        attr_sources={"c": _pointer()},
        attr_withheld={"a": _record(field_path="a")},
        requested=["a", "b", "c"])


def test_omitting_requested_still_runs_the_legacy_check():
    """It is optional only because the callers predate it. The weaker check must still fire."""
    with pytest.raises(ValueError, match="absent from attrs"):
        assert_withheld_disjoint(attrs={}, attr_sources={},
                                 attr_withheld={"ghost": _record(field_path="ghost")})


# --- finding 5: the non-answers carry proof of their own ----------------------------------------

def test_a_denial_can_name_the_evaluation_event_it_came_out_of():
    """"Decision D was made under bundle V, denying principal P, for purpose U, at T, in
    evaluation E" - that is the denial's provenance, and it is what makes it re-EXECUTABLE
    rather than merely described."""
    rec = _record(policy_version="sha256:beef", purpose="eligibility",
                  decision_id="ev-2026-08-16-0001",
                  decided_at=datetime(2026, 8, 16, tzinfo=timezone.utc))
    assert rec.decision_id == "ev-2026-08-16-0001"


def test_an_absence_can_name_the_snapshot_that_proves_it():
    """"Not in the source" is a claim ABOUT a source. A claim about a source that does not say
    which source, as of when, is not checkable."""
    snap = SnapshotIdentity(source_id="reg", as_of=datetime(2026, 8, 16, tzinfo=timezone.utc),
                            content_hash="h", schema_hash="sh")
    a = Absent(field_path="party.lei", snapshot=snap)
    assert a.snapshot is not None and a.snapshot.source_id == "reg"


def test_the_non_answers_still_carry_no_pointer_and_no_value():
    """The restated claim is not a licence to start attaching pointers to them. A pointer to the
    withheld value is exactly the disclosure these types exist to refuse."""
    for model in (WithholdingRecord, Withheld, Absent, Denial):
        assert not ({"pointer", "source_pointer", "value"} & set(model.model_fields)), model


def test_the_module_states_the_claim_precisely_rather_than_broadly():
    """R-003 finding 5 was about a CLAIM, so the fix has to be checkable in the claim."""
    import databridge_core.provenance as prov
    doc = prov.__doc__ or ""
    assert "discloses" in doc and "non-answer is grounded in a decision" in doc


def test_the_principal_is_a_string_and_the_docstring_says_so():
    """It proves a string is non-blank and nothing else. It must never be read as a confirmed
    identity determination - that needs an attestation object, and it is not this."""
    doc = WithholdingRecord.__doc__ or ""
    assert "never proof that a named human attested" in doc

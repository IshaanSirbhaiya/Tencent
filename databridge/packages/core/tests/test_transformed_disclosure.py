"""The fourth cell state: a TRANSFORMED disclosure (spine O1b, closed 2026-08-16).

The gap this closes was found twice, independently, from opposite directions:

* the **government** pack, writing its access model, could not say honestly what a masked value
  *is* — it is none of ``GroundedValue | Withheld | Absent``;
* the **finance** pack, whose ``egress_ceiling`` is a single scalar, could not *express* "may
  read, but only in transformed form", so two roles reached nothing at all because refusal was
  the only safe thing the vocabulary could say.

Mis-description and over-refusal are two symptoms of one missing concept. Every test here names
the invariant it defends, because each was verified by deleting that invariant and watching this
file fail — the standard set by ``test_trust_lattice_cannot_be_laundered.py``.

The load-bearing one, if you read only one: ``Transformed`` is **not** a ``GroundedValue``.
Downstream code pattern-matches on cell type, and a mask that answers ``True`` to
``isinstance(cell, GroundedValue)`` is a lie told silently, in every consumer at once.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from databridge_core import (
    DISCLOSING_CELL_TYPES,
    Absent,
    Cell,
    Denial,
    GroundedValue,
    PartialDisclosure,
    SourcePointer,
    TransformRecord,
    Transformed,
    Trust,
    Withheld,
    WithholdingRecord,
    assert_cell_states_disjoint,
    assert_withheld_disjoint,
)

TRANSFORMATION = "redact_pii@v1(column=email)"


# ---------------------------------------------------------------------------------------
# builders — one valid shape, perturbed per test
# ---------------------------------------------------------------------------------------

def raw_cell(trust: Trust | None = Trust.UNTRUSTED) -> SourcePointer:
    """The underlying cell: an ordinary verbatim citation nobody was allowed to see."""
    return SourcePointer(source_id="core_banking", kind="table_cell", table="party",
                         column="email", row_id="p-1", trust=trust)


def masked_pointer(underlying: SourcePointer, *, trust: Trust | None = Trust.UNTRUSTED,
                   derivation: str = TRANSFORMATION) -> SourcePointer:
    return SourcePointer(source_id="core_banking", kind="derived", derivation=derivation,
                         derived_from=[underlying], trust=trust)


def record(**over) -> TransformRecord:
    body = dict(field_path="party.email", policy_id="roles.yaml#portfolio_analyst",
                transformation=TRANSFORMATION, principal="svc:analyst-7",
                reason_code="egress_transformed_only", purpose="portfolio_review",
                policy_version="sha256:abcd", decided_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
                decision_id="eval-991", appealable=True)
    body.update(over)
    return TransformRecord(**body)


def transformed(*, value="<REDACTED:email>", trust: Trust | None = Trust.UNTRUSTED,
                **over) -> Transformed:
    under = over.pop("underlying", None) or raw_cell(trust)
    ptr = over.pop("pointer", None) or masked_pointer(under, trust=trust)
    return Transformed(disclosed=GroundedValue(value=value, pointer=ptr),
                       underlying=under, record=over.pop("record", None) or record(), **over)


def test_the_happy_path_builds_and_answers_all_five_questions() -> None:
    """Spine O1b required the state to answer five things. Read them off one object."""
    t = transformed()
    assert t.disclosed.value == "<REDACTED:email>"                  # what was disclosed
    assert t.record.transformation == TRANSFORMATION                # what produced it
    assert t.record.policy_id and t.record.decision_id              # under what policy
    assert t.underlying.kind == "table_cell"                        # what the underlying cell was
    assert isinstance(t, Transformed)                               # that it was PARTIAL


# ---------------------------------------------------------------------------------------
# THE one that matters: not mistakable for a full disclosure
# ---------------------------------------------------------------------------------------

def test_a_transformed_cell_is_not_a_GroundedValue_and_never_will_be() -> None:
    """Invariant: ``Transformed`` does not subclass ``GroundedValue``.

    This is the constraint the whole shape was chosen for. The rejected alternative — encode a
    mask as a ``GroundedValue`` with a ``derived`` pointer — fails exactly here, because
    ``derived`` is *already* the honest kind for many FULL disclosures (unit conversions, the
    aggregation compression, the registered identity rule). There would be no discriminator left.
    """
    t = transformed()
    assert not isinstance(t, GroundedValue)
    assert GroundedValue not in type(t).__mro__
    assert not issubclass(Transformed, GroundedValue)
    assert not issubclass(GroundedValue, Transformed)


def test_the_naive_downstream_unwrap_returns_a_non_answer_rather_than_the_mask() -> None:
    """The exact line every consumer already has (``databridge_adapter.results.cell_value``).

    A consumer that has not been taught about the fourth state must get *nothing*, not a masked
    value it will render as verbatim. Failing closed is the whole point of not subclassing.
    """
    def legacy_cell_value(cell):
        return cell.value if isinstance(cell, GroundedValue) else None

    assert legacy_cell_value(transformed()) is None
    assert legacy_cell_value(GroundedValue(value="x", pointer=raw_cell())) == "x"


def test_asking_did_I_get_anything_requires_naming_both_disclosing_states() -> None:
    """``DISCLOSING_CELL_TYPES`` exists so the acknowledgement is explicit rather than forgotten."""
    assert DISCLOSING_CELL_TYPES == (GroundedValue, Transformed)
    assert isinstance(transformed(), DISCLOSING_CELL_TYPES)
    assert not isinstance(Withheld(record=WithholdingRecord(
        field_path="party.email", policy_id="p", reason_code="not_in_grant",
        principal="svc:x")), DISCLOSING_CELL_TYPES)
    assert not isinstance(Absent(field_path="party.email"), DISCLOSING_CELL_TYPES)


def test_a_Cell_typed_field_keeps_a_transformed_cell_transformed() -> None:
    """Pydantic's union resolution must not quietly coerce a mask into a full disclosure."""
    class Row(BaseModel):
        model_config = ConfigDict(frozen=True)
        cell: Cell

    assert isinstance(Row(cell=transformed()).cell, Transformed)
    assert isinstance(Row(cell=GroundedValue(value="v", pointer=raw_cell())).cell, GroundedValue)
    round_tripped = Row.model_validate(Row(cell=transformed()).model_dump())
    assert isinstance(round_tripped.cell, Transformed)
    assert round_tripped.cell.disclosed.value == "<REDACTED:email>"


def test_the_union_names_four_states_not_three() -> None:
    assert set(Cell.__args__) == {GroundedValue, Transformed, Withheld, Absent}


# ---------------------------------------------------------------------------------------
# the citation must admit it is not verbatim
# ---------------------------------------------------------------------------------------

def _verbatim_pointers(under: SourcePointer) -> list[SourcePointer]:
    """Pointers of verbatim kinds that nonetheless declare the underlying cell as a parent.

    Built with explicit keyword arguments rather than a ``**splat`` because ``trust_gate``'s
    CI scan reports a splat as ``SPLAT`` — "we cannot tell whether a label was passed" — and it
    is right to, so this file does not hand it something it cannot read.
    """
    return [
        SourcePointer(source_id="core_banking", kind="table_cell", table="party", column="email",
                      row_id="p-1", trust=Trust.UNTRUSTED, derived_from=[under]),
        SourcePointer(source_id="core_banking", kind="doc_span", document_id="d1", char_start=0,
                      char_end=4, trust=Trust.UNTRUSTED, derived_from=[under]),
        SourcePointer(source_id="core_banking", kind="email_part", message_id="m1", part="body",
                      trust=Trust.UNTRUSTED, derived_from=[under]),
    ]


@pytest.mark.parametrize("index", [0, 1, 2])
def test_a_transformed_disclosure_may_not_be_cited_verbatim(index) -> None:
    """Invariant: ``disclosed.pointer.kind == "derived"``.

    The emitted bytes are not the cited bytes. A verbatim kind would vouch for a value the
    source never held — the precise lie this state exists to stop telling (§4a). Note that
    declaring the right parent does not rescue it: the *kind* is the claim being refused.
    """
    under = raw_cell()
    ptr = _verbatim_pointers(under)[index]
    with pytest.raises(ValidationError, match="must be cited with a `derived` pointer"):
        Transformed(disclosed=GroundedValue(value="<REDACTED:email>", pointer=ptr),
                    underlying=under, record=record())


def test_the_underlying_cell_must_be_a_declared_parent_of_the_citation() -> None:
    """Invariant: ``underlying`` appears among the disclosed pointer's parents.

    This is the single line that makes the trust lattice apply to a transformation, so it is a
    security invariant and not bookkeeping — see the laundering test below.
    """
    under = raw_cell()
    unrelated = SourcePointer(source_id="core_banking", kind="table_cell", table="party",
                              column="postcode", row_id="p-1", trust=Trust.UNTRUSTED)
    ptr = masked_pointer(unrelated)
    with pytest.raises(ValidationError, match="must appear among the disclosed pointer's parents"):
        Transformed(disclosed=GroundedValue(value="<REDACTED:email>", pointer=ptr),
                    underlying=under, record=record())


def test_an_equal_but_not_identical_underlying_pointer_is_accepted() -> None:
    """Frozen value objects: equality is the right test, and a rebuilt pointer must not fail."""
    under = raw_cell()
    twin = raw_cell()
    assert under is not twin and under == twin
    Transformed(disclosed=GroundedValue(value="<REDACTED:email>", pointer=masked_pointer(twin)),
                underlying=under, record=record())


# ---------------------------------------------------------------------------------------
# the policy must have authorised the function that actually ran
# ---------------------------------------------------------------------------------------

def test_the_cited_function_must_be_the_one_policy_authorised() -> None:
    """Invariant: ``pointer.derivation == record.transformation``.

    The record is what policy DECIDED; the pointer is how the value is CITED. If they disagree,
    the emitted value was not produced by the transformation that was permitted.
    """
    under = raw_cell()
    ptr = masked_pointer(under, derivation="bucket@v1(width=10000)")
    with pytest.raises(ValidationError, match="but policy authorised"):
        Transformed(disclosed=GroundedValue(value="<REDACTED:email>", pointer=ptr),
                    underlying=under, record=record())


def test_a_transformation_must_be_a_replayable_call_not_prose() -> None:
    """Invariant: ``TransformRecord.transformation`` matches ``<name>@v<n>(...)``.

    A transformation nobody can re-run is a claim, and a partial disclosure whose function is a
    claim is indistinguishable from a fabricated value.
    """
    for prose in ("redacted", "we masked it", "redact_pii", "redact_pii@v1", "Redact@v1()"):
        with pytest.raises(ValidationError, match="is not a replayable call"):
            record(transformation=prose)
    record(transformation="bucket@v1(width=10000)")
    record(transformation="mask@v2()")
    record(transformation="k_anon@v3(k=5) - generalised to the 5-anonymous band")


def test_the_core_check_is_looser_than_the_downstream_parser_on_purpose() -> None:
    """Core is the dependency root and must not own the derivation grammar.

    The safe asymmetry is: anything the replayer accepts, core accepts. Core never refuses a
    transformation that would have replayed fine. A note, extra whitespace and nested-looking
    argument text all pass here and are the replayer's problem, not core's.
    """
    record(transformation="  redact_pii@v1 (column=email)  ")
    record(transformation="redact_pii@v1(column=email) — mirrors the connector")


# ---------------------------------------------------------------------------------------
# a transformation that discloses nothing is a denial
# ---------------------------------------------------------------------------------------

def test_a_transformed_cell_disclosing_None_is_refused_because_it_is_a_denial() -> None:
    """Invariant: ``disclosed.value is not None``.

    A security rule, not pedantry. ``Withheld`` projects through ``Denial``, whose validator
    refuses a reason code that describes the withheld value. A null-valued ``Transformed`` would
    be a denial that skipped that hygiene — and ``PartialDisclosure`` deliberately does not run
    the token filter, on the justification that the value is already partly disclosed, which is
    false when there is no value.
    """
    with pytest.raises(ValidationError, match="discloses None is a denial"):
        transformed(value=None)


def test_the_route_around_Denials_reason_code_hygiene_is_closed() -> None:
    """The concrete attack the rule above blocks, spelled out.

    A leaky reason code is refused on the response plane of a withholding. Encoding the same
    denial as a null transformation would carry that reason code straight to the requester.
    """
    leaky = "sensitive_value_present"
    with pytest.raises(ValidationError, match="asserts something about the withheld value"):
        Denial(field_path="party.email", policy_id="p", reason_code=leaky)
    with pytest.raises(ValidationError, match="discloses None is a denial"):
        transformed(value=None, record=record(reason_code=leaky))


def test_a_partial_disclosure_MAY_describe_the_value_and_that_asymmetry_is_deliberate() -> None:
    """Not an oversight: recorded here so a later reader does not "fix" it.

    ``Denial`` refuses a value-describing reason code because a denial that explains itself by
    describing what it is hiding has admitted there is something to hide. That argument does not
    transfer to a state that has already handed the requester a function of the value. Applying
    the filter here would refuse honest codes while protecting nothing ``value`` does not already
    give away.
    """
    notice = transformed(record=record(reason_code="masked_sensitive_column")).public_disclosure()
    assert notice.reason_code == "masked_sensitive_column"
    with pytest.raises(ValidationError):
        Denial(field_path="party.email", policy_id="p", reason_code="masked_sensitive_column")


# ---------------------------------------------------------------------------------------
# trust: a transformation is not a promotion (D3)
# ---------------------------------------------------------------------------------------

def test_masking_an_untrusted_cell_does_not_launder_it() -> None:
    """Invariant: LUB still applies. Redacting an injected instruction does not make the
    remainder safe to steer on."""
    t = transformed(trust=Trust.UNTRUSTED)
    assert t.trust is Trust.UNTRUSTED
    assert t.disclosed.trust is Trust.UNTRUSTED


def test_a_TRUSTED_mask_over_an_UNTRUSTED_cell_cannot_be_constructed_at_all() -> None:
    """The join runs on the pointer, because ``underlying`` is required to be a parent.

    No new machinery: ``SourcePointer._check_trust_is_consistent`` already refuses this, and
    requiring the parent link is what routes a transformation through it.
    """
    under = raw_cell(Trust.UNTRUSTED)
    with pytest.raises(ValidationError, match="trust laundering REFUSED"):
        masked_pointer(under, trust=Trust.TRUSTED)


def test_an_unlabelled_underlying_cell_also_refuses_a_TRUSTED_mask() -> None:
    """"Never considered" is not evidence of safety — an unlabelled parent joins as UNTRUSTED."""
    with pytest.raises(ValidationError, match="trust laundering REFUSED"):
        masked_pointer(raw_cell(None), trust=Trust.TRUSTED)


def test_a_transformation_over_a_trusted_cell_stays_trusted() -> None:
    """Demotion only. The state must not over-restrict either, or it becomes unusable."""
    under = raw_cell(Trust.TRUSTED)
    t = transformed(underlying=under, pointer=masked_pointer(under, trust=Trust.TRUSTED))
    assert t.trust is Trust.TRUSTED


def test_a_forged_trust_label_on_the_mask_does_not_survive_being_read() -> None:
    """The read-time backstop reaches through the wrapper, because ``trust`` delegates."""
    t = transformed(trust=Trust.UNTRUSTED)
    object.__setattr__(t.disclosed.pointer, "trust", Trust.TRUSTED)
    assert t.disclosed.pointer.trust is Trust.TRUSTED       # the forgery is in the field
    assert t.trust is Trust.UNTRUSTED                        # and does not survive the read


# ---------------------------------------------------------------------------------------
# it must not leak the masked value, nor assert more existence than it already has
# ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("model, base", [
    (TransformRecord, dict(field_path="party.email", policy_id="p",
                           transformation=TRANSFORMATION, principal="svc:x")),
    (PartialDisclosure, dict(field_path="party.email", policy_id="p",
                             transformation=TRANSFORMATION, value="<REDACTED:email>")),
])
@pytest.mark.parametrize("leak", ["raw", "underlying_value", "value_was", "plaintext", "pointer"])
def test_neither_plane_has_anywhere_to_put_the_underlying_value(model, base, leak) -> None:
    """Invariant: ``extra="forbid"``. Structural, not conventional."""
    if leak in base:
        pytest.skip("field legitimately exists on this plane")
    with pytest.raises(ValidationError):
        model(**base, **{leak: "alice@example.com"})


def test_the_underlying_locator_carries_no_value_only_a_place_to_look() -> None:
    """Holding ``underlying`` is not an extra disclosure: a ``SourcePointer`` has no value field.

    That is the whole difference from ``Withheld``, which must never imply a value exists. Here
    the transformed value in hand already reveals a cell was there; the locator adds nothing
    beyond it, and resolving it goes through the PEP at pointer resolution (D4a).
    """
    assert "value" not in SourcePointer.model_fields
    serialised = transformed().model_dump()
    assert "alice@example.com" not in repr(serialised)
    assert serialised["disclosed"]["value"] == "<REDACTED:email>"


def test_the_public_projection_drops_every_audit_field() -> None:
    """Invariant: parallel to ``WithholdingRecord.public_denial()`` (R-003 finding 4)."""
    notice = transformed().public_disclosure()
    assert isinstance(notice, PartialDisclosure)
    dropped = ("principal", "purpose", "policy_version", "decided_at", "decision_id")
    for field in dropped:
        assert field not in PartialDisclosure.model_fields, field
    assert notice.value == "<REDACTED:email>"
    assert notice.field_path == "party.email" and notice.policy_id.endswith("portfolio_analyst")


def test_two_narrowings_differing_only_in_audit_detail_project_to_equal_objects() -> None:
    """Observational identity, for the part of it a type can hold."""
    a = transformed(record=record(principal="svc:a", purpose="p1", decision_id="e1",
                                  decided_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
    b = transformed(record=record(principal="svc:b", purpose="p2", decision_id="e2",
                                  decided_at=datetime(2026, 7, 7, tzinfo=timezone.utc)))
    assert a.record != b.record
    assert a.public_disclosure() == b.public_disclosure()


def test_an_unattributable_narrowing_is_not_an_audit_record() -> None:
    """Invariant: non-blank ``field_path``/``policy_id``/``transformation``/``principal``."""
    for blank_field in ("field_path", "policy_id", "principal"):
        with pytest.raises(ValidationError, match="requires non-blank"):
            record(**{blank_field: "   "})
    with pytest.raises(ValidationError, match="requires non-blank|not a replayable call"):
        record(transformation="   ")


# ---------------------------------------------------------------------------------------
# sealed like everything else in the module
# ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("model", [Transformed, TransformRecord, PartialDisclosure])
def test_model_construct_is_refused(model) -> None:
    with pytest.raises(TypeError, match="REFUSED"):
        model.model_construct(field_path="x")


@pytest.mark.parametrize("model", [Transformed, TransformRecord, PartialDisclosure])
def test_the_models_are_frozen_and_forbid_extras(model) -> None:
    assert model.model_config["frozen"] is True
    assert model.model_config["extra"] == "forbid"
    assert model.model_config["revalidate_instances"] == "always"


def test_model_copy_re_validates_rather_than_writing_into_dict() -> None:
    """The laundering path R-003 finding 1 closed, checked on the new state too."""
    t = transformed()
    with pytest.raises(ValidationError, match="but policy authorised"):
        t.model_copy(update={"record": record(transformation="bucket@v1(width=10)")})
    with pytest.raises(ValidationError, match="discloses None is a denial"):
        t.model_copy(update={
            "disclosed": GroundedValue(value=None, pointer=t.disclosed.pointer)})


def test_copy_replace_is_closed_by_the_same_override() -> None:
    t = transformed()
    with pytest.raises(ValidationError):
        copy.replace(t, record=record(transformation="bucket@v1(width=10)"))


def test_a_forged_instance_is_re_validated_when_it_crosses_model_validate() -> None:
    """``revalidate_instances="always"``, transitively."""
    t = transformed()
    object.__setattr__(t, "underlying", SourcePointer(
        source_id="core_banking", kind="table_cell", table="party", column="postcode",
        row_id="p-1", trust=Trust.UNTRUSTED))
    with pytest.raises(ValidationError, match="must appear among the disclosed pointer's parents"):
        Transformed.model_validate(t)


# ---------------------------------------------------------------------------------------
# D4 disjointness extends coherently to the fourth state
# ---------------------------------------------------------------------------------------

def disjoint_case(**over):
    base = dict(
        attrs={"email": "<REDACTED:email>", "postcode": None},
        attr_sources={"email": masked_pointer(raw_cell())},
        attr_withheld={"postcode": WithholdingRecord(
            field_path="party.postcode", policy_id="p", reason_code="not_in_grant",
            principal="svc:x")},
        requested=["email", "postcode"],
        attr_transformed={"email": record()},
    )
    base.update(over)
    return base


def test_the_valid_parallel_dict_shape_passes() -> None:
    assert_withheld_disjoint(**disjoint_case())
    assert_cell_states_disjoint(**disjoint_case())          # same function, clearer name


def test_a_key_cannot_be_both_withheld_and_transformed() -> None:
    """Invariant: denied and partially disclosed at once is not a decision."""
    case = disjoint_case(
        attrs={"email": "<REDACTED:email>"},
        attr_withheld={"email": WithholdingRecord(
            field_path="party.email", policy_id="p", reason_code="not_in_grant",
            principal="svc:x")},
        requested=["email"])
    with pytest.raises(ValueError, match="both withheld and transformed"):
        assert_withheld_disjoint(**case)


def test_a_transformed_key_MUST_carry_a_source_pointer() -> None:
    """The mirror of rule 4. A withholding is not a citation; a partial disclosure is one."""
    with pytest.raises(ValueError, match="carry no source pointer"):
        assert_withheld_disjoint(**disjoint_case(attr_sources={}))


def test_a_transformed_key_may_not_be_cited_verbatim() -> None:
    with pytest.raises(ValueError, match="cited with a verbatim pointer kind"):
        assert_withheld_disjoint(**disjoint_case(attr_sources={"email": raw_cell()}))


def test_a_transformed_key_must_cite_the_function_policy_authorised() -> None:
    with pytest.raises(ValueError, match="different function than policy authorised"):
        assert_withheld_disjoint(**disjoint_case(
            attr_sources={"email": masked_pointer(raw_cell(),
                                                  derivation="bucket@v1(width=10)")}))


def test_a_transformed_key_must_hold_a_non_null_value() -> None:
    """The exact mirror of rule 5, and it points the caller at ``Withheld``."""
    with pytest.raises(ValueError, match="transformed keys hold None"):
        assert_withheld_disjoint(**disjoint_case(attrs={"email": None, "postcode": None}))
    with pytest.raises(ValueError, match="transformed keys absent from attrs"):
        assert_withheld_disjoint(**disjoint_case(attrs={"postcode": None},
                                                 requested=["postcode"]))


def test_a_transformed_key_that_was_never_requested_announces_the_field() -> None:
    with pytest.raises(ValueError, match="transformed keys that were never requested"):
        assert_withheld_disjoint(**disjoint_case(requested=["postcode"]))


def test_requested_may_be_any_iterable_and_is_consumed_only_once() -> None:
    """``requested`` is typed ``Iterable[str]``, so a generator is a legal argument.

    Two rule blocks read it now. Consuming it twice would silently see an empty set the second
    time and report every transformed key as unrequested — a false positive on a *disclosure*
    rule, which is the direction that costs a caller a working read.
    """
    assert_withheld_disjoint(**disjoint_case(requested=(k for k in ("email", "postcode"))))
    assert_withheld_disjoint(**disjoint_case(requested={"email": 1, "postcode": 2}.keys()))
    assert_withheld_disjoint(**disjoint_case(requested=iter(["email", "postcode"])))


def test_omitting_attr_transformed_is_exactly_the_old_behaviour() -> None:
    """Additive: three adapters are being built against this signature right now."""
    assert_withheld_disjoint(
        attrs={"postcode": None},
        attr_sources={},
        attr_withheld={"postcode": WithholdingRecord(
            field_path="party.postcode", policy_id="p", reason_code="not_in_grant",
            principal="svc:x")},
        requested=["postcode"])
    # positionally, as the existing callers do
    assert_withheld_disjoint({"a": 1}, {"a": raw_cell()}, {})

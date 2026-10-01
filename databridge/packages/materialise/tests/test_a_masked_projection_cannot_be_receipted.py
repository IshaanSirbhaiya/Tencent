"""Spine §9 **O1b** — the known blocker, refused rather than papered over.

The cell vocabulary has three states: `GroundedValue`, `Withheld`, `Absent`. A *transformed*
disclosure — masked, redacted, generalised, bucketed — is none of them. It is not fully disclosed
(the consumer did not receive the value), it is not withheld (the consumer did receive something),
and it is certainly not absent.

That is not an abstract worry in this estate. `fixtures/packs/finance/sources.yaml` declares
`party.date_of_birth` and `party.notes` as sensitive; the adapter's Policy layer compiles
`transform: redact_pii@v1(...)` onto every disclosed sensitive column, and the Core then emits the
value behind a `derived` pointer. A Receipt over a dataset containing those cells would have to
count them as `grounded` or as `policy_suppressed`, and both are false.

**What changed on 2026-08-16, and why the refusal survived it.** `databridge_core.provenance`
now defines `Transformed`, a fourth cell state wrapping a `GroundedValue` whose pointer is
`derived`. The vocabulary gap is closed. The *plumbing* gap is not:
`databridge_adapter.results.Cell` remains a deliberately narrower three-state union that fails
closed on a `Transformed`, and Policy still hands a masked column back as an ordinary
`GroundedValue`. So this package still cannot distinguish a partial disclosure from a full one at
the point it would have to count it, and it still refuses — same behaviour, moved reason. The
refusal becomes a counter rather than a refusal on the day the adapter opts in.
"""
from __future__ import annotations

import pytest

from databridge_core import GroundedValue, SourcePointer, Trust
from databridge_materialise import (
    Dataset,
    EstateReader,
    TransformedDisclosureUnrepresentable,
    materialise,
    screen_transformed_cells,
)

from plans import party_dob_plan


def test_the_policy_really_does_compile_a_transform_for_this_projection(core, policy, compliance,
                                                                       packs_purposes):
    """The premise, checked against the real estate before anything is asserted about the refusal.

    Without this, the test below could be refusing something that never happens.
    """
    reader = EstateReader(core, compliance, policy=policy)
    req = reader.request("party", ["party_id", "legal_name", "date_of_birth", "notes"])
    table = reader.decision_table(req)
    transformed = {r.column: r.transform for r in table.columns if r.transform and not r.withheld}
    assert "date_of_birth" in transformed
    assert transformed["date_of_birth"].startswith("redact_pii@v1")


def test_materialising_a_masked_projection_is_refused_at_compile_time(core, policy, compliance,
                                                                      packs_purposes):
    """The refusal, before a single row is read.

    Deliberately at COMPILE time and from the policy's own table, so it does not depend on whether
    any particular row happened to hold a value the redaction matched. Whether a dataset is
    honestly *describable* is a property of the projection, not of the sample — and on this estate
    the sample would in fact rarely trigger it, because `redact_pii` passes a DATE through
    unchanged and matches a whole prose note against none of its patterns. A screen that only fired
    at emission would therefore pass here and fail in production on the first row that matched.
    """
    reader = EstateReader(core, compliance, policy=policy)
    with pytest.raises(TransformedDisclosureUnrepresentable) as err:
        materialise(party_dob_plan(), reader)
    message = str(err.value)
    assert "O1b" in message
    assert "date_of_birth" in message
    assert "databridge_core.provenance" in message, (
        "the refusal has to say where the fourth state belongs, or the next person fixes it here")
    assert err.value.invariant == "o1b_no_fourth_cell_state"


def test_the_same_projection_compiles_for_a_principal_who_gets_it_verbatim_or_not_at_all(
        core, policy, analyst, packs_purposes):
    """The refusal is about TRANSFORMATION, not about sensitivity.

    A credit analyst is *denied* `party.date_of_birth` outright — a plain `Withheld`, which the
    vocabulary represents perfectly well. Nothing here refuses that; a dataset full of withheld
    cells is honest, and the receipt counts them apart from absences. It is the halfway state that
    has no name.
    """
    reader = EstateReader(core, policy=policy, principal=analyst)
    req = reader.request("party", ["party_id", "date_of_birth"])
    table = reader.decision_table(req)
    rule = next(r for r in table.columns if r.column == "date_of_birth")
    assert rule.withheld and rule.transform is None
    assert rule.reason_code == "POLICY_COLUMN_DENIED"


def test_the_emission_screen_catches_a_transformed_cell_that_reached_the_rectangle():
    """The second gate, for the case where no `PolicyEngine` was supplied to the reader.

    Constructed rather than read, because on this estate the compile-time screen fires first and
    `redact_pii` happens not to alter the two sensitive columns' actual values. The cell below is
    exactly the shape `PostgresCore._emit_cell` produces when a redaction DOES change a value: a
    `derived` pointer over the raw cell, carrying the policy's registered rule.
    """
    raw = SourcePointer(source_id="core_banking", kind="table_cell", table="core_banking.party",
                        column="email", row_id="CB-P-000001", trust=Trust.UNTRUSTED)
    masked = SourcePointer(source_id="core_banking", kind="derived",
                           derivation="redact_pii@v1(column=email)", derived_from=[raw],
                           trust=Trust.UNTRUSTED)
    dataset = Dataset(columns=("party.email",),
                      rows=((GroundedValue(value="<REDACTED:email>", pointer=masked),),))
    with pytest.raises(TransformedDisclosureUnrepresentable) as err:
        screen_transformed_cells(dataset)
    assert "O1b" in str(err.value)
    assert "redact_pii@v1" in str(err.value)


def test_the_screen_does_not_refuse_this_packages_OWN_derived_pointers():
    """The screen must distinguish "policy transformed a disclosure" from "the materialiser
    computed an aggregate or sliced a chunk". Both are `derived`; only one is a partial
    disclosure."""
    raw = SourcePointer(source_id="core_banking", kind="table_cell", table="core_banking.facility",
                        column="notes", row_id="CB-F-000009", trust=Trust.UNTRUSTED)
    chunk = SourcePointer(source_id="core_banking", kind="derived",
                          derivation="text_slice@v1(start=0,end=5)", derived_from=[raw],
                          trust=Trust.UNTRUSTED)
    dataset = Dataset(columns=("text",),
                      rows=((GroundedValue(value="Prici", pointer=chunk),),))
    screen_transformed_cells(dataset)          # no raise

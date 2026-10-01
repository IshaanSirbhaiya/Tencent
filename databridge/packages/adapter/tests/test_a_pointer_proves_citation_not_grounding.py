"""§4a — the wall runs against the bytes, not against the pointer object.

The failure this catches is the one that passes every structural check: a Core emits ``"eligible"``
with a perfectly well-formed `query_cell` pointer. The pointer validates. `GroundedRows` validates.
The cell is accounted for, cited, and fabricated.

And the same rule immediately convicts the obvious redaction idiom, which is worth its own section
below because it is the one every implementer writes first.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from databridge_adapter import (
    UngroundedValue,
    assert_cells_grounded,
    assert_derived_from,
    assert_grounded,
    verbatim_match,
)
from databridge_core import GroundedValue, SourcePointer, Trust


def cell_pointer(column="legal_name") -> SourcePointer:
    return SourcePointer(source_id="core_banking", kind="table_cell", table="party",
                         column=column, row_id="P-1", trust=Trust.TRUSTED)


def span_pointer() -> SourcePointer:
    return SourcePointer(source_id="filings", kind="doc_span", document_id="d1",
                         char_start=0, char_end=40, trust=Trust.UNTRUSTED)


# --- the wall ------------------------------------------------------------------------

def test_a_value_the_source_never_said_is_refused():
    cell = GroundedValue(value="eligible", pointer=cell_pointer())
    with pytest.raises(UngroundedValue, match="fabricated"):
        assert_grounded(cell, fetched="Acme Pte Ltd")


def test_the_refusal_names_the_derived_escape_hatch():
    """An implementer who hits this must be told the legitimate way to emit a transformed value,
    or they will reach for the pointer instead."""
    cell = GroundedValue(value="<REDACTED:email>", pointer=cell_pointer("email"))
    with pytest.raises(UngroundedValue) as exc:
        assert_grounded(cell, fetched="ops@acme.example")
    assert "derived" in str(exc.value) and "derived_from" in str(exc.value)


def test_a_verbatim_value_passes():
    assert_grounded(GroundedValue(value="Acme Pte Ltd", pointer=cell_pointer()),
                    fetched="Acme Pte Ltd")


def test_a_doc_span_is_grounded_by_containment():
    """A span cites a slice of a larger block, so containment is the honest test there."""
    assert_grounded(GroundedValue(value="Acme Pte Ltd", pointer=span_pointer()),
                    fetched="the borrower, Acme Pte Ltd, of Singapore")


def test_a_whole_row_may_be_passed_as_the_fetched_evidence():
    assert_grounded(GroundedValue(value="Acme Pte Ltd", pointer=cell_pointer()),
                    fetched={"party_id": "P-1", "legal_name": "Acme Pte Ltd"})


def test_a_model_produced_claim_has_no_place_in_a_read():
    """A Core is deterministic and has no model; a generated claim here means a value was produced
    rather than fetched."""
    ptr = SourcePointer(source_id="core_banking", kind="generated_claim", claim_id="c1",
                        facts=[cell_pointer()], trust=Trust.UNTRUSTED)
    with pytest.raises(UngroundedValue, match="no place in an adapter read"):
        assert_grounded(GroundedValue(value="probably eligible", pointer=ptr), fetched="Acme")


# --- the redaction idiom (§4a) --------------------------------------------------------

def test_the_wrong_redaction_idiom_is_convicted():
    """`GroundedValue(value=redact(raw), pointer=<pointer to the RAW cell>)` - the citation
    vouches for something the source never said."""
    wrong = GroundedValue(value="<REDACTED:email>", pointer=cell_pointer("email"))
    with pytest.raises(UngroundedValue):
        assert_grounded(wrong, fetched="ops@acme.example")


def test_the_right_redaction_idiom_passes():
    raw = cell_pointer("email")
    derived = SourcePointer(
        source_id="core_banking", kind="derived",
        # A REGISTERED, REPLAYABLE rule rather than prose (review R-3A finding 2): the wall
        # re-runs it over the cited parent and requires it to produce exactly this value.
        derivation="redact_pii@v1(column=email) - RFC5322 local-part+domain replaced",
        derived_from=[raw], trust=raw.trust)
    cell = GroundedValue(value="<REDACTED:email>", pointer=derived)
    assert_grounded(cell, fetched="ops@acme.example")
    assert_derived_from(derived, raw)


def test_a_derived_pointer_must_state_its_derivation():
    """`SourcePointer` requires the field to be PRESENT; the wall requires it to say something.

    Core's per-kind validator only checks that `derivation` is not None, so a whitespace-only
    derivation constructs happily - and a `derived` pointer is exactly where the emitted bytes are
    ALLOWED to differ from the cited ones. An unexplained derivation there is an unsourced value
    with a plausible label, so the wall refuses it.
    """
    raw = cell_pointer("email")
    blank = SourcePointer(source_id="core_banking", kind="derived", derivation="   ",
                          derived_from=[raw], trust=raw.trust)
    with pytest.raises(UngroundedValue, match="state its derivation"):
        assert_grounded(GroundedValue(value="<REDACTED:email>", pointer=blank),
                        fetched="ops@acme.example")


def test_a_derived_pointer_that_drops_its_parent_is_refused():
    """Without `derived_from`, the raw cell is no longer resolvable by a principal entitled to see
    it, and the redaction has laundered a value into an unsourced one."""
    raw = cell_pointer("email")
    other = cell_pointer("phone")
    derived = SourcePointer(source_id="core_banking", kind="derived", derivation="redact(email)",
                            derived_from=[other], trust=Trust.TRUSTED)
    with pytest.raises(UngroundedValue, match="derived_from"):
        assert_derived_from(derived, raw)


def test_redaction_does_not_launder_trust():
    """LUB over {trusted < untrusted}: the derived pointer cannot claim TRUSTED over an untrusted
    parent. Enforced in `databridge_core`; asserted here because §4a's example depends on it."""
    raw = SourcePointer(source_id="core_banking", kind="table_cell", table="party",
                        column="notes", row_id="P-1", trust=Trust.UNTRUSTED)
    with pytest.raises(Exception, match="laundering"):
        SourcePointer(source_id="core_banking", kind="derived", derivation="redact(notes)",
                      derived_from=[raw], trust=Trust.TRUSTED)


# --- verbatim_match -------------------------------------------------------------------

@pytest.mark.parametrize("value,fetched", [
    ("Acme", "Acme"),
    ("Acme", " Acme  "),
    ("Acme", "the borrower Acme Pte Ltd"),
    (1, 1.0),
    ("1.50", Decimal("1.5")),
    (Decimal("1000.50"), 1000.5),
    (None, None),
    (True, True),
    (date(2026, 1, 1), date(2026, 1, 1)),
    (date(2026, 1, 1), "signed on 2026-01-01 in Singapore"),
    ("x", ["a", "x"]),
])
def test_verbatim_match_accepts(value, fetched):
    assert verbatim_match(value, fetched) is True


@pytest.mark.parametrize("value,fetched", [
    ("Acme Holdings", "Acme"),   # the emitted value is LONGER than what the source said
    ("eligible", "Acme Pte Ltd"),
    (None, "Acme"),
    ("Acme", None),
    (True, 1),                      # Python's True == 1 must not make a fabrication look grounded
    (1, True),
    ("", "Acme"),                   # every string contains ""; an empty emitted value is not found
    (1, 2),
])
def test_verbatim_match_refuses(value, fetched):
    assert verbatim_match(value, fetched) is False


def test_a_null_cell_grounds_only_against_a_null():
    assert_grounded(GroundedValue(value=None, pointer=cell_pointer()), fetched=None)
    with pytest.raises(UngroundedValue):
        assert_grounded(GroundedValue(value="Acme", pointer=cell_pointer()), fetched=None)


def test_a_datetime_grounds_against_its_iso_text():
    assert verbatim_match(datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc),
                          "2026-08-15T12:00:00+00:00") is True


# --- the row-level helper --------------------------------------------------------------

def test_a_row_check_refuses_an_arity_mismatch():
    """A zip() here would silently skip exactly the trailing cells nobody reviews."""
    cells = [GroundedValue(value="Acme", pointer=cell_pointer())]
    with pytest.raises(UngroundedValue, match="arity"):
        assert_cells_grounded(cells, ["Acme", "extra"])


def test_a_row_check_passes_when_every_cell_is_grounded():
    cells = [GroundedValue(value="P-1", pointer=cell_pointer("party_id")),
             GroundedValue(value="Acme", pointer=cell_pointer("legal_name"))]
    assert_cells_grounded(cells, ["P-1", "Acme"])

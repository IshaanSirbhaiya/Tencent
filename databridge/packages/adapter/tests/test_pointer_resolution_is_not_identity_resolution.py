"""`PointerResolution` is deliberately not `ResolutionResult` (§3).

`ResolutionResult` already means **identity** resolution and carries
``requires_human_selection: Literal[True]`` - the hard constraint that a machine never confirms
who someone is. Merging the two names would put that constraint one autocomplete away from a
data-fetch type, so they are kept lexically distant and this file asserts the distance rather than
trusting the convention.

The second half of the file constrains verdict against cell. The pairs that need constraining are
the ones that look harmless: a `FORBIDDEN` carrying a value, a `VANISHED` carrying a value, a
`VERIFIED` carrying nothing at all.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import databridge_adapter
from databridge_adapter import PointerResolution, Verdict
from databridge_core import (
    Absent,
    GroundedValue,
    SourcePointer,
    Trust,
    Withheld,
    WithholdingRecord,
)

NOW = datetime(2026, 8, 16, 9, 0, tzinfo=timezone.utc)
PACKAGE_DIR = Path(databridge_adapter.__file__).resolve().parent


def pointer() -> SourcePointer:
    return SourcePointer(source_id="core_banking", kind="table_cell", table="party",
                         column="legal_name", row_id="P-1", trust=Trust.TRUSTED)


def value() -> GroundedValue:
    return GroundedValue(value="Acme Pte Ltd", pointer=pointer())


def denial() -> Withheld:
    return Withheld(record=WithholdingRecord(
        field_path="party.legal_name", policy_id="pol-7", reason_code="RESTRICTED",
        principal="orchestrator@walnut"))


# --- the naming separation ------------------------------------------------------------

def test_resolution_result_is_not_exported_from_this_package():
    assert not hasattr(databridge_adapter, "ResolutionResult")
    assert "ResolutionResult" not in databridge_adapter.__all__


def test_no_type_here_declares_requires_human_selection():
    """The identity constraint lives with identity RESOLUTION, in the graph. A data-fetch type
    that declared it would be one rename away from being mistaken for the thing that carries it.

    Prose about *why* the names are kept apart is fine and is the point of the separation; a
    FIELD or an assignment is not. So the check is for a declaration - the identifier followed by
    an annotation or an assignment - rather than for the string.
    """
    declared = re.compile(r"^\s*requires_human_selection\s*[:=]", re.MULTILINE)
    offenders = [p.name for p in PACKAGE_DIR.glob("*.py")
                 if declared.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_the_separation_is_explained_where_it_matters():
    """A lexical rule nobody can find the reason for is a rule that gets refactored away."""
    assert "ResolutionResult" in (PointerResolution.__doc__ or "")


def test_the_two_names_are_lexically_distant():
    assert "PointerResolution" in databridge_adapter.__all__


# --- verdict and cell must agree --------------------------------------------------------

def test_forbidden_must_carry_a_denial():
    with pytest.raises(ValueError, match="Withheld"):
        PointerResolution(pointer=pointer(), verdict=Verdict.FORBIDDEN, resolved_at=NOW,
                          cell=value(), note="policy changed")


def test_forbidden_with_a_withholding_record_is_accepted():
    res = PointerResolution(pointer=pointer(), verdict=Verdict.FORBIDDEN, resolved_at=NOW,
                            cell=denial(), note="policy pol-7 now denies this field")
    assert res.must_escalate is False


def test_vanished_may_not_carry_a_value():
    with pytest.raises(ValueError, match="VANISHED"):
        PointerResolution(pointer=pointer(), verdict=Verdict.VANISHED, resolved_at=NOW,
                          cell=value(), note="row deleted")


def test_expired_may_carry_an_absence():
    res = PointerResolution(pointer=pointer(), verdict=Verdict.EXPIRED, resolved_at=NOW,
                            cell=Absent(field_path="party.legal_name"),
                            note="cursor window closed; full resync scheduled")
    assert res.verdict is Verdict.EXPIRED


def test_a_positive_verdict_must_produce_the_value_it_claims_matched():
    with pytest.raises(ValueError, match="assertion, not a"):
        PointerResolution(pointer=pointer(), verdict=Verdict.VERIFIED, resolved_at=NOW)


def test_verified_as_of_capture_is_a_positive_verdict_and_needs_a_note():
    res = PointerResolution(pointer=pointer(), verdict=Verdict.VERIFIED_AS_OF_CAPTURE,
                            resolved_at=NOW, cell=value(),
                            note="matched capture sha256:...; live source has moved on")
    assert res.verdict.is_positive


def test_anything_but_a_plain_verified_needs_a_note():
    """'Unverifiable' with no reason is indistinguishable from 'we did not try'."""
    with pytest.raises(ValueError, match="requires a note"):
        PointerResolution(pointer=pointer(), verdict=Verdict.UNVERIFIABLE, resolved_at=NOW)


def test_drift_material_escalates():
    res = PointerResolution(pointer=pointer(), verdict=Verdict.DRIFT_MATERIAL, resolved_at=NOW,
                            note="legal_name changed from 'Acme Pte Ltd' to 'Acme Holdings'")
    assert res.must_escalate is True


def test_a_naive_resolution_time_is_refused():
    with pytest.raises(ValueError, match="timezone-aware"):
        PointerResolution(pointer=pointer(), verdict=Verdict.VERIFIED,
                          resolved_at=datetime(2026, 8, 16, 9, 0), cell=value())

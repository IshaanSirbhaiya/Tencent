"""`GroundedRows`: no cell leaves without a pointer, a denial, or a named absence (D4, §4).

Three states, and the reason there are three rather than two: *"not present in the source"* and
*"present in the source, not permitted to you"* are different facts. Collapsing them is a
correctness bug in the credibility-critical path, and downstream it opens a missingness covert
channel in any dataset materialised from the graph.

What this file does NOT test is grounding - see `test_a_pointer_proves_citation_not_grounding.py`.
The validator here checks that every cell is *accounted for*, and `ADAPTER_AGENT_SPEC` §10 is
explicit that an ungrounded cell is caught by `assert_grounded` at emission, "not the GroundedRows
validator, which checks arity only".
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from databridge_adapter import AdapterResult, Cost, GroundedRows
from databridge_core import (
    Absent,
    GroundedValue,
    SnapshotIdentity,
    SourcePointer,
    Trust,
    Withheld,
    WithholdingRecord,
)

AS_OF = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def snapshot(query_hash="rh-1") -> SnapshotIdentity:
    return SnapshotIdentity(source_id="core_banking", as_of=AS_OF, content_hash="ch",
                            schema_hash="sh", query_hash=query_hash)


def gv(value, column="legal_name", row="P-1") -> GroundedValue:
    return GroundedValue(value=value, pointer=SourcePointer(
        source_id="core_banking", kind="table_cell", table="party", column=column,
        row_id=row, trust=Trust.TRUSTED))


def withheld(column="notes") -> Withheld:
    return Withheld(record=WithholdingRecord(
        field_path=f"party.{column}", policy_id="pol-7", reason_code="RESTRICTED_FIELD",
        principal="orchestrator@walnut"))


# --- arity ---------------------------------------------------------------------------

def test_a_short_row_is_refused():
    with pytest.raises(ValidationError, match="arity"):
        GroundedRows(columns=("party_id", "legal_name"), rows=((gv("P-1"),),),
                     snapshot=snapshot())


def test_a_long_row_is_refused():
    with pytest.raises(ValidationError, match="arity"):
        GroundedRows(columns=("party_id",), rows=((gv("P-1"), gv("Acme")),), snapshot=snapshot())


def test_columns_must_be_named():
    with pytest.raises(ValidationError):
        GroundedRows(columns=(), rows=(), snapshot=snapshot())


def test_duplicate_columns_are_refused():
    with pytest.raises(ValidationError, match="duplicate"):
        GroundedRows(columns=("notes", "notes"), rows=(), snapshot=snapshot())


# --- the three states ----------------------------------------------------------------

def test_all_three_cell_states_coexist_in_one_row():
    rows = GroundedRows(
        columns=("party_id", "notes", "lei"),
        rows=((gv("P-1", column="party_id"), withheld("notes"), Absent(field_path="party.lei")),),
        snapshot=snapshot())
    assert rows.withheld_count == 1
    assert rows.absent_count == 1


def test_a_withheld_cell_carries_no_pointer():
    """Withholding is not a citation: a pointer beside a withheld field would vouch for something
    the consumer was never shown."""
    with pytest.raises(ValidationError):
        Withheld(record=withheld().record, pointer=gv("x").pointer)  # type: ignore[call-arg]


def test_a_denial_attributed_to_the_wrong_field_is_refused():
    """A denial in the wrong column reads as an answer about a field nobody asked about."""
    with pytest.raises(ValidationError, match="wrong field"):
        GroundedRows(columns=("notes",), rows=((withheld("email"),),), snapshot=snapshot())


def test_an_absence_must_name_the_question_that_was_asked():
    with pytest.raises(ValidationError, match="question that was asked"):
        GroundedRows(columns=("lei",), rows=((Absent(field_path="party.notes"),),),
                     snapshot=snapshot())


def test_a_dotted_field_path_matches_on_its_last_segment():
    rows = GroundedRows(columns=("notes",),
                        rows=((Absent(field_path="core_banking.party.notes"),),),
                        snapshot=snapshot())
    assert rows.absent_count == 1


def test_a_bare_value_cannot_be_a_cell():
    with pytest.raises(ValidationError):
        GroundedRows(columns=("legal_name",), rows=(("Acme Pte Ltd",),),  # type: ignore[arg-type]
                     snapshot=snapshot())


def test_a_grounded_value_cannot_be_built_without_a_pointer():
    """Withholding cannot be expressed as a degenerate GroundedValue, and this is why."""
    with pytest.raises(ValidationError):
        GroundedValue(value=None, pointer=None)              # type: ignore[arg-type]


# --- the result binds itself to the read that produced it -----------------------------

def test_a_result_whose_snapshot_cites_another_read_is_refused():
    """read_hash BECOMES query_hash (D2). When they differ, nothing in the result can be verified
    by re-executing the request that produced it."""
    rows = GroundedRows(columns=("legal_name",), rows=((gv("Acme"),),),
                        snapshot=snapshot(query_hash="a-different-read"))
    with pytest.raises(ValidationError, match="query_hash"):
        AdapterResult(read_hash="rh-1", rows=rows)


def test_a_result_bound_to_its_read_is_accepted():
    rows = GroundedRows(columns=("legal_name",), rows=((gv("Acme"),),), snapshot=snapshot("rh-1"))
    result = AdapterResult(read_hash="rh-1", rows=rows, cost=Cost(rows=1, wall_ms=3))
    assert result.rows.snapshot.query_hash == result.read_hash


def test_a_result_must_name_the_read_it_answers():
    rows = GroundedRows(columns=("legal_name",), rows=(), snapshot=snapshot(""))
    with pytest.raises(ValidationError):
        AdapterResult(read_hash="   ", rows=rows)


def test_costs_add():
    assert (Cost(rows=2, wall_ms=5) + Cost(rows=3, wall_ms=7)).rows == 5


def test_a_cost_cannot_be_negative():
    with pytest.raises(ValidationError):
        Cost(rows=-1)

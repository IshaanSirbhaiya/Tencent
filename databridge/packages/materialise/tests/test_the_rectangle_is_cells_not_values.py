"""The dataset layer: what the rectangle holds, what the content hash covers, what gets written.

The claim these tests defend is narrow and load-bearing: there is no stage between the adapter and
the file at which a value is separated from its pointer. Everything else here follows from it —
the three missing reasons survive to the file, the content hash moves when a cell's *state*
changes and not only its value, and a format the plan did not name is refused rather than
substituted.
"""
from __future__ import annotations

import json

import pytest

from databridge_core import Absent, GroundedValue, SourcePointer, Trust, Withheld, WithholdingRecord
from databridge_materialise import Dataset, cell_digest, materialise, missing_reason, write_jsonl

from plans import covenant_plan


def _ptr(column="c"):
    return SourcePointer(source_id="s", kind="table_cell", table="t", column=column, row_id="r",
                         trust=Trust.TRUSTED)


def _withheld(column="c"):
    return Withheld(record=WithholdingRecord(field_path=f"t.{column}", policy_id="p",
                                             reason_code="POLICY_COLUMN_DENIED", principal="u"))


def test_the_three_missing_reasons_are_three_different_answers():
    assert missing_reason(GroundedValue(value=1, pointer=_ptr())) is None
    assert missing_reason(_withheld()) == "policy_suppressed"
    assert missing_reason(Absent(field_path="t.c")) == "not_in_source"


def test_a_withheld_cell_and_an_absent_cell_are_different_datasets():
    """Both render as null. If the content hash could not tell them apart, a policy change that
    turned one into the other would leave a receipt claiming byte reproduction across it."""
    a = cell_digest(_withheld(), float_scale=6)
    b = cell_digest(Absent(field_path="t.c"), float_scale=6)
    assert a != b


def test_the_content_hash_covers_order():
    """It is computed under the plan's declared `row_order`; two orderings of the same cells are
    two datasets, and the receipt names which one it built."""
    one = GroundedValue(value=1, pointer=_ptr())
    two = GroundedValue(value=2, pointer=_ptr())
    forward = Dataset(columns=("c",), rows=((one,), (two,)))
    backward = Dataset(columns=("c",), rows=((two,), (one,)))
    assert forward.content_hash() != backward.content_hash()


def test_sorting_is_total_even_when_a_sort_key_is_withheld(built):
    """A `Withheld` in a sort key must not make the dataset unorderable for the one principal who
    cannot see that column. Non-values sort last, and the order stays total because it contains
    the cohort key."""
    dataset = Dataset(columns=("k", "v"),
                      rows=((GroundedValue(value="b", pointer=_ptr("k")), _withheld("v")),
                            (GroundedValue(value="a", pointer=_ptr("k")),
                             GroundedValue(value=1, pointer=_ptr("v")))))
    ordered = dataset.sorted_by(("v", "k"))
    assert [c.value for c, _ in ordered.rows] == ["a", "b"]


def test_the_missing_reason_companions_reach_the_file(built, tmp_path):
    """`emit_missing_reason: true` is not a flag on a report — it is columns in the artefact, so a
    consumer that never opens the receipt still cannot mistake a suppressed cell for an absent
    one."""
    path = tmp_path / "out.jsonl"
    write_jsonl(built.dataset, path)
    first = json.loads(path.read_text("utf-8").splitlines()[0])
    for column in built.dataset.columns:
        assert f"{column}_missing_reason" in first
    assert "_split" in first


def test_parquet_is_written_when_the_plan_names_it(reader, tmp_path):
    """Parquet + Croissant JSON-LD is the de facto interchange pair, so the tabular shape has to
    land as Parquet when asked."""
    pytest.importorskip("pyarrow")
    plan = covenant_plan(output=covenant_plan().output.model_copy(update={"format": "parquet"}))
    built = materialise(plan, reader, out_dir=tmp_path)
    # `output_stem`, not `id`: file names use the FULL plan_hash (R-014 Finding 13).
    written = tmp_path / f"{plan.output_stem}.parquet"
    assert written.is_file()

    import pyarrow.parquet as pq

    table = pq.read_table(written)
    assert table.num_rows == built.dataset.n_rows
    assert "covenant_test.prior_observed_value_missing_reason" in table.column_names


def test_fully_absent_columns_are_reported_not_dropped():
    """A feature emptied by the temporal cut is a finding about the SOURCE — usually that it keeps
    only current state — and dropping the column silently would delete the finding with it."""
    dataset = Dataset(columns=("kept", "empty"),
                      rows=((GroundedValue(value=1, pointer=_ptr("kept")),
                             Absent(field_path="t.empty")),))
    assert dataset.fully_absent_columns() == ("empty",)


@pytest.fixture
def built(reader):
    return materialise(covenant_plan(), reader)

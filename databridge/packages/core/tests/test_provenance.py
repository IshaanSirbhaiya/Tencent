"""Property tests: every SourcePointer kind validates its required fields (02_TRD Task 1).

The contract is that a pointer of the wrong shape fails LOUDLY at construction — this is
what makes provenance a type-enforced invariant rather than a hope.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from databridge_core.provenance import (
    _REQUIRED_FIELDS,
    GroundedValue,
    SnapshotIdentity,
    SourcePointer,
)

SNAP = SnapshotIdentity(
    source_id="pg_main",
    as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
    content_hash="c0",
    schema_hash="s0",
)
LEAF = SourcePointer(source_id="pg_main", kind="table_cell", table="t", column="c", row_id="1")

# One minimal VALID example per kind.
VALID: dict[str, dict] = {
    "table_cell": dict(source_id="s", kind="table_cell", table="t", column="c", row_id="1"),
    "doc_span": dict(source_id="s", kind="doc_span", document_id="d", char_start=0, char_end=5),
    "transcript_span": dict(source_id="s", kind="transcript_span", document_id="d",
                            char_start=0, char_end=5, timestamp_ms=1000, segment_idx=2),
    "code_node": dict(source_id="s", kind="code_node", file_path="f.py", ast_node_id="n1"),
    "query_cell": dict(source_id="s", kind="query_cell", snapshot=SNAP, query_hash="q",
                       row_ordinal=3, column="c"),
    "workbook_cell": dict(source_id="s", kind="workbook_cell", workbook_sha="w",
                          sheet="Sheet1", cell="B7"),
    "ocr_bbox": dict(source_id="s", kind="ocr_bbox", media_id="m", page=1,
                     x0=0.0, y0=0.0, x1=1.0, y1=1.0, ocr_conf=0.9),
    "email_part": dict(source_id="s", kind="email_part", message_id="<m@x>", part="attachment:0"),
    "generated_claim": dict(source_id="s", kind="generated_claim", claim_id="c1", facts=[LEAF]),
    "derived": dict(source_id="s", kind="derived", derivation="SUM over 7d", derived_from=[LEAF]),
}


def test_every_kind_has_a_valid_example():
    # guards against a new kind being added without test coverage
    from databridge_core.provenance import SourcePointerKind
    import typing
    assert set(VALID) == set(typing.get_args(SourcePointerKind))


@pytest.mark.parametrize("kind", list(VALID))
def test_valid_pointer_constructs(kind):
    p = SourcePointer(**VALID[kind])
    assert p.kind == kind
    assert p.source_id == "s" or kind == "table_cell"


@pytest.mark.parametrize("kind", list(VALID))
def test_missing_required_field_raises(kind):
    for field in _REQUIRED_FIELDS.get(kind, ()):
        kwargs = dict(VALID[kind])
        kwargs.pop(field, None)
        with pytest.raises(ValidationError):
            SourcePointer(**kwargs)


def test_workbook_cell_requires_cell_or_range():
    SourcePointer(source_id="s", kind="workbook_cell", workbook_sha="w", sheet="S",
                  cell_range="A2:D9")                     # range alone is fine
    with pytest.raises(ValidationError):
        SourcePointer(source_id="s", kind="workbook_cell", workbook_sha="w", sheet="S")


def test_generated_claim_requires_nonempty_facts():
    with pytest.raises(ValidationError):
        SourcePointer(source_id="s", kind="generated_claim", claim_id="c1", facts=[])


def test_derived_requires_pointers_or_predicate():
    # compression path: predicate WITH the full verifiable bundle is valid
    SourcePointer(source_id="s", kind="derived", derivation="agg",
                  predicate="amount > 0", row_count=1000, rows_digest="d", snapshot=SNAP)
    with pytest.raises(ValidationError):
        SourcePointer(source_id="s", kind="derived", derivation="agg")


def test_derived_compression_must_be_verifiable():
    # predicate set but missing snapshot/row_count/rows_digest ⇒ unverifiable ⇒ reject
    with pytest.raises(ValidationError):
        SourcePointer(source_id="s", kind="derived", derivation="agg",
                      predicate="amount > 0")


def test_derived_sample_row_ids_capped_at_20():
    with pytest.raises(ValidationError):
        SourcePointer(source_id="s", kind="derived", derivation="agg",
                      predicate="amount > 0", row_count=99, rows_digest="d", snapshot=SNAP,
                      sample_row_ids=[str(i) for i in range(21)])


def test_email_to_attachment_chain():
    # provenance chain email → attachment → span (09_FINAL_PRD §11.6)
    email = SourcePointer(source_id="mail", kind="email_part", message_id="<m@x>",
                          part="attachment:0")
    val = GroundedValue(
        value="$4,200.00",
        pointer=SourcePointer(source_id="att0", kind="doc_span", document_id="att0.pdf",
                              char_start=812, char_end=821, derived_from=[email]),
    )
    assert val.confidence == 1.0
    assert val.pointer.derived_from[0].part == "attachment:0"


def test_grounded_value_default_confidence():
    gv = GroundedValue(value=42, pointer=LEAF)
    assert gv.confidence == 1.0


def test_pointers_and_values_are_frozen():
    with pytest.raises(ValidationError):
        LEAF.column = "other"
    gv = GroundedValue(value=1, pointer=LEAF)
    with pytest.raises(ValidationError):
        gv.confidence = 0.5

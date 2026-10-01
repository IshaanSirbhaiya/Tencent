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
    Trust,
)

SNAP = SnapshotIdentity(
    source_id="pg_main",
    as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
    content_hash="c0",
    schema_hash="s0",
)
LEAF = SourcePointer(source_id="pg_main", kind="table_cell", table="t", column="c", row_id="1",
                     trust=Trust.TRUSTED)

# One minimal VALID example per kind. Trust labels follow the real rule (D3): who can WRITE
# the bytes. A DB cell an operator controls is trusted; a document, an email, an OCR box or a
# model claim is not — however accurately it was read.
VALID: dict[str, dict] = {
    "table_cell": dict(source_id="s", kind="table_cell", table="t", column="c", row_id="1",
                       trust=Trust.TRUSTED),
    "doc_span": dict(source_id="s", kind="doc_span", document_id="d", char_start=0, char_end=5,
                     trust=Trust.UNTRUSTED),
    "transcript_span": dict(source_id="s", kind="transcript_span", document_id="d",
                            char_start=0, char_end=5, timestamp_ms=1000, segment_idx=2,
                            trust=Trust.UNTRUSTED),
    "code_node": dict(source_id="s", kind="code_node", file_path="f.py", ast_node_id="n1",
                      trust=Trust.TRUSTED),
    "query_cell": dict(source_id="s", kind="query_cell", snapshot=SNAP, query_hash="q",
                       row_ordinal=3, column="c", trust=Trust.TRUSTED),
    "workbook_cell": dict(source_id="s", kind="workbook_cell", workbook_sha="w",
                          sheet="Sheet1", cell="B7", trust=Trust.UNTRUSTED),
    "ocr_bbox": dict(source_id="s", kind="ocr_bbox", media_id="m", page=1,
                     x0=0.0, y0=0.0, x1=1.0, y1=1.0, ocr_conf=0.9, trust=Trust.UNTRUSTED),
    "email_part": dict(source_id="s", kind="email_part", message_id="<m@x>", part="attachment:0",
                       trust=Trust.UNTRUSTED),
    "generated_claim": dict(source_id="s", kind="generated_claim", claim_id="c1", facts=[LEAF],
                            trust=Trust.UNTRUSTED),
    "derived": dict(source_id="s", kind="derived", derivation="SUM over 7d", derived_from=[LEAF],
                    trust=Trust.TRUSTED),
}


def test_every_kind_has_a_valid_example():
    # guards against a new kind being added without test coverage
    from databridge_core.provenance import SourcePointerKind
    import typing
    assert set(VALID) == set(typing.get_args(SourcePointerKind))


@pytest.mark.parametrize("kind", list(VALID))
def test_valid_pointer_constructs(kind):
    p = SourcePointer(**VALID[kind])   # trust-gate: exempt - VALID labels every kind
    assert p.kind == kind
    assert p.source_id == "s" or kind == "table_cell"


@pytest.mark.parametrize("kind", list(VALID))
def test_missing_required_field_raises(kind):
    for field in _REQUIRED_FIELDS.get(kind, ()):
        kwargs = dict(VALID[kind])
        kwargs.pop(field, None)
        with pytest.raises(ValidationError):
            SourcePointer(**kwargs)    # trust-gate: exempt - malformed on purpose


def test_workbook_cell_requires_cell_or_range():
    SourcePointer(source_id="s", kind="workbook_cell", workbook_sha="w", sheet="S",
                  cell_range="A2:D9", trust=Trust.UNTRUSTED)     # range alone is fine
    with pytest.raises(ValidationError):
        SourcePointer(source_id="s", kind="workbook_cell", workbook_sha="w", sheet="S",
                      trust=Trust.UNTRUSTED)


def test_generated_claim_requires_nonempty_facts():
    with pytest.raises(ValidationError):
        SourcePointer(source_id="s", kind="generated_claim", claim_id="c1", facts=[],
                      trust=Trust.UNTRUSTED)


def test_derived_requires_pointers_or_predicate():
    # compression path: predicate WITH the full verifiable bundle is valid.
    # UNTRUSTED, not TRUSTED: a parentless `derived` may exist, it just may not claim trust —
    # see test_trust_lattice_cannot_be_laundered.py for that rule and why.
    SourcePointer(source_id="s", kind="derived", derivation="agg", trust=Trust.UNTRUSTED,
                  predicate="amount > 0", row_count=1000, rows_digest="d", snapshot=SNAP)
    with pytest.raises(ValidationError):
        SourcePointer(source_id="s", kind="derived", derivation="agg", trust=Trust.UNTRUSTED)


def test_derived_compression_must_be_verifiable():
    # predicate set but missing snapshot/row_count/rows_digest ⇒ unverifiable ⇒ reject
    with pytest.raises(ValidationError):
        SourcePointer(source_id="s", kind="derived", derivation="agg", trust=Trust.TRUSTED,
                      predicate="amount > 0")


def test_derived_sample_row_ids_capped_at_20():
    with pytest.raises(ValidationError):
        SourcePointer(source_id="s", kind="derived", derivation="agg", trust=Trust.TRUSTED,
                      predicate="amount > 0", row_count=99, rows_digest="d", snapshot=SNAP,
                      sample_row_ids=[str(i) for i in range(21)])


def test_email_to_attachment_chain():
    # provenance chain email → attachment → span (09_FINAL_PRD §11.6)
    email = SourcePointer(source_id="mail", kind="email_part", message_id="<m@x>",
                          part="attachment:0", trust=Trust.UNTRUSTED)
    val = GroundedValue(
        value="$4,200.00",
        pointer=SourcePointer(source_id="att0", kind="doc_span", document_id="att0.pdf",
                              char_start=812, char_end=821, derived_from=[email],
                              trust=Trust.UNTRUSTED),
    )
    assert val.confidence == 1.0
    assert val.pointer.derived_from[0].part == "attachment:0"
    assert val.trust is Trust.UNTRUSTED


def test_grounded_value_default_confidence():
    gv = GroundedValue(value=42, pointer=LEAF)
    assert gv.confidence == 1.0


def test_pointers_and_values_are_frozen():
    with pytest.raises(ValidationError):
        LEAF.column = "other"
    gv = GroundedValue(value=1, pointer=LEAF)
    with pytest.raises(ValidationError):
        gv.confidence = 0.5

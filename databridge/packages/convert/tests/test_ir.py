"""C01 IR layer: every IR is UNCONSTRUCTABLE without provenance (09_FINAL_PRD §7) —
an adapter that builds an IR without a snapshot fails CONSTRUCTION, not validation."""
from __future__ import annotations

from datetime import datetime, timezone

import pyarrow as pa
import pytest
from pydantic import ValidationError

from databridge_core import SnapshotIdentity
from databridge_convert.ir import DocumentRecord, RowBatch, TypedRecord

SNAP = SnapshotIdentity(source_id="s", as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        content_hash="h", schema_hash="sh")


def test_rowbatch_requires_snapshot():
    batch = pa.RecordBatch.from_pydict({"a": [1]})
    with pytest.raises(ValidationError):
        RowBatch(data=batch)                       # no snapshot → construction fails
    assert RowBatch(data=batch, snapshot=SNAP).num_rows == 1


def test_typedrecord_requires_snapshot():
    with pytest.raises(ValidationError):
        TypedRecord(root={"a": 1})                 # no snapshot
    tr = TypedRecord(root=[{"a": 1}, {"a": 2}], snapshot=SNAP)
    assert tr.records() == [{"a": 1}, {"a": 2}]


def test_documentrecord_requires_snapshot():
    with pytest.raises(ValidationError):
        DocumentRecord(document_id="d", sha256="x")   # no snapshot


def test_rowbatch_from_records_roundtrip():
    rb = RowBatch.from_records([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}], SNAP)
    assert rb.columns == ["a", "b"]
    assert rb.records() == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]


def test_typedrecord_records_view():
    assert TypedRecord(root={"a": 1}, snapshot=SNAP).records() == [{"a": 1}]
    assert TypedRecord(root="scalar", snapshot=SNAP).records() == []

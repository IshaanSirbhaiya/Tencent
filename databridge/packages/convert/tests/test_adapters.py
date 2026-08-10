"""EV-SF: round-trip every lossless adapter (read(write(ir)) preserves values), the
registry is populated, the CSV-injection guard fires, and unknown formats are refused."""
from __future__ import annotations

from datetime import datetime, timezone

from databridge_core import SnapshotIdentity
from databridge_convert import RowBatch, TargetSpec, TypedRecord, route
from databridge_convert.adapters import arrow as arrow_a
from databridge_convert.adapters import csv as csv_a
from databridge_convert.adapters import json as json_a
from databridge_convert.adapters import jsonl as jsonl_a
from databridge_convert.adapters import md_tables as md_a
from databridge_convert.adapters import parquet as parquet_a
from databridge_convert.router import REGISTRY

SNAP = SnapshotIdentity(source_id="s", as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        content_hash="h", schema_hash="sh")


def test_registry_has_all_ten_formats():
    assert {"csv", "tsv", "json", "jsonl", "parquet", "arrow",
            "yaml", "xml", "html_tables", "md_tables"} <= set(REGISTRY.formats())


def test_csv_roundtrip():
    rb = RowBatch.from_records([{"a": "1", "b": "x"}, {"a": "2", "b": "y"}], SNAP)
    assert csv_a.read(csv_a.write(rb)).records() == [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}]


def test_parquet_roundtrip():
    rb = RowBatch.from_records([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}], SNAP)
    assert parquet_a.read(parquet_a.write(rb)).records() == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]


def test_arrow_roundtrip():
    rb = RowBatch.from_records([{"a": 1, "b": 2.5}, {"a": 2, "b": 3.5}], SNAP)
    assert arrow_a.read(arrow_a.write(rb)).records() == [{"a": 1, "b": 2.5}, {"a": 2, "b": 3.5}]


def test_json_roundtrip():
    tr = TypedRecord(root={"a": [1, 2], "b": "x"}, snapshot=SNAP)
    assert json_a.read(json_a.write(tr)).root == {"a": [1, 2], "b": "x"}


def test_jsonl_roundtrip_and_malformed_skipped():
    tr = TypedRecord(root=[{"a": 1}, {"a": 2}], snapshot=SNAP)
    assert jsonl_a.read(jsonl_a.write(tr)).root == [{"a": 1}, {"a": 2}]
    # a malformed line is skipped, not fatal
    assert jsonl_a.read(b'{"a":1}\nNOT JSON\n{"a":2}\n').root == [{"a": 1}, {"a": 2}]


def test_md_tables_roundtrip():
    rb = RowBatch.from_records([{"name": "a", "val": "1"}], SNAP)
    assert md_a.read(md_a.write(rb)).records() == [{"name": "a", "val": "1"}]


def test_csv_injection_guard():
    rb = RowBatch.from_records([{"x": "=1+2"}, {"x": "@cmd"}], SNAP)
    out = csv_a.write(rb).decode("utf-8")
    assert "'=1+2" in out and "'@cmd" in out      # apostrophe-prefixed (§21.8)


def test_csv_injection_guard_leading_space():
    out = csv_a.write(RowBatch.from_records([{"x": " =1+2"}], SNAP)).decode("utf-8")
    assert "' =1+2" in out                        # guarded despite the leading space


def test_md_tables_escapes_pipes():
    rb = RowBatch.from_records([{"a": "x|y", "b": "z"}], SNAP)
    assert md_a.read(md_a.write(rb)).records() == [{"a": "x|y", "b": "z"}]


def test_unknown_format_refused():
    decision = route("nonsense", TargetSpec(modality="structured", format="csv"))
    assert any(r.code == "UNKNOWN_FORMAT" for r in decision.refusals)

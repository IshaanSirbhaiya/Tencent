"""C08 part 1: SQL source/target adapters, read-only probe, and Demo D4 — against an
in-process SQLite DB (no daemon). Read FROM sql → DuckDB transform → write TO sql."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text

from databridge_core import SnapshotIdentity
from databridge_convert import Frame, TransformSpec
from databridge_convert.demos.d4 import run_d4
from databridge_convert.ir import RowBatch
from databridge_convert.rules.engine import execute_transform
from databridge_convert.sqlsub import probe_readonly, read_sql, write_sql

SNAP = SnapshotIdentity(source_id="s", as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        content_hash="h", schema_hash="sh")


def _engine_with_t():
    e = create_engine("sqlite://")          # in-memory, single-connection (SingletonThreadPool)
    with e.begin() as c:
        c.execute(text("CREATE TABLE t (a INTEGER, b TEXT)"))
        c.execute(text("INSERT INTO t (a, b) VALUES (1, 'x'), (2, 'y')"))
    return e


def test_read_sql_to_rowbatch_with_provenance():
    rb = read_sql(_engine_with_t(), table="t", enforce_readonly=False)
    assert rb.records() == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
    assert rb.snapshot.query_hash and rb.snapshot.content_hash      # SQL provenance present


def test_write_sql_roundtrip():
    e = create_engine("sqlite://")
    n = write_sql(e, RowBatch.from_records([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}], SNAP),
                  "out", create=True)
    assert n == 2
    assert read_sql(e, table="out", enforce_readonly=False).records() == \
        [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]


def test_readonly_probe_flags_writable_sqlite():
    # SQLite has no role separation → the source is writable → flagged (§15.1)
    res = probe_readonly(_engine_with_t(), warn=False)
    assert res.can_write is True and res.flagged is True


def test_write_sql_escapes_weird_identifiers():
    # review should-fix: a "-containing table/column name must NOT break out of quoting.
    e = create_engine("sqlite://")
    write_sql(e, RowBatch.from_records([{'weird"name': 1, "ok": 2}], SNAP), 'my"table',
              create=True)
    assert read_sql(e, table='my"table', enforce_readonly=False).records() == \
        [{'weird"name': 1, "ok": 2}]


def test_strict_readonly_refuses_writable_source():
    with pytest.raises(PermissionError):
        read_sql(_engine_with_t(), table="t", strict_readonly=True)   # sqlite is writable → refuse


def test_sql_snapshot_content_hash_is_order_stable():
    from databridge_convert.sqlsub.snapshot import sql_snapshot
    a = sql_snapshot("s", "q", [{"x": 1}, {"x": 2}])
    b = sql_snapshot("s", "q", [{"x": 2}, {"x": 1}])
    assert a.content_hash == b.content_hash               # row order doesn't change the hash


def test_read_sql_then_duckdb_transform():
    # the rule engine reads FROM sql (RowBatch) and runs a DuckDB transform (Prompt 5/6 wire-up)
    rb = read_sql(_engine_with_t(), table="t", enforce_readonly=False)
    out, _ = execute_transform(
        TransformSpec(language="sql", expression="SELECT a, UPPER(b) AS b FROM data ORDER BY a"),
        Frame(rows=rb.records()))
    assert out.rows == [{"a": 1, "b": "X"}, {"a": 2, "b": "Y"}]


def test_demo_d4_typed_records_and_quarantine():
    r = run_d4()
    assert r["written"] == 3                       # 3 good rows loaded to the SQL target
    assert r["quarantine"]["quarantined"] == 1     # the seeded bad row
    assert r["quarantine_rows"][0]["payload"]["amount"] == "not_a_number"
    # coercions applied: EU locale decimal parsed → stored as a NUMERIC (typed!), "N/A" → NULL
    by_id = {row["id"]: row for row in r["loaded"]}
    assert float(by_id["1"]["amount"]) == 1234.56
    assert by_id["2"]["status"] is None

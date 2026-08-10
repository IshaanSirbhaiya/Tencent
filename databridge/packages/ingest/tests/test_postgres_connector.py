"""T03 connector tested against an in-process SQLite seeded from the SAME fixture SQL.

This runs the real reflection/sampling/FK-heuristic/PII path with no Docker daemon. The
live-Postgres run (TABLESAMPLE, pg_catalog) is exercised by test_live_postgres.py when a
fixture DB is reachable.
"""
from __future__ import annotations

import pathlib
import sqlite3

import pytest

from databridge_ingest import extract_schema

SEED = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "postgres" / "seed.sql"


@pytest.fixture
def fintech_url(tmp_path) -> str:
    db = tmp_path / "fintech.db"
    con = sqlite3.connect(db)
    con.executescript(SEED.read_text(encoding="utf-8"))
    con.commit()
    con.close()
    return f"sqlite:///{db}"


def test_extract_five_tables(fintech_url):
    s = extract_schema(fintech_url)
    assert s.table_names == {"users", "merchants", "transactions", "disputes", "support_messages"}


def test_declared_fks_reflected(fintech_url):
    txn = extract_schema(fintech_url).table("transactions")
    declared = {(fk.column, fk.ref_table) for fk in txn.foreign_keys if fk.declared}
    assert {("user_id", "users"), ("merchant_id", "merchants")} <= declared


def test_heuristic_finds_the_one_undeclared_fk(fintech_url):
    # 02_TRD Task 3 eval: the heuristic catches the ONE deliberately-undeclared FK.
    sm = extract_schema(fintech_url).table("support_messages")
    heuristic = [fk for fk in sm.foreign_keys if not fk.declared]
    assert any(fk.column == "user_id" and fk.ref_table == "users" and fk.confidence < 1.0
               for fk in heuristic)


def test_pii_redacted_before_leaving_connector(fintech_url):
    email = next(c for c in extract_schema(fintech_url).table("users").columns
                 if c.name == "email")
    assert email.sample_values and all(str(v).startswith("<REDACTED") for v in email.sample_values)


def test_pii_minmax_not_leaked(fintech_url):
    # review blocker: MIN/MAX of a PII column are raw emails — they must NOT travel.
    cols = {c.name: c for c in extract_schema(fintech_url).table("users").columns}
    assert cols["email"].min_val is None and cols["email"].max_val is None
    assert cols["risk_score"].min_val is not None       # numeric column keeps its range


def test_primary_keys_detected(fintech_url):
    assert [c.name for c in extract_schema(fintech_url).table("users").columns
            if c.is_primary_key] == ["id"]

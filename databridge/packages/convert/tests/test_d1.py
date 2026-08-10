"""Demo D1 (09_FINAL_PRD §27): PostgreSQL → joined+aggregated Excel workbook.

Acceptance: workbook opens; 3 sheets; semantic number formats applied; range provenance in
the manifest. (In-process SQLite stands in for live PG — same SQL + writer.)"""
from __future__ import annotations

from databridge_convert.demos.d1 import run_d1
from databridge_convert.excelsub.writer import SEMANTICS_UNCONFIRMED


def test_d1_three_data_sheets_plus_toc():
    r = run_d1()
    # TOC + the 3 required sheets, all openable (re-read succeeds)
    assert r["sheet_names"] == ["TOC", "Transactions", "By Merchant", "Disputes"]
    assert r["n_data_sheets"] == 3


def test_d1_semantic_number_formats_applied():
    r = run_d1()
    assert r["semantic_formats_applied"] is True
    # the SAME build without confirmation refuses (§16.1)
    assert r["unconfirmed_refusal"] == SEMANTICS_UNCONFIRMED


def test_d1_range_provenance_in_manifest():
    r = run_d1()
    ranges = dict((name, ptr) for name, _rng, ptr in r["range_provenance"])
    assert ranges["By Merchant"] == "A1:D3"            # 2 merchant rows × 4 cols
    assert ranges["Transactions"] == "A1:G4"           # 3 txn rows × 7 cols


def test_d1_sql_as_provenance():
    r = run_d1()
    # every source sheet carries a query hash (SQL-as-provenance, §15.4/§17)
    assert all(r["sql_provenance"].values())


def test_d1_aggregation_correct():
    r = run_d1()
    by = {row["merchant"]: row for row in r["merchant_rows"]}
    assert by["TechBay"]["txn_count"] == 2
    assert by["TechBay"]["total_amount"] == 2128.95    # 129.95 + 1999.00
    assert by["TechBay"]["disputed_amount"] == 129.95
    assert by["Sunrise Coffee"]["disputed_amount"] == 4.50


def test_d1_merchant_query_no_dispute_fanout():
    # review D1-3a: a transaction with >1 dispute must NOT inflate txn_count/total_amount.
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool
    from databridge_convert.demos.d1 import Q_MERCHANT
    engine = create_engine("sqlite://", poolclass=StaticPool)
    raw = engine.raw_connection()
    raw.executescript(
        "CREATE TABLE merchants(id INT, name TEXT);"
        "CREATE TABLE transactions(id INT, merchant_id INT, amount NUMERIC);"
        "CREATE TABLE disputes(id INT, transaction_id INT, amount_disputed NUMERIC);"
        "INSERT INTO merchants VALUES (1,'M1');"
        "INSERT INTO transactions VALUES (10,1,100.0);"
        # ONE transaction, TWO disputes — the fan-out trap
        "INSERT INTO disputes VALUES (1,10,30.0),(2,10,20.0);")
    raw.commit()
    with engine.connect() as conn:
        row = dict(conn.execute(text(Q_MERCHANT)).mappings().one())
    engine.dispose()
    assert row["txn_count"] == 1                        # NOT 2 (no fan-out)
    assert row["total_amount"] == 100.0                # NOT 200.0
    assert row["disputed_amount"] == 50.0              # 30 + 20 summed correctly


def test_d1_workbook_reopens_with_values():
    r = run_d1()
    reopened = r["reopened"]
    txn = next(s for s in reopened.sheets if s.name == "Transactions")
    # header + 3 data rows
    assert txn.cells[0][:3] == ["txn_id", "customer", "merchant"]
    assert len([row for row in txn.cells if row[0] is not None]) == 4

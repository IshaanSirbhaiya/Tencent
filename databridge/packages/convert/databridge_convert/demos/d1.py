"""Demo D1 (09_FINAL_PRD §27): PostgreSQL → joined + aggregated Excel workbook.

Plan: join transactions+merchants+users and aggregate by merchant → execute via the SQL
subsystem → ``WorkbookRecordBuilder`` → **3 sheets** (Transactions / By Merchant / Disputes)
with **semantic number formats** (money→currency, timestamp→date) and **range-level
provenance** in the artifact manifest.

No live Postgres in this env (no Docker), so the fintech fixture is loaded into an
in-process SQLite DB (StaticPool → one shared in-memory connection). Against the
docker-compose Postgres the ONLY change is the connection URL — the SQL reads and the
writer are identical. (Live-PG verification deferred per the session plan.)

§16.1 gate: semantic number formats depend on inferred semantics, so the build REFUSES
``SEMANTICS_UNCONFIRMED`` unless the source schema is confirmed — D1 confirms it (the
fintech AnnotatedSchema is human-signed in this demo)."""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from databridge_core import SemanticType
from ..excelsub import WorkbookRecordBuilder, WriteOptions, read_workbook
from ..excelsub.policies import ReadOptions
from ..excelsub.writer import SEMANTICS_UNCONFIRMED
from ..ir import RowBatch
from ..sqlsub.sources import read_sql

_SEED = Path(__file__).parents[4] / "fixtures" / "postgres" / "seed.sql"

Q_TXN = ("SELECT t.id AS txn_id, u.full_name AS customer, m.name AS merchant, t.amount, "
         "t.currency, t.status, t.created_at "
         "FROM transactions t JOIN merchants m ON t.merchant_id = m.id "
         "JOIN users u ON t.user_id = u.id ORDER BY t.id")
# Disputes are pre-aggregated per transaction in a subquery BEFORE the join, so a transaction
# with >1 dispute cannot fan out and inflate txn_count / total_amount (review D1-3a).
Q_MERCHANT = ("SELECT m.name AS merchant, COUNT(t.id) AS txn_count, "
              "COALESCE(SUM(t.amount), 0) AS total_amount, "
              "COALESCE(SUM(dd.disputed), 0) AS disputed_amount "
              "FROM merchants m LEFT JOIN transactions t ON t.merchant_id = m.id "
              "LEFT JOIN (SELECT transaction_id, SUM(amount_disputed) AS disputed "
              "FROM disputes GROUP BY transaction_id) dd ON dd.transaction_id = t.id "
              "GROUP BY m.name ORDER BY total_amount DESC")
Q_DISPUTES = ("SELECT d.id AS dispute_id, m.name AS merchant, d.reason, d.status, "
              "d.amount_disputed, d.opened_at "
              "FROM disputes d JOIN transactions t ON d.transaction_id = t.id "
              "JOIN merchants m ON t.merchant_id = m.id ORDER BY d.id")

# semantic types for the OUTPUT columns (drive number formats; §14.15)
SEM_TXN = {"amount": SemanticType.AMOUNT_MONEY, "created_at": SemanticType.TIMESTAMP_EVENT}
SEM_MERCHANT = {"txn_count": SemanticType.AMOUNT_COUNT,
                "total_amount": SemanticType.AMOUNT_MONEY,
                "disputed_amount": SemanticType.AMOUNT_MONEY}
SEM_DISPUTES = {"amount_disputed": SemanticType.AMOUNT_MONEY,
                "opened_at": SemanticType.TIMESTAMP_EVENT}


def _seed_engine():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    raw = engine.raw_connection()
    try:
        raw.executescript(_SEED.read_text(encoding="utf-8"))
        raw.commit()
    finally:
        raw.close()
    return engine


def _coerce_ts(batch: RowBatch, cols: list[str]) -> RowBatch:
    """Parse ISO timestamp strings to datetime so the date number-format actually renders
    (SQLite returns TIMESTAMP columns as text). Provenance snapshot is preserved."""
    recs = batch.records()
    for rec in recs:
        for col in cols:
            v = rec.get(col)
            if isinstance(v, str):
                try:
                    rec[col] = _dt.datetime.fromisoformat(v)
                except ValueError:
                    pass
    return RowBatch.from_records(recs, batch.snapshot,
                                column_provenance=batch.column_provenance,
                                column_semantics=batch.column_semantics)


def run_d1(target_path: Optional[str] = None) -> dict:
    engine = _seed_engine()
    rb_txn = _coerce_ts(read_sql(engine, query=Q_TXN, source_id="d1_pg",
                                 enforce_readonly=False), ["created_at"])
    rb_mer = read_sql(engine, query=Q_MERCHANT, source_id="d1_pg", enforce_readonly=False)
    rb_dis = _coerce_ts(read_sql(engine, query=Q_DISPUTES, source_id="d1_pg",
                                 enforce_readonly=False), ["opened_at"])
    engine.dispose()

    builder = (WorkbookRecordBuilder(WriteOptions(toc=True))
               .add_sheet("Transactions", rb_txn, SEM_TXN)
               .add_sheet("By Merchant", rb_mer, SEM_MERCHANT)
               .add_sheet("Disputes", rb_dis, SEM_DISPUTES))

    # §16.1 demonstration: without confirmation the SAME build refuses.
    refusal_code = None
    try:
        WorkbookRecordBuilder(WriteOptions()).add_sheet("By Merchant", rb_mer,
                                                        SEM_MERCHANT).build(confirmed=False)
    except Exception as e:                              # ExcelRefusal
        refusal_code = getattr(e, "code", None)

    result = builder.build(confirmed=True, source_id="d1")
    if target_path:
        Path(target_path).write_bytes(result.data)

    # verify: re-open the workbook (the cheapest most-skipped check, §19)
    reopened = read_workbook(result.data, options=ReadOptions(sheets="all"))
    sheet_names = [s.name for s in reopened.sheets]

    return {
        "data": result.data,
        "n_data_sheets": 3,
        "sheet_names": sheet_names,                    # incl. TOC
        "semantic_formats_applied": result.semantic_formats_applied,
        "unconfirmed_refusal": refusal_code,           # == SEMANTICS_UNCONFIRMED
        "range_provenance": [(m.name, m.written_range, m.range_pointer.cell_range)
                             for m in result.sheets],
        "sql_provenance": {
            "Transactions": rb_txn.snapshot.query_hash,
            "By Merchant": rb_mer.snapshot.query_hash,
            "Disputes": rb_dis.snapshot.query_hash},
        "merchant_rows": rb_mer.records(),
        "reopened": reopened,
    }


SEMANTICS_UNCONFIRMED_CODE = SEMANTICS_UNCONFIRMED

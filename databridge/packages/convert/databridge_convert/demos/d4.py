"""Demo D4 (09_FINAL_PRD §27): messy CSV → typed PG-compatible records.

A CSV with EU locale decimals ("1.234,56") + sentinel nulls ("N/A") + a seeded bad row →
the rules engine (coercions) → a TYPED RowBatch → the SQL target adapter (writes to a
real DB) → a quarantine report for the seeded bad rows.

The target here is an in-process SQLite DB (no daemon needed) so the FULL pipeline runs.
Against the docker-compose Postgres the only change is the connection URL — the SQL target
adapter is the same. (Live-PG verification deferred per the session plan.)
"""
from __future__ import annotations

from typing import Optional, Union

from sqlalchemy import Engine, create_engine, text

from ..adapters import csv as csv_adapter
from ..frame import Frame
from ..ir import RowBatch
from ..rules.engine import execute_transform
from ..rules.spec import CoercionUse, TransformSpec
from ..sqlsub.snapshot import sql_snapshot
from ..sqlsub.targets import write_sql

# 3 good rows + 1 seeded-bad row (unparseable amount).
D4_CSV = (
    b"id,amount,status\n"
    b'1,"1.234,56",active\n'           # EU locale decimal
    b'2,"2.000,00",N/A\n'              # sentinel null status
    b'3,"89,00",active\n'
    b"4,not_a_number,active\n"         # ★ seeded bad row → quarantined (coercion fails)
)

D4_TRANSFORM = TransformSpec(
    language="jsonata", expression="$",
    coercions=[
        CoercionUse(name="str_to_decimal", params={"locale": "eu"}, columns=["amount"]),
        CoercionUse(name="sentinel_to_null", params={"sentinels": ["N/A"]}, columns=["status"]),
    ],
)


def run_d4(target: Optional[Union[str, Engine]] = None) -> dict:
    """Run the D4 pipeline end-to-end and return the outcome (incl. quarantine report)."""
    rb_in = csv_adapter.read(D4_CSV)
    frame = Frame(rows=rb_in.records(), name="d4")
    # inspect mode for the demo (the bad row is 25% → would fail the §10.5 gate on a real run)
    out, q = execute_transform(D4_TRANSFORM, frame, enforce_threshold=False)

    typed = RowBatch.from_records(out.rows, sql_snapshot("d4_csv", "csv-load", out.rows))
    engine = create_engine(target) if isinstance(target, str) else (target or create_engine("sqlite://"))
    written = write_sql(engine, typed, "loaded_transactions", create=True)

    with engine.connect() as conn:
        back = [dict(r) for r in conn.execute(text("SELECT * FROM loaded_transactions")).mappings().all()]

    return {
        "csv_rows": len(rb_in.records()),
        "written": written,
        "loaded": back,
        "quarantine": q.report(),
        "quarantine_rows": [{"reason": r.reason_code, "payload": r.payload} for r in q.records],
    }

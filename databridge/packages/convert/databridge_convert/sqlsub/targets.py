"""SQL target adapter (09_FINAL_PRD §15.2): RowBatch → INSERT batches / CREATE TABLE AS.

Writes go against a CUSTOMER-DESIGNATED target schema with a SEPARATE target credential —
NEVER DDL/DML against a source connection. Connection-based (invoked via ``write_sql``).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Union

from sqlalchemy import Engine, create_engine, text

from ..ir import RowBatch


def _engine(url_or_engine: Union[str, Engine]) -> tuple[Engine, bool]:
    if isinstance(url_or_engine, Engine):
        return url_or_engine, False
    return create_engine(url_or_engine), True


def _sql_type(value: object) -> str:
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, (float, Decimal)):
        return "NUMERIC"
    return "TEXT"


def _infer_columns(rows: list[dict]) -> dict[str, str]:
    cols: dict[str, str] = {}
    for r in rows:
        for k, v in r.items():
            if k not in cols and v is not None:
                cols[k] = _sql_type(v)
    for r in rows:                          # ensure all-null columns still appear
        for k in r:
            cols.setdefault(k, "TEXT")
    return cols


def write_sql(
    url_or_engine: Union[str, Engine],
    rowbatch: RowBatch,
    table: str,
    *,
    create: bool = False,
    batch_size: int = 1000,
) -> int:
    """Write a RowBatch to ``table`` (CREATE TABLE if ``create``); returns rows inserted."""
    rows = rowbatch.records()
    engine, owned = _engine(url_or_engine)
    q = engine.dialect.identifier_preparer.quote      # proper identifier escaping (" -> "")
    try:
        with engine.begin() as conn:
            if create:
                coldefs = ", ".join(f"{q(c)} {t}" for c, t in _infer_columns(rows).items())
                conn.execute(text(f"CREATE TABLE {q(table)} ({coldefs})"))
            if not rows:
                return 0
            cols = rowbatch.columns
            # bind-param NAMES are safe synthetic keys (p0, p1, …), decoupled from the
            # column identifiers (which may contain quotes/spaces) — the identifiers are
            # quoted via the dialect preparer, the values are bound parameters.
            pnames = [f"p{i}" for i in range(len(cols))]
            placeholders = ", ".join(f":{p}" for p in pnames)
            collist = ", ".join(q(c) for c in cols)
            stmt = text(f"INSERT INTO {q(table)} ({collist}) VALUES ({placeholders})")
            for i in range(0, len(rows), batch_size):
                chunk = [{pnames[j]: _coerce(r.get(cols[j])) for j in range(len(cols))}
                         for r in rows[i:i + batch_size]]
                conn.execute(stmt, chunk)
        return len(rows)
    finally:
        if owned:
            engine.dispose()


def _coerce(v: object) -> object:
    # SQLAlchemy/DBAPI don't bind Decimal uniformly across SQLite/PG → str() Decimals
    return str(v) if isinstance(v, Decimal) else v

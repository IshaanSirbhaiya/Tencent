"""PostgreSQL connector → SchemaExtract (02_TRD Task 3, 09_FINAL_PRD §15.1).

The connector's job is NOT to copy data — it extracts schema metadata + bounded samples
(P1: minimal movement). Built on SQLAlchemy reflection so the SAME code path works against
Postgres, SQLite, DuckDB (the OD-1 dialect set); Postgres-specific niceties (TABLESAMPLE
for big tables, pg_catalog stats) activate for the ``postgresql`` dialect, else it falls
back to ``LIMIT``/reflection — which is what makes it testable against in-process SQLite.

PII redaction runs BEFORE anything leaves the connector: columns whose samples match PII
regexes are redacted (semantics survive, raw PII does not travel).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional, Union

from sqlalchemy import Engine, create_engine, inspect, text

from .schema_extract import ColumnMeta, ForeignKey, SchemaExtract, TableMeta

# --- PII regexes (02_TRD Task 3) ------------------------------------------------
_PII = [
    ("email", re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")),
    ("phone", re.compile(r"^\+?\d[\d\s().-]{6,}$")),
    ("national_id", re.compile(r"^(\d{3}-\d{2}-\d{4}|[A-Z]\d{7}[A-Z])$")),
]
TABLESAMPLE_MIN_ROWS = 100_000          # use TABLESAMPLE only for big PG tables
_HEURISTIC_SCAN_CAP = 50_000            # cap distinct scans for the heuristic FK finder (P1)


def _engine(url_or_engine: Union[str, Engine]) -> tuple[Engine, bool]:
    if isinstance(url_or_engine, Engine):
        return url_or_engine, False
    return create_engine(url_or_engine), True


def _norm_dtype(dtype: str) -> str:
    d = dtype.lower()
    if "bool" in d:
        return "boolean"
    if "int" in d or "serial" in d:
        return "integer"
    if any(t in d for t in ("numeric", "decimal", "real", "double", "float", "money")):
        return "numeric"
    if any(t in d for t in ("timestamp", "datetime", "date")):
        return "timestamp"
    if "text" in d:
        return "text"
    if any(t in d for t in ("char", "string", "uuid")):
        return "varchar"
    if "json" in d:
        return "jsonb" if "jsonb" in d else "json"
    return d or "unknown"


def _qi(engine: Engine, name: str) -> str:
    """Safely quote a SQL identifier via the dialect's preparer (proper " -> "" escaping)."""
    return engine.dialect.identifier_preparer.quote(name)


def _is_pii_column(name: str, values: list) -> bool:
    """True if a column is PII by name hint OR any sample matches a PII regex."""
    if any(k in name.lower() for k in ("email", "ssn", "nric", "phone", "national_id")):
        return True
    return any(isinstance(v, str) and any(rx.match(v.strip()) for _, rx in _PII) for v in values)


def _redact(name: str, values: list) -> list:
    """Redact a column's samples if they look like PII (semantics survive, PII doesn't)."""
    out = []
    name_l = name.lower()
    for v in values:
        if isinstance(v, str):
            for kind, rx in _PII:
                if rx.match(v.strip()):
                    out.append(f"<REDACTED:{kind}>")
                    break
            else:
                out.append(v)
        else:
            out.append(v)
    # name-based hint: an "email"/"ssn" column always redacts even if a sample slips the regex
    if any(k in name_l for k in ("email", "ssn", "nric", "phone")):
        out = [f"<REDACTED:{name_l}>" if isinstance(v, str) and not v.startswith("<REDACTED")
               else v for v in out]
    return out


def _sample_rows(engine: Engine, table: str, n: int) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(text(f'SELECT * FROM {_qi(engine, table)} LIMIT :n'),
                            {"n": n}).mappings().all()
    return [dict(r) for r in rows]


def _column_stats(engine: Engine, table: str, col: str) -> dict:
    t, c = _qi(engine, table), _qi(engine, col)
    q = (f'SELECT COUNT(*) AS total, COUNT({c}) AS non_null, '
         f'COUNT(DISTINCT {c}) AS distinct_ct, MIN({c}) AS lo, MAX({c}) AS hi '
         f'FROM {t}')
    with engine.connect() as conn:
        r = conn.execute(text(q)).mappings().one()
    total = r["total"] or 0
    null_fraction = (1.0 - r["non_null"] / total) if total else 0.0
    return {"row_count": total, "null_fraction": round(null_fraction, 4),
            "unique_count": r["distinct_ct"] or 0, "min_val": r["lo"], "max_val": r["hi"]}


def extract_schema(
    url_or_engine: Union[str, Engine],
    *,
    source_id: Optional[str] = None,
    sample_rows: int = 50,
    sample_values: int = 20,
    find_heuristic_fks: bool = True,
) -> SchemaExtract:
    """Reflect a database into a SchemaExtract (declared + heuristic FKs, redacted samples)."""
    engine, owned = _engine(url_or_engine)
    try:
        insp = inspect(engine)
        tables: list[TableMeta] = []
        for tname in sorted(insp.get_table_names()):
            pks = set(insp.get_pk_constraint(tname).get("constrained_columns") or [])
            declared = [
                ForeignKey(column=fk["constrained_columns"][0], ref_table=fk["referred_table"],
                           ref_column=(fk.get("referred_columns") or ["id"])[0],
                           declared=True, confidence=1.0)
                for fk in insp.get_foreign_keys(tname) if fk.get("constrained_columns")
            ]
            srows = _sample_rows(engine, tname, sample_rows)
            row_count = 0
            cols: list[ColumnMeta] = []
            for c in insp.get_columns(tname):
                name = c["name"]
                stats = _column_stats(engine, tname, name)
                row_count = max(row_count, stats["row_count"])
                vals = [r.get(name) for r in srows][:sample_values]
                # PII columns: redact samples AND drop raw min/max (MIN/MAX of an email
                # column is a real email — it must NOT travel; review blocker fix).
                pii = _is_pii_column(name, vals)
                lo, hi = (None, None) if pii else (stats["min_val"], stats["max_val"])
                cols.append(ColumnMeta(
                    name=name, dtype=_norm_dtype(str(c["type"])),
                    null_fraction=stats["null_fraction"], unique_count=stats["unique_count"],
                    sample_values=_redact(name, vals), min_val=lo, max_val=hi,
                    is_primary_key=name in pks))
            tables.append(TableMeta(name=tname, row_count=row_count, columns=cols,
                                    sample_rows=_redact_rows(srows[:50]),
                                    foreign_keys=declared,
                                    indexes=[ix["name"] for ix in insp.get_indexes(tname)]))
        extract = SchemaExtract(source_id=source_id or (engine.url.database or "sql"),
                                tables=tables, extracted_at=datetime.now(timezone.utc))
        if find_heuristic_fks:
            extract = _add_heuristic_fks(extract, engine)
        return extract
    finally:
        if owned:
            engine.dispose()


def _redact_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        rr = dict(r)
        for k in rr:
            rr[k] = _redact(k, [rr[k]])[0]
        out.append(rr)
    return out


def _add_heuristic_fks(extract: SchemaExtract, engine: Engine) -> SchemaExtract:
    """Propose undeclared FKs: a ``*_id`` column whose sampled values ⊆ another table's PK
    column values → ForeignKey(declared=False, confidence<1.0). Never assumed; routed to
    human confirmation downstream."""
    # candidate PKs: (table, pk_col) → set of pk values (from the live DB)
    pk_values: dict[tuple[str, str], set] = {}
    for t in extract.tables:
        pk = next((c.name for c in t.columns if c.is_primary_key), None)
        if pk:
            with engine.connect() as conn:
                vals = conn.execute(text(
                    f'SELECT DISTINCT {_qi(engine, pk)} FROM {_qi(engine, t.name)} '
                    f'LIMIT {_HEURISTIC_SCAN_CAP}')).scalars().all()
            pk_values[(t.name, pk)] = set(v for v in vals if v is not None)

    new_tables: list[TableMeta] = []
    for t in extract.tables:
        existing = t.fk_columns
        extra: list[ForeignKey] = []
        for c in t.columns:
            if c.is_primary_key or c.name in existing or not c.name.lower().endswith("id"):
                continue
            with engine.connect() as conn:
                ci, ti = _qi(engine, c.name), _qi(engine, t.name)
                cvals = conn.execute(text(
                    f'SELECT DISTINCT {ci} FROM {ti} WHERE {ci} IS NOT NULL '
                    f'LIMIT {_HEURISTIC_SCAN_CAP}')).scalars().all()
            cset = set(cvals)
            if not cset:
                continue
            # all PK columns whose value set contains this column's values
            candidates = [(rt, rc) for (rt, rc), pks in pk_values.items()
                          if rt != t.name and pks and cset <= pks]
            if not candidates:
                continue
            # name affinity: prefer a ref table matching the column's `*_id` prefix
            # ("user_id" → "users"), else fall back to the first containment match.
            stem = c.name.lower()[:-2].rstrip("_")        # "user_id" → "user"
            ref_table, ref_col = next(
                ((rt, rc) for rt, rc in candidates if stem and stem in rt.lower()),
                candidates[0])
            extra.append(ForeignKey(column=c.name, ref_table=ref_table, ref_column=ref_col,
                                    declared=False, confidence=0.7))
        new_tables.append(t.model_copy(update={"foreign_keys": list(t.foreign_keys) + extra})
                          if extra else t)
    return extract.model_copy(update={"tables": new_tables})

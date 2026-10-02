"""Two real fixture estates as live SQL engines, for tests that must hold on more than one estate
(finance dry-run fix wave, 2026-10-02: every new test uses BOTH).

* `workspace` — Larkspur Analytics (`fixtures/workspace_demo/db/01_schema_and_data.sql`), one schema;
* `finance`   — the synthetic lender (`fixtures/packs/finance/{core_banking,crm}`), two schemas.

The Postgres DDL is loaded into sqlite with the few dialect differences rewritten mechanically
(schemas become ATTACHed databases, `SERIAL` an integer key, cross-schema `REFERENCES` lose their
qualifier, `COMMENT ON` / `CREATE INDEX` / roles are dropped). Nothing about the data is changed.
Tables are then read through the REAL `crawl.fetch_table` behind a fake MCP SQL tool, so every
snapshot a test uses is what ingestion would capture.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from test_w4_dialects import EngineClient

DATABRIDGE = Path(__file__).resolve().parents[3]
FIXTURES = DATABRIDGE / "fixtures"

ESTATES = {
    "workspace": {"pack": "workspace", "schemas": {"public": [
        FIXTURES / "workspace_demo" / "db" / "01_schema_and_data.sql"]}},
    "finance": {"pack": "finance", "schemas": {
        name: [FIXTURES / "packs" / "finance" / name / "schema.sql",
               *sorted((FIXTURES / "packs" / "finance" / name / "seed").glob("*.sql"))]
        for name in ("core_banking", "crm")}},
}


def _sqlite_sql(text: str) -> str:
    text = re.sub(r"(?is)\bCOMMENT\s+ON\b[^']*'(?:[^']|'')*'\s*;", "", text)
    text = re.sub(r"(?is)\bCREATE\s+(UNIQUE\s+)?INDEX\b.*?;", "", text)
    text = re.sub(r"(?is)\b(DROP|CREATE)\s+SCHEMA\b.*?;", "", text)
    text = re.sub(r"(?is)\b(CREATE\s+ROLE|GRANT|REVOKE|ALTER\s+DEFAULT)\b.*?;", "", text)
    text = re.sub(r"(?i)\bREFERENCES\s+\w+\.", "REFERENCES ", text)
    text = re.sub(r"(?i)\bSERIAL\b", "INTEGER", text)
    return "\n".join(line for line in text.splitlines() if not line.startswith("\\"))


def build_estate(name: str, path: Path) -> sqlite3.Connection:
    """The estate's tables in a sqlite file (one ATTACHed database per schema)."""
    spec = ESTATES[name]
    db = sqlite3.connect(str(path), check_same_thread=False)
    db.row_factory = sqlite3.Row
    for schema, files in spec["schemas"].items():
        db.execute(f"ATTACH DATABASE ? AS {schema}", (str(path.with_name(f"{schema}.sqlite")),))
        for f in files:
            sql = _sqlite_sql(f.read_text(encoding="utf-8"))
            if schema == "public":
                sql = re.sub(r"(?i)\bCREATE\s+TABLE\s+(\w+)\s*\(", r"CREATE TABLE public.\1 (",
                             sql)
                sql = re.sub(r"(?i)\bINSERT\s+INTO\s+(\w+)\s", r"INSERT INTO public.\1 ", sql)
            db.executescript(sql)
    db.commit()
    return db


def tables_of(db: sqlite3.Connection, name: str) -> list[tuple[str, str]]:
    out = []
    for schema in ESTATES[name]["schemas"]:
        for r in db.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table' "
                            f"ORDER BY name"):
            out.append((schema, r[0]))
    return out


def engine_client(db: sqlite3.Connection) -> EngineClient:
    def run(sql):
        return [dict(r) for r in db.execute(sql).fetchall()]
    return EngineClient(run)


def sql_step():
    from databridge_mcp_ingest.plans import SqlSpec, StepSpec

    return StepSpec(id="sql", strategy="tabular",
                    sql=SqlSpec(tool="query", arg="sql", dialect="sqlite",
                                parse={"mode": "json_rows"}))


def snapshot(client, schema: str, table: str, *, limit=None, page_rows: int = 1000) -> dict:
    """One table exactly as ingestion captures it (`crawl.fetch_table`)."""
    from databridge_mcp_ingest.crawl import Item, fetch_table

    item = Item(source_id="estate", step_id="sql", kind="table", uri=f"{schema}.{table}",
                name=table, rel=f"{schema}.{table}", mime="application/json",
                ctx={"schema": schema, "table": table})
    return fetch_table(client, sql_step(), item, limit=limit, page_rows=page_rows)[1]


def snapshots(db: sqlite3.Connection, name: str, *, page_rows: int = 1000) -> dict[str, dict]:
    client = engine_client(db)
    return {f"{s}.{t}": snapshot(client, s, t, page_rows=page_rows)
            for s, t in tables_of(db, name)}

"""W4 — live: drafting the dialect for the demo Postgres through the real `postgres-mcp`, as the
least-privilege reader (walnut_reader), plus the measured profiling speedup.

Marked `integration`; skipped when the demo DB (localhost:5433) or `uvx` is unavailable. A
scratch schema with primary and foreign keys is created by the OWNER role, SELECT is granted to
walnut_reader, and the schema is DROPPED afterwards. Every expected number is DERIVED from the
live catalogue (pg_catalog read by the owner) — never written down.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import time
import uuid

import pytest

pytestmark = pytest.mark.integration

PORT = os.environ.get("WALNUT_DEMO_PG_PORT", "5433")
READER = (f"postgresql://walnut_reader:"
          f"{os.environ.get('WALNUT_DEMO_PG_READER_PASSWORD', 'walnut_reader_synthetic')}"
          f"@127.0.0.1:{PORT}/larkspur")
OWNER = (f"postgresql://{os.environ.get('WALNUT_DEMO_PG_USER', 'walnut')}:"
         f"{os.environ.get('WALNUT_DEMO_PG_PASSWORD', 'walnut_synthetic')}@127.0.0.1:{PORT}/larkspur")
#: What a REGISTERED connection holds: the password by env-var reference, never the value
#: (EF3, Codex round 3 c: register_connection refuses a literal secret).
# R-022 round 2 (Codex f2 #3): a defaulted credential reference is refused; the synthetic
# demo password is supplied through the environment instead.
os.environ.setdefault("WALNUT_DEMO_PG_READER_PASSWORD", "walnut_reader_synthetic")
READER_REF = ("postgresql://walnut_reader:${WALNUT_DEMO_PG_READER_PASSWORD}"
              f"@127.0.0.1:{PORT}/larkspur")
SOURCE = "w4_live_db"


def _pg(dsn: str):
    psycopg = pytest.importorskip("psycopg")
    try:
        return psycopg.connect(dsn, connect_timeout=3, autocommit=True)
    except Exception as exc:                                        # noqa: BLE001
        pytest.skip(f"demo Postgres not reachable ({type(exc).__name__})")


def _conn_decl() -> dict:
    return {"id": SOURCE, "transport": {"type": "stdio", "command": "uvx", "args": [
        "--with", "mcp<2", "postgres-mcp", "--access-mode=restricted"],
        "env": {"DATABASE_URI": READER_REF}}}


@pytest.fixture(scope="module")
def scratch():
    """A scratch schema: three tables, 3 PKs, 3 FKs (one composite-free chain + a second FK)."""
    if shutil.which("uvx") is None:
        pytest.skip("uvx not installed")
    name = f"w4_scratch_{uuid.uuid4().hex[:8]}"
    with _pg(OWNER) as c:
        c.execute(f'CREATE SCHEMA "{name}"')
        try:
            c.execute(f'CREATE TABLE "{name}".accounts (id int PRIMARY KEY, name text NOT NULL, '
                      f'opened date)')
            c.execute(f'CREATE TABLE "{name}".orders (id int PRIMARY KEY, account_id int '
                      f'REFERENCES "{name}".accounts(id), placed date, total numeric(10,2))')
            c.execute(f'CREATE TABLE "{name}".order_lines (order_id int REFERENCES '
                      f'"{name}".orders(id), line_no int, account_id int REFERENCES '
                      f'"{name}".accounts(id), sku text, PRIMARY KEY (order_id, line_no))')
            c.execute(f'INSERT INTO "{name}".accounts SELECT g, \'Account \' || g, '
                      f'DATE \'2026-01-01\' + g FROM generate_series(1, 9) g')
            c.execute(f'INSERT INTO "{name}".orders SELECT g, 1 + g % 9, DATE \'2026-02-01\' + g, '
                      f'g * 10.5 FROM generate_series(1, 30) g')
            c.execute(f'INSERT INTO "{name}".order_lines SELECT 1 + g % 30, g, 1 + g % 9, '
                      f'\'SKU-\' || g FROM generate_series(1, 60) g')
            c.execute(f'GRANT USAGE ON SCHEMA "{name}" TO walnut_reader')
            c.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{name}" TO walnut_reader')
            yield name
        finally:
            c.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
    with _pg(OWNER) as c:
        assert not c.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s",
                             (name,)).fetchall(), "scratch schema was not dropped"


@pytest.fixture(scope="module")
def live_client(scratch):
    from databridge_mcp_ingest import open_client
    from databridge_mcp_ingest.connections import SourceConnection

    with open_client(SourceConnection.model_validate(_conn_decl())) as client:
        yield client


def _truth(schema: str) -> dict:
    """Keys per table as pg_catalog reports them to the OWNER, and what the READER sees in
    information_schema — both derived live."""
    with _pg(OWNER) as c:
        rows = c.execute("""
            SELECT t.relname, c.contype, count(*) FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
            CROSS JOIN LATERAL unnest(c.conkey) k
            WHERE n.nspname = %s AND c.contype IN ('p', 'f') GROUP BY 1, 2""",
                         (schema,)).fetchall()
        cols = c.execute("SELECT count(*) FROM information_schema.columns WHERE "
                         "table_schema = %s", (schema,)).fetchone()[0]
        tables = c.execute("SELECT count(*) FROM information_schema.tables WHERE "
                           "table_schema = %s AND table_type = 'BASE TABLE'",
                           (schema,)).fetchone()[0]
    with _pg(READER) as c:
        hidden = c.execute("SELECT count(*) FROM information_schema.table_constraints WHERE "
                           "table_schema = %s AND constraint_type IN "
                           "('PRIMARY KEY', 'FOREIGN KEY')", (schema,)).fetchone()[0]
    pk = {t: n for t, k, n in rows if k == "p"}
    fk = sum(n for _, k, n in rows if k == "f")
    return {"tables": tables, "columns": cols, "pk_tables": len(pk), "pk_cols": sum(pk.values()),
            "fks": fk, "reader_sees_in_information_schema": hidden}


def test_live_draft_detects_postgres_and_finds_the_scratch_keys(walnut_env, scratch,
                                                                live_client):
    from databridge_mcp_ingest import register_connection
    from databridge_mcp_ingest.tabular.dialect_draft import approve_dialect, draft_dialect

    register_connection(_conn_decl())
    truth = _truth(scratch)
    assert truth["fks"] > 0 and truth["pk_cols"] > truth["pk_tables"] > 0      # non-vacuous
    t0 = time.monotonic()
    prop = draft_dialect(SOURCE, client=live_client, schemas=[scratch])
    took = round(time.monotonic() - t0, 2)
    print(f"\nW4 live draft ({took}s, {prop.probe['statements']} statements): "
          f"{prop.probe['summary']}")
    assert prop.engine == "postgres" and prop.detection["method"] == "probe"
    assert prop.origin == "rules" and prop.problems == [], prop.problems
    rep = prop.probe
    assert (rep["tables"], rep["columns"], rep["key_relationships"],
            rep["tables_with_primary_key"]) == (truth["tables"], truth["columns"], truth["fks"],
                                                truth["pk_tables"])
    assert rep["keys_catalogue"] == "keys"                          # pg_catalog, as shipped
    assert rep["keys_by_catalogue"]["keys"]["primary_key_columns"] == truth["pk_cols"]
    # The Postgres lesson, measured: the SELECT-only reader sees no key in information_schema,
    # and the probe says that is why the template does not use it.
    assert truth["reader_sees_in_information_schema"] == 0
    alt = rep["keys_by_catalogue"]["keys_alternatives[0]"]
    assert alt["primary_key_columns"] + alt["foreign_keys"] == 0
    assert any("hides keys from this role" in w for w in prop.warnings)
    assert "table_stats" in rep["queries"] and rep["queries"]["table_stats"]["ok"]
    assert approve_dialect(SOURCE, principal=__import__("conftest").test_principal("W4 integration")).status == "approved"


def test_live_a_template_that_reaches_for_information_schema_first_is_repaired(
        walnut_env, scratch, live_client):
    """The first S2 live run built 0 edges because keys came from information_schema. A template
    written that way is now caught by the probe and repaired from its alternative."""
    from databridge_mcp_ingest import register_connection
    from databridge_mcp_ingest.tabular.dialect_draft import draft_dialect, load_templates

    register_connection(_conn_decl())
    truth = _truth(scratch)
    pg = copy.deepcopy(next(t for t in load_templates() if t["engine"] == "postgres"))
    naive = {**pg, "keys": pg["keys_alternatives"][0]["sql"],
             "keys_alternatives": [{"name": "pg_catalog", "note": "readable by any role",
                                    "sql": pg["keys"]}]}
    prop = draft_dialect(SOURCE, client=live_client, schemas=[scratch], templates=[naive])
    rep = prop.probe
    assert rep["keys_catalogue"] == "alternative 'pg_catalog'"
    assert rep["keys_by_catalogue"]["keys"]["foreign_keys"] == 0
    assert rep["key_relationships"] == truth["fks"] > 0
    assert any(w.startswith("KEYS HIDDEN") for w in prop.warnings)
    assert prop.template["keys"] == pg["keys"]


def test_live_combined_profiling_is_faster_and_identical(scratch, live_client):
    """Before/after on the demo DB's public tables, one session, same role."""
    from databridge_mcp_ingest.client import ReadOnlyClient
    from databridge_mcp_ingest.plans import SqlSpec, StepSpec, sql_dialect
    from databridge_mcp_ingest.tabular.policy import load_policy
    from databridge_mcp_ingest.tabular.profiling import SqlRunner, profile_source

    step = StepSpec(id="t", strategy="tabular", sql=SqlSpec(tool="execute_sql",
                                                            dialect="postgres"))
    dialect = sql_dialect("postgres")
    policy = load_policy(dialect=dialect)
    ro = ReadOnlyClient(live_client)
    tables = [(r["table_schema"], r["table_name"]) for r in
              SqlRunner(ro, step, dialect).rows("list_tables", {}) if r["table_schema"] == "public"]
    assert tables
    results = {}
    for mode in ("per_column", "combined", "per_column_again", "combined_again"):
        r = SqlRunner(ro, step, dict(dialect))
        r.combined = mode.startswith("combined")
        t0 = time.monotonic()
        prof = profile_source(r, tables, policy)
        results[mode] = {"profiles": prof, "calls": r.calls, "seconds": time.monotonic() - t0,
                         "sql_seconds": r.seconds, "notes": r.notes}
    per, comb = results["per_column"], results["combined"]
    assert json.dumps(comb["profiles"], default=str, sort_keys=True) == \
        json.dumps(per["profiles"], default=str, sort_keys=True)
    assert comb["notes"] == []
    assert comb["calls"] == 3 * len(tables) < per["calls"]
    best = {m: min(results[m]["seconds"], results[m + "_again"]["seconds"])
            for m in ("per_column", "combined")}
    print(f"\nW4 live profiling, {len(tables)} tables: per-column {per['calls']} statements "
          f"{best['per_column']:.2f}s -> combined {comb['calls']} statements "
          f"{best['combined']:.2f}s (best of 2 each)")
    assert best["combined"] < best["per_column"]

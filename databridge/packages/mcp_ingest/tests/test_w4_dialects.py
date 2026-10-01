"""W4 — drafting a SQL dialect for a new database engine, offline.

Real engines (sqlite3, duckdb) stand behind a fake MCP SQL tool that behaves like postgres-mcp:
rows as JSON (or Python repr), a failed statement as `Error: …` text with isError false. Every
expected count is DERIVED from the engine's own catalogue (PRAGMA / duckdb_constraints) — never
written down.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import time
from pathlib import Path

import pytest

from databridge_mcp_ingest.client import ContentPart, ToolInfo, ToolResult


# --- a real engine behind a fake MCP SQL tool -----------------------------------------------------
class EngineClient:
    """One SQL tool over a live engine connection. `fmt`: json | repr."""

    supports_resources = False

    def __init__(self, execute, *, tool: str = "query", arg: str = "sql", fmt: str = "json",
                 extra_tools: tuple = ()) -> None:
        self.execute, self.tool, self.arg, self.fmt = execute, tool, arg, fmt
        self.extra_tools = extra_tools
        self.sql: list[str] = []

    def list_tools(self):
        return [ToolInfo(name=self.tool, description="Run a read-only SQL query",
                         input_schema={"type": "object",
                                       "properties": {self.arg: {"type": "string"}}}),
                *[ToolInfo(name=n) for n in self.extra_tools]]

    def list_resources(self):
        return []

    def call_tool(self, name, args):
        assert name == self.tool
        sql = args[self.arg]
        self.sql.append(sql)
        try:
            rows = self.execute(sql)
        except Exception as exc:                                     # noqa: BLE001
            text = f"Error: {exc}"
        else:
            text = json.dumps(rows, default=str) if self.fmt == "json" else repr(rows)
        return ToolResult(parts=(ContentPart(kind="text", text=text),))

    def close(self):
        pass


def sqlite_exec(path: Path):
    db = sqlite3.connect(str(path), check_same_thread=False)
    db.row_factory = sqlite3.Row

    def run(sql):
        return [dict(r) for r in db.execute(sql).fetchall()]
    return run, db


def duckdb_exec(path: Path):
    duckdb = pytest.importorskip("duckdb")
    db = duckdb.connect(str(path))

    def run(sql):
        cur = db.execute(sql)
        cols = [d[0] for d in cur.description or []]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    return run, db


SCHEMA_SQL = [
    "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT, "
    "joined DATE)",
    "CREATE TABLE projects (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id), "
    "title TEXT, budget NUMERIC, started DATE)",
    "CREATE TABLE invoices (id INTEGER PRIMARY KEY, project_id INTEGER REFERENCES projects(id), "
    "customer_id INTEGER REFERENCES customers(id), amount NUMERIC, issued DATE, note TEXT)",
    "CREATE TABLE tags (label TEXT)",
]


def _fill(execute_many) -> None:
    for s in SCHEMA_SQL:
        execute_many(s, [])
    execute_many("INSERT INTO customers VALUES (?,?,?,?)",
                 [(i, f"Customer {i}", None if i % 4 == 0 else f"c{i}@example.test",
                   f"2026-0{1 + i % 9}-1{i % 9}") for i in range(1, 13)])
    execute_many("INSERT INTO projects VALUES (?,?,?,?,?)",
                 [(i, 1 + i % 12, f"Project {i} with a fairly long descriptive title", i * 1000.5,
                   f"2026-0{1 + i % 9}-0{1 + i % 8}") for i in range(1, 21)])
    execute_many("INSERT INTO invoices VALUES (?,?,?,?,?,?)",
                 [(i, 1 + i % 20, 1 + i % 12, i * 12.25, f"2026-0{1 + i % 9}-2{i % 8}",
                   None if i % 3 else "paid late after two reminders were sent to the client")
                  for i in range(1, 41)])
    execute_many("INSERT INTO tags VALUES (?)", [("alpha",), ("beta",), (None,)])


@pytest.fixture
def sqlite_db(tmp_path):
    path = tmp_path / "engine.sqlite"
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys = ON")

    def many(sql, rows):
        db.executemany(sql, rows) if rows else db.execute(sql)
    _fill(many)
    db.commit()
    db.close()
    return path


@pytest.fixture
def duckdb_db(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    path = tmp_path / "engine.duckdb"
    db = duckdb.connect(str(path))

    def many(sql, rows):
        db.executemany(sql, rows) if rows else db.execute(sql)
    _fill(many)
    db.close()
    return path


def sqlite_truth(path: Path) -> dict:
    """What the engine's own catalogue says — the expected numbers, derived."""
    db = sqlite3.connect(path)
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND "
                                       "name NOT LIKE 'sqlite_%'")]
    cols = sum(len(db.execute(f"PRAGMA table_info('{t}')").fetchall()) for t in tables)
    fks = sum(len(db.execute(f"PRAGMA foreign_key_list('{t}')").fetchall()) for t in tables)
    with_pk = sum(any(r[5] for r in db.execute(f"PRAGMA table_info('{t}')")) for t in tables)
    db.close()
    return {"tables": len(tables), "columns": cols, "fks": fks, "with_pk": with_pk}


def duckdb_truth(path: Path) -> dict:
    import duckdb

    db = duckdb.connect(str(path), read_only=True)
    tables = db.execute("SELECT count(*) FROM duckdb_tables() WHERE NOT internal").fetchone()[0]
    cols = db.execute("SELECT count(*) FROM duckdb_columns() WHERE NOT internal").fetchone()[0]
    fks = db.execute("SELECT coalesce(sum(len(constraint_column_names)), 0) FROM "
                     "duckdb_constraints() WHERE constraint_type='FOREIGN KEY'").fetchone()[0]
    with_pk = db.execute("SELECT count(DISTINCT table_name) FROM duckdb_constraints() "
                         "WHERE constraint_type='PRIMARY KEY'").fetchone()[0]
    db.close()
    return {"tables": tables, "columns": cols, "fks": fks, "with_pk": with_pk}


def _register(source_id="newdb", **extra):
    from databridge_mcp_ingest import register_connection

    return register_connection({"id": source_id, "transport": {
        "type": "stdio", "command": "never-started", "args": []}, **extra})


def _dev():
    """The approving person, AUTHENTICATED (R-022 f1 #1): a typed name no longer approves."""
    from conftest import test_principal

    return test_principal("Dev")


def _template(engine: str) -> dict:
    from databridge_mcp_ingest.tabular.dialect_draft import load_templates

    t = next(t for t in load_templates() if t["engine"] == engine)
    return {k: v for k, v in t.items() if not k.startswith("_")}


# --- templates ------------------------------------------------------------------------------------
def test_every_shipped_template_validates_and_is_honest_about_its_status():
    from databridge_mcp_ingest import paths
    from databridge_mcp_ingest.tabular.dialect_draft import load_templates, validate_template

    templates = load_templates()
    on_disk = {p.name.removesuffix(".sql.yaml")
               for p in (paths.bundled_profiles_dir() / "sql_dialects").glob("*.sql.yaml")}
    assert on_disk, "no dialect templates shipped"
    engines = {t["engine"] for t in templates}
    assert on_disk <= engines                     # every file there is a template
    assert len(engines) == len(templates)         # one template per engine
    for t in templates:
        assert validate_template(t) == [], (t["engine"], validate_template(t))
        # `verified` is claimed only with evidence; a draft says what is unverified.
        assert (t["status"] == "draft") == bool(t.get("unverified")), t["engine"]
    assert any(t["status"] == "verified" for t in templates)


def test_validation_refuses_writes_bad_placeholders_and_unsupported_claims():
    from databridge_mcp_ingest.tabular.dialect_draft import validate_template

    base = _template("sqlite")
    cases = {
        "names a write keyword": {"count": "SELECT 1; DELETE FROM x"},
        "not a read": {"describe": "PRAGMA table_info(x)"},
        "unknown placeholder": {"keys": "SELECT {secret} AS column_name"},
        "missing catalogue query": {"column_range": ""},
        "needs a `verified` evidence line": {"status": "verified", "verified": ""},
        "must say what is unverified": {"status": "draft", "unverified": ""},
        "quote must be": {"quote": "ab"},
    }
    for needle, over in cases.items():
        problems = validate_template({**base, **over})
        assert any(needle in p for p in problems), (needle, problems)


def test_identifier_quoting_follows_the_dialect():
    from databridge_mcp_ingest.tabular.profiling import render_sql

    tpl = "SELECT * FROM {schema|ident}.{table|ident} WHERE x = {table|literal}"
    ctx = {"schema": "s", "table": 'we`ird]"t'}
    assert render_sql(tpl, ctx, {"quote": "`"}) == \
        "SELECT * FROM `s`.`we``ird]\"t` WHERE x = 'we`ird]\"t'"
    assert render_sql(tpl, ctx, {"quote": "[]"}) == \
        "SELECT * FROM [s].[we`ird]]\"t] WHERE x = 'we`ird]\"t'"
    assert render_sql(tpl, ctx, {}) == "SELECT * FROM \"s\".\"we`ird]\"\"t\" WHERE x = 'we`ird]\"t'"


def test_error_text_and_csv_rows_follow_the_dialect():
    from databridge_mcp_ingest.client import McpClientError
    from databridge_mcp_ingest.plans import SqlSpec, StepSpec
    from databridge_mcp_ingest.tabular.profiling import SqlRunner, parse_rows

    class Say:
        def __init__(self, text):
            self.text = text

        def call_tool(self, name, args):
            return ToolResult(parts=(ContentPart(kind="text", text=self.text),))

    step = StepSpec(id="t", strategy="tabular", sql=SqlSpec(tool="q", dialect="postgres"))
    with pytest.raises(McpClientError):
        SqlRunner(Say("Error: relation does not exist"), step).run("SELECT 1")
    assert parse_rows("a,b\n1,x\n2,y", {"mcp": {"rows": "csv"}}) == \
        [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}]


# --- detection ------------------------------------------------------------------------------------
@pytest.mark.parametrize("engine", ["sqlite", "duckdb"])
def test_detects_a_live_engine_by_its_version_probe(engine, sqlite_db, tmp_path):
    from databridge_mcp_ingest.tabular.dialect_draft import SqlTool, detect_engine, load_templates

    run = sqlite_exec(sqlite_db)[0] if engine == "sqlite" else duckdb_exec(
        tmp_path / "d.duckdb")[0]
    client = EngineClient(run)
    det = detect_engine(SqlTool(client, "query", "sql"), {"query"}, load_templates())
    assert det["engine"] == engine and det["method"] == "probe", det
    assert det["candidates"] == [engine]          # no other template's probe matched


#: What each engine's version query returns (the public format, synthetic values).
SELF_DESCRIPTIONS = {
    "postgres": "PostgreSQL 16.13 (Debian 16.13-1.pgdg13+1) on x86_64-pc-linux-gnu, 64-bit",
    "mysql": "MySQL Community Server - GPL",
    "sqlserver": "Microsoft SQL Server 2022 (RTM) - 16.0.1000.6 (X64)",
    "clickhouse": "24.3.2.23",
    "snowflake": "8.39.2",
    "bigquery": "walnut-demo-project",
}


@pytest.mark.parametrize("engine", sorted(SELF_DESCRIPTIONS))
def test_detection_picks_the_engine_whose_probe_matches(engine):
    """Several templates share `SELECT version()`; only the matching regex decides."""
    from databridge_mcp_ingest.tabular.dialect_draft import SqlTool, detect_engine, load_templates

    templates = load_templates()
    mine = next(t for t in templates if t["engine"] == engine)
    probe_sql = {p["sql"] for p in mine["detect"]["probes"]}

    def run(sql):
        if sql in probe_sql:
            return [{"v": SELF_DESCRIPTIONS[engine]}]
        raise RuntimeError("function does not exist")
    det = detect_engine(SqlTool(EngineClient(run), "query", "sql"), {"query"}, templates)
    assert det["engine"] == engine and det["method"] == "probe", det


def test_tool_names_alone_are_a_weak_signal_and_nothing_is_guessed():
    from databridge_mcp_ingest.tabular.dialect_draft import SqlTool, detect_engine, load_templates

    templates = load_templates()

    def refuse(sql):
        raise RuntimeError("syntax error")
    hinted = next(t for t in templates if t["engine"] == "clickhouse")
    tools = {"query", *hinted["detect"]["tool_hints"]}
    det = detect_engine(SqlTool(EngineClient(refuse), "query", "sql"), tools, templates)
    assert det["method"] == "tool_names" and det["engine"] == "clickhouse"
    assert "WEAK" in det["evidence"][0]
    det = detect_engine(SqlTool(EngineClient(refuse), "query", "sql"), {"query_thing"},
                        templates)
    assert det["engine"] is None and det["method"] == "none"


# --- draft → probe → approve ----------------------------------------------------------------------
def test_draft_probe_and_approve_on_a_live_sqlite(walnut_env, sqlite_db):
    from databridge_mcp_ingest.tabular.dialect_draft import (DialectError, approve_dialect,
                                                             approved_dialect, draft_dialect,
                                                             load_proposal)

    _register()
    truth = sqlite_truth(sqlite_db)
    client = EngineClient(sqlite_exec(sqlite_db)[0], tool="read_query", arg="query", fmt="repr")
    prop = draft_dialect("newdb", client=client)
    assert prop.status == "proposed" and prop.origin == "rules" and prop.engine == "sqlite"
    assert prop.sql_tool == "read_query" and prop.sql_arg == "query"
    rep = prop.probe
    assert (rep["tables"], rep["columns"], rep["key_relationships"],
            rep["tables_with_primary_key"]) == (truth["tables"], truth["columns"], truth["fks"],
                                                truth["with_pk"])
    assert truth["fks"] > 0 and truth["columns"] > truth["tables"] > 0    # non-vacuous
    assert rep["summary"].startswith(f"found {truth['tables']} tables, {truth['columns']} "
                                     f"columns, {truth['fks']} key relationships")
    assert rep["failed"] == [] and prop.problems == []
    assert rep["rows_format"] == ["repr"]
    ran = {k for k, v in rep["queries"].items() if v.get("ok")}
    assert {"list_tables", "describe", "keys", "count", "row_estimate", "column_range",
            "column_stats", "select_page", "table_stats"} <= ran
    # every statement the probe sent was a read, or the template's read-only session switch
    # (R-022 f2 #3: approval needs the database to enforce read-only, verified)
    ro_sql = prop.template["read_only_session"]["sql"]
    assert all(s.lstrip().upper().startswith(("SELECT", "WITH")) or s == ro_sql
               for s in client.sql)
    assert prop.probe["read_only"]["enforced_by"] == "database session"
    # proposed until a named human approves; unapproved drafts are never used
    assert load_proposal("newdb").status == "proposed" and approved_dialect("newdb") is None
    with pytest.raises(DialectError):
        approve_dialect("newdb", approved_by="  ")
    done = approve_dialect("newdb", principal=_dev())
    assert done.status == "approved" and done.approved_by == "Dev"
    assert done.approval["authenticated_by"] and done.approval["template_sha256"]
    assert approved_dialect("newdb")["keys"] == prop.template["keys"]


def test_duckdb_template_probes_clean_on_the_real_engine(walnut_env, duckdb_db):
    from databridge_mcp_ingest.tabular.dialect_draft import draft_dialect

    _register()
    truth = duckdb_truth(duckdb_db)
    prop = draft_dialect("newdb", client=EngineClient(duckdb_exec(duckdb_db)[0]))
    assert prop.engine == "duckdb" and prop.problems == [], (prop.problems, prop.probe["failed"])
    rep = prop.probe
    assert (rep["tables"], rep["columns"], rep["key_relationships"],
            rep["tables_with_primary_key"]) == (truth["tables"], truth["columns"], truth["fks"],
                                                truth["with_pk"])
    assert truth["fks"] > 0


def test_a_catalogue_that_hides_keys_is_detected_and_the_alternative_used(walnut_env, sqlite_db):
    """The PostgreSQL lesson, offline: the first keys catalogue answers — with nothing."""
    from databridge_mcp_ingest.tabular.dialect_draft import draft_dialect

    _register()
    truth = sqlite_truth(sqlite_db)
    real = _template("sqlite")
    hiding = copy.deepcopy(real)
    hiding["keys"] = real["keys"].replace("WHERE pk > 0", "WHERE pk > 0 AND 1 = 0") \
        .replace("pragma_foreign_key_list({table|literal}, {schema|literal})",
                 "pragma_foreign_key_list({table|literal}, {schema|literal}) WHERE 1 = 0")
    hiding["keys_alternatives"] = [{"name": "real_catalogue", "note": "sees the keys",
                                    "sql": real["keys"]}]
    prop = draft_dialect("newdb", client=EngineClient(sqlite_exec(sqlite_db)[0]),
                         templates=[hiding])
    rep = prop.probe
    assert rep["keys_catalogue"] == "alternative 'real_catalogue'"
    assert rep["keys_by_catalogue"]["keys"]["foreign_keys"] == 0
    assert rep["key_relationships"] == truth["fks"] > 0
    assert any(n.startswith("KEYS HIDDEN") for n in prop.warnings)
    assert prop.template["keys"] == real["keys"]                 # swapped in
    assert prop.template["keys_alternatives"][0]["sql"] == hiding["keys"]   # kept, demoted


def test_failed_catalogue_queries_are_reported_and_block_approval(walnut_env, sqlite_db):
    from databridge_mcp_ingest.tabular.dialect_draft import (DialectError, approve_dialect,
                                                             draft_dialect)

    _register()
    broken = _template("sqlite")
    # R-022 f1 #4: an unknown FUNCTION is now refused before any probe, so the live failure here
    # is a missing COLUMN (a statement that passes every static check and still fails)
    broken["column_stats"] = broken["column_stats"].replace("COUNT(*)", "COUNT(no_such_column)")
    broken["table_stats"] = ("SELECT COUNT(no_such_column) AS n{measures} "
                             "FROM {schema|ident}.{table|ident}")
    prop = draft_dialect("newdb", client=EngineClient(sqlite_exec(sqlite_db)[0]),
                         templates=[broken])
    assert "column_stats" in prop.probe["failed"] and "table_stats" in prop.probe["failed"]
    assert "FAILED: column_stats, table_stats" in prop.probe["summary"]
    assert any("column_stats failed" in p for p in prop.problems)
    assert "table_stats" not in prop.template                    # fell back to per column
    with pytest.raises(DialectError):
        approve_dialect("newdb", principal=_dev())


def test_no_template_matches_so_the_model_drafts_one(walnut_env, sqlite_db):
    """P16: rules first — here none match (the engine's own template is withheld), so the model
    (FakeBackend) drafts from the engine's self-description, and the live probe still decides."""
    from databridge_harness import FakeBackend, Harness

    from databridge_mcp_ingest.tabular.dialect_draft import (DRAFT_TASK_CLASS, DialectError,
                                                             draft_dialect, load_templates)

    _register()
    truth = sqlite_truth(sqlite_db)
    others = [t for t in load_templates() if t["engine"] != "sqlite"]
    seen: list = []
    ref = _template("sqlite")

    def handler(*, model, messages, guided_json) -> str:
        seen.append(messages)
        return json.dumps({
            "engine": "sqlite", "title": "SQLite (drafted)",
            "version_probe_sql": "SELECT sqlite_version() AS v", "version_match": r"^3\.",
            "quote": '"', **{k: ref[k] for k in ("list_tables", "describe", "keys", "count",
                                                   "row_estimate", "select", "select_page",
                                                   "column_stats", "column_range",
                                                   "aggregate")},
            "notes": "catalogue from sqlite_master and pragma table functions"})

    client = EngineClient(sqlite_exec(sqlite_db)[0])
    with pytest.raises(DialectError):
        draft_dialect("newdb", client=client, templates=others)          # no model, no guess
    harness = Harness(FakeBackend(handler), profile="cloud")
    prop = draft_dialect("newdb", client=client, templates=others, harness=harness)
    assert len(seen) == 1
    sent = json.loads(seen[0][-1]["content"])
    # the self-description the model saw: every probe, including the engine's own refusals
    assert sent["engine_self_description"]["version_probes"]
    assert any(not p["ok"] for p in sent["engine_self_description"]["version_probes"])
    assert prop.origin == "model" and prop.status == "proposed"
    assert prop.template["status"] == "draft" and prop.template["unverified"]
    assert prop.problems == [], prop.problems
    assert (prop.probe["tables"], prop.probe["columns"], prop.probe["key_relationships"]) == \
        (truth["tables"], truth["columns"], truth["fks"])
    assert any("model-drafted" in w for w in prop.warnings)
    assert harness is not None and DRAFT_TASK_CLASS == "schema_annotation"


def test_a_model_draft_that_does_not_run_is_not_approvable(walnut_env, sqlite_db):
    from databridge_harness import FakeBackend, Harness

    from databridge_mcp_ingest.tabular.dialect_draft import draft_dialect, load_templates

    _register()
    others = [t for t in load_templates() if t["engine"] != "sqlite"]
    bad = {k: "SELECT table_schema, table_name FROM information_schema.tables" for k in (
        "list_tables", "describe", "keys", "count", "row_estimate", "select", "select_page",
        "column_stats", "column_range", "aggregate")}
    harness = Harness(FakeBackend(json.dumps({
        "engine": "mystery", "version_probe_sql": "SELECT version() AS v", "version_match": "x",
        **bad})), profile="cloud")
    prop = draft_dialect("newdb", client=EngineClient(sqlite_exec(sqlite_db)[0]),
                         templates=others, harness=harness)
    assert prop.problems and "list_tables" in prop.probe["failed"]
    assert any("did not match its own regex" in p for p in prop.problems)


def test_the_tabular_stage_uses_the_approved_draft(walnut_env, sqlite_db):
    from databridge_mcp_ingest.plans import CrawlPlan, SqlSpec, StepSpec, save_plan
    from databridge_mcp_ingest.tabular.dialect_draft import approve_dialect, draft_dialect
    from databridge_mcp_ingest.tabular.pipeline import TabularStage

    _register()
    save_plan(CrawlPlan(source_id="newdb", status="approved", origin="manual", steps=[
        StepSpec(id="t", strategy="tabular",
                 sql=SqlSpec(tool="query", dialect="information_schema"))]))
    draft_dialect("newdb", client=EngineClient(sqlite_exec(sqlite_db)[0]))
    assert TabularStage("newdb").dialect.get("engine") is None      # draft not approved yet
    approve_dialect("newdb", principal=_dev())
    stage = TabularStage("newdb")
    assert stage.dialect["engine"] == "sqlite" and "approved draft" in stage.dialect_origin
    from databridge_mcp_ingest.plans import load_plan
    assert load_plan("newdb").steps[0].sql.dialect == "sqlite"     # the crawl resolves it too


# --- combined profiling ---------------------------------------------------------------------------
def _runner(execute, dialect: dict):
    from databridge_mcp_ingest.plans import SqlSpec, StepSpec
    from databridge_mcp_ingest.tabular.profiling import SqlRunner

    step = StepSpec(id="t", strategy="tabular", sql=SqlSpec(tool="query", dialect="postgres"))
    return SqlRunner(EngineClient(execute), step, dict(dialect))


def _profile_both(execute, engine: str):
    from databridge_mcp_ingest.tabular.policy import load_policy
    from databridge_mcp_ingest.tabular.profiling import profile_source

    dialect = _template(engine)
    policy = load_policy(dialect=dialect)
    tables = [(r["table_schema"], r["table_name"]) for r in execute(dialect["list_tables"])]
    out = {}
    for mode in ("per_column", "combined"):
        r = _runner(execute, dialect)
        r.combined = mode == "combined"
        t0 = time.perf_counter()
        profiles = profile_source(r, tables, policy)
        out[mode] = {"profiles": profiles, "calls": r.calls, "notes": r.notes,
                     "seconds": time.perf_counter() - t0}
    return tables, out


def _expected_per_column_calls(profiles: list[dict]) -> int:
    """describe + keys + count per table, one column_stats per column, one column_range per
    non-empty temporal/numeric column — the per-column path's own rule."""
    return sum(3 + len(p["columns"]) + sum(1 for c in p["columns"] if c["class"] in
                                           ("temporal", "numeric") and c["non_null"])
               for p in profiles)


@pytest.mark.parametrize("engine", ["sqlite", "duckdb"])
def test_combined_profiling_agrees_with_per_column_in_fewer_statements(engine, sqlite_db,
                                                                       duckdb_db):
    execute = sqlite_exec(sqlite_db)[0] if engine == "sqlite" else duckdb_exec(duckdb_db)[0]
    tables, out = _profile_both(execute, engine)
    per, comb = out["per_column"], out["combined"]
    assert comb["profiles"] == per["profiles"]                    # identical measurements
    assert comb["notes"] == []                                    # no fallback happened
    assert per["calls"] == _expected_per_column_calls(per["profiles"])
    assert comb["calls"] == 3 * len(tables)                       # describe, keys, table_stats
    assert comb["calls"] < per["calls"]
    print(f"\nW4 {engine} profiling: per-column {per['calls']} statements "
          f"{per['seconds']*1000:.1f} ms -> combined {comb['calls']} statements "
          f"{comb['seconds']*1000:.1f} ms")


def test_wide_tables_are_chunked_and_a_failing_combined_statement_falls_back_once(sqlite_db):
    from databridge_mcp_ingest.tabular.policy import _merge, load_policy
    from databridge_mcp_ingest.tabular.profiling import profile_source

    execute = sqlite_exec(sqlite_db)[0]
    dialect = _template("sqlite")
    tables = [(r["table_schema"], r["table_name"]) for r in execute(dialect["list_tables"])]
    base = load_policy(dialect=dialect)
    ref = _runner(execute, dialect)
    ref.combined = False
    want = profile_source(ref, tables, base)
    narrow = _merge(base, {"profiling": {"max_columns_per_statement": 2}})
    chunked = _runner(execute, dialect)
    assert profile_source(chunked, tables, narrow) == want
    widest = max(len(p["columns"]) for p in want)
    assert widest > 2 and chunked.calls > 3 * len(tables)          # really chunked
    broken = {**dialect, "table_stats": "SELECT nope() AS n{measures} FROM {table|ident}"}
    fb = _runner(execute, broken)
    assert profile_source(fb, tables, base) == want
    assert len(fb.notes) == 1 and "per-column statements used" in fb.notes[0]
    assert fb.calls == ref.calls + 1                               # one wasted statement, once


def test_a_model_draft_reaches_disk_before_the_probe(walnut_env, sqlite_db, monkeypatch):
    """P16: a crash in the live probe does not throw the model's output away — and the saved
    draft cannot be approved until it has been probed."""
    from databridge_harness import FakeBackend, Harness

    from databridge_mcp_ingest.tabular import dialect_draft as dd

    _register()
    ref = _template("sqlite")
    others = [t for t in dd.load_templates() if t["engine"] != "sqlite"]
    harness = Harness(FakeBackend(json.dumps({
        "engine": "sqlite", "version_probe_sql": "SELECT sqlite_version() AS v",
        "version_match": "^3", **{k: ref[k] for k in dd.REQUIRED}})), profile="cloud")

    def crash(*_a, **_k):
        raise ConnectionError("session dropped")
    monkeypatch.setattr(dd, "probe_template", crash)
    with pytest.raises(ConnectionError):
        dd.draft_dialect("newdb", client=EngineClient(sqlite_exec(sqlite_db)[0]),
                         templates=others, harness=harness)
    kept = dd.load_proposal("newdb")
    assert kept is not None and kept.origin == "model" and kept.template["list_tables"]
    with pytest.raises(dd.DialectError):
        dd.approve_dialect("newdb", principal=_dev())

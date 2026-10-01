"""AA — LIVE: an adapter agent adapts to real sources changing shape.

* a temp COPY of `fixtures/workspace_demo/files` served by Walnut's `files_blob` MCP server;
* a SCRATCH schema in the demo Postgres (:5433) served by `postgres-mcp` (the larkspur_db
  declaration's exact command), read as `walnut_reader`. The schema is created by the demo owner,
  granted to the reader, and DROPPED afterwards; the real demo tables are never touched.

Between syncs: a column is added (auto-applied, values land in the graph), a column is renamed
(candidate, held, asked), and a new file type is dropped into the folder (it widens the declared
scope, so it is asked — E2E F-24 — and captured the sync after a person approves it). Marked `integration`; skipped when the demo DB or `uvx` is unavailable. Every expected
value is read back from the live database or the folder.
"""
from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest

from aa_drift_kit import drift_env

pytestmark = pytest.mark.integration

PORT = os.environ.get("WALNUT_DEMO_PG_PORT", "5433")
READER = (f"postgresql://walnut_reader:"
          f"{os.environ.get('WALNUT_DEMO_PG_READER_PASSWORD', 'walnut_reader_synthetic')}"
          f"@127.0.0.1:{PORT}/larkspur")
OWNER_DSN = (f"postgresql://{os.environ.get('WALNUT_DEMO_PG_USER', 'walnut')}:"
             f"{os.environ.get('WALNUT_DEMO_PG_PASSWORD', 'walnut_synthetic')}"
             f"@127.0.0.1:{PORT}/larkspur")
#: What the REGISTERED connection holds: the password by env-var reference, expanded only at
#: connect time — the way real declarations do (register_connection refuses a literal secret).
# R-022 round 2 (Codex f2 #3): a defaulted credential reference is refused; the synthetic
# demo password is supplied through the environment instead.
os.environ.setdefault("WALNUT_DEMO_PG_READER_PASSWORD", "walnut_reader_synthetic")
READER_REF = ("postgresql://walnut_reader:${WALNUT_DEMO_PG_READER_PASSWORD}"
              f"@127.0.0.1:{PORT}/larkspur")
DATABRIDGE = Path(__file__).resolve().parents[3]
FILES_SERVER = DATABRIDGE / "packages" / "mcp_ingest" / "servers" / "files_blob" / "server.py"
CORPUS = DATABRIDGE / "fixtures" / "workspace_demo" / "files"
DB, FILES = "aa_live_db", "aa_live_files"


def _pg(dsn):
    psycopg = pytest.importorskip("psycopg")
    try:
        return psycopg.connect(dsn, connect_timeout=3, autocommit=True)
    except Exception as exc:                                        # noqa: BLE001
        pytest.skip(f"demo Postgres not reachable ({type(exc).__name__})")


@pytest.fixture
def scratch():
    if shutil.which("uvx") is None:
        pytest.skip("uvx not installed")
    schema = f"aa_drift_{uuid.uuid4().hex[:8]}"
    owner = _pg(OWNER_DSN)

    def run(sql: str) -> None:
        owner.execute(sql)
        owner.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO walnut_reader")

    try:
        owner.execute(f"CREATE SCHEMA {schema}")
        owner.execute(f"GRANT USAGE ON SCHEMA {schema} TO walnut_reader")
        run(f"""CREATE TABLE {schema}.organisations (id INTEGER PRIMARY KEY, name TEXT,
                                                   domain TEXT);
                CREATE TABLE {schema}.projects (id INTEGER PRIMARY KEY, name TEXT,
                                              code TEXT,
                                              organisation_id INTEGER
                                              REFERENCES {schema}.organisations(id));
                INSERT INTO {schema}.organisations VALUES
                    (1, 'Aldermoor Logistics', 'aldermoor.example'),
                    (2, 'Quillon Freight', 'quillon.example');
                INSERT INTO {schema}.projects VALUES (10, 'Route planner', 'RP-10', 1),
                                                     (11, 'Depot summaries', 'DS-11', 2);""")
        yield schema, run, owner
    finally:
        owner.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        owner.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    yield from drift_env(tmp_path, monkeypatch)


def test_live_sources_change_shape_and_the_agent_adapts(env, scratch, tmp_path):
    import databridge_mcp_ingest as mi

    schema, run_sql, owner = scratch
    corpus = tmp_path / "files"
    shutil.copytree(CORPUS, corpus)
    mi.register_connection({
        "id": DB, "transport": {"type": "stdio", "command": "uvx",
                                "args": ["--with", "mcp<2", "postgres-mcp",
                                         "--access-mode=restricted"],
                                "env": {"DATABASE_URI": READER_REF}},
        "scope": {"tables_include": [f"{schema}.*"]}, "auto_approve": True})
    mi.register_connection({
        "id": FILES, "transport": {"type": "stdio", "command": sys.executable,
                                   "args": [str(FILES_SERVER), "--root", str(corpus)]},
        "scope": {"file_types": [".md", ".txt"]}, "auto_approve": True})
    for c in (DB, FILES):
        plan = mi.propose_plan(c)
        assert not plan.problems, (c, plan.problems)
        # a connection flag never approves a plan: a named, authenticated person does
        from aa_drift_kit import OWNER

        # `execute_sql` is no longer blessed by default (FXI3): the approving person vouches
        # for it as a read, by name; the read-only SQL allow-list still applies at run time.
        vouch = ["execute_sql"] if c == DB else None
        assert mi.approve_plan(c, approved_by=OWNER.principal_id, principal=OWNER,
                               vouch_read_tools=vouch).status == "approved"
    env.approve_first_mapping(DB)                  # a person approves the proposed mapping
    spec = env.manager.create({
        "spec": "adapter_agent/v1", "id": "larkspur_live", "title": "Larkspur live",
        "created_from": "Keep the Larkspur files and database in the graph; adapt to changes",
        "connections": [DB, FILES], "plans": {DB: "approved", FILES: "approved"},
        "sync": {"type": "manual"}, "extract": {"pack": "workspace"}, "status": "active"},
        by=OWNER)              # the activating PRINCIPAL seals the auto_additive delegation (AA3)
    first = env.sync(spec.id)
    assert first.status == "completed", first.reason
    orgs, projects = f"{schema}.organisations", f"{schema}.projects"
    assert env.mapping(DB)["tables"][orgs]["status"] == "approved"

    # --- the sources change -------------------------------------------------------------------
    run_sql(f"ALTER TABLE {schema}.organisations ADD COLUMN region TEXT;"
            f"UPDATE {schema}.organisations SET region = 'north-' || id;")
    run_sql(f"ALTER TABLE {schema}.projects RENAME COLUMN code TO project_code;")
    (corpus / "ledger.csv").write_text("depot,volume\nNorth,12\n", encoding="utf-8")

    second = env.sync(spec.id)
    by = {c.connection: c for c in second.connections}
    assert by[DB].status == "completed" and by[FILES].status == "completed"
    # added column: applied as a new version, values in the graph with table_cell pointers
    assert env.mapping(DB)["tables"][orgs]["node"]["attrs"].get("region") == "region"
    live = dict(owner.execute(f"SELECT name, region FROM {schema}.organisations").fetchall())
    got = {}
    for n in env.nodes("organisation"):
        ptrs = env.store.attr_sources_for(n["id"])
        if "region" in ptrs and ptrs["region"].table == orgs:
            assert ptrs["region"].kind == "table_cell" and ptrs["region"].column == "region"
            got[n["label"]] = env.attrs(n)["region"]
    assert got == live
    # renamed column: a candidate, held, asked — not applied
    pend = {p["unit"]: p for p in env.manager.pending_adaptations(spec.id)}
    rename = pend[f"table:{projects}"]
    assert rename["drifts"][0]["kind"] == "renamed" and rename["drifts"][0]["candidate"]
    assert (rename["drifts"][0]["old"], rename["drifts"][0]["new"]) == ("code", "project_code")
    # held by whichever mechanism the runner offers: the holds file when it honours drift holds,
    # else the table's mapping status (derived from the hold the engine recorded)
    if rename["hold"]["via"] == "holds_file":
        from databridge_mcp_ingest.records import load_holds

        held = load_holds(DB, env.service.sources_root)
        assert held.tables.get(projects) == rename["adaptation_id"]
    else:
        assert env.mapping(DB)["tables"][projects]["status"] == "proposed"
    assert by[DB].exclusions_by_rule["excluded"].get("DRIFT_PENDING_APPROVAL") == 1
    # new file type: it WIDENS the declared scope, so it is asked (F-24), never added on its own
    from databridge_mcp_ingest.connections import load_connection

    from aa_drift_kit import OWNER

    assert ".csv" not in load_connection(FILES).scope.file_types
    [widen] = [p for p in env.manager.pending_adaptations(spec.id)
               if p["connection"] == FILES and p["proposal"]["artifact"] == "connection"]
    env.manager.approve_adaptation(spec.id, widen["adaptation_id"], by=OWNER)
    assert ".csv" in load_connection(FILES).scope.file_types
    third = env.sync(spec.id)
    captured = env.jsonl(env.run_dir(third, FILES) / "captured.jsonl")
    assert [c for c in captured if c["uri"].replace("\\", "/").endswith("/ledger.csv")]

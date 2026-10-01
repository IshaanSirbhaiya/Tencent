"""S2 — live: the real `postgres-mcp` against the synthetic demo Postgres (infra/docker-compose).

Marked `integration`; skipped when the demo DB (localhost:5433) or `uvx` is unavailable. Every
expected number is DERIVED by querying the live database directly with psycopg as walnut_reader —
never written down. The write test creates a scratch schema + role and drops both afterwards; the
demo schema is never altered.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

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
SOURCE = "larkspur_db"
SERVER = ["--with", "mcp<2", "postgres-mcp"]


def _pg(dsn: str):
    psycopg = pytest.importorskip("psycopg")
    try:
        return psycopg.connect(dsn, connect_timeout=3, autocommit=True)
    except Exception as exc:                                        # noqa: BLE001
        pytest.skip(f"demo Postgres not reachable ({type(exc).__name__})")


@pytest.fixture(scope="module")
def live_ok():
    if shutil.which("uvx") is None:
        pytest.skip("uvx not installed")
    with _pg(READER) as c:
        c.execute("SELECT 1")


def _conn(conn_id: str, dsn: str, mode: str, **extra) -> dict:
    return {"id": conn_id, "transport": {"type": "stdio", "command": "uvx",
                                         "args": [*SERVER, f"--access-mode={mode}"],
                                         "env": {"DATABASE_URI": dsn}}, **extra}


@pytest.fixture
def live_ingest(live_ok, walnut_env, tmp_path, monkeypatch):
    from databridge_graph.packs import deployment
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest import open_client, propose_plan, register_connection
    from databridge_mcp_ingest.tabular import ingest_database

    monkeypatch.setenv("WALNUT_FACTS_DIR", str(tmp_path / "facts"))
    monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(tmp_path / "passages"))
    conn = register_connection(_conn(SOURCE, READER_REF, "restricted",
                                     scope={"tables_include": ["public.*"]}, auto_approve=True))
    timings = {}
    t0 = time.monotonic()
    with deployment("workspace") as vocab, open_client(conn) as client:
        timings["mcp_session_start"] = round(time.monotonic() - t0, 3)
        t1 = time.monotonic()
        plan = propose_plan(SOURCE, client=client)
        timings["plan"] = round(time.monotonic() - t1, 3)
        store = GraphStore(str(tmp_path / "graph.db"))
        out = ingest_database(SOURCE, store, None, client=client, vocab=vocab)
    timings.update(out["timings"])
    print("\nS2 live timings (s):", json.dumps(timings))
    return {"plan": plan, "store": store, "out": out, "vocab": vocab, "timings": timings}


def _live(sql: str, params=()):
    with _pg(READER) as c:
        return c.execute(sql, params).fetchall()


def test_live_ingest_builds_nodes_edges_facts_and_cards(live_ingest):
    from databridge_mcp_ingest import capture
    from databridge_mcp_ingest.mapping import load_mapping

    plan, store, out = live_ingest["plan"], live_ingest["store"], live_ingest["out"]
    assert plan.profiles == ["postgres-mcp"] and plan.status == "approved"
    assert out["run"]["status"] == "ok", out["run"]["message"]
    tables = [r[0] for r in _live("SELECT table_name FROM information_schema.tables WHERE "
                                  "table_schema='public' AND table_type='BASE TABLE'")]
    mapping = load_mapping(SOURCE)
    # customers / contacts / projects / invoices (and employees) are nodes, one per live row
    for t in ("customers", "contacts", "projects", "invoices"):
        tm = mapping.tables[f"public.{t}"]
        assert tm.status == "approved", (t, tm.rationale)
        n = _live(f'SELECT COUNT(*) FROM public."{t}"')[0][0]
        # node id = the slugged row key; canonical_id = the mapped identifier column when its
        # value arrived unmasked, else the row key (P: exact-merge with document mentions)
        from databridge_graph.estate_seed import slug
        got = [x for x in store.open_nodes() if x["kind"] == tm.node["kind"]
               and (f"/public.{t}/" in (x["canonical_id"] or "")
                    or f"-{slug('public.' + t)}-" in x["id"])]
        assert len(got) == n > 0, t
        # FK edges: one per non-null key value
        for e in tm.edges:
            col = e["evidence"][0]
            want = _live(f'SELECT COUNT(*) FROM public."{t}" WHERE "{col}" IS NOT NULL')[0][0]
            got_e = [x for x in store.open_edges() if x["kind"] == e["kind"]
                     and x["src_id"].split(":")[0] == e["src"].split(":")[0]
                     and x["dst_id"].split(":")[0] == e["dst"].split(":")[0]]
            assert len(got_e) >= want > 0, (t, e)
    # INV-2026-0063 (DB-only) is in the graph
    assert _live("SELECT COUNT(*) FROM invoices WHERE invoice_no='INV-2026-0063'")[0][0] == 1
    assert any(x["label"] == "INV-2026-0063" for x in store.open_nodes()
               if x["kind"] == "invoice")
    # facts file row counts == live counts
    manifest = json.loads((Path(out["run"]["run_dir"]) / "facts.manifest.json")
                          .read_text("utf-8"))
    with closing(sqlite3.connect(manifest["facts_file"])) as db:
        for t in tables:
            live_n = _live(f'SELECT COUNT(*) FROM public."{t}"')[0][0]
            assert db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] == live_n, t
    # a schema card per table, captured, anchored, dated
    cards = [json.loads(l) for l in (Path(out["run"]["run_dir"]) / "cards.jsonl")
             .read_text("utf-8").splitlines()]
    sc = [c for c in cards if c["kind"] == "schema_card"]
    assert {c["table"] for c in sc} == {f"public.{t}" for t in tables}
    for c in sc:
        text = capture.get_text(c["sha256"])
        assert out["tabular"]["extracted_at"] in text
        assert store.open_node_row(c["anchor"]) is not None


def test_live_counting_helper_matches_the_database(live_ingest):
    from databridge_mcp_ingest.tabular import count_query

    want = _live("SELECT COUNT(*) FROM invoices WHERE customer_id=%s AND status=%s",
                 ("C-1001", "open"))[0][0]
    ans = count_query(SOURCE, "SELECT COUNT(*) FROM invoices WHERE customer_id='C-1001' AND "
                              "status='open'", question="open invoices for C-1001")
    assert ans.scalar == want == 1
    assert ans.citation.startswith("[SQL]") and live_ingest["out"]["tabular"]["extracted_at"] \
        in ans.citation


def test_live_mapping_round_trips(live_ingest):
    from databridge_mcp_ingest.mapping import load_mapping, save_mapping

    m = load_mapping(SOURCE)
    a = m.model_dump()
    save_mapping(m)
    b = load_mapping(SOURCE).model_dump()
    a.pop("updated_at"), b.pop("updated_at")
    assert a == b and any(t.status == "approved" for t in m.tables.values())


def test_restricted_ingest_connection_refuses_writes(live_ok):
    from databridge_mcp_ingest.connections import SourceConnection
    from databridge_mcp_ingest.client import open_client

    before = _live("SELECT COUNT(*) FROM invoices")[0][0]
    conn = SourceConnection.model_validate(_conn("larkspur_db_ro_probe", READER, "restricted"))
    with open_client(conn) as c:
        res = c.call_tool("execute_sql", {"sql": "DELETE FROM invoices WHERE false"})
    # postgres-mcp reports the refusal as TEXT with is_error False — the pyrepr parse is what
    # turns it into a loud failure in the tabular stage
    assert res.is_error or res.text.startswith("Error")
    assert _live("SELECT COUNT(*) FROM invoices")[0][0] == before


def test_scratch_writer_role_can_only_insert(live_ok):
    """The write-capable declaration, exercised in a SCRATCH schema with a SCRATCH role."""
    from databridge_mcp_ingest.client import open_client
    from databridge_mcp_ingest.connections import SourceConnection
    import os

    from databridge_mcp_ingest.tabular.writer import (apply_writer_password, insert_sql,
                                                      writer_role_sql)

    tag = uuid.uuid4().hex[:8]
    schema, role, pw = f"walnut_s2_scratch_{tag}", f"walnut_s2_writer_{tag}", f"pw_{tag}"
    # R-022 f1 #9: the DDL carries no credential; the password comes from the environment only
    os.environ["WALNUT_S2_SCRATCH_WRITER_PW"] = pw
    with _pg(OWNER) as owner:
        ddl = writer_role_sql(schema=schema, role=role, create_schema=True)
        assert pw not in ddl
        assert not [x for x in ddl.splitlines() if "PASSWORD" in x and not x.startswith("--")]
        owner.execute(ddl)
        apply_writer_password(owner.execute, role=role, password_env="WALNUT_S2_SCRATCH_WRITER_PW")
    try:
        dsn = f"postgresql://{role}:{pw}@127.0.0.1:{PORT}/larkspur"
        conn = SourceConnection.model_validate(_conn(f"s2_writer_{tag}", dsn, "unrestricted"))
        with open_client(conn) as c:
            ok = c.call_tool("execute_sql", {"sql": insert_sql(
                {"customer_id": "C-1001", "invoice_no": "INV-2026-0063",
                 "title": "Chase INV-2026-0063", "created_by": "s2-test"}, schema=schema)})
            assert not ok.is_error and not ok.text.startswith("Error"), ok.text
            for denied in (f"SELECT * FROM {schema}.follow_up_tasks",
                           f"DELETE FROM {schema}.follow_up_tasks",
                           f"UPDATE {schema}.follow_up_tasks SET title='x'",
                           "SELECT * FROM public.invoices"):
                r = c.call_tool("execute_sql", {"sql": denied})
                assert r.is_error or "permission denied" in r.text.lower(), (denied, r.text)
        with _pg(OWNER) as owner:
            assert owner.execute(f"SELECT COUNT(*) FROM {schema}.follow_up_tasks"
                                 ).fetchone()[0] == 1
    finally:
        with _pg(OWNER) as owner:
            owner.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            owner.execute(f"REVOKE ALL ON DATABASE larkspur FROM {role}")
            owner.execute(f"DROP ROLE IF EXISTS {role}")

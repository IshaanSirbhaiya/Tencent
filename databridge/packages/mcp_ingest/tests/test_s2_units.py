"""S2 — the database recipe, replayed from RECORDED postgres-mcp output (no network, no model).

`test_s2_recorded_postgres_mcp.json` holds every tool call a live `ingest_database` made against
the synthetic demo DB (postgres-mcp in restricted mode, role walnut_reader), verbatim — including
the Python-repr row strings (Decimal, datetime.date, tz-aware datetime, timedelta, UUID, None).
Expectations are DERIVED from those recorded outputs (parsed independently of the code under
test where it matters), never written down.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path

import pytest

from databridge_mcp_ingest.client import ContentPart, ToolInfo, ToolResult

RECORDED = Path(__file__).with_name("test_s2_recorded_postgres_mcp.json")
SOURCE = "larkspur_db"


# --- the replay client -----------------------------------------------------------------------------
class ReplayClient:
    """Answers exactly what the live server answered; an unrecorded call fails the test."""

    supports_resources = True

    def __init__(self, data: dict) -> None:
        self.data = data
        self.calls: list[tuple[str, dict]] = []

    def list_tools(self):
        return [ToolInfo(**t) for t in self.data["tools"]]

    def list_resources(self):
        return []

    def call_tool(self, name, arguments):
        key = json.dumps([name, arguments], sort_keys=True)
        self.calls.append((name, arguments))
        if key not in self.data["calls"]:
            raise AssertionError(f"unrecorded call: {key[:200]}")
        rec = self.data["calls"][key]
        return ToolResult(parts=(ContentPart(kind="text", text=rec["text"]),),
                          is_error=rec["is_error"])

    def read_resource(self, uri):
        raise AssertionError("no resources recorded")

    def close(self):
        pass


@pytest.fixture(scope="module")
def recorded() -> dict:
    return json.loads(RECORDED.read_text(encoding="utf-8"))


def _recorded_rows(recorded: dict, sql_prefix: str) -> list[dict]:
    from databridge_mcp_ingest.tabular.pyrepr import loads_rows

    hits = [v for k, v in recorded["calls"].items()
            if json.loads(k)[1].get("sql", "").startswith(sql_prefix)]
    assert hits, f"nothing recorded for {sql_prefix!r}"
    return loads_rows(hits[0]["text"], typed=True)


@pytest.fixture
def s2_env(walnut_env, tmp_path, monkeypatch):
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(tmp_path / "facts"))
    monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(tmp_path / "passages"))
    return walnut_env


@pytest.fixture
def workspace_vocab():
    from databridge_graph.packs import deployment

    with deployment("workspace") as vocab:
        yield vocab


def _register(auto_approve=True):
    from databridge_mcp_ingest import register_connection

    return register_connection({
        "id": SOURCE, "transport": {"type": "stdio", "command": "uvx", "args": [
            "--with", "mcp<2", "postgres-mcp", "--access-mode=restricted"],
            "env": {"DATABASE_URI": "postgresql://walnut_reader:${WALNUT_DEMO_PG_READER_PASSWORD}@127.0.0.1:5433/larkspur"}},
        "scope": {"tables_include": ["public.*"]}, "auto_approve": auto_approve})


@pytest.fixture
def ingested(s2_env, recorded, workspace_vocab, tmp_path):
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest import propose_plan
    from databridge_mcp_ingest.tabular import ingest_database

    _register()
    client = ReplayClient(recorded)
    plan = propose_plan(SOURCE, client=client)
    store = GraphStore(str(tmp_path / "graph.db"))
    out = ingest_database(SOURCE, store, None, client=client, vocab=workspace_vocab, index=False)
    return {"plan": plan, "store": store, "out": out, "client": client, "vocab": workspace_vocab}


# --- pyrepr ----------------------------------------------------------------------------------------
def test_pyrepr_parses_every_recorded_shape_and_never_touches_string_contents(recorded):
    from databridge_mcp_ingest.tabular.pyrepr import loads_rows

    probe = next(v["text"] for k, v in recorded["calls"].items() if "naive_ts" in k)
    typed = loads_rows(probe, typed=True)[0]
    text = loads_rows(probe)[0]
    assert isinstance(typed["ts"], dt.datetime) and typed["ts"].utcoffset() == dt.timedelta(0)
    assert typed["iv"] == dt.timedelta(days=1, hours=2)
    assert str(typed["u"]) == "84cd3e34-b482-45ea-be14-2a457d8be836"
    assert typed["nothing"] is None and typed["j"] == {"a": 1}
    # a string that CONTAINS constructor syntax is data, not code
    raw_literal = re.search(r"'tricky': \"(.*?)\"", probe).group(1)
    assert typed["tricky"] == raw_literal == text["tricky"]
    assert text["ts"].startswith("2026-09-30T18:28:45.927051") and text["iv"] == "P1DT2H"
    json.dumps(text)                                   # the text form is JSON-safe


def test_pyrepr_keeps_decimal_digits_and_iso_dates(recorded):
    rows = _recorded_rows(recorded, 'SELECT * FROM "public"."invoices"')
    assert rows and all(isinstance(r["net_gbp"], Decimal) for r in rows)
    from databridge_mcp_ingest.tabular.pyrepr import loads_rows, to_text

    raw = next(v["text"] for k, v in recorded["calls"].items()
               if json.loads(k)[1].get("sql", "").startswith('SELECT * FROM "public"."invoices"'))
    digits = re.findall(r"Decimal\('([^']+)'\)", raw)
    as_text = [to_text(r[c]) for r in rows for c in ("net_gbp", "vat_gbp", "total_gbp")]
    assert sorted(as_text) == sorted(digits)           # exactly what the server wrote
    for r in loads_rows(raw):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["issued_on"])


def test_pyrepr_refuses_non_literals():
    from databridge_mcp_ingest.tabular.pyrepr import ReprParseError, loads_rows

    for bad in ["[{'x': __import__('os').system('echo')}]", "[{'x': open('f')}]",
                "Error: Error validating query: DELETE FROM invoices"]:
        with pytest.raises(ReprParseError):
            loads_rows(bad)


# --- the whole recipe, replayed ---------------------------------------------------------------------
def test_replayed_ingest_profiles_maps_builds_and_publishes(ingested, recorded):
    from databridge_mcp_ingest.mapping import load_mapping

    plan, store, out = ingested["plan"], ingested["store"], ingested["out"]
    assert plan.profiles == ["postgres-mcp"] and plan.status == "approved" and not plan.problems
    assert out["run"]["status"] == "ok", out["run"]["message"]
    tab = out["tabular"]
    profiles = json.loads((Path(out["run"]["run_dir"]) / "profile.json").read_text("utf-8"))
    listed = _recorded_rows(recorded, "SELECT table_schema, table_name FROM")
    assert {p["ref"] for p in profiles} == {f"{r['table_schema']}.{r['table_name']}"
                                            for r in listed}
    # keys come from pg_catalog: every table in the demo DB has a primary key
    assert all(p["primary_key"] for p in profiles)
    mapping = load_mapping(SOURCE)
    mapped = {t: m for t, m in mapping.tables.items() if m.status == "approved"}
    for t, m in mapped.items():
        rows = next(p["row_count"] for p in profiles if p["ref"] == t)
        # the node id is the (slugged) source-qualified row key; canonical_id is the mapped
        # identifier column when its value arrived unmasked (so a document mention can
        # exact-merge with the row), else the row key
        from databridge_graph.estate_seed import slug
        got = [n for n in store.open_nodes() if n["kind"] == m.node["kind"]
               and (f"/{t}/" in (n["canonical_id"] or "") or f"-{slug(t)}-" in n["id"])]
        assert len(got) == rows, t
    # every FK in a mapped table became edges, one per non-null key value
    fx = sqlite3.connect(tab["facts"]["facts_file"])
    edges = list(store.open_edges())
    for t, m in mapped.items():
        for e in m.edges:
            col = e["evidence"][0]
            ft = t.split(".", 1)[1]
            n = fx.execute(f'SELECT COUNT(*) FROM "{ft}" WHERE "{col}" IS NOT NULL').fetchone()[0]
            got = [x for x in edges if x["kind"] == e["kind"]
                   and x["src_id"].split(":")[0] == e["src"].split(":")[0]
                   and x["dst_id"].split(":")[0] == e["dst"].split(":")[0]]
            assert len(got) >= n > 0, (t, e)
            decl = ingested["vocab"].edge(e["kind"])
            src_kind, dst_kind = e["src"].split(":")[0], e["dst"].split(":")[0]
            assert src_kind in decl.src or "*" in decl.src
            assert dst_kind in decl.dst or "*" in decl.dst
    # table_cell pointers carry the connection id
    for n in store.open_nodes():
        for p in store.sources_for(n["id"]):
            assert p.source_id == SOURCE
    fx.close()


def test_db_only_invoice_is_in_graph_and_facts(ingested, recorded):
    invoices = _recorded_rows(recorded, 'SELECT * FROM "public"."invoices"')
    numbers = {r["invoice_no"] for r in invoices}
    labels = {n["label"] for n in ingested["store"].open_nodes() if n["kind"] == "invoice"}
    assert numbers <= labels and "INV-2026-0063" in numbers
    fx = ingested["out"]["tabular"]["facts"]["facts_file"]
    with closing(sqlite3.connect(fx)) as db:
        assert db.execute("SELECT COUNT(*) FROM invoices WHERE invoice_no='INV-2026-0063'"
                          ).fetchone()[0] == 1


def test_facts_store_counts_equal_measured_counts(ingested):
    run_dir = Path(ingested["out"]["run"]["run_dir"])
    profiles = {p["ref"]: p for p in json.loads((run_dir / "profile.json").read_text("utf-8"))}
    manifest = json.loads((run_dir / "facts.manifest.json").read_text("utf-8"))
    assert manifest["status"] == "ok"
    db = sqlite3.connect(manifest["facts_file"])
    assert len(manifest["tables"]) == len(profiles) > 0
    for t in manifest["tables"]:
        assert t["mode"] == "rows"
        got = db.execute(f'SELECT COUNT(*) FROM "{t["facts_table"]}"').fetchone()[0]
        assert got == profiles[t["source_table"]]["row_count"] == t["rows_in_facts"]
    # dates are ISO text, money sums exactly
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for (d,) in
               db.execute("SELECT issued_on FROM invoices"))
    db.close()
    assert hashlib.sha256(Path(manifest["facts_file"]).read_bytes()).hexdigest() == \
        manifest["sha256"]


def test_schema_cards_captured_anchored_and_dated(ingested):
    from databridge_mcp_ingest import capture

    out, store = ingested["out"], ingested["store"]
    run_dir = Path(out["run"]["run_dir"])
    cards = [json.loads(l) for l in (run_dir / "cards.jsonl").read_text("utf-8").splitlines()]
    schema_cards = [c for c in cards if c["kind"] == "schema_card"]
    profiles = json.loads((run_dir / "profile.json").read_text("utf-8"))
    assert len(schema_cards) == len(profiles)
    extracted = out["tabular"]["extracted_at"]
    facts_name = Path(out["tabular"]["facts"]["facts_file"]).name
    for c in schema_cards:
        text = capture.get_text(c["sha256"])
        assert hashlib.sha256(capture.get_bytes(c["sha256"])).hexdigest() == c["sha256"]
        prof = next(p for p in profiles if p["ref"] == c["table"])
        assert f"Extract date: {extracted}" in text and facts_name in text
        assert f"holds {prof['row_count']} row" in text and "## Coverage" in text
        node = store.open_node_row(c["anchor"])
        assert node is not None and node["kind"] == "document"
        ptrs = store.sources_for(c["anchor"])
        assert any(p.capture_digest == c["sha256"] and p.source_id == SOURCE for p in ptrs)
    # the measured prose column is captured as passages, its coverage stated on the card
    prose_tables = {p["ref"] for p in profiles if p["prose_columns"]}
    assert prose_tables and {c["table"] for c in cards if c["kind"] == "prose"} == prose_tables
    unmapped_card = next(c for c in schema_cards if c["table"] in prose_tables)
    assert "PROSE" in capture.get_text(unmapped_card["sha256"])


def test_count_query_answers_by_sql_with_citation(ingested, recorded):
    from databridge_mcp_ingest.tabular import count_query

    invoices = _recorded_rows(recorded, 'SELECT * FROM "public"."invoices"')
    expected = sum(1 for r in invoices if r["customer_id"] == "C-1001" and r["status"] == "open")
    ans = count_query(SOURCE, "SELECT COUNT(*) FROM invoices WHERE customer_id = ? AND "
                              "status = ?", params=("C-1001", "open"),
                      question="How many open invoices does C-1001 have?")
    assert ans.scalar == expected == 1
    assert ans.citation.startswith("[SQL] SELECT COUNT(*) FROM invoices")
    assert ingested["out"]["tabular"]["extracted_at"] in ans.citation
    p = ans.pointers[0]
    assert p.kind == "query_cell" and p.source_id == SOURCE and p.row_ordinal == 0
    assert p.snapshot.content_hash == ans.facts_sha256
    total = count_query(SOURCE, "SELECT SUM(total_gbp) FROM invoices").scalar
    assert Decimal(str(total)) == sum(r["total_gbp"] for r in invoices)
    listed = count_query(SOURCE, "SELECT invoice_no FROM invoices WHERE status='open' "
                                 "ORDER BY invoice_no")
    assert [r[0] for r in listed.rows] == sorted(r["invoice_no"] for r in invoices
                                                 if r["status"] == "open")


@pytest.mark.parametrize("sql", [
    "INSERT INTO invoices (invoice_no) VALUES ('X')",
    "DELETE FROM invoices",
    "SELECT 1; DELETE FROM invoices",
    "ATTACH DATABASE 'x.db' AS x",
    "PRAGMA writable_schema = 1",
    "UPDATE invoices SET status = 'paid'",
    "DROP TABLE invoices",
])
def test_count_query_refuses_anything_but_one_select(ingested, sql):
    from databridge_mcp_ingest.tabular import FactsQueryError, count_query

    before = count_query(SOURCE, "SELECT COUNT(*) FROM invoices").scalar
    with pytest.raises(FactsQueryError):
        count_query(SOURCE, sql)
    assert count_query(SOURCE, "SELECT COUNT(*) FROM invoices").scalar == before


def test_describe_facts_lists_tables_and_columns(ingested):
    from databridge_mcp_ingest.tabular import describe_facts

    d = describe_facts(SOURCE)
    inv = next(t for t in d["tables"] if t["facts_table"] == "invoices")
    assert {"invoice_no", "customer_id", "total_gbp"} <= {c["column"] for c in inv["columns"]}
    assert d["extracted_at"] == ingested["out"]["tabular"]["extracted_at"]


# --- mapping ---------------------------------------------------------------------------------------
def test_mapping_round_trips_and_edits_are_vocabulary_checked(ingested):
    from databridge_mcp_ingest.mapping import (load_mapping, mapping_path, save_mapping,
                                               set_table_mapping)

    m = load_mapping(SOURCE)
    before = m.model_dump()
    save_mapping(m)
    again = load_mapping(SOURCE).model_dump()
    before.pop("updated_at"), again.pop("updated_at")
    assert again == before and mapping_path(SOURCE).is_file()
    with pytest.raises(ValueError):
        set_table_mapping(SOURCE, "public.contact_notes", edited_by="tester",
                          node={"kind": "not_a_kind", "label": "{note}", "sources": ["note_id"],
                                "attrs": {}}, vocab=ingested["vocab"])
    edited = set_table_mapping(SOURCE, "public.contact_notes", edited_by="tester",
                               node={"kind": "document", "label": "{note_id}",
                                     "sources": ["note_id"], "attrs": {"note": "note"}},
                               vocab=ingested["vocab"])
    tm = edited.tables["public.contact_notes"]
    assert tm.origin == "manual" and tm.status == "proposed"


def test_mapping_proposes_canonical_ids_from_measured_identifiers(ingested):
    from databridge_mcp_ingest.mapping import load_mapping

    vocab = ingested["vocab"]
    run_dir = Path(ingested["out"]["run"]["run_dir"])
    profiles = {p["ref"]: p for p in json.loads((run_dir / "profile.json").read_text("utf-8"))}
    for t, m in load_mapping(SOURCE).tables.items():
        if not m.node:
            continue
        col = m.node.get("canonical_id")
        kind = m.node["kind"]
        if vocab.is_subject(kind):
            # R-022 f2 #1: a subject has NO merge key; its declared identifier only proposes
            # candidates
            assert col is None
            cand = m.node.get("candidate_identifier")
            assert cand and cand in vocab.identifiers_for(kind)
        if col:
            c = next(c for c in profiles[t]["columns"] if c["name"] == col)
            assert c["unique"], (t, col)


def test_history_snapshot_needs_two_signals():
    from databridge_mcp_ingest.tabular.policy import load_policy
    from databridge_mcp_ingest.tabular.profiling import detect_history

    def prof(table, cols):
        return {"ref": f"public.{table}", "table": table,
                "columns": [{"name": c} for c in cols]}

    ps = [prof("invoices", ["invoice_no", "status"]),
          prof("invoices_history", ["invoice_no", "status", "snapshot_date"]),
          prof("audit_history", ["id", "detail"]),                       # name only
          prof("weekly_copy", ["invoice_no", "status", "as_of"])]        # column only
    detect_history(ps, load_policy())
    flagged = {p["table"] for p in ps if p["history_snapshot"]["is"]}
    assert flagged == {"invoices_history"}


# --- aggregation and publication (a SQLite stand-in for the SQL tool) --------------------------------
class SqliteSqlClient:
    """Executes the dialect's statements on a local SQLite whose tables live in schema `public`."""

    def __init__(self, path: Path) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.execute(f"ATTACH DATABASE '{path}' AS public")
        self.db.row_factory = sqlite3.Row

    def call_tool(self, name, args):
        rows = [dict(r) for r in self.db.execute(args["sql"]).fetchall()]
        return ToolResult(parts=(ContentPart(kind="text", text=json.dumps(rows)),))


def _big_table(path: Path, n: int) -> None:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, city TEXT, "
               "status TEXT, amount NUMERIC)")
    db.execute("DELETE FROM orders")
    db.executemany("INSERT INTO orders VALUES (?,?,?,?)",
                   [(i, f"city-{i % 12}", ("open", "paid")[i % 2], i * 1.5) for i in range(n)])
    db.commit()
    db.close()


def _stage(path: Path, policy_over: dict):
    from databridge_mcp_ingest.plans import SqlSpec, StepSpec
    from databridge_mcp_ingest.tabular.policy import _merge, load_policy
    from databridge_mcp_ingest.tabular.profiling import SqlRunner, profile_source

    step = StepSpec(id="t", strategy="tabular", sql=SqlSpec(tool="query", dialect="postgres"))
    runner = SqlRunner(SqliteSqlClient(path), step)
    runner.dialect = {**runner.dialect, "describe": "SELECT name AS column_name, type AS "
                      "data_type FROM pragma_table_info({table|literal}, 'public')",
                      "keys": "SELECT name AS column_name, 'PRIMARY KEY' AS constraint_type "
                              "FROM pragma_table_info({table|literal}, 'public') WHERE pk > 0"}
    policy = _merge(load_policy(), policy_over)
    return runner, policy, profile_source(runner, [("public", "orders")], policy)


def test_large_table_is_aggregated_by_measured_cardinality(s2_env, tmp_path):
    from databridge_mcp_ingest.tabular.cards import card_text
    from databridge_mcp_ingest.tabular.facts import build_facts

    src = tmp_path / "src.sqlite"
    n = 240
    _big_table(src, n)
    runner, policy, profiles = _stage(src, {"facts": {"row_cap": 100}})
    cities = next(c for c in profiles[0]["columns"] if c["name"] == "city")["distinct"]
    man = build_facts("agg_src", runner, profiles, policy)
    entry = man["tables"][0]
    lo = policy["facts"]["aggregate_min_distinct"]
    hi = policy["facts"]["aggregate_max_distinct"]
    assert lo <= cities <= hi and entry["mode"] == "aggregated" and entry["group_by"] == ["city"]
    with closing(sqlite3.connect(man["facts_file"])) as db:
        assert db.execute("SELECT SUM(row_count) FROM orders").fetchone()[0] == n
        assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == cities
    text = card_text("agg_src", profiles[0], entry, extracted_at=man["extracted_at"],
                     facts_file=man["facts_file"])
    assert "PARTIAL" in text and f"not the {n} individual rows" in text


def test_publication_refuses_a_shrunken_facts_file(s2_env, tmp_path):
    from databridge_mcp_ingest.tabular.facts import build_facts, load_manifest

    src = tmp_path / "src.sqlite"
    _big_table(src, 80)
    runner, policy, profiles = _stage(src, {})
    first = build_facts("shrink_src", runner, profiles, policy)
    assert first["status"] == "ok" and first["rows_total"] == 80
    _big_table(src, 20)
    runner, policy, profiles = _stage(src, {})
    second = build_facts("shrink_src", runner, profiles, policy)
    assert second["status"] == "refused" and "SHRUNK" in second["refused"]
    assert load_manifest("shrink_src")["rows_total"] == 80           # previous file kept
    with closing(sqlite3.connect(first["facts_file"])) as db:
        assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 80
    from conftest import test_principal

    forced = build_facts("shrink_src", runner, profiles, policy, force=test_principal())
    assert forced["status"] == "ok" and forced["rows_total"] == 20


# --- the write connection -------------------------------------------------------------------------
def test_writer_sql_file_is_the_rendered_single_source():
    from databridge_mcp_ingest import paths
    from databridge_mcp_ingest.tabular.writer import insert_sql, writer_role_sql

    f = paths.bundled_profiles_dir() / "postgres" / "agent_writer.sql"
    sql = f.read_text(encoding="utf-8")
    assert sql == writer_role_sql()
    grants = re.findall(r"GRANT (\w+(?: \w+)?) ON (?:TABLE |SEQUENCE |SCHEMA |DATABASE )?"
                        r"(\S+) TO walnut_agent_writer", sql)
    assert {g[0] for g in grants} == {"CONNECT", "USAGE", "INSERT"}      # no SELECT/UPDATE/DELETE
    assert insert_sql({"customer_id": "C-1001", "title": "O'Neil"}) == (
        "INSERT INTO public.follow_up_tasks (customer_id, title) VALUES ('C-1001', 'O''Neil')")
    with pytest.raises(ValueError):
        insert_sql({"customer_id": "x", "status": "paid"})


def test_write_declaration_is_never_a_crawl_profile():
    from databridge_mcp_ingest import paths
    from databridge_mcp_ingest.plans import load_profiles

    import yaml

    decl = yaml.safe_load((paths.bundled_profiles_dir() / "postgres" /
                           "write_connection.example.yaml").read_text(encoding="utf-8"))[0]
    assert decl["id"] != SOURCE and "--access-mode=unrestricted" in decl["direct"]["args"]
    assert all(p.id != decl["id"] for p in load_profiles())


def test_policy_word_lists_load_as_strings():
    """YAML 1.1 reads bare `no`/`yes`/`on`/`off` as booleans — `no` in canonical_columns once
    crashed the mapping (`str + bool`). Every word list in the policy must load as strings."""
    from databridge_mcp_ingest.tabular.policy import load_policy

    p = load_policy()
    lists = [p["canonical_columns"], p["fk_edge_metas"],
             *p["meta_kind_hints"].values(), *p["type_classes"].values(),
             p["history_snapshot"]["name_patterns"], p["history_snapshot"]["snapshot_columns"]]
    words = [w for lst in lists for w in lst]
    assert words and all(isinstance(w, str) for w in words)
    assert "no" in p["canonical_columns"]

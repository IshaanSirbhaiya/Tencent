"""FXI4 — Codex first review of mcp_ingest tabular + drift (R-022 f1). One test (or more) per
finding; each asserts the rule the code now enforces, with a non-vacuous check beside it."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from conftest import test_principal
from test_w4_dialects import EngineClient, sqlite_exec

pytestmark = pytest.mark.real_approval


# --- shared helpers -----------------------------------------------------------------------------
@pytest.fixture
def ws_vocab():
    from databridge_graph.packs import deployment

    with deployment("workspace") as vocab:
        yield vocab


@pytest.fixture
def facts_env(walnut_env, tmp_path, monkeypatch):
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(tmp_path / "facts"))
    monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(tmp_path / "passages"))
    return walnut_env


def _snap(table, cols, *, pk=("id",), fks=(), rows=None):
    return {"table": table, "columns": [{"name": c, "type": "text"} for c in cols],
            "primary_key": list(pk), "foreign_keys": list(fks),
            "rows": rows if rows is not None else [{c: f"{c}-{i}" for c in cols}
                                                   for i in range(3)]}


def _proposed_mapping(source_id, ws_vocab, root=None):
    from databridge_mcp_ingest.mapping import (SourceMapping, TableMapping, _table_mapping,
                                               save_mapping)

    tm = _table_mapping("invoice", _snap("invoice", ["id", "invoice_no", "title"]), "invoice",
                        {}, ws_vocab, origin="rules", why="test")
    tm2 = _table_mapping("organisation", _snap("organisation", ["id", "name"]), "organisation",
                         {}, ws_vocab, origin="rules", why="test")
    save_mapping(SourceMapping(source_id=source_id, tables={"invoice": tm, "organisation": tm2}),
                 root)


# === #1 approvals are authenticated, per table, digest-bound and append-only ====================
def test_1_mapping_approval_refuses_a_typed_name_and_records_each_approver(walnut_env, ws_vocab):
    from databridge_mcp_ingest.mapping import (MappingApprovalRefused, approval_log_path,
                                               approve_mapping, load_mapping, mapping_digest)

    _proposed_mapping("m1", ws_vocab)
    for claim in ("Alice", {"principal_id": "Alice"}, None):
        with pytest.raises(MappingApprovalRefused):
            approve_mapping("m1", approved_by=claim, vocab=ws_vocab)
    assert all(t.status == "proposed" for t in load_mapping("m1").tables.values())
    alice, bob = test_principal("alice"), test_principal("bob")
    approve_mapping("m1", principal=alice, tables=["invoice"], vocab=ws_vocab)
    m = approve_mapping("m1", principal=bob, tables=["organisation"], vocab=ws_vocab)
    # each table keeps ITS approver (Bob's later approval never re-attributes Alice's)
    assert m.tables["invoice"].approval["principal_id"] == "alice"
    assert m.tables["organisation"].approval["principal_id"] == "bob"
    assert m.tables["invoice"].approval["digest"] == mapping_digest(m.tables["invoice"])
    lines = [json.loads(x) for x in approval_log_path("m1").read_text("utf-8").splitlines()]
    assert [(r["table"], r["principal_id"]) for r in lines] == [("invoice", "alice"),
                                                               ("organisation", "bob")]
    assert all(r["authenticated_by"] and r["record_digest"] for r in lines)


def test_1_dialect_approval_refuses_a_typed_name_and_logs_the_principal(walnut_env, tmp_path):
    from databridge_mcp_ingest import register_connection
    from databridge_mcp_ingest.tabular.dialect_draft import (DialectError, approval_log_path,
                                                             approve_dialect, draft_dialect)

    db = _sqlite_with_tables(tmp_path / "d.sqlite")
    register_connection({"id": "d1", "transport": {"type": "stdio", "command": "uvx"}})
    draft_dialect("d1", client=EngineClient(sqlite_exec(db)[0]))
    with pytest.raises(DialectError, match="not an authenticated principal"):
        approve_dialect("d1", approved_by="Dev")
    done = approve_dialect("d1", principal=test_principal("dev"))
    assert done.approved_by == "dev" and done.approval["principal_id"] == "dev"
    [rec] = [json.loads(x) for x in approval_log_path("d1").read_text("utf-8").splitlines()]
    assert rec["template_sha256"] == done.approval["template_sha256"]


# === #5 edited mappings are re-validated at approval AND at run time ============================
@pytest.mark.parametrize("edit, why", [
    (lambda n, e: n.update(kind="spaceship"), "not in the active vocabulary"),
    (lambda n, e: n.update(colour="red"), "malformed"),
    (lambda n, e: e.append({"kind": "works_for", "src": "invoice:{walnut_row_key}",
                            "dst": "organisation:{walnut_fk_x}"}), "does not admit"),
    (lambda n, e: e.append({"kind": "billed_to", "src": "organisation:{walnut_row_key}",
                            "dst": "project:{walnut_fk_x}"}), "neither end on this table"),
])
def test_5_an_edited_mapping_that_breaks_the_ontology_is_refused(walnut_env, ws_vocab, edit, why):
    import yaml

    from databridge_mcp_ingest.mapping import (MappingApprovalRefused, approve_mapping,
                                               mapping_path)

    _proposed_mapping("m5", ws_vocab)
    p = mapping_path("m5")
    data = yaml.safe_load(p.read_text("utf-8"))
    node, edges = data["tables"]["invoice"]["node"], data["tables"]["invoice"]["edges"]
    edit(node, edges)
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(MappingApprovalRefused, match=why):
        approve_mapping("m5", principal=test_principal(), vocab=ws_vocab)


def test_5_a_subject_canonical_id_must_be_a_declared_identifier(walnut_env, ws_vocab):
    # R-022 f2 #1 supersedes f1 #5: a subject carries NO canonical_id at all; its declared
    # identifier is a `candidate_identifier`, and that must be a declared identifier
    from databridge_mcp_ingest.mapping import TableMapping, validate_table_mapping

    ident = ws_vocab.identifiers_for("person")[0]
    ok = TableMapping(node={"kind": "person", "label": "{name}", "sources": ["id"],
                            "attrs": {}, "candidate_identifier": ident})
    bad = ok.model_copy(update={"node": {**ok.node, "candidate_identifier": "name"}})
    merge = ok.model_copy(update={"node": {**ok.node, "canonical_id": ident}})
    assert validate_table_mapping("t", ok, ws_vocab) == []                  # non-vacuous
    assert any("not a declared identifier" in p for p in validate_table_mapping("t", bad,
                                                                               ws_vocab))
    assert any("cannot carry a canonical_id" in p
               for p in validate_table_mapping("t", merge, ws_vocab))


def test_5_run_time_ignores_a_yaml_approval_and_an_approval_edited_afterwards(walnut_env,
                                                                              ws_vocab):
    import yaml

    from databridge_mcp_ingest.mapping import (approve_mapping, effective_mapping, load_mapping,
                                               mapping_path)

    _proposed_mapping("m5r", ws_vocab)
    p = mapping_path("m5r")
    data = yaml.safe_load(p.read_text("utf-8"))
    data["tables"]["invoice"]["status"] = "approved"                  # a typed "approval"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    eff = effective_mapping(load_mapping("m5r"), ws_vocab)
    assert eff.tables["invoice"].status == "proposed"
    assert "not an approval" in eff.tables["invoice"].rationale[-1]
    approve_mapping("m5r", principal=test_principal(), vocab=ws_vocab)
    assert effective_mapping(load_mapping("m5r"), ws_vocab).tables["invoice"].status == \
        "approved"                                                        # non-vacuous
    data = yaml.safe_load(p.read_text("utf-8"))
    data["tables"]["invoice"]["node"]["kind"] = "contract"            # edited after approval
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    eff = effective_mapping(load_mapping("m5r"), ws_vocab)
    assert eff.tables["invoice"].status == "proposed"
    assert "digest mismatch" in eff.tables["invoice"].rationale[-1]


# === #3 untrusted metadata is screened before a model sees it ===================================
class _Recording:
    def __init__(self, answer):
        self.answer, self.seen = answer, []

    def complete_structured(self, task, messages, schema, seed=None):
        self.seen.append(messages)

        class R:
            parsed = schema.model_validate(self.answer)
        return R()


def test_3_table_metadata_the_screen_does_not_allow_never_reaches_the_model(ws_vocab):
    from databridge_mcp_ingest.mapping import propose_model

    snaps = {"AKIAABCDEFGHIJKLMNOP": _snap("AKIAABCDEFGHIJKLMNOP", ["id", "x"]),
             "people": _snap("people", ["id", "boss@example.test"]),             # masked
             "gizmos": _snap("gizmos", ["id", "name"])}
    h = _Recording({"tables": [{"table": "gizmos", "node_kind": "product"}]})
    out = propose_model(snaps, list(snaps), {}, ws_vocab, h)
    sent = json.loads(h.seen[0][-1]["content"])["tables"]
    assert [t["table"] for t in sent] == ["gizmos"]                    # the only clean one
    assert out["gizmos"].status == "proposed"                          # non-vacuous
    for t in ("AKIAABCDEFGHIJKLMNOP", "people"):
        assert out[t].status == "unmapped" and "not offered to the model" in out[t].rationale[0]


def test_3_a_dialect_self_description_the_screen_does_not_allow_is_not_sent(ws_vocab):
    from databridge_mcp_ingest.client import ToolInfo
    from databridge_mcp_ingest.tabular.dialect_draft import DialectError, model_draft

    h = _Recording({})
    tool = ToolInfo(name="q", description="runs sql. key AKIAABCDEFGHIJKLMNOP")
    with pytest.raises(DialectError, match="not sent to the model"):
        model_draft(h, tool=tool, detection={"probes": []})
    assert h.seen == []


# === #16 FK proposals do not depend on the order of the model's answer ==========================
def test_16_fk_edges_are_the_same_whatever_order_the_model_answers_in(ws_vocab):
    from databridge_mcp_ingest.mapping import propose_model

    snaps = {"bills": _snap("bills", ["id", "client_ref", "title"], fks=[
                 {"column": "client_ref", "ref_schema": None, "ref_table": "clients",
                  "ref_column": "id"}]),
             "clients": _snap("clients", ["id", "name"])}
    answers = [{"table": "bills", "node_kind": "invoice"},
               {"table": "clients", "node_kind": "organisation"}]
    one = propose_model(snaps, ["bills", "clients"], {}, ws_vocab,
                        _Recording({"tables": answers}))
    two = propose_model(snaps, ["bills", "clients"], {}, ws_vocab,
                        _Recording({"tables": answers[::-1]}))
    assert one["bills"].edges and one["bills"].edges == two["bills"].edges


# === #4 / #13 read-only probing, allow-list, digest binding, complete probes ====================
def _sqlite_with_tables(path: Path) -> Path:
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE orgs (id INTEGER PRIMARY KEY, name TEXT, founded DATE, size REAL);
        CREATE TABLE people (id INTEGER PRIMARY KEY, org_id INTEGER REFERENCES orgs(id),
                             email TEXT);
        INSERT INTO orgs VALUES (1, 'Acme Widgets', '2020-01-01', 10.5),
                                (2, 'Borealis', '2021-06-30', 3.25);
        INSERT INTO people VALUES (1, 1, 'a@example.test'), (2, 2, 'b@example.test');
    """)
    db.commit()
    db.close()
    return path


def _sqlite_template():
    from databridge_mcp_ingest.tabular.dialect_draft import load_templates

    return next(t for t in load_templates() if t["engine"] == "sqlite")


@pytest.mark.parametrize("stmt", ["SELECT nextval('seq') AS n FROM {schema|ident}.{table|ident}",
                                  "SELECT my_side_effect() AS n",
                                  "SELECT COUNT(*) AS n INTO copy FROM {schema|ident}.{table|ident}",
                                  "SELECT pg_catalog.setval('s', 1) AS n"])
def test_4_a_select_that_can_write_fails_validation(stmt):
    from databridge_mcp_ingest.tabular.dialect_draft import validate_template

    base = _sqlite_template()
    assert validate_template(base) == []                              # non-vacuous
    assert validate_template({**base, "count": stmt})


def test_4_a_model_draft_failing_the_checks_is_never_sent_to_the_source(walnut_env, tmp_path):
    from databridge_harness import FakeBackend, Harness

    from databridge_mcp_ingest import register_connection
    from databridge_mcp_ingest.tabular import dialect_draft as dd

    db = _sqlite_with_tables(tmp_path / "m.sqlite")
    register_connection({"id": "d4", "transport": {"type": "stdio", "command": "uvx"}})
    ref = _sqlite_template()
    others = [t for t in dd.load_templates() if t["engine"] != "sqlite"]
    evil = "SELECT COUNT(*) AS n, nextval('audit_seq') AS x FROM {schema|ident}.{table|ident}"
    harness = Harness(FakeBackend(json.dumps({
        "engine": "sqlite", "version_probe_sql": "SELECT sqlite_version() AS v",
        "version_match": "^3", **{k: ref[k] for k in dd.REQUIRED}, "count": evil})),
        profile="cloud")
    client = EngineClient(sqlite_exec(db)[0])
    prop = dd.draft_dialect("d4", client=client, templates=others, harness=harness)
    assert any("nextval" in p for p in prop.problems) and prop.probe.get("skipped")
    assert not any("nextval" in s for s in client.sql)
    assert client.sql                                  # detection did run (non-vacuous)
    with pytest.raises(dd.DialectError):
        dd.approve_dialect("d4", principal=test_principal())


def test_4_a_template_edited_after_its_probe_cannot_be_approved_or_used(walnut_env, tmp_path):
    import yaml

    from databridge_mcp_ingest import register_connection
    from databridge_mcp_ingest.tabular.dialect_draft import (DialectError, approve_dialect,
                                                             approved_dialect, dialect_path,
                                                             draft_dialect)

    db = _sqlite_with_tables(tmp_path / "e.sqlite")
    register_connection({"id": "d4e", "transport": {"type": "stdio", "command": "uvx"}})
    draft_dialect("d4e", client=EngineClient(sqlite_exec(db)[0]))
    p = dialect_path("d4e")
    data = yaml.safe_load(p.read_text("utf-8"))
    data["template"]["select"] = data["template"]["select"] + " "      # any edit at all
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(DialectError, match="changed after the live probe"):
        approve_dialect("d4e", principal=test_principal())
    # approve the unedited probe, then edit the approved file: the run time ignores it
    draft_dialect("d4e", client=EngineClient(sqlite_exec(db)[0]))
    approve_dialect("d4e", principal=test_principal())
    assert approved_dialect("d4e") is not None                          # non-vacuous
    data = yaml.safe_load(p.read_text("utf-8"))
    data["template"]["count"] = "SELECT 1 AS n"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert approved_dialect("d4e") is None


def test_4_the_session_is_switched_to_read_only_where_the_template_says_how():
    from databridge_mcp_ingest.tabular.dialect_draft import SqlTool, read_only_session

    seen = []

    class Client:
        ro = False

        def call_tool(self, name, args):
            from databridge_mcp_ingest.client import ContentPart, ToolResult

            sql = args["sql"]
            seen.append(sql)
            if sql.startswith("SET"):
                Client.ro = True
                rows = []
            else:
                rows = [{"v": 1 if Client.ro else 0}]
            return ToolResult(parts=(ContentPart(kind="text", text=json.dumps(rows)),))

    tpl = {"read_only_session": {"sql": "SET SESSION TRANSACTION READ ONLY",
                                 "check": "SELECT @@SESSION.transaction_read_only AS v",
                                 "expect": "^1$"}}
    got = read_only_session(SqlTool(Client(), "q", "sql"), tpl)
    assert got["enforced_by"] == "database session" and seen[0].startswith("SET")
    assert read_only_session(SqlTool(Client(), "q", "sql"), {})["enforced_by"] == \
        "statement allow-list only"
    evil = {"read_only_session": {**tpl["read_only_session"], "sql": "SET ROLE admin"}}
    seen.clear()
    assert read_only_session(SqlTool(Client(), "q", "sql"), evil)["enforced_by"] == \
        "statement allow-list only" and seen == []


def test_13_a_probe_that_reads_no_table_or_skips_a_statement_is_not_approvable(tmp_path):
    from databridge_mcp_ingest.tabular.dialect_draft import (DialectError, DialectProposal,
                                                             SqlTool, approval_problems,
                                                             probe_template, template_digest)

    db = _sqlite_with_tables(tmp_path / "p.sqlite")
    tool = SqlTool(EngineClient(sqlite_exec(db)[0]), "query", "sql")
    with pytest.raises(DialectError, match="max_tables"):
        probe_template(tool, _sqlite_template(), max_tables=0)
    # a count that answers NO row is a failure, not a pass
    empty = {**_sqlite_template(), "count": "SELECT COUNT(*) AS n FROM {schema|ident}."
                                            "{table|ident} HAVING COUNT(*) < 0"}
    rep, _ = probe_template(tool, empty)
    assert "count" in rep["failed"] and "one row" in rep["queries"]["count"]["error"]
    rep, tpl = probe_template(tool, _sqlite_template())
    assert rep["unprobed"] == [] and rep["tables_probed"] >= 1        # non-vacuous
    rep["template_sha256"] = template_digest(tpl)
    good = DialectProposal(source_id="x", template=tpl, probe=rep)
    assert approval_problems(good) == []
    skipped = DialectProposal(source_id="x", template=tpl,
                              probe={**rep, "unprobed": ["aggregate"]})
    assert any("never run" in p for p in approval_problems(skipped))


# === #15 the combined statement is compared on every statistic and every class ==================
def test_15_a_combined_statement_wrong_on_one_statistic_is_rejected(tmp_path):
    from databridge_mcp_ingest.tabular.dialect_draft import SqlTool, probe_template

    db = _sqlite_with_tables(tmp_path / "c.sqlite")
    tool = SqlTool(EngineClient(sqlite_exec(db)[0]), "query", "sql")
    rep, _ = probe_template(tool, _sqlite_template())
    assert rep["queries"]["table_stats"]["ok"]                        # non-vacuous
    assert len(rep["queries"]["table_stats"]["columns_compared"]) >= 3  # int, text, date, real
    tpl = _sqlite_template()
    assert "avg_len" in tpl["table_stats_column"]
    bad = {**tpl, "table_stats_column": tpl["table_stats_column"].replace(
        "AS {p}_avg_len", "+ 1 AS {p}_avg_len")}
    assert bad["table_stats_column"] != tpl["table_stats_column"]
    rep, _ = probe_template(SqlTool(EngineClient(sqlite_exec(db)[0]), "query", "sql"), bad)
    assert not rep["queries"]["table_stats"]["ok"]
    assert "avg_len" in rep["queries"]["table_stats"]["error"]


# === #6 query-cell pointers resolve uniquely ====================================================
def _facts_store(source_id: str, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory / f"{source_id}_facts.sqlite")
    db.executescript("CREATE TABLE t (id INTEGER, v TEXT); "
                     "INSERT INTO t VALUES (1, 'a'), (2, 'b'), (3, 'c');")
    db.commit()
    db.close()


def test_6_parameters_and_row_order_are_part_of_the_pointer_identity(facts_env, tmp_path):
    import hashlib

    from databridge_mcp_ingest.tabular.count import FactsQueryError, count_query

    d = tmp_path / "facts"
    _facts_store("q6", d)
    a = count_query("q6", "SELECT v FROM t WHERE id = ?", params=(1,))
    b = count_query("q6", "SELECT v FROM t WHERE id = ?", params=(2,))
    assert a.rows != b.rows
    assert a.pointers[0].query_hash != b.pointers[0].query_hash
    assert a.params_sha256 and a.params_sha256[:12] in a.citation
    # an ordered, parameterless read keeps the statement-only identity (a re-run resolves it);
    # R-022 f2 #8: the order must be PROVEN unique (rowid / a declared key), `id` here is not
    o = count_query("q6", "SELECT id, v FROM t ORDER BY rowid")
    assert o.pointers[0].query_hash == hashlib.sha256(o.sql.encode()).hexdigest()
    # an unordered multi-row read binds its ordinals to the result it indexes into
    u = count_query("q6", "SELECT id, v FROM t")
    assert not u.ordered and u.pointers[0].query_hash != hashlib.sha256(u.sql.encode()).hexdigest()
    with pytest.raises(FactsQueryError, match="duplicate column"):
        count_query("q6", "SELECT v, v FROM t")


def test_6_count_query_reads_a_given_facts_directory(facts_env, tmp_path):
    from databridge_mcp_ingest.tabular.count import FactsQueryError, count_query

    other = tmp_path / "elsewhere"
    _facts_store("q6d", other)
    with pytest.raises(FactsQueryError):
        count_query("q6d", "SELECT COUNT(*) FROM t")                   # not in the default dir
    assert count_query("q6d", "SELECT COUNT(*) FROM t", facts_dir=other).scalar == 3


# === #7 unknown values stay unknown ==============================================================
def test_7_an_unanswered_count_is_unknown_and_blocks_publication(facts_env, tmp_path):
    from test_s2_units import _big_table, _stage

    from databridge_mcp_ingest.tabular.facts import build_facts

    src = tmp_path / "src.sqlite"
    _big_table(src, 30)
    runner, policy, profiles = _stage(src, {})
    assert profiles[0]["row_count"] == 30                               # non-vacuous
    runner.dialect = {**runner.dialect, "count": "SELECT 1 AS n WHERE 1 = 0"}
    from databridge_mcp_ingest.tabular.profiling import profile_source

    runner.combined = False
    [p] = profile_source(runner, [("public", "orders")], policy)
    assert p["row_count"] is None and p["measure_problems"]
    man = build_facts("unmeasured_src", runner, [p], policy)
    assert man["status"] == "refused" and "UNMEASURED" in man["refused"]


def test_7_non_row_elements_and_opaque_values_are_not_silent():
    from databridge_mcp_ingest.tabular.pyrepr import ReprParseError, loads_rows

    with pytest.raises(ReprParseError, match="not rows"):
        loads_rows("[{'a': 1}, 'stray']")
    stats: dict = {}
    rows = loads_rows("[{'a': <memory at 0x7f00aa>, 'b': 2}]", stats=stats)
    assert rows == [{"a": None, "b": 2}] and stats["opaque"] == 1


# === #10 / #18 facts integrity and the aggregated column catalogue ==============================
def test_10_a_count_mismatch_is_refused_unless_a_person_forces_it(facts_env, tmp_path):
    from test_s2_units import _big_table, _stage

    from databridge_mcp_ingest.tabular.facts import build_facts

    src = tmp_path / "src.sqlite"
    _big_table(src, 40)
    runner, policy, profiles = _stage(src, {})
    profiles[0]["row_count"] = 41                                       # measured != copied
    man = build_facts("mm_src", runner, profiles, policy)
    assert man["status"] == "refused" and "INCONSISTENT" in man["refused"]
    assert build_facts("mm_src", runner, profiles, policy,
                       force=test_principal())["status"] == "ok"           # f2 #5: a person


def test_10_rows_measured_but_none_copied_is_refused(facts_env, tmp_path):
    from test_s2_units import _big_table, _stage

    from databridge_mcp_ingest.tabular.facts import build_facts

    src = tmp_path / "src.sqlite"
    _big_table(src, 25)
    runner, policy, profiles = _stage(src, {})
    runner.dialect = {**runner.dialect, "select_page": "SELECT * FROM {schema|ident}."
                      "{table|ident} WHERE 1 = 0 ORDER BY {order_by} LIMIT {limit|int}"}
    man = build_facts("zero_src", runner, profiles, policy, force=test_principal())
    assert man["status"] == "refused" and "none reached the facts store" in man["refused"]


def test_10_aggregated_rows_count_toward_the_shrink_check_and_18_are_catalogued(facts_env,
                                                                                 tmp_path):
    from test_s2_units import _big_table, _stage

    from databridge_mcp_ingest.tabular.count import describe_facts
    from databridge_mcp_ingest.tabular.facts import build_facts

    src = tmp_path / "src.sqlite"
    _big_table(src, 240)
    runner, policy, profiles = _stage(src, {"facts": {"row_cap": 100}})
    first = build_facts("agg10", runner, profiles, policy)
    assert first["tables"][0]["mode"] == "aggregated" and first["rows_total"] == 240
    cols = {c["column"] for t in describe_facts("agg10")["tables"] for c in t["columns"]}
    assert {"city", "row_count", "sum_amount"} <= cols
    _big_table(src, 110)                                    # still aggregated, shrank by >50%
    runner, policy, profiles = _stage(src, {"facts": {"row_cap": 100}})
    second = build_facts("agg10", runner, profiles, policy)
    assert second["status"] == "refused" and "SHRUNK" in second["refused"]


# === #2 / #11 pipeline: review-held prose; durable, incremental stages ===========================
_PROSE = ("The quarterly review found that the delivery team met every milestone it set for "
          "itself, and the customer asked for a follow up meeting next month.")


def _prose_source(tmp_path: Path, source_id: str) -> Path:
    from databridge_mcp_ingest import register_connection
    from databridge_mcp_ingest.plans import CrawlPlan, SqlSpec, StepSpec, save_plan

    db = tmp_path / f"{source_id}.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    con.executemany("INSERT INTO notes VALUES (?, ?)", [(i, f"{_PROSE} ({i})") for i in range(4)])
    con.commit()
    con.close()
    register_connection({"id": source_id, "transport": {"type": "stdio", "command": "uvx"}})
    save_plan(CrawlPlan(source_id=source_id, status="approved", origin="manual", steps=[
        StepSpec(id="t", strategy="tabular", sql=SqlSpec(tool="query", dialect="sqlite"))]))
    return db


def test_2_review_band_prose_is_held_until_an_owner_approves_that_content(facts_env, tmp_path,
                                                                          monkeypatch, ws_vocab):
    from databridge_mcp_ingest.screen import ScreenResult, approve_override
    from databridge_mcp_ingest.tabular import cards as cards_mod
    from databridge_mcp_ingest.tabular import pipeline

    db = _prose_source(tmp_path, "p2")
    real_screen = pipeline.screen_text                    # R-022 f2 #4: cards are screened too
    monkeypatch.setattr(pipeline, "screen_text", lambda text, cfg: ScreenResult(
        "review", 1.0, [{"rule": "test_signal"}], [], "", "one signal", "approve")
        if _PROSE[:40] in text else real_screen(text, cfg))
    indexed: list = []
    monkeypatch.setattr(cards_mod, "index_items", lambda items, sid: indexed.extend(items) or {
        "indexed": len(items), "indexed_ids": [i["item_id"] for i in items]})
    rep = pipeline.run_tabular("p2", client=EngineClient(sqlite_exec(db)[0]), vocab=ws_vocab)
    assert rep["prose_held_for_review"] == 1
    assert [i["kind"] for i in indexed] == ["schema_card"]              # no prose indexed
    run_dir = Path(pipeline.source_dir("p2")) / rep["run_id"]
    [held] = [json.loads(x) for x in (run_dir / "cards.held.jsonl").read_text().splitlines()]
    approve_override("p2", held["item_id"], digest=held["digest"], principal=test_principal(),
                     reason="reviewed: no personal data")
    indexed.clear()
    rep = pipeline.run_tabular("p2", client=EngineClient(sqlite_exec(db)[0]), vocab=ws_vocab)
    assert rep["prose_held_for_review"] == 0 and "prose" in [i["kind"] for i in indexed]


def test_11_a_failing_stage_is_recorded_on_disk_before_the_error_propagates(facts_env, tmp_path,
                                                                            monkeypatch,
                                                                            ws_vocab):
    from databridge_mcp_ingest.tabular import pipeline

    db = _prose_source(tmp_path, "p11")

    def boom(self, **kw):
        raise RuntimeError("facts exploded")
    monkeypatch.setattr(pipeline.TabularStage, "facts", boom)
    with pytest.raises(RuntimeError, match="facts exploded"):
        pipeline.run_tabular("p11", client=EngineClient(sqlite_exec(db)[0]), vocab=ws_vocab,
                             index=False)
    [run_dir] = [d for d in Path(pipeline.source_dir("p11")).iterdir() if d.is_dir()]
    stages = [json.loads(x) for x in (run_dir / "tabular.stages.jsonl").read_text().splitlines()]
    assert [(s["stage"], s["status"]) for s in stages] == [("profile", "ok"), ("mapping", "ok"),
                                                           ("facts", "failed")]
    assert (run_dir / "profile.json").is_file()                        # the stage before it
    report = json.loads((run_dir / "tabular.report.json").read_text())
    assert report["status"] == "failed" and report["failed_stage"] == "facts"


def test_11_an_identical_rerun_neither_republishes_nor_reindexes(facts_env, tmp_path, monkeypatch,
                                                                 ws_vocab):
    from databridge_mcp_ingest.tabular import cards as cards_mod
    from databridge_mcp_ingest.tabular import pipeline
    from databridge_mcp_ingest.tabular.facts import load_manifest

    db = _prose_source(tmp_path, "p11i")
    indexed: list = []
    monkeypatch.setattr(cards_mod, "index_items", lambda items, sid: indexed.extend(items) or {
        "indexed": len(items), "indexed_ids": [i["item_id"] for i in items]})   # f2 #12
    pipeline.run_tabular("p11i", client=EngineClient(sqlite_exec(db)[0]), vocab=ws_vocab)
    first = load_manifest("p11i")
    assert indexed and first["status"] == "ok"                          # non-vacuous
    n = len(indexed)
    rep = pipeline.run_tabular("p11i", client=EngineClient(sqlite_exec(db)[0]), vocab=ws_vocab)
    assert len(indexed) == n                                            # nothing re-indexed
    assert load_manifest("p11i")["extracted_at"] == first["extracted_at"]  # not re-published
    assert any("unchanged" in str(x) for x in rep["notes"])
    con = sqlite3.connect(db)
    con.execute("UPDATE notes SET body = body || ' amended' WHERE id = 1")
    con.commit()
    con.close()
    pipeline.run_tabular("p11i", client=EngineClient(sqlite_exec(db)[0]), vocab=ws_vocab)
    assert len(indexed) > n                                            # the change IS indexed
    assert load_manifest("p11i")["content_sha256"] != first["content_sha256"]


# === #12 / #14 drift probing: approval, scope, max_items, dialect quoting ========================
class _CannedSql:
    """A SQL tool answering the mysql template's catalogue from a fixed shape, recording SQL."""

    supports_resources = False
    supports_resource_templates = False

    def __init__(self, tables):
        self.tables, self.sql = tables, []

    def list_tools(self):
        from databridge_mcp_ingest.client import ToolInfo

        return [ToolInfo(name="query", description="Run a read-only SQL query",
                         input_schema={"type": "object", "properties": {"sql": {"type": "string"}}})]

    def call_tool(self, name, args):
        from databridge_mcp_ingest.client import ContentPart, ToolResult

        sql = args["sql"]
        self.sql.append(sql)
        if "information_schema.tables" in sql:
            rows = [{"table_schema": "db", "table_name": t} for t in self.tables]
        elif "information_schema.columns" in sql:
            rows = [{"column_name": "id", "data_type": "int"},
                    {"column_name": "name", "data_type": "text"}]
        elif "constraint" in sql.lower() or "key_column_usage" in sql:
            rows = [{"column_name": "id", "constraint_type": "PRIMARY KEY"}]
        else:
            rows = [{"id": 1, "name": "x"}]
        return ToolResult(parts=(ContentPart(kind="text", text=json.dumps(rows)),))


def _mysql_plan(status="approved"):
    from databridge_mcp_ingest.plans import CrawlPlan, SqlSpec, StepSpec

    return CrawlPlan(source_id="dr", status=status, origin="manual", steps=[
        StepSpec(id="t", strategy="tabular", sql=SqlSpec(tool="query", dialect="mysql"))])


def _conn(**scope):
    from databridge_mcp_ingest.connections import SourceConnection

    return SourceConnection.model_validate({"id": "dr", "transport": {"type": "stdio",
                                                                      "command": "uvx"},
                                            "scope": scope})


def test_12_a_drift_probe_needs_an_approved_plan_and_honours_max_items(walnut_env):
    from databridge_mcp_ingest.drift import DriftProbeRefused, probe_shape

    client = _CannedSql(["a", "b", "c"])
    with pytest.raises(DriftProbeRefused):
        probe_shape(_conn(), _mysql_plan("proposed"), client)
    assert client.sql == []                                            # nothing read
    shape = probe_shape(_conn(max_items=2), _mysql_plan(), client)
    assert sorted(shape.tables) == ["db.a", "db.b"] and [x["uri"] for x in shape.tables_out_of_scope] == ["db.c"]


def test_12_profile_tables_scope_checks_every_ref_before_any_sql(walnut_env):
    from databridge_mcp_ingest import register_connection
    from databridge_mcp_ingest.drift import DriftProbeRefused, profile_tables
    from databridge_mcp_ingest.plans import save_plan

    register_connection({"id": "dr", "transport": {"type": "stdio", "command": "uvx"},
                         "scope": {"tables_exclude": ["db.secret*"]}})
    save_plan(_mysql_plan())
    client = _CannedSql(["ok", "secret_pay"])
    with pytest.raises(DriftProbeRefused, match="secret_pay"):
        profile_tables("dr", ["db.ok", "db.secret_pay"], client=client)
    assert client.sql == []
    assert profile_tables("dr", ["db.ok"], client=client)                # non-vacuous


def test_14_the_sample_order_uses_the_dialects_identifier_quote(walnut_env):
    from databridge_mcp_ingest.drift import probe_shape

    client = _CannedSql(["a"])
    probe_shape(_conn(), _mysql_plan(), client)
    samples = [s for s in client.sql if "LIMIT" in s.upper() and "`a`" in s]
    assert samples and all("ORDER BY `id`" in s for s in samples)


# === #17 / #19 subject marking and the unobserved inventory =====================================
def test_17_a_removed_table_carrying_a_subject_identifier_is_subject_touching(ws_vocab):
    from databridge_mcp_ingest.drift import SourceShape, diff, mark_subject, table_shape

    ident = ws_vocab.identifiers_for("person")[0]
    old = SourceShape(source_id="s", tables={
        "db.contacts": table_shape("db.contacts", [{"name": "id"}, {"name": ident}], ["id"], []),
        "db.notes": table_shape("db.notes", [{"name": "id"}, {"name": "body"}], ["id"], [])})
    new = SourceShape(source_id="s", tables={})
    drifts = mark_subject(diff(old, new), vocab=ws_vocab)
    by = {d.table: d for d in drifts}
    assert by["db.contacts"].subject_touching and not by["db.notes"].subject_touching


def test_19_an_inventory_with_no_file_item_is_unobserved_not_empty(tmp_path):
    from databridge_mcp_ingest.drift import shape_from_run

    run = tmp_path / "run"
    run.mkdir()
    (run / "inventory.jsonl").write_text("", encoding="utf-8")
    shape = shape_from_run(run, "s")
    assert shape.file_types is None and shape.mimes is None


# === #8 / #9 the writer is generic and never renders a credential ===============================
def test_8_the_writer_module_names_nothing_its_spec_declares():
    import databridge_mcp_ingest.tabular.writer as w
    from databridge_mcp_ingest.tabular.writer import load_writer_spec

    spec = load_writer_spec()
    words = {spec[k] for k in ("action", "database", "table", "role", "reader_role")} | \
        {c["name"] for c in spec["columns"]}
    src = Path(w.__file__).read_text(encoding="utf-8")
    assert words and not [x for x in words if x in src]


def test_9_the_ddl_carries_no_credential_and_the_password_comes_from_the_environment(
        monkeypatch):
    from databridge_mcp_ingest.tabular.writer import (WriterSpecError, apply_writer_password,
                                                      load_writer_spec, writer_role_sql)

    spec = load_writer_spec()
    ddl = writer_role_sql(spec)
    statements = [x for x in ddl.splitlines() if not x.startswith("--")]
    assert statements and not [x for x in statements if "PASSWORD" in x.upper()]
    assert "CREATE ROLE" in ddl
    monkeypatch.delenv(spec["password_env"], raising=False)
    with pytest.raises(WriterSpecError, match="not set"):
        apply_writer_password(lambda s: None, spec)
    monkeypatch.setenv(spec["password_env"], "s3cr'et")
    sent: list = []
    assert apply_writer_password(sent.append, spec) is None
    # R-022 f2 #6: a SCRAM verifier is sent, never the password itself
    assert len(sent) == 1 and "s3cr" not in sent[0] and "SCRAM-SHA-256$4096:" in sent[0]
    with pytest.raises(WriterSpecError):
        writer_role_sql({**spec, "password_env": "literal-password!"})

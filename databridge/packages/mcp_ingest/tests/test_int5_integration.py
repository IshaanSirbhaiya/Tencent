"""INT5 — final integration pass before end-to-end run 3. One test (or more) per item that lands in
mcp_ingest; each asserts the rule the code enforces, with a non-vacuous check beside it."""
from __future__ import annotations

import json

import pytest

from test_fxi4_tabular_drift import facts_env  # noqa: F401  (fixture)


# --- item 1: the read-only guarantee comes from what the DATABASE answers -----------------------
def _session_client(*, accepts_set: bool, server_read_only: bool, seen: list):
    """A SQL tool modelled on postgres-mcp: in restricted mode the server wraps every statement in
    `BEGIN TRANSACTION READ ONLY` and its validator refuses any SET; unrestricted, SET works."""
    from databridge_mcp_ingest.client import ContentPart, ToolResult

    state = {"ro": server_read_only}

    class Client:
        def call_tool(self, name, args):
            sql = args["sql"]
            seen.append(sql)
            if sql.upper().startswith("SET"):
                if not accepts_set:
                    return ToolResult(parts=(ContentPart(
                        kind="text", text=f"Error: Error validating query: {sql}"),))
                state["ro"] = True
                return ToolResult(parts=(ContentPart(kind="text", text="[]"),))
            rows = [{"transaction_read_only": "on" if state["ro"] else "off"}]
            return ToolResult(parts=(ContentPart(kind="text", text=json.dumps(rows)),))

    return Client()


def _pg_session():
    from databridge_mcp_ingest.tabular.dialect_draft import load_templates

    tpl = next(t for t in load_templates() if t.get("engine") == "postgres"
               and isinstance(t.get("read_only_session"), dict))
    return {"read_only_session": dict(tpl["read_only_session"]), "mcp": tpl.get("mcp") or {}}


@pytest.mark.parametrize("accepts_set,server_ro,verified", [
    (True, False, True),      # unrestricted server: the SET is what makes it read-only
    (False, True, True),      # restricted server: it refuses SET, but the check answers 'on'
    (False, False, False),    # refuses SET and is NOT read-only: never verified
])
def test_int5_1_read_only_is_what_the_database_answers_not_whether_set_ran(accepts_set,
                                                                            server_ro, verified):
    from databridge_mcp_ingest.tabular.dialect_draft import SqlTool, read_only_session

    tpl = _pg_session()
    seen: list = []
    got = read_only_session(SqlTool(_session_client(accepts_set=accepts_set,
                                                    server_read_only=server_ro, seen=seen),
                                    "execute_sql", "sql"), tpl)
    assert (got["enforced_by"] == "database session") is verified, got
    # Non-vacuous: the shipped statement was attempted first, and the check really ran.
    assert seen[0] == tpl["read_only_session"]["sql"]
    assert tpl["read_only_session"]["check"] in seen
    if verified and not accepts_set:
        assert "server" in got["detail"] and "refused" in got["detail"]


def test_int5_1_an_invalid_session_statement_is_still_never_run():
    from databridge_mcp_ingest.tabular.dialect_draft import SqlTool, read_only_session

    tpl = _pg_session()
    tpl["read_only_session"]["sql"] = "SET ROLE admin"
    seen: list = []
    got = read_only_session(SqlTool(_session_client(accepts_set=True, server_read_only=True,
                                                    seen=seen), "execute_sql", "sql"), tpl)
    assert got["enforced_by"] == "statement allow-list only" and seen == []


# --- item 5a: the mapping approval log is the SAME hash-chained log plans.py uses ---------------
@pytest.fixture
def ws_vocab():
    from databridge_graph.packs import deployment

    with deployment("workspace") as vocab:
        yield vocab


def _approved(source_id, vocab):
    from test_int3_integration import _approved_org

    return _approved_org(source_id, vocab)


@pytest.mark.real_approval
def test_int5_5a_mapping_approvals_are_appended_to_the_hash_chained_log(walnut_env, ws_vocab):
    from databridge_mcp_ingest.mapping import approval_log_path, approval_problem, load_mapping
    from databridge_mcp_ingest.plans import read_chained

    _approved("c1", ws_vocab)
    chained = read_chained(approval_log_path("c1"))               # verifies the whole chain
    raw = [json.loads(x) for x in approval_log_path("c1").read_text("utf-8").splitlines()]
    assert len(chained) == len(raw) == 1 and raw[0]["prev"] == "genesis" and raw[0]["hash"]
    assert approval_problem("c1", "organisation", load_mapping("c1").tables["organisation"],
                            ws_vocab) is None


@pytest.mark.real_approval
def test_int5_5a_an_edited_log_line_is_not_honoured_even_with_its_digest_recomputed(walnut_env,
                                                                                     ws_vocab):
    from databridge_mcp_ingest.mapping import (_record_digest, approval_log_path,
                                               approval_problem, load_mapping)

    _approved("c2", ws_vocab)
    p = approval_log_path("c2")
    [rec] = [json.loads(x) for x in p.read_text("utf-8").splitlines()]
    rec["principal_id"] = "Mallory"                               # who approved, rewritten
    rec["record_digest"] = _record_digest({k: v for k, v in rec.items()
                                           if k not in ("prev", "hash")})
    p.write_text(json.dumps(rec, sort_keys=True) + "\n", encoding="utf-8")
    why = approval_problem("c2", "organisation", load_mapping("c2").tables["organisation"],
                           ws_vocab)
    assert why and "log" in why


@pytest.mark.real_approval
@pytest.mark.parametrize("field,value", [("principal_id", "Mallory"),
                                         ("authenticated_by", "typed"),
                                         ("at", "1999-01-01T00:00:00+00:00")])
def test_int5_5a_the_whole_approval_record_must_match_the_logged_one(walnut_env, ws_vocab,
                                                                    field, value):
    from databridge_mcp_ingest.mapping import (approval_problem, effective_mapping, load_mapping,
                                               save_mapping)

    _approved("c3", ws_vocab)
    m = load_mapping("c3")
    tm = m.tables["organisation"]
    assert tm.approval[field] != value                                     # a real edit
    edited = tm.model_copy(update={"approval": {**tm.approval, field: value}})
    save_mapping(m.model_copy(update={"tables": {"organisation": edited}}))
    tm2 = load_mapping("c3").tables["organisation"]
    assert tm2.approval["digest"] == tm.approval["digest"]               # the digest still matches
    assert approval_problem("c3", "organisation", tm2, ws_vocab)
    assert effective_mapping(load_mapping("c3"), ws_vocab).tables["organisation"].status == \
        "proposed"


# --- item 5b: the tabular stage pins its SQL tool to the APPROVED plan ------------------------
@pytest.mark.real_approval
def test_int5_5b_every_tabular_read_goes_through_a_client_bound_to_the_approved_plan(
        facts_env, tmp_path, monkeypatch, ws_vocab):
    from test_fxi4_tabular_drift import _prose_source
    from test_w4_dialects import EngineClient, sqlite_exec

    from databridge_mcp_ingest.plans import load_plan
    from databridge_mcp_ingest.tabular import pipeline

    db = _prose_source(tmp_path, "b5")
    real = pipeline.ReadOnlyClient
    made: list = []

    def recording(inner, **kw):
        made.append(kw.get("plan"))
        return real(inner, **kw)

    monkeypatch.setattr(pipeline, "ReadOnlyClient", recording)
    rep = pipeline.run_tabular("b5", client=EngineClient(sqlite_exec(db)[0]), vocab=ws_vocab)
    assert made, "the stage built no read-only client: the assertion would be vacuous"
    approved = load_plan("b5")
    assert approved.status == "approved"
    assert all(p is not None and p.source_id == "b5" and p.approval == approved.approval
               for p in made), made
    assert rep.get("tables") or rep.get("profiles") or rep                  # the run still ran


# --- item 5c: a step tool that changed on the server since approval is a HOLD, not a failure --
def _TxtServer(tools):
    """The FXJ3 fake server, listing one text document (so a read is really attempted)."""
    from test_fxj3_sources import _Server

    from databridge_mcp_ingest import client as mc

    class S(_Server):
        def call_tool(self, name, args):
            self.calls.append((name, dict(args)))
            text = "notes.txt" if name == "list_docs" else "Some synthetic notes."
            return mc.ToolResult(parts=(mc.ContentPart(kind="text", text=text),))

    return S(tools)


@pytest.mark.real_approval
@pytest.mark.parametrize("changed_tool", ["list_docs", "read_doc"])
def test_int5_5c_a_tool_changed_since_approval_holds_the_step_for_a_re_plan(walnut_env,
                                                                            changed_tool):
    from test_fxj3_sources import _OBJ, _Server, _approved_docs, _docs_tools

    import databridge_mcp_ingest as mi
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest.client import ToolInfo
    from databridge_mcp_ingest.ledger import read_jsonl
    from databridge_mcp_ingest.runner import TOOL_CHANGED

    _approved_docs(_docs_tools())
    tools = [t if t.name != changed_tool else
             ToolInfo(changed_tool, "Now also deletes things", dict(_OBJ))
             for t in _docs_tools()]
    rep = mi.run_ingest("src", store=GraphStore(), client=_TxtServer(tools))
    from pathlib import Path

    failed = read_jsonl(Path(rep.run_dir) / "failed.jsonl")
    held = [r for r in failed if r.get("rule") == TOOL_CHANGED]
    assert held, failed                                                  # recorded as a hold
    assert all(changed_tool in r["error"] and "re-propose" in r["reverse"] for r in held)
    assert rep.status != "failed" and rep.counts.get("tool_changed") == len(held)
    assert "HELD" in rep.message and "approve" in rep.message, rep.message
    assert any(TOOL_CHANGED in str(n) for n in rep.notes)                # never silent


@pytest.mark.real_approval
def test_int5_5c_an_unchanged_server_is_not_held(walnut_env):
    from test_fxj3_sources import _Server, _approved_docs, _docs_tools

    import databridge_mcp_ingest as mi
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest.runner import TOOL_CHANGED

    _approved_docs(_docs_tools())
    rep = mi.run_ingest("src", store=GraphStore(), client=_TxtServer(_docs_tools()))
    assert not rep.counts.get("tool_changed")
    assert rep.counts.get("captured", 0) >= 1                           # the read really ran
    assert not any(TOOL_CHANGED in str(n) for n in rep.notes)


# --- item 5d: the runner reads DocBuild.graph_ids directly (FXJ2 exposes it) ------------------
def test_int5_5d_the_runner_takes_every_id_the_build_reports_and_refuses_a_build_without_them():
    from types import SimpleNamespace

    from databridge_mcp_ingest.build import DocBuild
    from databridge_mcp_ingest.runner import _graph_ids

    out = DocBuild.__new__(DocBuild)
    for f, default in DocBuild.__dataclass_fields__.items():
        setattr(out, f, default.default_factory() if callable(default.default_factory)
                else default.default)
    out.graph_ids = ["edge:anchor-1", "node:org-1", "edge:rel-2"]
    out.anchor_id = "doc:1"
    got = _graph_ids(out)
    assert set(out.graph_ids) <= set(got) and "doc:1" in got
    # a build result that does not say what it wrote is not silently read as "wrote nothing"
    with pytest.raises(AttributeError):
        _graph_ids(SimpleNamespace(anchor_id="doc:1", pointers=[]))

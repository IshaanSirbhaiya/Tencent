"""W2 (2026-10-01): an agent compiled from English is an agent the runtime will RUN.

The bug: `databridge_automation` had its own declaration generator and its own capability names
(``larkspur_files.list_dir``) while `databridge_agent_runtime` names them
``larkspur_files.tool_list_dir`` and re-checks every pair (`pair_problems`) and re-compiles it
(`compile_for_run`) before each run. A draft could compile and activate here and then be refused
at run time. The fix makes automation call the runtime's generator and build its compile-time
catalogue with the runtime's `build_catalogue`; these tests drive the whole path:

    English -> compile_english (rules only / FakeBackend model) -> save_draft(activate=True)
            -> load_agent -> compile_for_run -> run_agent (scripted planner, fake live gateway)

The live gateway below is written by hand from what the MCP servers would report — it is NOT
derived from the automation catalogue, so the runtime screens the pair against an independent
statement of what exists.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from automation_testing import PACK, catalogue, purposes, repo_with_golden
from databridge_automation import ConnectionInfo, ModelProposal, classify_tool, compile_english, \
    save_draft
from databridge_automation.declaration import GENERATOR

rt = pytest.importorskip("databridge_agent_runtime")
from databridge_agent_runtime import (  # noqa: E402
    CompileInputs,
    ConnectionAdvert,
    GatewayResult,
    ToolAdvert,
    capability_for_tool,
    compile_for_run,
    load_agent,
    pair_problems,
    run_agent,
)
from databridge_harness import FakeBackend, Harness  # noqa: E402

GOLDEN = "packs/workspace/evals/automations.jsonl"

FOLLOWUPS = ConnectionInfo(
    id="larkspur-followups", title="Larkspur follow-ups", kind="files",
    tools=(classify_tool("list_directory"), classify_tool("read_text_file"),
           classify_tool("write_file")),
    locations=("Follow-ups",), plan_status="approved")
READER = ConnectionInfo(
    id="larkspur-db", title="Larkspur CRM database", kind="database",
    tools=(classify_tool("query", read_only=True),), locations=("public.invoices",),
    aliases=("crm",), plan_status="approved")
WRITER = ConnectionInfo(
    id="larkspur_db_writer", title="Larkspur CRM agent write", kind="database",
    purpose="agent_write", writes_for=("larkspur-db",), tools=(classify_tool("execute_sql"),))

BRIEFING = ("Brief me on a customer before a call: open invoices in the CRM, and the notes in "
            "Larkspur follow-ups.")
OVERDUE = ("Every weekday at 9am, find invoices in the CRM more than 14 days overdue, draft a "
           "polite reminder into the Follow-ups folder, and log a follow-up task in the CRM. Ask "
           "me before logging anything; just tell me after drafts are saved.")


class LiveGateway:
    """What the three MCP servers report, stated independently of the automation catalogue."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def advertise(self, connection: str) -> ConnectionAdvert:
        if connection == "larkspur-followups":
            return ConnectionAdvert(connection=connection, declared_in="var/sources/fu.yaml",
                                    tools=(ToolAdvert("list_directory", writes=False),
                                           ToolAdvert("read_text_file", writes=False),
                                           ToolAdvert("write_file", writes=True)))
        if connection == "larkspur-db":
            return ConnectionAdvert(connection=connection, declared_in="var/sources/db.yaml",
                                    tools=(ToolAdvert("query", writes=False),), sql=True,
                                    sql_tool="query")
        if connection == "larkspur_db_writer":
            return ConnectionAdvert(connection=connection, declared_in="var/sources/dbw.yaml",
                                    tools=(ToolAdvert("execute_sql", writes=True),))
        return ConnectionAdvert(connection=connection, reachable=False, problem="unknown")

    def call_tool(self, connection: str, tool: str, arguments: dict) -> GatewayResult:
        self.calls.append(("call_tool", connection, tool, dict(arguments)))
        if tool == "write_file":
            return GatewayResult(text=f"wrote {arguments.get('path')}")
        if tool == "execute_sql":
            return GatewayResult(text="INSERT 0 1")
        return GatewayResult(text="[]")

    def query(self, connection: str, sql: str) -> GatewayResult:
        self.calls.append(("query", connection, sql))
        return GatewayResult(text=json.dumps([{"invoice": "INV-1042", "customer": "Northwind",
                                               "days_overdue": 21}]))

    def writes(self) -> list[tuple]:
        return [c for c in self.calls if c[0] == "call_tool" and c[2] in ("write_file",
                                                                          "execute_sql")]


class _Retriever:
    def search(self, *a, **k):                                    # never called by these scripts
        return []


class _NoFacts:
    def available(self, _conn: str) -> bool:
        return False


def _cat():
    return catalogue(FOLLOWUPS, READER, WRITER, golden=GOLDEN)


def _model(agent_id: str, title: str) -> Harness:
    proposal = ModelProposal.model_validate({"kind": "agent", "title": title,
                                             "agent_id": agent_id,
                                             "instructions": ["Do what the request says."]})
    return Harness(FakeBackend(json.dumps(proposal.model_dump())), cache=None)


def _compile(text: str, root: Path, harness=None):
    res = compile_english(text, _cat(), harness=harness, purposes=purposes(), repo_root=root)
    assert res.spec is not None, [q.text for q in res.open_questions]
    assert res.open_questions == [], [q.text for q in res.open_questions]
    # the runtime wrote it — no fallback, no "declaration builder failed" note
    assert res.declaration_source == GENERATOR
    assert not any("declaration builder failed" in n or "generator refused" in n
                   for n in res.notes), res.notes
    assert res.compilation.compiled, (res.compilation.refusals, res.compilation.reason)
    # every capability is the runtime's name for a spec tool (derived, not written down)
    want = [c for c in (capability_for_tool(t) for t in res.spec.tools) if c is not None]
    assert want and yaml.safe_load(res.declaration_yaml)["capabilities"] == list(dict.fromkeys(want))
    return res


def _activate_and_load(res, tmp_path: Path):
    agents = tmp_path / "agents"
    save_draft(res, activate=True, agents_dir=agents)
    spec, decl = load_agent(res.spec.agent, agents)
    assert spec.status == "active"
    return spec, decl, agents


def _runtime_accepts(spec, decl, root: Path, gateway: LiveGateway):
    assert pair_problems(spec, decl) == []
    from databridge_graph.store import GraphStore
    decision, problems = compile_for_run(
        spec, decl, store=GraphStore(), retriever=_Retriever(), gateway=gateway,
        inputs=CompileInputs(purposes=purposes(), repo_root=root), facts=_NoFacts())
    assert problems == [], problems
    assert decision.outcome.value == "compiled", [(r.code.value, r.detail)
                                                  for r in decision.refusals]
    return decision


def _run(spec, decl, root, gateway, script, tmp_path):
    from databridge_graph.store import GraphStore
    state = {"i": 0}

    def respond(model, messages, guided_json):
        last = messages[-1]["content"]
        obs = json.loads(last[len("Observations:\n"):]) if isinstance(last, str) and \
            last.startswith("Observations:\n") else []
        turn = script(state["i"], obs)
        state["i"] += 1
        return json.dumps(turn)

    harness = Harness(FakeBackend(respond, tokens_in=5, tokens_out=5),
                      config={"routing": {"agent": "planner"},
                              "aliases": {"planner": {"model": "fake/planner"}},
                              "profiles": {"cloud": {}}}, profile="cloud", cache=None)
    return run_agent(spec, decl, store=GraphStore(), harness=harness, gateway=gateway,
                     retriever=_Retriever(), trigger={"type": "manual", "by": "dana"},
                     runs_dir=tmp_path / "runs", capture_dir=tmp_path / "capture",
                     compile_inputs=CompileInputs(purposes=purposes(), repo_root=root),
                     notifier=lambda _e: None, facts=_NoFacts())


def _refs(obj) -> list[str]:
    if isinstance(obj, dict):
        out = [obj["ref"]] if isinstance(obj.get("ref"), str) else []
        out += [x for x in obj.get("refs") or [] if isinstance(x, str)]
        for k, v in obj.items():
            if k not in ("ref", "refs"):
                out += _refs(v)
        return out
    if isinstance(obj, list):
        return [r for v in obj for r in _refs(v)]
    return []


# ---------------------------------------------------------------------------------------------
# (a) the read-only briefing — offline rules path and FakeBackend model path
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["rules", "model"])
def test_briefing_compiled_from_english_runs(tmp_path, path):
    root = repo_with_golden(tmp_path)
    harness = _model("customer_call_briefing", "Customer call briefing") if path == "model" \
        else None
    res = _compile(BRIEFING, root, harness)
    assert res.model_used == (path == "model")
    assert res.spec.mode == "read_only"
    spec, decl, _ = _activate_and_load(res, tmp_path)
    gw = LiveGateway()
    _runtime_accepts(spec, decl, root, gw)

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": "source.query:larkspur-db",
                                    "args": {"sql": "SELECT * FROM invoices"}}]}
        return {"final": {"claims": []}}

    rec = _run(spec, decl, root, gw, script, tmp_path)
    assert rec.status != "refused", rec.answer.markdown
    assert not any(s.get("reason_code") == "agent_not_runnable" for s in rec.coverage.skipped)
    assert [c[:2] for c in gw.calls] == [("query", "larkspur-db")]      # it really read
    assert gw.writes() == []


# ---------------------------------------------------------------------------------------------
# (b) the read-write overdue automation: write_file on larkspur-followups, execute_sql on the
#     declared write connection — both declared actions reach the runtime's action gate
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["rules", "model"])
def test_overdue_automation_compiled_from_english_runs(tmp_path, path):
    root = repo_with_golden(tmp_path)
    harness = _model("overdue_invoice_followups", "Overdue invoice follow-ups") \
        if path == "model" else None
    res = _compile(OVERDUE, root, harness)
    assert res.model_used == (path == "model")
    by_tool = {a.tool: a for a in res.spec.actions}
    assert set(by_tool) == {"source.write:larkspur-followups:write_file",
                            "source.write:larkspur_db_writer:execute_sql"}
    assert by_tool["source.write:larkspur_db_writer:execute_sql"].automation_mode == \
        "human_required"
    spec, decl, _ = _activate_and_load(res, tmp_path)
    assert {a.name for a in decl.actions} == {a.name for a in spec.actions}
    gw = LiveGateway()
    _runtime_accepts(spec, decl, root, gw)

    draft = next(a for a in spec.actions if a.tool.endswith(":write_file"))
    log = next(a for a in spec.actions if a.tool.endswith(":execute_sql"))

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": "source.query:larkspur-db",
                                    "args": {"sql": "SELECT * FROM invoices WHERE overdue"}}]}
        if i == 1:
            src = _refs(obs)[:1]
            return {"actions": [
                {"name": draft.name, "args": {"path": "/Follow-ups/INV-1042.txt",
                                              "content": "A polite reminder."},
                 "derived_from": src},
                {"name": log.name, "args": {"sql": "INSERT INTO tasks (invoice) "
                                                   "VALUES ('INV-1042')"},
                 "derived_from": src}]}
        return {"final": {"claims": []}}

    rec = _run(spec, decl, root, gw, script, tmp_path)
    assert rec.status != "refused", rec.answer.markdown
    status = {a.name: a.status for a in rec.actions}
    assert set(status) == {draft.name, log.name}, rec.coverage.reasons
    assert status[log.name] == "proposed"                        # human_required: waits
    # human_notified ran through the gateway; the SQL write did not
    assert [w[1:3] for w in gw.writes()] == [("larkspur-followups", "write_file")]


# ---------------------------------------------------------------------------------------------
# negative: the runtime still refuses a mismatched pair (and the old naming IS a mismatch)
# ---------------------------------------------------------------------------------------------
def test_declaration_in_the_old_naming_is_refused_at_run_time(tmp_path):
    root = repo_with_golden(tmp_path)
    res = _compile(OVERDUE, root)
    spec, decl, agents = _activate_and_load(res, tmp_path)
    path = agents / f"{spec.agent}.agent.yaml"
    text = path.read_text(encoding="utf-8")
    # what the removed automation generator wrote: `<conn>.<tool>` and `<conn>.query`
    old = text.replace(".tool_", ".").replace("larkspur_db.sql", "larkspur_db.query")
    assert old != text
    path.write_text(old, encoding="utf-8", newline="\n")
    spec2, decl2 = load_agent(spec.agent, agents)
    problems = pair_problems(spec2, decl2)
    # every problem is a pairing mismatch: a tool the declaration does not grant, or (agent
    # runtime R-021 #10) the old-named grant no workflow tool uses
    assert any("does not grant" in p for p in problems)
    assert all("does not grant" in p or "which no workflow tool uses" in p for p in problems)
    gw = LiveGateway()
    rec = _run(spec2, decl2, root, gw, lambda i, obs: {"final": {"claims": []}}, tmp_path)
    assert rec.status == "refused" and gw.calls == []


def test_undeclared_action_is_refused_at_run_time(tmp_path):
    root = repo_with_golden(tmp_path)
    res = _compile(OVERDUE, root)
    spec, decl, agents = _activate_and_load(res, tmp_path)
    data = yaml.safe_load((agents / f"{spec.agent}.agent.yaml").read_text(encoding="utf-8"))
    dropped = next(iter(data["actions"].pop(0)))
    (agents / f"{spec.agent}.agent.yaml").write_text(yaml.safe_dump(data, sort_keys=False),
                                                     encoding="utf-8", newline="\n")
    spec2, decl2 = load_agent(spec.agent, agents)
    assert any(dropped in p and "not declared" in p for p in pair_problems(spec2, decl2))


def test_catalogue_names_are_the_runtimes(tmp_path):
    """The compile-time catalogue IS the runtime's `build_catalogue` over the same tools, so a
    capability granted here is one `compile_for_run` will find advertised."""
    from databridge_agent_runtime import build_catalogue
    from databridge_automation import to_adapter_catalogue
    ours = to_adapter_catalogue(_cat())
    theirs, problems = build_catalogue(
        pack=PACK, connections=[LiveGateway().advertise(c.id) for c in (FOLLOWUPS, READER, WRITER)],
        graph=True, retrieval=True, facts=[READER.id])
    assert problems == []
    for sid in ("larkspur_followups", "larkspur_db", "larkspur_db_writer", "graph", "retrieve",
                "facts"):
        a, b = ours.get(sid), theirs.get(sid)
        assert a is not None and b is not None, sid
        assert (a.reads, a.writes) == (b.reads, b.writes), sid

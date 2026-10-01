"""EF1 — E2E-2026-10-01 F-01 and F-10 (wiki/40-reviews/R-020).

F-01: an agent compiled from English and saved `activatable` was refused at every run.
F-10: the compiler classified postgres-mcp's unannotated tools by name (reads) while the runtime
called them writes, so the compiler GRANTED what the runtime REFUSED.

Here BOTH sides classify the same raw tool list — the one a live `tools/list` returns, written once
below exactly as postgres-mcp and the filesystem server publish it (no annotations) — through
their own real code paths: the compiler through `load_connection_info` over on-disk declarations,
the runtime through `McpSourceGateway.advertise` over a stub MCP client. The expectation of which
tools are reads is DERIVED from the shared rule, never listed.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from automation_testing import catalogue, purposes, repo_with_golden
from databridge_automation import compile_english, save_draft
from databridge_automation.catalogue import classify_tool, load_connection_info

rt = pytest.importorskip("databridge_agent_runtime")
from databridge_agent_runtime import (  # noqa: E402
    CompileInputs,
    GatewayResult,
    McpSourceGateway,
    classify_mcp_tool,
    classify_tools,
    compile_for_run,
    load_agent,
    run_agent,
)
from databridge_harness import FakeBackend, Harness  # noqa: E402

GOLDEN = "packs/workspace/evals/automations.jsonl"

#: What the two MCP servers publish (names only; neither sets readOnlyHint / destructiveHint).
PG_TOOLS = ["list_schemas", "list_objects", "get_object_details", "explain_query",
            "analyze_workload_indexes", "analyze_query_indexes", "analyze_db_health",
            "get_top_queries", "execute_sql"]
FS_TOOLS = ["read_text_file", "read_bytes", "list_directory", "write_file", "move_file"]

DB_PLAN = {"source_id": "larkspur_db", "status": "approved", "profiles": ["postgres-mcp"],
           "steps": [{"id": "postgres-mcp", "strategy": "tabular", "roots": None, "list": None,
                      "read": None, "sql": {"tool": "execute_sql", "arg": "sql"}}],
           "tools_seen": PG_TOOLS, "deny_tools": []}
FS_PLAN = {"source_id": "larkspur-files", "status": "approved",
           "steps": [{"id": "fs", "strategy": "documents", "roots": None,
                      "list": {"tool": "list_directory"},
                      "read": {"text_tool": "read_text_file", "binary_tool": "read_bytes"},
                      "sql": None}],
           "tools_seen": FS_TOOLS, "deny_tools": []}


def _raw(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, description="", read_only=None, destructive=None,
                           input_schema={})


def _write_sources(root: Path) -> Path:
    src = root / "sources"
    src.mkdir(parents=True)
    for cid, kind, title, tools, plan in (
            ("larkspur_db", "database", "Larkspur CRM database", PG_TOOLS, DB_PLAN),
            ("larkspur-files", "files", "Larkspur files", FS_TOOLS, FS_PLAN)):
        (src / f"{cid}.yaml").write_text(yaml.safe_dump(
            {"id": cid, "title": title, "kind": kind, "purpose": "ingest",
             "scope": {"include": ["Contracts/**"], "deny_tools": []}}), encoding="utf-8")
        (src / f"{cid}.tools.yaml").write_text(yaml.safe_dump(
            {"tools": [{"name": n, "read_only": None, "destructive": None} for n in tools]}),
            encoding="utf-8")
        (src / f"{cid}.plan.yaml").write_text(yaml.safe_dump(plan), encoding="utf-8")
    return src


class LiveGateway(McpSourceGateway):
    """The runtime's REAL `advertise` (shared classifier, approved-plan evidence) over a stub MCP
    client that lists the raw tools above; reads answer from a tiny synthetic corpus."""

    def __init__(self) -> None:
        self._root, self._open = None, {}
        self._conns = SimpleNamespace(
            load_connection=lambda c, r: SimpleNamespace(purpose="ingest",
                                                         scope=SimpleNamespace(deny_tools=[])),
            connection_path=lambda c, r: Path(f"var/sources/{c}.yaml"))
        self.calls: list[tuple] = []

    def _plan(self, connection):
        raw = DB_PLAN if connection == "larkspur_db" else FS_PLAN
        steps = []
        for s in raw["steps"]:
            tools = [t for t in ((s.get("list") or {}).get("tool"),
                                 (s.get("read") or {}).get("text_tool"),
                                 (s.get("read") or {}).get("binary_tool"),
                                 (s.get("sql") or {}).get("tool")) if t]
            steps.append(SimpleNamespace(tools=lambda t=tools: list(t),
                                         sql=(SimpleNamespace(tool=s["sql"]["tool"],
                                                              arg=s["sql"]["arg"], extra_args={})
                                              if s.get("sql") else None)))
        return SimpleNamespace(status=raw["status"], steps=steps, deny_tools=[], profiles=[])

    def _client(self, connection):
        names = PG_TOOLS if connection == "larkspur_db" else FS_TOOLS

        def call_tool(tool, args):
            self.calls.append(("call_tool", connection, tool, dict(args)))
            text = ("[{'schema_name': 'public'}]" if tool != "read_text_file"
                    else "Pellucid MSA. Payment terms: net 30 days.")
            return SimpleNamespace(text=text, is_error=False, first_blob=lambda: None)

        return SimpleNamespace(list_tools=lambda: [_raw(n) for n in names], call_tool=call_tool)


class _NoFacts:
    def available(self, _conn: str) -> bool:
        return False


class _Retriever:
    def search(self, *a, **k):
        return []


# ------------------------------------------------------------------------------------------------
# F-10 — one classifier: the compiler and the runtime agree tool by tool
# ------------------------------------------------------------------------------------------------
def test_f10_compiler_and_runtime_classify_the_same_tools_the_same_way(tmp_path):
    src = _write_sources(tmp_path)
    gw = LiveGateway()
    for cid in ("larkspur_db", "larkspur-files"):
        compiler = {t.name: t.write for t in load_connection_info(
            src / f"{cid}.yaml", ingest_root=tmp_path / "ingest").tools}
        runtime = {t.name: t.writes for t in gw.advertise(cid).tools}
        assert compiler == runtime, cid
        assert any(not w for w in runtime.values()) and any(runtime.values())   # non-vacuous


def test_f10_postgres_mcp_reads_are_reads_and_unknowns_stay_writes():
    """Derived from the rule: a READ_VERB name with no write/exec verb is a read; `execute_sql`
    (an EXEC verb, nothing vouching) and `analyze_*_indexes` (no read verb) are writes — unless an
    approved plan routes `execute_sql` as its SQL step."""
    from databridge_agent_runtime.classify import reads_by_name, writes_by_name
    for name in PG_TOOLS:
        want_read = reads_by_name(name) and not writes_by_name(name)
        assert classify_tool(name).write is (not want_read), name
        assert classify_mcp_tool(name).writes is (not want_read), name
    reads = {n for n in PG_TOOLS if not classify_tool(n).write}
    assert {"list_schemas", "list_objects", "get_object_details", "analyze_db_health",
            "get_top_queries", "explain_query"} <= reads
    assert classify_tool("execute_sql").write and classify_tool("frobnicate").write
    assert not classify_tool("execute_sql", approved_read=True).write
    assert classify_tool("list_schemas", denied=True).write               # a deny list wins
    (only,) = classify_tools([_raw("list_schemas")], write_only=True)
    assert only.writes                                                     # agent_write connection


# ------------------------------------------------------------------------------------------------
# F-01 — a draft compiled `activatable` for a DB + files agent RUNS without a capability refusal
# ------------------------------------------------------------------------------------------------
def test_f01_compiled_db_and_files_agent_runs_without_capability_refusal(tmp_path):
    src = _write_sources(tmp_path)
    conns = [load_connection_info(src / f"{c}.yaml", ingest_root=tmp_path / "ingest")
             for c in ("larkspur_db", "larkspur-files")]
    root = repo_with_golden(tmp_path)
    res = compile_english("Brief me on a customer before a call: what the Larkspur CRM database "
                          "holds about them, and the contract in Larkspur files.",
                          catalogue(*conns, golden=GOLDEN), purposes=purposes(), repo_root=root)
    assert res.spec is not None and res.open_questions == [], [q.text for q in res.open_questions]
    assert res.activatable and res.compilation.compiled, (res.compilation.reason,
                                                          res.compilation.refusals)
    read_tools = [t for t in res.spec.tools if t.startswith("source.")]
    assert {t.split(":")[1] for t in read_tools} == {"larkspur_db", "larkspur-files"}
    agents = tmp_path / "agents"
    save_draft(res, activate=True, agents_dir=agents)
    spec, decl = load_agent(res.spec.agent, agents)

    gw = LiveGateway()
    from databridge_graph.store import GraphStore
    decision, problems = compile_for_run(
        spec, decl, store=GraphStore(), retriever=_Retriever(), gateway=gw,
        inputs=CompileInputs(purposes=purposes(), repo_root=root), facts=_NoFacts())
    assert problems == [] and decision.outcome.value == "compiled", (
        problems, [(r.code.value, r.detail) for r in decision.refusals])

    def args_for(tool: str) -> dict:
        if tool.startswith("source.query:"):
            return {"sql": "SELECT 1"}
        return {"path": "/Contracts/pellucid_msa.txt"}

    state = {"i": 0}

    def respond(model, messages, guided_json):
        state["i"] += 1
        if state["i"] == 1:
            return json.dumps({"tool_calls": [{"tool": t, "args": args_for(t)}
                                              for t in read_tools]})
        return json.dumps({"final": {"claims": []}})

    harness = Harness(FakeBackend(respond), config={"routing": {"agent": "planner"},
                                                    "aliases": {"planner": {"model": "fake/p"}},
                                                    "profiles": {"cloud": {}}},
                      profile="cloud", cache=None)
    rec = run_agent(spec, decl, store=GraphStore(), harness=harness, gateway=gw,
                    retriever=_Retriever(), trigger={"type": "manual", "by": "dana"},
                    runs_dir=tmp_path / "runs", capture_dir=tmp_path / "capture",
                    compile_inputs=CompileInputs(purposes=purposes(), repo_root=root),
                    notifier=lambda _e: None, facts=_NoFacts())
    assert rec.status == "completed", rec.answer.markdown
    refused = [s.policy_decision for s in rec.steps if s.policy_decision["decision"] != "permit"]
    assert refused == [], refused
    assert {c[1] for c in gw.calls} == {"larkspur_db", "larkspur-files"}   # it really read both

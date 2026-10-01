"""Codex review t02 (wiki/40-reviews/R-017) + §5.8 traceability + the S2 facts.query contract.

Every test names the finding it proves. Expectations are derived (from the harness config, the
evidence the fake sources return, the rule under test) — never a recalled answer.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent_runtime_helpers import (HARNESS_CONFIG, FakeGateway, briefing_spec, followup_spec,
                                   make_pair, person, refs_in, save_pair, scripted_harness)

from databridge_agent_runtime import (
    PLANNER_PROMPT_VERSION,
    ActionLog,
    ActionRefused,
    GatewayResult,
    McpSourceGateway,
    approve,
    classify_tools,
    pending_actions,
    run_agent,
    sql_is_read_only,
)
from databridge_agent_runtime.capture import document_identity
from databridge_agent_runtime.runs import actions_path, find_run


@pytest.fixture
def dirs(tmp_path):
    return {"runs_dir": tmp_path / "runs", "capture_dir": tmp_path / "capture"}


@pytest.fixture
def decide(tmp_path, compile_inputs, dirs):
    return {**dirs, "agents_dir": tmp_path / "agents", "compile_inputs": compile_inputs}


def _run(spec, decl, script, *, store, gateway, compile_inputs, dirs, question=None, **kw):
    harness, backend = scripted_harness(script)
    notes: list[dict] = []
    rec = run_agent(spec, decl, store=store, harness=harness, gateway=gateway,
                    trigger={"type": "manual", "by": "dana", "question": question},
                    compile_inputs=compile_inputs, notifier=notes.append, **dirs, **kw)
    return rec, backend, notes


def _events(rec, dirs):
    return ActionLog(actions_path(find_run(rec.run_id, dirs["runs_dir"]))).events()


SQL_WRITE = "INSERT INTO tasks (invoice) VALUES ('INV-1042')"


def _log_task_script(i, obs):
    if i == 0:
        return {"tool_calls": [{"tool": "source.query:larkspur-db",
                                "args": {"sql": "SELECT * FROM invoices WHERE overdue"}}]}
    if i == 1:
        return {"actions": [{"name": "log_followup_task", "args": {"sql": SQL_WRITE},
                             "derived_from": refs_in(obs)[:1]}]}
    return {"final": {"claims": []}}


def _proposed(tmp_path, store, gateway, compile_inputs, dirs, decide):
    spec, decl = save_pair(decide["agents_dir"], followup_spec(), save_followup_draft=False)
    rec, _, _ = _run(spec, decl, _log_task_script, store=store, gateway=gateway,
                     compile_inputs=compile_inputs, dirs=dirs)
    assert [a.status for a in rec.actions] == ["proposed"]
    return rec


# ================================================================================================
# finding 1 — a write disguised as an MCP read; deny by default
# ================================================================================================
def _tool(name, read_only=None, destructive=None):
    return SimpleNamespace(name=name, read_only=read_only, destructive=destructive,
                           description="")


def test_classification_is_deny_by_default_and_uses_the_shared_rule():
    pytest.importorskip("databridge_mcp_ingest.plans")
    from databridge_mcp_ingest.plans import writes_by_name
    tools = [_tool("add_observations"),                      # server-memory: a write by name
             _tool("delete_record", read_only=True),         # the hint is a claim; name wins
             _tool("frobnicate"),                            # nothing vouches for it (no hint,
                                                             # no plan, no READ_VERB — E2E F-10)
             _tool("list_directory"),                        # vouched for by an approved plan
             _tool("read_text_file", read_only=True),        # vouched for by the server
             _tool("search", read_only=True),                # vouched, but on a deny list
             _tool("get_thing", read_only=True, destructive=True)]
    got = {t.name: t.writes for t in classify_tools(
        tools, approved_reads=["list_directory"], denied=["search"])}
    assert writes_by_name("add_observations")                 # the shared rule says so
    assert got == {"add_observations": True, "delete_record": True, "frobnicate": True,
                   "list_directory": False, "read_text_file": False, "search": True,
                   "get_thing": True}


class _StubGateway(McpSourceGateway):
    """The real `advertise`, over a stub client, plan and connection (no MCP process)."""

    def __init__(self, tools, plan):
        self._tools, self._stub_plan, self._root, self._open = tools, plan, None, {}
        self._conns = SimpleNamespace(
            load_connection=lambda c, r: SimpleNamespace(scope=SimpleNamespace(deny_tools=[])),
            connection_path=lambda c, r: Path(f"var/sources/{c}.yaml"))

    def _client(self, connection):
        return SimpleNamespace(list_tools=lambda: self._tools)

    def _plan(self, connection):
        return self._stub_plan


def _plan(status, tools, sql_tool=None):
    step = SimpleNamespace(tools=lambda: list(tools) + ([sql_tool] if sql_tool else []),
                           sql=SimpleNamespace(tool=sql_tool) if sql_tool else None)
    return SimpleNamespace(status=status, steps=[step], deny_tools=[])


@pytest.mark.parametrize("name", ["createFolder", "moveFile", "deleteFile", "updateSheetValues"])
def test_camel_case_write_tools_are_never_reads(name):
    """Drive-style names carry no separator for a verb rule to match. Unannotated, they are
    writes by DENY BY DEFAULT (no name rule needed); annotated ``readOnlyHint: true``, the
    camelCase-split name still exposes the verb."""
    pytest.importorskip("databridge_mcp_ingest.plans")
    (bare,) = classify_tools([_tool(name)])
    assert bare.writes
    (claimed,) = classify_tools([_tool(name, read_only=True)])
    assert claimed.writes
    (read,) = classify_tools([_tool("listFiles", read_only=True)])       # a real read still reads
    assert not read.writes


def test_mcp_gateway_advertises_unvouched_tools_as_writes():
    pytest.importorskip("databridge_mcp_ingest.plans")
    # `frobnicate` carries no read verb (E2E F-10 made a READ_VERB name read evidence, shared with
    # the compiler), so only the approved plan can vouch for it
    tools = [_tool("add_observations"), _tool("frobnicate"), _tool("execute_sql")]
    approved = _StubGateway(tools, _plan("approved", ["frobnicate"], sql_tool="execute_sql"))
    adv = approved.advertise("mem")
    assert {t.name: t.writes for t in adv.tools} == {
        "add_observations": True, "frobnicate": False, "execute_sql": False}
    assert adv.sql and adv.sql_tool == "execute_sql"
    proposed = _StubGateway(tools, _plan("proposed", ["frobnicate"])).advertise("mem")
    assert all(t.writes for t in proposed.tools)          # an unapproved plan vouches for nothing


def test_read_only_agent_cannot_reach_add_observations(store, compile_inputs, dirs):
    """End to end: server-memory's `add_observations` is never a read, whatever the model asks."""
    pytest.importorskip("databridge_mcp_ingest.plans")

    class Memory(FakeGateway):
        def advertise(self, connection):
            if connection != "larkspur-files":
                return super().advertise(connection)
            from databridge_agent_runtime import ConnectionAdvert
            return ConnectionAdvert(connection=connection, declared_in="x", tools=classify_tools(
                [_tool("read_text_file", read_only=True), _tool("add_observations")]))

    gw = Memory()
    spec, decl = make_pair(briefing_spec(tools=["graph.search", "source.read:larkspur-files"]))
    script = lambda i, o: ({"tool_calls": [{"tool": "source.read:larkspur-files:add_observations",  # noqa: E731
                                            "args": {"observations": ["x"]}}]}
                           if i == 0 else {"final": {"claims": []}})
    rec, _, _ = _run(spec, decl, script, store=store, gateway=gw, compile_inputs=compile_inputs,
                     dirs=dirs)
    assert [c for c in gw.calls if c[0] == "call_tool"] == []
    assert rec.steps[0].policy_decision["reason_code"] == "not_an_advertised_read"


# ================================================================================================
# finding 2 — the SQL read-only check is an allow-list
# ================================================================================================
@pytest.mark.parametrize("sql", [
    "SELECT setval('invoice_seq', 999)",                    # codex's example
    "SELECT nextval('invoice_seq')",
    "select pg_advisory_lock(1)",
    "SELECT lo_import('/etc/passwd')",
    "SELECT dblink_exec('dbname=x', 'DELETE FROM t')",
    "SELECT set_config('role', 'admin', false)",
    "SELECT * FROM invoices FOR UPDATE",
    "SELECT * FROM invoices FOR NO KEY UPDATE",
    "SELECT * INTO backup FROM invoices",
    "select '--', setval('s', 1)",                          # comment marker inside a string
    "SELECT E'\\'', setval('s', 1), ''",                     # backslash escape
    "SELECT $$'$$, setval('s', 1), $$'$$",                  # dollar quoting
    "SELECT public.lower(name) FROM customers",             # an allow-listed name, shadowable
    "SELECT \"setval\"('s', 1)",
    "SELECT 1 /*! ; DELETE FROM t */",
    "EXPLAIN ANALYZE DELETE FROM invoices",
    "SELECT 1; SELECT 2",
    "with x as (delete from t returning *) select * from x",
])
def test_side_effecting_selects_are_refused(sql):
    ok, why = sql_is_read_only(sql)
    assert not ok and why, sql


@pytest.mark.parametrize("sql", [
    "SELECT 1", "SELECT 1;", "select 'delete me' as note",
    "SELECT count(*), sum(amount) FROM invoices WHERE days_overdue > 14",
    "with x as (select 1) select * from x",
    "SELECT coalesce(a, 0) FROM t WHERE b IN (1, 2) AND EXISTS (SELECT 1 FROM u)",
    "SELECT lower(name) FROM customers -- trailing comment",
    "SELECT owner, comment, start FROM tasks ORDER BY start",
    "SELECT CAST(amount AS numeric(10, 2)) FROM invoices",
    "SELECT customer, row_number() OVER (PARTITION BY customer ORDER BY due) FROM invoices",
])
def test_ordinary_reads_still_pass(sql):
    assert sql_is_read_only(sql) == (True, ""), sql


def test_sql_cannot_be_smuggled_through_a_read_tool(store, compile_inputs, dirs):
    class WithSqlTool(FakeGateway):
        def advertise(self, connection):
            adv = super().advertise(connection)
            if connection == "larkspur-db":
                return type(adv)(connection=connection, declared_in=adv.declared_in,
                                 tools=adv.tools, sql=True, sql_tool="query")
            return adv

    gw = WithSqlTool()
    spec, decl = make_pair(briefing_spec(tools=[
        "graph.search", "source.read:larkspur-db:query",
        "source.read:larkspur-files:read_text_file"]))

    sent = [("source.read:larkspur-db:query", {"sql": "SELECT 1"}),
            ("source.read:larkspur-db:query", {"sql": "DELETE FROM invoices WHERE 1 = 1"}),
            ("source.read:larkspur-files:read_text_file",
             {"path": "DELETE FROM invoices WHERE 1 = 1"})]

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": t, "args": a} for t, a in sent]}
        return {"final": {"claims": []}}

    rec, _, _ = _run(spec, decl, script, store=store, gateway=gw, compile_inputs=compile_inputs,
                     dirs=dirs)
    # nothing reaches a server tool unchecked: the SQL tool's statement goes through the checked
    # query path (E2E run 4 Q-02) - a read-only one runs there, anything else is refused there
    assert [c for c in gw.calls if c[0] == "call_tool"] == []
    want = ["granted" if t.endswith(":query") and sql_is_read_only(a["sql"])[0]
            else "sql_not_read_only" for t, a in sent]
    assert "granted" in want and "sql_not_read_only" in want              # non-vacuous
    assert [s.policy_decision["reason_code"] for s in rec.steps] == want
    assert [c[2] for c in gw.calls if c[0] == "query"] == \
        [a["sql"] for t, a in sent if t.endswith(":query") and sql_is_read_only(a["sql"])[0]]


# ================================================================================================
# finding 3 — the approver is an authenticated principal
# ================================================================================================
def test_approval_takes_a_principal_object_never_a_name(tmp_path, store, gateway,
                                                        compile_inputs, dirs, decide):
    rec = _proposed(tmp_path, store, gateway, compile_inputs, dirs, decide)
    for claim in ("Dana Lee", "", None):
        with pytest.raises(ActionRefused):
            approve(rec.run_id, "a1", claim, gateway=gateway, **decide)
    assert gateway.writes() == []
    # R-021 #5 (Codex batch 3): a duck-typed object, or an Approver a caller built by hand with a
    # made-up mechanism, is a CLAIM — refused. Only an authenticator-issued principal decides.
    from databridge_agent_runtime import Approver, register_authenticator
    for forged in (SimpleNamespace(principal_id="analyst-7", dev_mode=False),
                   Approver("analyst-7", "made-up"), Approver("analyst-7", "test-idp")):
        with pytest.raises(ActionRefused, match="not a principal|not issued"):
            approve(rec.run_id, "a1", forged, gateway=gateway, **decide)
    assert gateway.writes() == []
    issued = register_authenticator("test-idp").issue("analyst-7")
    after = approve(rec.run_id, "a1", issued, gateway=gateway, **decide)
    assert (after.actions[0].status, after.actions[0].by) == ("executed", "analyst-7")


# ================================================================================================
# finding 4 — action values the evidence does not hold
# ================================================================================================
def _draft_script(content):
    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": "source.query:larkspur-db",
                                    "args": {"sql": "SELECT * FROM invoices WHERE overdue"}}]}
        if i == 1:
            return {"actions": [{"name": "save_followup_draft",
                                 "args": {"path": "/Follow-ups/northwind.txt", "content": content},
                                 "derived_from": refs_in(obs)[:1]}]}
        return {"final": {"claims": []}}
    return script


def test_an_automatic_action_with_a_guessed_value_is_refused(store, gateway, compile_inputs,
                                                             dirs):
    spec, decl = make_pair(followup_spec(), save_followup_draft=False)
    evidence = gateway.query("larkspur-db", "SELECT 1").text            # what the query returns
    gateway.calls.clear()
    guessed = "77777"
    assert guessed not in evidence
    rec, _, _ = _run(spec, decl, _draft_script(f"Please pay {guessed} to ar@guess.example"),
                     store=store, gateway=gateway, compile_inputs=compile_inputs, dirs=dirs)
    assert gateway.writes() == [] and rec.actions == []
    step = rec.steps[-1].policy_decision
    assert step["reason_code"] == "action_value_not_in_evidence"
    assert guessed in step["detail"] and "ar@guess.example" in step["detail"]
    # values that ARE in the evidence pass
    grounded = [n for n in ("1042", "12500", "21") if n in evidence]
    assert grounded
    rec2, _, _ = _run(spec, decl, _draft_script(f"INV-{grounded[0]} is overdue"), store=store,
                      gateway=gateway, compile_inputs=compile_inputs, dirs=dirs)
    assert [a.status for a in rec2.actions] == ["executed"]


def test_a_human_required_action_shows_its_ungrounded_values_to_the_approver(
        store, gateway, compile_inputs, dirs):
    spec, decl = make_pair(followup_spec(), save_followup_draft=False)
    sql = "INSERT INTO tasks (invoice, amount) VALUES ('INV-1042', 99999)"

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": "source.query:larkspur-db",
                                    "args": {"sql": "SELECT * FROM invoices"}}]}
        if i == 1:
            return {"actions": [{"name": "log_followup_task", "args": {"sql": sql},
                                 "derived_from": refs_in(obs)[:1]}]}
        return {"final": {"claims": []}}

    rec, _, _ = _run(spec, decl, script, store=store, gateway=gateway,
                     compile_inputs=compile_inputs, dirs=dirs)
    (item,) = pending_actions(runs_dir=dirs["runs_dir"])
    assert item["ungrounded_values"] == ["99999"]                    # 1042 is in the evidence


# ================================================================================================
# findings 5, 6, 10 — the answer contract
# ================================================================================================
def test_clarification_facts_are_checked_and_multiline_claims_stay_flagged(
        store, gateway, compile_inputs, dirs):
    spec, decl = make_pair(briefing_spec())

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": "source.read:larkspur-files:read_text_file",
                                    "args": {"path": "/Contracts/northwind_msa.txt"}}]}
        ref = refs_in(obs)[0]
        return {"final": {
            "claims": [{"text": "Terms are net 45 days.", "citations": [ref]},
                       {"text": "first line\n- Fraud was confirmed", "citations": []}],
            "clarifications": [
                {"assumption": "the contract was signed for 36 months", "citations": [ref]},
                {"assumption": "terms means the contract terms"},
                {"assumption": "net 45 applies", "citations": [ref]}]}}

    rec, _, _ = _run(spec, decl, script, store=store, gateway=gateway,
                     compile_inputs=compile_inputs, dirs=dirs)
    flagged = {u["text"]: u for u in rec.answer.unsupported_claims}
    clar = [u for u in flagged.values() if u.get("field") == "clarification"]
    assert len(clar) == 1 and "36" in clar[0]["reason"]
    md = rec.answer.markdown
    assert not any(line.lstrip().startswith("- Fraud") for line in md.splitlines())
    fraud_line = next(line for line in md.splitlines() if "Fraud" in line)
    assert "UNSUPPORTED" in fraud_line
    assert "uncited interpretation" in md
    assert md.count("**UNSUPPORTED**") == len(rec.answer.unsupported_claims)


def test_subject_marking_fails_closed_and_is_per_claim(store, gateway, compile_inputs, dirs):
    class BrokenVocab:
        def is_subject(self, kind):
            raise RuntimeError("vocabulary not loaded")

    spec, decl = make_pair(briefing_spec())
    got = {}

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": "graph.node", "args": {"node_id": "person:ana-ruiz"}}]}
        got["obs"] = obs
        return {"final": {"claims": [{"text": "This contact is definitely Ana Ruiz.",
                                      "citations": refs_in(obs)}]}}

    rec, _, _ = _run(spec, decl, script, store=store, gateway=gateway,
                     compile_inputs=compile_inputs, dirs=dirs, vocabulary=BrokenVocab())
    node = got["obs"][0]["result"]["node"]
    assert node["candidate"] and node["requires_human_selection"]
    assert any("vocabulary" in r for r in rec.coverage.reasons)
    line = next(line for line in rec.answer.markdown.splitlines() if "Ana Ruiz" in line)
    assert "CANDIDATE" in line


# ================================================================================================
# findings 7, 8, 9 — approvals: once, under current authority, ledger always consistent
# ================================================================================================
def test_concurrent_approvals_execute_once(tmp_path, store, compile_inputs, dirs, decide):
    class Slow(FakeGateway):
        def call_tool(self, connection, tool, arguments):
            if tool == "execute_sql":
                time.sleep(0.3)
            return super().call_tool(connection, tool, arguments)

    gw = Slow()
    rec = _proposed(tmp_path, store, gw, compile_inputs, dirs, decide)
    results: list = []
    barrier = threading.Barrier(2)

    def go(pid):
        barrier.wait()
        try:
            results.append(approve(rec.run_id, "a1", person(pid), gateway=gw, **decide))
        except ActionRefused as e:
            results.append(e)

    threads = [threading.Thread(target=go, args=(p,)) for p in ("dana.lee", "lee.chan")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(gw.writes()) == 1                                   # exactly one write
    assert sum(isinstance(r, ActionRefused) for r in results) == 1
    kinds = [e["event"] for e in _events(rec, dirs)]
    assert kinds.count("approved") == 1 and kinds.count("executed") == 1


def test_approval_rechecks_current_authority(tmp_path, store, gateway, compile_inputs, dirs,
                                             decide):
    rec = _proposed(tmp_path, store, gateway, compile_inputs, dirs, decide)
    decl_path = decide["agents_dir"] / "overdue_invoice_followup.agent.yaml"
    original = decl_path.read_text(encoding="utf-8")
    data = yaml.safe_load(original)
    data["budget"]["escalations_per_day"] += 1                     # any reviewed change
    decl_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ActionRefused, match="changed since it was proposed"):
        approve(rec.run_id, "a1", person(), gateway=gateway, **decide)
    decl_path.write_text(original, encoding="utf-8")

    class WriterGone(FakeGateway):
        def advertise(self, connection):
            if connection == "larkspur_db_writer":
                return type(super().advertise("larkspur-db"))(
                    connection=connection, reachable=False, problem="revoked")
            return super().advertise(connection)

    with pytest.raises(ActionRefused, match="no longer compiles"):
        approve(rec.run_id, "a1", person(), gateway=WriterGone(), **decide)
    decl_path.unlink()
    with pytest.raises(ActionRefused, match="can no longer be loaded"):
        approve(rec.run_id, "a1", person(), gateway=gateway, **decide)
    assert gateway.writes() == []
    events = _events(rec, dirs)
    assert [e["event"] for e in events if e["event"] != "decision_refused"] == ["proposed"]
    # ... and each refusal is itself on the record (ACT / R-021)
    assert [e["reason_code"] for e in events if e["event"] == "decision_refused"] ==         ["authority_changed"] * 3


def test_a_write_that_succeeded_is_recorded_even_if_capture_fails(
        tmp_path, store, gateway, compile_inputs, dirs, decide, monkeypatch):
    rec = _proposed(tmp_path, store, gateway, compile_inputs, dirs, decide)
    import databridge_agent_runtime.runtime as rt

    def broken_capture(**_kw):
        raise OSError("capture store unavailable")

    monkeypatch.setattr(rt, "capture_live_read", broken_capture)
    after = approve(rec.run_id, "a1", person(), gateway=gateway, **decide)
    assert after.actions[0].status == "executed"
    executed = [e for e in _events(rec, dirs) if e["event"] == "executed"]
    assert len(executed) == 1 and "capture store unavailable" in executed[0]["capture_error"]
    with pytest.raises(ActionRefused):
        approve(rec.run_id, "a1", person(), gateway=gateway, **decide)
    assert len(gateway.writes()) == 1


def test_an_unknown_outcome_is_never_retried_but_a_reported_failure_is(
        tmp_path, store, compile_inputs, dirs, decide):
    class Flaky(FakeGateway):
        mode = "timeout"

        def call_tool(self, connection, tool, arguments):
            if tool == "execute_sql" and self.mode == "timeout":
                self.calls.append(("call_tool", connection, tool, dict(arguments)))
                raise TimeoutError("no reply")
            if tool == "execute_sql" and self.mode == "error":
                self.mode = "ok"
                return GatewayResult(is_error=True, text="relation tasks is locked")
            return super().call_tool(connection, tool, arguments)

    gw = Flaky()
    rec = _proposed(tmp_path, store, gw, compile_inputs, dirs, decide)
    with pytest.raises(ActionRefused, match="unknown"):
        approve(rec.run_id, "a1", person(), gateway=gw, **decide)
    gw.mode = "ok"
    with pytest.raises(ActionRefused, match="unknown"):
        approve(rec.run_id, "a1", person(), gateway=gw, **decide)
    assert len(gw.writes()) == 1                                   # the one uncertain attempt

    gw2 = Flaky()
    gw2.mode = "error"
    rec2 = _proposed(tmp_path, store, gw2, compile_inputs, dirs, decide)
    with pytest.raises(ActionRefused, match="write failed"):
        approve(rec2.run_id, "a1", person(), gateway=gw2, **decide)
    after = approve(rec2.run_id, "a1", person(), gateway=gw2, **decide)   # reported: retry ok
    assert after.actions[0].status == "executed"
    assert [e["event"] for e in _events(rec2, dirs)] == [
        "proposed", "approved", "execution_failed", "retry_approved", "executed"]


# ================================================================================================
# finding 11 — live documents get the hashed full-path identity
# ================================================================================================
def test_live_read_document_identity_is_the_hashed_full_path(store, gateway, compile_inputs,
                                                             dirs):
    spec, decl = make_pair(briefing_spec())
    path = "/Contracts/northwind_msa.txt"
    script = lambda i, o: ({"tool_calls": [{"tool": "source.read:larkspur-files:read_text_file",  # noqa: E731
                                            "args": {"path": path}}]}
                           if i == 0 else {"final": {"claims": []}})
    rec, _, _ = _run(spec, decl, script, store=store, gateway=gateway,
                     compile_inputs=compile_inputs, dirs=dirs)
    (ptr,) = rec.steps[0].result_pointers
    uri = gateway.call_tool("larkspur-files", "read_text_file", {"path": path}).uri
    assert ptr["document_id"] == document_identity("larkspur-files", uri)
    assert ptr["document_id"] == hashlib.sha256(
        f"mcp://larkspur-files/{uri}".encode("utf-8")).hexdigest()      # P4, the ingest formula
    assert path not in ptr["document_id"]


# ================================================================================================
# finding 12 — retrieval exclusions are counted into coverage
# ================================================================================================
def test_retrieval_exclusions_reduce_coverage(store, gateway, compile_inputs, dirs):
    from databridge_core import SourcePointer
    from databridge_core.provenance import Trust

    def mk(src):
        return SourcePointer(source_id=src, kind="doc_span", document_id=f"{src}/d", char_start=0,
                             char_end=5, capture_digest="ab" * 32, trust=Trust.UNTRUSTED)

    class R:
        def search(self, query, *, k=8, sources=None, kinds=None, mode="hybrid"):
            return [{"text": "kept", "pointer": mk("larkspur-files")},
                    {"text": "no pointer", "pointer": None},
                    {"text": "elsewhere", "pointer": mk("other-src")}]

    spec, decl = make_pair(briefing_spec(tools=["retrieve.search"]))
    script = lambda i, o: ({"tool_calls": [{"tool": "retrieve.search", "args": {"query": "x"}}]}  # noqa: E731
                           if i == 0 else {"final": {"claims": []}})
    rec, _, _ = _run(spec, decl, script, store=store, gateway=gateway,
                     compile_inputs=compile_inputs, dirs=dirs, retriever=R())
    codes = sorted(s["reason_code"] for s in rec.coverage.skipped)
    assert codes == ["no_pointer", "out_of_scope"]
    assert "Reduced coverage" in rec.answer.markdown


# ================================================================================================
# §5.8 — which agent and which model produced each step and the answer
# ================================================================================================
def test_every_step_and_the_answer_carry_produced_by(store, gateway, compile_inputs, dirs):
    spec, decl = make_pair(briefing_spec())
    expected_model = HARNESS_CONFIG["aliases"][HARNESS_CONFIG["routing"]["agent"]]["model"]

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": "graph.search", "args": {"query": "Northwind"}},
                                   {"tool": "graph.path", "args": {"src": "a", "dst": "b"}}]}
        return {"final": {"claims": [{"text": "Northwind is a customer.",
                                      "citations": refs_in(obs)[:1]}]}}

    rec, backend, _ = _run(spec, decl, script, store=store, gateway=gateway,
                           compile_inputs=compile_inputs, dirs=dirs)
    assert rec.steps and rec.answer.produced_by
    for pb in [s.produced_by for s in rec.steps] + [rec.answer.produced_by]:
        assert pb["model"] == expected_model
        assert pb["task_class"] == "agent"
        assert (pb["agent"], pb["agent_version"], pb["run_id"]) == (spec.agent, decl.version,
                                                                    rec.run_id)
        assert pb["prompt_version"].startswith(PLANNER_PROMPT_VERSION + "+sha256:")
        assert dt.datetime.fromisoformat(pb["at"])
    assert rec.models_used == [expected_model]
    assert backend.call_count == 2


def test_a_refused_run_calls_no_model_and_lists_none(store, gateway, compile_inputs, dirs):
    spec, decl = make_pair(briefing_spec(tools=["graph.search"]))
    wider = type(spec).model_validate(briefing_spec(tools=["graph.search", "graph.node"]))
    rec, backend, _ = _run(wider, decl, lambda i, o: {"final": {"claims": []}}, store=store,
                           gateway=gateway, compile_inputs=compile_inputs, dirs=dirs)
    assert rec.status == "refused" and backend.call_count == 0 and rec.models_used == []


# ================================================================================================
# contract change (S2): facts.query answers counting / listing by exact SQL
# ================================================================================================
class FakeFacts:
    """A facts store shaped like `count_query`'s answer, pointers built the way it builds them."""

    def __init__(self, rows, columns, *, available=True):
        self.rows, self.columns, self._available, self.asked = rows, columns, available, []

    def available(self, connection):
        return self._available

    def query(self, connection, sql, *, params=None, question=None):
        from databridge_core import SourcePointer
        from databridge_core.provenance import SnapshotIdentity, Trust
        self.asked.append((connection, sql, params, question))
        qh = hashlib.sha256(sql.encode()).hexdigest()
        snap = SnapshotIdentity(source_id=connection, as_of=dt.datetime(2026, 9, 30,
                                                                        tzinfo=dt.timezone.utc),
                                content_hash="c" * 64, schema_hash="s" * 64, query_hash=qh)
        ptrs = [SourcePointer(source_id=connection, kind="query_cell", snapshot=snap,
                              query_hash=qh, row_ordinal=i, column=c, capture_digest="c" * 64,
                              trust=Trust.UNTRUSTED)
                for i in range(len(self.rows)) for c in self.columns]
        return SimpleNamespace(columns=self.columns, rows=self.rows, row_count=len(self.rows),
                               truncated=False, extract_date="2026-09-30T00:00:00+00:00",
                               citation=f"[SQL] {sql} — facts store, extracted 2026-09-30",
                               pointers=ptrs)


def _facts_script(sql, claim_from_value):
    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": "facts.query:larkspur-db",
                                    "args": {"sql": sql, "question": "how many overdue?"}}]}
        res = obs[0].get("result") or {}
        cells = [c for row in res.get("rows", []) for c in row]
        return {"final": {"claims": [{"text": claim_from_value(cells),
                                      "citations": [c["ref"] for c in cells if c["ref"]]}]}}
    return script


def test_counting_is_answered_by_facts_query_with_citation(store, gateway, compile_inputs, dirs):
    facts = FakeFacts([[3]], ["n"])
    spec, decl = make_pair(briefing_spec(tools=["facts.query:larkspur-db"]))
    sql = "SELECT count(*) AS n FROM invoices WHERE days_overdue > 14"
    rec, _, _ = _run(spec, decl, _facts_script(sql, lambda cells: f"{cells[0]['value']} invoices "
                                                                  f"are overdue."),
                     store=store, gateway=gateway, compile_inputs=compile_inputs, dirs=dirs,
                     facts=facts)
    assert rec.status == "completed" and rec.answer.unsupported_claims == []
    assert facts.asked == [("larkspur-db", sql, None, "how many overdue?")]
    (cited,) = rec.answer.citations
    assert cited["kind"] == "query_cell" and cited["source_id"] == "larkspur-db"
    assert rec.steps[0].policy_decision["capability"] == "facts.larkspur_db"


def test_facts_query_refuses_a_write_before_the_store_sees_it(store, gateway, compile_inputs,
                                                              dirs):
    facts = FakeFacts([[1]], ["n"])
    spec, decl = make_pair(briefing_spec(tools=["facts.query:larkspur-db"]))
    rec, _, _ = _run(spec, decl, _facts_script("SELECT setval('s', 1)", lambda c: "x"),
                     store=store, gateway=gateway, compile_inputs=compile_inputs, dirs=dirs,
                     facts=facts)
    assert facts.asked == []
    assert rec.steps[0].policy_decision["reason_code"] == "sql_not_read_only"


def test_an_agent_needing_a_missing_facts_store_does_not_run(store, gateway, compile_inputs,
                                                             dirs):
    spec, decl = make_pair(briefing_spec(tools=["facts.query:larkspur-db"]))
    rec, backend, _ = _run(spec, decl, lambda i, o: {"final": {"claims": []}}, store=store,
                           gateway=gateway, compile_inputs=compile_inputs, dirs=dirs,
                           facts=FakeFacts([], [], available=False))
    assert rec.status == "refused" and backend.call_count == 0
    assert any("facts store" in r for r in rec.coverage.reasons)


def test_the_real_facts_store_adapter_runs_count_query(tmp_path, monkeypatch):
    """`McpFactsStore` over the real `count_query`, against a real read-only SQLite file."""
    p = str(Path(__file__).resolve().parents[2] / "mcp_ingest")
    if p not in sys.path:
        sys.path.append(p)
    count = pytest.importorskip("databridge_mcp_ingest.tabular.count")
    facts_mod = pytest.importorskip("databridge_mcp_ingest.tabular.facts")
    from databridge_agent_runtime import McpFactsStore

    db = tmp_path / "larkspur-db_facts.sqlite"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE invoices (id TEXT, days_overdue INTEGER)")
        c.executemany("INSERT INTO invoices VALUES (?, ?)",
                      [("INV-1", 3), ("INV-2", 20), ("INV-3", 31)])
    for mod in (count, facts_mod):
        # `facts_dir` (R-022 tabular cross-track 4) is an additive keyword of both helpers
        monkeypatch.setattr(mod, "facts_path", lambda source_id, facts_dir=None: db,
                            raising=False)
    monkeypatch.setattr(count, "load_manifest", lambda source_id, facts_dir=None: {},
                        raising=False)
    store = McpFactsStore()
    assert store.available("larkspur-db")
    ans = store.query("larkspur-db", "SELECT count(*) FROM invoices WHERE days_overdue > ?",
                      params=(14,))
    with sqlite3.connect(db) as c:
        expected = c.execute("SELECT count(*) FROM invoices WHERE days_overdue > 14").fetchone()[0]
    assert ans.scalar == expected and ans.citation.startswith("[SQL]")
    assert {p.kind for p in ans.pointers} == {"query_cell"}

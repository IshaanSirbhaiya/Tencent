"""The runtime's guarantees, each driven through `run_agent` with a scripted (fake) model."""
from __future__ import annotations

import hashlib
import json
import re

import pytest
import yaml

from agent_runtime_helpers import (PACK, briefing_spec, followup_spec, governance, make_pair,
                                   person, refs_in, save_pair, scripted_harness)

from databridge_agent_runtime import (
    ActionLog,
    ActionRefused,
    run_agent,
    approve,
    compile_for_run,
    declaration_from_workflow,
    parse_declaration_text,
    pending_actions,
    reject,
    sql_is_read_only,
)
from databridge_agent_runtime.runs import actions_path, find_run, read_run
from databridge_agent_runtime.tools import PointerRegistry, ToolLayer
from databridge_agentdef import GrantedAuthority
from databridge_workflow_spec import WorkflowSpec


@pytest.fixture
def dirs(tmp_path):
    return {"runs_dir": tmp_path / "runs", "capture_dir": tmp_path / "capture"}


@pytest.fixture
def decide(tmp_path, compile_inputs, dirs):
    """kwargs for `approve`: where runs live, and where the CURRENT agent is re-read from."""
    return {**dirs, "agents_dir": tmp_path / "agents", "compile_inputs": compile_inputs}


def _run(spec, decl, script, *, store, gateway, compile_inputs, dirs, question=None, **kw):
    harness, backend = scripted_harness(script, tokens=kw.pop("tokens", 5))
    notes: list[dict] = []
    rec = run_agent(spec, decl, store=store, harness=harness, gateway=gateway,
                    trigger={"type": "manual", "by": "dana", "question": question},
                    compile_inputs=compile_inputs, notifier=notes.append, **dirs, **kw)
    return rec, backend, notes


def _step_decisions(rec):
    return [(s.tool, s.policy_decision["decision"], s.policy_decision["reason_code"])
            for s in rec.steps]


# ------------------------------------------------------------------------------------------
# read-only really is read-only
# ------------------------------------------------------------------------------------------
def test_read_only_agent_cannot_write_even_when_the_model_asks(store, gateway, compile_inputs,
                                                               dirs):
    spec, decl = make_pair(briefing_spec())
    write_tool = "source.write:larkspur-files:write_file"

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": write_tool,
                                    "args": {"path": "/x.txt", "content": "pwned"}},
                                   {"tool": "source.query:larkspur-db",
                                    "args": {"sql": "DELETE FROM invoices"}}],
                    "actions": [{"name": "save_followup_draft", "args": {"path": "/y"},
                                 "derived_from": []}]}
        return {"final": {"claims": []}}

    rec, _, _ = _run(spec, decl, script, store=store, gateway=gateway,
                     compile_inputs=compile_inputs, dirs=dirs)
    assert gateway.writes() == []                                     # nothing reached a source
    refused = [d for d in _step_decisions(rec) if d[1] == "refuse"]
    assert len(refused) == 3                                          # all three recorded
    assert {d[2] for d in refused} == {"write_is_an_action", "sql_not_read_only",
                                       "read_only_agent"}
    assert rec.actions == []
    assert len(rec.coverage.skipped) == 3
    assert "Reduced coverage" in rec.answer.markdown


def test_tool_outside_the_workflow_is_refused_and_recorded(store, gateway, compile_inputs, dirs):
    spec, decl = make_pair(briefing_spec())
    outside = "graph.path"
    assert outside not in spec.tools

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": outside, "args": {"src": "a", "dst": "b"}}]}
        return {"final": {"claims": []}}

    rec, _, _ = _run(spec, decl, script, store=store, gateway=gateway,
                     compile_inputs=compile_inputs, dirs=dirs)
    assert _step_decisions(rec) == [(outside, "refuse", "not_in_workflow")]
    assert rec.coverage.skipped[0]["tool"] == outside


def test_tool_without_a_grant_is_refused_by_the_authority(store, gateway, compile_inputs):
    """Even a tool the workflow lists is refused when the compiled grants do not cover it."""
    spec, decl = make_pair(briefing_spec(tools=["graph.search"]))
    decision, problems = compile_for_run(spec, decl, store=store, retriever=None, gateway=gateway,
                                         inputs=compile_inputs)
    assert problems == []
    authority = GrantedAuthority.from_decision(decision)
    wider = WorkflowSpec.model_validate(briefing_spec(tools=["graph.search", "graph.node"]))
    layer = ToolLayer(spec=wider, authority=authority, registry=PointerRegistry(), store=store)
    assert layer.admit("graph.search").permitted
    out = layer.call("graph.node", {"node_id": "organisation:northwind"})
    assert (out.permitted, out.reason_code) == (False, "no_grant")
    assert out.observation is None                                    # never executed


def test_agent_whose_halves_disagree_does_not_run(store, gateway, compile_inputs, dirs):
    spec, decl = make_pair(briefing_spec(tools=["graph.search"]))
    wider = WorkflowSpec.model_validate(briefing_spec(tools=["graph.search", "graph.node"]))
    rec, backend, _ = _run(wider, decl, lambda i, o: {"final": {"claims": []}}, store=store,
                           gateway=gateway, compile_inputs=compile_inputs, dirs=dirs)
    assert rec.status == "refused"
    assert backend.call_count == 0                                    # the model was never asked
    assert any("graph.node" in r for r in rec.coverage.reasons)


def test_autonomous_without_reversal_refuses_to_run(store, gateway, compile_inputs, dirs):
    spec = WorkflowSpec.model_validate(followup_spec())
    data = yaml.safe_load(declaration_from_workflow(spec, PACK,
                                                    governance(save_followup_draft=False)))
    body = data["actions"][0]["save_followup_draft"]
    body["automation"]["mode"] = "autonomous"
    body.pop("reversal", None)
    decl = parse_declaration_text(yaml.safe_dump(data, sort_keys=False), declared_in="x")
    rec, backend, _ = _run(spec, decl, lambda i, o: {"final": {"claims": []}}, store=store,
                           gateway=gateway, compile_inputs=compile_inputs, dirs=dirs)
    assert rec.status == "refused"
    assert backend.call_count == 0
    assert any("autonomous_action_not_compensable" in r for r in rec.coverage.reasons)


# ------------------------------------------------------------------------------------------
# actions
# ------------------------------------------------------------------------------------------
def _followup_script(action_name, args):
    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": "source.query:larkspur-db",
                                    "args": {"sql": "SELECT * FROM invoices WHERE overdue"}}]}
        if i == 1:
            return {"actions": [{"name": action_name, "args": args,
                                 "derived_from": refs_in(obs)[:1]}]}
        return {"final": {"claims": []}}
    return script


def test_human_required_waits_for_a_named_person(store, gateway, compile_inputs, dirs, decide):
    spec, decl = save_pair(decide["agents_dir"], followup_spec(), save_followup_draft=False)
    sql = "INSERT INTO tasks (invoice) VALUES ('INV-1042')"
    rec, _, _ = _run(spec, decl, _followup_script("log_followup_task", {"sql": sql}),
                     store=store, gateway=gateway, compile_inputs=compile_inputs, dirs=dirs)
    assert [a.status for a in rec.actions] == ["proposed"]
    assert rec.actions[0].automation_mode == "human_required"
    assert rec.actions[0].pointers                                    # derived_from recorded
    assert gateway.writes() == []                                     # not executed

    inbox = pending_actions(runs_dir=dirs["runs_dir"])
    assert [(p["run_id"], p["name"], p["args"]) for p in inbox] == \
        [(rec.run_id, "log_followup_task", {"sql": sql})]

    with pytest.raises(ActionRefused):                                # a process is not a person
        approve(rec.run_id, "a1", person("agent"), gateway=gateway, **decide)
    with pytest.raises(ActionRefused, match="not an authenticated principal"):
        approve(rec.run_id, "a1", "Dana Lee", gateway=gateway, **decide)   # a name is a claim
    assert gateway.writes() == []

    after = approve(rec.run_id, "a1", person("dana.lee"), gateway=gateway, **decide)
    assert after.actions[0].status == "executed"
    assert after.actions[0].by == "dana.lee"
    assert gateway.writes() == [("call_tool", "larkspur_db_writer", "execute_sql", {"sql": sql})]
    log = ActionLog(actions_path(find_run(rec.run_id, dirs["runs_dir"]))).events()
    events = [e["event"] for e in log if e["event"] != "decision_refused"]
    assert events == ["proposed", "approved", "executed"]
    # the two refused attempts are on the record too (ACT / R-021), before the approval
    assert [e["reason_code"] for e in log if e["event"] == "decision_refused"] ==         ["principal_not_a_person", "principal_not_authenticated"]
    with pytest.raises(ActionRefused):                                # executed once, not twice
        approve(rec.run_id, "a1", person("dana.lee"), gateway=gateway, **decide)
    assert pending_actions(runs_dir=dirs["runs_dir"]) == []


def test_rejected_action_never_executes(store, gateway, compile_inputs, dirs, decide):
    spec, decl = save_pair(decide["agents_dir"], followup_spec(), save_followup_draft=False)
    rec, _, _ = _run(spec, decl, _followup_script("log_followup_task",
                                                  {"sql": "INSERT INTO tasks VALUES (1)"}),
                     store=store, gateway=gateway, compile_inputs=compile_inputs, dirs=dirs)
    out = reject(rec.run_id, "a1", person(), reason="not now", runs_dir=dirs["runs_dir"])
    assert out.actions[0].status == "rejected"
    with pytest.raises(ActionRefused):
        approve(rec.run_id, "a1", person(), gateway=gateway, **decide)
    assert gateway.writes() == []


def test_human_notified_executes_and_notifies(store, gateway, compile_inputs, dirs):
    spec, decl = make_pair(followup_spec(), save_followup_draft=False)
    args = {"path": "/Follow-ups/northwind.txt", "content": "Friendly reminder: INV-1042"}
    rec, _, notes = _run(spec, decl, _followup_script("save_followup_draft", args),
                         store=store, gateway=gateway, compile_inputs=compile_inputs, dirs=dirs)
    assert [a.status for a in rec.actions] == ["executed"]
    # E2E run 3 P-04: the path is normalised to the connection's root before it is written
    from databridge_agent_runtime.write_paths import normalise_write_paths
    assert gateway.writes() == [("call_tool", "larkspur-files", "write_file",
                                 normalise_write_paths(args, None)[0])]
    assert len(notes) == 1 and notes[0]["name"] == "save_followup_draft"
    events = [e["event"] for e in ActionLog(actions_path(find_run(rec.run_id,
                                                                  dirs["runs_dir"]))).events()]
    assert events == ["proposed", "executed", "notified"]


def test_action_without_input_pointers_is_refused(store, gateway, compile_inputs, dirs):
    spec, decl = make_pair(followup_spec(), save_followup_draft=False)

    def script(i, obs):
        if i == 0:
            return {"actions": [{"name": "save_followup_draft",
                                 "args": {"path": "/a", "content": "b"}, "derived_from": ["p99"]}]}
        return {"final": {"claims": []}}

    rec, _, _ = _run(spec, decl, script, store=store, gateway=gateway,
                     compile_inputs=compile_inputs, dirs=dirs)
    assert rec.actions == [] and gateway.writes() == []
    assert _step_decisions(rec)[-1][2] == "action_without_input_pointers"


# ------------------------------------------------------------------------------------------
# the answer contract
# ------------------------------------------------------------------------------------------
def test_answer_citing_two_sources_passes_and_uncited_claims_are_flagged(store, gateway,
                                                                         compile_inputs, dirs):
    spec, decl = make_pair(briefing_spec())
    question = "Brief me on Northwind Traders — what are their payment terms?"
    seen: dict[str, list[str]] = {}

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [
                {"tool": "graph.search", "args": {"query": "Northwind Traders"}},
                {"tool": "source.read:larkspur-files:read_text_file",
                 "args": {"path": "/Contracts/northwind_msa.txt"}}]}
        seen["graph"] = refs_in(obs[0])
        seen["file"] = refs_in(obs[1])
        return {"final": {
            "claims": [
                {"text": "Northwind Traders has payment terms of 45 days in the CRM.",
                 "citations": seen["graph"]},
                {"text": "The MSA states net 45 days.", "citations": seen["file"]},
                {"text": "They are our biggest customer.", "citations": []},
                {"text": "They signed in 2019.", "citations": seen["file"]},
                {"text": "Their CFO is named in the contract.", "citations": ["p999"]},
            ],
            "clarifications": [{"assumption": "payment terms means the contract terms",
                                "alternatives": ["terms stored on the CRM record"]}]}}

    rec, _, _ = _run(spec, decl, script, store=store, gateway=gateway,
                     compile_inputs=compile_inputs, dirs=dirs, question=question)
    assert rec.status == "completed"
    assert seen["graph"] and seen["file"]
    reasons = {u["text"]: u["reason"] for u in rec.answer.unsupported_claims}
    assert reasons["They are our biggest customer."] == "no_citation"
    assert reasons["Their CFO is named in the contract."] == "unknown_citation"
    assert reasons["They signed in 2019."].startswith("number_not_in_cited_evidence")
    assert len(reasons) == 3
    cited_sources = {p["source_id"] for p in rec.answer.citations}
    assert cited_sources == {"larkspur-db", "larkspur-files"}         # two distinct sources
    md = rec.answer.markdown
    assert question in md                                             # verbatim
    assert md.count("**UNSUPPORTED**") == 3                           # flagged, not dropped
    assert md.index("Clarification") > md.index("The MSA states")     # after the answer
    live = [p for p in rec.answer.citations if p["source_id"] == "larkspur-files"]
    digest = live[0]["capture_digest"]
    blob = dirs["capture_dir"] / digest[:2] / digest
    assert hashlib.sha256(blob.read_bytes()).hexdigest() == digest   # captured before cited


def test_person_nodes_are_candidates_and_out_of_scope_pointers_are_withheld(
        store, gateway, compile_inputs, dirs):
    spec, decl = make_pair(briefing_spec())
    got: dict = {}

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": "graph.neighbors",
                                    "args": {"node_id": "organisation:northwind"}},
                                   {"tool": "graph.node",
                                    "args": {"node_id": "organisation:elsewhere"}}]}
        got["obs"] = obs
        return {"final": {"claims": []}}

    rec, _, _ = _run(spec, decl, script, store=store, gateway=gateway,
                     compile_inputs=compile_inputs, dirs=dirs)
    nodes = got["obs"][0]["result"]["nodes"]
    people = [n for n in nodes if n["kind"] == "person"]
    assert people and all(n["candidate"] and n["requires_human_selection"] for n in people)
    assert got["obs"][1]["result"]["node"] is None                    # another source's node
    assert any(s["reason_code"] == "out_of_scope" for s in rec.coverage.skipped)


# ------------------------------------------------------------------------------------------
# limits and budget stop the loop, and the run says why
# ------------------------------------------------------------------------------------------
def _forever(i, obs):
    return {"tool_calls": [{"tool": "graph.search", "args": {"query": f"Northwind {i}"}}]}


@pytest.mark.parametrize("limits,expect", [
    ({"max_steps": 3, "max_tool_calls": 50, "tokens_per_run": 20000}, "step limit (3)"),
    ({"max_steps": 20, "max_tool_calls": 2, "tokens_per_run": 20000}, "tool-call limit (2)"),
    ({"max_steps": 20, "max_tool_calls": 50, "tokens_per_run": 1000}, "token budget"),
])
def test_limits_stop_the_loop_and_say_why(store, gateway, compile_inputs, dirs, limits, expect):
    spec, decl = make_pair(briefing_spec(limits=limits))
    rec, backend, _ = _run(spec, decl, _forever, store=store, gateway=gateway,
                           compile_inputs=compile_inputs, dirs=dirs, tokens=300)
    assert rec.status == "stopped"
    assert any(expect in r for r in rec.coverage.reasons)
    assert expect in rec.answer.markdown
    assert len(rec.steps) <= limits["max_tool_calls"]
    assert backend.call_count <= limits["max_steps"]


def test_sql_read_only_check():
    for ok in ("SELECT 1", "with x as (select 1) select * from x", "select 'delete me' as note"):
        assert sql_is_read_only(ok)[0], ok
    for bad in ("INSERT INTO t VALUES (1)", "select 1; drop table t", "UPDATE t SET a=1",
                "with x as (delete from t returning *) select * from x", ""):
        assert not sql_is_read_only(bad)[0], bad


# ------------------------------------------------------------------------------------------
# the run record is exactly §5.7
# ------------------------------------------------------------------------------------------
#: BUILD_PLAN_KNOWLEDGE_PLATFORM.md §5.7, copied once. The expected key sets are parsed from this
#: text, not from the implementation.
CONTRACT_5_7 = """{run_id, spec_digest, trigger, started, ended, status, steps:[{tool, args, result_pointers, policy_decision, ms}],
 actions:[{name, automation_mode, status: proposed|approved|executed|rejected|reversed, by, pointers}],
 answer:{markdown, citations:[pointer…], unsupported_claims:[…]}, coverage:{searched, skipped, reasons}}"""


#: §5.8 (runtime traceability), copied once: the keys `produced_by` carries, and where it goes.
CONTRACT_5_8 = """produced_by = {agent: <spec/agent id or pipeline stage>, agent_version, task_class, model,
prompt_version, run_id, at}"""
#: §5.8 says "every step and the final answer carry `produced_by`, and the run header lists every
#: model used" — the header key for that list is `models_used`.
WIDENED_BY_5_8 = {"": {"models_used"}, "steps": {"produced_by"}, "answer": {"produced_by"}}
#: Additive widening recorded in wiki/40-reviews/R-020 (FX8 deferred #4): the answer pairs each
#: cited ref with its pointer explicitly, so no reader pairs them by list order.
WIDENED_BY_R020 = {"answer": {"citation_refs"}}
#: Additive widening recorded in wiki/40-reviews/R-020-EF4 (E2E F-33): §5.8 names the harness
#: result's `tokens` and `cost_usd`; the run header carries the run's totals of both.
WIDENED_BY_EF4 = {"": {"tokens", "cost_usd"}}
#: Additive widening recorded in wiki/40-reviews/R-021 (FED answer contract item 2): every claim
#: with its structural `requires_human_selection` flag.
WIDENED_BY_R021 = {"answer": {"claims"}}
#: Additive widening by RTF2, recorded in wiki/40-reviews/R-023-RTF2 and BUILD_PLAN §5.7 (E2E run 2
#: N-12): the run's STRUCTURAL
#: outcome — reason_code, human_confirmation_required, coverage_status, candidates, withheld,
#: not_connected — as fields an evaluator asserts (packs/workspace/evals/agent_contract.jsonl).
WIDENED_BY_RTF2 = {"": {"outcome"}}


def _produced_by_keys() -> set[str]:
    body = CONTRACT_5_8.split("{", 1)[1].rsplit("}", 1)[0]
    return {part.split(":")[0].strip() for part in " ".join(body.split()).split(",")
            if part.split(":")[0].strip()}


def _contract_keys(text: str) -> dict[str, set[str]]:
    """Top-level keys (under ``""``) and each named block's keys, read from the contract text.
    A key is the part of an entry before ``:``; placeholders (``pointer…``, ``…``) are not keys."""
    out: dict[str, set[str]] = {"": set()}
    stack: list[str] = []
    token = ""

    def flush() -> None:
        nonlocal token
        key = token.split(":")[0].strip()
        if key and "…" not in key and stack:
            out.setdefault(stack[-1], set()).add(key)
        token = ""

    for ch in text.strip():
        if ch in "{[":
            t = token.strip()
            if t.endswith(":"):                      # a named block: `steps:[`, `answer:{`
                name = t[:-1].strip()
                out.setdefault(stack[-1] if stack else "", set()).add(name)
                stack.append(name)
                token = ""
            else:                                    # `{` inside `steps:[` belongs to steps
                stack.append(stack[-1] if stack else "")
        elif ch in "}]":
            flush()
            stack.pop()
        elif ch == ",":
            flush()
        else:
            token += ch
    return out


def test_run_record_has_exactly_the_contract_keys(store, gateway, compile_inputs, dirs):
    keys = _contract_keys(CONTRACT_5_7)
    for widening in (WIDENED_BY_5_8, WIDENED_BY_R020, WIDENED_BY_EF4, WIDENED_BY_R021,
                     WIDENED_BY_RTF2):
        for block, extra in widening.items():
            keys[block] = keys[block] | extra
    for block in ("steps", "actions", "answer", "coverage"):          # the parser found them
        assert block in keys[""] and keys[block], block
    spec, decl = make_pair(followup_spec(), save_followup_draft=False)
    rec, _, _ = _run(spec, decl, _followup_script("log_followup_task", {"sql": "INSERT 1"}),
                     store=store, gateway=gateway, compile_inputs=compile_inputs, dirs=dirs)
    on_disk = json.loads(find_run(rec.run_id, dirs["runs_dir"]).read_text(encoding="utf-8"))
    assert set(on_disk) == keys[""]
    assert on_disk["steps"] and on_disk["actions"]                   # non-vacuous
    for step in on_disk["steps"]:
        assert set(step) == keys["steps"]
    for action in on_disk["actions"]:
        assert set(action) == keys["actions"]
        allowed = re.search(r"status:\s*([a-z|]+)", CONTRACT_5_7).group(1).split("|")
        assert action["status"] in allowed
    assert set(on_disk["answer"]) == keys["answer"]
    assert set(on_disk["coverage"]) == keys["coverage"]
    pb_keys = _produced_by_keys()
    assert len(pb_keys) == 7, pb_keys                                 # the parser found them
    stamped = [s["produced_by"] for s in on_disk["steps"]] + [on_disk["answer"]["produced_by"]]
    assert stamped and all(set(pb) == pb_keys for pb in stamped)
    assert read_run(find_run(rec.run_id, dirs["runs_dir"])).run_id == rec.run_id


def test_trace_receives_the_steps(store, gateway, compile_inputs, dirs):
    """Records into anything with the gateway TraceHandle's method names."""
    calls: list[str] = []

    class Handle:
        def __getattr__(self, name):
            def rec(*a, **k):
                calls.append(name)
                return self
            return rec

    spec, decl = make_pair(briefing_spec())
    script = lambda i, o: ({"tool_calls": [{"tool": "graph.search",               # noqa: E731
                                            "args": {"query": "Northwind Traders"}}]}
                           if i == 0 else {"final": {"claims": []}})
    harness, _ = scripted_harness(script)
    run_agent(spec, decl, store=store, harness=harness, gateway=gateway, trace=Handle(),
              compile_inputs=compile_inputs, **dirs)
    for kind in ("principal", "budget", "capability", "policy", "from_source_pointer", "cost",
                 "close"):
        assert kind in calls, kind
    assert calls[-1] == "close"


def test_wildcard_read_never_reaches_a_write_tool(store, gateway, compile_inputs, dirs):
    spec, decl = make_pair(briefing_spec(tools=["graph.search", "source.read:larkspur-files"]))

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [
                {"tool": "source.read:larkspur-files:write_file",
                 "args": {"path": "/x", "content": "y"}},
                {"tool": "source.read:larkspur-files:read_text_file",
                 "args": {"path": "/Contracts/northwind_msa.txt"}}]}
        return {"final": {"claims": []}}

    rec, _, _ = _run(spec, decl, script, store=store, gateway=gateway,
                     compile_inputs=compile_inputs, dirs=dirs)
    assert gateway.writes() == []
    decisions = _step_decisions(rec)
    assert decisions[0][1:] == ("refuse", "not_an_advertised_read")
    assert decisions[1][1] == "permit"


def test_action_whose_tool_the_source_does_not_offer_does_not_run(store, compile_inputs, dirs):
    from agent_runtime_helpers import FakeGateway

    class NoWrites(FakeGateway):
        def advertise(self, connection):
            adv = super().advertise(connection)
            return type(adv)(connection=adv.connection, declared_in=adv.declared_in, sql=adv.sql,
                             tools=tuple(t for t in adv.tools if not t.writes))

    spec, decl = make_pair(followup_spec(), save_followup_draft=False)
    rec, backend, _ = _run(spec, decl, lambda i, o: {"final": {"claims": []}}, store=store,
                           gateway=NoWrites(), compile_inputs=compile_inputs, dirs=dirs)
    assert rec.status == "refused" and backend.call_count == 0
    assert any("write_file" in r for r in rec.coverage.reasons)


def test_retrieval_passages_are_scoped_and_carry_their_locator(store, gateway, compile_inputs,
                                                              dirs):
    from databridge_core import SourcePointer
    from databridge_core.provenance import Trust

    class FakeRetriever:
        def __init__(self):
            self.asked = []

        def search(self, query, *, k=8, sources=None, kinds=None, mode="hybrid"):
            self.asked.append(sources)
            mk = lambda src: SourcePointer(source_id=src, kind="doc_span",   # noqa: E731
                                           document_id=f"{src}/msa.pdf", char_start=0,
                                           char_end=20, capture_digest="ab" * 32,
                                           trust=Trust.UNTRUSTED)
            return [{"chunk_id": "c1", "doc_id": "msa", "source_id": "larkspur-files",
                     "text": "Payment terms: net 45 days", "locator": "p. 3",
                     "approx_locator": True, "score": 0.9, "pointer": mk("larkspur-files")},
                    {"chunk_id": "c2", "doc_id": "x", "source_id": "other-src", "text": "nope",
                     "locator": None, "approx_locator": False, "score": 0.1,
                     "pointer": mk("other-src")}]

    retriever = FakeRetriever()
    spec, decl = make_pair(briefing_spec(tools=["retrieve.search"]))
    got = {}

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": "retrieve.search", "args": {"query": "terms"}}]}
        got["obs"] = obs
        return {"final": {"claims": [{"text": "Terms are net 45 days.",
                                      "citations": refs_in(obs)}]}}

    rec, _, _ = _run(spec, decl, script, store=store, gateway=gateway,
                     compile_inputs=compile_inputs, dirs=dirs, retriever=retriever)
    assert retriever.asked == [sorted(spec.sources)]                 # scoped, never "all"
    passages = got["obs"][0]["result"]["passages"]
    assert [p["source"] for p in passages] == ["larkspur-files"]
    assert passages[0]["locator"].startswith("approx.")
    assert rec.answer.unsupported_claims == []
    assert "approx. p. 3" in rec.answer.markdown


def test_real_trace_recorder_accepts_the_run_without_defects(store, gateway, compile_inputs,
                                                             dirs):
    """Loads the gateway's `trace_recorder.py` by path (stdlib only; the gateway package itself
    imports FastAPI) and checks the run records into it with no integrity defect."""
    import importlib.util
    from pathlib import Path

    src = Path(__file__).resolve().parents[3] / "apps" / "gateway" / "databridge_gateway" / \
        "trace_recorder.py"
    spec_ = importlib.util.spec_from_file_location("_trace_recorder_under_test", src)
    mod = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(mod)
    recorder = mod.TraceRecorder()

    spec, decl = make_pair(followup_spec(), save_followup_draft=False)
    handle = recorder.begin(surface="agents", agent=spec.agent, version="1")
    harness, _ = scripted_harness(_followup_script("log_followup_task", {"sql": "INSERT 1"}))
    run_agent(spec, decl, store=store, harness=harness, gateway=gateway, trace=handle,
              trigger={"type": "manual", "by": "Dana Lee", "question": "go"},
              compile_inputs=compile_inputs, **dirs)
    trace = recorder.get(handle.request_id)
    assert trace.ended_at is not None
    assert trace.integrity()["complete"], trace.integrity()
    kinds = {s.kind for s in trace.steps}
    assert {"principal", "budget", "capability", "policy", "pointer", "escalation",
            "cost"} <= kinds

"""E2E run 2 (wiki/40-reviews/E2E-2026-10-01-run2.md) — N-05, N-06, N-11, N-12, N-13, fixed by RTF2.

Each test drives the real code path with the SHAPE of the failing input from the run-2 evidence
(databridge/var/e2e-20261001T0122/: the overdue run's `'p1'` source_pointer and agent id, its
`INV-2026-0057.md` / day-count drafts, the briefing's `list_objects {}`, the event agent's
`{'uri': …}`, the golden set's `C-1001`, `2026-10-01` and withheld-file claims). Expectations are
derived from what the fake sources return and the rule under test — never a recalled answer — and
each fix is tested both ways: what was wrongly refused now passes, and what is truly ungrounded
still refuses. No network, no live model.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_runtime_helpers import (FakeGateway, briefing_spec, followup_spec, make_pair, person,
                                   refs_in, save_pair)

from databridge_agent_runtime import (
    ConnectionAdvert,
    GatewayResult,
    PointerRegistry,
    ToolAdvert,
    approve,
    pending_actions,
    run_agent,
)
from databridge_agent_runtime.answer import (
    OUTCOME_REASON_CODES,
    Claim,
    Clarification,
    Derivation,
    FinalAnswer,
    check_answer,
    ungrounded_values,
)
from databridge_agent_runtime.derive import RULES, replay, same_value
from databridge_agent_runtime.runs import RunOutcome
from databridge_agent_runtime.runtime import MAX_FAILED_TOOL_CALLS
from databridge_agent_runtime.shape import describe_schema, shape_args
from databridge_agent_runtime.tools import pointer_id
from databridge_core import SourcePointer
from databridge_core.provenance import Trust
from databridge_harness import FakeBackend, Harness

pytest.importorskip("databridge_mcp_ingest.screen")

HARNESS_CONFIG = {"routing": {"agent": "planner"}, "aliases": {"planner": {"model": "fake/planner"}},
                  "profiles": {"cloud": {}}}

#: The row shape of run 2's `SELECT * FROM invoices …` (synthetic values).
INVOICE = {"invoice_no": "INV-2026-0057", "customer_id": "C-1002", "issued_on": "2026-09-15",
           "due_on": "2026-10-15", "net_gbp": "12750.00", "vat_gbp": "2550.00",
           "status": "open"}


@pytest.fixture
def dirs(tmp_path):
    return {"runs_dir": tmp_path / "runs", "capture_dir": tmp_path / "capture"}


@pytest.fixture
def decide(tmp_path, compile_inputs, dirs):
    return {**dirs, "agents_dir": tmp_path / "agents", "compile_inputs": compile_inputs}


def _obs(messages):
    last = messages[-1]["content"]
    if isinstance(last, str) and last.startswith("Observations:\n"):
        return json.loads(last[len("Observations:\n"):])
    return []


def _harness(script, *, tokens=5):
    """``script(i, observations, messages) -> turn``: like the shared helper, but the script also
    sees the messages (the run context: run id, agent id, date)."""
    state = {"i": 0}

    def respond(model, messages, guided_json):
        turn = script(state["i"], _obs(messages), messages)
        state["i"] += 1
        return json.dumps(turn)

    backend = FakeBackend(respond, tokens_in=tokens, tokens_out=tokens)
    return Harness(backend, config=HARNESS_CONFIG, profile="cloud", cache=None), backend


def _run(spec, decl, script, *, store, gateway, compile_inputs, dirs, question=None, tokens=5,
         **kw):
    harness, backend = _harness(script, tokens=tokens)
    rec = run_agent(spec, decl, store=store, harness=harness, gateway=gateway,
                    trigger={"type": "manual", "by": "dana", "question": question},
                    compile_inputs=compile_inputs, notifier=lambda _e: None, **dirs, **kw)
    return rec, backend


def _run_id(messages) -> str:
    ctx = next(m["content"] for m in messages if m["role"] == "user"
               and m["content"].startswith("Run context:"))
    return ctx.split("run id ", 1)[1].split(";", 1)[0]


class InvoiceGateway(FakeGateway):
    """`larkspur-db` answers the run-2 invoice row; tools may publish an inputSchema."""

    def __init__(self, rows=None, schemas=None, files=None):
        super().__init__(files)
        self.rows = rows if rows is not None else [INVOICE]
        self.schemas = schemas or {}

    def advertise(self, connection):
        adv = super().advertise(connection)
        if not self.schemas or not adv.reachable:
            return adv
        tools = tuple(ToolAdvert(t.name, writes=t.writes, input_schema=self.schemas.get(t.name))
                      for t in adv.tools)
        extra = tuple(ToolAdvert(n, writes=False, input_schema=s) for n, s in self.schemas.items()
                      if connection == "larkspur-files" and n not in {t.name for t in tools})
        return ConnectionAdvert(connection=connection, declared_in=adv.declared_in,
                                tools=tools + extra, sql=adv.sql)

    def query(self, connection, sql):
        self.calls.append(("query", connection, sql))
        return GatewayResult(text=json.dumps(self.rows))

    def call_tool(self, connection, tool, arguments):
        if tool == "list_objects":
            self.calls.append(("call_tool", connection, tool, dict(arguments)))
            return GatewayResult(text=json.dumps(["invoices", "customers"]))
        return super().call_tool(connection, tool, arguments)


def _query_then(actions_or_final):
    """Turn 0 reads the invoices; turn 1 sends what ``actions_or_final(obs, messages)`` builds;
    then a final answer."""
    def script(i, obs, messages):
        if i == 0:
            return {"tool_calls": [{"tool": "source.query:larkspur-db",
                                    "args": {"sql": "SELECT * FROM invoices"}}]}
        if i == 1:
            return actions_or_final(obs, messages)
        return {"final": {"claims": []}}
    return script


# ================================================================================================
# N-05 — the run-local ref and the agent's own id in a human_required write
# ================================================================================================
def _insert(run_id: str, agent: str, pointer: str, created_by: str | None = None) -> str:
    """The INSERT run 2's planner wrote (follow_up_tasks), with this run's values."""
    return ("INSERT INTO follow_up_tasks (customer_id, invoice_no, title, due_on, created_by, "
            "agent_run_id, source_pointer) VALUES ("
            f"'{INVOICE['customer_id']}', '{INVOICE['invoice_no']}', "
            f"'Follow up on invoice {INVOICE['invoice_no']} (GBP {INVOICE['net_gbp']} net, due "
            f"{INVOICE['due_on']})', DATE '{INVOICE['due_on']}', '{created_by or agent}', "
            f"'{run_id}', '{pointer}')")


def test_n05_ref_and_agent_id_in_a_column_are_grounded_and_the_write_is_approved(
        store, compile_inputs, dirs, decide):
    gateway = InvoiceGateway()
    spec, decl = save_pair(decide["agents_dir"], followup_spec(), save_followup_draft=False)
    seen = {}

    def act(obs, messages):
        ref = refs_in(obs)[0]
        seen["ref"] = ref
        return {"actions": [{"name": "log_followup_task",
                             "args": {"sql": _insert(_run_id(messages), spec.agent, ref)},
                             "derived_from": [ref]}]}

    rec, _ = _run(spec, decl, _query_then(act), store=store, gateway=gateway,
                  compile_inputs=compile_inputs, dirs=dirs)
    (item,) = pending_actions(runs_dir=dirs["runs_dir"])
    sql = item["args"]["sql"]
    assert f"'{seen['ref']}'" not in sql and "'ptr:sha256:" in sql      # the ref was resolved
    assert item["ungrounded_values"] == []                               # agent id + run id + ptr
    log = [e for e in item["history"] if e.get("event") == "proposed"][0]
    (resolved,) = log["resolved_refs"]
    assert resolved["ref"] == seen["ref"] and resolved["pointer_id"] in sql
    assert resolved["pointer_id"] == pointer_id(SourcePointer.model_validate(
        log["derived_from"][0]))                                         # the cited pointer's id
    out = approve(rec.run_id, "a1", person(), gateway=gateway, **decide)
    assert out.actions[0].status == "executed"
    (write,) = gateway.writes()
    assert write[3]["sql"] == sql


def test_n05_the_gate_still_refuses_invented_and_undisclosed_values(store, compile_inputs, dirs,
                                                                   decide):
    gateway = InvoiceGateway()
    spec, decl = save_pair(decide["agents_dir"], followup_spec(), save_followup_draft=False)
    unissued = "p999"

    def act(obs, messages):
        ref = refs_in(obs)[0]
        assert unissued not in refs_in(obs)
        sql = _insert(_run_id(messages), spec.agent, unissued, created_by="someone_else")
        return {"actions": [{"name": "log_followup_task", "args": {"sql": sql},
                             "derived_from": [ref]}]}

    rec, _ = _run(spec, decl, _query_then(act), store=store, gateway=gateway,
                  compile_inputs=compile_inputs, dirs=dirs)
    (item,) = pending_actions(runs_dir=dirs["runs_dir"])
    assert set(item["ungrounded_values"]) == {unissued, "someone_else"}
    from databridge_agent_runtime import ActionRefused
    with pytest.raises(ActionRefused, match="ungrounded"):
        approve(rec.run_id, "a1", person(), gateway=gateway, **decide)
    assert gateway.writes() == []


def test_n05_the_run_context_names_the_agent_id():
    from databridge_agent_runtime.runtime import run_context
    import datetime as dt
    text = run_context("run-x", dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc), "UTC",
                       agent="overdue_invoice_follow_up")
    assert "agent id overdue_invoice_follow_up" in text and "run id run-x" in text


# ================================================================================================
# N-06 — human_notified drafts whose values are DERIVED from the evidence
# ================================================================================================
def _draft_args(days: str, total: str) -> dict:
    """Run 2's draft: a file named from the invoice number, a day count, a computed total."""
    return {"path": f"Follow-ups/{INVOICE['invoice_no']}.md",
            "content": f"Invoice {INVOICE['invoice_no']} was issued {days} days before it falls "
                       f"due on {INVOICE['due_on']}. Total GBP {total}."}


DAYS = str((__import__("datetime").date.fromisoformat(INVOICE["due_on"])
            - __import__("datetime").date.fromisoformat(INVOICE["issued_on"])).days)
TOTAL = f"{float(INVOICE['net_gbp']) + float(INVOICE['vat_gbp']):.2f}"


def _derivations(days=DAYS, total=TOTAL, issued=INVOICE["issued_on"]):
    return [{"value": days, "rule": "days_between", "inputs": [issued, INVOICE["due_on"]]},
            {"value": total, "rule": "sum", "inputs": [INVOICE["net_gbp"], INVOICE["vat_gbp"]]}]


def _draft_run(derivations, *, store, gateway, compile_inputs, dirs, days=DAYS, total=TOTAL):
    spec, decl = make_pair(followup_spec(), save_followup_draft=False)

    def act(obs, messages):
        return {"actions": [{"name": "save_followup_draft", "args": _draft_args(days, total),
                             "derived_from": refs_in(obs)[:1], "derivations": derivations}]}

    return _run(spec, decl, _query_then(act), store=store, gateway=gateway,
                compile_inputs=compile_inputs, dirs=dirs)


def test_n06_a_declared_derivation_grounds_the_derived_values(store, compile_inputs, dirs):
    from databridge_agent_runtime.answer import _numbers
    held = _numbers(json.dumps(INVOICE))
    assert not (_numbers(DAYS) | _numbers(TOTAL)) & held                  # really derived
    gateway = InvoiceGateway()
    rec, _ = _draft_run(_derivations(), store=store, gateway=gateway,
                        compile_inputs=compile_inputs, dirs=dirs)
    assert [a.status for a in rec.actions] == ["executed"]
    # E2E run 3 P-04: a write lands directly in its connection's root (`normalise_write_path`)
    from databridge_agent_runtime.write_paths import normalise_write_path
    assert [w[3]["path"] for w in gateway.writes()] == [
        normalise_write_path(_draft_args(DAYS, TOTAL)["path"])]
    from databridge_agent_runtime.runs import actions_path, find_run
    from databridge_agent_runtime import ActionLog
    prop = ActionLog(actions_path(find_run(rec.run_id, dirs["runs_dir"]))).proposal("a1")
    calls = sorted(d["derivation"] for d in prop["derivations"])
    assert calls == ["days_between@v1()", "sum@v1()"]
    for d in prop["derivations"]:
        ptr = SourcePointer.model_validate(d["pointer"])
        assert ptr.kind == "derived" and ptr.derived_from and ptr.trust is Trust.UNTRUSTED


def test_n06_without_a_derivation_the_computed_values_still_refuse(store, compile_inputs, dirs):
    gateway = InvoiceGateway()
    rec, _ = _draft_run([], store=store, gateway=gateway, compile_inputs=compile_inputs,
                        dirs=dirs)
    assert gateway.writes() == [] and rec.actions == []
    step = rec.steps[-1].policy_decision
    assert step["reason_code"] == "action_value_not_in_evidence"
    flagged = step["detail"].split("cited evidence: ", 1)[1]
    assert DAYS in flagged and "15300" in flagged and "derivations" in step["detail"]
    assert f"{INVOICE['invoice_no']}.md" not in flagged          # the file name is grounded


@pytest.mark.parametrize("bad,why", [
    ({"value": str(int(DAYS) + 1), "rule": "days_between",
      "inputs": [INVOICE["issued_on"], INVOICE["due_on"]]},
     f"days_between@v1() over {[INVOICE['issued_on'], INVOICE['due_on']]} gives"),  # no replay
    ({"value": DAYS, "rule": "days_between",
      "inputs": ["2026-09-14", "2026-10-14"]},
     "['2026-09-14', '2026-10-14'] appear in none"),                            # invented inputs
    ({"value": str(int(DAYS) + 1), "rule": "days_between",                      # one invented
      "inputs": [INVOICE["issued_on"], "2026-10-16"]}, "['2026-10-16'] appear in none"),
    ({"value": DAYS, "rule": "make_it_so", "inputs": [INVOICE["issued_on"],
                                                      INVOICE["due_on"]]},
     "is not a derivation rule"),
])
def test_n06_a_derivation_that_does_not_replay_from_cited_inputs_refuses(bad, why, store,
                                                                         compile_inputs, dirs):
    gateway = InvoiceGateway()
    days = str(bad["value"])
    rec, _ = _draft_run([bad, _derivations()[1]], store=store, gateway=gateway,
                        compile_inputs=compile_inputs, dirs=dirs, days=days)
    assert gateway.writes() == []
    detail = rec.steps[-1].policy_decision["detail"]
    assert rec.steps[-1].policy_decision["reason_code"] == "action_value_not_in_evidence"
    assert days in detail.split("cited evidence: ", 1)[1] and why in detail


def test_n06_rules_replay_through_the_shared_registry():
    from databridge_adapter.grounding import registered_derivations
    assert {f"{r}@v1" for r in RULES} <= set(registered_derivations())
    assert replay("sum", ["12750.00", "2550.00"]) == "15300"
    assert same_value("15300.00", replay("sum", ["12750.00", "2550.00"]))
    assert replay("days_between", ["2026-10-15", "2026-10-01"]) == "14"
    assert replay("add_days", ["2026-10-01"], {"days": "30"}) == "2026-10-31"
    assert replay("concat", ["INV-2026-0057", "md"], {"sep": "."}) == "INV-2026-0057.md"
    assert replay("count", ["a", "b"]) == "2"
    from databridge_agent_runtime.derive import DerivationError
    with pytest.raises(DerivationError):
        replay("concat", ["a", "b"], {"sep": ", ;"})


def test_n06_a_bare_file_name_is_grounded_by_its_stem():
    ev = [SimpleNamespace(text=json.dumps([INVOICE]))]
    assert ungrounded_values({"path": f"{INVOICE['invoice_no']}.md"}, ev) == []
    assert ungrounded_values({"path": "INV-2026-9999.md"}, ev) == ["INV-2026-9999.md"]


# ================================================================================================
# N-11 — arguments shaped and checked against the tool's inputSchema; a failed-call cap
# ================================================================================================
PATH_SCHEMA = {"type": "object", "properties": {"path": {"type": "string"}},
               "required": ["path"], "additionalProperties": False}
LIST_SCHEMA = {"type": "object", "properties": {"schema_name": {"type": "string"}},
               "required": ["schema_name"]}


def test_n11_shape_args_renames_fills_and_coerces_only_when_unambiguous():
    assert shape_args({"uri": "a.md"}, PATH_SCHEMA)[0] == {"path": "a.md"}
    two = {"uri": "a.md", "file": "b.md"}
    assert shape_args(two, PATH_SCHEMA)[0] == two                        # ambiguous: untouched
    k = {"type": "object", "properties": {"k": {"type": "integer"}, "q": {"type": "string"},
                                          "flag": {"type": "boolean"}}}
    got, notes = shape_args({"k": "5", "q": 12, "flag": "true"}, k)
    assert got == {"k": 5, "q": "12", "flag": True} and len(notes) == 3
    assert shape_args({"k": "five"}, k)[0] == {"k": "five"}               # not exact: unchanged
    d = {"type": "object", "properties": {"mode": {"type": "string", "default": "fast"}},
         "required": ["mode"]}
    assert shape_args({}, d)[0] == {"mode": "fast"}
    assert shape_args({}, LIST_SCHEMA)[0] == {}                          # nothing settles it
    assert "schema_name: string (required)" in describe_schema(LIST_SCHEMA)


def _reader_spec(*reads, **over):
    tools = ["graph.search", *(f"source.read:larkspur-files:{t}" for t in reads)]
    return briefing_spec(tools=tools, **over)


def test_n11_a_wrong_argument_name_is_shaped_before_the_call(store, compile_inputs, dirs):
    path = next(iter(FakeGateway().files))
    gateway = InvoiceGateway(schemas={"read_text_file": PATH_SCHEMA})
    spec, decl = make_pair(_reader_spec("read_text_file"))

    def script(i, obs, messages):
        if i == 0:
            return {"tool_calls": [{"tool": "source.read:larkspur-files:read_text_file",
                                    "args": {"uri": path}}]}
        return {"final": {"claims": [{"text": "read it", "citations": refs_in(obs)[:1]}]}}

    rec, backend = _run(spec, decl, script, store=store, gateway=gateway,
                        compile_inputs=compile_inputs, dirs=dirs)
    assert ("call_tool", "larkspur-files", "read_text_file", {"path": path}) in gateway.calls
    assert rec.steps[0].args == {"path": path}
    system = backend.calls[0]["messages"][0]["content"]
    assert f"source.read:larkspur-files:read_text_file {describe_schema(PATH_SCHEMA)}" in system
    assert rec.outcome.coverage_status == "complete"


def test_n11_args_that_do_not_fit_are_not_sent_and_a_retry_recovers(store, compile_inputs, dirs):
    gateway = InvoiceGateway(schemas={"list_objects": LIST_SCHEMA})
    spec, decl = make_pair(_reader_spec("list_objects"))
    told = {}

    def script(i, obs, messages):
        if i == 0:
            return {"tool_calls": [{"tool": "source.read:larkspur-files:list_objects",
                                    "args": {}}]}
        if i == 1:
            told.update(obs[0]["result"])
            return {"tool_calls": [{"tool": "source.read:larkspur-files:list_objects",
                                    "args": {"schema_name": "public"}}]}
        return {"final": {"claims": [{"text": "listed", "citations": refs_in(obs)[:1]}]}}

    rec, _ = _run(spec, decl, script, store=store, gateway=gateway,
                  compile_inputs=compile_inputs, dirs=dirs)
    sent = [c for c in gateway.calls if c[2] == "list_objects"]
    assert sent == [("call_tool", "larkspur-files", "list_objects", {"schema_name": "public"})]
    assert told["args_invalid"] and "schema_name" in told["takes"]
    (bad,) = [s for s in rec.coverage.skipped if s["reason_code"] == "tool_args_invalid"]
    assert bad["recovered"] is True and rec.outcome.coverage_status == "complete"


def test_n11_the_failed_call_cap_asks_for_an_answer_from_what_was_found(store, compile_inputs,
                                                                        dirs):
    gateway = InvoiceGateway(schemas={"list_objects": LIST_SCHEMA})
    spec, decl = make_pair(_reader_spec("list_objects", limits={
        "max_steps": 20, "max_tool_calls": 50, "tokens_per_run": 100000}))

    def script(i, obs, messages):
        if "WRAP UP NOW" in messages[-1]["content"]:
            return {"final": {"claims": [], "clarifications": [
                {"assumption": "the object list could not be read", "alternatives": []}]}}
        return {"tool_calls": [{"tool": "source.read:larkspur-files:list_objects", "args": {}}]}

    rec, backend = _run(spec, decl, script, store=store, gateway=gateway,
                        compile_inputs=compile_inputs, dirs=dirs)
    failed = [s for s in rec.coverage.skipped if s["reason_code"] == "tool_args_invalid"]
    assert len(failed) == MAX_FAILED_TOOL_CALLS
    assert rec.status == "completed" and backend.call_count == MAX_FAILED_TOOL_CALLS + 1
    assert rec.outcome.coverage_status == "reduced"                        # never recovered


def test_n11_a_budget_stop_still_answers_and_says_what_was_read(store, compile_inputs, dirs):
    spec, decl = make_pair(briefing_spec(limits={"max_steps": 20, "max_tool_calls": 50,
                                                 "tokens_per_run": 1000}))
    gateway = InvoiceGateway()

    def answers(i, obs, messages):
        if "WRAP UP NOW" in messages[-1]["content"]:
            return {"final": {"claims": [{"text": f"{INVOICE['invoice_no']} is open",
                                          "citations": [r for m in messages
                                                        for r in refs_in(_obs([m]))][:1]
                                          or ["p1"]}]}}
        return {"tool_calls": [{"tool": "source.query:larkspur-db",
                                "args": {"sql": f"SELECT * FROM invoices -- {i}"}}]}

    rec, backend = _run(spec, decl, answers, store=store, gateway=gateway,
                        compile_inputs=compile_inputs, dirs=dirs, tokens=300)
    assert rec.status == "completed" and rec.answer.claims and rec.answer.claims[0]["supported"]
    assert any("WRAP UP NOW" in m["content"] for m in backend.calls[-1]["messages"])

    def never(i, obs, messages):
        return {"tool_calls": [{"tool": "source.query:larkspur-db",
                                "args": {"sql": f"SELECT * FROM invoices -- {i}"}}]}

    rec2, _ = _run(spec, decl, never, store=store, gateway=gateway,
                   compile_inputs=compile_inputs, dirs=dirs, tokens=300)
    assert rec2.status == "stopped" and "token budget" in rec2.answer.markdown
    assert "Read before it stopped" in rec2.answer.markdown
    assert "source.query:larkspur-db: 1 pointer(s)" in rec2.answer.markdown
    assert rec2.outcome.reason_code == "no_answer" and rec2.outcome.coverage_status == "reduced"


# ================================================================================================
# N-12 — the structural outcome
# ================================================================================================
GOLDEN = Path(__file__).resolve().parents[3] / "packs" / "workspace" / "evals" / \
    "agent_contract.jsonl"
REGISTER = [{"source": "larkspur-files", "item": "hr/Compensation_Bands_2026.md",
             "decision": "block", "rules": ["confidentiality_marking"]}]


def test_n12_the_outcome_speaks_the_golden_sets_vocabulary():
    cases = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]
    outcomes = [c["outcome"] for c in cases if c.get("outcome")]
    assert outcomes                                                     # non-vacuous
    fields = set(RunOutcome.model_fields)
    for o in outcomes:
        assert set(o) <= fields, set(o) - fields
        assert o["reason_code"] in OUTCOME_REASON_CODES
        assert o["coverage_status"] in ("complete", "reduced")


def _final_only(final):
    def script(i, obs, messages):
        return {"final": final}
    return script


def test_n12_a_withheld_item_the_answer_needed_reduces_coverage(store, gateway, compile_inputs,
                                                                dirs):
    spec, decl = make_pair(briefing_spec())
    item = REGISTER[0]["item"]
    final = {"claims": [], "clarifications": [
        {"assumption": f"{item} was withheld by the screen "
                       f"({REGISTER[0]['rules'][0]})", "alternatives": []}],
        "outcome": {"reason_code": "withheld_by_screen", "withheld": [item.split("/")[-1]]}}
    rec, _ = _run(spec, decl, _final_only(final), store=store, gateway=gateway,
                  compile_inputs=compile_inputs, dirs=dirs, withheld=REGISTER,
                  question="summarise the compensation bands for 2026")
    o = rec.outcome
    assert o.reason_code == "withheld_by_screen" and o.coverage_status == "reduced"
    assert o.withheld == REGISTER and not o.human_confirmation_required
    assert any(s["reason_code"] == "withheld_by_screen" and s["item"] == item
               for s in rec.coverage.skipped)
    assert not rec.answer.unsupported_claims                   # the register's own numbers pass
    # an item NOT on the register is not recorded, and the reason code falls back
    final["outcome"]["withheld"] = ["hr/Salaries_Invented.md"]
    rec2, _ = _run(spec, decl, _final_only(final), store=store, gateway=gateway,
                   compile_inputs=compile_inputs, dirs=dirs, withheld=REGISTER,
                   question="summarise the compensation bands for 2026")
    assert rec2.outcome.withheld == [] and rec2.outcome.reason_code == "value_absent"
    assert rec2.outcome.coverage_status == "complete"


@pytest.mark.parametrize("named,question,accepted", [
    ("Salesforce", "what does the Salesforce record say about Corriedale?", True),
    ("Salesforce", "what does the CRM say about Corriedale?", False),     # not in the question
    ("larkspur-db", "what does larkspur-db say about Corriedale?", False),   # its own source
])
def test_n12_a_source_named_in_the_question_that_is_not_connected(named, question, accepted, store,
                                                                  gateway, compile_inputs, dirs):
    spec, decl = make_pair(briefing_spec())
    final = {"claims": [], "clarifications": [{"assumption": f"{named} is not connected",
                                               "alternatives": []}],
             "outcome": {"reason_code": "source_not_connected", "not_connected": [named]}}
    rec, _ = _run(spec, decl, _final_only(final), store=store, gateway=gateway,
                  compile_inputs=compile_inputs, dirs=dirs, question=question, withheld=[])
    assert (rec.outcome.not_connected == [named]) is accepted
    assert (rec.outcome.reason_code == "source_not_connected") is accepted
    assert (rec.outcome.coverage_status == "reduced") is accepted


def test_n12_identity_candidates_are_structural(store, gateway, compile_inputs, dirs):
    spec, decl = make_pair(briefing_spec())

    def script(i, obs, messages):
        if i == 0:
            return {"tool_calls": [{"tool": "graph.search", "args": {"query": "Ana Ruiz"}}]}
        person_refs = [r for n in obs[0]["result"]["nodes"] if n.get("candidate")
                       for r in n["refs"]]
        return {"final": {"claims": [{"text": "The contact is Ana Ruiz, a candidate.",
                                      "citations": person_refs}],
                          "outcome": {"reason_code": "identity_candidate"}}}

    rec, _ = _run(spec, decl, script, store=store, gateway=gateway, compile_inputs=compile_inputs,
                  dirs=dirs, question="who is the Northwind contact?", withheld=[])
    o = rec.outcome
    assert o.reason_code == "identity_candidate" and o.human_confirmation_required
    (cand,) = o.candidates
    assert cand["value"] == "Ana Ruiz" and cand["subject_kind"] == "person" and cand["cites"]
    # declared without any candidate in the answer: not borne out
    final = {"claims": [], "outcome": {"reason_code": "identity_candidate"}}
    rec2, _ = _run(spec, decl, _final_only(final), store=store, gateway=gateway,
                   compile_inputs=compile_inputs, dirs=dirs, withheld=[])
    assert rec2.outcome.reason_code == "value_absent" and not rec2.outcome.candidates


def test_n12_out_of_scope_pointers_are_a_note_not_reduced_coverage(store, gateway, compile_inputs,
                                                                   dirs):
    spec, decl = make_pair(briefing_spec())

    def script(i, obs, messages):
        if i == 0:
            return {"tool_calls": [{"tool": "graph.node",
                                    "args": {"node_id": "organisation:elsewhere"}}]}
        return {"final": {"claims": [], "clarifications": [
            {"assumption": "no discount is recorded", "alternatives": []}],
            "outcome": {"reason_code": "value_absent"}}}

    rec, _ = _run(spec, decl, script, store=store, gateway=gateway, compile_inputs=compile_inputs,
                  dirs=dirs, withheld=[])
    assert any(s["reason_code"] == "out_of_scope" for s in rec.coverage.skipped)   # still listed
    assert rec.outcome.coverage_status == "complete" and rec.outcome.reason_code == "value_absent"
    assert "Coverage notes" in rec.answer.markdown and "Reduced coverage" not in \
        rec.answer.markdown


# ================================================================================================
# N-13 — answer-contract false positives, both ways
# ================================================================================================
def _cell(row: str, column: str) -> SourcePointer:
    return SourcePointer(source_id="larkspur_db", kind="table_cell", table="public.customers",
                         column=column, row_id=row, trust=Trust.UNTRUSTED)


def _claim(reg, text, refs, **kw):
    return check_answer(FinalAnswer(claims=[Claim(text=text, citations=refs, **kw)]), reg,
                        **{k: v for k, v in kw.items() if False}).claims[0]


@pytest.mark.parametrize("stated,ok", [("C-1001", True), ("C-1002", False)])
def test_n13_an_id_in_the_cited_cells_locator_is_grounded(stated, ok):
    reg = PointerRegistry()
    ref = reg.add(_cell("C-1001", "city"), "city: Southampton")
    c = check_answer(FinalAnswer(claims=[Claim(text=f"Customer {stated} is in Southampton.",
                                               citations=[ref])]), reg).claims[0]
    assert c.supported is ok


@pytest.mark.parametrize("text,ok", [
    ("As of today (2026-10-01) it is not yet overdue.", True),
    ("As of 2026-10-02 it is not yet overdue.", False),
    ("Logged by run run-20261001T015216-adf7f3f3.", True),
])
def test_n13_the_run_context_grounds_its_own_values(text, ok):
    literals = "run-20261001T015216-adf7f3f3\noverdue_invoice_follow_up\n2026-10-01"
    reg = PointerRegistry()
    ref = reg.add(_cell("INV-2026-0057", "due_on"), "due_on: 2026-10-15")
    final = FinalAnswer(claims=[Claim(text=text, citations=[ref])])
    assert check_answer(final, reg, literals=literals).claims[0].supported is ok
    assert not check_answer(final, reg).claims[0].supported               # needs the context


def test_n13_withheld_register_names_in_an_uncited_clarification():
    from databridge_agent_runtime.withheld import describe
    reg = PointerRegistry()
    sow = [{"source": "larkspur-files", "item": "contracts/Corriedale/SOW-2026-021_Demand.docx",
            "decision": "review", "rules": ["contract_value_wording"]}]
    ok = Clarification(assumption="contracts/Corriedale/SOW-2026-021_Demand.docx was withheld",
                       alternatives=[])
    bad = Clarification(assumption="SOW-2026-022 was withheld", alternatives=[])
    checked = check_answer(FinalAnswer(clarifications=[ok, bad]), reg, literals=describe(sow))
    assert [c.supported for c in checked.clarifications] == [True, False]
    assert "22" in checked.clarifications[1].reason


@pytest.mark.parametrize("days,derivation,ok", [
    ("14", {"value": "14", "rule": "days_between", "inputs": ["2026-10-01", "2026-10-15"]}, True),
    ("15", {"value": "15", "rule": "days_between", "inputs": ["2026-10-01", "2026-10-15"]}, False),
    ("14", None, False),
])
def test_n13_a_date_difference_is_supported_only_by_a_derivation_that_replays(days, derivation,
                                                                              ok):
    reg = PointerRegistry()
    ref = reg.add(_cell("INV-2026-0057", "due_on"), "due_on: 2026-10-15")
    claim = Claim(text=f"Due on 2026-10-15, in {days} days.", citations=[ref],
                  derivations=[Derivation(**derivation)] if derivation else [])
    c = check_answer(FinalAnswer(claims=[claim]), reg, literals="2026-10-01").claims[0]
    assert c.supported is ok
    if ok:
        (d,) = c.derivations
        assert d["derivation"] == "days_between@v1()" and d["pointer"]["kind"] == "derived"


def test_n13_a_person_named_from_their_own_cited_row_is_a_cited_candidate():
    reg = PointerRegistry(track_disclosure=True)
    person_ref = reg.add(_cell("C-1001:Selin Kaya-Morrow", "full_name"), "Selin Kaya-Morrow",
                         subject_candidate=True, identifiers=("Selin Kaya-Morrow",),
                         subject_kind="person")
    row = SourcePointer(source_id="larkspur_db", kind="doc_span", document_id="d1", char_start=0,
                        char_end=90, trust=Trust.UNTRUSTED)
    row_ref = reg.add(row, json.dumps([{"full_name": "Selin Kaya-Morrow",
                                        "role": "Head of Procurement"}]))
    org_ref = reg.add(_cell("C-1001", "name"), "name: Pellucid Freightways Ltd")
    reg.disclose([person_ref, row_ref, org_ref])
    text = "The head of procurement is Selin Kaya-Morrow (a candidate)."
    own = check_answer(FinalAnswer(claims=[Claim(text=text, citations=[row_ref])]), reg).claims[0]
    assert own.supported and own.subject_candidate and own.requires_human_selection
    assert own.candidates == [{"value": "Selin Kaya-Morrow", "subject_kind": "person",
                               "refs": [row_ref]}]
    org = check_answer(FinalAnswer(claims=[Claim(text=text, citations=[org_ref])]), reg).claims[0]
    assert not org.supported and "candidate_identity_uncited" in org.reason
    assert org.subject_candidate                                  # still flagged a candidate


# ================================================================================================
# coordinator (AUTO2 / FED3): the user's answers to federated planner questions pass through
# ================================================================================================
class _AnsweringFed:
    """A federated service whose `ask_delegate` takes ``answers`` (FED3's additive parameter)."""

    def __init__(self):
        self.asked: list = []

    def available(self, aa):
        return True

    def ask_delegate(self, aa, subtask, *, admission, run_id=None, answers=None):
        self.asked.append({"subtask": dict(subtask), "answers": answers})
        return SimpleNamespace(rows=[], live=False, method="tabular", error=None,
                               coverage=SimpleNamespace(skipped=[], sources_read=[],
                                                        truncated=False))


class _OldFed(_AnsweringFed):
    def ask_delegate(self, aa, subtask, *, admission, run_id=None):        # no `answers` yet
        self.asked.append({"subtask": dict(subtask)})
        return super().ask_delegate(aa, subtask, admission=admission)


def _fed_id(text: str) -> str:
    import hashlib
    return "fed:" + hashlib.sha1(" ".join(text.split()).encode("utf-8")).hexdigest()


def _delegate_call(fed, args):
    from databridge_agent_runtime.tools import ToolLayer, ToolOutcome
    spec, _ = make_pair(briefing_spec())
    layer = ToolLayer(spec=spec, authority=None, registry=PointerRegistry(), federation=fed)
    out = ToolOutcome(tool="delegate.ask:aa", args={}, permitted=True, reason_code="granted",
                      detail="", capability=None, grant_id=None)
    layer._delegate("aa", args, out)
    return out


def test_delegate_answers_pass_through_to_the_federated_service():
    qid = _fed_id("Which date axis: issued or due?")
    fed = _AnsweringFed()
    out = _delegate_call(fed, {"subtask": {"entity_kinds": ["invoice"]},
                               "answers": {qid: "due", "q1": "not a fed id"}})
    (asked,) = fed.asked
    assert asked["answers"] == {qid: "due"} and "answers" not in asked["subtask"]
    assert out.observation["answers_passed"] == [qid]
    assert "q1" in out.observation["answers_not_passed"]
    # answers written inside the sub-task are lifted out the same way
    fed2 = _AnsweringFed()
    _delegate_call(fed2, {"subtask": {"entity_kinds": ["invoice"], "answers": {qid: "due"}}})
    assert fed2.asked[0]["answers"] == {qid: "due"} and "answers" not in fed2.asked[0]["subtask"]


def test_delegate_answers_are_not_forced_on_a_service_that_does_not_take_them():
    qid = _fed_id("Which date axis: issued or due?")
    fed = _OldFed()
    out = _delegate_call(fed, {"subtask": {"entity_kinds": ["invoice"]}, "answers": {qid: "due"}})
    assert fed.asked and "answers" not in fed.asked[0]["subtask"]
    assert "does not take answers" in out.observation["answers_not_passed"]

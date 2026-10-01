"""E2E run 3 (wiki/40-reviews/E2E-2026-10-01-run3.md) — P-02, P-04, P-12 (agent_runtime side),
fixed by RT4.

Each test drives the real code path (`run_agent`, the real harness behind a scripted planner, the
real `SubTask` contract) with the SHAPE of the failing input from the run-3 evidence
(databridge/var/e2e-20261001T0836/: `artefacts/http/0100_p3_deleg_agent.json` for the five
delegate.ask sub-tasks; `artefacts/p3_overdue.json` and `p3d_crm.json` for the two draft paths;
`artefacts/s5_golden_larkspur_qa.json` ac-006 and `p3_fed.json` for the contract false
positives). Expectations are derived from the contract, the fake sources and the rule under test,
never recalled; each fix is tested both ways (what was wrongly refused now passes, what is truly
wrong still refuses). No network, no live model.
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from agent_runtime_helpers import (briefing_spec, followup_spec, make_pair, person, refs_in,
                                   save_pair)
from test_e2e_run2_rtf2 import (INVOICE, InvoiceGateway, _query_then, _run, _run_id,  # noqa: F401
                                decide, dirs)

from databridge_agent_runtime import PointerRegistry, approve, pending_actions
from databridge_agent_runtime.answer import Claim, FinalAnswer, check_answer
from databridge_agent_runtime.runtime import MAX_FAILED_TOOL_CALLS
from databridge_agent_runtime.subtask_shape import shape_subtask, subtask_model, summarise_error
from databridge_agent_runtime.write_paths import normalise_write_path
from databridge_core import SourcePointer
from databridge_core.provenance import Trust

SubTask = subtask_model()
pytestmark = pytest.mark.skipif(SubTask is None, reason="databridge_federated not importable")

AA = "larkspur_files_crm_adapter"

#: The five sub-tasks run 3's delegating agent sent (artefacts/http/0100_p3_deleg_agent.json),
#: copied as the planner wrote them.
_W1 = {"entity_kinds": ["invoice", "customer", "account", "meeting_note"],
       "filters": [{"field": "invoice.status", "op": "=", "value": "open"},
                   {"field": "customer", "op": "mentioned_in", "value": "meeting notes"}],
       "fields": ["account_owner", "invoice.amount", "invoice.due_date", "customer"],
       "time_window": {"axis": "invoice.due_date", "from": "2026-10-01", "to": "2026-12-31"},
       "aggregate": {"op": "sum", "field": "invoice.amount", "group_by": "account_owner"},
       "limit": 100}
_W3 = {"entity_kinds": ["invoice", "customer", "meeting_note"],
       "filters": [{"field": "invoice.status", "op": "eq", "value": "open"},
                   {"field": "invoice.due_date", "op": "ge", "value": "2026-10-01"},
                   {"field": "invoice.due_date", "op": "le", "value": "2026-12-31"},
                   {"field": "customer.mentioned_in_meeting_notes", "op": "eq", "value": True}],
       "fields": ["account_owner", "customer", "invoice.amount", "invoice.due_date"],
       "aggregate": {"op": "sum", "field": "invoice.amount", "group_by": ["account_owner"]},
       "limit": 100}
#: Run 1's first sub-task with ONLY the defects the contract settles (symbol op, axis given a
#: field name, a lone group_by, the sub-task's own kind as qualifier).
_SHAPEABLE = {**copy.deepcopy(_W1), "entity_kinds": ["invoice"],
              "filters": [_W1["filters"][0]]}
RUN3_SUBTASKS = [_W1, _W3, _SHAPEABLE]


def _validates(st: dict) -> bool:
    try:
        SubTask.model_validate({**st, "adapter_agent": AA})
        return True
    except Exception:                                        # noqa: BLE001
        return False


# ================================================================================================
# P-02 — delegate.ask arguments shaped and validated before the call
# ================================================================================================
def test_p02_the_run3_subtasks_fail_the_contract_as_sent():
    """Non-vacuous: every recorded run-3 shape really is refused by the contract unshaped."""
    assert all(not _validates(s) for s in RUN3_SUBTASKS)


def test_p02_shapeable_defects_are_shaped_and_the_result_validates():
    st, notes, problems = shape_subtask(copy.deepcopy(_SHAPEABLE), AA)
    assert problems == [] and _validates(st)
    # what was shaped is what the contract's own schema and name rules say, and it is reported
    schema = SubTask.model_json_schema()
    ops = schema["$defs"]["Filter"]["properties"]["op"]["enum"]
    axes = schema["$defs"]["TimeWindow"]["properties"]["axis"]
    assert st["filters"][0]["op"] in ops and st["time_window"]["axis"] == axes["default"]
    kind = _SHAPEABLE["entity_kinds"][0]
    for name in [st["filters"][0]["field"], *st["fields"], st["time_window"]["field"],
                 st["aggregate"]["field"], *st["aggregate"]["group_by"]]:
        assert "." not in name                                # plain names only
    assert st["time_window"]["field"] == _SHAPEABLE["time_window"]["axis"].split(".", 1)[1]
    assert isinstance(st["aggregate"]["group_by"], list)
    assert len(notes) >= 4 and any(kind in n for n in notes)


def test_p02_what_cannot_be_settled_is_reported_in_plain_words_not_guessed():
    for raw in (_W1, _W3):
        st, _, problems = shape_subtask(copy.deepcopy(raw), AA)
        assert problems, raw
        text = " ".join(problems)
        assert "errors.pydantic.dev" not in text and "validation error" not in text
    _, _, p1 = shape_subtask(copy.deepcopy(_W1), AA)
    assert any("mentioned_in" in p for p in p1)               # an unknown op is named, not mapped
    _, _, p3 = shape_subtask(copy.deepcopy(_W3), AA)
    assert any("ONE kind" in p and "customer" in p for p in p3)   # a second kind is named


class _ContractFed:
    """A federated service that validates exactly as the real one does (`SubTask`), records what
    it was asked, and answers one cell with its pointer."""

    def __init__(self):
        self.asked: list[dict] = []
        self.catalogue = None

    def available(self, aa):
        return aa == AA

    def ask_delegate(self, aa, subtask, *, admission, run_id=None):
        try:
            SubTask.model_validate({**subtask, "adapter_agent": aa})
        except Exception as e:                               # noqa: BLE001 - as the service does
            return SimpleNamespace(error=f"the sub-task is not well formed: {e}", rows=[],
                                   method=None, live=False, coverage=None)
        self.asked.append(dict(subtask))
        ptr = SourcePointer(source_id="larkspur-db", kind="table_cell", table="invoices",
                            column="net_gbp", row_id=INVOICE["invoice_no"], trust=Trust.UNTRUSTED)
        row = SimpleNamespace(row_id="r1", cells={"net_gbp": SimpleNamespace(
            value=INVOICE["net_gbp"], pointer=ptr)}, entities=[])
        return SimpleNamespace(rows=[row], live=False, method="tabular", error=None,
                               coverage=SimpleNamespace(skipped=[], sources_read=["larkspur-db"],
                                                        truncated=False))


def _delegating_spec(**over):
    return briefing_spec(agent="cross_source_qa", title="Cross-source QA",
                         tools=[f"delegate.ask:{AA}"], delegates=[AA], **over)


def test_p02_a_run_sends_only_subtasks_that_fit_and_summarises_the_rest(store, compile_inputs,
                                                                        dirs):
    fed = _ContractFed()
    spec, decl = make_pair(_delegating_spec())
    calls = [{"tool": f"delegate.ask:{AA}", "args": {"subtask": copy.deepcopy(s)}}
             for s in RUN3_SUBTASKS]

    def script(i, obs, messages):
        if i == 0:
            return {"tool_calls": calls}
        return {"final": {"claims": [], "clarifications": [
            {"assumption": "only part of the question could be asked", "alternatives": []}]}}

    rec, _ = _run(spec, decl, script, store=store, gateway=InvoiceGateway(),
                  compile_inputs=compile_inputs, dirs=dirs, federation=fed)
    # derived: exactly the sub-tasks the shaper accepts reach the service, already valid
    expected_sent = [s for s in RUN3_SUBTASKS
                     if not shape_subtask(copy.deepcopy(s), AA)[2]]
    assert expected_sent and len(fed.asked) == len(expected_sent)
    assert all(_validates(a) for a in fed.asked)
    invalid = [s for s in rec.coverage.skipped if s["reason_code"] == "tool_args_invalid"]
    assert len(invalid) == len(RUN3_SUBTASKS) - len(expected_sent)
    blob = json.dumps(rec.model_dump(mode="json"), default=str)
    assert "not asked" in blob                                        # the refusal is said
    assert "errors.pydantic.dev" not in blob and "validation errors for SubTask" not in blob


def test_p02_malformed_delegate_calls_hit_the_failed_call_cap(store, compile_inputs, dirs):
    fed = _ContractFed()
    spec, decl = make_pair(_delegating_spec(limits={"max_steps": 20, "max_tool_calls": 50,
                                                    "tokens_per_run": 100000}))

    def script(i, obs, messages):
        if "WRAP UP NOW" in messages[-1]["content"]:
            return {"final": {"claims": [], "clarifications": [
                {"assumption": "the delegate could not be asked", "alternatives": []}]}}
        return {"tool_calls": [{"tool": f"delegate.ask:{AA}",
                                "args": {"subtask": copy.deepcopy(_W1)}}]}

    rec, backend = _run(spec, decl, script, store=store, gateway=InvoiceGateway(),
                        compile_inputs=compile_inputs, dirs=dirs, federation=fed)
    failed = [s for s in rec.coverage.skipped if s["reason_code"] == "tool_args_invalid"]
    assert len(failed) == MAX_FAILED_TOOL_CALLS and fed.asked == []
    assert backend.call_count == MAX_FAILED_TOOL_CALLS + 1      # then one wrap-up turn
    assert rec.outcome.coverage_status == "reduced"


def test_p02_summarise_error_drops_urls_and_folds_dumps():
    raw = ("4 validation errors for SubTask\nfilters.0.op\n  Input should be 'eq' [type=x]\n"
           "    For further information visit https://errors.pydantic.dev/2.11/v/literal_error")
    s = summarise_error(raw)
    assert "pydantic.dev" not in s and "\n" not in s and "filters.0.op" in s
    assert len(summarise_error("x" * 5000)) <= 600


# ================================================================================================
# P-04 — a follow-up draft lands in the connection's root, its assembled name grounded by replay
# ================================================================================================
_FOLLOWUP_TEXT = ("Every weekday, find invoices that are overdue or coming due, draft a follow-up "
                  "e-mail for each into the follow-ups folder and log a task; tell me after.")


def _drafts(run_id: str, conn: str, invoice: str) -> dict[str, str]:
    """The two path shapes run 3's planner wrote (firing 1, firing 2)."""
    return {"firing1": f"{conn}/{invoice}.txt",
            "firing2": f"{run_id}/{invoice}_follow_up.txt"}


def _draft_script(paths_for):
    def act(obs, messages):
        ref = refs_in(obs)[0]
        return {"actions": [{"name": "save_followup_draft",
                             "args": {"path": p, "content": f"Reminder: {INVOICE['invoice_no']} "
                                                            f"is due on {INVOICE['due_on']}."},
                             "derived_from": [ref]}
                            for p in paths_for(_run_id(messages)).values()]}
    return _query_then(act)


def _write_conn(spec) -> str:
    from databridge_workflow_spec import parse_tool
    return parse_tool(next(a.tool for a in spec.actions
                           if a.name == "save_followup_draft"))["connection"]


def test_p04_run3_draft_paths_land_in_the_root_and_their_names_replay(store, compile_inputs,
                                                                      dirs):
    gateway = InvoiceGateway()
    spec, decl = make_pair(followup_spec(created_from=_FOLLOWUP_TEXT), save_followup_draft=False)
    conn = _write_conn(spec)
    seen: dict = {}

    def paths(run_id):
        seen.update(_drafts(run_id, conn, INVOICE["invoice_no"]))
        return dict(seen)

    rec, _ = _run(spec, decl, _draft_script(paths), store=store, gateway=gateway,
                  compile_inputs=compile_inputs, dirs=dirs)
    assert [a.status for a in rec.actions] == ["executed", "executed"]
    written = [w[3]["path"] for w in gateway.writes()]
    assert written == [normalise_write_path(p) for p in seen.values()]
    assert all("/" not in p and "\\" not in p for p in written)           # no folder assumed
    from databridge_agent_runtime import ActionLog
    from databridge_agent_runtime.runs import actions_path, find_run
    log = ActionLog(actions_path(find_run(rec.run_id, dirs["runs_dir"])))
    second = log.proposal("a2")
    stem = written[1].rsplit(".", 1)[0]
    assert second["runtime_derivations"] and stem in second["runtime_derivations"]
    d = next(x for x in second["derivations"] if x["value"] == stem)
    ptr = SourcePointer.model_validate(d["pointer"])
    assert ptr.kind == "derived" and ptr.derived_from and d["derivation"].startswith("concat@v1")
    assert second["requested_args"]["path"] == seen["firing2"]              # what was asked


def test_p04_a_human_required_draft_is_approvable_and_lands(store, compile_inputs, dirs,
                                                           decide):
    """Run 3's compiler made the draft human_required; its approval was refused as ungrounded."""
    gateway = InvoiceGateway()
    sd = followup_spec(created_from=_FOLLOWUP_TEXT)
    sd["actions"][0] = {**sd["actions"][0], "automation_mode": "human_required"}
    spec, decl = save_pair(decide["agents_dir"], sd, save_followup_draft=True)
    conn = _write_conn(spec)
    seen: dict = {}

    def paths(run_id):
        seen.update({"firing2": _drafts(run_id, conn, INVOICE["invoice_no"])["firing2"]})
        return dict(seen)

    rec, _ = _run(spec, decl, _draft_script(paths), store=store, gateway=gateway,
                  compile_inputs=compile_inputs, dirs=dirs)
    (item,) = pending_actions(runs_dir=dirs["runs_dir"])
    assert item["ungrounded_values"] == []
    out = approve(rec.run_id, "a1", person(), gateway=gateway, **decide)
    assert out.actions[0].status == "executed"
    assert [w[3]["path"] for w in gateway.writes()] == [normalise_write_path(seen["firing2"])]


@pytest.mark.parametrize("make_path", [
    lambda run_id, inv: f"{run_id}/INV-2026-9999_follow_up.txt",      # an id in no evidence
    lambda run_id, inv: f"{run_id}/{inv}_wire_now.txt",                # words in nothing declared
])
def test_p04_an_assembled_name_with_an_ungrounded_piece_still_refuses(make_path, store,
                                                                       compile_inputs, dirs):
    gateway = InvoiceGateway()
    spec, decl = make_pair(followup_spec(created_from=_FOLLOWUP_TEXT), save_followup_draft=False)
    rec, _ = _run(spec, decl, _draft_script(lambda rid: {"p": make_path(
        rid, INVOICE["invoice_no"])}), store=store, gateway=gateway,
        compile_inputs=compile_inputs, dirs=dirs)
    assert gateway.writes() == [] and rec.actions == []
    assert rec.steps[-1].policy_decision["reason_code"] == "action_value_not_in_evidence"


# ================================================================================================
# P-12 — the answer contract: the question's own numbers; ids in the cited query's arguments
# ================================================================================================
def test_p12_an_id_in_the_cited_querys_arguments_is_grounded(store, compile_inputs, dirs):
    gateway = InvoiceGateway(rows=[])
    spec, decl = make_pair(briefing_spec())
    cid = "C-1003"
    other = "C-1004"
    sql = f"SELECT * FROM invoices WHERE customer_id = '{cid}'"

    def script(i, obs, messages):
        if i == 0:
            return {"tool_calls": [{"tool": "source.query:larkspur-db", "args": {"sql": sql}}]}
        ref = refs_in(obs)[0]
        return {"final": {"claims": [
            {"text": f"The query for customer_id '{cid}' returned no invoices.",
             "citations": [ref]},
            {"text": f"The query for customer_id '{other}' returned no invoices.",
             "citations": [ref]}]}}

    rec, _ = _run(spec, decl, script, store=store, gateway=gateway,
                  compile_inputs=compile_inputs, dirs=dirs)
    supported = {c["text"]: c["supported"] for c in rec.answer.claims}
    assert len(supported) == 2
    assert [v for t, v in supported.items() if cid in t] == [True]
    assert [v for t, v in supported.items() if other in t] == [False]  # not in the query


def test_p12_numbers_the_question_states_are_grounded_and_others_are_not():
    reg = PointerRegistry()
    ptr = SourcePointer(source_id="larkspur-db", kind="table_cell", table="invoices",
                        column="total", row_id="LA-007", trust=Trust.UNTRUSTED)
    ref = reg.add(ptr, "total: 11100")
    question = ("What is the total by account owner for invoices due between 1 October 2026 and "
                "31 December 2026?")
    ok = Claim(text="Between 1 October 2026 and 31 December 2026 LA-007 is owed 11100.",
               citations=[ref])
    bad = Claim(text="Between 1 October 2026 and 31 December 2026 LA-007 is owed 11199.",
                citations=[ref])
    with_q = check_answer(FinalAnswer(claims=[ok, bad]), reg, question=question)
    assert [c.supported for c in with_q.claims] == [True, False]
    assert "11199" in with_q.claims[1].reason
    without = check_answer(FinalAnswer(claims=[ok]), reg)            # non-vacuous: the rule bites
    assert not without.claims[0].supported and "31" in without.claims[0].reason
    # a BARE number in the question could be the answer: it never grounds (shared FED4 rule)
    bare = check_answer(FinalAnswer(claims=[Claim(text="LA-007 has 5 open invoices.",
                                                  citations=[ref])]), reg,
                        question="Does LA-007 have 5 open invoices?")
    assert not bare.claims[0].supported and "5" in bare.claims[0].reason


def test_p12_query_ids_never_ground_a_written_value():
    from databridge_agent_runtime.answer import ungrounded_values
    from databridge_agent_runtime.tools import Evidence, query_ids
    ids = query_ids({"sql": "SELECT * FROM invoices WHERE customer_id = 'C-1003' LIMIT 10"})
    assert ids == ["C-1003"]                                          # ids only: no 10, no words
    ev = Evidence(ref="p1", pointer=SourcePointer(source_id="s", kind="doc_span",
                                                  document_id="d", char_start=0, char_end=2,
                                                  trust=Trust.UNTRUSTED),
                  text="[]", query_ids=tuple(ids))
    assert ungrounded_values({"customer_id": "C-1003"}, [ev]) == ["C-1003"]

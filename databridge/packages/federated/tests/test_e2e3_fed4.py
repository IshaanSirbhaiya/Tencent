"""E2E run 3 regressions for delegated querying (FED4): P-01, P-11, P-12 (narrator side).

* **P-01** — "how many open invoices does Pellucid Freightways have?" was answered "0" although
  one is open. The planner filtered ``customers.name = 'Pellucid Freightways'``; the stored name is
  ``'Pellucid Freightways Ltd'``. The customer sub-task read nothing, the semi link had nothing to
  match, and the ungrouped count was zero-filled — then narrated as a fact. Fixed twice over: an
  empty text filter is read as the ONE stored value holding all its words (stated as an
  assumption; ambiguity is reported, never chosen), and a semi link that matched nothing never
  yields a stated zero.
* **P-11** — a plan-validation error (``order_by`` naming the aggregate's input field) was shown
  to the user as a question. The planner now repairs it (deterministically when it can, else one
  model round with the problems) and only what survives reaches the user.
* **P-12** — dates the asker wrote in the question were flagged UNSUPPORTED in the narration.

The live run's own plan is replayed (``LIVE_PLAN``: the planner's output recorded in
``fed-20261001T084428-da312a46``). Expectations are COMPUTED from the fixture lists.
"""
from __future__ import annotations

import datetime as dt
import json
import re

import pytest

from fed_testkit import _write_facts
from test_e2e2_fed3 import (  # noqa: F401 - fixtures/data shared with the run-2 world
    AA,
    ANALYST,
    CUSTOMERS,
    DB,
    INVOICES,
    TODAY,
    _build_graph,
    _harness,
)

from databridge_federated import DelegationPlan

INV_COLS = ["invoice_no", "customer_id", "due_on", "total_gbp", "status"]
CUS_COLS = ["customer_id", "name", "account_owner"]
#: a second customer sharing words with an existing one: "Tamsinbay" is then ambiguous
EXTRA_CUSTOMERS = [("C-1006", "Tamsinbay Harbour Board", "LA-012")]


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def holders(asked: str, customers=CUSTOMERS + EXTRA_CUSTOMERS) -> list[tuple]:
    """Customers whose stored name holds the asked words contiguously (the test's own rule)."""
    w = _words(asked)
    return [c for c in customers
            if any(_words(c[1])[i:i + len(w)] == w for i in range(len(_words(c[1]))))]


def open_count(cid: str) -> int:
    return sum(1 for _no, c, _d, _a, st in INVOICES if c == cid and st == "open")


def live_plan(name: str, *, key_kind: str = "organisation") -> dict:
    """The plan the live planner wrote for P-01, with ``name`` as the asked customer."""
    return {
        "subtasks": [
            {"id": "t1", "adapter_agent": AA, "connection": DB, "entity_kinds": ["invoices"],
             "filters": [{"field": "status", "op": "eq", "value": "open"}],
             "fields": ["invoice_no", "customer_id", "status", "total_gbp", "due_on"]},
            {"id": "t2", "adapter_agent": AA, "connection": DB, "entity_kinds": ["customers"],
             "filters": [{"field": "name", "op": "eq", "value": name}],
             "fields": ["customer_id", "name"]}],
        "combine": {"base": "t1",
                    "links": [{"subtask": "t2", "key": "customer_id",
                               "other_field": "customer_id", "key_kind": key_kind,
                               "via": "canonical_id", "mode": "semi"}],
                    "aggregate": {"op": "count", "field": "invoice_no"}},
        "assumptions": [f"{name} is identified by customers.name"]}


@pytest.fixture
def lark4(env):
    from databridge_agent_runtime import McpFactsStore
    from databridge_graph.packs import deployment
    from databridge_graph.store import GraphStore
    from databridge_workflow_spec import AdapterAgentSpec, dump_spec

    _write_facts(env / "facts", DB, {
        "invoices": (INV_COLS, [dict(zip(INV_COLS, r)) for r in INVOICES]),
        "customers": (CUS_COLS, [dict(zip(CUS_COLS, r))
                                 for r in CUSTOMERS + EXTRA_CUSTOMERS])})
    aa_dir = env / "adapter_agents"
    aa_dir.mkdir(parents=True, exist_ok=True)
    spec = AdapterAgentSpec.model_validate({
        "id": AA, "title": "graph sync", "created_from": "keep both sources right",
        "connections": [DB, "shared-files"], "extract": {"pack": "workspace"},
        "status": "active"})
    (aa_dir / f"{AA}.yaml").write_text(dump_spec(spec), encoding="utf-8")
    with deployment("workspace") as vocab:
        store = GraphStore()
        _build_graph(store)

        def service(harness=None):
            from databridge_federated import build_service
            svc = build_service(store=store, harness=harness, facts=McpFactsStore(),
                                adapter_agents_root=aa_dir, vocabulary=vocab,
                                runs_dir=env / "runs")
            svc.clock = lambda: dt.datetime.combine(TODAY, dt.time(9), tzinfo=dt.timezone.utc)
            return svc
        yield service
        store.close()


def _value(ans) -> object:
    [row] = ans.table["rows"]
    cell = row["cells"]["count_invoice_no"]
    return None if cell is None else cell["value"]


def _scripted(plans: list[dict], narrate=None):
    """A planner that returns ``plans`` in order (the last one repeats); records every call."""
    calls: list[dict] = []

    def respond(model, messages, guided_json):
        if messages[0]["content"].startswith("You are Walnut's main agent"):
            n = sum(1 for c in calls if c["kind"] == "plan")
            calls.append({"kind": "plan", "messages": messages})
            return json.dumps(plans[min(n, len(plans) - 1)])
        payload = json.loads(messages[-1]["content"][len("TABLE:\n"):])
        calls.append({"kind": "narrate", "payload": payload})
        return json.dumps(narrate(payload) if narrate else {"claims": []})

    h, _b = _harness(respond)
    return h, calls


# ------------------------------------------------------------------------------------------------
# P-01
# ------------------------------------------------------------------------------------------------
def test_p01_live_plan_counts_the_customer_the_name_denotes(lark4):
    asked = "Pellucid Freightways"
    [cust] = holders(asked)
    assert cust[1] != asked                       # non-vacuous: the stored name is NOT the asked one
    want = open_count(cust[0])
    assert want >= 1                              # non-vacuous: the live failure stated 0
    ans = lark4().answer(f"how many open invoices does {asked} have?", admission=ANALYST,
                         plan=DelegationPlan.model_validate(live_plan(asked)))
    assert ans.status == "completed"
    assert _value(ans) == want
    # the reading is stated, naming the stored value it was read as
    assert any(cust[1] in a and asked in a for a in ans.plan["assumptions"])
    assert cust[1] in ans.answer_markdown


def test_p01_a_name_that_matches_nothing_never_states_zero(lark4):
    asked = "Brackenfold Logistics"
    assert not holders(asked)                     # non-vacuous: truly no such customer
    h, calls = _scripted([live_plan(asked)],
                         narrate=lambda p: {"claims": [{"text": "There are 0 open invoices.",
                                                        "citations": []}]})
    ans = lark4(h).answer(f"how many open invoices does {asked} have?", admission=ANALYST)
    assert _value(ans) is None                    # no zero in the table
    assert ans.coverage["status"] == "uncertain"
    assert not [c for c in calls if c["kind"] == "narrate"]   # nothing to narrate, so no model
    assert "No value is stated" in ans.answer_markdown
    assert not any(re.search(r"(?<![\w.-])0(?![\w.])", c["text"]) for c in ans.claims)
    assert ans.table["absent"] and "not found" in ans.table["absent"][0]


def test_p01_an_ambiguous_name_is_reported_never_chosen(lark4):
    asked = "Tamsinbay"
    found = holders(asked)
    assert len(found) >= 2                        # non-vacuous: genuinely ambiguous
    ans = lark4().answer(f"how many open invoices does {asked} have?", admission=ANALYST,
                         plan=DelegationPlan.model_validate(live_plan(asked)))
    assert _value(ans) is None
    reasons = " ".join(ans.coverage["reasons"])
    for _cid, name, _o in found:
        assert name in reasons                    # every candidate is named, none chosen
    assert not any("read as" in a for a in ans.plan["assumptions"])


def test_p01_a_person_link_never_resolves_a_name(lark4):
    from databridge_federated import name_resolvable
    svc = lark4()
    subject = sorted(svc.catalogue.subject_kinds)
    assert subject                                # non-vacuous: the pack declares a person kind
    asked = "Pellucid Freightways"
    org = DelegationPlan.model_validate(live_plan(asked))
    person = DelegationPlan.model_validate(live_plan(asked, key_kind=subject[0]))
    assert name_resolvable(org, svc.catalogue) == {"t2"}
    assert name_resolvable(person, svc.catalogue) == frozenset()
    ans = svc.answer("q", admission=ANALYST, plan=person)
    assert _value(ans) is None                    # not read as "… Ltd": who it is stays open


def test_p01_semi_link_that_matches_no_base_row_states_no_zero(lark4):
    """The customer IS found (an exact name, an identifier on the other side), but no open
    invoice carries its id: the link matched nothing, so no zero is stated either."""
    idle = [c for c in CUSTOMERS + EXTRA_CUSTOMERS if open_count(c[0]) == 0]
    assert idle                                   # non-vacuous: such a customer exists
    ans = lark4().answer("q", admission=ANALYST,
                         plan=DelegationPlan.model_validate(live_plan(idle[0][1])))
    assert _value(ans) is None and ans.coverage["status"] == "uncertain"
    assert not any("read as" in a for a in ans.plan["assumptions"])   # exact: nothing resolved


# ------------------------------------------------------------------------------------------------
# P-11
# ------------------------------------------------------------------------------------------------
def _ordered_plan(order_by: str) -> dict:
    p = live_plan("Pellucid Freightways Ltd")
    p["subtasks"][0]["fields"].append("customer_id")
    p["combine"]["group_by"] = ["customer_id"]
    p["combine"]["aggregate"] = {"op": "sum", "field": "total_gbp"}
    p["combine"]["order_by"] = order_by
    return p


def test_p11_order_by_the_input_field_is_repaired_without_asking(lark4):
    h, calls = _scripted([_ordered_plan("total_gbp")])
    ans = lark4(h).answer("total open by customer for Pellucid Freightways Ltd",
                          admission=ANALYST)
    assert ans.questions == [] and ans.status == "completed"
    assert sum(1 for c in calls if c["kind"] == "plan") == 1       # deterministic: no re-ask
    assert ans.plan["combine"]["order_by"] == "sum_total_gbp"
    assert any("total_gbp" in a and "sum_total_gbp" in a for a in ans.plan["assumptions"])


def test_p11_other_plan_errors_go_back_to_the_planner_first(lark4):
    bad = _ordered_plan("no_such_column")
    good = _ordered_plan("sum_total_gbp")
    h, calls = _scripted([bad, good])
    ans = lark4(h).answer("total open by customer for Pellucid Freightways Ltd",
                          admission=ANALYST)
    plans = [c for c in calls if c["kind"] == "plan"]
    assert len(plans) == 2                         # one repair round
    feedback = plans[1]["messages"][-1]["content"]
    assert "no_such_column" in feedback            # the planner was shown its own problem
    assert ans.questions == [] and ans.status == "completed"


def test_p11_unrepaired_problems_and_real_questions_still_reach_the_user(lark4):
    bad = _ordered_plan("no_such_column")
    h, calls = _scripted([bad])                     # the model repeats its mistake
    ans = lark4(h).answer("q", admission=ANALYST)
    assert ans.status == "stopped" and any("no_such_column" in q for q in ans.questions)
    asked = {"questions": ["Which customer do you mean?"]}
    h2, calls2 = _scripted([asked])
    ans2 = lark4(h2).answer("q", admission=ANALYST)
    assert ans2.questions == asked["questions"]
    assert sum(1 for c in calls2 if c["kind"] == "plan") == 1      # a real question: no re-ask


# ------------------------------------------------------------------------------------------------
# P-12 (narrator side)
# ------------------------------------------------------------------------------------------------
def test_p12_question_dates_and_ids_ground_a_restatement_bare_numbers_do_not(lark4):
    asked = "Pellucid Freightways"
    [cust] = holders(asked)
    want = open_count(cust[0])
    q = (f"how many open invoices does {asked} ({cust[0]}) have due between 1 October 2026 "
         f"and 31 December 2026, and is it 7?")
    restated = (f"{cust[0]} has {want} open invoices for the window 1 October 2026 to "
                f"31 December 2026.")
    bare = f"{cust[0]} has 7 open invoices."
    assert str(want) != "7"                        # non-vacuous: 7 is NOT the computed value

    def narrate(payload):
        ref = payload["rows"][0]["count_invoice_no"]["ref"]
        return {"claims": [{"text": restated, "citations": [ref]},
                           {"text": bare, "citations": [ref]}]}

    h, _calls = _scripted([live_plan(asked)], narrate=narrate)
    ans = lark4(h).answer(q, admission=ANALYST)
    by_text = {c["text"]: c for c in ans.claims}
    assert by_text[restated]["supported"], by_text[restated]["reason"]
    assert not by_text[bare]["supported"]          # the asker's bare number is not evidence
    assert "7" in by_text[bare]["reason"]


def test_p12_scope_literals_rule():
    from databridge_federated import scope_literals
    lit = scope_literals("invoices due 1 October 2026 to 2026-12-31 for C-1003, about 5?", None)
    assert "1 October 2026" in lit and "2026-12-31" in lit and "C-1003" in lit
    assert "5" not in re.findall(r"\d+", lit)

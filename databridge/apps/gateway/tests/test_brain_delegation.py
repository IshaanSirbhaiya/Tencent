"""`/brain/query` delegates cross-source questions (E2E run 2, N-14).

Routing is rules first, the harness router second; a delegated question is answered by the SAME
federated service `/federated/ask` uses, under the caller's operator grant. The synthetic world (a
real facts store, a real graph store, fake passages) and the scripted harness are the federated
package's test kit; every expected value is computed from its fixture rows, never written down.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import auth_headers

_KIT = Path(__file__).resolve().parents[3] / "packages" / "federated" / "tests"
for _p in (str(_KIT), str(_KIT.parent)):
    if _p not in sys.path:
        sys.path.append(_p)          # appended: never shadow this package's own `conftest`

from fed_testkit import (  # noqa: E402
    CRM,
    FOUNDER_PLAN,
    FOUNDER_Q,
    NOTES,
    PEOPLE,
    TICKETS,
    env,  # noqa: F401 - fixture
    expected_founder_table,
    scripted_harness,
    world,  # noqa: F401 - fixture
)

from databridge_gateway import brain_delegation as bd  # noqa: E402
from databridge_gateway import federated_routes  # noqa: E402


class _Router:
    """A harness stand-in for the router: records every call, answers with ``route``."""

    def __init__(self, route="federated", fail=False):
        self.calls, self.route, self.fail = [], route, fail

    def complete_structured(self, task, messages, model):
        self.calls.append({"task": task, "messages": messages})
        if self.fail:
            raise RuntimeError("router down")

        class _R:
            parsed = model(route=self.route, reason="scripted")
            event = None
        return _R()


def _cite_all(payload):
    claims = []
    for row in payload["rows"]:
        refs = [row[c]["ref"] for c in payload["columns"] if (row.get(c) or {}).get("ref")]
        text = "; ".join(f"{c}: {row[c]['value']}" for c in payload["columns"]
                         if (row.get(c) or {}).get("ref"))
        claims.append({"text": text, "citations": refs})
    return {"claims": claims}


# ================================================================================================
# routing — rules first
# ================================================================================================
@pytest.fixture
def catalogue(world):
    return world.service().catalogue


def _tables(catalogue):
    tables = bd.catalogue_tables(catalogue)
    assert tables, "the catalogue holds no facts table — every routing test would be vacuous"
    return tables


def test_aggregates_and_set_questions_route_to_delegation_by_rules(catalogue):
    """Derived from the catalogue's own table names: no domain noun is written here."""
    plural = bd._noun_forms(_tables(catalogue)[0])[1]
    router = _Router(route="graph")              # would contradict the rules if it were asked
    questions = [
        FOUNDER_Q,
        f"How many {plural} are there?",
        f"What is the average amount of {plural} by status?",
        f"Which {plural} are overdue?",
        f"List all {plural} due this quarter",
        f"{plural} due this quarter",
        f"customers who raised a complaint and have overdue {plural}",
        f"top 5 {plural} by amount",
        f"{plural} this month compared with last month",
    ]
    for q in questions:
        d = bd.route_question(q, catalogue=catalogue, router=router)
        assert d["route"] == bd.ROUTE_FEDERATED and d["decided_by"] == "rules", (q, d)
    assert router.calls == []                    # rules decided every one


def test_lookups_stay_on_the_graph_path_without_asking_the_router(catalogue):
    singular = bd._noun_forms(_tables(catalogue)[0])[0]
    router = _Router(route="federated")
    for q in ["which city is Corriedale Grocers based in?",
              "who is Pellucid Freightways' head of procurement?",
              f"what is the total of {singular} INV-2026-0041 including VAT?",
              f"what is the status of {singular} INV-2026-0057?",
              "Which project do we run for Tamsinbay Water Authority and who is their contact?",
              "what was the most recent meeting about?"]:
        d = bd.route_question(q, catalogue=catalogue, router=router)
        assert d["route"] == bd.ROUTE_GRAPH and d["decided_by"] == "rules", (q, d)
    assert router.calls == []


def test_an_undecided_question_goes_to_the_router_and_a_failure_stays_on_the_graph(catalogue):
    tables = _tables(catalogue)
    q = f"Do the meeting notes say anything about overdue {bd._noun_forms(tables[0])[1]}?"
    assert bd.route_by_rules(q, catalogue)["route"] == bd.ROUTE_UNDECIDED
    router = _Router(route="federated")
    d = bd.route_question(q, catalogue=catalogue, router=router)
    assert d["route"] == "federated" and d["decided_by"] == "router" and len(router.calls) == 1
    assert router.calls[0]["task"] == bd.ROUTER_TASK_CLASS
    assert tables[0] in router.calls[0]["messages"][0]["content"]   # it sees the catalogue
    down = bd.route_question(q, catalogue=catalogue, router=_Router(fail=True))
    assert down["route"] == bd.ROUTE_GRAPH and down["decided_by"] == "default"
    none = bd.route_question(q, catalogue=catalogue, router=None)
    assert none["route"] == bd.ROUTE_GRAPH and none["decided_by"] == "default"


def _domain_scanner():
    import importlib.util

    path = Path(__file__).resolve().parents[3] / "packages" / "graph" / "tests" /         "test_no_domain_branching.py"
    spec = importlib.util.spec_from_file_location("_ndb_guard", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_new_gateway_modules_name_no_domain_concept_in_code():
    """Rule 5, with the repo's own scanner (identifiers and non-docstring strings): the router's
    lexicon is generic English, table names come from the catalogue, and the N-15 refusal names
    no pack's graph."""
    guard = _domain_scanner()
    gw = Path(bd.__file__).resolve().parent
    hits = []
    for name in ("brain_delegation.py", "agent_ownership.py"):
        hits += guard._identifier_and_string_hits(gw / name, root=gw)
    assert not hits, hits
    from databridge_gateway.intelligence_routes import (NOTHING_ANSWERED_ALTERNATIVE,
                                                        NOTHING_ANSWERED_TEXT)
    for text in (NOTHING_ANSWERED_TEXT, NOTHING_ANSWERED_ALTERNATIVE):
        assert not guard._WORD.search(text.lower()), text
    # non-vacuous: the scanner fires on the sentence N-15 reported
    assert guard._WORD.search("nothing in the clinic graph answered that.")


# ================================================================================================
# end to end through /brain/query
# ================================================================================================
@pytest.fixture
def brain_app(world, monkeypatch):
    from databridge_gateway import create_app

    monkeypatch.setenv(federated_routes.GRANTS_ENV, json.dumps({"analyst-7": "*"}))
    holder = {}

    def mount(plan=FOUNDER_PLAN, narrate=_cite_all):
        h, calls = scripted_harness(plan, narrate)
        holder["calls"] = calls
        client.app.state.federated_factory = lambda: world.service(harness=h)
        return client

    with TestClient(create_app(), headers=auth_headers("analyst-7")) as client:
        client.app.state.brain_llm = None        # the graph path is the deterministic one
        yield mount, holder


def test_a_cross_source_question_is_delegated_and_returns_the_computed_table(world, brain_app):
    mount, _ = brain_app
    c = mount()
    r = c.post("/brain/query", json={"question": FOUNDER_Q, "by": "mallory"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["routed_to"] == "federated" and body["route"]["decided_by"] == "rules"
    expected = expected_founder_table(world.invoices)
    got = {row["cells"]["account_owner"]["value"]:
           Decimal(row["cells"]["sum_amount:this_quarter"]["value"])
           for row in body["table"]["rows"]}
    assert got and got == {k: v["this_quarter"] for k, v in expected.items()}
    for row in body["table"]["rows"]:                # every cell carries its own pointer
        for cell in row["cells"].values():
            assert cell is None or cell["pointer"]["kind"] == "derived"
    assert body["grounded"] is True and body["citations"]
    cov = body["coverage"]
    assert cov["reduced"] is bool(cov["skipped"] or cov["reasons"])
    assert body["candidate_flags"] == [] and body["requires_human_selection"] is False
    # the run is the caller's, never the body's `by`, and the Activity link names it
    rec = next(world.runs_dir.rglob(f"{body['run_id']}.json"))
    assert "analyst-7" in rec.parts and "mallory" not in rec.parts
    assert body["activity_url"].startswith(f"/trace?run={body['run_id']}")
    assert body["trace_request_id"] and f"request={body['trace_request_id']}" in \
        body["activity_url"]


def test_a_lookup_stays_on_the_graph_path_and_says_so(world, brain_app):
    mount, holder = brain_app
    c = mount()
    body = c.post("/brain/query", json={"question": "who is Pellucid Freightways' head of "
                                                    "procurement?"}).json()
    assert body.get("routed_to") == "graph" and body["route"]["route"] == "graph"
    assert holder["calls"] == [] and world.facts.calls == []      # nothing was delegated


def test_a_principal_without_a_grant_is_not_delegated_and_nothing_is_read(world, brain_app):
    mount, holder = brain_app
    c = mount()
    body = c.post("/brain/query", json={"question": FOUNDER_Q},
                  headers=auth_headers("fcc-3")).json()
    assert body["routed_to"] == "graph"
    assert body["route"]["delegation_refused"]["code"] == "FEDERATED_NOT_GRANTED"
    assert holder["calls"] == [] and world.facts.calls == [] and world.retriever.calls == []


def test_a_narrowed_grant_returns_reduced_coverage_and_no_half_computed_table(
        world, brain_app, monkeypatch):
    mount, _ = brain_app
    monkeypatch.setenv(federated_routes.GRANTS_ENV, json.dumps({"analyst-7": [CRM]}))
    c = mount()
    body = c.post("/brain/query", json={"question": FOUNDER_Q}).json()
    assert body["routed_to"] == "federated"
    assert body["coverage"]["reduced"] is True and body["grounded"] is False
    assert any(s.get("connection") == NOTES for s in body["coverage"]["skipped"])
    assert body["table"] == {}


def test_when_the_planner_asks_about_a_rule_routed_aggregate_its_question_is_the_answer(
        world, brain_app):
    """E2E run 5, W-03: the founder's total, routed by rule, met a planner question. The graph
    path computes no aggregate, so it could only narrate that the data "does not contain" the
    total; the planner's question goes back instead (the `/federated/ask` needs-input shape)."""
    mount, holder = brain_app
    c = mount(plan=None)                          # the scripted planner returns a question
    body = c.post("/brain/query", json={"question": FOUNDER_Q}).json()
    assert body["route"]["decided_by"] == "rules"
    assert body["route"]["rule"] in bd.AGGREGATE_RULES                  # non-vacuous
    assert body["routed_to"] == "federated" and body["answer_kind"] == "needs_clarification"
    assert body["needs_clarification"] is True and body["grounded"] is False
    assert body["questions"] and [i["text"] for i in body["question_items"]] == body["questions"]
    assert all(i["id"].startswith("fed:") for i in body["question_items"])
    assert body["run_id"] and body["activity_url"].startswith(f"/trace?run={body['run_id']}")
    assert "delegation_attempt" not in body and not body["citations"]
    assert [k["kind"] for k in holder["calls"]] == ["plan"]       # nothing was read or narrated


def test_when_the_planner_asks_about_a_router_routed_question_the_graph_answers_and_the_attempt_travels(
        world, brain_app):
    """A question the ROUTER (a model) sent to delegation was not shown to be an aggregate: the
    graph path still answers what it can, and the attempt travels with it."""
    mount, holder = brain_app
    c = mount(plan=None)
    c.app.state.brain_llm = _Router(route="federated")
    q = f"Do the meeting notes say anything about overdue {bd._noun_forms('invoices')[1]}?"
    body = c.post("/brain/query", json={"question": q}).json()
    assert body["route"]["decided_by"] == "router", body["route"]
    assert body["routed_to"] == "graph" and body["route"]["fallback_from"] == "federated"
    attempt = body["delegation_attempt"]
    assert attempt["questions"] and attempt["run_id"]
    assert attempt["activity_url"].startswith(f"/trace?run={attempt['run_id']}")
    assert [k["kind"] for k in holder["calls"]] == ["plan"]       # nothing was read or narrated


def test_a_delegation_failure_carries_no_exception_text(world, brain_app):
    """CONS standard 4: a fixed message and an opaque reference, never the exception's text."""
    mount, _ = brain_app
    c = mount()

    def boom():
        raise RuntimeError("secret-detail-7f3a in a private path")
    c.app.state.federated_factory = boom
    r = c.post("/brain/query", json={"question": FOUNDER_Q})
    route = r.json()["route"]
    assert route["delegation_unavailable"] and route["error_ref"].startswith("brain-")
    assert "secret-detail-7f3a" not in r.text and "RuntimeError" not in r.text


def test_candidate_rows_are_flagged_structurally(world, brain_app):
    mount, _ = brain_app
    plan = {
        "subtasks": [
            {"id": "tix", "adapter_agent": "crm_sources", "entity_kinds": ["tickets"],
             "fields": ["contact_email", "amount"]},
            {"id": "notes", "adapter_agent": "notes_sources", "entity_kinds": ["meeting"],
             "filters": [{"field": "text", "op": "contains", "value": "complaint"}],
             "time_window": {"axis": "valid_time", "period": "this_quarter"}}],
        "combine": {"base": "tix",
                    "links": [{"subtask": "notes", "key": "contact_email",
                               "entity_kind": "person"}],
                    "group_by": ["contact_email"], "aggregate": {"op": "sum", "field": "amount"}}}
    c = mount(plan=plan)
    body = c.post("/brain/query", json={
        "question": "Total amount by contact email, for people who complained this quarter"}
    ).json()
    assert body["routed_to"] == "federated", body
    identified = {p["person"]["canonical_id"] for p in PEOPLE if p["person"]["canonical_id"]}
    rows = body["table"]["rows"]
    assert {r["cells"]["contact_email"]["value"] for r in rows} == \
        {t["contact_email"] for t in TICKETS if t["contact_email"] in identified}
    flagged = {f["row"] for f in body["candidate_flags"] if f["where"] == "table_row"}
    assert flagged == {i for i, r in enumerate(rows) if r["candidate"]} and flagged
    assert body["requires_human_selection"] is True


def test_e2e3_p01_a_table_that_states_no_value_is_not_grounded():
    """E2E3 P-01: a delegated table whose only cell is null (a link matched nothing) is not a
    grounded answer; its refusal carries the engine's reason. A stated value still is."""
    why = "all rows / count_invoice_no: not found, so no value is stated"
    empty = {"status": "completed", "run_id": "r",
             "table": {"columns": ["count_invoice_no"],
                       "rows": [{"cells": {"count_invoice_no": None}}], "absent": [why]}}
    body = bd.federated_payload(empty, {"route": "federated"}, request_id=None)
    assert body["grounded"] is False and why in body["refusal"]
    stated = json.loads(json.dumps(empty))
    stated["table"]["rows"][0]["cells"]["count_invoice_no"] = {"value": 1, "pointer": {}}
    stated["table"]["absent"] = []
    body = bd.federated_payload(stated, {"route": "federated"}, request_id=None)
    assert body["grounded"] is True and body["refusal"] == ""


class _UnreadableFacts:
    """A facts store that exists but cannot be read (every statement raises a sharing violation)."""

    def __init__(self, inner):
        self.inner = inner

    def available(self, connection):
        return self.inner.available(connection)

    def query(self, connection, sql, **kw):
        raise PermissionError(13, "The process cannot access the file because it is being used "
                                  "by another process")


def test_e2e4_q01_a_refreshing_catalogue_is_said_never_planned_against(world, brain_app,
                                                                      monkeypatch):
    """E2E4 Q-01: while a facts store cannot be read, /brain/query never presents a plan over the
    partial catalogue: the delegated answer (or the graph route) says it is being refreshed."""
    from databridge_federated import build_service
    from databridge_federated import catalogue as catmod
    monkeypatch.setattr(catmod.time, "sleep", lambda _s: None)
    mount, holder = brain_app
    c = mount()
    h, calls = scripted_harness(FOUNDER_PLAN, _cite_all)

    def factory():
        svc = build_service(store=world.store, harness=h, facts=_UnreadableFacts(world.facts),
                            retriever=world.retriever, adapter_agents_root=world.aa_dir,
                            vocabulary=world.vocab, runs_dir=world.runs_dir)
        assert CRM in svc.catalogue.refreshing                         # non-vacuous
        return svc
    c.app.state.federated_factory = factory
    body = c.post("/brain/query", json={"question": FOUNDER_Q}).json()
    assert calls == []                                                 # no plan was asked for
    said = json.dumps(body)
    assert "being refreshed" in said and CRM in said
    assert "no invoices table" not in said.lower()
    if body.get("routed_to") == "federated":
        assert body["grounded"] is False and body["table"] == {}
    else:
        assert "catalogue_refreshing" in body["route"]


def test_a_table_name_is_routed_by_its_english_plural():
    """E2E run 5: "which facilities ..." never named a `facility` table ("facilitys")."""
    assert bd._noun_forms("facility") == ("facility", "facilities")
    assert bd._noun_forms("invoices") == ("invoice", "invoices")
    assert bd._noun_forms("survey") == ("survey", "surveys")
    assert bd._noun_forms("address") == ("address", "addresses")



# ================================================================================================
# Codex R-029 round 1
# ================================================================================================
def test_r1_4_partial_coverage_is_reduced_and_said_first():
    cov = bd.coverage_summary({"searched": ["x"], "skipped": [], "reasons": [],
                               "truncated": True, "exhaustive": False,
                               "excluded": ["x: the read stopped at its 20s time budget"]})
    assert cov["reduced"] is True and cov["partial"] is True
    assert bd.coverage_summary({"reduced": True})["reduced"] is True      # never overwritten
    body = bd.federated_payload({"status": "completed", "answer_markdown": "**Q**",
                                 "table": {"rows": [{"cells": {"n": {"value": 3}}}]},
                                 "coverage": {"skipped": [{"reason_code": "truncated"}],
                                              "reasons": ["capped"]}},
                                {"route": "federated"}, request_id=None)
    assert body["partial"] is True and body["answer"].startswith("> **Partial result.**")
    clean = bd.coverage_summary({"searched": ["x"], "skipped": [], "reasons": []})
    assert clean["reduced"] is False and clean["partial"] is False      # non-vacuous


def test_r1_8_a_period_is_not_a_record_and_an_unknown_id_is_not_one_record(catalogue):
    d = bd.route_by_rules("What was the total for Q1 invoices?", catalogue)
    assert d["route"] == bd.ROUTE_FEDERATED and d["rule"] == "total"
    q = "what is the total of invoice ZZ-9999-0001?"
    assert bd.route_by_rules(q, catalogue)["rule"] == "total_of_one_record"
    assert bd.route_by_rules(q, catalogue, names_record=lambda t: False)["route"] == \
        bd.ROUTE_FEDERATED
    assert bd.route_by_rules(q, catalogue, names_record=lambda t: True)["route"] == \
        bd.ROUTE_GRAPH


def test_r1_11_a_rule_routed_aggregate_asks_back_even_with_no_planning_model(world, brain_app):
    mount, _ = brain_app
    c = mount()
    c.app.state.federated_factory = lambda: world.service(harness=None)
    body = c.post("/brain/query", json={"question": FOUNDER_Q}).json()
    assert body["route"]["decided_by"] == "rules", body["route"]
    assert body["answer_kind"] == "needs_clarification" and body["questions"], body

"""Delegated cross-source querying (FED) — offline: a real facts store, a real graph store, the real
harness over a scripted FakeBackend, a fake passage retriever. No network, no live model.

Every expectation is COMPUTED from the fixture lists in `fed_testkit.py`; no number is written
down here.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from decimal import Decimal

import pytest

from fed_testkit import (
    CRM,
    FOUNDER_PLAN,
    FOUNDER_Q,
    NOTES,
    PEOPLE,
    TICKETS,
    TODAY,
    complainers_this_quarter,
    expected_founder_table,
    expected_ids,
    scripted_harness,
)

from databridge_federated import (
    CatalogueAdmission,
    DelegationPlan,
    expand,
    rows_digest,
)

ADMIN = CatalogueAdmission("dana.ops")
THIS, LAST = "sum_amount:this_quarter", "sum_amount:last_quarter"
CHANGE = "change:this_quarter-vs-last_quarter"


def _dec(cell) -> Decimal:
    return Decimal(str(cell["value"]))


def _owner_rows(ans) -> dict[str, dict]:
    return {r["cells"]["account_owner"]["value"]: r["cells"] for r in ans.table["rows"]}


def _cite_all(payload: dict) -> dict:
    """A faithful narrator: restates every value with its ref, verbatim."""
    claims = []
    for row in payload["rows"]:
        parts, refs = [], []
        for c in payload["columns"]:
            cell = row.get(c) or {}
            if cell.get("ref"):
                parts.append(f"{c}: {cell['value']}")
                refs.append(cell["ref"])
        claims.append({"text": "; ".join(parts), "citations": refs})
    return {"claims": claims, "clarifications": []}


# ------------------------------------------------------------------------------------------------
# (a) the founder's example, end to end
# ------------------------------------------------------------------------------------------------
def test_founder_question_matches_an_independent_computation(world):
    harness, calls = scripted_harness(FOUNDER_PLAN, _cite_all)
    ans = world.service(harness=harness).answer(FOUNDER_Q, admission=ADMIN)

    expected = expected_founder_table(world.invoices)
    assert expected, "fixture must produce a non-empty expected table"
    assert len(complainers_this_quarter()) >= 2                 # non-vacuous cohort
    got = _owner_rows(ans)
    assert set(got) == set(expected)
    for owner, exp in expected.items():
        assert _dec(got[owner][THIS]) == exp["this_quarter"]
        assert _dec(got[owner][LAST]) == exp["last_quarter"]
        assert _dec(got[owner][CHANGE]) == exp["this_quarter"] - exp["last_quarter"]
    # the note from LAST quarter (and the complaint-free note) did not admit anyone
    q2_only = {c for n in [x for x in __import__("fed_testkit").NOTES_DATA
                           if x["date"] < "2026-07-01"] for c in n["mentions"]}
    assert not {r["customer_id"] for r in world.invoices
                if r["account_owner"] in got and r["customer_id"] in q2_only} & \
        complainers_this_quarter()
    # the answer contract held: question verbatim, every claim supported, citations present
    assert ans.answer_markdown.count(FOUNDER_Q) == 1
    assert ans.unsupported_claims == []
    assert ans.citations
    assert ans.status == "completed"
    assert [c["kind"] for c in calls] == ["plan", "narrate"]
    # the run record exists with the §5.7 keys and produced_by on every step
    rec_path = next(world.runs_dir.rglob(f"{ans.run_id}.json"))
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    tools = [s["tool"] for s in rec["steps"]]
    assert tools[0] == "federated.plan" and "federated.combine" in tools
    assert {"delegate.ask:crm_sources", "delegate.ask:notes_sources"} <= set(tools)
    assert all(s["produced_by"] for s in rec["steps"])
    plan_step = rec["steps"][0]
    assert plan_step["produced_by"]["model"] == "fake/planner"
    combine_step = next(s for s in rec["steps"] if s["tool"] == "federated.combine")
    assert combine_step["produced_by"]["model"] is None
    assert combine_step["produced_by"]["by"] == "rules"
    assert rec["models_used"] == ["fake/planner"]


# ------------------------------------------------------------------------------------------------
# (b) every output value's pointer expands to exactly the contributing input cells
# ------------------------------------------------------------------------------------------------
def _resolve_query_cell(world, ptr) -> object:
    """Re-run the statement whose hash the pointer carries and read its (row, column) — the
    pointer is resolved, not trusted."""
    from databridge_mcp_ingest.tabular.count import count_query

    # The identity is the package's own (`count.query_identity`, R-022 f1 #6): the statement
    # alone only when it is unparameterised and ordered; otherwise it also binds the typed
    # parameters (and an unordered result's digest). So each recorded call is RE-RUN and its
    # identity compared, rather than re-deriving a hash of the statement text here.
    for sql, params in world.facts.calls:
        ans = count_query(ptr.source_id, sql, params=params)
        if any(p.query_hash == ptr.query_hash for p in ans.pointers):
            return ans.rows[ptr.row_ordinal], ans.columns
    raise AssertionError("no recorded statement matches the pointer's query hash")


def test_every_value_expands_to_exactly_its_input_cells(world):
    from databridge_core import SourcePointer

    harness, _ = scripted_harness(FOUNDER_PLAN, _cite_all)
    svc = world.service(harness=harness)
    ans = svc.answer(FOUNDER_Q, admission=ADMIN)
    checked = 0
    for row in ans.table["rows"]:
        owner = row["cells"]["account_owner"]["value"]
        for col, q in ((THIS, "this_quarter"), (LAST, "last_quarter")):
            ptr = SourcePointer.model_validate(row["cells"][col]["pointer"])
            want = expected_ids(world.invoices, owner, q)
            assert ptr.kind == "derived" and ptr.derivation == "SUM(amount)"
            assert ptr.row_count == len(want)
            assert ptr.rows_digest == rows_digest(f"{CRM}:invoices:{i}" for i in want)
            # R-021 r2 #8: the value derives from its amount cells PLUS one ELIGIBILITY pointer
            # (the complaint-note evidence that put those invoices in the cohort)
            elig = [p for p in ptr.derived_from if (p.derivation or "").startswith("ELIGIBILITY")]
            assert len(elig) == 1
            assert {x.source_id for x in expand(elig[0])} == {NOTES}
            leaves = [leaf for p in ptr.derived_from if p not in elig for leaf in expand(p)]
            assert len(leaves) == len(want)
            ids, total = set(), Decimal(0)
            for leaf in leaves:
                assert leaf.kind == "query_cell" and leaf.column == "amount"
                values, columns = _resolve_query_cell(world, leaf)
                rec = dict(zip(columns, values))
                ids.add(rec["invoice_id"])
                total += Decimal(repr(rec["amount"])) if isinstance(rec["amount"], float) \
                    else Decimal(str(rec["amount"]))
            assert ids == want                                   # exactly those cells
            assert total == Decimal(str(row["cells"][col]["value"]))
            assert "this_quarter" in (ptr.agg_window or "") or "last_quarter" in ptr.agg_window
            checked += 1
        delta = SourcePointer.model_validate(row["cells"][CHANGE]["pointer"])
        assert [p.derivation for p in delta.derived_from] == ["SUM(amount)", "SUM(amount)"]
        key = SourcePointer.model_validate(row["cells"]["account_owner"]["pointer"])
        key_inputs = [p for p in key.derived_from
                      if not (p.derivation or "").startswith("ELIGIBILITY")]
        assert key_inputs and all(leaf.column == "account_owner"
                                  for p in key_inputs for leaf in expand(p))
    assert checked >= 4                                          # non-vacuous


# ------------------------------------------------------------------------------------------------
# (c) pushdown: a single-source aggregate is ONE SQL statement
# ------------------------------------------------------------------------------------------------
def test_single_source_aggregate_is_pushed_down_as_one_query(world):
    svc = world.service(harness=None)                            # rules plan it: no model at all
    ans = svc.answer("What is the total amount of invoices by account owner?", admission=ADMIN)
    assert ans.status == "completed", ans.answer_markdown
    assert len(world.facts.calls) == 1
    sql = world.facts.calls[0][0]
    assert "GROUP BY" in sql and "invoices" in sql
    expected: dict[str, Decimal] = {}
    for r in world.invoices:
        expected[r["account_owner"]] = expected.get(r["account_owner"], Decimal(0)) + \
            Decimal(r["amount"])
    got = {r["cells"]["account_owner"]["value"]: Decimal(r["cells"]["sum_amount"]["value"])
           for r in ans.table["rows"]}
    assert got == expected
    assert ans.plan["combine"] is None and ans.plan["subtasks"][0]["aggregate"]["op"] == "sum"
    assert "planner" in ans.produced_by and ans.produced_by["planner"]["by"] == "rules"
    # the pushed-down value's pointer names its row set (verifiable without re-reading rows)
    from databridge_core import SourcePointer
    for r in ans.table["rows"]:
        p = SourcePointer.model_validate(r["cells"]["sum_amount"]["pointer"])
        owner = r["cells"]["account_owner"]["value"]
        ids = [x["invoice_id"] for x in world.invoices if x["account_owner"] == owner]
        assert p.row_count == len(ids)
        assert p.rows_digest == rows_digest(ids)
        # R-021 #9: the pointer expands to EXACTLY the contributing source cells (row key x
        # amount), not to one synthetic query cell - and those cells hold the total
        # Each leaf is RESOLVED against the facts store, not trusted: the row key (the table's
        # first column, which the pushed-down statement proved unique) x column, read from the
        # same snapshot the leaf is bound to.
        leaves = expand(p)
        assert {leaf.kind for leaf in leaves} == {"table_cell"}
        assert {leaf.column for leaf in leaves} == {"amount"}
        assert sorted(leaf.row_id for leaf in leaves) == sorted(ids)
        total = Decimal(0)
        for leaf in leaves:
            back = world.facts.inner.query(
                CRM, f'SELECT "{leaf.column}" FROM "{leaf.table}" WHERE "invoice_id" = ?',
                params=(leaf.row_id,))
            assert len(back.rows) == 1
            here, there = back.pointers[0].snapshot, leaf.snapshot   # the same facts file
            assert (here.content_hash, here.schema_hash) == (there.content_hash,
                                                             there.schema_hash)
            v = back.rows[0][0]
            total += Decimal(repr(v)) if isinstance(v, float) else Decimal(str(v))
        assert total == Decimal(r["cells"]["sum_amount"]["value"])
        assert "row_count" not in ans.table["columns"]           # internal metadata only


def test_count_question_rules_path_counts_exactly(world):
    ans = world.service().answer("How many invoices are there?", admission=ADMIN)
    assert len(world.facts.calls) == 1
    assert ans.table["rows"][0]["cells"]["count"]["value"] == len(world.invoices)


# ------------------------------------------------------------------------------------------------
# (d) parallel dispatch is measurably faster than serial
# ------------------------------------------------------------------------------------------------
def test_parallel_dispatch_beats_serial(world):
    from databridge_federated import FederatedService

    delay = 0.25
    plan = DelegationPlan.model_validate({"subtasks": [
        {"id": f"t{i}", "adapter_agent": "crm_sources", "entity_kinds": ["invoices"],
         "aggregate": {"op": "count"}} for i in range(3)]})

    class Slow(FederatedService):
        def run_subtask(self, task, **kw):
            time.sleep(delay)
            return super().run_subtask(task, **kw)

    base = world.service()
    timings = {}
    for label, width in (("parallel", 8), ("serial", 1)):
        svc = Slow(**{f: getattr(base, f) for f in base.__dataclass_fields__})
        svc.max_parallel = width
        t0 = time.perf_counter()
        results = svc.dispatch(list(plan.subtasks), admission=ADMIN)
        timings[label] = time.perf_counter() - t0
        assert all(not r.error for r in results.values())
    assert timings["serial"] >= 3 * delay * 0.9
    assert timings["parallel"] < timings["serial"] * 0.6, timings


def test_a_subtask_that_times_out_is_coverage_not_a_hang(world):
    from databridge_federated import FederatedService

    class Stuck(FederatedService):
        def run_subtask(self, task, **kw):
            if task.id == "slow":
                time.sleep(1.0)
            return super().run_subtask(task, **kw)

    base = world.service()
    svc = Stuck(**{f: getattr(base, f) for f in base.__dataclass_fields__})
    svc.subtask_timeout_s = 0.3
    plan = DelegationPlan.model_validate({"subtasks": [
        {"id": "slow", "adapter_agent": "crm_sources", "entity_kinds": ["invoices"],
         "aggregate": {"op": "count"}},
        {"id": "fast", "adapter_agent": "crm_sources", "entity_kinds": ["tickets"],
         "aggregate": {"op": "count"}}]})
    t0 = time.perf_counter()
    res = svc.dispatch(list(plan.subtasks), admission=ADMIN)
    assert time.perf_counter() - t0 < 0.9
    assert res["slow"].error and "timed out" in res["slow"].error
    assert not res["fast"].error


# ------------------------------------------------------------------------------------------------
# (e) an ungranted source is coverage, and the answer says so
# ------------------------------------------------------------------------------------------------
def test_ungranted_source_is_reported_as_reduced_coverage(world):
    harness, calls = scripted_harness(FOUNDER_PLAN, _cite_all)
    narrow = CatalogueAdmission("temp.contractor", connections={CRM})
    ans = world.service(harness=harness).answer(FOUNDER_Q, admission=narrow)
    assert ans.answer_markdown.startswith("> **Reduced coverage.**")
    reasons = " ".join(ans.coverage["reasons"])
    assert NOTES in reasons and "not granted" in reasons
    assert any(s.get("connection") == NOTES for s in ans.coverage["skipped"])
    assert NOTES not in ans.coverage["searched"] and CRM in ans.coverage["searched"]
    # the link cannot be evaluated, so NO number is computed from half the evidence
    assert ans.table == {} and ans.status == "stopped"
    assert world.retriever.calls == []                          # the refused source was not read
    assert [c["kind"] for c in calls] == ["plan"]               # nothing to narrate


# ------------------------------------------------------------------------------------------------
# (f) a person link across sources stays a candidate; never fuzzy
# ------------------------------------------------------------------------------------------------
def test_person_link_is_exact_and_stays_a_candidate(world):
    plan = DelegationPlan.model_validate({
        "subtasks": [
            {"id": "tix", "adapter_agent": "crm_sources", "entity_kinds": ["tickets"],
             "fields": ["contact_email", "amount"]},
            {"id": "notes", "adapter_agent": "notes_sources", "entity_kinds": ["meeting"],
             "filters": [{"field": "text", "op": "contains", "value": "complaint"}],
             "time_window": {"axis": "valid_time", "period": "this_quarter"}}],
        "combine": {"base": "tix",
                    "links": [{"subtask": "notes", "key": "contact_email",
                               "entity_kind": "person"}],
                    "group_by": ["contact_email"], "aggregate": {"op": "sum", "field": "amount"}}})
    ans = world.service().answer("Ticket amounts for people who complained", admission=ADMIN,
                                 plan=plan)
    identified = {p["person"]["canonical_id"] for p in PEOPLE if p["person"]["canonical_id"]}
    expected = {}
    for t in TICKETS:
        if t["contact_email"] in identified:
            expected[t["contact_email"]] = expected.get(t["contact_email"], Decimal(0)) + \
                Decimal(t["amount"])
    rows = ans.table["rows"]
    assert {r["cells"]["contact_email"]["value"] for r in rows} == set(expected)
    assert len(TICKETS) > sum(1 for t in TICKETS if t["contact_email"] in identified)  # some left
    for r in rows:
        assert r["candidate"] is True and r["requires_human_selection"] is True
        assert Decimal(r["cells"]["sum_amount"]["value"]) == \
            expected[r["cells"]["contact_email"]["value"]]
    assert "CANDIDATE" in ans.answer_markdown


# ------------------------------------------------------------------------------------------------
# (g) the model never does arithmetic; a number it invents is flagged
# ------------------------------------------------------------------------------------------------
def test_narrator_sees_only_the_computed_table_and_invented_numbers_are_flagged(world):
    seen = {}

    def narrate(payload):
        seen["payload"] = payload
        row = payload["rows"][0]
        good = row[THIS]
        wrong = str(Decimal(good["value"]) + Decimal("1"))       # an "arithmetic slip"
        return {"claims": [
            {"text": f"{row['account_owner']['value']} is owed {good['value']} this quarter.",
             "citations": [good["ref"], row["account_owner"]["ref"]]},
            {"text": f"That is roughly {wrong} in total.", "citations": [good["ref"]]}]}

    harness, _ = scripted_harness(FOUNDER_PLAN, narrate)
    ans = world.service(harness=harness).answer(FOUNDER_Q, admission=ADMIN)
    payload = seen["payload"]
    # the model received the computed table — every value in it, and nothing row-level
    table_values = {(c, str(r["cells"][c]["value"])) for r in ans.table["rows"]
                    for c in ans.table["columns"] if r["cells"].get(c)}
    got_values = {(c, str(r[c]["value"])) for r in payload["rows"] for c in payload["columns"]
                  if r.get(c) and r[c].get("value") is not None}
    assert got_values == {(c, v if c == "account_owner" else _norm(v))
                          for c, v in table_values}
    blob = json.dumps(payload)
    assert "INV-" not in blob                                   # no input rows reach the model
    assert len(ans.unsupported_claims) == 1
    assert ans.unsupported_claims[0]["reason"].startswith("number_not_in_cited_evidence")
    assert "**UNSUPPORTED**" in ans.answer_markdown


def _norm(v: str) -> str:
    d = Decimal(v)
    return format(d.normalize() if d == d.to_integral_value() else d, "f")


# ------------------------------------------------------------------------------------------------
# planner: unknown names are questions, never guesses
# ------------------------------------------------------------------------------------------------
def test_unknown_names_become_questions_and_nothing_is_read(world):
    bad = json.loads(json.dumps(FOUNDER_PLAN))
    bad["subtasks"][0]["adapter_agent"] = "billing_sources"
    bad["combine"]["group_by"] = ["region"]
    harness, calls = scripted_harness(bad)
    ans = world.service(harness=harness).answer(FOUNDER_Q, admission=ADMIN)
    assert ans.status == "stopped" and ans.questions
    assert any("billing_sources" in q for q in ans.questions)
    assert world.facts.calls == [] and world.retriever.calls == []
    # nothing was narrated; the planner was asked once, then shown its own plan's problems for
    # each repair round before they reached the user (E2E3 P-11)
    from databridge_federated.planner import PLAN_REPAIR_ROUNDS
    assert [c["kind"] for c in calls] == ["plan"] * (1 + PLAN_REPAIR_ROUNDS)

    bad2 = json.loads(json.dumps(FOUNDER_PLAN))
    bad2["combine"]["group_by"] = ["region"]
    harness, _ = scripted_harness(bad2)
    ans = world.service(harness=harness).answer(FOUNDER_Q, admission=ADMIN)
    assert any("region" in q for q in ans.questions)
    assert world.facts.calls == []


def test_no_model_and_no_rule_is_a_question(world):
    ans = world.service(harness=None).answer(FOUNDER_Q, admission=ADMIN)
    assert ans.status == "stopped" and ans.questions and world.facts.calls == []


# ------------------------------------------------------------------------------------------------
# links through declared graph edges (never fuzzy), and the contracts
# ------------------------------------------------------------------------------------------------
def test_graph_link_within_hops_uses_declared_edges_only(world):
    """A note mentions a PROJECT; the organisation is a party to that project. Linked through
    `party_to` within one hop — and not at all when that edge kind is not declared."""
    from databridge_core import SourcePointer
    from databridge_core.provenance import Trust
    from databridge_graph.kinds import EdgeKind, NodeKind
    from databridge_graph.schema import GraphEdge, GraphNode

    t = dt.datetime(2026, 8, 25, tzinfo=dt.timezone.utc)
    text = "Project Atlas steering: complaint about scope creep."
    span = SourcePointer(source_id=NOTES, kind="doc_span", document_id="note-atlas",
                         char_start=0, char_end=len(text), trust=Trust.UNTRUSTED)
    s = world.store
    s.add_node(GraphNode(id="project:atlas", kind=NodeKind("project"), label="Atlas",
                         sources=(span,), valid_from=t, system_from=t))
    s.add_node(GraphNode(id="meeting:note-atlas", kind=NodeKind("meeting"), label="Atlas mtg",
                         attrs={"date": "2026-08-25"}, attr_sources={"date": span},
                         sources=(span,), valid_from=t, system_from=t))
    s.add_edge(GraphEdge(id="e:m-atlas", src_id="meeting:note-atlas", dst_id="project:atlas",
                         kind=EdgeKind("mentions"), sources=(span,), valid_from=t, system_from=t))
    s.add_edge(GraphEdge(id="e:c6-atlas", src_id="organisation:c-006", dst_id="project:atlas",
                         kind=EdgeKind("party_to"), sources=(span,), valid_from=t, system_from=t))
    world.retriever.passages.append({"chunk_id": "note-atlas#0", "doc_id": "note-atlas",
                                     "source_id": NOTES, "text": text, "locator": "p1",
                                     "approx_locator": False, "score": 1.0, "pointer": span})

    def plan(edge_kinds):
        return DelegationPlan.model_validate({
            "subtasks": [
                {"id": "inv", "adapter_agent": "crm_sources", "entity_kinds": ["invoices"],
                 "fields": ["customer_id", "amount"]},
                {"id": "notes", "adapter_agent": "notes_sources", "entity_kinds": ["meeting"],
                 "filters": [{"field": "text", "op": "contains", "value": "scope creep"}]}],
            "combine": {"base": "inv",
                        "links": [{"subtask": "notes", "key": "customer_id",
                                   "entity_kind": "organisation", "via": "graph", "max_hops": 1,
                                   "edge_kinds": edge_kinds}],
                        "group_by": ["customer_id"], "aggregate": {"op": "count"}}})

    ans = world.service().answer("q", admission=ADMIN, plan=plan(["party_to"]))
    got = {r["cells"]["customer_id"]["value"]: r["cells"]["count"]["value"]
           for r in ans.table["rows"]}
    assert got == {"C-006": sum(1 for r in world.invoices if r["customer_id"] == "C-006")}
    none = world.service().answer("q", admission=ADMIN, plan=plan(["works_for"]))
    assert none.table["rows"] == []


def test_contracts_are_closed_and_reject_sql_in_names():
    from databridge_federated import Filter, SubTask

    with pytest.raises(Exception):
        SubTask.model_validate({"adapter_agent": "a", "fields": ["amount; DROP TABLE x"]})
    with pytest.raises(Exception):
        Filter.model_validate({"field": "a", "op": "eq"})
    with pytest.raises(Exception):
        SubTask.model_validate({"adapter_agent": "a", "write": True})
    with pytest.raises(Exception):
        DelegationPlan.model_validate({"subtasks": [{"id": "t1", "adapter_agent": "a"}],
                                       "combine": {"base": "nope"}})


def test_periods_resolve_from_the_injected_clock():
    from databridge_federated import resolve_period
    assert resolve_period("this_quarter", TODAY) == (dt.date(2026, 7, 1), dt.date(2026, 10, 1))
    assert resolve_period("last_quarter", dt.date(2026, 2, 3)) == (dt.date(2025, 10, 1),
                                                                   dt.date(2026, 1, 1))
    assert resolve_period("last_month", dt.date(2026, 1, 9)) == (dt.date(2025, 12, 1),
                                                                 dt.date(2026, 1, 1))


def test_filter_values_are_bound_never_spliced(world):
    hostile = "overdue' OR '1'='1"
    plan = DelegationPlan.model_validate({"subtasks": [
        {"id": "t1", "adapter_agent": "crm_sources", "entity_kinds": ["invoices"],
         "filters": [{"field": "status", "op": "eq", "value": hostile}],
         "aggregate": {"op": "count"}}]})
    ans = world.service().answer("q", admission=ADMIN, plan=plan)
    assert ans.table["rows"][0]["cells"]["count"]["value"] == 0
    assert all(hostile not in sql for sql, _ in world.facts.calls)
    assert re.search(r"= \?", world.facts.calls[0][0])

"""Regression tests for the SECOND Codex review of delegated querying (R-021 round 2, f2).

One test (or a small group) per finding, named ``test_r2_f<N>_…``. Each was run and seen to FAIL
against the pre-fix code before the fix landed. Expectations are computed from fixture lists
(`fed_testkit.py`, or the small tables written here), never written down.
"""
from __future__ import annotations

import json
import threading
from decimal import Decimal
from types import SimpleNamespace

import pytest

from fed_testkit import CRM, NOTES, TICKETS, TODAY, _write_facts, scripted_harness

from databridge_core import GroundedValue, SourcePointer
from databridge_core.provenance import Trust
from databridge_federated import (
    CatalogueAdmission,
    CombineError,
    CombineSpec,
    DelegationPlan,
    ResultRow,
    SubResult,
    SubTask,
    combine,
    expand,
    validate_plan,
)
from databridge_federated.planner import complete_plan

ADMIN = CatalogueAdmission("dana.ops")
ODD = "odd-db"


def _plan(d: dict) -> DelegationPlan:
    return DelegationPlan.model_validate(d)


def _cell(row: str, col: str, table: str = "t") -> SourcePointer:
    return SourcePointer(source_id=CRM, kind="table_cell", table=table, column=col, row_id=row,
                         trust=Trust.UNTRUSTED)


def _odd_source(world, tables: dict[str, tuple[list[str], list[dict]]]) -> None:
    """A second REAL facts store, ``odd-db``, owned by adapter agent ``odd_sources``."""
    from databridge_workflow_spec import AdapterAgentSpec, dump_spec

    _write_facts(world.root / "facts", ODD, tables)
    spec = AdapterAgentSpec.model_validate({
        "id": "odd_sources", "title": "odd sources", "created_from": "keep odd-db right",
        "connections": [ODD], "extract": {"pack": "workspace"}, "status": "active"})
    (world.aa_dir / "odd_sources.yaml").write_text(dump_spec(spec), encoding="utf-8")


# ------------------------------------------------------------------------------------------------
# 2 (Critical) — an anti-link never excludes a row on a person (candidate) match
# ------------------------------------------------------------------------------------------------
def _person_anti_plan() -> dict:
    return {"subtasks": [
        {"id": "tix", "adapter_agent": "crm_sources", "entity_kinds": ["tickets"],
         "fields": ["contact_email", "amount"]},
        {"id": "tix2", "adapter_agent": "crm_sources", "entity_kinds": ["tickets"],
         "fields": ["contact_email"],
         "filters": [{"field": "amount", "op": "gt", "value": 100}]}],
        "combine": {"base": "tix", "links": [
            {"subtask": "tix2", "key": "contact_email", "other_field": "contact_email",
             "key_kind": "person", "mode": "anti"}],
            "group_by": ["contact_email"], "aggregate": {"op": "count"}}}


def test_r2_f2_person_anti_link_is_refused_at_planning(world):
    qs = validate_plan(_plan(_person_anti_plan()), world.service().catalogue)
    assert any("anti" in q.lower() and "person" in q.lower() for q in qs), qs
    ans = world.service().answer("q", admission=ADMIN, plan=_plan(_person_anti_plan()))
    assert ans.status == "stopped" and world.facts.calls == []


def test_r2_f2_combine_refuses_a_candidate_anti_link(world):
    """Even a plan that bypassed validation cannot exclude rows on a person match."""
    cat = world.service().catalogue
    base = SubResult(subtask_id="b", adapter_agent="a", connection=CRM, key_field="id", rows=(
        ResultRow(row_id="r1", cells={"id": GroundedValue(value="r1", pointer=_cell("r1", "id")),
                                      "email": GroundedValue(value="x@y.test",
                                                             pointer=_cell("r1", "email"))}),))
    other = SubResult(subtask_id="o", adapter_agent="a", connection=CRM, key_field="id", rows=(
        ResultRow(row_id="o1", cells={"email": GroundedValue(value="x@y.test",
                                                             pointer=_cell("o1", "email"))}),))
    spec = CombineSpec.model_validate({"base": "b", "links": [
        {"subtask": "o", "key": "email", "other_field": "email", "key_kind": "person",
         "mode": "anti"}]})
    with pytest.raises(CombineError, match="candidate"):
        combine(spec, {"b": base, "o": other}, today=TODAY, catalogue=cat)


def test_r2_f2_graph_anti_link_through_a_person_is_refused(world):
    from test_review_r021 import _add_note, _span

    text = "Project Cedar sync: complaint about the handover."
    sp = _span("note-cedar", text)
    _add_note(world, "note-cedar", text, "2026-08-27",
              edges=[("meeting:note-cedar", "project:cedar", "mentions"),
                     ("person:cedar-lead", "project:cedar", "party_to"),
                     ("person:cedar-lead", "organisation:c-004", "works_for")],
              nodes=[("project:cedar", "project", "Cedar", None, sp),
                     ("person:cedar-lead", "person", "Cedar Lead", "lead@cedar.test", sp)])
    plan = _plan({
        "subtasks": [
            {"id": "inv", "adapter_agent": "crm_sources", "entity_kinds": ["invoices"],
             "fields": ["customer_id"]},
            {"id": "notes", "adapter_agent": "notes_sources", "entity_kinds": ["meeting"],
             "filters": [{"field": "text", "op": "contains", "value": "cedar"}]}],
        "combine": {"base": "inv",
                    "links": [{"subtask": "notes", "key": "customer_id",
                               "entity_kind": "organisation", "via": "graph", "max_hops": 2,
                               "edge_kinds": ["party_to", "works_for"], "mode": "anti"}],
                    "group_by": ["customer_id"], "aggregate": {"op": "count"}}})
    ans = world.service().answer("q", admission=ADMIN, plan=plan)
    assert ans.table == {}                           # nothing excluded on a candidate match
    assert any(s.get("reason_code") == "combine_failed" and "candidate" in s["reason"]
               for s in ans.coverage["skipped"])


# ------------------------------------------------------------------------------------------------
# 3 (Critical) — candidate status is structural in the output, whoever narrates
# ------------------------------------------------------------------------------------------------
def _person_semi_plan() -> dict:
    p = _person_anti_plan()
    p["combine"]["links"][0]["mode"] = "semi"
    return p


def test_r2_f3_rules_narration_marks_every_candidate_claim(world):
    ans = world.service(harness=None).answer("q", admission=ADMIN, plan=_plan(_person_semi_plan()))
    rows = ans.table["rows"]
    assert rows and all(r["requires_human_selection"] for r in rows)       # non-vacuous
    assert ans.claims and all(c["requires_human_selection"] for c in ans.claims)
    assert all(c["text"].startswith("Candidate — requires human selection") for c in ans.claims)
    assert ans.requires_human_selection is True


def test_r2_f3_model_claim_without_candidate_wording_is_rewritten(world):
    def narrate(payload):                          # a model that ignores the instruction
        return {"claims": [{"text": f"{r['contact_email']['value']} has "
                                    f"{r['count']['value']} tickets",
                            "citations": [r["contact_email"]["ref"], r["count"]["ref"]]}
                           for r in payload["rows"]]}

    harness, _ = scripted_harness(None, narrate)
    ans = world.service(harness=harness).answer("q", admission=ADMIN,
                                                plan=_plan(_person_semi_plan()))
    assert ans.claims and ans.unsupported_claims == []
    for c in ans.claims:
        assert c["requires_human_selection"] is True and c["subject_candidate"] is True
        assert c["text"].startswith("Candidate — requires human selection")


def test_r2_f3_a_claim_naming_a_candidate_row_is_a_candidate_whatever_it_cites(world):
    """The model states a candidate row's identifier but cites only a non-candidate value."""
    from databridge_federated.combine import CombinedTable, TableRow
    from databridge_federated.service import enforce_candidates

    svc = world.service()
    cand = TableRow(cells={"who": GroundedValue(value="dana@larkspur.test",
                                                pointer=_cell("r1", "who"))}, candidate=True)
    plain = TableRow(cells={"n": GroundedValue(value=5, pointer=_cell("r2", "n"))})
    table = CombinedTable(columns=["who", "n"], rows=[cand, plain], steps=[])
    registry, view = svc._registry(table)
    n_ref = view[1]["n"]["ref"]
    from databridge_agent_runtime import FinalAnswer
    from databridge_agent_runtime.answer import Claim
    final = FinalAnswer(claims=[Claim(text="dana@larkspur.test owes 5", citations=[n_ref])])
    out = enforce_candidates(final, table, view)
    c = out.claims[0]
    assert c.text.startswith("Candidate — requires human selection")
    assert view[0]["who"]["ref"] in c.citations   # the candidate's own ref is now cited
    from databridge_agent_runtime import check_answer
    assert check_answer(out, registry).claims[0].subject_candidate is True


# ------------------------------------------------------------------------------------------------
# 4 (High) — a linked sub-task selects the field it is joined on
# ------------------------------------------------------------------------------------------------
def test_r2_f4_linked_field_is_selected_not_silently_empty(world):
    p = _person_semi_plan()
    p["subtasks"][1]["fields"] = ["ticket_id"]       # the join field is NOT listed
    plan = _plan(p)
    assert validate_plan(plan, world.service().catalogue) == []
    done = complete_plan(plan, world.service().catalogue)
    assert "contact_email" in next(t for t in done.subtasks if t.id == "tix2").fields
    ans = world.service().answer("q", admission=ADMIN, plan=plan)
    big = {t["contact_email"] for t in TICKETS if Decimal(t["amount"]) > 100}
    want: dict[str, int] = {}
    for t in TICKETS:
        if t["contact_email"] in big:
            want[t["contact_email"]] = want.get(t["contact_email"], 0) + 1
    got = {r["cells"]["contact_email"]["value"]: r["cells"]["count"]["value"]
           for r in ans.table["rows"]}
    assert want and got == want


def test_r2_f4_combine_refuses_a_link_field_the_other_side_never_returned(world):
    cat = world.service().catalogue
    base = SubResult(subtask_id="b", adapter_agent="a", connection=CRM, key_field="id", rows=(
        ResultRow(row_id="r1", cells={"id": GroundedValue(value="r1", pointer=_cell("r1", "id")),
                                      "k": GroundedValue(value="v", pointer=_cell("r1", "k"))}),))
    other = SubResult(subtask_id="o", adapter_agent="a", connection=CRM, key_field="id", rows=(
        ResultRow(row_id="o1", cells={"id": GroundedValue(value="o1",
                                                          pointer=_cell("o1", "id"))}),))
    spec = CombineSpec.model_validate({"base": "b", "links": [
        {"subtask": "o", "key": "k", "other_field": "k", "key_kind": "organisation"}]})
    with pytest.raises(CombineError, match="'k'"):
        combine(spec, {"b": base, "o": other}, today=TODAY, catalogue=cat)


def test_r2_f4_document_text_link_needs_a_text_search(world):
    plan = _plan({"subtasks": [
        {"id": "inv", "adapter_agent": "crm_sources", "entity_kinds": ["invoices"],
         "fields": ["customer_id"]},
        {"id": "notes", "adapter_agent": "notes_sources", "entity_kinds": ["meeting"]}],
        "combine": {"base": "inv", "links": [
            {"subtask": "notes", "key": "customer_id", "other_field": "text",
             "key_kind": "organisation"}]}})
    assert any("'text'" in q for q in validate_plan(plan, world.service().catalogue))


# ------------------------------------------------------------------------------------------------
# 5 (High) — a pushed-down aggregate keeps order_by and top_k
# ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("order_by", ["sum_amount", None])
def test_r2_f5_pushdown_honours_order_and_top_k(world, order_by):
    comb = {"base": "inv", "group_by": ["account_owner"],
            "aggregate": {"op": "sum", "field": "amount"}, "top_k": 2, "descending": True}
    if order_by:
        comb["order_by"] = order_by
    plan = _plan({"subtasks": [{"id": "inv", "adapter_agent": "crm_sources",
                                "entity_kinds": ["invoices"]}], "combine": comb})
    ans = world.service().answer("q", admission=ADMIN, plan=plan)
    totals: dict[str, Decimal] = {}
    for r in world.invoices:
        totals[r["account_owner"]] = totals.get(r["account_owner"], Decimal(0)) + \
            Decimal(r["amount"])
    want = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:2]
    got = [(r["cells"]["account_owner"]["value"], Decimal(r["cells"]["sum_amount"]["value"]))
           for r in ans.table["rows"]]
    assert len(totals) > 2 and got == want
    assert len(world.facts.calls) == 1 and "GROUP BY" in world.facts.calls[0][0]   # still pushed


# ------------------------------------------------------------------------------------------------
# 6 (High) — an empty cohort counts 0, with a pointer
# ------------------------------------------------------------------------------------------------
def test_r2_f6_empty_cohort_counts_zero_with_a_pointer(world):
    absent = "NO-SUCH-CUSTOMER"
    assert all(r["customer_id"] != absent for r in world.invoices)       # non-vacuous
    plan = _plan({"subtasks": [{"id": "inv", "adapter_agent": "crm_sources",
                                "entity_kinds": ["invoices"], "fields": ["customer_id"]}],
                  "combine": {"base": "inv", "aggregate": {"op": "count"},
                              "filters": [{"field": "customer_id", "op": "eq",
                                           "value": absent}]}})
    ans = world.service().answer("q", admission=ADMIN, plan=plan)
    rows = ans.table["rows"]
    assert len(rows) == 1 and rows[0]["cells"]["count"]["value"] == 0
    p = SourcePointer.model_validate(rows[0]["cells"]["count"]["pointer"])
    assert p.kind == "derived" and p.row_count == 0 and p.snapshot is not None


def test_r2_f6_empty_base_read_counts_zero_per_period(world):
    plan = _plan({"subtasks": [{"id": "inv", "adapter_agent": "crm_sources",
                                "entity_kinds": ["invoices"], "fields": ["due_on"],
                                "filters": [{"field": "status", "op": "eq",
                                             "value": "never-a-status"}]}],
                  "combine": {"base": "inv", "aggregate": {"op": "count"},
                              "compare": {"field": "due_on",
                                          "periods": ["this_quarter", "last_quarter"]}}})
    ans = world.service().answer("q", admission=ADMIN, plan=plan)
    rows = ans.table["rows"]
    assert len(rows) == 1
    for col in ("count:this_quarter", "count:last_quarter", "change:this_quarter-vs-last_quarter"):
        assert rows[0]["cells"][col]["value"] == 0, col
        assert rows[0]["cells"][col]["pointer"]["kind"] == "derived"


# ------------------------------------------------------------------------------------------------
# 7 (High) — no source character can corrupt a pushed-down value or pointer
# ------------------------------------------------------------------------------------------------
def test_r2_f7_separator_characters_in_keys_and_values(world):
    rows = [{"k": "a\x1eb", "amount": "5", "note": None},
            {"k": "c\x1fd", "amount": "7", "note": "\x1d"},
            {"k": "e", "amount": "11", "note": "plain"}]
    _odd_source(world, {"odd": (["k", "amount", "note"], rows)})
    svc = world.service()

    def run(agg):
        t = SubTask.model_validate({"adapter_agent": "odd_sources", "entity_kinds": ["odd"],
                                    "aggregate": agg})
        r = svc.run_subtask(t, admission=ADMIN)
        assert not r.error, r.error
        return r.rows[0].cells

    cells = run({"op": "count"})
    assert cells["count"].value == len(rows)
    assert sorted(p.row_id for p in expand(cells["count"].pointer)) == sorted(r["k"] for r in rows)
    cells = run({"op": "count", "field": "note"})
    assert cells["count_note"].value == sum(1 for r in rows if r["note"] is not None)
    cells = run({"op": "sum", "field": "amount"})
    assert cells["sum_amount"].value == sum(Decimal(r["amount"]) for r in rows)


# ------------------------------------------------------------------------------------------------
# 8 (High) — a combined row's values are derived and cite what made the row eligible
# ------------------------------------------------------------------------------------------------
def _founder_rows_plan() -> dict:
    return {"subtasks": [
        {"id": "inv", "adapter_agent": "crm_sources", "entity_kinds": ["invoices"],
         "fields": ["customer_id", "amount"]},
        {"id": "notes", "adapter_agent": "notes_sources", "entity_kinds": ["meeting"],
         "filters": [{"field": "text", "op": "contains", "value": "complaint"}],
         "time_window": {"axis": "valid_time", "period": "this_quarter"},
         "link_edges": ["mentions"]}],
        "combine": {"base": "inv", "links": [{"subtask": "notes", "key": "customer_id",
                                               "entity_kind": "organisation",
                                               "via": "canonical_id"}]}}


def test_r2_f8_selected_cells_are_derived_and_cite_the_link(world):
    ans = world.service().answer("q", admission=ADMIN, plan=_plan(_founder_rows_plan()))
    rows = ans.table["rows"]
    assert rows
    for r in rows:
        p = SourcePointer.model_validate(r["cells"]["amount"]["pointer"])
        assert p.kind == "derived"
        leaves = expand(p)
        assert any(x.kind == "query_cell" and x.column == "amount" for x in leaves)
        assert any(x.source_id == NOTES for x in leaves)          # the link's evidence
    cited = {leaf.source_id for c in ans.citations
             for leaf in expand(SourcePointer.model_validate(c))}
    assert NOTES in cited                        # registered for narration, and cited


def test_r2_f8_aggregates_cite_eligibility_too(world):
    from fed_testkit import FOUNDER_PLAN, FOUNDER_Q
    harness, _ = scripted_harness(FOUNDER_PLAN)
    ans = world.service(harness=harness).answer(FOUNDER_Q, admission=ADMIN)
    assert ans.table["rows"]
    for r in ans.table["rows"]:
        p = SourcePointer.model_validate(r["cells"]["sum_amount:this_quarter"]["pointer"])
        elig = [x for x in p.derived_from if (x.derivation or "").startswith("ELIGIBILITY")]
        assert len(elig) == 1 and any(x.source_id == NOTES for x in expand(elig[0]))


# ------------------------------------------------------------------------------------------------
# 9 (High) — a live row missing a requested column is excluded and counted
# ------------------------------------------------------------------------------------------------
def test_r2_f9_live_rows_missing_columns_are_excluded(world, tmp_path):
    from databridge_federated import run_live
    from test_review_r021 import _Gateway

    entry = world.service().catalogue.agent("crm_sources").connections[0]
    task = SubTask.model_validate({"adapter_agent": "crm_sources", "entity_kinds": ["tickets"],
                                   "fields": ["amount"], "freshness": "live"})
    data = [{"ticket_id": "T-1", "amount": "1"}, {"ticket_id": "T-2"},
            {"ticket_id": "T-3", "amount": None}, {"ticket_id": "T-3", "amount": "4"}]
    res = run_live(task, entry, gateway=_Gateway(data), today=TODAY, principal="x",
                   run_id=None, capture_root=tmp_path)
    assert [r.row_id for r in res.rows] == [f"{CRM}:tickets:T-1"]
    joined = " ".join(res.coverage.excluded)
    assert "missing" in joined and "amount" in joined and "T-3" in joined


# ------------------------------------------------------------------------------------------------
# 10 (High) — row keys are measured and verified, never the first column by assumption
# ------------------------------------------------------------------------------------------------
def test_r2_f10_key_is_a_verified_unique_column(world):
    rows = [{"grp": "g1", "id": "i1", "amount": "1"}, {"grp": "g1", "id": "i2", "amount": "2"},
            {"grp": "g2", "id": "i3", "amount": "4"}]
    _odd_source(world, {"dup": (["grp", "id", "amount"], rows),
                        "nokey": (["grp", "amount"], [{"grp": "g", "amount": "1"},
                                                      {"grp": "g", "amount": "1"}])})
    svc = world.service()
    odd = svc.catalogue.agent("odd_sources").connections[0]
    assert odd.table("dup").key == "id"             # not "grp", the first column
    assert odd.table("nokey").key is None
    res = svc.run_subtask(SubTask.model_validate({"adapter_agent": "odd_sources",
                                                  "entity_kinds": ["dup"],
                                                  "fields": ["grp"]}), admission=ADMIN)
    assert sorted(r.row_id for r in res.rows) == sorted(f"{ODD}:dup:{r['id']}" for r in rows)
    res = svc.run_subtask(SubTask.model_validate({"adapter_agent": "odd_sources",
                                                  "entity_kinds": ["nokey"],
                                                  "fields": ["grp"]}), admission=ADMIN)
    assert res.rows == () and res.error and "key" in res.error


def test_r2_f10_plain_rows_with_a_duplicated_key_are_excluded():
    from databridge_federated.catalogue import FactsTable
    from databridge_federated.delegates import _plain_rows

    table = FactsTable(name="t", columns=("id", "amount"), key="id")
    ans = SimpleNamespace(columns=["id", "amount"], rows=[("r1", "1"), ("r1", "2"), ("r2", "3")],
                          pointers=[_cell(f"x{i}", c) for i in range(3) for c in ("id", "a")])
    rows, excluded = _plain_rows(ans, CRM, table)
    assert [r.row_id for r in rows] == [f"{CRM}:t:r2"]
    assert excluded and "r1" in " ".join(excluded)


# ------------------------------------------------------------------------------------------------
# 11 (Medium) — cancellation is checked between graph reads
# ------------------------------------------------------------------------------------------------
def test_r2_f11_cancel_stops_before_the_edge_read(world):
    from databridge_federated import run_documents
    from databridge_federated.delegates import DelegateCancelled

    cancel = threading.Event()
    seen: list[str] = []

    class Store:
        def __init__(self, inner):
            self.inner = inner

        def fetchall(self, sql, *a, **k):
            seen.append(sql)
            cancel.set()                                  # the deadline passes DURING this read
            return self.inner.fetchall(sql, *a, **k)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    svc = world.service()
    entry = svc.catalogue.agent("notes_sources").connections[0]
    task = SubTask.model_validate({"adapter_agent": "notes_sources", "entity_kinds": ["meeting"]})
    with pytest.raises(DelegateCancelled):
        run_documents(task, entry, store=Store(world.store), retriever=world.retriever,
                      catalogue=svc.catalogue, today=TODAY, principal="x", run_id=None,
                      admits=svc.admits(ADMIN), cancel=cancel)
    assert len(seen) == 1 and "nodes" in seen[0]         # the edge read never started


# ------------------------------------------------------------------------------------------------
# coordinator request (Activity page): the run record keeps the plan's assumptions and questions
# ------------------------------------------------------------------------------------------------
def _plan_step(world, run_id: str) -> dict:
    rec = json.loads(next(world.runs_dir.rglob(f"{run_id}.json")).read_text(encoding="utf-8"))
    return next(s for s in rec["steps"] if s["tool"] == "federated.plan")


def test_r2_run_record_keeps_plan_assumptions_and_questions(world):
    from fed_testkit import FOUNDER_PLAN, FOUNDER_Q
    harness, _ = scripted_harness(FOUNDER_PLAN)
    ans = world.service(harness=harness).answer(FOUNDER_Q, admission=ADMIN)
    step = _plan_step(world, ans.run_id)
    assert FOUNDER_PLAN["assumptions"]                                   # non-vacuous
    assert step["args"]["assumptions"] == FOUNDER_PLAN["assumptions"]
    assert step["args"]["questions"] == [] and step["args"]["question"] == FOUNDER_Q

    bad = json.loads(json.dumps(FOUNDER_PLAN))                           # a stopped run
    bad["subtasks"][0]["time_window"] = {"axis": "valid_time", "field": "due_on",
                                         "period": "this_quarter"}
    harness, _ = scripted_harness(bad)
    ans = world.service(harness=harness).answer(FOUNDER_Q, admission=ADMIN)
    step = _plan_step(world, ans.run_id)
    assert ans.questions and step["args"]["questions"] == ans.questions
    assert step["args"]["assumptions"] == bad["assumptions"]


def test_r2_f10_truncation_is_judged_on_raw_rows(world):
    """Excluding a row must not hide that the read was capped (limit + 1 raw rows came back)."""
    svc = world.service()
    limit = 2
    assert len(TICKETS) > limit                                          # non-vacuous
    res = svc.run_subtask(SubTask.model_validate({"adapter_agent": "crm_sources",
                                                  "entity_kinds": ["tickets"], "limit": limit,
                                                  "fields": ["amount"]}), admission=ADMIN)
    assert len(res.rows) == limit and res.coverage.truncated is True
    assert [r.row_id for r in res.rows] == [f"{CRM}:tickets:{t['ticket_id']}"
                                            for t in sorted(TICKETS, key=lambda t: t["ticket_id"])
                                            ][:limit]

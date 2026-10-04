"""Regression tests for the first Codex review of delegated querying (R-021, f1).

One test (or a small group) per finding, named ``test_f<N>_…``. Each was written to FAIL against
the pre-fix code. Expectations are computed from the fixture lists in `fed_testkit.py`.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest

from fed_testkit import CRM, FOUNDER_PLAN, FOUNDER_Q, NOTES, NOTES_DATA, TICKETS, TODAY, \
    scripted_harness

from databridge_core import GroundedValue, SourcePointer
from databridge_core.provenance import Trust
from databridge_federated import (
    CatalogueAdmission,
    CombineSpec,
    DelegationPlan,
    LinkSpec,
    ResultRow,
    SubResult,
    SubTask,
    build_catalogue,
    combine,
    expand,
    rules_plan,
    validate_plan,
)

ADMIN = CatalogueAdmission("dana.ops")


def _plan(d: dict) -> DelegationPlan:
    return DelegationPlan.model_validate(d)


def _span(doc: str, text: str, source: str = NOTES) -> SourcePointer:
    return SourcePointer(source_id=source, kind="doc_span", document_id=doc, char_start=0,
                         char_end=len(text), trust=Trust.UNTRUSTED)


def _cell(row: str, col: str) -> SourcePointer:
    return SourcePointer(source_id=CRM, kind="table_cell", table="t", column=col, row_id=row,
                         trust=Trust.UNTRUSTED)


def _add_note(world, doc: str, text: str, date: str, edges: list[tuple[str, str, str]],
              nodes: list[tuple[str, str, str, "str | None", SourcePointer]] = ()):
    """A dated meeting note (+ optional extra nodes and edges) with its passage."""
    from databridge_graph.kinds import EdgeKind, NodeKind
    from databridge_graph.schema import GraphEdge, GraphNode

    t = dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc)
    span = _span(doc, text)
    s = world.store
    for nid, kind, label, canon, src in nodes:
        s.add_node(GraphNode(id=nid, kind=NodeKind(kind), label=label, canonical_id=canon,
                             sources=(src,), valid_from=t, system_from=t))
    s.add_node(GraphNode(id=f"meeting:{doc}", kind=NodeKind("meeting"), label=doc,
                         attrs={"date": date}, attr_sources={"date": span}, sources=(span,),
                         valid_from=t, system_from=t))
    for i, (src_id, dst_id, kind) in enumerate(edges):
        src = span
        if isinstance(kind, tuple):
            kind, src = kind
        s.add_edge(GraphEdge(id=f"e:{doc}:{i}", src_id=src_id, dst_id=dst_id,
                             kind=EdgeKind(kind), sources=(src,), valid_from=t, system_from=t))
    world.retriever.passages.append({"chunk_id": f"{doc}#0", "doc_id": doc, "source_id": NOTES,
                                     "text": text, "locator": "p1", "approx_locator": False,
                                     "score": 1.0, "pointer": span})


# ------------------------------------------------------------------------------------------------
# 1 (Critical) — an explicit empty allowlist, or a falsy admission object, never widens to all
# ------------------------------------------------------------------------------------------------
def test_f1_empty_allowlist_grants_nothing(world):
    none = CatalogueAdmission("temp", connections=set())
    assert none.admit_read(CRM, "facts")[0] is False
    harness, _ = scripted_harness(FOUNDER_PLAN)
    ans = world.service(harness=harness).answer(FOUNDER_Q, admission=none)
    assert world.facts.calls == [] and world.retriever.calls == []
    assert ans.coverage["searched"] == [] and ans.table == {}


def test_f1_public_answer_keeps_a_falsy_admission(world):
    from databridge_federated import answer

    class Falsy(CatalogueAdmission):
        def __bool__(self) -> bool:                  # an object that is "empty" but real
            return False

    svc = world.service()
    ans = answer("How many invoices are there?", service=svc,
                 admission=Falsy("nobody", connections=set()))
    assert world.facts.calls == []                   # not replaced by an all-access admission
    assert ans.table == {}


def test_f1_graph_scope_admits_only_catalogue_connections(world):
    svc = world.service()
    admits = svc.admits(ADMIN)
    assert admits(CRM) and admits(NOTES)
    assert not admits("unregistered-db")             # evidence nobody registered is invisible
    assert not svc.admits(CatalogueAdmission("x", {NOTES}))(CRM)


# ------------------------------------------------------------------------------------------------
# 2 (Critical) — documents and graph links see only what the CALLER may read
# ------------------------------------------------------------------------------------------------
def test_f2_document_entities_are_scoped_to_the_caller(world):
    svc = world.service()
    task = SubTask.model_validate(FOUNDER_PLAN["subtasks"][1])
    wide = svc.run_subtask(task, admission=ADMIN)
    narrow = svc.run_subtask(task, admission=CatalogueAdmission("x", {NOTES}))
    assert not wide.error and not narrow.error
    kinds_wide = {e.kind for r in wide.rows for e in r.entities}
    kinds_narrow = {e.kind for r in narrow.rows for e in r.entities}
    assert "organisation" in kinds_wide              # non-vacuous: the wide caller sees them
    assert "organisation" not in kinds_narrow        # organisations are sourced from crm-db


def test_f2_graph_path_needs_admitted_edges(world):
    """The only path to C-006 runs over an edge whose evidence is from an UNREGISTERED source:
    for this caller that path does not exist."""
    text = "Project Borealis review: complaint about scope."
    hidden = _span("hr-doc", "x", source="unregistered-db")
    _add_note(world, "note-borealis", text, "2026-08-26",
              edges=[("meeting:note-borealis", "project:borealis", "mentions"),
                     ("organisation:c-006", "project:borealis", ("party_to", hidden))],
              nodes=[("project:borealis", "project", "Borealis", None, _span("note-borealis",
                                                                           text))])
    plan = _plan({
        "subtasks": [
            {"id": "inv", "adapter_agent": "crm_sources", "entity_kinds": ["invoices"],
             "fields": ["customer_id"]},
            {"id": "notes", "adapter_agent": "notes_sources", "entity_kinds": ["meeting"],
             "filters": [{"field": "text", "op": "contains", "value": "borealis"}]}],
        "combine": {"base": "inv",
                    "links": [{"subtask": "notes", "key": "customer_id",
                               "entity_kind": "organisation", "via": "graph", "max_hops": 1,
                               "edge_kinds": ["party_to"]}],
                    "group_by": ["customer_id"], "aggregate": {"op": "count"}}})
    ans = world.service().answer("q", admission=ADMIN, plan=plan)
    assert ans.table["rows"] == []


# ------------------------------------------------------------------------------------------------
# 3 (Critical) — person links are always candidates
# ------------------------------------------------------------------------------------------------
def _field_link_plan(key_kind: "str | None") -> dict:
    link = {"subtask": "tix2", "key": "contact_email", "other_field": "contact_email"}
    if key_kind:
        link["key_kind"] = key_kind
    return {"subtasks": [
        {"id": "tix", "adapter_agent": "crm_sources", "entity_kinds": ["tickets"],
         "fields": ["contact_email", "amount"]},
        {"id": "tix2", "adapter_agent": "crm_sources", "entity_kinds": ["tickets"],
         "fields": ["contact_email"]}],
        "combine": {"base": "tix", "links": [link], "group_by": ["contact_email"],
                    "aggregate": {"op": "sum", "field": "amount"}}}


def test_f3_field_link_on_a_person_identifier_is_a_candidate(world):
    ans = world.service().answer("q", admission=ADMIN, plan=_plan(_field_link_plan("person")))
    rows = ans.table["rows"]
    assert {r["cells"]["contact_email"]["value"] for r in rows} == \
        {t["contact_email"] for t in TICKETS}
    assert rows and all(r["candidate"] and r["requires_human_selection"] for r in rows)


def test_f3_field_link_must_say_what_it_identifies():
    with pytest.raises(Exception):
        _plan(_field_link_plan(None))


def test_f3_intermediate_person_on_a_graph_path_makes_a_candidate(world):
    """note -> project -party_to- person -works_for- organisation: the start (project) and the
    target (organisation) are not people, the middle is."""
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
                               "edge_kinds": ["party_to", "works_for"]}],
                    "group_by": ["customer_id"], "aggregate": {"op": "count"}}})
    ans = world.service().answer("q", admission=ADMIN, plan=plan)
    rows = ans.table["rows"]
    assert [r["cells"]["customer_id"]["value"] for r in rows] == ["C-004"]
    assert rows[0]["candidate"] is True


# ------------------------------------------------------------------------------------------------
# 4 (Critical) — the closed ontology fails CLOSED
# ------------------------------------------------------------------------------------------------
def test_f4_no_vocabulary_means_no_documents(world):
    from databridge_federated import DelegateError, run_documents

    cat = build_catalogue([], vocabulary=None)
    assert cat.vocabulary_loaded is False and cat.problems
    svc = world.service()
    svc.catalogue.vocabulary_loaded = False
    svc.catalogue.node_kinds = frozenset()
    svc.catalogue.edge_kinds = frozenset()
    qs = validate_plan(_plan(FOUNDER_PLAN), svc.catalogue)
    assert any("vocabulary" in q for q in qs)
    harness, _ = scripted_harness(FOUNDER_PLAN)
    svc.harness = harness
    ans = svc.answer(FOUNDER_Q, admission=ADMIN)
    assert ans.status == "stopped" and world.retriever.calls == [] and world.facts.calls == []
    entry = svc.catalogue.agent("notes_sources").connections[0]
    with pytest.raises(DelegateError):
        run_documents(SubTask.model_validate({"adapter_agent": "notes_sources",
                                              "entity_kinds": ["invented_kind"]}),
                      entry, store=world.store, retriever=world.retriever,
                      catalogue=svc.catalogue, today=TODAY, principal="x", run_id=None)


# ------------------------------------------------------------------------------------------------
# 5 (Critical) — an unknown date is never guessed; a pointerless value is never silently dropped
# ------------------------------------------------------------------------------------------------
def test_f5_missing_document_date_is_excluded_and_counted(world):
    svc = world.service()
    task = SubTask.model_validate({
        "adapter_agent": "notes_sources", "entity_kinds": ["meeting"],
        "time_window": {"axis": "valid_time", "field": "event_date", "period": "this_quarter"}})
    res = svc.run_subtask(task, admission=ADMIN)
    assert not res.error
    assert res.rows == ()                            # valid_from is NOT used in its place
    assert res.coverage.excluded and "event_date" in res.coverage.excluded[0]
    n_meetings = len({n["doc"] for n in NOTES_DATA}) + 2      # + the two PEOPLE notes
    assert str(n_meetings) in res.coverage.excluded[0]


def test_f5_pointerless_facts_row_is_excluded_and_reported():
    from databridge_federated.catalogue import FactsTable
    from databridge_federated.delegates import _plain_rows

    table = FactsTable(name="t", columns=("id", "amount"), key="id")
    good = [_cell("r1", "id"), _cell("r1", "amount")]
    ans = SimpleNamespace(columns=["id", "amount"], rows=[("r1", "1"), ("r2", "2")],
                          pointers=good + [_cell("r2", "id")])     # r2.amount has no pointer
    rows, excluded = _plain_rows(ans, CRM, table)
    assert [r.row_id for r in rows] == [f"{CRM}:t:r1"]
    assert excluded and "1 row" in excluded[0]


# ------------------------------------------------------------------------------------------------
# 6 (High) — the rules path answers only the question it fully parsed
# ------------------------------------------------------------------------------------------------
def test_f6_qualified_question_is_not_answered_by_rules(world):
    cat = world.service().catalogue
    assert rules_plan("What is the total amount of invoices by account owner?", cat) is not None
    for q in ("Sum amount in invoices for APAC customers during this quarter",
              "How many invoices are in APAC?",
              "Total amount of invoices this quarter"):
        assert rules_plan(q, cat) is None, q
    ans = world.service(harness=None).answer("How many invoices are in APAC?", admission=ADMIN)
    assert ans.status == "stopped" and world.facts.calls == []
    # E2E run 5 (F-07): a qualifier that IS a measured stored value of one column is parsed, not
    # dropped — the count carries it as a filter, and says so
    plan = rules_plan("How many invoices are overdue?", cat)
    assert plan is not None and [f.describe() for f in plan.subtasks[0].filters] ==         ["status eq 'overdue'"] and plan.assumptions


# ------------------------------------------------------------------------------------------------
# 7 (High) — a base window that does not cover the compared periods is a question
# ------------------------------------------------------------------------------------------------
def test_f7_partial_base_window_is_a_question_not_a_partial_period(world):
    bad = json.loads(json.dumps(FOUNDER_PLAN))
    bad["subtasks"][0]["time_window"] = {"axis": "valid_time", "field": "due_on",
                                         "period": "this_quarter"}
    harness, _ = scripted_harness(bad)
    ans = world.service(harness=harness).answer(FOUNDER_Q, admission=ADMIN)
    assert ans.status == "stopped" and ans.questions
    assert "last_quarter" in ans.questions[0]
    assert world.facts.calls == [] and world.retriever.calls == []


# ------------------------------------------------------------------------------------------------
# 8 (High) — no sub-task is dispatched and then discarded
# ------------------------------------------------------------------------------------------------
def test_f8_every_subtask_must_feed_the_answer(world):
    cat = world.service().catalogue
    two = {"subtasks": FOUNDER_PLAN["subtasks"]}
    assert any("combine" in q for q in validate_plan(_plan(two), cat))
    unused = json.loads(json.dumps(FOUNDER_PLAN))
    unused["subtasks"].append({"id": "extra", "adapter_agent": "crm_sources",
                               "entity_kinds": ["tickets"]})
    assert any("'extra'" in q for q in validate_plan(_plan(unused), cat))


# ------------------------------------------------------------------------------------------------
# 10 (High) — non-numeric inputs make the aggregate unknown, never a partial total
# ------------------------------------------------------------------------------------------------
def test_f10_pushdown_refuses_a_total_over_non_numeric_values():
    from databridge_federated.catalogue import FactsTable
    from databridge_federated.delegates import _pushdown_rows, encode_pair

    table = FactsTable(name="t", columns=("id", "amount"), key="id")
    task = SubTask.model_validate({"adapter_agent": "a", "entity_kinds": ["t"],
                                   "aggregate": {"op": "sum", "field": "amount"}})
    # (R-021 r2 #7 changed the group encoding; the finding under test is unchanged)
    pairs = "".join(encode_pair(k, v) for k, v in
                     [("r1", "10"), ("r2", "N/A"), ("r3", "20"), ("r4", None)])
    ans = SimpleNamespace(columns=["row_count", "key_dupes", "pairs"], rows=[(4, 0, pairs)],
                          pointers=[])
    rows, excluded = _pushdown_rows(task, table, ans, CRM, predicate="p")
    assert rows[0].cells.get("sum_amount") is None   # not 30
    assert any("non-numeric" in e for e in excluded)


def _sr(rows: list[tuple[str, object]], field: str = "amount") -> SubResult:
    return SubResult(subtask_id="b", adapter_agent="a", connection=CRM, key_field="id",
                     rows=tuple(ResultRow(row_id=k, cells={
                         "id": GroundedValue(value=k, pointer=_cell(k, "id")),
                         field: GroundedValue(value=v, pointer=_cell(k, field))})
                         for k, v in rows))


def test_f10_combine_refuses_a_total_over_non_numeric_values(world):
    cat = world.service().catalogue
    res = {"b": _sr([("r1", "10"), ("r2", "N/A"), ("r3", "20")])}
    t = combine(CombineSpec(base="b", aggregate={"op": "sum", "field": "amount"}), res,
                today=TODAY, catalogue=cat)
    assert t.rows[0].cells["sum_amount"] is None
    assert any("non-numeric" in e for e in t.excluded) and t.absent


# ------------------------------------------------------------------------------------------------
# 11 (High) — ranked passage search is reported as non-exhaustive
# ------------------------------------------------------------------------------------------------
def test_f11_passage_results_are_marked_non_exhaustive(world):
    harness, _ = scripted_harness(FOUNDER_PLAN)
    ans = world.service(harness=harness).answer(FOUNDER_Q, admission=ADMIN)
    codes = {s.get("reason_code") for s in ans.coverage["skipped"]}
    assert "non_exhaustive" in codes
    assert ans.answer_markdown.startswith("> **Reduced coverage.**")


# ------------------------------------------------------------------------------------------------
# 12 (High) — live filters reach the statement; a capped live read is reported truncated
# ------------------------------------------------------------------------------------------------
class _Gateway:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def query(self, conn, sql):
        self.sql.append(sql)
        return SimpleNamespace(is_error=False, text=json.dumps(self.rows), data=None,
                               mime="application/json")


def test_f12_live_null_filter_is_applied_and_limit_reports_truncation(world, tmp_path):
    from databridge_federated import DelegateError, run_live

    entry = world.service().catalogue.agent("crm_sources").connections[0]
    task = SubTask.model_validate({"adapter_agent": "crm_sources", "entity_kinds": ["tickets"],
                                   "fields": ["amount"], "freshness": "live", "limit": 2,
                                   "filters": [{"field": "amount", "op": "not_null"}]})
    gw = _Gateway([{"ticket_id": t["ticket_id"], "amount": t["amount"]} for t in TICKETS])
    res = run_live(task, entry, gateway=gw, today=TODAY, principal="x", run_id=None,
                   capture_root=tmp_path)
    assert '"amount" IS NOT NULL' in gw.sql[0] and "LIMIT 3" in gw.sql[0]
    assert len(res.rows) == 2 and res.coverage.truncated is True
    valued = SubTask.model_validate({**task.model_dump(), "filters": [
        {"field": "amount", "op": "gt", "value": 1}]})
    with pytest.raises(DelegateError):
        run_live(valued, entry, gateway=gw, today=TODAY,
                 principal="x", run_id=None, capture_root=tmp_path)


# ------------------------------------------------------------------------------------------------
# 13 (High) — graph links traverse declared edges only; no direct-mention shortcut
# ------------------------------------------------------------------------------------------------
def test_f13_graph_link_needs_declared_edges_and_a_real_path(world):
    with pytest.raises(Exception):
        LinkSpec(subtask="n", key="k", entity_kind="organisation", via="graph", max_hops=1)
    # note-q3-a MENTIONS C-001 and C-003 directly; no `party_to` path leads to them
    plan = _plan({
        "subtasks": [
            {"id": "inv", "adapter_agent": "crm_sources", "entity_kinds": ["invoices"],
             "fields": ["customer_id"]},
            {"id": "notes", "adapter_agent": "notes_sources", "entity_kinds": ["meeting"],
             "filters": [{"field": "text", "op": "contains", "value": "late deliveries"}]}],
        "combine": {"base": "inv",
                    "links": [{"subtask": "notes", "key": "customer_id",
                               "entity_kind": "organisation", "via": "graph", "max_hops": 1,
                               "edge_kinds": ["party_to"]}],
                    "group_by": ["customer_id"], "aggregate": {"op": "count"}}})
    assert any("late deliveries" in n["text"] for n in NOTES_DATA)       # non-vacuous
    ans = world.service().answer("q", admission=ADMIN, plan=plan)
    assert ans.table["rows"] == []


# ------------------------------------------------------------------------------------------------
# 14 (Medium) — ordering uses the named field, numeric or text, also before top-k on rows
# ------------------------------------------------------------------------------------------------
def test_f14_unknown_order_by_is_a_question(world):
    bad = json.loads(json.dumps(FOUNDER_PLAN))
    bad["combine"]["order_by"] = "sum_amount"         # not a column of a compared result
    assert any("order by" in q.lower() for q in validate_plan(_plan(bad), world.service()
                                                             .catalogue))


def test_f14_order_by_text_group_and_top_k_over_rows(world):
    plan = _plan({"subtasks": [{"id": "inv", "adapter_agent": "crm_sources",
                                "entity_kinds": ["invoices"], "fields": ["amount"]}],
                  "combine": {"base": "inv", "order_by": "amount", "descending": True,
                              "top_k": 3}})
    ans = world.service().answer("q", admission=ADMIN, plan=plan)
    got = [Decimal(str(r["cells"]["amount"]["value"])) for r in ans.table["rows"]]
    assert got == sorted((Decimal(r["amount"]) for r in world.invoices), reverse=True)[:3]

    by_owner = _plan({"subtasks": [{"id": "inv", "adapter_agent": "crm_sources",
                                    "entity_kinds": ["invoices"],
                                    "filters": [{"field": "status", "op": "eq",
                                                 "value": "overdue"}]}],
                      "combine": {"base": "inv", "filters": [{"field": "status", "op": "eq",
                                                              "value": "overdue"}],
                                  "group_by": ["account_owner"], "aggregate": {"op": "count"},
                                  "order_by": "account_owner", "descending": False}})
    ans = world.service().answer("q", admission=ADMIN, plan=by_owner)
    owners = [r["cells"]["account_owner"]["value"] for r in ans.table["rows"]]
    assert len(owners) >= 2 and owners == sorted(owners)


# ------------------------------------------------------------------------------------------------
# 15 (Medium) — no accepted-but-ignored aggregate field; count(field) skips NULLs
# ------------------------------------------------------------------------------------------------
def test_f15_aggregate_group_by_must_match_and_count_field_skips_nulls(world):
    with pytest.raises(Exception):
        CombineSpec(base="b", group_by=("a",), aggregate={"op": "count", "group_by": ["b"]})
    cat = world.service().catalogue
    res = {"b": _sr([("r1", "x"), ("r2", None), ("r3", "y")], field="note")}
    t = combine(CombineSpec(base="b", aggregate={"op": "count", "field": "note"}), res,
                today=TODAY, catalogue=cat)
    gv = t.rows[0].cells["count_note"]
    assert gv.value == 2 and sorted(p.row_id for p in expand(gv.pointer)) == ["r1", "r3"]
    assert any("NULL" in e for e in t.excluded)


# ------------------------------------------------------------------------------------------------
# 16 (Medium) — a timed-out delegate is cancelled before it reads; deadlines are per task
# ------------------------------------------------------------------------------------------------
def test_f16_timed_out_delegate_never_reads(world):
    from databridge_federated import FederatedService

    class Stuck(FederatedService):
        def run_subtask(self, task, **kw):
            time.sleep(0.6)                           # blocked before its read
            return super().run_subtask(task, **kw)

    base = world.service()
    svc = Stuck(**{f: getattr(base, f) for f in base.__dataclass_fields__})
    svc.subtask_timeout_s = 0.2
    plan = _plan({"subtasks": [{"id": "slow", "adapter_agent": "crm_sources",
                                "entity_kinds": ["invoices"], "aggregate": {"op": "count"}}]})
    res = svc.dispatch(list(plan.subtasks), admission=ADMIN)
    assert "timed out" in res["slow"].error
    time.sleep(0.8)                                   # let the abandoned thread wake up
    assert world.facts.calls == []                    # it was cancelled, not left to read


def test_f16_queued_task_gets_its_own_deadline(world):
    from databridge_federated import FederatedService

    class Slowish(FederatedService):
        def run_subtask(self, task, **kw):
            time.sleep(0.25)
            return super().run_subtask(task, **kw)

    base = world.service()
    svc = Slowish(**{f: getattr(base, f) for f in base.__dataclass_fields__})
    svc.subtask_timeout_s, svc.max_parallel = 0.4, 1      # two waves of one
    plan = _plan({"subtasks": [
        {"id": f"t{i}", "adapter_agent": "crm_sources", "entity_kinds": ["invoices"],
         "aggregate": {"op": "count"}} for i in range(2)]})
    res = svc.dispatch(list(plan.subtasks), admission=ADMIN)
    assert all(not r.error for r in res.values()), {k: r.error for k, r in res.items()}

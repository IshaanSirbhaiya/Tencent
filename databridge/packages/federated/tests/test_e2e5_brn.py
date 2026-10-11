"""E2E run 5 — delegated querying at size and source ownership (F-05, F-07, W-03).

* **F-07** "How many facilities are on the watchlist?" (truth 33, `facility.status` in any casing)
  was planned against a `watchlist_entry` kind of the filings keeper. Every documents connection
  was offered EVERY ontology kind, though the filings held none of that kind; and the planner
  never saw that `facility.status` holds ``WATCHLIST``. The documents read then typed the whole
  graph (50k nodes, 49k edges) and timed out at 30 s with no answer.
* **F-05** a graph link in the combine re-read every open edge once per row (201 s for 21 rows on
  a copy of the finance graph).
* **W-03** before an adapter agent kept the files source, the catalogue held only the database:
  "invoices of customers discussed in our meeting notes" was told there was no meeting source.

The synthetic world is the package's test kit; every expectation is computed from its fixture
lists or by reading the store independently, never written down.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from fed_testkit import CRM, NOTES, TODAY, make_invoices

from databridge_federated import (
    CatalogueAdmission,
    DelegationPlan,
    SubTask,
    rules_plan,
    run_documents,
    validate_plan,
)

ADMIN = CatalogueAdmission("analyst")


def _held_kinds(store, source: str) -> set[str]:
    """Independently of the catalogue: every open node with a pointer into ``source``."""
    from databridge_graph.store import row_to_node
    return {str(row_to_node(store, r).kind.value) for r in store.open_nodes()
            if any(p.source_id == source for p in row_to_node(store, r).sources)}


def _docs_entry(svc):
    return next(c for a in svc.catalogue.agents.values() for c in a.connections
                if c.connection == NOTES)


# ================================================================================================
# F-07 — a documents connection is offered only the kinds it holds
# ================================================================================================
def test_f07_a_documents_connection_offers_only_the_kinds_it_holds(world):
    svc = world.service()
    held = _held_kinds(world.store, NOTES)
    assert held and set(world.vocab.node_kinds) - held        # non-vacuous: some are not held
    described = next(c for a in svc.catalogue.describe()["adapter_agents"]
                     for c in a["connections"] if c["connection"] == NOTES)
    assert set(described["entity_kinds"]) == held


def test_f07_a_plan_asking_a_connection_for_a_kind_it_does_not_hold_is_a_question(world):
    svc = world.service()
    held = _held_kinds(world.store, NOTES)
    absent = sorted(set(world.vocab.node_kinds) - held - set(world.vocab.subject_kinds))[0]
    edge = sorted(world.vocab.edge_kinds)[0]

    def plan(kind):
        return DelegationPlan.model_validate({
            "subtasks": [{"id": "t1", "adapter_agent": "crm_sources", "connection": CRM,
                          "entity_kinds": ["invoices"], "fields": ["invoice_id", "customer_id"]},
                         {"id": "t2", "adapter_agent": "notes_sources", "connection": NOTES,
                          "entity_kinds": [kind], "link_edges": [edge]}],
            "combine": {"base": "t1", "links": [{"subtask": "t2", "key": "customer_id",
                                                 "entity_kind": absent, "via": "graph",
                                                 "max_hops": 1, "edge_kinds": [edge]}],
                        "aggregate": {"op": "count", "field": "invoice_id"}}})
    qs = validate_plan(plan(absent), svc.catalogue)
    assert any(NOTES in q and repr(absent) in q and "holds no" in q for q in qs), qs
    held_kind = sorted(held - set(world.vocab.subject_kinds))[0]
    assert not any("holds no" in q for q in validate_plan(plan(held_kind), svc.catalogue))


# ================================================================================================
# F-07 — categorical values are measured, and a count qualified by one is planned by rule
# ================================================================================================
def _categorical(rows: list[dict], col: str, key: str) -> bool:
    """The catalogue's rule, restated over the fixture rows (see `catalogue.measure_values`)."""
    if col == key:
        return False
    vals = [r[col] for r in rows if r[col] is not None]
    folded = {" ".join(str(v).casefold().split()) for v in vals}
    return (2 <= len(folded) <= 12 and len(rows) >= 4 * len(folded)
            and all(any(ch.isalpha() for ch in str(v)) for v in set(vals)))


def test_f07_categorical_values_are_measured_from_the_facts_store(world):
    svc = world.service()
    tab = next(c for a in svc.catalogue.agents.values() for c in a.connections
               if c.connection == CRM)
    inv = tab.table("invoices")
    rows = make_invoices()
    from fed_testkit import SUBJECT_COLUMNS
    # r1 #6: a column the approved mapping marks as naming people is never listed
    want = {c for c in inv.columns if _categorical(rows, c, inv.key)
            and c not in SUBJECT_COLUMNS["invoices"]}
    assert "status" in want and want != set(inv.columns)                 # non-vacuous
    got = dict(inv.values)
    assert set(got) == want
    for c in want:
        assert set(got[c]) == {str(r[c]) for r in rows}
    assert tab.table("tickets").values == ()             # 3 rows: no value repeats enough


def test_f07_a_column_holding_a_subjects_name_is_never_shown_to_the_planner(world):
    """CONS standard 7: the subject predicate governs model exposure."""
    from databridge_graph import GraphNode
    from databridge_graph.kinds import NodeKind
    from databridge_core import SourcePointer

    rows = make_invoices()
    owner = sorted({r["customer_id"] for r in rows})[0]
    before = dict(next(c for a in world.service().catalogue.agents.values()
                       for c in a.connections if c.connection == CRM).table("invoices").values)
    assert "customer_id" in before and "account_owner" not in before    # mapped people hidden
    subj = sorted(world.vocab.subject_kinds)[0]
    t = dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc)
    world.store.add_node(GraphNode(id="subj:owner", kind=NodeKind(subj), label=owner.title(),
                                   sources=(SourcePointer(source_id=NOTES, kind="doc_span",
                                                          document_id="x", char_start=0,
                                                          char_end=3),),
                                   valid_from=t, system_from=t))
    after = dict(next(c for a in world.service().catalogue.agents.values()
                      for c in a.connections if c.connection == CRM).table("invoices").values)
    assert "customer_id" not in after and "status" in after    # the graph guard, too


@pytest.mark.parametrize("shape", ["How many invoices are {v}?", "How many {v} invoices are there?",
                                   "how many invoices are marked {V}"])
def test_f07_a_count_qualified_by_a_stored_value_is_planned_and_answered_by_rule(world, shape):
    svc = world.service()
    rows = make_invoices()
    status = sorted({r["status"] for r in rows})[-1]
    q = shape.format(v=status, V=status.upper())
    plan = rules_plan(q, svc.catalogue)
    assert plan is not None and plan.subtasks[0].filters[0].field == "status", q
    ans = svc.answer(q, admission=ADMIN)
    assert ans.status == "completed", ans.answer_markdown
    [row] = ans.table["rows"]
    assert int(row["cells"]["count"]["value"]) == sum(r["status"] == status for r in rows)
    assert any("status" in a for a in ans.plan["assumptions"])


def test_f07_a_qualifier_that_is_no_stored_value_is_not_planned_by_rule(world):
    svc = world.service()
    assert rules_plan("How many invoices are on the moon?", svc.catalogue) is None
    assert rules_plan("How many invoices are overdue by customer?", svc.catalogue) is None


# ================================================================================================
# F-07 / F-05 — the documents read is indexed, gives the same rows, and is bounded
# ================================================================================================
def _doc_tasks(world):
    edge = "mentions"
    return [SubTask.model_validate({"id": "t", "adapter_agent": "notes_sources",
                                    "entity_kinds": ["meeting"], **extra})
            for extra in ({}, {"link_edges": [edge]},
                          {"filters": [{"field": "text", "op": "contains", "value": "complaint"}]},
                          {"time_window": {"axis": "valid_time", "field": "date",
                                           "period": "this_quarter"}})]


def _rows(res):
    return [(r.row_id, sorted((e.node_id, e.via) for e in r.entities)) for r in res.rows]


def test_f07_the_indexed_documents_read_returns_exactly_the_full_reads_rows(world, monkeypatch):
    import databridge_federated.delegates as dl

    svc = world.service()
    entry = _docs_entry(svc)
    kw = dict(store=world.store, retriever=world.retriever, catalogue=svc.catalogue, today=TODAY,
              principal="x", run_id=None, admits=svc.admits(ADMIN))
    indexed = [run_documents(t, entry, **kw) for t in _doc_tasks(world)]
    monkeypatch.setattr(dl, "index_complete", lambda store: False)       # the full read
    full = [run_documents(t, entry, **kw) for t in _doc_tasks(world)]
    assert any(r.rows for r in full)                                     # non-vacuous
    for a, b in zip(indexed, full):
        assert _rows(a) == _rows(b)
        assert a.coverage.exhaustive == b.coverage.exhaustive


def test_f07_the_indexed_read_never_scans_the_graph(world):
    svc = world.service()
    entry = _docs_entry(svc)
    seen: list[str] = []

    class Store:
        def fetchall(self, sql, *a, **k):
            seen.append(sql)
            return world.store.fetchall(sql, *a, **k)

        def __getattr__(self, name):
            return getattr(world.store, name)

    res = run_documents(_doc_tasks(world)[1], entry, store=Store(), retriever=world.retriever,
                        catalogue=svc.catalogue, today=TODAY, principal="x", run_id=None,
                        admits=svc.admits(ADMIN))
    assert res.rows and seen
    assert not any(q.strip() in ("SELECT * FROM nodes WHERE system_to IS NULL",
                                 "SELECT * FROM edges WHERE system_to IS NULL") for q in seen)


def test_f07_a_documents_read_past_its_budget_is_partial_and_says_so(world):
    svc = world.service()
    res = run_documents(_doc_tasks(world)[0], _docs_entry(svc), store=world.store,
                        retriever=world.retriever, catalogue=svc.catalogue, today=TODAY,
                        principal="x", run_id=None, admits=svc.admits(ADMIN), budget_s=0.0)
    assert res.error is None
    assert res.coverage.truncated and not res.coverage.exhaustive
    assert any("time budget" in x and "partial" in x for x in res.coverage.excluded)


def test_f05_a_graph_link_reads_by_index_not_the_whole_graph_per_row(world):
    """The combine's graph walk (`_graph_reach`): the same reach, its edges read per node."""
    from databridge_federated.combine import _graph_reach

    seen: list[str] = []

    class Store:
        def fetchall(self, sql, *a, **k):
            seen.append(sql)
            return world.store.fetchall(sql, *a, **k)

        def __getattr__(self, name):
            return getattr(world.store, name)

    svc = world.service()
    adm = svc.admits(ADMIN)
    start = next(r["id"] for r in world.store.open_nodes() if r["kind"] == "meeting")
    got = _graph_reach(Store(), start, hops=2, edge_kinds={"mentions"}, kind="organisation",
                       admits=adm)
    assert got and not any("WHERE system_to IS NULL\"" in q or q.strip().endswith(
        "WHERE system_to IS NULL") for q in seen), seen
    # the same reach as walking every edge: each reached node's canonical id, by plain SQL
    edges = world.store.fetchall("SELECT src_id, dst_id FROM edges WHERE system_to IS NULL "
                                 "AND kind = 'mentions'")
    ring = {start} | {d for s, d in edges if s == start} | {s for s, d in edges if d == start}
    two = ring | {d for s, d in edges if s in ring} | {s for s, d in edges if d in ring}
    want = {r["id"] for r in world.store.open_nodes() if r["id"] in two
            and r["kind"] == "organisation"}
    assert {n.id for n, _p, _k in got} == want


# ================================================================================================
# W-03 — a declared documents source with graph records needs no adapter agent to be asked
# ================================================================================================
def test_w03_a_declared_documents_source_without_an_adapter_agent_is_offered(world):
    from databridge_federated import unowned_document_specs

    offered, notes = unowned_document_specs([], world.store, world.facts,
                                            [NOTES, CRM, "nothing-built-src"])
    assert [s.id for s in offered] == [NOTES] and notes                   # CRM: facts store
    kept, _ = unowned_document_specs([offered[0]], world.store, world.facts, [NOTES])
    assert kept == []                                                     # already offered


def test_w03_the_catalogue_offers_it_with_its_measured_kinds(world, tmp_path):
    from databridge_federated import build_service

    empty = tmp_path / "no_agents"
    empty.mkdir()
    svc = build_service(store=world.store, facts=world.facts, retriever=world.retriever,
                        adapter_agents_root=empty, vocabulary=world.vocab,
                        connections=[CRM, NOTES])
    entry = next(c for a in svc.catalogue.agents.values() for c in a.connections
                 if c.connection == NOTES)
    assert entry.channel == "documents"
    assert set(svc.catalogue.connection_kinds(entry)) == _held_kinds(world.store, NOTES)


# ================================================================================================
# CONS standard 4 — a planner failure carries no exception text
# ================================================================================================
def test_a_planner_failure_carries_no_exception_text(world):
    from databridge_federated.planner import model_plan

    class Boom:
        def complete_structured(self, *a, **k):
            raise RuntimeError("secret-detail-91c in a private path")

    out = model_plan("anything", world.service().catalogue, Boom())
    assert out.plan is None and out.questions
    text = json.dumps(out.questions)
    assert "secret-detail-91c" not in text and "RuntimeError" not in text and "error_ref" in text


def test_f07_a_table_name_matches_its_english_plural():
    """"how many facilities" never matched a `facility` table ("facilitys?")."""
    import re

    from databridge_federated.planner import _name_pat
    for name, plural in (("facility", "facilities"), ("invoices", "invoices"),
                         ("txn", "txns"), ("address", "addresses"), ("survey", "surveys")):
        assert re.fullmatch(_name_pat(name), plural), (name, plural)
        assert re.fullmatch(_name_pat(name), name), name



# ================================================================================================
# Codex R-029 round 1
# ================================================================================================
def _staff_world(env, *, mapped: bool):
    """A facts store with a person-name column and an organisation-name column, each holding
    multi-word values that a one-word filter is a whole-word part of."""
    from fed_testkit import _write_facts
    from databridge_agent_runtime import McpFactsStore
    from databridge_federated import build_catalogue
    from databridge_graph.packs import deployment

    pairs = [("Alice Smith", "Pellmore Freight Ltd"), ("Bram Ostlund", "Quillon Grocers"),
             ("Cora Vane", "Quillon Grocers"), ("Dov Arkin", "Pellmore Freight Ltd")]
    rows = [{"staff_id": f"S-{i}", "full_name": n, "employer": e}
            for i, (n, e) in enumerate(pairs)]
    _write_facts(env / "facts", "staff-db", {"staff": (["staff_id", "full_name", "employer"],
                                                       rows)},
                 subject={"staff": ["full_name"]})
    if not mapped:
        (env / "sources" / "staff-db.mapping.yaml").unlink()

    class Spec:
        id, title, status, connections = "staff-db", "staff", "active", ("staff-db",)
    with deployment("workspace") as vocab:
        cat = build_catalogue([Spec()], facts=McpFactsStore(), vocabulary=vocab,
                              sources_dir=env / "sources")
    return cat, McpFactsStore()


def _read(cat, facts, field, value, *, resolve=False, fold=False):
    from databridge_federated import run_tabular
    entry = cat.agent("staff-db").connections[0]
    task = SubTask.model_validate({"id": "t", "adapter_agent": "staff-db",
                                   "entity_kinds": ["staff"], "fields": ["staff_id", field],
                                   "filters": [{"field": field, "op": "eq", "value": value}]})
    return run_tabular(task, entry, facts=facts, today=TODAY, principal="x", run_id=None,
                       resolve_names=resolve, fold_case=fold)


def test_r1_1_a_persons_name_is_never_resolved_or_case_widened(env):
    cat, facts = _staff_world(env, mapped=True)
    # non-vacuous: the SAME mechanisms still work on an organisation name in the same table
    org = _read(cat, facts, "employer", "Pellmore Freight", resolve=True)
    assert len(org.rows) == 2 and org.coverage.resolved
    assert len(_read(cat, facts, "employer", "QUILLON GROCERS", fold=True).rows) == 2
    person = _read(cat, facts, "full_name", "Alice", resolve=True)
    assert person.rows == () and not person.coverage.resolved
    assert any("may name people" in u for u in person.coverage.uncertain)
    assert _read(cat, facts, "full_name", "ALICE SMITH", fold=True).rows == ()


def test_r1_1_a_table_no_approved_mapping_covers_is_never_resolved(env):
    cat, facts = _staff_world(env, mapped=False)
    assert cat.agent("staff-db").connections[0].table("staff").subject_columns is None
    assert _read(cat, facts, "employer", "Pellmore Freight", resolve=True).rows == ()


def test_r1_6_a_mapped_person_column_is_hidden_whole_whatever_its_values(env):
    from databridge_federated.catalogue import FactsTable, screen_values

    cat, _f = _staff_world(env, mapped=True)
    assert cat.agent("staff-db").connections[0].table("staff").subject_columns == \
        frozenset({"full_name"})
    decorated = FactsTable(name="x", columns=("id", "owner", "status"), key="id",
                           values=(("owner", ("Alice Smith (CFO)", "Bram O.")),
                                   ("status", ("open", "paid"))),
                           subject_columns=frozenset({"owner"}))
    assert dict(screen_values(decorated, frozenset()).values) == {"status": ("open", "paid")}
    unmapped = FactsTable(name="x", columns=decorated.columns, key="id",
                          values=decorated.values, subject_columns=None)
    assert screen_values(unmapped, frozenset()).values == ()


def test_r1_5_no_stored_value_enters_the_planner_prompt(world):
    from databridge_federated.planner import _prompt
    svc = world.service()
    inv = next(c for a in svc.catalogue.agents.values() for c in a.connections
               if c.connection == CRM).table("invoices")
    assert inv.values                                                     # non-vacuous
    prompt = _prompt(svc.catalogue)
    described = json.dumps(svc.catalogue.describe())
    for _col, vals in inv.values:
        for v in vals:
            assert f'"{v}"' not in prompt and f'"{v}"' not in described, v
    assert all(f'"{col}"' in described for col, _v in inv.values)        # names only


def test_r1_7_unmeasured_documents_ownership_offers_nothing(world):
    from dataclasses import replace
    svc = world.service()
    blind = replace(_docs_entry(svc), kinds=None)
    assert svc.catalogue.connection_kinds(blind) == []
    aa = svc.catalogue.agents["notes_sources"]
    svc.catalogue.agents["notes_sources"] = replace(aa, connections=(blind,))
    plan = DelegationPlan.model_validate({"subtasks": [
        {"id": "t", "adapter_agent": "notes_sources", "connection": NOTES,
         "entity_kinds": ["meeting"]}]})
    qs = validate_plan(plan, svc.catalogue)
    assert any("could not be measured" in q for q in qs), qs


def test_r1_10_a_search_that_overruns_the_budget_is_not_processed(world):
    import time as _t
    svc = world.service()

    class Slow:
        def search(self, *a, **k):
            _t.sleep(0.2)
            return world.retriever.search(*a, **k)

    res = run_documents(_doc_tasks(world)[2], _docs_entry(svc), store=world.store,
                        retriever=Slow(), catalogue=svc.catalogue, today=TODAY, principal="x",
                        run_id=None, admits=svc.admits(ADMIN), budget_s=0.1)
    assert res.rows == () and res.coverage.truncated and not res.coverage.exhaustive
    assert any("searching passages" in x for x in res.coverage.excluded)



def _artefacts(env, source):
    d = env / "facts" / f"{source}_facts.queries"
    return {x.name: x.read_text(encoding="utf-8") for x in d.glob("*.json")} if d.is_dir() else {}


def test_tidy2_probes_persist_nothing_and_evidence_reads_persist_their_query(env):
    """BRN review of TIDY2's `_read_evidence`: a resolve or spelling PROBE binds the asker's own
    words and is never cited, so it writes no query artefact; a read whose cells ARE returned as
    evidence persists its artefact (so its pointers resolve later)."""
    cat, facts = _staff_world(env, mapped=True)
    before = _artefacts(env, "staff-db")
    # a probe-only path: the person column is never probed; an org filter that matches nothing
    # exactly is probed, then its evidence read re-runs
    _read(cat, facts, "employer", "Nowhere Haulage", resolve=True, fold=True)
    after = _artefacts(env, "staff-db")
    new = {k: v for k, v in after.items() if k not in before}
    import json as _json
    sqls = [_json.loads(v).get("sql", "") for v in new.values()]
    # (the resolve step's exact-count probe is the same statement as the empty read's own
    # cited count, R-021 r2 #6, so it is evidence either way; the two candidate probes are not)
    probe_shapes = ("instr(lower(", "lower(trim(")
    assert not any(any(p in q for p in probe_shapes) for q in sqls), sqls
    got = _read(cat, facts, "employer", "Quillon Grocers")
    assert got.rows                                                       # non-vacuous
    refs = {gv.pointer.query_hash for r in got.rows for gv in r.cells.values()
            if gv.pointer.kind == "query_cell"}
    persisted = _artefacts(env, "staff-db")
    assert not refs or all(f"{h}.json" in persisted for h in refs if h)

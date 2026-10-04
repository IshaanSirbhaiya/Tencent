"""E2E run 2 regressions for delegated querying (FED3): N-02, N-03, N-19.

* **N-02** — the cross-source answer was an EMPTY table marked complete although customer C-1001
  qualified. The graph the live run built has the shape reproduced here: a meeting note is a
  ``document`` node that ``mentions`` both a ``meeting`` node and an ``organisation`` node, and only
  the files-side organisation carries the customer id (the database-side one carries a domain).
  The documents delegate offered a row's NEIGHBOURS as its entities but never the row's own node,
  and never what the same document also mentions — so no plan shape the model wrote could reach
  an identifier. ``test_n02_replay_of_the_live_run`` replays the run's own plan against copies of
  the run's graph and facts when that evidence is present.
* **N-03** — a column of another table (account owner) could not be brought onto the base rows,
  and the planner's questions could not be answered back.
* **N-19** — the federated run record said 0 tokens / 0 cost after two model calls.

Expectations are COMPUTED from the fixture lists below, never written down.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Optional

import pytest

from fed_testkit import HARNESS_CONFIG, _write_facts

from databridge_core import SourcePointer
from databridge_core.provenance import Trust
from databridge_federated import (
    CatalogueAdmission,
    CombineSpec,
    DelegationPlan,
    ResultRow,
    SubResult,
    combine,
)
from databridge_federated.planner import question_id

TODAY = dt.date(2026, 10, 1)            # this quarter = Q4 2026, last quarter = Q3 2026
DB, FILES, AA = "acct-db", "shared-files", "graph_sync"
ANALYST = CatalogueAdmission("analyst")

CUSTOMERS = [  # customer_id, name, account_owner
    ("C-1001", "Pellucid Freightways Ltd", "LA-007"),
    ("C-1002", "Corriedale Grocers Co-operative", "LA-012"),
    ("C-1003", "Tamsinbay Water Authority", "LA-001"),
    ("C-1004", "Quillon Analytics", "LA-007"),
    ("C-1005", "Never Mentioned plc", "LA-001"),
]
INVOICES = [  # invoice_no, customer_id, due_on, total_gbp, status
    ("INV-01", "C-1001", "2026-10-30", "11100.00", "open"),
    ("INV-02", "C-1001", "2026-09-30", "22200.00", "paid"),
    ("INV-03", "C-1001", "2026-08-14", "1500.50", "open"),
    ("INV-04", "C-1002", "2026-10-15", "15300.00", "open"),
    ("INV-05", "C-1003", "2026-11-02", "980.25", "open"),
    ("INV-06", "C-1004", "2026-07-09", "4400.00", "open"),
    ("INV-07", "C-1004", "2026-12-01", "250.75", "open"),
    ("INV-08", "C-1005", "2026-10-05", "7000.00", "open"),
]
#: files: one document each. ``meeting`` names the meeting the note records (None: not a meeting
#: note); ``orgs`` are the organisations it mentions (label, canonical id or None).
DOCS = [
    {"doc": "kickoff", "meeting": "Kickoff, Route Optimisation Pilot",
     "orgs": [("Pellucid Freightways", "C-1001")]},
    {"doc": "steering", "meeting": "Steering meeting",
     "orgs": [("Corriedale Grocers", None), ("Quillon Analytics", "C-1004")]},
    {"doc": "msa", "meeting": None, "orgs": [("Tamsinbay Water Authority", "C-1003")]},
]
#: the database-side organisation nodes the live graph also held: canonical id = a DOMAIN, so they
#: never match invoices.customer_id (an ingestion fact, reported cross-track)
DB_ORGS = [("C-1001", "pellucid-freightways.example"), ("C-1002", "corriedale-grocers.example")]


def _quarter(d: str) -> Optional[str]:
    x = dt.date.fromisoformat(d)
    if dt.date(2026, 10, 1) <= x < dt.date(2027, 1, 1):
        return "this_quarter"
    if dt.date(2026, 7, 1) <= x < dt.date(2026, 10, 1):
        return "last_quarter"
    return None


def cohort(entity_kinds: tuple[str, ...]) -> set[str]:
    """Customer ids the documents side should offer, computed from DOCS by the documented rule:
    meeting rows reach what their note mentions; organisation rows are themselves; document rows
    reach what they mention."""
    out: set[str] = set()
    for d in DOCS:
        ids = {cid for _l, cid in d["orgs"] if cid}
        if "meeting" in entity_kinds and d["meeting"]:
            out |= ids
        if "organisation" in entity_kinds or "document" in entity_kinds:
            out |= ids
    return out


def expected(entity_kinds: tuple[str, ...], status: str = "open", by: str = "customer_id"
             ) -> dict[str, dict[str, Decimal]]:
    owner = {c: o for c, _n, o in CUSTOMERS}
    who = cohort(entity_kinds)
    out: dict[str, dict[str, Decimal]] = {}
    for _no, cid, due, amount, st in INVOICES:
        q = _quarter(due)
        if st != status or cid not in who or q is None:
            continue
        key = cid if by == "customer_id" else owner[cid]
        out.setdefault(key, {"this_quarter": Decimal(0), "last_quarter": Decimal(0)})
        out[key][q] += Decimal(amount)
    return out


def _span(doc: str, a: int, b: int) -> SourcePointer:
    return SourcePointer(source_id=FILES, kind="doc_span", document_id=doc, char_start=a,
                         char_end=b, trust=Trust.UNTRUSTED)


def _build_graph(store) -> None:
    from databridge_graph.kinds import EdgeKind, NodeKind
    from databridge_graph.schema import GraphEdge, GraphNode

    t = dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc)
    org_spans: dict[str, list[SourcePointer]] = {}
    org_ids: dict[str, Optional[str]] = {}
    for d in DOCS:
        text_len = 400
        doc_id = f"document:{d['doc']}"
        store.add_node(GraphNode(id=doc_id, kind=NodeKind("document"), label=d["doc"],
                                 sources=(_span(d["doc"], 0, text_len),), valid_from=t,
                                 system_from=t))
        if d["meeting"]:
            mid = f"meeting:{d['meeting'].lower()}"
            store.add_node(GraphNode(id=mid, kind=NodeKind("meeting"), label=d["meeting"],
                                     sources=(_span(d["doc"], 10, 40),), valid_from=t,
                                     system_from=t))
            store.add_edge(GraphEdge(id=f"e:{d['doc']}:m", src_id=doc_id, dst_id=mid,
                                     kind=EdgeKind("mentions"), sources=(_span(d["doc"], 10, 40),),
                                     valid_from=t, system_from=t))
        for i, (label, cid) in enumerate(d["orgs"]):
            org_spans.setdefault(label, []).append(_span(d["doc"], 100 + 40 * i, 120 + 40 * i))
            org_ids[label] = cid
    for label, spans in org_spans.items():
        store.add_node(GraphNode(id=f"organisation:{label.lower()}", kind=NodeKind("organisation"),
                                 label=label, canonical_id=org_ids[label], sources=tuple(spans),
                                 valid_from=t, system_from=t))
        for sp in spans:
            store.add_edge(GraphEdge(id=f"e:{sp.document_id}:{label}",
                                     src_id=f"document:{sp.document_id}",
                                     dst_id=f"organisation:{label.lower()}",
                                     kind=EdgeKind("mentions"), sources=(sp,), valid_from=t,
                                     system_from=t))
    for cid, domain in DB_ORGS:
        store.add_node(GraphNode(
            id=f"organisation:db-{cid.lower()}", kind=NodeKind("organisation"), label=cid,
            canonical_id=domain,
            sources=(SourcePointer(source_id=DB, kind="table_cell", table="customers",
                                   column="domain", row_id=cid, trust=Trust.UNTRUSTED),),
            valid_from=t, system_from=t))


@pytest.fixture
def lark(env):
    """A Larkspur-shaped world: ONE adapter agent managing a facts store and a files source."""
    from databridge_agent_runtime import McpFactsStore
    from databridge_graph.packs import deployment
    from databridge_graph.store import GraphStore
    from databridge_workflow_spec import AdapterAgentSpec, dump_spec

    _write_facts(env / "facts", DB, {
        "invoices": (["invoice_no", "customer_id", "due_on", "total_gbp", "status"],
                     [dict(zip(["invoice_no", "customer_id", "due_on", "total_gbp", "status"], r))
                      for r in INVOICES]),
        "customers": (["customer_id", "name", "account_owner"],
                      [dict(zip(["customer_id", "name", "account_owner"], r))
                       for r in CUSTOMERS])})
    aa_dir = env / "adapter_agents"
    aa_dir.mkdir(parents=True, exist_ok=True)
    spec = AdapterAgentSpec.model_validate({
        "id": AA, "title": "graph sync", "created_from": "keep both sources right",
        "connections": [DB, FILES], "extract": {"pack": "workspace"}, "status": "active"})
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


def _live_plan(entity_kinds, *, status="open", extra=None, combine_extra=None) -> dict:
    """The plan shape the live planner wrote (run fed-20261001T015628-b8fb8bb1)."""
    plan = {
        "subtasks": [
            {"id": "t1", "adapter_agent": AA, "connection": DB, "entity_kinds": ["invoices"],
             "filters": [{"field": "status", "op": "eq", "value": status}],
             "fields": ["invoice_no", "customer_id", "due_on", "total_gbp", "status"]},
            {"id": "t2", "adapter_agent": AA, "connection": FILES,
             "entity_kinds": list(entity_kinds), "link_edges": ["mentions"]},
            *(extra or [])],
        "combine": {"base": "t1",
                    "links": [{"subtask": "t2", "key": "customer_id",
                               "entity_kind": "organisation", "via": "canonical_id"}],
                    "group_by": ["customer_id"], "aggregate": {"op": "sum", "field": "total_gbp"},
                    "compare": {"axis": "valid_time", "field": "due_on",
                                "periods": ["this_quarter", "last_quarter"]},
                    **(combine_extra or {})}}
    return plan


def _table(ans, key: str = "customer_id") -> dict[str, dict[str, Decimal]]:
    out = {}
    for r in ans.table.get("rows", []):
        c = r["cells"]
        out[c[key]["value"]] = {p: Decimal(str(c[f"sum_total_gbp:{p}"]["value"]))
                                for p in ("this_quarter", "last_quarter")}
    return out


# ================================================================================================
# N-02 — the documents delegate offers identifiers; an empty link is never "complete"
# ================================================================================================
@pytest.mark.parametrize("kinds", [("meeting", "organisation"), ("meeting",), ("organisation",),
                                   ("document",)])
def test_n02_every_plan_shape_reaches_the_customer_id(lark, kinds):
    want = expected(kinds)
    assert want and "C-1001" in want                                     # non-vacuous
    ans = lark().answer("open invoices by customer for customers mentioned in meeting notes",
                        admission=ANALYST, plan=DelegationPlan.model_validate(_live_plan(kinds)))
    assert ans.status == "completed", ans.answer_markdown
    assert _table(ans) == want
    link = next(s for s in ans.table["steps"] if s["step"] == "link")
    assert link["identifiers_on_other_side"] >= len(cohort(kinds))


def test_n02_a_meeting_row_reaches_only_what_its_own_note_mentions(lark):
    """Co-mention is decided by provenance: the MSA (not a meeting note) mentions C-1003, so a
    meeting-rows plan must not reach it, while an organisation-rows plan does."""
    assert "C-1003" in cohort(("organisation",)) and "C-1003" not in cohort(("meeting",))
    from databridge_graph.kinds import EdgeKind, NodeKind
    from databridge_graph.schema import GraphEdge, GraphNode
    svc = lark()
    # a digest that MENTIONS the kickoff meeting (and Tamsinbay) but is not where the meeting
    # was read from: it does not contain the meeting row, so its mentions are not co-mentions
    t = dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc)
    svc.store.add_node(GraphNode(id="document:digest", kind=NodeKind("document"), label="digest",
                                 sources=(_span("digest", 0, 300),), valid_from=t, system_from=t))
    for dst, at in (("meeting:kickoff, route optimisation pilot", 5),
                    ("organisation:tamsinbay water authority", 60)):
        svc.store.add_edge(GraphEdge(id=f"e:digest:{dst}", src_id="document:digest", dst_id=dst,
                                     kind=EdgeKind("mentions"), sources=(_span("digest", at, at + 9),),
                                     valid_from=t, system_from=t))
    ans = svc.answer("q", admission=ANALYST,
                     plan=DelegationPlan.model_validate(_live_plan(("meeting",))))
    assert "C-1001" in _table(ans)                                       # non-vacuous
    assert "C-1003" not in _table(ans)


def test_n02_link_evidence_cites_both_edges_of_a_co_mention(lark):
    ans = lark().answer("q", admission=ANALYST,
                        plan=DelegationPlan.model_validate(_live_plan(("meeting",))))
    row = next(r for r in ans.table["rows"] if r["cells"]["customer_id"]["value"] == "C-1001")
    spans = {(p.get("document_id"), p.get("char_start")) for p in row["link_evidence"]}
    kickoff = next(d for d in DOCS if d["doc"] == "kickoff")
    org_at = 100 + 40 * [lbl for lbl, _c in kickoff["orgs"]].index("Pellucid Freightways")
    assert ("kickoff", 10) in spans and ("kickoff", org_at) in spans


def test_n02_unidentified_mentions_make_coverage_uncertain(lark):
    """Corriedale is mentioned in a meeting note but carries no id: the answer says so."""
    missing = sorted({lbl for d in DOCS if d["meeting"] for lbl, cid in d["orgs"] if not cid})
    assert missing                                                       # non-vacuous
    ans = lark().answer("q", admission=ANALYST,
                        plan=DelegationPlan.model_validate(_live_plan(("meeting",))))
    assert ans.coverage["status"] == "uncertain"
    text = " ".join(ans.coverage["reasons"])
    assert all(m in text for m in missing)
    assert "Reduced coverage" in ans.answer_markdown


def test_n02_a_link_with_no_identifiers_is_uncertain_never_complete(lark):
    """Before the fix this is exactly the live failure: an empty table, coverage complete."""
    plan = _live_plan(("meeting",))
    plan["combine"]["links"][0]["entity_kind"] = "location"   # no location carries an id
    ans = lark().answer("q", admission=ANALYST, plan=DelegationPlan.model_validate(plan))
    assert ans.table["rows"] == []
    assert ans.coverage["status"] == "uncertain"
    assert any("none carries a location.canonical_id identifier" in r
               for r in ans.coverage["reasons"])


def test_n02_identifiers_that_match_no_row_are_uncertain(lark):
    plan = _live_plan(("meeting",))
    plan["subtasks"][0]["filters"] = [{"field": "customer_id", "op": "eq", "value": "C-1005"}]
    assert "C-1005" not in cohort(("meeting",))
    ans = lark().answer("q", admission=ANALYST, plan=DelegationPlan.model_validate(plan))
    assert ans.table["rows"] == [] and ans.coverage["status"] == "uncertain"
    assert any("matched none of" in r for r in ans.coverage["reasons"])


def test_n02_combine_reports_the_empty_link_itself():
    """The rule in the engine, without the delegates: zero identifiers ⇒ ``uncertain``."""
    p = SourcePointer(source_id=DB, kind="table_cell", table="t", column="k", row_id="1",
                      trust=Trust.UNTRUSTED)
    from databridge_core import GroundedValue
    base = SubResult(subtask_id="b", adapter_agent=AA, connection=DB, rows=(
        ResultRow(row_id="1", cells={"k": GroundedValue(value="C-1", pointer=p)}),))
    other = SubResult(subtask_id="o", adapter_agent=AA, connection=FILES, rows=(
        ResultRow(row_id="x", cells={"document": GroundedValue(value="d", pointer=p)}),))
    from databridge_federated.catalogue import Catalogue
    cat = Catalogue(node_kinds=frozenset({"organisation"}), vocabulary_loaded=True)
    spec = CombineSpec.model_validate({"base": "b", "links": [
        {"subtask": "o", "key": "k", "entity_kind": "organisation"}]})
    t = combine(spec, {"b": base, "o": other}, today=TODAY, catalogue=cat)
    assert t.rows == [] and t.uncertain and "could not match anything" in t.uncertain[0]


def test_n02_a_person_row_links_only_as_a_candidate(lark, env):
    """A subject kind reached as the row ITSELF is still only a candidate."""
    from databridge_graph.kinds import EdgeKind, NodeKind
    from databridge_graph.schema import GraphEdge, GraphNode  # noqa: F401
    svc = lark()
    t = dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc)
    svc.store.add_node(GraphNode(id="person:dana", kind=NodeKind("person"), label="Dana Lee",
                                 canonical_id="C-1005", sources=(_span("kickoff", 200, 208),),
                                 valid_from=t, system_from=t))
    # a request after the write: the catalogue MEASURES which kinds a connection holds (E2E run
    # 5, F-07), so it is built after the person exists, as every request builds its own
    svc = lark()
    plan = _live_plan(("person",))
    plan["combine"]["links"][0]["entity_kind"] = "person"
    ans = svc.answer("q", admission=ANALYST, plan=DelegationPlan.model_validate(plan))
    assert "C-1005" in _table(ans)                                       # non-vacuous
    assert ans.requires_human_selection
    assert all(r["candidate"] for r in ans.table["rows"])
    _ = EdgeKind


EVIDENCE = Path(__file__).resolve().parents[3] / "var" / "e2e-20261001T0122"
RUN = EVIDENCE / "runs/federated/e2e2-analyst/fed-20261001T015628-b8fb8bb1.json"


@pytest.mark.skipif(not RUN.is_file(), reason="E2E run-2 evidence is not on this machine")
@pytest.mark.parametrize("kinds", [None, ("meeting",)])
def test_n02_replay_of_the_live_run(tmp_path, monkeypatch, kinds):
    """The run's OWN plan (and the [meeting] shape) against COPIES of the run's graph and facts
    (the evidence is never written). Expected: every invoice of the given status whose customer
    id is the canonical id of an organisation the files side reaches, computed by SQL here."""
    from databridge_graph.packs import deployment
    from databridge_graph.store import GraphStore
    from databridge_federated import build_service

    shutil.copytree(EVIDENCE / "facts", tmp_path / "facts")
    shutil.copytree(EVIDENCE / "adapter_agents", tmp_path / "adapter_agents")
    shutil.copy(EVIDENCE / "graph" / "workspace.db", tmp_path / "workspace.db")
    for k, sub in {"WALNUT_FACTS_DIR": "facts", "WALNUT_ADAPTER_AGENTS_DIR": "adapter_agents",
                   "WALNUT_RUNS_DIR": "runs"}.items():
        monkeypatch.setenv(k, str(tmp_path / sub))
    rec = json.loads(RUN.read_text(encoding="utf-8"))
    st = {s["tool"]: s for s in rec["steps"]}
    subtasks = [s["args"] for s in rec["steps"] if s["tool"].startswith("delegate.ask")]
    if kinds is not None:
        subtasks[1] = {**subtasks[1], "entity_kinds": list(kinds)}
    plan = DelegationPlan.model_validate({"subtasks": subtasks,
                                          "combine": st["federated.combine"]["args"]})
    files = subtasks[1]["connection"]
    g = sqlite3.connect(tmp_path / "workspace.db")
    q = ("SELECT DISTINCT o.canonical_id FROM nodes o JOIN edges e ON e.dst_id = o.id "
         "JOIN nodes d ON d.id = e.src_id WHERE o.kind='organisation' AND e.kind='mentions' "
         "AND o.system_to IS NULL AND e.system_to IS NULL AND d.system_to IS NULL "
         "AND o.canonical_id IS NOT NULL AND d.id LIKE ?")
    reached = {r[0] for r in g.execute(q, (f"document:mcp-{files}-%",))}
    if kinds == ("meeting",):
        meet = ("SELECT DISTINCT e.src_id FROM edges e JOIN nodes m ON m.id = e.dst_id "
                "WHERE m.kind='meeting' AND e.kind='mentions' AND e.system_to IS NULL")
        notes = {r[0] for r in g.execute(meet)}
        reached = {r[0] for r in g.execute(q + " AND d.id IN (%s)" % ",".join("?" * len(notes)),
                                           (f"document:mcp-{files}-%", *notes))}
    g.close()
    status = subtasks[0]["filters"][0]["value"]
    f = sqlite3.connect(tmp_path / "facts" / "larkspur_db_facts.sqlite")
    want: dict[str, Decimal] = {}
    for cid, due, total in f.execute("SELECT customer_id, due_on, total_gbp FROM invoices "
                                     "WHERE status = ? AND due_on >= '2026-10-01' AND "
                                     "due_on < '2027-01-01'", (status,)):
        if cid in reached:
            want[cid] = want.get(cid, Decimal(0)) + Decimal(repr(total))
    f.close()
    assert want, "the live run's data has a qualifying customer (C-1001)"   # non-vacuous
    with deployment("workspace") as vocab:
        store = GraphStore(str(tmp_path / "workspace.db"))
        try:
            svc = build_service(store=store, adapter_agents_root=tmp_path / "adapter_agents",
                                vocabulary=vocab, runs_dir=tmp_path / "runs")
            svc.clock = lambda: dt.datetime(2026, 10, 1, 1, 56, tzinfo=dt.timezone.utc)
            ans = svc.answer(rec["trigger"]["question"],
                             admission=CatalogueAdmission("e2e2-analyst"), plan=plan)
        finally:
            store.close()
    got = {r["cells"]["customer_id"]["value"]:
           Decimal(str(r["cells"]["sum_total_gbp:this_quarter"]["value"]))
           for r in ans.table["rows"]}
    assert {k: v for k, v in got.items() if v} == want
    assert ans.coverage["status"] != "complete" or all(
        s["identifiers_on_other_side"] for s in ans.table["steps"] if s["step"] == "link")


# ================================================================================================
# N-03 — lookups (account owner), "@today", and answering the planner's questions
# ================================================================================================
def test_n03_founder_plan_with_a_customers_link_groups_by_account_owner(lark):
    """The live planner's 'open' plan: t2 customers linked by customer_id, group by
    account_owner. It stopped with "t1 has no field account_owner"; the lookup is now repaired."""
    plan = _live_plan(("meeting", "organisation"), extra=[
        {"id": "t3", "adapter_agent": AA, "connection": DB, "entity_kinds": ["customers"],
         "fields": ["customer_id", "name", "account_owner"]}],
        combine_extra={"group_by": ["account_owner"]})
    plan["combine"]["links"].insert(0, {"subtask": "t3", "key": "customer_id",
                                        "other_field": "customer_id", "key_kind": "organisation"})
    ans = lark().answer("q", admission=ANALYST, plan=DelegationPlan.model_validate(plan))
    assert ans.status == "completed", ans.answer_markdown
    want = expected(("meeting", "organisation"), by="account_owner")
    assert want and _table(ans, "account_owner") == want
    # the owner value is grounded in the customers table cell it was looked up from
    from databridge_federated import expand
    row = ans.table["rows"][0]
    leaves = expand(SourcePointer.model_validate(row["cells"]["account_owner"]["pointer"]))
    assert any(getattr(lf, "column", None) == "account_owner" for lf in leaves)


def test_n03_an_unlinked_customers_subtask_becomes_a_key_lookup(lark):
    """The live planner's 'overdue' plan read customers but never linked it."""
    plan = _live_plan(("meeting",), extra=[
        {"id": "t3", "adapter_agent": AA, "connection": DB, "entity_kinds": ["customers"],
         "fields": ["customer_id", "name", "account_owner"]}],
        combine_extra={"group_by": ["account_owner"]})
    ans = lark().answer("q", admission=ANALYST, plan=DelegationPlan.model_validate(plan))
    assert ans.status == "completed", ans.answer_markdown
    assert _table(ans, "account_owner") == expected(("meeting",), by="account_owner")
    assert any("looked up from customers" in a for a in ans.plan["assumptions"])


def test_n03_ambiguous_lookup_excludes_the_row_never_picks():
    from databridge_core import GroundedValue
    from databridge_federated.catalogue import Catalogue

    def cell(v, row):
        return GroundedValue(value=v, pointer=SourcePointer(
            source_id=DB, kind="table_cell", table="t", column="c", row_id=row,
            trust=Trust.UNTRUSTED))
    base = SubResult(subtask_id="b", adapter_agent=AA, connection=DB, rows=(
        ResultRow(row_id="i1", cells={"cid": cell("C-1", "i1"), "amt": cell("5", "i1")}),
        ResultRow(row_id="i2", cells={"cid": cell("C-2", "i2"), "amt": cell("7", "i2")})))
    other = SubResult(subtask_id="o", adapter_agent=AA, connection=DB, rows=(
        ResultRow(row_id="c1", cells={"cid": cell("C-1", "c1"), "owner": cell("ann", "c1")}),
        ResultRow(row_id="c1b", cells={"cid": cell("C-1", "c1b"), "owner": cell("bo", "c1b")}),
        ResultRow(row_id="c2", cells={"cid": cell("C-2", "c2"), "owner": cell("cy", "c2")})))
    cat = Catalogue(node_kinds=frozenset({"organisation"}), vocabulary_loaded=True)
    spec = CombineSpec.model_validate({
        "base": "b", "links": [{"subtask": "o", "key": "cid", "other_field": "cid",
                                "key_kind": "organisation", "carry": ["owner"]}],
        "group_by": ["owner"], "aggregate": {"op": "sum", "field": "amt"}})
    t = combine(spec, {"b": base, "o": other}, today=TODAY, catalogue=cat)
    assert [r.cells["owner"].value for r in t.rows] == ["cy"]
    assert any("ambiguous" in e for e in t.excluded)


def test_n03_today_is_a_symbol_resolved_from_the_clock(lark):
    """'overdue' = open and due before today, without the model writing a date."""
    plan = _live_plan(("meeting", "organisation"))
    plan["subtasks"][0]["filters"].append({"field": "due_on", "op": "lt", "value": "@today"})
    want = {k: v for k, v in expected(("meeting", "organisation")).items()}
    overdue = {cid for _n, cid, due, _a, st in INVOICES
               if st == "open" and dt.date.fromisoformat(due) < TODAY}
    want = {}
    for _no, cid, due, amount, st in INVOICES:
        if st == "open" and cid in overdue and cid in cohort(("meeting", "organisation")) \
                and dt.date.fromisoformat(due) < TODAY and _quarter(due):
            want.setdefault(cid, {"this_quarter": Decimal(0), "last_quarter": Decimal(0)})
            want[cid][_quarter(due)] += Decimal(amount)
    assert want                                                          # non-vacuous
    ans = lark().answer("q", admission=ANALYST, plan=DelegationPlan.model_validate(plan))
    assert _table(ans) == want
    t1 = next(s for s in ans.plan["subtasks"] if s["id"] == "t1")
    assert {"field": "due_on", "op": "lt", "value": TODAY.isoformat()} in t1["filters"]


def _harness(respond, cost=0.0):
    from databridge_harness import FakeBackend, Harness
    backend = FakeBackend(respond, tokens_in=7, tokens_out=11, cost_usd=cost)
    return Harness(backend, config=HARNESS_CONFIG, profile="cloud", cache=None), backend


def test_n03_planner_questions_can_be_answered_back(lark):
    asked = "Should 'overdue' mean status = 'overdue', or open invoices whose due_on has passed?"
    good = _live_plan(("meeting",))
    seen: list[str] = []

    def respond(model, messages, guided_json):
        if messages[0]["content"].startswith("You are Walnut's main agent"):
            seen.append(messages[-1]["content"])
            if "ANSWERS" in messages[-1]["content"]:
                return json.dumps(good)
            return json.dumps({"questions": [asked]})
        return json.dumps({"claims": []})

    h, _b = _harness(respond)
    svc = lark(h)
    first = svc.answer("total overdue by customer for customers in meeting notes",
                       admission=ANALYST)
    assert first.status == "stopped" and first.questions == [asked]
    qid = first.question_items[0]["id"]
    assert qid == "fed:" + hashlib.sha1(" ".join(asked.split()).encode()).hexdigest()
    try:                                       # the compiler computes the SAME id (AUTO2)
        from databridge_automation.compiler import federated_question_id
        assert federated_question_id(asked) == qid
    except ImportError:
        pass
    reply = "open invoices whose due date has passed"
    second = svc.answer("total overdue by customer for customers in meeting notes",
                        admission=ANALYST, answers={qid: reply})
    assert second.status == "completed", second.answer_markdown
    assert reply in seen[-1] and asked in seen[-1]          # the answer WITH its question
    assert second.answers == {qid: reply}


def test_n03_question_ids_ignore_whitespace():
    assert question_id("a  b\n c ") == question_id("a b c")


# ================================================================================================
# N-19 — the run record meters the planner's and the narrator's model calls
# ================================================================================================
def test_n19_run_record_sums_harness_usage(lark, env):
    plan = _live_plan(("meeting",))

    def respond(model, messages, guided_json):
        if messages[0]["content"].startswith("You are Walnut's main agent"):
            return json.dumps(plan)
        return json.dumps({"claims": []})

    cost = 0.0125
    h, backend = _harness(respond, cost=cost)
    ans = lark(h).answer("q", admission=ANALYST)
    assert ans.status == "completed", ans.answer_markdown
    calls = backend.call_count
    assert calls == 2                                                    # planner + narrator
    rec = next((env / "runs").rglob(f"{ans.run_id}.json"))
    body = json.loads(rec.read_text(encoding="utf-8"))
    assert body["tokens"] == calls * (7 + 11)
    assert body["cost_usd"] == pytest.approx(calls * cost)
    _ = os

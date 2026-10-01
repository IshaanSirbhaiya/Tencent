"""E2E run 4 regressions for delegated querying (FED5): Q-01, Q-06.

* **Q-01** — while an adapter sync ran, the first federated question of the run was planned over a
  catalogue that offered ``larkspur_db`` as a DOCUMENTS connection (no facts tables), so the planner
  asked four false-premise questions ("the catalogue has no invoices table"). Reproduced here: the
  catalogue is described by several requests AT ONCE, the way the run's concurrent phase did. The
  facts store's read path writes a query artefact on first use with a per-process temporary name,
  so concurrent readers in one process collide on it (``PermissionError``); the catalogue took
  that as "no facts store" and fell back to documents. The catalogue now reads again, and a store
  that exists but cannot be read is REFRESHING: nothing is planned against it.
* **Q-06** — coverage was ``uncertain`` because an organisation mentioned in a meeting note (the
  asker's own company) has no canonical id, although it could not hold any invoice the link left
  out. An unidentified entity is now reported as an unlinked one, without reducing coverage, ONLY
  when every identifier the link did not match has a known holder whose name it does not resemble.

Expectations are COMPUTED from the fixture lists (or from SQL over copies of the run's evidence),
never written down.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import sqlite3
import threading
from decimal import Decimal
from pathlib import Path
from typing import Optional

import pytest

from fed_testkit import FOUNDER_Q, _write_facts, scripted_harness

from databridge_core import SourcePointer
from databridge_core.provenance import Trust
from databridge_federated import (
    CatalogueAdmission,
    DelegationPlan,
    SteadyFacts,
    build_catalogue,
)

TODAY = dt.date(2026, 10, 1)            # this quarter = Q4 2026, last quarter = Q3 2026
DB, FILES, AA = "ledger-db", "team-files", "ledger_sync"
ANALYST = CatalogueAdmission("analyst")

CUSTOMERS = [  # customer_id, name, account_owner — every one has a canonical-id node
    ("C-2001", "Harrowgate Freight Ltd", "OW-1"),
    ("C-2002", "Brindle Grocers Co-operative", "OW-2"),
    ("C-2003", "Fenwick Water Authority", "OW-3"),
]
INVOICES = [  # invoice_no, customer_id, due_on, total_gbp, status
    ("IN-1", "C-2001", "2026-10-20", "1200.00", "open"),
    ("IN-2", "C-2001", "2026-08-02", "300.50", "open"),
    ("IN-3", "C-2002", "2026-11-11", "4500.00", "open"),
    ("IN-4", "C-2003", "2026-12-01", "990.25", "open"),
    ("IN-5", "C-2002", "2026-09-30", "70.00", "paid"),
]
#: meeting notes: the organisations each mentions (label, canonical id or None). The asker's own
#: company is mentioned with no id — it is not a customer.
MEETINGS = [
    {"doc": "m1", "orgs": [("Harrowgate Freight Ltd", "C-2001"), ("Ashgrove Analytics Ltd", None)]},
    {"doc": "m2", "orgs": [("Brindle Grocers Co-operative", "C-2002")]},
]


# ------------------------------------------------------------------------------------------------
# the rule, restated independently of the engine (for computing expectations)
# ------------------------------------------------------------------------------------------------
def _words(s: str) -> set[str]:
    import re
    return set(re.findall(r"[^\W_]+", s.casefold()))


def could_be(label: str, name: str) -> bool:
    a, b = _words(label), _words(name)
    return not a or not b or 2 * len(a & b) >= min(len(a), len(b))


def _span(doc: str, a: int, b: int) -> SourcePointer:
    return SourcePointer(source_id=FILES, kind="doc_span", document_id=doc, char_start=a,
                         char_end=b, trust=Trust.UNTRUSTED)


def _build_graph(store, meetings: list[dict], customers: list[tuple]) -> None:
    from databridge_graph.kinds import EdgeKind, NodeKind
    from databridge_graph.schema import GraphEdge, GraphNode

    t = dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc)
    for cid, name, _o in customers:
        store.add_node(GraphNode(
            id=f"organisation:{cid.lower()}", kind=NodeKind("organisation"), label=name,
            canonical_id=cid,
            sources=(SourcePointer(source_id=DB, kind="table_cell", table="customers",
                                   column="name", row_id=cid, trust=Trust.UNTRUSTED),),
            valid_from=t, system_from=t))
    for m in meetings:
        doc_id, mid = f"document:{m['doc']}", f"meeting:{m['doc']}"
        store.add_node(GraphNode(id=doc_id, kind=NodeKind("document"), label=m["doc"],
                                 sources=(_span(m["doc"], 0, 400),), valid_from=t, system_from=t))
        store.add_node(GraphNode(id=mid, kind=NodeKind("meeting"), label=f"Meeting {m['doc']}",
                                 sources=(_span(m["doc"], 10, 40),), valid_from=t, system_from=t))
        store.add_edge(GraphEdge(id=f"e:{m['doc']}:m", src_id=doc_id, dst_id=mid,
                                 kind=EdgeKind("mentions"), sources=(_span(m["doc"], 10, 40),),
                                 valid_from=t, system_from=t))
        for i, (label, cid) in enumerate(m["orgs"]):
            sp = _span(m["doc"], 100 + 40 * i, 120 + 40 * i)
            oid = f"organisation:{cid.lower()}" if cid else f"organisation:x-{label.lower()}"
            if store.open_node_row(oid) is None:
                store.add_node(GraphNode(id=oid, kind=NodeKind("organisation"), label=label,
                                         canonical_id=cid, sources=(sp,), valid_from=t,
                                         system_from=t))
            store.add_edge(GraphEdge(id=f"e:{m['doc']}:{oid}", src_id=doc_id, dst_id=oid,
                                     kind=EdgeKind("mentions"), sources=(sp,), valid_from=t,
                                     system_from=t))


@pytest.fixture
def ledger(env):
    """One adapter agent over a facts store and a files source; ``make(meetings, customers)``."""
    from databridge_agent_runtime import McpFactsStore
    from databridge_graph.packs import deployment
    from databridge_graph.store import GraphStore
    from databridge_workflow_spec import AdapterAgentSpec, dump_spec

    cols_i = ["invoice_no", "customer_id", "due_on", "total_gbp", "status"]
    cols_c = ["customer_id", "name", "account_owner"]
    _write_facts(env / "facts", DB, {
        "invoices": (cols_i, [dict(zip(cols_i, r)) for r in INVOICES]),
        "customers": (cols_c, [dict(zip(cols_c, r)) for r in CUSTOMERS])})
    aa_dir = env / "adapter_agents"
    aa_dir.mkdir(parents=True, exist_ok=True)
    spec = AdapterAgentSpec.model_validate({
        "id": AA, "title": "ledger sync", "created_from": "keep both sources right",
        "connections": [DB, FILES], "extract": {"pack": "workspace"}, "status": "active"})
    (aa_dir / f"{AA}.yaml").write_text(dump_spec(spec), encoding="utf-8")
    stores = []
    with deployment("workspace") as vocab:
        def make(meetings=MEETINGS, customers=CUSTOMERS, facts=None, harness=None):
            from databridge_federated import build_service
            store = GraphStore()
            stores.append(store)
            _build_graph(store, meetings, customers)
            svc = build_service(store=store, harness=harness,
                                facts=facts if facts is not None else McpFactsStore(),
                                adapter_agents_root=aa_dir, vocabulary=vocab,
                                runs_dir=env / "runs")
            svc.clock = lambda: dt.datetime.combine(TODAY, dt.time(9), tzinfo=dt.timezone.utc)
            return svc
        yield make
        for s in stores:
            s.close()


def _plan() -> dict:
    """The run-4 plan shape (`artefacts/p3_fed.json` r1): invoices ⋉ customers (carry the owner)
    ⋉ organisations mentioned by meeting notes, grouped by owner, this vs last quarter."""
    return {
        "subtasks": [
            {"id": "t1", "adapter_agent": AA, "connection": DB, "entity_kinds": ["invoices"],
             "filters": [{"field": "status", "op": "eq", "value": "open"}],
             "fields": ["invoice_no", "customer_id", "due_on", "total_gbp", "status"]},
            {"id": "t2", "adapter_agent": AA, "connection": DB, "entity_kinds": ["customers"],
             "fields": ["customer_id", "name", "account_owner"]},
            {"id": "t3", "adapter_agent": AA, "connection": FILES, "entity_kinds": ["meeting"],
             "link_edges": ["mentions"]}],
        "combine": {"base": "t1", "links": [
            {"subtask": "t2", "key": "customer_id", "other_field": "customer_id",
             "key_kind": "organisation", "carry": ["account_owner"]},
            {"subtask": "t3", "key": "customer_id", "entity_kind": "organisation",
             "via": "canonical_id"}],
            "group_by": ["account_owner"], "aggregate": {"op": "sum", "field": "total_gbp"},
            "compare": {"axis": "valid_time", "field": "due_on",
                        "periods": ["this_quarter", "last_quarter"]}}}


def _expected_split(meetings, customers) -> tuple[set[str], set[str]]:
    """(proven unlinked, still uncertain) unidentified labels, by the documented rule."""
    mentioned = {cid for m in meetings for _l, cid in m["orgs"] if cid}
    known = {c for c, _n, _o in customers}
    base_keys = {cid for _n, cid, due, _t, st in INVOICES
                 if st == "open" and "2026-07-01" <= due < "2027-01-01" and cid in known}
    unmatched = base_keys - mentioned
    holders = [n for c, n, _o in customers if c in unmatched]
    if unmatched - {c for c, _n, _o in customers}:
        holders = None                                    # a holder we cannot name
    unidentified = {lbl for m in meetings for lbl, cid in m["orgs"] if not cid}
    if holders is None:
        return set(), unidentified
    proven = {u for u in unidentified if not any(could_be(u, h) for h in holders)}
    return proven, unidentified - proven


# ------------------------------------------------------------------------------------------------
# Q-06
# ------------------------------------------------------------------------------------------------
def test_q06_an_unlinked_organisation_that_holds_no_left_out_row_does_not_reduce_coverage(ledger):
    proven, rest = _expected_split(MEETINGS, CUSTOMERS)
    assert proven and not rest                                         # non-vacuous
    ans = ledger().answer("q", admission=ANALYST,
                          plan=DelegationPlan.model_validate(_plan()))
    assert ans.table["rows"]                                           # an answer was computed
    assert ans.coverage["status"] == "complete", ans.coverage
    notes = " ".join(ans.coverage.get("notes") or [])
    for label in proven:
        assert repr(label) in notes and "is an unlinked organisation, not a customer" in notes
    assert not any(label in r for r in ans.coverage["reasons"] for label in proven)
    assert ans.table["notes"] == ans.coverage["notes"]


def test_q06_a_short_form_that_could_be_a_left_out_customer_stays_uncertain(ledger):
    """The run-3 shape (Corriedale): a keyless mention that resembles the holder of an unmatched
    identifier could BE that customer, so coverage stays uncertain and names it."""
    holder = next(n for c, n, _o in CUSTOMERS
                  if c not in {cid for m in MEETINGS for _l, cid in m["orgs"]})
    short = holder.split()[0]
    meetings = [*MEETINGS, {"doc": "m3", "orgs": [(short, None)]}]
    proven, rest = _expected_split(meetings, CUSTOMERS)
    assert short in rest and proven                                    # both outcomes present
    ans = ledger(meetings).answer("q", admission=ANALYST,
                                  plan=DelegationPlan.model_validate(_plan()))
    assert ans.coverage["status"] == "uncertain"
    reasons = " ".join(ans.coverage["reasons"])
    assert repr(short) in reasons
    notes = " ".join(ans.coverage.get("notes") or [])
    assert all(repr(p) in notes for p in proven) and repr(short) not in notes


def test_q06_an_unmatched_identifier_with_no_known_holder_proves_nothing(ledger):
    """No graph node carries C-2003 (the unmatched key): its holder's name is unknown, so no
    unidentified mention can be shown unrelated — coverage stays uncertain."""
    customers = [c for c in CUSTOMERS if c[0] != "C-2003"]
    ans = ledger(MEETINGS, customers).answer("q", admission=ANALYST,
                                             plan=DelegationPlan.model_validate(_plan()))
    unidentified = {lbl for m in MEETINGS for lbl, cid in m["orgs"] if not cid}
    assert unidentified                                                # non-vacuous
    assert ans.coverage["status"] == "uncertain"
    assert all(repr(u) in " ".join(ans.coverage["reasons"]) for u in unidentified)
    assert not ans.coverage.get("notes")


def test_q06_never_for_a_subject_kind():
    """A person with no identifier is never shown to be "not" someone (hard constraint 1)."""
    from databridge_federated.catalogue import Catalogue
    from databridge_federated.combine import _unholders
    from databridge_federated.contracts import LinkSpec

    cat = Catalogue(subject_kinds=frozenset({"person"}))
    link = LinkSpec.model_validate({"subtask": "p", "key": "contact_id", "entity_kind": "person",
                                    "via": "canonical_id"})
    proven, _h = _unholders(link, ["Dana Lee"], set(), store=None, catalogue=cat,
                            admits=lambda _s: True)
    assert proven == []
    org = LinkSpec.model_validate({"subtask": "p", "key": "contact_id",
                                   "entity_kind": "organisation", "via": "canonical_id"})
    assert _unholders(org, ["Dana Lee"], set(), store=None, catalogue=cat,
                      admits=lambda _s: True)[0] == ["Dana Lee"]      # the guard is the kind


EVIDENCE4 = Path(__file__).resolve().parents[3] / "var" / "e2e-20261001T1255"
P3_FED = EVIDENCE4 / "artefacts" / "p3_fed.json"


def _copy_evidence(tmp_path: Path, monkeypatch) -> None:
    shutil.copytree(EVIDENCE4 / "facts", tmp_path / "facts",
                    ignore=shutil.ignore_patterns("*.queries"))
    shutil.copytree(EVIDENCE4 / "adapter_agents", tmp_path / "adapter_agents")
    for f in (EVIDENCE4 / "graph").iterdir():
        if f.name.startswith("workspace.db"):
            shutil.copy(f, tmp_path / f.name)
    for k, sub in {"WALNUT_FACTS_DIR": "facts", "WALNUT_ADAPTER_AGENTS_DIR": "adapter_agents",
                   "WALNUT_RUNS_DIR": "runs"}.items():
        monkeypatch.setenv(k, str(tmp_path / sub))


@pytest.mark.skipif(not P3_FED.is_file(), reason="E2E run-4 evidence is not on this machine")
def test_q06_replay_of_the_live_answer(tmp_path, monkeypatch):
    """The run's OWN plan (r1, after `answers`) against COPIES of the run's graph and facts (the
    evidence is never written). Expected by SQL over the copies, by the documented rule."""
    from databridge_graph.packs import deployment
    from databridge_graph.store import GraphStore
    from databridge_federated import build_service

    _copy_evidence(tmp_path, monkeypatch)
    body = json.loads(P3_FED.read_text(encoding="utf-8"))["primary"]["r1"]["body"]
    plan = DelegationPlan.model_validate(body["plan"])
    g = sqlite3.connect(tmp_path / "workspace.db")
    meet_docs = {r[0] for r in g.execute(
        "SELECT DISTINCT e.src_id FROM edges e JOIN nodes m ON m.id = e.dst_id WHERE "
        "m.kind='meeting' AND e.kind='mentions' AND e.system_to IS NULL AND m.system_to IS NULL")}
    orgs = g.execute(
        "SELECT DISTINCT e.src_id, o.label, o.canonical_id FROM edges e JOIN nodes o ON "
        "o.id = e.dst_id WHERE o.kind='organisation' AND e.kind='mentions' AND "
        "e.system_to IS NULL AND o.system_to IS NULL").fetchall()
    reached = {cid for src, _l, cid in orgs if src in meet_docs and cid}
    unidentified = {lbl for src, lbl, cid in orgs if src in meet_docs and not cid}
    holder_names = {cid: lbl for cid, lbl in g.execute(
        "SELECT canonical_id, label FROM nodes WHERE kind='organisation' AND system_to IS NULL "
        "AND canonical_id IS NOT NULL")}
    g.close()
    f = sqlite3.connect(tmp_path / "facts" / "larkspur_db_facts.sqlite")
    owner = dict(f.execute("SELECT customer_id, account_owner FROM customers"))
    base = [(cid, due, total) for cid, due, total in f.execute(
        "SELECT customer_id, due_on, total_gbp FROM invoices WHERE status = 'open' AND "
        "due_on >= '2026-07-01' AND due_on < '2027-01-01'") if cid in owner]
    f.close()
    unmatched = {cid for cid, _d, _t in base} - reached
    assert unidentified, "the run's notes mention an organisation with no id"   # non-vacuous
    names = [holder_names.get(c) for c in unmatched]
    proven = set() if None in names else {
        u for u in unidentified if not any(could_be(u, n) for n in names)}
    want: dict[str, Decimal] = {}
    for cid, due, total in base:
        if cid in reached and due >= "2026-10-01":
            want[owner[cid]] = want.get(owner[cid], Decimal(0)) + Decimal(repr(total))
    assert want                                                        # non-vacuous
    with deployment("workspace") as vocab:
        store = GraphStore(str(tmp_path / "workspace.db"))
        try:
            svc = build_service(store=store, adapter_agents_root=tmp_path / "adapter_agents",
                                vocabulary=vocab, runs_dir=tmp_path / "runs")
            svc.clock = lambda: dt.datetime(2026, 10, 1, 13, 0, tzinfo=dt.timezone.utc)
            ans = svc.answer(body["question"], admission=CatalogueAdmission("e2e4-analyst"),
                             plan=plan)
        finally:
            store.close()
    got = {r["cells"]["account_owner"]["value"]:
           Decimal(str(r["cells"]["sum_total_gbp:this_quarter"]["value"]))
           for r in ans.table["rows"]}
    assert {k: v for k, v in got.items() if v} == want
    notes = " ".join(ans.coverage.get("notes") or [])
    reasons = " ".join(ans.coverage["reasons"])
    assert proven                                                      # the live case
    for u in unidentified:
        assert (repr(u) in notes) == (u in proven)
        assert (repr(u) in reasons) == (u not in proven)
    assert ans.coverage["status"] == ("complete" if proven == unidentified else "uncertain")


# ------------------------------------------------------------------------------------------------
# Q-01
# ------------------------------------------------------------------------------------------------
def _measured_tables(path: Path) -> set[str]:
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                         "AND substr(name, 1, 1) <> '_'")}
    finally:
        db.close()


def _concurrent_catalogues(n: int, build) -> list:
    out: list = [None] * n
    gate = threading.Barrier(n)

    def go(i: int) -> None:
        gate.wait()
        out[i] = build()
    threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


@pytest.mark.skipif(not P3_FED.is_file(), reason="E2E run-4 evidence is not on this machine")
def test_q01_replay_concurrent_first_reads_of_the_run_store_all_see_its_tables(tmp_path,
                                                                              monkeypatch):
    """The run's facts store and registry (copies; no query artefacts yet, as at the run's first
    federated question), described by several requests at once. Each one must list the store as
    TABULAR with every table SQLite says it holds — never as documents."""
    from databridge_agent_runtime import McpFactsStore
    from databridge_federated import build_service, registry_specs

    _copy_evidence(tmp_path, monkeypatch)
    conn = "larkspur_db"
    want = _measured_tables(tmp_path / "facts" / f"{conn}_facts.sqlite")
    assert {"invoices", "customers"} <= want                           # non-vacuous
    specs, _bad = registry_specs(tmp_path / "adapter_agents")
    assert any(conn in s.connections for s in specs)
    for _round in range(3):
        shutil.rmtree(tmp_path / "facts" / f"{conn}_facts.queries", ignore_errors=True)
        svcs = _concurrent_catalogues(6, lambda: build_service(
            store=object(), facts=McpFactsStore(), adapter_agents_root=tmp_path / "adapter_agents",
            vocabulary=None, runs_dir=tmp_path / "runs"))
        for svc in svcs:
            entry = next(c for a in svc.catalogue.agents.values() for c in a.connections
                         if c.connection == conn)
            assert entry.channel == "tabular", svc.catalogue.problems
            assert {t.name for t in entry.tables} == want
            assert not svc.catalogue.refreshing


class FlakyFacts:
    """The real store, whose first ``fail`` statements raise what the run's store raised."""

    def __init__(self, fail: int, exc: Optional[BaseException] = None) -> None:
        from databridge_agent_runtime import McpFactsStore
        self.inner, self.fail, self.calls = McpFactsStore(), fail, 0
        self.exc = exc or PermissionError(13, "The process cannot access the file because it is "
                                              "being used by another process")

    def available(self, connection: str) -> bool:
        return self.inner.available(connection)

    def query(self, connection, sql, **kw):
        self.calls += 1
        if self.calls <= self.fail:
            raise self.exc
        return self.inner.query(connection, sql, **kw)


def _no_sleep(_s: float) -> None:
    return None


def test_q01_a_contended_store_is_read_again(world, monkeypatch):
    from databridge_federated import catalogue as catmod
    monkeypatch.setattr(catmod.time, "sleep", _no_sleep)
    from fed_testkit import CRM
    want = _measured_tables(Path(world.root) / "facts" / f"{CRM}_facts.sqlite")
    assert want                                                        # non-vacuous
    specs = [type("S", (), {"id": "a", "title": "a", "status": "active",
                            "connections": [CRM]})()]
    cat = build_catalogue(specs, facts=FlakyFacts(fail=catmod.DESCRIBE_ATTEMPTS - 1),
                          vocabulary=world.vocab)
    entry = cat.agent("a").connection(CRM)
    assert entry.channel == "tabular" and {t.name for t in entry.tables} == want
    assert not cat.refreshing


def test_q01_an_unreadable_store_is_refreshing_and_nothing_is_planned(world, monkeypatch):
    """Every read fails: the store's tables are UNKNOWN, not absent. It is not offered as a
    documents connection, the planning model is never asked, and the answer says the catalogue
    is being refreshed — no question about a missing table."""
    from databridge_federated import build_service
    from databridge_federated import catalogue as catmod
    from fed_testkit import CRM, FOUNDER_PLAN
    monkeypatch.setattr(catmod.time, "sleep", _no_sleep)
    harness, calls = scripted_harness(FOUNDER_PLAN)
    svc = build_service(store=world.store, harness=harness, facts=FlakyFacts(fail=10 ** 6),
                        retriever=world.retriever, adapter_agents_root=world.aa_dir,
                        vocabulary=world.vocab, runs_dir=world.runs_dir)
    assert CRM in svc.catalogue.refreshing
    assert all(c.connection != CRM for a in svc.catalogue.agents.values() for c in a.connections)
    ans = svc.answer(FOUNDER_Q, admission=CatalogueAdmission("analyst"))
    assert calls == []                                                 # no plan was asked for
    assert ans.status == "stopped" and ans.questions == [] and not ans.table
    assert "being refreshed" in ans.answer_markdown and CRM in ans.answer_markdown
    assert [s["reason_code"] for s in ans.coverage["skipped"]] == ["catalogue_refreshing"]
    res = svc.ask_delegate("crm_sources", {"id": "t", "entity_kinds": ["invoices"]},
                           admission=CatalogueAdmission("analyst"))
    assert res.error and "being refreshed" in res.error and not res.rows


def test_q01_steady_reads_retry_file_errors_only(world, monkeypatch):
    from databridge_federated import catalogue as catmod
    from fed_testkit import CRM
    monkeypatch.setattr(catmod.time, "sleep", _no_sleep)
    sql = "SELECT COUNT(*) AS n FROM invoices"
    ok = SteadyFacts(FlakyFacts(fail=2)).query(CRM, sql)
    assert ok.rows == [[len(world.invoices)]]
    refused = FlakyFacts(fail=1, exc=ValueError("refused: not read-only"))
    with pytest.raises(ValueError):
        SteadyFacts(refused).query(CRM, sql)
    assert refused.calls == 1                                          # a refusal is final

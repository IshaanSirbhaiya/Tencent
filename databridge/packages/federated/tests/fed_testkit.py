"""A synthetic world for delegated querying — every name and number is fictional.

* ``crm-db`` — a REAL facts store (SQLite + manifest, read through `count_query`): ``invoices``
  (invoice_id, customer_id, account_owner, amount, due_on, status, issued_on) across three
  quarters, and ``tickets`` (ticket_id, contact_email, amount, opened_on) for the person-link test;
* ``notes-files`` — meeting notes: graph ``meeting`` nodes (dated, grounded) that ``mention``
  organisations (canonical id = customer id) or people (canonical id = email), plus passages whose
  ``doc_span`` pointers resolve into the same documents;
* two adapter agents in a real registry directory: ``crm_sources`` and ``notes_sources``.

Expectations in the tests are COMPUTED from the fixture lists here, never written down.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from databridge_core import SourcePointer
from databridge_core.provenance import Trust

TODAY = dt.date(2026, 9, 15)            # this quarter = Q3 2026, last quarter = Q2 2026
CRM = "crm-db"
NOTES = "notes-files"
OWNERS = {"C-001": "alice", "C-002": "bob", "C-003": "alice", "C-004": "chen", "C-005": "bob",
          "C-006": "chen"}
DUE_DATES = ["2026-02-11", "2026-04-09", "2026-05-21", "2026-06-30", "2026-07-01", "2026-08-14",
             "2026-09-30", "2026-10-02", "2026-03-31"]


def make_invoices() -> list[dict]:
    rows = []
    customers = sorted(OWNERS)
    for i in range(45):
        c = customers[i % len(customers)]
        due = DUE_DATES[(i * 5) % len(DUE_DATES)]
        amount = f"{(i * 137) % 900 + 50}.{(i * 7) % 100:02d}"
        rows.append({"invoice_id": f"INV-{1000 + i}", "customer_id": c,
                     "account_owner": OWNERS[c], "amount": amount, "due_on": due,
                     "status": "paid" if i % 3 == 2 else "overdue",
                     "issued_on": (dt.date.fromisoformat(due) - dt.timedelta(days=30)).isoformat()})
    return rows


NOTES_DATA = [
    {"doc": "note-q3-a", "date": "2026-08-04",
     "text": "Quarterly review. The customer raised a complaint about late deliveries.",
     "mentions": ["C-001", "C-003"]},
    {"doc": "note-q3-b", "date": "2026-07-20", "text": "Kickoff went well; no issues raised.",
     "mentions": ["C-002"]},
    {"doc": "note-q2", "date": "2026-05-10", "text": "Customer complaint about invoice errors.",
     "mentions": ["C-004"]},
    {"doc": "note-q3-c", "date": "2026-09-01",
     "text": "Escalation: a formal Complaint was lodged over billing.", "mentions": ["C-005"]},
]
PEOPLE = [  # person-link fixture: one exact email, one same-name person with no identifier
    {"doc": "note-q3-p", "date": "2026-08-20",
     "text": "Dana Lee made a complaint about support response times.",
     "person": {"id": "person:dana-lee", "label": "Dana Lee", "canonical_id": "dana@larkspur.test"}},
    {"doc": "note-q3-p2", "date": "2026-08-21",
     "text": "Another Dana Lee complaint, identity unknown.",
     "person": {"id": "person:dana-lee-2", "label": "Dana Lee", "canonical_id": None}},
]
TICKETS = [
    {"ticket_id": "T-1", "contact_email": "dana@larkspur.test", "amount": "120.10",
     "opened_on": "2026-08-01"},
    {"ticket_id": "T-2", "contact_email": "dana@larkspur.test", "amount": "80.05",
     "opened_on": "2026-08-09"},
    {"ticket_id": "T-3", "contact_email": "dana.lee@other.test", "amount": "999.99",
     "opened_on": "2026-08-10"},
]


# ------------------------------------------------------------------------------------------------
def quarter(d: str) -> Optional[str]:
    x = dt.date.fromisoformat(d)
    if dt.date(2026, 7, 1) <= x < dt.date(2026, 10, 1):
        return "this_quarter"
    if dt.date(2026, 4, 1) <= x < dt.date(2026, 7, 1):
        return "last_quarter"
    return None


def complainers_this_quarter() -> set[str]:
    return {c for n in NOTES_DATA if "complaint" in n["text"].lower()
            and quarter(n["date"]) == "this_quarter" for c in n["mentions"]}


def expected_founder_table(invoices: list[dict]) -> dict[str, dict[str, Decimal]]:
    """owner -> {this_quarter, last_quarter} computed independently of the engine."""
    who = complainers_this_quarter()
    out: dict[str, dict[str, Decimal]] = {}
    for r in invoices:
        q = quarter(r["due_on"])
        if r["status"] != "overdue" or r["customer_id"] not in who or q is None:
            continue
        out.setdefault(r["account_owner"], {"this_quarter": Decimal(0),
                                            "last_quarter": Decimal(0)})
        out[r["account_owner"]][q] += Decimal(r["amount"])
    return out


def expected_ids(invoices: list[dict], owner: str, q: str) -> set[str]:
    who = complainers_this_quarter()
    return {r["invoice_id"] for r in invoices if r["status"] == "overdue"
            and r["customer_id"] in who and r["account_owner"] == owner
            and quarter(r["due_on"]) == q}


# ------------------------------------------------------------------------------------------------
class CountingFacts:
    """The real `McpFactsStore`, counting every statement (the pushdown proof)."""

    def __init__(self) -> None:
        from databridge_agent_runtime import McpFactsStore
        self.inner = McpFactsStore()
        self.calls: list[tuple[str, Any]] = []

    def available(self, connection: str) -> bool:
        return self.inner.available(connection)

    def query(self, connection, sql, *, params=None, question=None):
        self.calls.append((sql, params))
        return self.inner.query(connection, sql, params=params, question=question)


class FakeRetriever:
    """§5.5 shape over a fixed corpus: every passage containing the query (case-insensitive)."""

    def __init__(self, passages: list[dict]) -> None:
        self.passages = passages
        self.calls: list[str] = []

    def search(self, query, *, k=8, sources=None, kinds=None, mode="hybrid"):
        self.calls.append(query)
        out = [p for p in self.passages if (not sources or p["source_id"] in sources)
               and query.lower() in p["text"].lower()]
        return out[:k]


def write_mapping(sources_dir: Path, source: str, tables: dict[str, list[str]],
                  subject: Optional[dict[str, list[str]]] = None,
                  subject_tables: tuple[str, ...] = ()) -> None:
    """An APPROVED mapping for ``source`` (the shape `sources/<id>.mapping.yaml` has): each table
    a non-subject record, except ``subject_tables`` (mapped to the pack's subject kind); each
    column in ``subject[table]`` the evidence of an edge FROM a subject (Codex R-029 r1 #1/#6:
    the catalogue reads which columns name people from here, never from a column name)."""
    import yaml

    sources_dir.mkdir(parents=True, exist_ok=True)
    doc = {"source_id": source, "tables": {}}
    for name, cols in tables.items():
        kind = "person" if name in subject_tables else "record"
        doc["tables"][f"public.{name}"] = {
            "status": "approved", "node": {"kind": kind, "label": "{walnut_row_key}"},
            "columns": list(cols),
            "edges": [{"kind": "references", "src": f"person:{{walnut_fk_{c}}}",
                       "dst": "record:{walnut_row_key}", "evidence": [c]}
                      for c in (subject or {}).get(name, [])]}
    (sources_dir / f"{source}.mapping.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")


#: Which world columns name people (account owners are staff; ticket contacts are people).
SUBJECT_COLUMNS = {"invoices": ["account_owner"], "tickets": ["contact_email"]}


def _write_facts(facts_dir: Path, source: str, tables: dict[str, tuple[list[str], list[dict]]],
                 subject: Optional[dict[str, list[str]]] = None) -> None:
    from databridge_mcp_ingest.tabular.facts import sha256_file
    facts_dir.mkdir(parents=True, exist_ok=True)
    path = facts_dir / f"{source}_facts.sqlite"
    db = sqlite3.connect(path)
    for name, (cols, rows) in tables.items():
        types = {c: ("NUMERIC" if c == "amount" else "TEXT") for c in cols}
        db.execute(f'CREATE TABLE "{name}" (' + ", ".join(f'"{c}" {types[c]}' for c in cols)
                   + ")")
        db.executemany(f'INSERT INTO "{name}" VALUES ({", ".join("?" for _ in cols)})',
                       [tuple(r[c] for c in cols) for r in rows])
    db.execute("CREATE TABLE _facts_meta (key TEXT PRIMARY KEY, value TEXT)")
    db.commit()
    db.close()
    write_mapping(facts_dir.parent / "sources", source,
                  {name: list(cols) for name, (cols, _rows) in tables.items()}, subject)
    (facts_dir / f"{source}_facts.json").write_text(json.dumps(
        {"source_id": source, "extracted_at": "2026-09-15T06:00:00+00:00", "status": "ok",
         "sha256": sha256_file(path), "tables": []}), encoding="utf-8")


def _span(doc: str, start: int, end: int) -> SourcePointer:
    return SourcePointer(source_id=NOTES, kind="doc_span", document_id=doc, char_start=start,
                         char_end=end, trust=Trust.UNTRUSTED)


def _cell(table: str, col: str, row: str) -> SourcePointer:
    return SourcePointer(source_id=CRM, kind="table_cell", table=table, column=col, row_id=row,
                         trust=Trust.UNTRUSTED)


def _build_graph(store) -> list[dict]:
    from databridge_graph.kinds import EdgeKind, NodeKind
    from databridge_graph.schema import GraphEdge, GraphNode

    t_sys = dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc)
    for cid in sorted(OWNERS):
        store.add_node(GraphNode(
            id=f"organisation:{cid.lower()}", kind=NodeKind("organisation"),
            label=f"Customer {cid}", canonical_id=cid, sources=(_cell("customers", "id", cid),),
            valid_from=dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc), system_from=t_sys))
    passages = []
    for n in NOTES_DATA + PEOPLE:
        when = dt.datetime.fromisoformat(n["date"]).replace(tzinfo=dt.timezone.utc)
        text = n["text"]
        whole = _span(n["doc"], 0, len(text))
        store.add_node(GraphNode(
            id=f"meeting:{n['doc']}", kind=NodeKind("meeting"), label=f"Meeting {n['doc']}",
            attrs={"date": n["date"]}, attr_sources={"date": whole}, sources=(whole,),
            valid_from=when, system_from=t_sys))
        targets = [f"organisation:{c.lower()}" for c in n.get("mentions", [])]
        if n.get("person"):
            p = n["person"]
            if store.open_node_row(p["id"]) is None:
                store.add_node(GraphNode(
                    id=p["id"], kind=NodeKind("person"), label=p["label"],
                    canonical_id=p["canonical_id"], sources=(whole,), valid_from=when,
                    system_from=t_sys))
            targets.append(p["id"])
        for tgt in targets:
            store.add_edge(GraphEdge(
                id=f"edge:{n['doc']}:{tgt}", src_id=f"meeting:{n['doc']}", dst_id=tgt,
                kind=EdgeKind("mentions"), sources=(whole,), valid_from=when, system_from=t_sys))
        passages.append({"chunk_id": f"{n['doc']}#0", "doc_id": n["doc"], "source_id": NOTES,
                         "text": text, "locator": "p1", "approx_locator": False, "score": 1.0,
                         "pointer": whole})
    return passages


@dataclass
class World:
    root: Path
    facts: CountingFacts
    store: Any
    retriever: FakeRetriever
    invoices: list[dict]
    vocab: Any
    runs_dir: Path
    aa_dir: Path

    def service(self, *, harness: Any = None, **kw: Any):
        from databridge_federated import build_service
        svc = build_service(store=self.store, harness=harness, facts=self.facts,
                            retriever=self.retriever, adapter_agents_root=self.aa_dir,
                            vocabulary=self.vocab, runs_dir=self.runs_dir)
        svc.clock = lambda: dt.datetime.combine(TODAY, dt.time(9), tzinfo=dt.timezone.utc)
        for k, v in kw.items():
            setattr(svc, k, v)
        self.facts.calls.clear()                     # catalogue reads are not the answer's
        return svc


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name, sub in {"WALNUT_FACTS_DIR": "facts", "WALNUT_ADAPTER_AGENTS_DIR": "adapter_agents",
                      "WALNUT_RUNS_DIR": "runs", "WALNUT_CAPTURE_DIR": "capture",
                      "WALNUT_SOURCES_DIR": "sources"}.items():
        monkeypatch.setenv(name, str(tmp_path / sub))
    return tmp_path


@pytest.fixture
def world(env):
    from databridge_graph.packs import deployment
    from databridge_graph.store import GraphStore
    from databridge_workflow_spec import AdapterAgentSpec, dump_spec

    invoices = make_invoices()
    _write_facts(env / "facts", CRM, {
        "invoices": (["invoice_id", "customer_id", "account_owner", "amount", "due_on", "status",
                      "issued_on"], invoices),
        "tickets": (["ticket_id", "contact_email", "amount", "opened_on"], TICKETS)},
        subject=SUBJECT_COLUMNS)
    aa_dir = env / "adapter_agents"
    aa_dir.mkdir(parents=True, exist_ok=True)
    for aid, conns in (("crm_sources", [CRM]), ("notes_sources", [NOTES])):
        spec = AdapterAgentSpec.model_validate({
            "id": aid, "title": aid.replace("_", " "), "created_from": f"keep {conns} right",
            "connections": conns, "extract": {"pack": "workspace"}, "status": "active"})
        (aa_dir / f"{aid}.yaml").write_text(dump_spec(spec), encoding="utf-8")
    with deployment("workspace") as vocab:
        store = GraphStore()
        passages = _build_graph(store)
        yield World(root=env, facts=CountingFacts(), store=store,
                    retriever=FakeRetriever(passages), invoices=invoices, vocab=vocab,
                    runs_dir=env / "runs", aa_dir=aa_dir)
        store.close()


# ------------------------------------------------------------------------------------------------
# the founder's question, and a scripted model behind the REAL harness
# ------------------------------------------------------------------------------------------------
FOUNDER_Q = ("Total overdue amount by account owner, for customers who raised a complaint in a "
             "meeting note this quarter, compared with last quarter.")

FOUNDER_PLAN = {  # what a planning model returns: names and symbols only — no numbers, no dates
    "subtasks": [
        {"id": "inv", "adapter_agent": "crm_sources", "entity_kinds": ["invoices"],
         "filters": [{"field": "status", "op": "eq", "value": "overdue"}],
         "fields": ["customer_id", "account_owner", "amount", "due_on"]},
        {"id": "notes", "adapter_agent": "notes_sources", "entity_kinds": ["meeting"],
         "filters": [{"field": "text", "op": "contains", "value": "complaint"}],
         "time_window": {"axis": "valid_time", "period": "this_quarter"},
         "link_edges": ["mentions"]},
    ],
    "combine": {
        "base": "inv",
        "links": [{"subtask": "notes", "key": "customer_id", "entity_kind": "organisation",
                   "via": "canonical_id"}],
        "group_by": ["account_owner"],
        "aggregate": {"op": "sum", "field": "amount"},
        "compare": {"axis": "valid_time", "field": "due_on",
                    "periods": ["this_quarter", "last_quarter"]},
    },
    "assumptions": ["'this quarter' / 'last quarter' compare invoices by due date; the complaint "
                    "cohort is customers mentioned in a complaint note dated this quarter"],
}

HARNESS_CONFIG = {"routing": {"agent": "planner"}, "aliases": {"planner": {"model": "fake/planner"}},
                  "profiles": {"cloud": {}}}


def scripted_harness(plan: Optional[dict] = None,
                     narrate: Optional[Callable[[dict], dict]] = None):
    """The planner call returns ``plan``; the narrator call gets the TABLE payload and returns
    ``narrate(payload)``. Every call is recorded in ``calls``."""
    from databridge_harness import FakeBackend, Harness

    calls: list[dict] = []

    def respond(model, messages, guided_json):
        system = messages[0]["content"]
        if system.startswith("You are Walnut's main agent"):
            calls.append({"kind": "plan", "messages": messages})
            return json.dumps(plan or {"questions": ["no plan scripted"]})
        payload = json.loads(messages[-1]["content"][len("TABLE:\n"):])
        calls.append({"kind": "narrate", "payload": payload, "messages": messages})
        return json.dumps(narrate(payload) if narrate else {"claims": []})

    backend = FakeBackend(respond, tokens_in=5, tokens_out=5)
    h = Harness(backend, config=HARNESS_CONFIG, profile="cloud", cache=None)
    return h, calls

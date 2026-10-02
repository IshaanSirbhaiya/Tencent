"""Two REAL fixture estates for DSD (datasets built through delegation), built in ``tmp_path``.

* ``workspace`` — Larkspur Analytics: `fixtures/workspace_demo/db/01_schema_and_data.sql` (the DB)
  and the workspace's own text files (meeting notes, project briefs, the roadmap) as documents;
* ``finance``   — the synthetic lender: `fixtures/packs/finance/core_banking` (the DB) and the
  credit memos under `fixtures/packs/finance/credit_memos` as documents.

For each estate:

1. the seed SQL is loaded into sqlite and each table is read through the REAL ingestion crawl
   (`mcp_ingest/tests/estates.py`), so every snapshot is what ingestion would capture;
2. a REAL facts store is written from those rows (`_facts_tables`, `_facts_meta`, manifest);
3. the REAL rules mapper proposes the mapping (`mapping.propose_rules`) and an AUTHENTICATED test
   person approves it (`mapping.approve_mapping`) — so the links a design may use are exactly the
   approved foreign keys of that mapping;
4. two adapter agents are registered: one keeping the DB, one keeping the documents;
5. the documents are captured for real (§5.2 capture store) and put in a graph: one node per
   document (its kind from the pack: the kind whose name the folder names), linked by the pack's
   own declared edge to the entity whose canonical id the text names exactly. Which entity a text
   names is COMPUTED from the estate's rows (an id, or a name's leading word, found in the text).

Nothing here is an expected output: every test derives its expectations from these rows.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

DATABRIDGE = Path(__file__).resolve().parents[3]
FIXTURES = DATABRIDGE / "fixtures"
_MI_TESTS = DATABRIDGE / "packages" / "mcp_ingest" / "tests"

EXTRACTED_AT = "2026-10-01T17:08:19+00:00"
#: a test deployment approval key (>= 32 chars); real deployments keep theirs outside the repo
APPROVAL_KEY = "dsd-tests-approval-key-0123456789abcdef"

#: estate -> its sources, the tables read, and how documents are found (all fixture facts).
ESTATES: dict[str, dict] = {
    "workspace": {
        "pack": "workspace", "db": "larkspur_db", "docs": "larkspur_files",
        "db_agent": "larkspur_db_agent", "docs_agent": "larkspur_files_agent",
        "tables": ["public.customers", "public.invoices", "public.employees",
                   "public.projects", "public.contacts"],
        "doc_globs": [("meetings/*.txt", "meeting"), ("projects/*/*.md", "document"),
                      ("planning/*/*.md", "document")],
        "doc_root": FIXTURES / "workspace_demo" / "files",
        # the documents name an organisation: a customer's leading name word, exactly
        "doc_entity": ("customers", "organisation", "mentions"),
    },
    "finance": {
        "pack": "finance", "db": "finance_core_banking", "docs": "finance_credit_memos",
        "db_agent": "core_banking_agent", "docs_agent": "credit_memos_agent",
        "tables": ["core_banking.party", "core_banking.facility", "core_banking.covenant",
                   "core_banking.account"],
        "doc_globs": [("*/*.md", "credit_memo")],
        "doc_root": FIXTURES / "packs" / "finance" / "credit_memos",
        # the memos name a facility by its exact id
        "doc_entity": ("facility", "facility", "concerns_facility"),
    },
}


@dataclass
class Estate:
    name: str
    root: Path
    cfg: dict
    vocab: Any
    store: Any
    facts: Any                      # CountingFacts
    snaps: dict
    rows: dict                      # facts table -> list[dict]  (the source rows)
    keys: dict                      # facts table -> row key column
    mapping: Any
    docs: list = field(default_factory=list)   # [{node_id, kind, text, sha, entity_ids}]
    approver: Any = None

    @property
    def db(self) -> str:
        return self.cfg["db"]

    def service(self, *, harness: Any = None):
        from databridge_federated import build_service
        svc = build_service(store=self.store, harness=harness, facts=self.facts,
                            retriever=DocRetriever(self),
                            adapter_agents_root=self.root / "adapter_agents", vocabulary=self.vocab,
                            runs_dir=self.root / "runs",
                            connections=[self.cfg["db"], self.cfg["docs"]])
        self.facts.calls.clear()
        return svc

    def materialiser(self, *, harness: Any = None):
        from databridge_materialise.platform import PlatformMaterialiser
        return PlatformMaterialiser(store_provider=lambda: self.store, harness=harness,
                                    out_root=self.root / "materialise",
                                    facts_root=self.root / "facts",
                                    passages_root=self.root / "passages",
                                    capture_root=self.root / "capture", vocabulary=self.vocab)


class CountingFacts:
    """The real `McpFactsStore`, recording every statement (the pushdown proof)."""

    def __init__(self) -> None:
        from databridge_agent_runtime import McpFactsStore
        self.inner = McpFactsStore()
        self.calls: list[tuple[str, Any]] = []

    def available(self, connection: str) -> bool:
        return self.inner.available(connection)

    def query(self, connection, sql, *, params=None, question=None):
        self.calls.append((sql, params))
        return self.inner.query(connection, sql, params=params, question=question)


class DocRetriever:
    """Passage search over the estate's captured documents (one passage per document, its whole
    text): every passage containing the query, case-insensitive, at most ``k`` — a RANKED read."""

    def __init__(self, estate: "Estate") -> None:
        self.estate = estate

    def search(self, query, *, k=8, sources=None, kinds=None, mode="hybrid"):
        from databridge_core import SourcePointer
        from databridge_core.provenance import Trust
        out = []
        for d in self.estate.docs:
            if query.lower() in d["text"].lower():
                ptr = SourcePointer(source_id=self.estate.cfg["docs"], kind="doc_span",
                                    document_id=d["sha"], char_start=0, char_end=len(d["text"]),
                                    capture_digest=d["sha"], trust=Trust.UNTRUSTED)
                out.append({"chunk_id": f"{d['sha']}#0", "doc_id": d["sha"],
                            "source_id": self.estate.cfg["docs"], "text": d["text"],
                            "pointer": ptr, "score": 1.0})
        return [o for o in out if not sources or o["source_id"] in sources][:k]


def issued_principal(name: str = "Dana Reviewer (dsd tests)"):
    """An AUTHENTICATED principal: issued by a registered (test) authenticator."""
    from databridge_agent_runtime import register_authenticator
    return register_authenticator("dsd-tests").issue(name)


def _import_estates():
    """mcp_ingest's estate loader, by FILE (a `scripts/estates.py` shares its module name)."""
    import importlib.util                              # noqa: PLC0415
    if "dsd_mi_estates" in sys.modules:
        return sys.modules["dsd_mi_estates"]
    if str(_MI_TESTS) not in sys.path:
        sys.path.append(str(_MI_TESTS))                # for its own `test_w4_dialects` import
    spec = importlib.util.spec_from_file_location("dsd_mi_estates", _MI_TESTS / "estates.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dsd_mi_estates"] = mod
    spec.loader.exec_module(mod)
    return mod


def _facts_value(v: Any) -> Any:
    if isinstance(v, (int, float, str)) or v is None:
        return v
    return str(v)


def _write_facts(path: Path, conn_id: str, snaps: dict) -> tuple[dict, dict]:
    from databridge_mcp_ingest.tabular.facts import sha256_file
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE _facts_tables (facts_table TEXT PRIMARY KEY, source_table TEXT, mode TEXT,
            rows_source INTEGER, rows_in_facts INTEGER, group_by TEXT, reason TEXT,
            extracted_at TEXT, source_sql TEXT);
        CREATE TABLE _facts_meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    rows_by: dict[str, list[dict]] = {}
    keys: dict[str, str] = {}
    for ref, snap in snaps.items():
        table = snap["table"]
        cols = [c["name"] for c in snap["columns"]]
        rows = [{c: _facts_value(r.get(c)) for c in cols} for r in snap["rows"]]
        num = {c for c in cols if rows and all(isinstance(r[c], (int, float)) or r[c] is None
                                               for r in rows)}
        db.execute(f'CREATE TABLE "{table}" (' + ", ".join(
            f'"{c}" {"NUMERIC" if c in num else "TEXT"}' for c in cols) + ")")
        db.executemany(f'INSERT INTO "{table}" VALUES ({", ".join("?" for _ in cols)})',
                       [tuple(r[c] for c in cols) for r in rows])
        db.execute("INSERT INTO _facts_tables VALUES (?,?,?,?,?,?,?,?,?)",
                   (table, ref, "rows", len(rows), len(rows), "[]", "all rows copied",
                    EXTRACTED_AT, f"SELECT * FROM {ref}"))
        rows_by[table] = rows
        pk = list(snap.get("primary_key") or [])
        keys[table] = pk[0] if len(pk) == 1 else cols[0]
    db.executemany("INSERT INTO _facts_meta VALUES (?,?)",
                   [("source_id", conn_id), ("extracted_at", EXTRACTED_AT)])
    db.commit()
    db.close()
    (path.parent / f"{conn_id}_facts.json").write_text(json.dumps(
        {"source_id": conn_id, "extracted_at": EXTRACTED_AT, "status": "ok",
         "sha256": sha256_file(path), "tables": []}), encoding="utf-8")
    return rows_by, keys


def _register_agents(root: Path, cfg: dict) -> None:
    from databridge_workflow_spec import AdapterAgentSpec, dump_spec
    d = root / "adapter_agents"
    d.mkdir(parents=True, exist_ok=True)
    for aid, conn in ((cfg["db_agent"], cfg["db"]), (cfg["docs_agent"], cfg["docs"])):
        spec = AdapterAgentSpec.model_validate({
            "id": aid, "title": aid.replace("_", " "), "created_from": f"keep {conn} right",
            "connections": [conn], "extract": {"pack": cfg["pack"]}, "status": "active"})
        (d / f"{aid}.yaml").write_text(dump_spec(spec), encoding="utf-8")


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return "document"


def _build_documents(e: Estate) -> None:
    """Capture each document, add its node, and link it to every entity its text names."""
    from databridge_core import SourcePointer
    from databridge_core.provenance import Trust
    from databridge_graph.kinds import EdgeKind, NodeKind
    from databridge_graph.schema import GraphEdge, GraphNode
    from databridge_materialise.testing import write_capture

    cfg = e.cfg
    table, kind, edge = cfg["doc_entity"]
    rows = e.rows[table]
    key = e.keys[table]
    t_sys = dt.datetime(2026, 10, 1, 17, 0, tzinfo=dt.timezone.utc)
    t_valid = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
    # how a text names an entity: its exact id, else (organisations) its name's leading word
    names: dict[str, str] = {}
    for r in rows:
        rid = str(r[key])
        names[rid] = rid if kind != "organisation" else str(r.get("name") or "").split()[0]
    entity_nodes: dict[str, str] = {}
    files = sorted({p for pattern, _k in cfg["doc_globs"] for p in cfg["doc_root"].glob(pattern)})
    for f in files:
        dkind = next(k for pattern, k in cfg["doc_globs"] if f in set(cfg["doc_root"].glob(pattern)))
        text = f.read_text(encoding="utf-8")
        sha = write_capture(e.root / "capture", text, {"source": cfg["docs"], "title": f.name})

        def span(needle: str, start: int = 0) -> SourcePointer:
            i = text.index(needle, start)
            return SourcePointer(source_id=cfg["docs"], kind="doc_span", document_id=sha,
                                 char_start=i, char_end=i + len(needle), capture_digest=sha,
                                 trust=Trust.UNTRUSTED)
        whole = SourcePointer(source_id=cfg["docs"], kind="doc_span", document_id=sha,
                              char_start=0, char_end=len(text), capture_digest=sha,
                              trust=Trust.UNTRUSTED)
        label = _first_line(text)
        attrs, attr_sources = {}, {}
        m = re.search(r"\b(20\d\d-\d\d-\d\d)\b", text)
        if m:
            attrs["date"] = m.group(1)
            attr_sources["date"] = span(m.group(1))
        node_id = f"{dkind}:{sha[:16]}"
        e.store.add_node(GraphNode(id=node_id, kind=NodeKind(dkind), label=label, attrs=attrs,
                                   attr_sources=attr_sources, sources=(whole,),
                                   valid_from=t_valid, system_from=t_sys))
        hits = sorted(rid for rid, nm in names.items()
                      if nm and re.search(rf"(?<![\w-]){re.escape(nm)}(?![\w-])", text))
        for rid in hits:
            if rid not in entity_nodes:
                nid = f"{kind}:{rid.lower()}"
                e.store.add_node(GraphNode(
                    id=nid, kind=NodeKind(kind), label=rid, canonical_id=rid,
                    sources=(SourcePointer(source_id=cfg["db"], kind="table_cell",
                                           table=table, column=key, row_id=rid,
                                           trust=Trust.UNTRUSTED),),
                    valid_from=t_valid, system_from=t_sys))
                entity_nodes[rid] = nid
            e.store.add_edge(GraphEdge(id=f"{edge}:{node_id}->{rid}", src_id=node_id,
                                       dst_id=entity_nodes[rid], kind=EdgeKind(edge),
                                       sources=(span(names[rid]),), valid_from=t_valid,
                                       system_from=t_sys))
        e.docs.append({"node_id": node_id, "kind": dkind, "text": text, "sha": sha,
                       "label": label, "entity_ids": hits})


def build_estate(name: str, root: Path, monkeypatch) -> Estate:
    """Build one estate (the caller binds the pack with `deployment`)."""
    from databridge_graph.packs import active_vocabulary
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest.mapping import SourceMapping, approve_mapping, propose_rules, \
        save_mapping

    cfg = ESTATES[name]
    for env, sub in {"WALNUT_FACTS_DIR": "facts", "WALNUT_ADAPTER_AGENTS_DIR": "adapter_agents",
                     "WALNUT_RUNS_DIR": "runs", "WALNUT_CAPTURE_DIR": "capture",
                     "WALNUT_PASSAGES_DIR": "passages", "WALNUT_SOURCES_DIR": "sources",
                     "WALNUT_MATERIALISE_DIR": "materialise"}.items():
        (root / sub).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(env, str(root / sub))
    # the deployment approval key that seals design approvals (never under the estate's dirs)
    monkeypatch.setenv("WALNUT_APPROVAL_KEY", APPROVAL_KEY)
    est = _import_estates()
    db = est.build_estate(name, root / "src.sqlite")
    client = est.engine_client(db)
    snaps = {t: est.snapshot(client, *t.split(".")) for t in cfg["tables"]}
    db.close()
    vocab = active_vocabulary()
    rows, keys = _write_facts(root / "facts" / f"{cfg['db']}_facts.sqlite", cfg["db"], snaps)
    proposed = propose_rules(snaps, vocab)
    save_mapping(SourceMapping(source_id=cfg["db"], tables=proposed))
    approver = issued_principal()
    mapping = approve_mapping(cfg["db"], principal=approver, vocab=vocab)
    _register_agents(root, cfg)
    store = GraphStore(str(root / "graph.db"))
    e = Estate(name=name, root=root, cfg=cfg, vocab=vocab, store=store, facts=CountingFacts(),
               snaps=snaps, rows=rows, keys=keys, mapping=mapping, approver=approver)
    _build_documents(e)
    return e


# ------------------------------------------------------------------------------------------------
# a scripted DESIGN model behind the real harness (the "fake model backend")
# ------------------------------------------------------------------------------------------------
HARNESS_CONFIG = {"routing": {"agentic_orchestration": "designer"},
                  "aliases": {"designer": {"model": "fake/designer"}},
                  "profiles": {"cloud": {}}}


def scripted_designer(answer):
    """A Harness whose model returns ``answer(catalogue)`` — the catalogue it was SHOWN in the
    prompt — as its design draft. Every prompt is recorded in ``calls``."""
    from databridge_harness import FakeBackend, Harness
    calls: list[dict] = []

    def respond(model, messages, guided_json):
        prompt = messages[-1]["content"]
        cat = json.loads(prompt.split("CATALOGUE\n", 1)[1].split("\nREQUEST\n", 1)[0])
        calls.append({"prompt": prompt, "catalogue": cat})
        return json.dumps(answer(cat))

    h = Harness(FakeBackend(respond, tokens_in=5, tokens_out=5), config=HARNESS_CONFIG,
                profile="cloud", cache=None)
    return h, calls


__all__ = ["ESTATES", "Estate", "CountingFacts", "build_estate", "issued_principal",
           "scripted_designer", "HARNESS_CONFIG", "EXTRACTED_AT"]

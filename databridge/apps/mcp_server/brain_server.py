"""Clinic-brain tool layer — the shared implementation behind every mouth on the graph.

**This module is not a server, and it is not read-only.** It is the one place the graph tools are
implemented; ``brain_ingest`` among them writes. Three callers use it, and which of these methods
each one can reach is the capability boundary:

  ``brain_reader_server.py``  MCP, read tools only — the endpoint an agent may reach
  ``brain_writer_server.py``  MCP, ``brain_ingest`` only — the model-free ingestion path
  ``apps/gateway``            the FastAPI ``/brain/*`` routes (its own surface, its own gates)

The two MCP servers are **separate processes with separate tool registries**, declared in
``capabilities.py``. That split is R-34 in `REMEDIATION.md` §3.2: this module previously built a
single server carrying all five tools while its own header claimed read-only, and registering that
behind the aggregator would have put a write tool behind the agent's endpoint — collapsing the
boundary `docs/hackathon/GRAPH_AGENT_AND_MCP.md` §3 requires into a naming convention. Read
``capabilities.py`` for what the split does and does not enforce; it is deliberately specific.

The methods below are NOT forked per caller. "One brain, two mouths": the MCP tools and the
``/brain/*`` routes call the SAME methods, so what a test proves here holds on both surfaces. The
boundary is which methods a process exposes — never a second copy of the logic.

The tool bodies are plain Python and need no ``mcp`` SDK, so the gateway and the tests use them
directly with no server running.

Every answer value traces to a graph node/edge SourcePointer (P4); ``nl_query`` runs the answer
through C17's ``claim_check`` so an ungrounded quantity is refused, never narrated.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import pathlib
import re
import sqlite3
import threading
from typing import NamedTuple, Optional

from databridge_graph import (
    GraphStore,
    GroundedAnswer,
    extract_graph,
    neighbors,
    nl_query,
    path,
    resolve_entity,
    row_to_node,
    same_label_node_ids,
    timeline,
)
from databridge_graph.identity import ambiguous_refusal

log = logging.getLogger(__name__)

#: The frozen clock a library-built ``BrainTools()`` (tests, the demo CLI) stamps system time
#: with, so a seeded store is reproducible. The gateway and the MCP processes pass their own store
#: on the real clock. Kept as a literal here rather than imported from a pack's seed module, so
#: this shared surface imports no domain module at all (Codex f1 #7).
DEMO_SYSTEM_TIME = dt.datetime(2026, 8, 11, tzinfo=dt.timezone.utc)


# --- the ONE shared graph store (KNOWLEDGE_SYSTEM_PRINCIPLES §6 Batch 1, contract C1) ---------
#: Env var naming the SQLite file every surface reads and every ingester writes. ``:memory:`` is
#: accepted (tests), and is then private to the process that opened it.
GRAPH_DB_ENV = "WALNUT_GRAPH_DB_PATH"
#: Opt-in switch for the bound pack's demo seed (contract C2). Off by default: the store holds the
#: user's own ingested sources, and a demo appearing in it would be data nobody put there. The
#: variable's NAME is an external contract (start scripts, `.env` files) and is kept as is.
DEMO_SEED_ENV = "WALNUT_LOAD_CLINIC_SEED"

_DATABRIDGE_ROOT = pathlib.Path(__file__).resolve().parents[2]


def graph_db_path(*, create: bool = True) -> str:
    """The shared store's path: ``$WALNUT_GRAPH_DB_PATH``, else ``<databridge>/var/graph/
    workspace.db``.

    ``create=True`` (the WRITER default — the gateway and the ingesters) creates the parent
    directory, so a first boot needs no setup step. A reader passes ``create=False`` and touches
    nothing: the read-only process must not write to the filesystem, not even a directory
    (Codex f1 #4). See :func:`open_reader_store`."""
    raw = (os.environ.get(GRAPH_DB_ENV) or "").strip()
    if raw == ":memory:":
        return raw
    path = pathlib.Path(raw) if raw else _DATABRIDGE_ROOT / "var" / "graph" / "workspace.db"
    path = path.resolve()
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def demo_seed_enabled() -> bool:
    return (os.environ.get(DEMO_SEED_ENV) or "").strip() == "1"


def demo_seed_sources() -> dict[str, str]:
    """The registered demo documents the ENABLED packs declare (``demo_seed.yaml`` ``sources``) —
    the allow-list ``brain_ingest`` may read by name. Empty when no enabled pack declares any.
    Replaces the fixture import each caller used to make itself (Codex f2 #6)."""
    from databridge_graph.pack_seed import demo_seed_sources as _declared

    return _declared()



def open_shared_store(path: Optional[str] = None) -> GraphStore:
    """Open the shared, file-backed store on the REAL clock — the WRITER's handle.

    Every process that opens the same path sees the same rows: ``GraphStore`` caches nothing, so
    rows another process (the ingest CLI, the MCP writer) commits are visible to the next read
    without a restart. ``GraphStore.refresh()`` covers the one case it cannot follow on its own —
    the file being replaced by an atomic publish.

    ``GraphStore.__init__`` creates the file, its schema and its indexes, so this is a WRITE even
    when nothing is ingested. A read-only process uses :func:`open_reader_store` instead."""
    return GraphStore(path or graph_db_path())


class ReaderStoreUnavailable(RuntimeError):
    """The reader was pointed at a store that does not exist. It refuses rather than creating one."""


class ReadOnlyGraphStore(GraphStore):
    """A ``GraphStore`` over an EXISTING SQLite file, opened with SQLite's ``mode=ro``.

    ``GraphStore.__init__`` runs the schema script, the migrations and a commit — every one a
    write — so a reader cannot use it. This opens the connection read-only and skips all three;
    every read method is inherited unchanged. A write attempted through it fails in SQLite itself
    (``attempt to write a readonly database``), which is the guarantee: the reader boundary is not
    a convention this class hopes callers honour.
    """

    def __init__(self, path: str, *, clock=None):                  # noqa: D107 - see class
        p = pathlib.Path(path)
        if not p.is_file():
            raise ReaderStoreUnavailable(
                f"no graph store at {str(p)!r}: the read-only process never creates one — start "
                f"the gateway or an ingester (a writer) first")
        self.path = str(p)
        self.conn = self._connect_ro(self.path)
        self._lock = threading.RLock()
        self._clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))

    @staticmethod
    def _connect_ro(path: str) -> sqlite3.Connection:
        uri = pathlib.Path(path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def refresh(self) -> dict[str, int]:
        """Re-open read-only. The inherited version reconnects read-WRITE."""
        with self._lock:
            old, self.conn = self.conn, self._connect_ro(self.path)
            old.close()
            return self.counts()


def open_reader_store(path: Optional[str] = None) -> GraphStore:
    """The READ-ONLY process's handle on the shared store (Codex f1 #4).

    Never creates a directory, a file, a table or an index. ``:memory:`` (tests) is a private,
    in-process database and touches no filesystem; any other path must already exist and is
    opened with SQLite ``mode=ro``, so a write through it fails in SQLite, not in a code review.
    """
    target = path or graph_db_path(create=False)
    if target == ":memory:":
        return GraphStore(":memory:")
    return ReadOnlyGraphStore(target)


class EntityReference(NamedTuple):
    """An :class:`databridge_graph.EntityMatch` plus the MCP payload shape for a refusal.

    The three outcomes — resolved / not found / **ambiguous** — and the rule that produces them
    live in ``databridge_graph.identity``, because ``nl_query`` needs the same rule and a package
    cannot import an app. This type adds only ``failure()``: how a refusal is serialised for a
    tool caller, which is an MCP concern and nothing the graph should know about.
    """

    node_id: Optional[str] = None
    error: Optional[str] = None
    candidates: tuple[str, ...] = ()

    @classmethod
    def of(cls, match) -> "EntityReference":
        return cls(node_id=match.node_id, error=match.error, candidates=match.candidates)

    def failure(self, **empty) -> dict:
        """The refusal payload, shaped like the tool's own success payload.

        Every tool returns ``{"error": ...}`` plus its empty result keys, so a caller that reads
        ``["nodes"]`` without checking ``error`` gets nothing rather than a KeyError.
        ``candidates`` is always present so "which ones?" never needs a second round trip.
        """
        return {"error": self.error, "candidates": list(self.candidates), **empty}


# --- hybrid passage retrieval beside the graph (BUILD_PLAN §5.5, P14) ---------------
#: How many passages a query carries beside its graph answer, and how much of each is shown.
PASSAGE_K = 5
PASSAGE_CHARS = 320


def default_passage_search():
    """`databridge_retrieval.search` when that package is importable, else ``None``.

    Optional by design: the graph answer never depends on it, and a deployment with no passage
    index says so (``retrieval.available: false``) rather than failing."""
    try:
        from databridge_retrieval import search
    except Exception:                                                # noqa: BLE001 - optional
        return None
    return search


def _lexically_relevant(question: str, text: str) -> bool:
    """Does the passage share at least one content word with the question?

    The vector leg of hybrid retrieval ALWAYS returns its nearest neighbours — "what is the
    meaning of life?" has nearest passages too. A passage that shares no content word with the
    question may still be a fine *supporting* passage, but it is not something the system may
    offer as the answer when the graph had nothing; this is the floor for that one decision.
    """
    from databridge_graph.query import _q_tokens

    hay = (text or "").lower()
    return any(len(t) >= 3 and t in hay for t in _q_tokens(question))


def held_identities(ingest_root=None) -> frozenset[str]:
    """Every document identity the ingestion screen BLOCKED or HOLDS FOR REVIEW, in each source's
    latest reported run: its item id (the document id its pointers carry) and its content sha256
    (their capture digest). Codex R-029 r1 #2: a passage of such an item — a stale index entry,
    say — is never quoted, whatever it matches."""
    root = pathlib.Path(ingest_root) if ingest_root is not None else default_ingest_root()
    if root is None or not root.is_dir():
        return frozenset()
    try:
        from databridge_mcp_ingest.records import GovernanceRecord, read_jsonl_typed
    except Exception:                                                # noqa: BLE001 - optional
        return frozenset()
    out: set[str] = set()
    for src in sorted(p for p in root.iterdir() if p.is_dir()):
        runs = sorted(p for p in src.iterdir() if p.is_dir() and (p / "report.json").is_file())
        if not runs:
            continue
        try:
            recs = read_jsonl_typed(runs[-1] / "governance.jsonl", GovernanceRecord)
        except Exception:                                            # noqa: BLE001
            continue
        for rec in recs:
            if rec.decision in ("block", "review"):
                out |= {str(x) for x in (rec.item_id, rec.sha256) if x}
                out |= {f"sha256:{rec.sha256}"} if rec.sha256 else set()
    return frozenset(out)


def drop_held_passages(passages: list[dict], held: frozenset[str]) -> list[dict]:
    """``passages`` without any passage of a withheld or held item (by document id, capture
    digest or the passage's own doc id)."""
    if not held:
        return passages
    out = []
    for p in passages:
        ptr = p.get("pointer") or {}
        ids = {str(x) for x in (ptr.get("document_id"), ptr.get("capture_digest"),
                                p.get("doc_id")) if x}
        if not ids & held:
            out.append(p)
    return out


def search_passages(question: str, *, search=None, k: int = PASSAGE_K,
                    sources: Optional[list[str]] = None,
                    ingest_root=None) -> tuple[list[dict], dict]:
    """``(passages, retrieval)`` — every passage with the pointer that grounds it.

    ``retrieval`` states how the list was made: whether an index was available at all, whether
    the embedding was the labelled offline fallback, which stores were searched or skipped and
    why (P7/P12: reduced coverage is reported, never hidden). An error is reported, not raised —
    the graph answer beside it stands on its own.
    """
    fn = search if search is not None else default_passage_search()
    if fn is None:
        return [], {"available": False, "reason": "databridge_retrieval is not importable here"}
    try:
        found = fn(question, k=k, sources=sources, mode="hybrid")
    except Exception as exc:                                         # noqa: BLE001
        return [], {"available": False, "reason": f"passage search failed: {type(exc).__name__}"}
    out: list[dict] = []
    for p in found or []:
        pointer = getattr(p, "pointer", None)
        if pointer is None:
            continue                                  # a passage with no pointer is not evidence
        text = str(getattr(p, "text", "") or "")
        out.append({
            "chunk_id": getattr(p, "chunk_id", None), "doc_id": getattr(p, "doc_id", None),
            "source_id": getattr(p, "source_id", None) or pointer.source_id,
            "text": text[:PASSAGE_CHARS], "truncated": len(text) > PASSAGE_CHARS,
            "locator": getattr(p, "locator", None),
            "approx_locator": bool(getattr(p, "approx_locator", False)),
            "score": getattr(p, "score", None),
            "lexical_match": _lexically_relevant(question, text),
            "pointer": pointer_to_dict(pointer),
        })
    # r1 #2: an item the screen withheld or holds for review is never quoted
    kept = drop_held_passages(out, held_identities(ingest_root))
    withheld_n, out = len(out) - len(kept), kept
    meta = {
        "available": True,
        "offline_embedding": bool(getattr(found, "offline_embedding", False)),
        "embedding_label": getattr(found, "embedding_label", None),
        "coverage": getattr(found, "coverage", None),
        "notices": list(getattr(found, "notices", None) or []),
        "withheld_by_screen": withheld_n,
    }
    return out, meta


def passage_citation(p: dict) -> dict:
    """A passage in the ``citations`` shape `/brain` renders (entity citations beside it)."""
    locator = p.get("locator") or p.get("doc_id") or p.get("chunk_id")
    if locator and p.get("approx_locator"):
        locator = f"approx. {locator}"
    return {"entity_id": p.get("chunk_id"), "label": locator or "passage", "kind": "passage",
            "source_id": p.get("source_id"), "pointers": [p["pointer"]]}


def passages_answer(question: str, passages: list[dict]) -> Optional[str]:
    """An answer made ONLY of verbatim extracts, or ``None`` when no passage clears the floor.

    P15: *if it did not come from an extract, it is not said.* Nothing here paraphrases; each
    line is a quoted passage and its locator, and the answer says plainly that the graph held no
    matching entity."""
    usable = [p for p in passages if p.get("lexical_match")]
    if not usable:
        return None
    lines = ["No entity in the knowledge graph matches this question. The closest passages in "
             "the connected sources, quoted verbatim:"]
    for i, p in enumerate(usable, start=1):
        cite = passage_citation(p)["label"]
        quote = " ".join(p["text"].split()) + ("…" if p.get("truncated") else "")
        lines.append(f"[{i}] \"{quote}\" — {p.get('source_id')}, {cite}")
    return "\n".join(lines)


# --- reading a LARGE graph by index (E2E run 5, F-05) -----------------------------------------
# On the finance estate (50k nodes, 49k edges) one /brain question took 18-140 s. Profiled on a
# copy of that graph, nearly all of it was two whole-graph reads: entity linking turned EVERY open
# node into a typed `GraphNode` (two pointer queries each) to score its text, and the 1-hop
# neighbourhood turned EVERY open edge into a `GraphEdge` to keep the few touching one node. The
# readers below compute the same things from the raw rows and the indexes, and type only what
# they return.

#: The open edges touching one node, by index (R-029 TIDY2: ONE definition, the graph package's).
from databridge_graph.query import TOUCHING_EDGES_SQL  # noqa: E402


def link_entities_indexed(store, question: str, *, top_k: int = 3) -> list:
    """`databridge_graph.link_entities` — which itself reads by index since R-029 TIDY2 (the BM25
    index built from raw rows, cached per store, and only the top ``top_k`` typed; moved there from
    here so every caller is fast, not only /brain). Kept as a name for this module's callers."""
    from databridge_graph.query import link_entities

    return link_entities(store, question, top_k=top_k)


class NeighbourhoodView:
    """A read-only view of a store in which "the open graph" is ONE record's 1-hop neighbourhood:
    ``open_edges()`` is every open edge touching it (all of them, read by index) and
    ``open_nodes()`` the record and every open node at their other ends. Every other read passes
    through to the store.

    `nl_query` answers about its primary record from `neighbors(primary)`, which reads
    ``open_edges()``/``open_nodes()``: over this view it sees exactly the same neighbourhood, by
    index instead of by materialising the whole graph (E2E run 5, F-05). Used only where the
    record is ADDRESSED (named by id), so ranking other records — which this view narrows to the
    neighbourhood — never decides what is answered."""

    #: A token no question carries and no node id can be (a node id never holds this prefix), by
    #: which `address` names the root record to `nl_query`'s id door (`_named_outright`). Node ids
    #: may contain spaces ("organisation:pellucid freightways ltd"), so appending the id itself to
    #: a question would be split into tokens, one of which can be ANOTHER node's id.
    ADDRESS_TOKEN = "__addressed-record__"

    def __init__(self, store, root_id: str) -> None:
        self._store = store
        self._root = root_id
        self._edges = store.fetchall(TOUCHING_EDGES_SQL, (root_id, root_id, root_id))
        ids = [root_id, *{x for r in self._edges for x in (r["src_id"], r["dst_id"])}]
        rows: list = []
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows += store.fetchall("SELECT * FROM nodes WHERE system_to IS NULL AND id IN ("
                                   + ", ".join("?" for _ in chunk) + ")", tuple(chunk))
        self._nodes = sorted(rows, key=lambda r: r["rowid_pk"])

    def open_edges(self) -> list:
        return list(self._edges)

    def open_nodes(self) -> list:
        return list(self._nodes)

    def open_node_row(self, entity_id: str):
        if entity_id == self.ADDRESS_TOKEN:
            return self._store.open_node_row(self._root)
        return self._store.open_node_row(entity_id)

    def address(self, question: str) -> str:
        """``question`` addressed to the root record by id (see `ADDRESS_TOKEN`)."""
        return f"{question} {self.ADDRESS_TOKEN}"

    def __getattr__(self, name):
        return getattr(self._store, name)


def nl_query_indexed(store, question: str) -> GroundedAnswer:
    """`databridge_graph.nl_query`, with its two whole-graph reads done by index (F-05).

    `nl_query` links the question to its primary record (BM25 over every open node), refuses a
    name several records share, and answers about the primary record from its 1-hop
    neighbourhood. Its answer depends on the question ONLY through that primary record. So:

    * the primary record is chosen by the same ranking, read by index (`link_entities_indexed`,
      ``top_k=3`` as `nl_query` asks) — or is the node the question names by id;
    * the same-name refusal is the same rule (`same_label_node_ids`, `ambiguous_refusal`), applied
      here before anything is answered;
    * the answer is `nl_query` itself, ADDRESSED to the primary record by its id, over the
      record's `NeighbourhoodView` — the same neighbourhood, read by index;
    * ``question`` and ``matched_entities`` are restored to what `nl_query` returns: the asked
      question, and the primary record followed by the rest of the global ranking."""
    import dataclasses

    from databridge_graph.query import _named_outright

    explicit = _named_outright(store, question)
    matches = link_entities_indexed(store, question)
    if explicit is None and not matches:
        return GroundedAnswer(question, "", grounded=False,
                              refusal="no matching entity in the graph")
    primary = explicit if explicit is not None else matches[0]
    matched_ids = [primary.id, *(m.id for m in matches if m.id != primary.id)]
    if explicit is None:
        shared = same_label_node_ids(store, primary.id)
        if len(shared) > 1:
            return GroundedAnswer(question, "", grounded=False, matched_entities=matched_ids,
                                  candidates=shared, refusal=ambiguous_refusal(question, shared))
    view = NeighbourhoodView(store, primary.id)
    a = nl_query(view, question if explicit is not None else view.address(question))
    return dataclasses.replace(a, question=question, matched_entities=(
        matched_ids if a.matched_entities else list(a.matched_entities)))


# --- a record named by its identifier (E2E run 3, P-06) ----------------------------------------
#: An identifier-shaped token: letters and digits (at least one of EACH), optionally joined by
#: ``- _ . / :``. "INV-2026-0057", "C-1002", "SOW-2026-021" — never a bare year or a plain word.
_RECORD_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:-]*[A-Za-z0-9]")


def _identifier_tokens(question: str) -> list[str]:
    out: list[str] = []
    for tok in _RECORD_TOKEN.findall(question or ""):
        if (any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok)
                and tok not in out):
            out.append(tok)
    return out


def record_id_matches(store, question: str) -> list[list]:
    """Open NON-subject nodes the question names by their record identifier, in tiers.

    E2E run 3, P-06: "what is the status of invoice INV-2026-0057?" said *no entity matches*
    though the invoice node existed. ``_named_outright`` accepts only a graph node id (a hash-like
    string nobody types), and `link_entities` skips every DOCUMENT-shaped kind — an invoice is one
    — so a business identifier reached neither. This is the deterministic door for it:

    * a token must be identifier-shaped (letters AND digits) — a plain word never resolves here;
    * the match is EXACT (case-insensitive) on the node's ``canonical_id`` or its ``label`` —
      nothing fuzzy, so it is the record the caller typed, not a guess;
    * SUBJECT kinds are never resolved this way (hard constraint 1): a person is not selected by
      any string a question carries; they stay on the existing candidate path.

    Returns ``[tier0, tier1]``: tier 0 holds the records whose label AND canonical id both equal
    the token (the record the identifier names), tier 1 those matching on one of the two (another
    record carrying the same identifier, e.g. a task about that invoice). Both empty = no match.
    """
    tier0: list = []
    tier1: list = []
    seen: set[str] = set()
    for tok in _identifier_tokens(question):
        if store.open_node_row(tok) is not None:
            continue                       # a node id: `_named_outright` already handles it
        rows = store.fetchall(
            "SELECT * FROM nodes WHERE system_to IS NULL AND (canonical_id = ? COLLATE NOCASE "
            "OR label = ? COLLATE NOCASE) ORDER BY rowid_pk", (tok, tok))
        for r in rows:
            if r["id"] in seen or is_subject_kind(r["kind"]):
                continue
            seen.add(r["id"])
            both = (str(r["label"] or "").lower() == tok.lower()
                    and str(r["canonical_id"] or "").lower() == tok.lower())
            (tier0 if both else tier1).append(row_to_node(store, r))
    return [tier0, tier1]


# --- items the ingestion screen withheld (E2E run 3, P-06) -------------------------------------
_NAME_WORD = re.compile(r"[a-z]{4,}")
_NAME_STOP = frozenset(
    "about after before from have into that their there these they this those what when where "
    "which with would file files document documents folder record records note notes".split())


def _name_words(text: str) -> set[str]:
    """Content words of a file name or a question (lower case, with singular forms)."""
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "").lower()
    words = set(_NAME_WORD.findall(spaced)) - _NAME_STOP
    return words | {w[:-1] for w in words if w.endswith("s") and len(w) > 4}


def default_ingest_root() -> Optional[pathlib.Path]:
    try:
        from databridge_mcp_ingest import paths as ingest_paths
    except Exception:                                                # noqa: BLE001 - optional
        return None
    return ingest_paths.ingest_dir()


def withheld_items(question: str, ingest_root=None, *, named: bool = False) -> list[dict]:
    """Items the ingestion screen kept OUT of the graph that the question names by file name.

    A blocked or held document is not *missing*: it was captured, screened and withheld by a rule.
    Answering "no entity matches" about it hides that a decision was made. Each source's LATEST
    run with a report is read (every run re-screens what it captured); its ``governance.jsonl``
    lines with decision ``block``/``review`` are the withheld items. One is returned when a word
    of its file name (4+ letters) is a word of the question; the best-overlapping items only.
    Nothing of the item's CONTENT is read or returned — its name, the rule and how to reverse it.

    ``named=True`` (E2E run 5, F-06) keeps only items the question NAMES: at least two words of
    the file name, and at least half of them. That is the rule used when graph entities ALSO
    match — "What does the Yolande Mining interim review 2024 say?" matched the company's records
    and the held filing was never mentioned, so the answer said the review did not exist. A
    question that only shares a company name with a held file ("…Yolande Mining's limit?") is
    not about the file. A BLOCKED item and one HELD FOR REVIEW are the same here: both are named
    with their rule, and neither is quoted.
    """
    root = pathlib.Path(ingest_root) if ingest_root is not None else default_ingest_root()
    if root is None or not root.is_dir():
        return []
    try:
        from databridge_mcp_ingest.records import GovernanceRecord, read_jsonl_typed
    except Exception:                                                # noqa: BLE001 - optional
        return []
    asked = _name_words(question)
    if not asked:
        return []
    scored: list[tuple[int, dict]] = []
    for src in sorted(p for p in root.iterdir() if p.is_dir()):
        runs = sorted(p for p in src.iterdir() if p.is_dir() and (p / "report.json").is_file())
        if not runs:
            continue
        run = runs[-1]
        try:
            recs = read_jsonl_typed(run / "governance.jsonl", GovernanceRecord)
        except Exception:                                            # noqa: BLE001
            continue
        for rec in recs:
            if rec.decision not in ("block", "review"):
                continue
            name = pathlib.PureWindowsPath(rec.uri).name if "\\" in rec.uri \
                else pathlib.PurePosixPath(rec.uri).name
            own = _name_words(pathlib.PurePath(name).stem.replace("_", " ").replace("-", " "))
            overlap = asked & own
            if not overlap:
                continue
            # a singular form counts with its plural, so a word is counted once
            spoken = {w for w in overlap if not (w + "s") in overlap}
            whole = {w for w in own if not (w + "s") in own}
            if named and (len(spoken) < 2 or 2 * len(spoken) < len(whole)):
                continue
            fired = [str(f.get("rule")) if isinstance(f, dict) else str(f) for f in rec.fired]
            scored.append((len(overlap), {
                "source_id": src.name, "ingest_run_id": run.name, "item_id": rec.item_id,
                "name": name, "decision": rec.decision,
                "status": "withheld" if rec.decision == "block" else "held_for_review",
                "rules": [f for f in fired if f], "reason": rec.rationale,
                "reverse": rec.reverse, "screened_at": rec.at,
                "evidence": f"{src.name}/{run.name}/governance.jsonl item {rec.item_id}"}))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    return [item for s, item in scored if s == best]


def withheld_notice(items: list[dict]) -> str:
    """The sentence(s) that NAME a withheld item as withheld — never as missing."""
    lines = []
    for it in items:
        verb = ("was withheld by the ingestion screen" if it["status"] == "withheld"
                else "is held for review by the ingestion screen")
        rules = ", ".join(it.get("rules") or []) or "a screen rule"
        lines.append(f"'{it['name']}' in {it['source_id']} {verb} (rule: {rules}); its contents "
                     f"are not in the knowledge graph and are not quoted here. To include it: "
                     f"{it.get('reverse') or 'the data owner approves it'}")
    return "\n".join(lines)


# --- a question too vague to look anything up (E2E run 3, P-13) --------------------------------
#: The FIRST-PERSON pronouns — a closed grammatical class, singular and plural, with their
#: contractions — not a list of example questions. A question in the first person is about the
#: asker's own affairs (E2E run 4, Q-04: "Anything I should worry about?" carried only "I", and
#: the plural-only class sent it to a 422).
_FIRST_PERSON = (r"(?:i|me|my|mine|myself|we|us|our|ours|ourselves|let['’]s)"
                 r"|(?:(?:i|we)['’](?:m|ve|d|ll|re))")
_SELF_REFERENCE = re.compile(rf"(?<![\w'’])(?:{_FIRST_PERSON})(?![\w'’])", re.I)
_PRONOUN_ONLY = re.compile(_FIRST_PERSON, re.I)
_NAMED_TERM = re.compile(r"\b[A-Z][A-Za-z0-9'’&-]+")
_QUOTED = re.compile(r"[\"“”][^\"“”]{2,}[\"“”]")


def is_vague_question(question: str) -> bool:
    """A question about the asker's OWN affairs that names no specific thing.

    "Own affairs" means it is in the first person (`_FIRST_PERSON`, singular or plural).

    "How are things going with our clients?" names no record, no identifier, no quoted phrase,
    and no proper name — and it is about the asker's business ("our"). That is a question to ask
    BACK, not a 422. A question naming something the graph lacks ("…Brackenfold Logistics?") or
    outside the business ("the capital of Australia", "an unladen swallow") is not vague: it is
    a no-match, and stays one."""
    q = (question or "").strip()
    if not q or not _SELF_REFERENCE.search(q):
        return False
    if _identifier_tokens(q) or _QUOTED.search(q):
        return False
    # A capitalised word mid-sentence is a proper name, unless it is a first-person pronoun
    # ("I'm", "I've"): the asker is never the thing the question names.
    return not any(m.start() > 0 and not re.search(r"[.!?:]\s*$", q[:m.start()])
                   and not _PRONOUN_ONLY.fullmatch(m.group(0))
                   for m in _NAMED_TERM.finditer(q))


def clarification_for(store, question: str) -> dict:
    """``{questions, question_items}`` — the `/federated/ask` needs-input shape — asking which
    record the question is about. The options are the KINDS of record the graph holds (with
    counts), read from the store; never a name, so no person is put forward as a choice."""
    # Codex R-029 r1 #3: the options are the KINDS the store holds — a schema fact, not a value —
    # and NO count: a number with no SourcePointer is not emitted (hard constraint 2). Read with
    # one indexed statement (E2E run 5, F-05).
    kinds = sorted({str(r["kind"]) for r in store.fetchall(
        "SELECT DISTINCT kind FROM nodes WHERE system_to IS NULL")
        if not is_subject_kind(r["kind"])})
    options = kinds
    text = ("Your question does not name a record I can look up. Which one do you mean? Name "
            "it (by its name or its id) and the fact you want — a status, an amount, a date, a "
            "contact.")
    import hashlib

    qid = "brain:" + hashlib.sha1((question or "").encode("utf-8")).hexdigest()[:16]
    return {"questions": [text], "question_items": [{"id": qid, "text": text,
                                                     "options": options}]}


# --- serialisation (graph → JSON the React Flow UI / agents consume) --------------

def pointer_to_dict(p) -> dict:
    return p.model_dump(mode="json", exclude_none=True)


#: Attached to EVERY serialised SUBJECT node, in every tool. `node_detail` is not the only place
#: a record reaches a model: `brain_neighbors`, `brain_path` and `graph()` all return subject
#: nodes with a name (and, for a clinic pack, a date of birth) on them, and a subject reached
#: through someone else's 1-hop neighbourhood never passes through `node_detail` at all. Keyed on
#: the kind being a DECLARED SUBJECT of the bound packs (spine D4b), never on a kind name: this
#: used to read `node.kind is NodeKind.PATIENT`, which raised AttributeError under any binding
#: that does not declare `patient` — `/brain/graph` was a 500 under DATABRIDGE_PACKS=workspace
#: (E2E-2026-10-01 F-05). The safeguard is CLAUDE.md hard constraint 1 and is generalised, not
#: dropped: every pack's subject gets it.
#:
#: The last clause follows the pack's declared attestation mode (a closed engine vocabulary,
#: `packs.ATTESTATION_MODES`), so a subject confirmed over a verified channel is not told "in
#: person".
_ATTESTATION_CLAUSE = {
    "in_person": "staff check it in person",
    "verified_channel": "a named human confirms it against the system of record",
}


def attestation_clause(kind) -> str:
    """Who confirms a subject of this kind, and how — read off the pack's declared attestation
    mode. The ONE place identity wording is chosen (Codex f1 #6): the node caveat, the node
    detail's identity block, the insight feed and the model's context all call this."""
    from databridge_graph.packs import active_vocabulary

    mode = active_vocabulary().attestation_for(str(kind)) or ""
    return _ATTESTATION_CLAUSE.get(mode, "a named human confirms it")


def _kind_noun(kind) -> str:
    return str(getattr(kind, "value", kind)).replace("_", " ")


def subject_node_caveat(kind) -> str:
    """The caveat for a node of a declared subject kind. Never ``""``: an attestation mode this
    table does not know still gets the refusal, with the generic clause.

    It also says that reading the record by its id is not a selection (Codex f2 #1): an id in a
    request proves only that someone typed it — possibly an agent that just received it as a
    candidate — never that a human chose this record for a person."""
    return ("a registry row, not an identification — this system never verifies identity, and "
            "reading a record by its id does not select it for anyone; "
            f"{attestation_clause(kind)}")


def identity_caveat_for(kind, *, claimed: bool) -> str:
    """The identity block's caveat, worded for THIS subject kind and its attestation mode.

    Replaces `databridge_graph.identity`'s two fixed strings on this surface: they name one pack's
    subject and say "in person" whatever the pack declared, so a subject confirmed over a verified
    channel was told the wrong thing (Codex f1 #6)."""
    noun, clause = _kind_noun(kind), attestation_clause(kind)
    if claimed:
        return (f"A document naming this {noun} is a claim on the document, not a verified "
                f"identity; {clause}.")
    return (f"No document on file names this {noun}; this is a registry row, not a verified "
            f"identity. Names are not unique, and {clause}.")


def is_subject_kind(kind) -> bool:
    from databridge_graph.packs import active_vocabulary

    return active_vocabulary().is_subject(str(getattr(kind, "value", kind)))


def subject_annotation(kind) -> dict:
    """``{"identity_caveat", "identity_verified": False}`` for a declared subject kind, else ``{}``.

    The one serialiser every node REFERENCE goes through — a node dict, a path step, a timeline
    root, a citation, a connection, a claim — so a subject cannot leave this module on a path that
    forgot its caveat (Codex f1 #2)."""
    if not is_subject_kind(kind):
        return {}
    return {"identity_caveat": subject_node_caveat(kind), "identity_verified": False,
            # The intake contract, mirrored (`ResolutionResult.requires_human_selection` is
            # `Literal[True]`): this surface records no selection, so every subject it serves —
            # by name, by id, or reached through a neighbour — is a CANDIDATE (Codex f2 #1).
            "requires_human_selection": True, "selected_by_human": False}


def subject_selection_refusal(ref: str, candidates) -> str:
    """The refusal for a NAME that lands on a declared subject — even exactly one.

    A name matching one record today is still the system choosing a person from a name: the
    registry may hold a second one tomorrow, or the person at the counter may not be the one on
    file (Codex f1 #1). The ids that come back are candidate RECORDS: asking again with one reads
    that record, labelled as a candidate — it does not select it (Codex f2 #1). Selection happens
    in the intake flow, by a human, after attestation."""
    if len(candidates) > 1:
        return ambiguous_refusal(ref, list(candidates))
    return (f"{ref!r} names a subject record, and this system will not choose a person from a "
            f"name: that is an identity determination (CLAUDE.md constraint 1). The id in "
            f"`candidates` reads that record as a candidate only; a human selects which record "
            f"is the person, after attestation.")


def _withheld(values: dict, sources: dict) -> tuple[dict, list[str]]:
    """``attrs`` with every value lacking its OWN pointer replaced by ``None``, plus those names.

    Hard constraint 2: a value with no pointer is not emitted — it is ``None`` and its path is
    unresolved (Codex f1 #3). A node's entity pointers ground the node, never its values."""
    shown, withheld = {}, []
    for k, v in values.items():
        if v is not None and k not in sources:
            shown[k] = None
            withheld.append(k)
        else:
            shown[k] = v
    return shown, withheld


def node_to_dict(node) -> dict:
    attrs, withheld = _withheld(node.attrs, node.attr_sources)
    return {
        **subject_annotation(node.kind),
        "id": node.id, "kind": node.kind.value, "label": node.label,
        "canonical_id": node.canonical_id, "attrs": attrs,
        # A COUNT of entity citations beside a dict of N values is the pooled-citation shape in
        # its purest form: it reads as "3 citations back these 4 values" and cannot be checked,
        # because a count names nothing. This payload is consumed by a model, so it gets the same
        # treatment the node sheet and /evidence got — a pointer per value, and the unanswered
        # fields named rather than omitted.
        "attribute_sources": {k: pointer_to_dict(v) for k, v in node.attr_sources.items()},
        "unresolved": sorted(set(node.unresolved) | set(withheld)),
        "withheld_unpointed": sorted(withheld),
        "valid_from": node.valid_from.isoformat() if node.valid_from else None,
        "valid_to": node.valid_to.isoformat() if node.valid_to else None,
        "system_from": node.system_from.isoformat() if node.system_from else None,
        "system_to": node.system_to.isoformat() if node.system_to else None,
        # The pointers themselves, not only their count: the id and label are values too.
        "sources": [pointer_to_dict(p) for p in node.sources],
        "n_sources": len(node.sources),
        "sources_ground": "the identity of this node — not the values in `attrs`",
    }


def edge_to_dict(edge) -> dict:
    attrs, withheld = _withheld(edge.attrs, edge.attr_sources)
    return {
        "id": edge.id, "source": edge.src_id, "target": edge.dst_id,
        "kind": edge.kind.value, "weight": edge.weight, "attrs": attrs,
        "attribute_sources": {k: pointer_to_dict(v) for k, v in edge.attr_sources.items()},
        "unresolved": sorted(set(edge.unresolved) | set(withheld)),
        "withheld_unpointed": sorted(withheld),
        "valid_from": edge.valid_from.isoformat() if edge.valid_from else None,
        "valid_to": edge.valid_to.isoformat() if edge.valid_to else None,
        "sources": [pointer_to_dict(p) for p in edge.sources],
        "n_sources": len(edge.sources),
    }


def _subject_caveats(store, node_ids) -> dict:
    """``{node_id: caveat}`` for every id in ``node_ids`` whose open node is a declared subject."""
    out: dict = {}
    for nid in node_ids:
        if not nid or nid in out:
            continue
        row = store.open_node_row(nid)
        if row is not None and is_subject_kind(row["kind"]):
            out[nid] = subject_node_caveat(row["kind"])
    return out


def answer_to_dict(a: GroundedAnswer, store=None) -> dict:
    out = {
        "question": a.question, "answer": a.answer, "grounded": a.grounded,
        "refusal": a.refusal, "matched_entities": a.matched_entities,
        # An AMBIGUOUS question is refused, and the ids it could have meant travel with the
        # refusal — the same contract `EntityReference.failure` gives the other tools, so an
        # agent that hits "three people are called that" has a next call to make rather than a
        # dead end. Empty on every other outcome.
        "candidates": list(a.candidates),
        "citations": [{"entity_id": c.entity_id, "label": c.label, "kind": c.kind,
                       **subject_annotation(c.kind),
                       "pointers": [pointer_to_dict(p) for p in c.pointers]} for c in a.citations],
        # Every value the answer PRINTS, each with the pointer for that value alone (R-30), and
        # the paths it could not ground. `nl_query` computed both; this serialiser dropped them.
        "values": [{"entity_id": v.entity_id, "attr": v.attr, "value": v.value,
                    "pointer": pointer_to_dict(v.pointer)} for v in getattr(a, "values", [])],
        "unresolved": list(getattr(a, "unresolved", [])),
    }
    if store is not None:
        out["subject_caveats"] = _subject_caveats(
            store, [*a.matched_entities, *a.candidates, *(c.entity_id for c in a.citations)])
    return out


def _token_in(needle: str, hay: str) -> bool:
    needle = (needle or "").strip().lower()
    if not needle:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", hay.lower()) is not None


def passages_bound_to(store, node_id: str, passages: list[dict]) -> tuple[list[dict], int]:
    """``(kept, withheld_count)`` — only passages DETERMINISTICALLY bound to the selected subject.

    A human selected ``node_id`` by id; hybrid retrieval ranks passages by the question's words,
    and the words of a shared name match every same-named person's documents alike. Mixing those
    in would narrate another person's record as this one's (Codex r2 #1). A passage is bound when:

    * its text names the node's id or its canonical id as a whole token, or
    * it comes from a source that grounds a claim (an ABOUT-shaped edge from a document-shaped
      node) on THIS subject, and on no other subject carrying the same label.

    Everything else is withheld and counted, never silently dropped.
    """
    from databridge_graph import row_to_edge
    from databridge_graph.kinds import edge_kinds_binding_to

    row = store.open_node_row(node_id)
    if row is None:
        return [], len(passages)
    claim_kinds = {str(k) for k in edge_kinds_binding_to(_about_meta())}
    siblings = set(same_label_node_ids(store, node_id)) - {node_id}
    mine: set[str] = set()
    theirs: set[str] = set()
    for r in store.open_edges():
        if r["kind"] not in claim_kinds or r["dst_id"] not in siblings | {node_id}:
            continue
        e = row_to_edge(store, r)
        docs = {_doc_key(p.source_id, p.document_id) for p in e.sources}
        docs |= {_doc_key(p.source_id, p.document_id) for p in store.sources_for(e.src_id)}
        (mine if e.dst_id == node_id else theirs).update(docs)
    bound_docs = mine - theirs
    names = [node_id, row["canonical_id"] or ""]
    kept = []
    for p in passages:
        ptr = p.get("pointer") or {}
        doc = _doc_key(ptr.get("source_id") or p.get("source_id"),
                       ptr.get("document_id") or p.get("doc_id"))
        if any(_token_in(n, str(p.get("text") or "")) for n in names) or doc in bound_docs:
            kept.append(p)
    return kept, len(passages) - len(kept)


def _doc_key(source_id, document_id) -> tuple:
    """A DOCUMENT, not a whole connected source: one connection can hold thousands of documents,
    and binding on the source alone would bind all of them."""
    return (str(source_id or ""), str(document_id or ""))


def _about_meta():
    from databridge_graph.meta import EdgeMetaKind

    return EdgeMetaKind.ABOUT


class BrainTools:
    """Holds the live graph store; every MCP tool and gateway route delegates here."""

    def __init__(self, store: Optional[GraphStore] = None, *, harness=None,
                 sources: Optional[dict] = None, extraction_route: Optional[dict] = None):
        self.store = store or GraphStore(clock=lambda: DEMO_SYSTEM_TIME)
        self.harness = harness                       # used only by brain_ingest
        #: Which model extraction ACTUALLY runs on, and why — returned with every ingestion, so a
        #: fallback (or the gold backend) is visible in the result, not only in a log (Codex f2
        #: #7). ``None`` means the caller did not say; it is reported as such, never guessed.
        self.extraction_route = extraction_route
        # allow-list of ingestable sources: name → document text. brain_ingest NEVER opens an
        # arbitrary filesystem path (that would be an arbitrary-file-read primitive for any MCP
        # agent) — it ingests inline text or a registered source only.
        self.sources = dict(sources or {})
        #: Where the ingestion runs live, for naming a WITHHELD item (P-06). ``None`` = the
        #: deployment's ``WALNUT_INGEST_DIR``; tests point it at their own directory.
        self.ingest_root = None

    # -- demo seed ----------------------------------------------------------------
    def load_seed(self) -> dict:
        """Load the ENABLED packs' declared demo seeds into THIS store. Library callers (tests,
        the demo CLI) call it directly; the gateway and the MCP writer call it only when
        ``WALNUT_LOAD_CLINIC_SEED=1`` (:func:`demo_seed_enabled`). The read-only process never
        calls it.

        **What is seeded is the packs' declaration, not this module's** (Codex f2 #6). Each pack
        may ship ``demo_seed.yaml`` naming its loader (``databridge_graph.pack_seed``); this
        surface names no pack's seed and imports no pack's module. A pack that is not enabled
        contributes nothing, so a seed can no longer run under a vocabulary that does not declare
        its kinds — there is no wrong-binding branch left to catch. With no enabled pack declaring
        one, the result is ``{"loaded": False, "refusal": ...}`` and nothing is written."""
        from databridge_graph.pack_seed import load_demo_seeds

        out = load_demo_seeds(self.store)
        if not out.get("loaded"):
            log.warning("demo seed not loaded: %s", out.get("refusal"))
        return out

    #: Default ceiling for `graph()`. The payload is rendered in a browser canvas; a store with
    #: tens of thousands of nodes must answer with a bounded slice and SAY it is a slice, never
    #: materialise every row (`open_nodes()` of an estate-sized store is the failure this avoids).
    GRAPH_NODE_LIMIT = 1500

    def graph(self, limit: Optional[int] = None) -> dict:
        """A bounded view of the open graph: at most ``limit`` nodes (default
        ``GRAPH_NODE_LIMIT``) and only the edges whose BOTH endpoints are in that slice.

        Nodes are chosen by degree (most-connected first, then insertion order) with one SQL
        query, so the slice shows structure rather than whichever rows happened to load first.
        ``stats`` keeps the WHOLE store's counts; ``stats.shown_nodes``/``truncated`` say how
        much of it this payload is. Provenance % and ``provenance_by_source`` describe the nodes
        SHOWN — a figure is only reported over the members it was computed from.
        """
        from databridge_graph import row_to_edge

        cap = self.GRAPH_NODE_LIMIT if limit is None else max(0, int(limit))
        counts = self.store.counts()
        rows = self.store.fetchall(
            "SELECT n.* FROM nodes n "
            "LEFT JOIN (SELECT id, SUM(c) deg FROM ("
            "   SELECT src_id id, COUNT(*) c FROM edges WHERE system_to IS NULL GROUP BY src_id "
            "   UNION ALL "
            "   SELECT dst_id id, COUNT(*) c FROM edges WHERE system_to IS NULL GROUP BY dst_id"
            ") GROUP BY id) d ON d.id = n.id "
            "WHERE n.system_to IS NULL "
            "ORDER BY COALESCE(d.deg, 0) DESC, n.rowid_pk LIMIT ?", (cap,))
        # restore insertion order inside the slice so the payload is stable and reads naturally
        rows = sorted(rows, key=lambda r: r["rowid_pk"])
        nodes = [row_to_node(self.store, r) for r in rows]
        shown = {n.id for n in nodes}
        if len(shown) == counts["nodes"]:
            edge_rows = self.store.open_edges()
        elif shown:
            import json

            ids = json.dumps(sorted(shown))
            edge_rows = self.store.fetchall(
                "SELECT * FROM edges WHERE system_to IS NULL "
                "AND src_id IN (SELECT value FROM json_each(?)) "
                "AND dst_id IN (SELECT value FROM json_each(?)) ORDER BY rowid_pk", (ids, ids))
        else:
            edge_rows = []
        edges = [row_to_edge(self.store, r) for r in edge_rows]
        node_dicts = [node_to_dict(n) for n in nodes]
        # provenance % is BACKEND-computed (not a hardcoded "100%" in the UI) — the headline
        # differentiator must be measured, so it stays honest if a future node ever lacked a source.
        grounded = sum(1 for n in node_dicts if n["n_sources"] > 0)
        pct = round(100 * grounded / len(node_dicts)) if node_dicts else 100
        return {"nodes": node_dicts,
                "edges": [edge_to_dict(e) for e in edges],
                "stats": {**counts, "provenance_pct": pct, "shown_nodes": len(node_dicts),
                          "shown_edges": len(edges),
                          "truncated": len(node_dicts) < counts["nodes"], "node_limit": cap},
                "provenance_by_source": self.provenance_by_source(
                    [n.id for n in nodes] if len(node_dicts) < counts["nodes"] else None)}

    def provenance_by_source(self, node_ids: Optional[list[str]] = None) -> dict:
        """How many entities each source document grounds (the demo's provenance mini-sankey).
        Backend-computed (D-FE-2) over the SourcePointers — never derived in the browser. A node
        cited by two docs counts for both (corroboration), so the total ≥ node count. ``node_ids``
        restricts it to a slice (what `graph()` showed); ``None`` means every open node."""
        ids = node_ids if node_ids is not None else [r["id"] for r in self.store.open_nodes()]
        by_source: dict[str, int] = {}
        for nid in ids:
            seen = {p.source_id for p in self.store.sources_for(nid)}
            for sid in seen:
                by_source[sid] = by_source.get(sid, 0) + 1
        return dict(sorted(by_source.items()))

    def insights(self) -> dict:
        """Auto-generated GROUNDED insights from the graph structure (Recent Insights feed, §Part1
        Col 2). Each insight is computed server-side and carries the SourcePointers that back it +
        a ``focus`` node id to zoom to. Deterministic ordering. Counts are structural (not
        fact-shaped fabrication); any dollar figure rides inside a CITED node label.

        **Domain-free.** Every rule below is phrased over META-kinds (`meta.py`) and the enabled
        packs' declarations — DOCUMENT-shaped nodes, ABOUT-shaped claim edges into a declared
        SUBJECT kind, ISSUED, AUTHORISES → RULE → AGREEMENT, PART_OF, REFERENCES — never over one
        pack's kind names. A pack that declares none of those shapes simply gets no insight of that
        kind; an empty store gets ``{"insights": []}``. Wording uses the kind names the pack
        declared, so the text reads in the pack's own vocabulary.
        """
        from databridge_graph import row_to_edge
        from databridge_graph.kinds import edge_kinds_binding_to, node_kinds_binding_to
        from databridge_graph.meta import EdgeMetaKind, NodeMetaKind
        from databridge_graph.packs import active_vocabulary

        vocab = active_vocabulary()
        nodes = {r["id"]: row_to_node(self.store, r) for r in self.store.open_nodes()}
        edges = [row_to_edge(self.store, r) for r in self.store.open_edges()]
        if not nodes:
            return {"insights": []}

        def cites(*ids: str) -> list[dict]:
            out: list[dict] = []
            for nid in ids:
                n = nodes.get(nid)
                if n:
                    out.extend(pointer_to_dict(p) for p in n.sources)
            return out

        def of_meta(meta: EdgeMetaKind) -> list:
            kinds = {str(k) for k in edge_kinds_binding_to(meta)}
            return [e for e in edges if str(e.kind) in kinds
                    and e.src_id in nodes and e.dst_id in nodes]

        doc_kinds = {str(k) for k in node_kinds_binding_to(NodeMetaKind.DOCUMENT)}
        rule_kinds = {str(k) for k in node_kinds_binding_to(NodeMetaKind.RULE)}
        agreement_kinds = {str(k) for k in node_kinds_binding_to(NodeMetaKind.AGREEMENT)}
        subject_kinds = {str(k) for k in vocab.subject_kinds}

        def is_doc(nid: str) -> bool:
            return str(nodes[nid].kind) in doc_kinds

        items: list[dict] = []

        # Documents grouped by the SUBJECT claims they carry — ABOUT-shaped edges from a
        # document into a declared subject kind. Every claim is unconfirmed by construction (a
        # builder writes claims, never determinations), so these counts are structural facts about
        # the queue, not assertions about anyone's identity (CLAUDE.md constraint 1).
        claims_by_doc: dict[str, list[str]] = {}
        for e in of_meta(EdgeMetaKind.ABOUT):
            if is_doc(e.src_id) and str(nodes[e.dst_id].kind) in subject_kinds:
                claims_by_doc.setdefault(e.src_id, []).append(e.dst_id)

        # Which document kinds the ONTOLOGY says can carry such a claim. Derived from the
        # declarations, not from the edges present: a document whose resolution found no candidate
        # carries no claim edge, and keying the queue on edges would drop exactly the documents
        # that need the most human work.
        claimable_doc_kinds = {
            d for k in edge_kinds_binding_to(EdgeMetaKind.ABOUT)
            for d in doc_kinds
            if k.decl.allows("src", d) and any(k.decl.allows("dst", s) for s in subject_kinds)}

        # 1) the queue: EVERY claim-bearing document waiting on a human identity check. How that
        #    check is done is the pack's declared attestation for the subject kinds those
        #    documents can claim — never a fixed "in person" (Codex f1 #6).
        all_documents = sorted(nid for nid, n in nodes.items() if str(n.kind) in claimable_doc_kinds)
        unmatched = [d for d in all_documents if d not in claims_by_doc]
        if all_documents:
            claimable_subjects = sorted(
                s for s in subject_kinds
                for k in edge_kinds_binding_to(EdgeMetaKind.ABOUT)
                if any(k.decl.allows("src", str(nodes[d].kind)) for d in all_documents)
                and k.decl.allows("dst", s))
            clauses = sorted({attestation_clause(s) for s in claimable_subjects}) or                 ["a named human confirms it"]
            items.append({
                "text": f"{len(all_documents)} document(s) awaiting human identity verification "
                        f"({'; '.join(clauses)})"
                        + (f" ({len(unmatched)} with no candidate on record — a new record is "
                           f"needed)" if unmatched else ""),
                "focus": all_documents[0], "kind": str(nodes[all_documents[0]].kind),
                "citations": cites(*all_documents)})

        # 2) ambiguous identities — the ones a human must disambiguate in person
        for doc_id in sorted(d for d, subs in claims_by_doc.items() if len(subs) > 1):
            subs = claims_by_doc[doc_id]
            kinds = {str(nodes[s].kind) for s in subs}
            clause = "; ".join(sorted({attestation_clause(k) for k in kinds}))
            noun = f"{_kind_noun(next(iter(kinds)))}s" if len(kinds) == 1 else "records"
            items.append({
                "text": f"{nodes[doc_id].label} matches {len(subs)} registry {noun} — "
                        f"a human must select the correct one ({clause})",
                "focus": doc_id, "kind": str(nodes[doc_id].kind),
                "citations": cites(doc_id, *subs)})

        # 3) documents the extractor could not fully read. The COUNT is a value the text prints,
        #    so it rides with ITS OWN pointer (Codex f2 #5), not the document's entity pointers;
        #    a document whose count carries no pointer of its own is not ranked on it at all.
        def _count_ptr(nid: str):
            return nodes[nid].source_for("unresolved_count")

        incomplete = sorted(
            (nid for nid, n in nodes.items()
             if str(n.kind) in doc_kinds and int(n.attrs.get("unresolved_count") or 0) > 0
             and _count_ptr(nid) is not None),
            key=lambda nid: (-int(nodes[nid].attrs.get("unresolved_count") or 0), nid))
        if incomplete:
            top = incomplete[0]
            count_ptr = pointer_to_dict(_count_ptr(top))
            items.append({
                "text": f"{nodes[top].label} has "
                        f"{nodes[top].attrs['unresolved_count']} fields not stated on the document",
                "focus": top, "kind": str(nodes[top].kind),
                "values": {"unresolved_count": {"value": nodes[top].attrs["unresolved_count"],
                                                "pointer": count_ptr}},
                "citations": [*cites(top), count_ptr]})

        # 4) busiest issuer — which actor produced the most documents in the graph
        issued: dict[str, list[str]] = {}
        for e in of_meta(EdgeMetaKind.ISSUED):
            if is_doc(e.dst_id):
                issued.setdefault(e.src_id, []).append(e.dst_id)
        if issued:
            top = sorted(issued, key=lambda k: (-len(issued[k]), k))[0]
            docs = issued[top]
            items.append({
                "text": f"{nodes[top].label} issued {len(docs)} of the documents in the graph",
                "focus": top, "kind": str(nodes[top].kind), "citations": cites(top, *docs)})

        # 5) items referenced ON TOP OF the agreement a document authorises. Chained per
        #    document: document -AUTHORISES-> rule -AUTHORISES-> agreement -PART_OF-> members.
        #    "not included" means not in the ONE agreement THIS document's rule selects — a global
        #    member set (any agreement's) wrongly cleared an item that happens to sit in some
        #    unrelated agreement, silently under-reporting add-ons. A rule that selects no
        #    agreement (the document's own list IS the scope) makes nothing an add-on.
        authorises = of_meta(EdgeMetaKind.AUTHORISES)
        agreements_by_rule: dict[str, set[str]] = {}
        for e in authorises:
            if str(nodes[e.src_id].kind) in rule_kinds and \
                    str(nodes[e.dst_id].kind) in agreement_kinds:
                agreements_by_rule.setdefault(e.src_id, set()).add(e.dst_id)
        rule_by_doc: dict[str, str] = {
            e.src_id: e.dst_id for e in authorises
            if is_doc(e.src_id) and e.dst_id in agreements_by_rule}
        members_by_agreement: dict[str, set[str]] = {}
        for e in of_meta(EdgeMetaKind.PART_OF):
            if str(nodes[e.src_id].kind) in agreement_kinds:
                members_by_agreement.setdefault(e.src_id, set()).add(e.dst_id)
        member_kinds = {str(nodes[m].kind) for ms in members_by_agreement.values() for m in ms}

        def _members_for(doc_id: str) -> set[str]:
            covered: set[str] = set()
            for agreement in agreements_by_rule.get(rule_by_doc.get(doc_id, ""), ()):
                covered |= members_by_agreement.get(agreement, set())
            return covered

        add_on_edges = [
            e for e in of_meta(EdgeMetaKind.REFERENCES)
            if e.src_id in rule_by_doc
            and str(nodes[e.dst_id].kind) in member_kinds
            and e.dst_id not in _members_for(e.src_id)]
        add_ons = sorted({e.dst_id for e in add_on_edges})
        if add_ons:
            named = sorted({str(nodes[a].kind) for e in add_on_edges
                            for a in agreements_by_rule[rule_by_doc[e.src_id]]})
            items.append({
                "text": f"{len(add_ons)} item(s) requested on top of a named "
                        f"{'/'.join(named)} and not included in it — may be charged "
                        f"separately",
                "focus": add_ons[0], "kind": str(nodes[add_ons[0]].kind),
                "citations": cites(*add_ons)})

        return {"insights": items[:5]}

    def node_detail(self, entity_id: str) -> dict:
        """Full detail for the node-detail drawer: the node, its actual SourcePointers (the
        provenance receipts), its connections, and — for a document — the patient claims it
        carries. All grounded, computed server-side.

        ``claims`` is deliberately not called "patients": each entry is a candidate a document
        names, carrying the score and the candidate count, and every one requires an in-person
        check (CLAUDE.md §Hard constraints 1)."""
        from databridge_graph import row_to_edge
        from databridge_graph.kinds import edge_kinds_binding_to, node_kinds_binding_to
        from databridge_graph.meta import EdgeMetaKind, NodeMetaKind
        from databridge_graph.packs import active_vocabulary

        # Phrased over META-kinds and the bound packs' subject declarations, never one pack's kind
        # names (E2E F-05): a claim is an ABOUT-shaped edge, a claimant a DOCUMENT-shaped node,
        # and the identity block is keyed on the kind being a declared SUBJECT.
        vocab = active_vocabulary()
        claim_kinds = {str(k) for k in edge_kinds_binding_to(EdgeMetaKind.ABOUT)}
        doc_kinds = {str(k) for k in node_kinds_binding_to(NodeMetaKind.DOCUMENT)}
        ref = self._resolve(entity_id)
        if ref.node_id is None:
            return ref.failure()
        nid = ref.node_id
        row = self.store.open_node_row(nid)
        if row is None:
            return {"error": f"no open node {nid!r}"}
        node = node_to_dict(row_to_node(self.store, row))
        sources = [pointer_to_dict(p) for p in self.store.sources_for(nid)]
        by_id = {r["id"]: row_to_node(self.store, r) for r in self.store.open_nodes()}
        edges = [row_to_edge(self.store, r) for r in self.store.open_edges()]
        connections, claims = [], []
        for e in edges:
            if e.src_id != nid and e.dst_id != nid:
                continue
            out = e.src_id == nid
            other_id = e.dst_id if out else e.src_id
            other = by_id.get(other_id)
            # The other node's LABEL is a value too, and it is grounded by that node's own entity
            # pointers — never by the edge's (Codex f2 #5). A label with none is withheld.
            other_ptrs = [pointer_to_dict(p) for p in other.sources] if other else []
            connections.append({
                "edge_kind": e.kind.value, "direction": "out" if out else "in",
                "other_id": other_id,
                "other_label": other.label if other and other_ptrs else None,
                "other_label_pointers": other_ptrs,
                "other_kind": other.kind.value if other else "",
                "unresolved": [] if other_ptrs else ["other_label"],
                **(subject_annotation(other.kind) if other else {}),
                "pointers": [pointer_to_dict(p) for p in e.sources]})
            # OUTGOING only. A claim runs artefact → subject, so without the direction guard a
            # SUBJECT node's `claims` fills with the DOCUMENTS pointing at it — each rendered in
            # the drawer as a candidate subject, complete with a match score. Only a claim ON a
            # subject is one: an ABOUT edge to a topic or a project names no one.
            if (out and str(e.kind) in claim_kinds and other
                    and vocab.is_subject(other.kind)):
                # Each claim value only with ITS OWN pointer (Codex f1 #3): a score or a count
                # the edge holds without one is None, and named as unresolved.
                shown, withheld = _withheld(
                    {k: e.attrs.get(k) for k in ("match_score", "matched_on", "candidate_count")},
                    e.attr_sources)
                claims.append({
                    "id": other_id, "label": other.label if other_ptrs else None, **shown,
                    "label_pointers": other_ptrs,
                    "value_pointers": {k: pointer_to_dict(e.attr_sources[k])
                                       for k in shown if shown[k] is not None
                                       and k in e.attr_sources},
                    "unresolved": withheld + ([] if other_ptrs else ["label"]),
                    **subject_annotation(other.kind),
                    "requires_human_verification": True})
        connections.sort(key=lambda c: (c["edge_kind"], c["other_label"] or "", c["other_id"]))
        claims.sort(key=lambda c: (-(c.get("match_score") or 0), c["label"] or "", c["id"]))
        out = {"node": node, "sources": sources, "connections": connections, "claims": claims}
        if vocab.is_subject(node["kind"]):
            # Keyed on the KIND being a declared subject, and emitted for EVERY one. A registry row no document names
            # — 289 of the 300 once the whole registry is seeded — otherwise arrived as a name, a
            # date of birth and a nationality with nothing anywhere saying "not verified". Being
            # on file is not being identified (CLAUDE.md constraint 1).
            #
            # Read off `connections`, not `claims`: `claims` is OUTGOING by construction (see the
            # direction guard above), so on a SUBJECT node it is always empty and would have made
            # every patient look unclaimed.
            # …and only a DOCUMENT-shaped claimant counts. A claim edge may also run from an
            # event (visit → patient in the clinic pack), and counting one of those would pick
            # the LESS cautious caveat.
            claimed_by = sorted(c["other_id"] for c in connections
                                if c["edge_kind"] in claim_kinds
                                and c["direction"] == "in"
                                and c["other_kind"] in doc_kinds)
            out["identity"] = {
                "verified": False,
                "requires_human_verification": True,
                # The boolean travels with the ids it was computed from, so a caller can check it
                # against `connections` instead of trusting it — the same reason every figure in
                # `analytics.py` carries its members.
                "claimed_by_a_document": bool(claimed_by),
                "claimed_by": claimed_by,
                "caveat": identity_caveat_for(node["kind"], claimed=bool(claimed_by))}
        return out

    # -- entity reference resolution ---------------------------------------------
    # Both of these are now thin. The rule lives in ``databridge_graph.identity``, where
    # ``nl_query`` can reach it too — it was implemented HERE first and only here, which is
    # precisely how ``nl_query("Who is Loh Wei Ming?")`` went on silently answering about one of
    # three people while this layer refused to. One constraint, two surfaces, one of them
    # hardened. A package cannot import an app, so the shared half moved down rather than across.
    def _same_label_ids(self, node_id: str) -> list[str]:
        """Every open node of the same kind carrying the same label as ``node_id``. See
        ``databridge_graph.identity.same_label_node_ids``."""
        return same_label_node_ids(self.store, node_id)

    def _resolve(self, ref: str) -> EntityReference:
        """A node id OR a natural-language name → resolved / not found / **ambiguous**.

        ``databridge_graph.identity.resolve_entity`` decides — including that a node id always
        wins and is never ambiguous, which is what keeps the refusal from being a dead end. This
        wraps the outcome in the type that knows how to serialise a refusal for a tool caller.

        **A name never resolves a SUBJECT — not even a name that matches exactly one** (Codex f1
        #1). The ambiguity check only fires when a duplicate exists; one "Alice" on file was
        selected silently. A name comes back as candidates, however many there are.

        **An id resolves the REFERENCE, never the person** (Codex f2 #1). An id in a request is
        not evidence that a human chose anything: an agent can read it out of a candidate list and
        send it straight back. So a subject reached by id is served as what it is — one registry
        record, with ``requires_human_selection: True`` and ``selected_by_human: False`` on every
        reference (:func:`subject_annotation`) — and never as "the" person. This surface records
        no selection; that is the intake flow's attested, human step
        (``ResolutionResult.requires_human_selection`` is ``Literal[True]``).
        """
        if self.store.open_node_row(ref) is not None:
            return EntityReference(node_id=ref)
        match = resolve_entity(self.store, ref)
        if match.node_id is not None:
            row = self.store.open_node_row(match.node_id)
            if row is not None and is_subject_kind(row["kind"]):
                shared = same_label_node_ids(self.store, match.node_id) or [match.node_id]
                return EntityReference(error=subject_selection_refusal(ref, shared),
                                       candidates=tuple(shared))
        return EntityReference.of(match)

    # -- the tools ----------------------------------------------------------------
    # Four reads and one write. Which process exposes which is decided in ``capabilities.py``,
    # not here — this class is the shared implementation, and it deliberately knows nothing about
    # the boundary drawn across it.
    def brain_query(self, question: str, as_of: Optional[str] = None) -> dict:
        """The graph's grounded answer, with hybrid-retrieval passages beside it (P14).

        The graph answer (``nl_query``) is unchanged by the passages: they ride along as their own
        evidence list, each with its pointer. Only when the graph has NO matching entity (not an
        ambiguity refusal, not a claim-check refusal — those stand) and a passage shares a content
        word with the question is the answer made of passages instead, and then it is made of
        nothing BUT verbatim quotes, each cited (P15)."""
        from databridge_graph.query import _named_outright

        # as_of (valid-time) narrows the snapshot the answer is drawn from (audit/replay).
        explicit = _named_outright(self.store, question)
        records = record_id_matches(self.store, question) if explicit is None else [[], []]
        named_held = ([] if explicit is not None or records[0] or records[1]
                      else withheld_items(question, self.ingest_root, named=True))
        if named_held:
            # E2E run 5, F-06: the question NAMES an item the ingestion screen withheld or holds
            # for review. Records that share a word with it (the company a held filing is about)
            # are not what was asked about: the item is named as withheld, with its rule, and
            # nothing of it is quoted — never "it does not exist".
            return {"question": question, "answer": withheld_notice(named_held),
                    "grounded": False, "refusal": "", "answer_kind": "withheld",
                    "withheld_items": named_held, "matched_entities": [], "candidates": [],
                    "citations": [], "values": [], "unresolved": [], "passages": [],
                    "retrieval": {"available": False, "reason": "not searched: the question "
                                  "names a withheld item"}, "as_of": as_of}
        if records[0] or records[1]:
            # P-06: a record named by its identifier is answered about THAT record — the same
            # claim-checked `nl_query`, addressed by the node's id, so every printed value keeps
            # its own pointer. Tier 0 (label and canonical id both equal the token) answers; a
            # tier-1 record carrying the same identifier is listed beside it, never merged.
            primary = records[0] or records[1]
            out = self._answer_records(question, primary[:3])
            extra = [n.id for n in (records[1] if records[0] else records[1][3:])]
            out["matched_entities"] = list(dict.fromkeys([*out["matched_entities"], *extra]))
            out["record_lookup"] = {"identifiers": _identifier_tokens(question),
                                    "answered": [n.id for n in primary[:3]],
                                    "also_carrying_the_identifier": extra}
            a = None
        elif (explicit is None and is_vague_question(question)
              and not withheld_items(question, self.ingest_root)
              and not link_entities_indexed(self.store, question)):
            # P-13, F-05: a vague question that links to no record (the same ranking `nl_query`
            # would run, read by index) gets its question back without a whole-graph read
            out = {"question": question, "answer": "", "grounded": False, "refusal": "",
                   "answer_kind": "needs_clarification", "needs_clarification": True,
                   "matched_entities": [], "candidates": [], "citations": [], "values": [],
                   "unresolved": [], "passages": [], "as_of": as_of,
                   "retrieval": {"available": False,
                                 "reason": "not searched: the question needs a question back"},
                   **clarification_for(self.store, question)}
            return out
        else:
            # F-05: the same answer as `nl_query(self.store, question)`, read by index
            a = nl_query_indexed(self.store, question)
            out = answer_to_dict(a, self.store)
        out["as_of"] = as_of
        out["answer_kind"] = "graph" if out["grounded"] else "refused"
        if a is None:
            out["passages"], out["retrieval"] = search_passages(
                question, search=getattr(self, "passage_search", None),
                ingest_root=self.ingest_root)
            return out
        addressed = explicit.id if explicit is not None else None
        # EVERY matched entity is screened, not only the first (Codex f2 #3), plus every entity
        # whose value the answer prints. A subject among them that the question did not address
        # by id was reached from a NAME:
        #   * if the answer rests on it — it is the entity answered about, or a value of it is
        #     printed — the answer is withheld and the records come back as candidates;
        #   * otherwise (it only ranked: `nl_query` answers about its PRIMARY entity alone and
        #     prints values of that entity alone) it is removed from `matched_entities`, which
        #     would present it as resolved, and returned under `subject_candidates` instead.
        # Citations are not screened: a subject cited because a document names it was reached
        # through that document's claim edge, not chosen from a name, and every such citation
        # already carries its caveat (`answer_to_dict`).
        def _named_subject(nid) -> bool:
            if not nid or nid == addressed:
                return False
            row = self.store.open_node_row(nid)
            return row is not None and is_subject_kind(row["kind"])

        primary = a.matched_entities[0] if a.matched_entities else None
        served_on = [primary, *(v.entity_id for v in getattr(a, "values", []))]
        by_name = list(dict.fromkeys(n for n in served_on if _named_subject(n)))
        ranked_only = [n for n in dict.fromkeys(a.matched_entities[1:])
                       if _named_subject(n) and n not in by_name]
        if ranked_only:
            out["matched_entities"] = [m for m in out["matched_entities"] if m not in ranked_only]
            out["subject_candidates"] = ranked_only
            out.setdefault("subject_caveats", {}).update(
                _subject_caveats(self.store, ranked_only))
        if a.grounded and by_name:
            # `nl_query` resolved a SUBJECT from a name (Codex f1 #1) and the answer is ABOUT it.
            # That is a selection a human makes: the answer is withheld and the records come back
            # as candidates, as an ambiguous name does.
            shared: list[str] = []
            for nid in by_name:
                for sid in same_label_node_ids(self.store, nid) or [nid]:
                    if sid not in shared:
                        shared.append(sid)
            out.update(answer="", grounded=False,
                       refusal=subject_selection_refusal(question, shared),
                       candidates=list(shared), citations=[], values=[], unresolved=[],
                       answer_kind="refused",
                       subject_caveats=_subject_caveats(self.store, shared))
        if addressed is not None and is_subject_kind(explicit.kind):
            # Addressed by id: served as ONE record, a candidate — never "the" person (f2 #1).
            out["addressed_record"] = {"id": addressed, **subject_annotation(explicit.kind)}
        passages, retrieval = search_passages(question, search=getattr(self, "passage_search",
                                                                       None),
                                              ingest_root=self.ingest_root)
        if explicit is not None and is_subject_kind(explicit.kind):
            # A human selected ONE subject by id; only passages bound to it may ride along
            # (Codex r2 #1) — the question's words match every same-named person's documents.
            passages, withheld = passages_bound_to(self.store, explicit.id, passages)
            retrieval = {**retrieval, "withheld_unbound_to_selected_subject": withheld}
        out["passages"] = passages
        out["retrieval"] = retrieval
        no_entity = not a.grounded and not out["candidates"] and not a.citations
        if no_entity:
            # P-06: an item the ingestion screen WITHHELD is named as withheld — never reported
            # as missing, and nothing of its content is quoted.
            held = withheld_items(question, self.ingest_root)
            notice = withheld_notice(held) if held else ""
            if held:
                out["withheld_items"] = held
            text = passages_answer(question, passages)
            if held:
                # Codex R-029 r1 #2: once an item the question names is withheld or held for
                # review, the answer is that notice ALONE — no passage is searched into it or
                # quoted beside it (a stale extract of the held file would disclose it).
                out.update(answer=notice, refusal="", answer_kind="withheld", passages=[],
                           citations=[], values=[])
            elif is_vague_question(question):
                # P-13: a vague question about the asker's own affairs gets a question back.
                # E2E run 5, F-05: BEFORE any passage answer — on a large estate some passage
                # always shares a word ("should", "book") with a vague question, and a dump of
                # loosely matching extracts is not an answer to "is everything okay?".
                out.update(answer="", refusal="", answer_kind="needs_clarification",
                           needs_clarification=True, **clarification_for(self.store, question))
            elif text is not None:
                out.update(answer=text, grounded=True,
                           refusal="", answer_kind="passages",
                           citations=[passage_citation(p) for p in passages
                                      if p.get("lexical_match")])
        return out

    def _answer_records(self, question: str, nodes: list) -> dict:
        """`nl_query` addressed to each record by its node id, merged (P-06). Only the records
        the identifier named are answered; each answer is the claim-checked deterministic one."""
        # F-05: each record is ADDRESSED by its id, so `nl_query` answers about it from its own
        # neighbourhood, read by index (`NeighbourhoodView`), not from the whole graph
        parts = []
        for n in nodes:
            view = NeighbourhoodView(self.store, n.id)
            parts.append(answer_to_dict(nl_query(view, view.address(question)), self.store))
        if len(parts) == 1:
            out = parts[0]
        else:
            out = {**parts[0],
                   "answer": "\n".join(p["answer"] for p in parts if p["answer"]),
                   "grounded": all(p["grounded"] for p in parts),
                   "refusal": "; ".join(p["refusal"] for p in parts if p["refusal"]),
                   "matched_entities": list(dict.fromkeys(
                       m for p in parts for m in p["matched_entities"])),
                   "candidates": list(dict.fromkeys(c for p in parts for c in p["candidates"])),
                   "citations": [c for p in parts for c in p["citations"]],
                   "values": [v for p in parts for v in p["values"]],
                   "unresolved": [u for p in parts for u in p["unresolved"]]}
        out["question"] = question
        # the answered record leads; the rest of nl_query's ranking follows
        lead = [n.id for n in nodes]
        ranked = list(dict.fromkeys([*lead, *out["matched_entities"]]))
        # A subject that only RANKED is never presented as resolved (Codex f2 #3): it moves to
        # `subject_candidates`, as on the name path below.
        subjects = [m for m in ranked if m not in lead and (row := self.store.open_node_row(m))
                    is not None and is_subject_kind(row["kind"])]
        out["matched_entities"] = [m for m in ranked if m not in subjects]
        if subjects:
            out["subject_candidates"] = subjects
            out.setdefault("subject_caveats", {}).update(_subject_caveats(self.store, subjects))
        return out

    def brain_neighbors(self, entity: str, depth: int = 1) -> dict:
        ref = self._resolve(entity)
        if ref.node_id is None:
            return ref.failure(nodes=[], edges=[])
        nid = ref.node_id
        sub = neighbors(self.store, nid, depth=depth)
        return {"root": nid, "root_identity": _subject_caveats(self.store, [nid]).get(nid),
                "nodes": [node_to_dict(n) for n in sub.nodes],
                "edges": [edge_to_dict(e) for e in sub.edges]}

    def brain_timeline(self, entity: str) -> dict:
        ref = self._resolve(entity)
        if ref.node_id is None:
            return ref.failure(events=[])
        nid = ref.node_id
        evs = timeline(self.store, nid)
        # Every event carries the pointers of the version or edge it reports (Codex f1 #3), and
        # every subject it names carries the caveat (#2) — the root included.
        from databridge_graph.store import _parse

        # keyed on (id, system_from): an edge id has one row per bi-temporal VERSION, and each
        # event reports one version — its pointers are that version's, not the latest one's.
        edge_rows = {(r["id"], _parse(r["system_from"])): r
                     for r in self.store.edges_touching(nid)}
        node_rows = {_parse(r["system_from"]): r for r in self.store.all_node_versions(nid)}
        events = []
        for e in evs:
            if e.kind == "edge":
                r = edge_rows.get((e.entity_id, e.system_from))
                ends = [r["src_id"], r["dst_id"]] if r is not None else []
                ptrs = self._edge_row_pointers(r)
            else:
                ends = [nid]
                vrow = node_rows.get(e.system_from)
                ptrs = (self.store.sources_for(nid, version_pk=vrow["rowid_pk"])
                        if vrow is not None else [])
            events.append({
                "kind": e.kind, "label": e.label,
                "valid_from": e.valid_from.isoformat() if e.valid_from else None,
                "valid_to": e.valid_to.isoformat() if e.valid_to else None,
                "system_from": e.system_from.isoformat() if e.system_from else None,
                "system_to": e.system_to.isoformat() if e.system_to else None,
                "pointers": [pointer_to_dict(p) for p in ptrs],
                "subject_caveats": _subject_caveats(self.store, ends)})
        root = self.store.open_node_row(nid)
        return {"entity": nid, **(subject_annotation(root["kind"]) if root is not None else {}),
                "events": events}

    def _edge_row_pointers(self, row) -> list:
        """The entity pointers of one edge row (the version the timeline reports)."""
        if row is None:
            return []
        from databridge_graph import row_to_edge

        return list(row_to_edge(self.store, row).sources)

    def brain_path(self, from_entity: str, to_entity: str) -> dict:
        src, dst = self._resolve(from_entity), self._resolve(to_entity)
        # Reported per endpoint. A single "endpoint not found" for two arguments makes the caller
        # guess which one, and would flatten "I could not find it" and "it names four people"
        # into the same sentence.
        for ref in (src, dst):
            if ref.node_id is None:
                return ref.failure(found=False, steps=[])
        gp = path(self.store, src.node_id, dst.node_id)
        return {"from": src.node_id, "to": dst.node_id, "found": gp.found,
                "steps": [{"edge_kind": s.edge.kind.value, "to": s.to_node.id,
                           "to_label": s.to_node.label,
                           **subject_annotation(s.to_node.kind),
                           "to_pointers": [pointer_to_dict(p) for p in s.to_node.sources],
                           "pointers": [pointer_to_dict(p) for p in s.edge.sources]}
                          for s in gp.steps],
                # every subject the path names — both endpoints and every step (Codex f1 #2)
                "subject_caveats": _subject_caveats(
                    self.store, [src.node_id, dst.node_id, *(s.to_node.id for s in gp.steps)]),
                "provenance_pointers": [pointer_to_dict(p) for p in gp.pointers()]}

    def brain_ingest(self, source_path: str, *, text: Optional[str] = None) -> dict:
        """The single write path (04_PRD §9). Build a DocumentRecord from ``text`` (inline) or a
        REGISTERED source (``self.sources[source_path]``) and extract a grounded subgraph into the
        store. NEVER opens an arbitrary filesystem path — a caller asking for
        ``brain_ingest("/etc/passwd")`` gets a refusal, not a file read.

        Over MCP this is exposed by ``brain_writer_server.py`` alone; the reader process neither
        registers it nor holds a harness to run it with (R-34)."""
        if self.harness is None:
            return {"error": "ingest unavailable: no harness configured", "entities_added": 0}
        if text is None:
            text = self.sources.get(source_path)
        if text is None:
            return {"error": f"unknown source {source_path!r}: ingestion is restricted to inline "
                             f"text or a registered source (no arbitrary file access)",
                    "entities_added": 0, "relations_added": 0, "refused": []}
        import hashlib

        from databridge_convert.ir.models import Block, DocumentRecord
        from databridge_core import SnapshotIdentity
        # The CONTENT is the identity of what was read: hash it, and stamp that address on every
        # pointer the extraction emits (`capture_digest`), so a later change to the same
        # `source_path` is detectable from the pointer alone. This used to be the literal
        # "ingest" — a hash field that could not tell any two documents apart.
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        # `as_of` is the store's clock: the real one on the gateway's shared store, a frozen one
        # only where a test or the seed injected it (GraphStore(clock=...)).
        snap = SnapshotIdentity(source_id=source_path, as_of=self.store.now(),
                                content_hash=f"sha256:{digest}", schema_hash="doc")
        # Codex R-029 r1 #9: the DOCUMENT's identity is its source-qualified path, hashed (P4,
        # `capture.document_identity`), so two registered sources that share a file name never
        # collide; the CONTENT's identity (version) stays `sha256`.
        document_id = hashlib.sha256(f"brain://ingest/{source_path}".encode("utf-8")).hexdigest()
        doc = DocumentRecord(document_id=document_id, sha256=digest, snapshot=snap,
                             blocks=[Block(kind="Paragraph", text=text)])
        res = extract_graph(doc, self.harness, store=self.store,
                            capture_digest=f"sha256:{digest}")
        # report the genuinely NEW nodes (upsert action "inserted") vs corroborated existing ones —
        # the node upserts are the first len(entities) of res.upserts (edges are appended after).
        new_entities = [n.label for n, up in zip(res.entities, res.upserts) if up.action == "inserted"]
        return {"source_path": source_path, "document_id": document_id,
                "entities_added": len(res.entities),
                "new_entities": new_entities, "relations_added": len(res.relations),
                "refused": res.refused, "content_sha256": digest,
                "extraction_route": self.extraction_route,
                "stats": self.store.counts()}


# --- the combined server, removed (R-34) ------------------------------------------
def build_mcp_server(*_args, **_kwargs):
    """Removed. It built ONE server carrying the read tools and ``brain_ingest`` together.

    Kept as a tripwire rather than deleted: a caller that still imports this name is a caller about
    to front a write tool with the endpoint an agent connects to, and a clear failure beats an
    ``ImportError`` somebody fixes by reintroducing the combined builder.

    Use ``brain_reader_server.build_reader_server()`` (the agent-reachable process) or
    ``brain_writer_server.build_writer_server()`` (ingestion), and run them separately.
    """
    raise RuntimeError(
        "build_mcp_server() was removed by R-34: read tools and brain_ingest are now two "
        "processes with two registries. Use brain_reader_server.build_reader_server() or "
        "brain_writer_server.build_writer_server() — see capabilities.py.")

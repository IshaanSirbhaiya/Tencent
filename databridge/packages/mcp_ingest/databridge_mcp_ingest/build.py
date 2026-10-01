"""The build stage — approved text and rows into the shared graph, every value with a pointer.

**Prose.** A document is cut into ~1,800-char chunks with 15% overlap (principles §3). Each chunk
is screened AGAIN (P1: a chunk can carry what the document-level pass averaged away), and only an
allowed chunk's MASKED text reaches `extract_graph` — the single prose→graph path in
`packages/graph` (closed vocabulary, grounding wall). Offsets come back chunk-relative; they are
mapped back onto the document and re-minted as `doc_span` pointers carrying the source, the item
identity, the capture digest and `trust=untrusted`, so every pointer resolves against
`<capture>/<sha>.txt` (masking is same-length, so the offsets are the same in the masked text).

**The anchor.** Every ingested item gets a node of the kind bound to the `document` meta-kind (if
the active vocabulary has one — otherwise that absence is recorded), linked to each entity found in
it by a `references`-shaped edge whose evidence is the entity's own span.

**Rows.** Tabular items go through `estate_seed`'s row→node/edge builder (via `rows_to_graph`, the
single point of contact) with `AdapterRow`s carrying our own `table_cell` pointers, driven by an
APPROVED mapping (`mapping.py`).

**Speed (BUILD_PLAN §11a P).** The prose builder is split into prepare → extract → apply so the
model calls (`extract`) can run on a bounded worker pool while every graph write (`apply`) stays
on one thread, in document/chunk order, inside ONE transaction per document (`store_batch`). The
output is therefore identical to a serial run. Relations whose two ends fall in adjacent chunks
are recovered by a targeted "bridge" window (`ProseBuilder.bridge_jobs`) — grounded like any
chunk, screened like any chunk, and only issued where such a pair exists.
"""
from __future__ import annotations

import datetime as dt
import inspect
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from databridge_core import SourcePointer, Trust
from databridge_core.provenance import SnapshotIdentity

from .screen import ScreenConfig, apply_masks, screen_text

CHUNK_CHARS = 1800
CHUNK_OVERLAP = 0.15
MIN_CHUNK = 120


class BuildError(RuntimeError):
    pass


# --- chunking ------------------------------------------------------------------------------------
def chunk_spans(text: str, size: int = CHUNK_CHARS, overlap: float = CHUNK_OVERLAP,
                minimum: int = MIN_CHUNK) -> list[tuple[int, int]]:
    """(start, end) spans covering `text`. Ends prefer whitespace in the last 15% of a window; a
    trailing sliver shorter than `minimum` is folded into the previous chunk."""
    n = len(text)
    if n == 0:
        return []
    if n <= size:
        return [(0, n)]
    step_back = int(size * overlap)
    spans: list[tuple[int, int]] = []
    start = 0
    while start < n:
        end = min(start + size, n)
        if end < n:
            window_floor = end - int(size * 0.15)
            ws = max(text.rfind(" ", window_floor, end), text.rfind("\n", window_floor, end))
            if ws > start:
                end = ws
        spans.append((start, end))
        if end >= n:
            break
        start = max(end - step_back, start + 1)
    if len(spans) > 1 and spans[-1][1] - spans[-1][0] < minimum:
        last = spans.pop()
        spans[-1] = (spans[-1][0], last[1])
    return spans


# --- pointers ------------------------------------------------------------------------------------
def source_ref(source_id: str) -> str:
    """The `source_id` every pointer carries: EXACTLY the connection id. The agent runtime scopes
    what an agent may see by comparing this against the agent's declared sources, so a prefix or
    a path here would withhold everything this package ingests."""
    return source_id


def span_pointer(source_id: str, item_id: str, sha: str, start: int, end: int,
                 derivation: Optional[str] = None) -> SourcePointer:
    """`derivation` carries the extractor's §5.8 `produced_by` stamp across the chunk → document
    re-mint, so the graph row still says which agent/model/prompt wrote it."""
    return SourcePointer(source_id=source_ref(source_id), kind="doc_span", document_id=item_id,
                         char_start=start, char_end=end, capture_digest=sha,
                         trust=Trust.UNTRUSTED, derivation=derivation)


def cell_pointer(source_id: str, table: str, column: str, row_id: str,
                 sha: str) -> SourcePointer:
    return SourcePointer(source_id=source_ref(source_id), kind="table_cell", table=table,
                         column=column, row_id=row_id, capture_digest=sha,
                         trust=Trust.UNTRUSTED)


def derived_pointer(derivation: str, basis: list[SourcePointer]) -> SourcePointer:
    return SourcePointer(source_id=basis[0].source_id, kind="derived", derivation=derivation,
                         derived_from=list(basis), trust=Trust.UNTRUSTED)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# --- vocabulary helpers --------------------------------------------------------------------------
def document_kind(vocab, override: Optional[str] = None) -> tuple[Optional[str], str]:
    """(kind, note). The kind bound to the `document` meta-kind, or None with the reason."""
    from databridge_graph.meta import NodeMetaKind

    bound = vocab.nodes_binding_to(NodeMetaKind.DOCUMENT)
    if override:
        if override in bound:
            return override, f"document anchor kind {override!r} (connection setting)"
        return None, (f"connection names document_kind {override!r}, which does not bind to the "
                      f"document meta-kind in the active vocabulary {list(bound)}")
    if not bound:
        return None, ("the active vocabulary has no kind bound to the `document` meta-kind; items "
                      "are ingested without an anchor node (bind one in the pack ontology)")
    if len(bound) > 1:
        # R-022 #14: never resolved by list order. The one declared resolution: a pack kind that
        # carries the meta-kind's OWN name (`document`) is the pack's generic binding — kind
        # names are unique, so at most one kind can claim it. Anything else is REFUSED + recorded.
        generic = [k for k in bound if str(k) == NodeMetaKind.DOCUMENT.value]
        if len(generic) == 1:
            return generic[0], (f"document anchor kind {generic[0]!r} (the kind named for the "
                                f"`document` meta-kind; {len(bound)} kinds bind to it "
                                f"{list(bound)} — set document_kind on the connection to choose "
                                f"another)")
        return None, (f"AMBIGUOUS: {len(bound)} kinds bind to the `document` meta-kind "
                      f"{list(bound)}; no anchor node is written until the connection names one "
                      f"(set document_kind on the connection)")
    return bound[0], f"document anchor kind {bound[0]!r}"


def _admits(decl, end: str, kind: str) -> bool:
    allows = getattr(decl, "allows", None)
    if callable(allows):
        try:
            return bool(allows(end, kind))
        except Exception:                                            # noqa: BLE001
            pass
    kinds = tuple(getattr(decl, end) or ())
    return not kinds or "*" in kinds or kind in kinds


def edge_kind_choice(vocab, meta_names: tuple[str, ...], src_kind: str, dst_kind: str,
                     hint: str = "") -> tuple[Optional[str], list[str]]:
    """(kind, tied): the single MOST SPECIFIC declared edge kind (of the given meta-kinds)
    admitting src → dst — naming both kinds explicitly beats a wildcard. When two or more kinds
    are equally specific the choice is AMBIGUOUS (R-022 #14): `(None, [the tied kinds])`, and the
    caller refuses and records it; nothing is chosen by name order."""
    from databridge_graph.meta import ATTESTED_META_KINDS, EdgeMetaKind

    scored: list[tuple[int, str]] = []
    for meta_name in meta_names:
        meta = EdgeMetaKind(meta_name)
        if meta in ATTESTED_META_KINDS:
            continue
        for name in vocab.edges_binding_to(meta):
            decl = vocab.edge(name)
            if not (_admits(decl, "src", src_kind) and _admits(decl, "dst", dst_kind)):
                continue
            score = (2 if src_kind in (decl.src or ()) else 0) + \
                    (2 if dst_kind in (decl.dst or ()) else 0) + \
                    (1 if hint and hint.lower() in name.lower() else 0)
            scored.append((score, name))
    if not scored:
        return None, []
    top = max(s for s, _ in scored)
    tied = sorted({n for s, n in scored if s == top})
    return (tied[0], []) if len(tied) == 1 else (None, tied)


def edge_kind_for(vocab, meta_names: tuple[str, ...], src_kind: str, dst_kind: str,
                  hint: str = "") -> Optional[str]:
    """The most specific declared edge kind admitting src → dst; None when there is none OR the
    choice is ambiguous (`edge_kind_choice` says which)."""
    return edge_kind_choice(vocab, meta_names, src_kind, dst_kind, hint)[0]


# --- one transaction per document ------------------------------------------------------------------
class _DeferredCommit:
    """Stands in for a `sqlite3.Connection` while a batch is open: every statement runs on the real
    connection, `commit()` is deferred to the end of the batch, `rollback()` rolls the WHOLE batch
    back (which is what `GraphStore` asks for when one write fails half-way)."""

    __slots__ = ("_conn",)

    def __init__(self, conn) -> None:
        object.__setattr__(self, "_conn", conn)

    def commit(self) -> None:
        return None

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value) -> None:
        setattr(self._conn, name, value)


@contextmanager
def store_batch(store):
    """All graph writes inside the block commit ONCE, or not at all.

    A `GraphStore` commits after every node, edge and pointer; on a file-backed store each commit
    is a journal sync, and a document's worth of them dominated the build stage when the model was
    fast (measured: see `bench/`). Uses `store.batch()` when the store offers one; otherwise it
    holds the store's own lock for the block (single writer) and defers `commit()` on its
    connection. Anything that is not a sqlite-backed `GraphStore` is written through unbatched.
    A failure rolls back the whole document — never a half-written one."""
    native = getattr(store, "batch", None)
    if callable(native):
        with native():
            yield
        return
    conn = getattr(store, "conn", None)
    lock = getattr(store, "_lock", None)
    if not isinstance(conn, sqlite3.Connection) or lock is None:
        yield
        return
    with lock:
        store.conn = _DeferredCommit(conn)
        try:
            yield
        except BaseException:
            store.conn = conn
            conn.rollback()
            raise
        else:
            store.conn = conn
            conn.commit()


# --- the prose builder ---------------------------------------------------------------------------
#: Cross-chunk relations: two grounded entities in ADJACENT chunks, no chunk holding both, at most
#: this many characters apart, get one extra "bridge" window sent to the model.
def _max_evidence_chars() -> int:
    try:
        from databridge_graph.extractor import MAX_EVIDENCE_CHARS

        return int(MAX_EVIDENCE_CHARS)
    except (ImportError, AttributeError, TypeError, ValueError):
        return 600


#: The extractor refuses relation evidence longer than `MAX_EVIDENCE_CHARS`, and evidence spans
#: from the first name's start to the second name's end — so a pair is only worth a bridge call
#: when THAT span fits. Derived, never restated: the extractor's limit is the bridge's limit.
BRIDGE_MAX_SPAN = _max_evidence_chars()
BRIDGE_MAX_GAP = BRIDGE_MAX_SPAN        # an upper bound on the gap (names have length)
BRIDGE_PAD = 200


@dataclass
class DocBuild:
    item_id: str
    chunks: int = 0
    chunks_blocked: int = 0
    entities: int = 0
    relations: int = 0
    anchor_edges: int = 0
    refused: list[str] = field(default_factory=list)
    pointers: list[dict] = field(default_factory=list)
    anchor_id: Optional[str] = None
    model_called: int = 0
    bridges: int = 0
    bridge_relations: int = 0
    model_ms: float = 0.0
    write_ms: float = 0.0
    produced_by: list[dict] = field(default_factory=list)   # distinct §5.8 stamps
    model_cached: int = 0                                     # calls answered from a cache
    subject_mentions: int = 0
    subject_candidates: list[dict] = field(default_factory=list)
    name_joins: list[dict] = field(default_factory=list)
    """E2E run-3 P-05: mentions joined to a keyed record by the leading words of its grounded
    name (candidate-grade, non-subject kinds only), for audit and reversal."""
    graph_ids: list[str] = field(default_factory=list)
    """Every graph id this build wrote (anchor, entities, anchor edges, relations, bridge
    relations), in write order, each once — so the runner can check each has a ledgered pointer
    that resolves (FXJ1 cross-track)."""


@dataclass
class ChunkJob:
    """One window of a document's MASKED text that passed the per-chunk screen."""
    index: int                      # chunk ordinal; bridges carry the index of the LEFT chunk
    start: int
    end: int
    text: str
    bridge: bool = False


@dataclass
class DocPlan:
    item_id: str
    sha: str
    raw_text: str
    masked_text: str
    title: str
    uri: str
    retrieved_at: dt.datetime
    override_allow: bool
    jobs: list[ChunkJob] = field(default_factory=list)
    allowed_index: set = field(default_factory=set)
    out: Optional[DocBuild] = None


def _extract_kwargs(fn, *, capture_digest: str, run_id: Optional[str] = None) -> dict:
    params = inspect.signature(fn).parameters
    kw: dict[str, Any] = {}
    if "capture_digest" in params:
        kw["capture_digest"] = capture_digest
    if "trust" in params:
        kw["trust"] = Trust.UNTRUSTED
    if "run_id" in params and run_id is not None:
        kw["run_id"] = run_id
    return kw


class ProseBuilder:
    """Documents → graph in three steps, so the slow one can run in parallel and the one that
    writes cannot:

        prepare(...)   chunk + per-chunk screen (ledgered)                      main thread
        extract(job)   one model call through `extract_graph(store=None)`       any thread
        apply(plan, …) anchor, entities, edges — in chunk order, one transaction main thread

    `build(...)` is the three in sequence, for a caller with no pool. Output is identical however
    `extract` is scheduled, because `apply` consumes results in document/chunk order."""

    def __init__(self, store, harness, *, source_id: str, vocab, doc_kind: Optional[str],
                 screen: ScreenConfig, chunk_ledger=None, bridges: bool = True,
                 run_id: Optional[str] = None) -> None:
        self.store = store
        self.harness = harness
        self.source_id = source_id
        self.vocab = vocab
        self.doc_kind = doc_kind
        self.screen = screen
        self.chunk_ledger = chunk_ledger
        self.bridges = bridges
        self.run_id = run_id

    def anchor(self, *, item_id: str, sha: str, text_len: int, title: str, uri: str,
               retrieved_at: dt.datetime, notes: Optional[list] = None):
        """(anchor id, whole-text pointer) — `anchor_with_listing` without the listing pointer
        (the public two-value shape other callers unpack, e.g. `tabular.cards`)."""
        got = self.anchor_with_listing(item_id=item_id, sha=sha, text_len=text_len, title=title,
                                       uri=uri, retrieved_at=retrieved_at, notes=notes)
        return got[0], got[1]

    def listing_label(self, *, item_id: str, title: str, uri: str,
                      cell) -> tuple[str, SourcePointer, Optional[str]]:
        """(label, its pointer, note) for an anchor (R-022 f2 #4). The listing's title and uri are
        UNTRUSTED source metadata: they are screened before either becomes a graph label.

          * `block` or `review` — the title/uri is withheld; the label is a neutral
            `document <item id prefix>`, derived from the listing's `item_id` cell, and the note
            says which rule fired. (A review-band approval covers a document's CONTENT digest,
            not its listing, so it does not lift this.)
          * `allow` with a redaction — the label is the MASKED value, with a `derived` pointer
            ("masked by the screen") over the listing cell, as for a masked table cell.
          * otherwise — the listing cell itself."""
        label_col = "title" if title else "uri"
        raw = title or uri
        if self.screen is None:
            return raw, cell(label_col), None
        verdict = screen_text(" \n ".join(v for v in (title, uri) if v), self.screen)
        if verdict.decision != "allow":
            rules = sorted({f["rule"] for f in verdict.fired}) or [verdict.decision]
            label = f"document {item_id[:12]}"
            ptr = derived_pointer(f"label = 'document ' + the first 12 characters of item_id "
                                  f"(the listing's title/uri withheld: screen {verdict.decision}, "
                                  f"rules {', '.join(rules)})", [cell("item_id")])
            return label, ptr, (f"LISTING_METADATA_{verdict.decision.upper()}: the listing's "
                                f"title/uri for {item_id} matched {rules}; the anchor label is "
                                f"withheld (the owner renames the item at the source)")
        masked, red = apply_masks(raw, self.screen.redactions)
        if red:
            rules = sorted({r["rule"] for r in red})
            return masked, derived_pointer(f"masked by the screen (rules: {', '.join(rules)})",
                                           [cell(label_col)]), None
        return raw, cell(label_col), None

    def anchor_with_listing(self, *, item_id: str, sha: str, text_len: int, title: str, uri: str,
                            retrieved_at: dt.datetime, notes: Optional[list] = None):
        """The per-item document node. Its identity is the item identity (hashed full path), so
        two same-titled files never fuzzy-merge.

        Its `label` and `canonical_id` come from the LISTING, not from the document's text, so
        they cite the listing (R-022 #5): the listing record is captured as its own immutable,
        content-addressed artefact (`listing_capture`) and the label cites its `title` (or `uri`)
        cell; the canonical id is derived from its `source`, `uri` and `item_id` cells. Every one
        resolves to the value it names. Returns (anchor id, whole-text pointer, listing pointer)."""
        from databridge_graph.kinds import NodeKind
        from databridge_graph.merge import upsert_node
        from databridge_graph.schema import GraphNode

        if self.doc_kind is None:
            return None, None, None
        whole = span_pointer(self.source_id, item_id, sha, 0, max(text_len, 0))
        lsha = listing_capture(self.source_id, item_id, uri=uri, title=title)

        def cell(column: str) -> SourcePointer:
            return cell_pointer(self.source_id, LISTING_TABLE, column, item_id, lsha)

        label, label_ptr, note = self.listing_label(item_id=item_id, title=title, uri=uri,
                                                    cell=cell)
        if note and notes is not None:
            notes.append(note)
        kind = NodeKind(self.doc_kind)
        node = GraphNode(
            id=anchor_id_for(self.doc_kind, self.source_id, item_id), kind=kind,
            label=label, canonical_id=f"mcp:{self.source_id}:{item_id}",
            attr_sources={
                "canonical_id": derived_pointer(
                    "canonical id = 'mcp:' + source + ':' + item_id, where item_id = "
                    "sha256('mcp://' + source + '/' + uri) (the listing record's cells)",
                    [cell("source"), cell("uri"), cell("item_id")]),
                "label": label_ptr},
            sources=(whole,), valid_from=retrieved_at, system_from=self.store.now())
        outcome = upsert_node(self.store, node, self.vocab)
        return outcome.id, whole, label_ptr

    # -- step 1 -----------------------------------------------------------------------------------
    def prepare(self, *, item_id: str, sha: str, raw_text: str, masked_text: str, title: str,
                uri: str, retrieved_at: dt.datetime, override_allow: bool = False) -> DocPlan:
        plan = DocPlan(item_id=item_id, sha=sha, raw_text=raw_text, masked_text=masked_text,
                       title=title, uri=uri, retrieved_at=retrieved_at,
                       override_allow=override_allow, out=DocBuild(item_id=item_id))
        if self.harness is None:
            plan.out.refused.append("no model configured: entity extraction not run (anchor only)")
            return plan
        for index, (cs, ce) in enumerate(chunk_spans(masked_text)):
            plan.out.chunks += 1
            if self._screen_window(plan, index, cs, ce):
                plan.jobs.append(ChunkJob(index, cs, ce, masked_text[cs:ce]))
                plan.allowed_index.add(index)
            else:
                plan.out.chunks_blocked += 1
        return plan

    def _screen_window(self, plan: DocPlan, index, cs: int, ce: int, **extra) -> bool:
        verdict = screen_text(plan.raw_text[cs:ce], self.screen,
                              override_allow=plan.override_allow)
        if self.chunk_ledger is not None:
            from .records import GovernanceChunkRecord, line

            # built THROUGH the typed format its readers use, whatever ledger it was given
            self.chunk_ledger.write(line(GovernanceChunkRecord, {
                "item_id": plan.item_id, "chunk": index, "char_start": cs, "char_end": ce,
                **extra, **verdict.as_record()}))
        return verdict.decision == "allow"

    # -- step 2 -----------------------------------------------------------------------------------
    def extract(self, plan: DocPlan, job: ChunkJob):
        """One model call. Pure with respect to the store (`store=None`): safe on any thread."""
        from databridge_convert.ir import Block, DocumentRecord
        from databridge_graph.extractor import extract_graph

        doc = DocumentRecord(
            document_id=plan.item_id, sha256=plan.sha,
            snapshot=SnapshotIdentity(source_id=source_ref(self.source_id),
                                      as_of=plan.retrieved_at, content_hash=plan.sha,
                                      schema_hash=""),
            blocks=[Block(kind="Paragraph", text=job.text)],
            # `char_offset`: where this chunk starts in the document, so a subject's mention id
            # carries its span in the WHOLE document (`…@doc#start-end`) and two same-named
            # subjects in two chunks stay two nodes (R-019-extractor-graph-r2)
            source_meta={"capture_digest": plan.sha, "uri": plan.uri, "char_offset": job.start})
        t0 = time.perf_counter()
        result = extract_graph(doc, self.harness, store=None, valid_from=plan.retrieved_at,
                               system_from=self.store.now(),
                               **_extract_kwargs(extract_graph, capture_digest=plan.sha,
                                                 run_id=self.run_id))
        return result, (time.perf_counter() - t0) * 1000

    # -- cross-chunk relations ----------------------------------------------------------------------
    def bridge_jobs(self, plan: DocPlan, results: list) -> list[ChunkJob]:
        """Windows for relations whose two ends fell in DIFFERENT chunks.

        For each pair of adjacent allowed chunks (k, k+1): an entity grounded only in k and one
        grounded only in k+1, their joint span within `BRIDGE_MAX_SPAN`, whose kinds some offered
        edge kind admits (either direction), and which no single chunk holds together. Their
        spans (padded) become ONE window per boundary — screened again like any chunk — so the
        extra cost is one model call per boundary that has such a pair, and zero otherwise. The
        grounding wall still applies: a relation from a bridge must have both names in the
        window, and both ends must already be entities of this document."""
        if not self.bridges or not results:
            return []
        by_index = {job.index: (job, res) for job, (res, _) in zip(plan.jobs, results)
                    if not job.bridge}
        out: list[ChunkJob] = []
        n = len(plan.masked_text)
        for k in sorted(by_index):
            if k + 1 not in by_index:
                continue
            (ja, ra), (jb, rb) = by_index[k], by_index[k + 1]
            ents_a = _grounded(ra, ja.start)
            ents_b = _grounded(rb, jb.start)
            windows: list[tuple[int, int]] = []
            for ida, (kind_a, sa, ea) in ents_a.items():
                if ida in ents_b:
                    continue
                for idb, (kind_b, sb, eb) in ents_b.items():
                    if idb in ents_a or idb == ida:
                        continue
                    span = max(ea, eb) - min(sa, sb)
                    if span > BRIDGE_MAX_SPAN or not self._relatable(kind_a, kind_b):
                        continue
                    ws = max(0, min(sa, sb) - BRIDGE_PAD)
                    we = min(n, max(ea, eb) + BRIDGE_PAD)
                    if ja.start <= ws and we <= ja.end or jb.start <= ws and we <= jb.end:
                        continue                         # a chunk already saw this window
                    windows.append((ws, we))
            if not windows:
                continue
            ws, we = min(w[0] for w in windows), max(w[1] for w in windows)
            if we - ws > CHUNK_CHARS:                    # too wide for one call: the closest pair
                ws, we = min(windows, key=lambda w: w[1] - w[0])
            if self._screen_window(plan, f"bridge:{k}", ws, we, bridge=True):
                out.append(ChunkJob(k, ws, we, plan.masked_text[ws:we], bridge=True))
        return out

    def _relatable(self, kind_a: str, kind_b: str) -> bool:
        from databridge_graph.meta import ATTESTED_META_KINDS

        for name in self.vocab.edge_kinds:
            decl = self.vocab.edge(name)
            if getattr(decl, "binds_to", None) in ATTESTED_META_KINDS:
                continue
            if name in _reference_edge_names(self.vocab):
                continue                   # `mentions`-shaped edges are the anchor's, not prose's
            if (_admits(decl, "src", kind_a) and _admits(decl, "dst", kind_b)) or \
                    (_admits(decl, "src", kind_b) and _admits(decl, "dst", kind_a)):
                return True
        return False

    # -- step 3 -----------------------------------------------------------------------------------
    def apply(self, plan: DocPlan, results: list) -> DocBuild:
        """Write the document: `results[i]` is `(GraphExtractionResult, ms)` for `plan.jobs[i]`
        (bridges last). One transaction per document."""
        from databridge_graph.kinds import EdgeKind
        from databridge_graph.merge import upsert_edge, upsert_node
        from databridge_graph.schema import GraphEdge

        out = plan.out
        masked_text, item_id, sha = plan.masked_text, plan.item_id, plan.sha
        t0 = time.perf_counter()
        with store_batch(self.store):
            anchor_id, _, label_ptr = self.anchor_with_listing(
                item_id=item_id, sha=sha, text_len=len(plan.raw_text), title=plan.title,
                uri=plan.uri, retrieved_at=plan.retrieved_at, notes=out.refused)
            out.anchor_id = anchor_id
            if anchor_id is not None:
                _note_id(out, anchor_id)
            if anchor_id is not None:
                out.pointers.append({"entity": anchor_id, "kind": "doc_span", "sha": sha,
                                     "start": 0, "end": len(plan.raw_text), "label": None})
                out.pointers.append({"entity": anchor_id, **_ledger_pointer(label_ptr, None)})
            resolved: dict[str, str] = {}
            aliased: set[str] = set()
            main_edges: set[str] = set()
            for job, (result, ms) in zip(plan.jobs, results):
                out.model_called += 1
                out.model_ms += ms
                pb = getattr(result, "produced_by", None)
                if pb:
                    # `at` and `cached` vary per call, not per producer: they stay out of the
                    # stamp (a re-run from cache is the same producer); cache use is timed
                    stamp = {k: v for k, v in pb.items() if k not in ("at", "cached")}
                    if pb.get("cached"):
                        out.model_cached += 1
                    if stamp not in out.produced_by:
                        out.produced_by.append(stamp)
                # subjects are document-scoped mentions; candidates are listed, never linked
                out.subject_candidates.extend(getattr(result, "subject_candidates", None) or [])
                label = f"bridge {job.index}/{job.index + 1}" if job.bridge else \
                    f"chunk {job.index}"
                cs, chunk = job.start, job.text

                def remap(p: SourcePointer, depth: int = 0) -> SourcePointer:
                    # R-022 f2 #3: EVERY chunk-relative pointer is re-minted against the
                    # document, at any depth — a `derived` pointer's `derived_from` leaves are
                    # chunk-relative too. A kind the prose path never mints is refused, never
                    # passed through with offsets into the wrong text.
                    if p.kind == "derived" and depth < 8:
                        basis = [remap(q, depth + 1) for q in (p.derived_from or [])]
                        if not basis:
                            raise BuildError(f"derived pointer with no basis from {item_id} "
                                             f"{label}")
                        return p.model_copy(update={"derived_from": basis,
                                                    "source_id": source_ref(self.source_id),
                                                    "trust": Trust.UNTRUSTED})
                    if p.kind != "doc_span" or p.char_start is None or p.char_end is None:
                        raise BuildError(f"unsupported {p.kind!r} pointer from the extractor "
                                         f"for {item_id} {label} (the prose path re-mints "
                                         f"doc_span and derived pointers only)")
                    s, e = p.char_start + cs, p.char_end + cs
                    if masked_text[s:e] != chunk[p.char_start:p.char_end]:
                        raise BuildError(f"offset remap failed for {item_id} {label}")
                    return span_pointer(self.source_id, item_id, sha, s, e,
                                        derivation=p.derivation)

                if job.bridge:
                    out.bridges += 1
                    self._apply_bridge(out, result, remap, resolved, main_edges, label,
                                       aliased)
                    continue
                out.refused.extend(f"{label}: {r}" for r in result.refused)
                for node in result.entities:
                    extracted_id = node.id            # relations name the extractor's id
                    ptrs = tuple(remap(p) for p in node.sources)
                    attr_ptrs = {k: remap(v) for k, v in node.attr_sources.items()}
                    node = node.model_copy(update={
                        "sources": ptrs,
                        # attribute pointers (a grounded canonical_id / identifier) are
                        # chunk-relative too
                        "attr_sources": attr_ptrs})
                    subject = self.vocab.is_subject(str(node.kind))
                    if not subject and not node.canonical_id:
                        # E2E run-2 N-16: a mention whose grounded span IS a declared business
                        # key of its kind carries that key as its canonical id
                        node = _declared_key_mention(self.store, node, masked_text,
                                                     plan.raw_text)
                    if not subject:
                        # E2E run-2 N-17: a mention the graph folds onto a node it names
                        # DIFFERENTLY keeps that node's label; the mention is cited as a
                        # derivation over the span that says it, never as a label it is not
                        node, alias_of = _as_alias(self.store, node, self.vocab,
                                                   out.name_joins)
                        if alias_of is not None:
                            aliased.add(alias_of)
                    # a subject is a document-scoped MENTION: re-sighted by its own id only,
                    # never fuzzy- or canonically merged (hard constraint 1)
                    outcome = upsert_node(self.store, node, self.vocab, id_only=subject)
                    resolved[extracted_id] = resolved[node.id] = outcome.id
                    _note_id(out, outcome.id)
                    out.entities += 1
                    if subject:
                        out.subject_mentions += 1
                    ptrs = tuple(node.sources)
                    for p in ptrs:
                        out.pointers.append({"entity": outcome.id,
                                             **_prose_pointer_record(p, node.label)})
                    # R-022 f2 #3: attribute pointers are ledgered (and so verified) too
                    for attr, p in attr_ptrs.items():
                        out.pointers.append({"entity": outcome.id, "attr": attr,
                                             **_prose_pointer_record(p, None)})
                    if anchor_id is not None:
                        ek, tied = edge_kind_choice(self.vocab, ("references",), self.doc_kind,
                                                    str(node.kind))
                        if ek is None and tied:
                            out.refused.append(f"AMBIGUOUS: {len(tied)} references-shaped edge "
                                               f"kinds {tied} admit {self.doc_kind} -> "
                                               f"{node.kind} equally; entity not linked to its "
                                               f"document (declare one in the pack ontology)")
                        elif ek is None:
                            out.refused.append(f"no references-shaped edge admits "
                                               f"{self.doc_kind} -> {node.kind}; entity not "
                                               f"linked to its document")
                        else:
                            edge = GraphEdge(id=f"{anchor_id}|{ek}|{outcome.id}",
                                             src_id=anchor_id, dst_id=outcome.id,
                                             kind=EdgeKind(ek), sources=ptrs[:1],
                                             valid_from=plan.retrieved_at,
                                             system_from=self.store.now())
                            upsert_edge(self.store, edge)
                            out.anchor_edges += 1
                            _note_id(out, edge.id)
                            # FXJ1 cross-track: the anchor edge's evidence is ledgered too
                            for p in edge.sources:
                                out.pointers.append({"entity": edge.id,
                                                     **_prose_pointer_record(p, None)})
                for edge in result.relations:
                    edge = self._write_relation(out, edge, remap, resolved, aliased)
                    main_edges.add(edge.id)
        out.write_ms += (time.perf_counter() - t0) * 1000
        return out

    def _write_relation(self, out: DocBuild, edge, remap, resolved: dict,
                        aliased: Optional[set] = None):
        from databridge_graph.merge import upsert_edge

        ptrs = tuple(remap(p) for p in edge.sources)
        src = resolved.get(edge.src_id, edge.src_id)
        dst = resolved.get(edge.dst_id, edge.dst_id)
        named = [x for x in (src, dst) if aliased and x in aliased]
        if named:
            # N-17: an endpoint this text names by another surface form — the evidence is cited
            # as a derivation over the span, which does not hold the endpoint's label
            ptrs = tuple(derived_pointer(
                f"relation evidence; {', '.join(named)} is named here by another surface form",
                [p]) for p in ptrs)
        edge = edge.model_copy(update={"sources": ptrs, "src_id": src, "dst_id": dst,
                                       "id": f"{src}|{edge.kind.value}|{dst}"})
        upsert_edge(self.store, edge)
        _note_id(out, edge.id)
        out.relations += 1
        for p in ptrs:
            out.pointers.append({"entity": edge.id, **_prose_pointer_record(p, None)})
        return edge

    def _apply_bridge(self, out: DocBuild, result, remap, resolved: dict, main_edges: set,
                      label: str, aliased: Optional[set] = None) -> None:
        """Only RELATIONS are taken from a bridge window, and only between entities this
        document's chunks already grounded — a bridge adds edges, never new nodes."""
        out.refused.extend(f"{label}: {r}" for r in result.refused if r.startswith("relation"))
        for edge in result.relations:
            src = resolved.get(edge.src_id)
            dst = resolved.get(edge.dst_id)
            if src is None or dst is None:
                out.refused.append(f"{label}: relation {edge.id!r} names an entity no chunk "
                                   f"grounded (REFUSED — a bridge only links existing entities)")
                continue
            if f"{src}|{edge.kind.value}|{dst}" in main_edges:
                continue                                          # already found within a chunk
            written = self._write_relation(out, edge, remap, resolved, aliased)
            main_edges.add(written.id)
            out.bridge_relations += 1

    # -- all three, serially ------------------------------------------------------------------------
    def build(self, *, item_id: str, sha: str, raw_text: str, masked_text: str, title: str,
              uri: str, retrieved_at: dt.datetime, override_allow: bool = False) -> DocBuild:
        plan = self.prepare(item_id=item_id, sha=sha, raw_text=raw_text, masked_text=masked_text,
                            title=title, uri=uri, retrieved_at=retrieved_at,
                            override_allow=override_allow)
        results = [self.extract(plan, job) for job in plan.jobs]
        bridges = self.bridge_jobs(plan, results)
        plan.jobs.extend(bridges)
        results += [self.extract(plan, job) for job in bridges]
        return self.apply(plan, results)


#: The "table" a listing record is captured as: one row, keyed by `item_id`, in the same snapshot
#: shape as a captured database table, so a `table_cell` pointer into it resolves the same way.
LISTING_TABLE = "mcp_listing"


def listing_capture(source_id: str, item_id: str, *, uri: str, title: str) -> str:
    """Capture what the source's LISTING said about one item (its source, full uri, title and the
    item id derived from them) as an immutable content-addressed artefact; returns its sha. The
    anchor's metadata cites this, never the document text (R-022 #5)."""
    from . import capture

    snap = {"schema": "", "table": LISTING_TABLE, "primary_key": ["item_id"],
            "foreign_keys": [],
            "columns": [{"name": c, "type": "text"} for c in ("item_id", "source", "uri",
                                                              "title")],
            "rows": [{"item_id": item_id, "source": source_ref(source_id), "uri": uri,
                      "title": title or None}]}
    data = json.dumps(snap, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return capture.put_bytes(data)


def anchor_id_for(doc_kind: str, source_id: str, item_id: str) -> str:
    """The anchor node's id — a pure function of (kind, connection, item), so removal can find
    it again without the build's records."""
    from databridge_graph.kinds import NodeKind
    from databridge_graph.schema import node_id

    return node_id(NodeKind(doc_kind), f"mcp-{source_id}-{item_id}")


def _declared_key_mention(store, node, masked_text: str, raw_text: str):
    """E2E run-2 N-16, the document side. A non-subject mention whose grounded span is EXACTLY
    a value an open node of the same kind already holds as its canonical id — a business key the
    approved mapping declared (`canonical_id` column, approved by an authenticated principal) —
    carries that value as its own canonical id, cited by that span. The graph's exact-canonical
    rule then joins it to its twin. No pattern, no per-kind rule: the declaration is the approved
    mapping and the value is the source's own. A span the screen masked is never a key (its text
    is not what the model saw), and a subject never reaches this (callers check)."""
    for p in node.sources:
        if p.kind != "doc_span" or p.char_start is None or p.char_end is None:
            continue
        value = masked_text[p.char_start:p.char_end]
        if not value or value != value.strip() or value != raw_text[p.char_start:p.char_end]:
            continue
        if store.find_by_canonical(node.kind, value) is not None:
            return node.model_copy(update={
                "canonical_id": value,
                "attr_sources": {**node.attr_sources, "canonical_id": p}})
    return node


def _declared_key_twin(store, n) -> Optional[str]:
    """E2E run-2 N-16, the table side: the ONE open node of the row's kind that has no canonical
    id and whose label is exactly the row's declared business key, and that a document grounds
    (a `doc_span` pointer). None when there is none, or more than one (never chosen by order)."""
    rows = store.fetchall(
        "SELECT DISTINCT n.id AS id FROM nodes n JOIN sources s ON s.entity_id = n.id "
        "WHERE n.kind = ? AND n.system_to IS NULL AND n.valid_to IS NULL "
        "AND n.canonical_id IS NULL AND n.label = ? "
        "AND json_extract(s.source_pointer_json, '$.kind') = 'doc_span' LIMIT 2",
        (str(n.kind), n.canonical_id))
    return rows[0]["id"] if len(rows) == 1 else None


def _note_id(out: DocBuild, gid: str) -> None:
    if gid not in out.graph_ids:
        out.graph_ids.append(gid)


def _norm_label(s: Optional[str]) -> str:
    return " ".join(str(s or "").lower().split())


def _name_join(store, node, vocab, joins: Optional[list] = None):
    """(node re-addressed to its keyed record, that record's id) for a P-05 name join, else None.

    The mention takes the record's id and label (so no stale mention node of the same id can
    capture it); each of its spans is cited as a `derived` pointer that names the record, the
    attribute whose grounded value was matched, and that the join is by name. The record's own
    name keeps its own pointer on the record. Every join is listed in `joins` for audit."""
    try:
        from databridge_graph.merge import name_prefix_join
    except ImportError:                                              # pragma: no cover
        return None
    hit = name_prefix_join(store, node, vocab)
    if hit is None:
        return None
    row, surface = hit.row, node.label
    cell = hit.pointer
    where = (f"{cell.table}.{cell.column} row {cell.row_id}" if cell.kind == "table_cell"
             else f"its {hit.attr!r}")
    why = (f"mention of {row['label']!r} (key {row['canonical_id']!r}): the text says "
           f"{surface!r}, the leading words of its grounded name {hit.name!r} ({where}); the "
           f"only keyed {node.kind} so named (candidate join by name, not by key)")
    if joins is not None:
        joins.append({"mention": node.id, "surface": surface, "into": row["id"],
                      "kind": str(node.kind), "name_attr": hit.attr,
                      "status": "candidate_join", "basis": "leading words of the record's "
                      "own grounded name; unique among keyed records of the kind",
                      "reverse": "drop the derived mention pointers from the record; the "
                                 "document's spans are unchanged"})
    return node.model_copy(update={
        "id": row["id"], "label": row["label"],
        "sources": tuple(derived_pointer(why, [p]) for p in node.sources),
        "attr_sources": {k: v for k, v in node.attr_sources.items() if k != "label"}}), row["id"]


def _as_alias(store, node, vocab, joins: Optional[list] = None):
    """(node to upsert, id it aliases | None) — E2E run-2 N-17.

    Asks the graph's OWN merge resolution (never a restatement of it) which open node this
    mention resolves to. When that node is named differently, the mention is written as an ALIAS
    of it: the label stays the existing node's (grounded by that node's own pointers), and each of
    the mention's spans is cited as a `derived` pointer ("mention of <label>: the text says
    <surface>") over the span. So no node ever shows a label that a pointer beside it does not
    hold, and the mention is still cited, exactly where it is."""
    try:
        from databridge_graph.merge import resolve_existing     # public since INT5
    except ImportError:                                              # pragma: no cover
        return node, None
    row, matched_by = resolve_existing(store, node, vocab)
    if row is None or not row["canonical_id"]:
        # E2E run-3 P-05: a mention that resolves to nothing keyed, but whose words lead exactly
        # one keyed record's own grounded name, joins THAT record (non-subject kinds only; the
        # graph decides, see `name_prefix_join`). The join is cited over the span that says it
        joined = _name_join(store, node, vocab, joins)
        if joined is not None:
            return joined
    if row is None or _norm_label(row["label"]) == _norm_label(node.label):
        return node, None
    label, surface = row["label"], node.label
    why = (f"mention of {label!r}: the text says {surface!r} (merged by the graph's "
           f"{matched_by or 'id'} rule)")
    return node.model_copy(update={
        "label": label,
        "sources": tuple(derived_pointer(why, [p]) for p in node.sources),
        "attr_sources": {k: v for k, v in node.attr_sources.items() if k != "label"}}), row["id"]


def _prose_pointer_record(p: SourcePointer, label: Optional[str], depth: int = 0) -> dict:
    """A re-minted prose pointer in the `pointers.jsonl` shape the runner's resolver reads: a
    `doc_span` leaf (with the label it must ground, for an entity's own span), or a `derived`
    pointer with its `basis` of leaves (R-022 f2 #3)."""
    if p.kind == "derived" and depth < 8:
        return {"kind": "derived", "derivation": p.derivation,
                "basis": [_prose_pointer_record(q, None, depth + 1)
                          for q in (p.derived_from or [])]}
    return {"kind": "doc_span", "sha": p.capture_digest, "start": p.char_start,
            "end": p.char_end, "label": label}


def _grounded(result, offset: int) -> dict[str, tuple[str, int, int]]:
    """node id -> (kind, doc start, doc end) of its first grounded span in one chunk's result."""
    out: dict[str, tuple[str, int, int]] = {}
    for node in result.entities:
        for p in node.sources:
            if p.kind == "doc_span" and p.char_start is not None and node.id not in out:
                out[node.id] = (str(node.kind), p.char_start + offset, p.char_end + offset)
    return out


def _reference_edge_names(vocab) -> frozenset:
    from databridge_graph.meta import EdgeMetaKind

    try:
        return frozenset(vocab.edges_binding_to(EdgeMetaKind("references")))
    except Exception:                                                # noqa: BLE001
        return frozenset()


# --- the tabular builder -------------------------------------------------------------------------
ROW_KEY = "walnut_row_key"
FK_PREFIX = "walnut_fk_"
#: The exact-merge key a row's node carries: the mapping's canonical_id column when its value
#: reached us UNMASKED, else the row key. A masked value ("[EMAIL]*****") is not an identity —
#: two different addresses of one length mask alike, and merging on that would fuse two rows.
CANON_KEY = "walnut_canonical"


def _render_pk(row: dict, pk: list[str]) -> Optional[str]:
    vals = [row.get(c) for c in pk]
    if not pk or any(v is None for v in vals):
        return None
    return ",".join(str(v) for v in vals)


def table_rows(snapshot: dict, *, source_id: str, sha: str, mask_cfg: Optional[ScreenConfig],
               redactions: list, blocked_rows: list, screen_cfg: Optional[ScreenConfig] = None,
               canonical_col: Optional[str] = None, canonical_masked: Optional[list] = None,
               overrides=None, withheld_fks: Optional[list] = None,
               candidate_col: Optional[str] = None, candidate_values: Optional[dict] = None,
               candidate_masked: Optional[list] = None):
    """Rows of one captured table as `AdapterRow`s with our own `table_cell` pointers. Adds
    `walnut_row_key` (source-qualified row identity) and `walnut_fk_<col>` (the referenced row's
    identity) so a mapping can key nodes and join edges without colliding across tables/sources.

    Only rows the screen ALLOWS are built (R-022 #1). A `block` row is withheld; a `review` row is
    withheld unless `overrides` (a `screen.OverrideSet`) holds an authenticated approval for
    exactly this row's content (`row_ref`, `row_digest`). A row whose primary key was MASKED is
    withheld too (R-022 #7): its identity would be built from the raw value the mask hides. Every
    withheld row is appended to `blocked_rows` with its `decision` (`block` | `review` | `held`)
    and a `row_ref` + `row_digest` an owner can approve. An FK whose value was masked yields no
    referenced-row identity (listed in `withheld_fks`). Every present column — NULL included —
    gets a `table_cell` pointer (R-022 #10). A row of a KEYLESS table with any masked column is
    held (`KEYLESS_ROW_MASKED`: its hash identity would be computed from the raw value), and a row
    with no columns is held (`NO_POINTER_COLUMNS`) — never silently dropped (R-022 f2 #5, #7)."""
    import hashlib

    from databridge_graph.adapter_ingest import AdapterRow

    from .screen import apply_masks, row_digest, row_ref

    schema, table = snapshot.get("schema") or "", snapshot.get("table")
    tref = f"{schema}.{table}" if schema else str(table)
    pk = list(snapshot.get("primary_key") or [])
    fks = list(snapshot.get("foreign_keys") or [])
    for ordinal, row in enumerate(snapshot.get("rows") or []):
        pk_value = _render_pk(row, pk)
        row_id = pk_value if pk_value is not None else f"#{ordinal}"
        ref = row_ref(source_id, tref, row_id)
        digest = row_digest(row)
        # a key value the screen masks is never written out raw, not even in a ledger line
        pk_sensitive = mask_cfg is not None and any(
            isinstance(row.get(c), str) and apply_masks(row[c], mask_cfg.redactions)[1]
            for c in pk)

        def withhold(decision: str, record: dict) -> None:
            blocked_rows.append({"table": tref, "row_id": ref if pk_sensitive else row_id,
                                 "row_ref": ref, "row_digest": digest, **record,
                                 "decision": decision})

        if screen_cfg is not None:
            approved = overrides is not None and overrides.allows(ref, digest)
            verdict = screen_text(json.dumps(row, sort_keys=True, default=str), screen_cfg,
                                  override_allow=approved)
            if verdict.decision != "allow":
                rec = verdict.as_record()
                if verdict.decision == "review":
                    rec["reverse"] = ("an authenticated owner approves this row "
                                      "(screen.approve_override with its row_ref and row_digest) "
                                      "and the source is re-run")
                withhold(verdict.decision, rec)
                continue
        values: dict[str, Any] = {}
        pointers: dict[str, SourcePointer] = {}
        masked_cols: set[str] = set()
        for column, value in row.items():
            cell = cell_pointer(source_id, tref, column, row_id, sha)
            if isinstance(value, str) and mask_cfg is not None:
                masked, red = apply_masks(value, mask_cfg.redactions)
                if red:
                    rules = [r["rule"] for r in red]
                    redactions.append({"table": tref,
                                       "row_id": ref if pk_sensitive else row_id,
                                       "column": column, "rules": rules})
                    value = masked
                    masked_cols.add(column)
                    # E2E F-18: the cell holds the RAW value; what is shown is a transform of it.
                    cell = derived_pointer(f"masked by the screen (rules: {', '.join(rules)})",
                                           [cell])
            values[column] = value
            pointers[column] = cell
        hidden_pk = sorted(set(pk) & masked_cols)
        if hidden_pk:
            withhold("held", {"rule": "PRIMARY_KEY_MASKED", "columns": hidden_pk,
                              "rationale": "the row's primary key holds a value the screen masks; "
                                           "an identity built from it would carry the raw value",
                              "reverse": "declare a non-sensitive key for this table in the "
                                         "mapping (or remove the value from the key column)"})
            continue
        if pk_value is not None:
            values[ROW_KEY] = f"{source_id}/{tref}/{pk_value}"
            # E2E F-18: the row key is COMPOSED from the pk cell(s); it is not what they hold.
            pointers[ROW_KEY] = derived_pointer(
                f"row identity = '{source_id}/{tref}/' + primary key ({', '.join(pk)})",
                [cell_pointer(source_id, tref, c, row_id, sha) for c in pk])
        else:
            if not pointers:
                # R-022 f2 #7: a row with no columns has nothing to cite — withheld and SAID
                withhold("held", {"rule": "NO_POINTER_COLUMNS",
                                  "rationale": "the captured row has no columns, so no value in "
                                               "it (nor its identity) could carry a pointer",
                                  "reverse": "fix the capture (the source returned an empty "
                                             "row) and re-run"})
                continue
            if masked_cols:
                # R-022 f2 #5 (founder decision 2, default HOLD — revisitable): a keyless row's
                # identity is a hash of its values; with a masked column that hash would be
                # computed from the raw value the mask hides
                withhold("held", {"rule": "KEYLESS_ROW_MASKED", "columns": sorted(masked_cols),
                                  "rationale": "the table declares no primary key, so the row's "
                                               "identity would be a hash of its values — "
                                               "including the raw value(s) the screen masks",
                                  "reverse": "declare a non-sensitive key for this table in the "
                                             "mapping (or a primary key at the source)"})
                continue
            row_hash = hashlib.sha256(json.dumps(row, sort_keys=True, default=str)
                                      .encode()).hexdigest()[:16]
            values[ROW_KEY] = f"{source_id}/{tref}/row-{row_hash}"
            pointers[ROW_KEY] = derived_pointer("row identity = hash of the row's values",
                                                list(pointers.values()))
        for fk in fks:
            col = fk.get("column")
            if col and row.get(col) is not None and fk.get("ref_table"):
                if col in masked_cols:
                    # R-022 #7: the referenced row's identity would carry the raw masked value
                    if withheld_fks is not None:
                        withheld_fks.append({"table": tref, "row_ref": ref, "column": col,
                                             "rule": "FOREIGN_KEY_MASKED"})
                    continue
                ref_table = (f"{fk.get('ref_schema')}.{fk['ref_table']}" if fk.get("ref_schema")
                             else str(fk["ref_table"]))
                values[f"{FK_PREFIX}{col}"] = f"{source_id}/{ref_table}/{row.get(col)}"
                pointers[f"{FK_PREFIX}{col}"] = derived_pointer(
                    f"referenced row identity = '{source_id}/{ref_table}/' + {col}",
                    [pointers[col]])
        if canonical_col:
            raw = row.get(canonical_col)
            if raw is not None and canonical_col in pointers and values.get(canonical_col) == raw:
                values[CANON_KEY], pointers[CANON_KEY] = raw, pointers[canonical_col]
            else:
                if raw is not None and canonical_masked is not None:
                    canonical_masked.append({"table": tref, "row_id": row_id,
                                             "column": canonical_col})
                values[CANON_KEY], pointers[CANON_KEY] = values[ROW_KEY], pointers[ROW_KEY]
        if candidate_col and candidate_values is not None:
            # a SUBJECT's declared identifier (mapping `candidate_identifier`): never an identity
            # or an attribute — only the lookup value that OFFERS another record as a candidate.
            # A masked value is not used (it would match on the raw value the mask hides).
            raw = row.get(candidate_col)
            if raw is not None and candidate_col in masked_cols:
                if candidate_masked is not None:
                    candidate_masked.append({"table": tref, "row_id": ref if pk_sensitive
                                             else row_id, "column": candidate_col})
            elif raw is not None:
                candidate_values[values[ROW_KEY]] = str(raw)
        yield AdapterRow(values, pointers)


def rows_to_graph(rows, **kw) -> None:
    """The ONE place this package reaches into `estate_seed`'s row→node/edge builder. It prefers a
    public `ingest_rows` (the proposed stable name — see the P report) and falls back to the
    private `_ingest_rows` it aliases, so the day `packages/graph` publishes the name nothing here
    changes, and the day the private one is renamed this is the only line that breaks."""
    from databridge_graph import estate_seed as es

    fn = getattr(es, "ingest_rows", None) or getattr(es, "_ingest_rows")
    fn(rows, **kw)


def reconcile_edge_validity(nodes, edges) -> list:
    """Lift every edge's `valid_from` to the later of its own and both endpoints' — a relationship
    cannot begin before both things it relates exist. (Same rule as `estate_seed`'s private
    helper, restated here so this package depends on no private name for it.)"""
    starts = {n.id: n.valid_from for n in nodes if n.valid_from is not None}
    out = []
    for edge in edges:
        bounds = [d for d in (starts.get(edge.src_id), starts.get(edge.dst_id)) if d is not None]
        if edge.valid_from is not None:
            bounds.append(edge.valid_from)
        latest = max(bounds) if bounds else edge.valid_from
        out.append(edge if latest == edge.valid_from
                   else edge.model_copy(update={"valid_from": latest}))
    return out


def build_tables(store, vocab, *, source_id: str, tables: list[tuple[dict, str, dict]],
                 retrieved_at: dt.datetime, mask_cfg: Optional[ScreenConfig],
                 screen_cfg: Optional[ScreenConfig] = None, overrides=None,
                 item_ids: Optional[dict] = None) -> dict:
    """`tables`: (snapshot, sha, table_mapping) for every table with an APPROVED mapping. Builds
    all rows first (so FK edges between tables resolve), drops dangling edges (counted), then
    upserts. Returns counts + the pointers minted, for verification.

    `overrides` (`screen.load_overrides(source)`): authenticated approvals that admit a REVIEW
    row whose content digest they name. Without it every review row is withheld (fail closed).
    `blocked_rows` lists EVERY withheld row with its `decision` (block | review | held);
    `rows_blocked` / `rows_review` / `rows_held` count them apart.

    `pointers` ledgers EVERY pointer written (entity and attribute pointers of each node, the
    evidence of each edge): `table_cell` leaves and `derived` pointers with their `basis`, so the
    runner's verify resolves each derived pointer through its leaves. `item_ids` (capture sha →
    item id) stamps each entry with the table item it came from."""
    from databridge_graph import estate_seed as es
    from databridge_graph.merge import upsert_edge, upsert_node

    report = es.BuildReport()
    nodes: dict = {}
    raw_keys: dict = {}
    edges: dict = {}
    redactions: list = []
    blocked_rows: list = []
    canonical_masked: list = []
    candidate_values: dict[str, str] = {}
    candidate_masked: list = []
    withheld_fks: list = []
    fk_targets: dict[str, tuple[str, str, str]] = {}
    seed = es.SeedMap(pack="mcp_ingest", root=Path("."), system_time=store.now(),
                      default_valid_from=retrieved_at, datasets=())
    for snapshot, sha, tmap in tables:
        tref = (f"{snapshot.get('schema')}.{snapshot.get('table')}" if snapshot.get("schema")
                else str(snapshot.get("table")))
        node = tmap["node"]
        dataset = es.DatasetSpec(
            id=tref, reader="adapter", table=tref, paths=(), trust=Trust.UNTRUSTED,
            row_key="{" + ROW_KEY + "}",
            nodes=(es.NodeSpec(kind=node["kind"], key="{" + ROW_KEY + "}",
                               label=node.get("label") or "{" + ROW_KEY + "}",
                               sources=tuple(node.get("sources") or [ROW_KEY]),
                               canonical_id=CANON_KEY if node.get("canonical_id") else ROW_KEY,
                               attrs=dict(node.get("attrs") or {})),),
            edges=tuple(es.EdgeSpec(kind=e["kind"], src=e["src"], dst=e["dst"],
                                    evidence=tuple(e.get("evidence") or ()))
                        for e in tmap.get("edges") or []))
        rows = list(table_rows(snapshot, source_id=source_id, sha=sha, mask_cfg=mask_cfg,
                               redactions=redactions, blocked_rows=blocked_rows,
                               screen_cfg=screen_cfg, canonical_col=node.get("canonical_id"),
                               canonical_masked=canonical_masked, overrides=overrides,
                               withheld_fks=withheld_fks,
                               candidate_col=node.get("candidate_identifier"),
                               candidate_values=candidate_values,
                               candidate_masked=candidate_masked))
        rows_to_graph(rows, dataset=dataset, seed=seed, source_id=source_ref(source_id),
                      vocab=vocab, nodes=nodes, raw_keys=raw_keys, edges=edges, report=report)
        # endpoint id -> (kind, referenced table, referenced row id): how a later run finds a
        # referenced row that merged onto another node in an earlier run (`_resolve_endpoint`)
        for e in tmap.get("edges") or []:
            for end in (e["src"], e["dst"]):
                kind_name, _, tmpl = end.partition(":")
                col = tmpl.strip("{}")
                if not col.startswith(FK_PREFIX):
                    continue
                fk = next((f for f in snapshot.get("foreign_keys") or []
                           if f.get("column") == col[len(FK_PREFIX):]), None)
                if fk is None or not fk.get("ref_table"):
                    continue
                ref = (f"{fk.get('ref_schema')}.{fk['ref_table']}" if fk.get("ref_schema")
                       else str(fk["ref_table"]))
                for r in rows:
                    key = es._render(tmpl, r) if hasattr(es, "_render") else None
                    raw = r.get(col[len(FK_PREFIX):])
                    if key is not None and raw is not None:
                        fk_targets[f"{kind_name}:{key}"] = (kind_name, ref, str(raw))
    node_list = list(nodes.values())
    # node id -> its row's declared subject identifier (raw_keys: node id -> rendered row key)
    candidate_for = {nid: candidate_values[rk] for nid, rk in raw_keys.items()
                     if rk in candidate_values}
    pointers = []
    merged: list[dict] = []
    candidate_links: list[dict] = []
    id_map: dict[str, str] = {}
    with store_batch(store):
        # Nodes FIRST, so every edge endpoint is resolved against what the store actually holds
        # after the merge (E2E F-03: an edge kept the row-key id of a node that had merged onto a
        # document node's id, and dangled). Identity rule: a SUBJECT row is never merged onto
        # another record (id-only: re-sighting its own row); a non-subject merges only on an
        # EXACT canonical id, never on label similarity.
        for n in node_list:
            outcome, cand = _upsert_row_node(store, n, vocab, upsert_node,
                                             candidate_value=candidate_for.get(n.id))
            id_map[n.id] = outcome.id
            if outcome.id != n.id:
                merged.append({"row_node": n.id, "into": outcome.id, "kind": str(n.kind),
                               "matched_by": outcome.matched_by})
            if cand is not None:
                candidate_links.append(cand)
            for p in [*n.sources, *n.attr_sources.values()]:
                rec = _ledger_pointer(p, item_ids)
                if rec is not None:
                    pointers.append({"entity": outcome.id, **rec})
        kept, dangling_ids, ambiguous = [], [], []
        for edge in reconcile_edge_validity(node_list, list(edges.values())):
            src = _resolve_endpoint(store, edge.src_id, id_map, fk_targets, source_id, ambiguous)
            dst = _resolve_endpoint(store, edge.dst_id, id_map, fk_targets, source_id, ambiguous)
            if src is None or dst is None:
                dangling_ids.append(edge.id)
                continue
            if (src, dst) != (edge.src_id, edge.dst_id):
                edge = edge.model_copy(update={"id": f"{src}|{edge.kind.value}|{dst}",
                                               "src_id": src, "dst_id": dst})
            kept.append(edge)
        for e in kept:
            upsert_edge(store, e)
            for p in e.sources:
                rec = _ledger_pointer(p, item_ids)
                if rec is not None:
                    pointers.append({"entity": e.id, **rec})
    return {"nodes": len(node_list), "edges": len(kept), "dangling_edges": len(dangling_ids),
            "dangling_edge_ids": dangling_ids, "merged": merged,
            "candidate_links": candidate_links,
            "rows_blocked": sum(1 for b in blocked_rows if b.get("decision") == "block"),
            "rows_review": sum(1 for b in blocked_rows if b.get("decision") == "review"),
            "rows_held": sum(1 for b in blocked_rows if b.get("decision") == "held"),
            "blocked_rows": blocked_rows, "withheld_fks": withheld_fks,
            "ambiguous_endpoints": ambiguous,
            "canonical_masked": canonical_masked, "candidate_masked": candidate_masked,
            "redactions": redactions, "pointers": pointers, "report": report.as_record()}


def _ledger_pointer(p: SourcePointer, item_ids: Optional[dict], depth: int = 0) -> Optional[dict]:
    """A pointer in the `pointers.jsonl` shape the runner's resolver reads: a `table_cell` leaf, or
    a `derived` pointer with its `basis` (leaves, recursively). Kinds this builder never mints
    return None. `item_id` comes from the leaf's capture sha (the first leaf for a derivation)."""
    if p.kind == "table_cell":
        rec = {"kind": "table_cell", "sha": p.capture_digest, "table": p.table,
               "column": p.column, "row_id": p.row_id}
        iid = (item_ids or {}).get(p.capture_digest)
        if iid:
            rec["item_id"] = iid
        return rec
    if p.kind == "derived" and depth < 8:
        basis = [b for b in (_ledger_pointer(q, item_ids, depth + 1)
                             for q in (p.derived_from or [])) if b is not None]
        if not basis:
            return None
        rec = {"kind": "derived", "derivation": p.derivation, "basis": basis}
        iid = next((b["item_id"] for b in basis if b.get("item_id")), None)
        if iid:
            rec["item_id"] = iid
        return rec
    return None


def _upsert_row_node(store, n, vocab, upsert_node, candidate_value: Optional[str] = None):
    """Upsert one row's node under the ingest identity rule. Returns (outcome, candidate|None).

    * a SUBJECT kind is upserted `id_only` — it re-sights its own row and NOTHING else. When its
      declared identifier equals another open record's, that record is offered as a CANDIDATE
      link (`requires_human_selection`), never merged (CLAUDE.md hard constraint 1, spine D4b);
    * any other kind merges only on the same id or an EXACT canonical id — never by label
      similarity (a fuzzy merge of a row onto a document mention is a guess, not a key)."""
    subject = vocab.is_subject(str(n.kind))
    exact = None
    # a subject's lookup value is its declared identifier (`candidate_identifier`), which is
    # never written onto the node; any other kind's is its exact-merge key
    lookup = (candidate_value or n.canonical_id) if subject else n.canonical_id
    if store.open_node_row(n.id) is None and lookup:
        exact = store.find_by_canonical(n.kind, lookup)
    if subject:
        outcome = upsert_node(store, n, vocab, id_only=True)
        cand = None
        if exact is not None and exact["id"] != outcome.id:
            cand = {"row_node": outcome.id, "candidate": exact["id"], "kind": str(n.kind),
                    "basis": "exact declared identifier (canonical_id) — value withheld",
                    "requires_human_selection": True, "status": "proposed",
                    "reverse": "a named human confirms or rejects the match through the "
                               "subject's verified channel; nothing is merged until then"}
        return outcome, cand
    if exact is None and n.canonical_id and store.open_node_row(n.id) is None:
        twin = _declared_key_twin(store, n)
        if twin is not None:
            # E2E run-2 N-16: the row re-sights the document's mention of its declared key. The
            # mention keeps its label (its spans say it — N-17); the row adds its key and facts.
            existing = store.open_node_row(twin)
            joined = n.model_copy(update={
                "id": twin, "label": existing["label"],
                "attr_sources": {k: v for k, v in n.attr_sources.items() if k != "label"}})
            outcome = upsert_node(store, joined, vocab, id_only=True)
            outcome.matched_by = "declared_key"
            return outcome, None
    return upsert_node(store, n, vocab, id_only=exact is None), None


def _resolve_endpoint(store, node_ref: str, id_map: dict, fk_targets: dict,
                      source_id: str, ambiguous: Optional[list] = None) -> Optional[str]:
    """The id an edge endpoint names in the store NOW: this run's node (re-pointed if it merged),
    an open node of that id, or — for a reference to a row built in an EARLIER run that merged
    onto another node then — the open node whose pointers cite that row's own cells.

    The row's cells are found at ANY depth of a stored pointer (R-022 #6): a row node's default
    source is the `derived` row-key pointer, whose `derived_from` holds the primary-key
    `table_cell`s — a top-level-only lookup missed every such merge, which the merged node keeps
    (upsert appends the row's pointers to the node it merged onto). That is the store's persistent
    merge record. Two or more open nodes citing the row is AMBIGUOUS: refused (None) and listed in
    `ambiguous`, never resolved by order. None: dangling."""
    if node_ref in id_map:
        return id_map[node_ref]
    if store.open_node_row(node_ref) is not None:
        return node_ref
    target = fk_targets.get(node_ref)
    if target is None:
        return None
    kind, table, row_id = target
    rows = store.fetchall(
        "SELECT DISTINCT n.id AS id FROM sources s JOIN nodes n ON n.id = s.entity_id "
        "AND n.system_to IS NULL AND n.valid_to IS NULL, json_tree(s.source_pointer_json) t "
        "WHERE n.kind = ? AND s.source_pointer_json LIKE ? AND t.type = 'object' "
        "AND json_extract(t.value, '$.kind') = 'table_cell' "
        "AND json_extract(t.value, '$.source_id') = ? "
        "AND json_extract(t.value, '$.table') = ? "
        "AND json_extract(t.value, '$.row_id') = ? ORDER BY n.id LIMIT 3",
        (kind, "%" + table.replace("%", "") + "%", source_ref(source_id), table, row_id))
    ids = [r["id"] for r in rows]
    if len(ids) > 1:
        if ambiguous is not None:
            ambiguous.append({"endpoint": node_ref, "table": table, "candidates": ids,
                              "rule": "AMBIGUOUS_MERGED_ROW",
                              "reverse": "the referenced row is cited by more than one open "
                                         "node; a person decides which one it is"})
        return None
    return ids[0] if ids else None


__all__ = ["chunk_spans", "span_pointer", "cell_pointer", "derived_pointer", "ProseBuilder",
           "DocBuild", "DocPlan", "ChunkJob", "store_batch", "anchor_id_for", "rows_to_graph",
           "reconcile_edge_validity", "BRIDGE_MAX_GAP", "BRIDGE_MAX_SPAN", "BRIDGE_PAD",
           "build_tables", "table_rows", "document_kind", "edge_kind_for", "edge_kind_choice",
           "listing_capture", "LISTING_TABLE", "CHUNK_CHARS",
           "CHUNK_OVERLAP", "ROW_KEY", "FK_PREFIX", "CANON_KEY", "BuildError", "source_ref"]

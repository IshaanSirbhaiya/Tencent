"""What the `/intelligence` surface is allowed to say — one place, applied to every payload.

The page's contract: **structural counts only, no subject naming or ranking, no minutes, no
money.** It is enforced here, once, for everything the page emits — the figures, the agent's
citations, and the drill-down behind both.

## The naming rule is derived from the vocabulary, never written down per kind

It used to be three hand-kept lists of one vertical's kind names (which labels print, which get a
handle, which are withheld) plus an allow-list of that vertical's edge names. A pack the engine had
never seen was therefore either invisible (every kind unclassified) or — had the default been the
other way — fully exposed. The rule now comes from `subject_exposure.ExposurePolicy`, which reads
the bound packs' `subject_kinds` and their claim edges:

* **withheld** — a node whose kind is a pack SUBJECT kind, or a kind no bound pack declares. It
  appears on this page in no form, not even as a handle: a stable pseudonym is still a way to follow
  one person across a page whose contract says it does not know who they are.
* **handle** — a node that carries a subject's information (a document that can claim a subject,
  or anything grounded in material that names one). Reported as ``<kind> · <opaque handle>``: the
  kind is closed vocabulary, the handle is stable, drillable and names nothing. A filename can be
  ``Alice_Tan_referral.pdf``; the rule is about provenance, not phrasing.
* **label** — everything else. Ordinary knowledge prints by its label.

## A dedicated structural traversal

The drill-down walks every bound edge kind EXCEPT the claim and determination meta-kinds (``ABOUT``
/ ``RESOLVED_TO``), and never steps onto a withheld node — so a subject is unreachable from here at
any depth, and no edge attribute (a match score lives on a claim edge) is emitted at all.

## Bounds that refuse rather than truncate

A truncated count is a **wrong number presented as a right one**, and every figure on this page is
a count. :func:`check_graph_bounds` refuses at the door; the traversal refuses past its own size.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections import deque
from typing import Any, Iterable, Optional

from databridge_core import SourcePointer
from databridge_graph.meta import ATTESTED_META_KINDS, CLAIM_META_KINDS, NodeMetaKind
from databridge_graph.packs import active_vocabulary

from .subject_exposure import ExposurePolicy, entity_key

# --- bounds -----------------------------------------------------------------------------
#: The DETAIL envelope. Within it the figures list their members; beyond it the figures are still
#: served EXACT, without member lists (C-4), while the agent and the drill-down refuse rather than
#: walk part of a graph (`check_graph_bounds`).
MAX_GRAPH_NODES = 5_000
MAX_GRAPH_EDGES = 20_000
#: One drill-down. `depth` bounds hops; these bound degree and response size, which it does not.
MAX_SUBGRAPH_NODES = 250
MAX_SUBGRAPH_EDGES = 750


class SurfaceTooLarge(RuntimeError):
    """A read that would exceed this surface's bounds (or reach a withheld entity). Carries
    staff-facing text and a code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --- handles ------------------------------------------------------------------------------
#: Handle prefix = the first three letters of the node's META-kind — so a handle says what shape
#: of thing it names without naming any pack's kind, and a document's handle is the same whichever
#: pack called it a document. An undeclared kind gets the entity prefix.
def _prefix(meta: Optional[NodeMetaKind]) -> str:
    return (meta or NodeMetaKind.ENTITY).value[:3]


_HANDLE = re.compile(r"^[a-z]{3}-[0-9a-f]{10}$")
_HANDLE_CHARS = 10


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_HANDLE_CHARS]


def _meta_of_kind(kind: str) -> Optional[NodeMetaKind]:
    decl = active_vocabulary().node_kinds.get(kind)
    return decl.binds_to if decl is not None else None


def entity_handle(entity_id: str) -> str:
    """A stable, opaque, shape-prefixed handle for a graph entity id.

    Deterministic and stateless on purpose: a handle in an answer must still resolve after a
    restart, and a server-side alias table would be one more thing to keep and to leak. A
    document-shaped node's handle is computed from its KEY alone, so it equals
    :func:`document_handle` of the document id a pointer carries.
    """
    kind, sep, key = (entity_id or "").partition(":")
    if not sep:
        return f"{_prefix(None)}-{_digest(f'ref:{entity_id or ""}')}"
    meta = _meta_of_kind(kind)
    if meta is NodeMetaKind.DOCUMENT:
        return document_handle(key)
    return f"{_prefix(meta)}-{_digest(f'{kind}:{key}')}"


def document_handle(document_id: str) -> str:
    """The handle a document id gets — the SAME one its document-shaped node gets."""
    return f"{_prefix(NodeMetaKind.DOCUMENT)}-{_digest(f'doc:{document_id or ""}')}"


def source_handle(source_id: str) -> str:
    return f"src-{_digest(f'src:{source_id or ""}')}"


def looks_like_handle(token: str) -> bool:
    return bool(_HANDLE.match(token or ""))


# --- one snapshot per request -------------------------------------------------------------------
class EvidenceUnavailable(RuntimeError):
    """A snapshot row's OWN version pointers could not be read. Raised, never papered over with
    the current version's pointers (R-019 t08b #1)."""


class SnapshotView:
    """The three reads every consumer here uses, answered from ONE materialised snapshot.

    ``open_nodes`` / ``open_edges`` are the snapshot's rows (for ``as_of=None`` that IS the open
    graph); ``sources_for`` returns the pointers of the exact VERSION the snapshot holds for that
    id. Built once per request, it is what the naming policy, the bounds check, the traversal and
    the citations all read, so none of them can reach the live graph behind a rewound answer
    (R-019 t08b #1, #3, #5, #9). Read-only: it holds no write method and no ``fetchall``.
    """

    def __init__(self, store: Any, snapshot: Any) -> None:
        self._store = store
        self.snapshot = snapshot
        self._memo: dict[str, list] = {}

    @property
    def as_of(self) -> Optional[dt.datetime]:
        return self.snapshot.as_of

    @property
    def store(self) -> Any:
        """The reader the snapshot was taken from — for version-addressed pointer reads only."""
        return self._store

    def open_nodes(self) -> list:
        return list(self.snapshot.nodes)

    def open_edges(self) -> list:
        return list(self.snapshot.edges)

    def sources_for(self, entity_id: str, **_ignored: Any) -> list:
        if entity_id not in self._memo:
            from databridge_graph.analytics import row_sources

            row = self.snapshot.row(entity_id)
            if row is None:
                self._memo[entity_id] = []
            else:
                pointers = row_sources(self._store, row)
                if pointers is None:
                    raise EvidenceUnavailable(
                        f"the pointers of the version of {entity_id!r} this snapshot holds "
                        f"could not be read")
                self._memo[entity_id] = pointers
        return list(self._memo[entity_id])


def snapshot_view(store: Any, as_of: Optional[dt.datetime]) -> SnapshotView:
    """Materialise the requested snapshot once (``analytics.take_snapshot``) and wrap it. Raises
    ``analytics.SnapshotIncomplete`` for a historical read the reader cannot rewind."""
    from databridge_graph.analytics import take_snapshot

    if isinstance(store, SnapshotView):
        return store
    return SnapshotView(store, take_snapshot(store, as_of))


class SurfaceNames:
    """The naming policy, memoised over one request's view of the graph.

    Built from the same three-method read-only view the agent's tools hold, so the policy cannot
    reach anything the tools cannot and there is no second path to the store.
    """

    def __init__(self, view: Any) -> None:
        self._view = view
        self.policy = ExposurePolicy(view)
        self._display: dict[str, tuple[Optional[str], bool]] = {}
        self._by_handle: Optional[dict[str, str]] = None
        #: Rows of a rewound snapshot that are not open now (`adopt`).
        self._historic: dict[str, Any] = {}

    def adopt(self, rows: Iterable[Any]) -> None:
        """Make versions from an ``as_of`` snapshot that are no longer OPEN classifiable here.

        Without this a closed subject version in a rewound figure has no row, reads as "not a
        node", and is published as a handle — the one thing this page never does for a subject.
        Such an entity is classified by its kind and otherwise FAILS CLOSED: it prints as
        ``<kind> · <handle>``, never by a label nobody re-checked against today's pointers."""
        open_rows = self.rows()
        for row in rows:
            entity_id = row["id"]
            if entity_id not in open_rows and entity_id not in self._historic:
                self._historic[entity_id] = row
                self._display.pop(entity_id, None)
                self._by_handle = None

    # -- the node table ----------------------------------------------------------------
    def rows(self) -> dict[str, Any]:
        return self.policy.rows()

    def kind_of(self, entity_id: str) -> Optional[str]:
        row = self.rows().get(entity_id) or self._historic.get(entity_id)
        return None if row is None else row["kind"]

    def is_withheld(self, entity_id: str) -> bool:
        """True for a subject, and for a kind no bound pack declares (it cannot be classified,
        so it cannot be shown to be safe)."""
        kind = self.kind_of(entity_id)
        if kind is None:
            return False              # not a node — a raw pointer id; handled by projection
        return (kind in self.policy.subject_kinds
                or kind not in self.policy.vocabulary.node_kinds)

    # -- naming ------------------------------------------------------------------------
    def public_id(self, entity_id: str) -> str:
        """The id this page uses for an entity: its own id when its label prints, else a handle."""
        _text, handled = self.display(entity_id)
        return entity_handle(entity_id) if handled else entity_id

    def display(self, entity_id: str) -> tuple[Optional[str], bool]:
        """``(text, is_a_handle)`` — or ``(None, True)`` for an entity this page never reports."""
        if entity_id not in self._display:
            self._display[entity_id] = self._compute_display(entity_id)
        return self._display[entity_id]

    def _compute_display(self, entity_id: str) -> tuple[Optional[str], bool]:
        row = self.rows().get(entity_id)
        if row is None:
            historic = self._historic.get(entity_id)
            if historic is None:
                return entity_handle(entity_id), True
            if self.is_withheld(entity_id):
                return None, True
            return f"{historic['kind']} · {entity_handle(entity_id)}", True
        if self.is_withheld(entity_id):
            return None, True
        if self.policy.entity_exposed(entity_id):
            # The kind is closed vocabulary — ours, not a document's — and says what sort of thing
            # is waiting; the handle is what a reader clicks. Nothing an outsider wrote is repeated.
            return f"{row['kind']} · {entity_handle(entity_id)}", True
        return row["label"], False

    # -- pointers ----------------------------------------------------------------------
    def project(self, pointer: SourcePointer) -> SourcePointer:
        """Project a pointer onto what this page may publish.

        A pointer into material that names no subject passes through whole — it is ordinary
        provenance. One that does is rebuilt from an allow-list of fields with every name replaced
        by a handle, so a field added to `SourcePointer` later cannot travel through unnoticed.
        """
        exposed = self.policy.pointer_exposed(pointer)       # walks a derived pointer's inputs
        if pointer.kind == "derived":
            # Exposure is decided BEFORE the derived case (R-017 t08 #4): a derivation is free text
            # and a non-node source id is a raw string, and either can name the person the inputs
            # below are projected to hide. Over subject material both are replaced; only the
            # recursively projected inputs remain.
            if not exposed:
                return SourcePointer(
                    source_id=self._pointer_id(pointer.source_id), kind="derived",
                    derivation=pointer.derivation,
                    derived_from=[self.project(p) for p in (pointer.derived_from or [])])
            source_id = (self.public_id(pointer.source_id) if pointer.source_id in self.rows()
                         else source_handle(pointer.source_id))
            return SourcePointer(
                source_id=source_id, kind="derived",
                derivation=("derived from material that carries a subject's information; this "
                            "surface publishes the inputs' shape, not the derivation's text"),
                derived_from=[self.project(p) for p in (pointer.derived_from or [])])
        if not exposed:
            return pointer
        if pointer.kind == "doc_span":
            return SourcePointer(
                source_id=source_handle(pointer.source_id), kind="doc_span",
                document_id=document_handle(pointer.document_id or pointer.source_id),
                char_start=pointer.char_start, char_end=pointer.char_end)
        if pointer.kind == "table_cell":
            # The table and column are the part that makes the citation mean anything; the source
            # and the ROW are the part that names somebody.
            return SourcePointer(
                source_id=source_handle(pointer.source_id), kind="table_cell",
                table=pointer.table, column=pointer.column,
                row_id=_digest(str(pointer.row_id)))
        return SourcePointer(
            source_id=source_handle(pointer.source_id), kind="derived",
            derivation=f"a {pointer.kind} pointer; this surface publishes only that one exists")

    def _pointer_id(self, source_id: str) -> str:
        if source_id in self.rows():
            return self.public_id(source_id)
        return source_id

    # -- resolving a handle back to an entity ------------------------------------------
    def resolve(self, token: str) -> Optional[str]:
        """A handle or a raw entity id → the entity id, or ``None``."""
        token = (token or "").strip()
        if not token:
            return None
        if token in self.rows() or token in self._historic:
            return token
        if not looks_like_handle(token):
            return None
        if self._by_handle is None:
            self._by_handle = {entity_handle(entity_id): entity_id
                               for entity_id in (*self.rows(), *self._historic)}
        return self._by_handle.get(token)

    def source_display(self, source: str, documents: Iterable[str]) -> str:
        """A source's name, or a handle when any document it holds carries a subject's details —
        a clinical document's source id is its filename."""
        if any(self.policy.entity_exposed(d) for d in documents):
            return source_handle(source)
        return source


# --- bounds at the door -------------------------------------------------------------------
def check_graph_bounds(view: Any) -> None:
    """Refuse before any figure is computed if the graph is outside this surface's envelope."""
    nodes = len(view.open_nodes())
    edges = len(view.open_edges())
    if nodes > MAX_GRAPH_NODES or edges > MAX_GRAPH_EDGES:
        raise SurfaceTooLarge(
            "GRAPH_TOO_LARGE",
            f"this graph holds {nodes} nodes and {edges} relationships, beyond the "
            f"{MAX_GRAPH_NODES}/{MAX_GRAPH_EDGES} this page is built for. Every figure here is a "
            f"count, so a truncated answer would be a wrong number rather than a short one — it "
            f"is withheld instead.")


def within_detail_envelope(view: Any) -> bool:
    """Whether the figures may LIST their members for this snapshot (C-4). Past the envelope the
    figures are still served, exact, without member lists; nothing is refused for size."""
    return (len(view.open_nodes()) <= MAX_GRAPH_NODES
            and len(view.open_edges()) <= MAX_GRAPH_EDGES)


# --- the traversal ------------------------------------------------------------------------
def _valid_at(row: Any, as_of: Optional[dt.datetime]) -> bool:
    """The same validity rule `analytics.compute` applies, so a drill-down matches its figure."""
    if as_of is None:
        return True

    def parse(value: Optional[str]) -> Optional[dt.datetime]:
        if not value:
            return None
        parsed = dt.datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)

    valid_from, valid_to = parse(row["valid_from"]), parse(row["valid_to"])
    if valid_from and valid_from > as_of:
        return False
    if valid_to and valid_to <= as_of:
        return False
    return True


def structural_edge_kinds() -> frozenset[str]:
    """Every bound edge kind except those binding to a claim or a determination of identity."""
    vocab = active_vocabulary()
    return frozenset(n for n, d in vocab.edge_kinds.items()
                     if d.binds_to not in (CLAIM_META_KINDS | ATTESTED_META_KINDS))


def structural_neighbors(view: Any, names: SurfaceNames, root_id: str, *, depth: int = 1,
                         as_of: Optional[dt.datetime] = None) -> dict:
    """The drill-down this page serves: structure only, no subject, no ranking, bounded.

    ``view`` should be the request's :class:`SnapshotView` (its snapshot wins over ``as_of``);
    any other reader is snapshotted here, once, at ``as_of``."""
    if names.is_withheld(root_id):
        raise SurfaceTooLarge(
            "NOT_ON_THIS_SURFACE",
            "that entity is a subject (or of a kind no bound pack declares). This page counts "
            "identity work and never performs any: it reports how many candidates a document "
            "matched, and a named person settles which one, in person.")

    allowed = structural_edge_kinds()
    # The SAME snapshot `analytics.compute` counts over (every version, R-017 t08 #6), so a
    # drill-down matches its figure after a version closed — and, read through the SnapshotView,
    # every pointer counted below is the pointer of THAT version (R-019 t08b #1).
    view = snapshot_view(view, as_of)
    snap_nodes, snap_edges = view.open_nodes(), view.open_edges()
    names.adopt(snap_nodes)
    edges = [row for row in snap_edges if row["kind"] in allowed]
    by_node: dict[str, list[Any]] = {}
    for row in edges:
        if names.is_withheld(row["src_id"]) or names.is_withheld(row["dst_id"]):
            continue
        by_node.setdefault(row["src_id"], []).append(row)
        by_node.setdefault(row["dst_id"], []).append(row)

    seen_nodes: set[str] = {root_id}
    seen_edges: set[str] = set()
    out_edges: list[Any] = []
    frontier: deque[tuple[str, int]] = deque([(root_id, 0)])
    while frontier:
        node_id, hops = frontier.popleft()
        if hops >= depth:
            continue
        for row in by_node.get(node_id, []):
            if row["id"] not in seen_edges:
                seen_edges.add(row["id"])
                out_edges.append(row)
            other = row["dst_id"] if row["src_id"] == node_id else row["src_id"]
            if other not in seen_nodes:
                seen_nodes.add(other)
                frontier.append((other, hops + 1))
        _check_subgraph_bounds(len(seen_nodes), len(out_edges), depth)
    _check_subgraph_bounds(len(seen_nodes), len(out_edges), depth)

    node_rows = [row for row in snap_nodes if row["id"] in seen_nodes]
    live = {row["id"] for row in node_rows}
    out_edges = [row for row in out_edges if row["src_id"] in live and row["dst_id"] in live]

    nodes = []
    for row in node_rows:
        text, handled = names.display(row["id"])
        if text is None:                       # unreachable via the traversal; belt and braces
            continue
        nodes.append({
            "id": names.public_id(row["id"]),
            "kind": row["kind"],
            "label": text,
            # Only when the label itself prints — a handled entity's canonical id can be the very
            # filename the handle exists to stop repeating.
            "canonical_id": None if handled else row["canonical_id"],
            "valid_from": row["valid_from"],
            "n_sources": len(_sources(view, row["id"])),
        })
    return {
        "root": names.public_id(root_id),
        "nodes": nodes,
        # No `attrs`, on either side: publishing attributes at all is how a subject's details
        # were reachable before.
        "edges": [{
            "id": _digest(row["id"]),
            "source": names.public_id(row["src_id"]),
            "target": names.public_id(row["dst_id"]),
            "kind": row["kind"],
            "valid_from": row["valid_from"],
            "n_sources": len(_sources(view, row["id"])),
        } for row in out_edges],
    }


def _check_subgraph_bounds(nodes: int, edges: int, depth: int) -> None:
    if nodes > MAX_SUBGRAPH_NODES or edges > MAX_SUBGRAPH_EDGES:
        raise SurfaceTooLarge(
            "SUBGRAPH_TOO_LARGE",
            f"this traversal reaches more than {MAX_SUBGRAPH_NODES} entities or "
            f"{MAX_SUBGRAPH_EDGES} relationships at depth {depth}. Showing part of it would look "
            f"like the whole of it, so it is refused — ask for depth 1.")


def _sources(view: Any, entity_id: str) -> list[SourcePointer]:
    return list(view.sources_for(entity_id))


def subgraph_sources(view: Any, names: SurfaceNames, subgraph: dict) -> dict[str, list[dict]]:
    """The projected pointers behind every node in a traversal, keyed by its public id."""
    out: dict[str, list[dict]] = {}
    for node in subgraph["nodes"]:
        entity_id = names.resolve(node["id"]) or node["id"]
        out[node["id"]] = [names.project(p).model_dump(mode="json", exclude_none=True)
                           for p in _sources(view, entity_id)]
    return out


def subgraph_edge_sources(view: Any, names: SurfaceNames,
                          subgraph: dict) -> dict[str, list[dict]]:
    """The projected pointers behind every RELATIONSHIP in a traversal, keyed by its public id.

    An edge is a fact of its own ("A issued B"); publishing its kind and endpoints with pointers
    for the nodes only was a relationship emitted without a SourcePointer (R-019 t08b #2). The raw
    edge id is recovered from the same snapshot the traversal read, so the pointers are those of
    the version the traversal emitted."""
    raw = {_digest(row["id"]): row["id"] for row in view.open_edges()}
    out: dict[str, list[dict]] = {}
    for edge in subgraph.get("edges", []):
        edge_id = raw.get(edge["id"])
        out[edge["id"]] = ([] if edge_id is None else
                           [names.project(p).model_dump(mode="json", exclude_none=True)
                            for p in _sources(view, edge_id)])
    return out


# --- the figures --------------------------------------------------------------------------
#: Why a figure can be counted but not listed. Stated in the payload, not only in the UI.
MEMBERS_WITHHELD = ("this figure counts subject rows; listing them would name people, so the "
                    "count is reported without its members (CLAUDE.md hard constraint 1)")

#: Every `Count`-shaped figure in the analytics payload.
COUNT_FIGURES = ("subjects", "ambiguity", "unlinked_documents", "conflicts",
                 "uncovered_references", "cross_source", "orphans", "evidence_unavailable")

#: Said when the graph holds kinds no bound pack declares. Their NAMES are not published.
CLOSED_VOCABULARY_VIOLATION = (
    "the graph holds entities of kinds no bound pack declares. The ontology is closed, so those "
    "kinds are not named on this page; their entities are included in `nodes` and in the "
    "`undeclared` meta-kind count, and nowhere else")


def sanitise_analytics(payload: dict, names: SurfaceNames) -> dict:
    """Rewrite `analytics.compute`'s payload into what this page may render.

    Applied to the whole payload rather than to the figures known to be risky. When "every figure
    names the ids it counted" collides with "this page never names a subject", constraint 1 wins
    and the collision is announced (`members_withheld`) rather than papered over.
    """
    out = dict(payload)
    # A rewound payload can name versions that are no longer open (R-017 t08 #6): classify them
    # from the snapshot's own rows before anything is named.
    names.adopt((payload.get("entities") or {}).values())
    # The vocabulary summary keeps the packs and the subject KINDS (why a figure is absent); the
    # claim edge names go, since this page never walks a claim.
    out["vocabulary"] = {k: v for k, v in (out.get("vocabulary") or {}).items()
                         if k != "claim_edge_kinds"}
    # A kind no bound pack declares is outside the closed ontology: its entities are withheld,
    # and so is its NAME (R-019 t08b #8) — a raw kind string is still something a writer chose.
    kinds = out.get("kinds") or []
    undeclared = [k for k in kinds if not k.get("declared", True)]
    out["kinds"] = [k for k in kinds if k.get("declared", True)]
    out["closed_vocabulary_violation"] = (
        {"kinds": len(undeclared), "nodes": sum(int(k.get("n") or 0) for k in undeclared),
         "why": CLOSED_VOCABULARY_VIOLATION} if undeclared else None)

    def public(ids: Iterable[str]) -> tuple[list[str], int]:
        shown, withheld = [], 0
        for entity_id in ids:
            if names.is_withheld(entity_id):
                withheld += 1
                continue
            shown.append(names.public_id(entity_id))
        return shown, withheld

    for key in COUNT_FIGURES:
        figure = out.get(key)
        if not isinstance(figure, dict):
            continue
        shown, withheld = public(figure.get("members") or [])
        figure = dict(figure, members=shown)
        if withheld:
            figure["members_withheld"] = MEMBERS_WITHHELD
        out[key] = figure

    mix = []
    for row in out.get("issuer_mix") or []:
        text, _ = names.display(row["issuer_id"])
        if text is None:
            continue
        members, _withheld = public(row.get("members") or [])
        mix.append(dict(row, issuer_id=names.public_id(row["issuer_id"]), issuer=text,
                        members=members))
    out["issuer_mix"] = mix

    for key in ("most_connected", "recently_ingested"):
        ranked = []
        for row in out.get(key) or []:
            if names.is_withheld(row["id"]):
                continue
            ranked.append(dict(row, id=names.public_id(row["id"])))
        out[key] = ranked

    by_source = []
    for row in out.get("documents_by_source") or []:
        members = row.get("members") or []
        shown, _withheld = public(members)
        by_source.append(dict(row, source=names.source_display(row["source"], members),
                              members=shown))
    out["documents_by_source"] = by_source

    entities = {}
    for entity_id, entity in (out.get("entities") or {}).items():
        text, _ = names.display(entity_id)
        if text is None:
            continue
        public_id = names.public_id(entity_id)
        entities[public_id] = dict(entity, id=public_id, label=text)
    out["entities"] = entities
    return out


__all__ = [
    "CLOSED_VOCABULARY_VIOLATION",
    "COUNT_FIGURES",
    "EvidenceUnavailable",
    "MAX_GRAPH_EDGES",
    "MAX_GRAPH_NODES",
    "MAX_SUBGRAPH_EDGES",
    "MAX_SUBGRAPH_NODES",
    "MEMBERS_WITHHELD",
    "SnapshotView",
    "SurfaceNames",
    "SurfaceTooLarge",
    "within_detail_envelope",
    "check_graph_bounds",
    "document_handle",
    "entity_handle",
    "entity_key",
    "looks_like_handle",
    "sanitise_analytics",
    "source_handle",
    "structural_edge_kinds",
    "snapshot_view",
    "structural_neighbors",
    "subgraph_edge_sources",
    "subgraph_sources",
]

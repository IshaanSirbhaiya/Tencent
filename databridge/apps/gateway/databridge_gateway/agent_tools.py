"""The tool surface `POST /intelligence/ask` hands the agent — sealed, read-only, structural, and
parameterised by the bound vocabulary rather than by any one pack's kinds.

`packages/agent` imports no graph on purpose: it declares the interface and this composition root
implements it. Everything decided here is a decision about **what the agent can reach**, which is
the only kind of decision that survives a fully suborned model.

## One capability, two kinds of tool

Every adapter carries the structural read capability — counts and structure, naming no subject.
`CandidateRead` and `PatientScopedRead` are **deliberately absent**: a candidate tool would put
identity candidates on a page whose contract is that it reports identity work and never does any,
and a subject-scoped tool needs a read grant this process has no authenticated human to mint from.

* **Figure tools take no argument at all.** A schema with no properties has nothing for an
  injected document to fill in. They report the same traversal the figures on the page render
  (`analytics.compute`), so a number in an answer can always be reconciled with a card.
* **Entity tools take an entity reference** — `neighbours`, `sources_of`, `path_between` — and
  are declared `broad` with every argument GROUNDED (`policy.check_argument_grounding`): each word
  must already appear in the staff question or in something a tool returned, so a document cannot
  aim them at anything nobody asked about. They resolve only what this page may name
  (`SurfaceNames`); a subject, or a kind no pack declares, resolves to nothing — the same answer as
  an entity that does not exist, so the tool is not an existence oracle for people.

## One pointer per value, and every pointer names a graph entity

`ToolRow` refuses a value without its own `SourcePointer`. Every pointer emitted here is
`kind="derived"`, its `source_id` is a graph entity's PUBLIC id (so a citation walks to
`GET /intelligence/subgraph/{id}`), its `derivation` names the rule that produced the value, and its
`derived_from` carries the entity's own pointers, projected through the page's naming policy.

**What a `derived` pointer here does and does not establish** (R-32): that the named entity exists
in the graph, that these pointers grounded it, and that this named rule was run — not every input
the rule consumed. **Labels have no pointer of their own** (R-31), and the derivation on every
label value says exactly that.
"""
from __future__ import annotations

import datetime as dt
from collections import deque
from typing import Any, Callable, Optional

from databridge_agent import (
    ClinicStructuralRead,
    SealedReadAdapter,
    Tool,
    ToolRegistry,
    ToolResult,
    ToolRow,
)
from databridge_core import SourcePointer

from .intelligence_surface import (
    SnapshotView,
    SurfaceNames,
    snapshot_view,
    structural_edge_kinds,
)

#: Code-authored sentences. Nothing a document said reaches a staff-facing line except as a node
#: LABEL of an entity this page may name — which `render.present` still screens on the way out.
AMBIGUOUS_TEXT = ("more than one subject row matched this document; a named person settles it, "
                  "in person")
UNLINKED_TEXT = ("no subject row matched this document; it is settled by a named person, in "
                 "person")
COVERAGE_GAP_TEXT = ("referenced by a document, and no agreement in the graph includes it")
INJECTED_LABEL_TEXT = ("the title this entity carries in the graph is addressed to an automated "
                       "reader rather than to a person; it was not read as an instruction and is "
                       "not reproduced here")
LABEL_RULE = "graph node label; labels carry no pointer of their own, so this cites the node"

#: Hops `path_between` will search. A path is an explanation; a twelve-hop one explains nothing.
MAX_PATH_HOPS = 6
#: Rows an entity tool returns. Beyond it the tool says so in its note rather than trimming quietly.
MAX_ENTITY_ROWS = 25


class _ReadOnlyGraphView:
    """The three graph reads the tools need, and nothing else.

    **Hygiene, not an authorization boundary**: the bound methods still carry `__self__`. What it
    buys is that the ordinary way this rots — a convenient write added to a tool on a deadline —
    has to get past an `AttributeError` first.
    """

    def __init__(self, store: Any) -> None:
        self.open_nodes: Callable[[], list] = store.open_nodes
        self.open_edges: Callable[[], list] = store.open_edges
        self.sources_for: Callable[..., list] = store.sources_for
        fetchall = getattr(store, "fetchall", None)
        if callable(fetchall):
            # Every node and edge VERSION, through two fixed SELECTs — what an `as_of` rewind needs
            # (`analytics.snapshot_at`, R-017 t08 #6). Not `fetchall` itself: a view that could run
            # any statement would be a view that could write.
            self.version_rows: Callable[[], tuple] = lambda: (
                list(fetchall("SELECT * FROM nodes ORDER BY rowid_pk")),
                list(fetchall("SELECT * FROM edges ORDER BY rowid_pk")))


class _GraphSnapshot:
    """One traversal, shared by every tool in one answer — so two tools cannot report two
    different denominators for one question."""

    def __init__(self, view: Any, as_of: Optional[dt.datetime] = None, *,
                 snapshot: Optional[SnapshotView] = None) -> None:
        # ONE materialised snapshot per answer (R-019 t08b #5): the figures, the naming policy,
        # the structural edges and every citation are derived from these rows, and each pointer is
        # read for the exact VERSION the snapshot holds (t08b #1) — never re-read from the store.
        self._snap = snapshot if snapshot is not None else snapshot_view(view, as_of)
        self._view = self._snap
        self._as_of = self._snap.as_of
        self._analytics: Any = None
        self._edges: Optional[list] = None
        #: The one naming policy this page has. Held here rather than per tool, so a tool cannot
        #: decide on its own that its labels are fine. Built over the SNAPSHOT, so a rewound answer
        #: names (and resolves) what stood at `as_of`, not what stands now (t08b #3/#4).
        self.names = SurfaceNames(self._snap)

    @property
    def as_of_text(self) -> str:
        return self._as_of.isoformat() if self._as_of else "now"

    def analytics(self) -> Any:
        if self._analytics is None:
            from databridge_graph.analytics import compute

            self._analytics = compute(self._snap.store, snapshot=self._snap.snapshot)
        return self._analytics

    def structural_edges(self) -> list:
        """Structural (non-claim) edges of THIS snapshot, between reportable nodes."""
        if self._edges is None:
            allowed = structural_edge_kinds()
            self._edges = [
                e for e in self._snap.open_edges()
                if e["kind"] in allowed
                and not self.names.is_withheld(e["src_id"])
                and not self.names.is_withheld(e["dst_id"])]
        return self._edges

    def pointer(self, entity_id: str, derivation: str,
                also: tuple[str, ...] = ()) -> Optional[SourcePointer]:
        """A `derived` pointer for one value, or `None` when the evidence cannot be read (the
        caller then reports the value as unresolved rather than uncited — R-21)."""
        if self.names.is_withheld(entity_id):
            return None
        pointers = list(self._entity_pointers(entity_id))
        for other in also:
            if self.names.is_withheld(other):
                continue
            pointers.extend(p for p in self._entity_pointers(other) if p not in pointers)
        if not pointers:
            return None
        return SourcePointer(source_id=self.names.public_id(entity_id), kind="derived",
                             derivation=f"{derivation} [as of {self.as_of_text}]",
                             derived_from=[self.names.project(p) for p in pointers])

    def _entity_pointers(self, entity_id: str) -> list:
        try:
            return list(self._view.sources_for(entity_id))
        except Exception:                                            # noqa: BLE001
            return []

    def display(self, entity_id: str) -> Optional[str]:
        text, _handle = self.names.display(entity_id)
        return text

    def label_is_addressed_to_a_machine(self, entity_id: str) -> bool:
        """Is this entity's graph label an instruction to an automated reader? Read for the FLAG
        only; the label itself never leaves this method."""
        from databridge_agent.guardrails import looks_like_injection

        row = self.names.rows().get(entity_id)
        return bool(row is not None and looks_like_injection(row["label"] or ""))

    def resolve_reference(self, reference: str) -> Optional[str]:
        """An entity id, a handle, or an exact (case-insensitive) label → an entity this page may
        name, or ``None``. A withheld entity resolves to ``None`` — indistinguishable from absent.
        A label is matched only where it PRINTS: matching a handled entity by its hidden label
        would confirm what the handle exists to withhold."""
        reference = (reference or "").strip()
        if not reference:
            return None
        resolved = self.names.resolve(reference)
        if resolved is not None and resolved in self.names.rows():
            return None if self.names.is_withheld(resolved) else resolved
        wanted = reference.casefold()
        matches = sorted(entity_id for entity_id, row in self.names.rows().items()
                         if (row["label"] or "").casefold() == wanted
                         and not self.names.is_withheld(entity_id)
                         and not self.names.display(entity_id)[1])
        return matches[0] if len(matches) == 1 else None


def _row(pairs: dict) -> ToolRow:
    """Build a row from `{key: (value, pointer)}`; a value whose pointer is missing is `None` and
    appears under *Asked for, not found* rather than as an uncited value."""
    values: dict[str, Any] = {}
    sources: dict[str, SourcePointer] = {}
    for key, (value, pointer) in pairs.items():
        if value is None or pointer is None:
            values[key] = None
            continue
        values[key] = value
        sources[key] = pointer
    return ToolRow(values=values, value_sources=sources)


def _labelled(snapshot: _GraphSnapshot, entity_id: str, field: str = "entity") -> dict:
    pairs = {field: (snapshot.display(entity_id), snapshot.pointer(entity_id, LABEL_RULE))}
    if snapshot.label_is_addressed_to_a_machine(entity_id):
        pairs["note"] = (INJECTED_LABEL_TEXT, snapshot.pointer(entity_id, LABEL_RULE))
    return pairs


class _StructuralRead(SealedReadAdapter):
    """Base for the figure tools. Structural capability, no arguments, one shared snapshot."""

    parameters = {"type": "object", "properties": {}, "additionalProperties": False}
    breadth = "narrow"

    def __init__(self, snapshot: _GraphSnapshot) -> None:
        super().__init__(ClinicStructuralRead())
        self._snapshot = snapshot


# --- figure tools ------------------------------------------------------------------------------


class KindCounts(_StructuralRead):
    """How many of each kind of thing the graph holds."""

    name = "kind_counts"
    description = (
        "How many of each kind are in the graph: one row per kind present, with its count and its "
        "shape (document, organisation, event, entity…) — what the graph holds, by kind. Subject "
        "kinds are counted on the figures surface and never listed here.")

    def read(self, **_: Any) -> ToolResult:
        snap = self._snapshot
        analytics = snap.analytics()
        rows = []
        for kind in analytics.kinds:
            if kind.subject or not kind.declared:
                continue
            members = sorted(entity_id for entity_id, row in snap.names.rows().items()
                             if row["kind"] == kind.kind and not snap.names.is_withheld(entity_id))
            if not members:
                continue
            rule = (f"analytics.compute — open nodes of kind {kind.kind!r} "
                    f"(meta-kind {kind.meta}), counted over {analytics.nodes} node(s)")
            rows.append(_row({
                "kind": (kind.kind, snap.pointer(members[0], rule)),
                "count": (kind.n, snap.pointer(members[0], rule)),
                "shape": (kind.meta, snap.pointer(members[0], rule)),
            }))
        note = f"{len(rows)} kind(s) of entity, {analytics.nodes} entities in all."
        return ToolResult(tool=self.name, rows=tuple(rows), note=note)


class MostConnected(_StructuralRead):
    """The entities with the most relationships."""

    name = "most_connected"
    description = (
        "The best-connected entities in the graph — hubs, ranked by their number of "
        "relationships, highest first. Subjects are never ranked.")

    def read(self, **_: Any) -> ToolResult:
        snap = self._snapshot
        rows = []
        for ranked in snap.analytics().most_connected:
            if snap.names.is_withheld(ranked.id):
                continue
            pairs = _labelled(snap, ranked.id)
            pairs["relationships"] = (ranked.value, snap.pointer(
                ranked.id, "analytics.compute — open edges touching this entity"))
            rows.append(_row(pairs))
        return ToolResult(tool=self.name, rows=tuple(rows),
                          note="Ranked by open relationships; subjects are never ranked.")


class RecentlyIngested(_StructuralRead):
    """What entered the graph most recently."""

    name = "recently_ingested"
    description = ("The newest entities in the graph, by the time the graph recorded them — what "
                   "was recently ingested or added lately.")

    def read(self, **_: Any) -> ToolResult:
        snap = self._snapshot
        rows = []
        for ranked in snap.analytics().recently_ingested:
            if snap.names.is_withheld(ranked.id):
                continue
            pairs = _labelled(snap, ranked.id)
            pairs["recorded_at"] = (ranked.value, snap.pointer(
                ranked.id, "the entity's system_from — when the graph first believed it"))
            rows.append(_row(pairs))
        return ToolResult(tool=self.name, rows=tuple(rows),
                          note="Newest first, by when the graph recorded each entity.")


class DocumentsBySource(_StructuralRead):
    """How many documents each source contributed."""

    name = "documents_by_source"
    description = ("One row per source, with how many document nodes cite it — where the "
                   "paperwork in the graph comes from, source by source.")

    def read(self, **_: Any) -> ToolResult:
        snap = self._snapshot
        rows = []
        for source in snap.analytics().documents_by_source:
            documents = [d for d in source.documents if not snap.names.is_withheld(d)]
            if not documents:
                continue
            rule = "analytics.compute — document nodes whose pointers cite this source"
            anchor = documents[0]
            rows.append(_row({
                "source": (snap.names.source_display(source.source, documents),
                           snap.pointer(anchor, rule, also=tuple(documents[1:]))),
                "documents": (len(source.documents),
                              snap.pointer(anchor, rule, also=tuple(documents[1:]))),
            }))
        return ToolResult(tool=self.name, rows=tuple(rows),
                          note="A source is what the documents' own pointers cite.")


class CrossSourceEntities(_StructuralRead):
    """Entities that more than one source agrees exist."""

    name = "cross_source_entities"
    description = ("Entities cited by two or more distinct sources — cross-source entities, where "
                   "separate sources corroborate the same thing.")

    def read(self, **_: Any) -> ToolResult:
        snap = self._snapshot
        figure = snap.analytics().cross_source
        rows = []
        for entity_id in figure.members[:MAX_ENTITY_ROWS]:
            if snap.names.is_withheld(entity_id):
                continue
            rows.append(_row(_labelled(snap, entity_id)))
        more = len(figure.members) - MAX_ENTITY_ROWS
        note = (f"{figure.n} of {figure.of} entities are cited by two or more sources."
                + (f" The first {MAX_ENTITY_ROWS} are listed." if more > 0 else ""))
        return ToolResult(tool=self.name, rows=tuple(rows), note=note)


class IssuerDocumentMix(_StructuralRead):
    """Who produced the documents, and how many each produced."""

    name = "issuer_document_mix"
    description = (
        "One row per organisation or actor with an `issued` relationship to a document, with how "
        "many documents it issued — which issuers sent the most documents. Counted over every "
        "document the graph holds; there is no date window on this read.")

    def read(self, **_: Any) -> ToolResult:
        snap = self._snapshot
        analytics = snap.analytics()
        rows = []
        for issuer in analytics.issuer_mix:
            if snap.names.is_withheld(issuer.issuer_id):
                continue
            count_rule = (f"analytics.compute — ISSUED edges to document nodes, over "
                          f"{analytics.documents} document(s) in the graph")
            pairs = _labelled(snap, issuer.issuer_id, field="issuer")
            pairs["documents"] = (issuer.n, snap.pointer(issuer.issuer_id, count_rule,
                                                         also=issuer.documents))
            rows.append(_row(pairs))
        note = ("Counted over every document in the graph. This read has no date filter, so it "
                "cannot answer a question about one week on its own.")
        return ToolResult(tool=self.name, rows=tuple(rows), note=note)


class DocumentsNeedingAPerson(_StructuralRead):
    """Documents whose identity claims a person has to settle."""

    name = "documents_needing_a_person"
    description = (
        "One row per document a person has to settle: it claims more than one subject row, or it "
        "claims none. It says WHAT is waiting — what is still blocked — and never WHO: there is no "
        "candidate name, no ordering and no score anywhere in this read.")

    def read(self, **_: Any) -> ToolResult:
        snap = self._snapshot
        analytics = snap.analytics()
        rows = []
        blocked = [(d, AMBIGUOUS_TEXT, "analytics.compute — open claim edges to more than one "
                                       "subject row, so the resolver narrowed it to none")
                   for d in sorted(analytics.ambiguity.members)]
        blocked += [(d, UNLINKED_TEXT, "analytics.compute — no open claim edge at all, so no "
                                       "subject row matched it")
                    for d in sorted(analytics.unlinked_documents.members)]
        for doc_id, waiting_on, rule in blocked:
            pairs = _labelled(snap, doc_id, field="document")
            pairs["waiting_on"] = (waiting_on, snap.pointer(doc_id, rule))
            rows.append(_row(pairs))
        note = (f"{len(rows)} of {analytics.documents} document(s) in the graph need a person. "
                f"Waiting on a human is this system working, not failing.")
        return ToolResult(tool=self.name, rows=tuple(rows), note=note)


class UncoveredReferences(_StructuralRead):
    """Things a document references that no agreement includes."""

    name = "coverage_gaps"
    description = (
        "One row per entity that a document references and no agreement in the graph includes — "
        "the coverage gap, walked as REFERENCES from a document minus PART_OF from an agreement. "
        "It quotes no price: money is not on this surface.")

    def read(self, **_: Any) -> ToolResult:
        snap = self._snapshot
        figure = snap.analytics().uncovered_references
        rows = []
        for entity_id in figure.members:
            if snap.names.is_withheld(entity_id):
                continue
            rule = (f"analytics.compute — the target of a REFERENCES edge from a document and of "
                    f"no PART_OF edge from an agreement, over {figure.of} includable entities")
            pairs = _labelled(snap, entity_id)
            pairs["finding"] = (COVERAGE_GAP_TEXT, snap.pointer(entity_id, rule))
            rows.append(_row(pairs))
        return ToolResult(tool=self.name, rows=tuple(rows),
                          note="Structural only: whether anyone is charged is decided elsewhere.")


# --- entity tools (grounded arguments) --------------------------------------------------------


def _entity_parameters(*names: str) -> dict:
    return {"type": "object",
            "properties": {n: {"type": "string", "minLength": 1, "maxLength": 200}
                           for n in names},
            "required": list(names), "additionalProperties": False}


class _EntityRead(SealedReadAdapter):
    breadth = "broad"

    def __init__(self, snapshot: _GraphSnapshot) -> None:
        super().__init__(ClinicStructuralRead())
        self._snapshot = snapshot

    def _not_found(self, reference: str) -> ToolResult:
        return ToolResult(tool=self.name, rows=(),
                          note="No entity this page may name matches that reference.")


class Neighbours(_EntityRead):
    name = "neighbours"
    description = ("The entities directly related to one named entity (by id, handle or exact "
                   "name), with the relationship between them. Claims about who a document "
                   "concerns are not relationships and are not walked.")
    parameters = _entity_parameters("entity")
    grounded_arguments = frozenset({"entity"})

    def read(self, **arguments: Any) -> ToolResult:
        snap = self._snapshot
        root = snap.resolve_reference(str(arguments.get("entity", "")))
        if root is None:
            return self._not_found(arguments.get("entity", ""))
        rows = []
        edges = [e for e in snap.structural_edges() if root in (e["src_id"], e["dst_id"])]
        for edge in sorted(edges, key=lambda e: e["id"])[:MAX_ENTITY_ROWS]:
            other = edge["dst_id"] if edge["src_id"] == root else edge["src_id"]
            direction = "outgoing" if edge["src_id"] == root else "incoming"
            rule = f"open `{edge['kind']}` edge ({direction}) from the named entity"
            pairs = _labelled(snap, other, field="neighbour")
            pairs["relationship"] = (edge["kind"], snap.pointer(other, rule, also=(root,)))
            pairs["direction"] = (direction, snap.pointer(other, rule, also=(root,)))
            rows.append(_row(pairs))
        note = (f"{len(edges)} relationship(s)"
                + (f"; the first {MAX_ENTITY_ROWS} are listed." if len(edges) > MAX_ENTITY_ROWS
                   else "."))
        return ToolResult(tool=self.name, rows=tuple(rows), note=note)


class SourcesOf(_EntityRead):
    name = "sources_of"
    description = ("The sources one named entity (by id, handle or exact name) was read from — "
                   "each pointer's source, kind and locator.")
    parameters = _entity_parameters("entity")
    grounded_arguments = frozenset({"entity"})

    def read(self, **arguments: Any) -> ToolResult:
        snap = self._snapshot
        root = snap.resolve_reference(str(arguments.get("entity", "")))
        if root is None:
            return self._not_found(arguments.get("entity", ""))
        rows = []
        for pointer in snap._entity_pointers(root)[:MAX_ENTITY_ROWS]:
            projected = snap.names.project(pointer)
            locator = (f"{projected.table}.{projected.column} row {projected.row_id}"
                       if projected.kind == "table_cell" else
                       f"characters {projected.char_start}–{projected.char_end}"
                       if projected.char_start is not None else projected.kind)
            rule = "one of the pointers that grounds this entity"
            cite = snap.pointer(root, rule)
            rows.append(_row({"source": (projected.source_id, cite),
                              "pointer_kind": (projected.kind, cite),
                              "locator": (locator, cite)}))
        return ToolResult(tool=self.name, rows=tuple(rows),
                          note="Each row is one pointer grounding the entity.")


class PathBetween(_EntityRead):
    name = "path_between"
    description = (f"The shortest chain of relationships linking two named entities (by id, "
                   f"handle or exact name), up to {MAX_PATH_HOPS} hops — how two things are "
                   f"connected.")
    parameters = _entity_parameters("from_entity", "to_entity")
    grounded_arguments = frozenset({"from_entity", "to_entity"})

    def read(self, **arguments: Any) -> ToolResult:
        snap = self._snapshot
        start = snap.resolve_reference(str(arguments.get("from_entity", "")))
        goal = snap.resolve_reference(str(arguments.get("to_entity", "")))
        if start is None or goal is None:
            return self._not_found("")
        adjacency: dict[str, list[tuple[str, str]]] = {}
        for e in snap.structural_edges():
            adjacency.setdefault(e["src_id"], []).append((e["dst_id"], e["kind"]))
            adjacency.setdefault(e["dst_id"], []).append((e["src_id"], e["kind"]))
        previous: dict[str, tuple[Optional[str], Optional[str]]] = {start: (None, None)}
        frontier: deque[tuple[str, int]] = deque([(start, 0)])
        while frontier and goal not in previous:
            node, hops = frontier.popleft()
            if hops >= MAX_PATH_HOPS:
                continue
            for other, kind in sorted(adjacency.get(node, [])):
                if other not in previous:
                    previous[other] = (node, kind)
                    frontier.append((other, hops + 1))
        if goal not in previous:
            return ToolResult(tool=self.name, rows=(),
                              note=f"No chain of at most {MAX_PATH_HOPS} relationships links them.")
        chain: list[tuple[str, Optional[str]]] = []
        cursor: Optional[str] = goal
        while cursor is not None:
            before, kind = previous[cursor]
            chain.append((cursor, kind))
            cursor = before
        chain.reverse()
        rows = []
        for step, (entity_id, kind) in enumerate(chain):
            pairs = _labelled(snap, entity_id, field="entity")
            rule = f"step {step} of the shortest structural path"
            pairs["step"] = (step, snap.pointer(entity_id, rule))
            if kind is not None:
                pairs["via"] = (kind, snap.pointer(entity_id, rule))
            rows.append(_row(pairs))
        return ToolResult(tool=self.name, rows=tuple(rows),
                          note=f"{len(chain) - 1} hop(s), over structural relationships only.")


#: The figure tools every vocabulary supports.
GENERIC_TOOLS = (KindCounts, MostConnected, RecentlyIngested, DocumentsBySource,
                 CrossSourceEntities, IssuerDocumentMix)
#: Tools whose figure only exists when the vocabulary declares what it rests on.
CONDITIONAL_TOOLS = ((DocumentsNeedingAPerson, "ambiguity"), (UncoveredReferences,
                                                              "uncovered_references"))
ENTITY_TOOLS = (Neighbours, SourcesOf, PathBetween)


def build_structural_registry(store: Any, *,
                              as_of: Optional[dt.datetime] = None,
                              snapshot: Optional[SnapshotView] = None) -> ToolRegistry:
    """The registry `POST /intelligence/ask` hands the agent. Structural reads only.

    A tool whose figure the bound vocabulary cannot support (no subject kind → no identity-work
    figure) is not registered at all, rather than registered to return nothing: an empty tool
    would read as "nothing is waiting", which is a claim, not an absence.
    """
    snapshot = _GraphSnapshot(_ReadOnlyGraphView(store), as_of=as_of, snapshot=snapshot)
    analytics = snapshot.analytics()
    classes: list[type] = list(GENERIC_TOOLS)
    classes += [cls for cls, figure in CONDITIONAL_TOOLS
                if getattr(analytics, figure) is not None]
    classes += list(ENTITY_TOOLS)
    return ToolRegistry([Tool(adapter=cls(snapshot)) for cls in classes])


__all__ = [
    "AMBIGUOUS_TEXT", "COVERAGE_GAP_TEXT", "CONDITIONAL_TOOLS", "CrossSourceEntities",
    "DocumentsBySource", "DocumentsNeedingAPerson", "ENTITY_TOOLS", "GENERIC_TOOLS",
    "INJECTED_LABEL_TEXT", "IssuerDocumentMix", "KindCounts", "MostConnected", "Neighbours",
    "PathBetween", "RecentlyIngested", "SourcesOf", "UNLINKED_TEXT", "UncoveredReferences",
    "build_structural_registry",
]

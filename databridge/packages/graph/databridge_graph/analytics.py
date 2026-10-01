"""Graph-derived structural analytics over ANY bound vocabulary (`PLAN.md` §6.7, T-42/T-43).

Every figure is computed by walking the open graph and reasoning about **meta-kinds**
(`meta.py`) and the bound packs' **subject kinds** — never about one vertical's kind names. A pack
the engine has never seen gets every figure its declarations support, and a figure its
declarations cannot support is ``None`` (not applicable), never a zero.

## The constraint that shapes every function below

**These are counts of structural facts, never claims about outcomes.** "11 documents, 2 of them
ambiguous" is a fact about the graph; "we saved N hours" is a claim about the world. So:

- no derived rates presented as performance; no projection, extrapolation or annualisation;
- every figure carries the denominator it was computed over (`Count.of`);
- **and every figure carries the ids it was counted over** (`Count.members`), so a reader can walk
  to the subgraph that produced it instead of trusting the number. A count whose members cannot be
  named is indistinguishable from a literal.

## The figures, and the declaration each one rests on

* ``kinds`` — every kind present, with its meta-kind, its pack, and whether it is a subject.
* ``documents`` — nodes whose kind binds the ``DOCUMENT`` meta-kind.
* ``issuer_mix`` — ``ISSUED`` meta-edges into documents: who produced the paperwork.
* ``subjects`` / ``ambiguity`` / ``unlinked_documents`` / ``conflicts`` — the identity-work
  figures, over ``ABOUT`` meta-edges (a CLAIM that an artefact concerns an entity) from documents
  to **subject** nodes. ``None`` when the vocabulary declares no subject or no claim edge.
* ``uncovered_references`` — entities a document references (``REFERENCES`` meta) of a kind that
  agreements include (``PART_OF`` from an ``AGREEMENT``-shaped node), which no agreement includes.
* ``most_connected``, ``recently_ingested`` — rankings; **subject nodes are never ranked** (a
  ranking of people by connectedness is not a structural fact about the paperwork).
* ``documents_by_source``, ``cross_source``, ``orphans`` — provenance structure: which source each
  document's pointers cite, entities cited by two or more distinct sources, and entities with no
  relationship at all.

## What this deliberately does NOT do

It never ranks, scores or narrows a claim. ``ambiguity`` reports *how many* candidates a document
named; it never says which one is right (CLAUDE.md §Hard constraints 1). ``conflicts`` reports that
documents from different issuers claim the same subject row — deciding which applies is a human's
job, per record, in the pack's own rules.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from .meta import CLAIM_META_KINDS, EdgeMetaKind, NodeMetaKind
from .packs import Vocabulary, active_vocabulary

#: How many entries a ranked list carries. A ranking, not an export.
TOP_N = 10


def _parse(value: str | None) -> dt.datetime | None:
    """Store text → datetime. Python 3.11+ parses the store's trailing `Z`."""
    return dt.datetime.fromisoformat(value) if value else None


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    """Normalise to UTC-aware — a naive `as_of` is the obvious thing to type, and comparing naive
    and aware datetimes raises."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)


@dataclass(frozen=True)
class Count:
    """A count, the population it was counted over, and the ids behind it. Never one without
    the others — `members` is what makes the figure traceable to a traversal."""

    label: str
    n: int
    of: int
    members: tuple[str, ...] = ()

    @property
    def pct(self) -> float:
        return (self.n / self.of * 100.0) if self.of else 0.0

    def to_dict(self) -> dict:
        return {"label": self.label, "n": self.n, "of": self.of,
                "pct": round(self.pct, 1), "members": list(self.members)}


@dataclass(frozen=True)
class Entity:
    """The minimum needed to render an id as something a person can read and click."""

    id: str
    kind: str
    label: str

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.label}


@dataclass(frozen=True)
class IssuerDocuments:
    """One row of the issuer mix: who produced documents, and exactly which."""

    issuer_id: str
    issuer: str
    documents: tuple[str, ...] = ()

    @property
    def n(self) -> int:
        return len(self.documents)

    def to_dict(self) -> dict:
        return {"issuer_id": self.issuer_id, "issuer": self.issuer,
                "documents": self.n, "members": list(self.documents)}


@dataclass(frozen=True)
class KindCount:
    kind: str
    n: int
    meta: Optional[str]
    pack: Optional[str]
    subject: bool
    #: False for a kind stored in the graph that no bound pack declares — reported, never hidden.
    declared: bool

    def to_dict(self) -> dict:
        # No member list: a per-kind list would make the payload grow with the graph rather than
        # with the figures. A kind is drilled into through the graph surface, not through here.
        return {"kind": self.kind, "n": self.n, "meta": self.meta, "pack": self.pack,
                "subject": self.subject, "declared": self.declared}


@dataclass(frozen=True)
class Ranked:
    id: str
    value: Any

    def to_dict(self, key: str) -> dict:
        return {"id": self.id, key: self.value}


@dataclass(frozen=True)
class SourceDocuments:
    source: str
    documents: tuple[str, ...] = ()
    entities: int = 0

    def to_dict(self) -> dict:
        return {"source": self.source, "documents": len(self.documents),
                "entities": self.entities, "members": list(self.documents)}


@dataclass
class GraphAnalytics:
    nodes: int = 0
    edges: int = 0
    documents: int = 0
    kinds: list[KindCount] = field(default_factory=list)
    meta_kinds: dict[str, int] = field(default_factory=dict)
    subjects: Count | None = None
    issuer_mix: list[IssuerDocuments] = field(default_factory=list)
    ambiguity: Count | None = None
    unlinked_documents: Count | None = None
    conflicts: Count | None = None
    uncovered_references: Count | None = None
    most_connected: list[Ranked] = field(default_factory=list)
    recently_ingested: list[Ranked] = field(default_factory=list)
    documents_by_source: list[SourceDocuments] = field(default_factory=list)
    cross_source: Count | None = None
    orphans: Count | None = None
    #: Entities in the snapshot whose VERSION-specific pointers could not be read. Their sources
    #: are left out of the provenance figures rather than filled from the currently-open version
    #: (R-019 t08b #1): present-day evidence never stands behind a historical value.
    evidence_unavailable: Count | None = None
    #: Edges skipped because an endpoint is not a node of the snapshot (E2E F-03). ``edges`` counts
    #: the rest. A number, not a member list: an edge id embeds both endpoint ids.
    dangling_edges: int = 0
    as_of: str | None = None
    #: Which read produced the snapshot (``open`` · ``all_versions``) — `Snapshot.basis`.
    snapshot_basis: str | None = None
    #: What the vocabulary declared that the figures rest on — so a reader can see WHY a figure is
    #: absent (no subject kind declared) rather than guess.
    vocabulary: dict = field(default_factory=dict)
    # id → Entity for every id named anywhere above, so the caller never has to guess a label.
    entities: dict[str, Entity] = field(default_factory=dict)

    def to_dict(self) -> dict:
        def c(x: Count | None) -> dict | None:
            return None if x is None else x.to_dict()
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "documents": self.documents,
            "kinds": [k.to_dict() for k in self.kinds],
            "meta_kinds": dict(sorted(self.meta_kinds.items())),
            "subjects": c(self.subjects),
            "issuer_mix": [i.to_dict() for i in self.issuer_mix],
            "ambiguity": c(self.ambiguity),
            "unlinked_documents": c(self.unlinked_documents),
            "conflicts": c(self.conflicts),
            "uncovered_references": c(self.uncovered_references),
            "most_connected": [r.to_dict("degree") for r in self.most_connected],
            "recently_ingested": [r.to_dict("system_from") for r in self.recently_ingested],
            "documents_by_source": [s.to_dict() for s in self.documents_by_source],
            "cross_source": c(self.cross_source),
            "orphans": c(self.orphans),
            "evidence_unavailable": c(self.evidence_unavailable),
            "dangling_edges": self.dangling_edges,
            "as_of": self.as_of,
            "snapshot_basis": self.snapshot_basis,
            "vocabulary": self.vocabulary,
            "entities": {k: v.to_dict() for k, v in sorted(self.entities.items())},
        }


def _leaf_sources(pointer: Any) -> set[str]:
    """The source ids a pointer ultimately cites — a derived pointer cites its inputs' sources."""
    children = list(getattr(pointer, "derived_from", None) or [])
    children.extend(getattr(pointer, "facts", None) or [])
    if getattr(pointer, "kind", None) == "derived" and children:
        out: set[str] = set()
        for child in children:
            out |= _leaf_sources(child)
        return out
    return {pointer.source_id} if getattr(pointer, "source_id", None) else set()


def valid_at(row: Any, as_of: dt.datetime | None) -> bool:
    """Is this row's VALID-time window open at ``as_of``? (``None`` = every row.)"""
    if as_of is None:
        return True
    vf, vt = _aware(_parse(row["valid_from"])), _aware(_parse(row["valid_to"]))
    if vf and vf > as_of:
        return False
    if vt and vt <= as_of:
        return False
    return True


def _all_versions(store: Any) -> Optional[tuple[list, list]]:
    """Every node and edge VERSION, or ``None`` when this reader cannot see closed versions.

    A read-only view offers ``version_rows()``; the store itself answers through two fixed
    ``SELECT``s. Nothing here writes."""
    reader = getattr(store, "version_rows", None)
    if callable(reader):
        return reader()
    fetchall = getattr(store, "fetchall", None)
    if callable(fetchall):
        return (list(fetchall("SELECT * FROM nodes ORDER BY rowid_pk")),
                list(fetchall("SELECT * FROM edges ORDER BY rowid_pk")))
    return None


def _believed_at(rows: list, as_of: dt.datetime) -> list:
    """Per id, the version that describes ``as_of`` — the latest-recorded one whose validity
    covers it, among versions still standing as a description of their own window:

    * an OPEN version;
    * a closed version whose closure recorded a validity end (``valid_to``) — "true until then";
    * a closed version that has a SUCCESSOR — superseded, not withdrawn, so it is still what was
      believed about the time before its successor's validity began.

    A closed version with no validity end and no successor was WITHDRAWN, and is never read back.
    """
    by_id: dict[str, list] = {}
    for r in rows:
        by_id.setdefault(r["id"], []).append(r)
    out = []
    for versions in by_id.values():
        versions.sort(key=lambda r: (r["system_from"] or "", r["rowid_pk"]))
        standing = [r for i, r in enumerate(versions)
                    if valid_at(r, as_of) and (r["system_to"] is None or r["valid_to"]
                                               or i < len(versions) - 1)]
        if standing:
            out.append(standing[-1])
    out.sort(key=lambda r: r["rowid_pk"])
    return out


def snapshot_at(store: Any, as_of: dt.datetime | None) -> tuple[list, list, str]:
    """``(node_rows, edge_rows, basis)`` — the graph as it stood at ``as_of``.

    ``as_of=None`` is the open graph. Otherwise the rewind is built from EVERY version
    (R-017 t08 #6): filtering only today's open rows lost every fact whose validity ended — or
    whose version was superseded — before today, so each historical figure under-counted. An edge
    is kept only when both its endpoints stand in the same snapshot. ``basis`` says which read
    produced it (``open`` · ``all_versions`` · ``open_versions_only`` for a reader that cannot see
    closed versions — said, never hidden)."""
    as_of = _aware(as_of)
    if as_of is None:
        return list(store.open_nodes()), list(store.open_edges()), "open"
    versions = _all_versions(store)
    if versions is None:
        nodes = [r for r in store.open_nodes() if valid_at(r, as_of)]
        live = {r["id"] for r in nodes}
        edges = [e for e in store.open_edges()
                 if valid_at(e, as_of) and e["src_id"] in live and e["dst_id"] in live]
        return nodes, edges, "open_versions_only"
    nodes = _believed_at(versions[0], as_of)
    live = {r["id"] for r in nodes}
    edges = [e for e in _believed_at(versions[1], as_of)
             if e["src_id"] in live and e["dst_id"] in live]
    return nodes, edges, "all_versions"


class SnapshotIncomplete(RuntimeError):
    """A historical read was asked of a reader that cannot see closed versions.

    Refused rather than approximated (R-019 t08b #6): rewinding over today's OPEN rows alone drops
    every fact closed since, and returns ordinary-looking figures that under-count."""

    code = "HISTORY_UNAVAILABLE"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Snapshot:
    """The graph as it stood at ``as_of``, materialised ONCE (R-019 t08b #5).

    Every figure, every name, every traversal and every citation in one answer is derived from
    these rows — so two reads in one answer cannot straddle a write, and a historical row is cited
    with ITS version's pointers (``version_of``), never with today's."""

    nodes: tuple
    edges: tuple
    basis: str
    as_of: dt.datetime | None = None
    #: Edges left out because an endpoint is not a node of this snapshot (E2E F-03): a foreign key
    #: to a row never ingested. Skipped, and COUNTED — never a crash, never silently dropped.
    dangling_edges: int = 0

    def node_row(self, entity_id: str) -> Any:
        return self._index()[0].get(entity_id)

    def edge_row(self, entity_id: str) -> Any:
        return self._index()[1].get(entity_id)

    def row(self, entity_id: str) -> Any:
        found = self.node_row(entity_id)
        return found if found is not None else self.edge_row(entity_id)

    def _index(self) -> tuple[dict, dict]:
        cached = self.__dict__.get("_idx")
        if cached is None:
            cached = ({r["id"]: r for r in self.nodes}, {r["id"]: r for r in self.edges})
            object.__setattr__(self, "_idx", cached)
        return cached


def take_snapshot(store: Any, as_of: dt.datetime | None) -> Snapshot:
    """Materialise ``snapshot_at`` once. A historical read that could only see open versions
    raises :class:`SnapshotIncomplete` instead of returning an under-count."""
    as_of = _aware(as_of)
    nodes, edges, basis = snapshot_at(store, as_of)
    if as_of is not None and basis == "open_versions_only":
        raise SnapshotIncomplete(
            "this reader cannot see closed versions of the graph, so a figure as of "
            f"{as_of.isoformat()} would silently leave out every fact closed since — it is "
            "refused rather than under-counted")
    live = {r["id"] for r in nodes}
    kept = [e for e in edges if e["src_id"] in live and e["dst_id"] in live]
    return Snapshot(nodes=tuple(nodes), edges=tuple(kept), basis=basis, as_of=as_of,
                    dangling_edges=len(edges) - len(kept))


def row_sources(store: Any, row: Any) -> Optional[list]:
    """The pointers of THIS version, or ``None`` when they cannot be read.

    A reader without version addressing may answer for the OPEN version (that is the version it
    returns), but never for a closed one: substituting the current pointers for a historical row
    would cite evidence that did not support that value (R-019 t08b #1)."""
    try:
        return list(store.sources_for(row["id"], version_pk=row["rowid_pk"]))
    except TypeError:
        pass
    except (IndexError, KeyError):
        return None
    try:
        is_open = row["system_to"] is None
    except (IndexError, KeyError):
        is_open = False
    if not is_open:
        return None
    try:
        return list(store.sources_for(row["id"]))
    except Exception:                                                # noqa: BLE001
        return None


def compute(store: Any, *, as_of: dt.datetime | None = None,
            vocabulary: Optional[Vocabulary] = None, top: int = TOP_N,
            snapshot: Optional[Snapshot] = None) -> GraphAnalytics:
    """Walk the graph once and produce the aggregates.

    ``store`` needs ``open_nodes()``, ``open_edges()`` and ``sources_for(id)``; to rewind past a
    closed version it also needs ``version_rows()`` or ``fetchall`` (``snapshot_at``). ``as_of``
    rebuilds the graph as it stood at that instant from every version — the bi-temporal axis is what makes "how has this
    changed?" answerable without a separate time-series table. Labels are read from the SAME
    filtered snapshot as the counts, never from a fresh lookup of the currently-open row.

    ``snapshot`` — a :class:`Snapshot` the caller already materialised (its ``as_of`` wins), so
    the figures and everything else in one answer read the same rows. Without one, the snapshot is
    taken here; a historical read that cannot see closed versions raises
    :class:`SnapshotIncomplete` rather than under-counting.
    """
    vocab = vocabulary or active_vocabulary()
    if snapshot is None:
        snapshot = take_snapshot(store, as_of)
    as_of = snapshot.as_of

    # An edge whose endpoint is not valid at `as_of` is not a fact yet either (`snapshot_at`).
    node_rows, edges, basis = list(snapshot.nodes), list(snapshot.edges), snapshot.basis
    # A snapshot built elsewhere is re-checked: an edge naming a node this snapshot does not hold
    # is skipped and counted (E2E F-03), so no figure below ever indexes a missing endpoint.
    present = {r["id"] for r in node_rows}
    dangling = snapshot.dangling_edges + sum(
        1 for e in edges if e["src_id"] not in present or e["dst_id"] not in present)
    edges = [e for e in edges if e["src_id"] in present and e["dst_id"] in present]
    entities = {r["id"]: Entity(id=r["id"], kind=r["kind"], label=r["label"]) for r in node_rows}

    def meta_of(kind: str) -> Optional[NodeMetaKind]:
        decl = vocab.node_kinds.get(kind)
        return decl.binds_to if decl is not None else None

    def edge_meta(kind: str) -> Optional[EdgeMetaKind]:
        decl = vocab.edge_kinds.get(kind)
        return decl.binds_to if decl is not None else None

    subject_kinds = frozenset(vocab.subject_kinds)
    kind_of = {r["id"]: r["kind"] for r in node_rows}

    # --- kinds present -------------------------------------------------------------------
    members_by_kind: dict[str, list[str]] = {}
    for r in node_rows:
        members_by_kind.setdefault(r["kind"], []).append(r["id"])
    kinds: list[KindCount] = []
    meta_counts: Counter = Counter()
    for kind, ids in members_by_kind.items():
        decl = vocab.node_kinds.get(kind)
        meta = decl.binds_to.value if decl is not None else None
        meta_counts[meta or "undeclared"] += len(ids)
        kinds.append(KindCount(kind=kind, n=len(ids), meta=meta,
                               pack=decl.pack if decl is not None else None,
                               subject=kind in subject_kinds, declared=decl is not None))
    kinds.sort(key=lambda k: (-k.n, k.kind))

    doc_ids = {r["id"] for r in node_rows if meta_of(r["kind"]) is NodeMetaKind.DOCUMENT}
    subject_ids = {r["id"] for r in node_rows if r["kind"] in subject_kinds}

    # --- issuer mix: ISSUED meta-edges into documents ------------------------------------
    issued_docs: dict[str, set[str]] = {}
    # A SET per document (R-017 t08 #8): a document issued by two parties contributes both to the
    # conflict figure. A single slot kept whichever edge came last and could drop the one issuer
    # that made a subject's claims cross-issuer.
    issuers_of: dict[str, set[str]] = {}
    for e in edges:
        if edge_meta(e["kind"]) is EdgeMetaKind.ISSUED and e["dst_id"] in doc_ids:
            issued_docs.setdefault(e["src_id"], set()).add(e["dst_id"])
            issuers_of.setdefault(e["dst_id"], set()).add(e["src_id"])
    mix = [IssuerDocuments(issuer_id=oid, issuer=entities[oid].label,
                           documents=tuple(sorted(docs)))
           for oid, docs in issued_docs.items()]
    mix.sort(key=lambda i: (-i.n, i.issuer))

    # --- identity work: CLAIM meta-edges from documents to subject nodes -----------------
    claim_edge_kinds = {n for n, d in vocab.edge_kinds.items() if d.binds_to in CLAIM_META_KINDS}
    identity_applicable = bool(subject_kinds) and bool(claim_edge_kinds)
    claims: dict[str, set[str]] = {}
    for e in edges:
        if e["kind"] not in claim_edge_kinds:
            continue
        if e["src_id"] in doc_ids and e["dst_id"] in subject_ids:
            claims.setdefault(e["src_id"], set()).add(e["dst_id"])
    # The documents whose KIND can make such a claim — the honest population for "unlinked". A
    # document of a kind no claim edge starts from is not "unlinked"; it was never going to link.
    claim_src_kinds: set[str] = set()
    for name in claim_edge_kinds:
        decl = vocab.edge_kinds[name]
        if "*" in decl.dst or set(decl.dst) & subject_kinds:
            claim_src_kinds |= set(decl.src)
    claimable_docs = {d for d in doc_ids
                      if "*" in claim_src_kinds or kind_of[d] in claim_src_kinds}

    subjects = ambiguity = unlinked_count = conflicts = None
    if identity_applicable:
        ambiguous = sorted(d for d, subs in claims.items() if len(subs) > 1)
        linked = sorted(claims)
        unlinked = sorted(claimable_docs - set(linked))
        docs_by_subject: dict[str, set[str]] = {}
        for doc, subs in claims.items():
            for s in subs:
                docs_by_subject.setdefault(s, set()).add(doc)
        disagreeing = sorted(
            s for s, docs in docs_by_subject.items()
            if len(set().union(*(issuers_of.get(d, set()) for d in docs))) > 1)
        # "rows", not "people": a claim is still a CLAIM (constraint 1) — an ambiguous document
        # contributes two rows and neither is settled — so this is an upper bound on persons.
        subjects = Count("registry rows a document has claimed",
                         len(docs_by_subject), len(subject_ids),
                         tuple(sorted(docs_by_subject)))
        ambiguity = Count("documents naming more than one candidate",
                          len(ambiguous), len(linked), tuple(ambiguous))
        unlinked_count = Count("documents matching no registry row",
                               len(unlinked), len(claimable_docs), tuple(unlinked))
        conflicts = Count("registry rows claimed by documents from more than one issuer",
                          len(disagreeing), len(docs_by_subject), tuple(disagreeing))

    # --- references no agreement includes -------------------------------------------------
    # Which kinds an agreement CAN include is a declaration, not an observation (R-019 t08b #7):
    # deriving it from observed PART_OF edges alone made the figure vanish exactly when no
    # agreement included anything. A concrete destination of a PART_OF edge kind whose declared
    # source binds AGREEMENT counts; a wildcard end names no kind and is read from data only.
    includable: set[str] = set()
    for decl in vocab.edge_kinds.values():
        if decl.binds_to is not EdgeMetaKind.PART_OF:
            continue
        if not any(meta_of(k) is NodeMetaKind.AGREEMENT for k in decl.src if k != "*"):
            continue
        includable |= {k for k in decl.dst if k != "*" and k in vocab.node_kinds}
    included: set[str] = set()
    for e in edges:
        if (edge_meta(e["kind"]) is EdgeMetaKind.PART_OF
                and meta_of(kind_of[e["src_id"]]) is NodeMetaKind.AGREEMENT):
            includable.add(kind_of[e["dst_id"]])
            included.add(e["dst_id"])
    referenced = {e["dst_id"] for e in edges
                  if edge_meta(e["kind"]) is EdgeMetaKind.REFERENCES and e["src_id"] in doc_ids
                  and kind_of[e["dst_id"]] in includable}
    catalogue = [r["id"] for r in node_rows if r["kind"] in includable]
    uncovered = sorted(referenced - included)
    uncovered_references = (Count("referenced by a document, included by no agreement",
                                  len(uncovered), len(catalogue), tuple(uncovered))
                            if includable else None)

    # --- connectedness -----------------------------------------------------------------------
    degree: Counter = Counter()
    for e in edges:
        degree[e["src_id"]] += 1
        degree[e["dst_id"]] += 1
    rankable = [r for r in node_rows if r["kind"] not in subject_kinds]
    most_connected = [Ranked(r["id"], degree[r["id"]])
                      for r in sorted(rankable, key=lambda r: (-degree[r["id"]], r["id"]))
                      if degree[r["id"]] > 0][:top]
    # Subjects are left out of the provenance figures below as they are out of the rankings: a
    # registry row nobody referenced is not an orphaned fact, and its id would name a person.
    orphan_ids = sorted(r["id"] for r in rankable if degree[r["id"]] == 0)
    orphans = Count("non-subject entities with no relationship", len(orphan_ids), len(rankable),
                    tuple(orphan_ids))
    recently = [Ranked(r["id"], r["system_from"])
                for r in sorted(rankable, key=lambda r: (r["system_from"] or "", r["id"]),
                                reverse=True)][:top]

    # --- provenance structure ------------------------------------------------------------
    sources_of: dict[str, set[str]] = {}
    unreadable: list[str] = []
    for r in node_rows:
        cited: set[str] = set()
        pointers = row_sources(store, r)
        if pointers is None:
            unreadable.append(r["id"])
            pointers = []
        for pointer in pointers:
            cited |= _leaf_sources(pointer)
        sources_of[r["id"]] = cited
    evidence_unavailable = Count("entities whose version-specific evidence could not be read",
                                 len(unreadable), len(node_rows), tuple(sorted(unreadable)))
    by_source_docs: dict[str, set[str]] = {}
    by_source_entities: Counter = Counter()
    for entity_id, cited in sources_of.items():
        for source in cited:
            by_source_entities[source] += 1
            if entity_id in doc_ids:
                by_source_docs.setdefault(source, set()).add(entity_id)
    documents_by_source = sorted(
        (SourceDocuments(source=s, documents=tuple(sorted(by_source_docs.get(s, ()))),
                         entities=by_source_entities[s]) for s in by_source_entities),
        key=lambda s: (-len(s.documents), -s.entities, s.source))
    rankable_ids = {r["id"] for r in rankable}
    multi = sorted(i for i, cited in sources_of.items() if len(cited) >= 2 and i in rankable_ids)
    cross_source = Count("non-subject entities cited by two or more sources", len(multi),
                         len(rankable_ids), tuple(multi))

    # Only the ids actually named above travel with the payload.
    named: set[str] = {*(i.issuer_id for i in mix), *(r.id for r in most_connected),
                       *(r.id for r in recently)}
    for cnt in (subjects, ambiguity, unlinked_count, conflicts, uncovered_references,
                cross_source, orphans, evidence_unavailable):
        if cnt is not None:
            named.update(cnt.members)
    for issuer in mix:
        named.update(issuer.documents)
    for source in documents_by_source:
        named.update(source.documents)

    return GraphAnalytics(
        nodes=len(node_rows),
        edges=len(edges),
        documents=len(doc_ids),
        kinds=kinds,
        meta_kinds=dict(meta_counts),
        subjects=subjects,
        issuer_mix=mix,
        ambiguity=ambiguity,
        unlinked_documents=unlinked_count,
        conflicts=conflicts,
        uncovered_references=uncovered_references,
        most_connected=most_connected,
        recently_ingested=recently,
        documents_by_source=documents_by_source,
        cross_source=cross_source,
        orphans=orphans,
        evidence_unavailable=evidence_unavailable,
        dangling_edges=dangling,
        as_of=as_of.isoformat() if as_of else None,
        snapshot_basis=basis,
        vocabulary={"packs": list(vocab.packs), "subject_kinds": sorted(subject_kinds),
                    "claim_edge_kinds": sorted(claim_edge_kinds),
                    "identity_figures": identity_applicable},
        entities={k: v for k, v in entities.items() if k in named},
    )


__all__ = ["Count", "Entity", "GraphAnalytics", "IssuerDocuments", "KindCount", "Ranked",
           "Snapshot", "SnapshotIncomplete", "SourceDocuments", "TOP_N", "compute", "row_sources",
           "snapshot_at", "take_snapshot", "valid_at"]

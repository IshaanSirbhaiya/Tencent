"""Which graph entities carry a SUBJECT's information — derived from the vocabulary, never named.

`/evidence` and `/intelligence` both have to answer one question before they print anything: *does
this entity carry information about a subject* — the entity kind a pack declared whose identity the
system may never auto-confirm (spine D4b, CLAUDE.md hard constraint 1)? They used to answer it by
naming one vertical's kinds (a patient kind, a document kind, a coverage-rules file). This module
answers it from the bound vocabulary alone, so a pack the engine has never seen gets the same
protection by declaring its `subject_kinds` and its claim edges, and a pack that declares no
subject gets none withheld.

## The rule

An entity is **subject-bearing** when any of these holds:

1. its kind is a pack **subject kind**;
2. its kind is **claim-capable** — the source side of an edge kind binding to a CLAIM or
   ATTESTED meta-kind (``ABOUT`` / ``RESOLVED_TO``) whose destination may be a subject. A document
   kind that *can* claim to concern a person carries that person's details whether or not the
   resolver matched anyone: an unmatched document still names someone;
3. it is the source of an open claim edge to a subject node (the data-driven twin of 2);
4. any of its pointers reaches into material that names a subject — a document span of a
   claim-capable document, a document the graph does not hold while the vocabulary can claim
   subjects (unprovable, so it fails closed), or a table cell in the very row a subject node was
   read from.

Everything else is **ordinary knowledge**: shown by its pointers, labelled by its label.

## Why it fails closed where it does

Rule 4's "a document the graph does not hold" case is the one that costs something: an entity
grounded in a document nobody ingested as a node is withheld whenever the vocabulary can claim
subjects at all. The alternative — treat an unknown document as safe — is how a filename carrying a
person's name reached a business-intelligence screen before. When the vocabulary declares no
claim edge to a subject, the case never arises and nothing is withheld.

**Read-only, memoised per instance.** Build one per request; it reads the store through the three
read methods every surface already uses (``open_nodes`` / ``open_edges`` / ``sources_for``).
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from databridge_graph.meta import (
    ATTESTED_META_KINDS,
    CLAIM_META_KINDS,
    NodeMetaKind,
)
from databridge_graph.packs import ANY_KIND, Vocabulary, active_vocabulary

from .source_capture import normalise_digest

#: Pointer kinds that reach into something a document says.
DOCUMENT_POINTER_KINDS = frozenset({"doc_span", "ocr_bbox", "email_part", "transcript_span"})


def pointer_children(pointer: Any) -> list:
    out = list(getattr(pointer, "derived_from", None) or [])
    out.extend(getattr(pointer, "facts", None) or [])
    child = getattr(pointer, "child", None)
    if child is not None:
        out.append(child)
    return out


#: Prefix of a key that says "this pointer reaches a document but names none". It is never
#: indexed as any document's key, so it is never safe: an unidentifiable document fails closed.
UNIDENTIFIED = "unidentified:"


def document_keys_of(pointer: Any) -> set[str]:
    """The keys by which one pointer names a document — its own level only, not its children.

    A pointer's ``source_id`` is NOT a document identity (R-019 t08 #3): for a connector it is the
    whole connection, so keying on it fused every document in one drive — and let a connection
    whose name equals a safe document's id borrow that document's standing. A document-kind
    pointer that carries no document id is keyed by the narrowest item id its kind has (a media
    id, a message id), and otherwise by an :data:`UNIDENTIFIED` key that no document owns."""
    keys: set[str] = set()
    kind = getattr(pointer, "kind", None)
    document_id = getattr(pointer, "document_id", None)
    digest = normalise_digest(getattr(pointer, "capture_digest", None))
    if document_id:
        keys.add(f"id:{document_id}")
    if digest and kind != "table_cell":
        keys.add(f"sha:{digest}")
    if kind in DOCUMENT_POINTER_KINDS and not document_id:
        media_id = getattr(pointer, "media_id", None)
        message_id = getattr(pointer, "message_id", None)
        if media_id:
            keys.add(f"media:{media_id}")
        if message_id:
            keys.add(f"msg:{message_id}")
        if not keys:
            keys.add(f"{UNIDENTIFIED}{kind}")
    return keys


def walk_pointers(pointers: Iterable[Any]) -> Iterable[Any]:
    """Every level of every pointer — itself, its ``derived_from``, its ``facts``, its ``child`` —
    the ONE provenance walk classification, authorisation, tracing and verification all use
    (R-019 t08 #4)."""
    stack = list(pointers)
    while stack:
        pointer = stack.pop()
        yield pointer
        stack.extend(pointer_children(pointer))


#: :meth:`ExposurePolicy.document_identity` for a level whose keys name more than one document, or
#: a document key two documents answer to — no identity, so nothing can authorise it.
AMBIGUOUS = object()


def entity_key(entity_id: str) -> str:
    """``"kind:key"`` → ``"key"``. Node ids are minted as ``f"{kind}:{key}"`` (``schema.node_id``)."""
    _kind, sep, key = (entity_id or "").partition(":")
    return key if sep else entity_id


class ExposurePolicy:
    """The subject-exposure rule over one read of the graph. See the module docstring."""

    def __init__(self, view: Any, vocabulary: Optional[Vocabulary] = None) -> None:
        self._view = view
        vocab = vocabulary or active_vocabulary()
        self.vocabulary = vocab
        self.subject_kinds: frozenset[str] = frozenset(vocab.subject_kinds)
        claim_metas = CLAIM_META_KINDS | ATTESTED_META_KINDS
        self.claim_edge_kinds: frozenset[str] = frozenset(
            n for n, d in vocab.edge_kinds.items() if d.binds_to in claim_metas)
        self.document_kinds: frozenset[str] = frozenset(
            vocab.nodes_binding_to(NodeMetaKind.DOCUMENT))
        capable: set[str] = set()
        for name in self.claim_edge_kinds:
            decl = vocab.edge_kinds[name]
            dst = set(decl.dst)
            if not (ANY_KIND in dst or dst & self.subject_kinds) or not self.subject_kinds:
                continue
            src = set(decl.src)
            # A wildcard source is read as "any document-shaped kind" — a wildcard cannot mean
            # "every kind", or declaring one claim edge would withhold the whole graph.
            capable |= (self.document_kinds if ANY_KIND in src else src) - self.subject_kinds
        self.claim_capable_kinds: frozenset[str] = frozenset(capable)

        self._rows: Optional[dict[str, Any]] = None
        self._claiming: Optional[set[str]] = None
        self._subject_rows: Optional[set[tuple]] = None
        self._doc_index: Optional[tuple[set[str], set[str], dict[str, str]]] = None
        self._collided: set[str] = set()
        self._subject_keys: Optional[dict[str, set[str]]] = None
        self._pointers: dict[str, list] = {}
        self._entity: dict[str, bool] = {}

    # -- reads ---------------------------------------------------------------------------------
    def rows(self) -> dict[str, Any]:
        if self._rows is None:
            self._rows = {row["id"]: row for row in self._view.open_nodes()}
        return self._rows

    def pointers(self, entity_id: str) -> list:
        if entity_id not in self._pointers:
            self._pointers[entity_id] = list(self._view.sources_for(entity_id))
        return self._pointers[entity_id]

    def kind_of(self, entity_id: str) -> Optional[str]:
        row = self.rows().get(entity_id)
        if row is not None:
            return row["kind"]
        kind, sep, _ = (entity_id or "").partition(":")
        return kind if sep and kind in self.vocabulary.node_kinds else None

    def is_subject(self, entity_id: str) -> bool:
        return self.kind_of(entity_id) in self.subject_kinds

    def meta_of(self, kind: Optional[str]) -> Optional[NodeMetaKind]:
        decl = self.vocabulary.node_kinds.get(str(kind)) if kind else None
        return decl.binds_to if decl is not None else None

    # -- the data-driven halves -----------------------------------------------------------------
    def claiming_nodes(self) -> set[str]:
        """Nodes that are the source of an open claim edge to a subject node."""
        if self._claiming is None:
            self._claiming = set()
            if self.claim_edge_kinds and self.subject_kinds:
                for edge in self._view.open_edges():
                    if edge["kind"] not in self.claim_edge_kinds:
                        continue
                    if self.is_subject(edge["dst_id"]):
                        self._claiming.add(edge["src_id"])
                    elif self.is_subject(edge["src_id"]):
                        self._claiming.add(edge["dst_id"])
        return self._claiming

    def subject_rows(self) -> set[tuple]:
        """``(source_id, table, row_id)`` of every table cell a subject node was read from."""
        if self._subject_rows is None:
            self._subject_rows = set()
            fetchall = getattr(self._view, "fetchall", None)
            if callable(fetchall) and self.subject_kinds:
                # One read for every subject's pointers — entity AND attribute pointers, since an
                # attribute cell names the same row — instead of one query per subject row.
                marks = ",".join("?" for _ in self.subject_kinds)
                for rec in fetchall(
                        "SELECT s.source_pointer_json FROM sources s JOIN nodes n "
                        "ON n.id = s.entity_id AND n.system_to IS NULL "
                        f"WHERE n.kind IN ({marks})", tuple(sorted(self.subject_kinds))):
                    try:
                        p = json.loads(rec["source_pointer_json"])
                    except (TypeError, ValueError):
                        continue
                    if isinstance(p, dict) and p.get("kind") == "table_cell":
                        self._subject_rows.add((p.get("source_id"), p.get("table"),
                                                p.get("row_id")))
                return self._subject_rows
            for entity_id, row in self.rows().items():
                if row["kind"] not in self.subject_kinds:
                    continue
                for pointer in self._walk(self.pointers(entity_id)):
                    if getattr(pointer, "kind", None) == "table_cell":
                        self._subject_rows.add((pointer.source_id, pointer.table, pointer.row_id))
        return self._subject_rows

    def document_index(self) -> tuple[set[str], set[str], dict[str, str]]:
        """``(sensitive_keys, safe_keys, key → document node id)`` over document-shaped nodes.

        Ownership is a SET (R-019 t08 #3): a key two document nodes answer to — one filename from
        two paths, one capture under two ids — names no document. It is left out of the index
        (``collided_keys``), removed from the safe set, and, when the vocabulary can claim
        subjects at all, counted sensitive: a collision fails closed rather than letting one
        document's standing or confirmation speak for the other."""
        if self._doc_index is None:
            sensitive: set[str] = set()
            safe: set[str] = set()
            owners: dict[str, set[str]] = {}
            claiming = self.claiming_nodes()
            for entity_id, row in self.rows().items():
                if row["kind"] not in self.document_kinds:
                    continue
                keys = {f"id:{entity_key(entity_id)}"}
                if row["canonical_id"]:
                    keys.add(f"id:{row['canonical_id']}")
                for pointer in self.pointers(entity_id):
                    keys |= document_keys_of(pointer)
                keys = {k for k in keys if not k.startswith(UNIDENTIFIED)}
                for key in keys:
                    owners.setdefault(key, set()).add(entity_id)
                if row["kind"] in self.claim_capable_kinds or entity_id in claiming:
                    sensitive |= keys
                else:
                    safe |= keys
            collided = {k for k, ids in owners.items() if len(ids) > 1}
            index = {k: next(iter(ids)) for k, ids in owners.items() if len(ids) == 1}
            safe -= collided
            if self.claim_capable_kinds:
                sensitive |= collided
            self._collided = collided
            self._doc_index = (sensitive, safe, index)
        return self._doc_index

    def collided_keys(self) -> set[str]:
        self.document_index()
        return self._collided

    def document_identity(self, pointer: Any) -> Any:
        """The ONE document this level of a pointer names, as the set of names a committed row may
        cite it by — or ``None`` (it names no document) or :data:`AMBIGUOUS`.

        Ambiguous when its keys reach a collided key, reach two document nodes, or pair a document
        id the graph does not hold with a key the graph does (a capture digest borrowed from
        another document)."""
        keys = document_keys_of(pointer)
        if not keys:
            return None
        if any(k.startswith(UNIDENTIFIED) for k in keys):
            return AMBIGUOUS
        _s, _safe, index = self.document_index()
        if keys & self.collided_keys():
            return AMBIGUOUS
        nodes = {index[k] for k in keys if k in index}
        if len(nodes) > 1:
            return AMBIGUOUS
        document_id = getattr(pointer, "document_id", None)
        if nodes:
            node_id = next(iter(nodes))
            if document_id and f"id:{document_id}" not in index:
                return AMBIGUOUS
            return frozenset(self.document_names(node_id))
        if document_id:
            return frozenset({document_id})
        return AMBIGUOUS                     # a digest / item id the graph holds no document for

    def document_names(self, node_id: str) -> set[str]:
        """The names a document node answers to: the key of its id, and its canonical id."""
        names = {entity_key(node_id)}
        row = self.rows().get(node_id)
        if row is not None and row["canonical_id"]:
            names.add(row["canonical_id"])
        return names

    def level_exposed(self, pointer: Any) -> bool:
        """Does THIS level (not its children) reach material that names a subject?"""
        if getattr(pointer, "kind", None) == "table_cell":
            return bool(self.subject_kinds) and (
                (pointer.source_id, pointer.table, pointer.row_id) in self.subject_rows())
        keys = document_keys_of(pointer)
        if not keys:
            return False
        sensitive, safe, index = self.document_index()
        if keys & sensitive:
            return True
        if self.claim_capable_kinds:
            # EVERY key must be vouched for, not one of them (R-017 t08 #3): a pointer pairing a
            # safe document's id with some other capture's digest is read BY that digest, so
            # "at least one safe key" would launder whatever the digest names.
            if keys - safe:
                return True             # a document nobody can prove is not about a subject
            if len({index[k] for k in keys if k in index}) > 1:
                return True             # keys naming two different documents: no identity
        return False

    def document_node_for(self, pointer: Any) -> Optional[str]:
        """The document node a pointer's document keys name, if the graph holds one."""
        _s, _safe, index = self.document_index()
        for key in sorted(document_keys_of(pointer)):
            if key in index:
                return index[key]
        return None

    # -- the rule --------------------------------------------------------------------------------
    @staticmethod
    def _walk(pointers: Iterable[Any]) -> Iterable[Any]:
        return walk_pointers(pointers)

    def pointer_exposed(self, pointer: Any) -> bool:
        return any(self.level_exposed(p) for p in walk_pointers([pointer]))

    def entity_exposed(self, entity_id: str) -> bool:
        if entity_id in self._entity:
            return self._entity[entity_id]
        kind = self.kind_of(entity_id)
        # A stored kind the bound vocabulary does not declare cannot be classified, so it cannot be
        # shown to be a non-subject: a graph written under another pack binding fails closed.
        unclassified = kind is not None and kind not in self.vocabulary.node_kinds
        exposed = (unclassified or kind in self.subject_kinds or kind in self.claim_capable_kinds
                   or entity_id in self.claiming_nodes())
        if not exposed:
            try:
                exposed = any(self.pointer_exposed(p) for p in self.pointers(entity_id))
            except Exception:                                       # noqa: BLE001
                exposed = True                     # cannot prove it is safe — withhold
        self._entity[entity_id] = exposed
        return exposed

    def edge_exposed(self, kind: str, src_id: str, dst_id: str,
                     pointers: Iterable[Any] = ()) -> bool:
        if kind in self.claim_edge_kinds or kind not in self.vocabulary.edge_kinds:
            return True
        if self.entity_exposed(src_id) or self.entity_exposed(dst_id):
            return True
        return any(self.pointer_exposed(p) for p in pointers)

    # -- subjects by the key a confirmation records ------------------------------------------------
    def subject_node_for(self, selected: Optional[str]) -> Optional[str]:
        """The ONE subject node a committed selection names, or ``None`` (R-019 t08 #2).

        A typed selection — a full subject node id — names exactly that node. A bare key (what
        the ledger records today) names a node only when exactly one open subject node answers to
        it, by the key after ``kind:`` or by canonical id, across EVERY subject kind: the same key
        under two subject kinds, or a duplicated canonical id, names nobody rather than both."""
        if not selected:
            return None
        row = self.rows().get(selected)
        if row is not None:
            return selected if row["kind"] in self.subject_kinds else None
        if self._subject_keys is None:
            self._subject_keys = {}
            for entity_id, r in self.rows().items():
                if r["kind"] not in self.subject_kinds:
                    continue
                for key in {entity_key(entity_id), r["canonical_id"]} - {None, ""}:
                    self._subject_keys.setdefault(key, set()).add(entity_id)
        nodes = self._subject_keys.get(selected, set())
        return next(iter(nodes)) if len(nodes) == 1 else None

    def describe(self, *, include_edges: bool = True) -> dict:
        """The derived policy, for a payload that should say what it withheld and why."""
        out = {"subject_kinds": sorted(self.subject_kinds),
               "claim_capable_kinds": sorted(self.claim_capable_kinds)}
        if include_edges:
            out["claim_edge_kinds"] = sorted(self.claim_edge_kinds)
        return out


__all__ = ["AMBIGUOUS", "DOCUMENT_POINTER_KINDS", "ExposurePolicy", "UNIDENTIFIED",
           "document_keys_of", "entity_key", "pointer_children", "walk_pointers"]

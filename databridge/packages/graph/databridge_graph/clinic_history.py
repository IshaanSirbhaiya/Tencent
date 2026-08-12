"""The graph's implementation of ``databridge_intake.history.ClinicHistory``.

This is the adapter that makes the graph **load-bearing** rather than decorative: before it, nothing
in the front-desk decision path touched a traversal, and deleting the graph would have left the card
identical (PLAN.md §5 Phase 6).

The dependency direction is deliberate. ``packages/graph`` already imports ``packages/intake``, so
the port lives in intake and the implementation lives here — never the other way round.

**Every answer cites the path actually walked**, edges included. Citing only the endpoint *nodes*
looks grounded and is not: an issuer node's pointer is a row in ``coverage_rules.yaml``, which says
that insurer exists — it says nothing about this document having been issued by them. The ``ISSUED``
edge is the thing that does, so the edge's pointer is what makes the claim checkable.

**Nothing here decides anything.** These are lookups over ``FOR_PATIENT`` edges, which are *claims*
(ADR-0009) — a "prior intake" is really "a document on file that claims to be about this patient",
and ``candidate_count`` is carried so the caller can qualify how strong that claim was.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from databridge_intake.history import PriorIntake, PriorTest

from .schema import EdgeKind, GraphEdge, GraphNode, NodeKind
from .store import GraphStore

_EPOCH = dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def _node_from(row, sources: list, attr_sources: dict) -> GraphNode:
    """Build a node from a row plus PRE-FETCHED pointers, entity-level and per-attribute.

    Deliberately not `store.row_to_node`, which calls `sources_for(id)` — one query per entity, 182
    to build a snapshot of the 69-node seed. This path is on the intake critical path, so the
    pointers are fetched once in bulk and passed in.
    """
    import json

    return GraphNode(
        id=row["id"], kind=NodeKind(row["kind"]), label=row["label"],
        canonical_id=row["canonical_id"], attrs=json.loads(row["attrs_json"] or "{}"),
        attr_sources=attr_sources, sources=sources,
        valid_from=dt.datetime.fromisoformat(row["valid_from"]),
        valid_to=dt.datetime.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
        system_from=dt.datetime.fromisoformat(row["system_from"]),
        system_to=dt.datetime.fromisoformat(row["system_to"]) if row["system_to"] else None)


def _edge_from(row, sources: list, attr_sources: dict) -> GraphEdge:
    """As `_node_from`, for edges."""
    import json

    return GraphEdge(
        id=row["id"], src_id=row["src_id"], dst_id=row["dst_id"], kind=EdgeKind(row["kind"]),
        weight=row["weight"], attrs=json.loads(row["attrs_json"] or "{}"),
        attr_sources=attr_sources, sources=sources,
        valid_from=dt.datetime.fromisoformat(row["valid_from"]),
        valid_to=dt.datetime.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
        system_from=dt.datetime.fromisoformat(row["system_from"]),
        system_to=dt.datetime.fromisoformat(row["system_to"]) if row["system_to"] else None)


def _aware(value: Optional[dt.datetime]) -> Optional[dt.datetime]:
    """Normalise to UTC-aware.

    The store round-trips datetimes through SQLite text, so a naive value can come back. Comparing
    naive and aware datetimes raises ``TypeError`` — which the eligibility caller swallows, silently
    losing every finding for that candidate. A comparison that can throw is a comparison that will.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)


class _Snapshot:
    """Nodes, edges and the indexes built over them — read once, used for the whole call."""

    __slots__ = ("nodes", "edges", "issued_by", "authorises", "package_tests", "rule_package")

    def __init__(self, nodes: dict[str, GraphNode], edges: list[GraphEdge]) -> None:
        self.nodes = nodes
        self.edges = edges
        # document id → (issuer node id, the ISSUED edge). The edge is kept because it, not the
        # issuer node, is the evidence that THIS document came from THAT insurer.
        self.issued_by: dict[str, tuple[str, GraphEdge]] = {}
        # document id → list of (destination, edge). A LIST, not a single value: two open ISSUED or
        # AUTHORISES edges (a correction mid-supersession) previously resolved by "whichever row was
        # read last", so whether a conflict fired depended on row order.
        self.authorises: dict[str, list[tuple[str, GraphEdge]]] = {}
        self.package_tests: dict[str, list[tuple[str, GraphEdge]]] = {}
        self.rule_package: dict[str, list[tuple[str, GraphEdge]]] = {}

        for e in edges:
            if e.kind is EdgeKind.ISSUED:
                # Deterministic on a tie: lowest issuer node id wins, and never silently.
                existing = self.issued_by.get(e.dst_id)
                if existing is None or e.src_id < existing[0]:
                    self.issued_by[e.dst_id] = (e.src_id, e)
            elif e.kind is EdgeKind.AUTHORISES:
                src = nodes.get(e.src_id)
                if src is None:
                    continue
                if src.kind is NodeKind.DOCUMENT:
                    self.authorises.setdefault(e.src_id, []).append((e.dst_id, e))
                elif src.kind is NodeKind.COVERAGE_RULE:
                    self.rule_package.setdefault(e.src_id, []).append((e.dst_id, e))
            elif e.kind is EdgeKind.INCLUDES_TEST:
                self.package_tests.setdefault(e.src_id, []).append((e.dst_id, e))

    def documents_claiming(self, patient_node_id: str) -> list[tuple[GraphNode, GraphEdge]]:
        """(document node, claim edge) for every FOR_PATIENT edge pointing at this patient.

        Restricted to DOCUMENT sources. `FOR_PATIENT` also runs visit → patient in the vocabulary,
        and a VISIT returned as a "prior intake" would be a category error the card would render as
        a referral.
        """
        out = []
        for e in self.edges:
            if e.kind is not EdgeKind.FOR_PATIENT or e.dst_id != patient_node_id:
                continue
            doc = self.nodes.get(e.src_id)
            if doc is None or doc.kind is not NodeKind.DOCUMENT:
                continue
            out.append((doc, e))
        return out


class GraphClinicHistory:
    """Answers the history port by traversing an open snapshot of the clinic graph."""

    def __init__(self, store: GraphStore) -> None:
        self.store = store
        self._cache: Optional[tuple[tuple, _Snapshot]] = None

    def _generation(self) -> tuple:
        """A key that changes on ANY write, including a bi-temporal correction.

        Open-row COUNTS are not enough: `upsert_node`/`upsert_edge` on a fact conflict close the
        prior version and open a new one, so the open counts are identical before and after — and a
        `brain_ingest` that CORRECTED a document's issuer would have left the cache serving
        superseded values. `MAX(rowid_pk)` moves on every insert, which supersession always does.
        """
        node_max = self.store.fetchone("SELECT MAX(rowid_pk) m FROM nodes")["m"]
        edge_max = self.store.fetchone("SELECT MAX(rowid_pk) m FROM edges")["m"]
        src_max = self.store.fetchone("SELECT COUNT(*) c FROM sources")["c"]
        return (node_max, edge_max, src_max)

    def _all_sources(self) -> tuple[dict[str, list], dict[str, dict]]:
        """Every entity's pointers in ONE query — entity-level and per-attribute, kept apart.

        `row_to_node`/`row_to_edge` each call `sources_for(id)`, which is a query per entity — 182
        of them to build one snapshot of the 69-node/111-edge seed. This route is on the intake path,
        so that cost is paid on every upload and every selection.

        The two kinds must not be pooled: a `field IS NULL` pointer grounds the entity, a keyed one
        grounds exactly one attribute, and merging them is the laundering the `field` column was
        added to stop.
        """
        from databridge_core import SourcePointer

        entity: dict[str, list] = {}
        per_attr: dict[str, dict] = {}
        for row in self.store.fetchall(
                "SELECT entity_id, field, source_pointer_json FROM sources"):
            pointer = SourcePointer.model_validate_json(row["source_pointer_json"])
            if row["field"] is None:
                entity.setdefault(row["entity_id"], []).append(pointer)
            else:
                per_attr.setdefault(row["entity_id"], {})[row["field"]] = pointer
        return entity, per_attr

    def _snapshot(self) -> _Snapshot:
        """One read per graph generation, reused across candidates.

        Two problems this solves. First cost: the conflict check calls in once per candidate, so a
        full node+edge scan per call was O(candidates x graph). Second, and worse, correctness —
        nodes and edges were separate reads, so an ingest landing between them could expose an edge
        whose node was missing from the earlier read, producing a silent false negative, and
        different candidates could be evaluated against different graph versions.
        """
        key = self._generation()
        if self._cache is not None and self._cache[0] == key:
            return self._cache[1]

        sources, attr_sources = self._all_sources()
        nodes = {r["id"]: _node_from(r, sources.get(r["id"], []), attr_sources.get(r["id"], {}))
                 for r in self.store.open_nodes()}
        edges = [_edge_from(r, sources.get(r["id"], []), attr_sources.get(r["id"], {}))
                 for r in self.store.open_edges()]
        snapshot = _Snapshot(nodes, edges)
        self._cache = (key, snapshot)
        return snapshot

    @staticmethod
    def _patient_node_id(patient_id: str) -> str:
        from .clinic_seed import patient_node_id

        return patient_node_id(patient_id)

    @staticmethod
    def _document_id(doc: GraphNode) -> str:
        return doc.canonical_id or doc.label

    def _package_code_for(self, snap: _Snapshot,
                          doc: GraphNode) -> tuple[Optional[str], list]:
        """The package this document authorises, and the pointers proving it.

        Handles BOTH shapes the vocabulary allows: document → coverage_rule → package (what
        `clinic_seed` builds) and document → package directly. The first draft assumed every
        AUTHORISES destination was a rule and read `attrs["package_code"]`, which returns None for a
        package node — so a direct authorisation reported no package at all.
        """
        for dst_id, edge in snap.authorises.get(doc.id, []):
            dst = snap.nodes.get(dst_id)
            if dst is None:
                continue
            if dst.kind is NodeKind.PACKAGE:
                return (dst.canonical_id or dst.label), [*edge.sources, *dst.sources]
            if dst.kind is NodeKind.COVERAGE_RULE:
                code = dst.attrs.get("package_code")
                sources = [*edge.sources, *dst.sources]
                if code:
                    return str(code), sources
                # A document-defined rule names no package; still cite the rule we walked to.
                for pkg_id, pkg_edge in snap.rule_package.get(dst_id, []):
                    pkg = snap.nodes.get(pkg_id)
                    if pkg is not None and pkg.kind is NodeKind.PACKAGE:
                        return (pkg.canonical_id or pkg.label), [
                            *sources, *pkg_edge.sources, *pkg.sources]
                return None, sources
        return None, []

    # -- the port ------------------------------------------------------------------
    def prior_intakes_for(self, patient_id: str) -> list[PriorIntake]:
        return self._intakes(patient_id, exclude_document_id=None)

    def documents_naming(self, patient_id: str, *,
                         exclude_document_id: Optional[str] = None) -> list[PriorIntake]:
        return self._intakes(patient_id, exclude_document_id=exclude_document_id)

    def _intakes(self, patient_id: str,
                 *, exclude_document_id: Optional[str]) -> list[PriorIntake]:
        snap = self._snapshot()
        target = self._patient_node_id(patient_id)
        if target not in snap.nodes:
            return []

        out: list[PriorIntake] = []
        for doc, claim in snap.documents_claiming(target):
            document_id = self._document_id(doc)
            # Compare on BOTH the display id and the node id, so a graph whose document node lacks
            # a canonical_id cannot fail to exclude the document currently being read — and then
            # report it as conflicting with itself.
            if exclude_document_id is not None and exclude_document_id in {document_id, doc.id}:
                continue

            issuer_code: Optional[str] = None
            sources = [*claim.sources, *doc.sources]
            issued = snap.issued_by.get(doc.id)
            if issued is not None:
                issuer_node = snap.nodes.get(issued[0])
                if issuer_node is not None:
                    issuer_code = issuer_node.canonical_id
                    # The EDGE first: it is what proves this document came from this insurer.
                    sources += [*issued[1].sources, *issuer_node.sources]

            package_code, package_sources = self._package_code_for(snap, doc)
            sources += package_sources

            if not sources:
                # `PriorIntake` refuses ungrounded construction. A traversal that cannot cite its
                # path is not evidence, so it is dropped rather than reported.
                continue

            out.append(PriorIntake(
                document_id=document_id,
                patient_id=patient_id,
                issuer_code=issuer_code,
                package_code=package_code,
                seen_at=_aware(doc.valid_from),
                candidate_count=claim.attrs.get("candidate_count"),
                sources=sources))

        out.sort(key=lambda p: (_aware(p.seen_at) or _EPOCH, p.document_id), reverse=True)
        return out

    def tests_authorised_for(self, patient_id: str, *,
                             since: Optional[dt.datetime] = None) -> list[PriorTest]:
        """Tests any document on file authorised for this patient.

        Walks ``patient ←FOR_PATIENT— document —REQUESTS_TEST→ test``, plus the contents of whatever
        package the document authorised. A test the patient was sent for is a test they were sent
        for, whether it was printed on the letter or came from the package definition.
        """
        snap = self._snapshot()
        target = self._patient_node_id(patient_id)
        if target not in snap.nodes:
            return []
        bound = _aware(since)

        claims = {doc.id: (doc, edge) for doc, edge in snap.documents_claiming(target)}
        if not claims:
            return []

        out: list[PriorTest] = []
        seen: set[tuple[str, str]] = set()

        def add(doc: GraphNode, claim: GraphEdge, test_id: str, path: list) -> None:
            test = snap.nodes.get(test_id)
            if test is None or test.kind is not NodeKind.TEST:
                return
            when = _aware(doc.valid_from)
            # An undated document cannot be SHOWN to satisfy a `since` bound, so it is excluded
            # rather than admitted. "We do not know when" is not "recently".
            if bound is not None and (when is None or when < bound):
                return
            document_id = self._document_id(doc)
            key = (document_id, test.canonical_id or test.id)
            if key in seen:
                return
            sources = [*claim.sources, *doc.sources, *test.sources, *path]
            if not sources:
                return
            seen.add(key)
            out.append(PriorTest(
                canonical=test.canonical_id or test.id.split(":", 1)[-1],
                as_printed=test.label,
                document_id=document_id,
                performed_at=when,
                sources=sources))

        for e in snap.edges:
            if e.kind is EdgeKind.REQUESTS_TEST and e.src_id in claims:
                doc, claim = claims[e.src_id]
                add(doc, claim, e.dst_id, list(e.sources))

        # …and the package contents the document authorised, citing every hop of the way there.
        for doc_id, (doc, claim) in claims.items():
            for dst_id, auth_edge in snap.authorises.get(doc_id, []):
                dst = snap.nodes.get(dst_id)
                if dst is None:
                    continue
                if dst.kind is NodeKind.PACKAGE:
                    packages = [(dst_id, [*auth_edge.sources, *dst.sources])]
                elif dst.kind is NodeKind.COVERAGE_RULE:
                    packages = []
                    for pkg_id, pkg_edge in snap.rule_package.get(dst_id, []):
                        pkg = snap.nodes.get(pkg_id)
                        if pkg is None:
                            continue
                        packages.append((pkg_id, [*auth_edge.sources, *dst.sources,
                                                  *pkg_edge.sources, *pkg.sources]))
                else:
                    continue
                for package_id, path in packages:
                    for test_id, includes_edge in snap.package_tests.get(package_id, []):
                        add(doc, claim, test_id, [*path, *includes_edge.sources])

        out.sort(key=lambda t: (_aware(t.performed_at) or _EPOCH, t.canonical), reverse=True)
        return out

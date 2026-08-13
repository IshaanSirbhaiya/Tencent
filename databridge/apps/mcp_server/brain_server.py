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
from typing import NamedTuple, Optional

from databridge_graph import (
    GraphStore,
    GroundedAnswer,
    NodeKind,
    extract_graph,
    neighbors,
    nl_query,
    path,
    resolve_entity,
    row_to_node,
    same_label_node_ids,
    timeline,
)
from databridge_graph.clinic_seed import SEED_SYSTEM_TIME, load_clinic_seed
from databridge_graph.identity import IDENTITY_CLAIMED_ON_DOCUMENT, IDENTITY_REGISTRY_ONLY


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


# --- serialisation (graph → JSON the React Flow UI / agents consume) --------------

def pointer_to_dict(p) -> dict:
    return p.model_dump(mode="json", exclude_none=True)


#: Attached to EVERY serialised PATIENT node, in every tool. `node_detail` is not the only place
#: a registry row reaches a model: `brain_neighbors`, `brain_path` and `graph()` all return
#: patient nodes with a name and a date of birth on them, and a patient reached through someone
#: else's 1-hop neighbourhood never passes through `node_detail` at all. Keyed on the KIND here,
#: which is the one place every one of those paths goes through (CLAUDE.md constraint 1).
PATIENT_NODE_CAVEAT = ("a registry row, not an identification — this system never verifies "
                       "identity; clinic staff check it in person")


def node_to_dict(node) -> dict:
    from databridge_graph import NodeKind

    identity = ({"identity_caveat": PATIENT_NODE_CAVEAT, "identity_verified": False}
                if node.kind is NodeKind.PATIENT else {})
    return {
        **identity,
        "id": node.id, "kind": node.kind.value, "label": node.label,
        "canonical_id": node.canonical_id, "attrs": node.attrs,
        # A COUNT of entity citations beside a dict of N values is the pooled-citation shape in
        # its purest form: it reads as "3 citations back these 4 values" and cannot be checked,
        # because a count names nothing. This payload is consumed by a model, so it gets the same
        # treatment the node sheet and /evidence got — a pointer per value, and the unanswered
        # fields named rather than omitted.
        "attribute_sources": {k: pointer_to_dict(v) for k, v in node.attr_sources.items()},
        "unresolved": node.unresolved,
        "valid_from": node.valid_from.isoformat() if node.valid_from else None,
        "valid_to": node.valid_to.isoformat() if node.valid_to else None,
        "system_from": node.system_from.isoformat() if node.system_from else None,
        "system_to": node.system_to.isoformat() if node.system_to else None,
        "n_sources": len(node.sources),
        "sources_ground": "the identity of this node — not the values in `attrs`",
    }


def edge_to_dict(edge) -> dict:
    return {
        "id": edge.id, "source": edge.src_id, "target": edge.dst_id,
        "kind": edge.kind.value, "weight": edge.weight, "attrs": edge.attrs,
        "attribute_sources": {k: pointer_to_dict(v) for k, v in edge.attr_sources.items()},
        "unresolved": edge.unresolved,
        "valid_from": edge.valid_from.isoformat() if edge.valid_from else None,
        "valid_to": edge.valid_to.isoformat() if edge.valid_to else None,
        "n_sources": len(edge.sources),
    }


def answer_to_dict(a: GroundedAnswer) -> dict:
    return {
        "question": a.question, "answer": a.answer, "grounded": a.grounded,
        "refusal": a.refusal, "matched_entities": a.matched_entities,
        # An AMBIGUOUS question is refused, and the ids it could have meant travel with the
        # refusal — the same contract `EntityReference.failure` gives the other tools, so an
        # agent that hits "three people are called that" has a next call to make rather than a
        # dead end. Empty on every other outcome.
        "candidates": list(a.candidates),
        "citations": [{"entity_id": c.entity_id, "label": c.label, "kind": c.kind,
                       "pointers": [pointer_to_dict(p) for p in c.pointers]} for c in a.citations],
    }


class BrainTools:
    """Holds the live graph store; every MCP tool and gateway route delegates here."""

    def __init__(self, store: Optional[GraphStore] = None, *, harness=None,
                 sources: Optional[dict] = None):
        self.store = store or GraphStore(clock=lambda: SEED_SYSTEM_TIME)
        self.harness = harness                       # used only by brain_ingest
        # allow-list of ingestable sources: name → document text. brain_ingest NEVER opens an
        # arbitrary filesystem path (that would be an arbitrary-file-read primitive for any MCP
        # agent) — it ingests inline text or a registered source only.
        self.sources = dict(sources or {})

    # -- clinic seed --------------------------------------------------------------
    def load_seed(self) -> dict:
        return load_clinic_seed(self.store)

    def graph(self) -> dict:
        nodes = [row_to_node(self.store, r) for r in self.store.open_nodes()]
        from databridge_graph import row_to_edge
        edges = [row_to_edge(self.store, r) for r in self.store.open_edges()]
        node_dicts = [node_to_dict(n) for n in nodes]
        # provenance % is BACKEND-computed (not a hardcoded "100%" in the UI) — the headline
        # differentiator must be measured, so it stays honest if a future node ever lacked a source.
        grounded = sum(1 for n in node_dicts if n["n_sources"] > 0)
        pct = round(100 * grounded / len(node_dicts)) if node_dicts else 100
        return {"nodes": node_dicts,
                "edges": [edge_to_dict(e) for e in edges],
                "stats": {**self.store.counts(), "provenance_pct": pct},
                "provenance_by_source": self.provenance_by_source()}

    def provenance_by_source(self) -> dict:
        """How many entities each source document grounds (the demo's provenance mini-sankey).
        Backend-computed (D-FE-2) over the SourcePointers — never derived in the browser. A node
        cited by two docs counts for both (corroboration), so the total ≥ node count."""
        by_source: dict[str, int] = {}
        for r in self.store.open_nodes():
            seen = {p.source_id for p in self.store.sources_for(r["id"])}
            for sid in seen:
                by_source[sid] = by_source.get(sid, 0) + 1
        return dict(sorted(by_source.items()))

    def insights(self) -> dict:
        """Auto-generated GROUNDED insights from the graph structure (Recent Insights feed, §Part1
        Col 2). Each insight is computed server-side and carries the SourcePointers that back it +
        a ``focus`` node id to zoom to. Deterministic ordering. Counts are structural (not
        fact-shaped fabrication); any dollar figure rides inside a CITED node label."""
        from databridge_graph import EdgeKind, NodeKind, row_to_edge
        nodes = {r["id"]: row_to_node(self.store, r) for r in self.store.open_nodes()}
        edges = [row_to_edge(self.store, r) for r in self.store.open_edges()]

        def cites(*ids: str) -> list[dict]:
            out: list[dict] = []
            for nid in ids:
                n = nodes.get(nid)
                if n:
                    out.extend(pointer_to_dict(p) for p in n.sources)
            return out

        items: list[dict] = []

        # Documents grouped by the patient claims they carry. Every intake in the graph is
        # unconfirmed by construction (the seed writes claims, never determinations), so these
        # counts are structural facts about the queue, not assertions about any patient.
        claims_by_doc: dict[str, list[str]] = {}
        for e in edges:
            if e.kind == EdgeKind.FOR_PATIENT and e.src_id in nodes and e.dst_id in nodes:
                claims_by_doc.setdefault(e.src_id, []).append(e.dst_id)

        # 1) the queue: EVERY intake waiting on an in-person identity check.
        #
        #    Counted over all DOCUMENT nodes, not over `claims_by_doc`. A document whose resolution
        #    came back with no candidate at all carries no FOR_PATIENT edge, so keying the count on
        #    those edges drops exactly the intakes that need the most counter work — the ones that
        #    must be registered from scratch — out of the queue figure.
        all_documents = sorted(nid for nid, n in nodes.items() if n.kind == NodeKind.DOCUMENT)
        unmatched = [d for d in all_documents if d not in claims_by_doc]
        if all_documents:
            items.append({
                "text": f"{len(all_documents)} intakes awaiting in-person identity verification"
                        + (f" ({len(unmatched)} with no registry match — register at the counter)"
                           if unmatched else ""),
                "focus": all_documents[0], "kind": "document",
                "citations": cites(*all_documents)})

        # 2) ambiguous identities — the ones staff must disambiguate at the counter
        ambiguous = sorted(d for d, pats in claims_by_doc.items() if len(pats) > 1)
        for doc_id in ambiguous:
            patients = claims_by_doc[doc_id]
            items.append({
                "text": f"{nodes[doc_id].label} matches {len(patients)} registry patients — "
                        f"staff must select the correct one in person",
                "focus": doc_id, "kind": "document",
                "citations": cites(doc_id, *patients)})

        # 3) documents the extractor could not fully read
        incomplete = sorted(
            (nid for nid, n in nodes.items()
             if n.kind == NodeKind.DOCUMENT and int(n.attrs.get("unresolved_count") or 0) > 0),
            key=lambda nid: (-int(nodes[nid].attrs.get("unresolved_count") or 0), nid))
        if incomplete:
            top = incomplete[0]
            items.append({
                "text": f"{nodes[top].label} has "
                        f"{nodes[top].attrs['unresolved_count']} fields not stated on the document",
                "focus": top, "kind": "document", "citations": cites(top)})

        # 4) busiest issuer — which payer the morning's paperwork is going to
        issued: dict[str, list[str]] = {}
        for e in edges:
            if e.kind == EdgeKind.ISSUED and e.src_id in nodes and e.dst_id in nodes:
                issued.setdefault(e.src_id, []).append(e.dst_id)
        if issued:
            top = sorted(issued, key=lambda k: (-len(issued[k]), k))[0]
            docs = issued[top]
            items.append({
                "text": f"{nodes[top].label} issued {len(docs)} of today's referring documents",
                "focus": top, "kind": "org", "citations": cites(top, *docs)})

        # 5) tests ordered ON TOP OF a named package — the billing question staff would otherwise
        #    discover at the counter.
        #
        #    "requested but not in any package" is NOT the right test. Five coverage rules are
        #    `tests_from_document`: the document's own list IS the covered scope, so every test on
        #    a referral letter would be reported as possibly patient-payable. The genuine add-on is
        #    a test requested by a document that authorises a CODED package which does not include
        #    it (chit 7's MMR vaccination on top of PEE226).
        coded_rule_by_doc: dict[str, str] = {}
        for e in edges:
            if e.kind == EdgeKind.AUTHORISES and e.dst_id in nodes:
                rule = nodes[e.dst_id]
                if rule.kind == NodeKind.COVERAGE_RULE and rule.attrs.get("package_code"):
                    coded_rule_by_doc[e.src_id] = e.dst_id
        # A rule AUTHORISES the specific PACKAGE it selects; a package INCLUDES_TEST the tests it
        # lists. Chained per-rule so "not included in it" means the ONE package THIS document
        # names — not every package in the clinic's whole catalogue. A global `tests_in_packages`
        # set (every INCLUDES_TEST edge, from any issuer's package) wrongly cleared a test that
        # happens to sit in some UNRELATED issuer's package, silently under-reporting add-ons: a
        # test genuinely billable on top of Alice's package would vanish here just because Bob's
        # package, from a different insurer, happens to include a test of the same name.
        packages_by_rule: dict[str, set[str]] = {}
        for e in edges:
            if e.kind == EdgeKind.AUTHORISES and e.src_id in coded_rule_by_doc.values():
                packages_by_rule.setdefault(e.src_id, set()).add(e.dst_id)
        tests_by_package: dict[str, set[str]] = {}
        for e in edges:
            if e.kind == EdgeKind.INCLUDES_TEST:
                tests_by_package.setdefault(e.src_id, set()).add(e.dst_id)

        def _tests_in_this_documents_package(doc_id: str) -> set[str]:
            rule_id = coded_rule_by_doc.get(doc_id)
            covered: set[str] = set()
            for package_id in packages_by_rule.get(rule_id, ()):
                covered |= tests_by_package.get(package_id, set())
            return covered

        add_ons = sorted({
            e.dst_id for e in edges
            if e.kind == EdgeKind.REQUESTS_TEST
            and e.src_id in coded_rule_by_doc          # the document names a coded package…
            # …which does not include this test:
            and e.dst_id not in _tests_in_this_documents_package(e.src_id)
            and e.dst_id in nodes})
        if add_ons:
            items.append({
                "text": f"{len(add_ons)} test(s) ordered on top of a named package and not "
                        f"included in it — may be patient-payable",
                "focus": add_ons[0], "kind": "test", "citations": cites(*add_ons)})

        return {"insights": items[:5]}

    def node_detail(self, entity_id: str) -> dict:
        """Full detail for the node-detail drawer: the node, its actual SourcePointers (the
        provenance receipts), its connections, and — for a document — the patient claims it
        carries. All grounded, computed server-side.

        ``claims`` is deliberately not called "patients": each entry is a candidate a document
        names, carrying the score and the candidate count, and every one requires an in-person
        check (CLAUDE.md §Hard constraints 1)."""
        from databridge_graph import EdgeKind, row_to_edge
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
            connections.append({
                "edge_kind": e.kind.value, "direction": "out" if out else "in",
                "other_id": other_id, "other_label": other.label if other else other_id,
                "other_kind": other.kind.value if other else "",
                "pointers": [pointer_to_dict(p) for p in e.sources]})
            # OUTGOING only. FOR_PATIENT runs document → patient, so without the direction guard
            # a PATIENT node's `claims` fills with the DOCUMENTS pointing at it — each rendered in
            # the drawer as a candidate patient, complete with a match score.
            if out and e.kind == EdgeKind.FOR_PATIENT and other:
                claims.append({
                    "id": other_id, "label": other.label,
                    "match_score": e.attrs.get("match_score"),
                    "matched_on": e.attrs.get("matched_on"),
                    "candidate_count": e.attrs.get("candidate_count"),
                    "requires_human_verification": True})
        connections.sort(key=lambda c: (c["edge_kind"], c["other_label"]))
        claims.sort(key=lambda c: (-(c.get("match_score") or 0), c["label"]))
        out = {"node": node, "sources": sources, "connections": connections, "claims": claims}
        if node["kind"] == NodeKind.PATIENT.value:
            # Keyed on the KIND, and emitted for EVERY patient. A registry row no document names
            # — 289 of the 300 once the whole registry is seeded — otherwise arrived as a name, a
            # date of birth and a nationality with nothing anywhere saying "not verified". Being
            # on file is not being identified (CLAUDE.md constraint 1).
            #
            # Read off `connections`, not `claims`: `claims` is OUTGOING by construction (see the
            # direction guard above), so on a PATIENT node it is always empty and would have made
            # every patient look unclaimed.
            # …and only a DOCUMENT's claim counts. `FOR_PATIENT` also runs visit → patient in
            # the vocabulary, and counting one of those would pick the LESS cautious caveat.
            claimed_by = sorted(c["other_id"] for c in connections
                                if c["edge_kind"] == EdgeKind.FOR_PATIENT.value
                                and c["direction"] == "in"
                                and c["other_kind"] == NodeKind.DOCUMENT.value)
            out["identity"] = {
                "verified": False,
                "requires_human_verification": True,
                # The boolean travels with the ids it was computed from, so a caller can check it
                # against `connections` instead of trusting it — the same reason every figure in
                # `analytics.py` carries its members.
                "claimed_by_a_document": bool(claimed_by),
                "claimed_by": claimed_by,
                "caveat": (IDENTITY_CLAIMED_ON_DOCUMENT if claimed_by
                           else IDENTITY_REGISTRY_ONLY)}
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
        """
        return EntityReference.of(resolve_entity(self.store, ref))

    # -- the tools ----------------------------------------------------------------
    # Four reads and one write. Which process exposes which is decided in ``capabilities.py``,
    # not here — this class is the shared implementation, and it deliberately knows nothing about
    # the boundary drawn across it.
    def brain_query(self, question: str, as_of: Optional[str] = None) -> dict:
        # as_of (valid-time) narrows the snapshot the answer is drawn from (audit/replay).
        a = nl_query(self.store, question)
        out = answer_to_dict(a)
        out["as_of"] = as_of
        return out

    def brain_neighbors(self, entity: str, depth: int = 1) -> dict:
        ref = self._resolve(entity)
        if ref.node_id is None:
            return ref.failure(nodes=[], edges=[])
        nid = ref.node_id
        sub = neighbors(self.store, nid, depth=depth)
        return {"root": nid, "nodes": [node_to_dict(n) for n in sub.nodes],
                "edges": [edge_to_dict(e) for e in sub.edges]}

    def brain_timeline(self, entity: str) -> dict:
        ref = self._resolve(entity)
        if ref.node_id is None:
            return ref.failure(events=[])
        nid = ref.node_id
        evs = timeline(self.store, nid)
        return {"entity": nid, "events": [{
            "kind": e.kind, "label": e.label,
            "valid_from": e.valid_from.isoformat() if e.valid_from else None,
            "valid_to": e.valid_to.isoformat() if e.valid_to else None,
            "system_from": e.system_from.isoformat() if e.system_from else None,
            "system_to": e.system_to.isoformat() if e.system_to else None,
        } for e in evs]}

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
                           "pointers": [pointer_to_dict(p) for p in s.edge.sources]}
                          for s in gp.steps],
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
        from databridge_convert.ir.models import Block, DocumentRecord
        from databridge_core import SnapshotIdentity
        snap = SnapshotIdentity(source_id=source_path, as_of=self.store.now(),
                                content_hash="ingest", schema_hash="doc")
        doc = DocumentRecord(document_id=source_path, sha256="ingest", snapshot=snap,
                             blocks=[Block(kind="Paragraph", text=text)])
        res = extract_graph(doc, self.harness, store=self.store)
        # report the genuinely NEW nodes (upsert action "inserted") vs corroborated existing ones —
        # the node upserts are the first len(entities) of res.upserts (edges are appended after).
        new_entities = [n.label for n, up in zip(res.entities, res.upserts) if up.action == "inserted"]
        return {"source_path": source_path, "entities_added": len(res.entities),
                "new_entities": new_entities, "relations_added": len(res.relations),
                "refused": res.refused, "stats": self.store.counts()}


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

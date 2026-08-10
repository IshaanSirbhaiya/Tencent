"""Company-Brain MCP server (T08 Part 3, 04_PRD §9 — agent access, read-only by design).

Exposes the brain to AI agents over MCP:

  brain_query(question, as_of?)     → grounded answer + citations
  brain_neighbors(entity, depth?)   → subgraph JSON
  brain_ingest(source_path)         → IngestResult (the one write path: ingestion only)
  brain_timeline(entity)            → bi-temporal history
  brain_path(from_entity, to_entity)→ provenance path

"One brain, two mouths" (mirrors the gateway): the MCP tools and the FastAPI ``/brain/*`` routes
both call the SAME ``BrainTools`` methods — no logic forks. Tools are read-only except
``brain_ingest`` (single-writer integrity, 04_PRD §9). The actual ``mcp`` SDK is wired only if
installed; the tool functions are plain Python so the gateway and tests use them with no server.

Every answer value traces to a graph node/edge SourcePointer (P4); ``nl_query`` runs the answer
through C17's ``claim_check`` so an ungrounded quantity is refused, never narrated.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from databridge_graph import (
    GraphStore,
    GroundedAnswer,
    extract_graph,
    link_entities,
    neighbors,
    nl_query,
    path,
    row_to_node,
    timeline,
)
from databridge_graph.demo_seed import SEED_SYSTEM_TIME, load_demo_seed


# --- serialisation (graph → JSON the React Flow UI / agents consume) --------------
def node_to_dict(node) -> dict:
    return {
        "id": node.id, "kind": node.kind.value, "label": node.label,
        "canonical_id": node.canonical_id, "attrs": node.attrs,
        "valid_from": node.valid_from.isoformat() if node.valid_from else None,
        "valid_to": node.valid_to.isoformat() if node.valid_to else None,
        "system_from": node.system_from.isoformat() if node.system_from else None,
        "system_to": node.system_to.isoformat() if node.system_to else None,
        "n_sources": len(node.sources),
    }


def edge_to_dict(edge) -> dict:
    return {
        "id": edge.id, "source": edge.src_id, "target": edge.dst_id,
        "kind": edge.kind.value, "weight": edge.weight, "attrs": edge.attrs,
        "valid_from": edge.valid_from.isoformat() if edge.valid_from else None,
        "valid_to": edge.valid_to.isoformat() if edge.valid_to else None,
        "n_sources": len(edge.sources),
    }


def pointer_to_dict(p) -> dict:
    return p.model_dump(mode="json", exclude_none=True)


def answer_to_dict(a: GroundedAnswer) -> dict:
    return {
        "question": a.question, "answer": a.answer, "grounded": a.grounded,
        "refusal": a.refusal, "matched_entities": a.matched_entities,
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

    # -- demo seed ----------------------------------------------------------------
    def load_seed(self) -> dict:
        return load_demo_seed(self.store)

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
        # 1) most active decider
        decided: dict[str, list[str]] = {}
        for e in edges:
            # only count deciders whose node is OPEN (a superseded/closed decider isn't in `nodes`;
            # selecting it would KeyError on nodes[top]). Guard both src and dst.
            if e.kind == EdgeKind.DECIDED and e.src_id in nodes and e.dst_id in nodes:
                decided.setdefault(e.src_id, []).append(e.dst_id)
        if decided:
            top = sorted(decided, key=lambda k: (-len(decided[k]), k))[0]
            decs = decided[top]
            items.append({"text": f"{nodes[top].label} made {len(decs)} decisions",
                          "focus": top, "kind": "person", "citations": cites(top, *decs)})
        # 2) funded + active projects
        for e in sorted([e for e in edges if e.kind == EdgeKind.FUNDS], key=lambda e: e.dst_id):
            proj, dec = nodes.get(e.dst_id), nodes.get(e.src_id)
            if proj:
                status = proj.attrs.get("status", "")
                items.append({"text": f"{proj.label} is funded and {status}", "focus": proj.id,
                              "kind": "project", "detail": dec.label if dec else "",
                              "citations": cites(proj.id, e.src_id)})
        # 3) priority flags (decisions whose label mentions priority)
        for nid, n in sorted(nodes.items()):
            if n.kind == NodeKind.DECISION and "priorit" in n.label.lower():
                items.append({"text": n.label, "focus": nid, "kind": "decision",
                              "citations": cites(nid)})
        # 4) blocked projects
        for e in sorted([e for e in edges if e.kind == EdgeKind.BLOCKS], key=lambda e: e.dst_id):
            proj, dec = nodes.get(e.dst_id), nodes.get(e.src_id)
            if proj:
                who = dec.attrs.get("made_by", "a decision") if dec else "a decision"
                items.append({"text": f"{proj.label} is blocked by {who}'s decision",
                              "focus": proj.id, "kind": "decision", "citations": cites(proj.id, e.src_id)})
        # 5) project ownership
        owns: dict[str, list[str]] = {}
        for e in edges:
            if e.kind == EdgeKind.OWNS and e.src_id in nodes and e.dst_id in nodes:
                owns.setdefault(e.src_id, []).append(e.dst_id)
        if owns:
            def active_count(k: str) -> int:
                return sum(1 for p in owns[k] if nodes.get(p) and nodes[p].attrs.get("status") == "active")
            # surface the owner of the most ACTIVE work (deterministic: -active, -total, id)
            top = sorted(owns, key=lambda k: (-active_count(k), -len(owns[k]), k))[0]
            ps, active = owns[top], active_count(top)
            items.append({"text": f"{nodes[top].label} owns {active} active project"
                                  + ("s" if active != 1 else ""),
                          "focus": top, "kind": "person", "citations": cites(top, *ps)})
        return {"insights": items[:5]}

    def node_detail(self, entity_id: str) -> dict:
        """Full detail for the node-detail drawer (§Part 3): the node, its actual SourcePointers
        (the provenance receipts), its connections, and — for a person — the decisions it made.
        All grounded, computed server-side (D-FE-2)."""
        from databridge_graph import EdgeKind, row_to_edge
        nid = self._resolve(entity_id)
        if nid is None:
            return {"error": f"no entity matched {entity_id!r}"}
        row = self.store.open_node_row(nid)
        if row is None:
            return {"error": f"no open node {nid!r}"}
        node = node_to_dict(row_to_node(self.store, row))
        sources = [pointer_to_dict(p) for p in self.store.sources_for(nid)]
        by_id = {r["id"]: row_to_node(self.store, r) for r in self.store.open_nodes()}
        edges = [row_to_edge(self.store, r) for r in self.store.open_edges()]
        connections, decisions = [], []
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
            if out and e.kind == EdgeKind.DECIDED and other:
                decisions.append({"id": other_id, "label": other.label,
                                  "made_at": other.attrs.get("made_at")})
        connections.sort(key=lambda c: (c["edge_kind"], c["other_label"]))
        decisions.sort(key=lambda d: (d.get("made_at") or "", d["label"]))
        return {"node": node, "sources": sources, "connections": connections, "decisions": decisions}

    # -- entity reference resolution ---------------------------------------------
    def _resolve(self, ref: str) -> Optional[str]:
        """Accept a node id OR a natural-language name; return the node id (or None)."""
        if self.store.open_node_row(ref) is not None:
            return ref
        linked = link_entities(self.store, ref, top_k=1)
        return linked[0].id if linked else None

    # -- the five MCP tools -------------------------------------------------------
    def brain_query(self, question: str, as_of: Optional[str] = None) -> dict:
        # as_of (valid-time) narrows the snapshot the answer is drawn from (audit/replay).
        a = nl_query(self.store, question)
        out = answer_to_dict(a)
        out["as_of"] = as_of
        return out

    def brain_neighbors(self, entity: str, depth: int = 1) -> dict:
        nid = self._resolve(entity)
        if nid is None:
            return {"error": f"no entity matched {entity!r}", "nodes": [], "edges": []}
        sub = neighbors(self.store, nid, depth=depth)
        return {"root": nid, "nodes": [node_to_dict(n) for n in sub.nodes],
                "edges": [edge_to_dict(e) for e in sub.edges]}

    def brain_timeline(self, entity: str) -> dict:
        nid = self._resolve(entity)
        if nid is None:
            return {"error": f"no entity matched {entity!r}", "events": []}
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
        if src is None or dst is None:
            return {"error": "endpoint not found", "found": False, "steps": []}
        gp = path(self.store, src, dst)
        return {"from": src, "to": dst, "found": gp.found,
                "steps": [{"edge_kind": s.edge.kind.value, "to": s.to_node.id,
                           "to_label": s.to_node.label,
                           "pointers": [pointer_to_dict(p) for p in s.edge.sources]}
                          for s in gp.steps],
                "provenance_pointers": [pointer_to_dict(p) for p in gp.pointers()]}

    def brain_ingest(self, source_path: str, *, text: Optional[str] = None) -> dict:
        """The single write path (04_PRD §9). Build a DocumentRecord from ``text`` (inline) or a
        REGISTERED source (``self.sources[source_path]``) and extract a grounded subgraph into the
        store. NEVER opens an arbitrary filesystem path — an MCP agent calling
        ``brain_ingest("/etc/passwd")`` gets a refusal, not a file read."""
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


# --- optional MCP SDK wiring (only if `mcp` is installed) --------------------------
def build_mcp_server(tools: Optional[BrainTools] = None):
    """Construct an MCP ``Server`` exposing the five tools. Imports the SDK lazily so the module
    is usable (gateway + tests) without `mcp` installed."""
    from mcp.server.fastmcp import FastMCP  # type: ignore

    if tools is None:
        # default: demo seed + a gold extraction harness + the demo source registered, so every
        # tool (including brain_ingest) works out of the box for an agent connecting over MCP.
        from databridge_evals.e_brain import FIXTURE_TEXT, gold_harness
        tools = BrainTools(harness=gold_harness(), sources={"demo_sample.txt": FIXTURE_TEXT})
    tools.load_seed()
    mcp = FastMCP("databridge-brain")

    @mcp.tool()
    def brain_query(question: str, as_of: Optional[str] = None) -> dict:
        return tools.brain_query(question, as_of)

    @mcp.tool()
    def brain_neighbors(entity: str, depth: int = 1) -> dict:
        return tools.brain_neighbors(entity, depth)

    @mcp.tool()
    def brain_ingest(source_path: str) -> dict:
        return tools.brain_ingest(source_path)

    @mcp.tool()
    def brain_timeline(entity: str) -> dict:
        return tools.brain_timeline(entity)

    @mcp.tool()
    def brain_path(from_entity: str, to_entity: str) -> dict:
        return tools.brain_path(from_entity, to_entity)

    return mcp


def main() -> None:  # pragma: no cover (entrypoint)
    build_mcp_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()

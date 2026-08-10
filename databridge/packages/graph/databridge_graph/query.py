"""Graph queries (T08, 04_PRD §8) — all LLM-free up to synthesis.

  as_of(valid_time, system_time)   → point-in-time bi-temporal snapshot
  neighbors(node_id, edge_kind, depth) → subgraph (BFS over open edges)
  nl_query(question)               → entity-linking → subgraph → GROUNDED answer
  path(src_id, dst_id)             → shortest provenance path
  timeline(entity_id)              → bi-temporal version history

`nl_query` is the load-bearing one. It is deterministic (no model in the retrieval loop, the
gbrain proof point, 04_PRD §8) and every quantity/date in the answer is checked by **C17's
``claim_check``** against the cited graph nodes' grounded values. A number that does not equal a
cited fact ⇒ the answer is REFUSED, not emitted (P4 / anti-fabrication). Free-text comes only from
grounded node labels, each of which carries a SourcePointer.
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from databridge_convert.generate.claim_check import check_claims
from databridge_core import SourcePointer

from .schema import EdgeKind, GraphEdge, GraphNode, NodeKind
from .store import GraphStore, row_to_edge, row_to_node


# --- snapshot (bi-temporal as_of) -------------------------------------------------
def _live_at(row, vt: datetime, st: datetime) -> bool:
    from .store import _iso, _parse  # local import to reuse the canonical ISO codec
    vf, vto = _parse(row["valid_from"]), _parse(row["valid_to"])
    sf, sto = _parse(row["system_from"]), _parse(row["system_to"])
    return (vf <= vt and (vto is None or vto > vt)
            and sf <= st and (sto is None or sto > st))


@dataclass
class Snapshot:
    valid_time: datetime
    system_time: datetime
    nodes: list[GraphNode]
    edges: list[GraphEdge]


def as_of(store: GraphStore, valid_time: datetime, system_time: datetime) -> Snapshot:
    """What the graph held to be true at ``valid_time``, as believed at ``system_time``."""
    # materialise rows under the lock FIRST (store.fetchall), then build — never iterate a live
    # cursor while issuing sources_for() queries on the same shared connection.
    node_rows = store.fetchall("SELECT * FROM nodes")
    edge_rows = store.fetchall("SELECT * FROM edges")
    nodes = [row_to_node(store, r) for r in node_rows if _live_at(r, valid_time, system_time)]
    edges = [row_to_edge(store, r) for r in edge_rows if _live_at(r, valid_time, system_time)]
    return Snapshot(valid_time, system_time, nodes, edges)


# --- neighbors (subgraph) ---------------------------------------------------------
@dataclass
class Subgraph:
    nodes: list[GraphNode]
    edges: list[GraphEdge]

    def node_ids(self) -> set[str]:
        return {n.id for n in self.nodes}


def neighbors(store: GraphStore, node_id: str, *, edge_kind: Optional[EdgeKind] = None,
              depth: int = 1) -> Subgraph:
    """BFS up to ``depth`` hops over the OPEN graph (edges traversed in both directions for
    connectivity). ``edge_kind`` filters to one relationship type when set."""
    open_edges = [row_to_edge(store, r) for r in store.open_edges()]
    if edge_kind is not None:
        open_edges = [e for e in open_edges if e.kind == edge_kind]
    by_node: dict[str, list[GraphEdge]] = {}
    for e in open_edges:
        by_node.setdefault(e.src_id, []).append(e)
        by_node.setdefault(e.dst_id, []).append(e)

    seen_nodes, seen_edges = {node_id}, set()
    out_edges: list[GraphEdge] = []
    frontier = deque([(node_id, 0)])
    while frontier:
        nid, d = frontier.popleft()
        if d >= depth:
            continue
        for e in by_node.get(nid, []):
            if e.id not in seen_edges:
                seen_edges.add(e.id)
                out_edges.append(e)
            other = e.dst_id if e.src_id == nid else e.src_id
            if other not in seen_nodes:
                seen_nodes.add(other)
                frontier.append((other, d + 1))

    nodes = [row_to_node(store, r) for r in store.open_nodes() if r["id"] in seen_nodes]
    return Subgraph(nodes, out_edges)


# --- path (shortest provenance path) ----------------------------------------------
@dataclass
class PathStep:
    edge: GraphEdge
    to_node: GraphNode


@dataclass
class GraphPath:
    src_id: str
    dst_id: str
    steps: list[PathStep]
    found: bool

    def pointers(self) -> list[SourcePointer]:
        out: list[SourcePointer] = []
        for s in self.steps:
            out.extend(s.edge.sources)
        return out


def path(store: GraphStore, src_id: str, dst_id: str) -> GraphPath:
    """Shortest connecting path (BFS, edges undirected for connectivity). Each step carries the
    edge's SourcePointers — the path IS a provenance chain."""
    open_edges = [row_to_edge(store, r) for r in store.open_edges()]
    adj: dict[str, list[GraphEdge]] = {}
    for e in open_edges:
        adj.setdefault(e.src_id, []).append(e)
        adj.setdefault(e.dst_id, []).append(e)

    prev: dict[str, tuple[str, GraphEdge]] = {}
    q = deque([src_id])
    seen = {src_id}
    while q:
        nid = q.popleft()
        if nid == dst_id:
            break
        for e in adj.get(nid, []):
            other = e.dst_id if e.src_id == nid else e.src_id
            if other not in seen:
                seen.add(other)
                prev[other] = (nid, e)
                q.append(other)

    if dst_id not in seen:
        return GraphPath(src_id, dst_id, [], found=False)
    # reconstruct
    chain: list[tuple[str, GraphEdge]] = []
    cur = dst_id
    while cur != src_id:
        pnode, pedge = prev[cur]
        chain.append((cur, pedge))
        cur = pnode
    chain.reverse()
    node_by_id = {r["id"]: row_to_node(store, r) for r in store.open_nodes()}
    steps = [PathStep(edge=e, to_node=node_by_id[nid]) for nid, e in chain]
    return GraphPath(src_id, dst_id, steps, found=True)


# --- timeline (bi-temporal history) -----------------------------------------------
@dataclass
class TimelineEvent:
    kind: str                 # "node" | "edge"
    entity_id: str
    label: str
    valid_from: datetime
    valid_to: Optional[datetime]
    system_from: datetime
    system_to: Optional[datetime]


def timeline(store: GraphStore, entity_id: str) -> list[TimelineEvent]:
    """Every version of ``entity_id`` and every edge touching it, ordered by when we learned it
    (system_from). Lets the UI answer 'who knew what, when'."""
    events: list[TimelineEvent] = []
    from .store import _parse
    for r in store.all_node_versions(entity_id):
        events.append(TimelineEvent("node", r["id"], r["label"], _parse(r["valid_from"]),
                                    _parse(r["valid_to"]), _parse(r["system_from"]),
                                    _parse(r["system_to"])))
    for r in store.edges_touching(entity_id):
        lbl = f"{r['src_id']} --[{r['kind']}]--> {r['dst_id']}"
        events.append(TimelineEvent("edge", r["id"], lbl, _parse(r["valid_from"]),
                                    _parse(r["valid_to"]), _parse(r["system_from"]),
                                    _parse(r["system_to"])))
    events.sort(key=lambda e: (e.system_from, e.kind, e.entity_id))
    return events


# --- nl_query (grounded answer) ---------------------------------------------------
@dataclass
class Citation:
    entity_id: str
    label: str
    kind: str
    pointers: list[SourcePointer]


@dataclass
class GroundedAnswer:
    question: str
    answer: str
    grounded: bool                          # False ⇒ refused (no match, or claim_check failed)
    citations: list[Citation] = field(default_factory=list)
    matched_entities: list[str] = field(default_factory=list)
    refusal: str = ""

    def pointers(self) -> list[SourcePointer]:
        out: list[SourcePointer] = []
        for c in self.citations:
            out.extend(c.pointers)
        return out


_STOP = {"the", "is", "are", "was", "were", "a", "an", "of", "to", "for", "in", "on", "at",
         "what", "who", "which", "how", "much", "many", "our", "do", "does", "did", "and",
         "by", "with", "value", "whats", "tell", "me", "about", "s"}
_QTOK = re.compile(r"[a-z0-9]+")


def _q_tokens(q: str) -> list[str]:
    return [t for t in _QTOK.findall(q.lower()) if t not in _STOP and len(t) > 1]


def _searchable(node: GraphNode) -> str:
    parts = [node.label, node.canonical_id or ""]
    parts.extend(str(v) for v in node.attrs.values())
    return " ".join(parts).lower()


def link_entities(store: GraphStore, question: str, *, top_k: int = 3) -> list[GraphNode]:
    """Deterministic entity linking: rank open nodes by question-token overlap, tiebreaking on
    LABEL coverage so a question's named entity beats a fact that merely mentions it. No model.

    e.g. "who owns Project Atlas" → the PROJECT 'Project Atlas' (label fully covered) outranks the
    FACT 'Project Atlas estimated completion' (label half-covered); "who is Sarah Chen" → the
    PERSON (label fully covered) outranks a decision whose ``made_by`` is 'Sarah Chen'."""
    qt = set(_q_tokens(question))
    if not qt:
        return []
    scored: list[tuple[int, float, GraphNode]] = []
    for r in store.open_nodes():
        if r["kind"] == NodeKind.DOCUMENT.value:
            continue  # documents are PROVENANCE (the answer cites them), never the answer entity
        node = row_to_node(store, r)
        text = _searchable(node)
        overlap = sum(1 for t in qt if t in text)
        if not overlap:
            continue
        label_toks = set(_QTOK.findall(node.label.lower()))
        # coverage = fraction of the node's OWN name explained by the question (specificity)
        cov = (sum(1 for t in label_toks if t in qt) / len(label_toks)) if label_toks else 0.0
        scored.append((overlap, cov, node))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return [n for _, _, n in scored[:top_k]]


def _node_citation(store: GraphStore, node: GraphNode) -> Citation:
    return Citation(entity_id=node.id, label=node.label, kind=node.kind.value,
                    pointers=node.sources)


def nl_query(store: GraphStore, question: str) -> GroundedAnswer:
    """Entity-link → 1-hop subgraph → build a GROUNDED answer, every quantity claim-checked.

    The answer text is assembled ONLY from grounded node labels + each FACT node's canonical
    ``rendered`` attr (both carry SourcePointers). ``claim_check`` then re-verifies that no
    quantity/date in the answer escapes the cited set — a belt-and-braces anti-fabrication gate
    (if assembly ever introduced an uncited number, the answer is refused)."""
    matches = link_entities(store, question)
    if not matches:
        return GroundedAnswer(question, "", grounded=False,
                              refusal="no matching entity in the graph")

    primary = matches[0]
    sub = neighbors(store, primary.id, depth=1)
    node_by_id = {n.id: n for n in sub.nodes} | {primary.id: primary}

    sentences: list[str] = []
    citations: list[Citation] = []
    cited_ids: set[str] = set()
    allowed: list[str] = []          # grounded fact forms the answer may use — ALL from cited nodes

    def _allow_forms(n: GraphNode) -> list[str]:
        # every quantity-bearing form a cited node legitimately contributes (label + canonical
        # rendered fact + a decision's made_at date) — the ONLY source of `allowed` entries, so a
        # whitelisted token is, by construction, a value of a cited (grounded) node.
        forms = [n.label]
        if n.attrs.get("rendered"):
            forms.append(str(n.attrs["rendered"]))
        if n.kind == NodeKind.DECISION and n.attrs.get("made_at"):
            forms.append(str(n.attrs["made_at"]))
        return forms

    def cite(n: GraphNode) -> None:
        if n.id not in cited_ids:
            citations.append(_node_citation(store, n))
            cited_ids.add(n.id)
            allowed.extend(_allow_forms(n))

    cite(primary)

    # --- intent templates (deterministic) ---------------------------------------
    if primary.kind == NodeKind.FACT:
        rendered = primary.attrs.get("rendered", primary.label)
        sentences.append(f"{primary.label}." if rendered in primary.label
                         else f"{primary.label}: {rendered}.")
    elif primary.kind == NodeKind.PERSON:
        role = primary.attrs.get("role")
        org = next((node_by_id[e.dst_id] for e in sub.edges
                    if e.kind == EdgeKind.WORKS_AT and e.src_id == primary.id
                    and e.dst_id in node_by_id), None)
        bits = [primary.label]
        if role:
            bits.append(f"is {role}")
        if org:
            cite(org)
            bits.append(f"at {org.label}")
        sentences.append(" ".join(bits) + ".")
        decided = [node_by_id[e.dst_id] for e in sub.edges
                   if e.kind == EdgeKind.DECIDED and e.src_id == primary.id and e.dst_id in node_by_id]
        for d in decided:
            cite(d)
            sentences.append(f"{primary.label} decided: {d.label}.")
    elif primary.kind == NodeKind.DECISION:
        decider = next((node_by_id[e.src_id] for e in sub.edges
                        if e.kind == EdgeKind.DECIDED and e.dst_id == primary.id
                        and e.src_id in node_by_id), None)
        s = primary.label
        if decider:
            cite(decider)
            s += f" — decided by {decider.label}"
        made_at = primary.attrs.get("made_at")
        if made_at:
            s += f" on {made_at}"          # ISO date already in `allowed` via cite(primary)
        sentences.append(s + ".")
    elif primary.kind == NodeKind.PROJECT:
        owner = next((node_by_id[e.src_id] for e in sub.edges
                      if e.kind == EdgeKind.OWNS and e.dst_id == primary.id
                      and e.src_id in node_by_id), None)
        status = primary.attrs.get("status")
        s = primary.label + (f" (status: {status})" if status else "")
        if owner:
            cite(owner)
            s += f", owned by {owner.label}"
        sentences.append(s + ".")
        affecting = [(e, node_by_id[e.src_id]) for e in sub.edges
                     if e.kind in (EdgeKind.FUNDS, EdgeKind.BLOCKS, EdgeKind.ENABLES)
                     and e.dst_id == primary.id and e.src_id in node_by_id]
        for e, dnode in affecting:
            cite(dnode)
            sentences.append(f"{dnode.label} {e.kind.value} this project.")
    else:  # ORG / DOCUMENT
        sentences.append(f"{primary.label}.")

    answer = " ".join(sentences)
    # the anti-fabrication gate: every quantity/date must be a cited node value (`allowed`)
    cc = check_claims(answer, facts=[], allow=allowed)
    if not cc.ok:
        return GroundedAnswer(question, "", grounded=False, citations=citations,
                              matched_entities=[m.id for m in matches],
                              refusal=f"answer refused: ungrounded quantity "
                                      f"{[v.token for v in cc.violations]}")
    return GroundedAnswer(question, answer, grounded=True, citations=citations,
                          matched_entities=[m.id for m in matches])

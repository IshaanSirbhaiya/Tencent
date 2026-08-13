"""Graph queries (T08, 04_PRD §8) — all LLM-free up to synthesis.

  as_of(valid_time, system_time)   → point-in-time bi-temporal snapshot
  neighbors(node_id, edge_kind, depth) → subgraph (BFS over open edges)
  nl_query(question)               → entity-linking → subgraph → GROUNDED answer
  path(src_id, dst_id)             → shortest provenance path
  timeline(entity_id)              → bi-temporal version history

`nl_query` is the load-bearing one. It is deterministic (no model in the retrieval loop, the
gbrain proof point, 04_PRD §8) and every quantity/date in the answer is checked by **C17's
``claim_check``** against the cited graph nodes' grounded values. A number that does not equal a
cited fact ⇒ the answer is REFUSED, not emitted (P4 / anti-fabrication).

Two grounding channels, and an answer keeps them apart rather than pooling them (R-30): a
``Citation`` grounds an ENTITY — that the node exists and is the thing its label names — while an
``AnswerValue`` grounds one printed attribute with that attribute's own pointer. Free text comes
only from node labels, which are grounded by the entity citations and by nothing narrower; that is
a known gap (R-31: `label` has no pointer of its own) and the answer says which channel grounds
what rather than implying one covers both.
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from databridge_convert.generate.claim_check import check_claims
from databridge_core import SourcePointer

from .identity import ambiguous_refusal, identity_caveat, same_label_node_ids
from .retrieval import Bm25Index, Candidate
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
    """An ENTITY citation: that this node exists and is the thing named by its label.

    It grounds the label and the id, and **nothing else**. `pointers` here are `node.sources`,
    which have never seen the node's individual attribute values — see `AnswerValue`.
    """

    entity_id: str
    label: str
    kind: str
    pointers: list[SourcePointer]


@dataclass
class AnswerValue:
    """One attribute value the answer prints, carrying the pointer for **that value**.

    The defect this exists to close (R-30) is the same one the node sheet had: `nl_query` printed
    a patient's registry date of birth, an organisation's kind and billing party, a package's
    eligibility band and a visit's appointment into an answer whose citations were the nodes'
    ENTITY pointers. One citation appearing to vouch for values it had never seen — and this one
    feeds `/brain`'s answer box, so a judge clicking the citation finds it does not support the
    figure printed beside it.

    `GraphNode.attr_sources` already holds the right pointer for every attribute; the bug was that
    this module did not use it. Every value below travels with its own.
    """

    entity_id: str
    attr: str
    value: str
    pointer: SourcePointer


@dataclass
class GroundedAnswer:
    question: str
    answer: str
    grounded: bool                          # False ⇒ refused (no match, or claim_check failed)
    citations: list[Citation] = field(default_factory=list)
    matched_entities: list[str] = field(default_factory=list)
    refusal: str = ""
    #: every attribute value printed in `answer`, each with the pointer for that value alone
    values: list[AnswerValue] = field(default_factory=list)
    #: `entity.attr` paths the answer asked for and could not ground, so did NOT print
    unresolved: list[str] = field(default_factory=list)
    #: on an AMBIGUOUS refusal, the node ids the question could have meant. Asking again with one
    #: of them resolves cleanly — a refusal with no way forward is a dead end (see `identity.py`).
    candidates: list[str] = field(default_factory=list)

    def pointers(self) -> list[SourcePointer]:
        """Every pointer attached to this answer — entity citations AND per-value citations."""
        out: list[SourcePointer] = []
        for c in self.citations:
            out.extend(c.pointers)
        out.extend(v.pointer for v in self.values)
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
    """Deterministic entity linking by **BM25** over label + canonical_id + attribute text.

    e.g. "what does WELL2 include" → the PACKAGE 'WELL2' (its name is fully accounted for) outranks
    the COVERAGE_RULE that merely names it.

    This replaced a raw token-overlap scorer that used a **substring** test, under which the token
    ``life`` matched ``…Life Assurance`` and *"What is the meaning of life?"* returned four
    insurers. See `retrieval.py` for the scoring, and note that it can now return **nothing**: below
    `MIN_SCORE` we decline rather than hand back the least-bad node. `packages/evals/e_retrieval.py`
    measures both directions.

    DOCUMENT nodes stay excluded — they are PROVENANCE (the answer cites them), never the answer
    entity. Address them by `document_node_id`.
    """
    rows = [r for r in store.open_nodes() if r["kind"] != NodeKind.DOCUMENT.value]
    if not rows:
        return []
    nodes = [row_to_node(store, r) for r in rows]
    index = Bm25Index([
        Candidate(id=n.id, label=n.label, canonical_id=n.canonical_id,
                  attr_text=" ".join(str(v) for v in n.attrs.values()))
        for n in nodes
    ])
    by_id = {n.id: n for n in nodes}
    return [by_id[s.id] for s in index.search(question, top_k=top_k)]


def _node_citation(store: GraphStore, node: GraphNode) -> Citation:
    return Citation(entity_id=node.id, label=node.label, kind=node.kind.value,
                    pointers=node.sources)


def _named_outright(store: GraphStore, question: str) -> Optional[GraphNode]:
    """A node the question names by ID outright, or ``None`` — the way PAST an ambiguity refusal.

    ``nl_query`` takes free text, so the refusal's "ask again with one of these ids" needs a door
    that accepts one. A token equal to an open node id **is** an identifier, and a caller who
    typed it has already made the choice this module will not make for them — the same rule, and
    the same reason, as the node-id branch of `identity.resolve_entity`.

    Deliberately an EXACT id match and nothing looser. A fuzzy version of this would be the silent
    pick it exists to avoid, wearing a different hat.
    """
    for token in re.split(r"\s+", question.strip()):
        token = token.strip("?.,!;:'\"()[]")
        if not token:
            continue
        row = store.open_node_row(token)
        if row is not None:
            return row_to_node(store, row)
    return None


def nl_query(store: GraphStore, question: str) -> GroundedAnswer:
    """Entity-link → 1-hop subgraph → build a GROUNDED answer, every quantity claim-checked.

    Two grounding channels, and keeping them apart is the point (R-30):

    * ``citations`` ground the ENTITIES named — that each node exists and is what its label says.
    * ``values`` ground each attribute the answer PRINTS, one pointer per value, taken from that
      node's ``attr_sources``. An attribute with no pointer of its own is not printed at all; its
      path goes in ``unresolved``, exactly as ``node_sheet`` moves it to its *Unresolved* section.

    ``claim_check`` then re-verifies that no quantity/date in the answer escapes the grounded set —
    a belt-and-braces anti-fabrication gate (if assembly ever introduced an uncited number, the
    answer is refused).

    **A question that names more than one entity is REFUSED, not answered about one of them.**
    See ``identity.py``: the registry holds three "Loh Wei Ming", and ranking them returns
    whichever sorted first among equals. The refusal carries `candidates`, and a question
    containing one of those ids resolves cleanly."""
    # An id typed into the question wins outright, and is never ambiguous — that is what makes
    # the refusal below a fork in the road rather than a wall.
    explicit = _named_outright(store, question)
    matches = link_entities(store, question)
    if explicit is None and not matches:
        return GroundedAnswer(question, "", grounded=False,
                              refusal="no matching entity in the graph")

    primary = explicit if explicit is not None else matches[0]
    # The entity actually answered about leads, then the rest of the ranking. `/brain` highlights
    # these on the canvas, and an answer about a node the caller named by id that did not appear
    # in its own `matched_entities` highlighted everything except the thing it described.
    matched_ids = [primary.id, *(m.id for m in matches if m.id != primary.id)]
    if explicit is None:
        # **A NAME DOES NOT IDENTIFY A PERSON.** The registry holds three "Loh Wei Ming", four
        # "Rahman Zhi Hao" and four "Tan Mei Ling" (`merge.py` refuses to fuzzy-merge patients for
        # exactly this reason). `link_entities` ranks them equal and returns whichever sorted
        # first among equals, so `matches[0]` WAS the machine choosing between people who share a
        # name — silently, with nothing in the answer saying a choice had been made. It is the
        # determination reserved for a human, in person (CLAUDE.md constraint 1), so it is refused
        # and the candidate ids are handed back.
        shared = same_label_node_ids(store, primary.id)
        if len(shared) > 1:
            return GroundedAnswer(question, "", grounded=False,
                                  matched_entities=matched_ids,
                                  candidates=shared,
                                  refusal=ambiguous_refusal(question, shared))
    sub = neighbors(store, primary.id, depth=1)
    node_by_id = {n.id: n for n in sub.nodes} | {primary.id: primary}

    sentences: list[str] = []
    citations: list[Citation] = []
    cited_ids: set[str] = set()
    values: list[AnswerValue] = []
    unresolved: list[str] = []
    allowed: list[str] = []          # grounded fact forms the answer may use — ALL from cited nodes

    def _allow_forms(n: GraphNode) -> list[str]:
        # Every quantity-bearing form a cited node legitimately contributes: its label and each
        # attribute that carries a pointer OF ITS OWN. Iterating `attr_sources` rather than
        # `attrs` is deliberate: the whitelist is what lets a number through `check_claims`, so it
        # must be built from the values this module is able to cite individually, not from every
        # value the node happens to hold.
        return [n.label, *(str(n.attrs[k]) for k in sorted(n.attr_sources)
                           if n.attrs.get(k) is not None)]

    def cite(n: GraphNode) -> None:
        if n.id not in cited_ids:
            citations.append(_node_citation(store, n))
            cited_ids.add(n.id)
            allowed.extend(_allow_forms(n))

    def cited(n: GraphNode, attr: str) -> Optional[str]:
        """The value of ``attr`` **with its own pointer recorded**, or ``None`` — never a bare one.

        This is the whole of the R-30 fix, and it is deliberately the only door: a template below
        cannot print an attribute without coming through here, and coming through here attaches
        the pointer for that value to the answer. A value the graph cannot cite individually is
        not printed as a fact; its path is named in ``unresolved`` instead, which is the honest
        thing to show a reader who is about to click a citation.
        """
        if attr not in n.attrs:
            return None
        value, pointer = n.attrs[attr], n.source_for(attr)
        if value is None or pointer is None:
            unresolved.append(f"{n.id}.{attr}")
            return None
        text = str(value)
        values.append(AnswerValue(entity_id=n.id, attr=attr, value=text, pointer=pointer))
        return text

    cite(primary)

    # --- intent templates (deterministic) ---------------------------------------
    def _linked(kind: EdgeKind, *, incoming: bool) -> list[GraphNode]:
        end, other = ("dst_id", "src_id") if incoming else ("src_id", "dst_id")
        return [node_by_id[getattr(e, other)] for e in sub.edges
                if e.kind == kind and getattr(e, end) == primary.id
                and getattr(e, other) in node_by_id]

    if primary.kind == NodeKind.PATIENT:
        # A patient answer describes REGISTRY data and the documents that CLAIM to be about this
        # person. It never states that identity has been established — the FOR_PATIENT edges below
        # are claims, and where a document has several candidates it produced several such edges
        # (ADR-0009; CLAUDE.md §Hard constraints 1).
        bits = [primary.label]
        dob = cited(primary, "date_of_birth")
        if dob:
            bits.append(f"(registry date of birth {dob})")
        sentences.append(" ".join(bits) + ".")
        # DOCUMENTS only. `FOR_PATIENT` also runs visit → patient in the vocabulary, and an
        # unfiltered version narrated a visit as "<visit label> names this patient" and then
        # chose the *less* cautious caveat on the strength of it. A non-document claim leaves the
        # registry-only wording standing, which is the cautious direction to fail in.
        claiming = [n for n in _linked(EdgeKind.FOR_PATIENT, incoming=True)
                    if n.kind == NodeKind.DOCUMENT]
        for doc in claiming:
            cite(doc)
            sentences.append(f"{doc.label} names this patient.")
        # UNCONDITIONAL, and that is the fix. The caveat used to be appended INSIDE the loop
        # above, so it rode on a document existing. Once `clinic_seed` seeded the whole registry,
        # 289 of the 300 patients had no `FOR_PATIENT` edge at all and answered with a bare name
        # and date of birth — no caveat anywhere. Registry presence is not identification
        # (CLAUDE.md constraint 1); the caveat belongs to the KIND, not to the edge.
        sentences.append(identity_caveat(claimed=bool(claiming)))
    elif primary.kind == NodeKind.ORG:
        bits = [primary.label]
        org_kind = cited(primary, "kind")
        if org_kind:
            bits.append(f"is a {org_kind}")
        billing_party = cited(primary, "billing_party")
        if billing_party:
            bits.append(f"billing the {billing_party}")
        sentences.append(" ".join(bits) + ".")
        for doc in _linked(EdgeKind.ISSUED, incoming=False):
            cite(doc)
            sentences.append(f"{primary.label} issued {doc.label}.")
    elif primary.kind == NodeKind.PACKAGE:
        s = primary.label
        name = cited(primary, "name")
        if name:
            s += f" — {name}"
        eligibility = cited(primary, "eligibility")
        if eligibility:
            s += f" ({eligibility})"
        sentences.append(s + ".")
        included = _linked(EdgeKind.INCLUDES_TEST, incoming=False)
        for t in included:
            cite(t)
        if included:
            sentences.append(
                f"{primary.label} includes: {', '.join(t.label for t in included)}.")
    elif primary.kind == NodeKind.TEST:
        s = primary.label
        category = cited(primary, "category")
        if category:
            s += f" ({category})"
        sentences.append(s + ".")
        for pkg in _linked(EdgeKind.INCLUDES_TEST, incoming=True):
            cite(pkg)
            sentences.append(f"Included in package {pkg.label}.")
    elif primary.kind == NodeKind.COVERAGE_RULE:
        s = primary.label
        issuer_code = cited(primary, "issuer_code")
        if issuer_code:
            s += f" — issuer {issuer_code}"
        billed_to = cited(primary, "billed_to")
        if billed_to:
            s += f", billed to {billed_to}"
        eligibility = cited(primary, "eligibility")
        if eligibility:
            s += f" ({eligibility})"
        sentences.append(s + ".")
    elif primary.kind == NodeKind.VISIT:
        s = primary.label
        appointment = cited(primary, "appointment")
        if appointment:
            s += f" — appointment {appointment}"
        sentences.append(s + ".")
        for clinic in _linked(EdgeKind.AT_CLINIC, incoming=False):
            cite(clinic)
            sentences.append(f"At {clinic.label}.")
    else:  # CLINIC / DOCUMENT
        sentences.append(f"{primary.label}.")

    answer = " ".join(sentences)
    # the anti-fabrication gate: every quantity/date must be a cited node value (`allowed`)
    cc = check_claims(answer, facts=[], allow=allowed)
    if not cc.ok:
        # A refusal emits no answer text, so it emits no values either — a value list beside an
        # empty answer would be citations for something nobody was shown.
        return GroundedAnswer(question, "", grounded=False, citations=citations,
                              matched_entities=matched_ids,
                              refusal=f"answer refused: ungrounded quantity "
                                      f"{[v.token for v in cc.violations]}",
                              unresolved=sorted(set(unresolved)))
    return GroundedAnswer(question, answer, grounded=True, citations=citations,
                          matched_entities=matched_ids,
                          values=values, unresolved=sorted(set(unresolved)))

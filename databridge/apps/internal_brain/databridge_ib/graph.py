"""The permission-aware knowledge graph (``GET /ib/graph``) and the "can I open these?" check
(``POST /ib/graph/openable``) for Developer mode.

Rules (the map's, made version-safe; Codex and Opus plan reviews):

* **Only documents confirmed in THIS call.** Gate 1 (``index.readable``) bounds every candidate;
  then every document that could appear goes through :func:`authz.authorize` (live Gate 2 plus the
  pipeline's version-bound read-through). The graph is the INDUCED graph of those confirmed
  documents: containers, platforms and people are derived only from them, and an edge exists only
  between two nodes that are both shown. A document that failed Gate 2 is neither shown nor
  counted, and never takes a slot under the cap.
* **No hidden counts unless exact.** ``degree_hidden_safe`` is the number of a node's confirmed
  neighbours left out of this response, and it is set ONLY when the node's whole Gate-1
  neighbourhood was checked live in this call; otherwise it is ``null`` and ``more`` says only
  whether at least one confirmed neighbour was left out.
* **People are authors from the index** (the ``author`` field), shown only through a confirmed
  document. ``mentions`` edges are Jira keys written in a confirmed document that resolve INSIDE the
  readable set to another confirmed document; ``links`` edges are supported URLs that do the same.
  An unresolved key or URL never becomes a node.
* **Same negative answer.** A focus the person cannot open, and a focus that does not exist, give
  the same 404 body. An empty search is a normal empty graph.
* **Write-ahead.** ``graph.viewed`` (counts only) is appended before anything is returned; a ledger
  failure is a 503.
* **Bounded work.** A small dedicated worker pool (``authz.VIEW_POOL``), one timeout per call, and a
  cap on the number of live checks per mode.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Mapping, Optional

from . import authz
from .config import POLICY_VERSION, TOP_K
from .directory import Directory
from .index import Index
from .models import SCOPE_NOTE, DirectoryUser, GraphViewed
from .pipeline import PER_PLATFORM_K, platform_hints
from .timewin import parse as parse_window
from .timewin import strip as strip_window

DEFAULT_LIMIT, MAX_LIMIT = 120, 300
MAX_CHECKS_OVERVIEW = 1500          # the whole readable set of the demo world's widest persona
MAX_CHECKS_FOCUS = 400
MAX_OPENABLE = 300
PLATFORM_ORDER = ("confluence", "jira", "slack", "drive")


class NotFound(Exception):
    """The focus is unknown or not openable by this person (the same answer for both)."""


def cid(platform: str, container: str) -> str:
    return f"container:{platform}:{container}"


def pid(user_id: str) -> str:
    return f"person:{user_id}"


def plid(platform: str) -> str:
    return f"platform:{platform}"


class _Gate1View:
    """Structure of the person's Gate-1 readable set (never output directly: it only bounds which
    documents are checked live, and decides whether a count can be exact)."""

    def __init__(self, index: Index, readable: frozenset):
        self.readable = readable
        self.rows: dict[str, dict] = {}
        self.by_cont: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.by_author: dict[str, list[str]] = defaultdict(list)
        self.by_plat: dict[str, list[str]] = defaultdict(list)
        self.out: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self.inn: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for d in sorted(readable):
            row = index.doc(d)
            if row is None:
                continue
            self.rows[d] = row
            self.by_cont[(row["platform"], row["container"])].append(d)
            self.by_plat[row["platform"]].append(d)
            if row.get("author"):
                self.by_author[row["author"]].append(d)
        for d, row in self.rows.items():
            try:
                links = json.loads(row.get("links") or "[]")
            except ValueError:
                links = []
            for link in links:
                t = authz.resolve_link(str(link), readable)
                if t and t != d and t in self.rows and all(t != x for x, _ in self.out[d]):
                    # connectors store a Jira KEY written in text as "jira:KEY" (a mention) and a
                    # supported URL as "<platform>:<id>" (a link): see connectors/_http.extract_links
                    rel = "mentions" if str(link).startswith("jira:") else "links"
                    self.out[d].append((t, rel))
                    self.inn[t].append((d, rel))

    def recent(self, ids) -> list[str]:
        return sorted(ids, key=lambda d: (self.rows.get(d, {}).get("updated_at", ""), d), reverse=True)

    def link_nbrs(self, d: str) -> list[str]:
        return [t for t, _ in self.out.get(d, ())] + [s for s, _ in self.inn.get(d, ())]


def _bounded(ordered: list[str], cap: int) -> tuple[list[str], bool]:
    seen = list(dict.fromkeys(ordered))
    return seen[:cap], len(seen) > cap


def build_graph(user: DirectoryUser, *, index: Index, directory: Directory, ledger: Any,
                connectors: Mapping[str, Any], clock: Callable[[], datetime],
                focus: Optional[str] = None, q: Optional[str] = None,
                limit: int = DEFAULT_LIMIT, timeout_s: float = authz.VIEW_CHECK_TIMEOUT_S) -> dict:
    """The graph dict, or raises :class:`NotFound` (after ledgering it). Any ledger failure
    propagates (the route answers 503)."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    now = clock()
    epoch = getattr(directory, "epoch", 0)
    readable = index.readable(user, epoch)                          # GATE 1
    g1 = _Gate1View(index, readable)
    focus = (focus or "").strip() or None
    q = (q or "").strip() or None
    mode = "focus" if focus else ("search" if q else "overview")
    ranked: list[str] = []

    # 1 — which documents to check live (all inside Gate 1)
    universe: list[str] = []
    if mode == "overview":
        universe = g1.recent(g1.rows)
        cap = MAX_CHECKS_OVERVIEW
    elif mode == "search":
        window = parse_window(q, now)
        sq = strip_window(q)
        hits = index.search(sq, readable, k=TOP_K, window=window, relevant_only=True)
        seen = {h.chunk_id for h in hits}
        for platform in platform_hints(q):              # the pipeline's per-platform recall slots
            scoped = [d for d in readable if d.startswith(platform + ":")]
            for h in index.search(sq, scoped, k=PER_PLATFORM_K, window=window, relevant_only=True):
                if h.chunk_id not in seen:
                    hits.append(h)
                    seen.add(h.chunk_id)
        ranked = [d for d in dict.fromkeys(h.doc_id for h in hits) if d in g1.rows]
        universe = list(ranked)
        for d in ranked:
            r = g1.rows[d]
            universe += g1.recent(g1.by_cont[(r["platform"], r["container"])])
        cap = MAX_CHECKS_FOCUS
    else:
        universe = _focus_universe(focus, g1)
        if not universe:
            _ledger(ledger, user, mode, now, outcome="not_found")
            raise NotFound()
        cap = MAX_CHECKS_FOCUS
    universe, _ = _bounded(universe, cap)
    checked = set(universe)

    # 2 — GATE 2 + version-bound read-through, one batch
    ok, n_checked = authz.authorize(user, universe, index=index, connectors=connectors,
                                    readable=readable, timeout_s=timeout_s)
    ok_ids = set(ok)

    # 3 — the confirmed graph G (induced on confirmed docs only)
    nodes: dict[str, dict] = {}
    nbrs: dict[str, set] = defaultdict(set)
    edges: dict[tuple[str, str, str], dict] = {}

    def add_edge(src: str, dst: str, kind: str) -> None:
        edges[(src, dst, kind)] = {"source": src, "target": dst, "kind": kind}
        nbrs[src].add(dst)
        nbrs[dst].add(src)

    for d, s in ok.items():
        c, p = cid(s.platform, s.container), plid(s.platform)
        nodes[d] = {"id": d, "kind": "doc", "platform": s.platform, "label": s.title,
                    "container": c, "container_name": s.container_name, "doc_kind": s.kind,
                    "version": s.version, "updated_at": s.updated_at, "deep_link": s.deep_link}
        nodes.setdefault(c, {"id": c, "kind": "container", "platform": s.platform,
                             "label": s.container_name or s.container, "container": None})
        nodes.setdefault(p, {"id": p, "kind": "platform", "platform": s.platform,
                             "label": s.platform, "container": None})
        add_edge(c, d, "contains")
        add_edge(p, c, "contains")
        if s.author:
            a = pid(s.author)
            u = directory.get(s.author) if hasattr(directory, "get") else None
            nodes.setdefault(a, {"id": a, "kind": "person", "platform": None,
                                 "label": getattr(u, "name", None) or s.author, "container": None})
            add_edge(a, d, "authored")
    for d in ok_ids:
        for t, rel in g1.out.get(d, ()):
            if t in ok_ids:
                add_edge(d, t, rel)

    def exact(node_id: str) -> bool:
        n = nodes[node_id]
        if n["kind"] == "doc":
            return all(x in checked for x in g1.link_nbrs(node_id))
        if n["kind"] == "container":
            key = node_id.split(":", 2)
            return all(x in checked for x in g1.by_cont.get((key[1], key[2]), ()))
        if n["kind"] == "person":
            return all(x in checked for x in g1.by_author.get(node_id.split(":", 1)[1], ()))
        return all(x in checked for x in g1.by_plat.get(n["platform"], ()))

    def docs_under(node_id: str) -> list[str]:
        return [x for x in nbrs[node_id] if nodes[x]["kind"] == "doc"]

    # 4 — the focus must be confirmed in this call, or it is the same 404 as an unknown id
    if mode == "focus" and focus not in nodes:
        _ledger(ledger, user, mode, now, outcome="not_found", checked=n_checked)
        raise NotFound()

    # 5 — select what to show (confirmed nodes only; live-denied docs never take a slot)
    order: list[str] = []
    summary_edges: list[dict] = []
    if mode == "overview":
        plats = sorted({n for n in nodes if nodes[n]["kind"] == "platform"},
                       key=lambda n: (PLATFORM_ORDER.index(nodes[n]["platform"])
                                      if nodes[n]["platform"] in PLATFORM_ORDER else 99, n))
        conts = sorted((n for n in nodes if nodes[n]["kind"] == "container"),
                       key=lambda n: (-len(docs_under(n)), nodes[n]["label"].lower(), n))
        people = sorted((n for n in nodes if nodes[n]["kind"] == "person"),
                        key=lambda n: (-len(docs_under(n)), nodes[n]["label"].lower(), n))
        order = plats + conts[: max(0, limit - len(plats) - min(len(people), limit // 3))]
        order += people[: max(0, limit - len(order))]
        order += [c for c in conts if c not in order]
    elif mode == "search":
        hits = [d for d in ranked if d in ok_ids]
        order = list(hits)
        order += [nodes[d]["container"] for d in hits]
        order += [a for d in hits for a in nbrs[d] if nodes[a]["kind"] == "person"]
    else:
        f = nodes[focus]
        order = [focus]
        if f["kind"] == "doc":
            order += [x for x in nbrs[focus] if nodes[x]["kind"] in ("container", "person")]
            order += g1.recent([x for x in nbrs[focus] if nodes[x]["kind"] == "doc"])
            order.append(plid(f["platform"]))
        elif f["kind"] == "container":
            docs = g1.recent(docs_under(focus))
            first = docs[: max(1, (limit * 2) // 3)]
            order += [plid(f["platform"])] + first
            order += sorted({a for d in first for a in nbrs[d] if nodes[a]["kind"] == "person"})
            order += g1.recent({t for d in first for t in nbrs[d]
                                if nodes[t]["kind"] == "doc" and t not in docs})
            order += docs[len(first):]
        elif f["kind"] == "person":
            docs = g1.recent(docs_under(focus))
            first = docs[: max(1, (limit * 2) // 3)]
            order += first + list(dict.fromkeys(nodes[d]["container"] for d in first))
            order += docs[len(first):]
        else:                                                       # platform
            order += sorted((x for x in nbrs[focus] if nodes[x]["kind"] == "container"),
                            key=lambda n: (-len(docs_under(n)), nodes[n]["label"].lower(), n))
    order = [n for n in dict.fromkeys(order) if n in nodes]
    shown = order[:limit]
    truncated = len(order) > limit
    S = set(shown)

    if mode == "overview":                      # people -> containers they author in (summary)
        weight: dict[tuple[str, str], int] = defaultdict(int)
        for n in shown:
            if nodes[n]["kind"] == "person":
                for d in docs_under(n):
                    if nodes[d]["container"] in S:
                        weight[(n, nodes[d]["container"])] += 1
        summary_edges = [{"source": a, "target": c, "kind": "authored", "weight": w}
                         for (a, c), w in sorted(weight.items())]

    out_nodes = []
    for n in shown:
        node = dict(nodes[n])
        hidden = len(nbrs[n] - S)
        is_exact = exact(n)
        node["degree_hidden_safe"] = hidden if is_exact else None
        node["more"] = True if hidden > 0 else (False if is_exact else None)
        if node["kind"] in ("container", "person"):
            node["count"] = len(docs_under(n)) if is_exact else None
        elif node["kind"] == "platform":
            node["count"] = (len([x for x in nbrs[n] if nodes[x]["kind"] == "container"])
                             if is_exact else None)
        if mode == "search" and n in ok_ids and n in ranked:
            node["rank"] = [d for d in ranked if d in ok_ids].index(n) + 1
        out_nodes.append(node)
    out_edges = [e for (a, b, _), e in sorted(edges.items()) if a in S and b in S] + summary_edges
    n_docs = sum(1 for n in out_nodes if n["kind"] == "doc")
    _ledger(ledger, user, mode, now, outcome="ok", nodes=len(out_nodes), docs=n_docs,
            edges=len(out_edges), checked=n_checked)
    return {"as_of": now.isoformat(), "scope_note": SCOPE_NOTE, "sub": user.id, "mode": mode,
            "focus": focus if mode == "focus" else None, "query": q if mode == "search" else None,
            "limit": limit, "truncated": truncated, "nodes": out_nodes, "edges": out_edges,
            "freshness_note": "Every document shown was checked with its source just now."}


def _focus_universe(focus: str, g1: _Gate1View) -> list[str]:
    """Gate-1 documents needed to show ``focus`` and its one-hop neighbours exactly; empty for an
    unknown id and for one outside the readable set (the same path)."""
    if focus.startswith("container:"):
        parts = focus.split(":", 2)
        docs = g1.recent(g1.by_cont.get((parts[1], parts[2]), ())) if len(parts) == 3 else []
        return docs + [x for d in docs for x in g1.link_nbrs(d)] if docs else []
    if focus.startswith("person:"):
        return g1.recent(g1.by_author.get(focus.split(":", 1)[1], ()))
    if focus.startswith("platform:"):
        return g1.recent(g1.by_plat.get(focus.split(":", 1)[1], ()))
    row = g1.rows.get(focus)
    if row is None:
        return []
    out = [focus] + g1.link_nbrs(focus)
    out += g1.recent(g1.by_cont[(row["platform"], row["container"])])
    if row.get("author"):
        out += g1.recent(g1.by_author[row["author"]])
    return out


def openable(user: DirectoryUser, doc_ids: list[str], *, index: Index, directory: Directory,
             ledger: Any, connectors: Mapping[str, Any], clock: Callable[[], datetime],
             timeout_s: float = authz.VIEW_CHECK_TIMEOUT_S) -> dict:
    """The subset of ``doc_ids`` the CALLER can open now (Gate 1 + live Gate 2 + read-through), in
    request order. Ids left out mean "cannot open or does not exist" — the same thing."""
    now = clock()
    ids = [d for d in dict.fromkeys(doc_ids)][:MAX_OPENABLE]
    ok, n_checked = authz.authorize(user, ids, index=index, connectors=connectors,
                                    directory_epoch=getattr(directory, "epoch", 0),
                                    timeout_s=timeout_s)
    out = [d for d in ids if d in ok]
    _ledger(ledger, user, "openable", now, outcome="ok", docs=len(out), checked=n_checked)
    return {"sub": user.id, "as_of": now.isoformat(), "openable": out}


def _ledger(ledger: Any, user: DirectoryUser, mode: str, now: datetime, *, outcome: str,
            nodes: int = 0, docs: int = 0, edges: int = 0, checked: int = 0) -> None:
    ledger.append(GraphViewed(sub=user.id, mode=mode, outcome=outcome, nodes_returned=nodes,
                              docs_returned=docs, edges_returned=edges, docs_checked=checked,
                              policy_version=POLICY_VERSION, event_time=now))


__all__ = ["build_graph", "openable", "NotFound", "DEFAULT_LIMIT", "MAX_LIMIT", "MAX_OPENABLE"]

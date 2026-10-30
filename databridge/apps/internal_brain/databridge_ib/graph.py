"""The permission-aware knowledge graph (``GET /ib/graph``) and the "can I open these?" check
(``POST /ib/graph/openable``) for Developer mode.

Rules (the map's, made version-safe; Codex and Opus plan reviews):

* **Only documents confirmed in THIS call.** Gate 1 (``index.readable``) bounds every candidate;
  then every document that could appear goes through :func:`authz.authorize` (live Gate 2 plus the
  pipeline's version-bound read-through). The graph is built ONLY from those immutable snapshots
  (Codex final review #1): containers, platforms, people AND document-to-document edges come from
  the authorised, current version of each document, never from the index's older copy. An edge
  exists only between two confirmed documents. A document that failed Gate 2 is neither shown nor
  counted, and never takes a slot under the cap.
* **The cap is applied after the live check** (Codex final review #3). Candidates are checked in
  bounded batches until the work list is exhausted, the work cap is reached or the call's single
  deadline expires; the public ``limit`` is applied to the confirmed graph only. When the budget
  runs out first, ``complete`` is false and every count that depends on unchecked candidates is
  ``null`` (never a number that looks exact).
* **Search re-ranks after Gate 2** (Opus final review #8), as the pipeline does: a hit denied live
  leaves the search corpus and the search is run again (``SEARCH_ROUNDS``), so a document revoked
  at the source (webhook not yet arrived) never takes a hit slot from a permitted one.
* **No hidden counts unless exact.** ``degree_hidden_safe`` is the number of a node's confirmed
  neighbours left out of this response, and it is set ONLY when every candidate that could be a
  neighbour was decided live in this call (for a document: every target its CURRENT version links
  to, and every Gate-1 document whose indexed links point at it); otherwise it is ``null`` and
  ``more`` says only whether at least one confirmed neighbour was left out. A check that timed out
  is undecided, not "checked".
* **People are authors** from the authorised snapshot, shown only through a confirmed document.
  ``mentions`` edges are Jira keys written in a confirmed document's current version that resolve
  INSIDE the set of confirmed documents; ``links`` edges are supported URLs that do the same. An
  unresolved key or URL never becomes a node.
* **Same negative answer.** A focus the person cannot open, and a focus that does not exist, give
  the same 404 body. An empty search is a normal empty graph.
* **Write-ahead.** ``graph.viewed`` (counts only) is appended before anything is returned; a ledger
  failure is a 503.
* **Bounded work.** A small dedicated worker pool (``authz.VIEW_POOL``), ONE monotonic deadline per
  call through checks and read-through, and a cap on the number of live checks per mode.
* **One pass for two views** (``GET /ib/galaxy``, docs/finals/live_growth/GRAPH_PERF_DIAGNOSIS.md
  Fix 1). The overview can be built from an :class:`OverviewChecks` decided by the caller in the
  same request: the same Gate-1 view, the same work list (newest first, capped at
  ``MAX_CHECKS_OVERVIEW``) and the same live ``authz.authorize`` verdicts. Nothing else changes:
  ``complete`` is true only when the whole work list was decided, and every count is exact only
  when every document it depends on was decided in that pass.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
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
CHECK_BATCH = 100                   # live checks per batch (the cap is applied after the checks)
SEARCH_ROUNDS = 4                   # search -> check -> search again without the live-denied hits
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
    """Structure of the person's Gate-1 readable set (never output directly, and never the source of
    an edge: it only chooses which documents are checked live, and decides whether a count over the
    documents NOT checked could still change)."""

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


def gate1_view(index: Index, readable: frozenset) -> _Gate1View:
    """The Gate-1 structure the overview is built on (for a caller that checks it live itself)."""
    return _Gate1View(index, readable)


def overview_work(g1: _Gate1View) -> list[str]:
    """The overview's live-check work list, in the order ``build_graph`` checks it: every Gate-1
    document, newest first. Only the first ``MAX_CHECKS_OVERVIEW`` count for the overview."""
    return g1.recent(g1.rows)


@dataclass(frozen=True)
class OverviewChecks:
    """One live pass over :func:`overview_work` of ``g1``, decided by the caller in THIS request
    (``GET /ib/galaxy``). ``ok`` holds the authorised snapshots, ``undecided`` the ids whose check or
    read-through did not finish by the deadline, and ``attempted`` the ids handed to
    ``authz.authorize`` (an id never attempted is not decided either)."""
    g1: _Gate1View
    ok: Mapping[str, authz.AuthorizedDoc]
    undecided: frozenset
    attempted: frozenset


def build_graph(user: DirectoryUser, *, index: Index, directory: Directory, ledger: Any,
                connectors: Mapping[str, Any], clock: Callable[[], datetime],
                focus: Optional[str] = None, q: Optional[str] = None,
                limit: int = DEFAULT_LIMIT, timeout_s: float = authz.VIEW_CHECK_TIMEOUT_S,
                pre: Optional[OverviewChecks] = None,
                now: Optional[datetime] = None) -> dict:
    """The graph dict, or raises :class:`NotFound` (after ledgering it). Any ledger failure
    propagates (the route answers 503).

    ``pre`` (overview only): the live checks were already made in this request over
    ``overview_work(pre.g1)``; the overview is built from them without another source call."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    now = now if now is not None else clock()
    focus = (focus or "").strip() or None
    q = (q or "").strip() or None
    mode = "focus" if focus else ("search" if q else "overview")
    if pre is not None and mode != "overview":
        raise ValueError("pre-authorised checks serve the overview only")
    if pre is not None:
        g1 = pre.g1
        readable = g1.readable                                      # GATE 1, as checked
    else:
        epoch = getattr(directory, "epoch", 0)
        readable = index.readable(user, epoch)                      # GATE 1
        g1 = _Gate1View(index, readable)
    ranked: list[str] = []
    deadline = authz.deadline_in(timeout_s)
    ok: dict[str, authz.AuthorizedDoc] = {}
    undecided: set[str] = set()
    checked: set[str] = set()           # decided live in this call (allowed or denied)
    n_checked = 0
    settled = mode != "search"          # search: the hit list no longer changes after a check

    # 1 — which documents to check live (all inside Gate 1)
    universe: list[str] = []
    if mode == "overview":
        universe = overview_work(g1)
        cap = MAX_CHECKS_OVERVIEW
    elif mode == "search":
        window = parse_window(q, now)
        sq = strip_window(q)
        # Like the pipeline (Opus final review #8): a live-denied hit leaves the search corpus and
        # the search is run again, so a stale (revoked-at-source) document never takes a hit slot
        # from a permitted one. Each round checks only the new hits; bounded rounds and deadline.
        denied: set[str] = set()
        for _round in range(SEARCH_ROUNDS):
            pool = frozenset(readable) - denied
            hits = index.search(sq, pool, k=TOP_K, window=window, relevant_only=True)
            seen = {h.chunk_id for h in hits}
            for platform in platform_hints(q):          # the pipeline's per-platform recall slots
                scoped = [d for d in pool if d.startswith(platform + ":")]
                for h in index.search(sq, scoped, k=PER_PLATFORM_K, window=window,
                                      relevant_only=True):
                    if h.chunk_id not in seen:
                        hits.append(h)
                        seen.add(h.chunk_id)
            ranked = [d for d in dict.fromkeys(h.doc_id for h in hits) if d in g1.rows]
            new = [d for d in ranked if d not in checked and d not in undecided]
            if not new:
                settled = True
                break
            if authz.remaining(deadline) <= 0:
                break
            got, n = authz.authorize(user, new, index=index, connectors=connectors,
                                     readable=readable, deadline=deadline, undecided=undecided)
            n_checked += n
            ok.update(got)
            checked.update(d for d in new if d not in undecided)
            denied.update(d for d in new if d not in got)
        ranked = [d for d in ranked if d in ok]
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
    doc_focus = mode == "focus" and focus in g1.rows
    expand: set[str] = set()            # documents whose CURRENT links join the work list
    if doc_focus:
        expand = {focus}
    elif mode == "focus" and focus.startswith("container:"):
        parts = focus.split(":", 2)
        expand = set(g1.by_cont.get((parts[1], parts[2]), ())) if len(parts) == 3 else set()

    # 2 — GATE 2 + version-bound read-through, in bounded batches, under ONE deadline. A document
    # focus is decided FIRST: one that cannot be opened is the same 404 as an unknown id, without
    # checking its neighbourhood.
    queue = [d for d in dict.fromkeys(universe) if d not in checked and d not in undecided]
    queued = set(universe)
    i = 0
    if pre is not None:
        # the same work list and cap as the loop below, decided by the caller's single pass: an id
        # it did not attempt, or did not decide by its deadline, is undecided here as well
        covered = queue[:cap]
        ok = {d: pre.ok[d] for d in covered if d in pre.ok}
        undecided = {d for d in covered if d in pre.undecided or d not in pre.attempted}
        checked = {d for d in covered if d not in undecided}
        n_checked = len(checked)        # decided live; equals the loop's count when all were
        i = len(covered)
    while pre is None and i < len(queue) and n_checked < cap and authz.remaining(deadline) > 0:
        size = 1 if (i == 0 and doc_focus) else CHECK_BATCH
        batch = queue[i: i + min(size, cap - n_checked)]
        i += len(batch)
        got, n = authz.authorize(user, batch, index=index, connectors=connectors,
                                 readable=readable, deadline=deadline, undecided=undecided)
        n_checked += n
        ok.update(got)
        checked.update(d for d in batch if d not in undecided)
        if doc_focus and focus not in ok:
            break                       # the focus itself is not openable: stop (404 below)
        for d in batch:                 # targets the CURRENT version links to join the work list
            if d in got and d in expand:
                for link in got[d].links:
                    for t in authz.link_candidates(link, readable):
                        if t not in queued:
                            queue.append(t)
                            queued.add(t)
    complete = i >= len(queue) and not undecided and settled
    ok_ids = set(ok)

    # 3 — the confirmed graph G, built ONLY from the authorised snapshots
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
    for d, s in ok.items():             # edges from the CURRENT, authorised version only
        seen_t: set[str] = set()
        for link in s.links:
            t = authz.resolve_link(link, ok_ids)        # resolved inside the confirmed set
            if t and t != d and t not in seen_t:
                seen_t.add(t)
                # connectors store a Jira KEY written in text as "jira:KEY" (a mention) and a
                # supported URL as "<platform>:<id>" (a link): see connectors/_http.extract_links
                add_edge(d, t, "mentions" if str(link).startswith("jira:") else "links")

    def all_checked(ids) -> bool:
        return all(x in checked for x in ids)

    def exact(node_id: str) -> bool:
        n = nodes[node_id]
        if n["kind"] == "doc":
            outs = [c for link in ok[node_id].links for c in authz.link_candidates(link, readable)]
            return all_checked(outs) and all_checked(x for x, _ in g1.inn.get(node_id, ()))
        if n["kind"] == "container":
            key = node_id.split(":", 2)
            return all_checked(g1.by_cont.get((key[1], key[2]), ()))
        if n["kind"] == "person":
            return all_checked(g1.by_author.get(node_id.split(":", 1)[1], ()))
        return all_checked(g1.by_plat.get(n["platform"], ()))

    def recent(ids) -> list[str]:       # confirmed documents, newest CHECKED version first
        return sorted(ids, key=lambda d: (ok[d].updated_at if d in ok else "", d), reverse=True)

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
            order += recent([x for x in nbrs[focus] if nodes[x]["kind"] == "doc"])
            order.append(plid(f["platform"]))
        elif f["kind"] == "container":
            docs = recent(docs_under(focus))
            first = docs[: max(1, (limit * 2) // 3)]
            order += [plid(f["platform"])] + first
            order += sorted({a for d in first for a in nbrs[d] if nodes[a]["kind"] == "person"})
            order += recent({t for d in first for t in nbrs[d]
                             if nodes[t]["kind"] == "doc" and t not in docs})
            order += docs[len(first):]
        elif f["kind"] == "person":
            docs = recent(docs_under(focus))
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
            "limit": limit, "truncated": truncated, "complete": complete,
            "nodes": out_nodes, "edges": out_edges,
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

"""``/graph`` — the knowledge graph itself, as nodes and edges a person can look at.

**Why this exists.** Everything else in this gateway describes the graph without showing it.
`/trace` shows what the agents said to each other, `/ontology` shows which kinds are declarable,
`/evidence` shows one pointer at a time. The graph — the artefact the whole system exists to
produce — had no surface at all until this module. A system whose central output is invisible is
indistinguishable, to anyone looking at it, from a system that does not produce one.

**What it must not become.** Not a visualisation of "the data". Every node here is grounded: it
carries the pointers that put it there, and the adapter read that produced those pointers. The
value of showing the graph is that a reader can click a node and arrive at *why it is there*,
which is the same click-through `/evidence` serves for a single value. If this surface ever
renders a node without its provenance reachable, it has become a chart.

**Scale is the design constraint.** The government graph is 46,346 nodes and 52,747 edges. Nothing
here loads a graph into memory to answer a question about it, and no response is unbounded: every
route is a bounded SQL query (a `LIMIT`, an indexed lookup, or the reverse index's own bounded
closure) and every response that windows its result says, in the response body, how many rows
matched beyond the window — the same discipline `ReverseIndex.coverage()` follows when it answers
"unknown" with an exact count rather than implying "nothing" (`ptr_index.py`). The two
whole-graph aggregates — the degree ranking the default browse seeds from, and component
statistics in `/graph/facets` — read only the edge table's two endpoint columns, are cached per
store VERSION (max rowid + open-edge count), and component stats refuse to compute above
``COMPONENT_EDGE_CAP`` rather than sample.

**What `neighbors()` / `as_of()` in `query.py` do that this module never does.** Both call
`store.open_nodes()` / `store.open_edges()` — every open row, materialised as Python objects — and
that is exactly right for a demo-sized clinic graph and exactly wrong at estate scale (46k
nodes / 52k edges loaded on every request). This module re-implements a *bounded* BFS directly in
SQL instead: one indexed query per hop, capped by a node budget, so a click on `/graph` costs work
proportional to the neighbourhood shown, never to the graph's total size.

**Which store answers.** ONE store: the gateway's shared, file-backed workspace graph,
``app.state.graph_store`` (``WALNUT_GRAPH_DB_PATH``, default ``var/graph/workspace.db``) — the same
object ``app.state.brain.store`` holds, so `/graph`, `/brain` and ingestion never disagree about
what the graph contains. This module never opens a SQLite file of its own: a route module that
opened its own copy would answer about a different graph than the one ingestion just wrote to, and
a second connection cached here would survive a ``POST /graph/reload`` that replaced the shared
one. When ``app.state.graph_store`` is not attached (an older boot path, or a test app that only
sets ``app.state.brain``) it falls back to ``app.state.brain.store``. `GET /graph/facets` names
which one is answering (`graph_source`, including the file path) so a screenshot is never
ambiguous about what it is a screenshot of.

**Kind and source facets are read from the STORE, not from the bound pack vocabulary.** The
obvious alternative — `databridge_graph.packs.active_vocabulary()` — answers about whichever packs
this *process* has bound (`packs/deployment.yaml`), which need not be the packs that wrote the rows
in the store: a workspace graph can carry rows written under a binding that has since changed.
Constructing a `NodeKind` for a kind the current binding does not declare raises `UnknownKind` even
though the store is full of exactly that kind. Every kind a node in THIS store actually carries is,
by construction, one an enabled pack declared at the moment it was written (the grounding wall
never lets an uncoined kind through) — so reading `DISTINCT kind` off the store is the accurate
answer and the vocabulary-free one, and it has the added honesty that a declared-but-unused kind
never appears as a filter with nothing behind it.

**Connected, not confetti.** The unfiltered browse (no seed, no filter) used to be the first 60
rows by insertion order — which, for a graph that ingestion writes source by source, is 60 nodes
from one table with almost no edges between them: a scatter of isolated dots that reads as "the
graph is disconnected" when it is not. It now seeds from the highest-degree nodes and expands one
hop from each, so the first picture is the graph's actual backbone, and it drops any node that
would be drawn with no edge, reporting how many were omitted and why.

**Node ids are opaque strings, and identity is never inferred.** ``kind:slug`` is a convention, not
a promise this module parses; every id handed back to the browser is one the store itself produced.

**R-015.** Every route below reads real graph rows — attributes, pointers, historical versions,
source ids — and used to be reachable by anyone (`phase5-gw-graph-trace.md` #1). Every route takes
`auth.require_principal`. Every route is read-only except ``POST /graph/reload``, which only asks
the gateway to re-open the shared store it already owns (``app.state.reload_graph``); it never
writes a row.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Depends, FastAPI, Query
from fastapi.responses import JSONResponse

from .auth import AuthenticatedPrincipal, install_auth_handler, require_principal
from .error_shaping import safe_detail

log = logging.getLogger("databridge.gateway.graph")

#: Node-count ceiling for one neighbourhood response. A `/graph` click costs work bounded by this,
#: never by the graph's total size.
DEFAULT_NODE_LIMIT = 60
MAX_NODE_LIMIT = 250
DEFAULT_DEPTH = 2
MAX_DEPTH = 3
#: Edge rows read per BFS hop, across all frontier nodes. Bounds a single high-degree hub from
#: turning one hop into a full-table scan's worth of work.
EDGE_BUDGET_PER_HOP = 1200
#: How many (source_id, count) rows `/graph/facets` returns before it says "and N more".
SOURCE_FACET_LIMIT = 60
#: SQLite parameter cap safety margin for `IN (...)` chunking (mirrors `ptr_index.py`): one
#: `IN (...)` list per statement stays under the historical 999-variable limit.
_PARAM_CHUNK = 900
#: The edge probe binds its id list TWICE (`src_id IN (…) OR dst_id IN (…)`) plus one `LIMIT`, so
#: its chunk is half the margin — 900 ids there was 1,801 bindings (R-017 t07 M3).
_EDGE_PARAM_CHUNK = (_PARAM_CHUNK - 1) // 2
#: Edges read, per node in view, when pulling in the edges BETWEEN nodes already shown.
CLOSING_EDGES_PER_NODE = 4
#: One node's detail: incident edges and bi-temporal versions listed at most (each with an exact
#: total beside it). A detail page, not an export (R-017 t07 H4).
EDGE_TOUCH_LIMIT = 200
VERSION_LIMIT = 100
#: One entity's detail: entity-level pointers listed at most, and cited attributes listed at most
#: (each with an exact total and a `*_truncated` flag — R-019 t07 H3).
SOURCE_LIMIT = 200
ATTR_SOURCE_LIMIT = 500
SEARCH_DEFAULT_LIMIT = 20
SEARCH_MAX_LIMIT = 100


def _refusal(code: str, message: str, status: int = 422) -> JSONResponse:
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": None}})


def _chunks(items, size: int = _PARAM_CHUNK):
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start:start + size]


# --------------------------------------------------------------------------------------------
# Which store answers
# --------------------------------------------------------------------------------------------
def _store_file(store) -> Optional[str]:
    """The file backing ``store``, or ``None`` for an in-memory store. Read off SQLite itself
    (``PRAGMA database_list``) rather than remembered, so it cannot drift from what is open."""
    try:
        for row in store.fetchall("PRAGMA database_list"):
            if row["name"] == "main":
                return row["file"] or None
    except Exception:                                                # noqa: BLE001
        return None
    return None


def _resolve_store(app: FastAPI):
    """``(store, label)`` — the graph this module answers about, and a human-readable name for it.

    Resolved on EVERY request, never cached here: ``POST /graph/reload`` (and ingestion) may swap
    ``app.state.graph_store`` for a fresh object, and a module-level cache would keep answering
    about the old one.

    ``label`` travels in every response that names a store at all, so a person looking at a small
    response can tell which store — and which file — it came from.
    """
    store = getattr(app.state, "graph_store", None)
    origin = "app.state.graph_store"
    if store is None:
        brain = getattr(app.state, "brain", None)
        store = getattr(brain, "store", None) if brain is not None else None
        origin = "app.state.brain.store"
    if store is None:
        return None, None
    path = _store_file(store)
    return store, (f"file:{path} ({origin})" if path else f"in-memory ({origin})")


# --------------------------------------------------------------------------------------------
# Whole-graph statistics — computed once per store VERSION, not once per request
# --------------------------------------------------------------------------------------------
#: Above this many open edges, component statistics are reported as UNKNOWN rather than computed:
#: union-find over a few hundred thousand (src, dst) pairs is tens of milliseconds; over tens of
#: millions it would be the bottleneck the rest of this module exists to avoid.
COMPONENT_EDGE_CAP = 500_000
#: How many hubs the default browse considers before giving up on filling the window.
HUB_CANDIDATES = 200
_STATS_CACHE: dict[tuple, dict] = {}
_STATS_CACHE_MAX = 8


def _fingerprint(store, include_closed: bool) -> tuple:
    """Changes whenever a row is written or closed. ``MAX(rowid_pk)`` is a primary-key lookup;
    the open-edge count catches a close that inserts no replacement row."""
    n = store.fetchone("SELECT MAX(rowid_pk) m FROM nodes")
    e = store.fetchone("SELECT MAX(rowid_pk) m FROM edges")
    oe = store.fetchone("SELECT COUNT(*) c FROM edges WHERE system_to IS NULL")
    return (id(store), n["m"] if n else None, e["m"] if e else None,
            oe["c"] if oe else 0, bool(include_closed))


def _cached(store, include_closed: bool, name: str, compute):
    key = (*_fingerprint(store, include_closed), name)
    hit = _STATS_CACHE.get(key)
    if hit is not None:
        return hit["value"]
    value = compute()
    if len(_STATS_CACHE) >= _STATS_CACHE_MAX:
        _STATS_CACHE.pop(next(iter(_STATS_CACHE)))
    _STATS_CACHE[key] = {"value": value}
    return value


def _clear_stats_cache() -> None:
    _STATS_CACHE.clear()


def _edge_clause(include_closed: bool) -> str:
    return "1=1" if include_closed else "system_to IS NULL"


def _top_degree_nodes(store, *, include_closed: bool) -> list[tuple[str, int]]:
    """``[(node_id, degree)]`` for the ``HUB_CANDIDATES`` best-connected nodes, highest first
    (ties by id, so the order is deterministic). One grouped scan of the edge table's two
    indexed columns — never a node materialised."""
    clause = _edge_clause(include_closed)

    def compute():
        rows = store.fetchall(
            f"SELECT nid, SUM(c) deg FROM ("
            f"  SELECT src_id nid, COUNT(*) c FROM edges WHERE {clause} GROUP BY src_id"
            f"  UNION ALL"
            f"  SELECT dst_id nid, COUNT(*) c FROM edges WHERE {clause} GROUP BY dst_id"
            f") GROUP BY nid ORDER BY deg DESC, nid LIMIT ?", (HUB_CANDIDATES,))
        return [(r["nid"], int(r["deg"])) for r in rows]

    return _cached(store, include_closed, "top_degree", compute)


def _isolated_node_count(store, *, include_closed: bool) -> int:
    """Nodes in scope that touch no edge in scope. Two indexed ``EXISTS`` probes per node."""
    node_clause = "1=1" if include_closed else "n.system_to IS NULL"
    edge_extra = "" if include_closed else " AND e.system_to IS NULL"

    def compute():
        row = store.fetchone(
            f"SELECT COUNT(DISTINCT n.id) c FROM nodes n WHERE {node_clause} "
            f"AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.src_id = n.id{edge_extra}) "
            f"AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.dst_id = n.id{edge_extra})")
        return int(row["c"]) if row is not None else 0

    return _cached(store, include_closed, "isolated", compute)


def _component_stats(store) -> dict:
    """Connected-component statistics over OPEN edges, treated as undirected.

    Union-find over ``(src_id, dst_id)`` pairs read straight from SQLite — two short strings per
    edge, never a `GraphEdge`. Isolated nodes are their own component. Above
    ``COMPONENT_EDGE_CAP`` open edges the answer is ``computed: false`` with the reason, never a
    number derived from a sample."""
    def compute():
        open_edges = int(store.fetchone(
            "SELECT COUNT(*) c FROM edges WHERE system_to IS NULL")["c"])
        if open_edges > COMPONENT_EDGE_CAP:
            return {"computed": False, "count": None, "largest": None,
                    "isolated_nodes": _isolated_node_count(store, include_closed=False),
                    "reason": (f"{open_edges} open edges exceeds the {COMPONENT_EDGE_CAP}-edge cap "
                               f"for computing components on request")}
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            root = x
            while parent[root] != root:
                root = parent[root]
            while parent[x] != root:                       # path compression
                parent[x], x = root, parent[x]
            return root

        for r in store.fetchall("SELECT src_id, dst_id FROM edges WHERE system_to IS NULL"):
            a, b = r["src_id"], r["dst_id"]
            parent.setdefault(a, a)
            parent.setdefault(b, b)
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        sizes: dict[str, int] = {}
        for node in parent:
            root = find(node)
            sizes[root] = sizes.get(root, 0) + 1
        isolated = _isolated_node_count(store, include_closed=False)
        largest = max(sizes.values()) if sizes else (1 if isolated else 0)
        return {"computed": True, "count": len(sizes) + isolated, "largest": largest,
                "isolated_nodes": isolated,
                "connected_components": len(sizes),
                "reason": None}

    return _cached(store, False, "components", compute)


def _kind_edge_counts(store) -> dict[str, int]:
    """Open edges incident to open nodes of each kind (an edge between two kinds counts once for
    each end). A join on the indexed endpoint columns, cached per store version."""
    def compute():
        rows = store.fetchall(
            "SELECT kind, SUM(c) c FROM ("
            "  SELECT n.kind kind, COUNT(*) c FROM edges e JOIN nodes n "
            "    ON n.id = e.src_id AND n.system_to IS NULL WHERE e.system_to IS NULL GROUP BY n.kind"
            "  UNION ALL"
            "  SELECT n.kind kind, COUNT(*) c FROM edges e JOIN nodes n "
            "    ON n.id = e.dst_id AND n.system_to IS NULL WHERE e.system_to IS NULL GROUP BY n.kind"
            ") GROUP BY kind")
        return {r["kind"]: int(r["c"]) for r in rows}

    return _cached(store, False, "kind_edges", compute)


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


#: The SQL function name the case-folding helper is registered under on the store's connection.
_FOLD_FN = "walnut_casefold"


def _casefold(value: Any) -> Optional[str]:
    return value.casefold() if isinstance(value, str) else value


def _folding(fn: str) -> str:
    """What a fold function actually does, for the response: never a silent degrade (R-019 t07
    M2)."""
    return "unicode" if fn == _FOLD_FN else "ascii_only"


_ASCII_FOLD_NOTE = ("case-insensitive matching is ASCII-only on this store (the Unicode fold "
                    "could not be registered): a non-ASCII label may be missed")


def _fold_sql(store) -> str:
    """The SQL function a case-insensitive match folds with: Python's ``str.casefold`` registered
    on the store's connection, or SQLite's ASCII-only ``LOWER`` when it cannot be registered.

    SQLite's ``LOWER`` folds ASCII only, so ``ÅNGSTRÖM`` never matched a stored ``Ångström``
    (R-017 t07 M4). Registered on EVERY request, because ``POST /graph/reload`` swaps the
    connection object and a function registered on the old one is gone."""
    conn = getattr(store, "conn", None)
    if conn is None:
        return "LOWER"
    try:
        lock = getattr(store, "_lock", None)
        if lock is not None:
            with lock:
                conn.create_function(_FOLD_FN, 1, _casefold, deterministic=True)
        else:
            conn.create_function(_FOLD_FN, 1, _casefold, deterministic=True)
    except Exception:                                                # noqa: BLE001
        return "LOWER"
    return _FOLD_FN


# --------------------------------------------------------------------------------------------
# facets — SQL aggregates, never a materialised graph
# --------------------------------------------------------------------------------------------
def _node_kind_facets(store) -> list[dict]:
    rows = store.fetchall(
        "SELECT kind, COUNT(*) c FROM nodes WHERE system_to IS NULL "
        "GROUP BY kind ORDER BY c DESC, kind")
    edge_counts = _kind_edge_counts(store)
    return [{"kind": r["kind"], "count": int(r["c"]),
             "incident_edges": edge_counts.get(r["kind"], 0)} for r in rows]


def _edge_kind_facets(store) -> list[dict]:
    rows = store.fetchall(
        "SELECT kind, COUNT(*) c FROM edges WHERE system_to IS NULL "
        "GROUP BY kind ORDER BY c DESC, kind")
    return [{"kind": r["kind"], "count": int(r["c"])} for r in rows]


def _source_facets(store) -> dict:
    total = store.fetchone("SELECT COUNT(DISTINCT source_id) c FROM ptr_index")
    total_n = int(total["c"]) if total is not None else 0
    rows = store.fetchall(
        "SELECT source_id, COUNT(*) c FROM ptr_index GROUP BY source_id "
        "ORDER BY c DESC, source_id LIMIT ?", (SOURCE_FACET_LIMIT + 1,))
    items = [{"source_id": r["source_id"], "pointer_count": int(r["c"])} for r in rows]
    truncated = len(items) > SOURCE_FACET_LIMIT
    if truncated:
        items = items[:SOURCE_FACET_LIMIT]
    return {"items": items, "total_distinct": total_n, "returned": len(items),
            "truncated": truncated,
            "left_out": max(0, total_n - len(items))}


def _coverage(store) -> dict:
    from databridge_graph.ptr_index import Answer

    answer, reason, missing = store.reverse_index.coverage()
    return {"answer": answer.value if isinstance(answer, Answer) else str(answer),
            "reason": reason, "unindexed_pointers": missing}


# --------------------------------------------------------------------------------------------
# bounded BFS — one indexed query per hop, never the whole edge table
# --------------------------------------------------------------------------------------------
def _edges_touching_chunk(store, node_ids: list[str], *, include_closed: bool,
                          budget: int) -> tuple[list, bool]:
    """``(rows, complete)`` — see :func:`_edges_touching_chunk_counted`."""
    rows, complete, _raw = _edges_touching_chunk_counted(
        store, node_ids, include_closed=include_closed, budget=budget)
    return rows, complete


def _edges_touching_chunk_counted(store, node_ids: list[str], *, include_closed: bool,
                                  budget: int) -> tuple[list, bool, int]:
    """``(rows, complete, raw_read)`` — the edges touching ``node_ids``, deduplicated, whether
    that is ALL of them, and how many rows were actually READ (an edge joining two chunks is read
    once per chunk). A caller holding a budget across calls must charge ``raw_read``, not
    ``len(rows)`` — charging the deduplicated count let a dense frontier read past the advertised
    budget (R-019 t07 H2).

    ``complete`` is False when any chunk's query was capped at the remaining budget or any chunk
    was never queried (R-017 t07 H2): an edge joining two chunks is read twice and counted against
    the budget twice, so "fewer unique rows than the budget" is not evidence that nothing was
    left unread. The budget is charged for RAW rows read, because that is the work done."""
    if not node_ids:
        return [], True, 0
    if budget <= 0:
        return [], False, 0
    closed_clause = "" if include_closed else " AND system_to IS NULL"
    out = []
    seen_ids: set[str] = set()
    complete = True
    raw_read = 0
    chunks = list(_chunks(node_ids, _EDGE_PARAM_CHUNK))
    for index, chunk in enumerate(chunks):
        if budget <= 0:
            complete = False            # chunks [index:] were never read
            break
        marks = ",".join("?" * len(chunk))
        rows = store.fetchall(
            f"SELECT * FROM edges WHERE (src_id IN ({marks}) OR dst_id IN ({marks})){closed_clause} "
            f"ORDER BY rowid_pk LIMIT ?",
            (*chunk, *chunk, budget))
        if len(rows) >= budget:
            complete = False            # the LIMIT bit: this chunk may hold more
        for r in rows:
            if r["id"] in seen_ids:
                continue
            seen_ids.add(r["id"])
            out.append(r)
        budget -= len(rows)
        raw_read += len(rows)
    return out, complete, raw_read


def _closing_edges(store, visited: set[str], *, include_closed: bool) -> tuple[list, bool]:
    """Edges BETWEEN nodes already in view, and whether that list is complete."""
    rows, complete = _edges_touching_chunk(
        store, list(visited), include_closed=include_closed,
        budget=max(EDGE_BUDGET_PER_HOP, len(visited) * CLOSING_EDGES_PER_NODE))
    return [r for r in rows if r["src_id"] in visited and r["dst_id"] in visited], complete


def _bfs(store, seed_id: str, *, depth: int, node_limit: int,
         include_closed: bool) -> tuple[set[str], list, bool, bool]:
    """``(visited_node_ids, edge_rows, nodes_truncated, edges_truncated)`` — bounded expansion
    from one seed.

    Never materialises `store.open_edges()`. Each hop issues one indexed query per chunk of the
    current frontier (`ix_edges_src` / `ix_edges_dst`), and the whole walk stops the moment
    ``node_limit`` is reached — a hub with thousands of neighbours truncates rather than pulling
    the graph in behind it.

    The two flags are separate facts (R-017 t07 H3). ``nodes_truncated`` means a RESOURCE limit
    may have left NODES within ``depth`` unseen — the node cap rejected a real neighbour, or an
    edge budget ran out before a hop's edges were fully read. ``edges_truncated`` means the
    closing pass (the edges BETWEEN nodes in view) was capped, so some edges among the nodes shown
    are missing although every node is there. Neither is set merely because ``depth`` bounded how
    far the walk looked: that is the caller's own scope, already named in the response.
    """
    visited = {seed_id}
    frontier = [seed_id]
    edge_rows: list = []
    edge_ids_seen: set[str] = set()
    nodes_truncated = False
    remaining_edge_budget = EDGE_BUDGET_PER_HOP * max(1, depth)

    for _ in range(depth):
        if not frontier:
            break
        if len(visited) >= node_limit:
            # The frontier still has unexplored nodes and we are stopping anyway — a resource
            # limit, not the requested depth, is why this hop never ran.
            nodes_truncated = True
            break
        hop_edges, complete, raw_read = _edges_touching_chunk_counted(
            store, frontier, include_closed=include_closed, budget=remaining_edge_budget)
        if not complete:
            # A capped or skipped chunk: this hop's edge set is possibly incomplete.
            nodes_truncated = True
        remaining_edge_budget -= raw_read
        next_frontier: list[str] = []
        for r in hop_edges:
            if r["id"] not in edge_ids_seen:
                edge_ids_seen.add(r["id"])
                edge_rows.append(r)
            for other in (r["src_id"], r["dst_id"]):
                if other not in visited:
                    if len(visited) >= node_limit:
                        nodes_truncated = True
                        continue
                    visited.add(other)
                    next_frontier.append(other)
        frontier = next_frontier
        if not complete:
            break

    # Edges among the visited set that the BFS itself did not need to traverse (e.g. two nodes
    # both reached via a third) are still part of "the neighbourhood shown" — pull them in now,
    # bounded to the same visited set so the query touches only rows already in view.
    closing, closing_complete = _closing_edges(store, visited, include_closed=include_closed)
    for r in closing:
        if r["id"] not in edge_ids_seen:
            edge_ids_seen.add(r["id"])
            edge_rows.append(r)

    # Only edges BETWEEN retained nodes (R-019 t07 H1). A hop reads every edge of its frontier,
    # including edges to neighbours the node cap then rejected; returning those drew lines to
    # nodes the response does not contain. A dropped edge's far end was cut by the cap, so
    # `nodes_truncated` already says the window is incomplete.
    edge_rows = [r for r in edge_rows if r["src_id"] in visited and r["dst_id"] in visited]
    return visited, edge_rows, nodes_truncated, not closing_complete


#: Every value a `/graph` row emits is one of three things (R-019, Codex round 3): backed by a
#: pointer (`pointers`), withheld with only its name (`withheld_ungrounded`), or STRUCTURE — a
#: value the store itself computed or assigned (an id, the ontology kind the row was written
#: under, the bi-temporal bookkeeping, a count), which is not a claim about the world and so has
#: no source span to point at. These lists name the structure fields, so a reader never has to
#: guess which kind of value it is looking at. An edge's endpoints and kind ARE the claim its
#: entity pointer makes, so they are listed under that pointer's `grounds`, not here.
NODE_STRUCTURE_FIELDS = ("id", "kind", "valid_from", "valid_to", "system_from", "system_to",
                         "is_open", "attr_count", "unresolved_count", "degree")
EDGE_STRUCTURE_FIELDS = ("id", "weight", "valid_from", "valid_to", "system_from", "system_to",
                         "is_open")
EDGE_POINTER_GROUNDS = ("src_id", "kind", "dst_id")
#: Keys that describe provenance itself rather than carrying a value.
PROVENANCE_KEYS = ("pointers", "withheld_ungrounded", "sources_total", "structure_not_a_claim")


def _ptr_json(p) -> Optional[dict]:
    return p.model_dump(mode="json", exclude_none=True) if p is not None else None


def _row_pointers(store, r, fields: tuple[str, ...]) -> tuple[dict, list[str], int]:
    """``(pointers, fields_without_pointer, sources_total)`` for ONE version row (R-019 t07 C1).

    Every grounded value a list route emits travels with the pointer that grounds it, resolved
    against THIS row's version (``rowid_pk``) — never a pointer pooled from another version:

      * a field in ``fields`` whose value is set gets its own per-attribute pointer when the row
        has one; for ``label`` the entity pointer is the fallback (the label is what the entity
        pointer read), and the entry says which (``basis``: ``attribute`` / ``entity``);
      * ``edge`` (edges) / the entity pointer is the FIRST entity-level pointer, with the exact
        count beside it — one pointer per field, so a 250-node window stays bounded;
      * a set value with no pointer at all is NOT emitted: the caller withholds it and names the
        field in ``withheld_ungrounded`` (hard constraint 2).

    ``system_from`` / ``system_to`` are the store's own clock, not source values, and carry none.
    """
    vpk = r["rowid_pk"]
    entity, total = store.sources_window(r["id"], version_pk=vpk, limit=1)
    wanted = [f for f in fields if f not in ("edge",) and r[f] is not None]
    attr, _n = store.attr_sources_window(r["id"], version_pk=vpk, fields=wanted,
                                         limit=len(wanted) or 1) if wanted else ({}, 0)
    first = entity[0] if entity else None
    pointers: dict[str, Optional[dict]] = {}
    missing: list[str] = []
    for f in fields:
        if f == "edge":
            pointers[f] = ({**_ptr_json(first), "grounds": list(EDGE_POINTER_GROUNDS)}
                           if first is not None else None)
            if first is None:
                missing.append(f)
            continue
        if r[f] is None:
            continue
        p = attr.get(f)
        basis = "attribute"
        if p is None and f == "label" and first is not None:
            p, basis = first, "entity"
        if p is None:
            pointers[f] = None
            missing.append(f)
        else:
            pointers[f] = {**_ptr_json(p), "basis": basis}
    return pointers, missing, total


def _node_row_dict(r, store=None) -> dict:
    import json

    out = {
        "id": r["id"], "kind": r["kind"], "label": r["label"],
        "canonical_id": r["canonical_id"],
        "valid_from": r["valid_from"], "valid_to": r["valid_to"],
        "system_from": r["system_from"], "system_to": r["system_to"],
        "is_open": r["system_to"] is None,
        "attr_count": sum(1 for v in json.loads(r["attrs_json"] or "{}").values()
                          if v is not None),
        "unresolved_count": sum(1 for v in json.loads(r["attrs_json"] or "{}").values()
                                if v is None),
    }
    if store is not None:
        pointers, missing, total = _row_pointers(store, r, ("label", "canonical_id"))
        for f in missing:                       # never a value without its pointer
            out[f] = None
            pointers.pop(f, None)
        out.update({"pointers": pointers, "withheld_ungrounded": missing,
                    "sources_total": total,
                    "structure_not_a_claim": list(NODE_STRUCTURE_FIELDS)})
    return out


def _edge_row_dict(r, store=None) -> dict:
    out = {
        "id": r["id"], "kind": r["kind"], "src_id": r["src_id"], "dst_id": r["dst_id"],
        "weight": r["weight"],
        "valid_from": r["valid_from"], "valid_to": r["valid_to"],
        "system_from": r["system_from"], "system_to": r["system_to"],
        "is_open": r["system_to"] is None,
    }
    if store is not None:
        pointers, missing, total = _row_pointers(store, r, ("edge",))
        out.update({"pointers": pointers, "withheld_ungrounded": missing,
                    "sources_total": total,
                    "structure_not_a_claim": list(EDGE_STRUCTURE_FIELDS)})
    return out


def _grounded_edge_dicts(store, edge_rows) -> tuple[list[dict], int]:
    """``(edge_dicts, withheld_count)``. An edge's endpoints and kind ARE its claim; with no
    entity pointer behind them they would be a fact with nothing under it, so a pointerless edge
    (only a legacy row can be one -- `GraphEdge` refuses construction without a source) is left
    out of the listing entirely and only COUNTED (hard constraint 2; R-019 coordinator
    addition)."""
    out, withheld = [], 0
    for r in edge_rows:
        d = _edge_row_dict(r, store)
        if "edge" in d["withheld_ungrounded"]:
            withheld += 1
            continue
        out.append(d)
    return out, withheld


def _withhold_ungrounded(store, entity_id: str, version_pk: int, attrs: dict,
                         canonical_id: Optional[str] = None) -> tuple[dict, dict, list[str]]:
    """``(attrs_to_emit, their_pointers, withheld)`` — every non-``None`` attribute (and a set
    ``canonical_id``) that has NO pointer of its own on this version is withheld server-side and
    only its NAME is returned (hard constraint 2; R-019, handed over from the UI track). A ``None``
    value is an unresolved question, not a value, and stays. Bounded by the row's own attribute
    count: exactly the emitted fields are looked up."""
    cited = [k for k, v in attrs.items() if v is not None]
    if canonical_id is not None:
        cited.append("canonical_id")
    ptrs, _n = (store.attr_sources_window(entity_id, version_pk=version_pk, fields=cited,
                                          limit=len(cited)) if cited else ({}, 0))
    withheld = sorted(k for k in cited if k not in ptrs)
    kept = {k: v for k, v in attrs.items() if k not in withheld}
    return kept, ptrs, withheld


def _version_entry(store, v) -> dict:
    """One bi-temporal version of a node: its label cited against THAT version, or withheld."""
    pointers, missing, _total = _row_pointers(store, v, ("label",))
    return {"valid_from": v["valid_from"], "valid_to": v["valid_to"],
            "system_from": v["system_from"], "system_to": v["system_to"],
            "label": None if "label" in missing else v["label"],
            "is_open": v["system_to"] is None,
            "pointers": {k: p for k, p in pointers.items() if p is not None},
            "withheld_ungrounded": missing,
            "structure_not_a_claim": ["valid_from", "valid_to", "system_from", "system_to",
                                      "is_open"]}


def _fetch_nodes_by_id(store, ids: list[str], *, include_closed: bool) -> dict[str, Any]:
    if not ids:
        return {}
    closed_clause = "" if include_closed else " AND system_to IS NULL"
    out: dict[str, Any] = {}
    for chunk in _chunks(ids):
        marks = ",".join("?" * len(chunk))
        rows = store.fetchall(
            f"SELECT * FROM nodes WHERE id IN ({marks}){closed_clause} "
            f"ORDER BY rowid_pk DESC", tuple(chunk))
        for r in rows:
            out.setdefault(r["id"], r)          # first hit per id = most recent (rowid_pk DESC)
    return out


# --------------------------------------------------------------------------------------------
# browse mode (no seed): filter by kind / source / label search
# --------------------------------------------------------------------------------------------
def _browse_by_kind(store, *, kind: Optional[str], q: Optional[str], include_closed: bool,
                    limit: int) -> tuple[list, int]:
    where = ["1=1"] if include_closed else ["system_to IS NULL"]
    params: list = []
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if q:
        where.append(f"{_fold_sql(store)}(label) LIKE ? ESCAPE '\\'")
        params.append(f"%{_like_escape(q.casefold())}%")
    clause = " AND ".join(where)
    # One entry per NODE, not per version (R-019 t07 M3): with `include_closed` a node with three
    # versions matched three times, was counted three times and could fill the window with itself.
    # Count DISTINCT ids, and keep the newest matching version of each.
    total = store.fetchone(f"SELECT COUNT(DISTINCT id) c FROM nodes WHERE {clause}",
                           tuple(params))
    total_n = int(total["c"]) if total is not None else 0
    if include_closed:
        clause = (f"rowid_pk IN (SELECT MAX(rowid_pk) FROM nodes WHERE {clause} GROUP BY id)")
        params = list(params)
    # Best-connected matches first: a filtered window of 60 is far more useful as the 60 nodes
    # that sit in the graph than as the first 60 rows ingestion happened to write. Degree is two
    # indexed probes per candidate row (`ix_edges_src` / `ix_edges_dst`).
    edge_extra = "" if include_closed else " AND e.system_to IS NULL"
    rows = store.fetchall(
        f"SELECT nodes.*, "
        f"  (SELECT COUNT(*) FROM edges e WHERE e.src_id = nodes.id{edge_extra}) + "
        f"  (SELECT COUNT(*) FROM edges e WHERE e.dst_id = nodes.id{edge_extra}) AS _deg "
        f"FROM nodes WHERE {clause} ORDER BY _deg DESC, rowid_pk LIMIT ?", (*params, limit))
    return rows, total_n


def _browse_hubs(store, *, include_closed: bool,
                 limit: int) -> tuple[set[str], list, int, bool]:
    """The default view: the graph's backbone, not its first rows.

    Walks hubs in descending degree and takes each one plus its one-hop neighbourhood until the
    window is full. Every node drawn has at least one edge drawn (``_prune_isolated``), so the
    picture is a set of connected clusters around the best-connected nodes — never confetti.

    Returns ``(visited, edge_rows, hubs_used, edges_truncated)``.
    """
    visited: set[str] = set()
    edge_rows: list = []
    edge_ids: set[str] = set()
    hubs_used = 0
    edges_truncated = False
    for hub, _deg in _top_degree_nodes(store, include_closed=include_closed):
        remaining = limit - len(visited)
        if remaining < 2:                       # a lone hub with no room for a neighbour = a dot
            break
        if hub in visited:
            continue
        v, er, _nodes_cut, edges_cut = _bfs(store, hub, depth=1, node_limit=remaining,
                                            include_closed=include_closed)
        new = v - visited
        if not new:
            continue
        visited |= v
        hubs_used += 1
        edges_truncated = edges_truncated or edges_cut
        for r in er:
            if r["id"] not in edge_ids:
                edge_ids.add(r["id"])
                edge_rows.append(r)
    # Edges between two clusters (a neighbour of hub A that is also a neighbour of hub B).
    closing, complete = _closing_edges(store, visited, include_closed=include_closed)
    edges_truncated = edges_truncated or not complete
    for r in closing:
        if r["id"] not in edge_ids:
            edge_ids.add(r["id"])
            edge_rows.append(r)
    return visited, edge_rows, hubs_used, edges_truncated


def _prune_isolated(node_ids: set[str], edge_rows: list) -> set[str]:
    """Drop any node no drawn edge touches."""
    touched = {r["src_id"] for r in edge_rows} | {r["dst_id"] for r in edge_rows}
    return {n for n in node_ids if n in touched}


# --------------------------------------------------------------------------------------------
# path — bounded bidirectional BFS over open edges
# --------------------------------------------------------------------------------------------
DEFAULT_PATH_DEPTH = 6
MAX_PATH_DEPTH = 10
#: Total nodes either side may discover before the search gives up and SAYS it gave up.
PATH_NODE_BUDGET = 20_000
#: Total edge rows read across the whole search.
PATH_EDGE_BUDGET = 60_000


def _shortest_path(store, src: str, dst: str, *, max_depth: int) -> dict:
    """Undirected shortest path from ``src`` to ``dst`` over OPEN edges, at most ``max_depth``
    hops, found by expanding whichever side's frontier is smaller one full level at a time.

    Three honest outcomes, never conflated:
      * ``found``                     — the path, node by node and edge by edge;
      * ``no_path_within_max_depth``  — both sides were still growing when the hop limit hit;
      * ``disconnected``              — one side ran out of nodes: no path exists at ANY depth;
      * ``search_budget_exhausted``   — a node/edge budget stopped the search first, so absence
                                        is UNKNOWN, not established.
    """
    if src == dst:
        return {"found": True, "reason": None, "node_ids": [src], "edge_rows": [], "explored": 1}

    dist = ({src: 0}, {dst: 0})
    parent: tuple[dict, dict] = ({src: None}, {dst: None})
    frontier = ([src], [dst])
    edge_budget = PATH_EDGE_BUDGET
    budget_hit = False

    def meet_nodes() -> list[str]:
        small, big = (dist[0], dist[1]) if len(dist[0]) <= len(dist[1]) else (dist[1], dist[0])
        return [n for n in small if n in big]

    while True:
        if not frontier[0] or not frontier[1]:
            return {"found": False, "reason": "disconnected", "explored": len(dist[0]) + len(dist[1])}
        depth_so_far = max(dist[0].values()) + max(dist[1].values())
        if depth_so_far >= max_depth:
            return {"found": False, "reason": "no_path_within_max_depth",
                    "explored": len(dist[0]) + len(dist[1])}
        side = 0 if len(frontier[0]) <= len(frontier[1]) else 1
        rows, complete, raw_read = _edges_touching_chunk_counted(
            store, frontier[side], include_closed=False, budget=edge_budget)
        if not complete:
            # Any capped or skipped chunk leaves this level partly unread (R-017 t07 H2): from
            # here the only negative answer the search may give is "unknown".
            budget_hit = True
        edge_budget -= raw_read                  # rows READ, not rows kept (R-019 t07 H2)
        nxt: list[str] = []
        level = dist[side][frontier[side][0]] + 1
        frontier_set = set(frontier[side])
        for r in rows:
            for here, other in ((r["src_id"], r["dst_id"]), (r["dst_id"], r["src_id"])):
                if here in frontier_set and other not in dist[side]:
                    if len(dist[0]) + len(dist[1]) >= PATH_NODE_BUDGET:
                        # Checked BEFORE each insertion, so the node budget is a bound on memory
                        # and work, not a check after a hub has already been swallowed (M1).
                        budget_hit = True
                        break
                    dist[side][other] = level
                    parent[side][other] = (here, r)
                    nxt.append(other)
            if budget_hit and len(dist[0]) + len(dist[1]) >= PATH_NODE_BUDGET:
                break
        frontier = (nxt, frontier[1]) if side == 0 else (frontier[0], nxt)
        meets = meet_nodes()
        if meets:
            best = min(meets, key=lambda n: (dist[0][n] + dist[1][n], n))
            if dist[0][best] + dist[1][best] > max_depth:
                return {"found": False, "reason": "no_path_within_max_depth",
                        "explored": len(dist[0]) + len(dist[1])}
            left: list[str] = []
            left_edges: list = []
            cur = best
            while parent[0][cur] is not None:
                prev, row = parent[0][cur]
                left.append(cur)
                left_edges.append(row)
                cur = prev
            left.append(src)
            node_ids = list(reversed(left))
            edge_rows = list(reversed(left_edges))
            cur = best
            while parent[1][cur] is not None:
                nxt_node, row = parent[1][cur]
                node_ids.append(nxt_node)
                edge_rows.append(row)
                cur = nxt_node
            return {"found": True, "reason": None, "node_ids": node_ids, "edge_rows": edge_rows,
                    "explored": len(dist[0]) + len(dist[1])}
        if budget_hit or len(dist[0]) + len(dist[1]) >= PATH_NODE_BUDGET:
            return {"found": False, "reason": "search_budget_exhausted",
                    "explored": len(dist[0]) + len(dist[1])}


def _browse_by_source(store, *, source_id: str, kind: Optional[str], q: Optional[str],
                      include_closed: bool, limit: int) -> tuple[list, dict, Optional[int]]:
    """Nodes grounded by ``source_id`` — direct citations AND anything derived over them
    (`ReverseIndex.impact_of`'s ancestry closure), which is exactly "what does this source
    ground", the same primitive `/evidence`'s verify panel already trusts for "what relies on
    this". ``max_detail`` bounds the query to a source's own citation count, never the graph's."""
    index = store.reverse_index
    # A generous cap: bounded by what THIS source grounds, not by the graph. Still finite so one
    # pathologically over-cited source (a whole reference table) cannot make this call unbounded.
    detail_cap = max(limit * 20, 2000)
    report = index.impact_of(source_id=source_id, include_closed=include_closed,
                             max_detail=detail_cap)
    seen: dict[str, Any] = {}
    for e in report.nodes:
        if e.entity_id in seen:
            continue
        if kind and e.kind != kind:
            continue
        if q and (not e.label or q.casefold() not in e.label.casefold()):   # R-019 t07 M1
            continue
        seen[e.entity_id] = e
    node_ids = list(seen.keys())
    # `impact_of` itself may have been windowed by `max_detail` (a source with more bindings than
    # `detail_cap`). When it was, the dedup above saw only part of the closure, so the count of
    # distinct nodes is a floor, not a total — reported as unknown rather than as a wrong number,
    # same rule as the BFS branch above.
    total_n = None if report.truncated else len(node_ids)
    windowed_ids = node_ids[:limit]
    rows_by_id = _fetch_nodes_by_id(store, windowed_ids, include_closed=include_closed)
    rows = [rows_by_id[i] for i in windowed_ids if i in rows_by_id]
    coverage = {"answer": report.answer.value, "reason": report.reason,
               "unindexed_pointers": report.unindexed_pointers,
               "impact_truncated": report.truncated,
               "impact_binding_count": report.binding_count}
    return rows, coverage, total_n


def register_graph_routes(app: FastAPI) -> None:
    """Mount the knowledge-graph surface. Every route is read-only except ``POST /graph/reload``,
    which re-opens the shared store and writes nothing."""
    install_auth_handler(app)

    def _store_or_refusal(app: FastAPI):
        store, label = _resolve_store(app)
        if store is None:
            return None, None, _refusal(
                "GRAPH_UNAVAILABLE",
                "no graph store is attached to this gateway (neither app.state.graph_store nor "
                "app.state.brain.store is set)", status=503)
        return store, label, None

    @app.get("/graph/facets")
    def graph_facets(_principal: AuthenticatedPrincipal = Depends(require_principal)):
        """Everything the filter bar needs, and nothing that requires loading the graph: node/edge
        kind counts (a `GROUP BY`), a bounded top-N of sources with the exact count left out, the
        store's own totals, and the reverse index's coverage verdict."""
        store, label, refused = _store_or_refusal(app)
        if refused is not None:
            return refused
        try:
            counts = store.counts()
            return {
                "graph_source": label,
                "counts": counts,
                "node_kinds": _node_kind_facets(store),
                "edge_kinds": _edge_kind_facets(store),
                "sources": _source_facets(store),
                "reverse_index_coverage": _coverage(store),
                "components": _component_stats(store),
                # every value above is the store counting its own rows, not a claim about the
                # world: kinds are the ontology the rows were written under
                "structure_not_a_claim": ["counts", "node_kinds", "edge_kinds", "sources",
                                          "reverse_index_coverage", "components"],
            }
        except Exception as exc:                                 # noqa: BLE001
            message = safe_detail(log, context="graph_facets failed", exc=exc)
            return _refusal("GRAPH_FACETS_FAILED", message, status=503)

    @app.get("/graph/neighborhood")
    def graph_neighborhood(node: Optional[str] = None, kind: Optional[str] = None,
                           source: Optional[str] = None, q: Optional[str] = None,
                           depth: int = DEFAULT_DEPTH, limit: int = DEFAULT_NODE_LIMIT,
                           include_closed: bool = False,
                           _principal: AuthenticatedPrincipal = Depends(require_principal)):
        """A bounded neighbourhood: a seed's BFS expansion, or a filtered browse when there is no
        seed. Always windowed, and the window is always named in the response
        (`matched_total` / `returned` / `truncated`) — silently truncating would misstate coverage
        exactly the way an empty impact list would (`ptr_index.py`'s "known vs unknown")."""
        store, label, refused = _store_or_refusal(app)
        if refused is not None:
            return refused

        limit = max(1, min(int(limit), MAX_NODE_LIMIT))
        depth = max(1, min(int(depth), MAX_DEPTH))
        q_norm = (q or "").strip() or None
        kind_norm = (kind or "").strip() or None
        source_norm = (source or "").strip() or None

        try:
            coverage: Optional[dict] = None
            note: Optional[str] = None
            mode = "seed" if node else ("source" if source_norm else "filter")
            omitted: Optional[dict] = None
            edges_truncated = False
            if node:
                # The SAME version scope the walk and the node fetch use (R-017 t07 M2): a node
                # that exists only as closed versions is not a seed unless closed rows were asked
                # for — otherwise it passed here and then vanished from its own neighbourhood.
                seed_row = store.fetchone(
                    "SELECT id FROM nodes WHERE id = ?"
                    + ("" if include_closed else " AND system_to IS NULL") + " LIMIT 1", (node,))
                if seed_row is None:
                    return _refusal(
                        "UNKNOWN_NODE",
                        f"no {'' if include_closed else 'current '}node {node!r} in this graph"
                        + ("" if include_closed else
                           " (pass include_closed=true to walk from a closed version)"),
                        status=404)
                visited, edge_rows, truncated, edges_truncated = _bfs(
                    store, node, depth=depth, node_limit=limit, include_closed=include_closed)
                node_rows_by_id = _fetch_nodes_by_id(store, list(visited),
                                                     include_closed=include_closed)
                node_rows = [node_rows_by_id[i] for i in node_rows_by_id]
                returned = len(node_rows)
                # Bounded BFS cannot know the TRUE size of the reachable set without doing the
                # unbounded walk it exists to avoid. When it stopped early, `matched_total` is
                # reported as unknown (None) rather than a manufactured number — the same
                # known-vs-unknown distinction `ReverseIndex.coverage()` makes, applied here
                # because the walk itself, not just the index, can be the thing that ran out.
                matched_total = None if truncated else len(visited)
                if truncated:
                    note = (f"the walk stopped at a resource limit (the {limit}-node window or "
                            f"the per-hop edge budget) before it finished, so there may be more "
                            f"nodes within {depth} hop(s) of {node!r} than are shown here — raise "
                            f"'limit' or expand from a node in this view to see more")
            elif source_norm:
                node_rows, cov, matched_total = _browse_by_source(
                    store, source_id=source_norm, kind=kind_norm, q=q_norm,
                    include_closed=include_closed, limit=limit)
                coverage = cov
                returned = len(node_rows)
                truncated = matched_total is None or matched_total > returned
                if matched_total is None:
                    note = (f"the source {source_norm!r} grounds more bindings than this view "
                            f"inspects at once; the node count below is a floor, not a total "
                            f"(see coverage.impact_binding_count)")
                visited = {r["id"] for r in node_rows}
                edge_rows, complete = _closing_edges(store, visited,
                                                     include_closed=include_closed)
                edges_truncated = not complete
            elif kind_norm or q_norm:
                node_rows, matched_total = _browse_by_kind(
                    store, kind=kind_norm, q=q_norm, include_closed=include_closed, limit=limit)
                returned = len(node_rows)
                truncated = matched_total > returned
                visited = {r["id"] for r in node_rows}
                edge_rows, complete = _closing_edges(store, visited,
                                                     include_closed=include_closed)
                edges_truncated = not complete
            else:
                # The default view: hubs and their neighbourhoods, never an isolated dot.
                mode = "hubs"
                visited, edge_rows, hubs_used, edges_truncated = _browse_hubs(
                    store, include_closed=include_closed, limit=limit)
                visited = _prune_isolated(visited, edge_rows)
                rows_by_id = _fetch_nodes_by_id(store, list(visited),
                                                include_closed=include_closed)
                node_rows = list(rows_by_id.values())
                edge_rows = [r for r in edge_rows
                             if r["src_id"] in rows_by_id and r["dst_id"] in rows_by_id]
                returned = len(node_rows)
                total_row = store.fetchone(
                    "SELECT COUNT(DISTINCT id) c FROM nodes"
                    + ("" if include_closed else " WHERE system_to IS NULL"))
                matched_total = int(total_row["c"]) if total_row is not None else 0
                truncated = matched_total > returned
                isolated = _isolated_node_count(store, include_closed=include_closed)
                omitted = {
                    "nodes_not_drawn": max(0, matched_total - returned),
                    # Nodes that touch no edge at all. The default view never draws them — a dot
                    # with no line says nothing about the graph — so they are counted here instead.
                    "isolated_nodes": isolated,
                    "hubs_seeded": hubs_used,
                }
                if matched_total and not node_rows:
                    note = (f"this graph has {matched_total} node(s) and no edges between them, so "
                            f"there is no connected structure to draw; filter by kind or search "
                            f"to list them")

            folding = None
            if q_norm and mode in ("filter",):
                folding = _folding(_fold_sql(store))
                if folding != "unicode":
                    note = f"{note} Also: {_ASCII_FOLD_NOTE}" if note else _ASCII_FOLD_NOTE
            elif q_norm:
                folding = "unicode"                    # the source branch folds in Python
            if edges_truncated:
                edge_note = ("some edges BETWEEN the nodes shown were not read (the edge budget "
                             "for this view was reached); every node shown is real, but the "
                             "picture may be missing lines between them")
                note = f"{note} Also: {edge_note}" if note else edge_note
            edges_out, edges_withheld = _grounded_edge_dicts(store, edge_rows)
            if edges_withheld:
                w_note = (f"{edges_withheld} edge(s) between the nodes shown have no source "
                          f"pointer and are withheld -- a line with nothing behind it is not drawn")
                note = f"{note} Also: {w_note}" if note else w_note
            return {
                "graph_source": label,
                "seed": node,
                "depth": depth if node else None,
                "filters": {"kind": kind_norm, "source": source_norm, "q": q_norm},
                "nodes": [_node_row_dict(r, store) for r in node_rows],
                "edges": edges_out,
                "edges_withheld_ungrounded": edges_withheld,
                "case_folding": folding,
                "returned": returned,
                # `None` means "unknown — the walk stopped before it could count the rest", never
                # "nothing". Only a completed, unbounded-within-its-scope query (the exact COUNT(*)
                # in the browse branches, or a BFS that finished before hitting its cap) reports a
                # number here.
                "matched_total": matched_total,
                "left_out": (max(0, matched_total - returned) if matched_total is not None
                            else None),
                # `truncated` is ANY coverage gap; the two below say which (R-017 t07 H3).
                "truncated": bool(truncated or edges_truncated),
                "nodes_truncated": bool(truncated),
                "edges_truncated": bool(edges_truncated),
                "edges_returned": len(edges_out),
                "window": limit,
                "coverage": coverage,
                "note": note,
                "mode": mode,
                "omitted": omitted,
            }
        except Exception as exc:                                 # noqa: BLE001
            message = safe_detail(log, context="graph_neighborhood failed", exc=exc)
            return _refusal("GRAPH_QUERY_FAILED", message, status=503)

    @app.get("/graph/node/{node_id:path}")
    def graph_node(node_id: str, include_closed: bool = False,
                   _principal: AuthenticatedPrincipal = Depends(require_principal)):
        """One node's provenance: label, kind, attributes, the pointer grounding each one, and
        which attributes were asked for and never found (`unresolved`). Incident edges and
        bi-temporal versions are each a SQL ``COUNT`` plus a ``LIMIT``ed read (`EDGE_TOUCH_LIMIT`,
        `VERSION_LIMIT`) — never every incident edge materialised to be sliced afterwards
        (R-017 t07 H4) — so this route costs the same whether the node has 3 edges or 30,000.

        Deliberately UNGATED by `/evidence`'s R-28 confirmation wall: that wall exists because the
        evidence page surfaces ledger commit status and unsettled identity CANDIDATES for
        PATIENTS, and serving those before a human confirms would leak a draft decision
        (CLAUDE.md §Hard constraints 1 and 4). A graph node's own attributes and pointers are
        already durable — they were written to the store, under the grounding wall, before this
        request existed — and `/brain/node/{id}` and `/brain/node/{id}/sheet` already serve the
        same information with no such gate. Re-deriving R-28 here would also make every node in a
        pack graph that never touches the intake/ledger pipeline at all (government, finance)
        permanently unreadable, which is not what that wall is for.

        Deliberately does NOT call `row_to_node`/`row_to_edge` (`databridge_graph.store`): both
        construct a `GraphNode`/`GraphEdge`, whose `kind` field is a `NodeKind`/`EdgeKind` —
        validated against THIS PROCESS's bound deployment (`packs/deployment.yaml`). A row
        written under a pack the current binding does not include would raise `UnknownKind` the
        moment it was read, and the shared workspace store can legitimately hold such rows (the
        binding can change after they were written). `attrs_json` is
        read directly and pointers come from `sources_for`/`attr_sources_for` — store reads that
        return raw `SourcePointer`s and never touch the kind vocabulary at all.
        """
        import json

        store, label, refused = _store_or_refusal(app)
        if refused is not None:
            return refused
        try:
            versions_total = int(store.fetchone(
                "SELECT COUNT(*) c FROM nodes WHERE id = ?", (node_id,))["c"])
            if not versions_total:
                return _refusal("UNKNOWN_NODE", f"no node {node_id!r} in this graph", status=404)
            # The NEWEST versions, shown oldest-first as before.
            versions = list(reversed(store.fetchall(
                "SELECT * FROM nodes WHERE id = ? ORDER BY system_from DESC, rowid_pk DESC "
                "LIMIT ?", (node_id, VERSION_LIMIT))))
            current = store.fetchone(
                "SELECT * FROM nodes WHERE id = ? AND system_to IS NULL "
                "ORDER BY rowid_pk DESC LIMIT 1", (node_id,)) or versions[-1]
            attrs, _emitted_ptrs, withheld = _withhold_ungrounded(
                store, node_id, current["rowid_pk"], json.loads(current["attrs_json"] or "{}"),
                current["canonical_id"])
            attr_sources, attr_total = store.attr_sources_window(
                node_id, version_pk=current["rowid_pk"], limit=ATTR_SOURCE_LIMIT)
            entity_sources, sources_total = store.sources_window(
                node_id, version_pk=current["rowid_pk"], limit=SOURCE_LIMIT)
            unresolved = sorted(k for k, v in attrs.items() if v is None)
            label_withheld = "label" in _row_pointers(store, current, ("label",))[1]
            if label_withheld:
                withheld = sorted({*withheld, "label"})

            scope = "" if include_closed else " AND system_to IS NULL"
            touching_total = int(store.fetchone(
                f"SELECT COUNT(*) c FROM edges WHERE (src_id = ? OR dst_id = ?){scope}",
                (node_id, node_id))["c"])
            windowed = store.fetchall(
                f"SELECT * FROM edges WHERE (src_id = ? OR dst_id = ?){scope} "
                f"ORDER BY system_from, rowid_pk LIMIT ?", (node_id, node_id, EDGE_TOUCH_LIMIT))
            truncated_edges = touching_total > len(windowed)
            outgoing, incoming = [], []
            incident_withheld = 0
            for r in windowed:
                other_id = r["dst_id"] if r["src_id"] == node_id else r["src_id"]
                edge_attrs, edge_attr_ptrs, edge_withheld = _withhold_ungrounded(
                    store, r["id"], r["rowid_pk"], json.loads(r["attrs_json"] or "{}"))
                pointers, missing, total = _row_pointers(store, r, ("edge",))
                if "edge" in missing:           # its kind/other_id would be a bare claim
                    incident_withheld += 1
                    continue
                entry = {"id": r["id"], "kind": r["kind"], "other_id": other_id,
                         "attrs": edge_attrs,
                         "attribute_sources": {k: _ptr_json(v)
                                               for k, v in edge_attr_ptrs.items()},
                         "unresolved": sorted(k for k, v in edge_attrs.items() if v is None),
                         "pointers": pointers, "withheld_ungrounded": missing + edge_withheld,
                         # `other_id` / `kind` are the claim the edge pointer grounds
                         "structure_not_a_claim": ["id", "valid_from", "valid_to", "is_open"],
                         "sources_total": total,
                         "valid_from": r["valid_from"], "valid_to": r["valid_to"],
                         "is_open": r["system_to"] is None}
                (outgoing if r["src_id"] == node_id else incoming).append(entry)

            return {
                "graph_source": label,
                "id": current["id"], "kind": current["kind"],
                "label": None if label_withheld else current["label"],
                "canonical_id": (None if "canonical_id" in withheld
                                 else current["canonical_id"]),
                "valid_from": current["valid_from"], "valid_to": current["valid_to"],
                "system_from": current["system_from"], "system_to": current["system_to"],
                "is_open": current["system_to"] is None,
                "attrs": attrs,
                "withheld_ungrounded": withheld,
                # `label` is grounded by `sources` (the entity pointers), each attr and a set
                # `canonical_id` by `attribute_sources`; these are the store's own values
                "structure_not_a_claim": list(NODE_STRUCTURE_FIELDS),
                "attribute_sources": {k: v.model_dump(mode="json", exclude_none=True)
                                      for k, v in attr_sources.items()},
                "attribute_sources_total": attr_total,
                "attribute_sources_truncated": attr_total > len(attr_sources),
                "sources": [p.model_dump(mode="json", exclude_none=True) for p in entity_sources],
                "sources_total": sources_total,
                "sources_truncated": sources_total > len(entity_sources),
                "sources_window": SOURCE_LIMIT,
                "unresolved": unresolved,
                "versions": [_version_entry(store, v) for v in versions],
                "versions_total": versions_total,
                "versions_truncated": versions_total > len(versions),
                "versions_window": VERSION_LIMIT,
                "edges": {"outgoing": outgoing, "incoming": incoming,
                         "returned": len(outgoing) + len(incoming),
                         "withheld_ungrounded": incident_withheld,
                         "matched_total": touching_total,
                         "truncated": truncated_edges, "window": EDGE_TOUCH_LIMIT},
                "evidence_trace_url": f"/evidence?entity={node_id}",
            }
        except Exception as exc:                                 # noqa: BLE001
            message = safe_detail(log, context="graph_node failed", exc=exc)
            return _refusal("GRAPH_NODE_FAILED", message, status=503)

    @app.get("/graph/edge/{edge_id:path}")
    def graph_edge(edge_id: str,
                   _principal: AuthenticatedPrincipal = Depends(require_principal)):
        """An edge's own provenance — the same shape as a node, minus the incident-edge list.

        Same reason as `graph_node` for reading the row directly rather than through
        `row_to_edge`: constructing a `GraphEdge` validates `kind` against this process's bound
        vocabulary, which a row written under a different pack binding will not satisfy.
        """
        import json

        store, label, refused = _store_or_refusal(app)
        if refused is not None:
            return refused
        try:
            versions_total = int(store.fetchone(
                "SELECT COUNT(*) c FROM edges WHERE id = ?", (edge_id,))["c"])
            if not versions_total:
                return _refusal("UNKNOWN_EDGE", f"no edge {edge_id!r} in this graph", status=404)
            rows = list(reversed(store.fetchall(
                "SELECT * FROM edges WHERE id = ? ORDER BY system_from DESC, rowid_pk DESC "
                "LIMIT ?", (edge_id, VERSION_LIMIT))))
            current = store.fetchone(
                "SELECT * FROM edges WHERE id = ? AND system_to IS NULL "
                "ORDER BY rowid_pk DESC LIMIT 1", (edge_id,)) or rows[-1]
            attrs, _emitted_ptrs, withheld = _withhold_ungrounded(
                store, edge_id, current["rowid_pk"], json.loads(current["attrs_json"] or "{}"))
            attr_sources, attr_total = store.attr_sources_window(
                edge_id, version_pk=current["rowid_pk"], limit=ATTR_SOURCE_LIMIT)
            entity_sources, sources_total = store.sources_window(
                edge_id, version_pk=current["rowid_pk"], limit=SOURCE_LIMIT)
            unresolved = sorted(k for k, v in attrs.items() if v is None)
            # src/kind/dst are the claim the entity pointers ground: none, and they are withheld
            claim = {f: current[f] for f in EDGE_POINTER_GROUNDS}
            if not sources_total:
                withheld = sorted({*withheld, *EDGE_POINTER_GROUNDS})
                claim = {f: None for f in EDGE_POINTER_GROUNDS}
            return {
                "graph_source": label,
                "id": current["id"], "kind": claim["kind"],
                "src_id": claim["src_id"], "dst_id": claim["dst_id"],
                "weight": current["weight"],
                "valid_from": current["valid_from"], "valid_to": current["valid_to"],
                "system_from": current["system_from"], "system_to": current["system_to"],
                "is_open": current["system_to"] is None,
                "attrs": attrs,
                "withheld_ungrounded": withheld,
                # `src_id` / `kind` / `dst_id` are the claim `sources` ground
                "structure_not_a_claim": list(EDGE_STRUCTURE_FIELDS),
                "attribute_sources": {k: v.model_dump(mode="json", exclude_none=True)
                                      for k, v in attr_sources.items()},
                "attribute_sources_total": attr_total,
                "attribute_sources_truncated": attr_total > len(attr_sources),
                "sources": [p.model_dump(mode="json", exclude_none=True) for p in entity_sources],
                "sources_total": sources_total,
                "sources_truncated": sources_total > len(entity_sources),
                "sources_window": SOURCE_LIMIT,
                "unresolved": unresolved,
                "has_attestation": current["attestation_json"] is not None,
                "versions": [{
                    "valid_from": r["valid_from"], "valid_to": r["valid_to"],
                    "system_from": r["system_from"], "system_to": r["system_to"],
                    "is_open": r["system_to"] is None,
                } for r in rows],
                "versions_total": versions_total,
                "versions_truncated": versions_total > len(rows),
                "versions_window": VERSION_LIMIT,
                "evidence_trace_url": f"/evidence?entity={edge_id}",
            }
        except Exception as exc:                                 # noqa: BLE001
            message = safe_detail(log, context="graph_edge failed", exc=exc)
            return _refusal("GRAPH_EDGE_FAILED", message, status=503)

    @app.get("/graph/path")
    def graph_path(from_: str = Query("", alias="from"), to: str = "",
                   max_depth: int = DEFAULT_PATH_DEPTH,
                   _principal: AuthenticatedPrincipal = Depends(require_principal)):
        """The shortest undirected path between two nodes over OPEN edges, within ``max_depth``
        hops (capped at ``MAX_PATH_DEPTH``). A bounded bidirectional BFS: work is proportional to
        the two neighbourhoods it grows, never to the graph. When it finds nothing it says WHICH
        nothing — out of hops, genuinely disconnected, or out of budget (unknown)."""
        store, label, refused = _store_or_refusal(app)
        if refused is not None:
            return refused
        src = (from_ or "").strip()
        dst = (to or "").strip()
        if not src or not dst:
            return _refusal("PATH_NEEDS_TWO_NODES", "both 'from' and 'to' are required")
        depth = max(1, min(int(max_depth), MAX_PATH_DEPTH))
        try:
            for nid in (src, dst):
                if store.fetchone("SELECT 1 FROM nodes WHERE id = ? AND system_to IS NULL LIMIT 1",
                                  (nid,)) is None:
                    return _refusal("UNKNOWN_NODE", f"no current node {nid!r} in this graph",
                                    status=404)
            result = _shortest_path(store, src, dst, max_depth=depth)
            base = {"graph_source": label, "from": src, "to": dst, "max_depth": depth,
                    "found": result["found"], "reason": result["reason"],
                    "explored": result["explored"]}
            if not result["found"]:
                messages = {
                    "no_path_within_max_depth":
                        f"no path within {depth} hop(s); a longer one may exist — raise max_depth",
                    "disconnected":
                        "no path at any depth: the two nodes are in different connected components",
                    "search_budget_exhausted":
                        (f"the search stopped at its budget ({PATH_NODE_BUDGET} nodes / "
                         f"{PATH_EDGE_BUDGET} edges) before finding a path; whether one exists "
                         f"within {depth} hop(s) is unknown"),
                }
                return {**base, "hops": None, "nodes": [], "edges": [],
                        "message": messages[result["reason"]]}
            rows_by_id = _fetch_nodes_by_id(store, result["node_ids"], include_closed=False)
            edges_out, edges_withheld = _grounded_edge_dicts(store, result["edge_rows"])
            return {**base, "hops": len(result["edge_rows"]),
                    "nodes": [_node_row_dict(rows_by_id[i], store) for i in result["node_ids"]
                              if i in rows_by_id],
                    "edges": edges_out,
                    "edges_withheld_ungrounded": edges_withheld,
                    "message": (None if not edges_withheld else
                                f"{edges_withheld} hop(s) of this path rest on an edge with no "
                                f"source pointer; those edges are withheld, so the path is "
                                f"shown incomplete rather than vouched for")}
        except Exception as exc:                                 # noqa: BLE001
            message = safe_detail(log, context="graph_path failed", exc=exc)
            return _refusal("GRAPH_PATH_FAILED", message, status=503)

    @app.get("/graph/search")
    def graph_search(q: str = "", kind: Optional[str] = None, limit: int = SEARCH_DEFAULT_LIMIT,
                     _principal: AuthenticatedPrincipal = Depends(require_principal)):
        """Find seed nodes by label, id or canonical id (case-insensitive substring). Exact id /
        label matches rank first, then label prefixes, then the best-connected nodes. Windowed,
        and the window is named (`matched_total` / `truncated`)."""
        store, label, refused = _store_or_refusal(app)
        if refused is not None:
            return refused
        needle = (q or "").strip().casefold()
        if not needle:
            return _refusal("SEARCH_NEEDS_TEXT", "'q' must not be empty")
        limit = max(1, min(int(limit), SEARCH_MAX_LIMIT))
        kind_norm = (kind or "").strip() or None
        try:
            fold = _fold_sql(store)
            pat = f"%{_like_escape(needle)}%"
            where = (f"system_to IS NULL AND ({fold}(label) LIKE ? ESCAPE '\\' "
                     f"OR {fold}(id) LIKE ? ESCAPE '\\' "
                     f"OR {fold}(COALESCE(canonical_id, '')) LIKE ? ESCAPE '\\')")
            params: list = [pat, pat, pat]
            if kind_norm:
                where += " AND kind = ?"
                params.append(kind_norm)
            total = store.fetchone(f"SELECT COUNT(*) c FROM nodes WHERE {where}", tuple(params))
            total_n = int(total["c"]) if total is not None else 0
            rows = store.fetchall(
                f"SELECT nodes.*, "
                f"  (SELECT COUNT(*) FROM edges e WHERE e.src_id = nodes.id "
                f"     AND e.system_to IS NULL) + "
                f"  (SELECT COUNT(*) FROM edges e WHERE e.dst_id = nodes.id "
                f"     AND e.system_to IS NULL) AS _deg "
                f"FROM nodes WHERE {where} "
                f"ORDER BY ({fold}(id) = ?) DESC, ({fold}(label) = ?) DESC, "
                f"  ({fold}(label) LIKE ? ESCAPE '\\') DESC, _deg DESC, rowid_pk LIMIT ?",
                (*params, needle, needle, f"{_like_escape(needle)}%", limit))
            nodes = []
            for r in rows:
                d = _node_row_dict(r, store)
                d["degree"] = int(r["_deg"])
                nodes.append(d)
            folding = _folding(fold)
            return {"graph_source": label, "q": q, "kind": kind_norm, "nodes": nodes,
                    "returned": len(nodes), "matched_total": total_n,
                    "truncated": total_n > len(nodes), "window": limit,
                    "case_folding": folding,
                    "note": None if folding == "unicode" else _ASCII_FOLD_NOTE}
        except Exception as exc:                                 # noqa: BLE001
            message = safe_detail(log, context="graph_search failed", exc=exc)
            return _refusal("GRAPH_SEARCH_FAILED", message, status=503)

    @app.post("/graph/reload")
    def graph_reload(_principal: AuthenticatedPrincipal = Depends(require_principal)):
        """Ask the gateway to re-open its shared store (``app.state.reload_graph``). Writes no
        row; it only makes the gateway see rows another process (an ingestion run) wrote. A
        gateway that exposes no reload hook says so rather than pretending it reloaded."""
        reload_fn = getattr(app.state, "reload_graph", None)
        if not callable(reload_fn):
            return _refusal("RELOAD_UNSUPPORTED",
                            "this gateway exposes no graph reload hook (app.state.reload_graph)",
                            status=501)
        try:
            reload_fn()
            _clear_stats_cache()
        except Exception as exc:                                 # noqa: BLE001
            message = safe_detail(log, context="graph_reload failed", exc=exc)
            return _refusal("GRAPH_RELOAD_FAILED", message, status=503)
        store, label, refused = _store_or_refusal(app)
        if refused is not None:
            return refused
        return {"reloaded": True, "graph_source": label, "counts": store.counts()}

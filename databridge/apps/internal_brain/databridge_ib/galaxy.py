"""``GET /ib/galaxy``: the graph page's two views (the overview and the knowledge map) from ONE live pass.

Why (docs/finals/live_growth/GRAPH_PERF_DIAGNOSIS.md, Fix 1): the graph page needs both
``GET /ib/graph`` (overview) and ``GET /ib/map``. Each of them checks, live at the source, every
document the person's stored permissions allow, so one page load checked every document twice. This
route checks each one ONCE and builds both existing response bodies from the same immutable
:class:`authz.AuthorizedDoc` snapshots.

The rules are those of the two views, unchanged:

* **Gate 1 then Gate 2, in this request.** The candidates are the person's Gate-1 documents
  (``graph.overview_work``: every readable indexed document, newest first). Every one of them goes
  through :func:`authz.authorize` (live source check plus the version-bound read-through) under ONE
  monotonic deadline (``GALAXY_CHECK_TIMEOUT_S``), on the galaxy's own pool (``GALAXY_POOL``) so the
  checks never queue ahead of an answer's Gate 2. An error, timeout or 429 is a deny (``authorize``
  unchanged).
* **Budget and workers.** Before, the page ran the overview (20 s, ``authz.VIEW_POOL``, 6 workers)
  and the map (45 s, the map pool, 8 workers) side by side. The galaxy keeps the larger of the two
  budgets (45 s, ``knowledge_map.MAP_CHECK_TIMEOUT_S``) and the same total concurrency (6 + 8 = 14
  workers) in a pool of its own, so it never starves, and is never starved by, ``GET /ib/map`` or
  ``GET /ib/graph`` running at the same time. The page's worst case stays the map's 45 s; the
  overview can now wait up to 45 s instead of 20 s, but only in a pass the map would have waited
  for anyway, and it then decides more of its documents rather than fewer.
* **Only confirmed documents, in both bodies.** The overview is ``graph.build_graph`` in overview
  mode fed with this pass (:class:`graph.OverviewChecks`): its ``complete`` flag is true only when
  every document of its work list was decided, and a count is exact only when every document it
  depends on was decided; otherwise it is ``null``, as today. The map is
  ``knowledge_map.shape_map`` over the same snapshots: anything denied or undecided is left out and
  counted nowhere. Restricted is indistinguishable from non-existent in both (P2). The map body
  carries one extra field, ``complete`` (a bool): false when any check of this pass was undecided.
  It is a flag only: no count of undecided documents is given anywhere.
* **Audit.** ``graph.viewed.docs_checked`` has the meaning it has on ``GET /ib/graph``: the number
  of the overview's documents handed to ``authz.authorize`` (its ``n``, timeouts included), capped
  at the overview's check cap.
* **Write-ahead.** ``graph.viewed`` (mode ``overview``) and ``map.viewed`` are both appended before
  the response, with the same event types and fields as the two single views (so audit stats and
  verify are unchanged). Any ledger failure propagates and the route answers 503.
* The response is ``{"overview": <GET /ib/graph?limit=N body>, "map": <GET /ib/map body>}``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping, Optional

from . import authz, graph
from .directory import Directory
from .index import Index
from concurrent.futures import ThreadPoolExecutor

from .knowledge_map import MAP_CHECK_TIMEOUT_S, shape_map
from .models import DirectoryUser

#: one budget for both bodies: no smaller than the map's own (45 s); the overview alone had 20 s
GALAXY_CHECK_TIMEOUT_S = MAP_CHECK_TIMEOUT_S
#: the galaxy's own workers: the overview's 6 plus the map's 8, which the page used side by side
GALAXY_POOL = ThreadPoolExecutor(max_workers=14, thread_name_prefix="ib-galaxy-gate2")


def build_galaxy(user: DirectoryUser, *, index: Index, directory: Directory, ledger: Any,
                 connectors: Mapping[str, Any], clock: Callable[[], datetime],
                 limit: int = graph.DEFAULT_LIMIT,
                 timeout_s: Optional[float] = None) -> dict:
    """``{"overview": ..., "map": ...}`` from one live pass. Any ledger failure propagates (the
    route answers 503); both view events are appended before this returns."""
    epoch = getattr(directory, "epoch", 0)
    readable = index.readable(user, epoch)                          # GATE 1
    g1 = graph.gate1_view(index, readable)
    work = graph.overview_work(g1)
    undecided: set[str] = set()
    ok, n_handed = authz.authorize(user, work, index=index, connectors=connectors, readable=readable,
                            deadline=authz.deadline_in(
                                GALAXY_CHECK_TIMEOUT_S if timeout_s is None else timeout_s),
                            undecided=undecided,
                            pool=GALAXY_POOL)                       # GATE 2 + read-through, once
    now = clock()
    overview = graph.build_graph(
        user, index=index, directory=directory, ledger=ledger, connectors=connectors, clock=clock,
        limit=limit, now=now,
        pre=graph.OverviewChecks(g1=g1, ok=ok, undecided=frozenset(undecided),
                                 attempted=frozenset(d for d in work if d in readable),
                                 n_handed=n_handed))        # appends graph.viewed
    body, viewed = shape_map(user, work, ok, now)
    body["complete"] = not undecided                                # a flag, never a count
    ledger.append(viewed)                                           # map.viewed, before returning
    return {"overview": overview, "map": body}


__all__ = ["build_galaxy", "GALAXY_CHECK_TIMEOUT_S", "GALAXY_POOL"]

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
  monotonic deadline (``GALAXY_CHECK_TIMEOUT_S``), on the map's own small pool so the checks never
  queue ahead of an answer's Gate 2. An error, timeout or 429 is a deny (``authorize`` unchanged).
* **Only confirmed documents, in both bodies.** The overview is ``graph.build_graph`` in overview
  mode fed with this pass (:class:`graph.OverviewChecks`): its ``complete`` flag is true only when
  every document of its work list was decided, and a count is exact only when every document it
  depends on was decided; otherwise it is ``null``, as today. The map is
  ``knowledge_map.shape_map`` over the same snapshots: anything denied or undecided is left out and
  counted nowhere. Restricted is indistinguishable from non-existent in both (P2).
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
from .knowledge_map import _MAP_POOL, shape_map
from .models import DirectoryUser

#: one budget for both bodies: the overview alone had 20 s, the map alone 45 s; one pass of the
#: widest demo persona takes about 5 to 9 s in process (GRAPH_PERF_DIAGNOSIS.md §3)
GALAXY_CHECK_TIMEOUT_S = 30.0


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
    ok, _ = authz.authorize(user, work, index=index, connectors=connectors, readable=readable,
                            deadline=authz.deadline_in(
                                GALAXY_CHECK_TIMEOUT_S if timeout_s is None else timeout_s),
                            undecided=undecided,
                            pool=_MAP_POOL)                         # GATE 2 + read-through, once
    now = clock()
    overview = graph.build_graph(
        user, index=index, directory=directory, ledger=ledger, connectors=connectors, clock=clock,
        limit=limit, now=now,
        pre=graph.OverviewChecks(g1=g1, ok=ok, undecided=frozenset(undecided),
                                 attempted=frozenset(work)))        # appends graph.viewed
    body, viewed = shape_map(user, work, ok, now)
    ledger.append(viewed)                                           # map.viewed, before returning
    return {"overview": overview, "map": body}


__all__ = ["build_galaxy", "GALAXY_CHECK_TIMEOUT_S"]

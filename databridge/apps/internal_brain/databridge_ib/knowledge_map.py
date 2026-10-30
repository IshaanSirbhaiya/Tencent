"""The knowledge map: what ONE person can open, grouped platform -> space/channel/project -> item.

Same rules as an answer:
- Gate 1 picks the candidates (the stored source permissions), then Gate 2 asks each source, live,
  whether this person may open each item NOW; only confirmed items are listed or counted. An error,
  timeout or rate limit leaves the item out (fail closed), and a revocation whose webhook was lost
  is honoured immediately (Codex review track 5 #1).
- Version-bound (Codex final review, plan item 4): the check goes through
  :func:`authz.authorize`, the same rule as the graph and document views. When the source's version
  moved on, the document is re-read, its fresh ACL must still admit the person and the fetched
  version must be the one checked; what is listed (title, kind, version, dates, link) comes from
  that immutable snapshot, never from the index row's older metadata. Nothing the person cannot open is listed,
  counted or hinted at, so the map of a world WITH restricted documents equals the map of the same
  world WITHOUT them (P2).
- The view is written to the access ledger BEFORE anything is returned (``map.viewed``).
- Titles only: no document text leaves through this route.
"""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from . import authz
from .config import POLICY_VERSION
from .directory import Directory
from .index import Index
from .models import SCOPE_NOTE, DirectoryUser, MapViewed

PLATFORM_ORDER = ("confluence", "jira", "slack", "drive")
MAX_ITEMS_PER_CONTAINER = 200
MAP_CHECK_TIMEOUT_S = 45.0          # one budget for the whole batch; late = left out
#: the map's own workers: its hundreds of live checks never delay an answer's Gate 2 batch
_MAP_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="ib-map-gate2")


def build_map(user: DirectoryUser, *, index: Index, directory: Directory, ledger: Any,
              connectors: Mapping[str, Any], clock: Callable[[], datetime],
              timeout_s: float = MAP_CHECK_TIMEOUT_S) -> dict:
    epoch = getattr(directory, "epoch", 0)
    readable = index.readable(user, epoch)
    candidates = sorted(readable)
    ok, _ = authz.authorize(user, candidates, index=index, connectors=connectors,
                            readable=readable, timeout_s=timeout_s, pool=_MAP_POOL)
    now = clock()
    body, viewed = shape_map(user, candidates, ok, now)
    ledger.append(viewed)
    return body


def shape_map(user: DirectoryUser, candidates: Iterable[str],
              ok: Mapping[str, authz.AuthorizedDoc], now: datetime) -> tuple[dict, MapViewed]:
    """The map body and its ``map.viewed`` event, built ONLY from the authorised snapshots in ``ok``
    (``GET /ib/map`` and ``GET /ib/galaxy`` share this). A candidate without a snapshot (denied,
    undecided or never checked) is left out and counts nowhere. Pure: no I/O, no ledger write; the
    caller appends the event before it returns the body."""
    groups: dict[tuple[str, str], dict] = {}
    items: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for doc_id in sorted(set(candidates)):
        s = ok.get(doc_id)
        if s is None:
            continue
        key = (s.platform, s.container)
        groups.setdefault(key, {"container": s.container, "name": s.container_name or s.container})
        items[key].append({"doc_id": doc_id, "title": s.title, "kind": s.kind,
                           "version": s.version, "updated_at": s.updated_at,
                           "deep_link": s.deep_link})
    platforms = []
    for platform in sorted({p for p, _ in groups}, key=lambda p: (
            PLATFORM_ORDER.index(p) if p in PLATFORM_ORDER else 99, p)):
        containers = []
        for (p, c), meta in sorted(groups.items(), key=lambda kv: kv[1]["name"].lower()):
            if p != platform:
                continue
            docs = sorted(items[(p, c)], key=lambda d: d["updated_at"], reverse=True)
            containers.append({**meta, "count": len(docs), "items": docs[:MAX_ITEMS_PER_CONTAINER]})
        platforms.append({"platform": platform, "count": sum(x["count"] for x in containers),
                          "containers": containers})
    n_docs = sum(p["count"] for p in platforms)
    n_containers = sum(len(p["containers"]) for p in platforms)
    n_returned = sum(len(c["items"]) for p in platforms for c in p["containers"])
    viewed = MapViewed(sub=user.id, docs_listed=n_docs, items_returned=n_returned,
                       containers_listed=n_containers, policy_version=POLICY_VERSION,
                       event_time=now)
    return ({"as_of": now.isoformat(), "scope_note": SCOPE_NOTE, "sub": user.id,
             "totals": {"items": n_docs, "containers": n_containers, "platforms": len(platforms)},
             "platforms": platforms,
             "freshness_note": "Each item was checked with its source just now."}, viewed)

"""The knowledge map: what ONE person can open, grouped platform -> space/channel/project -> item.

Same rules as an answer:
- Gate 1 picks the candidates (the stored source permissions), then Gate 2 asks each source, live,
  whether this person may open each item NOW; only confirmed items are listed or counted. An error,
  timeout or rate limit leaves the item out (fail closed), and a revocation whose webhook was lost
  is honoured immediately (Codex review track 5 #1). Nothing the person cannot open is listed,
  counted or hinted at, so the map of a world WITH restricted documents equals the map of the same
  world WITHOUT them (P2).
- The view is written to the access ledger BEFORE anything is returned (``map.viewed``).
- Titles only: no document text leaves through this route.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Mapping

from . import gates
from .config import POLICY_VERSION
from .directory import Directory
from .index import Index
from .models import SCOPE_NOTE, DirectoryUser, MapViewed

PLATFORM_ORDER = ("confluence", "jira", "slack", "drive")
MAX_ITEMS_PER_CONTAINER = 200
MAP_CHECK_TIMEOUT_S = 20.0          # one budget for the whole batch; late = left out


def build_map(user: DirectoryUser, *, index: Index, directory: Directory, ledger: Any,
              connectors: Mapping[str, Any], clock: Callable[[], datetime],
              timeout_s: float = MAP_CHECK_TIMEOUT_S) -> dict:
    epoch = getattr(directory, "epoch", 0)
    candidates = sorted(index.readable(user, epoch))
    rows = {d: (index.doc(d) or {}) for d in candidates}
    checks = gates.check_all(user, candidates, {d: r.get("platform", "") for d, r in rows.items()},
                             connectors, timeout_s=timeout_s,
                             known_versions={d: r.get("version") for d, r in rows.items()})
    readable = [d for d in candidates if checks.get(d) is not None and checks[d].allowed]
    groups: dict[tuple[str, str], dict] = {}
    items: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for doc_id in readable:
        row = index.doc(doc_id)
        if row is None:
            continue
        key = (row["platform"], row["container"])
        groups.setdefault(key, {"container": row["container"],
                                "name": row.get("container_name") or row["container"]})
        items[key].append({"doc_id": doc_id, "title": row["title"], "kind": row["kind"],
                           "version": row["version"], "updated_at": row["updated_at"],
                           "deep_link": row.get("deep_link") or ""})
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
    now = clock()
    ledger.append(MapViewed(sub=user.id, docs_listed=n_docs, items_returned=n_returned,
                            containers_listed=n_containers, policy_version=POLICY_VERSION,
                            event_time=now))
    return {"as_of": now.isoformat(), "scope_note": SCOPE_NOTE, "sub": user.id,
            "totals": {"items": n_docs, "containers": n_containers, "platforms": len(platforms)},
            "platforms": platforms,
            "freshness_note": "Each item was checked with its source just now."}

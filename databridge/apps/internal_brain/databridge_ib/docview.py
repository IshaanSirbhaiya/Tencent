"""One document the person can open (``GET /ib/doc/{doc_id}``): metadata, a masked excerpt, the links
they can also open, and lineage.

Order (Opus plan review #3):

1. **Ledger first.** ``doc.viewed {sub, doc_id}`` is appended BEFORE the access decision, so a ledger
   outage is the same 503 for a permitted, a restricted and an unknown id.
2. **Gate 1, Gate 2, read-through** through :func:`authz.authorize` (the pipeline's version-bound
   rule). Not readable, unknown, a failed or timed-out check, drift that cannot be confirmed: the
   SAME 404, built by the route with no echo of the id.
3. **Whitelisted output** from the immutable snapshot: no ACL, no raw link ids, no full text. The
   excerpt is cut from the masked indexed text (``mask_doc`` ran at ingest and on read-through).
   Linked objects are listed only when they resolve inside the readable set AND pass Gate 2 now.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping, Optional

from . import authz
from .config import POLICY_VERSION
from .directory import Directory
from .index import Index
from .models import SCOPE_NOTE, DirectoryUser, DocViewed

EXCERPT_CHARS = 1200
MAX_LINKS = 12


class NotFound(Exception):
    """Not readable, unknown, or the live check failed — one answer for all."""


def view_doc(user: DirectoryUser, doc_id: str, *, index: Index, directory: Directory,
             ledger: Any, connectors: Mapping[str, Any], ingest_status: Mapping[str, dict],
             clock: Callable[[], datetime], q: Optional[str] = None,
             timeout_s: float = authz.VIEW_CHECK_TIMEOUT_S) -> dict:
    now = clock()
    q = (q or "").strip() or None
    # 1 — write-ahead, before any decision (a failure propagates: the route answers 503)
    ledger.append(DocViewed(sub=user.id, doc_id=str(doc_id)[:300], with_query=bool(q),
                            policy_version=POLICY_VERSION, event_time=now))
    # 2 — Gate 1 + Gate 2 + read-through
    readable = index.readable(user, getattr(directory, "epoch", 0))
    ok, _ = authz.authorize(user, [doc_id], index=index, connectors=connectors, readable=readable,
                            timeout_s=timeout_s)
    snap = ok.get(doc_id)
    if snap is None:
        raise NotFound()
    # 3 — the excerpt: the best chunk for ``q`` (scored over this document only), else the start
    excerpt, how = snap.text[:EXCERPT_CHARS], "start"
    if q:
        try:
            hits = index.search(q, {doc_id}, k=1)
        except Exception:                                           # noqa: BLE001
            hits = []
        if hits and hits[0].text and hits[0].text in snap.text:   # same version as the snapshot
            excerpt, how = hits[0].text[:EXCERPT_CHARS], "best_chunk"
    # linked objects: resolve inside the readable set only, then confirm live
    targets: list[tuple[str, str]] = []
    for link in snap.links:
        t = authz.resolve_link(link, readable)
        if t and t != doc_id and t not in [x for x, _ in targets]:
            targets.append((t, "mentions" if link.startswith("jira:") else "links"))
        if len(targets) >= MAX_LINKS:
            break
    linked_ok, _ = authz.authorize(user, [t for t, _ in targets], index=index,
                                   connectors=connectors, readable=readable, timeout_s=timeout_s)
    links = [{**{k: v for k, v in authz.public_meta(linked_ok[t]).items()
                 if k in ("doc_id", "platform", "title", "container_name", "kind")},
              "relation": rel} for t, rel in targets if t in linked_ok]
    author = None
    if snap.author:
        u = directory.get(snap.author)
        author = {"id": snap.author, "name": getattr(u, "name", None) or snap.author}
    st = ingest_status.get(snap.platform) or {}
    return {
        **authz.public_meta(snap),
        "created_at": snap.created_at,
        "author": author,
        "excerpt": excerpt, "excerpt_kind": how,
        "excerpt_truncated": how == "start" and len(snap.text) > EXCERPT_CHARS,
        "links": links,
        "lineage": {"connector": snap.platform, "last_sync_at": st.get("last_sync_at"),
                    "content_checked_at": snap.content_checked_at.isoformat(),
                    "authz_checked_at": snap.authz_checked_at.isoformat(),
                    "live_read": snap.live_read},
        "scope_note": SCOPE_NOTE, "as_of": now.isoformat(),
    }


__all__ = ["view_doc", "NotFound", "EXCERPT_CHARS"]

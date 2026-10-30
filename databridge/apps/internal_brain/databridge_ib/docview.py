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
   Linked objects are listed only when they resolve inside the set of documents that pass Gate 2
   now (taken from the snapshot's CURRENT links).
4. **The link cap is applied after the live check** (Codex final review #3). Link candidates are
   checked in bounded batches until ``MAX_LINKS`` resolve, the candidates run out, the work cap is
   reached or the view's single deadline expires; a live-denied target never takes a slot. When the
   budget runs out before the list is settled, the list is OMITTED (``links: []`` with
   ``links_complete: false``) rather than shown as if it were the whole neighbourhood.
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
MAX_LINK_CHECKS = 240               # work cap on live link-target checks per view
#: the longest id a view will look up (the ledger keeps the first 300 characters); a longer id is the
#: same 404 as an unknown one, decided after the write-ahead record (CodeBuddy finals review CB7 F7)
MAX_DOC_ID = 300


class NotFound(Exception):
    """Not readable, unknown, or the live check failed — one answer for all."""


def view_doc(user: DirectoryUser, doc_id: str, *, index: Index, directory: Directory,
             ledger: Any, connectors: Mapping[str, Any], ingest_status: Mapping[str, dict],
             clock: Callable[[], datetime], q: Optional[str] = None,
             timeout_s: float = authz.VIEW_CHECK_TIMEOUT_S) -> dict:
    now = clock()
    deadline = authz.deadline_in(timeout_s)             # ONE budget for the whole view
    q = (q or "").strip() or None
    # 1 — write-ahead, before any decision (a failure propagates: the route answers 503)
    ledger.append(DocViewed(sub=user.id, doc_id=str(doc_id)[:300], with_query=bool(q),
                            policy_version=POLICY_VERSION, event_time=now))
    if not doc_id or len(doc_id) > MAX_DOC_ID:          # never a real id: no index or source lookup
        raise NotFound()
    # 2 — Gate 1 + Gate 2 + read-through
    readable = index.readable(user, getattr(directory, "epoch", 0))
    ok, _ = authz.authorize(user, [doc_id], index=index, connectors=connectors, readable=readable,
                            deadline=deadline)
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
    links, links_complete = _linked(user, snap, index=index, connectors=connectors,
                                    readable=readable, deadline=deadline)
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
        "links": links, "links_complete": links_complete,
        "lineage": {"connector": snap.platform, "last_sync_at": st.get("last_sync_at"),
                    "content_checked_at": snap.content_checked_at.isoformat(),
                    "authz_checked_at": snap.authz_checked_at.isoformat(),
                    "live_read": snap.live_read},
        "scope_note": SCOPE_NOTE, "as_of": now.isoformat(),
    }


def _linked(user: DirectoryUser, snap: authz.AuthorizedDoc, *, index: Index,
            connectors: Mapping[str, Any], readable, deadline: float) -> tuple[list[dict], bool]:
    """``(links, complete)``. Each CURRENT link of the snapshot resolves to its first candidate
    that passed Gate 2 now; candidates are decided in link order, in bounded batches, until
    ``MAX_LINKS`` distinct targets are settled. Restricted-at-source targets are decided and skipped,
    so they never use up a slot (the list equals the list of a world without them)."""
    cands = [(link, [c for c in authz.link_candidates(link, readable) if c != snap.doc_id])
             for link in snap.links]
    queue = list(dict.fromkeys(c for _, cs in cands for c in cs))
    ok: dict[str, authz.AuthorizedDoc] = {}
    decided: set[str] = set()
    undecided: set[str] = set()

    def settle() -> tuple[list[tuple[str, str]], bool]:
        """Targets of the leading links whose candidates are all decided; ``True`` when done."""
        out: list[tuple[str, str]] = []
        for link, cs in cands:
            if any(c not in decided for c in cs):
                return out, False                       # this link is not settled yet
            t = next((c for c in cs if c in ok), None)
            if t and t not in [x for x, _ in out]:
                out.append((t, "mentions" if link.startswith("jira:") else "links"))
                if len(out) >= MAX_LINKS:
                    return out, True
        return out, True

    i, n_checked = 0, 0
    targets, done = settle()
    while (not done and i < len(queue) and n_checked < MAX_LINK_CHECKS
           and authz.remaining(deadline) > 0):
        batch = queue[i: i + min(MAX_LINKS, MAX_LINK_CHECKS - n_checked)]
        i += len(batch)
        got, n = authz.authorize(user, batch, index=index, connectors=connectors,
                                 readable=readable, deadline=deadline, undecided=undecided)
        n_checked += n
        ok.update(got)
        decided.update(d for d in batch if d not in undecided)
        targets, done = settle()
    if not done:
        return [], False                # the budget ran out: omit, never a partial "whole" list
    return [{**{k: v for k, v in authz.public_meta(ok[t]).items()
                if k in ("doc_id", "platform", "title", "container_name", "kind")},
             "relation": rel} for t, rel in targets], True


__all__ = ["view_doc", "NotFound", "EXCERPT_CHARS", "MAX_LINKS"]

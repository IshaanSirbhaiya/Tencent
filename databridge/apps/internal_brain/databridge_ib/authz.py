"""Version-bound authorisation for every read OUTSIDE the ask pipeline (graph, doc, trace receipts).

The pipeline's read-through (``pipeline.Pipeline._read_through``) is the reference; this module copies
its semantics so the developer views cannot be weaker than an answer (Codex plan review #4):

1. **Gate 1** — only ids in ``index.readable(user)`` (the stored source permissions) are considered.
2. **Gate 2** — the SOURCE is asked live (``gates.check_all``, with the indexed version as the known
   version). Forbidden, vanished, unverifiable, timeout, 429, error: deny.
3. **Read-through on drift** — when the source's version moved on, the document is re-fetched, its
   fresh ACL compiled, the text masked (``mask_doc``) and re-indexed; it is admitted ONLY if the
   fresh ACL still admits this user AND the fetched version is the version Gate 2 checked.
   ``drift_material`` without a version is a deny.
4. **Snapshot** — what is returned is an immutable :class:`AuthorizedDoc` built at decision time (the
   fresh doc, or a copy of the index row bound to the checked version and re-checked against the
   stored policy), never a later re-read of a mutable row. Any exception: deny.

Callers serialise only whitelisted fields of a snapshot (:func:`public_meta`); the raw index row, its
ACL expression, the full text and unresolved link ids never leave through these helpers.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from . import acl as aclmod
from . import gates
from .index import Index, identifiers
from .models import AccessCheck, DirectoryUser
from .sensitive import mask_doc

#: the developer views' own small pool: their live checks never queue ahead of an answer's Gate 2
VIEW_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="ib-view-gate2")
VIEW_CHECK_TIMEOUT_S = 20.0


@dataclass(frozen=True)
class AuthorizedDoc:
    doc_id: str
    platform: str
    kind: str
    container: str
    container_name: str
    title: str
    version: str
    created_at: str
    updated_at: str
    deep_link: str
    author: str                     # IB user id, "" when unknown
    links: tuple[str, ...]          # raw link ids as indexed (internal; resolve before output)
    text: str                       # masked text (internal; only excerpts leave, via /ib/doc)
    indexed_at: str
    authz_checked_at: datetime
    content_checked_at: datetime
    live_read: bool


def _parse_dt(raw: Any, default: datetime) -> datetime:
    try:
        d = datetime.fromisoformat(str(raw))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return default


def _from_row(row: Mapping[str, Any], chk: AccessCheck) -> AuthorizedDoc:
    try:
        links = tuple(str(x) for x in json.loads(row.get("links") or "[]"))
    except ValueError:
        links = ()
    return AuthorizedDoc(
        doc_id=str(row["doc_id"]), platform=str(row["platform"]), kind=str(row.get("kind") or ""),
        container=str(row.get("container") or ""), container_name=str(row.get("container_name") or ""),
        title=str(row.get("title") or ""), version=str(row.get("version") or ""),
        created_at=str(row.get("created_at") or ""), updated_at=str(row.get("updated_at") or ""),
        deep_link=str(row.get("deep_link") or ""), author=str(row.get("author") or ""),
        links=links, text=str(row.get("text") or ""), indexed_at=str(row.get("indexed_at") or ""),
        authz_checked_at=chk.checked_at,
        content_checked_at=_parse_dt(row.get("indexed_at"), chk.checked_at), live_read=False)


def _snapshot(user: DirectoryUser, doc_id: str, chk: AccessCheck, index: Index,
              connectors: Mapping[str, Any]) -> Optional[AuthorizedDoc]:
    if not chk.allowed or chk.doc_id != doc_id:
        return None
    row = index.doc(doc_id)
    if row is None:
        return None
    row = dict(row)                                     # a copy, taken at decision time
    if chk.verdict == "drift_material" and not chk.current_version:
        return None                                     # material drift, no version: cannot verify
    if chk.current_version and chk.current_version != row.get("version"):
        conn = connectors.get(row.get("platform", ""))
        if conn is None:
            return None
        fresh = conn.fetch(doc_id)
        if fresh is None:
            index.delete(doc_id)
            return None
        fresh_acl = conn.acl(doc_id)
        masked = mask_doc(fresh)                        # masked like any ingest
        index.upsert(masked, fresh_acl)
        if not aclmod.allows(fresh_acl, user):
            return None                                 # the new version is not readable by them
        if masked.version != chk.current_version:
            return None                                 # fetched another version than was checked
        frow = {"doc_id": masked.doc_id, "platform": masked.platform, "kind": masked.kind,
                "container": masked.container, "container_name": masked.container_name,
                "title": masked.title, "version": masked.version,
                "created_at": masked.created_at.isoformat(),
                "updated_at": masked.updated_at.isoformat(), "deep_link": masked.deep_link,
                "author": masked.author or "", "links": json.dumps(list(masked.links)),
                "text": masked.text, "indexed_at": chk.checked_at.isoformat()}
        snap = _from_row(frow, chk)
        return AuthorizedDoc(**{**snap.__dict__, "content_checked_at": chk.checked_at,
                                "live_read": True})
    # no drift: the stored policy must still admit the user at snapshot time (a concurrent ACL
    # change between the check and now is a deny, not a race)
    pol = index.acl_of(doc_id)
    if pol is None or not aclmod.allows(pol, user):
        return None
    return _from_row(row, chk)


def authorize(user: DirectoryUser, doc_ids: Iterable[str], *, index: Index,
              connectors: Mapping[str, Any], readable: Optional[frozenset] = None,
              directory_epoch: int = 0, timeout_s: float = VIEW_CHECK_TIMEOUT_S,
              pool: Optional[ThreadPoolExecutor] = None) -> tuple[dict[str, AuthorizedDoc], int]:
    """``({doc_id: snapshot} for every id that passes Gate 1, Gate 2 and read-through, n_checked)``.
    Ids outside Gate 1 are dropped before any source call, exactly like unknown ids."""
    readable = readable if readable is not None else index.readable(user, directory_epoch)
    cands = [d for d in dict.fromkeys(doc_ids) if isinstance(d, str) and d in readable]
    if not cands:
        return {}, 0
    rows = {d: (index.doc(d) or {}) for d in cands}
    checks = gates.check_all(user, cands, {d: r.get("platform", "") for d, r in rows.items()},
                             connectors, timeout_s=timeout_s,
                             known_versions={d: r.get("version") for d, r in rows.items()},
                             pool=pool or VIEW_POOL)
    out: dict[str, AuthorizedDoc] = {}
    for d in cands:
        c = checks.get(d)
        if c is None:
            continue
        try:
            snap = _snapshot(user, d, c, index, connectors)
        except Exception:                                           # noqa: BLE001 — fail closed
            snap = None
        if snap is not None:
            out[d] = snap
    return out, len(cands)


def resolve_link(link: str, readable) -> Optional[str]:
    """Same rule as ``Pipeline._resolve_link``: resolve ONLY inside the asker's readable set, so a
    restricted object with the same identifier never changes what a link resolves to."""
    candidates = [link] + [p + link for p in ("jira:", "confluence:", "drive:")]
    if identifiers(link):
        candidates.append("jira:" + link.upper())
    for c in candidates:
        if c in readable:
            return c
    return None


def public_meta(s: AuthorizedDoc) -> dict[str, Any]:
    """The whitelisted metadata of an authorised document (no text, no ACL, no raw links)."""
    return {"doc_id": s.doc_id, "platform": s.platform, "kind": s.kind,
            "container": s.container, "container_name": s.container_name, "title": s.title,
            "version": s.version, "updated_at": s.updated_at, "deep_link": s.deep_link}


__all__ = ["AuthorizedDoc", "authorize", "resolve_link", "public_meta", "VIEW_POOL"]

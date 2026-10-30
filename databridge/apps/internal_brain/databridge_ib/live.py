"""Live growth of the knowledge graph: ``GET /ib/graph/changes?since=<cursor>`` (Developer mode).

The web graph polls this every few seconds and merges what comes back, so a note posted at the
source (``/sim/add-document``, or any real change) pulses into the person's graph a moment after
its webhook is ingested.

Rules (the graph's own, ``graph.py``):

* **Only what the CALLER can open now.** Candidates are the documents that are NEW or whose
  version CHANGED in the index since the cursor's time (a full re-sync that re-indexes the same
  version is not a change), intersected with the caller's Gate-1 readable set, then authorised
  LIVE with :func:`authz.authorize` (Gate 2 plus the version-bound read-through). Nodes and edges
  are built only from those snapshots, in the same shapes as ``/ib/graph``. Documents a new one
  links to or mentions (a Jira key in its text, a supported URL) are authorised live the same way
  and included, so the new node arrives with its ``links``/``mentions`` edges to documents the
  person can already open; a link to anything else is dropped silently. Container, person and
  platform nodes carry ``count``, ``degree_hidden_safe`` and ``more`` as ``null`` (not exact here:
  show no number; the client keeps the numbers it already has from ``/ib/graph``).
* **A constant response shape** ``{"cursor", "nodes", "edges"}``: no sequence numbers, no count of
  filtered or denied changes, no "something changed that you cannot see". A change the caller
  cannot open is indistinguishable from no change.
* **An opaque, per-person cursor that depends only on the caller and the clock** (PLAN L-1, L-2).
  ``base64url(json{v, s, t}) "." base64url(HMAC-SHA256)`` under a key derived from the token key;
  ``s`` is the caller, ``t`` the tracker's high-water mark AT REPLY (never a document's stamp, so a
  candidate that was filtered, denied, undecided or past the cap changes nothing the caller can
  decode). A poll delivers the documents stamped in ``[t_prev, t_now)``; stamps are never below an
  issued mark, so nothing falls into a gap and nothing is sent twice. A cursor that is missing,
  malformed, forged, issued to someone else or older than ``IDLE_S`` (10 minutes) starts a NEW
  window: no nodes, a fresh cursor, nothing back-filled.
* **Ledger (write-ahead, ``graph.viewed``; counts only).** Ledgering every 3-second poll would
  flood the audit log with rows that record no read, so: the FIRST poll of a window (a new cursor
  issued) is ledgered with zero counts, and after that only a NON-EMPTY delta is ledgered, before
  the response, with ``docs_checked`` = the live checks it ran. An empty poll inside a window is
  not ledgered: nothing was returned to the person. (Candidates that failed their live check in
  such a poll are not recorded either; they were never shown.) A ledger failure is a 503. The
  rows carry ``mode="changes"`` (PLAN L-4), so compliance views never count a poll as a focus.
* **Demo-only** (D-24). ``bootstrap.build_app`` mounts this router only when
  ``IbConfig.demo_features_on`` (``IB_DEMO_FEATURES=1`` and not ``IB_DEPLOYED=1``); otherwise the
  path answers exactly what an unknown route answers.
* **Bounded work, cap after the check** (PLAN L-1, R5). Up to ``MAX_CHECK`` Gate-1 candidates,
  newest first, are authorised under ONE deadline (with their linked documents, PLAN L-6); then
  the AUTHORISED ones are capped to ``MAX_DOCS``. An undecided candidate or one past the cap is
  simply not shown (fail closed); the next full ``/ib/graph`` load shows it.
* **Reset.** ``rebaseline()`` re-stamps everything as old; ``/sim/reset`` calls it after its full
  reconciliation, so a reset does not look like a burst of "updated" documents (PLAN L-3).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from . import authz
from .config import POLICY_VERSION
from .graph import cid, pid, plid
from .models import GraphViewed

log = logging.getLogger("databridge_ib.live")

EMPLOYEE_ROLES = frozenset({"employee", "compliance_officer", "admin_it"})
IDLE_S = 600.0                      # a cursor older than this starts a new (ledgered) window
WINDOW_LEDGER_S = 5.0               # at most one new-window ledger row per person per 5 s (round 3 I2)
MAX_DOCS = 25                       # authorised documents returned per poll
MAX_CHECK = 200                     # Gate-1 candidates checked live per poll (newest first)
MAX_LINKED = 20                     # linked documents checked live per poll (edges to the old graph)
CHECK_TIMEOUT_S = 10.0
CURSOR_MAX = 400


class ChangeTracker:
    """When each (document, version) was first seen in the index, in this process. The first scan
    is the baseline (everything already there is "old"); later scans stamp a document whose id is
    new or whose version moved on. Deleted documents are forgotten.

    Time is the tracker's own: ``mark()`` (a cursor's ``t``) never goes below an earlier mark, and a
    stamp is never below the last mark, so ``[t_prev, t_now)`` windows tile without gaps or
    repeats even on a coarse or frozen clock."""

    def __init__(self, index, clock: Callable[[], float] = time.time) -> None:
        self.index = index
        self.clock = clock
        self._lock = threading.Lock()
        self._seen: dict[str, tuple[str, float]] = {}
        self._gen: Optional[int] = None
        self._last_mark = 0.0
        self._opened: dict[str, float] = {}

    def window_opened(self, sub: str) -> bool:
        """True when ``sub``'s new window should be ledgered: the first in ``WINDOW_LEDGER_S``."""
        now = float(self.clock())
        with self._lock:
            last = self._opened.get(sub)
            if last is not None and 0 <= now - last < WINDOW_LEDGER_S:
                return False
            self._opened[sub] = now
            return True

    def mark(self) -> float:
        with self._lock:
            self._last_mark = max(float(self.clock()), self._last_mark)
            return self._last_mark

    def scan(self) -> None:
        with self._lock:
            gen = getattr(self.index, "acl_version", None)
            if self._gen is not None and gen == self._gen:
                return
            baseline = self._gen is None
            now = max(float(self.clock()), self._last_mark)
            ids = self.index.doc_ids()
            live = set(ids)
            for d in ids:
                row = self.index.doc(d)
                if row is None:
                    continue
                ver = str(row.get("version") or "")
                old = self._seen.get(d)
                if old is None or old[0] != ver:
                    self._seen[d] = (ver, 0.0 if baseline else now)
            for d in [x for x in self._seen if x not in live]:
                self._seen.pop(d, None)
            self._gen = gen

    def rebaseline(self) -> None:
        """Everything now in the index is "old" (after a reset's full reconciliation)."""
        with self._lock:
            self._seen.clear()
            self._gen = None
        self.scan()

    def window(self, t_from: float, t_to: float) -> list[tuple[float, str]]:
        """``[(stamp, doc_id)]`` with ``t_from <= stamp < t_to``, NEWEST first."""
        with self._lock:
            return sorted(((s, d) for d, (_v, s) in self._seen.items() if t_from <= s < t_to),
                          reverse=True)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class CursorCodec:
    def __init__(self, token_key: str) -> None:
        self._key = hmac.new(str(token_key).encode("utf-8"), b"ib-live-cursor-v1",
                             hashlib.sha256).digest()

    def encode(self, sub: str, t: float) -> str:
        payload = json.dumps({"v": 1, "s": sub, "t": round(float(t), 6)},
                             separators=(",", ":"), sort_keys=True).encode("utf-8")
        sig = hmac.new(self._key, payload, hashlib.sha256).digest()
        return f"{_b64(payload)}.{_b64(sig)}"

    def decode(self, sub: str, cursor: Optional[str]) -> Optional[float]:
        """The cursor's time, or None when it is missing, malformed, forged or someone else's."""
        if not cursor or len(cursor) > CURSOR_MAX or cursor.count(".") != 1:
            return None
        try:
            p64, s64 = cursor.split(".")
            payload, sig = _unb64(p64), _unb64(s64)
            if not hmac.compare_digest(sig, hmac.new(self._key, payload, hashlib.sha256).digest()):
                return None
            data = json.loads(payload)
            if not isinstance(data, dict) or data.get("v") != 1 or data.get("s") != sub:
                return None
            t = data.get("t")
            return float(t) if isinstance(t, (int, float)) and not isinstance(t, bool) else None
        except (ValueError, TypeError):
            return None


def _delta_graph(ok: dict, directory) -> tuple[list[dict], list[dict]]:
    """Nodes and edges in the ``/ib/graph`` shapes, built only from the authorised snapshots."""
    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str, str], dict] = {}

    def edge(a: str, b: str, kind: str) -> None:
        edges[(a, b, kind)] = {"source": a, "target": b, "kind": kind}

    unknown = {"degree_hidden_safe": None, "more": None}
    for d, s in ok.items():
        c, p = cid(s.platform, s.container), plid(s.platform)
        nodes[d] = {"id": d, "kind": "doc", "platform": s.platform, "label": s.title,
                    "container": c, "container_name": s.container_name, "doc_kind": s.kind,
                    "version": s.version, "updated_at": s.updated_at, "deep_link": s.deep_link,
                    **unknown}
        nodes.setdefault(c, {"id": c, "kind": "container", "platform": s.platform,
                             "label": s.container_name or s.container, "container": None,
                             "count": None, **unknown})
        nodes.setdefault(p, {"id": p, "kind": "platform", "platform": s.platform,
                             "label": s.platform, "container": None, "count": None, **unknown})
        edge(c, d, "contains")
        edge(p, c, "contains")
        if s.author:
            a = pid(s.author)
            u = directory.get(s.author) if hasattr(directory, "get") else None
            nodes.setdefault(a, {"id": a, "kind": "person", "platform": None,
                                 "label": getattr(u, "name", None) or s.author, "container": None,
                                 "count": None, **unknown})
            edge(a, d, "authored")
    ids = set(ok)
    for d, s in ok.items():                     # doc -> doc only inside the confirmed delta
        for link in s.links:
            t = authz.resolve_link(link, ids)
            if t and t != d:
                edge(d, t, "mentions" if str(link).startswith("jira:") else "links")
    return list(nodes.values()), [e for _k, e in sorted(edges.items())]


def changes(user, *, index, directory, ledger, connectors, tracker: ChangeTracker,
            codec: CursorCodec, since: Optional[str], idle_s: float = IDLE_S,
            max_docs: int = MAX_DOCS, max_check: int = MAX_CHECK,
            timeout_s: float = CHECK_TIMEOUT_S) -> dict:
    """The delta dict. Ledger failures propagate (the route answers 503)."""
    tracker.scan()
    t_now = tracker.mark()                                 # the reply's t: caller + clock only
    t = codec.decode(user.id, since)
    empty = {"cursor": codec.encode(user.id, t_now), "nodes": [], "edges": []}
    if t is None or t_now - t > idle_s or t > t_now:
        # a new window: ledgered with zero counts, at most once per person per WINDOW_LEDGER_S
        # (round 3 I2: cursor-less or garbage-cursor polls cannot append unbounded rows; the
        # reply carries a cursor only, no node, edge or count)
        if tracker.window_opened(user.id):
            _ledger(ledger, user, 0, 0, 0, 0)
        return empty
    readable = index.readable(user, getattr(directory, "epoch", 0))         # GATE 1
    cands = [d for _s, d in tracker.window(t, t_now) if d in readable][:max_check]
    if not cands:
        return empty                                       # no source call (R4)
    deadline = authz.deadline_in(timeout_s)                # ONE deadline for every check (L-6)
    got, n_checked = authz.authorize(user, cands, index=index, connectors=connectors,
                                     readable=readable, deadline=deadline)
    ok = {d: got[d] for d in cands if d in got}            # newest first, authorised only
    ok = dict(list(ok.items())[:max_docs])                 # the cap AFTER the live check (R5)
    if not ok:
        return empty
    # documents a new one links to or mentions (a Jira key, a supported URL): checked live too, so
    # the new node arrives CONNECTED to what the person can already open (never to anything else)
    targets = [c for s in ok.values() for link in s.links
               for c in authz.link_candidates(link, readable) if c not in ok]
    targets = list(dict.fromkeys(targets))[:MAX_LINKED]
    if targets and authz.remaining(deadline) > 0:
        linked, n = authz.authorize(user, targets, index=index, connectors=connectors,
                                    readable=readable, deadline=deadline)
        n_checked += n
        ok = {**ok, **linked}
    nodes, edges = _delta_graph(ok, directory)
    _ledger(ledger, user, len(nodes), len(ok), len(edges), n_checked)    # write-ahead
    return {"cursor": empty["cursor"], "nodes": nodes, "edges": edges}


def _ledger(ledger, user, nodes: int, docs: int, edges: int, checked: int) -> None:
    ledger.append(GraphViewed(sub=user.id, mode="changes", outcome="ok", nodes_returned=nodes,
                              docs_returned=docs, edges_returned=edges, docs_checked=checked,
                              policy_version=POLICY_VERSION,
                              event_time=datetime.now(timezone.utc)))


def make_live_router(svc: Any, current: Callable, *, clock: Callable[[], float] = time.time,
                     idle_s: float = IDLE_S, max_docs: int = MAX_DOCS,
                     max_check: int = MAX_CHECK) -> APIRouter:
    """``GET /ib/graph/changes``. ``svc`` is the app's ``Services``; ``current`` the principal
    dependency. Build it AFTER the initial sync, so the baseline is the synced index."""
    router = APIRouter()
    tracker = ChangeTracker(svc.index, clock=clock)
    tracker.scan()                                         # the baseline
    codec = CursorCodec(svc.cfg.token_key)
    router.tracker = tracker                               # type: ignore[attr-defined]

    @router.get("/ib/graph/changes")
    def graph_changes(since: Optional[str] = Query(None, max_length=CURSOR_MAX),
                      user=Depends(current)):
        if not (set(user.roles) & EMPLOYEE_ROLES):
            raise HTTPException(403, "forbidden")
        try:
            return changes(user, index=svc.index, directory=svc.directory, ledger=svc.ledger,
                           connectors=svc.connectors, tracker=tracker, codec=codec, since=since,
                           idle_s=idle_s, max_docs=max_docs, max_check=max_check)
        except Exception as exc:                                    # noqa: BLE001
            log.error("graph changes failed: %s", type(exc).__name__)   # incl. ledger down
            return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})

    return router


__all__ = ["make_live_router", "changes", "ChangeTracker", "CursorCodec", "IDLE_S", "MAX_DOCS",
           "MAX_CHECK"]

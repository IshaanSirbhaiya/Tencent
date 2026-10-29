"""Aggregate statistics over the verified access ledger, for the compliance/developer analysis page.

Counts, timings and distributions only: no question text, no answer text, no document titles. The
input is the stream of verified ledger events (`AccessLedger.events`), so every number on the page is
derived from the same sealed record the audit console reads.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from typing import Any, Iterable, Optional

STAGES = ("accept", "gate1", "gate2", "seal", "compose")
PLATFORMS = ("confluence", "jira", "slack", "drive")
ALLOWED_VERDICTS = ("verified", "drift_material")      # AccessCheck.allowed


def _platform(doc_id: str) -> str:
    head = str(doc_id).split(":", 1)[0]
    return head if head in PLATFORMS else "other"


def _when(data: dict[str, Any], recorded_at: Optional[str]) -> Optional[dt.datetime]:
    raw = data.get("event_time") or recorded_at
    if not raw:
        return None
    try:
        t = dt.datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _pct(values: list[int], p: float) -> Optional[int]:
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
    return s[k]


def compute_stats(events: Iterable[Any], *, now: dt.datetime, days: int = 30,
                  include_replay: bool = True) -> dict[str, Any]:
    """`events` yields objects with ``kind``, ``data``, ``seq`` and ``recorded_at`` (LedgerEvent)."""
    since = now - dt.timedelta(days=days)
    kinds: Counter = Counter()
    channels: Counter = Counter()
    models: Counter = Counter()
    gate2_verdicts: Counter = Counter()
    gate2_reasons: Counter = Counter()
    per_platform: dict[str, Counter] = defaultdict(Counter)
    per_user: dict[str, Counter] = defaultdict(Counter)
    per_day: dict[str, Counter] = defaultdict(Counter)
    stage_vals: dict[str, list[int]] = defaultdict(list)
    latencies: list[int] = []
    totals = Counter()
    first_seq = last_seq = None
    head_hash = None
    for le in events:
        data = le.data if isinstance(le.data, dict) else {}
        k = le.kind
        if first_seq is None:
            first_seq = le.seq
        last_seq, head_hash = le.seq, getattr(le, "hash", None)
        totals["ledger_events"] += 1
        when = _when(data, getattr(le, "recorded_at", None))
        if when is None or when < since:
            continue
        if data.get("synthetic_replay") and not include_replay:
            continue
        kinds[k] += 1
        sub = str(data.get("sub") or "")
        day = when.date().isoformat()
        if k == "request.accepted":
            totals["requests"] += 1
            channels[str(data.get("channel") or "ui")] += 1
            per_user[sub]["asked"] += 1
            per_day[day]["asked"] += 1
        elif k == "retrieval.decided":
            considered = list(data.get("considered") or ())
            released = list(data.get("released") or ())
            totals["considered"] += len(considered)
            totals["released_docs"] += len(released)
            for d in considered:
                per_platform[_platform(d)]["considered"] += 1
            for d in released:
                per_platform[_platform(d)]["released"] += 1
            for g in data.get("gate2") or ():
                if not isinstance(g, dict):
                    continue
                verdict = str(g.get("verdict") or "")
                gate2_verdicts[verdict] += 1
                gate2_reasons[str(g.get("reason_code") or "")] += 1
                per_platform[_platform(g.get("doc_id", ""))]["gate2_checked"] += 1
        elif k == "answer.released":
            refused = bool(data.get("refused"))
            totals["refused" if refused else "answered"] += 1
            per_user[sub]["refused" if refused else "answered"] += 1
            per_day[day]["refused" if refused else "answered"] += 1
            m = data.get("model") or {}
            models[f"{m.get('provider', '?')}/{m.get('model', '?')}"] += 1
            if m.get("fallback_used"):
                totals["fallback_used"] += 1
            totals["citations"] += len(data.get("citations") or ())
            totals["dropped_sentences"] += int(data.get("dropped_sentences_count") or 0)
            if isinstance(data.get("latency_ms"), int):
                latencies.append(int(data["latency_ms"]))
            for st, v in (data.get("stage_ms") or {}).items():
                if st in STAGES and isinstance(v, int):
                    stage_vals[st].append(v)
        elif k == "request.failed":
            totals["failed"] += 1
            per_day[day]["failed"] += 1
        elif k == "audit.reveal":
            totals["reveals"] += 1
        elif k == "audit.query":
            totals["audit_queries"] += 1
        elif k in ("acl.changed", "membership.changed", "directory.changed"):
            totals["access_changes"] += 1
        elif k == "ingest.sync":
            totals["syncs"] += 1
        elif k == "map.viewed":
            totals["map_views"] += 1
        elif k == "auth.login":
            totals["logins"] += 1
    allowed = sum(v for key, v in gate2_verdicts.items() if key in ALLOWED_VERDICTS)
    gate2_total = sum(gate2_verdicts.values())
    finished = totals["answered"] + totals["refused"]
    return {
        "window": {"since": since.isoformat(), "until": now.isoformat(), "days": days},
        "ledger": {"events": totals["ledger_events"], "first_seq": first_seq, "last_seq": last_seq,
                   "head": head_hash},
        "totals": {
            "requests": totals["requests"], "answered": totals["answered"],
            "refused": totals["refused"], "failed": totals["failed"],
            "answer_rate": round(totals["answered"] / finished, 4) if finished else None,
            "citations": totals["citations"], "dropped_sentences": totals["dropped_sentences"],
            "fallback_used": totals["fallback_used"], "reveals": totals["reveals"],
            "audit_queries": totals["audit_queries"], "access_changes": totals["access_changes"],
            "syncs": totals["syncs"], "map_views": totals["map_views"], "logins": totals["logins"],
        },
        "funnel": {
            "asked": totals["requests"], "considered_docs": totals["considered"],
            "gate2_checked": gate2_total, "gate2_allowed": allowed,
            "gate2_denied": gate2_total - allowed, "released_docs": totals["released_docs"],
            "cited": totals["citations"], "answered": totals["answered"],
        },
        "gate2": {"verdicts": dict(gate2_verdicts), "reasons": dict(gate2_reasons)},
        "latency_ms": {"count": len(latencies), "p50": _pct(latencies, 0.5),
                       "p95": _pct(latencies, 0.95), "max": max(latencies) if latencies else None,
                       "mean": round(sum(latencies) / len(latencies)) if latencies else None},
        "stages_ms": {st: {"p50": _pct(stage_vals[st], 0.5), "p95": _pct(stage_vals[st], 0.95),
                           "count": len(stage_vals[st])} for st in STAGES},
        "models": dict(models),
        "channels": dict(channels),
        "platforms": {p: dict(per_platform[p]) for p in (*PLATFORMS, "other") if per_platform.get(p)},
        "people": sorted(({"sub": s, **dict(c)} for s, c in per_user.items() if s),
                         key=lambda r: (-r.get("asked", 0), r["sub"])),
        "days": [{"day": d, **dict(per_day[d])} for d in sorted(per_day)],
        "kinds": dict(kinds),
    }


__all__ = ["compute_stats", "STAGES"]

"""Deterministic time-window parsing ("last week", "last quarter", "last 30 days" …) — no model.

Windows are a RANKING PREFERENCE in retrieval (docs inside the window are boosted; a question like
"status of the migration and blockers raised last week" still needs the undated Jira status), and a
hard FILTER in audit queries. Relative to an explicit clock so a seeded world never drifts.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

Window = tuple[datetime, datetime]

_N_DAYS = re.compile(r"\b(?:last|past|previous)\s+(\d{1,3})\s+(day|week|month)s?\b", re.I)


def _start_of_day(t: datetime) -> datetime:
    return t.replace(hour=0, minute=0, second=0, microsecond=0)


def parse(text: str, now: datetime) -> Optional[Window]:
    s = (text or "").lower()
    m = _N_DAYS.search(s)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        days = n * {"day": 1, "week": 7, "month": 30}[unit]
        return now - timedelta(days=min(days, 365)), now
    today = _start_of_day(now)
    monday = today - timedelta(days=today.weekday())
    if re.search(r"\b(?:last|previous|past)\s+week\b", s):
        return monday - timedelta(days=7), monday
    if re.search(r"\bthis\s+week\b", s):
        return monday, now
    if re.search(r"\b(?:last|previous|past)\s+sprint\b", s):
        return monday - timedelta(days=14), monday
    if re.search(r"\byesterday\b", s):
        return today - timedelta(days=1), today
    if re.search(r"\btoday\b", s):
        return today, now
    if re.search(r"\b(?:last|previous|past)\s+month\b", s):
        first = today.replace(day=1)
        prev = (first - timedelta(days=1)).replace(day=1)
        return prev, first
    if re.search(r"\b(?:last|previous|past)\s+quarter\b", s):
        q0 = ((today.month - 1) // 3) * 3 + 1
        this_q = today.replace(month=q0, day=1)
        prev_end = this_q
        pm = q0 - 3
        prev_q = this_q.replace(year=this_q.year - 1, month=pm + 12) if pm < 1 else this_q.replace(month=pm)
        return prev_q, prev_end
    return None


def overlaps(window: Window, created: datetime, updated: datetime) -> bool:
    lo, hi = window
    return created < hi and updated >= lo

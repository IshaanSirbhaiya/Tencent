"""The two cuts, as functions. Everything the temporal contract means, executable.

The rule (R05 §1.3), unpacked for a per-row decision instant T_d and a build instant T_b:

| Component | Filter | Why |
|---|---|---|
| **Features** | `system_time <= T_d` | replays what the organisation KNEW at the decision |
| **Feature semantics** (optional) | `valid_time <= T_d` | "what did we then believe *about* T_d" |
| **Labels** | `valid_time > T_d`, within the window, read at latest system time | the outcome must postdate the decision, and it must be the best-known truth including corrections |
| **Maturity** | `label.system_time <= T_b - lag` | an outcome not yet RECORDED is not a negative |
| **Window closed** | `T_d + within <= T_b - lag` | a label about a future instant is not known yet |

Two things this module refuses to be clever about.

**A naive instant is refused, not assumed to be UTC.** "2026-08-16T09:00" is a different moment in
Singapore than in London, and a training-set cut that depends on the reader's timezone is not a
cut. Estate columns declared as dates (no time component) are the one exception and are widened to
midnight UTC explicitly, because a `DATE` genuinely has no time-of-day to preserve — the widening
is stated here rather than happening by accident somewhere downstream.

**A missing timestamp is never treated as "long ago".** A row whose system-time column is NULL, or
withheld from this principal, cannot be shown to have been known at T_d, so it does not enter the
feature cut. The tempting default — treat unknown as always-known — admits every late-arriving
fact, which is the leak this whole module exists to close.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Optional

from .durations import parse_duration


class TemporalValueError(ValueError):
    pass


def to_instant(value: Any) -> Optional[datetime]:
    """Coerce a cell value to a timezone-aware UTC instant, or `None` if it is not one.

    `None` in, `None` out — and `None` out means "this row cannot be placed on the axis", which
    every caller here treats as *excluded*, never as *unbounded*.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise TemporalValueError(
                f"naive datetime {value!r} on a temporal axis: it means a different instant in "
                f"every deployment, so the cut it produces is not reproducible. Store the offset")
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        # A DATE has no time of day. Widening to midnight UTC is stated, not assumed: a `test_date`
        # of 2025-03-04 places the decision at the start of that day, so a fact recorded later the
        # same day was NOT available at the decision instant. That is the conservative direction,
        # which is the correct one for a leakage control.
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def known_at(system_time: Any, decision_time: datetime) -> bool:
    """Was this fact RECORDED by the decision instant? The anti-leakage cut, in one line.

    An unparseable or missing system time answers `False`. It is the only safe answer: the
    alternative asserts that something whose recording instant nobody wrote down was nonetheless
    available at a moment nobody can check.
    """
    instant = to_instant(system_time)
    return instant is not None and instant <= decision_time


def in_force_at(valid_time: Any, decision_time: datetime) -> bool:
    """The optional secondary filter: was this fact true ABOUT the decision instant?

    Separate from `known_at` and never a substitute for it. Using this alone is the Feast default,
    and it admits a value backfilled or corrected after the decision.
    """
    instant = to_instant(valid_time)
    return instant is not None and instant <= decision_time


def label_in_window(valid_time: Any, decision_time: datetime, within: str) -> bool:
    """`(T_d, T_d + within]` on VALID time. Strictly after: an outcome simultaneous with the
    decision is not caused by it and is not an outcome of it."""
    instant = to_instant(valid_time)
    if instant is None:
        return False
    return decision_time < instant <= decision_time + parse_duration(within)


def label_is_mature(system_time: Any, build_time: datetime, lag: str) -> bool:
    """`label.system_time <= T_b - lag`. The right-censoring guard.

    Without it, an outcome that occurred inside the window but has not yet been recorded becomes a
    NEGATIVE example. The bias is always in the same direction and it is invisible in every metric
    the training run reports, which is why the lag is a required declaration rather than a default.
    """
    instant = to_instant(system_time)
    return instant is not None and instant <= build_time - parse_duration(lag)


def label_horizon(decision_time: datetime, within: str) -> datetime:
    """The label's own business instant: the END of its window, `T_d + within`.

    A label "the status 30 days after issue" is a fact ABOUT `T_d + P30D`. It is computed from the
    row's grounded decision instant and the plan's declared window — never from when a store
    happened to record a version — so it holds however a source's rows were timestamped.
    """
    return decision_time + parse_duration(within)


def label_window_closed(decision_time: datetime, within: str, build_time: datetime,
                        lag: str) -> bool:
    """`T_d + within <= T_b - lag`: has the label's business instant ARRIVED (and matured)?

    E2E run 4 (Q-07): `label_is_mature` tested only when a candidate outcome was RECORDED. A source
    whose versions are stamped at the ingest instant then put "today's status" inside every still
    open window, and an invoice issued two weeks ago was labelled with its status "30 days after
    issue" — a date sixteen days in the future. A window that has not closed cannot have its
    outcome known, whatever the store holds; the row is immature, never labelled.
    """
    return label_horizon(decision_time, within) <= build_time - parse_duration(lag)


def within_lookback(instant: Any, decision_time: datetime, window: Optional[str]) -> bool:
    """`window` `None` means unbounded history; otherwise `[T_d - window, T_d]`."""
    parsed = to_instant(instant)
    if parsed is None:
        return False
    if parsed > decision_time:
        return False
    if window is None:
        return True
    return parsed >= decision_time - parse_duration(window)


__all__ = ["TemporalValueError", "to_instant", "known_at", "in_force_at", "label_in_window",
           "label_is_mature", "label_horizon", "label_window_closed", "within_lookback"]

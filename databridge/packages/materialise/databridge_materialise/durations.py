"""ISO-8601 durations, restricted to the subset a temporal contract may state.

Days, hours and minutes only. **Months and years are deliberately unsupported**, and the refusal
is the point: `P1M` is not a fixed length of time, so a maturity lag or a label window expressed
in months means a different cut in February than in July. A dataset whose row admission depends on
which month the build ran is not reproducible, and the plan hash would not say so.

`P0D` is legal and means "no lag" — an operator may state that explicitly. What the compiler will
not accept is the *absence* of a declaration, which is a different thing entirely (see
`MaturityLagMissing`).
"""
from __future__ import annotations

import re
from datetime import timedelta

_ISO = re.compile(
    r"^P(?!$)(?:(?P<weeks>\d+(?:\.\d+)?)W)?(?:(?P<days>\d+(?:\.\d+)?)D)?"
    r"(?:T(?!$)(?:(?P<hours>\d+(?:\.\d+)?)H)?(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$")

_CALENDAR = re.compile(r"^P(?!$)(?:\d+(?:\.\d+)?[YM])")


class DurationError(ValueError):
    pass


def parse_duration(text: str) -> timedelta:
    """`"P14D"` -> `timedelta(days=14)`. Refuses anything whose length is not fixed."""
    value = (text or "").strip()
    if not value:
        raise DurationError(
            "an empty duration is not a duration. If the intent is 'no lag', say P0D — the "
            "difference between 'declared as zero' and 'not declared' is exactly what the "
            "maturity-lag invariant is checking")
    if _CALENDAR.match(value):
        raise DurationError(
            f"duration {value!r} uses a calendar unit (Y or M). A month is not a fixed length of "
            f"time, so the same plan would cut differently depending on which month it ran in, "
            f"and the plan hash would not record the difference. Express it in days")
    m = _ISO.match(value)
    if not m:
        raise DurationError(
            f"duration {value!r} is not an ISO-8601 duration in the supported subset "
            f"(weeks/days/hours/minutes/seconds, e.g. P14D, P365D, PT12H)")
    parts = {k: float(v) for k, v in m.groupdict().items() if v is not None}
    return timedelta(weeks=parts.get("weeks", 0.0), days=parts.get("days", 0.0),
                     hours=parts.get("hours", 0.0), minutes=parts.get("minutes", 0.0),
                     seconds=parts.get("seconds", 0.0))


def is_duration(text: str) -> bool:
    try:
        parse_duration(text)
    except DurationError:
        return False
    return True


__all__ = ["DurationError", "parse_duration", "is_duration"]

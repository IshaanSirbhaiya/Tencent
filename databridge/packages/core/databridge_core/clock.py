"""The deployment's "today": read from the clock, in the deployment's time zone.

C-5 (finance dry run, 2026-10-02): moved here from ``databridge_intake.clock``, which every pack's
surfaces used. The rule is unchanged (HC1, 2026-10-01): the default is the clock, never a frozen
fixture date. A caller that needs a fixture date passes it explicitly, where a reader can see it.

The zone is ``WALNUT_TIMEZONE`` (the variable ``databridge_agent_runtime`` and
``databridge_automation`` read), defaulting to ``Asia/Singapore``. A *date* is zone-dependent: at
00:30 in Singapore the UTC date is still yesterday, and an age or validity window that ticks over a
day late is wrong for that whole morning.
"""
from __future__ import annotations

import datetime as dt
import os
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: The env var naming the deployment's IANA zone.
TIMEZONE_ENV = "WALNUT_TIMEZONE"

#: The deployment default when ``WALNUT_TIMEZONE`` is unset.
DEFAULT_TIMEZONE = "Asia/Singapore"


class ClockError(RuntimeError):
    """``WALNUT_TIMEZONE`` names no zone this host knows. Fail loud; never fall back to UTC."""


def deployment_timezone() -> ZoneInfo:
    name = (os.environ.get(TIMEZONE_ENV) or "").strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ClockError(f"{TIMEZONE_ENV}={name!r} is not a known IANA time zone") from exc


def deployment_today(now: Optional[dt.datetime] = None) -> dt.date:
    """Today's date in the deployment's zone. ``now`` (aware) is for tests that pin an instant."""
    zone = deployment_timezone()
    if now is None:
        return dt.datetime.now(zone).date()
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware; a naive datetime has no date in any zone")
    return now.astimezone(zone).date()


def resolve_today(today: Optional[dt.date]) -> dt.date:
    """``today`` when the caller passed one, else the clock. The one place defaults resolve."""
    return today if today is not None else deployment_today()


__all__ = ["ClockError", "DEFAULT_TIMEZONE", "TIMEZONE_ENV", "deployment_timezone",
           "deployment_today", "resolve_today"]

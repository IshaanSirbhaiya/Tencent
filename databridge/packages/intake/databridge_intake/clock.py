"""The deployment's "today" — re-exported from ``databridge_core.clock`` (C-5, 2026-10-02).

Every pack's surfaces read the deployment clock, so it lives in the dependency root. The names stay
importable here. :func:`resolve_today` is defined HERE rather than re-exported, on purpose: it reads
this module's ``deployment_today`` at call time, so a test that pins the clock by patching
``databridge_intake.clock.deployment_today`` still pins every intake default that resolves through
it (HC1, 2026-10-01 — the default is the clock, never the fixture date).
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from databridge_core.clock import (
    DEFAULT_TIMEZONE,
    TIMEZONE_ENV,
    ClockError,
    deployment_timezone,
    deployment_today,
)


def resolve_today(today: Optional[dt.date]) -> dt.date:
    """``today`` when the caller passed one, else the clock. The one place defaults resolve."""
    return today if today is not None else deployment_today()


__all__ = ["ClockError", "DEFAULT_TIMEZONE", "TIMEZONE_ENV", "deployment_timezone",
           "deployment_today", "resolve_today"]

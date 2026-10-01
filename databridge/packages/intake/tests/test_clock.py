"""HC1 (2026-10-01), SWEEP hardcode audit #3: "today" is the clock in the deployment's zone, not the
fixture date. `DATASET_TODAY` survives only as a value a test passes explicitly."""
from __future__ import annotations

import datetime as dt

import pytest

from databridge_intake import clock
from databridge_intake.registry import DATASET_TODAY, parse_dob_two_digit_year


def test_the_date_is_taken_in_the_deployment_zone(monkeypatch):
    # 16:30 UTC is already the next day in UTC+8 and still the same day in UTC.
    instant = dt.datetime(2026, 10, 1, 16, 30, tzinfo=dt.timezone.utc)
    monkeypatch.setenv(clock.TIMEZONE_ENV, "UTC")
    utc_day = clock.deployment_today(instant)
    monkeypatch.setenv(clock.TIMEZONE_ENV, clock.DEFAULT_TIMEZONE)
    local_day = clock.deployment_today(instant)
    # Derived from the zone's own offset, not written down.
    offset = clock.deployment_timezone().utcoffset(instant.replace(tzinfo=None))
    assert local_day == (instant + offset).date()
    assert utc_day == instant.date()
    assert local_day != utc_day, "the instant must straddle midnight or this test proves nothing"


def test_the_default_zone_applies_when_unset(monkeypatch):
    monkeypatch.delenv(clock.TIMEZONE_ENV, raising=False)
    assert clock.deployment_timezone().key == clock.DEFAULT_TIMEZONE


def test_an_unknown_zone_fails_loud_rather_than_falling_back_to_utc(monkeypatch):
    monkeypatch.setenv(clock.TIMEZONE_ENV, "Not/AZone")
    with pytest.raises(clock.ClockError):
        clock.deployment_today()


def test_a_naive_instant_is_refused():
    with pytest.raises(ValueError):
        clock.deployment_today(dt.datetime(2026, 10, 1, 12, 0))


def test_an_unpassed_today_reads_the_clock_not_the_fixture_date(monkeypatch):
    """The mutation this guards: restoring `today=DATASET_TODAY` as the default. Pin the clock far
    from the fixture date, pick a two-digit year that pivots differently on the two days, and the
    default must follow the clock."""
    future = DATASET_TODAY.replace(year=DATASET_TODAY.year + 30)
    yy = (DATASET_TODAY.year + 15) % 100          # 20yy is future at the fixture date, past at `future`
    raw = f"01/01/{yy:02d}"
    monkeypatch.setattr(clock, "deployment_today", lambda now=None: future)
    on_clock = parse_dob_two_digit_year(raw)
    on_fixture = parse_dob_two_digit_year(raw, today=DATASET_TODAY)
    assert on_fixture.year == 1900 + yy
    assert on_clock.year == 2000 + yy
    assert on_clock != on_fixture


def test_run_intake_reads_the_clock_when_no_date_is_passed(monkeypatch):
    from databridge_intake.paths import chits_dir
    from databridge_intake.pipeline import run_intake

    calls: list[int] = []

    def pinned(now=None):
        calls.append(1)
        return DATASET_TODAY

    monkeypatch.setattr(clock, "deployment_today", pinned)
    path = sorted(chits_dir().glob("chit_0*.txt"))[0]
    run_intake(path.read_text(encoding="utf-8"), document_id=path.name)
    assert len(calls) >= 1

"""Cron correctness, checked against a brute-force oracle rather than written-down answers.

The oracle walks minute by minute in the schedule's timezone and applies the INTENT directly
("a weekday, at 09:00"), so the expectation is derived from what the schedule means, not recalled.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from databridge_automation.cron import CronError, CronSchedule

SGT = ZoneInfo("Asia/Singapore")


def oracle_next(after: datetime, tz: ZoneInfo, predicate, horizon_days: int = 40) -> datetime:
    t = after.astimezone(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(horizon_days * 24 * 60):
        if predicate(t.astimezone(tz)):
            return t
        t += timedelta(minutes=1)
    raise AssertionError("oracle found no firing")


def weekday_9(local: datetime) -> bool:
    return local.weekday() < 5 and local.hour == 9 and local.minute == 0


@pytest.mark.parametrize("start", [
    datetime(2026, 10, 2, 10, 0, tzinfo=SGT),       # Friday after the slot -> Monday
    datetime(2026, 10, 3, 12, 0, tzinfo=SGT),       # Saturday
    datetime(2026, 10, 5, 8, 59, tzinfo=SGT),       # Monday just before
    datetime(2026, 10, 5, 9, 0, tzinfo=SGT),        # exactly on a slot -> strictly after
    datetime(2026, 12, 31, 23, 30, tzinfo=SGT),     # year boundary
    datetime(2026, 3, 8, 1, 0, tzinfo=timezone.utc),  # a US DST day; Singapore has none
])
def test_weekday_nine_matches_oracle_in_singapore(start):
    cron = CronSchedule.parse("0 9 * * 1-5", "Asia/Singapore")
    got = cron.next_fire(start)
    assert got == oracle_next(start, SGT, weekday_9)
    local = got.astimezone(SGT)
    assert local.weekday() < 5 and (local.hour, local.minute) == (9, 0)
    # Singapore is UTC+8 all year: the UTC hour of a 09:00 slot never moves
    assert got.utcoffset() == timedelta(0) and got.hour == 1


def test_every_month_of_the_year_fires_at_the_same_local_time():
    cron = CronSchedule.parse("0 9 * * 1-5", "Asia/Singapore")
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    seen_hours = set()
    for _ in range(12):
        t = cron.next_fire(t) + timedelta(days=30)
        seen_hours.add(cron.next_fire(t).astimezone(SGT).hour)
    assert seen_hours == {9}


def test_ranges_steps_and_lists_agree_with_matches():
    cron = CronSchedule.parse("*/15 9-17 * * mon,wed-fri", "Asia/Singapore")
    t = datetime(2026, 10, 1, tzinfo=timezone.utc)
    fires = list(cron.fires_between(t, t + timedelta(days=7)))
    assert fires, "non-vacuous"
    for f in fires:
        local = f.astimezone(SGT)
        assert local.minute % 15 == 0 and 9 <= local.hour <= 17
        assert local.weekday() in {0, 2, 3, 4}
        assert cron.matches(f)
    # and the oracle count over the same week agrees
    expected = 0
    m = t
    while m < t + timedelta(days=7):
        m += timedelta(minutes=1)
        lo = m.astimezone(SGT)
        if lo.minute % 15 == 0 and 9 <= lo.hour <= 17 and lo.weekday() in {0, 2, 3, 4}:
            expected += 1
    assert len(fires) == expected


def test_day_of_month_or_day_of_week_when_both_restricted():
    cron = CronSchedule.parse("0 9 1 * 1", "UTC")
    t = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
    for f in cron.fires_between(t, t + timedelta(days=62)):
        assert f.day == 1 or f.weekday() == 0


def test_dst_gap_is_skipped_not_shifted():
    ny = ZoneInfo("America/New_York")
    cron = CronSchedule.parse("30 2 * * *", "America/New_York")
    got = cron.next_fire(datetime(2026, 3, 8, 0, 0, tzinfo=ny))   # 02:30 does not exist that day
    assert got.astimezone(ny).date().isoformat() == "2026-03-09"
    assert got.astimezone(ny).hour == 2


@pytest.mark.parametrize("expr", ["0 9 * *", "60 9 * * *", "0 24 * * *", "0 9 * * 8",
                                  "0 9 5-1 * *", "0 9 * * funday", "*/0 * * * *"])
def test_bad_cron_is_refused(expr):
    with pytest.raises(CronError):
        CronSchedule.parse(expr, "UTC")


def test_unknown_timezone_is_refused():
    with pytest.raises(CronError):
        CronSchedule.parse("0 9 * * *", "Mars/Olympus")


def test_describe_reads_as_english():
    assert "weekday" in CronSchedule.parse("0 9 * * 1-5", "Asia/Singapore").describe()
    assert "every hour" in CronSchedule.parse("0 * * * *", "UTC").describe()
    assert "Monday" in CronSchedule.parse("0 9 * * 1", "UTC").describe()


def test_naive_datetime_is_refused():
    with pytest.raises(CronError):
        CronSchedule.parse("0 9 * * *", "UTC").next_fire(datetime(2026, 10, 1))

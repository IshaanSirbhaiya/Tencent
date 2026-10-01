"""Five-field cron, timezone-aware, computed here (croniter is not a dependency of this repo).

    minute  hour  day-of-month  month  day-of-week
    0-59    0-23  1-31          1-12   0-7 (0 and 7 are Sunday)

Supported per field: ``*``, ``n``, ``a-b``, ``*/s``, ``a-b/s``, ``n/s``, comma lists, and
English three-letter names for months (``jan``) and weekdays (``mon``). Day-of-month and
day-of-week follow the classic Vixie rule: when BOTH are restricted, a day matches if EITHER does;
otherwise both must.

Times are evaluated in the schedule's IANA timezone. A local time that does not exist (a DST
spring-forward gap) is skipped rather than shifted; a repeated local time (fall-back) fires once,
on its first occurrence. Asia/Singapore has no DST, which is the deployment default.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterator, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_DAYS = {d: i for i, d in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}

#: Search horizon for the next fire: one full Gregorian cycle (400 years = 146,097 days), after
#: which the calendar repeats exactly — so a schedule that does not fire inside it NEVER fires.
#: (Five years was too short: `0 0 29 2 *` after 2096 next fires in 2104 — R-017 #15.) Months the
#: schedule excludes are skipped whole, so even a never-firing `31 2` costs ~12k day checks.
_HORIZON_DAYS = 146_097


def _next_month(d: date) -> date:
    return (d.replace(day=1) + timedelta(days=32)).replace(day=1)


class CronError(ValueError):
    """A cron expression that cannot be parsed. The message names the field and the value."""


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CronError(f"timezone {name!r} is not a known IANA timezone") from exc


def _value(token: str, lo: int, hi: int, names: Optional[dict[str, int]], field: str) -> int:
    t = token.strip().lower()
    if names and t in names:
        return names[t]
    if not t.isdigit():
        raise CronError(f"cron {field} value {token!r} is not a number"
                        + (f" or one of {sorted(names)}" if names else ""))
    v = int(t)
    if not lo <= v <= hi:
        raise CronError(f"cron {field} value {v} is outside {lo}-{hi}")
    return v


def _field(text: str, lo: int, hi: int, names: Optional[dict[str, int]], field: str
           ) -> tuple[frozenset[int], bool]:
    """Parse one field into the set of allowed values and whether it was ``*`` (unrestricted)."""
    out: set[int] = set()
    star = False
    for part in text.split(","):
        if not part:
            raise CronError(f"cron {field} has an empty list item in {text!r}")
        step = 1
        if "/" in part:
            base, _, s = part.partition("/")
            if not s.isdigit() or int(s) < 1:
                raise CronError(f"cron {field} step {s!r} must be a positive integer")
            step = int(s)
        else:
            base = part
        if base == "*":
            start, end = lo, hi
            if step == 1:
                star = True
        elif "-" in base:
            a, _, b = base.partition("-")
            start, end = _value(a, lo, hi, names, field), _value(b, lo, hi, names, field)
            if start > end:
                raise CronError(f"cron {field} range {base!r} runs backwards")
        else:
            start = _value(base, lo, hi, names, field)
            end = hi if "/" in part else start
        out.update(range(start, end + 1, step))
    return frozenset(out), star and len(text.split(",")) == 1


@dataclass(frozen=True)
class CronSchedule:
    expr: str
    timezone: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]          # 0 = Sunday .. 6 = Saturday (7 folded to 0)
    dom_star: bool
    dow_star: bool

    @classmethod
    def parse(cls, expr: str, timezone_name: str = "UTC") -> "CronSchedule":
        parts = expr.split()
        if len(parts) != 5:
            raise CronError(f"cron {expr!r} must have five fields (min hour day month weekday)")
        zone(timezone_name)
        minutes, _ = _field(parts[0], 0, 59, None, "minute")
        hours, _ = _field(parts[1], 0, 23, None, "hour")
        days, dom_star = _field(parts[2], 1, 31, None, "day-of-month")
        months, _ = _field(parts[3], 1, 12, _MONTHS, "month")
        dows, dow_star = _field(parts[4], 0, 7, _DAYS, "day-of-week")
        dows = frozenset(0 if d == 7 else d for d in dows)
        return cls(expr=expr, timezone=timezone_name, minutes=minutes, hours=hours, days=days,
                   months=months, weekdays=dows, dom_star=dom_star, dow_star=dow_star)

    # -- matching -------------------------------------------------------------------------------
    def _day_matches(self, d: date) -> bool:
        if d.month not in self.months:
            return False
        dom_ok = d.day in self.days
        dow_ok = ((d.weekday() + 1) % 7) in self.weekdays      # Python Mon=0 -> cron Mon=1
        if not self.dom_star and not self.dow_star:
            return dom_ok or dow_ok
        return dom_ok and dow_ok

    def matches(self, when: datetime) -> bool:
        """Whether the minute containing ``when`` (any tz-aware instant) is a firing minute."""
        local = _aware(when).astimezone(zone(self.timezone))
        if local.fold == 1 and local.replace(fold=0).utcoffset() != local.utcoffset():
            return False            # the REPEAT of a fall-back minute: first occurrence only
        return (local.minute in self.minutes and local.hour in self.hours
                and self._day_matches(local.date()))

    def _day_instants(self, day: date, tz: ZoneInfo) -> Iterator[datetime]:
        """Every firing instant on local ``day`` (UTC, ascending): DST-gap times skipped, a
        repeated time taken once (its first occurrence)."""
        for h in sorted(self.hours):
            for m in sorted(self.minutes):
                naive = datetime(day.year, day.month, day.day, h, m)
                cand = naive.replace(tzinfo=tz, fold=0)
                back = cand.astimezone(timezone.utc).astimezone(tz)
                if back.replace(tzinfo=None) != naive:
                    continue                                    # does not exist (DST gap)
                yield cand.astimezone(timezone.utc)

    def count_between(self, start: datetime, end: datetime) -> int:
        """The exact number of firing instants in ``(start, end]`` — by day, not by instant, so a
        year of an every-minute schedule is ~365 steps. Used to account for a long outage
        exactly rather than truncating it (R-017 #14)."""
        tz = zone(self.timezone)
        s_utc, e_utc = _aware(start).astimezone(timezone.utc), _aware(end).astimezone(timezone.utc)
        if e_utc <= s_utc:
            return 0
        first, last = s_utc.astimezone(tz).date(), e_utc.astimezone(tz).date()
        per_day = len(self.hours) * len(self.minutes)
        count, day = 0, first
        while day <= last:
            if day.month not in self.months:
                day = _next_month(day)
                continue
            if self._day_matches(day):
                nxt = day + timedelta(days=1)
                steady = (datetime(day.year, day.month, day.day, tzinfo=tz).utcoffset()
                          == datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz).utcoffset())
                if first < day < last and steady:
                    count += per_day                            # whole day inside, no DST change
                else:
                    count += sum(1 for t in self._day_instants(day, tz) if s_utc < t <= e_utc)
            day = day + timedelta(days=1)
        return count

    def next_fire(self, after: datetime) -> Optional[datetime]:
        """The first firing instant strictly after ``after``, as an aware UTC datetime."""
        tz = zone(self.timezone)
        after_utc = _aware(after).astimezone(timezone.utc)
        local = after_utc.astimezone(tz)
        day = local.date()
        try:
            stop = day + timedelta(days=_HORIZON_DAYS)
        except OverflowError:                                   # pragma: no cover - year 9999
            stop = date.max
        while day <= stop:
            if day.month not in self.months:
                try:
                    day = _next_month(day)
                except (OverflowError, ValueError):             # pragma: no cover - year 9999
                    return None
                continue
            if self._day_matches(day):
                for cand_utc in self._day_instants(day, tz):
                    if cand_utc > after_utc:
                        return cand_utc
            try:
                day = day + timedelta(days=1)
            except OverflowError:                               # pragma: no cover - year 9999
                return None
        return None

    def fires_between(self, start: datetime, end: datetime, *, limit: int = 10_000
                      ) -> Iterator[datetime]:
        """Every firing instant in ``(start, end]``, oldest first, at most ``limit``."""
        cur = start
        for _ in range(limit):
            nxt = self.next_fire(cur)
            if nxt is None or nxt > _aware(end):
                return
            yield nxt
            cur = nxt

    # -- English --------------------------------------------------------------------------------
    def describe(self) -> str:
        """A plain-English rendering for the "here is what I understood" summary."""
        return describe_cron(self)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise CronError("cron evaluation needs a timezone-aware datetime")
    return dt


_DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def _weekday_phrase(days: frozenset[int]) -> str:
    s = sorted(days)
    if s == [1, 2, 3, 4, 5]:
        return "every weekday (Monday to Friday)"
    if s == [0, 6]:
        return "every weekend day"
    if len(s) == 7:
        return "every day"
    names = [_DAY_NAMES[d] for d in s]
    return "every " + (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1])


def describe_cron(c: CronSchedule) -> str:
    mins, hours = sorted(c.minutes), sorted(c.hours)
    if len(mins) == 60 and len(hours) == 24:
        when = "every minute"
    elif len(hours) == 24 and len(mins) == 1:
        when = "every hour" + (f" at :{mins[0]:02d}" if mins[0] else "")
    elif len(hours) == 24 and len(mins) > 1:
        step = mins[1] - mins[0]
        when = f"every {step} minutes" if mins == list(range(0, 60, step)) else \
            f"at minutes {', '.join(map(str, mins))} of every hour"
    elif len(mins) == 1 and len(hours) > 1 and hours == list(range(hours[0], 24, hours[1] - hours[0])) \
            and hours[0] == 0:
        when = f"every {hours[1] - hours[0]} hours" + (f" at :{mins[0]:02d}" if mins[0] else "")
    else:
        times = [f"{h:02d}:{m:02d}" for h in hours for m in mins]
        when = "at " + (times[0] if len(times) == 1 else ", ".join(times))
    if c.dom_star and c.dow_star and len(c.months) == 12 and len(hours) == 24:
        return f"{when} ({c.timezone})"
    if c.dom_star and c.dow_star:
        days = "every day"
    elif c.dom_star:
        days = _weekday_phrase(c.weekdays)
    elif c.dow_star:
        days = "on day " + ", ".join(map(str, sorted(c.days))) + " of the month"
    else:
        days = ("on day " + ", ".join(map(str, sorted(c.days))) + " of the month or "
                + _weekday_phrase(c.weekdays))
    if len(c.months) != 12:
        month_names = [k.capitalize() for k, v in sorted(_MONTHS.items(), key=lambda kv: kv[1])
                       if v in c.months]
        days += " in " + ", ".join(month_names)
    return f"{days}, {when} ({c.timezone})"


__all__ = ["CronError", "CronSchedule", "describe_cron", "zone"]

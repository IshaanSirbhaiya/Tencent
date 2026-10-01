"""Read a Python-repr row list safely — what `postgres-mcp` returns from `execute_sql`.

`postgres-mcp` answers `str(list_of_row_dicts)`, so a value arrives as the Python constructor that
would rebuild it::

    [{'invoice_no': 'INV-2026-0041', 'issued_on': datetime.date(2026, 8, 31),
      'net_gbp': Decimal('18500.00'), 'paid_at': datetime.datetime(2026, 9, 30, 18, 28, 45,
      927051, tzinfo=zoneinfo.ZoneInfo(key='Etc/UTC')), 'note': None}]

A regex that unwraps `Decimal(...)` breaks on nested calls (a timezone inside a datetime) and —
worse — rewrites text INSIDE a string value that happens to contain `Decimal(`. This module parses
the text with `ast` and evaluates ONLY literals plus a closed whitelist of constructors, so a string
value is never touched and nothing is ever executed.

Two output modes:

* `typed=True`  — Decimal, date, datetime, time, timedelta, UUID objects (for the facts store,
  where a number must stay a number and a date must sort as a date);
* `typed=False` — the JSON-safe canonical text of each value: `Decimal('18500.00')` → `"18500.00"`
  (exactly the digits the server wrote — never re-rounded through a float), dates and datetimes →
  ISO 8601, a timedelta → ISO 8601 duration, a UUID → its string. This is what gets captured.
"""
from __future__ import annotations

import ast
import datetime as dt
import ipaddress
import json
import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

__all__ = ["loads", "loads_rows", "to_text", "ReprParseError", "NamedZone"]


class ReprParseError(ValueError):
    """The text is neither JSON nor a safe Python literal."""


class NamedZone(dt.tzinfo):
    """A zone known only by name (no tz database on this host). Its offset is UNKNOWN, so it is
    never invented: the value's text is the wall time plus `[zone]` (RFC 9557 suffix form)."""

    def __init__(self, key: str) -> None:
        self.key = key

    def utcoffset(self, _dt):
        return None

    def dst(self, _dt):
        return None

    def tzname(self, _dt):
        return self.key

    def __repr__(self) -> str:
        return f"NamedZone({self.key!r})"


def _zone(key: str) -> dt.tzinfo:
    if key.upper() in ("UTC", "ETC/UTC", "Z", "GMT", "ETC/GMT", "UCT", "ETC/UCT"):
        return dt.timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(key)
    except Exception:                                              # noqa: BLE001
        return NamedZone(key)


def _timedelta(*args, **kw) -> dt.timedelta:
    names = ("days", "seconds", "microseconds", "milliseconds", "minutes", "hours", "weeks")
    return dt.timedelta(**{**dict(zip(names, args)), **kw})


#: The only callables the evaluator will apply — each maps a dotted name to a constructor.
_CALLS = {
    "Decimal": lambda v="0": Decimal(str(v)),
    "decimal.Decimal": lambda v="0": Decimal(str(v)),
    "datetime.date": dt.date,
    "date": dt.date,
    "datetime.datetime": dt.datetime,
    "datetime": dt.datetime,
    "datetime.time": dt.time,
    "time": dt.time,
    "datetime.timedelta": _timedelta,
    "timedelta": _timedelta,
    "datetime.timezone": lambda off=dt.timedelta(0), name=None: (
        dt.timezone(off, name) if name else dt.timezone(off)),
    "timezone": lambda off=dt.timedelta(0), name=None: (
        dt.timezone(off, name) if name else dt.timezone(off)),
    "zoneinfo.ZoneInfo": lambda key="UTC": _zone(key),
    "ZoneInfo": lambda key="UTC": _zone(key),
    "UUID": lambda v: uuid.UUID(str(v)),
    "uuid.UUID": lambda v: uuid.UUID(str(v)),
    "IPv4Address": ipaddress.IPv4Address,
    "IPv6Address": ipaddress.IPv6Address,
    "IPv4Network": ipaddress.IPv4Network,
    "IPv6Network": ipaddress.IPv6Network,
    "IPv4Interface": ipaddress.IPv4Interface,
    "IPv6Interface": ipaddress.IPv6Interface,
    "float": float,
    "frozenset": lambda v=(): list(v),
    "set": lambda v=(): list(v),
}
_ATTRS = {"datetime.timezone.utc": dt.timezone.utc, "timezone.utc": dt.timezone.utc}

#: Opaque reprs no literal can express (`<memory at 0x…>` for bytea). Replaced by None and
#: counted — never guessed.
_OPAQUE = re.compile(r"<[A-Za-z_][\w.]* (?:at|object at) 0x[0-9a-fA-F]+>")


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    raise ReprParseError(f"unsupported callee {ast.dump(node)[:80]}")


def _eval(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_eval(e) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return [_eval(e) for e in node.elts]
    if isinstance(node, ast.Set):
        return [_eval(e) for e in node.elts]
    if isinstance(node, ast.Dict):
        out = {}
        for k, v in zip(node.keys, node.values):
            if k is None:
                raise ReprParseError("dict unpacking is not a literal")
            key = _eval(k)
            out[key if isinstance(key, (str, int, float, bool)) or key is None else str(key)] = \
                _eval(v)
        return out
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        val = _eval(node.operand)
        if isinstance(val, (int, float, Decimal)):
            return -val if isinstance(node.op, ast.USub) else val
        if isinstance(val, dt.timedelta):
            return -val if isinstance(node.op, ast.USub) else val
        raise ReprParseError("unary sign on a non-number")
    if isinstance(node, ast.Attribute):
        name = _dotted(node)
        if name in _ATTRS:
            return _ATTRS[name]
        raise ReprParseError(f"attribute {name!r} is not an allowed literal")
    if isinstance(node, ast.Call):
        name = _dotted(node.func)
        fn = _CALLS.get(name)
        if fn is None:
            raise ReprParseError(f"constructor {name!r} is not on the allowed list")
        args = [_eval(a) for a in node.args]
        kwargs = {kw.arg: _eval(kw.value) for kw in node.keywords if kw.arg}
        try:
            return fn(*args, **kwargs)
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise ReprParseError(f"{name}(...) could not be rebuilt: {exc}") from exc
    raise ReprParseError(f"not a literal: {type(node).__name__}")


def _iso_duration(td: dt.timedelta) -> str:
    total = td.total_seconds()
    sign = "-" if total < 0 else ""
    td = abs(td)
    days, secs, micros = td.days, td.seconds, td.microseconds
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    sec = f"{s}.{micros:06d}".rstrip("0").rstrip(".") if micros else str(s)
    time_part = "".join(f"{v}{u}" for v, u in ((h, "H"), (m, "M")) if v) + \
        (f"{sec}S" if (s or micros) else "")
    out = f"{sign}P{days}D" if days else f"{sign}P"
    if time_part:
        out += "T" + time_part
    return out if out not in ("P", "-P") else "PT0S"


def to_text(value: Any) -> Any:
    """The JSON-safe canonical form of one parsed value (recursively for containers)."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) \
            else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dt.datetime):
        if isinstance(value.tzinfo, NamedZone):
            return f"{value.replace(tzinfo=None).isoformat()}[{value.tzinfo.key}]"
        return value.isoformat()
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return _iso_duration(value)
    if isinstance(value, dict):
        return {str(k): to_text(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_text(v) for v in value]
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def loads(text: str, *, typed: bool = False, stats: dict | None = None) -> Any:
    """JSON first; else a safe Python literal. `typed` keeps Decimal/date objects.

    `stats` (optional dict): `stats["opaque"]` is increased by the number of opaque reprs
    (`<memory at 0x…>`) replaced by None — counted so the caller can record the loss (R-022 f1
    #7), never silently."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    cleaned, n_opaque = _OPAQUE.subn("None", text)
    if stats is not None:
        stats["opaque"] = int(stats.get("opaque", 0)) + n_opaque
    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise ReprParseError(f"output is neither JSON nor a Python literal: {text[:120]!r}") \
            from exc
    value = _eval(tree.body)
    return value if typed else to_text(value)


def loads_rows(text: str, *, typed: bool = False, stats: dict | None = None) -> list[dict]:
    """A list of row dicts. A single dict becomes one row; an empty answer is no rows. An element
    that is not a row (R-022 f1 #7) is an ERROR, never silently dropped."""
    data = loads(text, typed=typed, stats=stats)
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    if not isinstance(data, list):
        raise ReprParseError(f"expected a list of rows, got {type(data).__name__}")
    bad = [type(r).__name__ for r in data if not isinstance(r, dict)]
    if bad:
        raise ReprParseError(f"{len(bad)} of {len(data)} result element(s) are not rows "
                             f"({sorted(set(bad))}); refused rather than dropped")
    return data

"""Derived values an agent may state or write (E2E run 2, N-06 / N-13): the derived-pointer rule.

A value that is not IN the evidence but is COMPUTED from it — a sum, a difference, a count, the days
between two dates, a date plus N days, a file name joined from an invoice number — is grounded only
when its derivation is RECORDED and REPRODUCIBLE: the planner declares ``{value, rule, inputs,
args}``, every input is found in the cited evidence (or the run context), and the rule, replayed
over those inputs, gives exactly the declared value. The accepted value is then cited with a
``derived`` `SourcePointer` whose ``derived_from`` are the evidence pointers its inputs came from
and whose ``derivation`` is the canonical call (``days_between@v1()``).

The rules live in the SAME closed, versioned registry every other derived pointer replays against
(`databridge_adapter.grounding.register_derivation` / `replay_derivation`); nothing here keeps a
second registry. A rule is a pure function of its inputs — no clock, no model, no I/O — so the
same pointer replays to the same value forever. Nothing is guessed: no rule is searched for, no
input is inferred. An invented value has no declared derivation that replays, so it still refuses.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional, Sequence

from databridge_adapter.grounding import (
    DerivationError,
    parse_derivation,
    register_derivation,
    replay_derivation,
)

#: The rules a planner may declare, with what each takes. Shown to the planner verbatim.
RULES: dict[str, str] = {
    "sum": "the sum of two or more numbers",
    "difference": "the first number minus the second",
    "count": "how many inputs are listed",
    "days_between": "whole days between two ISO dates (unsigned)",
    "add_days": "an ISO date plus args.days days (negative goes back)",
    "concat": "the inputs joined by args.sep: one of - _ / . : space (default: nothing)",
}
VERSION = 1

#: The rules whose inputs are DATES (E2E run 4 Q-08). Only these may take the RUN'S OWN DATE (the
#: clock the runtime gave the planner, captured as evidence of the run) in place of a cited value:
#: "the run date plus the 30 days the instruction states" is a window bound the run computed, not
#: a fact about the data. A number rule never may — a sum of the date's digits grounds nothing.
CLOCK_RULES = frozenset({"days_between", "add_days"})

#: Separators `concat` may join with. Written as these words/characters because a derivation call's
#: argument values are stripped when parsed (a bare space would vanish).
_SEPS = {"": "", "-": "-", "_": "_", "/": "/", ".": ".", ":": ":", "space": " "}


def _num(v: Any) -> Decimal:
    if isinstance(v, bool):
        raise DerivationError("a boolean is not a number")
    try:
        return Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise DerivationError(f"{v!r} is not a number") from None


def _date(v: Any) -> dt.date:
    s = str(v).strip()
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        raise DerivationError(f"{v!r} is not an ISO date (YYYY-MM-DD)") from None


def _plain(d: Decimal) -> str:
    d = d.normalize()
    return format(Decimal(0) if d == 0 else d, "f")


def _sum_v1(parents: Sequence[Any], args: Mapping[str, str]) -> str:
    if len(parents) < 2:
        raise DerivationError("sum@v1 adds two or more numbers")
    return _plain(sum((_num(p) for p in parents), Decimal(0)))


def _difference_v1(parents: Sequence[Any], args: Mapping[str, str]) -> str:
    if len(parents) != 2:
        raise DerivationError("difference@v1 takes exactly two numbers")
    return _plain(_num(parents[0]) - _num(parents[1]))


def _count_v1(parents: Sequence[Any], args: Mapping[str, str]) -> str:
    if not parents:
        raise DerivationError("count@v1 counts at least one input")
    return str(len(parents))


def _days_between_v1(parents: Sequence[Any], args: Mapping[str, str]) -> str:
    if len(parents) != 2:
        raise DerivationError("days_between@v1 takes exactly two dates")
    return str(abs((_date(parents[1]) - _date(parents[0])).days))


def _add_days_v1(parents: Sequence[Any], args: Mapping[str, str]) -> str:
    if len(parents) != 1:
        raise DerivationError("add_days@v1 takes exactly one date")
    n = _num(args.get("days", ""))
    if n != n.to_integral_value():
        raise DerivationError("add_days@v1 needs a whole number of days")
    return (_date(parents[0]) + dt.timedelta(days=int(n))).isoformat()


def _concat_v1(parents: Sequence[Any], args: Mapping[str, str]) -> str:
    sep = str(args.get("sep", "")).strip()
    if sep not in _SEPS:
        raise DerivationError("concat@v1 joins with one of: - _ / . : space")
    sep = _SEPS[sep]
    if len(parents) < 2:
        raise DerivationError("concat@v1 joins two or more inputs")
    return sep.join(str(p) for p in parents)


_FNS = {"sum": _sum_v1, "difference": _difference_v1, "count": _count_v1,
        "days_between": _days_between_v1, "add_days": _add_days_v1, "concat": _concat_v1}
for _name, _fn in _FNS.items():
    register_derivation(_name, VERSION, _fn, doc=RULES[_name])


def canonical(rule: str, args: Optional[Mapping[str, Any]] = None) -> str:
    """The canonical derivation call (``days_between@v1()``), as a `derived` pointer records it."""
    body = ",".join(f"{k}={args[k]}" for k in sorted(args or {}))
    return f"{rule}@v{VERSION}({body})"


def replay(rule: str, inputs: Sequence[Any], args: Optional[Mapping[str, Any]] = None) -> str:
    """Replay ``rule`` over ``inputs`` through the shared registry. `DerivationError` if the rule
    is not one of `RULES`, the call does not parse, or the rule refuses its inputs."""
    if rule not in RULES:
        raise DerivationError(f"{rule!r} is not a derivation rule; use one of "
                              f"{', '.join(sorted(RULES))}")
    for k, v in (args or {}).items():
        if any(ch in str(v) for ch in "(),=") or any(ch in str(k) for ch in "(),= "):
            raise DerivationError(f"argument {k}={v!r} cannot be written in a derivation call")
    call = parse_derivation(canonical(rule, {k: str(v) for k, v in (args or {}).items()}))
    return str(replay_derivation(call, list(inputs)))


def same_value(a: Any, b: Any) -> bool:
    """Whether a declared value equals a replayed one: numbers by VALUE, dates by date, otherwise
    exactly (surrounding whitespace aside)."""
    sa, sb = str(a).strip(), str(b).strip()
    if sa == sb:
        return True
    try:
        return _num(sa) == _num(sb)
    except DerivationError:
        pass
    try:
        return len(sa) >= 10 and len(sb) >= 10 and _date(sa) == _date(sb) and \
            sa[10:].strip() == sb[10:].strip()
    except DerivationError:
        return False


__all__ = ["RULES", "VERSION", "CLOCK_RULES", "DerivationError", "canonical", "replay", "same_value"]

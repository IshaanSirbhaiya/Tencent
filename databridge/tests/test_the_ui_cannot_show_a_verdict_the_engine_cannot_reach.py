"""The frontend may not name a verdict the engine has no state for.

The eight-state verdict lattice is one of the load-bearing claims in this system: verification is
not a boolean, and an adapter may only report a verdict it can actually compute. A UI that renders
a ninth state quietly falsifies that claim to the only audience that matters — the person reading
the screen — and it does so in a file no Python test was watching.

This was not hypothetical. `apps/frontend/config/platform-preview.json` shipped `AS_OF_CAPTURE`
and `DRIFT_IMMATERIAL`, neither of which is a member of `Verdict`. The first was a truncation of
`VERIFIED_AS_OF_CAPTURE`. The second was worse than a typo: the engine's term is `DRIFT_BENIGN`,
meaning *something moved and it was not what we cited*, whereas "immaterial" asserts a judgement
about significance that the engine deliberately never makes. Both were found on 2026-08-16 by an
agent auditing figures for a document pack, not by the test suite — which is why this file exists.

The check is deliberately generic: it reads whatever verdict strings the config carries and asks
the enum whether it knows them. It does not carry its own list of the eight, because a second list
is a second thing to drift.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from databridge_adapter.verdicts import Verdict

#: databridge/tests/ -> databridge/
DATABRIDGE_ROOT = Path(__file__).resolve().parents[1]
PREVIEW = DATABRIDGE_ROOT / "apps" / "frontend" / "config" / "platform-preview.json"


def _verdict_strings(node, found: list[tuple[str, str]], path: str = "") -> list[tuple[str, str]]:
    """Every value under a `"verdict"` key, at any depth, paired with the path that reached it.

    The path matters because the JSON key `"verdict"` is **overloaded across two closed
    vocabularies**, which this test learned the hard way on its first run. Pointer verification
    answers with `Verdict` (the eight-state lattice). The Agent Builder answers with `Outcome`
    (`compiled` / `REFUSED`) — a *compilation* decision about a declaration, not a statement about
    a cited value. They are different questions and must not be collapsed into one enum; keeping
    the path lets this test hold each section to its own vocabulary instead of weakening to a
    union, which would let a genuine drift hide behind the other vocabulary's membership.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            if key == "verdict" and isinstance(value, str):
                found.append((here, value))
            else:
                _verdict_strings(value, found, here)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _verdict_strings(item, found, f"{path}[{i}]")
    return found


def _builder_outcomes() -> set[str]:
    """The Agent Builder's own closed set, or empty if the compiler is not importable.

    Empty is the safe direction: it makes the test STRICTER (a builder outcome would then have to
    be a `Verdict`, and would fail), never more permissive.
    """
    try:
        from databridge_agentdef.compiler import Outcome
    except Exception:
        return set()
    return {o.name for o in Outcome} | {o.value for o in Outcome}


@pytest.mark.skipif(not PREVIEW.exists(), reason="frontend preview fixture is not present")
def test_every_verdict_the_preview_names_is_a_member_of_the_lattice():
    named = _verdict_strings(json.loads(PREVIEW.read_text(encoding="utf-8")), [])
    assert named, "the preview names no verdicts at all — this test would pass vacuously"

    lattice = {v.name for v in Verdict} | {v.value for v in Verdict}
    outcomes = _builder_outcomes()

    unknown = []
    for path, value in named:
        allowed = outcomes if path.startswith("builder") else lattice
        if value not in allowed:
            unknown.append(f"{path} = {value!r}")

    assert not unknown, (
        f"{PREVIEW.name} renders states the engine cannot produce: "
        + "; ".join(sorted(unknown))
        + f". The verification lattice is {sorted(v.name for v in Verdict)}. A UI state with "
        f"no engine state behind it is a claim nothing can substantiate - rename it, or add "
        f"the state to the lattice deliberately, but do not let the two vocabularies drift.")


@pytest.mark.skipif(not PREVIEW.exists(), reason="frontend preview fixture is not present")
def test_the_two_verdicts_that_drifted_do_not_come_back():
    """A named regression for the exact pair that shipped, so a copy-paste cannot reintroduce them.

    `DRIFT_IMMATERIAL` is the one to watch: it reads like a synonym for `DRIFT_BENIGN` and is not
    one. Benign says *nothing we cited moved*. Immaterial says *what moved did not matter*, which
    is a judgement about significance, and this system's whole position is that it reports what
    changed and lets a human decide whether it mattered.
    """
    raw = PREVIEW.read_text(encoding="utf-8")
    for gone in ('"AS_OF_CAPTURE"', '"DRIFT_IMMATERIAL"'):
        assert gone not in raw, (
            f"{gone} is back in {PREVIEW.name}. It is not a member of Verdict; the engine's terms "
            f"are VERIFIED_AS_OF_CAPTURE and DRIFT_BENIGN.")

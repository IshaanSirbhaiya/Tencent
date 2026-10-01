"""The registered, replayable derivation rules (`ADAPTER_AGENT_SPEC` §4a, review R-3A finding 2).

§4a's rule is that if the emitted bytes differ from the cited bytes, the pointer kind is `derived`
or the value is ungrounded. Review R-3A pointed out that the first half of that had no teeth: a
`derived` pointer passed the wall on the strength of a non-blank derivation string, so
``derivation="redact"`` beside any value at all was accepted.

A derivation is therefore a **call into this registry**, and `grounding.assert_grounded` replays
it. The rules here are pure, deterministic, versioned functions over the cited parent values -
no I/O, no clock, no model, nothing that could make the same inputs produce two answers.

## Why the PII patterns are duplicated from `databridge_ingest`

`packages/ingest/databridge_ingest/postgres.py` already redacts PII before anything leaves the
connector, and its patterns are the ones this rule must reproduce byte-for-byte - the whole point
is that the two layers redact identically. Importing it would drag SQLAlchemy into a package whose
top level is a driver-free contract, so the patterns are restated here and
`test_the_redaction_rule_matches_the_ingest_connector` imports the connector to assert the two
lists have not drifted. Duplication guarded by a test, rather than a layering violation guarded by
a comment.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .grounding import DerivationError, register_derivation

#: Mirrors ``databridge_ingest.postgres._PII``. Guarded by a test; do not edit one without the
#: other. Ordered, because the first match wins and the order decides the emitted label.
PII_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("email", re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")),
    ("phone", re.compile(r"^\+?\d[\d\s().-]{6,}$")),
    ("national_id", re.compile(r"^(\d{3}-\d{2}-\d{4}|[A-Z]\d{7}[A-Z])$")),
)

#: Column-name hints that redact regardless of whether a sample matched a pattern. Mirrors the
#: connector's ``_redact`` name-based rule, which exists because a value can slip a regex while
#: the column name says exactly what it holds.
PII_NAME_HINTS: tuple[str, ...] = ("email", "ssn", "nric", "phone")


def redact_pii(value: Any, column: str) -> Any:
    """The redaction itself: deterministic, total, and identical to the connector's.

    Non-strings pass through unchanged - a `DATE` or a `NUMERIC` has no PII pattern to match, and
    inventing a redacted form for one would emit a value whose type the source never held.

    Returns the value unchanged when nothing matched, which is what lets the Core decide between a
    verbatim pointer and a `derived` one by comparing the two.
    """
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    for kind, pattern in PII_PATTERNS:
        if pattern.match(stripped):
            return f"<REDACTED:{kind}>"
    name = column.lower()
    for hint in PII_NAME_HINTS:
        if hint in name:
            return f"<REDACTED:{name}>"
    return value


def _redact_pii_v1(parents: Sequence[Any], args: Mapping[str, str]) -> Any:
    column = args.get("column")
    if not column:
        raise DerivationError(
            "redact_pii@v1 requires a `column` argument: the connector's name-based rule redacts "
            "an `email`/`phone`/`nric` column even when the value slips the pattern, so the rule "
            "cannot be replayed without knowing which column it ran on")
    if len(parents) != 1:
        raise DerivationError(
            f"redact_pii@v1 transforms exactly one cell, got {len(parents)} parent value(s)")
    return redact_pii(parents[0], column)


register_derivation(
    "redact_pii", 1, _redact_pii_v1,
    doc="Replace a cell whose content matches an RFC5322 address, a phone number or a national "
        "identifier - or whose column name says it holds one - with a typed <REDACTED:kind> "
        "marker. Mirrors databridge_ingest.postgres._redact.")


def _identity_v1(parents: Sequence[Any], args: Mapping[str, str]) -> Any:
    """The null derivation. Registered because a Core that emits it is stating something true and
    checkable - "this value passed through unchanged" - which is better than a caller inventing an
    unregistered name for the same idea."""
    if len(parents) != 1:
        raise DerivationError(
            f"identity@v1 takes exactly one parent value, got {len(parents)}")
    return parents[0]


register_derivation("identity", 1, _identity_v1,
                    doc="Emit the cited parent value unchanged.")

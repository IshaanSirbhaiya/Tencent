"""The port: **five verbs** (`ADAPTER_AGENT_SPEC` §3).

R02 and R03 converged on the same five independently, which is the strongest signal in the
research that this is the right decomposition. Implementations are per-source; the orchestrator
knows only this Protocol and selects an adapter by ``source_id`` from the graph - never by asking
a model to choose among sixty tools (§9a: tool-selection accuracy degrades past 30-50 tools, which
a fleet reaches at about a dozen sources).

Why `snapshot()` takes the whole request and not a bare timestamp: ``schema_hash`` covers the read
PROJECTION ONLY (spine D6), and a method that never sees the projection cannot compute it. For a
T0 source it must additionally open the same transaction the read will use - a snapshot
established on a different connection identifies nothing (§7).
"""
from __future__ import annotations

import inspect
import re
from typing import Protocol, runtime_checkable

from databridge_core import SnapshotIdentity, SourcePointer

from .contract import CapabilityRequest
from .describe import PinningTier, SourceDescription
from .results import AdapterResult, ReadPlan
from .verdicts import Verdict

#: The five verbs, in the order the spec lists them. Used by `assert_conforms` and by the
#: tool-definition budget gate (§9a) so that "how many verbs does a fleet expose?" has one answer.
VERBS: tuple[str, ...] = ("describe", "snapshot", "plan", "resolve", "verify")


@runtime_checkable
class Adapter(Protocol):
    """The port. Five verbs, one source, no natural language anywhere on the boundary."""

    source_id: str
    capabilities: frozenset[str]        # closed set, advertised, screened at registration
    pinning_tier: PinningTier

    def describe(self) -> SourceDescription:
        """Schema, capability matrix, retention reality, egress class. **No data.**"""
        ...

    def snapshot(self, req: CapabilityRequest) -> SnapshotIdentity:
        """Pin a point in time. Tier-dependent; raises `CannotPinPast` if the tier cannot honour
        ``req.as_of``. Takes the whole request because `schema_hash` covers the projection."""
        ...

    def plan(self, req: CapabilityRequest) -> ReadPlan:
        """Cost/row estimate plus the exact operations. Deterministic. No I/O beyond metadata."""
        ...

    def resolve(self, req: CapabilityRequest) -> AdapterResult:
        """Execute. Returns `GroundedRows`: every cell carries a `SourcePointer`."""
        ...

    def verify(self, pointer: SourcePointer) -> Verdict:
        """Is this pointer still good? Returns the LATTICE, never a boolean (spine D6)."""
        ...


#: Expected parameter names per verb, checked by `assert_conforms`.
_SIGNATURES: dict[str, tuple[str, ...]] = {
    "describe": (),
    "snapshot": ("req",),
    "plan": ("req",),
    "resolve": ("req",),
    "verify": ("pointer",),
}


#: Same shape the contract accepts for a capability name (`contract._IDENT`), restated here so the
#: port does not import the contract's private regex.
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


class NotAnAdapter(TypeError):
    """A class claims the port and does not implement it. Registration-time failure, by design."""


def assert_conforms(candidate: object) -> None:
    """Check an implementation against the port properly.

    ``isinstance(x, Adapter)`` is available because the Protocol is ``runtime_checkable``, but be
    precise about what that buys: a runtime-checkable Protocol checks only that the ATTRIBUTES
    EXIST. An object with ``resolve = None``, or with ``def resolve(self, sql: str)``, passes it.
    That is not enough for a boundary whose whole purpose is that the orchestrator never learns
    what kind of source it is talking to, so registration uses this instead.

    Checked: the five verbs exist and are callable, their first non-self parameter is named as the
    port names it, and the three required attributes are present with plausible types.
    """
    missing = [v for v in VERBS if not callable(getattr(candidate, v, None))]
    if missing:
        raise NotAnAdapter(
            f"{type(candidate).__name__} is missing callable verb(s): {missing}. The port is five "
            f"verbs; a partial implementation would force the orchestrator to know which source "
            f"it is talking to, which is the one thing the port exists to prevent")

    # R-3A finding 11: checking a PREFIX of the positional names let unusable adapters register.
    # `describe(required_argument)` passed because the expected tuple is empty;
    # `resolve(req, another_required)` passed because only the first name was compared; a required
    # KEYWORD-ONLY argument was invisible. None of those verbs is callable through the port, which
    # is the only thing this function exists to establish - so the whole signature is checked and
    # the test is "can the orchestrator call this with exactly the port's arguments".
    for verb, expected in _SIGNATURES.items():
        bound = getattr(candidate, verb)
        sig = inspect.signature(bound)
        params = [p for p in sig.parameters.values() if p.name != "self"]
        positional = [p for p in params
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        names = tuple(p.name for p in positional)
        if names != expected:
            extra = [n for n in names[len(expected):]]
            raise NotAnAdapter(
                f"{type(candidate).__name__}.{verb}{sig} does not match the port: expected "
                f"positional parameter(s) {expected or '()'}, got {names or '()'}"
                + (f" - the orchestrator has nothing to pass for {extra}, so this verb is not "
                   f"callable through the port" if extra else "")
                + ". A verb that takes something else takes something the contract cannot hash")
        required_kwonly = sorted(p.name for p in params
                                 if p.kind is p.KEYWORD_ONLY and p.default is p.empty)
        if required_kwonly:
            raise NotAnAdapter(
                f"{type(candidate).__name__}.{verb}{sig} requires keyword-only argument(s) "
                f"{required_kwonly}. The orchestrator calls the port and nothing else, so a "
                f"required argument it does not know about makes the verb uncallable; give it a "
                f"default if the adapter needs it internally")

    for attr, kind in (("source_id", str), ("capabilities", frozenset), ("pinning_tier", str)):
        value = getattr(candidate, attr, None)
        if value is None:
            raise NotAnAdapter(f"{type(candidate).__name__} does not declare {attr!r}")
        if not isinstance(value, kind):
            raise NotAnAdapter(
                f"{type(candidate).__name__}.{attr} must be {kind.__name__}, got "
                f"{type(value).__name__}")

    # A capability becomes part of a `read_hash` and of a compiled query, so it has to be the same
    # closed identifier shape the contract accepts. `frozenset({object()})` passed before.
    bad = sorted(repr(c) for c in candidate.capabilities
                 if not isinstance(c, str) or not _CAPABILITY.match(c))
    if bad:
        raise NotAnAdapter(
            f"{type(candidate).__name__} advertises capabilities that are not lowercase "
            f"snake_case identifiers: {', '.join(bad)}. A capability is hashed into a pointer "
            f"identity and screened by name; anything else cannot be either")
    if not getattr(candidate, "source_id").strip():
        raise NotAnAdapter(f"{type(candidate).__name__}.source_id is blank: a read nobody can "
                           f"attribute to a source is not a read anybody can verify")
    if getattr(candidate, "pinning_tier") not in ("T0", "T1", "T2", "T3"):
        raise NotAnAdapter(
            f"{type(candidate).__name__}.pinning_tier must be one of T0/T1/T2/T3 - the tier is a "
            f"claim about what the source can PROVE, and an unrecognised one proves nothing")
    if not getattr(candidate, "capabilities"):
        raise NotAnAdapter(
            f"{type(candidate).__name__} advertises no capabilities: the capability screen would "
            f"be vacuous and every request would have to be judged by something else")

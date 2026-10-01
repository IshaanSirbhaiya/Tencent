"""Tainted values and guarded sinks (spine D3, `ADAPTER_AGENT_SPEC` §4).

**Read the honesty section before using any of this.**

An earlier draft of the spec screened ``req.resolve`` for untrusted pointers and called it done.
That is wrong twice over, and the correction is the whole design:

* **it over-blocks** - `resolve` is the mechanism for RE-FETCHING an existing pointer. Refusing
  untrusted pointers there makes every source document and every email impossible to re-resolve
  or verify through the advertised contract. Using a locator to retrieve bytes is not the same as
  letting the bytes decide something.
* **it under-protects** - a caller can read an untrusted value, turn it into a string, and drop it
  into `predicate`, `projection.entity`, or a prompt. No pointer accompanies it, so a pointer-list
  check sees nothing.

So the model is **tainted values and guarded sinks**, and `resolve` is explicitly not a sink.

## What is honestly built here, and what is not

`ADAPTER_AGENT_SPEC` Appendix A lists ``TaintMap`` - "value-level taint registration and lifetime"
- as **undefined**, and adds: *"without it, D3 is a naming convention, not a control."* That is
still true, and this module does not pretend otherwise.

What exists: a `TaintMap` protocol, a registry implementation that remembers the exact values
unwrapped from untrusted pointers, and `screen_sinks`, which refuses a `CapabilityRequest` whose
sink positions carry one of them. That catches the direct flow - read an untrusted cell, put it
straight into a filter - which is the flow an adapter fleet actually produces.

What does **not** exist, and is asserted as a known gap by
``test_taint_map_is_not_yet_load_bearing``: taint does not survive string operations.
``"prefix" + tainted`` is a new object that the registry has never seen, and it will pass. Closing
that needs either a tainted-string wrapper type threaded through every unwrap site or a
quarantined interpreter in CaMeL's sense; both are real work and neither is in Phase 3A.

**Therefore: `screen_sinks` is a second line, not the control.** The control that is real today is
structural - the Shell process holds no credentials and no driver handle, `SourcePointer.trust` is
stamped by the Core from the pack's source contract rather than inferred, and trust flows up the
lattice so laundering by aggregation is refused in `databridge_core`. This module raises the cost
of the direct attack. Do not quote it as CaMeL.
"""
from __future__ import annotations

from typing import Any, Iterable, Iterator, Protocol, runtime_checkable

from databridge_core import GroundedValue, SourcePointer, Trust

from .contract import CapabilityRequest
from .describe import SourceDescription
from .errors import UntrustedColumnInPolicy, UntrustedInControlFlow

#: Positions where an untrusted value changes WHAT HAPPENS NEXT. `resolve` is deliberately absent:
#: retrieval is not action. The last two are business-plane sinks that `screen_sinks` cannot see
#: from a `CapabilityRequest`; they are listed because Appendix A calls the trusted-sink inventory
#: for the business plane an open item, and dropping them from the list would hide that.
#:
#: **Review R-3A finding 7 added the whole second block.** The first version screened the filter
#: and projection positions and stopped, which left every field that steers the read *outside* the
#: projection unguarded - an untrusted value could pick the SOURCE, the PURPOSE, the GRANT, the
#: instant, or the row budget, and it did not even need the documented string-transformation
#: bypass to do it. Each of those changes what happens next, which is the definition this list is
#: built from, so each belongs on it:
#:
#: * ``source_id`` selects the adapter, and therefore the credentials and the egress class;
#: * ``principal.purpose`` selects the policy that runs, and lands in the audit record as the
#:   lawful basis;
#: * ``principal.grant_id`` / ``principal.actor`` / ``principal.on_behalf_of`` decide which rows
#:   the compiled decision table admits and who the denial is attributed to;
#: * ``mode`` selects the schedule an adapter may branch on;
#: * ``as_of`` selects which historical state is served - or whether the read is refused;
#: * ``limit`` selects resource consumption, which is a denial-of-service lever.
TRUSTED_SINKS: tuple[str, ...] = (
    "predicate.params",             # filters
    "capability",                   # which operation runs
    "projection.entity",            # what is read
    "projection.fields",            # which columns are read (extends §4's list; same argument)
    "projection.key",               # what a pointer cites as row identity
    "order_by",                     # how rows are identified by ordinal
    "source_id",                    # WHICH SOURCE - and so which credentials and egress class
    "capability_version",           # which revision of a pack-declared named query runs
    "principal.purpose",            # which policy applies, and the recorded lawful basis
    "principal.grant_id",           # which subject-scoped grant is claimed
    "principal.actor",              # who the read is attributed to
    "principal.on_behalf_of",       # which human it is attributed to
    "mode",                         # build vs query - a schedule an adapter may branch on
    "as_of",                        # which historical state, or whether the read is refused
    "limit",                        # resource consumption
    "action.arguments",             # any Action invocation (business plane) - NOT screened here
    "prompt.instruction",           # anything a model may FOLLOW rather than quote - NOT screened
    #: **Five independent Codex reviews of Phase 3A converged on this as one root cause** (see
    #: `wiki/40-reviews/raw/phase3-adapters-*.md`): trust was stamped when a pointer is EMITTED,
    #: but nothing checked it when a value ENTERED policy evaluation. `screen_sinks` cannot see
    #: any of the three below - none of them is a `CapabilityRequest` field, they are the
    #: COMPILED policy artifact's own columns - so they are named here, exactly like the
    #: business-plane pair above, and guarded a different way: `require_trusted_scope_column`,
    #: called from `PolicyEngine._compile` (`policy.py`), refuses to COMPILE a decision table
    #: whose row scope or subquery is not source-contract-TRUSTED, rather than evaluating an
    #: untrusted comparison per row.
    "policy.row_rule.column",       # a RowRule's comparison column (row scope, both "eq" and
                                     # "in_subquery" kinds) - NOT screened here, see above
    "policy.subquery.select_column",  # a row rule's `IN (SELECT <col> ...)` column - NOT screened
    "policy.subquery.where_column",   # a row rule's subquery `WHERE <col> = ...` column - NOT
                                       # screened here, see above
)

#: The subset `screen_sinks` can actually inspect on a `CapabilityRequest`: everything above
#: except the business-plane positions and the policy-compile-time positions, neither of which
#: any `CapabilityRequest` carries - the business-plane pair belongs to an Action invocation or a
#: model prompt, and the policy pair belongs to a compiled `RowRule`/`Subquery` (`rolemodel.py`),
#: guarded instead by `require_trusted_scope_column` at compile time.
ADAPTER_SINKS: frozenset[str] = frozenset(
    s for s in TRUSTED_SINKS if not s.startswith(("action.", "prompt.", "policy.")))


@runtime_checkable
class TaintMap(Protocol):
    """Which values are known to have come from an untrusted pointer."""

    def of(self, value: Any) -> Trust:
        """The trust label of ``value``. See `RegistryTaintMap.of` on the fail-open default."""
        ...


class RegistryTaintMap:
    """Remembers values unwrapped from untrusted pointers, by equality.

    ``of()`` answers `TRUSTED` for anything it has never seen. That is **fail-open**, and it is
    the honest encoding of what this class knows: it is a registry of observations, not a lattice
    over the value space. Every literal an orchestrator writes into a request is unregistered, so
    the alternative default would refuse every request ever made.

    The consequence is stated plainly in the module docstring and asserted by a test: a value that
    has been through a string operation is a value this map has never seen.
    """

    __slots__ = ("_tainted",)

    def __init__(self) -> None:
        self._tainted: set[Any] = set()

    def register(self, value: Any, trust: Trust | None) -> None:
        """Record ``value`` as tainted if ``trust`` is not TRUSTED.

        ``None`` - an unlabelled pointer, mid-migration - registers as tainted, matching
        `databridge_core.trust_lub`: "never considered" is not evidence of safety.
        """
        if trust is Trust.TRUSTED:
            return
        try:
            self._tainted.add(value)
        except TypeError:
            # Unhashable values cannot be registered. Say so rather than dropping it silently:
            # a taint that fails to record is worse than one that was never attempted, because
            # the caller believes it recorded.
            raise TypeError(
                f"cannot register taint for unhashable {type(value).__name__}: unwrap the scalar "
                f"you actually intend to use before registering it") from None

    def register_value(self, gv: GroundedValue) -> None:
        """Register a `GroundedValue`'s payload under its pointer's trust label."""
        self.register(gv.value, gv.trust)

    def register_pointer_values(self, pointer: SourcePointer, values: Iterable[Any]) -> None:
        for v in values:
            self.register(v, pointer.trust)

    def of(self, value: Any) -> Trust:
        try:
            return Trust.UNTRUSTED if value in self._tainted else Trust.TRUSTED
        except TypeError:
            return Trust.TRUSTED

    def __len__(self) -> int:
        return len(self._tainted)


#: Sink name -> how to read the value(s) out of a request. A table rather than a chain of `elif`s
#: so that adding a field to `CapabilityRequest` and forgetting to screen it is visible as a
#: missing row, and `test_every_adapter_sink_is_readable` can assert the two stay in step.
_SINK_READERS: dict[str, Any] = {
    "capability":              lambda r: (r.capability,),
    "capability_version":      lambda r: ((r.capability_version,)
                                          if r.capability_version is not None else ()),
    "projection.entity":       lambda r: (r.projection.entity,),
    "projection.fields":       lambda r: r.projection.fields,
    "projection.key":          lambda r: r.projection.key,
    "order_by":                lambda r: r.order_by,
    "predicate.params":        lambda r: tuple(v for _, v in r.params),
    "source_id":               lambda r: (r.source_id,),
    "principal.purpose":       lambda r: (r.principal.purpose,),
    "principal.grant_id":      lambda r: ((r.principal.grant_id,)
                                          if r.principal.grant_id is not None else ()),
    "principal.actor":         lambda r: (r.principal.actor,),
    "principal.on_behalf_of":  lambda r: ((r.principal.on_behalf_of,)
                                          if r.principal.on_behalf_of is not None else ()),
    "mode":                    lambda r: (r.mode,),
    "as_of":                   lambda r: ((r.as_of,) if r.as_of is not None else ()),
    "limit":                   lambda r: (r.limit,),
}


def _sink_values(req: CapabilityRequest, sink: str) -> Iterator[Any]:
    reader = _SINK_READERS.get(sink)
    if reader is None:                                           # pragma: no cover - defensive
        return
    yield from reader(req)


def screen_sinks(req: CapabilityRequest, taint: TaintMap) -> CapabilityRequest:
    """Guard the sinks. **`resolve` is NOT a sink** - retrieval is not action.

    Returns the request unchanged, or raises `UntrustedInControlFlow`. Returning the request makes
    the screened value the one that flows onward, so a caller cannot screen one object and pass
    another.
    """
    for sink in TRUSTED_SINKS:
        if sink not in ADAPTER_SINKS:
            continue
        for value in _sink_values(req, sink):
            if taint.of(value) is Trust.UNTRUSTED:
                raise UntrustedInControlFlow(
                    f"{sink} carries a value derived from an untrusted source "
                    f"({value!r}). Untrusted values are quotable, not actionable (D3): they may "
                    f"be stored, cited, displayed and materialised, but they may never select a "
                    f"branch, populate a tool argument, become part of a predicate, or enter a "
                    f"prompt that produces an action.")
    return req


def require_trusted_scope_column(description: SourceDescription, *, entity: str, column: str,
                                  rule_id: str, position: str) -> None:
    """Refuse to COMPILE a policy rule that scopes access through an untrusted column.

    The other half of D3's sink inventory: `screen_sinks` guards the ORCHESTRATOR's request;
    this guards the ESTATE's own compiled policy. Called from `PolicyEngine._compile`
    (`policy.py`) for every column a `RowRule` or its `Subquery` reads to decide row inclusion —
    the ``policy.row_rule.column`` / ``policy.subquery.select_column`` /
    ``policy.subquery.where_column`` sinks named in `TRUSTED_SINKS` above, which
    `screen_sinks` cannot see because none of them is a `CapabilityRequest` field.

    ``position`` names WHERE in the rule the column was found (e.g. "row_filters comparison
    column", "subquery select_column"), so the raised error points at the exact declaration a
    data owner would need to edit, not just at the rule as a whole.

    **A lookup, never an inference.** The trust comes from `description.trust_of` — the source
    contract's own per-field declaration — exactly as a `Core` stamps trust onto an emitted
    pointer. There is no branch here on which SOURCE this is: the same rule shape is refused
    identically whatever source the description describes.

    **Fails closed.** `SourceDescription.trust_of` raises `KeyError` for a column the contract
    never classified at all (no entity of that name, or no field of that name on it). That is
    treated exactly like an explicit `Trust.UNTRUSTED` declaration, not like `Trust.TRUSTED`:
    "never classified" is not evidence of safety, matching `RegistryTaintMap.register`'s handling
    of an unlabelled pointer a few lines up in this module.

    Deliberately narrower than `screen_sinks`: this raises only for the SCOPING columns a row
    rule reads to decide inclusion, never for a column merely being PROJECTED and disclosed.
    Reading and disclosing an untrusted column is the quotable half of D3 and stays untouched —
    only letting that column's bytes decide who sees a row is the actionable half this refuses.
    """
    try:
        trust = description.trust_of(entity, column)
    except KeyError:
        trust = Trust.UNTRUSTED
    if trust is Trust.UNTRUSTED:
        raise UntrustedColumnInPolicy(
            f"{rule_id} scopes access through {description.source_id}.{entity}.{column} "
            f"({position}), which the source contract declares UNTRUSTED "
            f"(or does not classify at all — fails closed the same way). An untrusted column may "
            f"be stored, cited and displayed; it may never decide which rows or fields a "
            f"principal sees (D3). Refused at policy COMPILE time — a rule scoping through this "
            f"column cannot be safely evaluated, and evaluating it per row instead of refusing it "
            f"now is exactly the gap this refusal closes.")

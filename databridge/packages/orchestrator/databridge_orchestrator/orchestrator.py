"""`Orchestrator` — the main agent. The loop the brief names and nothing in this repo ran.

**A question goes in. The main agent chooses which sources can answer it, hands each one's Shell a
brief, the Shell proposes a `CapabilityRequest`, Policy admits or refuses, the Core executes, and
grounded cells come back — with every step recorded.**

## What this module is, precisely

* It selects adapters from their own `describe()` (`selection.py`) — never from a model choosing
  among the whole fleet's tools, and never from anything a source returned.
* For each selected adapter it constructs an `AdapterShell` over that adapter's own description
  and calls `propose()` exactly once. A rejection (`EscalationRequired`,
  `CapabilityNotAdvertised`) is recorded and reported; it is never fed back to the model — that
  retry loop is `shell.py`'s whole argument, and repeating it here would undo it one layer up.
* It hands the resulting `CapabilityRequest` to the adapter's `resolve()`, which holds its own
  `PolicyEngine` and admits or refuses the read itself (`policy.py`: "a Core cannot be constructed
  without a PolicyEngine"). This module never constructs a `PolicyEngine` and never calls
  `PolicyEngine.admit` directly — that would be a second, unaudited path to the same decision.
* A whole-request refusal (purpose mismatch, egress ceiling, an uncompilable row scope, budget
  exhausted, …) becomes a `Withheld` cell with a machine-readable reason code — never a missing
  row, never a silent gap.
* What it assembles from a successful read is **cells and pointers**, taken verbatim from the
  `AdapterResult` the Core returned. There is no step here that renders a row to a sentence. If a
  caller wants prose, it asks a model to write ABOUT the returned cells on the client side of this
  boundary — this module's contract ends at "here are the grounded cells and what happened to each
  source", and it holds that line even though prose would look more finished in a demo.
* Every step — the principal, each capability request, each policy outcome, each cell, each
  escalation — is recorded through an injected trace sink with the same method names
  `apps/gateway/databridge_gateway/trace_recorder.TraceHandle` already exposes. This module does
  not import the gateway (that would invert the dependency the brief draws), so the sink is
  duck-typed: anything with `.principal()/.capability()/.policy()/.cell()/.pointer()/
  .ledger_append()/.released()/.escalation()/.cost()/.note()` works, and passing nothing at all
  (`trace=None`) is silently a no-op via `_NullTrace` below — this module is unit-testable without
  standing up a trace recorder.

## What it deliberately does not do

* It does not retry a rejected Shell proposal against the model. `AdapterShell.propose` already
  enforces "one model call, one validation, then a request or an escalation" (`shell.py` §10); an
  orchestrator loop that called `propose()` again after `EscalationRequired` would just move the
  retry loop up one layer and erase the guarantee.
* It does not construct or hold a `PolicyEngine`. Admission is the Core's job, every time, so that
  there is exactly one code path — inside `resolve()` — that can grant anything.
* It does not paraphrase. `SourceOutcome.cells` is the only thing a caller can render as "the
  answer", and every entry in it is a `GroundedCell` carrying a pointer or a denial, never a string
  a model wrote about the row.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Optional, Protocol, Sequence, runtime_checkable

from databridge_adapter import (
    AdapterError,
    CapabilityNotAdvertised,
    CapabilityRequest,
    EscalationRequired,
    Principal,
    SourceDescription,
    pointer_digest,
)
from databridge_adapter.shell import DEFAULT_TASK_CLASS, AdapterShell
from databridge_core import Absent, GroundedValue, Withheld, WithholdingRecord

from .selection import select_sources

CellState = Literal["grounded", "withheld", "absent"]
SourceStatus = Literal["answered", "refused", "escalated"]

_POLICY_CODE = re.compile(r"\bPOLICY_[A-Z][A-Z0-9_]*\b")
_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


# =============================================================================================
# what one adapter is, to this orchestrator
# =============================================================================================
@dataclass(frozen=True)
class AdapterHandle:
    """One source the orchestrator may send work to. Nothing here holds a credential — that stays
    inside ``adapter``, which is the Core, constructed with its own `PolicyEngine` elsewhere
    (mirroring `apps/gateway/databridge_gateway/verify_fleet.py`'s construction rather than a
    fourth copy of it)."""

    source_id: str
    #: Anything satisfying the five-verb port (`databridge_adapter.port.Adapter`): `describe`,
    #: `snapshot`, `plan`, `resolve`, `verify`. Not type-pinned to a concrete Core so a fake adapter
    #: can stand in for a unit test without dragging SQLAlchemy into this package.
    adapter: Any
    description: SourceDescription


# =============================================================================================
# what crosses the boundary — cells and pointers, never prose
# =============================================================================================
@dataclass(frozen=True)
class GroundedCell:
    """One cell, exactly as the Core emitted it. A pointer with a trust label, or a denial with a
    reason code, or a named absence — never a bare value and never a sentence about one."""

    source_id: str
    entity: str
    column: str
    state: CellState
    row_id: Optional[str] = None
    value: Any = None
    trust: Optional[str] = None
    pointer: Optional[dict] = None
    reason_code: Optional[str] = None
    policy_id: Optional[str] = None
    appealable: Optional[bool] = None
    pointer_id: Optional[str] = None
    """The pointer's stable id (`ptr:sha256:<pointer_digest>`) for a grounded cell: what the trace
    records on its `pointer` step and names on the `released` step (INT5)."""


@dataclass(frozen=True)
class SourceOutcome:
    """What happened when this orchestrator asked ONE source. One of three shapes:

    * ``answered`` — the Shell produced a request, Policy admitted it (wholly or in part), and
      ``cells`` holds whatever the Core returned, cell state included: a source can answer with
      cells that are themselves ``withheld`` or ``absent``, and that is still "answered" at the
      source level — the denial is per-cell, not per-source.
    * ``refused`` — the Shell produced a request and the Core's own admission refused the WHOLE
      read (a purpose mismatch, an egress ceiling, an uncompilable row scope, a budget). ``denial``
      carries a real `Withheld` naming the reason; ``cells`` is empty because nothing was read.
    * ``escalated`` — the Shell could not produce a request inside this source's advertised
      capability set at all, or proposed one the source does not advertise. No `CapabilityRequest`
      exists, so there is nothing for Policy to have admitted or refused.
    """

    source_id: str
    status: SourceStatus
    message: str = ""
    capability: Optional[str] = None
    entity: Optional[str] = None
    fields: tuple[str, ...] = ()
    cells: tuple[GroundedCell, ...] = ()
    #: Populated only when status == "refused". A real `Withheld`, not a lookalike shape, so a
    #: caller that already knows how to render a `Withheld` cell needs no second code path for a
    #: source-level denial.
    denial: Optional[Withheld] = None


@dataclass(frozen=True)
class AskResult:
    """One run of the loop, end to end."""

    question: str
    principal_actor: str
    principal_on_behalf_of: Optional[str]
    principal_purpose: str
    sources_considered: tuple[str, ...]
    selection_note: str
    sources: tuple[SourceOutcome, ...]

    @property
    def cells(self) -> tuple[GroundedCell, ...]:
        """Every cell across every source that answered, flattened. Still cells and pointers —
        this is a convenience view, not a summarisation; nothing here changes a value or drops a
        pointer."""
        return tuple(c for outcome in self.sources for c in outcome.cells)

    @property
    def answered(self) -> bool:
        return any(o.status == "answered" and o.cells for o in self.sources)


# =============================================================================================
# the trace sink — duck-typed so this package does not import the gateway
# =============================================================================================
@runtime_checkable
class TraceSink(Protocol):
    def principal(self, **kw: Any) -> Any: ...
    def capability(self, request: Any = None, **kw: Any) -> Any: ...
    def policy(self, decision: Any = None, **kw: Any) -> Any: ...
    def cell(self, **kw: Any) -> Any: ...
    def pointer(self, **kw: Any) -> Any: ...
    def ledger_append(self, **kw: Any) -> Any: ...
    def released(self, **kw: Any) -> Any: ...
    def escalation(self, **kw: Any) -> Any: ...
    def cost(self, **kw: Any) -> Any: ...
    def note(self, **kw: Any) -> Any: ...


class _NullTrace:
    """A trace sink that records nothing. Lets `Orchestrator.ask` be called with `trace=None` in a
    unit test without every call site growing an `if trace is not None`."""

    def _noop(self, *_a: Any, **_k: Any) -> "_NullTrace":
        return self

    principal = capability = policy = cell = pointer = _noop
    ledger_append = released = escalation = cost = note = _noop


# =============================================================================================
# the orchestrator
# =============================================================================================
class Orchestrator:
    """Owns no credential, no `PolicyEngine`, and no driver. It owns the loop.

    ``handles`` is fixed at construction — the deployment's fleet, built once wherever the
    concrete adapters are wired (a gateway process, a CLI). ``harness`` is shared across every
    `AdapterShell` this orchestrator constructs; a `Harness` is safe to share (it is the model
    call-site, not a connection), so one instance here is one budget/cache scope for the whole
    fleet rather than a fresh one per source.
    """

    def __init__(self, handles: Sequence[AdapterHandle], *, harness: Any,
                 task_class: str = DEFAULT_TASK_CLASS) -> None:
        if not handles:
            raise ValueError(
                "an Orchestrator with no adapters can answer nothing, and silently accepting one "
                "would make every ask() look like a refusal with no source to blame it on")
        seen = set()
        for h in handles:
            if h.source_id in seen:
                raise ValueError(f"duplicate source_id {h.source_id!r} in the fleet")
            seen.add(h.source_id)
        self._handles = tuple(handles)
        self._harness = harness
        self._task_class = task_class

    @property
    def handles(self) -> tuple[AdapterHandle, ...]:
        return self._handles

    def fleet_description(self) -> tuple[SourceDescription, ...]:
        """The capability matrix this orchestrator can draw on, for a status/inspection surface.
        No data, no credentials — the same thing every `describe()` promises."""
        return tuple(h.description for h in self._handles)

    # --- the loop ----------------------------------------------------------------------
    def ask(self, question: str, *, principal: Principal, trace: Optional[TraceSink] = None,
            max_sources: int = 4, limit: int = 200) -> AskResult:
        """Run the loop once. Never raises for a refusal or an escalation — those are OUTCOMES,
        reported per source; only a programming error (a bad `Principal`, an adapter that is not a
        real port) should ever surface as an exception here."""
        sink: TraceSink = trace if trace is not None else _NullTrace()
        sink.principal(actor=principal.actor, on_behalf_of=principal.on_behalf_of,
                       purpose=principal.purpose, grant_id=principal.grant_id)

        chosen, note = select_sources(question, self._handles, max_sources=max_sources)
        sink.note(key="source_selection", text=note)

        outcomes = tuple(
            self._ask_one(handle, question, principal=principal, sink=sink, limit=limit)
            for handle in chosen)

        return AskResult(
            question=question, principal_actor=principal.actor,
            principal_on_behalf_of=principal.on_behalf_of, principal_purpose=principal.purpose,
            sources_considered=tuple(h.source_id for h in chosen), selection_note=note,
            sources=outcomes)

    # --- one adapter ---------------------------------------------------------------------
    def _ask_one(self, handle: AdapterHandle, question: str, *, principal: Principal,
                 sink: TraceSink, limit: int) -> SourceOutcome:
        shell = AdapterShell(description=handle.description, harness=self._harness,
                             task_class=self._task_class)
        try:
            req: CapabilityRequest = shell.propose(question, principal=principal, limit=limit)
        except (EscalationRequired, CapabilityNotAdvertised) as exc:
            # **Never retried.** shell.py §10: a rejection fed back to the model until something
            # passes is a screen that eventually passes anything. This is the one call to
            # propose() for this source, and this except block is where it ends.
            sink.escalation(to="human", reason=str(exc), blocking=False)
            return SourceOutcome(source_id=handle.source_id, status="escalated", message=str(exc))

        sink.capability(req)

        try:
            result = handle.adapter.resolve(req)
        except AdapterError as exc:
            # A WHOLE-REQUEST refusal: the admission inside resolve() denied the read before any
            # row was touched. Represented as a real Withheld, not a lookalike — see
            # SourceOutcome.denial's docstring on why.
            reason_code = _reason_code(exc)
            policy_id = f"orchestrator_admission:{handle.source_id}"
            denial = Withheld(record=WithholdingRecord(
                field_path=req.projection.entity, policy_id=policy_id, reason_code=reason_code,
                principal=principal.actor, purpose=principal.purpose))
            sink.policy(field_path=req.projection.entity, withheld=True, policy_id=policy_id,
                       reason_code=reason_code, source_id=handle.source_id)
            return SourceOutcome(
                source_id=handle.source_id, status="refused", message=str(exc),
                capability=req.capability, entity=req.projection.entity,
                fields=req.projection.fields, denial=denial)

        cells = tuple(
            _cell_record(handle.source_id, req.projection.entity, column, cell)
            for row in result.rows.rows
            for column, cell in zip(result.rows.columns, row))

        sink.cost(rows=result.cost.rows, wall_ms=result.cost.wall_ms,
                  llm_tokens=result.cost.llm_tokens or None)

        if cells:
            # One ledger append covers the whole read, recorded BEFORE any `released` step below —
            # trace_recorder.TraceHandle.released() checks exactly this ordering
            # (AGENT_PLANE §7 property 3: the audit cannot lag the disclosure).
            sink.ledger_append(entry_id=f"{handle.source_id}:{req.read_hash[:16]}",
                               covers=["*"], source_id=handle.source_id)

        for gc in cells:
            field_path = f"{req.projection.entity}.{gc.column}"
            sink.cell(state=gc.state, field_path=field_path, trust=gc.trust,
                     reason_code=gc.reason_code, policy_id=gc.policy_id,
                     source_id=handle.source_id)
            if gc.state == "withheld":
                # A PER-COLUMN denial, distinct from the whole-request refusal above. Recorded as
                # its own `policy` step (not just a `cell` step) so the rendered /trace view's
                # ADAPTER branch shows "POLICY <field>: withheld · <reason>" per column, the same
                # shape a whole-source refusal gets — a reader should not have to already know to
                # look inside `cell` steps to find a column-level denial.
                sink.policy(field_path=field_path, withheld=True, policy_id=gc.policy_id,
                           reason_code=gc.reason_code, appealable=gc.appealable,
                           source_id=handle.source_id)
            if gc.state == "grounded":
                # INT5 (ACT3): the release names the pointer this step just recorded for the
                # SAME source, so `released()` can prove where the value came from.
                sink.pointer(origin="live_source", pointer_id=gc.pointer_id,
                            source_id=handle.source_id,
                            kind=(gc.pointer or {}).get("kind"), trust=gc.trust)
                sink.released(field_path=field_path, source_id=handle.source_id,
                              pointer_id=gc.pointer_id)

        return SourceOutcome(
            source_id=handle.source_id, status="answered", capability=req.capability,
            entity=req.projection.entity, fields=req.projection.fields, cells=cells)


# =============================================================================================
# helpers
# =============================================================================================
def _reason_code(exc: Exception) -> str:
    """A machine-readable code for a whole-request refusal. Most `PolicyEngine` refusals already
    embed one of `roles.yaml`'s own ``POLICY_*`` codes in the message (see `policy.py`'s stage
    comments) — that one is used verbatim so the code an operator sees here is the same one the
    estate's own file defines. Anything else (`CannotPinPast`, `BudgetExceeded`, a bare
    `AdapterError`) falls back to its exception class name, upper-snake-cased, which is still a
    stable, closed-enough vocabulary for a reason nobody wrote a YAML entry for."""
    found = _POLICY_CODE.search(str(exc))
    if found:
        return found.group(0)
    return _CAMEL_BOUNDARY.sub("_", type(exc).__name__).upper()


def _jsonable(value: Any) -> Any:
    """Values leave this module JSON-safe. A `Decimal` becomes a string, never a float, so a money
    figure does not acquire a rounding error crossing this boundary (mirrors
    `verify_fleet.py::_jsonable`, kept local rather than imported so this package does not depend
    on the gateway)."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


def _cell_record(source_id: str, entity: str, column: str, cell: Any) -> GroundedCell:
    if isinstance(cell, GroundedValue):
        pointer = cell.pointer
        trust = getattr(pointer.trust, "value", pointer.trust)
        return GroundedCell(
            source_id=source_id, entity=entity, column=column, state="grounded",
            row_id=getattr(pointer, "row_id", None), value=_jsonable(cell.value), trust=trust,
            pointer=pointer.model_dump(mode="json", exclude_none=True),
            pointer_id=f"ptr:sha256:{pointer_digest(pointer)}")
    if isinstance(cell, Withheld):
        rec = cell.record
        return GroundedCell(
            source_id=source_id, entity=entity, column=column, state="withheld",
            reason_code=rec.reason_code, policy_id=rec.policy_id, appealable=rec.appealable)
    if isinstance(cell, Absent):
        return GroundedCell(source_id=source_id, entity=entity, column=column, state="absent")
    raise TypeError(                                              # pragma: no cover - port floor
        f"cell for {entity}.{column} is a bare {type(cell).__name__}; GroundedRows already "
        f"guarantees GroundedValue | Withheld | Absent, so this is a defect in that guarantee, "
        f"not a case this module should quietly handle")

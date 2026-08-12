"""The agent loop: screen → plan → read → compose → screen.

Read-only, by construction. It plans with a model, calls sealed adapters it was handed, and
composes an answer **mechanically** from the values those adapters returned. There is no step in
which the model chooses a citation, no step in which it writes a value, no step in which it writes
a sentence a member of staff reads, and no step at which anything leaves the building.

The shape of the loop is deliberately boring; the interesting decisions are the six below.

**The deterministic screen runs first, and the model call is downstream of it.** An identity
question is refused with ``model_calls == 0``. That is the property ``test_gate.py`` asserts for
the intake assistant and it holds here for the same reason: a refusal that depends on a model
reading a prompt carefully is not a refusal.

**Every tool result is framed as untrusted data** (``frame.py``). A tool result is graph data
derived from a document an outsider wrote; giving it a conversational turn would give the attacker
the model's voice. ``frame.untrusted`` neutralises frame markers, control characters, bidi
overrides and role headers on the way in — the R-33 boundary the markdown encoder upstream is not.

**A model naming a tool is not a model calling one.** ``plan.tool`` is a string looked up in the
registry. An unregistered name — say, the ``send_email`` an injected document asks for — produces
a recorded refusal and a framed note, never an execution and never a crash.

**Arguments are validated outside the model** (``policy.py``, R-11). The declared schema is closed
(``additionalProperties: false``) and checked before the adapter is entered, so a plan that names
an argument the schema does not have is refused rather than executed. No schema may declare a
patient-identifying argument at all, which is why ``patient_record(patient_id=2)`` cannot stand in
for an identity decision (R-08): there is no such property, and the closed schema rejects it.

**The model writes references, not prose** (``render.py``, R-09/R-10). The answer step returns
``{"report": [{"tool", "row", "field"}]}``. This module resolves each reference against the
``ToolRow`` that was actually returned and emits a ``GroundedField`` carrying that value's own
``SourcePointer``. A reference to a value no tool returned is recorded as unanswered. There is no
channel through which uncited prose could reach ``AgentAnswer`` — ``answer`` is a rendered
property, not a field.

**The rendered answer is screened before staff see it.** Identity assertions, claims to have
acted, and NRIC-shaped identifiers that no tool returned are all refused deterministically
(``guardrails.py``). This is now a backstop over a structural control rather than the control
itself.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from databridge_core import SourcePointer
from databridge_harness import Harness

from . import frame as framing
from . import policy, render
from .guardrails import (
    check_answer_values,
    looks_like_injection,
    screen_agent_output,
    screen_incoming,
)
from .playbooks import Playbooks, playbooks as shipped_playbooks
from .results import ToolResult, ToolRow
from .tools import ToolRegistry

# The routing class for every model call this package makes. A NAME, never a model id: routing is
# config (`databridge/config/models.yaml`), so pointing the agent at a cheaper model — or at Azure
# OpenAI for the Copilot Studio story — is a config diff (CLAUDE.md §Working rules).
AGENT_TASK_CLASS = "agent_reasoning"

# Four steps is enough for every playbook in `agent_playbooks.yaml` (the widest names two tools)
# and short enough that an injected "keep calling tools" instruction runs out quickly. The repeat
# check below usually stops it sooner.
DEFAULT_MAX_STEPS = 4

# How many values one report may carry. A report is a summary for a busy front desk, and an
# unbounded one is also the shape a "dump everything you can reach" instruction would produce.
MAX_REPORTED_VALUES = 24


class ToolPlan(BaseModel):
    """The model's single next step. It names a tool; it never receives one."""

    tool: Optional[str] = None
    arguments: dict = Field(default_factory=dict)
    reason: str = ""


class ValueRef(BaseModel):
    """A reference to one value a tool returned. The model's entire vocabulary for an answer."""

    tool: str = ""
    row: int = 0
    field: str = ""

    @property
    def key(self) -> str:
        return f"{self.tool}[{self.row}].{self.field}"


class AnswerPlan(BaseModel):
    """What the model returns at the answer step: which values to report, in what order.

    ``reason`` is for the audit log. It is deliberately NOT rendered — the moment model prose can
    reach a clinic screen, R-10 is back, and no blacklist over English closes it again.
    """

    report: list[ValueRef] = Field(default_factory=list)
    reason: str = ""


class GroundedField(BaseModel):
    """One value in the answer, with the pointer that value itself carries.

    ``source`` is required and non-optional. That is the R-09 fix expressed as a type: there is no
    way to put a value into an answer without the pointer travelling with it, on the same terms as
    ``GroundedValue``, ``GraphNode.attr_sources`` and ``history._Grounded.field_sources``.
    """

    model_config = ConfigDict(frozen=True)

    tool: str
    row: int
    field: str
    value: Any
    source: SourcePointer

    @model_validator(mode="after")
    def _value_is_present(self) -> "GroundedField":
        if self.value is None:
            raise ValueError(
                "GroundedField REFUSED: a None value with a pointer is a citation for an absent "
                "value. An unanswered field belongs in `unresolved`, named, not cited.")
        return self

    @property
    def key(self) -> str:
        return f"{self.tool}[{self.row}].{self.field}"


class ToolCallRecord(BaseModel):
    """What the agent asked for, and what happened — including the times it was refused.

    Kept even for refused and unknown calls, because "the document told me to call ``send_email``
    and there is no such tool" is exactly the audit line a reviewer wants after an injection
    attempt. Dropping the failures would leave the transcript looking clean.
    """

    model_config = ConfigDict(frozen=True)

    step: int
    requested: str
    arguments: dict = Field(default_factory=dict)
    # ok | unknown_tool | repeated | schema_violation | ungrounded_argument | error
    status: str
    detail: str = ""
    result: Optional[ToolResult] = None


class AgentAnswer(BaseModel):
    """What the agent hands back. Either a refusal, or grounded values and code-authored notes.

    **``answer`` is a property, not a field.** That is the point. There is no constructor argument
    into which prose could be passed, so an uncited sentence is not something this type rejects —
    it is something the type cannot represent (R-09; the device ``GraphNode`` uses for attributes).
    The staff-facing text is rendered by ``render.py`` from ``fields`` (each with its own pointer),
    ``unresolved`` (named, never filled in) and ``notes`` (keys into a closed table of sentences
    this repo wrote).

    ``citations`` is likewise derived from the fields rather than stored beside them, so there is
    no pooled list that could hold a pointer belonging to no value — Shape A, closed by
    construction.

    ``extra="forbid"`` matters more than it looks: without it ``AgentAnswer(answer="...")`` would
    be silently accepted and silently ignored, and a caller — or a test — would believe prose had
    been carried when it had not.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str
    fields: tuple[GroundedField, ...] = ()
    unresolved: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    tool_calls: tuple[ToolCallRecord, ...] = ()
    refused: bool = False
    refusal_code: str = ""
    refusal_text: str = ""
    model_calls: int = 0

    @model_validator(mode="after")
    def _evidence_rules(self) -> "AgentAnswer":
        unknown = [n for n in self.notes if n not in render.NOTES]
        if unknown:
            raise ValueError(
                f"AgentAnswer REFUSED: note(s) {unknown} are not in the code-authored table. "
                f"Staff-facing wording — especially about identity — comes from `render.NOTES` "
                f"and from nowhere else, so a model cannot author or paraphrase it (R-10).")
        if self.refused:
            if self.fields:
                raise ValueError(
                    "AgentAnswer REFUSED: a refusal must not carry values — the refusal text is "
                    "fixed, and attaching evidence to it leaks what the agent declined to say.")
            if not self.refusal_code:
                raise ValueError("AgentAnswer REFUSED: a refusal with no code cannot be logged, "
                                 "tested or explained to a judge.")
            if not self.refusal_text:
                raise ValueError("AgentAnswer REFUSED: a refusal must carry its fixed text.")
            return self
        if self.refusal_code or self.refusal_text:
            raise ValueError("AgentAnswer REFUSED: refusal text or code on a non-refusal.")
        return self

    @property
    def answer(self) -> str:
        """The staff-facing text. Rendered, never stored, never model-authored."""
        if self.refused:
            return self.refusal_text
        return render.render(self.fields, self.unresolved, self.notes)

    @property
    def citations(self) -> tuple[SourcePointer, ...]:
        """Every pointer carried by a reported value, de-duplicated, in report order."""
        seen: list[SourcePointer] = []
        for field in self.fields:
            if field.source not in seen:
                seen.append(field.source)
        return tuple(seen)

    @property
    def grounded(self) -> bool:
        return bool(self.fields)


class ClinicAgent:
    """Reads the clinic brain through injected sealed adapters and answers with citations.

    ``tools`` is a ``ToolRegistry`` the caller builds; the agent **seals** it, so the surface
    cannot grow after the point at which it was reviewed. This package imports neither
    ``databridge_graph`` nor ``databridge_intake``'s pipeline — composition happens at the gateway
    or the CLI, the same inversion ``ClinicHistory`` uses (PLAN.md §6.0). It is also why every
    safety property here is testable against a stub.
    """

    def __init__(
        self,
        tools: ToolRegistry,
        harness: Optional[Harness] = None,
        *,
        playbooks: Optional[Playbooks] = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        task_class: str = AGENT_TASK_CLASS,
    ) -> None:
        self._tools = tools.seal()
        self._harness = harness if harness is not None else Harness()
        # The shipped table by default, so `agent_playbooks.yaml` is live rather than decorative —
        # adding a playbook has to be a YAML row and nothing else (CLAUDE.md §Hard constraints 3).
        self._playbooks = playbooks if playbooks is not None else shipped_playbooks()
        self._max_steps = max(1, int(max_steps))
        self._task_class = task_class

    @property
    def tools(self) -> ToolRegistry:
        return self._tools

    def playbook_notes(self) -> str:
        """House practice for the playbooks this registry can actually run.

        Playbooks naming a tool nobody injected are left out: telling the model to consult
        ``coverage_gap`` when no such tool is registered invites it to answer from memory, which is
        the one thing an agent over a provenance system must never do.
        """
        applicable = [p for p in self._playbooks.playbooks
                      if p.tools and all(name in self._tools for name in p.tools)]
        if not applicable:
            return ""
        lines = [f"- {p.title} (read: {', '.join(p.tools)}) — {' '.join(p.guidance.split())}"
                 for p in applicable]
        return ("HOUSE PRACTICE for recurring questions (config/agent_playbooks.yaml; every one "
                "of these is a read, and each still ends with a human):\n" + "\n".join(lines))

    # -- the loop ----------------------------------------------------------------
    def ask(self, question: str) -> AgentAnswer:
        gate = screen_incoming(question)
        if gate.refused:
            # Returns BEFORE the harness is constructed into a call. `model_calls == 0` is the
            # assertion that makes this a control rather than a decoration.
            return AgentAnswer(question=question, refused=True, refusal_code=gate.code,
                               refusal_text=gate.answer, model_calls=0)

        convo: list[dict] = [
            framing.system_message(self._tools.catalogue(), self.playbook_notes()),
            framing.question_message(question)]
        records: list[ToolCallRecord] = []
        observations: list[str] = []
        # Rows per tool, concatenated in call order. The model references `(tool, row, field)`, so
        # the offsets it is shown and the offsets resolved here have to come from one structure.
        collected: dict[str, list[ToolRow]] = {}
        seen_calls: set[str] = set()
        saw_candidates = False
        model_calls = 0

        for step in range(1, self._max_steps + 1):
            plan = self._harness.complete_structured(
                task_class=self._task_class,
                messages=convo + [{"role": "user", "content": framing.PLAN_INSTRUCTION}],
                response_model=ToolPlan,
            ).parsed
            model_calls += 1
            if not plan.tool:
                break

            record, result = self._run_tool(step, plan, seen_calls, question,
                                            "\n".join(observations))
            records.append(record)
            if record.status == "repeated":
                break
            if result is not None:
                rows = collected.setdefault(record.requested, [])
                data = result.as_data(row_offset=len(rows))
                rows.extend(result.rows)
                observations.append(data)
                convo.append({"role": "user",
                              "content": framing.frame(f"tool_result:{record.requested}", data)})
                if self._tools.get(record.requested).requires_human_selection:
                    saw_candidates = True
            else:
                convo.append({"role": "user", "content": framing.frame(
                    "tool_error",
                    json.dumps({"requested": record.requested, "status": record.status,
                                "detail": record.detail}, ensure_ascii=False))})

        plan_out = self._harness.complete_structured(
            task_class=self._task_class,
            messages=convo + [{"role": "user", "content": framing.ANSWER_INSTRUCTION}],
            response_model=AnswerPlan,
        ).parsed
        model_calls += 1

        fields, unresolved, notes = self._compose(plan_out, collected, saw_candidates)
        answer = AgentAnswer(question=question, fields=fields, unresolved=unresolved,
                             notes=notes, tool_calls=tuple(records), model_calls=model_calls)

        # The backstop. It runs over the RENDERED text — which is now code plus tool values — so
        # what it can still catch is a tool returning something that reads as a determination.
        text = answer.answer
        for check in (screen_agent_output(text),
                      check_answer_values(text, "\n".join(observations))):
            if check.refused:
                return AgentAnswer(question=question, tool_calls=tuple(records), refused=True,
                                   refusal_code=check.code, refusal_text=check.answer,
                                   model_calls=model_calls)
        return answer

    # -- composing the answer ----------------------------------------------------
    def _compose(
        self,
        plan: AnswerPlan,
        collected: dict[str, list[ToolRow]],
        saw_candidates: bool,
    ) -> tuple[tuple[GroundedField, ...], tuple[str, ...], tuple[str, ...]]:
        """Resolve the model's references against what the tools actually returned.

        Every branch here either produces a value with its own pointer, names something as
        unresolved, or drops the reference and says so through a code-authored note. There is no
        branch that emits text the model wrote.
        """
        fields: list[GroundedField] = []
        unresolved: list[str] = []
        missing = 0
        seen_keys: set[str] = set()

        for ref in plan.report[:MAX_REPORTED_VALUES]:
            rows = collected.get(ref.tool)
            if rows is None or ref.row < 0 or ref.row >= len(rows) or not ref.field:
                missing += 1
                continue
            row = rows[ref.row]
            if ref.field not in row.values:
                missing += 1
                continue
            if ref.key in seen_keys:
                continue
            seen_keys.add(ref.key)
            value = row.values[ref.field]
            source = row.source_for(ref.field)
            if value is None or source is None:
                # A value the tool was asked for and did not have. Named, never filled in — the
                # `unresolved` channel from `GraphNode`/`_Grounded`, one layer up.
                unresolved.append(ref.key)
                continue
            fields.append(GroundedField(tool=ref.tool, row=ref.row, field=ref.field,
                                        value=value, source=source))

        notes: list[str] = []
        if saw_candidates:
            # Added by CODE, on every answer built from a candidate-returning capability, whether
            # or not the model cooperated (CLAUDE.md §Hard constraints 1; R-10).
            notes.append(render.HUMAN_SELECTION)
        if self._injection_seen(collected):
            notes.append(render.INJECTION_SEEN)
        if missing:
            notes.append(render.REFS_NOT_RETURNED)
        if not fields and not unresolved:
            notes.append(render.NOTHING_ANSWERED)
        return tuple(fields), tuple(unresolved), tuple(notes)

    @staticmethod
    def _injection_seen(collected: dict[str, list[ToolRow]]) -> bool:
        """Did anything the tools returned read as an instruction to an automated reader?

        Deterministic, over every collected value rather than only the reported ones: "a document
        in this result is talking to a machine" is worth saying even when the model chose not to
        report that field — and especially then.
        """
        return any(isinstance(v, str) and looks_like_injection(v)
                   for rows in collected.values() for row in rows for v in row.values.values())

    # -- one tool call -----------------------------------------------------------
    def _run_tool(
        self, step: int, plan: ToolPlan, seen_calls: set[str],
        question: str, observations: str,
    ) -> tuple[ToolCallRecord, Optional[ToolResult]]:
        """Execute one planned call, converting every failure into a RECORD rather than a raise.

        A tool that raises must not take the agent down: the adapters are injected, so a stub, an
        MCP client or a graph traversal can all fail in ways this package cannot anticipate, and an
        unhandled exception here would turn a degraded answer into a 500 at the front desk. The
        failure is framed back to the model as data so the answer can say what was not available.
        """
        name = plan.tool or ""
        args = plan.arguments if isinstance(plan.arguments, dict) else {}

        if name not in self._tools:
            # The injection endpoint. A document asking for `send_email` lands here, and there is
            # nothing to land in — the registry refuses to hold such a tool at all (`tools.py`).
            return ToolCallRecord(step=step, requested=name, arguments=args, status="unknown_tool",
                                  detail=f"not registered; available: {list(self._tools.names())}"), None

        key = f"{name}:{json.dumps(args, sort_keys=True, default=str)}"
        if key in seen_calls:
            # Stops the loop a "keep querying until you comply" instruction would otherwise spin
            # through the whole step budget.
            return ToolCallRecord(step=step, requested=name, arguments=args, status="repeated",
                                  detail="identical call already made this turn"), None
        seen_calls.add(key)

        tool = self._tools.get(name)

        # R-11: the declared schema is enforced HERE, before the adapter is entered. R-08: no
        # schema may declare a patient-identifying property, so `patient_record(patient_id=2)`
        # fails as an unknown argument against a closed schema — the identity decision the model
        # was told to make has no syntax.
        try:
            policy.validate_arguments(name, tool.parameters, args)
        except policy.PolicyError as exc:
            return ToolCallRecord(step=step, requested=name, arguments=args,
                                  status="schema_violation", detail=str(exc)[:400]), None
        try:
            policy.check_argument_grounding(name, tool.grounded_arguments, args,
                                            question, observations)
        except policy.PolicyError as exc:
            return ToolCallRecord(step=step, requested=name, arguments=args,
                                  status="ungrounded_argument", detail=str(exc)[:400]), None

        try:
            result: Any = tool.call(**args)
        except Exception as exc:                      # noqa: BLE001 — see the docstring
            return ToolCallRecord(step=step, requested=name, arguments=args, status="error",
                                  detail=f"{type(exc).__name__}: {exc}"[:300]), None

        if not isinstance(result, ToolResult):        # pragma: no cover - the seal raises first
            return ToolCallRecord(step=step, requested=name, arguments=args, status="error",
                                  detail=f"tool returned {type(result).__name__}, not ToolResult"), None

        return ToolCallRecord(step=step, requested=name, arguments=args, status="ok",
                              result=result), result

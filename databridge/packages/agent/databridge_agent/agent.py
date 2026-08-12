"""The agent loop: screen → plan → read → answer → screen.

Read-only, by construction. It plans with a model, calls tools it was handed, and composes an
answer whose citations it assembles **mechanically** from what those tools returned. There is no
step in which the model chooses a citation, no step in which it writes a value the tools did not
hold, and no step at which anything leaves the building — ``tools.py`` makes a send-capable tool
unconstructible, so the worst outcome of a fully suborned model here is a sentence a guardrail
refuses to print.

The shape of the loop is deliberately boring; the interesting decisions are the four below.

**The deterministic screen runs first, and the model call is downstream of it.** An identity
question is refused with ``model_calls == 0``. That is the property ``test_gate.py`` asserts for
the intake assistant and it holds here for the same reason: a refusal that depends on a model
reading a prompt carefully is not a refusal.

**Every tool result is framed as untrusted data** (``frame.py``). A tool result is graph data
derived from a document an outsider wrote; giving it a conversational turn would give the attacker
the model's voice.

**A model naming a tool is not a model calling one.** ``plan.tool`` is a string looked up in the
registry. An unregistered name — say, the ``send_email`` an injected document asks for — produces
a recorded refusal and a framed note, never an execution and never a crash. Since no send tool can
be registered at all, "the model asked to send" is a fact the answer can report, and nothing more.

**The answer is screened before staff see it.** Identity assertions, claims to have acted, and
NRIC-shaped identifiers that no tool returned are all refused deterministically after the model
has spoken (``guardrails.py``).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from databridge_core import SourcePointer
from databridge_harness import Harness

from . import frame as framing
from .guardrails import check_answer_values, screen_agent_output, screen_incoming
from .playbooks import Playbooks, playbooks as shipped_playbooks
from .tools import ToolRegistry, ToolResult

# The routing class for every model call this package makes. A NAME, never a model id: routing is
# config (`databridge/config/models.yaml`), so pointing the agent at a cheaper model — or at Azure
# OpenAI for the Copilot Studio story — is a config diff (CLAUDE.md §Working rules).
AGENT_TASK_CLASS = "agent_reasoning"

# Four steps is enough for every playbook in `agent_playbooks.yaml` (the widest names two tools)
# and short enough that an injected "keep calling tools" instruction runs out quickly. The repeat
# check below usually stops it sooner.
DEFAULT_MAX_STEPS = 4

NO_TOOLS_ANSWERED = (
    "Nothing in the clinic graph answered that. I would rather say so than fill the gap.")


class ToolPlan(BaseModel):
    """The model's single next step. It names a tool; it never receives one."""

    tool: Optional[str] = None
    arguments: dict = Field(default_factory=dict)
    reason: str = ""


class DraftAnswer(BaseModel):
    """The model's prose, before the output screen. Not what the caller gets back."""

    answer: str = ""


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
    status: str                     # ok | unknown_tool | repeated | bad_arguments | error
    detail: str = ""
    result: Optional[ToolResult] = None


class AgentAnswer(BaseModel):
    """What the agent hands back. Either a refusal, or an answer with its evidence.

    ``citations`` are composed by the loop from ``ToolResult.citations`` — the model has no way to
    add, drop or invent one. The validator is the backstop for any *other* constructor: an answer
    built on tool rows must carry the pointers behind them, on the same terms as ``GroundedValue``
    and ``history._Grounded`` (CLAUDE.md §Hard constraints 2).
    """

    model_config = ConfigDict(frozen=True)

    question: str
    answer: str
    citations: tuple[SourcePointer, ...] = ()
    tool_calls: tuple[ToolCallRecord, ...] = ()
    refused: bool = False
    refusal_code: str = ""
    model_calls: int = 0

    @model_validator(mode="after")
    def _evidence_rules(self) -> "AgentAnswer":
        if self.refused:
            if self.citations:
                raise ValueError(
                    "AgentAnswer REFUSED: a refusal must not carry citations — the refusal text is "
                    "fixed, and attaching evidence to it leaks what the agent declined to say.")
            if not self.refusal_code:
                raise ValueError("AgentAnswer REFUSED: a refusal with no code cannot be logged, "
                                 "tested or explained to a judge.")
            return self
        grounded_rows = any(c.result is not None and c.result.rows for c in self.tool_calls)
        if grounded_rows and not self.citations:
            raise ValueError(
                "AgentAnswer REFUSED: the tools returned rows and the answer cites nothing. Every "
                "value the agent reports is one the graph handed it, and the pointer comes with "
                "the value or the value does not go out.")
        return self

    @property
    def grounded(self) -> bool:
        return bool(self.citations)


class ClinicAgent:
    """Reads the clinic brain through injected tools and answers with citations.

    ``tools`` is a ``ToolRegistry`` the caller builds. This package imports neither
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
        self._tools = tools
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
            return AgentAnswer(question=question, answer=gate.answer, refused=True,
                               refusal_code=gate.code, model_calls=0)

        convo: list[dict] = [
            framing.system_message(self._tools.catalogue(), self.playbook_notes()),
            framing.question_message(question)]
        records: list[ToolCallRecord] = []
        observations: list[str] = []
        citations: list[SourcePointer] = []
        seen_calls: set[str] = set()
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

            record, result = self._run_tool(step, plan, seen_calls)
            records.append(record)
            if record.status == "repeated":
                break
            if result is not None:
                data = result.as_data()
                observations.append(data)
                convo.append({"role": "user",
                              "content": framing.frame(f"tool_result:{record.requested}", data)})
                for pointer in result.citations:
                    if pointer not in citations:
                        citations.append(pointer)
            else:
                convo.append({"role": "user", "content": framing.frame(
                    "tool_error",
                    json.dumps({"requested": record.requested, "status": record.status,
                                "detail": record.detail}, ensure_ascii=False))})

        draft = self._harness.complete_structured(
            task_class=self._task_class,
            messages=convo + [{"role": "user", "content": framing.ANSWER_INSTRUCTION}],
            response_model=DraftAnswer,
        ).parsed
        model_calls += 1

        text = (draft.answer or "").strip() or NO_TOOLS_ANSWERED
        for check in (screen_agent_output(text),
                      check_answer_values(text, "\n".join(observations))):
            if check.refused:
                # `check.code` is always set on a refusal — the guardrail constructors see to
                # that — and `AgentAnswer` refuses a code-less refusal, so a regression there
                # fails loudly here rather than shipping an unlabelled refusal to a log.
                return AgentAnswer(question=question, answer=check.answer,
                                   tool_calls=tuple(records), refused=True,
                                   refusal_code=check.code, model_calls=model_calls)

        return AgentAnswer(question=question, answer=text, citations=tuple(citations),
                           tool_calls=tuple(records), model_calls=model_calls)

    # -- one tool call -----------------------------------------------------------
    def _run_tool(
        self, step: int, plan: ToolPlan, seen_calls: set[str],
    ) -> tuple[ToolCallRecord, Optional[ToolResult]]:
        """Execute one planned call, converting every failure into a RECORD rather than a raise.

        A tool that raises must not take the agent down: the tools are injected, so a stub, an MCP
        client or a graph traversal can all fail in ways this package cannot anticipate, and an
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

        if not all(isinstance(k, str) for k in args):
            return ToolCallRecord(step=step, requested=name, arguments={},
                                  status="bad_arguments",
                                  detail="argument names must be strings"), None

        tool = self._tools.get(name)
        try:
            result: Any = tool.call(**args)
        except Exception as exc:                      # noqa: BLE001 — see the docstring
            return ToolCallRecord(step=step, requested=name, arguments=args, status="error",
                                  detail=f"{type(exc).__name__}: {exc}"[:300]), None

        if not isinstance(result, ToolResult):
            # A tool that returns a bare dict has skipped the grounding validator, so its rows
            # carry no pointers and the answer built on them could not be cited.
            return ToolCallRecord(step=step, requested=name, arguments=args, status="error",
                                  detail=f"tool returned {type(result).__name__}, not ToolResult"), None

        return ToolCallRecord(step=step, requested=name, arguments=args, status="ok",
                              result=result), result

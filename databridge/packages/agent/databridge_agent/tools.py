"""The tool registry — and the structural reason the agent cannot act.

Tools are **injected**. This module imports neither ``databridge_graph`` nor
``databridge_intake``; a caller hands ``ToolRegistry`` a list of ``Tool`` objects and the agent
calls what it was given. That is the same dependency inversion ``ClinicHistory`` uses
(``databridge_intake/history.py``, PLAN.md §6.0): the package that needs the capability declares
the interface, the package that has it implements it, and composition happens at the gateway. It
also means every test here runs against stubs, so a test never needs a graph to prove a safety
property.

**The whole safety story of this file is that a send tool cannot be registered, and that a
registered tool cannot be an arbitrary callable.** ``docs/hackathon/GRAPH_AGENT_AND_MCP.md`` §3
states the rule the design hangs on — *the agent never holds send credentials* — because patient
documents are attacker-controlled text and an agent that could send is one prompt injection away
from mailing patient data out.

Five layers, in the order they fire:

1. **``Tool.adapter`` must be a ``SealedReadAdapter``** (``sealed.py``). This is R-07's fix. The
   old ``call: Callable[..., ToolResult]`` accepted *anything* — a tool honestly marked
   ``effect="read"`` and named ``clinic_lookup`` could POST or write to the ledger, and this
   package would never know. A sealed adapter carries one of three closed read capabilities and is
   scanned at class definition for transport, vendor SDKs and write-shaped calls.
2. ``Tool.effect`` is ``Literal["read"]``. A send-capable tool is **unconstructible** — the same
   device as ``ResolutionResult.requires_human_selection: Literal[True]`` (CLAUDE.md §Hard
   constraints 1).
3. ``screen_declaration`` refuses a send-SHAPED name or description even if it is honestly marked
   ``read``. Layer 2 catches the honest mistake; this catches the tool called ``notify_insurer``
   that "only drafts, really".
4. ``policy.screen_parameters`` refuses an argument schema that is not closed, and refuses any
   tool whose arguments could name a patient. That is R-08: screening tool *names* stopped
   ``select_patient`` and did nothing about ``patient_record(patient_id=2)``.
5. It refuses an identity-DECISION-shaped name — ``select_patient``, ``verify_identity`` — so the
   agent has no such tool *at all*.

Layers 3–5 are deterministic matching over the declaration, and they run at REGISTRATION — before
any model has spoken. An injected instruction cannot summon a tool that was never registered, and
the model has no path to registration.

**The registry is frozen once the agent holds it.** ``ClinicAgent`` seals its registry in
``__init__``, and a sealed registry refuses ``register``. Previously the set of things the agent
could do was mutable for the lifetime of the process, so "the tool surface was reviewed" was a
statement about one moment rather than about the run.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Iterator, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from . import policy
from .results import ToolResult, ToolRow
from .sealed import (
    CandidateRead,
    ClinicStructuralRead,
    PatientScopedRead,
    ReadCapability,
    SealedReadAdapter,
)


class ToolError(RuntimeError):
    """A tool declaration was refused. Subclasses map to stable codes."""


class SendCapabilityRefused(ToolError):
    """A tool that could put something outside the building. The agent never gets one."""


class IdentityToolRefused(ToolError):
    """A tool that would let the agent decide who someone is. There is no such tool."""


class DuplicateTool(ToolError):
    """Two tools with one name — the agent would call whichever won, silently."""


class UnknownTool(ToolError):
    """The agent asked for a tool that is not registered. Raised, never guessed at."""


class RegistrySealed(ToolError):
    """The tool surface was frozen when the agent took it, and something tried to widen it."""


# --- the deterministic declaration screens ----------------------------------------

# Verbs that move information OUT. Matched as whole tokens in a tool NAME (`send_email`,
# `slack_post`, `notifyInsurer`), so `blocked_intakes` and `coverage_gap` pass and anything that
# reads like a transport does not.
#
# It over-matches on purpose: a read tool called `post_visit_summary` is refused because `post` is
# in the list. The refusal is loud and names the token, and renaming it to `visit_summary` costs
# nothing. If you land here, rename the tool — do not shorten this list, which is the only thing
# standing between a hurried afternoon and a tool that can talk to the outside world.
_SEND_TOKENS = frozenset({
    "send", "sends", "sent", "email", "emails", "mail", "mails", "post", "posts",
    "notify", "notifies", "notification", "dispatch", "dispatches", "publish", "publishes",
    "transmit", "transmits", "deliver", "delivers", "sms", "slack", "gmail", "smtp",
    "webhook", "dm", "message", "messages", "reply", "replies", "submit", "submits", "forward",
})

# A description screen, deliberately NARROWER than the name screen: a read tool may legitimately
# say it returns an insurer's contact email, and refusing that would push people to write vaguer
# descriptions — which costs more than it protects. So the pattern needs an action VERB with an
# outbound OBJECT, which is how a send tool describes itself and how a read tool does not.
_SEND_DESCRIPTION = re.compile(
    r"\b(?:send|sends|sending|email|emails|emailing|post|posts|posting|notify|notifies|"
    r"dispatch|dispatches|deliver|delivers|transmit|transmits|submit|submits)\b"
    r"\s+(?:a|an|the|one)?\s*"
    r"\b(?:e-?mail|message|mail|slack|notification|sms|letter|reply|dm|claim|draft|form)\b",
    re.I)

# An identity DECISION. The agent may surface candidates; it may not have a verb for choosing one.
_IDENTITY_NAME = re.compile(
    r"(?:^|[^a-z])(?:select|choose|pick|confirm|verify|validate|authenticate|resolve|match)"
    r"[_\- ]?(?:patient|identity|person|candidate|nric|fin|passport|ecard|e_card)"
    r"|(?:^|[^a-z])(?:patient|identity)[_\- ]?(?:select|selection|confirm|confirmation|"
    r"verify|verification|match)",
    re.I)


def _name_tokens(name: str) -> set[str]:
    """Split a tool name into lowercase words across snake_case, kebab-case and camelCase."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return {t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t}


def screen_declaration(name: str, description: str = "") -> None:
    """Raise if this tool declaration would give the agent a capability it must not have.

    Public because the playbook loader runs it over every tool a YAML file names: config is edited
    more casually than code, and ``config/agent_playbooks.yaml`` must not become a way to smuggle
    in a send step (CLAUDE.md §Hard constraints 3 makes config powerful; that cuts both ways).
    """
    offending = sorted(_name_tokens(name) & _SEND_TOKENS)
    if offending:
        raise SendCapabilityRefused(
            f"tool {name!r} REFUSED: the name contains {offending} — the agent has no send "
            f"capability of any kind, and a read tool should not be named like a transport "
            f"(GRAPH_AGENT_AND_MCP.md §3)")
    if description and _SEND_DESCRIPTION.search(description):
        raise SendCapabilityRefused(
            f"tool {name!r} REFUSED: its description describes an outbound action. Sending is the "
            f"dispatcher's job (T-46) and the dispatcher has no model in the loop")
    if _IDENTITY_NAME.search(name):
        raise IdentityToolRefused(
            f"tool {name!r} REFUSED: identity is decided in person by a named human. The agent "
            f"may report candidates and must have no verb for choosing one "
            f"(CLAUDE.md §Hard constraints 1)")


# --- the declared tool ------------------------------------------------------------


class Tool(BaseModel):
    """A declared capability: a sealed read adapter, exposed to the model under a screened name.

    Everything the model is shown comes from the adapter. There is no way to declare a name,
    description or schema that differs from the thing that will actually run, which is how a
    reviewed catalogue and an executed call get out of step.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    adapter: SealedReadAdapter
    effect: Literal["read"] = "read"

    @model_validator(mode="after")
    def _screen(self) -> "Tool":
        screen_declaration(self.name, self.description)
        policy.screen_parameters(self.name, self.parameters)
        return self

    # -- everything below is the adapter's declaration, never a parallel copy of it --
    @property
    def name(self) -> str:
        return self.adapter.name

    @property
    def description(self) -> str:
        return self.adapter.description

    @property
    def parameters(self) -> dict:
        return self.adapter.parameters

    @property
    def breadth(self) -> str:
        return self.adapter.breadth

    @property
    def grounded_arguments(self) -> frozenset:
        return self.adapter.grounded_arguments

    @property
    def capability(self) -> ReadCapability:
        return self.adapter.capability

    @property
    def requires_human_selection(self) -> bool:
        return self.adapter.requires_human_selection

    @property
    def subject(self) -> str:
        """The patient a patient-scoped tool is bound to — from a human's grant, not an argument."""
        return self.adapter.subject

    def call(self, **arguments: Any) -> ToolResult:
        """Run the adapter. Arguments are validated by the LOOP before this is reached."""
        return self.adapter(**arguments)

    def spec(self) -> dict:
        """The declaration shown to the model. The adapter is not in it — a model names a tool,
        it never receives a reference to one."""
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters, "effect": self.effect,
                "breadth": self.breadth, "scope": self.capability.scope,
                "requires_human_selection": self.requires_human_selection}


class ToolRegistry:
    """The closed set of things the agent can do. Nothing executes that is not in here.

    Frozen by ``seal()`` when an agent takes it. Before that a composition root builds it; after
    that the surface is fixed for the life of the object, so "these are the tools" is a statement
    about the run rather than about the moment somebody looked.
    """

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        self._sealed = False
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if self._sealed:
            raise RegistrySealed(
                f"tool {getattr(tool, 'name', tool)!r} REFUSED: this registry was sealed when an "
                f"agent took it. A tool surface that can grow after review is a tool surface "
                f"nobody has reviewed (REMEDIATION.md R-07)")
        if not isinstance(tool, Tool):
            raise ToolError(
                f"{type(tool).__name__} is not a Tool. The registry holds declarations backed by "
                f"sealed read adapters; a duck-typed stand-in would skip every screen.")
        if tool.name in self._tools:
            raise DuplicateTool(
                f"tool {tool.name!r} is already registered; a silently shadowed tool means the "
                f"agent calls one thing and the reviewer reads another")
        # Screened again here even though `Tool` screens at construction: `register` is the door
        # every tool comes through, and it must hold however the Tool was built.
        screen_declaration(tool.name, tool.description)
        policy.screen_parameters(tool.name, tool.parameters)
        self._tools[tool.name] = tool

    def seal(self) -> "ToolRegistry":
        self._sealed = True
        return self

    @property
    def sealed(self) -> bool:
        return self._sealed

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownTool(
                f"no tool named {name!r}; registered: {sorted(self._tools)}") from None

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def specs(self) -> tuple[dict, ...]:
        return tuple(policy.preferred_order(t.spec() for t in self._tools.values()))

    def catalogue(self) -> str:
        """The tool list as JSON, for the system message. Narrow reads first (``policy``)."""
        return json.dumps(list(self.specs()), ensure_ascii=False, sort_keys=True)

    def scopes(self) -> tuple[str, ...]:
        return tuple(sorted({t.capability.scope for t in self._tools.values()}))

    def patient_scoped(self) -> tuple[Tool, ...]:
        """Tools bound to one patient by a human's grant. Empty before an attestation exists."""
        return tuple(t for t in self._tools.values()
                     if isinstance(t.capability, PatientScopedRead))

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)


__all__ = [
    "CandidateRead",
    "ClinicStructuralRead",
    "DuplicateTool",
    "IdentityToolRefused",
    "PatientScopedRead",
    "RegistrySealed",
    "SendCapabilityRefused",
    "Tool",
    "ToolError",
    "ToolRegistry",
    "ToolResult",
    "ToolRow",
    "UnknownTool",
    "screen_declaration",
]

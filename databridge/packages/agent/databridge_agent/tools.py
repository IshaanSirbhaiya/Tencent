"""The tool registry — and the structural reason the agent cannot act.

Tools are **injected**. This module imports neither ``databridge_graph`` nor
``databridge_intake``; a caller hands ``ToolRegistry`` a list of ``Tool`` objects and the agent
calls what it was given. That is the same dependency inversion ``ClinicHistory`` uses
(``databridge_intake/history.py``, PLAN.md §6.0): the package that needs the capability declares
the interface, the package that has it implements it, and composition happens at the gateway. It
also means every test here runs against stubs, so a test never needs a graph to prove a safety
property.

**The whole safety story of this file is that a send tool cannot be registered.**
``docs/hackathon/GRAPH_AGENT_AND_MCP.md`` §3 states the rule the design hangs on — *the agent never
holds send credentials* — because patient documents are attacker-controlled text and an agent that
could send is one prompt injection away from mailing patient data out. Three layers make that a
property of the code rather than a promise in a prompt:

1. ``Tool.effect`` is ``Literal["read"]``. There is no other value, so a send-capable tool is
   **unconstructible** — the same device as
   ``ResolutionResult.requires_human_selection: Literal[True]`` (CLAUDE.md §Hard constraints 1).
   Adding a second value is a visible schema change that cannot happen by accident in a hurried
   demo.
2. ``ToolRegistry.register`` refuses a send-SHAPED name or description even if it is honestly
   marked ``read``. Layer 1 catches the honest mistake; this catches the tool called
   ``notify_insurer`` that "only drafts, really".
3. It refuses an identity-DECISION-shaped name — ``select_patient``, ``verify_identity`` — so the
   agent has no such tool *at all* (CLAUDE.md §Hard constraints 1). It may report "3 candidates,
   needs a human"; there is no callable through which it could choose one.

Layers 2 and 3 are deterministic string matching over the declaration, and they run at REGISTRATION
— before any model has spoken. An injected instruction cannot summon a tool that was never
registered, and the model has no path to registration.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from databridge_core import SourcePointer


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


# --- what a tool returns ----------------------------------------------------------


class ToolResult(BaseModel):
    """One tool's answer: rows of data, and the pointers they came from.

    ``rows`` are plain JSON-able mappings — whatever the graph returned. ``citations`` are the
    ``SourcePointer``s behind them, and the validator below refuses a result that has rows but no
    pointers. The agent composes its citations mechanically from these, so a tool that returned
    facts with nothing to click through to would produce a cited-looking answer with no evidence
    under it — the exact failure ``GroundedValue`` exists to make impossible (CLAUDE.md
    §Hard constraints 2).

    A tool that legitimately found nothing returns no rows and, honestly, no citations: "nothing is
    blocked today" is not a claim about a document. ``note`` carries that.
    """

    model_config = ConfigDict(frozen=True)

    tool: str
    rows: tuple[dict, ...] = ()
    citations: tuple[SourcePointer, ...] = ()
    note: str = ""

    @model_validator(mode="after")
    def _rows_must_be_grounded(self) -> "ToolResult":
        if self.rows and not self.citations:
            raise ValueError(
                f"ToolResult({self.tool!r}) REFUSED: {len(self.rows)} row(s) and no SourcePointer. "
                f"The agent may only compose values the graph already returned, and it composes "
                f"its citations from this field — an ungrounded row would become an uncitable "
                f"sentence.")
        return self

    def as_data(self) -> str:
        """The rows as JSON, for the framed observation the model sees.

        JSON, not prose: a sentence inside a JSON string value reads to a model as *the value of a
        field*, which is what it is. Prose formatting is what makes injected text look like a line
        of the conversation.
        """
        payload: dict[str, Any] = {"tool": self.tool, "rows": [dict(r) for r in self.rows]}
        if self.note:
            payload["note"] = self.note
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


class Tool(BaseModel):
    """A declared capability: a name, a description, a JSON-Schema for its arguments, a callable.

    ``effect`` is the constraint. ``Literal["read"]`` means a send-capable tool cannot be
    constructed — not "is not constructed", *cannot be*. See the module docstring.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str
    description: str
    parameters: dict = Field(default_factory=dict)      # JSON Schema for the arguments object
    call: Callable[..., ToolResult]
    effect: Literal["read"] = "read"

    @model_validator(mode="after")
    def _screen(self) -> "Tool":
        screen_declaration(self.name, self.description)
        return self

    def spec(self) -> dict:
        """The declaration shown to the model. The callable is not in it — a model names a tool,
        it never receives a reference to one."""
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters, "effect": self.effect}


class ToolRegistry:
    """The closed set of things the agent can do. Nothing executes that is not in here."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise DuplicateTool(
                f"tool {tool.name!r} is already registered; a silently shadowed tool means the "
                f"agent calls one thing and the reviewer reads another")
        # Screened again here even though `Tool` screens at construction: `register` is the door
        # every tool comes through, and it must hold for a duck-typed stand-in too.
        screen_declaration(tool.name, tool.description)
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownTool(
                f"no tool named {name!r}; registered: {sorted(self._tools)}") from None

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def specs(self) -> tuple[dict, ...]:
        return tuple(t.spec() for t in self._tools.values())

    def catalogue(self) -> str:
        """The tool list as JSON, for the system message."""
        return json.dumps([t.spec() for t in self._tools.values()],
                          ensure_ascii=False, sort_keys=True)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

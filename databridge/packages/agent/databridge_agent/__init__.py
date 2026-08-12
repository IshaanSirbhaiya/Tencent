"""databridge_agent — the read-only clinic agent (T-40).

It reads the clinic brain through **injected** tools and answers with citations. Three things it
cannot do, each structurally rather than by instruction:

* **It cannot send.** No email, no Slack, no submission, no stub that could later be pointed at a
  transport. ``Tool.effect`` is ``Literal["read"]`` and the registry refuses a send-shaped
  declaration, so an injected document asking the agent to mail the patient list finds nothing to
  call (``tools.py``; ``GRAPH_AGENT_AND_MCP.md`` §3).
* **It cannot decide who a person is.** There is no ``select_patient`` tool and the registry
  refuses to hold one. It reports candidates and says a human must choose, in person
  (CLAUDE.md §Hard constraints 1).
* **It cannot state a value the graph did not return.** Citations are composed by the loop from
  what the tools handed back; the model never picks them (CLAUDE.md §Hard constraints 2).

Import the public surface from here, not from submodules.
"""
from __future__ import annotations

from .agent import (
    AGENT_TASK_CLASS,
    AgentAnswer,
    ClinicAgent,
    DraftAnswer,
    ToolCallRecord,
    ToolPlan,
)
from .guardrails import (
    IDENTITY_REFUSAL_CODE,
    IDENTITY_REFUSAL_TEXT,
    NO_SEND_CODE,
    NO_SEND_TEXT,
    UNGROUNDED_VALUE_CODE,
    UNGROUNDED_VALUE_TEXT,
    check_answer_values,
    screen_agent_output,
    screen_incoming,
)
from .playbooks import (
    IssuerRouting,
    IssuerTable,
    Playbook,
    PlaybookError,
    Playbooks,
    load_playbooks,
    playbooks,
)
from .tools import (
    DuplicateTool,
    IdentityToolRefused,
    SendCapabilityRefused,
    Tool,
    ToolError,
    ToolRegistry,
    ToolResult,
    UnknownTool,
    screen_declaration,
)

__all__ = [
    # agent
    "AGENT_TASK_CLASS",
    "AgentAnswer",
    "ClinicAgent",
    "DraftAnswer",
    "ToolCallRecord",
    "ToolPlan",
    # tools
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "ToolError",
    "SendCapabilityRefused",
    "IdentityToolRefused",
    "DuplicateTool",
    "UnknownTool",
    "screen_declaration",
    # guardrails
    "screen_incoming",
    "screen_agent_output",
    "check_answer_values",
    "IDENTITY_REFUSAL_CODE",
    "IDENTITY_REFUSAL_TEXT",
    "NO_SEND_CODE",
    "NO_SEND_TEXT",
    "UNGROUNDED_VALUE_CODE",
    "UNGROUNDED_VALUE_TEXT",
    # playbooks
    "Playbook",
    "Playbooks",
    "PlaybookError",
    "IssuerRouting",
    "IssuerTable",
    "load_playbooks",
    "playbooks",
]

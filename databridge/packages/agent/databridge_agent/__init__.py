"""databridge_agent — the read-only clinic agent (T-40).

It reads the clinic brain through **injected, sealed** adapters and answers with citations. Four
things it cannot do, each structurally rather than by instruction:

* **It cannot send.** No email, no Slack, no submission, no stub that could later be pointed at a
  transport. ``Tool.effect`` is ``Literal["read"]``, the registry refuses a send-shaped
  declaration, and an adapter class defined in a module that imports a transport is refused at
  import (``sealed.py``). An injected document asking the agent to mail the patient list finds
  nothing to call.
* **It cannot decide who a person is.** There is no ``select_patient`` tool, the registry refuses
  to hold one, and — the part R-08 found missing — **no tool schema may declare a patient,
  candidate or identity argument**, so the decision cannot be smuggled in as
  ``patient_record(patient_id=2)`` either. A patient-scoped read exists only when a human has
  created a ``PatientReadGrant``, and its subject is bound at construction (CLAUDE.md §Hard
  constraints 1).
* **It cannot state a value the graph did not return.** ``AgentAnswer.answer`` is a rendered
  property, not a field: the model returns typed references, and code prints each value beside the
  ``SourcePointer`` that value itself carries (CLAUDE.md §Hard constraints 2).
* **It cannot author identity language.** The human-selection wording is a fixed sentence in
  ``render.NOTES``, added by code whenever a candidate-returning capability was read.

Import the public surface from here, not from submodules.
"""
from __future__ import annotations

from .agent import (
    AGENT_TASK_CLASS,
    MAX_REPORTED_VALUES,
    AgentAnswer,
    AnswerPlan,
    ClinicAgent,
    GroundedField,
    ToolCallRecord,
    ToolPlan,
    ValueRef,
)
from .guardrails import (
    IDENTITY_REFUSAL_CODE,
    IDENTITY_REFUSAL_TEXT,
    NO_SEND_CODE,
    NO_SEND_TEXT,
    UNGROUNDED_VALUE_CODE,
    UNGROUNDED_VALUE_TEXT,
    check_answer_values,
    looks_like_injection,
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
from .policy import (
    ArgumentSchemaViolation,
    IdentityArgumentRefused,
    PolicyError,
    SchemaNotStrict,
    UngroundedArgument,
    check_argument_grounding,
    screen_parameters,
    validate_arguments,
)
from .adapters import StaticReadAdapter
# NOTE: only names, never the `render` function itself — binding `databridge_agent.render` to a
# function would shadow the submodule of the same name, and a test importing the module would
# silently get a callable instead.
from .render import NOTES, note_text
from .results import ToolResult, ToolRow
from .sealed import (
    AdapterRefused,
    AdapterSourceUnavailable,
    CandidateRead,
    ClinicStructuralRead,
    GrantRefused,
    PatientReadGrant,
    PatientScopedRead,
    SealBroken,
    SealedReadAdapter,
    TransportInAdapter,
    WriterInAdapter,
    scan_source,
)
from .tools import (
    DuplicateTool,
    IdentityToolRefused,
    RegistrySealed,
    SendCapabilityRefused,
    Tool,
    ToolError,
    ToolRegistry,
    UnknownTool,
    screen_declaration,
)

__all__ = [
    # agent
    "AGENT_TASK_CLASS",
    "MAX_REPORTED_VALUES",
    "AgentAnswer",
    "AnswerPlan",
    "ClinicAgent",
    "GroundedField",
    "ToolCallRecord",
    "ToolPlan",
    "ValueRef",
    # tools
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "ToolRow",
    "ToolError",
    "SendCapabilityRefused",
    "IdentityToolRefused",
    "DuplicateTool",
    "RegistrySealed",
    "UnknownTool",
    "screen_declaration",
    # sealed read adapters + capabilities
    "AdapterRefused",
    "AdapterSourceUnavailable",
    "CandidateRead",
    "ClinicStructuralRead",
    "GrantRefused",
    "PatientReadGrant",
    "PatientScopedRead",
    "SealBroken",
    "SealedReadAdapter",
    "StaticReadAdapter",
    "TransportInAdapter",
    "WriterInAdapter",
    "scan_source",
    # deterministic call policy
    "ArgumentSchemaViolation",
    "IdentityArgumentRefused",
    "PolicyError",
    "SchemaNotStrict",
    "UngroundedArgument",
    "check_argument_grounding",
    "screen_parameters",
    "validate_arguments",
    # rendering
    "NOTES",
    "note_text",
    # guardrails
    "screen_incoming",
    "screen_agent_output",
    "check_answer_values",
    "looks_like_injection",
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

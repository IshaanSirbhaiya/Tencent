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
* **It cannot author identity language, and it cannot recommend a candidate by showing a subset.**
  The human-selection wording is a fixed sentence in ``render.NOTES``, added by code whenever a
  candidate-returning capability was read; the candidate rows themselves are printed in full, in a
  code-fixed order, with no input from the model (``agent._compose``).

**Returning an answer from a route:** call ``AgentAnswer.public()`` and return the ``AgentResponse``
(``response.py``). ``AgentAnswer`` is the audit record — it serializes raw model arguments and
unredacted rows, and it does *not* serialize the rendered answer at all.

**What is NOT guaranteed — read this before wiring this package to anything.** The four properties
above are enforced against *the model*: they hold however the model behaves, including a model
fully suborned by an injected document. They are **not** enforced against the process. Two limits
in particular, both stated at length where they live:

* *The read-only scan is not a sandbox* (``sealed.py``). It reads the text of the one module an
  adapter class is defined in. It does not follow imports, and it cannot see through ``getattr``,
  ``importlib``, ``eval``, an injected callable or a handle captured elsewhere. Do not give this
  process network egress, mail credentials or a writable ledger handle on the strength of it.
* *A ``PatientReadGrant`` is an assertion, not proof of attestation* (``sealed.py``). It records
  that somebody said a named human selected a patient in person; nothing here binds that to an
  authenticated session. The composition root must build grants from server-side attestation state
  and must never deserialize one from a request.

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
    gate_answer,
    screen_question,
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
    SelectorArgumentRefused,
    UngroundedArgument,
    check_argument_grounding,
    screen_parameters,
    validate_arguments,
    walk_schema,
)
from .response import (
    READ_STATUSES,
    AgentNote,
    AgentResponse,
    AnswerValue,
    ReadRecord,
    to_response,
)
from .adapters import StaticReadAdapter
# NOTE: only names, never the `render` function itself — binding `databridge_agent.render` to a
# function would shadow the submodule of the same name, and a test importing the module would
# silently get a callable instead.
from .render import NOTES, UNKNOWN_TOOL_NAME, note_text, public_name
from .results import ToolResult, ToolRow
from .sealed import (
    AdapterRefused,
    AdapterSealMutated,
    AdapterSourceUnavailable,
    CandidateRead,
    ClinicStructuralRead,
    GrantRefused,
    PatientReadGrant,
    PatientScopedRead,
    SealBroken,
    SealedReadAdapter,
    SubjectMismatch,
    TransportInAdapter,
    WriterInAdapter,
    scan_source,
)
from .tools import (
    DuplicateTool,
    IdentityToolRefused,
    MultipleSubjectsRefused,
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
    "UNKNOWN_TOOL_NAME",
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
    "MultipleSubjectsRefused",
    "RegistrySealed",
    "UnknownTool",
    "screen_declaration",
    # sealed read adapters + capabilities
    "AdapterRefused",
    "AdapterSealMutated",
    "AdapterSourceUnavailable",
    "CandidateRead",
    "ClinicStructuralRead",
    "GrantRefused",
    "PatientReadGrant",
    "PatientScopedRead",
    "SealBroken",
    "SealedReadAdapter",
    "StaticReadAdapter",
    "SubjectMismatch",
    "TransportInAdapter",
    "WriterInAdapter",
    "scan_source",
    # deterministic call policy
    "ArgumentSchemaViolation",
    "IdentityArgumentRefused",
    "PolicyError",
    "SchemaNotStrict",
    "SelectorArgumentRefused",
    "UngroundedArgument",
    "check_argument_grounding",
    "screen_parameters",
    "validate_arguments",
    "walk_schema",
    # the public response contract — what a route returns
    "READ_STATUSES",
    "AgentNote",
    "AgentResponse",
    "AnswerValue",
    "ReadRecord",
    "to_response",
    # rendering
    "NOTES",
    "gate_answer",
    "note_text",
    "public_name",
    "screen_question",
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

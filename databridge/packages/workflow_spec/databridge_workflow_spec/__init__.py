"""**databridge_workflow_spec** — the frozen contract for user-defined agents and adapter agents.

`BUILD_PLAN_KNOWLEDGE_PLATFORM.md` §5.6 is the prose form of this module; they must agree.

* A **WorkflowSpec** is the *behaviour* half of an agent: its instructions, the sources and tools it
  may use, its declared write actions, and its trigger. The *governance* half is an `agentdef`
  declaration (grants, actions with `automation_mode`, budget, citation). An agent runs only when
  both exist and `compile_agent` accepts the declaration.
* An **AdapterAgentSpec** manages sources: which connections, which crawl plans, when to sync, how
  to screen and extract, and how fresh the data must be.

Validation here is structural and deterministic (P16: rules first). Whether a named connection,
tool or ontology kind actually exists is checked by the English compiler and the runtime against
the live catalogue — this module has no I/O.

A validated spec is deeply immutable (its sequences are tuples and its maps read-only views, so no
container mutator exists to call), so what was validated is what runs (R-017, R-021). Digest a spec
with `spec_digest` (canonical JSON), never with the presentation YAML of `dump_spec`.
"""
from __future__ import annotations

from .models import (
    ALWAYS_ASKED,
    AUTOMATION_MODES,
    BY_TO_PRODUCED_BY,
    DRIFT_KINDS,
    AdaptPolicy,
    AdaptWatch,
    ActionSpec,
    AdapterAgentSpec,
    Derivation,
    EventTrigger,
    ExtractPolicy,
    Freshness,
    FrozenDict,
    FrozenList,
    Limits,
    Outputs,
    PRODUCED_BY,
    ScheduleTrigger,
    SpecError,
    Trigger,
    WorkflowSpec,
    canonical_json,
    check_cron,
    check_timezone,
    dump_spec,
    is_identifier,
    load_spec,
    parse_tool,
    spec_digest,
)

__all__ = [
    "ALWAYS_ASKED", "AUTOMATION_MODES", "DRIFT_KINDS", "AdaptPolicy", "AdaptWatch", "Derivation", "ActionSpec", "AdapterAgentSpec", "EventTrigger", "ExtractPolicy",
    "Freshness", "FrozenDict", "FrozenList", "Limits", "Outputs", "ScheduleTrigger", "SpecError", "Trigger", "WorkflowSpec",
    "check_cron", "check_timezone", "dump_spec", "load_spec", "parse_tool",
    "BY_TO_PRODUCED_BY", "PRODUCED_BY", "canonical_json", "is_identifier", "spec_digest",
]

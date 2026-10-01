"""**databridge_automation** — plain English in, a governed automation out; and the trigger engine.

Two halves (BUILD_PLAN_KNOWLEDGE_PLATFORM §6 T):

* `compile_english(text, catalogue, *, harness)` — rules first (schedule / event / source /
  write-intent / approval-phrase detection, no model), then the model through the harness for what
  rules leave open, then deterministic validation against the live `Catalogue`. Returns a
  `DraftResult`: the `WorkflowSpec` or `AdapterAgentSpec`, the agentdef declaration (compiled by
  `compile_agent`), a plain-English "here is what I understood", and open questions instead of
  guesses. `save_draft()` writes the §5.6 files and refuses activation of anything not compiled.
* `TriggerEngine` — asyncio service: timezone-aware cron, §5.3 event tailing with per-consumer
  byte offsets, manual "run now", idempotency keys, missed-run = skip-and-record, every firing in
  `var/automation/firings.jsonl`, dispatch through an injectable `runner(spec, trigger_info)`.

CLI: ``python -m databridge_automation {compile "text" | serve | fire <id> | status}``.
"""
from __future__ import annotations

from .catalogue import (
    AdapterAgentInfo,
    Catalogue,
    CatalogueLike,
    ConnectionInfo,
    Governance,
    ToolRef,
    classify_tool,
    load_catalogue,
    to_adapter_catalogue,
)
from .compiler import (
    ActivationRefused,
    federated_question_id,
    DraftResult,
    ModelProposal,
    PROMPT_VERSION,
    compile_english,
    save_draft,
)
from .cron import CronError, CronSchedule
from .declaration import Compilation, compile_declaration, declaration_from_workflow
from .engine import (
    ClaimStore,
    FiringLog,
    TriggerEngine,
    admission_refusal,
    event_matches,
    load_specs_from_dirs,
    spec_digest,
)
from .prepass import PrePass, Question, prepass

__all__ = [
    "Catalogue", "CatalogueLike", "ConnectionInfo", "Governance", "ToolRef", "classify_tool",
    "load_catalogue", "to_adapter_catalogue", "AdapterAgentInfo", "federated_question_id",
    "compile_english", "DraftResult", "ModelProposal", "save_draft", "ActivationRefused",
    "CronError", "CronSchedule",
    "Compilation", "compile_declaration", "declaration_from_workflow",
    "TriggerEngine", "FiringLog", "ClaimStore", "admission_refusal", "event_matches",
    "load_specs_from_dirs", "spec_digest", "PROMPT_VERSION",
    "PrePass", "Question", "prepass",
]

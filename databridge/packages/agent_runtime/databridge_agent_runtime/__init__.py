"""**databridge_agent_runtime** — runs a user-defined agent, governed (BUILD_PLAN §6 H).

An agent = a `WorkflowSpec` (behaviour, §5.6) + an agentdef declaration (governance). This package:

* maps one onto the other (`declaration_from_workflow`, `capability_for_tool`) and refuses to run
  an agent whose halves disagree or whose declaration `compile_agent` refuses — no override;
* gives the planner (harness ``task_class="agent"``) four tool families — ``graph.*`` over the
  shared store, ``retrieve.search`` (§5.5), and ``source.read/query`` through a `SourceGateway`
  (§5.4) — each call admitted by the compiled grants, live reads captured (§5.2) before they are
  cited;
* checks the answer against P15 (every claim cites pointers; unsupported claims flagged; question
  verbatim; clarification block; reduced coverage on top);
* turns writes into action proposals gated by ``human_required | human_notified | autonomous``,
  with `approve` / `reject` / `reverse` for people;
* writes a run record with exactly the §5.7 keys under ``WALNUT_RUNS_DIR`` and records every step
  into a gateway `TraceHandle` when one is passed.
"""
from __future__ import annotations

from .answer import (
    ActionReport,
    ActionRequest,
    Claim,
    Clarification,
    FinalAnswer,
    PlannerTurn,
    ToolCallRequest,
    check_action_claims,
    check_answer,
)
from .capture import capture_live_read
from .federation import (
    READ_CHANNELS,
    Federation,
    ReadAdmission,
    ToolLayerAdmission,
    federated_answer,
)
from .mapping import (
    ConnectionAdvert,
    Governance,
    MappingError,
    ToolAdvert,
    build_catalogue,
    capability_for_tool,
    declaration_from_workflow,
    load_agent,
    load_declaration_file,
    pair_problems,
    parse_declaration_text,
    spec_digest,
)
from .runs import (
    ACTION_STATUSES,
    RUN_STATUSES,
    ActionLog,
    RunRecord,
    find_run,
    list_runs,
    read_run,
    run_path,
)
from .classify import Classification, plan_denied_tools, plan_read_tools
from .classify import classify_tool as classify_mcp_tool
from .runtime import (
    AGENT_TASK_CLASS,
    PLANNER_PROMPT_VERSION,
    ActionRefused,
    Approver,
    Authenticator,
    CompileInputs,
    is_issued_principal,
    register_authenticator,
    approve,
    args_schema_problems,
    compile_for_run,
    pending_actions,
    reject,
    reverse,
    run_agent,
)
from .tools import (
    FactsStore,
    McpFactsStore,
    default_facts,
    GatewayResult,
    McpSourceGateway,
    PointerRegistry,
    RetrievalModuleRetriever,
    Retriever,
    SourceGateway,
    ToolLayer,
    default_gateway,
    default_retriever,
    classify_tools,
    sql_is_read_only,
)

__all__ = [
    "AGENT_TASK_CLASS", "PLANNER_PROMPT_VERSION", "ACTION_STATUSES", "RUN_STATUSES",
    "run_agent", "approve", "reject", "reverse", "pending_actions", "compile_for_run",
    "CompileInputs", "ActionRefused", "Approver", "Authenticator", "register_authenticator",
    "is_issued_principal", "RunRecord", "ActionLog", "find_run", "list_runs",
    "read_run", "run_path",
    "declaration_from_workflow", "Governance", "capability_for_tool", "build_catalogue",
    "ConnectionAdvert", "ToolAdvert", "MappingError", "load_agent", "load_declaration_file",
    "parse_declaration_text", "pair_problems", "spec_digest",
    "SourceGateway", "GatewayResult", "McpSourceGateway", "default_gateway", "Retriever",
    "RetrievalModuleRetriever", "default_retriever", "ToolLayer", "PointerRegistry",
    "sql_is_read_only", "classify_tools", "capture_live_read", "FactsStore",
    "McpFactsStore", "default_facts",
    "PlannerTurn", "ToolCallRequest", "ActionRequest", "ActionReport", "FinalAnswer", "Claim", "Clarification",
    "check_answer", "check_action_claims", "args_schema_problems",
    "Classification", "classify_mcp_tool", "plan_read_tools", "plan_denied_tools",
    "READ_CHANNELS", "Federation", "ReadAdmission", "ToolLayerAdmission", "federated_answer",
]

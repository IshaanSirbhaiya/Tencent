"""databridge_mcp_ingest — connect any MCP server, and Walnut extracts from it on its own.

    declare (connection YAML) → plan (profiles first, model second, approved) → collect (capture
    raw bytes, content-addressed) → extract (docling / text) → screen (deterministic, pre-model)
    → build (closed-vocabulary graph, every value with a pointer) → verify (reconcile) → report

Public API (the gateway's batch-2 routes call these):

    register_connection(spec) -> SourceConnection
    import_declarations(path=None, route="direct") -> [SourceConnection]   # infra/mcp_servers.yaml
    propose_plan(source_id, harness=None) -> CrawlPlan
    approve_plan(source_id, principal=<authenticated principal>) -> CrawlPlan
    approve_mapping(source_id, principal=<authenticated principal>) -> SourceMapping
    run_ingest(source_id, store, harness, progress_cb) -> RunReport
    list_runs(source_id) -> [dict]
    verify_run(source_id, run_id) -> dict
    open_session(connection_or_id)  # async context manager — BUILD_PLAN §5.4

See KNOWLEDGE_SYSTEM_PRINCIPLES.md §1–§4 and §8.
"""
from __future__ import annotations

from .client import AsyncSession, McpClientError, open_client, open_session
from .connections import (ConnectionError_, Scope, SourceConnection, import_declarations,
                          list_connections, load_connection, register_connection)
from .mapping import approve_mapping, load_mapping
from .plans import CrawlPlan, PlanError, approve_plan, load_plan, propose_plan
from .runner import (NotAnIngestConnection, PlanNotApproved, RunReport, list_runs,
                     run_ingest, verify_run)


def install_adapter_hooks(manager=None) -> dict:
    """The gateway's ONE boot call for adapter-agent drift hooks (idempotent):
    `list_changed` — an MCP server's tools/resources list_changed notification (pooled sessions
    included) re-checks that connection now (`AdapterAgentManager.on_list_changed`);
    `plan_approval` — a plan approved with an authenticated principal that re-plans held steps is
    handed to the adapter agents to decide. Each value says whether that hook is live."""
    from .client import install_list_changed_hook
    from .plans import install_plan_approval_hook

    return {"list_changed": install_list_changed_hook(manager),
            "plan_approval": install_plan_approval_hook(manager)}

__all__ = [
    "SourceConnection", "Scope", "ConnectionError_", "register_connection", "load_connection",
    "list_connections", "import_declarations",
    "CrawlPlan", "PlanError", "propose_plan", "approve_plan", "load_plan",
    "approve_mapping", "load_mapping",
    "run_ingest", "verify_run", "list_runs", "RunReport", "PlanNotApproved",
    "NotAnIngestConnection",
    "open_session", "open_client", "AsyncSession", "McpClientError",
    "install_adapter_hooks",
]

"""Wiring for callers outside this package: the default manager and the TriggerEngine runner.

`sync_runner(spec, trigger_info)` is what the trigger engine (`packages/automation`, agent T)
calls when an adapter agent's `sync` trigger fires. It re-reads the spec from the registry when
the agent is registered — the registry is the source of truth, so a pause that landed after the
engine loaded the spec is honoured — and returns the sync summary as a plain dict.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional, Union

from databridge_workflow_spec import AdapterAgentSpec

from . import paths
from .ingest_service import McpIngestService
from .manager import AdapterAgentManager
from .registry import AdapterAgentRegistry, RegistryError

_DEFAULT: Optional[AdapterAgentManager] = None
_GUARD = threading.Lock()


def open_shared_store(path: Optional[Path] = None):
    """The §5.1 shared graph store, file-backed. Ingest is the single writer (§11)."""
    from databridge_graph.store import GraphStore

    target = path or paths.graph_db_path()
    if str(target) != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    return GraphStore(str(target))


def default_manager() -> AdapterAgentManager:
    """A process-wide manager over the real ingest pipeline and the shared store. The harness is
    `None` here, and `run_ingest(harness=None)` builds document anchors ONLY — no entity
    extraction, so no refusals and no ontology proposals. The gateway installs its own manager
    (with its harness) through `set_default_manager`; a standalone caller that wants extraction
    must do the same."""
    global _DEFAULT
    with _GUARD:
        if _DEFAULT is None:
            _DEFAULT = AdapterAgentManager(service=McpIngestService(),
                                           registry=AdapterAgentRegistry(),
                                           store_factory=open_shared_store)
        return _DEFAULT


def set_default_manager(manager: Optional[AdapterAgentManager]) -> None:
    """Let the gateway (or a test) install the manager `sync_runner` uses."""
    global _DEFAULT
    with _GUARD:
        _DEFAULT = manager


def sync_runner(spec: Union[AdapterAgentSpec, str], trigger_info: Optional[dict[str, Any]] = None,
                *, manager: Optional[AdapterAgentManager] = None) -> dict[str, Any]:
    """TriggerEngine entry point. `trigger_info` is recorded verbatim on the run (§5.7)."""
    m = manager or default_manager()
    agent_id = spec if isinstance(spec, str) else spec.id
    trigger = dict(trigger_info or {})
    if "type" not in trigger:
        trigger["type"] = (spec.sync.type if isinstance(spec, AdapterAgentSpec) else "manual")
    try:
        current = m.registry.get(agent_id)
    except RegistryError as exc:
        if isinstance(spec, AdapterAgentSpec) and not m.registry.exists(agent_id):
            current = spec           # an unregistered spec the engine holds directly
        else:
            return {"agent_id": agent_id, "status": "refused", "reason": str(exc),
                    "trigger": trigger, "connections": []}
    return m.sync(agent_id, trigger=trigger, spec=current).as_dict()


def install_list_changed_hook(manager: Optional[AdapterAgentManager] = None) -> bool:
    """Register with `databridge_mcp_ingest.client` so an MCP server's `tools/list_changed` (or
    `resources/list_changed`) notification makes every agent managing that connection re-check
    its shape (`AdapterAgentManager.on_list_changed`). The listener only starts a worker thread —
    it never blocks the client's event loop. Returns False while the client offers no
    `add_list_changed_listener` (P's hook; the diff is in wiki/40-reviews/AA-*.md)."""
    try:
        from databridge_mcp_ingest import client
    except ImportError:
        return False
    add = getattr(client, "add_list_changed_listener", None)
    if not callable(add):
        return False

    def listener(connection_id: str, kind: str = "tools") -> None:
        m = manager or default_manager()
        threading.Thread(target=m.on_list_changed, args=(connection_id,), kwargs={"kind": kind},
                         daemon=True, name=f"list-changed-{connection_id}").start()

    add(listener)
    return True


def install_plan_approval_hook(manager: Optional[AdapterAgentManager] = None) -> bool:
    """Register with `databridge_mcp_ingest.plans` so a plan approved with an authenticated
    principal that re-plans held steps decides those adaptations
    (`AdapterAgentManager.on_plan_approved`). Synchronous: the approval returns once the agents
    have decided. False when the ingest package offers no such listener API."""
    try:
        from databridge_mcp_ingest import plans
    except ImportError:
        return False
    add = getattr(plans, "add_plan_approval_listener", None)
    if not callable(add):
        return False

    def listener(connection_id: str, replanned: list, principal: Any) -> list:
        return (manager or default_manager()).on_plan_approved(connection_id, replanned,
                                                               by=principal)

    add(listener)
    return True


__all__ = ["sync_runner", "default_manager", "set_default_manager", "open_shared_store",
           "install_list_changed_hook", "install_plan_approval_hook"]

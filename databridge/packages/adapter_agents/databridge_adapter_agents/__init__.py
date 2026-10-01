"""**databridge_adapter_agents** — adapter agents manage data sources (BUILD_PLAN §6 "M").

An adapter agent (`AdapterAgentSpec`, §5.6) is a managed group of MCP connections: their approved
crawl plans, when they sync, the screen/extract policy, a freshness target, health, and proposals
for ontology kinds it keeps meeting and cannot store. Agents automate work; adapter agents keep
the data right.

Python API (what the gateway wraps)::

    m = AdapterAgentManager(service=McpIngestService(), store_factory=open_shared_store)
    m.create(yaml_or_dict_or_spec, by=...)   m.update(id, {...}, by=...)
    m.pause(id) / m.resume(id) / m.delete(id, reason=...)   # delete is a status, files are kept
    m.sync(id, trigger={...}) -> SyncSummary                 # per-connection isolation
    m.health(id) -> dict                                     # freshness, failures, counts
    m.refresh_plans(id)                                      # mirror on-disk plan status
    sync_runner(spec, trigger_info)                          # the TriggerEngine's callable
    m.pending_adaptations(id) / m.approve_adaptation(id, aid, by=<principal>)
    m.reject_adaptation(id, aid, by=<principal>, reason=...)   # shape drift (adapt.py)
    m.on_list_changed(connection_id)                         # an MCP list_changed notification
"""
from __future__ import annotations

from .adapt import (AdaptationEngine, AdaptationRefused, AdaptAuditError, Policy as AdaptPolicy,
                    trust_principal_type)
from .events import EVENT_KEYS, emit, read_events
from .ingest_service import (IngestRunReport, IngestService, IngestUnavailable, McpIngestService,
                             normalize_report, read_run_reports)
from .manager import AdapterAgentManager, ConnectionResult, SyncSummary
from .proposals import ActiveVocabularyView, ProposalLog, ProposalPolicy, file_proposals
from .registry import AdapterAgentRegistry, RegistryError, spec_digest
from .runner import (default_manager, install_list_changed_hook, install_plan_approval_hook,
                     open_shared_store,
                     set_default_manager, sync_runner)

__all__ = [
    "AdaptationEngine", "AdaptationRefused", "AdaptAuditError", "AdaptPolicy",
    "install_list_changed_hook", "install_plan_approval_hook", "trust_principal_type",
    "AdapterAgentManager", "AdapterAgentRegistry", "ActiveVocabularyView", "ConnectionResult",
    "EVENT_KEYS", "IngestRunReport", "IngestService", "IngestUnavailable", "McpIngestService",
    "ProposalLog", "ProposalPolicy", "RegistryError", "SyncSummary", "default_manager", "emit",
    "file_proposals", "normalize_report", "open_shared_store", "read_events", "read_run_reports",
    "set_default_manager", "spec_digest", "sync_runner",
]

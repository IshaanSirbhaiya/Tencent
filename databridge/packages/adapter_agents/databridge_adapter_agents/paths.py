"""Where adapter-agent state lives. Every location has an env override; defaults sit under `var/`.

    WALNUT_ADAPTER_AGENTS_DIR  var/adapter_agents      <id>.yaml specs (§5.6) + _state/ _history/
                                                       _proposals/ (this package's own records)
    WALNUT_RUNS_DIR            var/runs                adapter_agent/<id>/<run_id>.json (§5.7)
    WALNUT_EVENTS_PATH         var/events/events.jsonl the §5.3 event log (shared with ingest)
    WALNUT_INGEST_DIR          var/ingest              ingest run artefacts (§5.2), read only here
    WALNUT_GRAPH_DB_PATH       var/graph/workspace.db  the shared graph store (§5.1)

Resolved per call, never at import, so a test (or the gateway) can point them elsewhere.
"""
from __future__ import annotations

import os
from pathlib import Path

#: `databridge/` — this file is databridge/packages/adapter_agents/databridge_adapter_agents/paths.py
DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


def adapter_agents_dir() -> Path:
    return _env_path("WALNUT_ADAPTER_AGENTS_DIR", DATABRIDGE_ROOT / "var" / "adapter_agents")


def runs_dir() -> Path:
    return _env_path("WALNUT_RUNS_DIR", DATABRIDGE_ROOT / "var" / "runs")


def events_path() -> Path:
    return _env_path("WALNUT_EVENTS_PATH", DATABRIDGE_ROOT / "var" / "events" / "events.jsonl")


def ingest_dir() -> Path:
    return _env_path("WALNUT_INGEST_DIR", DATABRIDGE_ROOT / "var" / "ingest")


def graph_db_path() -> Path:
    return _env_path("WALNUT_GRAPH_DB_PATH", DATABRIDGE_ROOT / "var" / "graph" / "workspace.db")

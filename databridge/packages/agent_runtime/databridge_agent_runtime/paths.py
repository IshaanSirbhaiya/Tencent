"""Where the runtime reads and writes. Every location has an env override, resolved per call.

    WALNUT_AGENTS_DIR    var/agents     <agent>.workflow.yaml + <agent>.agent.yaml (§5.6)
    WALNUT_RUNS_DIR      var/runs       <kind>/<id>/<run_id>.json (§5.7) + <run_id>.actions.jsonl
    WALNUT_CAPTURE_DIR   var/capture    live reads are captured here (§5.2) before they are cited
"""
from __future__ import annotations

import os
from pathlib import Path

#: `databridge/` — this file is databridge/packages/agent_runtime/databridge_agent_runtime/paths.py
DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


def agents_dir() -> Path:
    return _env_path("WALNUT_AGENTS_DIR", DATABRIDGE_ROOT / "var" / "agents")


def runs_dir() -> Path:
    return _env_path("WALNUT_RUNS_DIR", DATABRIDGE_ROOT / "var" / "runs")


def capture_dir() -> Path:
    return _env_path("WALNUT_CAPTURE_DIR", DATABRIDGE_ROOT / "var" / "capture")


def graph_db_path() -> Path:
    return _env_path("WALNUT_GRAPH_DB_PATH", DATABRIDGE_ROOT / "var" / "graph" / "workspace.db")

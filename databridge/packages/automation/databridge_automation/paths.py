"""Where automation state lives. Every location has an env override; defaults sit under `var/`.

    WALNUT_EVENTS_PATH         var/events/events.jsonl        §5.3 event log (read, never written)
    WALNUT_EVENTS_OFFSETS_DIR  var/events/offsets/            per-consumer byte offsets
    WALNUT_AUTOMATION_DIR      var/automation/                firings.jsonl, schedule_state.json
    WALNUT_AGENTS_DIR          var/agents/                    <agent>.workflow.yaml + .agent.yaml
    WALNUT_ADAPTER_AGENTS_DIR  var/adapter_agents/            <id>.yaml
    WALNUT_SOURCES_DIR         var/sources/                   connection YAML (mcp_ingest's)
    WALNUT_TIMEZONE            (unset)                        the deployment's zone for English
                                                              schedules; unset + no zone in the
                                                              sentence -> the compiler ASKS
    DATABRIDGE_PACKS           workspace                      the bound pack (vocabulary + purpose)

Resolved per call, never at import, so a test or the gateway can point them elsewhere.
"""
from __future__ import annotations

import os
from pathlib import Path

#: `databridge/` — this file is databridge/packages/automation/databridge_automation/paths.py
DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]

#: The deployment default when neither the sentence nor `WALNUT_TIMEZONE` names a zone. The build
#: plan's examples are Singapore deployments (§5.6); the summary always states the zone used.
DEFAULT_TIMEZONE = "Asia/Singapore"


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


def var_dir() -> Path:
    return DATABRIDGE_ROOT / "var"


def events_path() -> Path:
    return _env_path("WALNUT_EVENTS_PATH", var_dir() / "events" / "events.jsonl")


def offsets_dir() -> Path:
    return _env_path("WALNUT_EVENTS_OFFSETS_DIR", events_path().parent / "offsets")


def automation_dir() -> Path:
    return _env_path("WALNUT_AUTOMATION_DIR", var_dir() / "automation")


def firings_path() -> Path:
    return automation_dir() / "firings.jsonl"


def schedule_state_path() -> Path:
    return automation_dir() / "schedule_state.json"


def agents_dir() -> Path:
    return _env_path("WALNUT_AGENTS_DIR", var_dir() / "agents")


def adapter_agents_dir() -> Path:
    return _env_path("WALNUT_ADAPTER_AGENTS_DIR", var_dir() / "adapter_agents")


def sources_dir() -> Path:
    return _env_path("WALNUT_SOURCES_DIR", var_dir() / "sources")


def packs_dir() -> Path:
    return _env_path("DATABRIDGE_PACKS_ROOT", DATABRIDGE_ROOT / "packs")


def default_timezone() -> str:
    return os.environ.get("WALNUT_TIMEZONE") or DEFAULT_TIMEZONE


def configured_timezone() -> str | None:
    """The deployment's zone ONLY if the deployment configured one (`WALNUT_TIMEZONE`). The English
    compiler uses this, not `default_timezone()`: with no configured zone and none in the
    sentence it asks, rather than assuming the constant above (R-017 #5)."""
    return os.environ.get("WALNUT_TIMEZONE") or None


def bound_pack() -> str:
    """The first pack in `DATABRIDGE_PACKS` (the launcher sets `workspace`), else `workspace`."""
    raw = os.environ.get("DATABRIDGE_PACKS") or "workspace"
    first = [p.strip() for p in raw.split(",") if p.strip()]
    return first[0] if first else "workspace"

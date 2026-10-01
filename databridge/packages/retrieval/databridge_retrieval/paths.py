"""Where retrieval reads and writes. Every location has an env override; resolved per call.

    WALNUT_PASSAGES_DIR   var/passages   one SQLite passage store per source x tier + router.json
    WALNUT_CAPTURE_DIR    var/capture    the capture store (BUILD_PLAN §5.2) — read only here
    DATABRIDGE_PACKS_ROOT packs/         where packs/<pack>/screen.yaml lives
"""
from __future__ import annotations

import os
from pathlib import Path

#: `databridge/` — this file is databridge/packages/retrieval/databridge_retrieval/paths.py
DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


def passages_dir() -> Path:
    return _env_path("WALNUT_PASSAGES_DIR", DATABRIDGE_ROOT / "var" / "passages")


def capture_dir() -> Path:
    return _env_path("WALNUT_CAPTURE_DIR", DATABRIDGE_ROOT / "var" / "capture")


def packs_dir() -> Path:
    return _env_path("DATABRIDGE_PACKS_ROOT", DATABRIDGE_ROOT / "packs")


def default_pack() -> str:
    """The first pack named in DATABRIDGE_PACKS (the launcher sets `workspace`)."""
    raw = os.environ.get("DATABRIDGE_PACKS", "workspace")
    first = [p.strip() for p in raw.split(",") if p.strip()]
    return first[0] if first else "workspace"

"""Where things live on disk. Every location has an env override; defaults sit under `databridge/var/`.

    WALNUT_SOURCES_DIR     var/sources            connection + plan + mapping YAML (declarations)
    WALNUT_INGEST_DIR      var/ingest             run artefacts: <source>/<run_id>/*.jsonl|json
    WALNUT_CAPTURE_DIR     var/capture            content-addressed bytes: <sha[:2]>/<sha256>
    WALNUT_GRAPH_DB_PATH   var/graph/workspace.db the shared graph store
    WALNUT_CRAWL_PROFILES_DIR  (optional)         extra crawl profiles, read AFTER the bundled ones

Resolved per call, never at import, so a test (or the gateway) can point them elsewhere.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

#: `databridge/` — this file is databridge/packages/mcp_ingest/databridge_mcp_ingest/paths.py
DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_DIR = Path(__file__).resolve().parent


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


#: One path SEGMENT a caller may name (a source id, a run id): a letter or digit, then letters,
#: digits, `.`, `_`, `-` — never a separator, never `.`/`..`, never a drive or a trailing dot.
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class UnsafePathSegment(ValueError):
    """A caller-supplied id that would leave its directory (`..`, a separator, a drive)."""


def safe_segment(value: object, what: str = "id") -> str:
    """`value` if it is ONE safe path segment, else `UnsafePathSegment` (Codex f1 #4). The value
    is never echoed back beyond its length, so a pasted secret is not repeated in an error."""
    text = value if isinstance(value, str) else ""
    if not _SEGMENT.match(text) or ".." in text or text.endswith("."):
        raise UnsafePathSegment(f"{what} is not a safe path segment ({len(str(value))} chars): "
                                f"use letters, digits, '.', '_' or '-' only, no '..'")
    return text


def contained(base: Path, *segments: str, what: str = "id") -> Path:
    """`base/<segments...>`, each segment checked by `safe_segment`, and the resolved result
    proven to lie beneath the resolved `base` (belt and braces against links / odd names)."""
    target = base
    for seg in segments:
        target = target / safe_segment(seg, what)
    b, t = base.resolve(), target.resolve()
    if t != b and b not in t.parents:
        raise UnsafePathSegment(f"{what} resolves outside {base}")
    return target


def sources_dir() -> Path:
    return _env_path("WALNUT_SOURCES_DIR", DATABRIDGE_ROOT / "var" / "sources")


def ingest_dir() -> Path:
    return _env_path("WALNUT_INGEST_DIR", DATABRIDGE_ROOT / "var" / "ingest")


def capture_dir() -> Path:
    return _env_path("WALNUT_CAPTURE_DIR", DATABRIDGE_ROOT / "var" / "capture")


def graph_db_path() -> Path:
    return _env_path("WALNUT_GRAPH_DB_PATH", DATABRIDGE_ROOT / "var" / "graph" / "workspace.db")


def bundled_profiles_dir() -> Path:
    return PACKAGE_DIR / "profiles"


def extra_profiles_dir() -> Path | None:
    value = os.environ.get("WALNUT_CRAWL_PROFILES_DIR")
    return Path(value) if value else None


def default_screen_path() -> Path:
    return PACKAGE_DIR / "defaults" / "screen.yaml"


def packs_dir() -> Path:
    value = os.environ.get("DATABRIDGE_PACKS_ROOT")
    return Path(value) if value else DATABRIDGE_ROOT / "packs"

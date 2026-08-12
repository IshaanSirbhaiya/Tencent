"""Where this package's config lives.

Same shape as ``databridge_intake.paths.config_dir`` — resolved from ``__file__`` rather than the
cwd, so pytest, uvicorn and the CLI all find the same file no matter where they were launched.
"""
from __future__ import annotations

import pathlib


def config_dir() -> pathlib.Path:
    """``packages/agent/databridge_agent/config`` — the playbook table."""
    return pathlib.Path(__file__).resolve().parent / "config"


def playbooks_path() -> pathlib.Path:
    return config_dir() / "agent_playbooks.yaml"

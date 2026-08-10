"""Config: the model routing table loader (02_TRD §A.4, Task 1).

``resolve_model(task_class)`` maps a task class to a concrete model id, honouring the
active profile (``cloud`` vs ``airgapped``). The PROFILE is determined per 02_TRD Task 1
(env var → yaml → default): an explicit ``profile=`` argument wins when given (used by
tests and explicit callers); otherwise ``DATABRIDGE_PROFILE`` is read, falling back to
the ``cloud`` default.

Fail LOUD on an unknown ``task_class`` — a typo silently routing to the wrong model is
exactly the class of bug that is hard to find later (02_TRD Task 1 DECISIONS). No
vendor SDK is imported here; this module only resolves NAMES. The actual model call
lives in packages/harness (Task 2).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml

# config/models.yaml at the repo root: parents[0]=databridge_core, [1]=core,
# [2]=packages, [3]=repo root (databridge/).
DEFAULT_MODELS_PATH = Path(__file__).resolve().parents[3] / "config" / "models.yaml"
DEFAULT_PROFILE = "cloud"

_CACHE: dict[str, dict] = {}


class ConfigError(Exception):
    """Malformed or inconsistent model routing config."""


class UnknownTaskClass(ConfigError):
    """A task_class with no entry in the routing table — fail loud, never guess."""


def models_config_path() -> Path:
    """The active models.yaml path (env override: ``DATABRIDGE_MODELS_CONFIG``)."""
    override = os.environ.get("DATABRIDGE_MODELS_CONFIG")
    return Path(override) if override else DEFAULT_MODELS_PATH


def active_profile() -> str:
    """The active deployment profile (env: ``DATABRIDGE_PROFILE``; default ``cloud``)."""
    return os.environ.get("DATABRIDGE_PROFILE", DEFAULT_PROFILE)


def load_model_config(path: Optional[Path] = None, *, use_cache: bool = True) -> dict:
    """Load and lightly validate models.yaml. Cached by absolute path."""
    p = Path(path) if path is not None else models_config_path()
    key = str(p.resolve())
    if use_cache and key in _CACHE:
        return _CACHE[key]
    if not p.exists():
        raise ConfigError(f"models config not found at {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    for section in ("routing", "aliases", "profiles"):
        if section not in data:
            raise ConfigError(f"models config missing required section: {section!r}")
    if DEFAULT_PROFILE not in data["profiles"]:
        raise ConfigError(f"models config has no {DEFAULT_PROFILE!r} profile")
    if use_cache:
        _CACHE[key] = data
    return data


def _resolve_target(target: Any, aliases: dict, task_class: str, depth: int = 0) -> str:
    """Resolve an alias name / inline ``{model: ...}`` / bare model id to a model string."""
    if depth > 8:
        raise ConfigError(f"alias resolution cycle while resolving {task_class!r}")
    if isinstance(target, dict):
        model = target.get("model")
        if not model:
            raise ConfigError(f"alias for {task_class!r} is a dict without a 'model' key")
        return str(model)
    if isinstance(target, str):
        if target in aliases:
            return _resolve_target(aliases[target], aliases, task_class, depth + 1)
        return target  # bare string not in the alias table → treat as a literal model id
    raise ConfigError(f"unresolvable routing target {target!r} for {task_class!r}")


def resolve_model(
    task_class: str,
    profile: Optional[str] = None,
    config: Optional[dict] = None,
) -> str:
    """Resolve ``task_class`` to a concrete model id under the active profile.

    >>> resolve_model("schema_annotation", profile="cloud")
    'anthropic/claude-sonnet-4-6'
    >>> resolve_model("schema_annotation", profile="airgapped")
    'ollama_chat/qwen3:72b'
    """
    cfg = config if config is not None else load_model_config()
    profile = profile or active_profile()

    routing = cfg["routing"]
    aliases = cfg["aliases"]
    profiles = cfg["profiles"]

    if profile not in profiles:
        raise ConfigError(f"unknown profile {profile!r}; known: {sorted(profiles)}")
    if task_class not in routing:
        raise UnknownTaskClass(
            f"unknown task_class {task_class!r}; known: {sorted(routing)}"
        )

    alias = routing[task_class]
    overrides = profiles.get(profile) or {}        # cloud is {} → no overrides
    target = overrides.get(alias, alias)           # profile may remap the alias
    return _resolve_target(target, aliases, task_class)

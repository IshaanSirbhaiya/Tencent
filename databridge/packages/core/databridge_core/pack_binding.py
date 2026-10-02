"""Which **pack** this deployment is bound to, and where the packs live — an engine concern.

C-5 (finance dry run, 2026-10-02). This module used to be ``databridge_intake.pack``: the deployment
binding was read by one vertical's package, and every other pack — and every engine surface that
had to choose wording — imported that vertical to find out which pack it was running under. The
mechanism names no domain, so it lives in the dependency root. ``databridge_intake.pack``
re-exports every name here, so existing callers keep working; engine code imports this module.

Three questions, three functions
--------------------------------
* :func:`enabled_packs` — which packs the deployment binds (``DATABRIDGE_PACKS`` →
  ``packs/deployment.yaml``). A deployment that binds nothing is refused, never defaulted.
* :func:`active_pack` — which ONE enabled pack carries behaviour rules (ships a ``config/``
  directory). Exactly one, or ``DATABRIDGE_PACK`` / :func:`bind_pack` names it; otherwise refused.
  Refusing an ambiguous binding is the point: silently taking the first would make which rule table
  applies depend on the order of a list in a YAML file.
* :func:`voice_pack` — which pack's WORDING a shared surface speaks in. A pinned pack that ships
  the file, else the single enabled pack if it ships the file, else ``None`` (the engine's neutral
  wording). Unlike :func:`active_pack` this never infers a voice from which pack happens to ship
  rules: a deployment binding three verticals is none of theirs, and an engine surface answering
  over all three must not adopt one vertical's nouns because that vertical is the only one with a
  rule table.

Computed, never named: nothing here mentions a pack by name.
"""
from __future__ import annotations

import os
import pathlib
import threading
from contextlib import contextmanager
from typing import Iterator, Optional, Sequence

import yaml

#: The pack directory that holds a pack's behaviour rules, beside its ``ontology.yaml``.
CONFIG_DIRNAME = "config"

#: Layer 2 of the three-layer schema — which packs this checkout has enabled.
DEPLOYMENT_FILE = "deployment.yaml"
ENABLED_KEY = "enabled_packs"

#: Environment overrides (same names and precedence as the graph loader's ``configured_packs``).
PACKS_ROOT_ENV = "DATABRIDGE_PACKS_ROOT"
PACKS_ENV = "DATABRIDGE_PACKS"
PACK_ENV = "DATABRIDGE_PACK"

_LOCK = threading.RLock()
_BOUND: Optional[str] = None            # set by bind_pack(); None ⇒ read the deployment binding


class PackConfigError(RuntimeError):
    """No pack — or more than one — carries this deployment's rules. Fail loud, never guess."""


def default_packs_root() -> pathlib.Path:
    """``databridge/packs`` of THIS checkout — resolved from this file, never from the cwd."""
    # databridge/packages/core/databridge_core/pack_binding.py → parents[3] is databridge/
    return pathlib.Path(__file__).resolve().parents[3] / "packs"


def packs_root() -> pathlib.Path:
    """The active packs directory (env override: ``DATABRIDGE_PACKS_ROOT``)."""
    override = os.environ.get(PACKS_ROOT_ENV)
    return pathlib.Path(override) if override else default_packs_root()


def enabled_packs(root: Optional[pathlib.Path] = None) -> tuple[str, ...]:
    """The deployment binding: ``DATABRIDGE_PACKS`` → ``packs/deployment.yaml``.

    Same precedence as the graph loader's ``configured_packs()``. A deployment that binds nothing
    is refused rather than defaulting to every pack on disk: an implicit binding would make the
    vocabulary — and the rule table — open.
    """
    env = os.environ.get(PACKS_ENV)
    if env:
        names = tuple(n.strip() for n in env.split(",") if n.strip())
        if names:
            return names
    r = pathlib.Path(root) if root is not None else packs_root()
    cfg = r / DEPLOYMENT_FILE
    if not cfg.is_file():
        raise PackConfigError(
            f"no deployment binding: {cfg} does not exist and {PACKS_ENV} is not set")
    data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    names = tuple(str(n) for n in (data.get(ENABLED_KEY) or []))
    if not names:
        raise PackConfigError(f"{cfg}: {ENABLED_KEY} is empty — a deployment must enable a pack")
    return names


def bind_pack(name: Optional[str]) -> None:
    """Pin (or release, with ``None``) the pack whose rules this process reads."""
    global _BOUND
    with _LOCK:
        _BOUND = name


def bound_pack_override() -> Optional[str]:
    """The in-process pin set by :func:`bind_pack` (not the environment). For re-exporters."""
    with _LOCK:
        return _BOUND


def pinned_pack() -> Optional[str]:
    """The pack this process was explicitly pinned to (``bind_pack`` or ``DATABRIDGE_PACK``), or
    ``None``. Unlike :func:`active_pack` this never infers one from which packs ship rules."""
    with _LOCK:
        pinned = _BOUND
    return pinned or os.environ.get(PACK_ENV) or None


def active_pack(root: Optional[pathlib.Path] = None) -> str:
    """The name of the pack carrying this deployment's rules. Computed; never a literal."""
    with _LOCK:
        pinned = _BOUND
    named = pinned or os.environ.get(PACK_ENV) or ""
    r = pathlib.Path(root) if root is not None else packs_root()
    bound = enabled_packs(r)
    if named:
        if named not in bound:
            raise PackConfigError(
                f"pack {named!r} is not enabled by this deployment (enabled: {list(bound)})")
        if not (r / named / CONFIG_DIRNAME).is_dir():
            raise PackConfigError(
                f"pack {named!r} ships no {CONFIG_DIRNAME}/ directory at "
                f"{r / named / CONFIG_DIRNAME}")
        return named
    with_rules = [p for p in bound if (r / p / CONFIG_DIRNAME).is_dir()]
    if not with_rules:
        raise PackConfigError(
            f"none of the enabled packs {list(bound)} ships a {CONFIG_DIRNAME}/ directory under "
            f"{r} — a pack's determination rules and baselines live there")
    if len(with_rules) > 1:
        raise PackConfigError(
            f"packs {with_rules} each ship a {CONFIG_DIRNAME}/ directory and this deployment "
            f"enables all of them. Set {PACK_ENV} (or call bind_pack) to say which rule "
            f"table applies — taking the first would make the outcome depend on list order")
    return with_rules[0]


def pack_dir(root: Optional[pathlib.Path] = None) -> pathlib.Path:
    """``packs/<active pack>/`` — the vertical, as a directory."""
    r = pathlib.Path(root) if root is not None else packs_root()
    return r / active_pack(r)


def pack_config_dir(root: Optional[pathlib.Path] = None) -> pathlib.Path:
    """``packs/<active pack>/config/`` — the rules/schema tables of the active pack."""
    return pack_dir(root) / CONFIG_DIRNAME


def available_pack_configs(root: Optional[pathlib.Path] = None) -> tuple[str, ...]:
    """Every pack on disk that ships rules — what a deployment *could* bind."""
    r = pathlib.Path(root) if root is not None else packs_root()
    if not r.is_dir():
        return ()
    return tuple(sorted(p.name for p in r.iterdir() if (p / CONFIG_DIRNAME).is_dir()))


def packs_on_disk(root: Optional[pathlib.Path] = None) -> tuple[str, ...]:
    """Every pack directory under ``root`` (enabled or not), sorted. Never raises."""
    r = pathlib.Path(root) if root is not None else packs_root()
    if not r.is_dir():
        return ()
    return tuple(sorted(p.name for p in r.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))))


def voice_pack(filename: str, root: Optional[pathlib.Path] = None) -> Optional[str]:
    """Which pack's ``filename`` (a wording file) a shared surface speaks with, or ``None`` for the
    engine's neutral wording. See the module docstring.

    * pinned (``bind_pack`` / ``DATABRIDGE_PACK``) and the pinned pack ships ``filename`` → it;
    * pinned but it ships no such file → ``None`` (never another pack's voice);
    * otherwise, exactly one ENABLED pack, and it ships the file → it;
    * otherwise → ``None``. Counting only the packs that ship a file would let a three-vertical
      deployment speak in the one vertical that wrote one.
    """
    r = pathlib.Path(root) if root is not None else packs_root()

    def ships(name: str) -> bool:
        return (r / name / filename).is_file()

    pinned = pinned_pack()
    if pinned:
        return pinned if ships(pinned) else None
    try:
        bound = list(enabled_packs(r))
    except PackConfigError:
        bound = []
    return bound[0] if len(bound) == 1 and ships(bound[0]) else None


@contextmanager
def _bound(names: Optional[Sequence[str]]) -> Iterator[str]:
    prior = bound_pack_override()
    bind_pack(names[0] if names else None)
    try:
        yield active_pack()
    finally:
        bind_pack(prior)


def bound_packs(names: Optional[Sequence[str]] = None):
    """Context manager: bind one pack for the duration of a block, then restore (tests)."""
    return _bound(names)


__all__ = [
    "CONFIG_DIRNAME", "DEPLOYMENT_FILE", "ENABLED_KEY", "PACKS_ENV", "PACKS_ROOT_ENV", "PACK_ENV",
    "PackConfigError", "active_pack", "available_pack_configs", "bind_pack", "bound_pack_override",
    "bound_packs", "default_packs_root", "enabled_packs", "pack_config_dir", "pack_dir",
    "packs_on_disk", "packs_root", "pinned_pack", "voice_pack",
]

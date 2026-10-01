"""Which **pack** this deployment's determination rules come from, and where they live.

The rules this package reads — coverage, questionnaires, the assistant persona, the time
baseline — used to sit at ``packages/intake/databridge_intake/config/``: a directory *inside* the
engine, which made one vertical's behaviour a property of the code rather than of a pack. It is
now ``packs/<pack>/config/``, a peer of ``packs/<pack>/ontology.yaml`` (spine §4, Layer 1). The
mechanics stay here — classify → extract → ground → resolve → eligibility → prefill → billing →
ledger is engine, and none of it is domain-specific; only the *rows it reads* moved.

The rule that made this worth doing is unchanged and now runs through the pack:
**adding an insurer is a YAML change with zero code changes** (CLAUDE.md §Hard constraints 3).
Adding an *industry* is now adding a directory.

Which pack
----------
Computed, never named. Among the packs the deployment binds, the ones that ship a ``config/``
directory are the ones that carry behaviour rules; exactly one of those is the active pack.

* none          → refuse, naming the enabled packs and the directory that is missing;
* exactly one   → that one;
* more than one → refuse unless ``DATABRIDGE_PACK`` picks between them.

Refusing an ambiguous binding is the point. Silently taking the first would make which rule table
the front desk is billing against depend on the order of a list in a YAML file.

Why this does not import ``databridge_graph.packs``
---------------------------------------------------
``test_no_graph_import`` (``packages/intake/tests/test_history.py``) asserts this package never
imports the graph, and that seam is deliberate: intake talks to the graph through the
``ClinicHistory`` port and nothing else, so the pipeline runs with no graph at all. The deployment
binding is a *file* convention — ``packs/deployment.yaml`` and two environment variables — not a
graph concept, so it is read here directly. The two readers agree on the same three inputs and on
their precedence; ``test_pack_binding_matches_the_graph_loader`` pins that they do, so a change to
one that is not made to the other fails rather than drifting.

The one input this reader cannot see is the graph loader's in-process ``bind_deployment()``, which
is a Python global in another package. ``bind_pack()`` below is the equivalent for this side and is
what the tests here use.
"""
from __future__ import annotations

import os
import pathlib
import threading
from typing import Optional, Sequence

import yaml

#: The pack directory that holds a pack's behaviour rules, beside its ``ontology.yaml``.
CONFIG_DIRNAME = "config"

#: Layer 2 of the three-layer schema — which packs this checkout has enabled.
DEPLOYMENT_FILE = "deployment.yaml"
ENABLED_KEY = "enabled_packs"

_LOCK = threading.RLock()
_BOUND: Optional[str] = None            # set by bind_pack(); None ⇒ read the deployment binding


class PackConfigError(RuntimeError):
    """No pack — or more than one — carries this deployment's rules. Fail loud, never guess."""


def packs_root() -> pathlib.Path:
    """The active packs directory (env override: ``DATABRIDGE_PACKS_ROOT``)."""
    from .paths import repo_root

    override = os.environ.get("DATABRIDGE_PACKS_ROOT")
    return pathlib.Path(override) if override else repo_root() / "databridge" / "packs"


def enabled_packs(root: Optional[pathlib.Path] = None) -> tuple[str, ...]:
    """The deployment binding: ``DATABRIDGE_PACKS`` → ``packs/deployment.yaml``.

    Same precedence as the graph loader's ``configured_packs()``. A deployment that binds nothing
    is refused rather than defaulting to every pack on disk: an implicit binding would make the
    vocabulary — and now the rule table — open.
    """
    env = os.environ.get("DATABRIDGE_PACKS")
    if env:
        names = tuple(n.strip() for n in env.split(",") if n.strip())
        if names:
            return names
    r = pathlib.Path(root) if root is not None else packs_root()
    cfg = r / DEPLOYMENT_FILE
    if not cfg.is_file():
        raise PackConfigError(
            f"no deployment binding: {cfg} does not exist and DATABRIDGE_PACKS is not set")
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


def pinned_pack() -> Optional[str]:
    """The pack this process was explicitly pinned to (``bind_pack`` or ``DATABRIDGE_PACK``), or
    ``None``. Unlike :func:`active_pack` this never infers one from which packs ship rules — a
    caller choosing pack-specific *wording* must not adopt one vertical's voice just because that
    vertical is the only one with a rule table."""
    with _LOCK:
        pinned = _BOUND
    return pinned or os.environ.get("DATABRIDGE_PACK") or None


def active_pack(root: Optional[pathlib.Path] = None) -> str:
    """The name of the pack carrying this deployment's rules. Computed; never a literal."""
    with _LOCK:
        pinned = _BOUND
    named = pinned or os.environ.get("DATABRIDGE_PACK") or ""
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
            f"{r} — the determination rules (coverage, questionnaires, baselines) live there")
    if len(with_rules) > 1:
        raise PackConfigError(
            f"packs {with_rules} each ship a {CONFIG_DIRNAME}/ directory and this deployment "
            f"enables all of them. Set DATABRIDGE_PACK (or call bind_pack) to say which rule "
            f"table applies — taking the first would make billing depend on list order")
    return with_rules[0]


def pack_dir(root: Optional[pathlib.Path] = None) -> pathlib.Path:
    """``packs/<active pack>/`` — the vertical, as a directory."""
    r = pathlib.Path(root) if root is not None else packs_root()
    return r / active_pack(r)


def pack_config_dir(root: Optional[pathlib.Path] = None) -> pathlib.Path:
    """``packs/<active pack>/config/`` — the rules/schema tables this package reads."""
    return pack_dir(root) / CONFIG_DIRNAME


def available_pack_configs(root: Optional[pathlib.Path] = None) -> tuple[str, ...]:
    """Every pack on disk that ships rules — what a deployment *could* bind."""
    r = pathlib.Path(root) if root is not None else packs_root()
    if not r.is_dir():
        return ()
    return tuple(sorted(p.name for p in r.iterdir() if (p / CONFIG_DIRNAME).is_dir()))


def bound_packs(names: Optional[Sequence[str]] = None):
    """Context manager: bind one pack for the duration of a block, then restore (tests)."""
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        with _LOCK:
            prior = _BOUND
        bind_pack(names[0] if names else None)
        try:
            yield active_pack()
        finally:
            bind_pack(prior)

    return _cm()


__all__ = [
    "CONFIG_DIRNAME", "DEPLOYMENT_FILE", "ENABLED_KEY", "PackConfigError",
    "active_pack", "available_pack_configs", "bind_pack", "bound_packs", "enabled_packs",
    "pack_config_dir", "pack_dir", "packs_root", "pinned_pack",
]

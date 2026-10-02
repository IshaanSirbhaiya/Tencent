"""Which **pack** this deployment's determination rules come from — re-exported from the engine.

C-5 (finance dry run, 2026-10-02). The deployment binding — which packs are enabled, which one
carries the rules, which one a process is pinned to, where the packs live — is read by every pack's
surfaces, not only this package's. It names no domain, so it now lives in the dependency root:
``databridge_core.pack_binding``. This module keeps every old name importable so no caller breaks;
engine code imports the core module directly (``test_engine_does_not_import_the_vertical``).

The rules this package reads — the coverage table, the forms, the assistant persona, the time
baseline — still live at ``packs/<pack>/config/``, a peer of the pack's ``ontology.yaml``. Adding
an insurer is a YAML change with zero code changes (CLAUDE.md §Hard constraints 3); adding an
industry is adding a directory.

Why this does not import ``databridge_graph.packs``: ``test_no_graph_import`` asserts this package
never imports the graph. The binding is a *file* convention (``packs/deployment.yaml`` and two
environment variables), and ``test_pack_binding_matches_the_graph_loader`` pins that the core reader
and the graph loader agree on it.
"""
from __future__ import annotations

from databridge_core import pack_binding as _core
from databridge_core.pack_binding import (
    CONFIG_DIRNAME,
    DEPLOYMENT_FILE,
    ENABLED_KEY,
    PackConfigError,
    active_pack,
    available_pack_configs,
    bind_pack,
    bound_packs,
    enabled_packs,
    pack_config_dir,
    pack_dir,
    packs_root,
    pinned_pack,
)


def __getattr__(name: str):
    # ``_BOUND`` was a module global here; tests read it to save and restore a pin. It is the
    # core module's now, read live so a re-export never hands back a stale copy.
    if name == "_BOUND":
        return _core.bound_pack_override()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CONFIG_DIRNAME", "DEPLOYMENT_FILE", "ENABLED_KEY", "PackConfigError",
    "active_pack", "available_pack_configs", "bind_pack", "bound_packs", "enabled_packs",
    "pack_config_dir", "pack_dir", "packs_root", "pinned_pack",
]

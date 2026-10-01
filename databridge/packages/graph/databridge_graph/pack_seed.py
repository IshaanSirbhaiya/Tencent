"""Pack-declared demo seeds — the engine loads whatever the ENABLED packs declare, and names none.

A pack may ship ``demo_seed.yaml`` beside its ``ontology.yaml``::

    loader: some_module:some_function      # called as some_function(store) -> dict
    sources:                               # optional: the registered documents the demo ingests
      demo_sample.txt: some_module:SOME_TEXT

The shared brain surface used to hard-code one pack's seed function and import one pack's fixture
text (Codex f2 #6). A catch around the call only turned the wrong binding into a refusal; it did
not remove the branch. Now the engine asks the bound packs what they seed, so:

* a pack that is not enabled contributes nothing — its seed can never run under a binding whose
  vocabulary does not declare its kinds, so there is nothing left to refuse;
* adding a demo to a new pack is a YAML file, with zero engine code.

The locators name Python objects, so this file is deployment configuration of the same trust
class as ``config/models.yaml`` and the pack's ontology — not data a source wrote. Each locator is
validated for shape and for what it resolves to (a callable loader, string sources) before use.
"""
from __future__ import annotations

import importlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

import yaml

from .packs import PackError, active_vocabulary, packs_root

DEMO_SEED_FILE = "demo_seed.yaml"
_ALLOWED_KEYS = frozenset({"loader", "sources"})
_LOCATOR = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")


@dataclass(frozen=True)
class DemoSeed:
    """One pack's declared demo seed."""

    pack: str
    loader: str
    sources: Mapping[str, str] = field(default_factory=dict)
    path: Optional[Path] = None


def _check_locator(value, where: str) -> str:
    if not isinstance(value, str) or not _LOCATOR.match(value):
        raise PackError(f"{where}: {value!r} is not a 'module:attribute' locator")
    return value


def resolve_locator(locator: str):
    """The object a ``module:attribute`` locator names."""
    module, _, attr = _check_locator(locator, "locator").partition(":")
    try:
        return getattr(importlib.import_module(module), attr)
    except (ImportError, AttributeError) as exc:
        raise PackError(f"locator {locator!r} does not resolve: {exc}") from exc


def read_demo_seed(pack: str, root: Optional[Path] = None) -> Optional[DemoSeed]:
    """``packs/<pack>/demo_seed.yaml`` parsed and shape-checked, or ``None`` when absent."""
    path = (Path(root) if root is not None else packs_root()) / pack / DEMO_SEED_FILE
    if not path.is_file():
        return None
    where = f"{pack}/{DEMO_SEED_FILE}"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise PackError(f"{where}: expected a mapping")
    unknown = sorted(set(data) - _ALLOWED_KEYS)
    if unknown:
        raise PackError(f"{where}: unknown key(s) {unknown}; allowed: {sorted(_ALLOWED_KEYS)}")
    loader = _check_locator(data.get("loader"), f"{where}: loader")
    raw_sources = data.get("sources") or {}
    if not isinstance(raw_sources, dict):
        raise PackError(f"{where}: sources must be a mapping of name -> locator")
    sources = {str(k): _check_locator(v, f"{where}: sources.{k}") for k, v in raw_sources.items()}
    return DemoSeed(pack=pack, loader=loader, sources=sources, path=path)


def declared_demo_seeds(names: Optional[Sequence[str]] = None,
                        root: Optional[Path] = None) -> tuple[DemoSeed, ...]:
    """The demo seeds of the ENABLED packs (the bound vocabulary's), in binding order."""
    packs = tuple(names) if names is not None else active_vocabulary().packs
    return tuple(s for s in (read_demo_seed(p, root) for p in packs) if s is not None)


def demo_seed_sources(names: Optional[Sequence[str]] = None,
                      root: Optional[Path] = None) -> dict[str, str]:
    """``{registered source name: text}`` for every enabled pack's demo sources.

    The allow-list ``brain_ingest`` may read from. Two packs declaring the same name is refused —
    which document a name means must never depend on binding order."""
    out: dict[str, str] = {}
    owner: dict[str, str] = {}
    for seed in declared_demo_seeds(names, root):
        for name, loc in seed.sources.items():
            if name in out:
                raise PackError(f"demo source {name!r} is declared by both {owner[name]!r} and "
                                f"{seed.pack!r}")
            text = resolve_locator(loc)
            if not isinstance(text, str):
                raise PackError(f"{seed.pack}/{DEMO_SEED_FILE}: sources.{name} resolves to "
                                f"{type(text).__name__}, not text")
            out[name], owner[name] = text, seed.pack
    return out


def load_demo_seeds(store, names: Optional[Sequence[str]] = None,
                    root: Optional[Path] = None) -> dict:
    """Run every enabled pack's declared seed loader against ``store``.

    ``{"loaded": False, "refusal": ...}`` when no enabled pack declares one — nothing is written.
    Otherwise ``{"loaded": True, "seeded_packs": [...], "by_pack": {pack: result}}``; with exactly
    one seed, that seed's own result keys are also returned at the top level, so a caller that
    reads the loader's counts keeps working."""
    seeds = declared_demo_seeds(names, root)
    if not seeds:
        packs = list(names) if names is not None else list(active_vocabulary().packs)
        return {"loaded": False,
                "refusal": f"no enabled pack declares a demo seed ({DEMO_SEED_FILE}); "
                           f"enabled packs: {packs}; nothing was written"}
    by_pack: dict[str, dict] = {}
    for seed in seeds:
        fn = resolve_locator(seed.loader)
        if not callable(fn):
            raise PackError(f"{seed.pack}/{DEMO_SEED_FILE}: loader {seed.loader!r} is not callable")
        result = fn(store)
        by_pack[seed.pack] = dict(result) if isinstance(result, Mapping) else {"result": result}
    out: dict = {}
    if len(by_pack) == 1:
        out.update(next(iter(by_pack.values())))
    out.update({"loaded": True, "seeded_packs": list(by_pack), "by_pack": by_pack})
    return out

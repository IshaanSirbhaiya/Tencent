"""Walnut's grounding wall, reused by import WITHOUT importing the legacy gateway (plan §4.1, §4.4).

``databridge_gateway/__init__.py`` imports ``main`` (every legacy route). The grounding functions in
``databridge_gateway/llm.py`` need only ``databridge_core`` at import time, so the module is loaded
from its file under a private name: the legacy gateway's routes are never imported into this
process. ``_ungrounded``'s ``from .main import …`` then fails inside its own try and falls back to
the module's local ``UngroundedNarration`` — the exception this package catches.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_LLM_PATH = Path(__file__).resolve().parents[2] / "gateway" / "databridge_gateway" / "llm.py"
_NAME = "databridge_ib._walnut_grounding"


def _load():
    if _NAME in sys.modules:
        return sys.modules[_NAME]
    spec = importlib.util.spec_from_file_location(_NAME, _LLM_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_NAME] = mod
    spec.loader.exec_module(mod)                     # type: ignore[union-attr]
    return mod


_mod = _load()

GroundedNarrator = _mod.GroundedNarrator
UngroundedNarration = _mod.UngroundedNarration
ground_narration = _mod.ground_narration
bind_sentences = _mod.bind_sentences
unsupported_named_terms = _mod.unsupported_named_terms
identity_assertion_violations = _mod.identity_assertion_violations

__all__ = ["GroundedNarrator", "UngroundedNarration", "ground_narration", "bind_sentences",
           "unsupported_named_terms", "identity_assertion_violations"]

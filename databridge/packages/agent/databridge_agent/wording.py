"""The agent's domain wording, read from the bound pack — never written in this package.

HC1 (2026-10-01, SWEEP hardcode audit #2 and #4). ``frame.SYSTEM_PROMPT`` opened with *"You are the
clinic agent for a Parkway Shenton front desk"* and ``render`` printed *"What the clinic graph
holds:"* and *"checks the NRIC, FIN or passport"* — under every pack, so a finance deployment's
agent introduced itself as a clinic's. The engine names no domain (CLAUDE.md rule 5).

What moved and what did not
---------------------------
**Moved to ``packs/<pack>/agent_wording.yaml``:** the nouns and sentences that name a vertical —
the persona line, where the data comes from, who writes it, which identity documents exist, the
report headings and the staff-facing notes.

**Stayed in code (``frame.py``):** the injection boundary — the frame markers, the rules about
treating framed text as data, "you cannot send", "you cannot state a value the tools did not
return", and the answer protocol. ``frame.py`` decision 3 still holds: a YAML edit that softened
*those* would be a security change wearing a config change's clothes. A pack supplies words to
slot into that structure; it cannot remove a rule from it. ``AgentWording`` refuses any key it does
not know and any empty value, so a pack cannot blank a note either.

Which pack's wording
--------------------
Computed, never named:

* a pack the process is **pinned** to (``DATABRIDGE_PACK`` / ``databridge_core.pack_binding.bind_pack``)
  that ships ``agent_wording.yaml`` → that pack's;
* otherwise, a deployment that enables **exactly one** pack, and that pack ships the file →
  that pack's;
* otherwise → the engine's neutral wording (``config/neutral_wording.yaml`` in this package), which
  names no domain and is therefore the one copy the engine may ship.

A deployment binding several verticals gets the neutral voice on purpose: an agent answering over
a finance graph and a clinic graph at once is neither a clinic's agent nor a bank's. The rule is
``databridge_core.pack_binding.voice_pack`` — the same one the identity refusal uses, so the agent
and the refusal can never speak in two different packs' voices (C-5, finance dry run 2026-10-02).

The file also carries ``refusals`` (the no-send refusal, closed keys) and an optional ``identity``
section, which is validated here but READ by ``databridge_core.identity_guardrail``: the identity
refusal and the identity nouns are the engine guardrail's, and a pack declaring them badly must
fail when its wording loads, not on the first question that trips the guardrail.
"""
from __future__ import annotations

import functools
import pathlib
from types import MappingProxyType
from typing import Mapping, Optional

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

#: The file a pack ships beside its ``ontology.yaml``.
WORDING_FILENAME = "agent_wording.yaml"

#: The ``pack`` value of the engine's neutral wording, used when no single vertical is bound.
NEUTRAL_PACK = "neutral"
#: The neutral wording ships with the engine: a packs root need not carry it.
NEUTRAL_WORDING_PATH = pathlib.Path(__file__).resolve().parent / "config" / "neutral_wording.yaml"

#: The note keys ``render`` may emit. Closed: a pack supplies their TEXT, never new keys (R-10).
NOTE_KEYS = ("human_selection", "injection_seen", "refs_not_returned", "nothing_answered")
HEADING_KEYS = ("values", "unresolved", "candidates")
CONSOLE_NOTE_KEYS = ("proposal_only", "needs_an_intake", "no_authentication",
                     "figures_on_request", "answer_is_a_list")
#: The refusals the agent itself words (closed). The identity refusal is the engine guardrail's
#: (``identity`` section), not a key here — one reader per sentence.
REFUSAL_KEYS = ("no_send",)


def _paragraphs(text: str) -> str:
    """Fold whitespace within each paragraph; keep blank-line paragraph breaks."""
    import re

    return "\n\n".join(" ".join(p.split()) for p in re.split(r"\n\s*\n", str(text or ""))
                       if p.strip())


class WordingError(RuntimeError):
    """A pack's agent wording is missing, incomplete or carries keys nobody reads. Fail loud."""


def _closed(value: Mapping[str, str], keys: tuple[str, ...], what: str) -> Mapping[str, str]:
    got = dict(value or {})
    missing = [k for k in keys if not str(got.get(k) or "").strip()]
    extra = sorted(k for k in got if k not in keys)
    if missing or extra:
        raise ValueError(f"{what}: missing or empty {missing}, unknown {extra}; the keys are "
                         f"closed ({list(keys)}) so a pack can reword a note but never add or "
                         f"drop one")
    return MappingProxyType({k: " ".join(str(got[k]).split()) for k in keys})


class AgentWording(BaseModel):
    """Every domain-bearing sentence the agent and its console print. All required, all closed."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    pack: str
    persona: str
    data_origin: str
    outside_authors: str
    identity_tools: str
    identity_argument: str
    question_label: str
    headings: Mapping[str, str]
    notes: Mapping[str, str]
    console_notes: Mapping[str, str]
    refusals: Mapping[str, str]
    identity: Optional[Mapping] = None

    @field_validator("persona", "data_origin", "outside_authors", "identity_tools",
                     "identity_argument", "question_label", "pack")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        v = " ".join(str(v or "").split())
        if not v:
            raise ValueError("every wording field must be non-empty")
        return v

    @field_validator("headings")
    @classmethod
    def _headings(cls, v):
        return _closed(v, HEADING_KEYS, "headings")

    @field_validator("notes")
    @classmethod
    def _notes(cls, v):
        return _closed(v, NOTE_KEYS, "notes")

    @field_validator("console_notes")
    @classmethod
    def _console(cls, v):
        return _closed(v, CONSOLE_NOTE_KEYS, "console_notes")

    @field_validator("refusals")
    @classmethod
    def _refusals(cls, v):
        closed = _closed(v, REFUSAL_KEYS, "refusals")          # validates presence and key set
        return MappingProxyType({k: _paragraphs(dict(v)[k]) for k in closed})

    @field_validator("identity")
    @classmethod
    def _identity(cls, v):
        if v is None:
            return None
        from databridge_core.identity_guardrail import parse_identity_section

        parse_identity_section({"identity": dict(v)}, pack=None, where="identity")
        return MappingProxyType(dict(v))


def load_wording(path: pathlib.Path) -> AgentWording:
    path = pathlib.Path(path)
    if not path.is_file():
        raise WordingError(f"agent wording not found at {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise WordingError(f"{path} did not parse to a mapping")
    try:
        return AgentWording.model_validate(data)
    except Exception as exc:                                         # noqa: BLE001
        raise WordingError(f"{path}: {exc}") from exc


def _packs_root() -> pathlib.Path:
    from databridge_core.pack_binding import packs_root

    return packs_root()


def wording_pack(root: Optional[pathlib.Path] = None) -> str:
    """Which pack's wording applies to this process right now. See the module docstring."""
    from databridge_core.pack_binding import voice_pack

    r = pathlib.Path(root) if root is not None else _packs_root()
    # Exactly one ENABLED pack — not "exactly one that ships wording" (``voice_pack``).
    return voice_pack(WORDING_FILENAME, r) or NEUTRAL_PACK


@functools.lru_cache(maxsize=16)
def _cached(path: str, mtime_ns: int) -> AgentWording:              # noqa: ARG001 — cache key
    return load_wording(pathlib.Path(path))


def wording(root: Optional[pathlib.Path] = None) -> AgentWording:
    """The wording in force. Re-resolved per call (the binding is rebindable); parsed once per file
    version."""
    r = pathlib.Path(root) if root is not None else _packs_root()
    pack = wording_pack(r)
    path = NEUTRAL_WORDING_PATH if pack == NEUTRAL_PACK else r / pack / WORDING_FILENAME
    if not path.is_file():
        raise WordingError(f"agent wording not found at {path}")
    return _cached(str(path), path.stat().st_mtime_ns)


__all__ = ["AgentWording", "CONSOLE_NOTE_KEYS", "HEADING_KEYS", "NEUTRAL_PACK",
           "NEUTRAL_WORDING_PATH", "NOTE_KEYS", "REFUSAL_KEYS",
           "WORDING_FILENAME", "WordingError", "load_wording", "wording", "wording_pack"]

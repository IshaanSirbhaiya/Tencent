"""The **pack-declared purpose vocabulary** (spine D5, `ADAPTER_AGENT_SPEC` §2).

> *"Purpose is part of the principal, not a field on the request. A purpose that can be omitted is
> a purpose that will be omitted, and GDPR 5(1)(b) / PDPA purpose limitation is not retrofittable
> once an audit has begun."*

Two consequences the code has to carry, not merely document:

1. **The vocabulary is closed and it lives in the pack**, exactly like the node/edge kinds
   (`databridge_graph.packs`). A purpose enum in engine code would make "why was this read
   allowed?" an engine question, and the engine has no domain. So each pack ships a
   ``purposes.yaml`` beside its ``ontology.yaml`` and the union of the ENABLED packs' purposes is
   the only vocabulary a `Principal` may draw on.
2. **It fails closed.** A purpose no enabled pack declares is `PurposeNotDeclared`, naming the
   file that would have to declare it. There is no fallback purpose, no ``"unspecified"``, and no
   branch that turns an unknown purpose into a permitted one.

``subject_scoped`` is the second half of `ADAPTER_AGENT_SPEC` §1's rule: *"`Principal.grant_id` is
`None` for build-time crawls precisely because those reads are not subject-scoped, and a
subject-scoped read without a grant is refused rather than defaulted."* The pack declares which of
its purposes are subject-scoped; `Principal` enforces the grant.

Resolution order for the active vocabulary:

1. whatever ``use_purposes(...)`` has bound on this thread/task (tests, and any caller that wants
   an explicit vocabulary);
2. otherwise the enabled packs of the active deployment binding — read through
   ``databridge_graph.packs`` so there is exactly ONE definition of "which packs are enabled".
"""
from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterator, Mapping, Optional, Sequence

import yaml

from .errors import PurposeNotDeclared

PURPOSES_FILE = "purposes.yaml"

#: A purpose id is a dotted lowercase path with at least two segments: ``<area>.<activity>``.
#: Two segments minimum on purpose - a bare ``"billing"`` is a category, and a category is not a
#: purpose you can hold a controller to.
_PURPOSE_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class PurposeError(Exception):
    """A ``purposes.yaml`` is missing, malformed, or inconsistent. Fail loud, name the file."""


@dataclass(frozen=True)
class PurposeDecl:
    """One purpose a pack declares. The unit of lawful basis, not of convenience."""

    id: str
    pack: str
    description: str
    subject_scoped: bool
    #: Free-form, pack-supplied, e.g. "PDPA s13 consent" - recorded, never interpreted by the
    #: engine. The engine's job is to make sure it was declared and carried, not to judge it.
    lawful_basis: Optional[str] = None


class PurposeVocabulary:
    """The closed union of the enabled packs' purposes. Sealed; see `databridge_graph.packs`."""

    __slots__ = ("_purposes", "_packs")

    def __init__(self, purposes: Mapping[str, PurposeDecl], packs: Sequence[str]) -> None:
        # A copy behind a proxy, for the reason `packs._sealed` documents: a MappingProxyType over
        # the caller's own dict leaves them holding a live handle into a "closed" vocabulary, and
        # this object is cached process-wide.
        self._purposes: Mapping[str, PurposeDecl] = MappingProxyType(dict(purposes))
        self._packs: tuple[str, ...] = tuple(packs)

    @property
    def packs(self) -> tuple[str, ...]:
        return self._packs

    @property
    def purposes(self) -> Mapping[str, PurposeDecl]:
        return self._purposes

    def declares(self, purpose: str) -> bool:
        return purpose in self._purposes

    def require(self, purpose: str) -> PurposeDecl:
        """The declaration for ``purpose``, or `PurposeNotDeclared` naming what to add where."""
        try:
            return self._purposes[purpose]
        except KeyError:
            known = ", ".join(sorted(self._purposes)) or "(none - no enabled pack declares any)"
            packs = ", ".join(self._packs) or "(no packs enabled)"
            raise PurposeNotDeclared(
                f"purpose {purpose!r} is not declared by any enabled pack. Enabled: {packs}. "
                f"Declared purposes: {known}. To add one, declare it in "
                f"packs/<pack>/{PURPOSES_FILE} - a purpose is a pack statement about lawful "
                f"basis, never an engine literal (spine D5)."
            ) from None

    def __repr__(self) -> str:                                      # pragma: no cover - debug aid
        return f"PurposeVocabulary(packs={self._packs}, n={len(self._purposes)})"


# --- loading ----------------------------------------------------------------------
_LOCK = threading.RLock()
_CACHE: dict[tuple[str, tuple[str, ...]], PurposeVocabulary] = {}


def _require(mapping: dict, key: str, where: str):
    if key not in mapping or mapping[key] in (None, ""):
        raise PurposeError(f"{where}: missing required key {key!r}")
    return mapping[key]


def load_pack_purposes(pack: str, root: Path) -> dict[str, PurposeDecl]:
    """Read one pack's ``purposes.yaml``. A pack with no such file declares no purposes.

    Absence is not an error - a pack may legitimately have no read purposes of its own - but it
    is not a licence either: with no declaration, every purpose naming that pack is refused.
    """
    path = (Path(root) / pack / PURPOSES_FILE).resolve()
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:                                   # pragma: no cover - yaml detail
        raise PurposeError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise PurposeError(f"{path}: top level must be a mapping")

    where = str(path)
    declared = _require(data, "pack", where)
    if declared != pack:
        raise PurposeError(
            f"{where}: declares pack {declared!r} but lives in directory {pack!r} - the "
            f"directory name is the pack's identity")

    entries = data.get("purposes") or []
    if not isinstance(entries, list):
        raise PurposeError(f"{where}: 'purposes' must be a list")

    out: dict[str, PurposeDecl] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise PurposeError(f"{where}: each purposes entry must be a mapping")
        pid = _require(entry, "id", where)
        if not isinstance(pid, str) or not _PURPOSE_ID.match(pid):
            raise PurposeError(
                f"{where}: purpose id {pid!r} must be dotted lowercase with at least two "
                f"segments, e.g. 'intake.eligibility' - a single word names a category, and a "
                f"category is not a purpose")
        if pid in out:
            raise PurposeError(f"{where}: duplicate purpose id {pid!r}")
        scoped = entry.get("subject_scoped")
        if not isinstance(scoped, bool):
            raise PurposeError(
                f"{where}: purpose {pid!r} must declare subject_scoped: true|false explicitly. "
                f"Defaulting it would decide, silently, whether a grant is required (D4b/D5)")
        out[pid] = PurposeDecl(
            id=pid,
            pack=pack,
            description=str(entry.get("description") or "").strip(),
            subject_scoped=scoped,
            lawful_basis=(str(entry["lawful_basis"]) if entry.get("lawful_basis") else None),
        )
    return out


def purpose_vocabulary(packs: Sequence[str], root: Path,
                       *, use_cache: bool = True) -> PurposeVocabulary:
    """The union of ``packs``' declared purposes, loaded from ``root``.

    Two packs declaring the same purpose id is a **collision, not a merge**: they would carry
    different lawful bases behind one name, and an auditor reading the ledger could not tell which
    applied. Refused at load time.
    """
    key = (str(Path(root).resolve()), tuple(packs))
    if use_cache:
        with _LOCK:
            hit = _CACHE.get(key)
            if hit is not None:
                return hit

    merged: dict[str, PurposeDecl] = {}
    for pack in packs:
        for pid, decl in load_pack_purposes(pack, Path(root)).items():
            prior = merged.get(pid)
            if prior is not None:
                raise PurposeError(
                    f"purpose {pid!r} is declared by both {prior.pack!r} and {pack!r}. Two packs "
                    f"cannot share a purpose id: they would carry different lawful bases behind "
                    f"one name and the ledger could not say which applied")
            merged[pid] = decl
    vocab = PurposeVocabulary(merged, packs)
    if use_cache:
        with _LOCK:
            _CACHE[key] = vocab
    return vocab


# --- the active vocabulary --------------------------------------------------------
_BOUND: ContextVar[Optional[PurposeVocabulary]] = ContextVar(
    "databridge_adapter_purposes", default=None)


def active_purposes() -> PurposeVocabulary:
    """The vocabulary a `Principal` is validated against right now.

    Falls through to the deployment binding, read from `databridge_graph.packs` so that "which
    packs are enabled" has exactly one definition in the codebase. The import is deliberately
    lazy: the contract types must be importable without dragging the graph in.
    """
    bound = _BOUND.get()
    if bound is not None:
        return bound
    from databridge_graph.packs import active_vocabulary, packs_root   # noqa: PLC0415
    return purpose_vocabulary(active_vocabulary().packs, packs_root())


@contextmanager
def use_purposes(vocab: PurposeVocabulary) -> Iterator[PurposeVocabulary]:
    """Bind an explicit purpose vocabulary for the duration of the block.

    Mirrors `databridge_graph.packs.deployment()`. Context-local rather than global so a test (or
    a concurrently-serving request) cannot widen another's vocabulary.
    """
    token = _BOUND.set(vocab)
    try:
        yield vocab
    finally:
        _BOUND.reset(token)

"""What each adapter **advertises** - read from the estate, never from the agent's own declaration.

A capability grant is only worth minting if something independent of the requester states what the
adapter offers. In this checkout two things do:

* ``fixtures/packs/<pack>/sources.yaml`` - one entry per source, each with a
  ``capabilities: [...]`` list. Those are the port verbs (`describe`, `plan`, `resolve`,
  `verify`, `snapshot_present`, `verify_live_bytes`, `verify_against_capture`), which is what a
  relational or document source actually advertises.
* ``fixtures/packs/<pack>/adapters/*.yaml`` - the Adapter Builder declarations (AGENT_PLANE §6.2),
  whose ``capabilities: {read: [...], write: [...]}`` block names *tools*, e.g. ``permit_lookup``.

Both shapes are real and the catalogue carries both, because the point of the screen is that the
agent author cannot widen it. AGENT_PLANE §3 illustrates capabilities as ``core_banking.account_read``;
no such capability is advertised anywhere in this tree, and a compiler that accepted it would be
validating a name against itself.

## Two facts the catalogue carries besides the capability list

**`max_trust`** - the worst trust label the adapter can emit, computed from the estate's declared
``trust_class`` and ``untrusted_fields`` (spine D3: trust is per field, never per source; a source
whose default is `trusted` still carries free-text columns that are not). It is what a grant's
trust ceiling is checked against and what the sink policy is derived from.

**`writes`** - kept separate and never grantable. A write is an `ActionType` with a human gate
(D12, §6.4), not a checkbox on a read adapter, and an agent declaration that names one is refused
rather than quietly given a read grant with a misleading name.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping, Optional, Sequence

import yaml

from ._yaml_strict import strict_safe_load
from .errors import CatalogueError
from .paths import estates_root, relative_to_root

ADAPTERS_DIR = "adapters"
SOURCES_FILE = "sources.yaml"

#: Trust labels, as strings. Mirrors `databridge_core.Trust`; kept as strings so the catalogue can
#: be read without importing the enum into every record.
_TRUSTED = "trusted"
_UNTRUSTED = "untrusted"


@dataclass(frozen=True)
class AdapterAdvert:
    """One adapter, as the estate advertises it. A statement of fact, not of permission."""

    source_id: str
    pack: str
    reads: frozenset[str]
    writes: frozenset[str]
    max_trust: str
    egress_class: str
    declared_in: str
    kind: str = ""

    def __post_init__(self) -> None:
        if not self.reads and not self.writes:
            raise CatalogueError(
                f"{self.declared_in}: source {self.source_id!r} advertises no capabilities. An "
                f"adapter that can be asked for nothing is not grantable, and an empty set would "
                f"make the capability screen vacuous (the same rule `SourceDescription` applies)")
        if self.max_trust not in (_TRUSTED, _UNTRUSTED):
            raise CatalogueError(
                f"{self.declared_in}: source {self.source_id!r} has max_trust "
                f"{self.max_trust!r}, which is not a label of the trust lattice")

    @property
    def capabilities(self) -> frozenset[str]:
        """Read capabilities only. Writes are deliberately absent - see the module docstring."""
        return self.reads

    def advertises(self, capability: str) -> bool:
        return capability in self.reads

    def is_write(self, capability: str) -> bool:
        return capability in self.writes

    def as_record(self) -> dict:
        return {"source_id": self.source_id, "pack": self.pack,
                "reads": sorted(self.reads), "writes": sorted(self.writes),
                "max_trust": self.max_trust, "egress_class": self.egress_class,
                "declared_in": self.declared_in}


class AdapterCatalogue:
    """The adapters one or more packs' estates advertise. Sealed after construction.

    Sealed for the reason `PurposeVocabulary` is: this object decides whether a grant may be
    minted, it is cached, and a caller holding a live handle into a "closed" advertisement could
    widen an agent's authority after it compiled.
    """

    __slots__ = ("_adverts", "_packs")

    def __init__(self, adverts: Mapping[str, AdapterAdvert], packs: Sequence[str]) -> None:
        self._adverts: Mapping[str, AdapterAdvert] = MappingProxyType(dict(adverts))
        self._packs: tuple[str, ...] = tuple(packs)

    @property
    def packs(self) -> tuple[str, ...]:
        return self._packs

    @property
    def adapters(self) -> Mapping[str, AdapterAdvert]:
        return self._adverts

    def __contains__(self, source_id: object) -> bool:
        return source_id in self._adverts

    def __iter__(self) -> Iterator[AdapterAdvert]:
        return iter(self._adverts.values())

    def get(self, source_id: str) -> Optional[AdapterAdvert]:
        return self._adverts.get(source_id)

    def require(self, source_id: str) -> AdapterAdvert:
        try:
            return self._adverts[source_id]
        except KeyError:
            known = ", ".join(sorted(self._adverts)) or "(none)"
            raise CatalogueError(
                f"no adapter {source_id!r} is advertised by the estates of pack(s) "
                f"{list(self._packs)}. Advertised: {known}"
            ) from None


# --------------------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------------------
def _load_yaml(path: Path) -> dict:
    try:
        # Same hazard as finding 8 (R-013) in declaration.py: an estate's sources.yaml or
        # adapters/*.yaml is what a capability grant is screened AGAINST, so a duplicate key that
        # silently keeps its last value would let the estate's own advertisement disagree with
        # what a reviewer read.
        data = strict_safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise CatalogueError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise CatalogueError(f"{path}: top level must be a mapping")
    return data


def _capability_list(value: Any, where: str, what: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise CatalogueError(f"{where}: {what} must be a list of strings, got {value!r}")
    return frozenset(value)


def _max_trust(entry: Mapping[str, Any]) -> str:
    """The worst trust label this source can put on a cell.

    Reads the estate's own two statements and takes the stricter, in the strict direction only -
    exactly as ``sources.yaml`` says its ``untrusted_fields`` override works. There is no
    field-level promotion to trusted anywhere in this design, so this cannot go the other way.
    """
    declared = str(entry.get("trust_class") or _UNTRUSTED).strip().lower()
    if declared not in (_TRUSTED, _UNTRUSTED):
        raise CatalogueError(f"source {entry.get('id')!r}: trust_class {declared!r} is not a "
                             f"label of the trust lattice")
    if declared == _UNTRUSTED:
        return _UNTRUSTED
    untrusted_fields = entry.get("untrusted_fields") or []
    return _UNTRUSTED if untrusted_fields else _TRUSTED


def _max_trust_from_field_map(trust_class: Any, where: str) -> str:
    """Same question for an Adapter Builder declaration, whose ``trust_class`` is per FIELD.

    The default for an absent field is `untrusted` (AGENT_PLANE §6.3: everything a third-party
    server returns is untrusted unless a human declared otherwise, in writing). So an adapter
    declaration with no trust map at all is untrusted, not trusted.
    """
    if trust_class is None:
        return _UNTRUSTED
    if not isinstance(trust_class, dict):
        raise CatalogueError(f"{where}: trust_class must be a per-field mapping")
    labels = {str(v).strip().lower() for v in trust_class.values()}
    unknown = sorted(labels - {_TRUSTED, _UNTRUSTED})
    if unknown:
        raise CatalogueError(f"{where}: trust_class carries labels {unknown} that are not in the "
                             f"trust lattice")
    return _UNTRUSTED if _UNTRUSTED in labels or not labels else _TRUSTED


def load_estate_sources(pack: str, estate: Path) -> dict[str, AdapterAdvert]:
    """Read ``<estate>/sources.yaml``. A pack with no estate advertises no adapters."""
    path = estate / SOURCES_FILE
    if not path.is_file():
        return {}
    data = _load_yaml(path)
    entries = data.get("sources")
    if entries is None:
        return {}
    if not isinstance(entries, list):
        raise CatalogueError(f"{path}: 'sources' must be a list")
    where = relative_to_root(path)
    out: dict[str, AdapterAdvert] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id"):
            raise CatalogueError(f"{path}: each sources entry must be a mapping with an 'id'")
        source_id = str(entry["id"])
        reads = _capability_list(entry.get("capabilities"), where,
                                 f"source {source_id!r} capabilities")
        if not reads:
            # A source that advertises nothing is not an adapter this catalogue can speak for.
            # Skipped rather than refused: `sources.yaml` also carries hazard entries (named
            # failure scenarios) under the same list, and they legitimately advertise nothing.
            continue
        out[source_id] = AdapterAdvert(
            source_id=source_id, pack=pack, reads=reads, writes=frozenset(),
            max_trust=_max_trust(entry),
            egress_class=str(entry.get("egress_class") or "INTERNAL"),
            declared_in=where, kind=str(entry.get("kind") or ""))
    return out


def load_estate_adapters(pack: str, estate: Path) -> dict[str, AdapterAdvert]:
    """Read every ``<estate>/adapters/*.yaml`` - the Adapter Builder declarations (§6.2)."""
    base = estate / ADAPTERS_DIR
    if not base.is_dir():
        return {}
    out: dict[str, AdapterAdvert] = {}
    for path in sorted(base.glob("*.yaml")):
        data = _load_yaml(path)
        where = relative_to_root(path)
        source_id = str(data.get("source_id") or data.get("adapter") or "").strip()
        if not source_id:
            raise CatalogueError(f"{path}: an adapter declaration must name `adapter` or "
                                 f"`source_id`")
        caps = data.get("capabilities")
        if caps is not None and not isinstance(caps, dict):
            raise CatalogueError(f"{path}: capabilities must be a mapping with `read` and `write`")
        caps = caps or {}
        reads = _capability_list(caps.get("read"), where, "capabilities.read")
        writes = _capability_list(caps.get("write"), where, "capabilities.write")
        overlap = sorted(reads & writes)
        if overlap:
            raise CatalogueError(
                f"{path}: capabilit{'y' if len(overlap) == 1 else 'ies'} {overlap} appear(s) in "
                f"both read and write. A write is a different capability class with its own rules "
                f"(§6.4); one name meaning both makes the grant screen unable to say which was "
                f"granted")
        out[source_id] = AdapterAdvert(
            source_id=source_id, pack=pack, reads=reads, writes=writes,
            max_trust=_max_trust_from_field_map(data.get("trust_class"), where),
            egress_class=str(data.get("egress_class") or "INTERNAL"),
            declared_in=where, kind="mcp")
    return out


def load_catalogue(packs: Sequence[str], root: Optional[Path] = None) -> AdapterCatalogue:
    """Every adapter the given packs' estates advertise.

    Two packs advertising the same ``source_id`` is a **collision, not a merge**, for the reason
    `purpose_vocabulary` refuses a shared purpose id: one name would carry two credential scopes
    and two egress classes, and a grant record could not say which applied.
    """
    base = Path(root) if root is not None else estates_root()
    merged: dict[str, AdapterAdvert] = {}
    for pack in packs:
        estate = base / pack
        for source_id, advert in {**load_estate_sources(pack, estate),
                                  **load_estate_adapters(pack, estate)}.items():
            prior = merged.get(source_id)
            if prior is not None and prior.pack != pack:
                raise CatalogueError(
                    f"source {source_id!r} is advertised by both {prior.pack!r} "
                    f"({prior.declared_in}) and {pack!r} ({advert.declared_in}). One source id "
                    f"cannot mean two adapters: they carry different credentials and different "
                    f"egress classes, and a grant record could not say which was resolved")
            merged[source_id] = advert
    return AdapterCatalogue(merged, packs)

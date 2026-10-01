"""The **source contract**: `sources.yaml` plus reflection, turned into a `SourceDescription`.

Two halves, and keeping them apart is the whole point.

* **What the source declares about itself** — pinning tier, cursor honesty, delete detectability,
  effective retention, egress class, reachable verdicts, which columns hold personal data, and
  which columns are third-party writable. That is `fixtures/packs/<estate>/sources.yaml`, and it
  is a statement by the estate's data owner. `describe.source_description_from_yaml` already reads
  most of it.
* **What the source actually has** — the columns, their types, the primary key. That is
  reflection, and it is a statement by the database.

`describe()` needs both, and neither may be inferred from the other. In particular:

**Trust is a lookup, never an inference** (spine D3). A field's trust class comes from the
contract's `untrusted_text_columns`, which is a statement about *who could write the bytes*. It is
not derived from the column's type, its name, its contents, or the fact that the table is
first-party. `core_banking.party.notes` is `UNTRUSTED` in a database the bank owns, because a
customer's own words get copied into it — and R06a's finding that free-text notes columns are
exactly where national identifiers end up is the same observation from the PII side.

**A source the contract does not classify has no trust default.** `describe.trust_of` deliberately
has no fallback for a field reflection never described at all; this loader's own fallback is the
SOURCE's declared `trust_class` (`classification_axes` in `sources.yaml`) - never a hard-coded
"trusted". A source with no `trust_class` is refused at construction, naming the source and the
file: the alternative is a fail-open where every unclassified column silently becomes trusted, and
"deliberately trusted" and "never considered" stop being distinguishable. `untrusted_fields` (or
the v1 spelling `untrusted_text_columns`) narrows individual columns to UNTRUSTED on top of that
default; there is no field-level promotion back to trusted anywhere in any estate.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import yaml

from databridge_core import Trust

from ..describe import (
    EntityDescription,
    FieldDescription,
    SourceDescription,
    source_description_from_yaml,
)


class SourceContractError(Exception):
    """`sources.yaml` is missing, malformed, or does not classify something reflection found."""


@lru_cache(maxsize=8)
def _load_sources_yaml(path: str) -> Mapping[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise SourceContractError(
            f"{p}: no source contract. An adapter with no contract cannot stamp trust, and trust "
            f"that is inferred instead of declared is the fail-open D3 exists to prevent")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SourceContractError(f"{p}: top level must be a mapping")
    return data


#: `classification_axes.trust_class` in `sources.yaml`: the SOURCE-LEVEL default every reflected
#: column inherits unless `untrusted_fields` narrows it. "The source default is `trusted`; these
#: [untrusted_fields] override it in the strict direction" (`fixtures/packs/finance/sources.yaml`,
#: `core_banking.untrusted_fields`) - finance declares no field-level promotion back to trusted at
#: all. Some `untrusted`-default sources DO need one: `inspection_photos` (government) is
#: `trust_class: untrusted` because its OCR text is world-input, but `inspection_id`,
#: `photo_batch_ref`, `captured_by` and `captured_at` "come from the agency's own device fleet and
#: batch assignment, not from the photographed world" - so that source declares them explicitly in
#: `trusted_fields`, a targeted, per-field EXCEPTION to its own default, never a blanket one.
_TRUST_FROM_YAML: Mapping[str, Trust] = {"trusted": Trust.TRUSTED, "untrusted": Trust.UNTRUSTED}


def _covers(declared: str, *, source_id: str, entity: str, column: str) -> bool:
    """Does one `untrusted_fields` / `trusted_fields` / `sensitive_columns` entry cover this field?

    Four forms the estates actually write, least to most specific:

    * ``"*"``                    the whole SOURCE.
    * ``"<entity>.*"``           a whole ENTITY, written RELATIVE to the entity - the module
                                  docstring's own example is ``filings.*``, not
                                  ``core_banking.filings.*``.
    * ``"<entity>.<column>"`` or the fully-qualified ``"<source_id>.<entity>.<column>"`` - one
      field.
    * ``"<column>"``             a bare column name, with NO entity qualifier at all -
                                  `inspection_photos.trusted_fields` writes `inspection_id`,
                                  `photo_batch_ref`, `captured_by`, `captured_at` this way, because
                                  the declaration is "these come from the agency's device fleet,
                                  wherever this source exposes them", not one specific entity.

    **Review R-005 finding 5's wildcard bug.** The previous check compared the entity-relative
    wildcard against the FULLY QUALIFIED path (``f"{source_id}.{entity}.{column}"``), which never
    starts with ``f"{entity}."`` - so ``"party.*"`` silently covered NOTHING and every column of
    an entity an estate meant to blanket-declare untrusted was actually left at the source
    default. This checks the wildcard against both forms, and additionally accepts a bare column
    name for the fourth form above.
    """
    path = f"{source_id}.{entity}.{column}"
    short = f"{entity}.{column}"
    if declared in ("*", path, short, column):
        return True
    if declared.endswith(".*"):
        prefix = declared[:-1]                      # "party." or "core_banking.party."
        return short.startswith(prefix) or path.startswith(prefix)
    return False


class SourceContract:
    """One source's entry in `sources.yaml`, with the per-field statements it makes."""

    __slots__ = ("_entry", "_path", "_estate", "_untrusted", "_trusted", "_sensitive",
                "_default_trust")

    def __init__(self, entry: Mapping[str, Any], *, path: Path, estate: str) -> None:
        self._entry = dict(entry)
        self._path = path
        self._estate = estate
        # `untrusted_fields` is the estate's v2 spelling; `untrusted_text_columns` was v1. Both
        # are read, because a contract that renamed a key must not silently stop labelling
        # anything - "no untrusted columns declared" and "we are reading the wrong key" look
        # identical from here, and only one of them is safe.
        self._untrusted = tuple(str(c) for c in (entry.get("untrusted_fields")
                                                 or entry.get("untrusted_text_columns") or ()))
        # `trusted_fields`: a targeted, per-field PROMOTION back to trusted, for a source whose
        # own default is untrusted but which still has some columns nothing in the world writes
        # (see the module-level comment on `_TRUST_FROM_YAML`). Read the same way as
        # `_untrusted` - a list of paths/wildcards, matched with the identical `_covers` rule.
        self._trusted = tuple(str(c) for c in (entry.get("trusted_fields") or ()))
        self._sensitive = tuple(str(c) for c in (entry.get("sensitive_columns") or ()))

        # **Review R-005 finding 5.** `trust_of` used to fall through to `Trust.TRUSTED` for any
        # column absent from `_untrusted` - fail-OPEN, and the exact opposite of
        # `describe.SourceDescription.trust_of`, which fails closed with `KeyError` for a field it
        # was never told about. The source's own declared `trust_class` (`classification_axes` in
        # `sources.yaml`) is the DEFAULT every reflected column inherits; `untrusted_fields` only
        # narrows it, never promotes it back. A source that declares no `trust_class` at all has
        # nothing to default an unclassified column TO, and silently choosing "trusted" for it is
        # precisely the fail-open D3 exists to prevent - so this refuses at construction, naming
        # the source and the file, rather than emitting a description that quietly makes every
        # unlisted column actionable.
        raw_trust_class = entry.get("trust_class")
        key = str(raw_trust_class).strip().lower() if raw_trust_class is not None else ""
        if key not in _TRUST_FROM_YAML:
            raise SourceContractError(
                f"{path}: source {entry.get('id')!r} declares no trust_class (got {raw_trust_class!r}"
                f", expected 'trusted' or 'untrusted'). Every reflected column defaults to this "
                f"source-level trust unless `untrusted_fields`/`untrusted_text_columns` narrows it "
                f"further, in the strict direction only; a source with no trust_class has no default "
                f"to give an unclassified column, and defaulting it to TRUSTED anyway is the fail-open "
                f"D3 exists to prevent. Classify the source with `trust_class: trusted|untrusted`")
        self._default_trust = _TRUST_FROM_YAML[key]

    @property
    def source_id(self) -> str:
        return str(self._entry["id"])

    @property
    def entry(self) -> Mapping[str, Any]:
        return dict(self._entry)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def estate(self) -> str:
        return self._estate

    @property
    def schema(self) -> Optional[str]:
        """The namespace the source's entities live in, parsed out of the declared transport.

        `core_banking`'s transport carries ``options=-csearch_path%3Dcore_banking``. That string is
        how the estate says which schema its tables are in, so it is read rather than restated —
        one fact, one place.
        """
        transport = str(self._entry.get("transport") or "")
        marker = "search_path%3D"
        if marker in transport:
            return transport.split(marker, 1)[1].split("&", 1)[0]
        return None

    def stream(self, entity: str) -> Optional[Mapping[str, Any]]:
        """The `streams[]` entry for one entity, if the contract declares per-stream facts.

        The v2 finance contract is explicit that **the source tier is the MINIMUM over its
        streams** and that the per-stream tier "is the one an adapter plans on". So an adapter
        that only ever read the source-level tier would plan against the weakest table in the
        database for every read - and one that only read the per-stream tier would advertise a
        capability matrix the source as a whole cannot honour. Both are needed, separately.
        """
        want = f"{self.source_id}.{entity}"
        for stream in (self._entry.get("streams") or ()):
            if str(stream.get("name")) in (want, entity):
                return dict(stream)
        return None

    def entity_tier(self, entity: str) -> str:
        """The tier an adapter PLANS on for one entity, falling back to the source's."""
        stream = self.stream(entity)
        if stream and stream.get("tier"):
            return str(stream["tier"]).upper()
        return str(self._entry.get("pinning_tier") or "T2").upper()

    def change_signal_field(self, entity: str) -> Optional[str]:
        """The entity's declared watermark column, if it has one.

        Read from the stream's own cursor rather than guessed from column names. The v2 contract
        is emphatic that `updated_at` is "absent from six tables and is written by whichever code
        path happened to touch the row" - so a Core that assumed the column existed because the
        name is familiar would be inventing a change signal, which is exactly what §5 forbids
        before emitting `DRIFT_BENIGN`.
        """
        stream = self.stream(entity) or {}
        cursor = dict(stream.get("cursor") or {})
        field = cursor.get("field") or cursor.get("fallback_field")
        return str(field) if field else None

    @property
    def declared_tier(self) -> str:
        return str(self._entry.get("pinning_tier") or "T2").upper()

    @property
    def detects_benign_drift(self) -> bool:
        """Whether the contract says this source can substantiate `DRIFT_BENIGN` at all (§5)."""
        return bool(self._entry.get("drift_benign_capable", False))

    @property
    def hazards(self) -> tuple[str, ...]:
        return tuple(str(h) for h in (self._entry.get("hazards") or ()))

    @property
    def append_only_tables(self) -> frozenset[str]:
        return frozenset(str(t) for t in (self._entry.get("append_only_tables") or ()))

    # --- the per-field statements ---
    def trust_of(self, entity: str, column: str) -> Trust:
        """The declared trust class of one column. A LOOKUP; there is no inference path here.

        Starts from the SOURCE's own declared default (`trust_class`), narrowed to UNTRUSTED for
        anything `_untrusted` covers, with `_trusted` a targeted exception back the other way for
        a source whose default IS untrusted but which still has some columns nothing in the world
        writes (`inspection_photos`'s `trusted_fields`). `_untrusted` is checked FIRST and wins on
        overlap - a column named in both is the estate contradicting itself, and the safe reading
        of a contradiction is the more restrictive one, not the more permissive one.
        """
        if any(_covers(declared, source_id=self.source_id, entity=entity, column=column)
              for declared in self._untrusted):
            return Trust.UNTRUSTED
        if any(_covers(declared, source_id=self.source_id, entity=entity, column=column)
              for declared in self._trusted):
            return Trust.TRUSTED
        return self._default_trust

    def is_sensitive(self, entity: str, column: str) -> bool:
        return any(_covers(declared, source_id=self.source_id, entity=entity, column=column)
                  for declared in self._sensitive)

    def declared_column_paths(self) -> frozenset[str]:
        return frozenset(self._untrusted) | frozenset(self._sensitive)

    # --- assembling the description ---
    def describe(self, *, capabilities: frozenset[str],
                 entities: Sequence[EntityDescription]) -> SourceDescription:
        return source_description_from_yaml(self._entry, capabilities=capabilities,
                                            entities=tuple(entities))

    def entity_description(self, entity: str, *, columns: Sequence[Mapping[str, Any]],
                           key: Sequence[str]) -> EntityDescription:
        """Turn reflected columns into a described entity, stamping trust per field.

        ``columns`` is SQLAlchemy's reflection shape: ``{"name", "type", "nullable", ...}``. The
        pack-level type name is derived from the dialect type by the same normalisation
        `databridge_ingest.postgres` uses, because a description that leaked ``VARCHAR(18)`` into
        the capability matrix would make the matrix dialect-specific — and the whole point of the
        port is that the orchestrator never learns what kind of source it is talking to.
        """
        fields = tuple(
            FieldDescription(
                name=str(c["name"]),
                type=pack_type(str(c.get("type", ""))),
                trust=self.trust_of(entity, str(c["name"])),
                sensitive=self.is_sensitive(entity, str(c["name"])),
            )
            for c in columns
        )
        if not fields:
            raise SourceContractError(
                f"{self.source_id}.{entity}: reflection found no columns. An entity with no "
                f"described fields cannot be projected, and returning an empty description would "
                f"read as 'this table is empty' rather than 'we could not see it'")
        return EntityDescription(name=entity, fields=fields, key=tuple(key))


def pack_type(dialect_type: str) -> str:
    """Dialect type -> the PACK's type name.

    Mirrors ``databridge_ingest.postgres._norm_dtype``, and
    `test_the_pack_type_map_matches_the_ingest_connector` asserts the two agree on the estate's
    own DDL. Restated rather than imported for the reason `derivations.py` gives: importing the
    connector would pull SQLAlchemy into a package whose top level is a driver-free contract.
    """
    d = dialect_type.lower()
    if "bool" in d:
        return "boolean"
    if "int" in d or "serial" in d:
        return "integer"
    if any(t in d for t in ("numeric", "decimal", "real", "double", "float", "money")):
        return "numeric"
    if any(t in d for t in ("timestamp", "datetime", "date")):
        return "timestamp"
    if "text" in d:
        return "text"
    if any(t in d for t in ("char", "string", "uuid")):
        return "varchar"
    if "json" in d:
        return "jsonb" if "jsonb" in d else "json"
    return d or "unknown"


def load_source_contract(source_id: str, *, sources_yaml: Path) -> SourceContract:
    """Read one source's contract out of an estate's `sources.yaml`."""
    data = _load_sources_yaml(str(Path(sources_yaml).resolve()))
    for entry in (data.get("sources") or ()):
        if str(entry.get("id")) == source_id:
            return SourceContract(entry, path=Path(sources_yaml),
                                  estate=str(data.get("estate") or ""))
    known = ", ".join(sorted(str(e.get("id")) for e in (data.get("sources") or ())))
    raise SourceContractError(
        f"{sources_yaml}: no source {source_id!r}. Declared: {known or '(none)'}. An adapter for "
        f"an undeclared source has no contract to stamp trust from, so it is refused at "
        f"construction rather than reading with everything unlabelled")

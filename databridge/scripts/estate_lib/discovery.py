"""Discover estates, sources and their SQL from the fixtures on disk.

Rules this module obeys:
  * estates are found by globbing for `sources.yaml`; none is named in code;
  * a source's SQL file list is obtained by *following its own loader script*
    (`\\i` / `\\ir` / `\\include`), never by listing `seed/*.sql` and hoping the
    order is right;
  * the schema name is parsed out of the DDL, not typed here.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# `\i file` / `\ir file` / `\include file` / `\include_relative file`
_PSQL_INCLUDE = re.compile(r"^\s*\\(?:i|ir|include|include_relative)\s+(\S+)\s*$")
# any other psql backslash meta-command (`\set ON_ERROR_STOP on`, `\echo ...`)
_PSQL_META = re.compile(r"^\s*\\")
_CREATE_SCHEMA = re.compile(r"\bCREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)", re.I)


class DiscoveryError(RuntimeError):
    """The estate on disk does not match what `sources.yaml` says about it."""


@dataclass(frozen=True)
class SourceSpec:
    """One entry from a pack's `sources.yaml`, resolved against the filesystem."""

    pack: str
    pack_dir: Path
    id: str
    raw: dict[str, Any]

    @property
    def title(self) -> str:
        return str(self.raw.get("title") or self.id)

    @property
    def kind(self) -> str:
        return str(self.raw.get("kind") or "unknown")

    @property
    def pinning_tier(self) -> str:
        return str(self.raw.get("pinning_tier") or "?")

    @property
    def held_out(self) -> bool:
        return bool(self.raw.get("held_out"))

    @property
    def is_relational(self) -> bool:
        return self.kind == "postgresql"

    @property
    def formats(self) -> list[str]:
        f = self.raw.get("formats")
        return [str(x).lstrip(".").lower() for x in f] if isinstance(f, list) else []

    def root(self) -> Path | None:
        """Best-effort filesystem root for a non-relational source.

        Preference order: the `file://` transport, then the directory holding the
        declared entrypoint, then a directory named after the source id.
        """
        transport = self.raw.get("transport")
        if isinstance(transport, str) and transport.startswith("file://"):
            p = (self.pack_dir / transport[len("file://"):]).resolve()
            if p.exists():
                return p
        entry = self.raw.get("entrypoint")
        if isinstance(entry, str):
            p = (self.pack_dir / entry).resolve()
            if p.is_dir():
                return p
            if p.exists():
                return p.parent
            if p.parent.is_dir():
                return p.parent
        p = (self.pack_dir / self.id).resolve()
        return p if p.is_dir() else None

    def index_csv(self) -> Path | None:
        """A declared index/listing CSV for this source, if one exists."""
        entry = self.raw.get("entrypoint")
        if isinstance(entry, str) and entry.lower().endswith(".csv"):
            p = (self.pack_dir / entry).resolve()
            if p.is_file():
                return p
        root = self.root()
        for base in filter(None, (root, root.parent if root else None)):
            for name in ("INDEX.csv", "listing.csv", "index.csv"):
                cand = base / name
                if cand.is_file():
                    return cand
        return None


@dataclass(frozen=True)
class RelationalSource:
    """A `postgresql` source, with its SQL resolved into an ordered file list."""

    spec: SourceSpec
    schema: str
    sql_files: list[Path] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def pack(self) -> str:
        return self.spec.pack

    def fingerprint(self, translator_version: str = "") -> str:
        """Content hash over the ordered SQL, used to make loading idempotent."""
        h = hashlib.sha256()
        h.update(translator_version.encode())
        for p in self.sql_files:
            h.update(p.name.encode())
            h.update(p.read_bytes())
        return h.hexdigest()

    def total_sql_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.sql_files)


@dataclass(frozen=True)
class EstateSpec:
    pack: str
    pack_dir: Path
    doc: dict[str, Any]
    sources: list[SourceSpec]

    @property
    def relational(self) -> list[SourceSpec]:
        return [s for s in self.sources if s.is_relational]

    @property
    def non_relational(self) -> list[SourceSpec]:
        return [s for s in self.sources if not s.is_relational]


def packs_dir(repo_root: Path) -> Path:
    return repo_root / "fixtures" / "packs"


def discover_estates(repo_root: Path, only: list[str] | None = None) -> list[EstateSpec]:
    """Every pack under `fixtures/packs/` that ships a `sources.yaml`."""
    out: list[EstateSpec] = []
    for yml in sorted(packs_dir(repo_root).glob("*/sources.yaml")):
        pack = yml.parent.name
        if only and pack not in only:
            continue
        doc = yaml.safe_load(yml.read_text(encoding="utf-8"))
        srcs = doc.get("sources") or []
        if not isinstance(srcs, list):
            raise DiscoveryError(f"{yml}: `sources` must be a list, got {type(srcs).__name__}")
        specs = [
            SourceSpec(pack=pack, pack_dir=yml.parent, id=str(s["id"]), raw=s)
            for s in srcs
            if isinstance(s, dict) and s.get("id")
        ]
        out.append(EstateSpec(pack=pack, pack_dir=yml.parent, doc=doc, sources=specs))
    if only:
        found = {e.pack for e in out}
        missing = [p for p in only if p not in found]
        if missing:
            raise DiscoveryError(f"no such estate(s): {', '.join(missing)}")
    return out


def _expand_loader(path: Path, seen: tuple[Path, ...] = ()) -> list[Path]:
    """Follow psql `\\i` / `\\ir` includes into a flat, ordered file list.

    A file with includes must contain *only* includes and meta/comments — otherwise
    the SQL it also carries would be silently reordered, so we refuse instead.
    """
    if path in seen:
        raise DiscoveryError(f"include cycle at {path}")
    if not path.is_file():
        raise DiscoveryError(f"loader references a missing file: {path}")
    includes: list[Path] = []
    stray_sql = False
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _PSQL_INCLUDE.match(line)
        if m:
            includes.append((path.parent / m.group(1)).resolve())
            continue
        s = line.strip()
        if s and not s.startswith("--") and not _PSQL_META.match(line):
            stray_sql = True
    if not includes:
        return [path]
    if stray_sql:
        raise DiscoveryError(
            f"{path} mixes \\i includes with inline SQL; the load order would be ambiguous"
        )
    out: list[Path] = []
    for inc in includes:
        out.extend(_expand_loader(inc, seen + (path,)))
    return out


def resolve_relational(spec: SourceSpec) -> RelationalSource:
    """Turn a `postgresql` source descriptor into an ordered list of SQL files."""
    entry = spec.raw.get("entrypoint")
    if not entry:
        raise DiscoveryError(f"{spec.pack}/{spec.id}: relational source has no `entrypoint`")
    files = _expand_loader((spec.pack_dir / entry).resolve())
    seed = spec.raw.get("seed")
    if seed:
        files += _expand_loader((spec.pack_dir / seed).resolve())

    schema = ""
    for p in files:
        m = _CREATE_SCHEMA.search(p.read_text(encoding="utf-8", errors="replace"))
        if m:
            schema = m.group(1)
            break
    if not schema:
        raise DiscoveryError(
            f"{spec.pack}/{spec.id}: no CREATE SCHEMA found in {len(files)} SQL file(s); "
            "cannot determine the schema name without guessing"
        )
    return RelationalSource(spec=spec, schema=schema, sql_files=files)


def all_relational(estates: list[EstateSpec]) -> list[RelationalSource]:
    return [resolve_relational(s) for e in estates for s in e.relational]

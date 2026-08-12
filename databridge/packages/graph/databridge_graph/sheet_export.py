"""Node evidence sheets, written out as **files** — a folder you can open, diff, or index.

``node_sheet.node_markdown`` already renders everything the graph knows about one entity, with a
citation per value. This module does one thing with that output: it writes it down, once per node,
plus an ``index.md``. Nothing else. The provenance story is easy to *assert* and hard to *show*;
a directory of readable, greppable, diffable markdown is the version a person can check without
running the app, and the version that survives the demo being switched off.

**The exported bytes are exactly what the renderer returned.** No wrapping, no front-matter, no
tidying, no trailing-newline convention — ``sheet.encode("utf-8")`` and nothing else, written in
binary so Windows text mode cannot rewrite the line endings underneath us. Every deviation would
have to be re-justified against the sheet, and the whole value of the export is that it needs no
justification: it IS the sheet. ``test_exported_bytes_are_exactly_the_sheet`` pins this.

**This is not a new disclosure surface.** It exports the nodes ``node_markdown`` will already
render and skips every node it returns ``None`` for. There is no path here that reads a node the
sheet refuses to show, and no field that reaches a file without passing through that renderer.

**Filenames are derived from the entity id, never from a label.** Labels are document text — which
is attacker-controlled — and a filename is not a place where markdown escaping helps: what is at
stake is *which file gets written*, not how it renders. So the name is built from the id, and even
then only from characters that cannot mean anything to a filesystem (see ``sheet_filename``).
Ids themselves are partly attacker-derived (``document:<the uploaded filename>``), so the same rule
applies to them; the sanitiser assumes hostility rather than trusting the shape ids happen to have.

**A historical export names only what existed then.** ``as_of`` threads into the renderer, so the
sheets are the ones that instant would have produced. A node that did not exist yet is *omitted* —
not written empty, and not written with today's label, which is the leak ``node_sheet._node_at``
exists to prevent. The ids of the omitted nodes go in ``manifest.json`` (a record of the export
RUN, for whoever pressed the button) and are deliberately kept out of ``index.md`` (part of the
historical DOCUMENT), because a table of contents that names entities the clinic had never heard of
at that instant is exactly the leak, moved one file sideways.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, Sequence

# The sheet renderer, and the two helpers that decide WHICH node a request is even about and how a
# foreign string is allowed to appear in markdown. They are private to `node_sheet`, and importing
# them is deliberate: the alternative is a second temporal-resolution rule and a second escaper
# living here, either of which could drift out of agreement with the document being exported. The
# rule this module follows is the one in CLAUDE.md — reuse, do not rebuild.
from .node_sheet import _code, _node_at, _text, _vocab, node_markdown

__all__ = [
    "DEFAULT_ROOT", "INDEX_NAME", "MANIFEST_NAME",
    "ExportManifest", "SheetExport", "SheetFile", "SkippedNode",
    "assign_filenames", "export_sheets", "iter_zip_chunks", "sheet_filename",
]

DEFAULT_ROOT = "clinic-evidence-sheets"
INDEX_NAME = "index.md"
MANIFEST_NAME = "manifest.json"

# --- filename safety ---------------------------------------------------------------------------
#
# ALLOW-list, not a deny-list. A deny-list of "dangerous" characters is a list of the attacks
# someone thought of; this keeps ASCII letters, digits, `.`, `-` and `_` and turns EVERYTHING else
# into `-`. That single rule is what makes traversal impossible rather than merely unlikely:
# `/`, `\`, `:` (drive letters and NTFS alternate data streams), NUL, newlines, `*?"<>|`, and every
# non-ASCII character cannot survive it, so the result is always ONE path component. `..` cannot
# survive either — not because dots are removed, but because a bare `..` strips to empty.
_DISALLOWED = re.compile(r"[^A-Za-z0-9._-]+")
_DASH_RUN = re.compile(r"-{2,}")

# Names Windows refuses regardless of extension: `NUL.md` IS the null device, and writing to it
# succeeds while producing no file — a silently-dropped evidence sheet, which is the worst
# possible failure for this particular artifact. `index`/`manifest` are reserved for the same practical
# reason: a node must not be able to overwrite the export's own two files.
_RESERVED_STEMS = (
    {"con", "prn", "aux", "nul", "index", "manifest"}
    | {f"com{i}" for i in range(10)} | {f"lpt{i}" for i in range(10)}
)

# Long enough for every real id, short enough that root + name stays inside Windows' 260-char path
# limit with room to spare. Truncation is safe because uniqueness is restored by the hash below.
MAX_STEM = 100


def sheet_filename(entity_id: str) -> str:
    """A single, safe path component for one entity id — ``<stem>.md``.

    Rejects by construction (never by inspection): path separators, `..`, absolute paths, drive
    letters, control characters and newlines, and the Windows device names. The value is
    *derived* from the id rather than validated, so there is no "unsafe input" branch to forget.

    Collisions are possible after sanitising and are NOT resolved here — see `assign_filenames`,
    which needs the whole set to resolve them the same way every run.
    """
    stem = _DISALLOWED.sub("-", entity_id)
    stem = _DASH_RUN.sub("-", stem).strip("-.")
    if len(stem) > MAX_STEM:
        stem = stem[:MAX_STEM].rstrip("-.")
    if not stem:
        # An id made entirely of characters we refuse to keep (`../..`, `:::`) still needs a file.
        # It gets a generic stem and, because every such id collides, a hash from `assign_filenames`.
        stem = "node"
    if stem.split(".")[0].casefold() in _RESERVED_STEMS:
        # Windows resolves the device name from the part BEFORE the first dot, so `nul.md` is the
        # null device but `nul_.md` is an ordinary file.
        stem += "_"
    return f"{stem}.md"


def _digest(entity_id: str, length: int) -> str:
    return hashlib.sha256(entity_id.encode("utf-8")).hexdigest()[:length]


def assign_filenames(entity_ids: Sequence[str]) -> dict[str, str]:
    """``entity_id -> filename``, unique across the whole set, and stable between runs.

    Two ids can sanitise to the same stem (`patient:a/b` and `patient:a-b`), and on Windows and
    macOS two stems differing only in case are the SAME file. Either way one sheet would silently
    overwrite the other — the export would look complete and be short by one node, which is a
    failure mode that never announces itself.

    So a stem shared by more than one id disqualifies itself: EVERY id in that group takes a
    ``-<sha256[:8]>`` suffix. Both survive, neither is "the winner", and — the property that makes
    these files diffable — the name depends only on the id, so adding a node tomorrow does not
    renumber the files exported today. That is why this is not a `-2`, `-3` counter.
    """
    stems = {entity_id: sheet_filename(entity_id) for entity_id in entity_ids}
    shared = Counter(name.casefold() for name in stems.values())
    names = {
        entity_id: (f"{name[:-3]}-{_digest(entity_id, 8)}.md" if shared[name.casefold()] > 1
                    else name)
        for entity_id, name in stems.items()
    }
    # A hashed name can still land on some other id's plain stem. Vanishingly unlikely, but the
    # consequence is a lost sheet, so it is resolved rather than argued about: the full digest of
    # two different ids cannot be equal short of a SHA-256 collision.
    still_shared = Counter(name.casefold() for name in names.values())
    for entity_id, name in list(names.items()):
        if still_shared[name.casefold()] > 1:
            names[entity_id] = f"{sheet_filename(entity_id)[:-3]}-{_digest(entity_id, 64)}.md"
    return names


def _safe_join(directory: Path, name: str) -> Path:
    """``directory / name``, or raise. The belt to the sanitiser's braces.

    `sheet_filename` already makes an escaping name unconstructible; this asserts it at the point
    of the actual write, so a future change to the sanitiser fails loudly here instead of writing
    somewhere it should not.
    """
    if name in ("", ".", "..") or name != Path(name).name:
        raise ValueError(f"refusing to write {name!r}: not a single path component")
    target = directory / name
    if target.resolve().parent != directory.resolve():
        raise ValueError(f"refusing to write {name!r}: it escapes {directory}")
    return target


# --- the manifest ------------------------------------------------------------------------------


@dataclass(frozen=True)
class SheetFile:
    """One written sheet. ``label`` is the label AT ``as_of`` — never today's."""

    entity_id: str
    kind: str
    label: str
    filename: str


@dataclass(frozen=True)
class SkippedNode:
    entity_id: str
    reason: str


@dataclass
class ExportManifest:
    """What the run actually wrote, so a caller can report it instead of assuming it."""

    generated_at: dt.datetime
    as_of: Optional[dt.datetime]
    root: str
    files: list[SheetFile] = field(default_factory=list)
    skipped: list[SkippedNode] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.files)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at.isoformat(),
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "root": self.root,
            "index": INDEX_NAME,
            "count": self.count,
            # `label` is untrusted document text, and JSON is its own encoder — `json.dumps`
            # escapes what matters HERE (quotes, control characters), whereas the markdown
            # escaping `index.md` needs would corrupt the value for anything reading this file.
            "files": [{"entity_id": f.entity_id, "kind": f.kind, "label": f.label,
                       "filename": f.filename} for f in self.files],
            "skipped": [{"entity_id": s.entity_id, "reason": s.reason} for s in self.skipped],
            "note": ("Each .md file is the byte-for-byte output of the graph's own evidence-sheet "
                     "renderer. This manifest records the export run; the evidence is in the "
                     "sheets."),
        }


def _as_utc(when: Optional[dt.datetime]) -> Optional[dt.datetime]:
    """The graph stores UTC instants; a naive one is read as UTC rather than crashing.

    Comparing a naive datetime with the store's aware ones raises `TypeError` deep inside the
    renderer — a 500 for what is really a caller passing `2026-05-01` without a zone. Every
    timestamp in the store is UTC, so UTC is the only self-consistent reading of a bare instant.
    """
    if when is None or when.tzinfo is not None:
        return when
    return when.replace(tzinfo=dt.timezone.utc)


class SheetExport:
    """One export run: which nodes, rendered how, under what name.

    Iterating ``iter_files()`` yields ``(name, bytes)`` for every sheet, then ``index.md``, then
    ``manifest.json`` — index last because it lists what came before it. ``manifest`` fills in as
    iteration proceeds and is complete once it ends.
    """

    def __init__(self, store, *, as_of: Optional[dt.datetime] = None,
                 generated_at: Optional[dt.datetime] = None, root: str = DEFAULT_ROOT):
        self.store = store
        self.as_of = _as_utc(as_of)
        self.generated_at = _as_utc(generated_at) or dt.datetime.now(dt.timezone.utc)
        self.root = root.strip("/")
        self.manifest = ExportManifest(generated_at=self.generated_at, as_of=self.as_of,
                                       root=self.root)

    # -- selection ------------------------------------------------------------------------
    def _all_ids(self) -> list[str]:
        """Every entity id the graph has ever held, current or superseded, in a stable order.

        Filenames are assigned over THIS set rather than over the exported subset, so an entity's
        file is named the same whether the export is of today or of a Tuesday in May. A name that
        moved because a different node was in scope would make two exports diff as though every
        file had changed.
        """
        return [row["id"] for row in
                self.store.fetchall("SELECT DISTINCT id FROM nodes ORDER BY id")]

    def _candidate_ids(self) -> list[str]:
        if self.as_of is None:
            return [row["id"] for row in self.store.open_nodes()]
        # Rewound, a node whose record was later replaced still existed at that instant, and
        # `node_markdown` will render it (labelled as a superseded belief). Enumerating only the
        # currently-open ids would drop exactly those from a historical export.
        return self._all_ids()

    # -- rendering ------------------------------------------------------------------------
    def iter_files(self) -> Iterator[tuple[str, bytes]]:
        # Re-iterating is a fresh run, not an append: a manifest that accumulated across two
        # passes would report twice as many files as the zip contains.
        self.manifest.files.clear()
        self.manifest.skipped.clear()
        names = assign_filenames(self._all_ids())

        # Two passes, and the split is deliberate. The first decides WHICH entities the export is
        # of and in what order (kind, then label) — cheap, and it needs the whole set before it can
        # sort. The second renders them one at a time and hands each straight to the caller, so the
        # process holds ONE sheet rather than a graph's worth of markdown waiting to be zipped.
        records: list[SheetFile] = []
        for entity_id in self._candidate_ids():
            node, _superseded = _node_at(self.store, entity_id, self.as_of)
            if node is None:
                self.manifest.skipped.append(SkippedNode(
                    entity_id,
                    "did not exist at the requested as_of" if self.as_of
                    else "no open version in the graph"))
                continue
            # The label is read at `as_of` too — the index must not caption a rewound sheet with
            # the name the entity goes by today.
            records.append(SheetFile(entity_id=entity_id, kind=node.kind.value, label=node.label,
                                     filename=names[entity_id]))
        records.sort(key=lambda r: (r.kind, r.label.casefold(), r.entity_id))

        for record in records:
            sheet = node_markdown(self.store, record.entity_id, as_of=self.as_of)
            if sheet is None:
                # Unreachable while the renderer's existence test is the one used above — kept
                # because the invariant that matters is "the export never writes what the sheet
                # refuses to render", and it must hold even if the two ever disagree.
                self.manifest.skipped.append(SkippedNode(
                    record.entity_id, "the renderer declined to produce a sheet"))
                continue
            self.manifest.files.append(record)
            # THE export. Nothing is added, removed or re-wrapped: the file is the sheet.
            yield self._path(record.filename), sheet.encode("utf-8")

        yield self._path(INDEX_NAME), self._index_markdown().encode("utf-8")
        yield self._path(MANIFEST_NAME), (
            json.dumps(self.manifest.to_dict(), indent=2, sort_keys=False).encode("utf-8"))

    def _path(self, name: str) -> str:
        # Always "/" — a zip entry name is not an OS path, and a backslash in one is a filename on
        # POSIX rather than a directory.
        return f"{self.root}/{name}" if self.root else name

    def _index_markdown(self) -> str:
        as_of = (f"{self.as_of.isoformat()} (valid time)" if self.as_of
                 else "the graph as it stands now")
        lines = [
            "# Clinic evidence sheets",
            "",
            "One file per entity in the clinic knowledge graph. Each is the byte-for-byte output "
            "of the same renderer the app shows when you click a node — and the same text the "
            "assistant is given as grounding, so what a reader can check is what the model read.",
            "",
            f"- **Generated:** {self.generated_at.isoformat()}",
            f"- **As of:** {as_of}",
            f"- **Sheets:** {self.manifest.count}",
            "",
            "Nothing here is generated prose. Every value in every sheet carries the citation for "
            "that value alone, and anything the graph could not ground is named under "
            "*Unresolved* rather than filled in. Identity is never determined by this system: a "
            "document naming a patient records a CLAIM against registry candidates, which clinic "
            "staff check in person.",
        ]

        by_kind: dict[str, list[SheetFile]] = {}
        for record in self.manifest.files:
            by_kind.setdefault(record.kind, []).append(record)
        for kind in sorted(by_kind):
            entries = by_kind[kind]
            # `kind` is a NodeKind — a closed vocabulary from schema.py, and the one class of
            # string here that is ours. The label and the id are not, and go through the sheet
            # renderer's own encoders; the FILENAME needs neither, because the sanitiser above
            # already left it with nothing markdown can read as syntax.
            lines += ["", f"## {_vocab(kind)} ({len(entries)})", ""]
            lines += [f"- [{_text(r.label)}]({r.filename}) — {_code(r.entity_id)}"
                      for r in entries]

        if self.manifest.skipped:
            lines += [
                "",
                "## Omitted",
                "",
                f"_{len(self.manifest.skipped)} entities in the graph today are not part of this "
                f"export because they did not exist at the instant it was taken. A historical "
                f"export names only what existed then; the run's own record of what it skipped is "
                f"in `{MANIFEST_NAME}`._",
            ]
        return "\n".join(lines)


def export_sheets(store, dest, *, as_of: Optional[dt.datetime] = None,
                  generated_at: Optional[dt.datetime] = None) -> ExportManifest:
    """Write the whole export into ``dest`` (created if absent). Returns what was written.

    Flat: one directory of files plus ``index.md``, because the caller already chose the folder.
    """
    directory = Path(dest)
    directory.mkdir(parents=True, exist_ok=True)
    export = SheetExport(store, as_of=as_of, generated_at=generated_at, root="")
    for name, data in export.iter_files():
        # Binary, always. Text mode on Windows rewrites "\n" to "\r\n", and the file would then
        # not be the bytes the renderer produced — the one property this module promises.
        _safe_join(directory, name).write_bytes(data)
    return export.manifest


def iter_zip_chunks(export: SheetExport) -> Iterator[bytes]:
    """The export as a zip, in chunks, holding one sheet in memory at a time.

    `zipfile` writes happily to a non-seekable stream (it falls back to data descriptors), so the
    archive is drained entry by entry instead of being assembled whole and then sent.
    """
    buffer = _ChunkBuffer()
    stamp = export.generated_at.timetuple()[:6]
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in export.iter_files():
            info = zipfile.ZipInfo(name, stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
            chunk = buffer.drain()
            if chunk:
                yield chunk
    tail = buffer.drain()          # the central directory, written by ZipFile.close()
    if tail:
        yield tail


class _ChunkBuffer(io.RawIOBase):
    """A write-only sink `zipfile` can target, emptied after every entry."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._position = 0

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:                                  # type: ignore[override]
        payload = bytes(data)
        self._chunks.append(payload)
        self._position += len(payload)
        return len(payload)

    def tell(self) -> int:
        # zipfile records offsets from here; it must count every byte ever written, not the
        # bytes still buffered.
        return self._position

    def drain(self) -> bytes:
        out = b"".join(self._chunks)
        self._chunks.clear()
        return out

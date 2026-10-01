"""`DocumentCore` — the T3 Core (`ADAPTER_AGENT_SPEC` §5, §7a). **Deterministic. No LLM.**

Three readers behind one adapter. `PostgresCore` reads a source that can at least tell you what it
says *now*, inside a transaction, against a schema it will admit to. This one reads sources that
can do none of that:

* **`case_files`** — OCR text dropped on a filesystem. No version, no tombstone, and an `mtime`
  the estate's own contract calls *"wrong in both directions"*.
* **`legacy_records`** — a 220-byte fixed-width cut of a 1998 mainframe file, re-cut in full every
  night. One generation exists. Yesterday's is gone, and record offsets are not stable across
  cuts.
* **`inspection_photos`** — OCR bounding-box sidecars for photographs that **do not ship and never
  existed**. The sidecar corroborates only itself.

**T3 is the whole point, and it is what makes the Capture Store load-bearing rather than
decorative.** There is no past to return to, so:

1. `snapshot(as_of=<anything>)` **raises `CannotPinPast`**. There is no branch that substitutes
   "now" — a caller that believes it received the past and did not is the failure the tier system
   exists to prevent, and it is silent, because the rows look exactly like rows.
2. **Every ledger-bound read is captured before it is parsed**, and every pointer carries
   `capture_digest`. A `doc_span` or `ocr_bbox` pointer that cites only a file path cites a file
   anyone can edit; without the digest nobody can later prove *which* bytes were parsed, and the
   store is decoration.
3. `verify()` resolves against the **capture**, not the live file, and its default positive answer
   is `VERIFIED_AS_OF_CAPTURE`. The live path is consulted to detect `VANISHED` — and, for the one
   source whose contract declares a live-bytes check, to promote to `VERIFIED` or to report the
   re-scan as `DRIFT_MATERIAL`.
4. **`DRIFT_BENIGN` is not advertisable here.** These sources have no entity-level change signal
   covering anything, so "something moved and it was not what we cited" is unanswerable. §5 says
   an adapter that cannot compute it never emits it, and `assert_verdict_advertised` enforces that
   rather than trusting this module to remember.

## The reader is chosen by the CONTRACT, never by sniffing a filename

`sources.yaml` declares `kind: document_drop | fixed_width | json_sidecar`, and `_READERS` maps
that declaration to a reader class. Nothing here looks at a file extension to decide how to parse
a file: a `.txt` in the drop and a `.md` in the drop are the same kind of artefact because the
contract says the source is a document drop, and a source that re-labelled its files tomorrow
would not silently change how it is read. An undeclared kind is a refusal at construction.

The same rule governs the layout. `FixedWidthReader` parses **`COPYBOOK.cpy`** — the field names
and their PIC widths come out of the declaration and the offsets are accumulated from them. There
is no table of column positions in this file to drift away from the copybook, and the accumulated
width is checked against the contract's declared `record_bytes` (220) so a copybook edit that does
not add up fails loudly.

## OCR noise, and the one thing an adapter must not do about it

The estate plants character confusions (`OATE OF BIRTH`, `ADDRES5`, `CA5E REFERENCE`), mid-word
hyphenation from re-flowed wrapping, page furniture and misread identifiers, and it says exactly
what the correct behaviour is:

> *"Some values are genuinely unrecoverable, and the correct output for those is `unresolved`, not
> a repaired guess."*

So the text reader is a **generic labelled-line reader**: any line of the form `LABEL: value` is a
field, and the column name is the label slugged. `DATE OF BIRTH:` yields `date_of_birth`;
`OATE OF BIRTH:` yields `oate_of_birth`, which means a projection asking for `date_of_birth`
receives **`Absent`** for that document. That is the estate's rule implemented rather than
described. There is no alias table repairing `OATE` to `DATE`, because such a table is a guess
wearing a citation, and the whole system exists to prevent exactly that.

It also means the field set of a document drop is **discovered, not declared** — a drop has no
schema but the union of what its documents happen to say — so `describe()` scans the drop once and
caches. That is real I/O in `describe()`, and it is named here rather than hidden: for a
schemaless source, "what columns exist" *is* a fact about the files.

## Emission: verbatim where the bytes match, `derived` where they cannot

§4a is unambiguous — if the emitted bytes differ from the cited bytes, the pointer kind is
`derived` or the value is ungrounded. Three places here cannot be verbatim, and each takes a
registered, replayable derivation rather than a comment:

| Where | Why not verbatim | Rule |
|---|---|---|
| fixed-width `PIC X` fields | `LM-SURNAME` is `"LOMACOTT"` followed by 22 spaces. Emitting the trimmed name against a span covering all 30 bytes is not byte-identical | `fixed_width_trim@v1(width=30)` |
| JSON non-strings | the cited bytes are `false` / `0.879`; the emitted value is a `bool` / `float` | `json_scalar@v1()` |
| JSON strings carrying escapes | the cited bytes are the escaped form | `json_scalar@v1()` |

A JSON string with no escapes cites its **inner** span and stays verbatim, because a locator that
points at the value itself is worth more than one that points at the value plus its quotes.

## What a `doc_span` pointer has to survive

§4a: a span pointer is verified by **slicing out the cited offsets first**, and only then
comparing. So `_assert_grounded_in_capture` hands the wall the *whole captured artefact* — never
the pre-sliced value — and additionally requires the pointer's `capture_digest` to name the very
capture those bytes came from. That closes the gap `grounding.py`'s docstring names honestly:
`assert_grounded` is given bytes by its caller and cannot know they came from the cited location.
Here it can, because the caller checks the content address first.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field as _dc_field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Union

from databridge_core import (
    Absent,
    GroundedValue,
    SnapshotIdentity,
    SourcePointer,
    Trust,
    Withheld,
)

from ..canonical import canonical_json, canonical_scalar, sha256_of
from ..capture import Capture, CaptureError, CaptureNotFound, CaptureShredded, CaptureStore
from ..contract import CapabilityRequest
from ..describe import EntityDescription, FieldDescription, SourceDescription
from ..errors import (
    AdapterError,
    CannotPinPast,
    CapabilityNotAdvertised,
    PolicyNotApplied,
    UngroundedValue,
    UnorderedPointeredRead,
)
from ..grounding import (
    assert_derived_from,
    assert_grounded,
    parse_derivation,
    register_derivation,
    replay_derivation,
)
from ..policy import Admission, PolicyEngine
from ..results import AdapterResult, Cell, Cost, GroundedRows, PointerResolution, ReadPlan
from ..verdicts import Verdict, assert_verdict_advertised, combine_verdicts
from .contract import SourceContract, load_source_contract

__all__ = [
    "CAPABILITIES",
    "Artefact",
    "Bbox",
    "DocCell",
    "DocEntity",
    "DocField",
    "DocRow",
    "DocumentCore",
    "DocumentSourceError",
    "FixedWidthReader",
    "READERS",
    "SidecarReader",
    "Span",
    "TextDropReader",
    "cell_digest",
    "rows_digest",
    "slug",
]

#: What this Core can be asked for. A closed set, advertised in `describe()` and screened by Policy
#: before anything runs. `predicate=` is refused by the contract itself until §6's portable grammar
#: exists, so `document_read` is the whole-projection ordered read over one entity and nothing else.
CAPABILITIES: frozenset[str] = frozenset({"structural_read", "document_read"})

#: A read cap large enough that no reader ever hits it before exhausting its actual rows — used by
#: `resolve()`'s resolution mode (R-005 finding 12), which must search the reader's FULL
#: enumeration for the cited row rather than only the first `req.limit` of them.
_UNBOUNDED_READ: int = 2**31


class DocumentSourceError(AdapterError):
    """The estate on disk does not match what its contract declares.

    Fails closed and names the file. A document drop whose index points at a file that is not
    there, a copybook whose PIC widths do not add up to the declared record length, or a sidecar
    declaring a coordinate system this reader does not implement are all the same class of
    problem: the contract and the bytes disagree, and guessing which one is right is how a
    citation ends up addressing something nobody checked.
    """


# =======================================================================================
# digests
# =======================================================================================


def cell_digest(value: Any) -> str:
    """The content address of ONE cell — what `verify()` compares to decide material drift.

    Per-cell, and §7a correction 1 is the reason: putting the read-level digest on every pointer
    makes *any* change anywhere in the read report as material drift for *every* pointer from it,
    which collapses the lattice to "everything is always material".
    """
    try:
        token = canonical_scalar(value)
    except (TypeError, ValueError):                              # pragma: no cover - defensive
        token = {"t": f"opaque:{type(value).__name__}", "v": sha256_of(repr(value))}
    return sha256_of(canonical_json(token))


def rows_digest(rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> str:
    """**Order-independent** digest of what was actually read (§5, rule 2).

    Sorted per-row digests, so a different directory-listing order, a different shard order or a
    changed pagination boundary does not read as data drift. Without this rule verification
    produces noise within a week and somebody turns it off, which makes every pointer decorative.
    """
    per_row = sorted(
        sha256_of(canonical_json({f: cell_digest(r.get(f)) for f in fields})) for r in rows)
    return sha256_of(canonical_json(per_row))


def projection_schema_hash(entity: str, fields: Sequence[str],
                           declared: Mapping[str, str]) -> str:
    """`schema_hash` over the **read projection only** (spine D6).

    R02's finding: hashing the whole source schema produces false-drift storms. For a document
    drop the "schema" is the set of labels the drop happens to carry, so hashing all of it would
    invalidate every pointer into the estate the first time one scanned page misread one label.
    """
    return sha256_of(canonical_json({
        "entity": entity,
        "fields": [{"name": f, "type": declared.get(f, "text")} for f in fields],
    }))


# =======================================================================================
# replayable derivations (§4a)
# =======================================================================================


def _fixed_width_trim_v1(parents: Sequence[Any], args: Mapping[str, str]) -> Any:
    """Strip the trailing pad from one fixed-width field.

    ``width`` is required and checked, not decorative: the rule has to be replayable five years
    from now against the same bytes, and a trim whose declared width does not match the cited span
    is trimming something other than the field it claims.
    """
    if len(parents) != 1:
        raise UngroundedValue(
            f"fixed_width_trim@v1 transforms exactly one field, got {len(parents)} parent(s)")
    raw = parents[0]
    if not isinstance(raw, str):
        raise UngroundedValue(
            f"fixed_width_trim@v1 operates on the cited characters, got {type(raw).__name__}")
    declared = args.get("width")
    if declared is None:
        raise UngroundedValue(
            "fixed_width_trim@v1 requires a `width`: without it the rule cannot be checked "
            "against the span it claims to have trimmed")
    if int(declared) != len(raw):
        raise UngroundedValue(
            f"fixed_width_trim@v1(width={declared}) was replayed over {len(raw)} character(s): "
            f"the cited span is not the field the derivation names")
    return raw.rstrip(" ")


register_derivation(
    "fixed_width_trim", 1, _fixed_width_trim_v1,
    doc="Remove the trailing space padding from a fixed-width COBOL `PIC X` field. The cited span "
        "covers the whole declared field; the emitted value is the field without its pad.")


def _json_scalar_v1(parents: Sequence[Any], args: Mapping[str, str]) -> Any:
    """Decode one JSON scalar literal from the cited bytes.

    Used where the emitted value cannot be the cited characters: `false` is four characters and a
    `bool`, `0.879` is five characters and a `float`, and a string carrying an escape is not its
    own contents. Each of those is a transformation, so each takes a `derived` pointer (§4a).
    """
    if len(parents) != 1:
        raise UngroundedValue(
            f"json_scalar@v1 decodes exactly one literal, got {len(parents)} parent(s)")
    raw = parents[0]
    if not isinstance(raw, str):
        raise UngroundedValue(
            f"json_scalar@v1 decodes the cited characters, got {type(raw).__name__}")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UngroundedValue(
            f"json_scalar@v1 replayed over {raw!r} is not a JSON literal: {exc}") from exc
    if isinstance(decoded, (dict, list)):
        raise UngroundedValue(
            "json_scalar@v1 decodes SCALARS. A container has internal structure that a single "
            "cell cannot cite, and flattening it would emit a value no span addresses")
    return decoded


register_derivation(
    "json_scalar", 1, _json_scalar_v1,
    doc="Decode one JSON scalar literal (number, boolean, null, or an escaped string) from the "
        "characters the span cites.")


# =======================================================================================
# locators
# =======================================================================================


@dataclass(frozen=True)
class Span:
    """A character range of one captured artefact. Becomes a `doc_span` pointer.

    Offsets are into the DECODED text of the captured bytes, and `verify()` re-slices them out of
    the capture — so the round trip is exact by construction rather than by convention.
    """

    document_id: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class Bbox:
    """One OCR block's geometry. Becomes an `ocr_bbox` pointer.

    The sidecar declares `coordinate_origin` and `bbox_format`, and `SidecarReader` refuses
    anything it has not implemented rather than assuming. An unstated coordinate convention is a
    locator that means a different rectangle to every reader.
    """

    media_id: str
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    ocr_conf: float


@dataclass(frozen=True)
class Artefact:
    """One captured file, and the decoded text the cells were parsed out of.

    `capture` is the identity: the pointers minted from this artefact carry
    `capture.qualified_digest`, and `verify()` goes back to the store rather than to `live_path`.
    `live_path` exists only so `verify()` can answer "is it still there?" and, where the contract
    declares a live-bytes check, "has it been re-scanned?".
    """

    id: str
    capture: Capture
    text: str
    live_path: Path


@dataclass(frozen=True)
class DocCell:
    """One value, the artefact it came from, and exactly where in it.

    Exactly one of `span` / `bbox` is set. `derivation`, when present, names a registered rule
    whose replay over `raw` must produce the emitted value — the Core does not compute the emitted
    value itself, it *replays the declared rule*, so what is emitted and what a later auditor can
    reproduce are the same thing by construction.
    """

    column: str
    raw: Any
    artefact: Artefact
    span: Optional[Span] = None
    bbox: Optional[Bbox] = None
    derivation: Optional[str] = None


@dataclass(frozen=True)
class DocRow:
    """One row. A column absent from `cells` was looked for and not found — it becomes `Absent`.

    That is the OCR rule (`unresolved`, never a repaired guess) and the fixed-width rule (an
    all-pad field holds nothing) expressed as data rather than as a branch in the Core.
    """

    row_id: str
    cells: Mapping[str, DocCell]
    #: Values used only for ordering and for row-level policy. Never emitted, never cited.
    sort_values: Mapping[str, Any] = _dc_field(default_factory=dict)


@dataclass(frozen=True)
class DocField:
    """One readable column, and the path it has in the SOURCE CONTRACT's own notation.

    `contract_field` is the join between two vocabularies that were written independently. The
    government contract says `inspection_photos.ocr.blocks[].text` is untrusted; this reader calls
    that column `ocr_block.text`. Trust is a **lookup in the contract**, so the reader declares
    which contract path each of its columns is, and the Core looks the trust up there. It is not
    an inference and it is not the reader asserting a trust class — a Core that let its reader
    choose would let the thing being contained decide whether it is contained.
    """

    name: str
    type: str = "text"
    contract_field: Optional[str] = None


@dataclass(frozen=True)
class DocEntity:
    """One readable entity of a document source."""

    name: str
    fields: tuple[DocField, ...]
    #: The stable row identity a pointer cites. Empty where the source has none — see
    #: `DocumentCore._row_identity` on why an ordinal is never the identity here.
    key: tuple[str, ...] = ()

    def field(self, name: str) -> Optional[DocField]:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    @property
    def types(self) -> Mapping[str, str]:
        return {f.name: f.type for f in self.fields}


# =======================================================================================
# span-preserving parsers
# =======================================================================================

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slug(label: str) -> Optional[str]:
    """A document label, slugged into a projectable column name — or `None` if it cannot be one.

    Deliberately dumb. `DATE OF BIRTH` becomes `date_of_birth` and `OATE OF BIRTH` becomes
    `oate_of_birth`, because the second is what the page says and repairing it would be a guess
    with a citation attached. `None` is returned for a label that cannot be a `CapabilityRequest`
    identifier at all (empty, or leading digit), which drops the column rather than mangling it
    into a name that addresses nothing.
    """
    text = _SLUG_STRIP.sub("_", label.strip().lower()).strip("_")
    if not text or not text[0].isalpha():
        return None
    return text


def _contained_path(base: Path, relative: str) -> Path:
    """Resolve `relative` against `base`, and refuse if the result would land outside `base`.

    `relative` is a cell out of an INDEX the estate wrote — CSV content nobody but the estate
    produced, and the contract makes no promise about what a row's path column contains. An
    absolute path (`pathlib` silently drops everything before an absolute operand when joined —
    ``Path("/a") / "/b" == Path("/b")``), a `..` climb, or a symlink that leaves `base` would let
    the index choose an arbitrary file on the machine to be captured and cited as though it
    belonged to the declared source: the untrusted index deciding what gets read (invariant 5). So
    this fails closed and names the row rather than reading whatever it names — a file-read oracle
    is exactly the class of bug a T3 Core exists not to have (R-005 finding 1).
    """
    resolved_base = base.resolve(strict=False)
    candidate = (base / relative).resolve(strict=False)
    if not candidate.is_relative_to(resolved_base):
        raise DocumentSourceError(
            f"{relative!r} resolves to {candidate}, which is outside the declared source "
            f"directory {resolved_base}. An index row does not get to choose a file outside its "
            f"own drop; refusing rather than reading whatever it names")
    return candidate


def csv_rows_with_spans(text: str) -> tuple[list[str], list[list[tuple[str, int, int]]]]:
    """Parse a CSV into `(header, rows)` where every field carries its exact character span.

    `csv.reader` gives values and loses locations, and a pointer needs the location. So the fields
    are scanned here, and a quoted field whose content is not identical to its inner span — one
    carrying a doubled quote — is **refused** rather than mis-sliced: a span that is one character
    off is worse than no span, because it verifies against the wrong bytes.

    The header is the first NON-BLANK line, not line zero (R-005 finding 15): a blank line ahead of
    the header used to make the real header get read as the first DATA row instead, silently
    narrowing the columns and turning column values into row content.
    """
    header: list[str] = []
    rows: list[list[tuple[str, int, int]]] = []
    pos = 0
    header_taken = False
    for line in text.split("\n"):
        line_len = len(line)
        stripped = line[:-1] if line.endswith("\r") else line
        if stripped.strip():
            fields = _scan_csv_line(stripped, pos)
            if not header_taken:
                header = [v for v, _s, _e in fields]
                header_taken = True
            else:
                rows.append(fields)
        pos += line_len + 1
    return header, rows


def _scan_csv_line(line: str, offset: int) -> list[tuple[str, int, int]]:
    """One line, RFC 4180 style: `""` inside a quoted field is an escaped quote, not the end of it.

    R-005 finding 15: the previous scan stopped at the FIRST `"`, so a field written as `"a""b"`
    was silently truncated to `a` instead of being refused as promised — the doubled-quote check
    below it could never fire, because a value sliced that way can never contain a bare `"`. An
    unterminated quoted field (no closing quote anywhere on the line) was silently accepted too,
    taking the rest of the line as the field. Both are refused now instead of silently accepted.
    """
    out: list[tuple[str, int, int]] = []
    i = 0
    n = len(line)
    while True:
        if i < n and line[i] == '"':
            j = i + 1
            while True:
                j = line.find('"', j)
                if j < 0:
                    raise DocumentSourceError(
                        "a quoted CSV field has no closing quote on its line; multiline quoted "
                        "fields are not supported here, and taking the rest of the line as the "
                        "field would silently accept a value this scanner cannot actually bound")
                if j + 1 < n and line[j + 1] == '"':
                    j += 2                          # an escaped "" inside the field — keep going
                    continue
                break
            raw = line[i + 1:j]
            if '""' in raw:
                raise DocumentSourceError(
                    "a CSV field with a doubled quote decodes to different characters than its "
                    "cited span contains; refusing rather than emitting a locator whose bytes do "
                    "not match the value")
            out.append((raw, offset + i + 1, offset + j))
            i = j + 1
            if i < n and line[i] == ",":
                i += 1
                continue
            break
        j = line.find(",", i)
        if j < 0:
            out.append((line[i:], offset + i, offset + n))
            break
        out.append((line[i:j], offset + i, offset + j))
        i = j + 1
    return out


_JSON_KEY = re.compile(r'"(?P<key>[A-Za-z0-9_]+)"\s*:\s*')


def json_scalar_spans(text: str, *, keys: Iterable[str],
                      within: Optional[tuple[int, int]] = None) -> dict[str, tuple[Any, int, int]]:
    """Locate the top-level scalar members of a JSON object, with their exact spans.

    A JSON parse gives values and loses locations, so the literals are found here with
    `raw_decode`, which returns both the value and where it ended. Containers are skipped: a dict
    or a list has internal structure no single cell can cite, and flattening one would emit a
    value that no span addresses.
    """
    decoder = json.JSONDecoder()
    wanted = set(keys)
    lo, hi = within if within is not None else (0, len(text))
    found: dict[str, tuple[Any, int, int]] = {}
    for m in _JSON_KEY.finditer(text, lo, hi):
        key = m.group("key")
        if key not in wanted or key in found:
            continue
        start = m.end()
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:                             # pragma: no cover - defensive
            continue
        if isinstance(value, (dict, list)):
            continue
        found[key] = (value, start, end)
    return found


# =======================================================================================
# readers
# =======================================================================================


class _Reader:
    """The contract between `DocumentCore` and the three ways of getting bytes off a disk.

    A reader knows how to enumerate an entity's rows and where every value it emits lives inside a
    captured artefact. It knows nothing about pointers, policy, trust or verdicts — those are the
    Core's, and keeping them out of here is what makes a fourth reader a small change.
    """

    #: The contract `kind` this reader implements. Matched against `sources.yaml`, never against a
    #: file extension.
    kind: str = ""

    def __init__(self, *, source_id: str, root: Path, contract: SourceContract,
                 capture: Callable[[Path, str], Artefact]) -> None:
        self.source_id = source_id
        self.root = Path(root)
        self.contract = contract
        self._capture = capture

    # --- schema ---
    def entities(self) -> tuple[DocEntity, ...]:                 # pragma: no cover - abstract
        raise NotImplementedError

    def entity(self, name: str) -> DocEntity:
        for e in self.entities():
            if e.name == name:
                return e
        raise DocumentSourceError(
            f"{self.source_id!r} has no entity {name!r}; it reads "
            f"{sorted(e.name for e in self.entities())}")

    # --- data ---
    def read(self, entity: str, *, limit: int) -> list[DocRow]:  # pragma: no cover - abstract
        raise NotImplementedError

    def estimated_rows(self, entity: str) -> int:                # pragma: no cover - abstract
        raise NotImplementedError

    def live_path_of(self, artefact_id: str) -> Optional[Path]:  # pragma: no cover - abstract
        """Where the artefact a pointer cites lives NOW, or `None` if the index no longer has it.

        Used by `verify()` for exactly two questions — is it still there, and (where the contract
        declares a live-bytes check) has it been re-scanned. Never to resolve a value.
        """
        raise NotImplementedError

    def operations(self, entity: str) -> tuple[str, ...]:        # pragma: no cover - abstract
        raise NotImplementedError


# --- 1. text documents -----------------------------------------------------------------

_LABELLED_LINE = re.compile(
    r"(?m)^(?P<label>[A-Z][A-Z0-9 /_.'\-]{1,38}):[ \t]+(?P<value>\S.*?)[ \t]*$")


class TextDropReader(_Reader):
    """OCR text on a filesystem, cited by exact character offsets (`doc_span`).

    Two artefacts, and both are captured. `INDEX.csv` is one of them: the scanner's own index is
    as unversioned as the pages it lists, so the structural columns cite spans **inside the
    captured index**, not values a reader has to take on faith. The document body is the other,
    and `content` cites the whole of it.

    **`case_ref` is renamed, and the new name comes out of the contract.** `sources.yaml` declares
    ``trusted_binding: {field: case_ref, exposed_as: claimed_case_ref}`` — a value OCR'd off a
    form a member of the public filled in. It may be stored, cited and displayed; it may not be a
    row predicate, may not create a traversable edge and may not decide who sees the document. The
    rename is read from the contract rather than written here, so the estate can change what it
    calls the claim without a code change.
    """

    kind = "document_drop"

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self._index_path = self.root / str(self.contract.entry["entrypoint"])
        self._index: Optional[Artefact] = None
        self._rows: Optional[list[dict[str, Any]]] = None
        self._entities: Optional[tuple[DocEntity, ...]] = None
        binding = dict(self.contract.entry.get("trusted_binding") or {})
        self._claim_field = str(binding.get("field") or "")
        self._claim_alias = str(binding.get("exposed_as") or "") or self._claim_field

    # --- index ---
    def _load_index(self) -> tuple[Artefact, list[dict[str, Any]]]:
        if self._index is not None and self._rows is not None:
            return self._index, self._rows
        if not self._index_path.is_file():
            raise DocumentSourceError(
                f"{self._index_path}: the contract names this as the entrypoint and it is not "
                f"there. Failing closed: a drop read without its index is a directory listing, "
                f"and a directory listing cannot say which files were meant to be in it")
        artefact = self._capture(self._index_path, "text/csv")
        header, raw_rows = csv_rows_with_spans(artefact.text)
        rows: list[dict[str, Any]] = []
        for fields in raw_rows:
            row = {header[i]: fields[i] for i in range(min(len(header), len(fields)))}
            rows.append(row)
        self._index, self._rows = artefact, rows
        return artefact, rows

    def _column_name(self, raw: str) -> Optional[str]:
        if self._claim_field and raw == self._claim_field:
            return self._claim_alias
        return slug(raw)

    # --- schema ---
    def entities(self) -> tuple[DocEntity, ...]:
        if self._entities is not None:
            return self._entities
        index, rows = self._load_index()
        header, _ = csv_rows_with_spans(index.text)
        fields: list[DocField] = []
        seen: set[str] = set()
        for raw in header:
            name = self._column_name(raw)
            # `relative_path` is deliberately not projectable. It is the reader's own plumbing,
            # and a path is not evidence: citing one would invite a consumer to go and read the
            # live file, which is the thing the Capture Store exists to stop.
            if name is None or name in seen or raw == "relative_path":
                continue
            seen.add(name)
            fields.append(DocField(name=name, type="text", contract_field=name))
        fields.append(DocField(name="content", type="text", contract_field="content"))
        seen.add("content")
        # A document drop has no schema but the union of what its documents say. So the label
        # columns are DISCOVERED, and describe() scans once to find them. Stated in the module
        # docstring rather than hidden: for a schemaless source, "what columns exist" is a fact
        # about the files.
        for name in sorted(self._discover_labels(rows)):
            if name not in seen:
                seen.add(name)
                fields.append(DocField(name=name, type="text", contract_field="content"))
        self._entities = (DocEntity(name="document", fields=tuple(fields),
                                    key=("document_id",) if "document_id" in seen else ()),)
        return self._entities

    def _discover_labels(self, rows: Sequence[Mapping[str, Any]]) -> set[str]:
        labels: set[str] = set()
        for row in rows:
            path = self._path_of(row)
            if path is None or not path.is_file():
                continue
            for m in _LABELLED_LINE.finditer(path.read_text(encoding="utf-8")):
                name = slug(m.group("label"))
                if name is not None:
                    labels.add(name)
        return labels

    def _path_of(self, row: Mapping[str, Any]) -> Optional[Path]:
        cell = row.get("relative_path")
        if cell is None:
            return None
        base = self.root / str(self.contract.entry["entrypoint"]).rsplit("/", 1)[0]
        return _contained_path(base, cell[0])

    # --- data ---
    def read(self, entity: str, *, limit: int) -> list[DocRow]:
        declared = self.entity(entity)
        index, rows = self._load_index()
        out: list[DocRow] = []
        for row in rows[:limit]:
            path = self._path_of(row)
            if path is None or not path.is_file():
                # The estate's own contract: "a file that disappears is indistinguishable from
                # one that was never indexed". So it is skipped, never inferred as a delete.
                continue
            body = self._capture(path, "text/plain")
            cells: dict[str, DocCell] = {}
            sort_values: dict[str, Any] = {}
            for raw_name, cell in row.items():
                name = self._column_name(raw_name)
                if name is None or declared.field(name) is None:
                    continue
                value, start, end = cell
                cells[name] = DocCell(column=name, raw=value, artefact=index,
                                      span=Span(index.id, start, end))
                sort_values[name] = value
            cells["content"] = DocCell(column="content", raw=body.text, artefact=body,
                                       span=Span(body.id, 0, len(body.text)))
            for name, (value, start, end) in self._labelled_fields(body).items():
                if name in cells or declared.field(name) is None:
                    continue
                cells[name] = DocCell(column=name, raw=value, artefact=body,
                                      span=Span(body.id, start, end))
                sort_values[name] = value
            out.append(DocRow(row_id=body.id, cells=cells, sort_values=sort_values))
        return out

    @staticmethod
    def _labelled_fields(body: Artefact) -> dict[str, tuple[str, int, int]]:
        """Every `LABEL: value` line, first occurrence winning.

        First-wins because a scanned page repeats its furniture — `SIGNED:` appears on every page
        of a three-page record — and picking the last would make the cited span depend on how many
        pages the scanner produced, which is not a property of the value.
        """
        found: dict[str, tuple[str, int, int]] = {}
        for m in _LABELLED_LINE.finditer(body.text):
            name = slug(m.group("label"))
            if name is None or name in found:
                continue
            found[name] = (m.group("value"), m.start("value"), m.end("value"))
        return found

    def estimated_rows(self, entity: str) -> int:
        _index, rows = self._load_index()
        return len(rows)

    def live_path_of(self, artefact_id: str) -> Optional[Path]:
        if artefact_id == self._index_path.name:
            return self._index_path if self._index_path.is_file() else None
        _index, rows = self._load_index()
        for row in rows:
            path = self._path_of(row)
            if path is not None and path.name == artefact_id:
                return path if path.is_file() else None
        return None

    def operations(self, entity: str) -> tuple[str, ...]:
        return (
            f"capture {self._index_path.name} (text/csv) into the Capture Store",
            "capture each indexed document (text/plain) into the Capture Store",
            "emit doc_span pointers over the CAPTURED bytes, with capture_digest",
            "parse `LABEL: value` lines; a label OCR damage made unreadable yields Absent, never "
            "a repaired guess",
        )


# --- 2. fixed-width legacy records ------------------------------------------------------

_PIC = re.compile(
    r"^\s*(?P<level>\d\d)\s+(?P<name>[A-Z0-9\-]+)\s+PIC\s+(?P<pic>[X9])\((?P<width>\d+)\)\s*\.")


class FixedWidthReader(_Reader):
    """A COBOL flat file, read **from its copybook** — never from column positions typed in here.

    The copybook declares the field names and their PIC widths in order; the offsets are the
    running sum of the widths, and the total is checked against the contract's declared
    `record_bytes`. That is the difference between a reader that survives a copybook change and
    one that silently starts slicing the wrong bytes: if the declaration stops adding up, this
    refuses at construction instead of emitting 8,000 well-formed citations to the wrong fields.

    **`PIC X` fields are emitted `derived`.** `LM-SURNAME` is thirty bytes of which twenty-two are
    pad; the trimmed name is not the cited bytes, so it takes `fixed_width_trim@v1(width=30)` over
    a span that covers the whole declared field (§4a). An all-pad field is `Absent` — the record
    holds nothing there, and emitting `""` would make "the file says nothing" and "the file says
    the empty string" the same fact.

    **Zero-filled dates are emitted verbatim.** The copybook calls `00000000` "unknown", and the
    estate ships impossible dates like `19980230` on purpose. Neither is repaired and neither is
    reinterpreted: `00000000` is what the record holds, it is cited, and what it means is a
    question for the reader of the case — not a judgement for an adapter.
    """

    kind = "fixed_width"

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self._layout = self._parse_copybook()
        self._entity_name = "legacy_master"
        self._shards: Optional[list[Path]] = None

    # --- the copybook IS the layout ---
    def _parse_copybook(self) -> tuple[tuple[str, str, int, int, int], ...]:
        rel = self.contract.entry.get("copybook")
        if not rel:
            raise DocumentSourceError(
                f"{self.source_id!r} is declared `fixed_width` and names no copybook. A "
                f"fixed-width record with no declaration is a byte string, and every field "
                f"position in it would be this reader's invention")
        path = self.root / str(rel)
        if not path.is_file():
            raise DocumentSourceError(f"{path}: the contract names this copybook and it is not "
                                      f"there")
        fields: list[tuple[str, str, int, int, int]] = []
        offset = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            m = _PIC.match(line)
            if not m or m.group("level") == "01":
                continue
            width = int(m.group("width"))
            name = slug(m.group("name"))
            if name is None:                                     # pragma: no cover - defensive
                raise DocumentSourceError(f"{path}: field {m.group('name')!r} cannot be a column")
            fields.append((name, m.group("pic"), offset, offset + width, width))
            offset += width
        declared = int(self.contract.entry.get("record_bytes") or 0)
        if declared and offset != declared:
            raise DocumentSourceError(
                f"{path}: the copybook's PIC widths sum to {offset} bytes and the contract "
                f"declares {declared}. Refusing rather than slicing: an off-by-N layout produces "
                f"well-formed citations to the wrong fields, which is worse than no read at all")
        if not fields:
            raise DocumentSourceError(f"{path}: no PIC clauses found")
        return tuple(fields)

    def _shard_paths(self) -> list[Path]:
        if self._shards is None:
            data = self.root / str(self.contract.entry["entrypoint"]).rsplit("/", 1)[0] / "data"
            self._shards = sorted(data.glob("*.dat"))
            if not self._shards:
                raise DocumentSourceError(f"{data}: the cut is empty; there is nothing to read")
        return self._shards

    def _stride(self, size: int) -> int:
        """220 bytes, plus a record delimiter if this copy has one. Derived, never assumed.

        The copybook says the original has no delimiter and that these copies are LF-terminated,
        so both have to work — and picking the wrong one shifts every record after the first.
        """
        record = self._layout[-1][3]
        for stride in (record + 1, record):
            if size % stride == 0:
                return stride
        raise DocumentSourceError(
            f"a {size}-byte cut is not a whole number of {record}-byte records with or without a "
            f"delimiter; the file and the copybook disagree about the record length")

    # --- schema ---
    def entities(self) -> tuple[DocEntity, ...]:
        fields = tuple(
            DocField(name=name, type="text",
                     contract_field=f"LM-{name[3:].upper().replace('_', '-')}"
                     if name.startswith("lm_") else name)
            for name, _pic, _s, _e, _w in self._layout)
        # No key. The file is re-cut in full nightly, ~8% of records are orphans and some carry a
        # blank LM-LEGACY-KEY, so no column is a stable identity. The pointer's identity is its
        # LOCATOR — the capture digest plus the offsets — which is stable precisely because it
        # addresses the captured cut rather than tonight's.
        return (DocEntity(name="legacy_master", fields=fields, key=()),)

    # --- data ---
    def read(self, entity: str, *, limit: int) -> list[DocRow]:
        self.entity(entity)
        out: list[DocRow] = []
        for shard in self._shard_paths():
            if len(out) >= limit:
                break
            artefact = self._capture(shard, "application/octet-stream")
            stride = self._stride(len(artefact.text))
            for offset in range(0, len(artefact.text), stride):
                if len(out) >= limit:
                    break
                cells: dict[str, DocCell] = {}
                sort_values: dict[str, Any] = {}
                for name, pic, start, end, width in self._layout:
                    raw = artefact.text[offset + start:offset + end]
                    span = Span(artefact.id, offset + start, offset + end)
                    if pic == "X":
                        if not raw.strip(" "):
                            continue          # all pad: the record holds nothing here -> Absent
                        cells[name] = DocCell(
                            column=name, raw=raw, artefact=artefact, span=span,
                            derivation=f"fixed_width_trim@v1(width={width})")
                        sort_values[name] = raw.rstrip(" ")
                    else:
                        cells[name] = DocCell(column=name, raw=raw, artefact=artefact, span=span)
                        sort_values[name] = raw
                out.append(DocRow(row_id=f"{artefact.id}@{offset}", cells=cells,
                                  sort_values=sort_values))
        return out

    def estimated_rows(self, entity: str) -> int:
        total = 0
        for shard in self._shard_paths():
            size = shard.stat().st_size
            total += size // self._stride(size)
        return total

    def live_path_of(self, artefact_id: str) -> Optional[Path]:
        for shard in self._shard_paths():
            if shard.name == artefact_id:
                return shard if shard.is_file() else None
        return None

    def operations(self, entity: str) -> tuple[str, ...]:
        widths = ", ".join(f"{n}:{w}" for n, _p, _s, _e, w in self._layout[:4])
        return (
            f"parse {self.contract.entry.get('copybook')} -> "
            f"{len(self._layout)} fields ({widths}, ...)",
            "capture each .dat shard into the Capture Store",
            "slice each record by the COPYBOOK's accumulated offsets",
            "emit PIC X fields as derived (fixed_width_trim@v1) over the raw padded span",
        )


# --- 3. OCR bbox sidecars ---------------------------------------------------------------


class SidecarReader(_Reader):
    """OCR bounding-box sidecars for photographs that do not ship.

    Two entities, because they make two different claims and conflating them is how OCR text ends
    up binding a photo to a case:

    * **`photo`** — the sidecar's own metadata. `inspection_id`, `photo_batch_ref`, `captured_by`
      and `captured_at` come from the agency's device fleet and the contract declares them
      trusted. The sidecar's `application_id` does not: the contract calls it "a convenience copy
      to be reconciled", and it is left at the source's trust class.
    * **`ocr_block`** — one row per OCR block, whose `text` carries the `ocr_bbox` pointer with
      page, coordinates and `ocr_conf`. **OCR text binds nothing.**

    **What an `ocr_bbox` pointer here can and cannot prove.** The sidecar bytes re-read from the
    Capture Store and match their digest; the bbox lies inside the declared `image.width ×
    image.height`; the coordinate origin and format are declared, so the geometry is unambiguous.
    What is *not* checkable is that the text was ever in an image, that it was at that location,
    or that an image existed at all — `image_present` is `false` throughout. So the verdict is
    `VERIFIED_AS_OF_CAPTURE` **as to the sidecar** and `UNVERIFIABLE` **as to the image**, and
    `verify_with_note` reports both. Reporting only the first would let a reader believe the
    photograph had been checked.
    """

    kind = "json_sidecar"

    #: Geometry this reader implements. Anything else is refused rather than assumed — an unstated
    #: coordinate convention is a locator that means a different rectangle to every reader.
    ORIGIN = "top_left"
    BBOX_FORMAT = "xywh_pixels"

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self._index_path = self.root / str(self.contract.entry["entrypoint"])
        self._index: Optional[list[Mapping[str, tuple[str, int, int]]]] = None
        self._entities: Optional[tuple[DocEntity, ...]] = None
        trusted = self.contract.entry.get("trusted_fields") or ()
        self._trusted_fields = tuple(str(f) for f in trusted)

    def _load_index(self) -> list[Mapping[str, tuple[str, int, int]]]:
        if self._index is None:
            if not self._index_path.is_file():
                raise DocumentSourceError(f"{self._index_path}: the contract names this as the "
                                          f"entrypoint and it is not there")
            # R-005 finding 13: this used to read the index with `Path.read_text` directly, which
            # is the one thing `TextDropReader._load_index` gets right on the exact same job — the
            # index decides which sidecar files a READ enumerates, not merely how `describe()`
            # summarises the drop, so it is ledger-bound the same as any other structural read and
            # must be captured before it is parsed.
            artefact = self._capture(self._index_path, "text/csv")
            header, rows = csv_rows_with_spans(artefact.text)
            self._index = [{header[i]: r[i] for i in range(min(len(header), len(r)))}
                           for r in rows]
        return self._index

    def _sidecar_path(self, row: Mapping[str, tuple[str, int, int]]) -> Optional[Path]:
        cell = row.get("sidecar_path")
        if cell is None:
            return None
        base = self.root / str(self.contract.entry["entrypoint"]).rsplit("/", 1)[0]
        return _contained_path(base, cell[0])

    # --- schema ---
    def entities(self) -> tuple[DocEntity, ...]:
        if self._entities is not None:
            return self._entities
        rows = self._load_index()
        # R-005 finding 13: stopping at the FIRST existing sidecar made the advertised `photo`
        # schema only that one sidecar's own field set — any scalar field present in a LATER
        # sidecar but absent from the first became permanently unreachable, silently narrowing the
        # schema for the whole source. `TextDropReader._discover_labels` already gets this right
        # for its own drop (the union of every document's labels); this unions every sidecar's
        # scalar keys the same way. Real I/O in `entities()`, same as `_discover_labels` — named,
        # not hidden: for a schemaless source, "what columns exist" is a fact about the files, not
        # about whichever one happened to be read first.
        seen_types: dict[str, str] = {}
        for row in rows:
            path = self._sidecar_path(row)
            if path is None or not path.is_file():
                continue
            sample = json.loads(path.read_text(encoding="utf-8"))
            for k, v in sample.items():
                if k not in seen_types and not isinstance(v, (dict, list)):
                    seen_types[k] = _json_type(v)
        photo_fields = [
            DocField(name=k, type=t, contract_field=k) for k, t in sorted(seen_types.items())]
        block_fields = (
            DocField(name="image_id", type="text", contract_field="image_id"),
            DocField(name="page", type="integer", contract_field="ocr.page"),
            DocField(name="line", type="integer", contract_field="ocr.blocks[].line"),
            # The one field the estate names in `untrusted_fields`, spelled in the contract's own
            # notation so the trust lookup is a lookup and not a translation.
            DocField(name="text", type="text", contract_field="ocr.blocks[].text"),
            DocField(name="confidence", type="numeric",
                     contract_field="ocr.blocks[].confidence"),
        )
        self._entities = (
            DocEntity(name="photo", fields=tuple(photo_fields),
                      key=("image_id",) if any(f.name == "image_id" for f in photo_fields)
                      else ()),
            DocEntity(name="ocr_block", fields=block_fields, key=()),
        )
        return self._entities

    # --- data ---
    def read(self, entity: str, *, limit: int) -> list[DocRow]:
        declared = self.entity(entity)
        out: list[DocRow] = []
        for row in self._load_index():
            if len(out) >= limit:
                break
            path = self._sidecar_path(row)
            if path is None or not path.is_file():
                continue
            artefact = self._capture(path, "application/json")
            document = json.loads(artefact.text)
            if entity == "photo":
                out.append(self._photo_row(artefact, document, declared))
            else:
                for block_row in self._block_rows(artefact, document, declared):
                    if len(out) >= limit:
                        break
                    out.append(block_row)
        return out

    def _photo_row(self, artefact: Artefact, document: Mapping[str, Any],
                   declared: DocEntity) -> DocRow:
        names = [f.name for f in declared.fields]
        spans = json_scalar_spans(artefact.text, keys=names)
        cells: dict[str, DocCell] = {}
        sort_values: dict[str, Any] = {}
        for name in names:
            located = spans.get(name)
            if located is None:
                continue
            value, start, end = located
            cells[name] = _scalar_cell(name, value, artefact, start, end)
            sort_values[name] = value
        return DocRow(row_id=str(document.get("image_id") or artefact.id), cells=cells,
                      sort_values=sort_values)

    def _block_rows(self, artefact: Artefact, document: Mapping[str, Any],
                    declared: DocEntity) -> list[DocRow]:
        ocr = dict(document.get("ocr") or {})
        origin = str(ocr.get("coordinate_origin") or "")
        if origin != self.ORIGIN:
            raise DocumentSourceError(
                f"{artefact.live_path.name}: coordinate_origin is {origin!r} and this reader "
                f"implements {self.ORIGIN!r}. Refusing rather than assuming: an unstated origin "
                f"is a bbox that means a different rectangle to every reader")
        # Every field below is a LOCATOR component — `page`/`image_id` end up on every block's
        # `ocr_bbox` pointer, and `width`/`height` are the only thing that lets the geometry be
        # checked at all. `int(x or 1)`, `str(x or artefact.id)` and `float(x or 0)` each INVENT a
        # value the sidecar never stated whenever the real one is missing, null, or falsy (`page: 0`
        # is a real, wrong value that "or 1" would silently keep as if it were fine) — a locator
        # with no source is exactly what this Core exists not to emit (R-005 finding 5). So each is
        # required to be genuinely declared, or the whole sidecar refuses.
        raw_page = ocr.get("page")
        if not isinstance(raw_page, int) or isinstance(raw_page, bool) or raw_page < 1:
            raise DocumentSourceError(
                f"{artefact.live_path.name}: ocr.page is {raw_page!r}, not a positive integer. "
                f"Every OCR pass declares the page it recognised; inventing page 1 for a sidecar "
                f"that does not say would put a locator value with no source on every block")
        page = raw_page

        raw_image_id = document.get("image_id")
        if not isinstance(raw_image_id, str) or not raw_image_id:
            raise DocumentSourceError(
                f"{artefact.live_path.name}: no `image_id` declared. Substituting the sidecar's "
                f"own filename would mint a media identity the source never stated")
        image_id = raw_image_id

        image = dict(document.get("image") or {})
        raw_width, raw_height = image.get("width"), image.get("height")
        if (isinstance(raw_width, bool) or isinstance(raw_height, bool)
                or not isinstance(raw_width, (int, float))
                or not isinstance(raw_height, (int, float))
                or raw_width <= 0 or raw_height <= 0):
            raise DocumentSourceError(
                f"{artefact.live_path.name}: image.width/height are {raw_width!r}/{raw_height!r}, "
                f"not positive numbers. Without them a bbox cannot be checked against the page it "
                f"claims to be on, and silently skipping the check — rather than refusing — is "
                f"exactly how an out-of-bounds block gets through unnoticed")
        width, height = float(raw_width), float(raw_height)
        image_span = json_scalar_spans(artefact.text, keys=("image_id", "page"))
        rows: list[DocRow] = []
        text_pos = 0
        for index, block in enumerate(ocr.get("blocks") or ()):
            fmt = str(block.get("bbox_format") or "")
            if fmt != self.BBOX_FORMAT:
                raise DocumentSourceError(
                    f"{artefact.live_path.name} block {index}: bbox_format is {fmt!r} and this "
                    f"reader implements {self.BBOX_FORMAT!r}")
            x, y, w, h = (float(v) for v in block["bbox"])

            # `str(block.get("text") or "")` FABRICATED a value exactly the way the confidence
            # coercion below used to: a missing or `null` `text` — genuinely unrecoverable, per the
            # estate's own rule "the correct output for those is `unresolved`, not a repaired
            # guess" — became the empty string, indistinguishable from a block that genuinely
            # recognised nothing (which IS a value: `''` is a value, not an absence, invariant 4).
            # A non-string truthy `text` (a stray `false`/number in a malformed sidecar) was also
            # silently coerced with no registered derivation. Both are refused instead (R-005
            # finding 4): `text` must be the string the sidecar actually declares.
            raw_text = block.get("text")
            if not isinstance(raw_text, str):
                raise DocumentSourceError(
                    f"{artefact.live_path.name} block {index}: `text` is {raw_text!r}, not a "
                    f"string. Every block in `ocr.blocks` is a recognised line and carries the "
                    f"engine's own text — which may legitimately BE the empty string; a missing "
                    f"or non-string `text` is a malformed sidecar, and str(None) or str(False) "
                    f"here would fabricate a value with no source")
            text = raw_text

            # `float(block.get("confidence") or 0.0)` FABRICATED a confidence, and did it twice
            # over: a null (the producer's way of saying "this line came from a PDF text layer, no
            # recognition ran, there is no confidence to report") became 0.0, and a genuinely
            # measured 0.0 became indistinguishable from an absent one — because `0.0 or 0.0` is
            # `0.0` either way.
            #
            # An invented `ocr_conf` is a value with no source, in the pointer field whose entire
            # job is to say how much the recogniser trusted itself. `docling_extract` is careful
            # never to emit one; consuming its output by inventing the number throws that away at
            # the last step. Refuse instead: a block in `ocr.blocks` is by construction a
            # recognised line, so a missing confidence there is a malformed sidecar, not a
            # text-layer line. Text-layer lines live in `text_layer.blocks` and are read elsewhere.
            raw_conf = block.get("confidence")
            if raw_conf is None:
                raise DocumentSourceError(
                    f"{artefact.live_path.name} block {index}: no confidence. Every block in "
                    f"`ocr.blocks` is a recognised line and carries the engine's own confidence; "
                    f"a null there is a malformed sidecar. A text-layer line — which genuinely "
                    f"has no confidence because no recognition step ran — belongs in "
                    f"`text_layer.blocks`, and inventing 0.0 for it would put a number with no "
                    f"source into `ocr_conf`.")
            conf = float(raw_conf)
            cells: dict[str, DocCell] = {}
            for shared in ("image_id", "page"):
                located = image_span.get(shared)
                if located is not None:
                    cells[shared] = _scalar_cell(shared, located[0], artefact, located[1],
                                                 located[2])
            block_span = json_scalar_spans(
                artefact.text, keys=("line", "confidence"),
                within=_block_bounds(artefact.text, text, text_pos))
            for name in ("line", "confidence"):
                found = block_span.get(name)
                if found is not None:
                    cells[name] = _scalar_cell(name, found[0], artefact, found[1], found[2])
            if x < 0 or y < 0 or x + w > width or y + h > height:
                # The one geometric fact the sidecar CAN corroborate, and it does not: `text` is
                # PRESENT in the source — this is not "the page does not say" — only its bbox
                # locator is broken. A prior version emitted the row without its `text` cell,
                # which `_emit_row` then turns into `Absent`: exactly the fabrication invariant 4
                # forbids in the other direction, misrepresenting "present but unlocatable" as "not
                # in the source". Invariant 2 is unambiguous that a value that cannot be cited
                # correctly is refused, not silently downgraded to a fact about the page it is not
                # (R-005 finding 6).
                raise DocumentSourceError(
                    f"{artefact.live_path.name} block {index}: bbox [{x},{y},{x + w},{y + h}] "
                    f"lies outside the declared {width}x{height} image. The block's text is "
                    f"present in the sidecar, so emitting it as Absent would misrepresent 'the "
                    f"page does not say' as the fact; emitting it anyway would cite a rectangle "
                    f"outside the page it names. Refusing rather than doing either")
            cells["text"] = DocCell(
                column="text", raw=text, artefact=artefact,
                bbox=Bbox(media_id=image_id, page=page, x0=x, y0=y, x1=x + w, y1=y + h,
                          ocr_conf=conf))
            rows.append(DocRow(row_id=f"{image_id}#{index}", cells=cells,
                               sort_values={"line": block.get("line"), "text": text}))
            text_pos = artefact.text.find(json.dumps(text), text_pos) + 1
        return rows

    def estimated_rows(self, entity: str) -> int:
        rows = self._load_index()
        if entity == "photo":
            return len(rows)
        total = 0
        for row in rows:
            cell = row.get("ocr_block_count")
            total += int(cell[0]) if cell and str(cell[0]).isdigit() else 0
        return total

    def live_path_of(self, artefact_id: str) -> Optional[Path]:
        for row in self._load_index():
            path = self._sidecar_path(row)
            if path is not None and path.name.rsplit(".", 1)[0] == artefact_id.rsplit(".", 1)[0]:
                return path if path.is_file() else None
        return None

    def operations(self, entity: str) -> tuple[str, ...]:
        return (
            f"read {self._index_path.name} for the sidecar set",
            "capture each sidecar (application/json) into the Capture Store",
            f"check coordinate_origin == {self.ORIGIN!r} and bbox_format == {self.BBOX_FORMAT!r}",
            "emit ocr_bbox pointers (page, x0/y0/x1/y1, ocr_conf) bound to the capture digest"
            if entity == "ocr_block" else
            "emit doc_span pointers into the captured sidecar JSON",
        )


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "numeric"
    return "text"


def _scalar_cell(name: str, value: Any, artefact: Artefact, start: int, end: int) -> DocCell:
    """One JSON member as a cell: verbatim where the bytes ARE the value, `derived` where not.

    A plain string cites its **inner** span, so the pointer addresses the value rather than the
    value plus its quotes. Everything else — numbers, booleans, `null`, and any string carrying an
    escape — is a decode, and a decode is a transformation (§4a).
    """
    literal = artefact.text[start:end]
    if isinstance(value, str) and literal == f'"{value}"':
        return DocCell(column=name, raw=value, artefact=artefact,
                       span=Span(artefact.id, start + 1, end - 1))
    return DocCell(column=name, raw=literal, artefact=artefact,
                   span=Span(artefact.id, start, end), derivation="json_scalar@v1()")


def _block_bounds(text: str, block_text: str, start: int) -> tuple[int, int]:
    """The character range of one OCR block object inside the sidecar JSON.

    Located from the block's own text literal so that `line` and `confidence` are read from the
    block they belong to. A wider search would let one block's confidence vouch for another's — so
    when the anchor cannot be found, this REFUSES rather than falling back to "the rest of the
    document" (R-005 finding 11): the old fallback was exactly that wide search, silently letting
    any later block's `line`/`confidence` be attributed to this one.
    """
    anchor = text.find(json.dumps(block_text), start)
    if anchor < 0:
        raise DocumentSourceError(
            "an OCR block's own text literal could not be found in the captured sidecar from its "
            "expected position onward; refusing to search the rest of the document for "
            "`line`/`confidence`, which would let one block's fields vouch for another's")
    lo = text.rfind("{", 0, anchor)
    hi = text.find("}", anchor)
    return (lo if lo >= 0 else start, hi + 1 if hi >= 0 else len(text))


#: contract `kind` -> reader. The ONLY thing that decides how bytes are parsed. A source whose
#: `kind` is not here is refused at construction, because the alternative is a reader chosen by
#: guessing, and a wrong guess produces well-formed citations to the wrong bytes.
READERS: Mapping[str, type] = {
    TextDropReader.kind: TextDropReader,
    FixedWidthReader.kind: FixedWidthReader,
    SidecarReader.kind: SidecarReader,
}


# =======================================================================================
# the Core
# =======================================================================================


class DocumentCore:
    """Deterministic. No LLM. Owns the filesystem handle and the Capture Store.

    **It cannot be constructed without a `PolicyEngine` or a `CaptureStore`, and it obtains its
    own admission inside every verb.** The first is the in-process half of Appendix A's
    Policy→Core protocol; the second is D6's capture rule made structural — there is no
    constructor argument through which a caller could ask for an uncaptured T3 read.
    """

    pinning_tier: str = "T3"
    capabilities: frozenset[str] = CAPABILITIES

    def __init__(self, *, source_id: str, root: Union[str, Path], policy: PolicyEngine,
                 capture_store: CaptureStore, sources_yaml: Optional[Path] = None,
                 contract: Optional[SourceContract] = None,
                 now: Optional[Callable[[], datetime]] = None) -> None:
        if policy is None:                                       # pragma: no cover - typing
            raise PolicyNotApplied(
                "a Core cannot be constructed without a PolicyEngine: the documented path to a "
                "source has to run through Policy, and an optional policy is no policy")
        if capture_store is None:
            raise DocumentSourceError(
                "a T3 Core cannot be constructed without a CaptureStore. There is no past to "
                "return to here, so a pointer that does not resolve against a capture cites a "
                "file anyone can edit (spine D6)")
        if contract is None:
            if sources_yaml is None:
                raise ValueError("DocumentCore needs either `contract` or `sources_yaml`")
            contract = load_source_contract(source_id, sources_yaml=sources_yaml)
        declared_tier = str(contract.entry.get("pinning_tier") or "T3").upper()
        if declared_tier != "T3":
            raise DocumentSourceError(
                f"{source_id!r} declares tier {declared_tier}; this Core implements T3 only. A "
                f"source that can pin something needs a Core that uses what it can pin, and "
                f"reading it through the tier that promises least would silently discard that")
        kind = str(contract.entry.get("kind") or "")
        reader_cls = READERS.get(kind)
        if reader_cls is None:
            raise DocumentSourceError(
                f"{source_id!r} declares kind {kind!r}; this Core implements "
                f"{sorted(READERS)}. The reader is chosen by the CONTRACT, never by sniffing a "
                f"filename, so an undeclared kind is refused rather than guessed at")
        self.source_id = source_id
        self.root = Path(root)
        self._contract = contract
        self._policy = policy
        self._captures = capture_store
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._artefacts: dict[str, Artefact] = {}
        self._described: Optional[SourceDescription] = None
        self._reader: _Reader = reader_cls(source_id=source_id, root=self.root,
                                           contract=contract, capture=self._capture_artefact)
        verbs = tuple(str(c) for c in (contract.entry.get("capabilities") or ()))
        self._verbs = verbs
        #: Whether the CONTRACT declares that a live re-read is worth doing. `case_files` does and
        #: says why — "it is how a re-scan is DETECTED". `legacy_records` does not, because record
        #: offsets are not stable across nightly cuts, so a byte-range pointer into last night's
        #: cut addresses different data today.
        self._verify_live_bytes = "verify_live_bytes" in verbs

    # --- capture ---------------------------------------------------------------------
    def _capture_artefact(self, path: Path, media_type: str) -> Artefact:
        """Capture BEFORE parsing. The bytes cited are the bytes stored.

        Not "capture if the caller asked", and not "capture on the way out". D6's rule is that
        anything ledger-bound is captured and T3 is always ledger-bound, so this runs on the read
        path with no flag in front of it. Idempotent by content: re-reading an unchanged file
        stores no second artefact — `CaptureStore.put` is itself idempotent by digest.

        **Always reads the live file.** A prior version cached the resulting `Artefact` by path for
        the lifetime of this `DocumentCore`, so that a SECOND `snapshot()`/`resolve()` on the same
        Core — after the file underneath had changed — silently returned the FIRST read's bytes
        while `_snapshot_of` stamped them with a fresh `self._now()`. That is a "now" read
        substituting an earlier state, exactly the failure `CannotPinPast` exists to make loud
        elsewhere (R-005 finding 2). `CaptureStore.put()` already deduplicates by content; caching
        by live pathname on top of that was the unsafe part, not the deduplication.
        `self._artefacts` is kept only as a debugging aid (last artefact seen per path) — nothing
        here ever returns a value FROM it.
        """
        content = path.read_bytes()
        capture = self._captures.put(
            content=content, media_type=media_type, retention="ledger_bound",
            source_id=self.source_id, note=f"{self.source_id}:{path.name}")
        artefact = Artefact(id=path.name, capture=capture,
                            text=content.decode("utf-8", errors="strict"), live_path=path)
        self._artefacts[str(path)] = artefact
        return artefact

    # --- verb 1: describe --------------------------------------------------------------
    def describe(self) -> SourceDescription:
        """Schema, capability matrix, retention reality, egress class. **No data.**

        It does read files — a document drop's field set is the union of what its documents say,
        and there is nowhere else for that to come from — and reading an index captures it, because
        the capture rule has no exception for metadata. What it does not do is emit a value: the
        labels are schema, the text behind them is not, and nothing here leaves with a citation.
        """
        if self._described is not None:
            return self._described
        entities = tuple(
            EntityDescription(
                name=e.name,
                fields=tuple(FieldDescription(name=f.name, type=f.type,
                                              trust=self._trust_of(e.name, f),
                                              sensitive=self._is_sensitive(e.name, f))
                             for f in e.fields),
                key=e.key)
            for e in self._reader.entities())
        self._described = self._contract.describe(capabilities=self.capabilities,
                                                  entities=entities)
        return self._described

    def _contract_path(self, entity: str, field: DocField) -> tuple[str, str]:
        """The (entity, column) pair the SOURCE CONTRACT uses for this reader column.

        The contract writes `inspection_photos.ocr.blocks[].text`; this reader calls the column
        `ocr_block.text`. Splitting the declared path on its last dot recovers exactly the pair
        `SourceContract.trust_of` will reassemble, so the lookup matches the contract's own
        spelling rather than the reader's.
        """
        declared = field.contract_field or field.name
        head, _, tail = declared.rpartition(".")
        return (head or entity, tail)

    def _trust_of(self, entity: str, field: DocField) -> Trust:
        """Stamped from the contract. A lookup in three declared places, never an inference.

        1. `untrusted_fields` — the contract naming this field, or `"*"` for the whole source;
        2. `trusted_fields` — the contract naming it as agency-produced (the sidecar's
           `inspection_id`, `photo_batch_ref`, `captured_by`, `captured_at`);
        3. otherwise the source-level `trust_class`.

        Step 3 is why the sidecar's own `application_id` comes out untrusted, which is what the
        contract intends: it calls it "a convenience copy to be reconciled against that path,
        never trusted alone". There is no fallback to TRUSTED for a field nobody classified — the
        source-level class is a declaration, and using it is not a guess.
        """
        mapped_entity, column = self._contract_path(entity, field)
        if self._contract.trust_of(mapped_entity, column) is Trust.UNTRUSTED:
            return Trust.UNTRUSTED
        declared_trusted = tuple(str(f) for f in (self._contract.entry.get("trusted_fields") or ()))
        if declared_trusted:
            if column in declared_trusted or f"{mapped_entity}.{column}" in declared_trusted:
                return Trust.TRUSTED
            return (Trust.UNTRUSTED
                    if str(self._contract.entry.get("trust_class") or "").lower() == "untrusted"
                    else Trust.TRUSTED)
        return (Trust.UNTRUSTED
                if str(self._contract.entry.get("trust_class") or "").lower() == "untrusted"
                else Trust.TRUSTED)

    def _is_sensitive(self, entity: str, field: DocField) -> bool:
        mapped_entity, column = self._contract_path(entity, field)
        return self._contract.is_sensitive(mapped_entity, column)

    # --- verb 2: snapshot --------------------------------------------------------------
    def snapshot(self, req: CapabilityRequest) -> SnapshotIdentity:
        """Pin a point in time — which, at T3, means pinning **now** or refusing.

        There is deliberately no branch that substitutes `now()` for a requested past instant. A
        caller that believes it received the past and did not is the failure the whole tier system
        exists to prevent, and it is silent: the rows look exactly like rows.
        """
        # R-005 finding 10: `resolve()` re-verifies its compiled decision table with `.check()`
        # before using it; this verb fetched the same `Admission` and never verified it. `.check()`
        # is cheap and every verb that holds an `Admission` should hold the same guarantee about
        # it, not just the one whose caller happened to read the source code closely enough to see
        # `resolve()` do it.
        admission = self._admit(req).check()
        self._check_as_of(req)
        rows = self._reader.read(req.projection.entity, limit=req.limit)
        return self._snapshot_of(req, rows, admission)

    def _check_as_of(self, req: CapabilityRequest) -> None:
        if req.as_of is None:
            return
        why = str(self._contract.entry.get("tier_justification")
                  or "an unversioned drop with no version field, no tombstone and no cursor")
        raise CannotPinPast(
            f"{self.source_id!r} is T3: {why} There is no readable snapshot of any instant but "
            f"this one, and {req.as_of.isoformat()} is not it. To cite a past state, resolve the "
            f"pointer against the Capture Store — which is where every pointer this Core mints "
            f"already resolves.")

    def _snapshot_of(self, req: CapabilityRequest, rows: Sequence[DocRow],
                     admission: Admission) -> SnapshotIdentity:
        fields = req.projection.fields
        raw = [{c: cell.raw for c, cell in row.cells.items()} for row in rows]
        return SnapshotIdentity(
            source_id=self.source_id,
            as_of=self._now(),
            content_hash=rows_digest(raw, fields),
            schema_hash=projection_schema_hash(
                req.projection.entity, fields,
                self._reader.entity(req.projection.entity).types),
            query_hash=req.read_hash,           # D2 — the pointer identity lives HERE
        )

    # --- verb 3: plan ------------------------------------------------------------------
    def plan(self, req: CapabilityRequest) -> ReadPlan:
        """Cost/row estimate plus the exact operations. Deterministic; metadata I/O only."""
        admission = self._admit(req).check()          # R-005 finding 10 — see `snapshot()`
        entity = req.projection.entity
        self._reader.entity(entity)
        operations = list(self._reader.operations(entity))
        withheld = [c.column for c in admission.decisions.columns if c.withheld]
        if withheld:
            operations.append(f"-- withheld by policy, not emitted: {', '.join(withheld)}")
        estimated = min(self._reader.estimated_rows(entity), req.limit)
        return ReadPlan(
            read_hash=req.read_hash, source_id=self.source_id, entity=entity,
            operations=tuple(operations), estimated_rows=estimated,
            estimated_cost=Cost(rows=estimated),
            # Always true here, and it is not a flag a deployment may turn off: T3 has no past to
            # return to, so a citation that does not resolve against a capture cites nothing that
            # can be checked.
            requires_capture=True,
            completeness_caveat=self._completeness_caveat())

    def _completeness_caveat(self) -> str:
        """What this source's own cursor honestly cannot promise. Declared, never inferred."""
        cursor = dict(self._contract.entry.get("cursor") or {})
        rejected = cursor.get("rejected_candidate")
        why = cursor.get("rejected_reason")
        base = (f"{self.source_id} has no change cursor at all, so an incremental read is not "
                f"expressible and a full read cannot be shown to have missed nothing")
        if rejected:
            base += f"; {rejected!r} was considered and rejected: {why}"
        deletes = str(self._contract.entry.get("deletes_detectable") or "none")
        if deletes == "none":
            base += (". Deletes are not detectable: an indexed file that is no longer there is "
                     "skipped, never reported as removed")
        return base

    # --- verb 4: resolve ---------------------------------------------------------------
    def resolve(self, req: CapabilityRequest) -> AdapterResult:
        """Execute. Returns `GroundedRows`: every cell carries a `SourcePointer`."""
        started = time.perf_counter()
        admission = self._admit(req).check()
        entity = req.projection.entity
        declared = self._reader.entity(entity)
        self._check_as_of(req)
        self._check_ordering(req, declared)

        # R-005 finding 12 (resolution half): a pointer is a LOCATOR, and re-resolving one must
        # not depend on where its row happens to fall in the reader's enumeration order. Capping
        # the raw read at `req.limit` BEFORE resolution-mode filtering meant a caller asking to
        # resolve one specific, previously-emitted pointer with a small `limit` could get back
        # nothing solely because that row was not among the first `limit` the reader happened to
        # enumerate — a valid citation failing to re-resolve for a reason that has nothing to do
        # with whether it is still good. Resolution mode reads the reader's full enumeration; the
        # OUTPUT is still exactly the rows the cited pointers name, never more. Not bounded by
        # `estimated_rows()`: that estimate is itself sourced from the estate's own metadata (a
        # declared `ocr_block_count`, a shard's file size) and undercounting it would silently
        # reintroduce the same bug it is meant to fix.
        rows = self._reader.read(entity, limit=_UNBOUNDED_READ if req.resolve else req.limit)
        if req.resolve:
            # RESOLUTION MODE: re-fetch exactly the artefacts the given pointers address, and
            # nothing else. An untrusted pointer is perfectly legal here — retrieval is not
            # action (§4, and acceptance test §11.5).
            #
            # Matched on `row_id` — the LOCATOR — wherever the pointers carry one. Falling back to
            # the artefact id would be far too wide for a document drop, where every structural
            # cell in the whole source cites the same captured `INDEX.csv`: one pointer would
            # re-resolve to every row in the estate.
            wanted = {p.row_id for p in req.resolve if p.row_id is not None}
            if wanted:
                rows = [r for r in rows if r.row_id in wanted]
            else:
                artefacts = {p.document_id or p.media_id for p in req.resolve}
                rows = [r for r in rows
                        if any(c.artefact.id in artefacts for c in r.cells.values())]
        rows = self._ordered(rows, req.order_by)

        snap = self._snapshot_of(req, rows, admission)
        cells: list[tuple[Cell, ...]] = []
        withheld_rows = 0
        for ordinal, row in enumerate(rows):
            denial = admission.decisions.row_verdict(
                dict(row.sort_values), attributes=admission.attribute_map, subquery_sets={})
            if denial is not None:
                withheld_rows += 1
                continue
            cells.append(self._emit_row(req, row, ordinal, snap, admission))

        warnings = list(admission.notes)
        if withheld_rows:
            warnings.append(
                f"{withheld_rows} row(s) withheld by a row-level rule; this result is scoped, not "
                f"complete")
        warnings.append(self._completeness_caveat())
        result = GroundedRows(columns=req.projection.fields, rows=tuple(cells), snapshot=snap)
        return AdapterResult(
            read_hash=req.read_hash, rows=result,
            # Egress is about bytes LEAVING the boundary, so a withheld or absent cell contributes
            # nothing: policy stopped it before it left, and counting it would make a denial look
            # like a disclosure on the budget.
            cost=Cost(rows=len(cells), wall_ms=int((time.perf_counter() - started) * 1000),
                      bytes_egressed=sum(len(str(c.value)) for row in cells for c in row
                                         if isinstance(c, GroundedValue))),
            warnings=tuple(warnings))

    def _check_ordering(self, req: CapabilityRequest, declared: DocEntity) -> None:
        """The projection's declared key must be the entity's, and nothing else may claim to be.

        Where the entity has no key — `legacy_master`, re-cut nightly with blank keys and orphan
        records — a projection that declares one is refused rather than humoured: it would produce
        citations addressing a row identity the source does not have.
        """
        if tuple(req.projection.key) != tuple(declared.key):
            raise UnorderedPointeredRead(
                f"the projection declares key {tuple(req.projection.key) or '()'} and "
                f"{declared.name}'s declared key is {declared.key or '()'}. A pointer cites the "
                f"locator, and a request naming a key the source does not have would produce "
                f"citations that address nothing")
        for term in req.order_by:
            name = term.lstrip("-")
            if declared.field(name) is None:
                raise UnorderedPointeredRead(
                    f"order_by names {name!r}, which {declared.name} does not have")

    @staticmethod
    def _ordered(rows: Sequence[DocRow], order_by: Sequence[str]) -> list[DocRow]:
        """Sort by the requested terms, with the LOCATOR as the final tiebreak.

        The tiebreak is what makes the order total. `row_ordinal` is only ever a hint here — the
        identity is the capture digest plus the offsets — but an ordering with ties still makes a
        result set that differs between two identical reads, and a digest over it would then
        report drift that did not happen.
        """
        out = sorted(rows, key=lambda r: r.row_id)
        for term in reversed(list(order_by)):
            desc, name = term.startswith("-"), term.lstrip("-")
            out = sorted(out, key=lambda r, n=name: str(r.sort_values.get(n, "")), reverse=desc)
        return out

    def _row_identity(self, row: DocRow) -> str:
        """A document row's identity is its LOCATOR, never its ordinal.

        An ordinal over a directory listing moves when a file is added, and an ordinal over a
        nightly re-cut moves when a record is inserted. The capture digest plus the offsets do
        not: they address bytes that, by content addressing, cannot become different bytes.
        """
        return row.row_id

    # --- emission ---------------------------------------------------------------------
    def _emit_row(self, req: CapabilityRequest, row: DocRow, ordinal: int,
                  snap: SnapshotIdentity, admission: Admission) -> tuple[Cell, ...]:
        entity = req.projection.entity
        out: list[Cell] = []
        for column in req.projection.fields:
            decision = admission.decisions.decide(entity, column, dict(row.sort_values),
                                                  req.principal)
            if decision.withheld:
                # D4 — a denial, NOT an assertion that a value exists behind it. It carries no
                # pointer, because withholding is not a citation.
                out.append(Withheld(record=decision.to_record(
                    field_path=f"{entity}.{column}", principal=req.principal,
                    policy_version=admission.decisions.policy_version)))
                continue
            cell = row.cells.get(column)
            if cell is None:
                # "Not in the source" — the label was looked for and the page did not carry it,
                # or OCR damaged it past recognition. The estate's own rule: unresolved, never a
                # repaired guess.
                out.append(Absent(field_path=f"{entity}.{column}"))
                continue
            out.append(self._emit_cell(req, entity, cell, self._row_identity(row), ordinal, snap))
        return tuple(out)

    def _emit_cell(self, req: CapabilityRequest, entity: str, cell: DocCell, row_id: str,
                   ordinal: int, snap: SnapshotIdentity) -> GroundedValue:
        field = self._reader.entity(entity).field(cell.column)
        assert field is not None                                  # narrowed by the projection
        trust = self._trust_of(entity, field)
        per_cell = SnapshotIdentity(
            source_id=self.source_id, as_of=snap.as_of, content_hash=cell_digest(cell.raw),
            schema_hash=snap.schema_hash, query_hash=snap.query_hash)
        raw_ptr = self._locator_pointer(cell, per_cell, req.read_hash, row_id, ordinal, trust)

        if cell.derivation is None:
            value = GroundedValue(value=cell.raw, pointer=raw_ptr, confidence=1.0)
            self._assert_grounded_in_capture(value, cell)
            return value

        # A transformation, so a `derived` pointer over a REGISTERED, REPLAYABLE rule — and the
        # emitted value is what the replay produces, not what this module computed beside it.
        call = parse_derivation(cell.derivation)
        produced = replay_derivation(call, [cell.raw])
        derived_ptr = SourcePointer(
            source_id=self.source_id, kind="derived", derivation=cell.derivation,
            derived_from=[raw_ptr],
            trust=trust,                      # LUB — a decode does not launder trust
        )
        assert_derived_from(derived_ptr, raw_ptr)
        value = GroundedValue(value=produced, pointer=derived_ptr, confidence=1.0)
        self._assert_grounded_in_capture(value, cell)
        return value

    def _locator_pointer(self, cell: DocCell, per_cell: SnapshotIdentity, read_hash: str,
                         row_id: str, ordinal: int, trust: Trust) -> SourcePointer:
        digest = cell.artefact.capture.qualified_digest
        common = dict(
            source_id=self.source_id, snapshot=per_cell, query_hash=read_hash,
            row_id=row_id, row_ordinal=ordinal, column=cell.column,
            # THE CAPTURE IS THE CITED ARTEFACT. Without its digest this pointer names a path,
            # and a path is a thing anyone with write access can change under it.
            capture_digest=digest,
            trust=trust,
        )
        if cell.span is not None:
            return SourcePointer(
                kind="doc_span", document_id=cell.span.document_id,
                char_start=cell.span.char_start, char_end=cell.span.char_end, **common)
        if cell.bbox is not None:
            b = cell.bbox
            return SourcePointer(
                kind="ocr_bbox", media_id=b.media_id, page=b.page, x0=b.x0, y0=b.y0, x1=b.x1,
                y1=b.y1, ocr_conf=b.ocr_conf, **common)
        raise UngroundedValue(                                   # pragma: no cover - defensive
            f"cell {cell.column!r} carries no locator: a value with nowhere to point is exactly "
            f"what this Core exists not to emit")

    def _assert_grounded_in_capture(self, value: GroundedValue, cell: DocCell) -> None:
        """The wall, run at emission against the CAPTURED bytes the pointer names.

        `grounding.assert_grounded` is honest that it cannot know the bytes it was handed came
        from the cited location. Here it can: the content address on the pointer is checked
        against the capture the bytes came out of first, and only then is the whole artefact
        handed to the wall so the cited offsets are sliced out of it (§4a). Passing the pre-sliced
        value would test that a string contains itself.
        """
        pointer = value.pointer
        expected = cell.artefact.capture.qualified_digest
        carried = pointer.capture_digest or (
            (pointer.derived_from or [None])[0].capture_digest if pointer.derived_from else None)
        if carried != expected:
            raise UngroundedValue(
                f"cell {cell.column!r} cites capture {carried!r} and was parsed out of "
                f"{expected!r}. The two must be the same object or the grounding check is being "
                f"run against bytes the pointer does not name")
        if pointer.kind == "derived":
            assert_grounded(value, [cell.raw])
            return
        if cell.bbox is not None:
            assert_grounded(value, cell.raw)
            return
        assert_grounded(value, cell.artefact.text)

    # --- verb 5: verify ----------------------------------------------------------------
    def verify(self, pointer: SourcePointer, *, principal: Any = None) -> Verdict:
        """Is this pointer still good? Returns the LATTICE, never a boolean (spine D6)."""
        description = self.describe()
        verdict, _note = self._verify(pointer, description, principal)
        return assert_verdict_advertised(verdict, description)

    def verify_with_note(self, pointer: SourcePointer,
                         *, principal: Any = None) -> tuple[Verdict, Optional[str]]:
        """`verify` plus the operator-language reason — and, for an `ocr_bbox`, the caveat.

        The estate is explicit that an `ocr_bbox` pointer here reaches `VERIFIED_AS_OF_CAPTURE`
        **as to the sidecar** and `UNVERIFIABLE` **as to the image**, and that *both* must be
        reported: no images ship, so the sidecar corroborates only itself, and reporting only the
        first would let a reader believe the photograph had been checked.
        """
        description = self.describe()
        verdict, note = self._verify(pointer, description, principal)
        return assert_verdict_advertised(verdict, description), note

    def resolve_pointer(self, pointer: SourcePointer,
                        *, principal: Any = None) -> PointerResolution:
        """Re-resolve ONE pointer into a `PointerResolution` — never a `ResolutionResult`.

        The two names are kept lexically distant on purpose: `ResolutionResult` means IDENTITY
        resolution and carries `requires_human_selection: Literal[True]`. Nothing in this method
        confirms anyone's identity, and nothing in it ever should.
        """
        verdict, note = self.verify_with_note(pointer, principal=principal)
        cell: Optional[Cell] = None
        if verdict.is_positive:
            value = self._value_from_capture(pointer)
            cell = GroundedValue(value=value, pointer=pointer, confidence=1.0)
        return PointerResolution(pointer=pointer, verdict=verdict, resolved_at=self._now(),
                                 cell=cell, note=note)

    def _verify(self, pointer: SourcePointer, description: SourceDescription,
                principal: Any) -> tuple[Verdict, Optional[str]]:
        if pointer.source_id != self.source_id:
            return Verdict.UNVERIFIABLE, (
                f"pointer cites source {pointer.source_id!r}; this adapter serves "
                f"{self.source_id!r} and has no handle to the other")

        if pointer.kind == "derived":
            parents = list(pointer.derived_from or ())
            if not parents:
                return Verdict.UNVERIFIABLE, (
                    "a derived pointer with no cited parents cannot be re-resolved")
            results = [self._verify(p, description, principal) for p in parents]
            joined = combine_verdicts(v for v, _n in results)
            # The WORST parent's own note travels with the verdict. A derived cell that reports
            # only "the worst of 1 parent is vanished" tells an operator the verdict and withholds
            # the fact they have to act on, which is the same as not reporting it.
            worst = next(n for v, n in results if v is joined)
            note = _join(
                f"derived value: the worst verdict over {len(parents)} cited parent(s) is "
                f"{joined.value}.", worst)
            if not joined.is_positive:
                return joined, note
            # R-005 finding 7: "provenance structurally present" is not "derivation verified". A
            # positive verdict over the PARENTS says nothing about whether the `derivation` this
            # pointer names is even a real, registered, replayable rule — an unknown, malformed or
            # wrong-arity `derivation` string used to sail through here unread, because this branch
            # never touched `pointer.derivation` at all. Replay it now, over the parents' actual
            # captured values, and refuse to advertise a verdict this adapter cannot compute.
            try:
                call = parse_derivation(pointer.derivation or "")
                parent_values = [self._value_from_capture(p) for p in parents]
                replay_derivation(call, parent_values)
            except (UngroundedValue, CaptureError) as exc:
                return Verdict.UNVERIFIABLE, (
                    f"the derivation this pointer names could not be replayed over its cited "
                    f"parent(s): {exc}. A verdict cannot be advertised for a value this adapter "
                    f"cannot compute")
            return joined, note

        if pointer.kind not in ("doc_span", "ocr_bbox"):
            return Verdict.UNVERIFIABLE, (
                f"this Core re-resolves captured document locators; pointer kind "
                f"{pointer.kind!r} is not one")

        if principal is not None:
            try:
                self._policy.binding_for(principal)
            except AdapterError:
                return Verdict.FORBIDDEN, (
                    "the principal is no longer in the directory, so the read that produced this "
                    "citation would be refused now")

        if not pointer.capture_digest:
            # The whole reason `capture_digest` exists. Without it this pointer names a path, and
            # a path is a thing anyone with write access can change under it — so there is nothing
            # here that could be checked, and saying so is the honest answer.
            return Verdict.UNVERIFIABLE, (
                "the pointer carries no capture_digest, so it cites a file path rather than a set "
                "of bytes. At T3 the live path is not evidence: it can be re-scanned in place, "
                "and nobody could afterwards prove which bytes were parsed")

        try:
            content = self._captures.get(pointer.capture_digest)
        except CaptureShredded as exc:
            return Verdict.VANISHED, (
                f"the capture this pointer cites was destroyed under an erasure request "
                f"({exc.tombstone.get('authority')}): "
                f"{exc.tombstone.get('reason')}")
        except CaptureNotFound:
            return Verdict.VANISHED, (
                "the capture this pointer cites is not in the store, so the cited bytes cannot "
                "be produced")
        except CaptureError as exc:
            return Verdict.VANISHED, (
                f"the capture this pointer cites will not be served: {exc}")

        text = content.decode("utf-8", errors="strict")
        found, note = self._locate(pointer, text)
        if found is _MISSING:
            return Verdict.DRIFT_MATERIAL, note
        cited = pointer.snapshot.content_hash if pointer.snapshot else None
        if cited is None:
            return Verdict.UNVERIFIABLE, (
                "the pointer carries no cell digest, so a re-read has nothing to compare against")
        if cell_digest(found) != cited:
            return Verdict.DRIFT_MATERIAL, (
                "the cited offsets inside the cited capture no longer hold the value this "
                "citation vouches for")

        # The capture answers. Now the live path — consulted for existence, and for the one
        # question the CONTRACT says a live re-read is worth asking.
        artefact_id = pointer.document_id or pointer.media_id or ""
        live = self._reader.live_path_of(artefact_id)
        caveat = self._image_caveat(pointer)
        if live is None:
            return Verdict.VANISHED, _join(
                f"the capture still resolves, but {artefact_id!r} is no longer at the path it was "
                f"read from. NOT evidence of deletion — this source cannot detect deletes",
                caveat)
        if self._verify_live_bytes:
            if CaptureStore.digest_of(live.read_bytes()) == self._captures.parse_digest(
                    pointer.capture_digest):
                return Verdict.VERIFIED, caveat
            return Verdict.DRIFT_MATERIAL, _join(
                "the file at this path no longer digests to the captured bytes: the page has been "
                "re-scanned and the OCR pass this citation was made from is gone. The citation "
                "still resolves against the capture", caveat)
        return Verdict.VERIFIED_AS_OF_CAPTURE, _join(
            "matched the Capture Store entry. A live re-read is not attempted for this source: "
            f"{self._contract.entry.get('retention_note') or 'it is unversioned'}", caveat)

    def _image_caveat(self, pointer: SourcePointer) -> Optional[str]:
        if pointer.kind != "ocr_bbox":
            return None
        return ("UNVERIFIABLE as to the image: no image bytes ship for this source, so the "
                "sidecar corroborates only itself. That the text was ever in an image, and that "
                "it was at this rectangle, is not checkable here")

    def _locate(self, pointer: SourcePointer, text: str) -> tuple[Any, Optional[str]]:
        """Re-read the cited value out of the captured bytes, or say why it cannot be."""
        if pointer.kind == "doc_span":
            start, end = pointer.char_start or 0, pointer.char_end or 0
            if start < 0 or end > len(text) or end < start:
                return _MISSING, (
                    f"the cited range [{start}:{end}] does not fit the {len(text)}-character "
                    f"capture; the locator addresses nothing in the artefact it names")
            return text[start:end], None
        document = json.loads(text)
        for block in (dict(document.get("ocr") or {}).get("blocks") or ()):
            x, y, w, h = (float(v) for v in block["bbox"])
            if (x, y, x + w, y + h) == (pointer.x0, pointer.y0, pointer.x1, pointer.y1):
                found = block.get("text")
                if not isinstance(found, str):
                    # Mirrors emission's own rule (R-005 finding 4): `str(None or "")` here would
                    # re-derive a value that was never in the sidecar, and a re-read is exactly the
                    # place that fabrication would go undetected forever.
                    return _MISSING, (
                        "the OCR block at the cited rectangle has no string `text` in the "
                        "captured sidecar; nothing here can be re-read as the cited value")
                return found, None
        return _MISSING, ("no OCR block in the captured sidecar occupies the rectangle this "
                          "pointer cites")

    def _value_from_capture(self, pointer: SourcePointer) -> Any:
        """The value a positive verdict just established. Read back from the capture, not cached."""
        if pointer.kind == "derived":
            parent = list(pointer.derived_from or ())[0]
            call = parse_derivation(pointer.derivation or "")
            return replay_derivation(call, [self._value_from_capture(parent)])
        content = self._captures.get(str(pointer.capture_digest))
        found, _note = self._locate(pointer, content.decode("utf-8"))
        return None if found is _MISSING else found

    # --- internals --------------------------------------------------------------------
    def _admit(self, req: CapabilityRequest) -> Admission:
        """Every verb goes through Policy. There is no argument that bypasses this."""
        if req.source_id != self.source_id:
            raise PolicyNotApplied(
                f"request targets {req.source_id!r}; this adapter serves {self.source_id!r}")
        if req.capability not in self.capabilities:
            raise CapabilityNotAdvertised(
                f"{self.source_id!r} advertises {sorted(self.capabilities)}, not "
                f"{req.capability!r}")
        if req.capability_version is not None:
            raise CapabilityNotAdvertised(
                f"{self.source_id!r} advertises only structural verbs, whose meaning is fixed by "
                f"the canonicalisation scheme; it declares no named queries, so "
                f"capability_version={req.capability_version!r} names a revision of something "
                f"that does not exist")
        return self._policy.admit(req, description=self.describe())


class _Missing:
    def __repr__(self) -> str:                                   # pragma: no cover - display
        return "<the cited location holds nothing>"


_MISSING = _Missing()


def _join(*parts: Optional[str]) -> str:
    return " ".join(p for p in parts if p)

"""Build a grounded graph from a pack's data estate, **driven by the pack's own map**.

This is the generic half of the government deliverable: a builder that reads a declarative
`seed_map.yaml` beside a pack's `ontology.yaml` and executes it against that pack's fixtures. The
pack-specific entry point is `government_seed.py`, which is four lines long, because everything
that knows what the estate contains lives in the map — which is where a domain's vocabulary is
supposed to live (`00_SPINE.md` §4, and `test_no_domain_branching`).

## Deterministic pathway, not a lookup table

The rule this module is written to satisfy is the one that separates a compiler from a fixture:
**a deterministic path computes; a hardcode recalls.** The map declares a BINDING — "the column
named there becomes an attribute named here, grounded by a pointer at that cell" — and the builder
computes every node and every edge from the rows it actually reads. Nothing in the map names an
answer. Delete a row from a fixture and the graph is one node smaller; change a value and the
attribute changes with it; there is no table anywhere that turns an input into a prepared output.

The same property is what makes the result checkable: every value in the graph traces to a cell in
a file a person can open, because the pointer is built from the coordinates the reader used to get
it, not asserted alongside it.

## The vocabulary is closed to the pack, and the builder cannot widen it

`NodeKind` / `EdgeKind` construction raises for any kind the enabled packs do not declare, so a map
naming a kind that is not in the ontology fails at build time with the kind named. That is the
whole point of the deliverable: the graph is built from the PREDEFINED library, and something the
library has no kind for goes through `growth.py` — the proposal protocol — rather than being
coined here.

## What it refuses to do

*It never writes a determination.* Identity claims are `about`-shaped edges and nothing else. The
map cannot express an attested edge: `_build_edge` refuses a kind bound to an attested meta-kind
before it builds one, so no map, however written, can make this builder decide who somebody is. That is
`CLAUDE.md` constraint 1, enforced here rather than trusted to the map's author.

*It never invents a value.* A cell that is NULL contributes no attribute — the key lands in the
node's `unresolved` instead — and a template that names a NULL column produces no node and no
edge at all, counted in the report rather than silently dropped. There is no default, no
inference and no "reasonable guess" anywhere in the file.

*It never merges by similarity.* Nodes are keyed by identifiers computed from the source's own
columns. Two rows that look alike stay two nodes, which is the safe direction and the only one
available to a builder that has no human to ask.

## Readers

Four, because the estate has four shapes: `sql_insert` (a relational dump), `csv`, `json_pages`
(captured API pages), and `fixed_width` (a legacy record file described by a layout). Each yields
`(row_key, {column: value})` pairs plus the coordinates a pointer needs, and knows nothing about
what the rows mean.
"""
from __future__ import annotations

import csv as _csv
import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Sequence

import yaml

from databridge_core import SourcePointer, Trust

from .kinds import EdgeKind, NodeKind
from .merge import token_set_ratio, upsert_edge, upsert_node
from .meta import ATTESTED_META_KINDS
from .packs import PackError, Vocabulary, active_vocabulary, check_pack_name, packs_root
from .schema import GraphEdge, GraphNode, node_id
from .store import GraphStore, row_to_node

SEED_MAP_FILE = "seed_map.yaml"

#: Substituted into a `key` / `label` / endpoint template. Nothing else is interpolated: the
#: template language is deliberately one construct wide, because a map that can compute is a map
#: that can decide, and deciding belongs in the ontology and the protocol, not in a binding file.
_TEMPLATE = re.compile(r"\{([a-zA-Z0-9_.]+)\}")

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


class SeedMapError(PackError):
    """A pack's `seed_map.yaml` is missing, malformed, or names something the ontology does not."""


def slug(value: object) -> str:
    """A node-key fragment. Lowercase, punctuation folded — stable across runs and machines."""
    return _NON_ALNUM.sub("-", str(value).lower()).strip("-")


# ======================================================================================
# Readers — each yields (row_key, row_mapping); none of them knows what a row means
# ======================================================================================
_SQL_INSERT = re.compile(
    r"INSERT\s+INTO\s+([A-Za-z0-9_.\"]+)\s*\(([^)]*)\)\s*VALUES", re.IGNORECASE)


def _sql_scalar(token: str):
    """One SQL literal as a Python value. `NULL` becomes `None` and nothing else does."""
    t = token.strip()
    if not t or t.upper() == "NULL":
        return None
    if t.startswith("'") and t.endswith("'") and len(t) >= 2:
        return t[1:-1].replace("''", "'")
    low = t.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        return t


def _split_sql_tuple(inner: str) -> list[str]:
    """Split one `(...)` row on commas that are OUTSIDE quotes.

    Written by hand rather than with a regex because the quoting rule that matters — `''` is an
    escaped apostrophe inside a quoted string — is precisely the one a naive split gets wrong, and
    getting it wrong shifts every later column by one silently.
    """
    out: list[str] = []
    buf: list[str] = []
    in_quote = False
    i = 0
    n = len(inner)
    while i < n:
        ch = inner[i]
        if in_quote:
            if ch == "'":
                if i + 1 < n and inner[i + 1] == "'":
                    buf.append("''")
                    i += 2
                    continue
                in_quote = False
            buf.append(ch)
        elif ch == "'":
            in_quote = True
            buf.append(ch)
        elif ch == ",":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    out.append("".join(buf))
    return out


def _take_one_tuple(text: str) -> tuple[Optional[str], str]:
    """Pull the first complete, balanced, quote-aware `(...)` group off the front of ``text``
    (skipping leading whitespace and a separating comma), or ``(None, text)`` if ``text`` does not
    yet CONTAIN one — a tuple that spans multiple physical lines, which the caller accumulates more
    text and retries. Returns ``(inner_without_parens, everything_after_the_closing_paren)``.

    Written by hand for the same reason `_split_sql_tuple` is: a `''`-escaped apostrophe inside a
    quoted string, or a literal `)` inside one, is exactly what a regex or a naive scan for the
    next `)` gets wrong.
    """
    i = 0
    n = len(text)
    while i < n and text[i] in " \t,":
        i += 1
    if i >= n or text[i] != "(":
        return None, text
    depth = 0
    in_quote = False
    j = i
    while j < n:
        ch = text[j]
        if in_quote:
            if ch == "'":
                if j + 1 < n and text[j + 1] == "'":
                    j += 2
                    continue
                in_quote = False
            j += 1
            continue
        if ch == "'":
            in_quote = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[i + 1:j], text[j + 1:]
        j += 1
    return None, text                          # unbalanced so far — more lines needed


def read_sql_insert(path: Path, *, max_rows: Optional[int] = None) -> Iterator[Mapping[str, Any]]:
    """Rows out of a generated `INSERT INTO ... VALUES (...),(...);` dump.

    Streams line by line and stops as soon as `max_rows` is reached, which is what makes a bounded
    build cheap: the estate's largest table is millions of characters, and a sampled build must not
    have to read all of it to produce a smaller graph.

    Review R-010 finding 9: the previous version matched the `INSERT INTO ... VALUES` header and
    then unconditionally `continue`d to the NEXT line, discarding anything after `VALUES` on the
    SAME line — so `INSERT INTO t (id, name) VALUES (1, 'Alice');`, a single physical line and a
    wholly ordinary shape for a generated dump, silently yielded zero rows. It also only ever
    parsed the buffer as ONE `(...)` tuple, so `VALUES (1,'a'),(2,'b');` on one line lost the
    second row. Both are fixed by draining every complete tuple `_take_one_tuple` can find out of
    the running buffer — which starts with whatever followed `VALUES` on the header's own line —
    rather than assuming a tuple's boundaries line up with a physical line's.
    """
    columns: list[str] = []
    emitted = 0
    buffer = ""
    in_statement = False
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not in_statement:
                if not stripped or stripped.startswith("--"):
                    continue
                match = _SQL_INSERT.search(stripped)
                if not match:
                    continue
                columns = [c.strip().strip('"') for c in match.group(2).split(",")]
                buffer = stripped[match.end():]
                in_statement = True
            else:
                buffer = f"{buffer} {stripped}".strip() if buffer else stripped

            while True:
                inner, rest = _take_one_tuple(buffer)
                if inner is None:
                    break
                values = [_sql_scalar(v) for v in _split_sql_tuple(inner)]
                if len(values) != len(columns):
                    raise SeedMapError(
                        f"{path}: a row has {len(values)} values for {len(columns)} columns. The "
                        f"dump and the column list disagree, and a silent off-by-one would shift "
                        f"every later column")
                yield dict(zip(columns, values))
                emitted += 1
                buffer = rest
                if max_rows is not None and emitted >= max_rows:
                    return

            if ";" in buffer:                   # the statement is terminated — reset for the next one
                in_statement = False
                columns = []
                buffer = ""


def read_csv(path: Path, *, max_rows: Optional[int] = None) -> Iterator[Mapping[str, Any]]:
    with path.open(encoding="utf-8", newline="") as fh:
        for i, row in enumerate(_csv.DictReader(fh)):
            if max_rows is not None and i >= max_rows:
                return
            # An empty CSV cell is ABSENT, not the empty string: the difference decides whether an
            # attribute is written with a pointer or lands in `unresolved`.
            yield {k: (v if (v is not None and v != "") else None) for k, v in row.items()}


def _dotted(record: Mapping, path: str):
    cur: Any = record
    for part in path.split("."):
        if isinstance(cur, Mapping):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _get(row: Mapping[str, Any], column: str):
    """One column's value, dotted-path aware — the SAME lookup `_render`/`_label` already use to
    substitute a `{column}` template placeholder.

    Review R-010 finding 11: grounding (`spec.sources`), attribute extraction (`spec.attrs`),
    `canonical_id`, and edge evidence (`spec.evidence`) all read a column with plain `row.get(column)`
    instead — so a map naming a nested JSON path (`person.email`) in any of THOSE positions (as
    opposed to inside a `{...}` template, which already worked) got `None` back even when the value
    was genuinely present: a node skipped as ungrounded, or an attribute landing in `unresolved`,
    for data that was right there. `json_pages`' own docstring promises dotted-path reach; this is
    what makes that promise true everywhere a column name is read, not only inside templates.
    """
    return _dotted(row, column) if "." in column else row.get(column)


def _derived_ptr(derivation: str, derived_from: Sequence[SourcePointer]) -> SourcePointer:
    """A pointer for a value this builder COMPUTED rather than read verbatim off one cell.

    Used exactly where the emitted bytes differ from any single cited cell's bytes: a fallback
    label built from the node's own key, or `resolve_incoming`'s combined label / selector-column
    marker. `derived` is the honest kind (invariant 2, `provenance.py`'s `derived` pointer) — it
    names the rule and keeps the pointers it was computed from, rather than citing a cell that does
    not actually contain the emitted string. Mirrors `clinic_seed._derived_ptr` (same pattern, this
    package's other seed builder); not shared because the two modules' pointer vocabularies —
    `RULES_SOURCE_ID` there, a static id here — are deliberately independent.
    """
    basis = [p for p in derived_from if p is not None]
    if not basis:
        raise SeedMapError(f"_derived_ptr({derivation!r}): no basis pointer to derive from")
    return SourcePointer(source_id="estate_seed:derived", kind="derived", derivation=derivation,
                         derived_from=basis)


def read_json_pages(path: Path, *, records_key: str = "data",
                    max_rows: Optional[int] = None) -> Iterator[Mapping[str, Any]]:
    """Records out of one captured API page. Nested objects are reachable by dotted path.

    Review R-010 finding 10: `payload.get(records_key) or []` reads as "empty page", the honest
    outcome, for THREE different situations that are not honest at all — `records_key` missing
    entirely, or present but a mapping/string/number instead of a list — and a non-mapping element
    inside a genuine list was silently skipped rather than counted. Invariant 7: fail closed, fail
    EXPLICITLY; "nothing found" where the truth is "I could not read this page" is a defect. A page
    the pack's own glob matched is expected to have this shape — this raises rather than degrades.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise SeedMapError(
            f"{path}: expected a JSON object at the top level, got {type(payload).__name__}")
    if records_key not in payload:
        raise SeedMapError(
            f"{path}: no {records_key!r} key — a page with zero records still declares an empty "
            f"list there; a missing key means the page could not be read, not that it is empty")
    records = payload[records_key]
    if not isinstance(records, list):
        raise SeedMapError(
            f"{path}: {records_key!r} must be a list of records, got {type(records).__name__}")
    for i, record in enumerate(records):
        if max_rows is not None and i >= max_rows:
            return
        if not isinstance(record, Mapping):
            raise SeedMapError(
                f"{path}: record {i} under {records_key!r} is not an object "
                f"(got {type(record).__name__}) — silently skipping it would undercount the page")
        yield record


def read_fixed_width(path: Path, *, layout: Sequence[Mapping[str, Any]],
                     max_rows: Optional[int] = None) -> Iterator[Mapping[str, Any]]:
    """Records out of a fixed-width file, sliced by a layout the MAP declares.

    The layout is data, not code: adding a field is an edit to `seed_map.yaml`. A genuinely blank
    (whitespace-only) field becomes `None`, because a legacy file pads absence with spaces rather
    than recording it, and a padded blank presented as a value would be a fabricated one.

    Review R-010 finding 12, two defects:

    1. **Decoding.** `errors="replace"` turned an invalid byte sequence into U+FFFD replacement
       characters and cited the cell as though the bytes matched — a silent corruption stamped
       with a pointer that vouches for text nobody wrote. Reading strictly (the default) instead:
       a file whose declared layout does not agree with its actual bytes fails LOUDLY, which is
       the honest outcome for a mis-declared legacy encoding (invariant 7).
    2. **All-zero-is-absent.** Blanket, for every field regardless of what it holds, this was a
       DOMAIN assumption (a zero-filled DATE field genuinely means "no date on record" in this
       estate's convention) embedded in engine code rather than declared by the layout that is
       supposed to own field-level meaning — and for any field where an all-zero VALUE is
       legitimate (an account code of `0000`, say) it silently discarded a value the source
       actually held. Now OPT-IN per field (`zero_is_absent: true` in one `layout` entry) rather
       than assumed for all of them, defaulting to `True` so today's one known user of this reader
       (`packs/government/seed_map.yaml`'s `dob_cymd`) keeps its current, correct behaviour without
       a map edit; a pack adding a field where an all-zero value is meaningful sets it `false`.
    """
    fields = [(str(f["name"]), int(f["start"]) - 1, int(f["end"]),
              bool(f.get("zero_is_absent", True))) for f in layout]
    with path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if max_rows is not None and i >= max_rows:
                return
            if not line.strip():
                continue
            row: dict[str, Any] = {}
            for name, start, end, zero_is_absent in fields:
                raw = line[start:end].strip()
                row[name] = None if (not raw or (zero_is_absent and set(raw) == {"0"})) else raw
            yield row


def _adapter_reader_not_supplied(path: Path, *, max_rows: Optional[int] = None,
                                 **_options: Any) -> Iterator[Mapping[str, Any]]:
    """The placeholder behind `reader: adapter`. Only reachable if `build_pack_graph` is asked to
    build a dataset declaring it WITHOUT an `adapter_rows=` producer for that dataset id — the
    normal call path (see `build_pack_graph`) checks for that and raises a clearer, dataset-scoped
    error before ever reaching a reader lookup. This exists so `load_seed_map`'s `reader not in
    READERS` validation accepts `reader: adapter` in a map at ALL, and so that if some future call
    path ever reaches a reader function directly (bypassing `build_pack_graph`'s guard) the failure
    is loud rather than a silent, empty read — an adapter-routed dataset has no file at `path` to
    fall back to; its rows come from a live Core, never from disk.
    """
    raise SeedMapError(
        f"{path}: this dataset declares reader: adapter, whose rows come from a Core "
        f"(`adapter_ingest.rows_from_core`), never from a file. Reached the direct-file reader "
        f"path anyway, which means build_pack_graph's own adapter_rows guard was bypassed")


READERS: Mapping[str, Callable[..., Iterator[Mapping[str, Any]]]] = {
    "sql_insert": read_sql_insert,
    "csv": read_csv,
    "json_pages": read_json_pages,
    "fixed_width": read_fixed_width,
    # Never actually called on the normal path — see `_adapter_reader_not_supplied`'s docstring
    # and `build_pack_graph`'s `adapter_rows` guard, which raises first, by dataset id, with a
    # clearer message.
    "adapter": _adapter_reader_not_supplied,
}


# ======================================================================================
# The map
# ======================================================================================
@dataclass(frozen=True)
class NodeSpec:
    kind: str
    key: str
    label: str
    sources: tuple[str, ...]
    canonical_id: Optional[str] = None
    attrs: Mapping[str, str] = field(default_factory=dict)
    valid_from: Optional[str] = None
    when: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EdgeSpec:
    kind: str
    src: str
    dst: str
    evidence: tuple[str, ...]
    attrs: Mapping[str, str] = field(default_factory=dict)
    when: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolveCase:
    """One row of a `resolve:` block's `cases:` table — see `ResolveSpec`."""

    row_column: str
    target_attr: str
    target_type_column: Optional[str] = None
    target_type_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolveSpec:
    """A dataset's declared instruction for matching its own nodes against a kind already in the
    store, entirely in terms the map supplies — see `resolve_incoming`'s docstring for the
    mechanism this drives and the two-signal rule it always applies. Every string here is DATA:
    this module never branches on what any of them means, only on their shape.
    """

    from_kind: str
    against_kind: str
    result_kind: str
    result_edge_kind: str
    claim_edge_kind: str
    name_threshold: float
    selector_column: Optional[str]
    cases: Mapping[str, ResolveCase]


@dataclass(frozen=True)
class DatasetSpec:
    """One file (or set of files) of one shape, and what the rows in it become."""

    id: str
    reader: str
    table: str
    paths: tuple[str, ...]
    trust: Trust
    row_key: str
    nodes: tuple[NodeSpec, ...] = ()
    edges: tuple[EdgeSpec, ...] = ()
    reader_options: Mapping[str, Any] = field(default_factory=dict)
    resolve: Optional[ResolveSpec] = None


@dataclass(frozen=True)
class SeedMap:
    pack: str
    root: Path
    system_time: dt.datetime
    default_valid_from: dt.datetime
    datasets: tuple[DatasetSpec, ...]


def _as_dt(value, where: str) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min, tzinfo=dt.timezone.utc)
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise SeedMapError(f"{where}: {value!r} is not an ISO timestamp") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def load_seed_map(pack: str, root: Optional[Path] = None) -> SeedMap:
    """Read and validate `packs/<pack>/seed_map.yaml`.

    The pack name goes through the same `check_pack_name` gate the ontology loader uses, and the
    fixtures root is required to stay under the repository the map lives in — the map is a pack
    artefact, and a pack artefact that can name any path on disk reopens the hole the pack loader
    was just closed against.
    """
    check_pack_name(pack)
    packs_dir = Path(root) if root is not None else packs_root()
    path = (packs_dir / pack / SEED_MAP_FILE)
    if not path.is_file():
        raise SeedMapError(f"pack {pack!r} has no {SEED_MAP_FILE} at {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SeedMapError(f"{path}: top level must be a mapping")
    if data.get("pack") != pack:
        raise SeedMapError(
            f"{path}: declares pack {data.get('pack')!r} but lives in {pack!r}")

    fixtures = (path.parent / str(data.get("fixtures_root") or ".")).resolve()
    if not fixtures.is_dir():
        raise SeedMapError(f"{path}: fixtures_root resolves to {fixtures}, which is not a directory")

    datasets: list[DatasetSpec] = []
    for entry in data.get("datasets") or []:
        if not isinstance(entry, dict):
            raise SeedMapError(f"{path}: each datasets entry must be a mapping")
        reader = str(entry.get("reader") or "")
        if reader not in READERS:
            raise SeedMapError(
                f"{path}: dataset {entry.get('id')!r} names reader {reader!r}; "
                f"available: {sorted(READERS)}")
        raw_paths = entry.get("paths") or ([entry["path"]] if entry.get("path") else [])
        resolved: list[str] = []
        for spec in raw_paths:
            matches = sorted(fixtures.glob(str(spec))) if any(
                c in str(spec) for c in "*?[") else [fixtures / str(spec)]
            for m in matches:
                m = m.resolve()
                # Review R-010 finding 13 (High): `str(m).startswith(str(fixtures))` is a STRING
                # prefix test, so `.../fixtures-escape/x` passes containment under fixtures root
                # `.../fixtures` — it starts with the right characters without being inside the
                # directory at all. `Path.is_relative_to` compares PATH COMPONENTS, which a sibling
                # directory that merely shares a prefix does not satisfy.
                if not m.is_relative_to(fixtures):
                    raise SeedMapError(
                        f"{path}: dataset {entry.get('id')!r} names {m}, outside its "
                        f"fixtures root {fixtures}")
                resolved.append(str(m))
        trust_value = str(entry.get("trust") or "untrusted")
        try:
            trust = Trust(trust_value)
        except ValueError as exc:
            raise SeedMapError(
                f"{path}: dataset {entry.get('id')!r} declares trust {trust_value!r}; "
                f"valid: {[t.value for t in Trust]}") from exc
        resolve_raw = entry.get("resolve")
        resolve = None
        if resolve_raw:
            if not isinstance(resolve_raw, dict):
                raise SeedMapError(
                    f"{path}: dataset {entry.get('id')!r}'s resolve: must be a mapping")
            cases: dict[str, ResolveCase] = {}
            for case_key, case_val in (resolve_raw.get("cases") or {}).items():
                if not isinstance(case_val, dict) or "row_column" not in case_val \
                        or "target_attr" not in case_val:
                    raise SeedMapError(
                        f"{path}: dataset {entry.get('id')!r}'s resolve.cases[{case_key!r}] "
                        f"must declare row_column and target_attr")
                cases[str(case_key)] = ResolveCase(
                    row_column=str(case_val["row_column"]),
                    target_attr=str(case_val["target_attr"]),
                    target_type_column=(str(case_val["target_type_column"])
                                        if case_val.get("target_type_column") else None),
                    target_type_values=tuple(
                        str(v).lower() for v in (case_val.get("target_type_values") or ())))
            for required in ("from_kind", "against_kind", "result_kind", "result_edge_kind",
                             "claim_edge_kind"):
                if not resolve_raw.get(required):
                    raise SeedMapError(
                        f"{path}: dataset {entry.get('id')!r}'s resolve: is missing {required!r}")
            if not cases:
                raise SeedMapError(
                    f"{path}: dataset {entry.get('id')!r}'s resolve: declares no cases")
            resolve = ResolveSpec(
                from_kind=str(resolve_raw["from_kind"]),
                against_kind=str(resolve_raw["against_kind"]),
                result_kind=str(resolve_raw["result_kind"]),
                result_edge_kind=str(resolve_raw["result_edge_kind"]),
                claim_edge_kind=str(resolve_raw["claim_edge_kind"]),
                name_threshold=float(resolve_raw.get("name_threshold", 0.999)),
                selector_column=(str(resolve_raw["selector_column"])
                                 if resolve_raw.get("selector_column") else None),
                cases=cases)
        datasets.append(DatasetSpec(
            id=str(entry.get("id") or entry.get("table") or reader),
            reader=reader,
            table=str(entry.get("table") or entry.get("id") or ""),
            paths=tuple(resolved),
            trust=trust,
            row_key=str(entry.get("row_key") or ""),
            reader_options=dict(entry.get("reader_options") or {}),
            nodes=tuple(
                NodeSpec(kind=str(n["kind"]), key=str(n["key"]), label=str(n.get("label") or n["key"]),
                         sources=tuple(str(s) for s in (n.get("sources") or [])),
                         canonical_id=(str(n["canonical_id"]) if n.get("canonical_id") else None),
                         attrs=dict(n.get("attrs") or {}),
                         valid_from=(str(n["valid_from"]) if n.get("valid_from") else None),
                         when=dict(n.get("when") or {}))
                for n in (entry.get("nodes") or [])),
            edges=tuple(
                EdgeSpec(kind=str(e["kind"]), src=str(e["src"]), dst=str(e["dst"]),
                         evidence=tuple(str(s) for s in (e.get("evidence") or [])),
                         attrs=dict(e.get("attrs") or {}), when=dict(e.get("when") or {}))
                for e in (entry.get("edges") or [])),
            resolve=resolve,
        ))
    if not datasets:
        raise SeedMapError(f"{path}: declares no datasets, so it would build an empty graph")
    return SeedMap(
        pack=pack, root=fixtures,
        system_time=_as_dt(data.get("system_time"), str(path)),
        default_valid_from=_as_dt(data.get("default_valid_from"), str(path)),
        datasets=tuple(datasets))


# ======================================================================================
# The build
# ======================================================================================
@dataclass
class BuildReport:
    """What the build did, and — as importantly — what it declined to do.

    Every number here is a thing that would otherwise be invisible. A graph that is quietly smaller
    than its estate is indistinguishable from one that lost rows to a bug, so the reasons rows did
    not become nodes are counted and named rather than swallowed.
    """

    rows_read: dict = field(default_factory=dict)
    nodes_by_kind: dict = field(default_factory=dict)
    edges_by_kind: dict = field(default_factory=dict)
    skipped_nodes_missing_key: dict = field(default_factory=dict)
    skipped_edges_missing_endpoint: dict = field(default_factory=dict)
    dangling_edges: dict = field(default_factory=dict)
    ungrounded_attrs_dropped: int = 0
    #: Two different raw (pre-slug) key values rendered the SAME node id — every occurrence,
    #: regardless of kind (R-010 findings 1/3). Counted so a graph that is one node smaller than
    #: its estate because two distinct rows collided is never confused with one that just lost a
    #: row to a bug — see `skipped_nodes_slug_collision` for the subset of these the builder
    #: refused to fuse rather than silently corroborating.
    slug_collisions: dict = field(default_factory=dict)
    #: The SUBJECT-kind subset of `slug_collisions`: refused outright rather than corroborated,
    #: because for a subject a silent fuse on a normalisation accident is the automated identity
    #: merge CLAUDE.md constraint 1 forbids (R-010 finding 1). The row is not represented at this
    #: id at all until the map is fixed to key subjects on something that does not collide.
    skipped_nodes_slug_collision: dict = field(default_factory=dict)
    #: A row's declared `valid_from` value was present but not a parseable timestamp (R-010
    #: finding 6). The node is skipped rather than dated with the map's fallback, which would be a
    #: value nobody wrote substituted for one that failed to parse.
    skipped_nodes_bad_valid_from: dict = field(default_factory=dict)
    #: A node's label template referenced a NULL column, so its label was DERIVED from the node's
    #: key instead of fabricated with no citation at all (R-010 finding 5) — counted because a
    #: derived label is worth knowing about even though it is honestly grounded.
    labels_derived_from_key: int = 0
    #: An edge spec joined node kinds its ontology's `src`/`dst` do not allow, keyed
    #: `dataset:edge_kind`, counted per ROW the spec would have fired on. Never written: a
    #: relationship between kinds the vocabulary says cannot be related is off-ontology.
    skipped_edges_wrong_endpoint_kind: dict = field(default_factory=dict)

    @property
    def node_count(self) -> int:
        return sum(self.nodes_by_kind.values())

    @property
    def edge_count(self) -> int:
        return sum(self.edges_by_kind.values())

    def as_record(self) -> dict:
        return {
            "nodes": self.node_count,
            "edges": self.edge_count,
            "rows_read": dict(sorted(self.rows_read.items())),
            "nodes_by_kind": dict(sorted(self.nodes_by_kind.items())),
            "edges_by_kind": dict(sorted(self.edges_by_kind.items())),
            "skipped_nodes_missing_key": dict(sorted(self.skipped_nodes_missing_key.items())),
            "skipped_edges_missing_endpoint": dict(
                sorted(self.skipped_edges_missing_endpoint.items())),
            "dangling_edges": dict(sorted(self.dangling_edges.items())),
            "ungrounded_attrs_dropped": self.ungrounded_attrs_dropped,
            "slug_collisions": dict(sorted(self.slug_collisions.items())),
            "skipped_nodes_slug_collision": dict(sorted(self.skipped_nodes_slug_collision.items())),
            "skipped_nodes_bad_valid_from": dict(sorted(self.skipped_nodes_bad_valid_from.items())),
            "labels_derived_from_key": self.labels_derived_from_key,
            "skipped_edges_wrong_endpoint_kind": dict(
                sorted(self.skipped_edges_wrong_endpoint_kind.items())),
        }


def _bump(counter: dict, key: str, by: int = 1) -> None:
    counter[key] = counter.get(key, 0) + by


def _render(template: str, row: Mapping[str, Any]) -> Optional[str]:
    """Substitute `{column}` from the row, or return ``None`` if any referenced column is NULL.

    `None` is the load-bearing return. A key built over a missing column would be a key that means
    "unknown" — and two rows missing the same column would collide into one node, fusing records
    that share nothing but an absence. So the row contributes nothing and the skip is counted.
    """
    missing = False

    def repl(match: re.Match) -> str:
        nonlocal missing
        value = _dotted(row, match.group(1)) if "." in match.group(1) else row.get(match.group(1))
        if value is None or str(value).strip() == "":
            missing = True
            return ""
        return slug(value)

    rendered = _TEMPLATE.sub(repl, template)
    return None if missing or not rendered.strip() else rendered


def _label(template: str, row: Mapping[str, Any]) -> Optional[str]:
    """A human-readable label. Unlike a key it is not slugified — it is text for a person to read —
    and a missing column still makes it ``None`` rather than a label with a hole in it."""
    missing = False

    def repl(match: re.Match) -> str:
        nonlocal missing
        value = _dotted(row, match.group(1)) if "." in match.group(1) else row.get(match.group(1))
        if value is None:
            missing = True
            return ""
        return str(value)

    rendered = _TEMPLATE.sub(repl, template).strip()
    return None if missing or not rendered else rendered


def _matches(when: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    """The map's row filter. Three predicates, all of them about presence or equality.

    Deliberately not an expression language. A filter that can compute is a filter that can decide,
    and every decision this builder is allowed to make is one the ontology already declared.
    """
    for column, condition in when.items():
        value = row.get(column)
        if condition == "not_null":
            if value is None:
                return False
        elif condition == "null":
            if value is not None:
                return False
        elif isinstance(condition, list):
            if value not in condition:
                return False
        elif value != condition:
            return False
    return True


def _cell(source_id: str, table: str, column: str, row_id: str, trust: Trust) -> SourcePointer:
    return SourcePointer(source_id=source_id, kind="table_cell", table=table,
                         column=column, row_id=row_id, trust=trust)


def _pointer(row: Mapping[str, Any], source_id: str, dataset: DatasetSpec, column: str,
            row_id: str) -> SourcePointer:
    """The pointer for one cell — a Core's OWN pointer when `row` was read through the adapter
    fleet, carrying that Core's real PER-FIELD trust; `_cell()`'s dataset-level stamp for every
    other reader, exactly as before this function existed.

    `row.pointer_for` is duck-typed rather than an isinstance check against `AdapterRow`
    (`adapter_ingest.py`) on purpose: this module has no import-time dependency on
    `databridge_adapter` — only `adapter_ingest.py` and a pack's own ingest wiring
    (`government_seed.py`) do — so a plain dict from `read_csv`/`read_sql_insert`/etc, which has no
    `pointer_for` attribute at all, falls straight through to the unchanged `_cell()` path.
    """
    native = getattr(row, "pointer_for", None)
    if native is not None:
        ptr = native(column)
        if ptr is not None:
            return ptr
    return _cell(source_id, dataset.table, column, row_id, dataset.trust)


def _valid_from(value, fallback: dt.datetime) -> Optional[dt.datetime]:
    """The row's declared `valid_from`, or ``None`` if it cannot honestly be produced.

    A MISSING value (the map named no `valid_from` column, or this row's cell is NULL) uses
    `fallback` — that is the map's own default, not a claim about this row, so substituting it is
    not a fabrication. Review R-010 finding 6: a NON-NULL value that fails to PARSE used to be
    treated the same way, which silently replaced bytes the row never contained (a malformed
    `not-a-date`) with a date nobody wrote — invariants 2 and 7 both name this a defect. Returning
    ``None`` here is load-bearing the same way it is in `_render`: the caller (`_build_node`) skips
    the node and counts it, rather than dating it with a value that is not the row's.
    """
    if value is None:
        return fallback
    try:
        return _as_dt(value, "valid_from")
    except SeedMapError:
        return None


def _ingest_rows(rows: Iterable[Mapping[str, Any]], *, dataset: DatasetSpec, seed: SeedMap,
                 source_id: str, vocab, nodes: dict, raw_keys: dict[str, str], edges: dict,
                 report: BuildReport) -> None:
    """One dataset's rows -> nodes and edges. Shared by the direct-file loop and the adapter-routed
    branch below (`build_pack_graph`) so the two paths cannot drift: a row is a row, whether it
    came from `_csv.DictReader` or from a Core's `resolve()`, and everything downstream of "here is
    a `(row_key, {column: value})` pair" is identical either way — only how the pointer for each
    value is minted differs (`_pointer`, per-row, via `AdapterRow.pointer_for` when present).
    """
    for row in rows:
        _bump(report.rows_read, dataset.id)
        row_id = _render(dataset.row_key, row) if dataset.row_key else None
        if row_id is None:
            row_id = str(report.rows_read[dataset.id])
        for spec in dataset.nodes:
            _build_node(spec, row, row_id, dataset, seed, source_id, vocab, nodes, raw_keys, report)
        for spec in dataset.edges:
            _build_edge(spec, row, row_id, dataset, seed, source_id, vocab, edges, report)


def build_pack_graph(pack: str, *, max_rows_per_dataset: Optional[int] = None,
                     packs_dir: Optional[Path] = None,
                     only: Optional[Iterable[str]] = None,
                     adapter_rows: Optional[Mapping[str, Callable[[], Iterable[Mapping[str, Any]]]]]
                     = None,
                     ) -> tuple[list[GraphNode], list[GraphEdge], BuildReport]:
    """Execute a pack's `seed_map.yaml` and return `(nodes, edges, report)`.

    `max_rows_per_dataset` truncates each dataset to its first N rows *in file order*, which is
    deterministic: the same bound produces the same graph on any machine. It exists because the
    full estate is tens of thousands of rows and a test that has to build all of them is a test
    nobody runs.

    `adapter_rows` routes specific datasets through the adapter fleet instead of a direct file
    read (`adapter_ingest.py`). It maps a dataset id to a zero-argument callable returning an
    iterable of rows — call it again for a fresh read, since a Core's `resolve()` may re-admit
    through Policy each time. **Opt-in and per-dataset**: a dataset whose id is not a key here
    reads exactly as it always has, straight off disk, so the default call (no `adapter_rows`
    argument at all, what every existing caller passes) is byte-for-byte the pre-existing
    behaviour — the pinned government build's node/edge counts do not move. A dataset declaring
    `reader: adapter` with no matching entry here contributes nothing to an ordinary full build
    (`only=None`) — it is opt-in by construction, so a build that does not name it is unaffected by
    its existing in the map at all — but is refused LOUDLY, naming the dataset, if `only=` names it
    explicitly and still supplies no producer: a caller that asked for it by id gets a clear error
    rather than a silent zero, because unlike every other dataset it has no file to fall back to.

    Nothing is written to a store here. Building and persisting are separate so that the graph can
    be counted, inspected and asserted against without a database, and so that a build failure is
    never a half-written store.
    """
    seed = load_seed_map(pack, packs_dir)
    vocab = active_vocabulary()
    wanted = set(only) if only is not None else None
    adapter_rows = adapter_rows or {}
    report = BuildReport()
    nodes: dict[str, GraphNode] = {}
    #: `identifier -> the RAW (pre-slug) key text that produced it` — the slug-collision guard
    #: `_build_node` needs to tell "the same key, seen again" from "two DIFFERENT keys that
    #: normalised alike" (R-010 findings 1/3). Threaded alongside `nodes` rather than folded into
    #: it because it is bookkeeping for THIS build pass, not part of the graph it produces.
    raw_keys: dict[str, str] = {}
    edges: dict[str, GraphEdge] = {}

    for dataset in seed.datasets:
        if wanted is not None and dataset.id not in wanted:
            continue
        producer = adapter_rows.get(dataset.id)
        if producer is not None:
            source_id = f"adapter:{dataset.id}"
            _ingest_rows(producer(), dataset=dataset, seed=seed, source_id=source_id, vocab=vocab,
                        nodes=nodes, raw_keys=raw_keys, edges=edges, report=report)
            continue
        if dataset.reader == "adapter":
            if wanted is not None and dataset.id in wanted:
                # Explicitly asked for, by id, and nothing supplied to build it: fail loud rather
                # than silently return zero rows for a dataset the caller specifically named.
                raise SeedMapError(
                    f"dataset {dataset.id!r} declares reader: adapter but adapter_rows={{}} (or a "
                    f"mapping without a {dataset.id!r} key) was passed to build_pack_graph. There "
                    f"is no file behind this dataset to fall back to — its rows come from a Core, "
                    f"and the caller has to supply one for this dataset id")
            # NOT explicitly requested (an ordinary full build, `only=None`) and no producer
            # supplied: this dataset does not exist for this build. An adapter-routed dataset is
            # opt-in by construction — a full build that does not know or care that it exists must
            # not break because it is present in the map with nothing to build it. This is what
            # keeps the pinned government build (`only=None`, `adapter_rows=None`) byte-for-byte
            # unchanged by adding this dataset to the map at all.
            continue
        reader = READERS[dataset.reader]
        for raw_path in dataset.paths:
            path = Path(raw_path)
            source_id = str(path.relative_to(seed.root)).replace("\\", "/")
            options = dict(dataset.reader_options)
            rows = reader(path, max_rows=max_rows_per_dataset, **options)
            _ingest_rows(rows, dataset=dataset, seed=seed, source_id=source_id, vocab=vocab,
                        nodes=nodes, raw_keys=raw_keys, edges=edges, report=report)

    # Endpoints that resolve to nothing are DROPPED and COUNTED, never quietly kept. A dangling
    # edge is a real finding in this estate — a fraction of land references genuinely do not exist
    # in the survey — and it belongs in the report where somebody can read it, not in the graph
    # where it would look like a relationship to a node nobody can open.
    kept: list[GraphEdge] = []
    for edge in edges.values():
        if edge.src_id in nodes and edge.dst_id in nodes:
            kept.append(edge)
        else:
            _bump(report.dangling_edges, str(edge.kind))
            _bump(report.edges_by_kind, str(edge.kind), -1)

    node_list = list(nodes.values())
    return node_list, _reconcile_edge_validity(node_list, kept), report


def _build_node(spec: NodeSpec, row: Mapping[str, Any], row_id: str, dataset: DatasetSpec,
                seed: SeedMap, source_id: str, vocab, nodes: dict, raw_keys: dict[str, str],
                report: BuildReport) -> None:
    if not _matches(spec.when, row):
        return
    key = _render(spec.key, row)
    if key is None:
        _bump(report.skipped_nodes_missing_key, f"{dataset.id}:{spec.kind}")
        return
    try:
        kind = NodeKind(spec.kind)
    except Exception as exc:
        raise SeedMapError(
            f"dataset {dataset.id!r} names node kind {spec.kind!r}, which pack {seed.pack!r} does "
            f"not declare. The graph is built from the PREDEFINED ontology; a kind the library "
            f"lacks goes through the growth protocol, never through the builder") from exc

    identifier = node_id(kind, key)
    grounding = tuple(
        _pointer(row, source_id, dataset, column, row_id)
        for column in spec.sources if _get(row, column) is not None)
    if not grounding:
        # Every column the map offered as evidence is NULL, so there is nothing to cite for the
        # claim that this thing exists. P4: no pointer, no node.
        _bump(report.skipped_nodes_missing_key, f"{dataset.id}:{spec.kind}")
        return

    attrs: dict[str, Any] = {}
    attr_sources: dict[str, SourcePointer] = {}
    for name, column in spec.attrs.items():
        value = _get(row, column)
        if value is None:
            attrs[name] = None                       # lands in `unresolved`; never guessed
            report.ungrounded_attrs_dropped += 1
        else:
            attrs[name] = value if isinstance(value, (str, int, float, bool)) else str(value)
            attr_sources[name] = _pointer(row, source_id, dataset, column, row_id)

    canonical = None
    if spec.canonical_id:
        raw = _get(row, spec.canonical_id)
        if raw is not None:
            canonical = str(raw)
            # `canonical_id` is the exact-merge key — the value that decides two records are the
            # same thing — so it carries its OWN pointer rather than borrowing the entity's.
            attr_sources["canonical_id"] = _pointer(
                row, source_id, dataset, spec.canonical_id, row_id)

    valid_from = _valid_from(_get(row, spec.valid_from) if spec.valid_from else None,
                             seed.default_valid_from)
    if valid_from is None:
        # R-010 finding 6: the row DECLARED a `valid_from` and it did not parse. Skipping (rather
        # than falling back to the map's default) is the same rule `key`/`grounding` already
        # follow: a value that failed to read is not replaced by one nobody wrote.
        _bump(report.skipped_nodes_bad_valid_from, f"{dataset.id}:{spec.kind}")
        return

    label = _label(spec.label, row)
    if label is None:
        # R-010 finding 5: `_label` correctly returned None (the label template named a NULL
        # column) — the old code silently swapped in the slugified `key` with no citation at all,
        # a fabricated value with no source (hard constraint 2). The key is still the best
        # available label, but now it is a DERIVED one: `derived_from` cites the same grounding
        # that justifies the node existing at all, so the pointer is honest about what it is.
        label = key
        label_ptr: Optional[SourcePointer] = _derived_ptr(
            f"label fallback: template {spec.label!r} referenced a missing column; used the "
            f"node's own key instead", list(grounding))
        report.labels_derived_from_key += 1
    else:
        label_ptr = None

    # R-010 findings 1/3: `identifier` is `f"{kind}:{slug(key)}"`, and `slug()` is a LOSSY
    # normalisation — "AB.1" and "AB-1" both fold to "ab-1". Two DIFFERENT raw keys landing on the
    # same identifier is a collision the map's author did not intend, and the old code treated it
    # exactly like a genuine re-sighting of the same row (first writer wins, later sightings only
    # contribute pointers) — for a SUBJECT kind that is precisely the automated identity merge
    # CLAUDE.md constraint 1 forbids, reached without ever asking `merge.upsert_node` a single
    # question, because the two rows never became two separate `GraphNode`s in the first place.
    raw_key = _label(spec.key, row)                 # the SAME substitution, without the slug
    existing = nodes.get(identifier)
    if existing is not None and raw_keys.get(identifier) != raw_key:
        _bump(report.slug_collisions, f"{dataset.id}:{spec.kind}")
        if vocab.is_subject(spec.kind):
            # Refuse rather than silently fuse. The row is not represented at this id until the
            # map keys this subject on something that does not collide — visible as a count, the
            # same "counted, not silently dropped" treatment every other refusal in this file gets.
            _bump(report.skipped_nodes_slug_collision, f"{dataset.id}:{spec.kind}")
            return

    node = GraphNode(
        id=identifier, kind=kind, label=label, canonical_id=canonical,
        attrs=attrs,
        attr_sources=(attr_sources if label_ptr is None
                     else {**attr_sources, "label": label_ptr}),
        sources=grounding, valid_from=valid_from, system_from=seed.system_time)
    if existing is None:
        nodes[identifier] = node
        raw_keys[identifier] = raw_key
        _bump(report.nodes_by_kind, spec.kind)
    else:
        # First writer wins on identity; a later sighting CORROBORATES by contributing its
        # pointers. Overwriting would let the last file read decide what a thing is, which makes
        # the graph depend on dataset order.
        fresh = tuple(p for p in node.sources if p not in existing.sources)
        if fresh:
            nodes[identifier] = existing.model_copy(
                update={"sources": (*existing.sources, *fresh)})


def _build_edge(spec: EdgeSpec, row: Mapping[str, Any], row_id: str, dataset: DatasetSpec,
                seed: SeedMap, source_id: str, vocab, edges: dict, report: BuildReport) -> None:
    if not _matches(spec.when, row):
        return
    try:
        kind = EdgeKind(spec.kind)
    except Exception as exc:
        raise SeedMapError(
            f"dataset {dataset.id!r} names edge kind {spec.kind!r}, which pack {seed.pack!r} does "
            f"not declare") from exc
    if kind.meta in ATTESTED_META_KINDS:
        # Structural, not a matter of how the map is written. An edge bound to an attested
        # meta-kind records a DETERMINATION of identity, and no automated builder may write one —
        # the system presents candidates and a named human attests (CLAUDE.md constraint 1).
        raise SeedMapError(
            f"dataset {dataset.id!r} asks for edge kind {spec.kind!r}, which binds to "
            f"{kind.meta.value!r} — an identity DETERMINATION. A builder may never write one: it "
            f"is recorded only on the far side of a named human's attestation. Use an "
            f"`about`-shaped kind to record the CLAIM instead")

    # The ontology's `src`/`dst` lists are the edge's closed shape. An endpoint template names its
    # node kind literally (`kind:{key}`), so a map that joins the wrong kinds is detectable before
    # any row is rendered. Skipped and COUNTED rather than raised: the rest of the build is still
    # sound, and a report that names `dataset:kind` is how the map author finds the line.
    decl = kind.decl
    if not (decl.allows("src", _endpoint_kind(spec.src))
            and decl.allows("dst", _endpoint_kind(spec.dst))):
        _bump(report.skipped_edges_wrong_endpoint_kind, f"{dataset.id}:{spec.kind}")
        return

    src = _render_endpoint(spec.src, row)
    dst = _render_endpoint(spec.dst, row)
    if src is None or dst is None:
        _bump(report.skipped_edges_missing_endpoint, f"{dataset.id}:{spec.kind}")
        return

    evidence = tuple(
        _pointer(row, source_id, dataset, column, row_id)
        for column in spec.evidence if _get(row, column) is not None)
    if not evidence:
        _bump(report.skipped_edges_missing_endpoint, f"{dataset.id}:{spec.kind}")
        return

    attrs: dict[str, Any] = {}
    attr_sources: dict[str, SourcePointer] = {}
    for name, column in spec.attrs.items():
        value = _get(row, column)
        if value is None:
            attrs[name] = None
        else:
            attrs[name] = value if isinstance(value, (str, int, float, bool)) else str(value)
            attr_sources[name] = _pointer(row, source_id, dataset, column, row_id)

    identifier = f"{src}|{kind.value}|{dst}"
    if identifier in edges:
        return
    edges[identifier] = GraphEdge(
        id=identifier, src_id=src, dst_id=dst, kind=kind,
        attrs=attrs, attr_sources=attr_sources, sources=evidence,
        valid_from=seed.default_valid_from, system_from=seed.system_time)
    _bump(report.edges_by_kind, spec.kind)


def _endpoint_kind(template: str) -> str:
    """The literal node kind an endpoint template names (`customer:{id}` → `customer`)."""
    if ":" not in template:
        raise SeedMapError(
            f"edge endpoint {template!r} must be 'kind:key-template' so the node it names is "
            f"unambiguous")
    return template.split(":", 1)[0]


def _render_endpoint(template: str, row: Mapping[str, Any]) -> Optional[str]:
    """An endpoint is `kind:{template}` — the kind is literal, the key is computed."""
    if ":" not in template:
        raise SeedMapError(
            f"edge endpoint {template!r} must be 'kind:key-template' so the node it names is "
            f"unambiguous")
    kind_name, key_template = template.split(":", 1)
    key = _render(key_template, row)
    return None if key is None else f"{kind_name}:{key}"


def _reconcile_edge_validity(nodes: Sequence[GraphNode],
                             edges: Sequence[GraphEdge]) -> list[GraphEdge]:
    """Lift every edge's `valid_from` to the later of its own and both endpoints'.

    A relationship cannot begin before both things it relates exist. Done as a pass over the
    finished lists rather than inside the edge builder because an edge is routinely constructed
    before both of its endpoint nodes are, and at that moment the correct date is not yet knowable.
    """
    starts = {n.id: n.valid_from for n in nodes if n.valid_from is not None}
    out: list[GraphEdge] = []
    for edge in edges:
        bounds = [d for d in (starts.get(edge.src_id), starts.get(edge.dst_id)) if d is not None]
        if edge.valid_from is not None:
            bounds.append(edge.valid_from)
        latest = max(bounds) if bounds else edge.valid_from
        out.append(edge if latest == edge.valid_from
                   else edge.model_copy(update={"valid_from": latest}))
    return out


def load_pack_graph(store: GraphStore, pack: str, *,
                    max_rows_per_dataset: Optional[int] = None,
                    packs_dir: Optional[Path] = None,
                    only: Optional[Iterable[str]] = None,
                    adapter_rows: Optional[Mapping[str, Callable[[], Iterable[Mapping[str, Any]]]]]
                    = None) -> dict:
    """Build the pack's graph and upsert it into `store`. Idempotent. See `build_pack_graph` on
    `adapter_rows` — opt-in, per-dataset, and it does not change the default (no-argument) path."""
    before = store.counts()
    nodes, edges, report = build_pack_graph(
        pack, max_rows_per_dataset=max_rows_per_dataset, packs_dir=packs_dir, only=only,
        adapter_rows=adapter_rows)
    for node in nodes:
        upsert_node(store, node)
    for edge in edges:
        upsert_edge(store, edge)
    after = store.counts()
    return {
        "nodes_created": after["nodes"] - before["nodes"],
        "edges_created": after["edges"] - before["edges"],
        "sources_attached": after["sources"] - before["sources"],
        "attribute_sources_attached": after["attribute_sources"] - before["attribute_sources"],
        "report": report.as_record(),
    }


def orphan_node_ids(nodes: Iterable[GraphNode], edges: Iterable[GraphEdge]) -> list[str]:
    """Node ids with no incident edge. Reported rather than pruned: a thing the estate holds and
    relates to nothing is a fact about the estate."""
    connected: set[str] = set()
    for edge in edges:
        connected.add(edge.src_id)
        connected.add(edge.dst_id)
    return sorted(n.id for n in nodes if n.id not in connected)


# ======================================================================================
# Incremental resolution — joining a dataset already IN the store against another kind
# already in the store, entirely as `seed_map.yaml` declares it
# ======================================================================================
def resolve_incoming(store: GraphStore, pack: str, *, dataset_id: str,
                     vocab: Optional[Vocabulary] = None,
                     packs_dir: Optional[Path] = None) -> dict:
    """Match every open node of `dataset_id`'s declared `from_kind` (already in `store`) against
    every open node of its declared `against_kind` (already in `store`), following the `resolve:`
    block that dataset names in `seed_map.yaml` — never a rule this module writes down itself.
    Everything domain-specific about what is being matched, and on what, is DATA read from the
    map at call time; nothing here branches on what any of it means.

    Unlike `build_pack_graph`, this reads the STORE rather than a fixture row, because the
    question it answers — "does this newly-loaded node correspond to something already believed?"
    — is not answerable from one row in isolation. That is also why it runs as a step of its own,
    after the dataset's own nodes are already upserted (`load_pack_graph(..., only=[dataset_id])`
    or a full build): there is nothing yet to resolve against until then.

    ## The two-signal rule

    A match requires BOTH of two independent signals the map supplies, never one alone:

    1. **Name.** `token_set_ratio` (`merge.py`) between the incoming node's `label` and a
       candidate's `label`, at or above the map's declared `name_threshold` — the same
       legal-form-folding, order-insensitive metric entity resolution elsewhere in this package
       uses, applied here to CANDIDATE selection, never to a decision.
    2. **A decisive attribute**, selected by the incoming node's own `selector_column` value
       (looked up in the map's `cases`) and compared, case-normalised, to the SAME-named attribute
       on the candidate. The incoming row's own value must be present; the candidate's may be
       ABSENT without vetoing the match — an incomplete record is not evidence against one, and
       is exactly the case a human, not this pass, should see and weigh — but if the candidate
       carries a value AND it disagrees, that is an explicit conflict and the match is refused. A
       `target_type_column` / `target_type_values` pair, where the map declares one, additionally
       requires the candidate's own type attribute to be in the declared set before it is even
       considered.

    Signal 2 is not a refinement of signal 1; it is what keeps two same- or similar-named
    candidates apart when the map's own data plants exactly that collision — a name match with no
    second signal would fuse them, and the whole reason this function requires both is to refuse
    that. Where signal 2 comes back an explicit conflict, NOTHING is written: the incoming node
    stays exactly as unlinked as it was, which is the honest, visible outcome, not a defect.

    ## Why a match is a new node and two edges, never a write to an existing one

    This function never constructs a node of `against_kind`; it only ever constructs a node of the
    map's declared `result_kind`, plus a `result_edge_kind` edge back to the row that produced the
    match and a `claim_edge_kind` edge to the EXISTING candidate node — read, never rewritten, its
    id, attrs and `system_from` untouched. That is not a convention this function trusts a caller
    to honour: it is the only thing its code path can do, because `upsert_node` here is called on
    nothing but `result_kind`, and `against_kind` nodes are read through `row_to_node` and never
    passed to it. A subject kind on the far side of `against_kind` (spine D4b, `never_fuzzy_merge`)
    is therefore never at risk of an automated merge regardless of what threshold the map sets —
    the merge this function could perform simply does not exist as an operation it has.

    Returns counts a caller can assert against: `incoming`, `targets_considered`,
    `name_candidates` (pairs clearing signal 1), `vetoed` (cleared signal 1, failed signal 2 or
    the type check), `matched`, and how many `result_kind` nodes / edges were newly created.
    Idempotent — re-running against an unchanged store creates nothing new.
    """
    seed = load_seed_map(pack, packs_dir)
    dataset = next((d for d in seed.datasets if d.id == dataset_id), None)
    if dataset is None or dataset.resolve is None:
        raise SeedMapError(
            f"dataset {dataset_id!r} declares no resolve: block in pack {pack!r}'s seed map")
    spec = dataset.resolve
    # `vocab` is accepted for interface symmetry with the rest of this module (a caller that
    # already resolved the active vocabulary can thread it through) but is not consulted for node
    # resolution below — see the comment beside `store.open_node_row` for why an exact id lookup,
    # never `merge.upsert_node`'s fuzzy fallback, is what a `result_kind` node's identity needs.
    del vocab

    from_kind = NodeKind(spec.from_kind)
    against_kind = NodeKind(spec.against_kind)
    result_kind = NodeKind(spec.result_kind)
    result_edge_kind = EdgeKind(spec.result_edge_kind)
    claim_edge_kind = EdgeKind(spec.claim_edge_kind)

    incoming_nodes = [row_to_node(store, r) for r in store.open_nodes_of_kind(from_kind)]
    targets = [row_to_node(store, r) for r in store.open_nodes_of_kind(against_kind)]

    report = {
        "incoming": len(incoming_nodes), "targets_considered": len(targets),
        "name_candidates": 0, "vetoed": 0, "matched": 0,
        "result_nodes_created": 0, "edges_created": 0,
    }

    for row_node in incoming_nodes:
        selector = ((row_node.attrs.get(spec.selector_column) or "").strip().upper()
                    if spec.selector_column else None)
        case = spec.cases.get(selector) if selector is not None else next(
            iter(spec.cases.values()), None)
        if case is None:
            continue
        row_value = (row_node.attrs.get(case.row_column) or "").strip().upper()

        for target in targets:
            if token_set_ratio(row_node.label, target.label) < spec.name_threshold:
                continue
            report["name_candidates"] += 1

            if case.target_type_column:
                target_type = (target.attrs.get(case.target_type_column) or "").strip().lower()
                if target_type not in case.target_type_values:
                    report["vetoed"] += 1
                    continue

            if not row_value:
                report["vetoed"] += 1
                continue
            # A candidate's OWN decisive attribute may be missing on the target side (an
            # incomplete registry row) without that absence counting as a mismatch: absence is
            # not evidence against a match, and an incomplete record is exactly the case a human
            # reviewer, not this pass, should see and decide on. An EXPLICIT conflict — the
            # attribute present on both sides and disagreeing — is the only thing that vetoes.
            target_value = (target.attrs.get(case.target_attr) or "").strip().upper()
            if target_value and target_value != row_value:
                report["vetoed"] += 1
                continue

            report["matched"] += 1
            decisive_ptr = row_node.attr_sources.get(case.row_column)
            # `row_value` was already checked truthy above, and a non-None `attrs` value is
            # REQUIRED by the grounding wall to carry its own `attr_sources` pointer
            # (`schema._attrs_are_grounded` rule 1) — so `decisive_ptr` is structurally guaranteed
            # here, not merely likely.
            basis = tuple(row_node.sources) + ((decisive_ptr,) if decisive_ptr else ())

            # R-010 finding 4: `label` and `attrs["matched_on"]` are both COMPUTED strings that
            # appear in no single cell — `label` concatenates two different nodes' labels;
            # `matched_on` names the SELECTOR COLUMN (`case.row_column`, e.g. `"employer_id"`),
            # not the value read from it — yet both used to be cited with ordinary `table_cell`-
            # rooted pointers (`decisive_ptr` cites the CELL's VALUE, not the column-name string
            # sitting in `attrs["matched_on"]`). Emitted bytes differing from cited bytes with no
            # `derived` pointer is exactly invariant 2's violation. Fixed with `derived` pointers
            # that name the rule and keep the real pointers as their basis, so the citation is
            # honest about being computed rather than read.
            result_key = f"{row_node.id.split(':', 1)[1]}--{target.id.split(':', 1)[1]}"
            label_ptr = _derived_ptr(
                f"label = matched incoming node's label + candidate's label (selector "
                f"{case.row_column!r})", list(basis) + list(target.sources))
            matched_on_ptr = _derived_ptr(
                f"matched_on names the selector column {case.row_column!r} that decided this "
                f"match, not a value read from it", [decisive_ptr] if decisive_ptr else list(basis))
            result_node = GraphNode(
                id=node_id(result_kind, result_key), kind=result_kind,
                label=f"{row_node.label} / {target.label}",
                attrs={"matched_on": case.row_column},
                attr_sources={"matched_on": matched_on_ptr, "label": label_ptr},
                sources=basis, valid_from=row_node.valid_from, system_from=row_node.system_from)
            # NOT `merge.upsert_node`: its resolution order falls through to a FUZZY label match
            # for any kind outside `never_fuzzy_merge_kinds` (spine D4b's own `subject_kinds`),
            # and `result_kind` is an event, never declared a subject, so it is not in that set.
            # This node's identity is `result_key` — an exact, deterministic composite of two ids
            # already in the graph — never a name, so fuzzy resolution is not a safety net here;
            # it is the exact failure mode the trap fixture exists to catch, one level up: two
            # DIFFERENT matched pairs against the SAME incoming row can carry near-identical
            # labels ("X / near-duplicate-of-X"), and a fuzzy fallback would fuse their results
            # under one id, silently dropping one pair's claim. An exact id lookup is what
            # `result_key` was built for, so it is the only resolution used here.
            existing_row = store.open_node_row(result_node.id)
            if existing_row is None:
                store.add_node(result_node)
                report["result_nodes_created"] += 1
            else:
                existing_result = row_to_node(store, existing_row)
                fresh = [p for p in result_node.sources if p not in existing_result.sources]
                if fresh:
                    store.append_sources(existing_result.id, fresh)

            result_edge = GraphEdge(
                id=f"{result_node.id}|{result_edge_kind.value}|{row_node.id}",
                src_id=result_node.id, dst_id=row_node.id, kind=result_edge_kind,
                sources=basis, valid_from=row_node.valid_from, system_from=row_node.system_from)
            if upsert_edge(store, result_edge).action == "inserted":
                report["edges_created"] += 1

            claim_edge = GraphEdge(
                id=f"{result_node.id}|{claim_edge_kind.value}|{target.id}",
                src_id=result_node.id, dst_id=target.id, kind=claim_edge_kind,
                sources=basis, valid_from=row_node.valid_from, system_from=row_node.system_from)
            if upsert_edge(store, claim_edge).action == "inserted":
                report["edges_created"] += 1

    return report


__all__ = [
    "BuildReport", "DatasetSpec", "EdgeSpec", "NodeSpec", "READERS", "ResolveCase", "ResolveSpec",
    "SEED_MAP_FILE", "SeedMap", "SeedMapError", "build_pack_graph", "load_pack_graph",
    "load_seed_map", "orphan_node_ids", "read_csv", "read_fixed_width", "read_json_pages",
    "read_sql_insert", "resolve_incoming", "slug",
]

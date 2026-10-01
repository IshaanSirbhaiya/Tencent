"""Probe the non-relational sources: count and sample, never load.

Mailboxes, document drops, OCR sidecars, fixed-width flat files, CSV and JSONL
streams are read where they lie. The point is that one `status` command covers
every source in an estate, not only the ones that happen to live in a database.

Metric names are derived from the data (file stems, record kinds), and matched
against the estate's declared counts through a small alias table — `messages` and
`emails` are the same fact under two names. The alias table maps *names*; it never
maps a name to a number.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .discovery import EstateSpec, SourceSpec
from .verify import prose_orphan_rate

# names for the same fact, so a probe metric can meet an estate's chosen wording
ALIASES: dict[str, tuple[str, ...]] = {
    "documents": ("docs", "files", "scans"),
    "messages": ("emails", "mail", "items"),
    "records": ("rows", "entries", "lines"),
    "sidecars": ("photos", "images", "files"),
    "events": ("messages", "records"),
    "shards": ("parts", "files"),
    "snapshot_pages": ("pages",),
}

_SHARD_SUFFIX = re.compile(
    r"(?:_part\d+|_\d{5,}|_\d{4}|_\d{4}-\d{2}-\d{2}|_\d{4}-\d{2})$", re.I
)
_LAYOUT_ROW = re.compile(
    r"^\|\s*`?([A-Z0-9][A-Z0-9-]*)`?\s*\|\s*`?([^|`]+?)`?\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|"
)
_RECLEN = re.compile(r"record length\s*\**\s*(\d+)\s*bytes", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


_METRIC_SUFFIX = re.compile(r"_(rows|count|counts|entries|total|totals)$", re.I)


def _metric_matches(declared: str, names: tuple[str, ...]) -> bool:
    """Does a declared metric name mean the same thing as one of a probe's names?

    Exact, alias, or — because packs write `limit_utilisation_rows` where the file is
    `limit_utilisation.csv` — a shared prefix of at least four characters.
    """
    want = {_norm(x) for x in names}
    got = _norm(declared)
    if got in want:
        return True
    trimmed = _norm(_METRIC_SUFFIX.sub("", declared))
    if trimmed in want:
        return True
    return any(len(trimmed) >= 4 and (n.startswith(trimmed) or trimmed.startswith(n)) for n in want if len(n) >= 4)


@dataclass
class Match:
    key: str
    observed: int
    expected: int
    matched_at: str = ""  # which variant of the observation agreed, if not the primary

    @property
    def verdict(self) -> str:
        return "OK" if self.observed == self.expected else "MISMATCH"


@dataclass
class Probe:
    source: SourceSpec
    root: Path | None
    files: int = 0
    bytes: int = 0
    metrics: dict[str, int] = field(default_factory=dict)
    # a metric that legitimately has more than one value — a capture store holds the
    # same collection at several instants, and a declared count is a count *at one of
    # them*. Keyed metric -> variant label -> count.
    variants: dict[str, dict[str, int]] = field(default_factory=dict)
    extensions: dict[str, int] = field(default_factory=dict)
    sample: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    matches: list[Match] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.source.id


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _walk(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def _stem_stream(path: Path) -> str:
    stem = path.stem
    while True:
        new = _SHARD_SUFFIX.sub("", stem)
        if new == stem:
            return stem
        stem = new


def _csv_rows(path: Path) -> tuple[int, list[str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            return 0, []
        return sum(1 for _ in reader), header


def _first_line(path: Path, limit: int = 150) -> str:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    return line[:limit] + ("…" if len(line) > limit else "")
    except OSError:
        pass
    return ""


def _bump(d: dict[str, int], key: str, n: int) -> None:
    d[key] = d.get(key, 0) + n


def _records_in(doc: Any) -> list[dict]:
    """The record list inside a paged API response, whatever the envelope calls it."""
    if isinstance(doc, list):
        return [x for x in doc if isinstance(x, dict)]
    if isinstance(doc, dict):
        for value in doc.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    return []


# ---------------------------------------------------------------------------
# per-kind probes
# ---------------------------------------------------------------------------


def _probe_documents(p: Probe, files: list[Path]) -> None:
    formats = set(p.source.formats)
    docs = [f for f in files if not formats or f.suffix.lstrip(".").lower() in formats]
    index = p.source.index_csv()
    if index is not None:
        docs = [f for f in docs if f != index]
        rows, header = _csv_rows(index)
        _bump(p.metrics, "documents", rows)
        p.notes.append(f"index {index.name}: {rows} row(s), {len(header)} column(s)")
        col = next((c for c in header if "path" in c.lower()), None)
        if col:
            missing = 0
            listed: set[Path] = set()
            with index.open(encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    target = (index.parent / row[col]).resolve()
                    listed.add(target)
                    if not target.exists():
                        missing += 1
            if missing:
                p.problems.append(f"{p.id}: {missing} indexed document(s) missing on disk")
            extra = [f for f in docs if f.resolve() not in listed and f.name not in ("README.md",)]
            if extra:
                p.notes.append(f"{len(extra)} file(s) on disk are not listed in the index")
    else:
        _bump(p.metrics, "documents", len(docs))
    if docs:
        p.sample.append(f"{docs[0].relative_to(p.root)}: {_first_line(docs[0])}")


def _probe_mail(p: Probe, files: list[Path]) -> None:
    eml = [f for f in files if f.suffix.lower() == ".eml"]
    _bump(p.metrics, "messages", len(eml))
    index = p.source.index_csv()
    if index is not None:
        rows, header = _csv_rows(index)
        p.notes.append(f"index {index.name}: {rows} row(s)")
        if rows != len(eml):
            p.problems.append(f"{p.id}: index lists {rows} message(s) but {len(eml)} .eml on disk")
    if eml:
        headers = []
        with eml[0].open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    break
                if line[:1] not in " \t" and ":" in line:
                    name = line.split(":", 1)[0]
                    if name.lower() in ("from", "subject", "date") or name.lower().startswith("x-"):
                        headers.append(line.strip()[:90])
        p.sample.extend(headers[:3])


def _probe_csv(p: Probe, files: list[Path]) -> None:
    streams: dict[str, list[Path]] = {}
    for f in files:
        if f.suffix.lower() != ".csv":
            continue
        streams.setdefault(_stem_stream(f), []).append(f)
    for stream, paths in sorted(streams.items()):
        total = 0
        header: list[str] = []
        for path in paths:
            n, h = _csv_rows(path)
            total += n
            header = header or h
        name = "restated_rows" if "restated" in stream.lower() else stream
        _bump(p.metrics, name, total)
        p.notes.append(
            f"{stream}: {total} row(s) over {len(paths)} file(s); columns = {', '.join(header[:6])}"
            + ("…" if len(header) > 6 else "")
        )
    if len(streams) == 1:
        # a one-stream CSV source: expose the total under a neutral name too, so a
        # pack that calls it `entries` or `rows` can still be matched
        _bump(p.metrics, "records", sum(p.metrics.values()))
    if streams:
        first = sorted(streams.items())[0][1][0]
        p.sample.append(f"{first.name}: {_first_line(first)}")


def _probe_jsonl(p: Probe, files: list[Path]) -> None:
    shards = [f for f in files if f.suffix.lower() in (".jsonl", ".ndjson")]
    total = 0
    ids: set[str] = set()
    id_field: str | None = None
    sample_obj: dict[str, Any] | None = None
    for f in shards:
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                total += 1
                if sample_obj is None or id_field:
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if sample_obj is None:
                        sample_obj = obj
                        id_field = next(
                            (k for k in obj if k.lower().endswith("_id") and isinstance(obj[k], str)),
                            None,
                        )
                    if id_field and id_field in obj:
                        ids.add(str(obj[id_field]))
    _bump(p.metrics, "events", total)
    _bump(p.metrics, "shards", len(shards))
    if id_field and ids:
        dupes = total - len(ids)
        _bump(p.metrics, "duplicate_deliveries", dupes)
        p.notes.append(f"{total} event(s), {len(ids)} distinct {id_field}, {dupes} redelivered")
    # the cursor sits beside the shard directory, not inside it
    sidecars = list(files)
    if p.root is not None and p.root.parent.is_dir():
        sidecars += [f for f in p.root.parent.iterdir() if f.is_file()]
    for cursor in (f for f in sidecars if f.name.lower() == "cursor.json"):
        doc = json.loads(cursor.read_text(encoding="utf-8"))
        bits = [f"{k}={v}" for k, v in doc.items() if isinstance(v, (int, str, bool)) and k != "note"]
        p.notes.append("cursor.json: " + ", ".join(bits[:6]))
        gap = doc.get("known_gap")
        if isinstance(gap, dict):
            p.notes.append(f"declared cursor gap {gap.get('from')}–{gap.get('to')}: {gap.get('cause')}")
    if sample_obj is not None:
        p.sample.append(json.dumps(sample_obj)[:200] + "…")


def _parse_layout(root: Path) -> tuple[int | None, list[tuple[str, int, int]]]:
    """Read the shipped record layout (`LAYOUT.md`) rather than assuming offsets."""
    fields: list[tuple[str, int, int]] = []
    reclen: int | None = None
    for md in sorted(root.rglob("*.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        m = _RECLEN.search(text)
        if m and reclen is None:
            reclen = int(m.group(1))
        for line in text.splitlines():
            hit = _LAYOUT_ROW.match(line)
            if hit:
                fields.append((hit.group(1), int(hit.group(3)), int(hit.group(4))))
        if fields:
            break
    return reclen, fields


def _find_truth_csv(pack_dir: Path) -> Path | None:
    """A CSV that maps `source` + `local_key` to a ground-truth entity, if one ships."""
    for path in sorted(pack_dir.glob("*.csv")):
        try:
            with path.open(encoding="utf-8", newline="") as fh:
                header = next(csv.reader(fh), [])
        except OSError:
            continue
        low = {h.lower() for h in header}
        if "source" in low and "local_key" in low:
            return path
    return None


def _probe_fixed_width(p: Probe, files: list[Path]) -> None:
    assert p.root is not None
    reclen, fields = _parse_layout(p.root)
    data = [f for f in files if f.suffix.lower() in (".dat", ".txt", ".fix")]
    records = 0
    widths: set[int] = set()
    first: str | None = None
    for f in data:
        with f.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.rstrip("\n").rstrip("\r")
                if not line:
                    continue
                records += 1
                widths.add(len(line))
                if first is None:
                    first = line
    _bump(p.metrics, "records", records)
    stream = _stem_stream(data[0]) if data else ""
    if stream:
        _bump(p.metrics, stream, records)
    if reclen:
        p.notes.append(f"declared record length {reclen} bytes; observed {sorted(widths)[:4]}")
        if widths and widths != {reclen}:
            p.problems.append(
                f"{p.id}: record widths {sorted(widths)} do not all match the declared {reclen}"
            )
    if fields and first:
        decoded = [f"{name}={first[s - 1:e].strip()!r}" for name, s, e in fields[:6]]
        p.sample.append(f"{len(fields)} fields from LAYOUT.md · " + " ".join(decoded))

    # planted orphans: legacy keys that resolve to nobody in the ground-truth map
    truth = _find_truth_csv(p.source.pack_dir)
    key_field = next(((s, e) for name, s, e in fields if "KEY" in name.upper()), None)
    if truth and key_field and records:
        known: set[str] = set()
        with truth.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if _norm(str(row.get("source", ""))).startswith(_norm(p.id)):
                    known.add(str(row.get("local_key", "")).strip())
        if known:
            s, e = key_field
            orphans = 0
            for f in data:
                with f.open(encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if line.strip():
                            orphans += line[s - 1:e].strip() not in known
            rate = orphans / records
            declared = prose_orphan_rate(p.source.raw)
            p.notes.append(
                f"orphans against {truth.name}: {orphans}/{records} = {rate:.1%}"
                + (f" (estate declares ~{declared:.0%})" if declared is not None else "")
            )
            if declared is not None and abs(rate - declared) > max(0.02, declared * 0.30):
                p.problems.append(
                    f"{p.id}: measured orphan rate {rate:.1%} is off the declared ~{declared:.0%}"
                )


def _probe_sidecars(p: Probe, files: list[Path]) -> None:
    sidecars = [f for f in files if f.suffix.lower() == ".json"]
    _bump(p.metrics, "sidecars", len(sidecars))
    index = p.source.index_csv()
    if index is not None:
        rows, _ = _csv_rows(index)
        p.notes.append(f"index {index.name}: {rows} row(s)")
        if rows != len(sidecars):
            p.problems.append(f"{p.id}: index lists {rows} but {len(sidecars)} sidecar(s) on disk")
    if sidecars:
        doc = json.loads(sidecars[0].read_text(encoding="utf-8"))
        p.sample.append(f"{sidecars[0].name}: keys = {', '.join(list(doc)[:8])}")


def _probe_capture_store(p: Probe, files: list[Path]) -> None:
    pages = [f for f in files if f.suffix.lower() == ".json" and f.name != "manifest.json"]
    _bump(p.metrics, "snapshot_pages", len(pages))

    # ids per (snapshot, collection). A capture store holds the SAME collection at
    # several instants; counting their union would silently inflate every collection
    # by whatever drifted between captures — which is the one thing this source exists
    # to demonstrate.
    per: dict[tuple[str, str], set[str]] = {}
    for f in pages:
        snapshot = f.parent.name
        collection = _stem_stream(f).rsplit("_page", 1)[0]
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            p.problems.append(f"{p.id}: {f.name} is not valid JSON")
            continue
        items = _records_in(doc)
        bucket = per.setdefault((snapshot, collection), set())
        for item in items:
            ident = next((str(item[k]) for k in item if k.lower().endswith("id")), None)
            bucket.add(ident or json.dumps(item, sort_keys=True))

    snapshots = sorted({snap for snap, _ in per})
    collections = sorted({coll for _, coll in per})
    latest = snapshots[-1] if snapshots else None
    for coll in collections:
        counts = {snap: len(per.get((snap, coll), set())) for snap in snapshots}
        if latest is not None:
            _bump(p.metrics, coll, counts[latest])
        p.variants[coll] = counts
        p.notes.append(
            f"{coll}: " + ", ".join(f"{s} → {n}" for s, n in counts.items())
            + (f"; {len(set().union(*(per[(s, coll)] for s in snapshots)))} distinct across all"
               if len(snapshots) > 1 else "")
        )
    if snapshots:
        p.notes.append(f"{len(snapshots)} snapshot(s): " + ", ".join(snapshots)
                       + f" — counts reported for the latest ({latest})")
    if pages:
        first = _records_in(json.loads(pages[0].read_text(encoding="utf-8")))
        if first:
            p.sample.append(f"{pages[0].parent.name}/{pages[0].name}: "
                            + json.dumps(first[0])[:180] + "…")


def _probe_api(p: Probe, files: list[Path]) -> None:
    p.notes.append(
        "live API — nothing is materialised in the repo; the snapshots that back its "
        "pointers are a separate capture_store source in this estate."
    )


_PROBES = {
    "document_drop": _probe_documents,
    "rfc5322_drop": _probe_mail,
    "csv": _probe_csv,
    "csv_export": _probe_csv,
    "jsonl_stream": _probe_jsonl,
    "fixed_width": _probe_fixed_width,
    "json_sidecar": _probe_sidecars,
    "capture_store": _probe_capture_store,
    "json_api": _probe_api,
}


# ---------------------------------------------------------------------------


def probe_source(spec: SourceSpec) -> Probe:
    root = spec.root()
    p = Probe(source=spec, root=root)
    handler = _PROBES.get(spec.kind)
    if handler is None:
        p.problems.append(f"{spec.id}: no probe for kind {spec.kind!r}")
        return p
    files = _walk(root) if root else []
    p.files = len(files)
    p.bytes = sum(f.stat().st_size for f in files)
    for f in files:
        _bump(p.extensions, f.suffix.lower() or "<none>", 1)
    if root is None and spec.kind != "json_api":
        p.problems.append(f"{spec.id}: no local root resolved from transport/entrypoint")
        return p
    handler(p, files)
    return p


def match_expectations(probes: list[Probe], expectations: dict[str, int]) -> dict[str, int]:
    """Attach declared counts to probe metrics; return the expectations left over."""
    remaining = dict(expectations)
    for p in probes:
        for key, expected in list(remaining.items()):
            if "." not in key:
                continue
            prefix, metric = key.split(".", 1)
            np, ns = _norm(prefix), _norm(p.id)
            if not (np and (np in ns or ns in np)):
                continue
            for name, observed in p.metrics.items():
                names = (name,) + ALIASES.get(name, ())
                if not _metric_matches(metric, names):
                    continue
                at = ""
                if observed != expected:
                    at = next(
                        (label for label, n in p.variants.get(name, {}).items() if n == expected),
                        "",
                    )
                    if at:
                        p.notes.append(
                            f"{key}: declares {expected}; that is this source at {at} "
                            f"(now {observed}) — the difference is the drift this source ships"
                        )
                        observed = expected
                if observed != expected:
                    p.problems.append(
                        f"{key}: probed {observed}, estate declares {expected} "
                        f"({observed - expected:+d})"
                    )
                p.matches.append(Match(key, observed, expected, at))
                remaining.pop(key, None)
                break
    return remaining


def probe_estate(estate: EstateSpec, expectations: dict[str, int]) -> tuple[list[Probe], dict[str, int]]:
    probes = [probe_source(s) for s in estate.non_relational]
    leftover = match_expectations(probes, expectations)
    return probes, leftover

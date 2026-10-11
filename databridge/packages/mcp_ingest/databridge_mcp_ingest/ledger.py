"""Append-only JSONL artefacts and the §5.3 event log.

`Ledger` creates its file on open, so a stage that excludes nothing still leaves an EMPTY file
(P3: governance outputs are written every run, including when empty — a stale file is never read
as current). Each record is ONE `write()` of one line plus a flush, so a reader tailing the file
never sees half a record and a crash loses at most the line being written (P9, P10).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import uuid
from pathlib import Path
from typing import Iterator, Optional


#: `password=…`, `token: …`, `api_key "…"`, `Authorization: Basic …` — a credential-NAMED key
#: followed by its value, in free text (an exception message, a server's error dump).
_CRED_KV = re.compile(
    r"(?i)(?P<k>\b[\w.-]*(?:pass(?:word|wd)?|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|"
    r"private[_-]?key|client[_-]?secret|authorization|credential)[\w.-]*[\"']?\s*[:=]\s*)"
    r"(?P<q>[\"']?)(?:(?:basic|bearer|token)\s+)?(?!\[REDACTED\])(?P<v>[^\s\"',;&)}\]]+)")


def scrub(text: object) -> str:
    """`text` with every recognisable credential masked, for ANY line ingestion persists or prints
    (ledgers, report, events, notes) — Codex f1 #5. The key shapes and URL userinfo come from
    `secret_scan.redact` (one rule set, not a copy); credential-NAMED `key=value` / `key: value`
    pairs are masked here. Masking over-reaches on purpose: a lost diagnostic word costs less than
    a leaked password."""
    out = str(text)
    try:
        from .secret_scan import redact
        out = redact(out)
    except Exception:                                                # noqa: BLE001
        pass
    return _CRED_KV.sub(lambda m: f"{m.group('k')}{m.group('q')}[REDACTED]", out)


#: An absolute server path left in free text after the known directories are made relative:
#: a drive path (``C:\\x\\y``, ``C:/x/y``), a UNC path, or a POSIX path of two or more segments
#: — including one right after a colon (``path:/opt/x``) and a ``file:`` URI — but never a URL's
#: path (a ``scheme://host`` precedes it; R-029 Codex f1 #8).
_ABS_PATH = re.compile(r"\bfile:/+[^\s'\"<>|*?,;()]*"
                       r"|(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)[^\s'\"<>|*?,;()]*"
                       r"|(?<![\w/.~-])/(?:[\w.-]+/)+[\w.-]+")


def _known_dirs() -> list[tuple[str, str]]:
    """``(server directory, what it is shown as)``: the deployment's own tree, shown relative to
    it. Anything else absolute is reduced to its last component (`public_text`)."""
    from . import paths as _paths                                  # noqa: PLC0415

    out: list[tuple[str, str]] = []
    for d, shown in ((_paths.DATABRIDGE_ROOT, ""),):
        try:
            full = str(Path(d).resolve())
        except OSError:
            continue
        out.append((full, shown))
    return sorted(out, key=lambda x: len(x[0]), reverse=True)


def public_text(text: object, roots: tuple = ()) -> str:
    """``text`` with no SERVER path in it (R-029 TIDY2): a path under one of the source's
    ``roots`` is shown relative to that root, one under the deployment's own tree relative to it,
    and any other absolute path by its last component (``.../name``).
    For the free text a person reads back (a run report's notes and message); never for an
    identity or locator a stage reads (those stay as the source names them)."""
    out = str(text)
    pairs = [(str(r), "") for r in roots if isinstance(r, str) and r.strip()] + _known_dirs()
    for full, shown in sorted(pairs, key=lambda x: len(x[0]), reverse=True):
        for form in {full, full.replace("\\", "/")}:
            if not form:
                continue
            out = re.sub(re.escape(form.rstrip("\\/")) + r"[\\/]?",
                         lambda _m, s=shown: s, out, flags=re.IGNORECASE)
    return _ABS_PATH.sub(lambda m: ".../" + re.split(r"[\\/]", m.group(0).rstrip("\\/"))[-1],
                         out)


def _scrub_value(value: object) -> object:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_value(v) for v in value]
    return value


#: Fields of a ledger record that carry a LOCATOR or free text from the source or an error (a URI,
#: a relative path, a title, an error / detail line, source-native attributes): every string under
#: them is `scrub`bed at write time (Codex f2 #4), so a credential in a signed URL or `user:pw@`
#: userinfo never reaches a permanent artefact. Hashes, ids and counts are left exactly as given.
LOCATOR_KEYS = frozenset({"uri", "rel", "name", "title", "roots", "attrs", "error", "detail",
                          "note", "reverse", "message"})


def scrub_locators(record: dict) -> dict:
    """`record` with every value under a `LOCATOR_KEYS` key scrubbed (recursively)."""
    return {k: (_scrub_value(v) if k in LOCATOR_KEYS else v) for k, v in record.items()}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Ledger:
    """One JSONL artefact. With `model` (a `records.py` model — `typed_ledger` picks it from the
    file name), every record is built THROUGH that model before it is written, so a writer can
    never emit a line its readers (`records.read_run`) would reject: a bad record raises
    `records.ArtefactError` at write time instead of degrading a consumer silently (R-017)."""

    def __init__(self, path: Path, *, truncate: bool = False, model=None) -> None:
        self.path = path
        self.model = model
        path.parent.mkdir(parents=True, exist_ok=True)
        if not truncate:
            _quarantine_torn_tail(path)
        self._fh = open(path, "wb" if truncate else "ab")
        self._fh.flush()
        self.count = 0

    def write(self, record: dict) -> None:
        record = scrub_locators(record)
        if self.model is not None:
            from .records import line as typed_line

            record = typed_line(self.model, record)
        line = json.dumps(record, sort_keys=True, default=str).encode("utf-8") + b"\n"
        self._fh.write(line)
        self._fh.flush()
        self.count += 1

    def sync(self) -> None:
        """Make every line written so far DURABLE (flush + fsync) — called before a stage is
        marked done, so a durable "done" marker never outlives its ledger's tail (Codex f2 #14)."""
        if not self._fh.closed:
            self._fh.flush()
            os.fsync(self._fh.fileno())

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._fh.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def typed_ledger(path: Path, *, truncate: bool = False) -> Ledger:
    """A `Ledger` typed by its file stem (`excluded.drift.jsonl` → `ExclusionRecord`); a ledger
    with no typed format yet is written as is."""
    from .records import model_for_ledger

    return Ledger(path, truncate=truncate, model=model_for_ledger(path.stem))


NL = chr(10).encode()


def _quarantine_torn_tail(path: Path) -> Optional[Path]:
    """A crash mid-`write()` can leave a final line with no newline. Appending after it would
    weld the next record onto the fragment and corrupt the MIDDLE of the file, so before a ledger
    is reopened for append the unterminated fragment is MOVED (never dropped) to
    `<name>.torn.<n>` and the ledger cut back to its last complete line (Codex f2 #14)."""
    if not path.is_file():
        return None
    data = path.read_bytes()
    if not data or data.endswith(NL):
        return None
    cut = data.rfind(NL) + 1
    n = 1
    while (dest := path.with_name(f"{path.name}.torn.{n}")).exists():
        n += 1
    dest.write_bytes(data[cut:])
    with open(path, "r+b") as fh:
        fh.truncate(cut)
        fh.flush()
        os.fsync(fh.fileno())
    return dest


def read_jsonl(path: Path) -> list[dict]:
    """Every record of a JSONL file. An UNTERMINATED final line that does not parse is a write
    torn by a crash: it is skipped (and quarantined when the ledger is next opened). A bad line
    anywhere else is real damage and raises."""
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    lines = text.split(NL.decode())
    out = []
    for n, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            if n == len(lines) - 1:              # the last piece, with no newline after it
                break
            raise
    return out


def iter_jsonl(path: Path) -> Iterator[dict]:
    if not path.is_file():
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


# --- §5.3 events ---------------------------------------------------------------------------------
EVENT_TYPES = frozenset({"item_added", "item_changed", "item_removed", "run_completed",
                         "run_failed"})


def events_path() -> Path:
    from .paths import DATABRIDGE_ROOT

    value = os.environ.get("WALNUT_EVENTS_PATH")
    return Path(value) if value else DATABRIDGE_ROOT / "var" / "events" / "events.jsonl"


_EVENT_NS = uuid.UUID("6f1c2e1a-5b8e-4c1e-9a53-0c2d9e7f4b11")


def item_event_id(type_: str, *, source_id: str, run_id: str, item_id: Optional[str],
                  content_hash: Optional[str]) -> str:
    """A DETERMINISTIC id for an item event: the same (type, source, run, item, content) is the
    same event, so a resumed run that re-emits it can be recognised (and skipped) and a consumer
    can de-duplicate by id (Codex f1 #6)."""
    return str(uuid.uuid5(_EVENT_NS, "|".join(str(x) for x in (type_, source_id, run_id,
                                                               item_id, content_hash))))


def event_ids_for_run(run_id: str, path: Optional[Path] = None) -> set[str]:
    """Ids of the events already in the log for one run (a resumed run skips these)."""
    target = path or events_path()
    out: set[str] = set()
    for r in iter_jsonl(target):
        if r.get("run_id") == run_id and r.get("event_id"):
            out.add(r["event_id"])
    return out


def emit_event(type_: str, *, source_id: str, run_id: str, item_id: Optional[str] = None,
               uri: Optional[str] = None, content_hash: Optional[str] = None,
               attrs: Optional[dict] = None, path: Optional[Path] = None,
               event_id: Optional[str] = None) -> dict:
    """Append one event line (BUILD_PLAN §5.3): a single write of one line + newline + flush.
    Every string in `attrs` is `scrub`bed — a caller's attrs or an error text never carry a
    credential into the log."""
    if type_ not in EVENT_TYPES:
        raise ValueError(f"unknown event type {type_!r}; known: {sorted(EVENT_TYPES)}")
    record = {"event_id": event_id or str(uuid.uuid4()), "ts": now_iso(), "type": type_,
              "source_id": source_id, "item_id": item_id,
              "uri": scrub(uri) if uri is not None else None,          # Codex f2 #4
              "content_hash": (f"sha256:{content_hash}" if content_hash
                               and not content_hash.startswith("sha256:") else content_hash),
              "run_id": run_id, "attrs": _scrub_value(dict(attrs or {}))}
    target = path or events_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, default=str).encode("utf-8") + b"\n"
    fd = os.open(str(target), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0),
                 0o644)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)
    return record


__all__ = ["Ledger", "typed_ledger", "read_jsonl", "iter_jsonl", "now_iso", "emit_event", "events_path",
           "EVENT_TYPES", "scrub", "item_event_id", "event_ids_for_run", "LOCATOR_KEYS",
           "scrub_locators"]

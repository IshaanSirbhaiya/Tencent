"""Read-only access to the two artefacts ingestion leaves on disk: the capture store and the run
ledgers. Used by ``/evidence`` (the captured snippet behind a pointer, and a re-verify of it) and by
``/intelligence`` (coverage and exclusion figures per source).

Nothing here writes, and nothing here decides what may be shown — that is the caller's policy
(``subject_exposure``). This module only answers "what does the disk say?", and says "nothing" when
the disk says nothing: a missing capture is reported as missing, never papered over.

**Layout (the ingestion contract).**

* Capture store, content-addressed, rooted at ``WALNUT_CAPTURE_DIR`` (default ``var/capture``)::

      <sha[:2]>/<sha256>        the raw bytes as retrieved
      <sha[:2]>/<sha256>.txt    the extracted text that char offsets index into
      <sha[:2]>/<sha256>.json   {source, uri, mime, title, retrieved_at}

* Run ledgers, rooted at ``WALNUT_INGEST_DIR`` (default ``var/ingest``)::

      <source>/<run_id>/report.json
      <source>/<run_id>/*.jsonl  (inventory, excluded, blocked, governance, …)

**Ledger files are found by pattern, never by a list of names** (KNOWLEDGE_SYSTEM_PRINCIPLES P3):
a hard-coded list goes stale the day a stage writes a tagged variant (``excluded_ocr.jsonl``), and
the register then under-reports with no error. Every ``*.jsonl`` in a run directory is read and
classified by the stem it starts with.

**A digest is validated before it touches a path.** The digest comes from a pointer, and a pointer
can come from anywhere; a digest that is not 64 hex characters is refused rather than joined onto
the capture root, so ``../`` can never reach the filesystem through here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

#: Characters of context shown either side of a cited span. Enough to read the span in its
#: sentence; not so much that a snippet becomes a copy of the document.
SNIPPET_CONTEXT = 80

#: The ledger families a run directory can hold, keyed by the stem prefix that marks them. A file
#: whose stem matches none of these is still counted (under ``other``) — an unrecognised ledger is
#: reported, never skipped.
LEDGER_FAMILIES = ("inventory", "excluded", "blocked", "governance", "extract_skipped",
                   "build_skipped")

#: Fields an exclusion row may state its reason under. Read in this order; the first present wins.
_REASON_FIELDS = ("rule", "reason", "rule_id", "why")

#: Upper bound on rows read from one ledger file. A register larger than this is summarised from
#: the first rows and the truncation is SAID (``truncated: true``), never hidden.
MAX_LEDGER_ROWS = 50_000


def databridge_root() -> Path:
    """``databridge/`` — three levels above this package directory."""
    return Path(__file__).resolve().parents[3]


def capture_dir() -> Path:
    raw = os.environ.get("WALNUT_CAPTURE_DIR", "").strip()
    return Path(raw) if raw else databridge_root() / "var" / "capture"


def ingest_dir() -> Path:
    raw = os.environ.get("WALNUT_INGEST_DIR", "").strip()
    return Path(raw) if raw else databridge_root() / "var" / "ingest"


def normalise_digest(digest: Optional[str]) -> Optional[str]:
    """``"sha256:ABC…"`` → ``"abc…"``, or ``None`` if it is not a sha256 content address."""
    if not digest or not isinstance(digest, str):
        return None
    text = digest.strip().lower()
    if text.startswith("sha256:"):
        text = text[len("sha256:"):]
    return text if _HEX64.match(text) else None


# --- the capture store -------------------------------------------------------------------------


@dataclass(frozen=True)
class Capture:
    """What the capture store holds for one digest. Every part may be absent, and says so."""

    digest: str
    bytes_path: Optional[Path]
    text_path: Optional[Path]
    meta: dict = field(default_factory=dict)
    #: why the metadata was NOT trusted (it failed `capture.get_meta`'s digest check — swapped or
    #: edited). When set, ``meta`` is empty: nothing from a tampered file reaches a caller.
    meta_problem: Optional[str] = None

    @property
    def present(self) -> bool:
        return self.bytes_path is not None or self.text_path is not None

    def text(self) -> Optional[str]:
        if self.text_path is not None:
            try:
                return self.text_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return None
        mime = str(self.meta.get("mime") or "")
        if self.bytes_path is not None and (mime.startswith("text/") or not mime):
            # No extracted text was written, but the bytes ARE text: offsets index into them.
            try:
                return self.bytes_path.read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError):
                return None
        return None

    def bound_text(self) -> tuple[Optional[str], Optional[str]]:
        """``(text, binding)`` — the text offsets index into, and HOW it is tied to the digest.

        The digest authenticates the raw bytes only. ``<digest>.txt`` is a separate file, so a
        span read from it is authenticated only when the text is provably the bytes' own:

        * ``"bytes_are_text"`` — no text file, or the text file equals the bytes decoded as UTF-8
          (a leading BOM and CRLF line endings are the only differences tolerated — a deterministic
          regeneration, not a judgement);
        * ``None`` — the text exists but nothing ties it to the hashed bytes (R-017 t08 #5).

        A ``text_sha256`` recorded in the ``<digest>.json`` meta file is NOT a binding (R-019
        t08 #6): the meta file lives beside the text in the same mutable folder and nothing the
        capture digest or the pointer carries authenticates it, so rewriting the text and its
        recorded hash together would read as ``verified``. Derived text (a PDF's extraction) is
        therefore ``text_unbound`` until its digest is anchored where the pointer can vouch for
        it — see ``wiki/40-reviews/R-019-evidence-intelligence-r2.md``.
        """
        if self.text_path is None:
            text = self.text()
            return text, ("bytes_are_text" if text is not None else None)
        text = self.text()
        if text is None:
            return None, None
        if self.bytes_path is not None:
            try:
                decoded = self.bytes_path.read_bytes().decode("utf-8-sig")
            except (OSError, UnicodeDecodeError):
                decoded = None
            if decoded is not None and (decoded == text or
                                        decoded.replace("\r\n", "\n") == text):
                return text, "bytes_are_text"
        return text, None

    def recompute_digest(self) -> Optional[str]:
        if self.bytes_path is None:
            return None
        h = hashlib.sha256()
        try:
            with self.bytes_path.open("rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    h.update(block)
        except OSError:
            return None
        return h.hexdigest()

    def source_meta(self) -> dict:
        """The published subset of the meta file — a closed set of keys, never the whole file."""
        return {k: self.meta.get(k) for k in ("source", "uri", "mime", "title", "retrieved_at")
                if self.meta.get(k) is not None}


def read_capture(digest: Optional[str], root: Optional[Path] = None) -> Optional[Capture]:
    """The capture for a digest, or ``None`` when the digest is not a content address."""
    d = normalise_digest(digest)
    if d is None:
        return None
    cap_root = root or capture_dir()
    base = cap_root / d[:2]
    raw, txt = base / d, base / f"{d}.txt"
    meta, problem = _read_meta(d, cap_root)
    return Capture(digest=d, bytes_path=raw if raw.is_file() else None,
                   text_path=txt if txt.is_file() else None, meta=meta, meta_problem=problem)


def _read_meta(digest: str, root: Path) -> tuple[dict, Optional[str]]:
    """The capture's metadata through the capture store's own reader
    (`databridge_mcp_ingest.capture.get_meta`), which checks its `meta_digest` against the content
    address. ``({}, reason)`` when it fails — never the tampered values, never an exception. A
    legacy file without a digest reads as before; no file reads as ``{}``."""
    from databridge_mcp_ingest.capture import CaptureCorrupt, get_meta  # noqa: PLC0415

    try:
        meta = get_meta(digest, root)
    except CaptureCorrupt as exc:
        return {}, str(exc)
    except OSError as exc:
        return {}, f"capture metadata could not be read ({exc})"
    return (meta if isinstance(meta, dict) else {}), None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def value_in_text(value: Any, text: str) -> Optional[bool]:
    """Does the cited text still contain this value? ``None`` when there is nothing to look for.

    Whitespace-normalised and case-folded — the same leniency the grounding wall applies to an
    exact match — and applied to scalars only. A structured value has no single rendering to find.
    """
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    needle = _norm(str(value))
    if not needle:
        return None
    return needle in _norm(text)


def snippet_and_verify(pointer: Any, *, value: Any = None,
                       root: Optional[Path] = None) -> Optional[dict]:
    """The captured snippet behind one document-span pointer, and a re-verification of it.

    Returns ``None`` for a pointer that carries no capture binding — the caller reports that
    honestly as "this pointer names no captured bytes". Otherwise a dict:

    * ``snippet`` — ``{before, match, after, char_start, char_end}``; ``match`` is exactly the
      cited span, so a renderer highlights what was cited and nothing else.
    * ``source`` — uri / title / retrieved_at / mime / source from the capture's meta file.
    * ``verify`` — ``status`` is one of ``verified`` · ``hash_mismatch`` · ``value_not_in_span`` ·
      ``capture_missing`` · ``capture_meta_corrupt`` · ``span_out_of_range`` · ``no_text`` · ``no_bytes`` ·
      ``text_unbound``; plus the checks separately (``hash_matches``, ``value_in_span``,
      ``text_bound`` / ``text_binding``, ``value_checked``) so a reader sees which half failed.
      ``verified`` means the bytes still hash to the digest AND the text the span was read from
      is bound to those bytes (``Capture.bound_text``); ``value_checked`` says whether a value was
      also found in the span (a structured value has no single rendering to look for).

    The signature and the return shape are a contract (``verify_fleet.verify_by_capture`` reuses
    them): keys are only ever ADDED here.
    """
    digest = getattr(pointer, "capture_digest", None)
    capture = read_capture(digest, root)
    if capture is None:
        return None
    out: dict = {"capture_digest": f"sha256:{capture.digest}",
                 "source": capture.source_meta(), "snippet": None}
    if not capture.present:
        out["verify"] = {"status": "capture_missing", "hash_matches": None, "value_in_span": None,
                         "detail": "the pointer names a capture that is not in the capture store"}
        return out
    if capture.meta_problem is not None:
        # the metadata beside the bytes was swapped or edited: the capture is not trusted, so no
        # snippet is shown from it and no source is published (INT1)
        out["verify"] = {"status": "capture_meta_corrupt", "hash_matches": None,
                         "value_in_span": None, "value_checked": False,
                         "detail": "the capture's metadata failed its digest check — it was "
                                   f"swapped or edited, so nothing from it is shown "
                                   f"({capture.meta_problem})"}
        return out

    recomputed = capture.recompute_digest()
    hash_matches = None if recomputed is None else recomputed == capture.digest
    text, binding = capture.bound_text()
    start, end = getattr(pointer, "char_start", None), getattr(pointer, "char_end", None)
    verify: dict = {"hash_matches": hash_matches, "value_in_span": None,
                    "value_checked": False,
                    "text_bound": binding is not None, "text_binding": binding,
                    "recomputed_digest": f"sha256:{recomputed}" if recomputed else None}
    if text is None:
        verify["status"] = "hash_mismatch" if hash_matches is False else "no_text"
        verify["detail"] = "no extracted text was captured, so the span cannot be read back"
        out["verify"] = verify
        return out
    if start is None or end is None or start < 0 or end > len(text) or start > end:
        verify["status"] = "hash_mismatch" if hash_matches is False else "span_out_of_range"
        verify["detail"] = (f"the pointer's span [{start}, {end}) does not fit the captured text "
                            f"({len(text)} characters)")
        out["verify"] = verify
        return out

    match = text[start:end]
    out["snippet"] = {
        "before": text[max(0, start - SNIPPET_CONTEXT):start],
        "match": match,
        "after": text[end:end + SNIPPET_CONTEXT],
        "char_start": start, "char_end": end,
        "truncated_before": start > SNIPPET_CONTEXT,
        "truncated_after": end + SNIPPET_CONTEXT < len(text),
    }
    verify["value_in_span"] = value_in_text(value, match)
    verify["value_checked"] = verify["value_in_span"] is not None
    if hash_matches is False:
        verify["status"] = "hash_mismatch"
        verify["detail"] = ("the captured bytes no longer hash to the digest this pointer "
                            "recorded — the capture changed after it was cited")
    elif verify["value_in_span"] is False:
        verify["status"] = "value_not_in_span"
        verify["detail"] = "the cited span does not contain the value it is cited for"
    elif hash_matches is None:
        verify["status"] = "no_bytes"
        verify["detail"] = ("the extracted text is present but the raw bytes are not, so the "
                            "content hash cannot be recomputed")
    elif binding is None:
        verify["status"] = "text_unbound"
        verify["detail"] = ("the captured bytes still hash to the recorded digest, but the text "
                            "this span was read from is not the bytes' own and records no digest "
                            "of its own — the hash does not vouch for the highlighted text")
    else:
        verify["status"] = "verified"
        verify["detail"] = ("the captured bytes still hash to the recorded digest"
                            + (" and the span contains the value"
                               if verify["value_in_span"] else ""))
    out["verify"] = verify
    return out


# --- the run ledgers ---------------------------------------------------------------------------


def _family(stem: str) -> str:
    for family in LEDGER_FAMILIES:
        if stem == family or stem.startswith(family + "_") or stem.startswith(family + "-"):
            return family
    return "other"


def _read_jsonl(path: Path) -> tuple[list[dict], bool]:
    rows: list[dict] = []
    truncated = False
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                if len(rows) >= MAX_LEDGER_ROWS:
                    truncated = True
                    break
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return [], False
    return rows, truncated


#: Quoted text inside a free-text reason is material a source supplied — an entity name, a
#: filename, a cell value (`chunk 3: entity 'Alice Tan': unknown kind`). The register publishes
#: the reason's shape and never the quote (R-019 t08 #7).
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"|\[[^\]]*\]")


def _reason_of(row: dict) -> str:
    for key in _REASON_FIELDS:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return _QUOTED.sub("'…'", value.strip())
    return "no reason recorded"


#: `report.json` keys the register publishes as text. Everything else a run writes as a string (a
#: free-text `message` quoting extracted entities, a filesystem `run_dir`, a title) can carry what
#: a document said, so only these closed-vocabulary strings — and numbers/booleans — travel
#: (R-019 t08 #7).
REPORT_TEXT_KEYS = frozenset({"run_id", "source_id", "status", "started_at", "finished_at"})

#: The one screen decision that lets an item through. Every OTHER decision the governance ledger
#: records (block, review, and any the screen adds later) kept the item out of the build and is
#: counted as an exclusion (E2E F-14) — a new decision is held until someone says otherwise.
PASS_DECISION = "allow"


def _screen_rules(row: dict) -> tuple[str, ...]:
    """The rule ids a governance row says fired — ids from the pack's screen, never content."""
    fired = row.get("fired")
    rules: list[str] = []
    if isinstance(fired, list):
        for item in fired:
            rule = item.get("rule") if isinstance(item, dict) else None
            if isinstance(rule, str) and rule:
                rules.append(rule)
    return tuple(rules)


def _row_strings(row: Any) -> Iterable[str]:
    if isinstance(row, dict):
        for value in row.values():
            yield from _row_strings(value)
    elif isinstance(row, list):
        for value in row:
            yield from _row_strings(value)
    elif isinstance(row, str):
        yield row


@dataclass
class IngestRun:
    source: str
    run_id: str
    path: Path
    report: dict = field(default_factory=dict)
    ledgers: dict[str, list[dict]] = field(default_factory=dict)
    ledger_files: list[str] = field(default_factory=list)
    truncated: bool = False

    def digests(self) -> set[str]:
        """Every content address named anywhere in this run's inventory rows."""
        out: set[str] = set()
        for row in self.ledgers.get("inventory", []):
            for text in _row_strings(row):
                d = normalise_digest(text)
                if d:
                    out.add(d)
        return out

    def summary(self) -> dict:
        counts = {family: len(rows) for family, rows in sorted(self.ledgers.items())}
        exclusions: dict[str, int] = {}
        for family in ("excluded", "blocked", "extract_skipped", "build_skipped"):
            for row in self.ledgers.get(family, []):
                reason = f"{family}: {_reason_of(row)}"
                exclusions[reason] = exclusions.get(reason, 0) + 1
        # The ingestion SCREEN records its block / review decisions in the governance ledger, not
        # in `excluded`/`blocked` (E2E F-14): a run whose screen held four items read "0 excluded".
        # Counted as exclusions, and listed per (decision, rules) with the pack's reverse text.
        screen: dict[tuple, dict] = {}
        for row in self.ledgers.get("governance", []):
            try:
                decision = row["decision"]
            except KeyError:
                continue
            if not isinstance(decision, str) or not decision or decision == PASS_DECISION:
                continue
            rules = _screen_rules(row)
            reason = f"screen {decision}: {', '.join(rules) or 'no rule recorded'}"
            exclusions[reason] = exclusions.get(reason, 0) + 1
            reverse = row.get("reverse") if isinstance(row.get("reverse"), str) else ""
            entry = screen.setdefault((decision, rules, reverse), {
                "decision": decision, "rules": list(rules), "reverse": reverse, "n": 0})
            entry["n"] += 1
        report = {k: v for k, v in self.report.items()
                  if (k in REPORT_TEXT_KEYS and (v is None or isinstance(v, str)))
                  or isinstance(v, (int, float))}          # bool is an int: kept
        return {
            "source": self.source, "run_id": self.run_id,
            "ledger_files": sorted(self.ledger_files),
            "counts": counts,
            "excluded": sum(n for n in exclusions.values()),
            "exclusion_reasons": [{"reason": r, "n": n}
                                  for r, n in sorted(exclusions.items(), key=lambda x: (-x[1], x[0]))],
            "screen_decisions": sorted(screen.values(),
                                       key=lambda d: (-d["n"], d["decision"], d["rules"])),
            "report": report,
            "truncated": self.truncated,
        }


def _safe_name(name: str) -> bool:
    return bool(name) and name not in (".", "..") and "/" not in name and "\\" not in name


def list_runs(root: Optional[Path] = None, source: Optional[str] = None) -> list[IngestRun]:
    """Every ingestion run on disk, newest run id last within each source. Empty when none."""
    base = root or ingest_dir()
    if source is not None and not _safe_name(source):
        return []
    if not base.is_dir():
        return []
    runs: list[IngestRun] = []
    sources = [base / source] if source is not None else sorted(p for p in base.iterdir()
                                                                  if p.is_dir())
    for source_dir in sources:
        if not source_dir.is_dir():
            continue
        for run_dir in sorted(p for p in source_dir.iterdir() if p.is_dir()):
            run = IngestRun(source=source_dir.name, run_id=run_dir.name, path=run_dir)
            report_path = run_dir / "report.json"
            if report_path.is_file():
                try:
                    loaded = json.loads(report_path.read_text(encoding="utf-8"))
                    run.report = loaded if isinstance(loaded, dict) else {}
                except (OSError, ValueError):
                    run.report = {"unreadable": True}
            for ledger in sorted(run_dir.glob("*.jsonl")):
                rows, truncated = _read_jsonl(ledger)
                run.ledger_files.append(ledger.name)
                run.ledgers.setdefault(_family(ledger.stem), []).extend(rows)
                run.truncated = run.truncated or truncated
            runs.append(run)
    return runs


def source_summaries(root: Optional[Path] = None) -> list[dict]:
    """Per source: its runs, the latest run's counts, and every exclusion reason with a count."""
    by_source: dict[str, list[IngestRun]] = {}
    for run in list_runs(root):
        by_source.setdefault(run.source, []).append(run)
    out = []
    for source, runs in sorted(by_source.items()):
        latest = runs[-1].summary()
        out.append({"source": source, "runs": len(runs),
                    "run_ids": [r.run_id for r in runs], "latest": latest})
    return out


__all__ = [
    "Capture", "IngestRun", "LEDGER_FAMILIES", "SNIPPET_CONTEXT", "capture_dir", "ingest_dir",
    "list_runs", "normalise_digest", "read_capture", "snippet_and_verify", "source_summaries",
    "value_in_text",
]

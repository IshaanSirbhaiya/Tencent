"""Tamper scenarios on a COPY of an access ledger (decision D-19, plan v2 B11).

Each scenario copies a ledger var directory (``ledger/`` + ``anchors/``) and the external head file
into its own folder, tampers with that copy the way an attacker would, and records what
`AccessLedger.verify()` reports: internally (chain, seals, the anchor ledger in its own directory)
and against the latest externally published head.

Two callers:

* ``scripts/tamper_demo.py`` (command line; builds a synthetic demo ledger, or uses ``--var``);
* ``POST /audit/tamper-demo`` (compliance officer): `run_on_live_copy` snapshots the LIVE ledger
  under its append lock into a temporary directory, runs the scenarios on that snapshot, deletes the
  directory and confirms that every byte of the live files that existed at snapshot time is
  unchanged. The live ledger is never opened for writing here.

Scenarios (``expected`` = whether verification must flag it):

  baseline                    untouched copy                                      not flagged
  edit-record                 one historical field changed, not re-sealed         flagged (chain)
  suffix-delete               the last N records deleted                          flagged (anchor)
  directory-rollback          log + anchors restored from an earlier backup        flagged ONLY by
                              (before the last external publication)               the external head
  rollback-after-publication  the same, to a point AFTER the last publication      NOT flagged: the
                                                                                    stated window
  remove-internal-anchors     the anchor ledger file deleted                       flagged
  external-head-missing       the external head file lost                          flagged
  key-holder-rewrite          an insider WITH the key edits a record, re-seals     flagged ONLY by
                              the chain and rebuilds the anchors                   the external head
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from databridge_core.decision_ledger import digest_of, seal_of

from ..config import IbConfig
from .anchor import FileSink
from .ledger import LOG_ID, AccessLedger

#: (id, title, expected to be flagged)
SCENARIOS: tuple[tuple[str, str, bool], ...] = (
    ("baseline", "Untouched copy", False),
    ("edit-record", "Edit one old record", True),
    ("suffix-delete", "Delete the newest records", True),
    ("directory-rollback", "Restore an old backup of log and anchors", True),
    ("rollback-after-publication", "Roll back after the last external publication", False),
    ("remove-internal-anchors", "Delete the internal anchor ledger", True),
    ("external-head-missing", "Lose the external head", True),
    ("key-holder-rewrite", "Rewrite history with the sealing key", True),
)
_META = {sid: (title, exp) for sid, title, exp in SCENARIOS}
_RECORD_N = re.compile(r"^record (\d+):")
#: one live run at a time (each run copies the ledger once per scenario)
_RUN_LOCK = threading.Lock()


class TamperDemoBusy(RuntimeError):
    """Another tamper demo is running in this process."""


# ------------------------------------------------------------------------------------------------
# file helpers (the attacker's tools)
# ------------------------------------------------------------------------------------------------
def _lines(p: Path) -> list[str]:
    return [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _write_lines(p: Path, lines: list[str]) -> None:
    p.write_text("".join(ln + "\n" for ln in lines), encoding="utf-8", newline="\n")


def _log(var: Path) -> Path:
    return var / "ledger" / "access.jsonl"


def _anchors(var: Path) -> Path:
    return var / "anchors" / "access.anchors.jsonl"


def restore_prefix(var: Path, keep: int) -> None:
    """Make ``var`` look exactly like a backup taken when the log held ``keep`` records: both
    append-only files are cut back to that moment (log to ``keep`` records, the anchor ledger to the
    line that anchored seq ``keep``)."""
    log, anc = _log(var), _anchors(var)
    _write_lines(log, _lines(log)[:keep])
    out = []
    for ln in _lines(anc):
        out.append(ln)
        rec = json.loads(ln)
        if rec.get("log_id") == LOG_ID and int(rec.get("seq", 0)) == keep:
            break
    _write_lines(anc, out)


def reseal(var: Path, key: bytes, mutate: Callable[[list[dict]], list[dict]]) -> None:
    """A KEY HOLDER's rewrite: change records, re-seal the whole chain with the key and regenerate
    the internal anchor ledger so that everything verifies internally."""
    log, anc = _log(var), _anchors(var)
    recs = mutate([json.loads(ln) for ln in _lines(log)])
    head, out = "genesis", []
    for i, r in enumerate(recs, start=1):
        r = {k: v for k, v in r.items() if k not in ("seq", "prev", "hash", "seal")}
        r["seq"], r["prev"] = i, head
        r["hash"] = digest_of(r, exclude=("hash", "seal"))
        r["seal"] = seal_of(key, r["hash"])
        head = r["hash"]
        out.append(json.dumps(r, sort_keys=True, default=str))
    _write_lines(log, out)
    a = {"log_id": LOG_ID, "seq": len(recs), "head": head,
         "at": dt.datetime.now(dt.timezone.utc).isoformat(), "lseq": 1, "lprev": ""}
    a["record_digest"] = digest_of(a, exclude=("record_digest", "seal"))
    a["seal"] = seal_of(key, a["record_digest"])
    _write_lines(anc, [json.dumps(a, sort_keys=True)])


def _verify(var: Path, ext: Optional[Path], key: str) -> dict:
    cfg = IbConfig(var_root=var, ledger_key=key)
    ledger = AccessLedger(cfg, external=FileSink(ext) if ext is not None else None)
    return ledger.verify().as_dict()


def _first_bad_seq(rep: dict) -> Optional[int]:
    """The first record the chain check rejected (a ``record N: ...`` problem), else None."""
    for p in rep.get("problems") or []:
        m = _RECORD_N.match(str(p))
        if m:
            return int(m.group(1))
    return None


# ------------------------------------------------------------------------------------------------
# the scenarios
# ------------------------------------------------------------------------------------------------
def run_scenarios(src_var: Path, external: Optional[Path], key: str, *, work: Path,
                  suffix_n: int = 2, budget_s: Optional[float] = None,
                  scrub: Optional[str] = None) -> list[dict]:
    """Run every scenario on its own copy of ``src_var`` (+ ``external``); ``src_var`` is never
    written. Returns one dict per scenario::

        {id, title, what_we_did, expected ("flagged"|"not flagged"), expected_detected,
         detected, ok (the outcome matched the expectation), verify_ok, internal_ok,
         external_head_match, records, problems, first_bad_seq?}

    ``budget_s``: scenarios not started within this many seconds are reported with
    ``skipped: true`` (and ``ok: false``) instead of running. ``scrub``: a path prefix replaced
    with ``<copy>`` in problem text."""
    work = Path(work)
    t0 = time.monotonic()
    log_lines = _lines(_log(src_var))
    n = len(log_lines)
    pub = FileSink(external).latest(LOG_ID) if external is not None and external.is_file() else None
    pub_seq = int(pub["seq"]) if pub else 0
    k = max(1, min(n - 1, pub_seq // 2 or n // 2))
    results: list[dict] = []

    def scenario(sid: str, how: str, tamper: Callable[[Path, Optional[Path]], Optional[Path]]) -> None:
        title, expect = _META[sid]
        base = {"id": sid, "title": title, "what_we_did": how,
                "expected": "flagged" if expect else "not flagged", "expected_detected": expect}
        if budget_s is not None and time.monotonic() - t0 > budget_s:
            results.append({**base, "skipped": True, "detected": None, "ok": False,
                            "problems": ["not run: the time budget was used up"]})
            return
        d = work / sid
        var = d / "var"
        shutil.copytree(src_var, var)
        ext: Optional[Path] = None
        if external is not None:
            ext = d / "external" / external.name
            ext.parent.mkdir(parents=True)
            if external.is_file():
                shutil.copy2(external, ext)
        used = tamper(var, ext)
        rep = _verify(var, used if used is not None else ext, key)
        detected = not rep["ok"]
        problems = [str(p) for p in rep.get("problems") or []]
        if scrub:
            problems = [p.replace(scrub, "<copy>") for p in problems]
        row = {**base, "detected": detected, "ok": detected == expect, "verify_ok": rep["ok"],
               "internal_ok": rep.get("internal_ok"),
               "external_head_match": rep.get("external_head_match"),
               "records": rep.get("records"), "problems": problems}
        fb = _first_bad_seq(rep)
        if fb is not None:
            row["first_bad_seq"] = fb
        results.append(row)
        shutil.rmtree(d, ignore_errors=True)                 # keep the work folder small

    scenario("baseline", "Copied the log and changed nothing.", lambda v, e: None)

    def edit(v: Path, e: Optional[Path]) -> None:
        p = _log(v)
        lines = _lines(p)
        rec = json.loads(lines[k - 1])
        rec.setdefault("ib_event", {})["sub"] = "someone-else"
        lines[k - 1] = json.dumps(rec, sort_keys=True)
        _write_lines(p, lines)
    scenario("edit-record", f"Changed who made record {k} (ib_event.sub) without re-sealing it.",
             edit)

    def suffix(v: Path, e: Optional[Path]) -> None:
        p = _log(v)
        _write_lines(p, _lines(p)[:-suffix_n])
    scenario("suffix-delete", f"Deleted the last {suffix_n} records of the log.", suffix)

    if pub_seq > 1:
        back = max(1, pub_seq - 3)
        scenario("directory-rollback",
                 f"Restored the log and its anchors to a backup at record {back}; the external "
                 f"head was published at record {pub_seq}.",
                 lambda v, e: restore_prefix(v, back))
    if pub_seq and pub_seq + 1 < n:
        scenario("rollback-after-publication",
                 f"Restored the log and its anchors to record {n - 1}, after the last external "
                 f"publication (record {pub_seq}). Records written after the last publication can "
                 f"be rolled back undetected: this is the stated window.",
                 lambda v, e: restore_prefix(v, n - 1))

    def no_anchors(v: Path, e: Optional[Path]) -> None:
        _anchors(v).unlink(missing_ok=True)
    scenario("remove-internal-anchors", "Deleted the internal anchor ledger file.", no_anchors)

    if external is not None and pub_seq:
        def no_external(v: Path, e: Optional[Path]) -> Optional[Path]:
            if e is not None:
                e.unlink(missing_ok=True)
            return e
        scenario("external-head-missing", "Deleted the external head file.", no_external)

    def rewrite(v: Path, e: Optional[Path]) -> None:
        def mut(recs: list[dict]) -> list[dict]:
            recs[k - 1].setdefault("ib_event", {})["sub"] = "someone-else"
            return recs
        reseal(v, key.encode("utf-8"), mut)
    if pub_seq:
        scenario("key-holder-rewrite",
                 f"Edited record {k} with the sealing key, re-sealed the whole chain and rebuilt "
                 f"the anchors.", rewrite)
    return results


# ------------------------------------------------------------------------------------------------
# the live copy (POST /audit/tamper-demo)
# ------------------------------------------------------------------------------------------------
def sink_file(sink) -> Optional[Path]:
    """The readable file behind an external head sink (FileSink, or a Slack sink's mirror)."""
    for s in (sink, getattr(sink, "mirror", None)):
        p = getattr(s, "path", None)
        if p is not None:
            return Path(p)
    return None


@dataclass
class _Snap:
    path: Path
    size: int
    digest: str


def _snap(p: Path, copy_to: Path) -> Optional[_Snap]:
    if not p.is_file():
        return None
    data = p.read_bytes()
    copy_to.parent.mkdir(parents=True, exist_ok=True)
    copy_to.write_bytes(data)
    return _Snap(p, len(data), hashlib.sha256(data).hexdigest())


def _unchanged(s: Optional[_Snap]) -> bool:
    """Every byte the file held at snapshot time is still there, unchanged (append-only files may
    legitimately have grown since: other requests keep writing)."""
    if s is None:
        return True
    try:
        with open(s.path, "rb") as fh:
            head = fh.read(s.size)
    except OSError:
        return False
    return len(head) == s.size and hashlib.sha256(head).hexdigest() == s.digest


def run_on_live_copy(ledger: AccessLedger, sink, *, budget_s: float = 20.0,
                     lock_timeout_s: float = 10.0) -> dict:
    """Snapshot the live ledger (log, anchor ledger, external head file) under its append lock into a
    temporary directory, run the scenarios there, delete the directory. Returns::

        {"real_log_modified": bool, "head_before": "<12 hex>", "records_copied": int,
         "external_head_copied": bool, "scenarios": [...], "elapsed_ms": int}

    Raises `TamperDemoBusy` when another run is in progress."""
    if not _RUN_LOCK.acquire(blocking=False):
        raise TamperDemoBusy("a tamper demo is already running")
    t0 = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="ib-tamper-live-"))
    try:
        src = tmp / "source" / "var"
        ext_src = sink_file(sink)
        with ledger.core.lock(timeout=lock_timeout_s):         # no append is half-written
            s_log = _snap(Path(ledger.path), _log(src))
            s_anc = _snap(Path(ledger.anchors_path), _anchors(src))
            s_ext = (_snap(ext_src, tmp / "source" / "external" / ext_src.name)
                     if ext_src is not None else None)
        if s_log is None:
            raise FileNotFoundError("the access ledger has no records yet")
        if s_anc is None:
            _anchors(src).parent.mkdir(parents=True, exist_ok=True)
        lines = _lines(_log(src))
        head = json.loads(lines[-1]).get("hash") if lines else None
        ext_copy = (tmp / "source" / "external" / ext_src.name) if s_ext is not None else None
        scen = run_scenarios(src, ext_copy, ledger.cfg.ledger_key, work=tmp / "work",
                             budget_s=max(0.0, budget_s - (time.monotonic() - t0)),
                             scrub=str(tmp))
        modified = not (_unchanged(s_log) and _unchanged(s_anc) and _unchanged(s_ext))
        return {"real_log_modified": modified,
                "head_before": (str(head)[:12] if head else None),
                "records_copied": len(lines), "external_head_copied": s_ext is not None,
                "scenarios": scen, "elapsed_ms": int((time.monotonic() - t0) * 1000)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        _RUN_LOCK.release()


__all__ = ["SCENARIOS", "TamperDemoBusy", "restore_prefix", "reseal", "run_scenarios",
           "run_on_live_copy", "sink_file"]

"""Tamper demo for the Internal Brain access ledger (P5).

Copies a ledger var directory (``ledger/`` + ``anchors/``) into a temporary directory per scenario,
tampers with the copy, and prints what `AccessLedger.verify()` says — internally (chain, seals, the
anchor ledger in its own directory) and against the latest EXTERNALLY published head.

    python scripts/tamper_demo.py                      # builds a synthetic demo ledger first
    python scripts/tamper_demo.py --var var/ib --external <anchor-repo>/heads.jsonl [--key ...]

Scenarios:
  baseline                      untouched copy                                -> ok
  edit-record                   one historical field changed, not re-sealed   -> chain broken at seq N
  suffix-delete                 last N records deleted                        -> rolled back vs internal anchor
  directory-rollback            log + internal anchors restored from an       -> internally OK; detected
                                earlier backup (before the last publication)     ONLY via the external head
  rollback-after-publication    the same, to a point AFTER the last           -> NOT detected: the stated
                                publication                                      window between publications
  remove-internal-anchors       the anchor ledger file deleted                -> anchor mismatch
  external-head-missing         the external head file lost                   -> log says it published
  key-holder-rewrite            an insider WITH the key edits a record,       -> internally OK; detected
                                re-seals the chain and rebuilds the anchors      ONLY via the external head

Nothing here touches the source directory.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]                      # databridge/
for rel in ("apps/internal_brain", "packages/core"):
    if str(ROOT / rel) not in sys.path:
        sys.path.insert(0, str(ROOT / rel))

from databridge_core.decision_ledger import digest_of, seal_of  # noqa: E402

from databridge_ib.audit.anchor import FileSink, publish_head  # noqa: E402
from databridge_ib.audit.ledger import LOG_ID, AccessLedger  # noqa: E402
from databridge_ib.config import IbConfig  # noqa: E402
from databridge_ib.models import (AuditQueryEvent, RequestAccepted,  # noqa: E402
                                  SourceChangeEvent)

DEMO_KEY = "tamper-demo-ledger-key-0123456789abcdef"


# ------------------------------------------------------------------------------------------------
# file helpers (the attacker's tools)
# ------------------------------------------------------------------------------------------------
def _lines(p: Path) -> list[str]:
    return [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _write_lines(p: Path, lines: list[str]) -> None:
    p.write_text("".join(ln + "\n" for ln in lines), encoding="utf-8", newline="\n")


def restore_prefix(var: Path, keep: int) -> None:
    """Make ``var`` look exactly like a backup taken when the log held ``keep`` records: both
    append-only files are cut back to that moment (log to ``keep`` records, the anchor ledger to the
    line that anchored seq ``keep``)."""
    log, anc = var / "ledger" / "access.jsonl", var / "anchors" / "access.anchors.jsonl"
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
    log, anc = var / "ledger" / "access.jsonl", var / "anchors" / "access.anchors.jsonl"
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


# ------------------------------------------------------------------------------------------------
# a demo ledger
# ------------------------------------------------------------------------------------------------
def build_demo(base: Path, *, key: str = DEMO_KEY) -> tuple[Path, Path]:
    """A small synthetic ledger with two external publications (the second one followed by a few
    more events, which are the unprotected window). Returns (var dir, external head file)."""
    cfg = IbConfig(var_root=base / "var", ledger_key=key)
    ledger = AccessLedger(cfg)
    sink = FileSink(base / "external-anchor-repo" / "heads.jsonl")
    t0 = dt.datetime(2026, 9, 15, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
    users = ("jdoe", "priya.sre", "ken.ext", "sara.sec")

    def burst(n: int, start: int) -> None:
        for i in range(start, start + n):
            ledger.append(RequestAccepted(req_id=f"demo-{i}", sub=users[i % 4], channel="replay",
                                          question=f"synthetic question {i}", question_sha256="0" * 64,
                                          policy_version="ib-policy-1", directory_epoch=1,
                                          event_time=t0 + dt.timedelta(days=i % 28),
                                          synthetic_replay=True))

    burst(8, 0)
    publish_head(ledger, sink)
    ledger.append(SourceChangeEvent(kind="acl.changed", platform="confluence",
                                    detail={"doc_ids": ["confluence:cf-1003"]}, event_time=t0))
    burst(6, 8)
    publish_head(ledger, sink)
    ledger.append(AuditQueryEvent(sub="sara.sec", filter={"op": "demo"}, result_count=0,
                                  event_time=t0))
    burst(2, 14)
    return cfg.var_root, sink.path


# ------------------------------------------------------------------------------------------------
# scenarios
# ------------------------------------------------------------------------------------------------
def _verify(var: Path, ext: Optional[Path], key: str) -> dict:
    cfg = IbConfig(var_root=var, ledger_key=key)
    ledger = AccessLedger(cfg, external=FileSink(ext) if ext is not None else None)
    return ledger.verify().as_dict()


def run_scenarios(src_var: Path, external: Path, key: str, *, work: Optional[Path] = None,
                  suffix_n: int = 2) -> list[dict]:
    work = Path(work or tempfile.mkdtemp(prefix="ib-tamper-"))
    log_lines = _lines(src_var / "ledger" / "access.jsonl")
    n = len(log_lines)
    pub = FileSink(external).latest(LOG_ID)
    pub_seq = int(pub["seq"]) if pub else 0
    results = []

    def scenario(name: str, expect_detected: bool, how: str,
                 tamper: Callable[[Path, Path], Optional[Path]]) -> None:
        d = work / name
        var = d / "var"
        shutil.copytree(src_var, var)
        ext = d / "external" / external.name
        ext.parent.mkdir(parents=True)
        shutil.copy2(external, ext)
        ext_used = tamper(var, ext)
        rep = _verify(var, ext_used if ext_used is not None else ext, key)
        detected = not rep["ok"]
        results.append({"scenario": name, "how": how, "expect_detected": expect_detected,
                        "detected": detected, **rep})

    scenario("baseline", False, "untouched copy", lambda v, e: None)

    k = max(1, min(n - 1, pub_seq // 2 or n // 2))

    def edit(v: Path, e: Path) -> None:
        p = v / "ledger" / "access.jsonl"
        lines = _lines(p)
        rec = json.loads(lines[k - 1])
        rec.setdefault("ib_event", {})["sub"] = "someone-else"
        lines[k - 1] = json.dumps(rec, sort_keys=True)
        _write_lines(p, lines)
    scenario("edit-record", True, f"record {k}: ib_event.sub changed, not re-sealed", edit)

    def suffix(v: Path, e: Path) -> None:
        p = v / "ledger" / "access.jsonl"
        _write_lines(p, _lines(p)[:-suffix_n])
    scenario("suffix-delete", True, f"last {suffix_n} records deleted", suffix)

    if pub_seq > 1:
        back = max(1, pub_seq - 3)
        scenario("directory-rollback", True,
                 f"log + internal anchors restored to a backup at seq {back} "
                 f"(external head published at seq {pub_seq})",
                 lambda v, e: restore_prefix(v, back))
    if pub_seq + 1 < n:
        scenario("rollback-after-publication", False,
                 f"restored to seq {n - 1}, after the last publication (seq {pub_seq}): the "
                 f"stated window, not detectable — records appended after the last external publication can be rolled back undetected (stated window)",
                 lambda v, e: restore_prefix(v, n - 1))

    def no_anchors(v: Path, e: Path) -> None:
        (v / "anchors" / "access.anchors.jsonl").unlink()
    scenario("remove-internal-anchors", True, "the internal anchor ledger file deleted", no_anchors)

    def no_external(v: Path, e: Path) -> Path:
        e.unlink()
        return e
    scenario("external-head-missing", True, "the external head file lost", no_external)

    def rewrite(v: Path, e: Path) -> None:
        def mut(recs: list[dict]) -> list[dict]:
            recs[k - 1].setdefault("ib_event", {})["sub"] = "someone-else"
            return recs
        reseal(v, key.encode("utf-8"), mut)
    scenario("key-holder-rewrite", True,
             f"record {k} edited by a key holder, chain re-sealed, anchors regenerated", rewrite)
    return results


def _print(results: list[dict]) -> int:
    bad = 0
    for r in results:
        verdict = "DETECTED" if r["detected"] else "not detected"
        flag = "" if r["detected"] == r["expect_detected"] else "   <-- UNEXPECTED"
        bad += bool(flag)
        print(f"\n[{r['scenario']}] {r['how']}")
        print(f"  {verdict}: ok={r['ok']} internal_ok={r['internal_ok']} "
              f"external_head_match={r['external_head_match']} records={r['records']}{flag}")
        for p in r["problems"]:
            print(f"   - {p}")
    print(f"\n{len(results)} scenarios, {bad} unexpected")
    return 1 if bad else 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--var", type=Path, help="an IB var dir holding ledger/ and anchors/")
    ap.add_argument("--external", type=Path, help="the external head file (FileSink JSONL)")
    ap.add_argument("--key", default=os.environ.get("IB_LEDGER_KEY", ""))
    ap.add_argument("--suffix", type=int, default=2)
    a = ap.parse_args(argv)
    work = Path(tempfile.mkdtemp(prefix="ib-tamper-"))
    if a.var is None:
        var, ext = build_demo(work / "source")
        key = DEMO_KEY
        print(f"built a synthetic demo ledger in {var}")
    else:
        if a.external is None or not a.key:
            ap.error("--var needs --external and --key (or IB_LEDGER_KEY)")
        var, ext, key = a.var, a.external, a.key
    return _print(run_scenarios(var, ext, key, work=work / "scenarios", suffix_n=a.suffix))


if __name__ == "__main__":
    raise SystemExit(main())

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

Nothing here touches the source directory. The scenario logic is shared with the compliance
endpoint ``POST /audit/tamper-demo`` (``databridge_ib/audit/tamper.py``), which runs it on a copy
of the live ledger.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]                      # databridge/
for rel in ("apps/internal_brain", "packages/core"):
    if str(ROOT / rel) not in sys.path:
        sys.path.insert(0, str(ROOT / rel))

from databridge_ib.audit import tamper as _tamper  # noqa: E402
from databridge_ib.audit.anchor import FileSink, publish_head  # noqa: E402
from databridge_ib.audit.ledger import AccessLedger  # noqa: E402
from databridge_ib.audit.tamper import reseal, restore_prefix  # noqa: E402,F401 (re-exported)
from databridge_ib.config import IbConfig  # noqa: E402
from databridge_ib.models import (AuditQueryEvent, RequestAccepted,  # noqa: E402
                                  SourceChangeEvent)

DEMO_KEY = "tamper-demo-ledger-key-0123456789abcdef"


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
# scenarios (the logic lives in databridge_ib/audit/tamper.py, shared with POST /audit/tamper-demo)
# ------------------------------------------------------------------------------------------------
def run_scenarios(src_var: Path, external: Path, key: str, *, work: Optional[Path] = None,
                  suffix_n: int = 2) -> list[dict]:
    """The scenarios on copies of ``src_var``, in this script's original row shape
    (``scenario``, ``how``, ``expect_detected``, ``detected`` + the verify report fields)."""
    work = Path(work or tempfile.mkdtemp(prefix="ib-tamper-"))
    rows = _tamper.run_scenarios(src_var, external, key, work=work, suffix_n=suffix_n)
    return [{"scenario": r["id"], "how": r["what_we_did"], "expect_detected": r["expected_detected"],
             "detected": r["detected"], "ok": r.get("verify_ok"), "internal_ok": r.get("internal_ok"),
             "external_head_match": r.get("external_head_match"), "records": r.get("records"),
             "problems": r.get("problems", []), "first_bad_seq": r.get("first_bad_seq")}
            for r in rows]


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

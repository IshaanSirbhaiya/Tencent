"""Turn the Internal Brain leak eval's results into the JSON the analytics page reads (plan B8).

    .venv/Scripts/python scripts/eval_summary.py                      # from databridge/
    .venv/Scripts/python scripts/eval_summary.py --smoke <dir>        # also record a --limit run
    .venv/Scripts/python scripts/eval_summary.py --no-collect         # skip the pytest count

Reads ``evals/internal_brain/results.json`` (the machine-readable twin the eval writes), falling back
to the headline table of ``RESULTS.md``; writes
``apps/internal_brain_web/public/eval_summary.json``. Every number is copied from the eval output or
from a command run here (``pytest --collect-only`` for the backend test count, ``git`` for the
revision); nothing is typed in by hand except the e2e count, which is a parameter (``--e2e``).

Honesty fields:
* ``full_run`` is false when the eval was run with ``--limit``;
* ``stale`` is true when Internal Brain code changed after the eval was generated, so the page can
  say "re-run pending" instead of presenting old numbers as current. When the results record
  ``config.ib_code_sha256`` (the digest of ``apps/internal_brain/databridge_ib/**/*.py`` the eval
  ran on), stale means the current code's digest differs, whether or not it is committed yet.
  Older results without it fall back to git: a later commit to that folder, or uncommitted changes
  there;
* ``smoke`` (optional) holds the headline of a smaller run made after the change.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]                         # databridge/
EVAL_DIR = ROOT / "evals" / "internal_brain"
OUT = ROOT / "apps" / "internal_brain_web" / "public" / "eval_summary.json"
IB_CODE = "apps/internal_brain/databridge_ib"
PYPATH = ["apps/internal_brain", "packages/core", "packages/harness", "packages/adapter",
          "packages/retrieval"]


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                              timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _pct_range(values: list[float]) -> dict | None:
    vals = [v for v in values if isinstance(v, (int, float))]
    return {"min": round(min(vals), 4), "max": round(max(vals), 4)} if vals else None


def from_results_json(res: dict) -> dict:
    h = res.get("headline") or {}
    util = res.get("utility") or {}
    released = {m: (u.get("positive_probes") or {}).get("ALL", {}).get("released_recall")
                for m, u in util.items()}
    cited = {m: (u.get("positive_probes") or {}).get("ALL", {}).get("cited_recall")
             for m, u in util.items()}
    per_mode = {m: {"requests": v.get("requests"), "with_leak": v.get("requests_with_leak"),
                    "upper95": v.get("leak_rate_upper95"),
                    "with_model_payload": v.get("requests_with_model_payload"),
                    "refused": v.get("refused")}
                for m, v in (res.get("per_mode") or {}).items()}
    args = ((res.get("config") or {}).get("args") or {})
    return {
        "run_date": res.get("generated_at"),
        "ib_code_sha256": (res.get("config") or {}).get("ib_code_sha256"),
        "full_run": not args.get("limit"),
        "limit": args.get("limit") or 0,
        "leaks": h.get("leaks"),
        "leak_requests": h.get("leak_requests"),
        "requests": h.get("n_requests_all_modes"),
        "requests_per_mode": h.get("n_requests"),
        "upper95": h.get("leak_rate_upper95"),
        "payload_requests": h.get("payload_requests"),
        "payload_upper95": h.get("payload_leak_upper95"),
        "violations": h.get("violations"),
        "differential": {"pairs": h.get("p2_compared"), "mismatches": h.get("p2_mismatches"),
                         "control_mismatches": h.get("p2_control_mismatches")},
        "injection": {"requests": (res.get("injection") or {}).get("probes"),
                      "hard_failures": h.get("injection_hard_failures")},
        "recall": {"released": released, "released_all_modes": _pct_range(list(released.values()))},
        "cited_recall": {"per_mode": cited, "range": _pct_range(list(cited.values()))},
        "quality_findings": h.get("quality_findings"),
        "canaries_valid": h.get("canaries_valid"),
        "runtime_s": h.get("runtime_s"),
        "per_mode": per_mode,
    }


_MD_ROWS = {
    "leaks": r"Leaks found.*?\*\*(\d+)\*\* across (\d+) requests",
    "upper": r"95% upper bound on per-request leak rate.*?\|\s*([\d.]+)%",
    "p2": r"P2 differential: compared / mismatches \|\s*(\d+) / \*\*(\d+)\*\*",
    "inj": r"Injection hard failures.*?\|\s*(\d+)",
    "recall": r"positive-probe released recall \|\s*fake-capture: ([\d.]+)%, extractive: ([\d.]+)%",
    "date": r"Generated (\S+) by",
}


def from_results_md(text: str) -> dict:
    """Fallback when results.json is missing: the headline table only."""
    m = {k: re.search(p, text) for k, p in _MD_ROWS.items()}
    g = lambda k, i=1: m[k].group(i) if m[k] else None              # noqa: E731
    rec = [float(x) / 100 for x in (g("recall", 1), g("recall", 2)) if x]
    return {"run_date": g("date"), "full_run": None, "limit": None,
            "leaks": int(g("leaks")) if g("leaks") else None,
            "requests": int(g("leaks", 2)) if g("leaks") else None,
            "upper95": float(g("upper")) / 100 if g("upper") else None,
            "differential": {"pairs": int(g("p2")) if g("p2") else None,
                             "mismatches": int(g("p2", 2)) if g("p2") else None},
            "injection": {"hard_failures": int(g("inj")) if g("inj") else None},
            "recall": {"released_all_modes": _pct_range(rec)}, "cited_recall": None}


def _rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return p.name                          # outside the repo (a scratch run): no local path


def load(eval_dir: Path) -> tuple[dict, str]:
    rj, md = eval_dir / "results.json", eval_dir / "RESULTS.md"
    if rj.exists():
        return from_results_json(json.loads(rj.read_text(encoding="utf-8"))), _rel(rj)
    if md.exists():
        return from_results_md(md.read_text(encoding="utf-8")), _rel(md)
    raise SystemExit(f"no eval results in {eval_dir}")


def backend_test_count() -> int | None:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(str(ROOT / p) for p in PYPATH)}
    try:
        out = subprocess.run([sys.executable, "-m", "pytest", "apps/internal_brain/tests",
                              "--collect-only", "-q", "-p", "no:cacheprovider"], cwd=ROOT,
                             capture_output=True, text=True, timeout=600, env=env).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+) tests? collected", out)
    return int(m.group(1)) if m else None


def ib_code_sha256() -> str:
    """The same digest as ``evals/internal_brain/report.py::ib_code_sha256`` (tested equal)."""
    import hashlib
    root = ROOT / IB_CODE
    h = hashlib.sha256()
    for f in sorted(root.rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        h.update(f.relative_to(root).as_posix().encode("utf-8") + bytes(1))
        h.update(f.read_bytes().replace(bytes([13, 10]), bytes([10])) + bytes(1))
    return h.hexdigest()


def stale(run_date: str | None, code_sha256: str | None = None) -> bool | None:
    if code_sha256:
        return ib_code_sha256() != code_sha256
    if not run_date:
        return None
    try:
        ran = datetime.fromisoformat(run_date)
    except ValueError:
        return None
    last = _git("log", "-1", "--format=%cI", "--", IB_CODE)
    dirty = bool(_git("status", "--porcelain", "--", IB_CODE))
    changed = bool(last) and datetime.fromisoformat(last) > ran
    return changed or dirty


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--eval-dir", type=Path, default=EVAL_DIR)
    ap.add_argument("--smoke", type=Path, default=None,
                    help="directory of a smaller (--limit) run made after the last code change")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--e2e", type=int, default=38, help="Playwright e2e test count")
    ap.add_argument("--no-collect", action="store_true")
    args = ap.parse_args(argv)
    summary, source = load(args.eval_dir)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "git_sha": _git("rev-parse", "HEAD") or None,
        "stale": stale(summary.get("run_date"), summary.get("ib_code_sha256")),
        **summary,
        "tests": {"backend": None if args.no_collect else backend_test_count(), "e2e": args.e2e},
        "smoke": None,
    }
    if args.smoke:
        s, ssrc = load(args.smoke)
        out["smoke"] = {"source": "scratch run (not committed)", "run_date": s.get("run_date"),
                        "limit": s.get("limit"), "leaks": s.get("leaks"),
                        "requests": s.get("requests"), "differential": s.get("differential"),
                        "injection": s.get("injection"), "recall": s.get("recall"),
                        "cited_recall": s.get("cited_recall"), "violations": s.get("violations")}
    out["note"] = ("Full evaluation re-run pending: the Internal Brain changed after this run."
                   if out["stale"] else "Numbers are from the latest full evaluation run.")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {args.out} (leaks={out['leaks']}, requests={out['requests']}, "
          f"stale={out['stale']}, backend tests={out['tests']['backend']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

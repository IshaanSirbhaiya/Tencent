"""Ingest benchmark (P17: measure before you quote).

Ingests `fixtures/workspace_demo/files` through the synthetic stdio MCP server
(`tests/fixture_server.py` — a real subprocess and a real MCP session, whose `read_media_file`
returns bytes so PDF/DOCX go through docling) with a FakeBackend that SLEEPS a configurable number
of milliseconds per model call, standing in for a real model's latency. Nothing touches the
network or a real model. Everything is written under a throw-away directory.

Runs, on the SAME input:
  cold        empty capture store, empty caches, empty graph
  warm-cache  NEW ingest state + NEW graph, but the capture store and the extract/convert caches
              of the cold run kept — what a re-ingest of unchanged content costs

    python packages/mcp_ingest/bench/bench_ingest.py --latency-ms 400 --concurrency 4
    python packages/mcp_ingest/bench/bench_ingest.py --concurrency 1 --scale 3 --json out.json

Prints a stage -> ms table per run. Stage seconds come from `stages.json` (written by every run);
the finer breakdown (MCP connect/list/read, docling, screen, model calls/tokens/latency, graph
write, retrieval) from `report.json["timings"]` when the runner records it.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
DATABRIDGE = PKG.parents[1]
for _p in (PKG, PKG / "tests", *(DATABRIDGE / "packages" / n for n in
                                 ("core", "harness", "convert", "graph", "intake", "adapter",
                                  "ingest", "retrieval"))):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

DEMO = DATABRIDGE / "fixtures" / "workspace_demo" / "files"
FIXTURE_SERVER = PKG / "tests" / "fixture_server.py"

_NAME = re.compile(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+){1,2})\b")


def fake_model(latency_ms: float, stats: dict):
    """A deterministic 'model': capitalised 2-word phrases are people, 3-word ones organisations;
    a person followed by an organisation in the same sentence gets `works_for`. Offsets are
    computed from the text it was SENT, so the grounding wall still has real work to do."""
    lock = threading.Lock()

    def handler(*, model, messages, guided_json) -> str:
        t0 = time.perf_counter()
        time.sleep(latency_ms / 1000.0)
        last = messages[-1]["content"]
        out = {"entities": [], "relations": []}
        if last.startswith("DOCUMENT TEXT:\n"):
            text = last[len("DOCUMENT TEXT:\n"):]
            seen = {}
            for m in _NAME.finditer(text):
                name = m.group(1)
                if name in seen:
                    continue
                kind = "person" if name.count(" ") == 1 else "organisation"
                seen[name] = kind
                out["entities"].append({"name": name, "kind": kind, "char_start": m.start(),
                                        "char_end": m.end()})
            for sm in re.finditer(r"[^.!?]+[.!?]?", text):
                sentence = sm.group(0)
                people = [n for n, k in seen.items() if k == "person" and n in sentence]
                orgs = [n for n, k in seen.items() if k == "organisation" and n in sentence]
                for p in people[:1]:
                    for o in orgs[:1]:
                        out["relations"].append({"src_name": p, "rel_type": "works_for",
                                                 "dst_name": o,
                                                 "evidence_char_start": sm.start(),
                                                 "evidence_char_end": sm.end()})
        with lock:
            stats["calls"] += 1
            stats["latency_ms"] += (time.perf_counter() - t0) * 1000
        return json.dumps(out)
    return handler


def _corpus(base: Path, scale: int) -> Path:
    if scale <= 1:
        return DEMO
    root = base / "corpus"
    for i in range(scale):
        shutil.copytree(DEMO, root / f"copy{i}")
    # distinct copies must differ in content or they are one capture; append a marker line
    for i in range(scale):
        for f in (root / f"copy{i}").rglob("*"):
            if f.is_file() and f.suffix in (".md", ".txt"):
                f.write_text(f.read_text(encoding="utf-8") + f"\nCopy {i}.\n", encoding="utf-8")
    return root


def run_once(label: str, base: Path, corpus: Path, *, latency_ms: float, concurrency: int,
             fresh_capture: bool, extra: dict) -> dict:
    from databridge_graph.packs import deployment
    from databridge_graph.store import GraphStore
    from databridge_harness import FakeBackend, Harness

    import databridge_mcp_ingest as mi
    from databridge_mcp_ingest.runner import run_ingest

    state = base / label
    env = {"WALNUT_SOURCES_DIR": state / "sources", "WALNUT_INGEST_DIR": state / "ingest",
           "WALNUT_EVENTS_PATH": state / "events" / "events.jsonl",
           "WALNUT_GRAPH_DB_PATH": state / "graph" / "bench.db",
           "WALNUT_PASSAGES_DIR": state / "passages"}
    shared = base / "shared"
    env["WALNUT_CAPTURE_DIR"] = (state / "capture") if fresh_capture else (shared / "capture")
    env["WALNUT_CACHE_DIR"] = (state / "cache") if fresh_capture else (shared / "cache")
    for k, v in env.items():
        os.environ[k] = str(v)
    stats = {"calls": 0, "latency_ms": 0.0}
    harness = Harness(FakeBackend(fake_model(latency_ms, stats)), profile="cloud")
    env["WALNUT_GRAPH_DB_PATH"].parent.mkdir(parents=True, exist_ok=True)
    store = GraphStore(str(env["WALNUT_GRAPH_DB_PATH"]))
    params = inspect.signature(run_ingest).parameters
    kw = {k: v for k, v in {"concurrency": concurrency, **extra}.items() if k in params}
    with deployment("workspace"):
        mi.register_connection({"id": "bench", "title": "bench", "auto_approve": True,
                                "transport": {"type": "stdio", "command": sys.executable,
                                              "args": [str(FIXTURE_SERVER), "--root",
                                                       str(corpus)]},
                                "scope": {}})
        mi.propose_plan("bench")
        t0 = time.perf_counter()
        report = run_ingest("bench", store=store, harness=harness, **kw)
        wall = time.perf_counter() - t0
    store.close()
    run_dir = Path(report.run_dir)
    stages = json.loads((run_dir / "stages.json").read_text(encoding="utf-8"))
    rep = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    return {"label": label, "status": report.status, "wall_ms": round(wall * 1000, 1),
            "stages_ms": {k: round(v.get("seconds", 0) * 1000, 1) for k, v in stages.items()
                          if isinstance(v, dict) and "seconds" in v},
            "timings": rep.get("timings"), "backend_calls": stats["calls"],
            "backend_latency_ms": round(stats["latency_ms"], 1),
            "counts": {k: rep.get("counts", {}).get(k) for k in
                       ("captured", "extracted", "built", "entities", "relations", "pointers")},
            "kwargs": kw}


def _table(results: list[dict]) -> str:
    rows: dict[str, list] = {}
    order: list[str] = []

    def put(key, i, val):
        if key not in rows:
            rows[key] = [""] * len(results)
            order.append(key)
        rows[key][i] = val

    for i, r in enumerate(results):
        put("TOTAL wall", i, r["wall_ms"])
        for k, v in r["stages_ms"].items():
            put(f"stage {k}", i, v)
        for k, v in ((r.get("timings") or {}).get("buckets") or {}).items():
            put(f"  {k}", i, f"{v['ms']} ({v['n']})")
        put("model backend calls", i, r["backend_calls"])
        put("model latency sum", i, r["backend_latency_ms"])
        for k, v in r["counts"].items():
            put(f"count {k}", i, v)
    w = max(len(k) for k in order) + 2
    head = "".join(f"{r['label']:>22}" for r in results)
    lines = [f"{'stage -> ms':<{w}}{head}"]
    for k in order:
        lines.append(f"{k:<{w}}" + "".join(f"{str(v):>22}" for v in rows[k]))
    return "\n".join(lines)


def _in_subprocess(label: str, base: Path, corpus: Path, a, fresh: bool) -> dict:
    """Each run in its OWN process, so a per-process cost (docling's model load, imports) is
    paid by every run that would pay it in production, not hidden by an earlier run."""
    import subprocess

    out = base / f"{label}.result.json"
    cmd = [sys.executable, str(Path(__file__).resolve()), "--_run", label, "--_base", str(base),
           "--_corpus", str(corpus), "--latency-ms", str(a.latency_ms),
           "--concurrency", str(a.concurrency), "--_out", str(out)]
    if fresh:
        cmd.append("--_fresh")
    if a.passages:
        cmd.append("--passages")
    subprocess.run(cmd, check=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    return json.loads(out.read_text(encoding="utf-8"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])
    ap.add_argument("--latency-ms", type=float, default=400.0)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--scale", type=int, default=1, help="copies of the demo corpus")
    ap.add_argument("--passages", action="store_true", help="also index passages (retrieval)")
    ap.add_argument("--json", help="write the raw results here")
    ap.add_argument("--keep", action="store_true", help="keep the temp directory")
    ap.add_argument("--_run")
    ap.add_argument("--_base")
    ap.add_argument("--_corpus")
    ap.add_argument("--_out")
    ap.add_argument("--_fresh", action="store_true")
    a = ap.parse_args(argv)
    extra = {"passages": True} if a.passages else {}
    if a._run:
        result = run_once(a._run, Path(a._base), Path(a._corpus), latency_ms=a.latency_ms,
                          concurrency=a.concurrency, fresh_capture=a._fresh, extra=extra)
        Path(a._out).write_text(json.dumps(result, default=str), encoding="utf-8")
        return 0
    base = Path(tempfile.mkdtemp(prefix="walnut-bench-"))
    try:
        corpus = _corpus(base, a.scale)
        cold = _in_subprocess("cold", base, corpus, a, fresh=True)
        _in_subprocess("prime", base, corpus, a, fresh=False)
        warm = _in_subprocess("warm-cache", base, corpus, a, fresh=False)
        results = [cold, warm]
        print(f"latency={a.latency_ms}ms concurrency={a.concurrency} scale={a.scale} "
              f"corpus={corpus}", flush=True)
        print(_table(results), flush=True)
        if a.json:
            Path(a.json).write_text(json.dumps(results, indent=1, default=str),
                                    encoding="utf-8")
    finally:
        if not a.keep:
            shutil.rmtree(base, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

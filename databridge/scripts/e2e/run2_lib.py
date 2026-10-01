"""Shared helpers for the parallel E2E re-run (E2E2): timings, polling, a thread pool.

Every step is timed into <run>/artefacts/timings_steps.jsonl (one line per step, with its
wall-clock start/end so overlap — the parallelism — is visible) and summarised by
`collect_timings.py` into timings.json.
"""
from __future__ import annotations

import concurrent.futures as cf
import contextlib
import json
import threading
import time
from pathlib import Path

from e2e_client import call
from e2e_env import e2e_dir

_LOCK = threading.Lock()
POOL = cf.ThreadPoolExecutor(max_workers=24)


def art(name: str) -> Path:
    p = e2e_dir() / "artefacts" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def save(name: str, obj) -> None:
    art(name).write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def load(name: str, default=None):
    p = art(name)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else default


@contextlib.contextmanager
def step(name: str, **extra):
    t0 = time.time()
    rec = {"step": name, "start": t0, **extra}
    try:
        yield rec
        rec["ok"] = True
    except Exception as exc:  # noqa: BLE001
        rec["ok"] = False
        rec["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        rec["end"] = time.time()
        rec["secs"] = round(rec["end"] - t0, 2)
        with _LOCK, art("timings_steps.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        print(f"[step] {name} {rec['secs']}s ok={rec.get('ok')}", flush=True)


def poll(path: str, name: str, *, done=lambda j: j.get("status") not in ("running", "pending",
                                                                          "queued"),
         every: float = 2.0, limit: float = 1800.0, token=None) -> dict:
    t0 = time.time()
    while True:
        st, j, _ = call("GET", path, name=name, token=token, save=False)
        if st != 200 or done(j) or time.time() - t0 > limit:
            call("GET", path, name=name, token=token)          # save the final state once
            return j
        time.sleep(every)


def parallel(**fns):
    """Run zero-arg callables concurrently; return {name: result or Exception}."""
    futs = {k: POOL.submit(f) for k, f in fns.items()}
    out = {}
    for k, f in futs.items():
        try:
            out[k] = f.result()
        except Exception as exc:  # noqa: BLE001
            out[k] = exc
            print(f"[parallel] {k} raised {type(exc).__name__}: {exc}", flush=True)
    return out


def note(text: str) -> None:
    with _LOCK, (e2e_dir() / "notes.md").open("a", encoding="utf-8") as f:
        f.write(f"- {time.strftime('%H:%M:%S')} {text}\n")
    print("[note]", text, flush=True)

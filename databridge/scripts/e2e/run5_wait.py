"""E2E5: poll GET <path> until its status is final or --limit seconds pass (default 170 s).

    python scripts/e2e/run5_wait.py PATH [--limit 170] [--name label] [--as who] [--field status]
Prints the final status and a compact summary; exit 3 when still running at the limit.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("WALNUT_E2E_BASE", "http://127.0.0.1:8015")
from e2e_env import setup_env  # noqa: E402

setup_env()
from e2e_client import call, tokens  # noqa: E402

RUNNING = ("running", "queued", "pending", "started", "in_progress", None)


def main(argv):
    path = argv[0]
    limit, name, who, field = 170.0, "", "primary", "status"
    it = iter(argv[1:])
    for a in it:
        if a == "--limit":
            limit = float(next(it))
        elif a == "--name":
            name = next(it)
        elif a == "--as":
            who = next(it)
        elif a == "--field":
            field = next(it)
    t0 = time.time()
    while True:
        st, j, _ = call("GET", path, token=tokens()[who], save=False)
        s = j.get(field) if isinstance(j, dict) else None
        if st != 200 or s not in RUNNING or time.time() - t0 > limit:
            break
        time.sleep(3)
    call("GET", path, token=tokens()[who], name=name or "wait_final")
    rep = (j.get("report") or {}) if isinstance(j, dict) else {}
    print("HTTP", st, field, s, "after", round(time.time() - t0), "s")
    print(json.dumps({"counts": rep.get("counts"), "error": j.get("error") if isinstance(j, dict) else None,
                      "keys": list(j)[:30] if isinstance(j, dict) else None}, default=str)[:2500])
    sys.exit(3 if s in RUNNING else 0)


if __name__ == "__main__":
    main(sys.argv[1:])

"""E2E5: one HTTP call against the run's gateway, saved under <run>/artefacts/http/, compact print.

    python scripts/e2e/run5_call.py METHOD PATH [json-body|@file] [--as primary|second|operator|none]
                                    [--name label] [--max N] [--jq dotted.path]
Env: WALNUT_E2E_DIR, WALNUT_E2E_ESTATE, WALNUT_E2E_BASE (default http://127.0.0.1:8015).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("WALNUT_E2E_BASE", "http://127.0.0.1:8015")
from e2e_env import setup_env  # noqa: E402

setup_env()
from e2e_client import call, tokens  # noqa: E402


def main(argv: list[str]) -> None:
    who, name, mx, jq = "primary", "", 4000, None
    rest = []
    it = iter(argv)
    for a in it:
        if a == "--as":
            who = next(it)
        elif a == "--name":
            name = next(it)
        elif a == "--max":
            mx = int(next(it))
        elif a == "--jq":
            jq = next(it)
        else:
            rest.append(a)
    method, path = rest[0], rest[1]
    body = None
    if len(rest) > 2:
        b = rest[2]
        body = json.loads(Path(b[1:]).read_text(encoding="utf-8") if b.startswith("@") else b)
    tok = "" if who == "none" else tokens()[who]
    st, data, ms = call(method, path, body, name=name, token=tok, timeout=900)
    if jq:
        for k in jq.split("."):
            data = data[int(k)] if isinstance(data, list) else (data or {}).get(k)
    out = json.dumps(data, indent=1, default=str)
    print(f"HTTP {st} {ms:.0f}ms")
    print(out[:mx] + ("\n...[truncated %d chars]" % (len(out) - mx) if len(out) > mx else ""))


if __name__ == "__main__":
    main(sys.argv[1:])

"""Is Tencent Hunyuan (Hy3) live on TokenHub for this account? One small call per configured model.

    .venv/Scripts/python scripts/hy3_check.py            # table + exit 0 when the FIRST model answers
    .venv/Scripts/python scripts/hy3_check.py --quiet    # exit code only (used by scripts/demo_up.sh)

Reads TOKENHUB_API_KEY (and optional IB_MODELS, TOKENHUB_BASE) from the environment, falling back to
databridge/.env.ib.local. The key is never printed. Exit codes: 0 live, 1 not live, 2 no key.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = "https://tokenhub-intl.tencentcloudmaas.com/v1"
DEFAULT_CHAIN = ("hy3", "deepseek-v4.1-flash")
HINT = {"401006": "model service not activated/paid in the TokenHub console (Online Inference)",
        "400004": "model id unknown to TokenHub; set IB_MODELS to the console's model or service id"}


def _env() -> dict:
    env = dict(os.environ)
    f = ROOT / ".env.ib.local"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"'))
    return env


def check(model: str, key: str, base: str) -> tuple[bool, str, float]:
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "Reply with OK."}],
                       "max_tokens": 5}).encode()
    req = urllib.request.Request(f"{base.rstrip('/')}/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            json.loads(r.read())
            return True, "live", time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        try:
            code = str(json.loads(e.read()).get("error", {}).get("code", ""))
        except Exception:                                            # noqa: BLE001
            code = ""
        return False, f"HTTP {e.code} {code} {HINT.get(code, '')}".strip(), time.perf_counter() - t0
    except Exception as e:                                           # noqa: BLE001
        return False, type(e).__name__, time.perf_counter() - t0


def main() -> int:
    quiet = "--quiet" in sys.argv
    env = _env()
    key = env.get("TOKENHUB_API_KEY", "")
    if not key:
        if not quiet:
            print("no TOKENHUB_API_KEY (environment or databridge/.env.ib.local)")
        return 2
    base = env.get("TOKENHUB_BASE", DEFAULT_BASE)
    models = [m.strip() for m in env.get("IB_MODELS", "").split(",") if m.strip()] or list(DEFAULT_CHAIN)
    results = [(m, *check(m, key, base)) for m in models]
    if not quiet:
        for m, ok, why, secs in results:
            print(f"  {m:<28} {'LIVE' if ok else 'not live':<9} {secs:5.1f}s  {why if not ok else ''}")
        print("Hy3 is LIVE: start the demo with IB_FORCE_OFFLINE=0" if results[0][1]
              else "first model not live: answers stay on the honest extractive fallback")
    return 0 if results[0][1] else 1


if __name__ == "__main__":
    sys.exit(main())

"""Tiny HTTP client for the e2e gateway; saves every response under <e2e>/artefacts/http/.

Thread-safe (run 2 drives scenarios concurrently). Base URL from WALNUT_E2E_BASE (default
http://127.0.0.1:8012, run 1). The bearer token is the run's PRIMARY principal token from
<run>/auth/tokens.json (run 2: WALNUT_API_TOKENS, a real deployment's auth) or, for run 1, the
dev token. `token=` picks another principal; `token=""` sends no Authorization header.
"""
from __future__ import annotations

import itertools
import json
import os
import re
import threading
import time
from pathlib import Path

import httpx

from e2e_env import e2e_dir

BASE = os.environ.get("WALNUT_E2E_BASE", "http://127.0.0.1:8012")
_LOCK = threading.Lock()
_COUNTER: list = []


def tokens() -> dict:
    p = e2e_dir() / "auth" / "tokens.json"
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"primary": "dev-local-only"}


def _next() -> int:
    with _LOCK:
        if not _COUNTER:
            d = e2e_dir() / "artefacts" / "http"
            d.mkdir(parents=True, exist_ok=True)
            _COUNTER.append(itertools.count(len(list(d.glob("*.json"))) + 1))
        return next(_COUNTER[0])


def _save(name: str, method: str, path: str, body, resp, ms: float) -> None:
    d = e2e_dir() / "artefacts" / "http"
    n = _next()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{method}_{path}")[:90]
    try:
        payload = resp.json()
    except Exception:
        payload = resp.text[:20000]
    rec = {"method": method, "path": path, "request": body, "status": resp.status_code,
           "ms": round(ms, 1), "t_end": time.time(), "response": payload}
    (d / f"{n:04d}_{name or safe}.json").write_text(json.dumps(rec, indent=2, default=str),
                                                    encoding="utf-8")


def call(method: str, path: str, body=None, *, name: str = "", timeout: float = 900.0, params=None,
         token=None, save: bool = True):
    tok = tokens()["primary"] if token is None else token
    hdr = {"Authorization": f"Bearer {tok}"} if tok else {}
    t0 = time.monotonic()
    with httpx.Client(timeout=timeout) as c:
        r = c.request(method, BASE + path, json=body, headers=hdr, params=params)
    ms = (time.monotonic() - t0) * 1000
    if save:
        _save(name, method, path, body, r, ms)
    try:
        data = r.json()
    except Exception:
        data = {"_text": r.text}
    return r.status_code, data, ms


def raw(method: str, path: str, *, token=None, timeout: float = 120.0):
    """Unparsed response (downloads)."""
    tok = tokens()["primary"] if token is None else token
    hdr = {"Authorization": f"Bearer {tok}"} if tok else {}
    with httpx.Client(timeout=timeout) as c:
        return c.request(method, BASE + path, headers=hdr)

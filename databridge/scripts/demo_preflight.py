"""Demo Day preflight and pre-warm for Walnut DataBridge (docs/finals/demo_day/DEMO_DAY_RUNBOOK.md).

    .venv/Scripts/python scripts/demo_preflight.py                 # checks only (read-only)
    .venv/Scripts/python scripts/demo_preflight.py --warm          # + one ask per scripted question
    .venv/Scripts/python scripts/demo_preflight.py --offline       # the mock build on :3101
    .venv/Scripts/python scripts/demo_preflight.py --sim-reset     # admin.it -> Simulator reset first

A non-loopback --web is refused unless --allow-remote, and then only over https (the demo sign-in
and IB_DEMO_PASSCODE go to that origin).

Everything goes through the web app's own proxy (/api/ib/*), the path the stage browser uses, so a
green line means the browser path works, not only the Python service behind it. The services are
also probed directly (/health on :8100, :8200, :8300).

The pre-warm asks are REAL asks (channel "ui"), signed in as each demo persona with demo sign-in:
they appear in the ledger as ordinary asks made before the demo, which is what they are. Nothing
else is written, except the ledger's own records of the sign-ins, the Verify run and the audit
reads (every audit read is ledgered by design).

Results are written to var/demo-day/preflight.json, which /present/preflight shows. Exit code 0
when every required check passed, 1 otherwise. Standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]                     # databridge/
WEB_DIR = ROOT / "apps" / "internal_brain_web"
OUT_DEFAULT = ROOT / "var" / "demo-day" / "preflight.json"

# The exact strings of the Demo Day script (components/presenter/script.ts, lib/personas.ts).
Q_VELQUARRY = "Why has Velquarry Trading's account been restricted, and what should I tell the customer?"
Q_BAYFRONT = "Why has Bayfront Spice Traders' account been restricted, and what should I tell the customer?"
Q_WORKBUDDY = ("What's the status of the database migration project and were there any blockers "
               "raised in Slack last week?")
CLIPS = ("clipA_compare_amy_rachel_ken.mp4", "clipB_journey_audit_tamper.mp4", "clipC_workbuddy_jordan.mp4")


class Client:
    """One signed-in browser-like session against the web app's /api/ib proxy."""

    def __init__(self, web: str, timeout: float = 60):
        self.web = web.rstrip("/")
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method: str, path: str, body: Optional[dict] = None, timeout: Optional[float] = None
             ) -> tuple[int, Any, float]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{self.web}/api/ib/{path}", data=data, headers=headers, method=method)
        t0 = time.perf_counter()
        try:
            with self.op.open(req, timeout=timeout or self.timeout) as r:
                raw = r.read()
                code = r.status
        except urllib.error.HTTPError as e:
            raw, code = e.read(), e.code
        except Exception as e:                                       # noqa: BLE001  (timeout, refused)
            return 0, {"error": type(e).__name__}, time.perf_counter() - t0
        secs = time.perf_counter() - t0
        try:
            return code, json.loads(raw or b"null"), secs
        except ValueError:
            return code, None, secs

    def sign_in(self, persona: str) -> bool:
        body: dict[str, Any] = {"persona": persona}
        if os.environ.get("IB_DEMO_PASSCODE"):
            body["passcode"] = os.environ["IB_DEMO_PASSCODE"]
        code, _, _ = self.call("POST", "auth/demo-login", body, timeout=15)
        return code == 200

    def page_ok(self, path: str, timeout: float = 20) -> tuple[bool, str]:
        """GET a web page with this session's cookie (the preflight board needs a signed-in loopback visitor)."""
        try:
            with self.op.open(urllib.request.Request(f"{self.web}{path}", method="GET"), timeout=timeout) as r:
                return 200 <= r.status < 400, f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}"
        except Exception as e:                                       # noqa: BLE001
            return False, type(e).__name__

    def sign_out(self) -> None:
        try:
            self.call("POST", "auth/logout", {}, timeout=10)
        except Exception:                                            # noqa: BLE001
            pass


def http_ok(url: str, timeout: float = 4) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 400, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:                                           # noqa: BLE001
        return False, type(e).__name__


class Report:
    def __init__(self):
        self.items: list[dict[str, Any]] = []

    def add(self, group: str, key: str, label: str, status: str, detail: str = "", **extra: Any) -> None:
        """status: pass | fail | warn | info."""
        self.items.append({"group": group, "key": key, "label": label, "status": status,
                           "detail": detail, **extra})
        mark = {"pass": "PASS", "fail": "FAIL", "warn": "WARN", "info": "INFO"}[status]
        print(f"  [{mark}] {label}{(': ' + detail) if detail else ''}", flush=True)

    @property
    def ok(self) -> bool:
        return not any(i["status"] == "fail" for i in self.items)


def same_refusal(a: dict, b: dict) -> bool:
    keys = ("answer", "citations", "scope_note", "model")
    return all(a.get(k) == b.get(k) for k in keys)


_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def origin_error(url: str, allow_remote: bool) -> str | None:
    """Why *url* must not receive the demo sign-in (and IB_DEMO_PASSCODE), or None when it may.

    Round 2b finding 6: loopback only by default. A non-loopback host needs --allow-remote, and even
    then plain http is refused, so the passcode never crosses a network in clear text.
    """
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return f"{url!r} is not an http(s) origin"
    if p.username or p.password:
        return "the origin must not contain credentials"
    if p.hostname.lower() in _LOOPBACK:
        return None
    if not allow_remote:
        return (f"{p.hostname} is not a loopback host; the demo sign-in (and IB_DEMO_PASSCODE) is sent "
                "only to 127.0.0.1, localhost or ::1 unless you pass --allow-remote")
    if p.scheme != "https":
        return f"{p.hostname} is remote: use https:// (the passcode is never sent over plain http off this machine)"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="check the mock build on :3101 (no services)")
    ap.add_argument("--web", default=None, help="web app origin (default :3100, or :3101 with --offline)")
    ap.add_argument("--warm", action="store_true", help="ask every scripted question once (real asks)")
    ap.add_argument("--sim-reset", action="store_true", help="as admin.it, reset the platform simulator first")
    ap.add_argument("--max-s", type=float, default=8.0, help="slowest acceptable ask, seconds (default 8)")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--allow-remote", action="store_true",
                    help="allow a non-loopback https --web (the demo sign-in and passcode go there)")
    a = ap.parse_args()

    web = (a.web or ("http://127.0.0.1:3101" if a.offline else "http://127.0.0.1:3100")).rstrip("/")
    err = origin_error(web, a.allow_remote)
    if err:
        print(f"refusing --web: {err}", file=sys.stderr)
        return 2
    rep = Report()
    started = dt.datetime.now().astimezone()
    print(f"Walnut DataBridge preflight  {started:%Y-%m-%d %H:%M:%S %Z}  ({'OFFLINE mock' if a.offline else 'live'} {web})")

    # -- 1. services ------------------------------------------------------------------------
    print("Services")
    ok, why = http_ok(f"{web}/login", timeout=15)
    rep.add("services", "web", f"Web app {web}", "pass" if ok else "fail", why)
    if a.offline:
        rep.add("services", "mode", "Mode", "info", "offline mock build: answers are recorded fixtures, no services used")
    else:
        for key, label, url, required in (
                ("ib", "Internal Brain :8100", "http://127.0.0.1:8100/health", True),
                ("mock", "Mock platforms :8200 (Confluence, Jira, Slack, Drive)", "http://127.0.0.1:8200/health", True),
                ("engine", "Walnut engine gateway :8300 (Developer > Brain only)", "http://127.0.0.1:8300/health", False)):
            ok, why = http_ok(url)
            rep.add("services", key, label, "pass" if ok else ("fail" if required else "warn"), why)

    # -- 2. clips ---------------------------------------------------------------------------
    print("Backup clips")
    for name in CLIPS:
        f = WEB_DIR / "public" / "clips" / name
        served, why = http_ok(f"{web}/clips/{name}", timeout=10)
        if f.exists() and served:
            rep.add("clips", name, f"Clip {name[4]}  {name}", "pass", f"{f.stat().st_size / 1e6:.1f} MB, served")
        else:
            rep.add("clips", name, f"Clip {name[4]}  {name}", "fail",
                    "missing on disk" if not f.exists() else f"not served by the web app ({why}); rebuild")

    # -- 3. model, ledger, history (as Maya) --------------------------------------------------
    print("Model and ledger (as maya.compliance)")
    maya = Client(web)
    model_info: dict[str, Any] = {}
    if not maya.sign_in("maya.compliance"):
        rep.add("ledger", "signin", "Sign in as maya.compliance", "fail", "demo sign-in refused")
    else:
        # The board is served only on loopback to a signed-in person (round-4 Opus #2), so check it as Maya.
        ok, why = maya.page_ok("/present/preflight")
        rep.add("services", "preflight_page", "Preflight page /present/preflight", "pass" if ok else "warn",
                why if ok else f"{why} (the web build is older than the demo kit: Stop, then Start, rebuilds it)")
        if a.sim_reset:
            adm = Client(web)
            if adm.sign_in("admin.it"):
                print("  (Simulator reset re-reads every platform: about 1 minute)", flush=True)
                code, body, secs = adm.call("POST", "sim/reset", {}, timeout=300)
                good = code == 200 and isinstance(body, dict) and body.get("ok") is not False
                rep.add("ledger", "sim_reset", "Simulator reset (admin.it)", "pass" if good else "fail",
                        f"{secs:.1f}s" if good else f"HTTP {code} {body}")
                adm.sign_out()
            else:
                rep.add("ledger", "sim_reset", "Simulator reset (admin.it)", "fail", "demo sign-in refused")
        code, st, _ = maya.call("GET", "ib/status", timeout=15)
        if code == 200 and isinstance(st, dict):
            m = st.get("models") or {}
            live = bool(m.get("active"))
            chain = ", ".join(m.get("active_chain") or m.get("chain") or []) or "none"
            model_info = {"live": live, "chain": chain, "force_offline": st.get("force_offline"),
                          "key_configured": st.get("key_configured")}
            if a.offline:
                rep.add("model", "model", "Model", "info", "mock fixtures (offline build)")
            elif live:
                rep.add("model", "model", "Model: Tencent Hunyuan LIVE via TokenHub", "pass", chain)
            else:
                why = "IB_FORCE_OFFLINE=1" if st.get("force_offline") else "no live model"
                rep.add("model", "model", "Model: extractive fallback (quoted from the sources)", "warn",
                        f"{why}; say 'quoted from the sources, with the same checks'. Expected when TokenHub is not live")
        else:
            rep.add("model", "model", "Model status /ib/status", "fail", f"HTTP {code}")
        code, v, secs = maya.call("GET", "audit/verify", timeout=120)
        if code == 200 and isinstance(v, dict):
            good = bool(v.get("ok"))
            rep.add("ledger", "verify", "Audit chain verify (as Maya)", "pass" if good else "fail",
                    f"{'intact' if good else 'BROKEN'}, {v.get('records', '?')} records, {secs:.1f}s",
                    records=v.get("records"))
        else:
            rep.add("ledger", "verify", "Audit chain verify (as Maya)", "fail", f"HTTP {code}")
        code, s, _ = maya.call("GET", "audit/stats?days=30", timeout=60)
        if code == 200 and isinstance(s, dict):
            totals = s.get("totals") or {}
            req_n = totals.get("requests") or 0
            ch = s.get("channels") or s.get("by_channel") or {}
            replay = ch.get("replay") if isinstance(ch, dict) else None
            detail = f"{req_n} requests in the last 30 days" + (f", {replay} seeded replay" if replay else "")
            rep.add("ledger", "history", "Seeded demo history", "pass" if req_n >= 100 else "fail",
                    detail if req_n >= 100 else f"{detail}; reseed with Reset --fresh", requests=req_n)
        else:
            rep.add("ledger", "history", "Seeded demo history", "fail", f"HTTP {code}")

    # -- 4. pre-warm: every scripted question once ----------------------------------------------
    warm: list[dict[str, Any]] = []
    if a.warm:
        print(f"Pre-warm (real asks, pass when under {a.max_s:.0f} s)")
        res: dict[str, dict] = {}

        def ask(key: str, persona: str, who: str, q: str, expect: str) -> Optional[dict]:
            c = Client(web)
            if not c.sign_in(persona):
                rep.add("warm", key, f"{who}: sign-in", "fail", "demo sign-in refused")
                return None
            code, body, secs = c.call("POST", "ib/ask", {"question": q}, timeout=90)
            if code != 200 or not isinstance(body, dict):
                rep.add("warm", key, f"{who}", "fail", f"HTTP {code} after {secs:.1f}s", seconds=round(secs, 2))
                return None
            cites = body.get("citations") or []
            refused = not cites
            right = (expect == "refuse") == refused
            if expect == "answer2":
                right = len(cites) >= 2
            fast = secs <= a.max_s
            model = (body.get("model") or {}).get("model", "?")
            outcome = "refused" if refused else f"answered, {len(cites)} citations"
            status = "pass" if right and fast else "fail"
            detail = f"{secs:.1f}s, {outcome}, model {model}"
            if not right:
                detail += " (NOT what the script expects)"
            if not fast:
                detail += f" (slower than {a.max_s:.0f} s)"
            rep.add("warm", key, who, status, detail, seconds=round(secs, 2), model=model, outcome=outcome)
            warm.append({"key": key, "persona": persona, "seconds": round(secs, 2), "ok": status == "pass"})
            res[key] = body
            if expect == "answer2" and len(cites) >= 2:      # step 3 opens citation [2]: warm the live read
                d = cites[1].get("doc_id", "")
                code2, _, s2 = c.call("GET", f"ib/doc/{urllib.request.quote(d, safe='')}", timeout=30)
                rep.add("warm", "rachel_doc", "Rachel: open citation [2] (sources sheet)",
                        "pass" if code2 == 200 and s2 <= a.max_s else "fail", f"{s2:.1f}s HTTP {code2}")
            c.sign_out()
            return body

        ask("amy", "amy.am", "Amy (account manager): Velquarry", Q_VELQUARRY, "answer")
        rachel = ask("rachel", "mlro.tan", "Rachel (MLRO): Velquarry", Q_VELQUARRY, "answer2")
        kv = ask("ken_v", "ken.ext", "Ken (contractor): Velquarry", Q_VELQUARRY, "refuse")
        kb = ask("ken_b", "ken.ext", "Ken (contractor): Bayfront (made up)", Q_BAYFRONT, "refuse")
        if kv and kb:
            same = same_refusal(kv, kb)
            rep.add("warm", "ken_identical", "Ken's two refusals are identical", "pass" if same else "fail",
                    "same text, citations, scope note and model" if same else "the two refusals differ")
        ask("jordan", "jdoe", "Jordan (WorkBuddy question, in-app)", Q_WORKBUDDY, "answer")
        if rachel and maya.jar:
            rid = rachel.get("request_id", "")
            code, _, secs = maya.call("GET", f"audit/journey/{rid}", timeout=30)
            rep.add("warm", "journey", "Maya: journey of Rachel's request", "pass" if code == 200 and secs <= a.max_s else "fail",
                    f"{secs:.1f}s HTTP {code}")
        if a.offline:
            labelled = sorted({str(i.get("model")) for i in rep.items
                               if i["group"] == "warm" and i.get("model") not in (None, "extractive-v1")})
            if labelled:
                rep.add("warm", "fixture_label", "Fixture answers carry a model label", "warn",
                        f"the mock labels them {', '.join(labelled)}: they are recorded fixtures. Say "
                        "'this is the recorded fixture mode'; never say Hy3 wrote them")
    maya.sign_out()

    # -- 5. clock -----------------------------------------------------------------------------
    now = dt.datetime.now().astimezone()
    off = now.utcoffset() or dt.timedelta(0)
    rep.add("clock", "clock", "Laptop clock", "pass" if off == dt.timedelta(hours=8) else "warn",
            f"{now:%a %d %b %H:%M:%S} UTC{off.total_seconds() / 3600:+.0f}"
            + ("" if off == dt.timedelta(hours=8) else " (Singapore is UTC+8: set the time zone)"))

    out = {"generated_at": now.isoformat(), "mode": "offline" if a.offline else "live", "web": web,
           "ok": rep.ok, "max_s": a.max_s, "warmed": a.warm, "model": model_info, "warm": warm,
           "items": rep.items}
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    fails = [i["label"] for i in rep.items if i["status"] == "fail"]
    print(f"\nPREFLIGHT {'GREEN' if rep.ok else 'RED'}: {len(rep.items) - len(fails)}/{len(rep.items)} OK"
          + (f"; failed: {'; '.join(fails)}" if fails else "") + f"\nWritten to {p}")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())

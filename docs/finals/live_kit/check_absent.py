"""Absence proof for the live-ingestion kit: shows that a sample is NOT in the brain yet.

    python docs/finals/live_kit/check_absent.py shard3-resume        # one sample
    python docs/finals/live_kit/check_absent.py --all                # every sample
    python docs/finals/live_kit/check_absent.py --all --base http://127.0.0.1:8100
    (a non-loopback --base is refused unless --allow-remote, and then only over https)
    python docs/finals/live_kit/check_absent.py --all --offline      # world checks only, no HTTP
    python docs/finals/live_kit/check_absent.py --all --sim          # ALSO call /sim/check-absent (admin.it)

What it checks, per sample (read-only; standard library only):
  1. SETUP   the Slack channel / Drive folder exists in fixtures/enterprise/world.json and the
             sign_in_as person is a member (Slack) or can write (Drive; admin.it may write anywhere).
             The file (upload) exists and is under 1 MB.
  2. WORLD   the proof_phrase occurs nowhere in world.json (case-insensitive, whitespace-collapsed)
             and, for a text sample, the text really contains the proof_phrase.
  3. ASK     over HTTP (POST /auth/demo-login, then POST /ib/ask on 127.0.0.1:8100) the
             before_question gets the constant refusal with no citations, and so do the outsiders
             (Ken, Amy) where the sample says so.
  Ad hoc: --phrase "..." --question "..." --as jdoe checks a judge's own sentence the same way.
  4. SIM     (only with --sim) /sim/check-absent as admin.it returns count 0. Off by default
             because it is a /sim endpoint and writes an audit record; do not run it on the shared
             stack while someone is presenting.

Writes docs/finals/live_kit/ABSENCE_PROOF.txt. Exit code 1 if any check fails.
Only demo sign-in is used (IB_DEMO_LOGIN=1; set IB_DEMO_PASSCODE in the environment if the
server has one). Each sign-in writes one session.started ledger record, nothing else changes.
"""
from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
WORLD = REPO / "databridge" / "fixtures" / "enterprise" / "world.json"
SAMPLES = REPO / "databridge" / "apps" / "internal_brain_web" / "public" / "finals" / "live_samples.json"
OUT = HERE / "ABSENCE_PROOF.txt"
MAX_FILE = 1_000_000
WRITE_ROLES = {"writer", "fileOrganizer", "organizer", "owner"}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).lower()


class World:
    def __init__(self, path: Path):
        self.raw = json.loads(path.read_text(encoding="utf-8"))
        self.text = norm(json.dumps(self.raw, ensure_ascii=False))
        self.chan = {c["id"]: c for c in self.raw["slack"]["channels"]}
        self.folders = {f["id"]: f for f in self.raw["drive"]["folders"]}
        self.groups = {g["id"]: g for g in self.raw["directory"]["groups"]}
        self.users = {u["id"]: u for u in self.raw["directory"]["users"]}

    def group_people(self, gid: str, seen=None) -> set:
        seen = seen or set()
        if gid in seen or gid not in self.groups:
            return set()
        seen.add(gid)
        g = self.groups[gid]
        out = set(g["members"])
        for sub in g["member_groups"]:
            out |= self.group_people(sub, seen)
        return out

    def can_write(self, person: str, folder_id: str) -> bool:
        if "admin_it" in self.users[person]["roles"]:
            return True
        f = self.folders[folder_id]
        domain = self.users[person]["email"].split("@")[1]
        for p in f["permissions"]:
            if p["role"] not in WRITE_ROLES:
                continue
            if p["type"] == "user" and p["principal"] == person:
                return True
            if p["type"] == "group" and person in self.group_people(p["principal"]):
                return True
            if p["type"] == "domain" and p["principal"] == domain:
                return True
        return False


class Client:
    def __init__(self, base: str, persona: str):
        self.base = base.rstrip("/")
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        body = {"persona": persona}
        if os.environ.get("IB_DEMO_PASSCODE"):
            body["passcode"] = os.environ["IB_DEMO_PASSCODE"]
        self.post("/auth/demo-login", body)

    def post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={"content-type": "application/json"})
        with self.op.open(req, timeout=120) as r:
            return json.loads(r.read())


def sim_absent(r: dict) -> tuple[bool, str]:
    """Read ``/sim/check-absent``'s reply (``{"documents_containing": n, "index_size": m}``).
    Passes only when the count is present and exactly 0; a missing or non-integer count fails."""
    n = r.get("documents_containing") if isinstance(r, dict) else None
    ok = isinstance(n, int) and not isinstance(n, bool) and n == 0
    size = r.get("index_size") if isinstance(r, dict) else None
    return ok, f"/sim/check-absent: documents_containing={n} of {size} indexed docs"


def run_sample(s: dict, w: World, base: str | None, use_sim: bool, refusal: str) -> list[tuple[bool, str]]:
    res: list[tuple[bool, str]] = []

    def rec(ok: bool, msg: str) -> None:
        res.append((ok, msg))

    # 1 setup
    who = s["sign_in_as"]
    if s["kind"] == "slack-note":
        ch = w.chan.get(s["channel"]["id"])
        rec(bool(ch) and ch["name"] == s["channel"]["name"], f"channel #{s['channel']['name']} ({s['channel']['id']}) exists in world.json")
        rec(bool(ch) and who in ch["members"], f"{who} is a member of #{s['channel']['name']}")
        rec(s["proof_phrase"].lower() in norm(s["text"]), "the note text contains the proof phrase")
        rec(len(s["text"]) <= 2000, f"note is {len(s['text'])} chars (limit 2000)")
        ask_ok = bool(ch) and s["ask_as"] in ch["members"]
        rec(ask_ok, f"{s['ask_as']} (who asks after ingest) is a member of the channel")
    else:
        fo = w.folders.get(s["folder"]["id"])
        rec(bool(fo) and fo["name"] == s["folder"]["name"], f"folder '{s['folder']['name']}' ({s['folder']['id']}) exists in world.json")
        rec(bool(fo) and w.can_write(who, s["folder"]["id"]), f"{who} can write to '{s['folder']['name']}' (admin.it: anywhere)")
        fp = REPO / s["file"]["path"]
        rec(fp.is_file(), f"file exists: {fp.name}")
        if fp.is_file():
            rec(fp.stat().st_size < MAX_FILE, f"file is {fp.stat().st_size} bytes (< 1 MB)")
    # 2 world
    present = norm(s["proof_phrase"]) in w.text
    rec(not present, f"proof_phrase {s['proof_phrase']!r} is absent from world.json")
    if base is None:
        return res
    # 3 ask
    def refused(persona: str, q: str, label: str) -> None:
        try:
            r = Client(base, persona).post("/ib/ask", {"question": q})
            ok = r.get("answer", "").strip() == refusal and not r.get("citations")
            rec(ok, f"{label}: {persona} asks {q!r} -> " + ("constant refusal, 0 citations" if ok else f"NOT a refusal: {r.get('answer','')[:90]!r}"))
            rec(s["proof_phrase"].lower() not in r.get("answer", "").lower(), f"{label}: answer does not contain the proof phrase")
        except Exception as e:  # noqa: BLE001
            rec(False, f"{label}: ask failed: {type(e).__name__}: {e}")

    refused(s["ask_as"], s["before_question"], "BEFORE")
    ken = s.get("ken", {})
    if ken.get("expect") == "refusal":
        refused("ken.ext", ken["question"], "KEN (before)")
    out = s.get("outsider")
    if out:
        refused(out["persona"], out["question"], "OUTSIDER (before)")
    # 4 sim
    if use_sim:
        try:
            r = Client(base, "admin.it").post("/sim/check-absent", {"phrase": s["proof_phrase"]})
            rec(*sim_absent(r))
        except Exception as e:  # noqa: BLE001
            rec(False, f"/sim/check-absent failed: {type(e).__name__}: {e}")
    return res


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
    ap = argparse.ArgumentParser()
    ap.add_argument("sample", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--base", default="http://127.0.0.1:8100")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--phrase", help="ad hoc: check a judge's own phrase (with --question and --as)")
    ap.add_argument("--question")
    ap.add_argument("--as", dest="persona", default="jdoe")
    ap.add_argument("--allow-remote", action="store_true",
                    help="allow a non-loopback https --base (the demo sign-in and passcode go there)")
    a = ap.parse_args()
    if not a.offline:
        err = origin_error(a.base, a.allow_remote)
        if err:
            print(f"refusing --base: {err}", file=sys.stderr)
            return 2
    if a.phrase:                       # ad hoc check for a templated, judge-supplied sentence
        w = World(WORLD)
        absent = norm(a.phrase) not in w.text
        print(f"phrase {a.phrase!r} absent from world.json: {absent}")
        ok = absent
        if a.question and not a.offline:
            r = Client(a.base, a.persona).post("/ib/ask", {"question": a.question})
            refused = r.get("answer", "").strip().startswith("I couldn't find an answer") and not r.get("citations")
            print(f"{a.persona} asks {a.question!r} -> {'constant refusal' if refused else r.get('answer','')[:120]}")
            ok = ok and refused
        return 0 if ok else 1
    data = json.loads(SAMPLES.read_text(encoding="utf-8"))
    w = World(WORLD)
    sel = data["samples"] if a.all or not a.sample else [s for s in data["samples"] if s["id"] == a.sample]
    if not sel:
        print("no such sample; ids:", ", ".join(s["id"] for s in data["samples"]))
        return 2
    base = None if a.offline else a.base
    lines = [f"ABSENCE PROOF  {datetime.now().astimezone().isoformat(timespec='seconds')}",
             f"world.json sha256 {hashlib.sha256(WORLD.read_bytes()).hexdigest()[:16]}   samples {SAMPLES.name}",
             f"mode: {'offline (world checks only)' if base is None else 'HTTP ' + base}{'  + /sim/check-absent' if a.sim else ''}",
             f"refusal text: {data['refusal']}", ""]
    bad = 0
    for s in sel:
        r = run_sample(s, w, base, a.sim, data["refusal"])
        fails = sum(1 for ok, _ in r if not ok)
        bad += fails
        lines.append(f"[{'PASS' if not fails else 'FAIL'}] {s['id']}  ({s['kind']}, {s['category']})")
        lines += [f"    {'ok  ' if ok else 'FAIL'} {m}" for ok, m in r]
        lines.append("")
    lines.append("RESULT: " + ("all checks passed" if not bad else f"{bad} check(s) FAILED"))
    text = "\n".join(lines) + "\n"
    OUT.write_text(text, encoding="utf-8")
    sys.stdout.buffer.write(text.encode("utf-8", "replace"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

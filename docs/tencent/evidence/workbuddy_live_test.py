"""Drive the WorkBuddy MCP connectors exactly as WorkBuddy does (stdio, one process per connector,
identity only from IB_TOKEN) against the LIVE Internal Brain, call every tool, check the results,
and write a Markdown transcript.

    databridge/.venv/Scripts/python.exe docs/tencent/evidence/workbuddy_live_test.py \
        --tokens <dir with jdoe.tok, ken.ext.tok, maya.compliance.tok> --out <transcript.md>

Mint the tokens first (never print them):
    databridge/.venv/Scripts/python.exe databridge/scripts/ib_mint_token.py jdoe --out <dir>/jdoe.tok
Tokens are read from files and passed only in the child process environment; they are never
written to the transcript. The client is the official ``mcp`` Python SDK (stdio_client).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parents[3]
DATABRIDGE = REPO / "databridge"
PY = DATABRIDGE / ".venv" / "Scripts" / "python.exe"
IB_URL = os.environ.get("IB_URL", "http://127.0.0.1:8100")

HEADLINE = ("What's the status of the database migration project and were there any blockers raised "
            "in Slack last week?")
RESTRICTED = "Why is Velquarry Trading's account restricted?"
MADE_UP = "Why is Bayfront Spice Traders' account restricted?"

OUT: list[str] = []
CHECKS: list[tuple[str, bool, str]] = []


def say(line: str = "") -> None:
    OUT.append(line)
    print(line, flush=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    say(f"**CHECK {'PASS' if ok else 'FAIL'}**: {name}" + (f" ({detail})" if detail else ""))


def _text(result) -> str:
    parts = [c.text for c in (result.content or []) if getattr(c, "type", "") == "text"]
    return "\n".join(parts)


class Connector:
    """One WorkBuddy connector = one stdio MCP server process with one person's token."""

    def __init__(self, name: str, module: str, token: str):
        self.name, self.module, self.token = name, module, token

    def params(self) -> StdioServerParameters:
        env = dict(os.environ)
        env.update({"PYTHONPATH": "apps/internal_brain", "IB_URL": IB_URL, "IB_TOKEN": self.token})
        return StdioServerParameters(command=str(PY), args=["-m", self.module], env=env,
                                     cwd=str(DATABRIDGE))

    async def run(self, calls):
        """``calls``: async callable(session) -> None."""
        async with stdio_client(self.params()) as (r, w):
            async with ClientSession(r, w) as s:
                info = await s.initialize()
                tools = (await s.list_tools()).tools
                say(f"Server `{info.serverInfo.name}` started over stdio for connector "
                    f"`{self.name}` ({self.module}). Tools: "
                    + ", ".join(f"`{t.name}({', '.join((t.inputSchema or {}).get('properties', {}))})`"
                                for t in tools))
                say()
                for t in tools:
                    props = set((t.inputSchema or {}).get("properties", {}))
                    bad = props & {"user", "persona", "sub", "principal", "identity"}
                    if t.name != "audit_events":           # its `user` is a log filter, not identity
                        check(f"{self.name}: `{t.name}` takes no identity argument", not bad,
                              ", ".join(sorted(props)) or "no arguments")
                await calls(s, {t.name for t in tools})


async def call(s: ClientSession, conn: str, tool: str, args: dict, *, timeout: float = 180) -> str:
    t0 = time.monotonic()
    res = await asyncio.wait_for(s.call_tool(tool, args), timeout)
    ms = int((time.monotonic() - t0) * 1000)
    txt = _text(res)
    say(f"#### `{conn}` → `{tool}({json.dumps(args, ensure_ascii=False)[1:-1]})`  ({ms} ms)")
    say()
    say("```text")
    say(txt)
    say("```")
    say()
    return txt


def _ledger_event(maya_token: str, req_id: str) -> dict | None:
    """Read the request.accepted record of ``req_id`` straight from the audit API (as Maya)."""
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
    r = httpx.get(f"{IB_URL}/audit/events", params={"user": "jdoe", "kind": "request.accepted",
                                                    "since": since, "limit": 200},
                  headers={"Authorization": f"Bearer {maya_token}"}, timeout=30)
    r.raise_for_status()
    for e in r.json().get("events", []):
        if e.get("req_id") == req_id:
            return e
    return None


async def main_async(tokens: Path) -> None:
    tok = {p: (tokens / f"{p}.tok").read_text(encoding="utf-8").strip()
           for p in ("jdoe", "ken.ext", "maya.compliance")}
    jdoe = Connector("ib-as-jdoe", "databridge_ib.mcp_server", tok["jdoe"])
    ken = Connector("ib-as-ken", "databridge_ib.mcp_server", tok["ken.ext"])
    maya = Connector("ib-compliance", "databridge_ib.audit_mcp_server", tok["maya.compliance"])
    jdoe_audit = Connector("audit server with jdoe's token (negative test)",
                           "databridge_ib.audit_mcp_server", tok["jdoe"])
    state: dict = {}

    say("### 1. Jordan (`jdoe`, payments engineer) through `ib-as-jdoe`")
    say()

    async def as_jdoe(s, names):
        a = await call(s, "ib-as-jdoe", "ask_internal_brain", {"question": HEADLINE})
        cites = [ln for ln in a.splitlines() if ln.startswith("  - [")]
        check("Jordan's headline answer is cited", len(cites) >= 1, f"{len(cites)} citation(s)")
        plats = {m.group(1) for ln in cites if (m := re.match(r"  - \[(\w+)\]", ln))}
        say(f"Platforms cited: {', '.join(sorted(plats)) or 'none'}")
        check("answered as jdoe (identity from the token)", "answered as jdoe" in a)
        m = re.search(r"request id: (\S+)", a)
        state["req"] = m.group(1) if m else None
        check("the answer carries its request id", bool(state["req"]))
        await call(s, "ib-as-jdoe", "my_recent_questions", {})
        if "my_answer_receipt" in names and state["req"]:
            r = await call(s, "ib-as-jdoe", "my_answer_receipt", {"request_id": state["req"]})
            check("Jordan's receipt for his own answer", "No receipt found" not in r
                  and "database migration" in r)
        if "what_can_i_open" in names:
            w = await call(s, "ib-as-jdoe", "what_can_i_open", {})
            check("what_can_i_open lists no document ids", ":cf-" not in w and "jira:" not in w
                  and "slack:" not in w and "drive:" not in w)

    await jdoe.run(as_jdoe)

    say("### 2. Ken (`ken.ext`, external contractor) through `ib-as-ken`")
    say()

    async def as_ken(s, names):
        r1 = await call(s, "ib-as-ken", "ask_internal_brain", {"question": RESTRICTED})
        r2 = await call(s, "ib-as-ken", "ask_internal_brain", {"question": MADE_UP})
        body = lambda t: [ln for ln in t.splitlines() if not ln.startswith("request id:")]
        check("Ken's restricted question gets the constant refusal (no citations)",
              "(no citations)" in r1 and "answered as ken.ext" in r1)
        check("restricted (Velquarry) and made-up (Bayfront) replies are identical apart from "
              "the request id", body(r1) == body(r2))
        if "my_answer_receipt" in names:
            other = await call(s, "ib-as-ken", "my_answer_receipt", {"request_id": state.get("req") or "x"})
            unknown = await call(s, "ib-as-ken", "my_answer_receipt",
                                 {"request_id": "00000000000000000000000000000000"})
            check("Ken asking for Jordan's receipt == Ken asking for an unknown id", other == unknown)
        if "what_can_i_open" in names:
            await call(s, "ib-as-ken", "what_can_i_open", {})

    await ken.run(as_ken)

    say("### 3. Maya (`maya.compliance`, compliance officer) through `ib-compliance`")
    say()
    doc = None
    m = httpx.get(f"{IB_URL}/ib/map", headers={"Authorization": f"Bearer {tok['jdoe']}"}, timeout=60)
    if m.status_code == 200:
        for p in m.json().get("platforms", []):
            for c in p.get("containers", []):
                for it in c.get("items", []):
                    doc = doc or it.get("doc_id")

    async def as_maya(s, names):
        if "org_activity_summary" in names:
            o = await call(s, "ib-compliance", "org_activity_summary", {"days": 30})
            check("org_activity_summary works for the officer", "requests" in o.lower()
                  and "error" not in o.lower())
            check("the WorkBuddy channel shows in the org summary", "workbuddy-mcp" in o)
        v = await call(s, "ib-compliance", "audit_verify", {})
        check("audit chain verifies (before the tamper demo)", "verification: OK" in v)
        await call(s, "ib-compliance", "audit_events", {"user": "jdoe", "since_days": 1})
        if doc:
            await call(s, "ib-compliance", "audit_doc_accessors", {"doc_id": doc})
        if "run_tamper_demo" in names:
            t = await call(s, "ib-compliance", "run_tamper_demo", {})
            check("tamper demo ran on a copy; real log not modified",
                  "real log modified: no" in t.lower())
            v2 = await call(s, "ib-compliance", "audit_verify", {})
            check("the real audit chain still verifies after the tamper demo",
                  "verification: OK" in v2)

    await maya.run(as_maya)

    say("### 4. Negative test: the audit server with an employee's token")
    say()

    async def as_jdoe_audit(s, names):
        v = await call(s, "jdoe on audit server", "audit_verify", {})
        check("an employee token is refused on audit_verify", "HTTP 403" in v)
        if "org_activity_summary" in names:
            o = await call(s, "jdoe on audit server", "org_activity_summary", {"days": 30})
            check("an employee token is refused on org_activity_summary", "HTTP 403" in o)
        if "run_tamper_demo" in names:
            t = await call(s, "jdoe on audit server", "run_tamper_demo", {})
            check("an employee token is refused on run_tamper_demo", "HTTP 403" in t)

    await jdoe_audit.run(as_jdoe_audit)

    say("### 5. The ledger record of Jordan's WorkBuddy question")
    say()
    ev = _ledger_event(tok["maya.compliance"], state.get("req") or "") if state.get("req") else None
    if ev:
        say("```json")
        say(json.dumps({k: ev.get(k) for k in ("seq", "kind", "req_id", "sub", "event_time")}
                       | {"channel": (ev.get("event") or {}).get("channel"),
                          "hash": (ev.get("hash") or "")[:12] + "…"}, indent=2))
        say("```")
    check("the ledger shows channel workbuddy-mcp for Jordan's question",
          bool(ev) and (ev.get("event") or {}).get("channel") == "workbuddy-mcp")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--tokens", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    started = dt.datetime.now(dt.timezone.utc).astimezone()
    asyncio.run(main_async(a.tokens))
    passed = sum(1 for _, ok, _ in CHECKS if ok)
    head = [
        "# WorkBuddy connectors: live test against the running Internal Brain",
        "",
        f"Run {started:%Y-%m-%d %H:%M %Z} against `{IB_URL}` by "
        "`docs/tencent/evidence/workbuddy_live_test.py`. Each connector is started exactly as "
        "WorkBuddy starts it (stdio, `python -m <module>`, identity only from `IB_TOKEN`) and driven "
        "with the official `mcp` Python client. Tokens were minted fresh with "
        "`databridge/scripts/ib_mint_token.py --out` and are not in this file.",
        "",
        f"**Result: {passed} of {len(CHECKS)} checks passed.**",
        "",
        "| Check | Result |",
        "|---|---|",
        *[f"| {n} | {'PASS' if ok else 'FAIL'}{(' (' + d + ')') if d else ''} |" for n, ok, d in CHECKS],
        "",
        "## Transcript",
        "",
    ]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("\n".join(head + OUT) + "\n", encoding="utf-8")
    print(f"\n{passed}/{len(CHECKS)} checks passed; transcript written to {a.out}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())

"""WORKAROUND for E2E finding (the compiler catalogue can never see a write connection's tools):
list each connection's tools LIVE over MCP (databridge_mcp_ingest.open_session) and write them to
`<WALNUT_SOURCES_DIR>/<id>.tools.yaml` — the side file `automation.catalogue.load_connection_info`
reads but nothing in the product writes. Also sets `purpose: agent_write` on connections given with
--agent-write (the infra declaration of larkspur-followups carries no purpose).

    python scripts/e2e/write_tool_adverts.py larkspur-followups larkspur_db_writer --agent-write larkspur-followups
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import setup_env  # noqa: E402

E = setup_env()
import yaml  # noqa: E402
from databridge_mcp_ingest import open_session  # noqa: E402

args = sys.argv[1:]
aw = set()
if "--agent-write" in args:
    i = args.index("--agent-write")
    aw = set(args[i + 1:])
    args = args[:i]


async def one(cid: str) -> list[dict]:
    async with open_session(cid) as s:
        tools = await s.list_tools()
    out = []
    for t in tools:
        d = t if isinstance(t, dict) else (t.model_dump() if hasattr(t, "model_dump") else vars(t))
        ann = d.get("annotations") or {}
        if not isinstance(ann, dict):
            ann = ann.model_dump() if hasattr(ann, "model_dump") else vars(ann)
        out.append({"name": d.get("name"), "description": (d.get("description") or "")[:300],
                    "read_only": ann.get("readOnlyHint"), "destructive": ann.get("destructiveHint")})
    return out


report = {}
for cid in args:
    tools = asyncio.run(one(cid))
    (E / "sources" / f"{cid}.tools.yaml").write_text(
        yaml.safe_dump({"tools": tools, "source": "e2e workaround: live tools/list"}, sort_keys=False),
        encoding="utf-8")
    report[cid] = tools
    print(cid, [(t["name"], t["read_only"], t["destructive"]) for t in tools])
for cid in aw:
    p = E / "sources" / f"{cid}.yaml"
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    d["purpose"] = "agent_write"
    p.write_text(yaml.safe_dump(d, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print("purpose agent_write ->", cid)
(E / "artefacts" / "write_tool_adverts.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

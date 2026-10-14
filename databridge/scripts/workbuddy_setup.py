"""Write the three WorkBuddy demo workspaces, each with a project-level .workbuddy/mcp.json.

    python databridge/scripts/workbuddy_setup.py [--out DIR] [--hours N] [--ib-url URL]

Each workspace acts as ONE persona: its MCP server runs databridge_ib.mcp_server (or the audit
server for compliance) with that persona's short-lived bearer token, minted by ib_mint_token.py
from IB_TOKEN_KEY in databridge/.env.ib.local. Tokens are secrets, so the default output folder
is OUTSIDE the repository (<repo>/../workbuddy-demo). Re-run it to refresh tokens (max 8 h).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

DATABRIDGE = Path(__file__).resolve().parents[1]
REPO = DATABRIDGE.parent
WORKSPACES = (
    # folder, persona, server module, what to ask in the demo
    ("IB as jdoe", "jdoe", "databridge_ib.mcp_server",
     "What's the status of the database migration project and were there any blockers raised in "
     "Slack last week?"),
    ("IB as ken.ext", "ken.ext", "databridge_ib.mcp_server",
     "What's the status of the database migration project and were there any blockers raised in "
     "Slack last week?"),
    ("IB compliance", "maya.compliance", "databridge_ib.audit_mcp_server",
     "Show me everything jdoe accessed in the payment-gateway space in the last 30 days, then "
     "verify the audit chain."),
)


def _python() -> Path:
    for p in (DATABRIDGE / ".venv" / "Scripts" / "python.exe", DATABRIDGE / ".venv" / "bin" / "python"):
        if p.exists():
            return p
    return Path(sys.executable)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=REPO.parent / "workbuddy-demo")
    ap.add_argument("--hours", type=int, default=8)
    ap.add_argument("--ib-url", default="http://127.0.0.1:8100")
    args = ap.parse_args(argv)
    py = _python()
    for folder, persona, module, question in WORKSPACES:
        token = subprocess.run([str(py), str(DATABRIDGE / "scripts" / "ib_mint_token.py"), persona,
                                "--hours", str(args.hours)], capture_output=True, text=True,
                               check=True).stdout.strip()
        ws = args.out / folder
        (ws / ".workbuddy").mkdir(parents=True, exist_ok=True)
        name = "internal-brain-audit" if module.endswith("audit_mcp_server") else "internal-brain"
        cfg = {"mcpServers": {name: {
            "command": py.as_posix(),
            "args": ["-m", module],
            "cwd": DATABRIDGE.as_posix(),
            "env": {"PYTHONPATH": (DATABRIDGE / "apps" / "internal_brain").as_posix(),
                    "IB_URL": args.ib_url, "IB_TOKEN": token},
        }}}
        (ws / ".workbuddy" / "mcp.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        (ws / "DEMO_QUESTION.txt").write_text(
            f"This WorkBuddy workspace talks to the Walnut Internal Brain as '{persona}'.\n"
            f"Ask:\n{question}\n", encoding="utf-8")
        print(f"wrote {ws / '.workbuddy' / 'mcp.json'}  (persona {persona}, {module})")
    print(f"tokens expire in {min(args.hours, 8)} h; re-run this script to refresh them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

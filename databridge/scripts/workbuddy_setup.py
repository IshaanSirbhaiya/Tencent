"""Write the three WorkBuddy demo workspaces, each with a project-level .workbuddy/mcp.json.

    python databridge/scripts/workbuddy_setup.py [--out DIR] [--hours N] [--ib-url URL] [--user-config]

Each workspace acts as ONE persona: its MCP server runs databridge_ib.mcp_server (or the audit
server for compliance) with that persona's short-lived bearer token, minted by ib_mint_token.py
from IB_TOKEN_KEY in databridge/.env.ib.local. Tokens are secrets, so the default output folder
is OUTSIDE the repository (<repo>/../workbuddy-demo). Re-run it to refresh tokens (max 8 h).

``--user-config`` also writes all three as WorkBuddy's own connector list
(``~/.workbuddy-ai/mcp.json``: ib-as-jdoe, ib-as-ken, ib-compliance), so one WorkBuddy chat can use
them without opening a folder. Restart WorkBuddy, then press Trust on each in
Experts · Skills · Connectors > Custom connectors. Any other servers already in that file are kept.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DATABRIDGE = Path(__file__).resolve().parents[1]
REPO = DATABRIDGE.parent
sys.path.insert(0, str(DATABRIDGE / "scripts"))
from ib_mint_token import mint_for  # noqa: E402  (tokens never pass through stdout)

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


#: names of the connectors in WorkBuddy's own connector list (--user-config)
USER_NAMES = {"jdoe": "ib-as-jdoe", "ken.ext": "ib-as-ken", "maya.compliance": "ib-compliance"}


def _python() -> Path:
    for p in (DATABRIDGE / ".venv" / "Scripts" / "python.exe", DATABRIDGE / ".venv" / "bin" / "python"):
        if p.exists():
            return p
    return Path(sys.executable)


def _owner_only(path: Path) -> None:
    """Best effort: only the current user may read a file holding a token."""
    if os.name == "nt":
        user = os.environ.get("USERNAME", "")
        if user:
            subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                           capture_output=True, check=False)
    else:
        os.chmod(path, 0o600)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=REPO.parent / "workbuddy-demo")
    ap.add_argument("--hours", type=int, default=8)
    ap.add_argument("--ib-url", default="http://127.0.0.1:8100")
    ap.add_argument("--user-config", action="store_true",
                    help="also write the three connectors to ~/.workbuddy-ai/mcp.json")
    args = ap.parse_args(argv)
    out = args.out.resolve()
    if out == REPO or REPO in out.parents:          # tokens are secrets: never inside the repo
        ap.error(f"--out must be outside the repository ({REPO})")
    py = _python()
    user_servers = {}
    for folder, persona, module, question in WORKSPACES:
        token = mint_for(persona, args.hours)
        ws = out / folder
        (ws / ".workbuddy").mkdir(parents=True, exist_ok=True)
        name = "internal-brain-audit" if module.endswith("audit_mcp_server") else "internal-brain"
        cfg = {"mcpServers": {name: {
            "command": py.as_posix(),
            "args": ["-m", module],
            "cwd": DATABRIDGE.as_posix(),
            "env": {"PYTHONPATH": (DATABRIDGE / "apps" / "internal_brain").as_posix(),
                    "IB_URL": args.ib_url, "IB_TOKEN": token},
        }}}
        user_servers[USER_NAMES[persona]] = cfg["mcpServers"][name]
        cfg_path = ws / ".workbuddy" / "mcp.json"
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        _owner_only(cfg_path)
        (ws / "DEMO_QUESTION.txt").write_text(
            f"This WorkBuddy workspace talks to the Walnut Internal Brain as '{persona}'.\n"
            f"Ask:\n{question}\n", encoding="utf-8")
        print(f"wrote {ws / '.workbuddy' / 'mcp.json'}  (persona {persona}, {module})")
    if args.user_config:
        user_cfg = Path.home() / ".workbuddy-ai" / "mcp.json"
        user_cfg.parent.mkdir(parents=True, exist_ok=True)
        current = json.loads(user_cfg.read_text(encoding="utf-8")) if user_cfg.exists() else {}
        current.setdefault("mcpServers", {}).update(user_servers)
        user_cfg.write_text(json.dumps(current, indent=2), encoding="utf-8")
        _owner_only(user_cfg)
        print(f"wrote {user_cfg}  ({', '.join(user_servers)}); restart WorkBuddy and press Trust on each")
    print(f"tokens expire in {min(args.hours, 8)} h; re-run this script to refresh them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

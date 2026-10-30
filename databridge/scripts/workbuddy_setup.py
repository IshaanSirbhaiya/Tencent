"""Write the three WorkBuddy demo workspaces, each with a project-level .workbuddy/mcp.json.

    python databridge/scripts/workbuddy_setup.py [--out DIR] [--hours N] [--ib-url URL]
                                                 [--user-config | --compliance-user-config]

Each workspace acts as ONE persona: its MCP server runs databridge_ib.mcp_server (or the audit
server for compliance) with that persona's short-lived bearer token, minted by ib_mint_token.py
from IB_TOKEN_KEY in databridge/.env.ib.local. Tokens are secrets, so the default output folder
is OUTSIDE the repository (<repo>/../workbuddy-demo). Re-run it to refresh tokens (max 8 h).

``--user-config`` also writes the two EMPLOYEE connectors as WorkBuddy's own connector list
(``~/.workbuddy-ai/mcp.json``: ib-as-jdoe, ib-as-ken), so one WorkBuddy chat can use them without
opening a folder. It never writes the compliance connector there, and it removes an ``ib-compliance``
entry an older run left behind (round 2b finding 2: source text an employee connector returns must
not be able to steer the same agent into the audit tools). The compliance seat uses its own
workspace folder ("IB compliance"), or ``--compliance-user-config`` on a separate machine or user
account: that writes ONLY ib-compliance and removes the employee connectors from that list. The two
flags cannot be combined. Restart WorkBuddy, then press Trust on each in
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
#: the compliance connector is never written next to the employee connectors
COMPLIANCE_NAMES = frozenset({"ib-compliance"})
EMPLOYEE_NAMES = frozenset(USER_NAMES.values()) - COMPLIANCE_NAMES


def merge_user_servers(current: dict, user_servers: dict, *, compliance: bool) -> dict:
    """Return the new ``mcpServers`` map for WorkBuddy's own connector list.

    Employee mode (``compliance=False``) adds only the employee connectors and drops any compliance
    connector; compliance mode adds only the compliance connector and drops the employee ones. So one
    WorkBuddy connector list never holds both an employee identity and the audit identity. Servers
    that are not ours are kept untouched.
    """
    keep, drop = (COMPLIANCE_NAMES, EMPLOYEE_NAMES) if compliance else (EMPLOYEE_NAMES, COMPLIANCE_NAMES)
    servers = {k: v for k, v in (current.get("mcpServers") or {}).items() if k not in drop}
    servers.update({k: v for k, v in user_servers.items() if k in keep})
    return servers


def _python() -> Path:
    for p in (DATABRIDGE / ".venv" / "Scripts" / "python.exe", DATABRIDGE / ".venv" / "bin" / "python"):
        if p.exists():
            return p
    return Path(sys.executable)


def _owner_only(path: Path) -> None:
    """Only the current user may read a file holding a token; says so if that could not be set."""
    if os.name == "nt":
        user = os.environ.get("USERNAME", "")
        domain = os.environ.get("USERDOMAIN", "")
        who = f"{domain}\\{user}" if domain and user else user
        ok = bool(who) and subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{who}:F"],
                                          capture_output=True, check=False).returncode == 0
        if not ok:
            print(f"WARNING: could not restrict {path} to your account; it holds tokens, keep it private")
    else:
        os.chmod(path, 0o600)


def _write_private(path: Path, text: str) -> None:
    """Write via a temporary file in the same folder, restrict it, then swap it in, so a crash never
    leaves a half-written config and the file is never readable by others, even briefly."""
    tmp = path.with_name(path.name + ".tmp")
    if os.name == "nt":
        tmp.write_text(text, encoding="utf-8")
    else:
        fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    _owner_only(tmp)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=REPO.parent / "workbuddy-demo")
    ap.add_argument("--hours", type=int, default=8)
    ap.add_argument("--ib-url", default="http://127.0.0.1:8100")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--user-config", action="store_true",
                      help="also write the two employee connectors (ib-as-jdoe, ib-as-ken) to "
                           "~/.workbuddy-ai/mcp.json; never the compliance connector")
    mode.add_argument("--compliance-user-config", action="store_true",
                      help="compliance seat only (a separate machine or account): write ONLY "
                           "ib-compliance to ~/.workbuddy-ai/mcp.json and remove the employee connectors")
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
        _write_private(cfg_path, json.dumps(cfg, indent=2))
        (ws / "DEMO_QUESTION.txt").write_text(
            f"This WorkBuddy workspace talks to the Walnut Internal Brain as '{persona}'.\n"
            f"Ask:\n{question}\n", encoding="utf-8")
        print(f"wrote {ws / '.workbuddy' / 'mcp.json'}  (persona {persona}, {module})")
    if args.user_config or args.compliance_user_config:
        compliance = bool(args.compliance_user_config)
        user_cfg = Path.home() / ".workbuddy-ai" / "mcp.json"
        user_cfg.parent.mkdir(parents=True, exist_ok=True)
        current = json.loads(user_cfg.read_text(encoding="utf-8")) if user_cfg.exists() else {}
        if not isinstance(current, dict) or not isinstance(current.get("mcpServers", {}), dict):
            ap.error(f"{user_cfg} is not a WorkBuddy MCP config; fix or move it first")
        current["mcpServers"] = merge_user_servers(current, user_servers, compliance=compliance)
        _write_private(user_cfg, json.dumps(current, indent=2))
        written = sorted(COMPLIANCE_NAMES if compliance else EMPLOYEE_NAMES)
        print(f"wrote {user_cfg}  ({', '.join(written)}); restart WorkBuddy and press Trust on each")
        if compliance:
            print("note: this connector list now holds only the compliance connector; keep it on the "
                  "compliance seat, never on an employee's WorkBuddy.")
        else:
            print("note: the compliance connector is NOT in this list (use the 'IB compliance' workspace "
                  "folder, or --compliance-user-config on the compliance seat). These connectors are "
                  "visible in every WorkBuddy chat for up to 8 h; each only ever answers as its own "
                  "person. Remove them after the demo.")
    print(f"tokens expire in {min(args.hours, 8)} h; re-run this script to refresh them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

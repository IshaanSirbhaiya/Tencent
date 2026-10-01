"""E2E3: create a fresh run directory deployed like a second instance (run-2 layout, plus INT5/FXJ3 auth).

    python scripts/e2e/run3_setup.py            -> prints the run dir (var/e2e-<UTC timestamp>)

Layout: <run>/root (WALNUT_ROOT) with `packages` a junction to the code and
`fixtures/workspace_demo/files` a private corpus copy; <run>/mcp_servers.yaml a verbatim copy of the
operator's manifest (read through WALNUT_MCP_SERVERS -> declarations_path, the N-22 fix);
<run>/auth/{tokens,api_tokens,federated_grants,operators}.json with THREE principals (analyst,
second, operator) and random tokens; <run>/keys/approval.key (256 random bits). Tokens and the key
are never printed.
"""
from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> Path:
    ts = time.strftime("%Y%m%dT%H%M", time.gmtime())
    E = ROOT / "var" / f"e2e-{ts}"
    if E.exists():
        sys.exit(f"{E} exists")
    dep = E / "root"
    (dep / "fixtures" / "workspace_demo").mkdir(parents=True)
    (dep / "var").mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(dep / "packages"), str(ROOT / "packages")],
                   check=True, capture_output=True)
    shutil.copytree(ROOT / "fixtures" / "workspace_demo" / "files",
                    dep / "fixtures" / "workspace_demo" / "files")
    shutil.copy2(ROOT / "infra" / "mcp_servers.yaml", E / "mcp_servers.yaml")
    auth = E / "auth"
    auth.mkdir()
    import os
    pre = os.environ.get("WALNUT_E2E_PREFIX", "e2e3")   # E2E4: run 4 sets e2e4
    roles = {"primary": f"{pre}-analyst", "second": f"{pre}-second", "operator": f"{pre}-operator"}
    toks = {r: secrets.token_urlsafe(32) for r in roles}
    (auth / "tokens.json").write_text(json.dumps(toks), encoding="utf-8")
    (auth / "api_tokens.json").write_text(json.dumps({toks[r]: p for r, p in roles.items()}),
                                          encoding="utf-8")
    (auth / "federated_grants.json").write_text(json.dumps({roles["primary"]: "*"}),
                                                encoding="utf-8")
    (auth / "operators.json").write_text(json.dumps([roles["operator"]]), encoding="utf-8")
    (E / "keys").mkdir()
    (E / "keys" / "approval.key").write_text(secrets.token_hex(32), encoding="utf-8")
    for d in ("artefacts", "logs"):
        (E / d).mkdir()
    print(E)
    return E


if __name__ == "__main__":
    main()

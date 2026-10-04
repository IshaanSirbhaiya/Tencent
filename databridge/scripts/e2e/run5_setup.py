"""E2E5: a fresh run dir per estate, deployed like a second instance.

    python scripts/e2e/run5_setup.py workspace|finance   -> prints var/e2e5-<estate>-<UTC ts>

Same layout and auth as run3_setup.py (three principals: analyst, second, operator; operator-mode
registration; a run-local approval key), generalised to both estates:
- workspace: <run>/root/fixtures/workspace_demo/files (private corpus copy), manifest
  infra/mcp_servers.yaml copied verbatim to <run>/mcp_servers.yaml.
- finance: <run>/root/fixtures/packs/finance (private copy of the synthetic finance estate),
  manifest infra/mcp_servers.finance.yaml copied verbatim to <run>/mcp_servers.yaml, so its
  ${WALNUT_ROOT} paths (incl. the follow-ups write root) land inside the run, never var/finance.
Tokens and the key are never printed.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

LAYOUT = {
    "workspace": ("infra/mcp_servers.yaml", "fixtures/workspace_demo/files"),
    "finance": ("infra/mcp_servers.finance.yaml", "fixtures/packs/finance"),
}


def main(estate: str) -> Path:
    manifest, corpus = LAYOUT[estate]
    ts = time.strftime("%Y%m%dT%H%M", time.gmtime())
    E = ROOT / "var" / f"e2e5-{estate}-{ts}"
    if E.exists():
        sys.exit(f"{E} exists")
    dep = E / "root"
    dep.mkdir(parents=True)
    (dep / "var").mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(dep / "packages"), str(ROOT / "packages")],
                   check=True, capture_output=True)
    shutil.copytree(ROOT / corpus, dep / corpus)
    shutil.copy2(ROOT / manifest, E / "mcp_servers.yaml")
    auth = E / "auth"
    auth.mkdir()
    pre = os.environ.get("WALNUT_E2E_PREFIX", "e2e5")
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
    (E / "estate.txt").write_text(estate, encoding="utf-8")
    print(E)
    return E


if __name__ == "__main__":
    main(sys.argv[1])

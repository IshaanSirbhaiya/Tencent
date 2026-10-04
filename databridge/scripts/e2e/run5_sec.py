"""E2E5 scenario 11: security probes (run 3's, plus the FEAT8 routes). -> <run>/artefacts/s11_sec.json"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("WALNUT_E2E_BASE", "http://127.0.0.1:8015")
from e2e_env import setup_env  # noqa: E402

E = setup_env()
import httpx  # noqa: E402
from e2e_client import BASE, call, tokens  # noqa: E402

DISCLOSING = [("GET", "/graph/search?q=a"), ("POST", "/brain/query"), ("GET", "/brain/graph"),
              ("GET", "/activity"), ("GET", "/agents"), ("GET", "/agents/approvals"),
              ("GET", "/sources"), ("GET", "/adapter-agents"), ("GET", "/materialise/jobs"),
              ("POST", "/federated/ask"), ("GET", "/platform/catalogue"), ("GET", "/intelligence"),
              ("GET", "/source-batch/plans"), ("POST", "/source-batch/plans/propose"),
              ("POST", "/source-batch/runs"), ("GET", "/adapter-setup/sources"),
              ("POST", "/intelligence/one-time/compile"), ("GET", "/materialise/designs"),
              ("POST", "/materialise/designs"), ("GET", "/ontology/identity-rules"),
              ("GET", "/activity/automations")]


def main():
    out: dict = {"no_token": {}}
    for m, p in DISCLOSING:
        body = {"question": "x", "text": "x", "request": "x"} if m == "POST" else None
        out["no_token"][f"{m} {p}"] = call(m, p, body, name="s11_notoken", token="")[0]
    out["bad_token"] = call("GET", "/sources", name="s11_badtoken", token="not-a-real-token")[0]
    with httpx.Client(timeout=60) as c:
        r = c.post(BASE + "/source-batch/plans/approve", content=json.dumps("approve all"),
                   headers={"Authorization": f"Bearer {tokens()['primary']}",
                            "Content-Type": "application/json"})
        out["batch_approval_plain_string"] = {"status": r.status_code, "body": r.text[:300]}
    from databridge_mcp_ingest.plans import require_authenticated_approver  # noqa: PLC0415
    try:
        require_authenticated_approver("alice", what="e2e5 check")
        out["package_typed_name"] = "ACCEPTED"
    except Exception as exc:  # noqa: BLE001
        out["package_typed_name"] = f"refused: {type(exc).__name__}"
    st, d, _ = call("POST", "/sources", {"mode": "url", "id": "e2e5_leaky_url",
                                         "url": "https://example.com/mcp?api_key=SUPERSECRETVALUE7654321"},
                    name="s11_literal_secret")
    out["literal_secret"] = {"status": st, "code": (d.get("refusal") or {}).get("code"),
                             "echoed": "SUPERSECRETVALUE7654321" in json.dumps(d)}
    for host in ("http://10.0.0.5/mcp", "http://127.0.0.1:8080/mcp", "http://169.254.169.254/latest",
                 "http://[::1]:8015/mcp", "http://localhost:5433/"):
        st, d, _ = call("POST", "/sources", {"mode": "url", "id": "e2e5_ssrf", "url": host},
                        name="s11_ssrf")
        out.setdefault("ssrf", {})[host] = {"status": st, "code": (d.get("refusal") or {}).get("code")}
    st, d, _ = call("POST", "/sources", {"mode": "connection", "connection": {
        "id": "e2e5_cmd", "title": "cmd", "transport": {
            "type": "stdio", "command": "cmd.exe", "args": ["/c", "whoami"]}}}, name="s11_command")
    out["command_source"] = {"status": st, "code": (d.get("refusal") or {}).get("code")}
    st, d, _ = call("POST", "/sources", {"mode": "import", "path": "C:/Windows/win.ini"},
                    name="s11_import_path")
    out["import_path"] = {"status": st, "code": (d.get("refusal") or {}).get("code")}
    st, d, _ = call("GET", "/sources", name="s11_sources_after")
    out["sources_after"] = sorted(s.get("id") for s in d.get("sources", []))
    (E / "artefacts" / "s11_sec.json").write_text(json.dumps(out, indent=2), "utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

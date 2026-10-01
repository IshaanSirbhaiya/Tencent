"""S3 against the REAL Google Drive server and a REAL, signed-in Drive folder. Marked `integration`.

It never signs in and never opens a browser: it runs only when the one-time sign-in has already
happened (docs/GOOGLE_DRIVE_SETUP.md, Part 6) and a folder is declared. Otherwise it SKIPS and the
reason says exactly what is missing. Requirements, all of them:

  * `npx` on PATH (Node.js)
  * databridge/var/secrets/gdrive/gcp-oauth.keys.json   (the OAuth client — Parts 4-5)
  * databridge/var/secrets/gdrive/tokens.json           (the drive.readonly sign-in — Part 6)
  * WALNUT_GDRIVE_FOLDER_ID                             (the folder to read — Part 7)
  * the S3 core extension applied (see test_s3_gdrive_profile.py)

The connection comes from `infra/mcp_servers.yaml` (`google_drive`), so this test also proves the
declaration a person actually uses. Screen decisions are compared with
`fixtures/workspace_demo/expected_outcomes.yaml` for every file found at a matching path (the demo
upload); a folder of other files is ingested and verified without that comparison.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import yaml

import databridge_mcp_ingest as mi
from databridge_mcp_ingest import paths
from databridge_mcp_ingest.ledger import read_jsonl

from test_s3_gdrive_profile import CORE_EXTENSION, _activate, _read_only_names

pytestmark = pytest.mark.integration

SECRETS = paths.DATABRIDGE_ROOT / "var" / "secrets" / "gdrive"
CREDENTIALS = SECRETS / "gcp-oauth.keys.json"
TOKENS = SECRETS / "tokens.json"
NPX = shutil.which("npx") or shutil.which("npx.cmd")
FOLDER = os.environ.get("WALNUT_GDRIVE_FOLDER_ID", "").strip()
SIGN_IN = ("from databridge/ in PowerShell: $env:GOOGLE_DRIVE_OAUTH_CREDENTIALS = "
           "\"$PWD\\var\\secrets\\gdrive\\gcp-oauth.keys.json\"; $env:GOOGLE_DRIVE_MCP_TOKEN_PATH = "
           "\"$PWD\\var\\secrets\\gdrive\\tokens.json\"; $env:GOOGLE_DRIVE_MCP_SCOPES = "
           "\"drive.readonly\"; $env:GOOGLE_DRIVE_MCP_AUTH_PORT = \"3100\"; "
           "npx -y @piotr-agier/google-drive-mcp@2.12.0 auth")


def _missing() -> list[str]:
    out = []
    if NPX is None:
        out.append("npx is not installed (install Node.js)")
    if not CREDENTIALS.is_file():
        out.append(f"no OAuth client file at {CREDENTIALS} (docs/GOOGLE_DRIVE_SETUP.md Parts 4-5)")
    if not TOKENS.is_file():
        out.append(f"not signed in: no {TOKENS} — run the one-time sign-in ({SIGN_IN})")
    if not FOLDER:
        out.append("WALNUT_GDRIVE_FOLDER_ID is not set (the id after /folders/ in the folder's URL)")
    if not CORE_EXTENSION:
        out.append("the S3 core extension is not applied (tests/fixtures/gdrive/"
                   "core_extension.patch)")
    return out


@pytest.mark.skipif(bool(_missing()), reason="Google Drive integration not configured: "
                                             + "; ".join(_missing()))
def test_the_declared_drive_folder_is_ingested_end_to_end(walnut_env, tmp_path, monkeypatch,
                                                          fake_harness):
    from databridge_graph.packs import deployment
    from databridge_graph.store import GraphStore

    from databridge_mcp_ingest.connections import import_declarations, register_connection

    _activate(monkeypatch, tmp_path)
    monkeypatch.setenv("WALNUT_ROOT", str(paths.DATABRIDGE_ROOT))
    conn = import_declarations(only=["google_drive"], register=False)[0]
    # the declaration says `npx`; on Windows the child needs the resolved launcher
    data = conn.model_dump(mode="json")
    data["transport"]["command"] = NPX
    data["id"] = "gdrive-live"
    conn = register_connection(data)
    assert conn.scope.roots, "the infra declaration must name the folder (scope.roots)"

    packs = paths.packs_dir()
    pack = "workspace" if (packs / "workspace" / "screen.yaml").is_file() else "common"
    store = GraphStore(str(tmp_path / "g.db"))
    with deployment(pack):
        plan = mi.propose_plan(conn.id)
        assert plan.profiles == ["google-drive-mcp"], (plan.profiles, plan.notes)
        assert not plan.problems, plan.problems
        # the live server may be newer than the recording; the profile's read tool must still be
        # a labelled read, and nothing it denies may be called
        assert {t for s in plan.steps for t in s.tools()} <= _read_only_names()
        mi.approve_plan(conn.id, approved_by="integration test")
        report = mi.run_ingest(conn.id, store=store, harness=fake_harness)
    assert report.verify["ok"], report.verify["problems"]
    assert report.status == "ok", report.message
    run = Path(report.run_dir)
    captured = read_jsonl(run / "captured.jsonl")
    assert captured, "the folder listed nothing that could be captured"
    # the declared root is "${WALNUT_GDRIVE_FOLDER_ID}", expanded at run time into identities
    assert all(r["uri"].startswith(f"gdrive:///{FOLDER}/") for r in captured)
    assert all(r.get("attrs", {}).get("modified") for r in captured)

    expected_path = paths.DATABRIDGE_ROOT / "fixtures" / "workspace_demo" / "expected_outcomes.yaml"
    expected = yaml.safe_load(expected_path.read_text(encoding="utf-8"))["files"]

    def rel_of(uri: str) -> str:
        return uri.split("?id=", 1)[0].split("/", 4)[-1]

    matched = 0
    for rec in read_jsonl(run / "governance.jsonl"):
        rel = rel_of(rec["uri"])
        key = next((k for k in expected if rel == k or rel.endswith("/" + k)), None)
        if key is None or pack != "workspace":
            continue
        assert rec["decision"] == expected[key]["decision"], (rel, rec.get("rationale"))
        matched += 1
    print(f"live Drive: {len(captured)} captured, {matched} screen decisions matched the demo "
          f"corpus expectations", flush=True)

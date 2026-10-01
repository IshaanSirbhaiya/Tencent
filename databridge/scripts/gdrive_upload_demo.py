"""Upload the synthetic Larkspur demo corpus into a folder in YOUR Google Drive — optional.

    python scripts/gdrive_upload_demo.py                  # show what WOULD be uploaded (no sign-in)
    python scripts/gdrive_upload_demo.py --upload         # create the folder and upload
    python scripts/gdrive_upload_demo.py --upload --also-google-doc

Uploading by hand in the browser works just as well (docs/GOOGLE_DRIVE_SETUP.md, Part 7); this
script does the same thing through the same MCP server Walnut reads Drive with
(`@piotr-agier/google-drive-mcp`), using its write tools `createFolder` and `uploadFile`.

**It uses a SEPARATE sign-in with a separate, narrower-for-writing scope.** Walnut's ingestion
token (`var/secrets/gdrive/tokens.json`) is `drive.readonly` and must stay that way, so this
script never touches it. It signs in with `drive.file` — Google's "only files this app created"
scope — into `var/secrets/gdrive/tokens.upload.json`. The first `--upload` opens a browser tab for
that consent (the MCP server's own sign-in, on port 3100). Delete that token file afterwards if
you like; ingestion does not need it.

What it does, in order (and what a re-run does):
  1. creates `Walnut demo — Larkspur` in My Drive (or reuses the one this script created before);
  2. recreates the sub-folders of fixtures/workspace_demo/files inside it — keeping
     `planning/Q3 #roadmap` with its `#`, which is part of the demo;
  3. uploads every file AS IS (no conversion), skipping any whose name already exists in its
     folder, so a re-run never makes duplicates;
  4. with `--also-google-doc`, also uploads `meetings/2026-08-19_steering_corriedale.docx`
     converted to a native Google Doc named "Steering notes (Corriedale)" (the server converts
     only Office formats), so Walnut's Google-Doc export path is exercised too;
  5. prints the folder id — the value for WALNUT_GDRIVE_FOLDER_ID / `--root`.

All content is synthetic. Nothing here reads from Drive beyond checking for existing names.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]                       # databridge/
CORPUS = ROOT / "fixtures" / "workspace_demo" / "files"
SECRETS = ROOT / "var" / "secrets" / "gdrive"
CREDENTIALS = SECRETS / "gcp-oauth.keys.json"
UPLOAD_TOKENS = SECRETS / "tokens.upload.json"
PACKAGE = "@piotr-agier/google-drive-mcp@2.12.0"
FOLDER_NAME = "Walnut demo — Larkspur"
CONVERT = ("meetings/2026-08-19_steering_corriedale.docx", "Steering notes (Corriedale)")
MIME = {".md": "text/markdown", ".txt": "text/plain", ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
_ID = re.compile(r"ID: ([A-Za-z0-9_-]+)")
_LINE = re.compile(r"^(?P<name>.+?) \((?P<mime>[^()\s]+)\) \[id: (?P<id>[A-Za-z0-9_-]+),")


def plan() -> tuple[list[str], list[Path]]:
    files = sorted(p for p in CORPUS.rglob("*") if p.is_file())
    folders = sorted({p.parent.relative_to(CORPUS).as_posix() for p in files} - {"."})
    # every ancestor too, parents first
    allf: set[str] = set()
    for f in folders:
        parts = f.split("/")
        allf.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    return sorted(allf, key=lambda s: (s.count("/"), s)), files


class Drive:
    """The MCP server's write tools, one call at a time, errors in plain words."""

    def __init__(self, session) -> None:
        self.s = session

    async def call(self, tool: str, args: dict) -> str:
        result = await self.s.call_tool(tool, args)
        text = "\n".join(getattr(c, "text", "") for c in result.content)
        if result.isError:
            raise RuntimeError(f"{tool} failed: {text[:300]}")
        return text

    async def children(self, folder_id: str) -> dict[str, str]:
        """name -> id of what is already in a folder (read-only `search`, every page)."""
        out: dict[str, str] = {}
        token = None
        while True:
            args = {"query": f"'{folder_id}' in parents", "rawQuery": True, "pageSize": 100,
                    "orderBy": "name"}
            if token:
                args["pageToken"] = token
            text = await self.call("search", args)
            for line in text.splitlines():
                m = _LINE.match(line)
                if m:
                    out.setdefault(m.group("name"), m.group("id"))
            m = re.search(r"Use pageToken: (\S+)", text)
            if not m:
                return out
            token = m.group(1)

    async def folder(self, name: str, parent: str) -> str:
        existing = await self.children(parent)
        if name in existing:
            return existing[name]
        text = await self.call("createFolder", {"name": name, "parent": parent})
        return _ID.search(text).group(1)

    async def upload(self, path: Path, parent: str, *, name: str | None = None,
                     convert: bool = False) -> str:
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        mime = MIME.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] \
            or "application/octet-stream"
        args = {"name": name or path.name, "contentBase64": data, "mimeType": mime,
                "parentFolderId": parent}
        if convert:
            args["convertToGoogleFormat"] = True
        text = await self.call("uploadFile", args)
        return _ID.search(text).group(1)


async def run(also_doc: bool) -> str:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if npx is None:
        raise SystemExit("npx not found — install Node.js (it brings npx) and try again.")
    if not CREDENTIALS.is_file():
        raise SystemExit(f"No OAuth client file at {CREDENTIALS} — see docs/GOOGLE_DRIVE_SETUP.md "
                         f"Parts 1-5.")
    env = {**os.environ,
           "GOOGLE_DRIVE_OAUTH_CREDENTIALS": str(CREDENTIALS),
           "GOOGLE_DRIVE_MCP_TOKEN_PATH": str(UPLOAD_TOKENS),     # NOT the ingestion token
           "GOOGLE_DRIVE_MCP_SCOPES": "drive.file",               # only files this app creates
           "GOOGLE_DRIVE_MCP_AUTH_PORT": "3100"}                  # 3000 is Walnut's web page
    folders, files = plan()
    params = StdioServerParameters(command=npx, args=["-y", PACKAGE], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=None) as session:
            await session.initialize()
            drive = Drive(session)
            print("Signing in (a browser tab opens the first time)...", flush=True)
            root = await drive.folder(FOLDER_NAME, "root")
            ids = {"": root}
            for rel in folders:
                parent_rel, _, name = rel.rpartition("/")
                ids[rel] = await drive.folder(name, ids[parent_rel])
                print(f"  folder  {rel}", flush=True)
            for path in files:
                rel = path.relative_to(CORPUS).as_posix()
                rel_parent = path.parent.relative_to(CORPUS).as_posix()
                parent = ids["" if rel_parent == "." else rel_parent]
                if path.name in await drive.children(parent):
                    print(f"  exists  {rel}", flush=True)
                    continue
                await drive.upload(path, parent)
                print(f"  upload  {rel}", flush=True)
            if also_doc:
                rel, title = CONVERT
                parent = ids[rel.rpartition("/")[0]]
                if title in await drive.children(parent):
                    print(f"  exists  {rel.rpartition('/')[0]}/{title} (Google Doc)", flush=True)
                else:
                    await drive.upload(CORPUS / rel, parent, name=title + ".docx", convert=True)
                    print(f"  upload  {rel.rpartition('/')[0]}/{title} (as a Google Doc)",
                          flush=True)
            return root


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--upload", action="store_true", help="actually sign in and upload")
    ap.add_argument("--also-google-doc", action="store_true",
                    help="also upload one .docx converted to a native Google Doc")
    a = ap.parse_args()
    folders, files = plan()
    if not a.upload:
        print(f"Would create '{FOLDER_NAME}' in My Drive with {len(folders)} sub-folders and "
              f"upload {len(files)} files:")
        for p in files:
            print(f"  {p.relative_to(CORPUS).as_posix()}")
        if a.also_google_doc:
            print(f"  + {CONVERT[0]} converted to a Google Doc named {CONVERT[1]!r}")
        print("\nRe-run with --upload to do it (signs in with drive.file into "
              f"{UPLOAD_TOKENS.relative_to(ROOT)}).")
        return 0
    try:
        root = asyncio.run(run(a.also_google_doc))
    except RuntimeError as exc:
        print(f"Stopped: {exc}", flush=True)
        return 1
    print(f"\nDone. Folder id: {root}")
    print("Use it with Walnut:")
    print(f'  $env:WALNUT_GDRIVE_FOLDER_ID = "{root}"')
    print(json.dumps({"folder": FOLDER_NAME, "id": root}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

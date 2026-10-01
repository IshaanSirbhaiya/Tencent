"""A recorded fake of `@piotr-agier/google-drive-mcp` 2.12.0 over stdio. All data is synthetic.

    python fake_gdrive_server.py --drive MANIFEST.json [--log CALLS.jsonl] [--page-cap N]

What is REAL here (recorded, not invented):
  * `tools/list` is the server's own tool list, byte for byte (`tools_list.2.12.0.json`: 130 tools,
    their input schemas, and the `readOnlyHint` annotations on the 35 read tools).
  * The text of `search`, `listFolder` and the error envelope, and the whole `resources/list` /
    `resources/read` behaviour (Docs -> text/markdown, Sheets -> text/csv, Slides -> text/plain,
    Drawings -> image/png, text/* and application/json as text, every other file as a base64
    blob), follow the handlers in the package's `dist/index.js` (npm pack 2.12.0).

What is FAKE: the Drive behind it — a JSON manifest of folders and files (re-read on every request,
so a test can change the Drive between runs). Only the query shape a crawl sends to `search` is
answered (`'<folderId>' in parents`, rawQuery); anything else is an error, loudly. Every other tool
answers with the server's error envelope, and EVERY call is appended to `--log`, so a test can
prove which tools a crawl touched. `--page-cap` lowers the page size the way a large folder would,
so paging is exercised with a small corpus.

Manifest: {"my_drive_id": "...", "items": [{"id", "name", "mimeType", "parents": [id],
           "createdTime", "modifiedTime", "trashed"?: bool, "content_path"?: str,
           "content_text"?: str}]}   (content_path is relative to the manifest)
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server

HERE = Path(__file__).resolve().parent
RECORDED = json.loads((HERE / "tools_list.2.12.0.json").read_text(encoding="utf-8"))
FOLDER_MIME = "application/vnd.google-apps.folder"
#: resources/read export map — the `switch (mimeType)` in registerResourceHandlers
EXPORTS = {"application/vnd.google-apps.document": "text/markdown",
           "application/vnd.google-apps.spreadsheet": "text/csv",
           "application/vnd.google-apps.presentation": "text/plain",
           "application/vnd.google-apps.drawing": "image/png"}
ORDER_BY = ("modifiedTime desc", "modifiedTime", "createdTime desc", "createdTime",
            "recency desc", "recency", "name", "name_natural")
PARENT_QUERY = re.compile(r"^\s*'([^']+)' in parents\s*(?:and trashed = false\s*)?$")


class Drive:
    def __init__(self, manifest: Path) -> None:
        self.path = manifest

    def load(self) -> tuple[str, dict[str, dict]]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        items = {i["id"]: i for i in data["items"] if not i.get("trashed")}
        return data["my_drive_id"], items

    def content(self, item: dict) -> bytes:
        if "content_text" in item:
            return item["content_text"].encode("utf-8")
        return (self.path.parent / item["content_path"]).read_bytes()


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=f"Error: {message}")],
                                isError=True)


def _text(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)],
                                isError=False)


def _token(query: str, order: str, offset: int) -> str:
    sig = hashlib.sha256(f"{query}|{order}".encode()).hexdigest()[:10]
    return f"~!!~AI9FV7{sig}{offset:06d}"


def _offset(token: str | None, query: str, order: str) -> int | None:
    if not token:
        return 0
    sig = hashlib.sha256(f"{query}|{order}".encode()).hexdigest()[:10]
    if not token.startswith(f"~!!~AI9FV7{sig}"):
        return None
    return int(token[-6:])


def _sort(files: list[dict], order: str) -> list[dict]:
    key, desc = order.split(" ")[0], order.endswith(" desc")
    if key in ("name", "name_natural"):
        return sorted(files, key=lambda f: (f["name"].casefold(), f["id"]))
    field = {"modifiedTime": "modifiedTime", "createdTime": "createdTime",
             "recency": "modifiedTime"}[key]
    return sorted(files, key=lambda f: (f.get(field) or "", f["id"]), reverse=desc)


def build(drive: Drive, log: Path | None, page_cap: int) -> Server:
    server = Server("google-drive-mcp", version=RECORDED["server"]["version"])
    tools = [types.Tool.model_validate(t) for t in RECORDED["tools"]]

    def record(entry: dict) -> None:
        if log is not None:
            with log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")

    def parent_path(items: dict, my_drive: str, folder_id: str, depth: int = 0) -> str:
        # resolveParentPath: name chain up to the top, joined by "/"; "My Drive" at the top
        if depth >= 10:
            return folder_id
        if folder_id in (my_drive, "root"):
            return "My Drive"
        folder = items.get(folder_id)
        if folder is None:
            return folder_id
        parents = folder.get("parents") or []
        if parents and parents[0] != folder_id:
            return f"{parent_path(items, my_drive, parents[0], depth + 1)}/{folder['name']}"
        return folder["name"]

    def children(items: dict, my_drive: str, folder_id: str) -> list[dict]:
        target = my_drive if folder_id == "root" else folder_id
        return [i for i in items.values() if target in (i.get("parents") or [])]

    def search(args: dict) -> types.CallToolResult:
        query, raw = args.get("query") or "", bool(args.get("rawQuery"))
        if not query:
            return _error("Search query is required")
        order = args.get("orderBy") or "modifiedTime desc"
        if order not in ORDER_BY:
            return _error(f"Invalid enum value. Expected {' | '.join(ORDER_BY)}")
        size = args.get("pageSize")
        if size is not None and (int(size) != size or not 1 <= size <= 100):
            return _error("Number must be less than or equal to 100")
        m = PARENT_QUERY.match(query) if raw else None
        if m is None:
            return _error("fake drive: only raw \"'<folderId>' in parents\" queries are recorded")
        my_drive, items = drive.load()
        offset = _offset(args.get("pageToken"), query, order)
        if offset is None:
            return _error("Invalid Value: pageToken")
        files = _sort(children(items, my_drive, m.group(1)), order)
        per_page = min(int(size or 50), 100, page_cap)
        page = files[offset:offset + per_page]
        lines = []
        for f in page:
            parents = f.get("parents") or []
            folder = parent_path(items, my_drive, parents[0]) if parents else ""
            lines.append(f"{f['name']} ({f['mimeType']}) [id: {f['id']}, path: {folder or '/'}] "
                         f"[created: {f.get('createdTime') or 'N/A'}, "
                         f"modified: {f.get('modifiedTime') or 'N/A'}]")
        text = f"Found {len(page)} files (ordered by {order}):\n" + "\n".join(lines)
        if offset + per_page < len(files):
            text += (f"\n\nMore results available. Use pageToken: "
                     f"{_token(query, order, offset + per_page)}")
            text += f"\nPass orderBy: {order} again to keep this ordering."
        return _text(text)

    def list_folder(args: dict) -> types.CallToolResult:
        size = args.get("pageSize")
        if size is not None and (int(size) != size or not 1 <= size <= 100):
            return _error("Number must be less than or equal to 100")
        folder_id = args.get("folderId") or "root"
        my_drive, items = drive.load()
        query = f"'{folder_id}' in parents and trashed = false"
        offset = _offset(args.get("pageToken"), query, "name")
        if offset is None:
            return _error("Invalid Value: pageToken")
        files = _sort(children(items, my_drive, folder_id), "name")
        per_page = min(int(size or 50), 100, page_cap)
        page = files[offset:offset + per_page]
        folder_icon, file_icon = "\U0001F4C1", "\U0001F4C4"
        body = "\n".join(f"{folder_icon if f['mimeType'] == FOLDER_MIME else file_icon} "
                         f"{f['name']} (ID: {f['id']})" for f in page)
        text = f"Contents of folder:\n\n{body}"
        if offset + per_page < len(files):
            text += f"\n\nMore items available. Use pageToken: {_token(query, 'name', offset + per_page)}"
        return _text(text)

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return tools

    @server.call_tool(validate_input=False)
    async def _call_tool(name: str, arguments: dict) -> types.CallToolResult:
        record({"method": "tools/call", "name": name, "args": arguments})
        if name == "search":
            return search(arguments)
        if name == "listFolder":
            return list_folder(arguments)
        if name not in {t.name for t in tools}:
            return _error("Tool not found")
        return _error(f"fake drive: {name!r} is not recorded (a crawl should never call it)")

    @server.list_resources()
    async def _list_resources(req: types.ListResourcesRequest) -> types.ListResourcesResult:
        cursor = req.params.cursor if req.params else None
        record({"method": "resources/list", "cursor": cursor})
        _, items = drive.load()
        files = sorted(items.values(), key=lambda f: f["id"])
        start = int(cursor) if cursor else 0
        page = files[start:start + 1000]
        return types.ListResourcesResult(
            resources=[types.Resource(uri=f"gdrive:///{f['id']}", name=f["name"] or "Untitled",
                                      mimeType=f["mimeType"] or "application/octet-stream")
                       for f in page],
            nextCursor=str(start + 1000) if start + 1000 < len(files) else None)

    @server.read_resource()
    async def _read_resource(uri) -> list[ReadResourceContents]:
        record({"method": "resources/read", "uri": str(uri)})
        _, items = drive.load()
        file_id = str(uri).replace("gdrive:///", "")
        item = items.get(file_id)
        if item is None:
            raise ValueError(f"File not found: {file_id}")
        mime = item["mimeType"]
        data = drive.content(item)
        if mime.startswith("application/vnd.google-apps"):
            export = EXPORTS.get(mime, "text/plain")
            # the real handler exports with responseType "text" — always a text content
            return [ReadResourceContents(content=data.decode("utf-8", errors="replace"),
                                         mime_type=export)]
        if mime.startswith("text/") or mime == "application/json":
            return [ReadResourceContents(content=data.decode("utf-8"), mime_type=mime)]
        return [ReadResourceContents(content=data, mime_type=mime)]

    return server


async def _main(args) -> None:
    server = build(Drive(Path(args.drive)), Path(args.log) if args.log else None, args.page_cap)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--drive", required=True)
    ap.add_argument("--log")
    ap.add_argument("--page-cap", type=int, default=100)
    asyncio.run(_main(ap.parse_args()))


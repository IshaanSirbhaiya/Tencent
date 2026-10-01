"""walnut-files-blob — a tiny filesystem MCP server that delivers ORIGINAL bytes (stdio, FastMCP).

Why it exists: `@modelcontextprotocol/server-filesystem` 2026.8.31 cannot deliver PDF/DOCX bytes.
Its `read_media_file` builds an embedded-resource block for non-image/audio files, and the TS SDK
then rejects its own tools/call result (`MCP error -32602: Invalid tools/call result`,
invalid_union on `type`); it declares no `resources` capability either (`resources/list` →
"Method not found"). Measured 2026-10-01 against the Larkspur corpus. `markitdown-mcp` returns
converted Markdown, not the file — the captured bytes would not be the source (the evaluation is
recorded in profiles/files/walnut_files_blob.profile.yaml).

Two modes, never both in one process (a reader cannot write, a writer cannot read the corpus):

  READ  (ingestion)   python server.py --root DIR [--root DIR2] [--max-bytes N]
      list_roots()              -> JSON {"roots": [abs path, ...]}
      list_dir(path)            -> JSON {"path", "entries": [{name, type: dir|file, path, size,
                                                             mime}]}  (one level, sorted)
      stat_file(path)           -> JSON {path, type, size, mtime, mime, sha256 (files)}
      read_bytes(path)          -> ONE embedded resource {uri: file://…, mimeType, blob: base64}
                                   with _meta {sha256, size} of the bytes on disk, so a client can
                                   check the transport did not alter them.
  WRITE (agent action) python server.py --write-root DIR [--write-ext .md --write-ext .txt]
      list_roots(), list_dir(path)     scoped to the ONE writable root
      write_file(path, content)        CREATE-ONLY (refuses to overwrite), UTF-8 text, allowed
                                       extensions only, parent folder must already exist inside the
                                       root. Returns JSON {path, sha256, bytes} — the reversal is
                                       "delete that file", and nothing else was touched.

Path safety: every path (absolute, or relative to the single root) is resolved with
`os.path.realpath` — `..`, symlinks and junctions are followed BEFORE the containment check — and
must equal a declared root or sit beneath it (case-insensitive on Windows). A listed entry whose
real target escapes the roots is still listed (so it is recorded, not silently dropped) and every
read of it is refused. Depends only on the `mcp` SDK (1.x FastMCP) so it runs outside this repo.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import mimetypes
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import BlobResourceContents, EmbeddedResource, ToolAnnotations

SERVER_NAME = "walnut-files-blob"
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_WRITE_MAX_BYTES = 256 * 1024
DEFAULT_WRITE_EXT = (".md", ".txt")

_EXTRA_MIME = {
    ".md": "text/markdown", ".markdown": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".yaml": "application/yaml", ".yml": "application/yaml", ".eml": "message/rfc822",
}


class AccessDenied(ValueError):
    pass


def mime_of(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    return _EXTRA_MIME.get(ext) or mimetypes.guess_type("x" + ext)[0] or "application/octet-stream"


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


class Roots:
    """The declared roots and the one containment check every tool goes through."""

    def __init__(self, roots: list[str]) -> None:
        if not roots:
            raise SystemExit("at least one root is required")
        real = []
        for r in roots:
            rp = os.path.realpath(r)
            if not os.path.isdir(rp):
                raise SystemExit(f"root is not a directory: {r}")
            real.append(rp)
        self.roots = real

    def contains(self, real: str) -> bool:
        n = _norm(real)
        for r in self.roots:
            nr = _norm(r)
            if n == nr or n.startswith(nr.rstrip("\\/") + os.sep):
                return True
        return False

    def resolve(self, path: str) -> str:
        if not isinstance(path, str) or not path.strip() or "\x00" in path:
            raise AccessDenied("a non-empty path is required")
        p = path.strip()
        if not os.path.isabs(p):
            if len(self.roots) != 1:
                raise AccessDenied("relative paths are only accepted when one root is declared")
            p = os.path.join(self.roots[0], p)
        real = os.path.realpath(p)
        if not self.contains(real):
            raise AccessDenied(f"Access denied - path outside the declared roots: {path}")
        return real


def _entry(full: str) -> dict:
    name = os.path.basename(full)
    is_dir = os.path.isdir(full)
    out = {"name": name, "type": "dir" if is_dir else "file", "path": full}
    if not is_dir:
        try:
            out["size"] = os.path.getsize(full)
        except OSError:
            out["size"] = None
        out["mime"] = mime_of(name)
    return out


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _add_listing_tools(mcp: FastMCP, roots: Roots) -> None:
    ro = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                         openWorldHint=False)

    @mcp.tool(annotations=ro, structured_output=False)
    def list_roots() -> str:
        """The directories this server may read (absolute paths)."""
        return json.dumps({"roots": roots.roots})

    @mcp.tool(annotations=ro, structured_output=False)
    def list_dir(path: str) -> str:
        """List ONE directory: its files and sub-directories with full path, type, size, mime."""
        real = roots.resolve(path)
        if not os.path.isdir(real):
            raise AccessDenied(f"not a directory: {path}")
        # The entry path keeps the ADDRESS the caller used (parent path + name), so identity is
        # the path as seen through the declared root, not wherever a link points.
        base = os.path.normpath(path) if os.path.isabs(path) else real
        names = sorted(os.listdir(real), key=lambda s: (s.lower(), s))
        return json.dumps({"path": base,
                           "entries": [_entry(os.path.join(base, n)) for n in names]},
                          ensure_ascii=False)


def build_reader(mcp: FastMCP, roots: Roots, max_bytes: int) -> None:
    _add_listing_tools(mcp, roots)
    ro = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                         openWorldHint=False)

    @mcp.tool(annotations=ro, structured_output=False)
    def stat_file(path: str) -> str:
        """Size, modified time (UTC), mime and — for files — the sha256 of the bytes on disk."""
        real = roots.resolve(path)
        st = os.stat(real)
        out = {"path": path, "type": "dir" if os.path.isdir(real) else "file",
               "size": st.st_size,
               "mtime": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc)
               .isoformat(timespec="seconds")}
        if out["type"] == "file":
            out["mime"] = mime_of(real)
            out["sha256"] = _sha256_file(real)
        return json.dumps(out, ensure_ascii=False)

    # structured_output=False: the blob travels ONCE (as the embedded resource), never again as a
    # structuredContent copy.
    @mcp.tool(annotations=ro, structured_output=False)
    def read_bytes(path: str) -> list:
        """Read one file's ORIGINAL bytes as an embedded resource (base64 blob) with its mime.
        Works for every type (pdf, docx, md, txt, ...). _meta carries the sha256 and size."""
        real = roots.resolve(path)
        if not os.path.isfile(real):
            raise AccessDenied(f"not a file: {path}")
        size = os.path.getsize(real)
        if size > max_bytes:
            raise AccessDenied(f"file is {size} bytes; this server's cap is {max_bytes}")
        data = Path(real).read_bytes()
        blob = BlobResourceContents.model_validate({
            "uri": Path(real).as_uri(), "mimeType": mime_of(real),
            "blob": base64.b64encode(data).decode("ascii"),
            "_meta": {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}})
        return [EmbeddedResource(type="resource", resource=blob)]


def build_writer(mcp: FastMCP, root: Roots, exts: tuple[str, ...], max_bytes: int) -> None:
    _add_listing_tools(mcp, root)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                          idempotentHint=False, openWorldHint=False),
              structured_output=False)
    def write_file(path: str, content: str) -> str:
        """Create a NEW UTF-8 text file inside the writable root. Refuses to overwrite, refuses
        other extensions, and never creates folders. Returns {path, sha256, bytes}."""
        target = path if os.path.isabs(path) else os.path.join(root.roots[0], path)
        if os.path.splitext(target)[1].lower() not in exts:
            raise AccessDenied(f"only {list(exts)} files may be written")
        parent = root.resolve(os.path.dirname(target) or root.roots[0])
        if not os.path.isdir(parent):
            raise AccessDenied("the parent folder must already exist inside the writable root")
        final = os.path.join(parent, os.path.basename(target))
        if not root.contains(os.path.realpath(final)):
            raise AccessDenied(f"Access denied - path outside the writable root: {path}")
        data = content.encode("utf-8")
        if len(data) > max_bytes:
            raise AccessDenied(f"content is {len(data)} bytes; the write cap is {max_bytes}")
        try:
            fd = os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
        except FileExistsError:
            raise AccessDenied(f"refused: {os.path.basename(final)} already exists "
                               f"(write_file only creates new files)") from None
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return json.dumps({"path": final, "sha256": hashlib.sha256(data).hexdigest(),
                           "bytes": len(data)}, ensure_ascii=False)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog=SERVER_NAME, description=__doc__.split("\n", 1)[0])
    ap.add_argument("--root", action="append", default=[], help="readable root (repeatable)")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    ap.add_argument("--write-root", help="the ONE writable root (write mode; excludes --root)")
    ap.add_argument("--write-ext", action="append", default=[])
    ap.add_argument("--write-max-bytes", type=int, default=DEFAULT_WRITE_MAX_BYTES)
    ap.add_argument("--create-write-root", action="store_true",
                    help="create the declared writable root if it does not exist")
    a = ap.parse_args(argv)
    if a.write_root and a.root:
        raise SystemExit("--write-root and --root are exclusive: run a reader and a writer as "
                         "two servers (two connections)")
    mcp = FastMCP(SERVER_NAME, log_level="WARNING")
    if a.write_root:
        if a.create_write_root:
            os.makedirs(a.write_root, exist_ok=True)
        exts = tuple(e.lower() if e.startswith(".") else f".{e.lower()}"
                     for e in (a.write_ext or DEFAULT_WRITE_EXT))
        build_writer(mcp, Roots([a.write_root]), exts, a.write_max_bytes)
    elif a.root:
        build_reader(mcp, Roots(a.root), a.max_bytes)
    else:
        raise SystemExit("give --root DIR (read mode) or --write-root DIR (write mode)")
    mcp.run()


if __name__ == "__main__":
    sys.exit(main())

"""A tiny synthetic MCP server over stdio (FastMCP), for tests. All data is invented.

    python fixture_server.py --root DIR            filesystem-like tools, the SAME names and output
                                                   shapes as @modelcontextprotocol/server-filesystem
    python fixture_server.py --db PATH             a `query(sql)` tool + one schema resource per
                                                   table, like @modelcontextprotocol/server-postgres
    python fixture_server.py --root DIR --db PATH  both
    python fixture_server.py --odd                 tools no profile knows (model-planned path)

The SQL side is SQLite dressed as PostgreSQL: the data file is ATTACHed as schema `public`, and an
in-memory `information_schema` (tables, columns, table_constraints, key_column_usage,
constraint_column_usage) is built from PRAGMAs, so the bundled information_schema SQL runs as-is.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sqlite3
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import BlobResourceContents, EmbeddedResource, ImageContent


def build_fs(mcp: FastMCP, root: Path) -> None:
    root = root.resolve()

    def valid(path: str) -> Path:
        p = Path(path)
        p = (p if p.is_absolute() else root / p).resolve()
        if p != root and root not in p.parents:
            raise ValueError(f"Access denied - path outside allowed directories: {p}")
        return p

    @mcp.tool()
    def list_allowed_directories() -> str:
        """Returns the list of directories this server is allowed to access."""
        return f"Allowed directories:\n{root}"

    @mcp.tool()
    def list_directory(path: str) -> str:
        """Get a detailed listing of all files and directories in a specified path."""
        p = valid(path)
        lines = [f"{'[DIR]' if e.is_dir() else '[FILE]'} {e.name}" for e in sorted(p.iterdir())]
        return "\n".join(lines)

    @mcp.tool()
    def read_text_file(path: str) -> str:
        """Read the complete contents of a file from the file system as text."""
        return valid(path).read_text(encoding="utf-8")

    @mcp.tool()
    def read_media_file(path: str) -> list:
        """Read a file and return it as a base64-encoded content block with its MIME type."""
        p = valid(path)
        data = base64.b64encode(p.read_bytes()).decode("ascii")
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if mime.startswith("image/"):
            return [ImageContent(type="image", data=data, mimeType=mime)]
        return [EmbeddedResource(type="resource", resource=BlobResourceContents(
            uri=p.as_uri(), mimeType="application/octet-stream", blob=data))]

    @mcp.tool()
    def write_file(path: str, content: str) -> str:
        """Create a new file or completely overwrite an existing file."""
        valid(path).write_text(content, encoding="utf-8")
        return f"Successfully wrote to {path}"


def _emulated(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ? AS public", (str(db_path),))
    conn.execute("ATTACH DATABASE ':memory:' AS information_schema")
    conn.executescript("""
        CREATE TABLE information_schema.tables (table_schema, table_name, table_type);
        CREATE TABLE information_schema.columns (table_schema, table_name, column_name,
                                                 data_type, ordinal_position);
        CREATE TABLE information_schema.table_constraints (constraint_name, constraint_schema,
                                                           table_schema, table_name,
                                                           constraint_type);
        CREATE TABLE information_schema.key_column_usage (constraint_name, table_schema,
                                                          table_name, column_name,
                                                          ordinal_position);
        CREATE TABLE information_schema.constraint_column_usage (constraint_name,
                                                                 constraint_schema, table_schema,
                                                                 table_name, column_name);
    """)
    names = [r[0] for r in conn.execute(
        "SELECT name FROM public.sqlite_master WHERE type='table' ORDER BY name")]
    for t in names:
        conn.execute("INSERT INTO information_schema.tables VALUES ('public', ?, 'BASE TABLE')",
                     (t,))
        cols = conn.execute(f'PRAGMA public.table_info("{t}")').fetchall()
        pk_cols = sorted((c["pk"], c["name"]) for c in cols if c["pk"])
        for c in cols:
            conn.execute("INSERT INTO information_schema.columns VALUES ('public',?,?,?,?)",
                         (t, c["name"], (c["type"] or "text").lower(), c["cid"] + 1))
        if pk_cols:
            cname = f"{t}_pkey"
            conn.execute("INSERT INTO information_schema.table_constraints VALUES "
                         "(?, 'public', 'public', ?, 'PRIMARY KEY')", (cname, t))
            for i, (_, col) in enumerate(pk_cols, 1):
                conn.execute("INSERT INTO information_schema.key_column_usage VALUES "
                             "(?, 'public', ?, ?, ?)", (cname, t, col, i))
                conn.execute("INSERT INTO information_schema.constraint_column_usage VALUES "
                             "(?, 'public', 'public', ?, ?)", (cname, t, col))
        for fk in conn.execute(f'PRAGMA public.foreign_key_list("{t}")').fetchall():
            cname = f"{t}_{fk['from']}_fkey"
            conn.execute("INSERT INTO information_schema.table_constraints VALUES "
                         "(?, 'public', 'public', ?, 'FOREIGN KEY')", (cname, t))
            conn.execute("INSERT INTO information_schema.key_column_usage VALUES "
                         "(?, 'public', ?, ?, 1)", (cname, t, fk["from"]))
            conn.execute("INSERT INTO information_schema.constraint_column_usage VALUES "
                         "(?, 'public', 'public', ?, ?)", (cname, fk["table"], fk["to"]))
    return conn


def build_sql(mcp: FastMCP, db_path: Path) -> None:
    conn = _emulated(db_path)

    @mcp.tool()
    def query(sql: str) -> str:
        """Run a read-only SQL query."""
        if not sql.lstrip().lower().startswith("select"):
            raise ValueError("read-only: only SELECT is allowed")
        rows = [dict(r) for r in conn.execute(sql).fetchall()]
        return json.dumps(rows, indent=2, default=str)

    tables = [r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables")]
    def make(t: str):
        def schema_resource() -> str:
            cols = conn.execute("SELECT column_name, data_type FROM information_schema.columns "
                                "WHERE table_name = ?", (t,)).fetchall()
            return json.dumps([dict(c) for c in cols])
        return schema_resource

    for t in tables:
        mcp.resource(f"postgres://fixture/{t}/schema", name=f'"{t}" database schema',
                     mime_type="application/json")(make(t))


def build_odd(mcp: FastMCP, root: Path) -> None:
    """Tools no bundled profile knows: a JSON catalogue and a fetch-by-id reader."""
    files = sorted(p for p in root.rglob("*") if p.is_file())

    @mcp.tool()
    def catalogue_entries() -> str:
        """List every entry in the catalogue as JSON."""
        return json.dumps({"entries": [{"key": str(p.relative_to(root)).replace("\\", "/"),
                                        "title": p.name} for p in files]})

    @mcp.tool()
    def fetch_entry(key: str) -> str:
        """Fetch one catalogue entry's text by key."""
        return (root / key).read_text(encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root")
    ap.add_argument("--db")
    ap.add_argument("--odd", action="store_true")
    a = ap.parse_args()
    mcp = FastMCP("walnut-fixture", log_level="WARNING")
    if a.odd:
        build_odd(mcp, Path(a.root))
    elif a.root:
        build_fs(mcp, Path(a.root))
    if a.db:
        build_sql(mcp, Path(a.db))
    mcp.run()


if __name__ == "__main__":
    main()

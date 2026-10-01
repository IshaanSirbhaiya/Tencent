"""Mechanical PostgreSQL -> SQLite translation.

There is exactly one copy of the estate schema — the PostgreSQL one the estates
ship. This module rewrites it on the way in, and every rewrite it performs is
recorded as a `Rewrite` so `estates.py translate --explain` can show a human what
was changed and why. A construct with no rule raises `UntranslatableConstruct`;
nothing is ever skipped silently.

Rules, in application order:
  psql_meta          `\\set ON_ERROR_STOP on` and friends are dropped
  schema_object      `DROP/CREATE SCHEMA` becomes a no-op (one SQLite file *is* the schema)
  comment_on         `COMMENT ON ... IS '...'` is captured into `_pg_comment`, not discarded
  qualified_name     `<schema>.foo` -> `foo`
  column_type        PostgreSQL column types -> SQLite affinities
  cast_operator      `x::t` -> the SQLite function that means the same thing
  date_trunc         `date_trunc('month', x)` -> `date(x, 'start of month')`
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

TRANSLATOR_VERSION = "1"

# SQLite gained `FILTER (WHERE ...)` on aggregates in 3.30 and `TRUE`/`FALSE`
# keywords in 3.23. Both appear in the estate DDL; assert rather than mis-load.
MIN_SQLITE = (3, 30, 0)


class UntranslatableConstruct(RuntimeError):
    """A PostgreSQL construct with no mechanical SQLite equivalent."""


@dataclass
class Rewrite:
    rule: str
    before: str
    after: str

    def __str__(self) -> str:
        before = _one_line(self.before)
        after = _one_line(self.after) or "<dropped>"
        return f"[{self.rule}] {before}  ->  {after}"


@dataclass
class Translation:
    """The result of translating one source's SQL."""

    statements: list[str] = field(default_factory=list)
    rewrites: list[Rewrite] = field(default_factory=list)
    comments: list[tuple[str, str, str]] = field(default_factory=list)  # kind, object, text
    dropped: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rewrites:
            out[r.rule] = out.get(r.rule, 0) + 1
        return out


def _one_line(s: str, limit: int = 88) -> str:
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# statement splitting (quote/comment aware, and fast enough for 12 MB of seed)
# ---------------------------------------------------------------------------

_INTERESTING = re.compile(r"'|--|/\*|;")


def strip_leading_comments(chunk: str) -> str:
    """Drop the comment banner that precedes a statement.

    The estates open every file with a large `--` header, and a chunk cut on `;`
    carries the comments that sat between it and the previous statement. Leaving
    them attached would defeat every `^CREATE ...` classification below.
    """
    i, n = 0, len(chunk)
    while i < n:
        while i < n and chunk[i] in " \t\r\n":
            i += 1
        if chunk.startswith("--", i):
            j = chunk.find("\n", i)
            i = n if j < 0 else j + 1
        elif chunk.startswith("/*", i):
            j = chunk.find("*/", i)
            if j < 0:
                break
            i = j + 2
        else:
            break
    return chunk[i:]


def split_statements(sql: str) -> list[str]:
    """Split on `;` at top level, respecting '' strings and -- / /* */ comments."""
    out: list[str] = []
    start = 0
    i = 0
    n = len(sql)
    while i < n:
        m = _INTERESTING.search(sql, i)
        if not m:
            break
        tok = m.group(0)
        j = m.end()
        if tok == "'":
            while True:
                k = sql.find("'", j)
                if k < 0:
                    raise UntranslatableConstruct("unterminated string literal")
                if k + 1 < n and sql[k + 1] == "'":
                    j = k + 2
                    continue
                j = k + 1
                break
            i = j
        elif tok == "--":
            k = sql.find("\n", j)
            i = n if k < 0 else k + 1
        elif tok == "/*":
            k = sql.find("*/", j)
            if k < 0:
                raise UntranslatableConstruct("unterminated block comment")
            i = k + 2
        else:  # ';'
            chunk = strip_leading_comments(sql[start:j]).strip()
            if chunk.rstrip(";").strip():
                out.append(chunk)
            start = j
            i = j
    tail = strip_leading_comments(sql[start:]).strip()
    if tail and tail.rstrip(";").strip():
        out.append(tail)
    return out


def code_segments(sql: str) -> list[tuple[bool, str]]:
    """Split into (is_string_literal, text) runs.

    Needed because the seed data is free text: `party.notes` and `interaction.summary`
    genuinely contain the word "CRM.", and a blind schema-qualifier substitution over
    the whole statement would edit the fixture's data.
    """
    out: list[tuple[bool, str]] = []
    i = start = 0
    n = len(sql)
    while i < n:
        k = sql.find("'", i)
        if k < 0:
            break
        out.append((False, sql[start:k]))
        j = k + 1
        while True:
            e = sql.find("'", j)
            if e < 0:
                raise UntranslatableConstruct("unterminated string literal")
            if e + 1 < n and sql[e + 1] == "'":
                j = e + 2
                continue
            j = e + 1
            break
        out.append((True, sql[k:j]))
        i = start = j
    out.append((False, sql[start:]))
    return out


def sub_outside_strings(pattern: re.Pattern[str], repl: str, sql: str) -> tuple[str, bool]:
    """Apply a substitution to SQL code only, never inside a string literal."""
    if not pattern.search(sql):
        return sql, False
    parts, hit = [], False
    for is_literal, text in code_segments(sql):
        if is_literal:
            parts.append(text)
        else:
            new = pattern.sub(repl, text)
            hit = hit or new != text
            parts.append(new)
    return "".join(parts), hit


def strip_psql_meta(sql: str) -> tuple[str, list[str]]:
    """Remove psql backslash meta-commands. Includes are resolved in discovery."""
    kept, dropped = [], []
    for line in sql.splitlines():
        if line.lstrip().startswith("\\"):
            dropped.append(line.strip())
        else:
            kept.append(line)
    return "\n".join(kept), dropped


# ---------------------------------------------------------------------------
# statement classification
# ---------------------------------------------------------------------------

_SCHEMA_STMT = re.compile(r"^\s*(?:DROP|CREATE)\s+SCHEMA\b", re.I)
_COMMENT_ON = re.compile(
    r"^\s*COMMENT\s+ON\s+(TABLE|COLUMN|VIEW|SCHEMA|INDEX|CONSTRAINT|FUNCTION)\s+(.+?)\s+IS\s+(.*)$",
    re.I | re.S,
)
_CREATE_TABLE = re.compile(r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([^\s(]+)\s*\((.*)\)\s*$", re.I | re.S)

# ---------------------------------------------------------------------------
# type mapping. PostgreSQL declares intent; SQLite has five affinities. Mapping
# every type explicitly means an unknown type is an error, not a silent NUMERIC.
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "text": "TEXT",
    "varchar": "TEXT",
    "character varying": "TEXT",
    "char": "TEXT",
    "uuid": "TEXT",
    "json": "TEXT",
    "jsonb": "TEXT",
    # dates and timestamps are ISO-8601 strings in this estate. Left un-mapped they
    # would take NUMERIC affinity, which is a coercion waiting to happen.
    "date": "TEXT",
    "time": "TEXT",
    "timestamp": "TEXT",
    "timestamptz": "TEXT",
    "timestamp with time zone": "TEXT",
    "timestamp without time zone": "TEXT",
    # booleans arrive as the TRUE/FALSE keywords and land as 1/0.
    "boolean": "INTEGER",
    "bool": "INTEGER",
    "smallint": "INTEGER",
    "integer": "INTEGER",
    "int": "INTEGER",
    "int2": "INTEGER",
    "int4": "INTEGER",
    "int8": "INTEGER",
    "bigint": "INTEGER",
    "serial": "INTEGER",
    "bigserial": "INTEGER",
    "real": "REAL",
    "double precision": "REAL",
    "float8": "REAL",
    "numeric": "NUMERIC",
    "decimal": "NUMERIC",
    "bytea": "BLOB",
}

_COL_CONSTRAINT_LEAD = re.compile(
    r"^\s*(CONSTRAINT|PRIMARY|UNIQUE|CHECK|FOREIGN|EXCLUDE|LIKE)\b", re.I
)
_TYPE_HEAD = re.compile(
    r"^\s*("
    r"\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*"  # column name
    r")\s+("  # type: multi-word forms first, so `double precision` never matches as `double`
    r"timestamp\s+with(?:out)?\s+time\s+zone"
    r"|character\s+varying"
    r"|double\s+precision"
    r"|[A-Za-z_][A-Za-z0-9_]*"
    r")\s*(\([^)]*\))?(?=\s|$|,)",
    re.I,
)


def _split_top_level(body: str) -> list[str]:
    """Split a CREATE TABLE body on commas that are not inside () or ''."""
    out, depth, start, i, n = [], 0, 0, 0, len(body)
    while i < n:
        c = body[i]
        if c == "'":
            i += 1
            while i < n:
                if body[i] == "'":
                    if i + 1 < n and body[i + 1] == "'":
                        i += 2
                        continue
                    break
                i += 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "," and depth == 0:
            out.append(body[start:i])
            start = i + 1
        i += 1
    out.append(body[start:])
    return out


def _rewrite_column_types(stmt: str, rewrites: list[Rewrite]) -> str:
    m = _CREATE_TABLE.match(stmt)
    if not m:
        return stmt
    table, body = m.group(1), m.group(2)
    items = _split_top_level(body)
    changed: list[str] = []
    for item in items:
        if _COL_CONSTRAINT_LEAD.match(item) or not item.strip():
            changed.append(item)
            continue
        hm = _TYPE_HEAD.match(item)
        if not hm:
            raise UntranslatableConstruct(
                f"cannot read a column definition in {table}: {_one_line(item)}"
            )
        col, pg_type, size = hm.group(1), hm.group(2).strip(), hm.group(3) or ""
        key = " ".join(pg_type.lower().split())
        if key not in _TYPE_MAP:
            raise UntranslatableConstruct(
                f"{table}.{col}: no SQLite mapping for PostgreSQL type {pg_type!r}"
            )
        sqlite_type = _TYPE_MAP[key]
        # keep NUMERIC(p,s) precision: SQLite ignores it but it documents intent
        new_head = f" {col} {sqlite_type}{size if sqlite_type == 'NUMERIC' else ''}"
        rest = item[hm.end():]
        if sqlite_type.lower() != key:
            rewrites.append(
                Rewrite("column_type", f"{table}.{col} {pg_type}{size}", f"{table}.{col} {sqlite_type}")
            )
        changed.append(new_head + rest)
    return f"CREATE TABLE {table} (" + ",".join(changed) + ")"


# ---------------------------------------------------------------------------
# `x::type`
# ---------------------------------------------------------------------------

_CAST_TAIL = re.compile(
    r"::\s*("
    r"timestamp\s+with(?:out)?\s+time\s+zone"
    r"|character\s+varying"
    r"|double\s+precision"
    r"|[A-Za-z_][A-Za-z0-9_]*"
    r")",
    re.I,
)
_IDENT_TAIL = re.compile(r"[A-Za-z0-9_.\"]+$")

_CAST_FN = {
    "date": lambda x: f"date({x})",
    "timestamp": lambda x: f"datetime({x})",
    "timestamptz": lambda x: f"datetime({x})",
    "text": lambda x: f"CAST({x} AS TEXT)",
    "integer": lambda x: f"CAST({x} AS INTEGER)",
    "int": lambda x: f"CAST({x} AS INTEGER)",
    "bigint": lambda x: f"CAST({x} AS INTEGER)",
    "numeric": lambda x: f"CAST({x} AS NUMERIC)",
    "real": lambda x: f"CAST({x} AS REAL)",
    "double precision": lambda x: f"CAST({x} AS REAL)",
}


def _operand_start(s: str, end: int) -> int:
    """Walk back from `end` over one operand: a balanced (...) call, or an identifier."""
    i = end - 1
    while i >= 0 and s[i] in " \t\n":
        i -= 1
    if i >= 0 and s[i] == ")":
        depth = 0
        while i >= 0:
            if s[i] == ")":
                depth += 1
            elif s[i] == "(":
                depth -= 1
                if depth == 0:
                    break
            i -= 1
        if i < 0:
            raise UntranslatableConstruct("unbalanced parentheses before a `::` cast")
        m = _IDENT_TAIL.search(s[:i])  # a function name in front of the (
        return m.start() if m else i
    m = _IDENT_TAIL.search(s[: i + 1])
    if not m:
        raise UntranslatableConstruct("cannot find the operand of a `::` cast")
    return m.start()


def _literal_spans(sql: str) -> list[tuple[int, int]]:
    """Character ranges covered by string literals, for skipping matches inside data."""
    spans, pos = [], 0
    for is_literal, text in code_segments(sql):
        if is_literal:
            spans.append((pos, pos + len(text)))
        pos += len(text)
    return spans


def _inside_literal(spans: list[tuple[int, int]], pos: int) -> bool:
    return any(a <= pos < b for a, b in spans)


def _rewrite_casts(stmt: str, rewrites: list[Rewrite]) -> str:
    from_pos = 0
    while True:
        m = _CAST_TAIL.search(stmt, from_pos)
        if not m:
            return stmt
        # `::` and `date_trunc(` also occur in free text. Rewriting one there would be a
        # silent edit of the fixture's data, so matches inside a literal are stepped over.
        if _inside_literal(_literal_spans(stmt), m.start()):
            from_pos = m.end()
            continue
        pg_type = " ".join(m.group(1).lower().split())
        if pg_type not in _CAST_FN:
            raise UntranslatableConstruct(f"no SQLite equivalent for cast `::{pg_type}`")
        start = _operand_start(stmt, m.start())
        operand = stmt[start:m.start()].strip()
        replacement = _CAST_FN[pg_type](operand)
        rewrites.append(Rewrite("cast_operator", f"{operand}::{m.group(1).strip()}", replacement))
        stmt = stmt[:start] + replacement + stmt[m.end():]


# ---------------------------------------------------------------------------
# `date_trunc(unit, x)`
# ---------------------------------------------------------------------------

_DATE_TRUNC = re.compile(r"\bdate_trunc\s*\(", re.I)
_TRUNC_MODIFIER = {
    "year": "'start of year'",
    "month": "'start of month'",
    "day": None,  # date() already truncates to the day
}


def _match_paren(s: str, open_idx: int) -> int:
    depth, i, n = 0, open_idx, len(s)
    while i < n:
        c = s[i]
        if c == "'":
            i += 1
            while i < n:
                if s[i] == "'":
                    if i + 1 < n and s[i + 1] == "'":
                        i += 2
                        continue
                    break
                i += 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise UntranslatableConstruct("unbalanced parentheses in date_trunc()")


def _rewrite_date_trunc(stmt: str, rewrites: list[Rewrite]) -> str:
    from_pos = 0
    while True:
        m = _DATE_TRUNC.search(stmt, from_pos)
        if not m:
            return stmt
        if _inside_literal(_literal_spans(stmt), m.start()):
            from_pos = m.end()
            continue
        open_idx = m.end() - 1
        close_idx = _match_paren(stmt, open_idx)
        args = _split_top_level(stmt[open_idx + 1:close_idx])
        if len(args) != 2:
            raise UntranslatableConstruct("date_trunc() with other than 2 arguments")
        unit = args[0].strip().strip("'").lower()
        if unit not in _TRUNC_MODIFIER:
            raise UntranslatableConstruct(f"date_trunc unit {unit!r} has no SQLite modifier")
        mod = _TRUNC_MODIFIER[unit]
        expr = args[1].strip()
        replacement = f"date({expr}, {mod})" if mod else f"date({expr})"
        rewrites.append(Rewrite("date_trunc", stmt[m.start():close_idx + 1], replacement))
        stmt = stmt[: m.start()] + replacement + stmt[close_idx + 1:]


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

_COMMENT_TEXT = re.compile(r"^'(.*)'$", re.S)


def check_sqlite_version() -> None:
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise UntranslatableConstruct(
            f"SQLite {sqlite3.sqlite_version} is too old; the estate DDL needs "
            f">= {'.'.join(map(str, MIN_SQLITE))} for aggregate FILTER and TRUE/FALSE"
        )


def translate(sql: str, schema: str) -> Translation:
    """Translate one source's concatenated PostgreSQL SQL for SQLite."""
    check_sqlite_version()
    out = Translation()
    sql, meta = strip_psql_meta(sql)
    for line in meta:
        out.rewrites.append(Rewrite("psql_meta", line, ""))
        out.dropped.append(line)

    qualified = re.compile(rf"\b{re.escape(schema)}\.", re.I)
    saw_qualified = False

    for stmt in split_statements(sql):
        body = stmt.rstrip().rstrip(";").strip()
        if not body:
            continue

        if _SCHEMA_STMT.match(body):
            out.rewrites.append(Rewrite("schema_object", body, ""))
            out.dropped.append(body)
            continue

        cm = _COMMENT_ON.match(body)
        if cm:
            kind, obj, text = cm.group(1).upper(), cm.group(2).strip(), cm.group(3).strip()
            tm = _COMMENT_TEXT.match(text)
            out.comments.append((kind, qualified.sub("", obj), (tm.group(1) if tm else text).replace("''", "'")))
            out.rewrites.append(Rewrite("comment_on", body, "-> _pg_comment"))
            continue

        body, hit = sub_outside_strings(qualified, "", body)
        saw_qualified = saw_qualified or hit

        body = _rewrite_casts(body, out.rewrites)
        body = _rewrite_date_trunc(body, out.rewrites)
        body = _rewrite_column_types(body, out.rewrites)
        out.statements.append(body)

    if saw_qualified:
        out.rewrites.append(
            Rewrite("qualified_name", f"{schema}.<object>", "<object> (one SQLite file is the schema)")
        )
    return out


COMMENT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS _pg_comment (
    object_kind TEXT NOT NULL,
    object_name TEXT NOT NULL,
    comment     TEXT NOT NULL
)
"""

META_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS _estate_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

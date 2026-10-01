"""Counting and listing, MEASURED — "how many / list all" answered by SQL on the facts store (P6).

`count_query(source_id, sql)` runs ONE read-only SELECT against `<source>_facts.sqlite` and
returns the rows with a `[SQL]` citation that carries the statement, the facts file, its sha256
and the extract date, plus a `query_cell` pointer per returned cell (source_id == the connection
id, so agent scoping works on it as on every other pointer).

Read-only three times over: the file is opened `mode=ro`, `PRAGMA query_only` is on, and a SQLite
authorizer permits only reads and functions — an INSERT, ATTACH, PRAGMA write or a second
statement is refused before it runs. A measured figure is exact and dated: it is never rounded or
softened, and the answer says "as at <extract date>".

Resolvable pointers (R-022 f2 #8). A `query_cell` pointer whose identity is the statement alone
(no parameters, and a row order PROVEN unique, or at most one row) is resolved by re-running that
statement. Every other answer writes a QUERY ARTEFACT beside the facts store,
`<source>_facts.queries/<query_hash>.json`: the statement, the bound parameters (typed, so they can
be re-bound), their digest and the result digest. `resolve_query_cell(pointer)` loads it, re-runs
the query against the facts file the pointer captured, checks the identity again and returns the
cell. ORDER BY alone is not proof of a stable order (ties): the order is proven only when the
ORDER BY terms of a single-table query cover `rowid` or a declared key of that table.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import os
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from databridge_core import SourcePointer, Trust
from databridge_core.provenance import SnapshotIdentity

from .facts import facts_path, load_manifest, sha256_file

MAX_ROWS = 1000


class FactsQueryError(ValueError):
    """The statement is not a single read-only SELECT, or the facts store is missing."""


_ALLOWED = {getattr(sqlite3, n) for n in ("SQLITE_SELECT", "SQLITE_READ", "SQLITE_FUNCTION",
                                         "SQLITE_RECURSIVE") if hasattr(sqlite3, n)}


def _authorizer(action, *_args):
    return sqlite3.SQLITE_OK if action in _ALLOWED else sqlite3.SQLITE_DENY


@dataclass
class CountAnswer:
    source_id: str
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    extract_date: str
    facts_file: str
    facts_sha256: str
    citation: str
    question: Optional[str] = None
    pointers: list[SourcePointer] = field(default_factory=list)
    #: sha256 of the canonical result (columns + rows) — what a row ordinal is an ordinal OF
    result_sha256: str = ""
    #: sha256 of the bound parameters (canonical, typed); "" when there were none
    params_sha256: str = ""
    #: True when the row order is PROVEN unique (`proven_order`): ordinals are a stable identity
    ordered: bool = False
    #: the query artefact that makes the pointers resolvable ("" when the statement alone does)
    query_ref: str = ""

    @property
    def scalar(self) -> Any:
        """The single value of a one-row, one-column answer (a count)."""
        if len(self.rows) == 1 and len(self.columns) == 1:
            return self.rows[0][0]
        raise FactsQueryError(f"not a scalar answer ({len(self.rows)} rows × "
                              f"{len(self.columns)} columns)")

    def as_dict(self) -> dict:
        return {"source_id": self.source_id, "question": self.question, "sql": self.sql,
                "columns": self.columns, "rows": self.rows, "row_count": self.row_count,
                "truncated": self.truncated, "extract_date": self.extract_date,
                "facts_file": self.facts_file, "facts_sha256": self.facts_sha256,
                "citation": self.citation, "result_sha256": self.result_sha256,
                "params_sha256": self.params_sha256, "ordered": self.ordered,
                "query_ref": self.query_ref,
                "pointers": [p.model_dump(mode="json", exclude_none=True)
                             for p in self.pointers]}


def _check_statement(sql: str) -> str:
    s = (sql or "").strip().rstrip(";").strip()
    if not s:
        raise FactsQueryError("empty statement")
    if not re.match(r"(?is)^\s*(select|with)\b", s):
        raise FactsQueryError("only a single SELECT (or WITH … SELECT) is allowed on the facts "
                              "store")
    return s


def _typed(v: Any) -> Any:
    """A bound parameter in a canonical, TYPED form: 1 and '1' hash differently."""
    if isinstance(v, (bytes, bytearray)):
        return ["bytes", hashlib.sha256(bytes(v)).hexdigest()]
    return [type(v).__name__, v if isinstance(v, (int, float, str, bool)) or v is None
            else str(v)]


def params_digest(params: Optional[tuple | list | dict]) -> str:
    """sha256 of the bound parameters, canonical and typed; "" for none."""
    if not params:
        return ""
    if isinstance(params, dict):
        body: Any = {str(k): _typed(v) for k, v in sorted(params.items(), key=lambda kv: str(kv[0]))}
    else:
        body = [_typed(v) for v in params]
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     default=str).encode("utf-8")).hexdigest()


def result_digest(columns: list[str], rows: list[list[Any]]) -> str:
    return hashlib.sha256(json.dumps({"columns": columns, "rows": rows}, default=str,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


_ORDERED = re.compile(r"(?is)\border\s+by\b([^()]*)$")
_SINGLE_FROM = re.compile(r'(?is)^\s*select\b.*?\bfrom\s+("?)([A-Za-z_][A-Za-z0-9_]*)\1\s*'
                          r'(?:(?:as\s+)?[A-Za-z_][A-Za-z0-9_]*\s*)?'
                          r'(?:where\b.*?)?\border\s+by\b[^()]*$')
_ROWID = {"rowid", "_rowid_", "oid"}


def _unique_keys(conn: sqlite3.Connection, table: str) -> list[set[str]]:
    """The column sets SQLite guarantees unique in `table`: its primary key and every UNIQUE
    index (read before the authorizer is installed)."""
    keys: list[set[str]] = []
    try:
        info = conn.execute(f"PRAGMA table_info({json.dumps(table)})").fetchall()
        pk = {r[1].lower() for r in info if r[5]}
        if pk:
            keys.append(pk)
        for idx in conn.execute(f"PRAGMA index_list({json.dumps(table)})").fetchall():
            if idx[2]:                                               # unique
                cols = {r[2].lower() for r in conn.execute(
                    f"PRAGMA index_info({json.dumps(idx[1])})").fetchall() if r[2]}
                if cols:
                    keys.append(cols)
    except sqlite3.DatabaseError:
        return []
    return keys


def proven_order(stmt: str, keys_of) -> bool:
    """True only when the row order is PROVEN unique (R-022 f2 #8): one table, no join / group /
    distinct / compound / sub-query, and the final ORDER BY terms include `rowid` or every column
    of a declared key of that table (`keys_of(table) -> [set]`). Anything else is unproven."""
    m = _SINGLE_FROM.match(stmt)
    o = _ORDERED.search(stmt)
    if m is None or o is None:
        return False
    if re.search(r"(?is)\b(join|group\s+by|distinct|union|intersect|except|limit\s+\S+\s*,)\b",
                 stmt) or "(" in stmt or "," in re.split(r"(?i)\bwhere\b",
                                                         stmt[m.start(2):o.start()])[0]:
        return False
    terms = set()
    for t in o.group(1).split(","):
        words = t.strip().strip('"').split()
        if words:
            terms.add(words[0].strip('"').lower())
    if terms & _ROWID:
        return True
    return any(k and k <= terms for k in keys_of(m.group(2)))


def query_identity(stmt: str, params_sha: str, result_sha: str, *, ordered: bool,
                   rows: int) -> str:
    """The `query_hash` a `query_cell` pointer carries (R-022 f1 #6): the statement alone when it
    has no parameters and its row order is PROVEN (`proven_order`, f2 #8), or it has at most one
    row — so a re-run by statement resolves it; otherwise the statement + the typed
    parameters (`WHERE id = ?` with different ids never share an identity) and, when the row order
    is not fixed, the hash of the result the ordinals index into."""
    if not params_sha and (ordered or rows <= 1):
        return hashlib.sha256(stmt.encode("utf-8")).hexdigest()
    body = {"sql": stmt, "params": params_sha}
    if not (ordered or rows <= 1):
        body["result"] = result_sha
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def queries_dir(source_id: str, facts_dir: Optional[Path | str] = None) -> Path:
    return facts_path(source_id, facts_dir).with_name(f"{source_id}_facts.queries")


def _param_value(v: Any) -> Any:
    if isinstance(v, (bytes, bytearray)):
        return {"bytes_b64": base64.b64encode(bytes(v)).decode("ascii")}
    return v if isinstance(v, (int, float, str, bool)) or v is None else str(v)


def _param_restore(v: Any) -> Any:
    if isinstance(v, dict) and set(v) == {"bytes_b64"}:
        return base64.b64decode(v["bytes_b64"])
    return v


def _matches(final: Path, data: str) -> bool:
    try:
        return final.is_file() and final.read_text(encoding="utf-8") == data
    except PermissionError:          # Windows: another writer is replacing it right now
        return False


def _write_query_artefact(source_id: str, facts_dir, qhash: str, body: dict) -> str:
    d = queries_dir(source_id, facts_dir)
    d.mkdir(parents=True, exist_ok=True)
    final = d / f"{qhash}.json"
    data = json.dumps(body, sort_keys=True, default=str, indent=1)
    if _matches(final, data):
        return str(final)
    # Unique per CALL, not per process: concurrent threads in one gateway raced on a pid-only name
    # (E2E run 4, Q-01 root cause). On Windows a replace (or a read) can still fail transiently
    # while another writer holds the target; the artefact is content-addressed, so the outcome is
    # correct exactly when the target ends up holding these bytes — retry briefly until it does.
    tmp = d / f".{qhash}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    tmp.write_text(data, encoding="utf-8")
    try:
        for attempt in range(40):
            try:
                os.replace(tmp, final)
                return str(final)
            except PermissionError:
                if _matches(final, data):
                    return str(final)
                time.sleep(0.005 * (attempt + 1))
        raise PermissionError(f"could not publish query artefact {final.name}")
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _run(path: Path, stmt: str, params, max_rows: int):
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        schema = "\n".join(r[0] or "" for r in conn.execute(
            "SELECT sql FROM sqlite_master ORDER BY name"))
        key_cache: dict[str, list[set[str]]] = {}

        def keys_of(t: str) -> list[set[str]]:
            if t not in key_cache:
                key_cache[t] = _unique_keys(conn, t)
            return key_cache[t]

        ordered = proven_order(stmt, keys_of)
        conn.set_authorizer(_authorizer)
        try:
            cur = conn.execute(stmt, params or ())
        except (sqlite3.DatabaseError, sqlite3.Warning) as exc:
            raise FactsQueryError(f"refused or failed: {exc}") from exc
        columns = [d[0] for d in cur.description or []]
        fetched = cur.fetchmany(max_rows + 1)
    finally:
        conn.close()
    return schema, ordered, columns, fetched


def count_query(source_id: str, sql: str, *, params: Optional[tuple | dict] = None,
                question: Optional[str] = None, max_rows: int = MAX_ROWS,
                facts_dir: Optional[Path | str] = None) -> CountAnswer:
    """`facts_dir`: read the facts store in THIS directory instead of the process default
    (`WALNUT_FACTS_DIR`) — additive, for a caller that owns its own store (materialise)."""
    path = facts_path(source_id, facts_dir)
    manifest = load_manifest(source_id, facts_dir) or {}
    if not path.is_file():
        raise FactsQueryError(f"no facts store for source {source_id!r} at {path}; run the "
                              f"tabular stage first")
    stmt = _check_statement(sql)
    digest = sha256_file(path)
    schema, ordered, columns, fetched = _run(path, stmt, params, max_rows)
    # R-022 f1 #6: a pointer names a cell by (query, row ordinal, COLUMN NAME) — two result
    # columns with one name would be two cells with one pointer. Refused; alias them.
    dupes = sorted({c for c in columns if columns.count(c) > 1})
    if dupes:
        raise FactsQueryError(f"the result has duplicate column names {dupes}: alias each one "
                              f"(AS ...) so every cell has its own pointer")
    truncated = len(fetched) > max_rows
    rows = [list(r) for r in fetched[:max_rows]]
    extracted = manifest.get("extracted_at") or dt.datetime.fromtimestamp(
        path.stat().st_mtime, dt.timezone.utc).isoformat(timespec="seconds")
    if manifest.get("sha256") and manifest["sha256"] != digest:
        raise FactsQueryError("the facts file does not match its manifest hash — it was altered "
                              "after publication; re-run the tabular stage")
    psha = params_digest(params)
    rsha = result_digest(columns, [[str(v) if isinstance(v, bytes) else v for v in r]
                                   for r in rows])
    qhash = query_identity(stmt, psha, rsha, ordered=ordered, rows=len(rows))
    query_ref = ""
    if qhash != hashlib.sha256(stmt.encode("utf-8")).hexdigest():
        # R-022 f2 #8: the statement alone does not resolve these pointers — persist what does
        if isinstance(params, dict):
            bound: Any = {str(k): _param_value(v) for k, v in params.items()}
        else:
            bound = [_param_value(v) for v in (params or ())]
        query_ref = _write_query_artefact(source_id, facts_dir, qhash, {
            "query_hash": qhash, "source_id": source_id, "sql": stmt, "params": bound,
            "params_kind": "named" if isinstance(params, dict) else "positional",
            "params_sha256": psha, "ordered": ordered,
            "result_sha256": rsha if not (ordered or len(rows) <= 1) else None,
            "rows": len(rows), "max_rows": max_rows})
    snap = SnapshotIdentity(source_id=source_id,
                            as_of=dt.datetime.fromisoformat(extracted.replace("Z", "+00:00")),
                            content_hash=digest,
                            schema_hash=hashlib.sha256(schema.encode("utf-8")).hexdigest(),
                            query_hash=qhash)
    pointers = [SourcePointer(source_id=source_id, kind="query_cell", snapshot=snap,
                              query_hash=qhash, row_ordinal=i, column=c, capture_digest=digest,
                              trust=Trust.UNTRUSTED)
                for i in range(len(rows)) for c in columns]
    # the parameters are cited by DIGEST (a bound value may be personal data), with their count
    pnote = (f" with {len(params)} bound parameter(s) (sha256 {psha[:12]}…)" if psha else "")
    citation = (f"[SQL] {stmt}{pnote} — facts store {path.name} (sha256 {digest[:12]}…), source "
                f"{source_id}, extracted {extracted}; result sha256 {rsha[:12]}…")
    return CountAnswer(source_id=source_id, sql=stmt, columns=columns, rows=rows,
                       row_count=len(rows), truncated=truncated, extract_date=extracted,
                       facts_file=str(path), facts_sha256=digest, citation=citation,
                       question=question, pointers=pointers, result_sha256=rsha,
                       params_sha256=psha, ordered=ordered, query_ref=query_ref)


def resolve_query_cell(pointer: SourcePointer, *, sql: Optional[str] = None,
                       facts_dir: Optional[Path | str] = None) -> Any:
    """The value a `query_cell` pointer names, re-derived (R-022 f2 #8): the facts file must still
    be the one the pointer captured; the query is re-run (from its artefact when the statement
    alone is not the identity, with the bound parameters re-bound) and its identity recomputed —
    a different result, order or parameter set never resolves."""
    if pointer.kind != "query_cell" or not pointer.query_hash:
        raise FactsQueryError("not a query_cell pointer")
    path = facts_path(pointer.source_id, facts_dir)
    if not path.is_file() or sha256_file(path) != pointer.capture_digest:
        raise FactsQueryError("the facts file the pointer captured is no longer published")
    art = queries_dir(pointer.source_id, facts_dir) / f"{pointer.query_hash}.json"
    if art.is_file():
        body = json.loads(art.read_text(encoding="utf-8"))
        stmt = body["sql"]
        if body.get("params_kind") == "named":
            params: Any = {k: _param_restore(v) for k, v in (body.get("params") or {}).items()}
        else:
            params = tuple(_param_restore(v) for v in body.get("params") or [])
        max_rows = int(body.get("max_rows") or MAX_ROWS)
    elif sql is not None and hashlib.sha256(
            _check_statement(sql).encode("utf-8")).hexdigest() == pointer.query_hash:
        stmt, params, max_rows = _check_statement(sql), None, MAX_ROWS
    else:
        raise FactsQueryError(f"no query artefact for {pointer.query_hash[:12]}… and no "
                              f"statement whose identity it is (pass sql=)")
    _schema, ordered, columns, fetched = _run(path, stmt, params, max_rows)
    rows = [list(r) for r in fetched[:max_rows]]
    rsha = result_digest(columns, [[str(v) if isinstance(v, bytes) else v for v in r]
                                   for r in rows])
    if query_identity(stmt, params_digest(params), rsha, ordered=ordered,
                      rows=len(rows)) != pointer.query_hash:
        raise FactsQueryError("the re-run query no longer has the pointer's identity")
    if pointer.row_ordinal is None or not 0 <= pointer.row_ordinal < len(rows) \
            or pointer.column not in columns:
        raise FactsQueryError("the pointer's row or column is not in the result")
    return rows[pointer.row_ordinal][columns.index(pointer.column)]


def describe_facts(source_id: str, *, facts_dir: Optional[Path | str] = None) -> dict:
    """What an agent needs to write SQL: every facts table, its mode and columns, the date."""
    manifest = load_manifest(source_id, facts_dir)
    if manifest is None:
        raise FactsQueryError(f"no facts store for source {source_id!r}")
    ans = count_query(source_id, "SELECT facts_table, column_name, source_type, class, prose "
                                 "FROM _facts_columns ORDER BY facts_table, rowid",
                      facts_dir=facts_dir)
    cols: dict[str, list] = {}
    for t, c, typ, cls, prose in ans.rows:
        cols.setdefault(t, []).append({"column": c, "type": typ, "class": cls,
                                       "prose": bool(prose)})
    return {"source_id": source_id, "extracted_at": manifest.get("extracted_at"),
            "facts_file": manifest.get("facts_file"),
            "tables": [{**t, "columns": cols.get(t.get("facts_table") or "", [])}
                       for t in manifest.get("tables") or []]}


__all__ = ["count_query", "describe_facts", "CountAnswer", "FactsQueryError", "params_digest",
           "result_digest", "query_identity", "proven_order", "resolve_query_cell",
           "queries_dir"]

"""Executing a plan step against a live client: enumerate what exists, decide scope, fetch bytes.

Nothing here knows a server. A step says which tool lists, how to parse its output, and which
tool reads; the engine walks, applies the connection's DECLARED scope, and fetches. Every item it
sees is reported with a decision, so the collect stage can ledger it (P3, P5).

Identity (P4): `item_id = sha256("mcp://<source_id>/<full uri>")` — the source-qualified FULL
path or URI the server reported, never the display name, so two `notes.txt` in different folders
are two items and a citation can never land on the wrong one.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

from .client import ContentPart, McpClientError
from .connections import Scope
from .extract import ext_of, ext_of_mime, extractor_for, guess_mime
from .parse import join_path, parse_output, render
from .plans import StepSpec, sql_dialect


def item_identity(source_id: str, uri: str) -> str:
    return hashlib.sha256(f"mcp://{source_id}/{uri}".encode("utf-8")).hexdigest()


@dataclass
class Item:
    source_id: str
    step_id: str
    kind: str                       # "document" | "table"
    uri: str                        # full path / URI / schema.table, as the server names it
    name: str
    rel: str                        # path relative to its root — what scope globs match
    mime: Optional[str] = None
    size: Optional[int] = None
    ctx: dict = field(default_factory=dict)
    attrs: dict = field(default_factory=dict)
    """Listing fields the plan asked to keep (`list.meta_fields`), e.g. a modified time."""
    scope_problem: Optional[str] = None
    """Set when `rel` (what scope globs match) could not be derived unambiguously from the uri —
    the item is then excluded, never matched against a guessed path (R-022 #4)."""
    alt_rel: Optional[str] = None
    """A listed document's CANONICAL path (percent-decoded, `.`/`..` resolved) when it differs
    from `rel` as the server spelled it (R-022 f2 #2). `blocked` / `exclude` apply if EITHER
    spelling matches; `include` admits only if BOTH do — so a spelling can never step around a
    rule, and `rel` (which item identity templates may use) is left exactly as listed."""

    def scope_rels(self) -> tuple[str, ...]:
        if not self.alt_rel or self.alt_rel == self.rel:
            return (self.rel,)
        return (self.rel, self.alt_rel)

    @property
    def item_id(self) -> str:
        return item_identity(self.source_id, self.uri)

    def as_record(self) -> dict:
        rec = {"item_id": self.item_id, "source_id": self.source_id, "step": self.step_id,
               "kind": self.kind, "uri": self.uri, "name": self.name, "rel": self.rel,
               "mime": self.mime, "size": self.size}
        if self.attrs:
            rec["attrs"] = dict(self.attrs)
        return rec


@dataclass(frozen=True)
class Decision:
    outcome: str                    # collect | excluded | blocked
    rule: str = ""
    reverse: str = ""
    hold: Optional[str] = None
    """The adaptation id when an adapter agent HOLDS an otherwise in-scope item (rule
    `DRIFT_PENDING_APPROVAL`). The core runner still captures such an item (so it stays
    inventoried and observable) but never builds it; everything else that decides scope (the
    database recipe's profiling, mapping, facts and cards) skips it."""


_HOLDS_CACHE: dict[str, tuple[Optional[int], object]] = {}


def _holds_for(source_id: str):
    """The source's drift holds (`<sources>/<id>.holds.yaml`), re-read only when the file
    changes. A holds file that does not parse RAISES — a hold is never silently dropped."""
    from .records import DriftHolds, holds_path, load_holds

    path = holds_path(source_id)
    try:
        mtime: Optional[int] = path.stat().st_mtime_ns
    except OSError:
        return DriftHolds(source_id=source_id)
    key = str(path)
    hit = _HOLDS_CACHE.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    holds = load_holds(source_id)
    _HOLDS_CACHE[key] = (mtime, holds)
    return holds


def _glob(rel: str, pattern: str) -> bool:
    rel_l = rel.replace("\\", "/").lower()
    pat = pattern.replace("\\", "/").lower()
    cands = {rel_l, "/" + rel_l}
    pats = {pat}
    if pat.startswith("**/"):
        pats.add(pat[3:])
    return any(fnmatch.fnmatchcase(c, p) for c in cands for p in pats)


def decide(item: Item, scope: Scope, *, collected_so_far: int, holds=None) -> Decision:
    """The declared scope, applied in a fixed order. Every non-collect names its rule + reversal.

    `holds`: an item that scope would collect but an adapter agent holds (a pending shape drift)
    is `excluded` with rule `DRIFT_PENDING_APPROVAL` and `Decision.hold` set. None (the default)
    reads the source's holds file; pass a `records.DriftHolds` to use one already loaded, or
    False to decide on scope alone (the drift PROBE must observe a held item, or it would read
    its absence as a removal)."""
    decision = _decide_scope(item, scope, collected_so_far=collected_so_far)
    if decision.outcome != "collect" or holds is False:
        return decision
    if holds is None:
        holds = _holds_for(item.source_id)
    aid = holds.item_hold(item.kind, item.uri, item.rel)
    if not aid:
        return decision
    from .records import DRIFT_PENDING_APPROVAL

    return Decision("excluded", DRIFT_PENDING_APPROVAL, holds.reverse_text(aid), hold=aid)


def _decide_scope(item: Item, scope: Scope, *, collected_so_far: int) -> Decision:
    if item.kind == "table":
        for pat in scope.tables_exclude:
            if fnmatch.fnmatchcase(item.uri.lower(), pat.lower()):
                return Decision("excluded", f"tables_exclude:{pat}",
                                f"remove {pat!r} from scope.tables_exclude")
        if scope.tables_include and not any(fnmatch.fnmatchcase(item.uri.lower(), p.lower())
                                            for p in scope.tables_include):
            return Decision("excluded", "not_in_tables_include",
                            "add a matching pattern to scope.tables_include")
        if collected_so_far >= scope.max_items:
            return Decision("excluded", "max_items", "raise scope.max_items")
        return Decision("collect")
    if item.scope_problem:
        return Decision("excluded", "unresolvable_path",
                        f"{item.scope_problem}; declare a scope.roots entry (or the plan's "
                        f"uri_prefix) that this uri lies under")
    rels = item.scope_rels()
    for pat in scope.blocked:
        if any(_glob(r, pat) for r in rels):
            return Decision("blocked", f"blocked:{pat}",
                            f"remove {pat!r} from scope.blocked (a data-owner decision)")
    if scope.include and not all(any(_glob(r, p) for p in scope.include) for r in rels):
        return Decision("excluded", "not_included", "add a matching pattern to scope.include")
    for pat in scope.exclude:
        if any(_glob(r, pat) for r in rels):
            return Decision("excluded", f"exclude:{pat}", f"remove {pat!r} from scope.exclude")
    # a name without an extension (a Google Doc title) takes the extension of the type it is
    # captured as, so `file_types: [.md]` admits a Doc exported as Markdown
    ext = ext_of(item.name or item.uri) or ext_of_mime(item.mime)
    if scope.file_types and ext not in scope.file_types:
        return Decision("excluded", f"file_type:{ext or '(none)'}",
                        f"add {ext or 'this type'!r} to scope.file_types")
    if extractor_for(item.name or item.uri, item.mime) is None:
        return Decision("excluded", f"no_extractor:{ext or item.mime or '(unknown)'}",
                        "add an extractor for this type (packages/mcp_ingest extract.py)")
    if item.size is not None and item.size > scope.max_bytes:
        return Decision("excluded", "size_cap", f"raise scope.max_bytes above {item.size}")
    if collected_so_far >= scope.max_items:
        return Decision("excluded", "max_items", "raise scope.max_items")
    return Decision("collect")


class CrawlError(RuntimeError):
    pass


def _call_text(client, tool: str, args: dict) -> str:
    result = client.call_tool(tool, args)
    if result.is_error:
        raise CrawlError(f"{tool} returned an error: {result.text[:300]}")
    return result.text


def _list_pages(client, spec, args: dict) -> Iterator[str]:
    """Every page of one listing call. Without `page` there is exactly one call. A repeated token
    or too many pages raises — the listing is then recorded as incomplete, never cut short
    silently (trap 1: success with partial results)."""
    text = _call_text(client, spec.tool, args)
    yield text
    if spec.page is None:
        return
    rx = re.compile(spec.page.regex, re.M)
    seen: set[str] = set()
    for _ in range(spec.page.max_pages - 1):
        m = rx.search(text)
        if not m:
            return
        token = m.group("token")
        if token in seen:
            raise CrawlError(f"{spec.tool} repeated page token {token[:24]!r}; listing stopped")
        seen.add(token)
        text = _call_text(client, spec.tool, {**args, spec.page.arg: token})
        yield text
    if rx.search(text):
        raise CrawlError(f"{spec.tool}: more than {spec.page.max_pages} pages; raise "
                         f"list.page.max_pages")


# --- documents ---------------------------------------------------------------------------------
def enumerate_documents(client, step: StepSpec, scope: Scope, source_id: str,
                        on_dir: Optional[Callable[[dict], None]] = None) -> Iterator[Item]:
    """Walk the listing tool from each root. Directories are descended (depth-capped); a blocked
    directory is reported through `on_dir` and never entered — its contents are never seen."""
    assert step.list is not None
    # `${VAR}` in a declared root (infra/mcp_servers.yaml: "${WALNUT_GDRIVE_FOLDER_ID}") is
    # expanded here, at run time, like every other `${...}` in a connection — never stored
    from .connections import expand

    roots: list[str] = [expand(str(r)) for r in scope.roots]
    if not roots and step.roots is not None:
        text = _call_text(client, step.roots.tool, render(step.roots.args, {}))
        roots = [r.get("path") or r.get("id") or r.get("name") for r in
                 parse_output(text, step.roots.parse)]
        roots = [r for r in roots if r]
    if not roots:
        if step.list.root_arg_default is None:
            raise CrawlError("no roots: set scope.roots, or use a plan with a roots step")
        roots = [step.list.root_arg_default]
    dir_values = {v.lower() for v in step.list.dir_values}

    def identity(entry: dict, root: str, parent: str, rel: str, address: str) -> str:
        if not step.list.uri:
            return address
        ctx = {k: v for k, v in entry.items() if isinstance(v, (str, int, float))}
        ctx.update({"root": root, "parent": parent, "rel": rel, "address": address,
                    "id": entry.get("id") or address})
        return str(render(step.list.uri, ctx))

    def walk(root: str, parent: str, rel_prefix: str, depth: int) -> Iterator[Item]:
        args = render(step.list.args, {"path": parent, "id": parent})
        entries = [e for text in _list_pages(client, step.list, args)
                   for e in parse_output(text, step.list.parse)]
        for entry in entries:
            name = str(entry.get("name") or entry.get("id") or entry.get("path") or "")
            if not name:
                continue
            address = str(entry.get("path") or entry.get("id") or join_path(parent, name))
            rel = f"{rel_prefix}/{name}" if rel_prefix else name
            uri = identity(entry, root, parent, rel, address)
            is_dir = str(entry.get("type", "")).lower() in dir_values
            # R-022 f2 #2: the server's names are untrusted — the canonical spelling is matched
            # too, and a name or a declared path that leaves its directory is a problem
            canon, problem = listed_rel(rel_prefix, name)
            if problem is None and entry.get("path"):
                problem = address_problem(str(entry["path"]), parent, name)
            rels = (rel,) if canon in (None, rel) else (rel, canon)
            if is_dir:
                if not step.list.recurse:
                    continue
                if problem:
                    if on_dir:
                        on_dir({"uri": uri, "rel": rel, "outcome": "excluded",
                                "rule": "unresolvable_path", "problem": problem,
                                "reverse": "the server lists this directory under a name or "
                                           "path that leaves its parent; fix the listing"})
                    continue
                blocked = next((p for p in scope.blocked
                                if any(_glob(r + "/", p) for r in rels)), None)
                if blocked:
                    if on_dir:
                        on_dir({"uri": uri, "rel": rel, "outcome": "blocked",
                                "rule": f"blocked:{blocked}",
                                "reverse": f"remove {blocked!r} from scope.blocked"})
                    continue
                if depth + 1 > scope.max_depth:
                    if on_dir:
                        on_dir({"uri": uri, "rel": rel, "outcome": "excluded",
                                "rule": "max_depth",
                                "reverse": f"raise scope.max_depth above {scope.max_depth}"})
                    continue
                yield from walk(root, address, rel, depth + 1)
                continue
            size = entry.get("size")
            try:
                size = int(size) if size not in (None, "") else None
            except (TypeError, ValueError):
                size = None
            ctx = {k: v for k, v in entry.items() if isinstance(v, (str, int, float))}
            ctx.update({"path": address, "id": entry.get("id") or address, "name": name})
            attrs = {f: entry[f] for f in step.list.meta_fields if entry.get(f) not in (None, "")}
            yield Item(source_id=source_id, step_id=step.id, kind="document", uri=uri, attrs=attrs,
                       name=name, rel=rel,
                       mime=(entry.get("mime") or entry.get("mimeType")
                             or getattr(step.list, "item_mime", None)),
                       size=size, ctx=ctx, scope_problem=problem,
                       alt_rel=canon if canon != rel else None)

    for root in roots:
        yield from walk(str(root), str(root), "", 0)


def fetch_document(client, step: StepSpec, item: Item) -> tuple[bytes, str]:
    """Bytes + mime for one document item."""
    if step.strategy == "resources":
        parts = client.read_resource(item.uri)
        return _bytes_of(parts, item)
    assert step.read is not None
    if step.read.resource_uri:
        return _bytes_of(client.read_resource(str(render(step.read.resource_uri, item.ctx))), item)
    # R-022 #8: an extensionless item is typed by its declared mime, exactly as `decide` and
    # `extractor_for` typed it — a DOCX/PDF declared by mime is fetched through the binary tool
    ext = ext_of(item.name or item.uri) or ext_of_mime(item.mime)
    binary_exts = {e.lower() for e in step.read.binary_extensions}
    use_binary = step.read.binary_tool and (ext in binary_exts or not step.read.text_tool)
    tool = step.read.binary_tool if use_binary else step.read.text_tool
    args = step.read.binary_args if use_binary else step.read.text_args
    result = client.call_tool(tool, render(args, item.ctx))
    if result.is_error:
        raise CrawlError(f"{tool} returned an error: {result.text[:300]}")
    return _bytes_of(result.parts, item)


def _bytes_of(parts: tuple[ContentPart, ...], item: Item) -> tuple[bytes, str]:
    blob = next((p for p in parts if p.kind == "blob"), None)
    if blob is not None:
        mime = blob.mime if blob.mime and blob.mime != "application/octet-stream" else None
        return blob.data, guess_mime(item.name or item.uri, mime or item.mime)
    text = "\n".join(p.text for p in parts if p.kind == "text")
    declared = next((p.mime for p in parts if p.mime), None)
    return text.encode("utf-8"), guess_mime(item.name or item.uri, declared or item.mime)


def under_root(uri: str, root: str) -> bool:
    """`uri` is the root itself or lies below it (a path boundary, never a bare string prefix:
    root `docs` does not contain `docs-old/x`)."""
    root = root.strip()
    if not root:
        return False
    return uri == root or uri.startswith(root if root.endswith(("/", ":")) else root + "/")


def listed_rel(rel_prefix: str, name: str) -> tuple[Optional[str], Optional[str]]:
    """(canonical rel, problem) for a document listed as `name` inside the directory at
    `rel_prefix` (R-022 f2 #2): percent-escapes decoded, backslashes read as `/`, `.`/`..` resolved — the
    same canonical form `resource_rel` gives a resource. A name that resolves to nothing or climbs
    out of its directory is a problem (the item is excluded, the directory not entered)."""
    import posixpath
    from urllib.parse import unquote

    decoded = unquote(name).replace("\\", "/").strip("/")
    leaf = posixpath.normpath(decoded) if decoded else ""
    if leaf in ("", ".") or leaf == ".." or leaf.startswith("../"):
        return None, f"listed name {name!r} does not name an entry inside its directory"
    prefix = unquote(rel_prefix).replace("\\", "/").strip("/") if rel_prefix else ""
    canon = posixpath.normpath(f"{prefix}/{leaf}") if prefix else leaf
    if canon == ".." or canon.startswith("../"):
        return None, f"listed name {name!r} climbs out of its root"
    return canon, None


def address_problem(address: str, parent: str, name: str) -> Optional[str]:
    """When the listing declares a path-like `path` for an entry, it must lie DIRECTLY inside the
    directory that was listed and end in the listed name — otherwise scope rules (which match
    names) and the fetch (which uses the path) would see two different files (R-022 f2 #2). Ids
    (no separator, e.g. a Drive file id) are not paths and are not checked."""
    import posixpath
    from urllib.parse import unquote

    def norm(v: str) -> str:
        return posixpath.normpath(unquote(v).replace("\\", "/")) if v else ""

    path_like = ("/", "\\")
    if not any(s in address for s in path_like) or not any(s in parent for s in path_like):
        return None
    a, p = norm(address), norm(parent).rstrip("/")
    leaf = norm(name).strip("/")
    if not (a.startswith(p + "/") or (p == "" and a)):
        return f"listed path {address!r} is not inside the directory listed ({parent!r})"
    if a[len(p) + 1:] != leaf:
        return f"listed path {address!r} does not match the listed name {name!r}"
    return None


def resource_rel(uri: str, roots: list[str], uri_prefix: str = "") -> tuple[str, Optional[str]]:
    """(rel, problem) for a listed resource: its path BELOW the declared root it lies under (the
    longest one), else below the plan's `uri_prefix`, else its whole path after `scheme://` — so
    `include` / `exclude` / `blocked` globs see the hierarchy, never just a display name
    (R-022 #4). Percent-escapes are decoded and `.` / `..` segments resolved before matching, so
    `private%2Fsecret.pdf` or `x/../private/y` cannot step around `blocked: [private/**]`. A path
    that climbs out of its root, or is empty, is a `problem` (the item is excluded)."""
    import posixpath
    from urllib.parse import unquote

    base = ""
    under = [r for r in roots if under_root(uri, r)]
    if under:
        base = max(under, key=len)
    elif uri_prefix and uri.startswith(uri_prefix):
        base = uri_prefix
    tail = uri[len(base):] if base else uri.split("://", 1)[-1]
    decoded = unquote(tail).replace("\\", "/").strip("/")
    rel = posixpath.normpath(decoded) if decoded else ""
    if rel in ("", "."):
        if base and uri.rstrip("/") == base.rstrip("/"):          # the root itself
            name = unquote(uri).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            return (name, None) if name else ("", f"resource uri {uri!r} names no path")
        return "", f"resource uri {uri!r} names no path below its root"
    if rel == ".." or rel.startswith("../"):
        return "", f"resource uri {uri!r} climbs out of its root"
    return rel, None


def enumerate_resources(client, step: StepSpec, source_id: str, scope: Optional[Scope] = None,
                        on_outside: Optional[Callable[[int, list], None]] = None
                        ) -> Iterator[Item]:
    """Every listed resource under the step's `uri_prefix`. When the connection DECLARES
    `scope.roots`, they are binding here too: a resource whose uri is not under one of them is
    never captured — however much the server lists (a signed-in Drive lists the whole Drive).
    Those are not itemised (their names are outside what the owner declared); their COUNT is
    reported through `on_outside` and recorded."""
    from .connections import expand

    roots = [expand(str(r)) for r in (scope.roots if scope is not None else [])]
    outside = 0
    for r in client.list_resources():
        if step.uri_prefix and not r.uri.startswith(step.uri_prefix):
            continue
        if roots and not any(under_root(r.uri, root) for root in roots):
            outside += 1
            continue
        name = r.name or r.uri.rsplit("/", 1)[-1]
        # R-022 #4: scope globs match the resource's PATH under its root, never its display name
        rel, problem = resource_rel(r.uri, roots, step.uri_prefix or "")
        # a resource name rarely carries an extension; let the declared mime pick the extractor
        yield Item(source_id=source_id, step_id=step.id, kind="document", uri=r.uri, name=name,
                   rel=rel, mime=r.mime, size=r.size, ctx={"uri": r.uri, "name": name},
                   scope_problem=problem)
    if outside and on_outside is not None:
        on_outside(outside, roots)


# --- tabular -------------------------------------------------------------------------------------
def _sql(client, step: StepSpec, template: str, ctx: dict,
         dialect: Optional[dict] = None) -> list[dict]:
    """Run one dialect template. `{x|ident}` is quoted by the DIALECT's rule (`quote:`), not
    always ANSI (R-022 #9)."""
    from .tabular.profiling import render_sql

    assert step.sql is not None
    sql = render_sql(template, ctx, dialect)
    text = _call_text(client, step.sql.tool, {step.sql.arg: sql, **step.sql.extra_args})
    return parse_output(text, step.sql.parse)


def enumerate_tables(client, step: StepSpec, source_id: str) -> Iterator[Item]:
    dialect = sql_dialect(step.sql.dialect)
    for row in _sql(client, step, dialect["list_tables"], {}, dialect):
        schema, table = row.get("table_schema"), row.get("table_name")
        if not table:
            continue
        uri = f"{schema}.{table}" if schema else str(table)
        yield Item(source_id=source_id, step_id=step.id, kind="table", uri=uri, name=str(table),
                   rel=uri, mime="application/json",
                   ctx={"schema": schema or "", "table": table})


def _q(name: str, dialect: Optional[dict] = None) -> str:
    """An identifier quoted by the dialect's `quote:` (ANSI double quotes when it declares none):
    in default MySQL `ORDER BY "id"` orders by a string constant, not the column (R-022 #9)."""
    from .tabular.profiling import dialect_quote

    return dialect_quote(dialect, name)


def fetch_table(client, step: StepSpec, item: Item, *, limit: Optional[int] = None,
                page_rows: int = 1000) -> tuple[bytes, dict]:
    """Describe, key, count and read one table — the WHOLE table by default (C-2 / FIN-18), page
    by page in key order through the dialect's `select_page`, `page_rows` rows per call. `limit`
    is an explicit cap (the connection's `scope.max_rows_per_table`, None = none): a capped table
    is recorded as such (`snapshot["limit"]`) and the run reports "N of M rows" (`coverage`).
    A dialect without `select_page` reads one bounded statement (the cap, else the counted total)
    — still never silently short: the snapshot says what was read and what was counted.
    Returns canonical JSON bytes (what is captured) and the parsed snapshot."""
    dialect = sql_dialect(step.sql.dialect)
    ctx = dict(item.ctx)
    columns = [{"name": r.get("column_name"), "type": r.get("data_type")}
               for r in _sql(client, step, dialect["describe"], ctx, dialect)]
    keys = _sql(client, step, dialect["keys"], ctx, dialect) if dialect.get("keys") else []
    pk = [k["column_name"] for k in keys if str(k.get("constraint_type")).upper() == "PRIMARY KEY"]
    fks = [{"column": k["column_name"], "ref_schema": k.get("ref_schema"),
            "ref_table": k.get("ref_table"), "ref_column": k.get("ref_column")}
           for k in keys if str(k.get("constraint_type")).upper() == "FOREIGN KEY"]
    total = None
    if dialect.get("count"):
        counted = _sql(client, step, dialect["count"], ctx, dialect)
        if counted:
            try:
                total = int(next(iter(counted[0].values())))
            except (TypeError, ValueError, StopIteration):
                total = None
    cap = int(limit) if limit else None
    page_rows = max(1, int(page_rows or 1000))
    key_order = ", ".join(_q(c, dialect) for c in pk) if pk else "1"
    rows: list[dict] = []
    if total is not None and total <= page_rows:
        # the whole table fits one statement (the counted total): one read, as always
        rows = _sql(client, step, dialect["select"],
                    {**ctx, "order_by": key_order,
                     "limit": cap if cap is not None and cap < page_rows else page_rows}, dialect)
    elif dialect.get("select_page"):
        # a keyless table is ordered by EVERY column, so OFFSET pages do not shift under ties
        order_by = key_order if pk else (", ".join(str(i + 1) for i in range(len(columns)))
                                         or "1")
        prev_page: Optional[list] = None
        while True:
            want = page_rows if cap is None else min(page_rows, cap - len(rows))
            if want <= 0:
                break
            got = _sql(client, step, dialect["select_page"],
                       {**ctx, "order_by": order_by, "limit": want, "offset": len(rows)}, dialect)
            if got and len(got) == want and got == prev_page:
                raise CrawlError(f"{item.uri}: the server returned the same page twice (OFFSET "
                                 f"ignored?); the table cannot be read whole by paging")
            prev_page = got
            rows.extend(got)
            if len(got) < want:
                break
    else:
        bound = cap if cap is not None else (total if total is not None else page_rows)
        rows = _sql(client, step, dialect["select"],
                    {**ctx, "order_by": key_order, "limit": max(bound, 1)}, dialect)
    snapshot = {"schema": ctx.get("schema"), "table": ctx.get("table"), "columns": columns,
                "primary_key": pk, "foreign_keys": fks, "row_count_total": total,
                # the capture is content-addressed: HOW it was paged is not part of it
                "limit": cap, "rows": rows}
    data = json.dumps(snapshot, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    return data, json.loads(data)


def table_text(snapshot: dict) -> str:
    """The captured table as text: one JSON row per line (what `.txt` holds for a table)."""
    return "\n".join(json.dumps(r, sort_keys=True, ensure_ascii=False, default=str)
                     for r in snapshot.get("rows") or [])


__all__ = ["Item", "Decision", "decide", "item_identity", "enumerate_documents",
           "enumerate_resources", "enumerate_tables", "fetch_document", "fetch_table",
           "table_text", "CrawlError", "McpClientError", "resource_rel", "listed_rel",
           "address_problem"]

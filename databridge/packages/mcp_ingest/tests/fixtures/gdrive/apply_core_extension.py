"""Apply the S3 core extension to databridge_mcp_ingest — for the coordinator, not for tests.

    (from databridge/)
    python packages/mcp_ingest/tests/fixtures/gdrive/apply_core_extension.py packages/mcp_ingest/databridge_mcp_ingest

The same change as `core_extension.patch`, written as exact-text replacements so it can be
re-applied after other workstreams move the surrounding lines (the core was being edited while
this was written). Each replacement asserts its anchor occurs exactly once and stops otherwise —
it never guesses. Line endings are preserved per file (some core files are CRLF, some LF).

What it adds (generic — nothing names a server):
  plans.py    PageSpec; ListSpec.page / .uri / .meta_fields; ReadSpec.resource_uri; validation of
              resource reads (route must read resources; template fields must exist); a followed
              page arg is not reported as first-page-only; `writes_by_name` splits camelCase
  crawl.py    paging loop (repeated token / too many pages -> error, never a silent cut); identity
              uri template; item attrs from meta_fields; `${VAR}` in scope.roots expanded at run
              time; resource reads for documents steps; a nameless-extension item scoped by the
              extension of its declared mime
  extract.py  ext_of_mime()
  runner.py   item attrs into captured.jsonl and capture meta (put_meta keeps extra keys)
"""
import pathlib
import sys

PKG = pathlib.Path(sys.argv[1])
_state = {"s": "", "crlf": False}


def load(name):
    raw = (PKG / name).read_bytes().decode("utf-8")
    _state["crlf"] = "\r\n" in raw
    _state["s"] = raw.replace("\r\n", "\n")


def save(name):
    s = _state["s"]
    if _state["crlf"]:
        s = s.replace("\n", "\r\n")
    (PKG / name).write_bytes(s.encode("utf-8"))


def rep(old, new):
    s = _state["s"]
    assert s.count(old) == 1, (s.count(old), old[:90])
    _state["s"] = s.replace(old, new)


# ------------------------------------------------------------------------------------ plans.py
load("plans.py")
rep('''def writes_by_name(name: str) -> bool:
    return bool(WRITE_VERB.search(name) or WRITE_VERB_EXTRA.search(name))
''', '''_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def words_of(name: str) -> str:
    """`createGoogleDoc` -> `create_Google_Doc`. The verb patterns need a separator around the
    verb, so a camelCase name (`createFolder`, `deleteItem`, `shareFile`) is split first."""
    return _CAMEL.sub("_", name)


def writes_by_name(name: str) -> bool:
    words = words_of(name)
    return bool(WRITE_VERB.search(words) or WRITE_VERB_EXTRA.search(words))
''')
rep('''class ListSpec(CallSpec):
    recurse: bool = True''', '''class PageSpec(_Strict):
    """How a listing tool pages. After each call `regex` is searched (multiline) in its output; a
    match's `token` group is passed as argument `arg` on the next call, until no match. A repeated
    token or more than `max_pages` pages is an ERROR (recorded; the enumeration is incomplete) —
    never a silent truncation (trap 1)."""
    arg: str
    regex: str
    max_pages: int = 1000


class ListSpec(CallSpec):
    recurse: bool = True''')
rep('''    `excluded: no_extractor`, which is recorded, not silent."""


class ReadSpec(_Strict):
    text_tool: Optional[str] = None
    text_args: dict[str, Any] = Field(default_factory=dict)
    binary_tool: Optional[str] = None
    binary_args: dict[str, Any] = Field(default_factory=dict)
    binary_extensions: list[str] = Field(default_factory=list)
''', '''    `excluded: no_extractor`, which is recorded, not silent."""
    page: Optional[PageSpec] = None
    uri: Optional[str] = None
    """Template for an item's IDENTITY uri (P4), rendered from the entry's fields plus `root`,
    `rel` (the path below the root, names joined by "/"), `parent` and `address`. Default: the
    address the server reported. Use it when the address is an opaque id and identity must be the
    source-qualified full path (e.g. "gdrive:///{root}/{rel}?id={id}")."""
    meta_fields: list[str] = Field(default_factory=list)
    """Listing fields carried onto the item as `attrs` — inventory and captured.jsonl rows and the
    capture meta keep them (e.g. a modified time, the source-native mime)."""


class ReadSpec(_Strict):
    text_tool: Optional[str] = None
    text_args: dict[str, Any] = Field(default_factory=dict)
    binary_tool: Optional[str] = None
    binary_args: dict[str, Any] = Field(default_factory=dict)
    binary_extensions: list[str] = Field(default_factory=list)
    resource_uri: Optional[str] = None
    """Read each listed item with `resources/read` at this uri template (e.g. "gdrive:///{id}")
    instead of a tool: listed by tool (so the declared scope applies), read as a resource (text
    or bytes)."""
''')
rep('''            if step.read.binary_tool:
                calls.append((step.read.binary_tool, step.read.binary_args, item_fields))
        if step.sql:''', '''            if step.read.binary_tool:
                calls.append((step.read.binary_tool, step.read.binary_args, item_fields))
            if step.read.resource_uri and item_fields is not None:
                unknown = sorted(template_fields(step.read.resource_uri) - item_fields)
                if unknown:
                    problems.append(f"step {step.id!r}: read.resource_uri uses {unknown}, which "
                                    f"the listing never provides")
        if step.sql:''')
rep('''        if step.strategy == "documents":
            if step.list is None or step.read is None or not (step.read.text_tool
                                                               or step.read.binary_tool):
                problems.append(f"step {step.id!r}: a documents step needs list + read tools")''',
'''        if step.strategy == "documents":
            if step.list is None or step.read is None or not (step.read.text_tool
                                                               or step.read.binary_tool
                                                               or step.read.resource_uri):
                problems.append(f"step {step.id!r}: a documents step needs list + read tools")
            if step.read is not None and step.read.resource_uri and not supports_resources:
                problems.append(f"step {step.id!r}: reads items as resources, which this route "
                                f"cannot do — connect the server directly (via: direct)")''')
rep('''            props = (by_name[spec.tool].input_schema or {}).get("properties") or {}
            out.extend(f"{spec.tool}.{p}" for p in props
                       if _PAGE_ARG.match(p) and p not in spec.args)''',
'''            props = (by_name[spec.tool].input_schema or {}).get("properties") or {}
            followed = {spec.page.arg} if getattr(spec, "page", None) is not None else set()
            out.extend(f"{spec.tool}.{p}" for p in props
                       if _PAGE_ARG.match(p) and p not in spec.args and p not in followed)''')
rep('''        page = _NEXT_PAGE.search(raw) if step.list is not None else None''',
    '''        page = (_NEXT_PAGE.search(raw) if step.list is not None and step.list.page is None
                else None)''')
rep('''__all__ = ["CrawlPlan", "StepSpec", "CallSpec", "ListSpec", "ReadSpec", "SqlSpec", "Profile",''',
    '''__all__ = ["CrawlPlan", "StepSpec", "CallSpec", "ListSpec", "PageSpec", "ReadSpec", "SqlSpec",
           "Profile", "words_of",''')
save("plans.py")

# ------------------------------------------------------------------------------------ crawl.py
load("crawl.py")
rep('''import fnmatch
import hashlib
import json
''', '''import fnmatch
import hashlib
import json
import re
''')
rep('''from .extract import ext_of, extractor_for, guess_mime''',
    '''from .extract import ext_of, ext_of_mime, extractor_for, guess_mime''')
rep('''    ctx: dict = field(default_factory=dict)

    @property
    def item_id(self) -> str:
        return item_identity(self.source_id, self.uri)

    def as_record(self) -> dict:
        return {"item_id": self.item_id, "source_id": self.source_id, "step": self.step_id,
                "kind": self.kind, "uri": self.uri, "name": self.name, "rel": self.rel,
                "mime": self.mime, "size": self.size}
''', '''    ctx: dict = field(default_factory=dict)
    attrs: dict = field(default_factory=dict)
    """Listing fields the plan asked to keep (`list.meta_fields`), e.g. a modified time."""

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
''')
rep('''    ext = ext_of(item.name or item.uri)
    if scope.file_types and ext not in scope.file_types:''',
'''    # a name without an extension (a Google Doc title) takes the extension of the type it is
    # captured as, so `file_types: [.md]` admits a Doc exported as Markdown
    ext = ext_of(item.name or item.uri) or ext_of_mime(item.mime)
    if scope.file_types and ext not in scope.file_types:''')
rep('''    roots: list[str] = list(scope.roots)
''', '''    # `${VAR}` in a declared root (infra/mcp_servers.yaml: "${WALNUT_GDRIVE_FOLDER_ID}") is
    # expanded here, at run time, like every other `${...}` in a connection — never stored
    from .connections import expand

    roots: list[str] = [expand(str(r)) for r in scope.roots]
''')
rep('''def _call_text(client, tool: str, args: dict) -> str:
    result = client.call_tool(tool, args)
    if result.is_error:
        raise CrawlError(f"{tool} returned an error: {result.text[:300]}")
    return result.text
''', '''def _call_text(client, tool: str, args: dict) -> str:
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
''')
rep('''    def walk(parent: str, rel_prefix: str, depth: int) -> Iterator[Item]:
        text = _call_text(client, step.list.tool, render(step.list.args,
                                                         {"path": parent, "id": parent}))
        for entry in parse_output(text, step.list.parse):
            name = str(entry.get("name") or entry.get("id") or entry.get("path") or "")
            if not name:
                continue
            address = str(entry.get("path") or entry.get("id") or join_path(parent, name))
            rel = f"{rel_prefix}/{name}" if rel_prefix else name
            is_dir = str(entry.get("type", "")).lower() in dir_values''',
'''    def identity(entry: dict, root: str, parent: str, rel: str, address: str) -> str:
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
            is_dir = str(entry.get("type", "")).lower() in dir_values''')
rep('''                if blocked:
                    if on_dir:
                        on_dir({"uri": address, "rel": rel, "outcome": "blocked",''',
'''                if blocked:
                    if on_dir:
                        on_dir({"uri": uri, "rel": rel, "outcome": "blocked",''')
rep('''                if depth + 1 > scope.max_depth:
                    if on_dir:
                        on_dir({"uri": address, "rel": rel, "outcome": "excluded",''',
'''                if depth + 1 > scope.max_depth:
                    if on_dir:
                        on_dir({"uri": uri, "rel": rel, "outcome": "excluded",''')
rep('''                yield from walk(address, rel, depth + 1)
                continue''', '''                yield from walk(root, address, rel, depth + 1)
                continue''')
rep('''            ctx.update({"path": address, "id": entry.get("id") or address, "name": name})
            yield Item(source_id=source_id, step_id=step.id, kind="document", uri=address,''',
'''            ctx.update({"path": address, "id": entry.get("id") or address, "name": name})
            attrs = {f: entry[f] for f in step.list.meta_fields if entry.get(f) not in (None, "")}
            yield Item(source_id=source_id, step_id=step.id, kind="document", uri=uri, attrs=attrs,''')
rep('''    for root in roots:
        yield from walk(str(root), "", 0)''', '''    for root in roots:
        yield from walk(str(root), str(root), "", 0)''')
rep('''    assert step.read is not None
    ext = ext_of(item.name or item.uri)''', '''    assert step.read is not None
    if step.read.resource_uri:
        return _bytes_of(client.read_resource(str(render(step.read.resource_uri, item.ctx))), item)
    ext = ext_of(item.name or item.uri)''')
save("crawl.py")

# ---------------------------------------------------------------------------------- extract.py
load("extract.py")
rep('''def guess_mime(''', '''def ext_of_mime(mime: Optional[str]) -> str:
    """The extension a declared mime stands for ("" when unknown) — for items whose NAME carries
    no extension (a Google Doc titled "Kick-off notes", captured as text/markdown)."""
    if not mime:
        return ""
    m = mime.split(";")[0].strip().lower()
    return _MIME_TO_EXT.get(m) or (mimetypes.guess_extension(m) or "")


def guess_mime(''')
rep('''           "ext_of", "docling_available"]''', '''           "ext_of", "ext_of_mime", "docling_available"]''')
save("extract.py")

# ----------------------------------------------------------------------------------- runner.py
load("runner.py")
rep('''    capture.put_meta(sha, {"source": conn.id, "uri": item.uri, "mime": mime,
                           "title": item.name, "retrieved_at": now_iso()})''',
'''    meta = {"source": conn.id, "uri": item.uri, "mime": mime, "title": item.name,
            "retrieved_at": now_iso()}
    if item.attrs:
        meta["attrs"] = dict(item.attrs)          # e.g. modified time + source-native mime
    capture.put_meta(sha, meta)''')
rep('''    ledgers["captured"].write({"item_id": item.item_id, "uri": item.uri, "name": item.name,
                               "kind": item.kind, "sha256": sha, "bytes": len(data),
                               "mime": mime, "step": item.step_id, "at": now_iso()})''',
'''    ledgers["captured"].write({"item_id": item.item_id, "uri": item.uri, "name": item.name,
                               "kind": item.kind, "sha256": sha, "bytes": len(data),
                               "mime": mime, "step": item.step_id, "at": now_iso(),
                               **({"attrs": dict(item.attrs)} if item.attrs else {})})''')
save("runner.py")
print("patched", PKG)

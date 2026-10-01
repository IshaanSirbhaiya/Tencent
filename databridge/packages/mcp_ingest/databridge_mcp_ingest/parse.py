"""Two small interpreters the crawl profiles are written in — argument templates and output parsers.

This is how server-specific knowledge stays in YAML: a profile says "call `list_directory` with
`{path: "{path}"}` and read each output line with this regex", and the engine only knows how to
render a template and run a parser. No Python branch names a server.

**Templates.** `"{name}"` alone returns the raw value (so an integer stays an integer); inside a
longer string it is substituted as text. Filters make SQL safe to compose from names a server
reported: `{table|ident}` → `"my""table"`, `{schema|literal}` → `'public'`, `{limit|int}`.

**Parsers** turn a tool's text output into a list of dicts:
  lines        one regex per line shape; named groups become fields; `set:` adds constants
  json         parse JSON (or a Python literal), walk `items_path`, map fields
  tree         a nested JSON tree (name/type/children) flattened to relative paths
  json_rows    a list of row dicts (JSON, or a Python repr with Decimal/date wrappers)
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping, Optional

_FIELD = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?:\|([a-z]+))?\}")
_WHOLE = re.compile(r"^\{([a-zA-Z_][a-zA-Z0-9_]*)(?:\|([a-z]+))?\}$")


class TemplateError(ValueError):
    pass


class ParseError(ValueError):
    pass


def _filter(value: Any, name: Optional[str]) -> Any:
    if name is None or name == "raw":
        return value
    if name == "ident":
        return '"' + str(value).replace('"', '""') + '"'
    if name == "literal":
        return "'" + str(value).replace("'", "''") + "'"
    if name == "int":
        return int(value)
    raise TemplateError(f"unknown template filter {name!r} (known: raw, ident, literal, int)")


def render(template: Any, ctx: Mapping[str, Any]) -> Any:
    """Render a template (str / list / dict, recursively) against `ctx`."""
    if isinstance(template, dict):
        return {k: render(v, ctx) for k, v in template.items()}
    if isinstance(template, list):
        return [render(v, ctx) for v in template]
    if not isinstance(template, str):
        return template
    whole = _WHOLE.match(template)
    if whole:
        key, filt = whole.group(1), whole.group(2)
        if key not in ctx:
            raise TemplateError(f"template {template!r} needs {key!r}; have {sorted(ctx)}")
        return _filter(ctx[key], filt)

    def repl(m: re.Match) -> str:
        key, filt = m.group(1), m.group(2)
        if key not in ctx:
            raise TemplateError(f"template {template!r} needs {key!r}; have {sorted(ctx)}")
        return str(_filter(ctx[key], filt))

    return _FIELD.sub(repl, template)


def template_fields(template: Any) -> set[str]:
    """Every `{name}` a template references — used to validate a plan before running it."""
    if isinstance(template, dict):
        return set().union(*(template_fields(v) for v in template.values())) if template else set()
    if isinstance(template, list):
        return set().union(*(template_fields(v) for v in template)) if template else set()
    if isinstance(template, str):
        return {m.group(1) for m in _FIELD.finditer(template)}
    return set()


# --- structured text --------------------------------------------------------------------------
def loads_structured(text: str) -> Any:
    """JSON, or a Python literal (some servers return `str(list_of_dicts)`). Constructor values
    such as `Decimal('1.50')` or `datetime.date(2026, 1, 2)` become their canonical TEXT ("1.50",
    "2026-01-02") — the digits the server wrote, never re-typed through a float."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass

    # A Python repr (`str(list_of_row_dicts)`): parsed with `ast` against a closed whitelist of
    # constructors (`tabular.pyrepr`), rendered as canonical text — `Decimal('3')` → "3",
    # `datetime.date(2026, 8, 31)` → "2026-08-31", tz-aware datetimes → ISO 8601 with offset. A
    # string value that merely CONTAINS `Decimal(` is never rewritten (the old regex did).
    from .tabular.pyrepr import loads as repr_loads

    try:
        return repr_loads(text)
    except (ValueError, SyntaxError) as exc:
        raise ParseError(f"output is neither JSON nor a Python literal: {text[:120]!r}") from exc


def _walk(data: Any, dotted: str) -> Any:
    if not dotted:
        return data
    cur = data
    for part in dotted.split("."):
        if isinstance(cur, Mapping):
            cur = cur.get(part)
        else:
            return None
    return cur


def parse_output(text: str, spec: Mapping[str, Any]) -> list[dict]:
    """Run one parser spec over a tool's text output."""
    mode = spec.get("mode", "lines")
    if mode == "lines":
        patterns = [(re.compile(p["regex"]), dict(p.get("set") or {}))
                    for p in spec.get("patterns") or []]
        if not patterns:
            raise ParseError("lines parser needs at least one pattern")
        out = []
        for line in text.splitlines():
            line = line.rstrip()
            if not line:
                continue
            for rx, extra in patterns:
                m = rx.match(line)
                if m:
                    item = {k: v.strip() if isinstance(v, str) else v
                            for k, v in m.groupdict().items() if v is not None}
                    item.update(extra)
                    out.append(item)
                    break
        return out
    if mode in ("json", "json_rows"):
        data = _walk(loads_structured(text), str(spec.get("items_path") or ""))
        if data is None:
            return []
        if isinstance(data, Mapping):
            data = [data]
        if not isinstance(data, list):
            raise ParseError(f"items_path did not reach a list (got {type(data).__name__})")
        fields = spec.get("fields")
        rows = []
        for item in data:
            if not isinstance(item, Mapping):
                if mode == "json" and "value_field" in spec:
                    rows.append({spec["value_field"]: item})
                continue
            rows.append({k: _walk(item, v) for k, v in fields.items()} if fields else dict(item))
        return rows
    if mode == "tree":
        data = loads_structured(text)
        name_key = spec.get("name_key", "name")
        type_key = spec.get("type_key", "type")
        children_key = spec.get("children_key", "children")
        dir_values = set(spec.get("dir_values") or ["directory", "dir", "folder"])
        out: list[dict] = []

        def visit(nodes: Any, prefix: str) -> None:
            for node in nodes or []:
                if not isinstance(node, Mapping):
                    continue
                name = str(node.get(name_key, ""))
                rel = f"{prefix}/{name}" if prefix else name
                is_dir = str(node.get(type_key, "")) in dir_values or children_key in node
                out.append({"name": name, "rel": rel, "type": "dir" if is_dir else "file"})
                if is_dir:
                    visit(node.get(children_key), rel)

        visit(data if isinstance(data, list) else [data], "")
        return out
    raise ParseError(f"unknown parser mode {mode!r} (lines, json, json_rows, tree)")


def join_path(parent: str, name: str) -> str:
    """Join with the separator the SERVER uses in `parent` — a Windows server reports `C:\\x`."""
    if not parent:
        return name
    sep = "\\" if ("\\" in parent and "/" not in parent) else "/"
    return parent if not name else (parent.rstrip("/\\") + sep + name)


__all__ = ["render", "template_fields", "parse_output", "loads_structured", "join_path",
           "TemplateError", "ParseError"]

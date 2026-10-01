"""Drafting a SQL dialect for a NEW database engine — detect, draft, live-probe, approve (W4).

Adding an engine used to mean a person hand-writing `<engine>.sql.yaml`. Now:

    draft_dialect(source_id)      connect → find the SQL tool → DETECT the engine (rules first:
                                  each template's version probe, tool-name hints) → pick its
                                  template; no match → the model (task class schema_annotation)
                                  drafts one from the engine's self-description (P16) →
                                  LIVE-PROBE it read-only → save `<source>.dialect.yaml` PROPOSED
    approve_dialect(source_id, principal=…)   an AUTHENTICATED principal approves (R-022 f1)
    approved_dialect(source_id)   the approved template, which the tabular stage then uses

The live probe runs every catalogue query through the connection's own SQL tool and reports
"found N tables, M columns, K key relationships" plus every query that failed. A role that can
read tables but not the catalogue the template reaches for first (the PostgreSQL lesson: a
SELECT-only role sees NO keys in information_schema) is detected by trying every `keys`
alternative the template carries and keeping the one that sees the most — said in the report.

A DIALECT TEMPLATE is one YAML (`profiles/sql_dialects/<engine>.sql.yaml`, or the vetted
`profiles/postgres/postgres.sql.yaml`). Its keys:

    name, engine, title        identity (`name` is what a plan's `sql.dialect` names)
    status                     verified (live engine + live MCP server) | engine_tested (live
                               engine, no MCP server seen) | draft (never run — say so in
                               `unverified`)
    detect.probes              [{sql, match}] — a version query and a regex its first value must
                               match; detect.tool_hints (names typical of the engine's MCP
                               servers; a weak signal); detect.priority
    mcp.rows                   auto | json | repr | csv — how the server returns rows
    mcp.error_prefixes         text a failed statement starts with when the server reports it
                               as ordinary output (isError false)
    quote                      identifier quote: '"' (default), '`', or '[]' (open, close)
    catalogue queries          see RESULT_COLUMNS for each one's required result columns
    keys_alternatives          [{name, note, sql}] — other catalogues for keys
    table_stats[_column|_range] the combined one-statement-per-table profiling (optional)
    tabular_policy             overrides of tabular/defaults.yaml (e.g. type classes)

Nothing here names an engine: every engine-specific word lives in a template.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..parse import TemplateError
from .policy import load_policy, type_class
from .profiling import dialect_quote, is_error_text, parse_rows, render_sql

#: The harness task class the model drafts with (closed-vocab structural annotation).
DRAFT_TASK_CLASS = "schema_annotation"

#: Result columns each catalogue query must return (the shape the tabular stage reads).
RESULT_COLUMNS: dict[str, set[str]] = {
    "list_tables": {"table_schema", "table_name"},
    "describe": {"column_name", "data_type"},
    "keys": {"column_name", "constraint_type"},
    "count": {"n"},
    "row_estimate": {"n"},
    "column_stats": {"n", "non_null", "distinct_n", "avg_len", "max_len", "avg_spaces"},
    "column_range": {"lo", "hi"},
}
#: Every template must carry these (the tabular stage and the core crawl read them).
REQUIRED = ("list_tables", "describe", "keys", "count", "select", "select_page", "column_stats",
            "column_range", "row_estimate", "aggregate")
OPTIONAL_SQL = ("table_stats", "table_stats_column", "table_stats_range")
STATUSES = ("verified", "engine_tested", "draft")
#: Placeholders a template may use (ctx the engine supplies) and filters parse.render knows.
_FIELDS = {"schema", "table", "column", "limit", "offset", "order_by", "group_cols", "measures",
           "p"}
_FILTERS = {None, "raw", "ident", "literal", "int"}
_FIELD_RX = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?:\|([a-z]+))?\}")
#: A catalogue statement is a read: it starts with one of these …
_READ_START = re.compile(r"(?is)^\s*(select|with|show|describe|desc)\b")
# … and names none of these (whole words; a fixed template, so a keyword check is sound). R-022 f1
# #4 widened it: `into` (SELECT … INTO creates a table), session/transaction control, locks, and
# statements that execute or load something.
_WRITE_WORDS = re.compile(
    r"(?i)\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|merge|call|exec|"
    r"execute|copy|attach|detach|vacuum|install|into|set|reset|lock|unlock|pragma|load|import|"
    r"export|upsert|refresh|optimize|analyze|analyse|reindex|cluster|checkpoint|begin|commit|"
    r"rollback|savepoint|prepare|deallocate|listen|notify|comment|rename|use|handler|do|"
    r"discard|replace\s+into)\b")
#: R-022 f1 #4: a statement that STARTS with SELECT can still write (`SELECT nextval('s')`, a
#: side-effecting UDF). Every function a statement calls must be on a READ-ONLY allow-list: these
#: generic SQL functions, plus the `read_functions` a SHIPPED (human-reviewed) template declares
#: for its engine. A model-drafted template may use only this list and the shipped declarations —
#: its own `read_functions`, if any, are ignored.
READ_FUNCTIONS = frozenset({
    "abs", "array_agg", "array_length", "avg", "cardinality", "cast", "ceil", "ceiling",
    "char_length", "character_length", "coalesce", "concat", "count", "date_trunc", "dense_rank",
    "extract", "first_value", "floor", "greatest", "group_concat", "ifnull", "instr", "isnull",
    "lag", "last_value", "lead", "least", "left", "length", "lower", "ltrim", "max", "min", "mod",
    "nullif", "nvl", "octet_length", "position", "power", "rank", "replace", "right", "round",
    "row_number", "rtrim", "sign", "sqrt", "string_agg", "strpos", "substr", "substring", "sum",
    "trim", "unnest", "upper", "version"})
#: Words that may precede "(" without being a function call.
_PAREN_WORDS = frozenset({
    "in", "and", "or", "not", "exists", "as", "from", "join", "on", "over", "values", "using",
    "filter", "within", "any", "all", "some", "case", "when", "then", "else", "where", "select",
    "top", "by", "union", "intersect", "except", "with", "lateral", "is", "between", "like",
    "having", "limit", "offset", "cross", "inner", "outer", "left", "right", "full", "set"})
_CALL_RX = re.compile(r"(?<![\w.$\"`\]])([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)\s*\(")
_STRING_RX = re.compile(r"'(?:[^']|'')*'")
_QUOTED_ID_RX = re.compile(r"\"(?:[^\"]|\"\")*\"|`[^`]*`|\[[^\]]*\]")
_FN_NAME = re.compile(r"^[a-z_][a-z0-9_$]*(\.[a-z_][a-z0-9_$]*)*$")
#: A read-only SESSION statement a shipped template may declare (`read_only_session.sql`): a
#: SET / PRAGMA that switches the session to read-only — nothing else.
_SESSION_RX = re.compile(r"(?is)^\s*(set|pragma)\b[^;]*\b(read[\s_]*only|query_only)\b[^;]*$")
_DUMMY = {"schema": "s", "table": "t", "column": "c", "limit": 1, "offset": 0,
          "order_by": '"c"', "group_cols": '"c"', "measures": "", "p": "c0"}


class DialectError(ValueError):
    pass


# --- templates ------------------------------------------------------------------------------------
def _profile_dirs() -> list[Path]:
    from .. import paths

    dirs = [paths.bundled_profiles_dir()]
    extra = paths.extra_profiles_dir()
    if extra is not None:
        dirs.append(extra)
    return [d for d in dirs if d.is_dir()]


def load_templates() -> list[dict]:
    """Every dialect TEMPLATE (a `*.sql.yaml` that declares an `engine`) under the profile dirs.
    `_path` records where each came from."""
    out: list[dict] = []
    for d in _profile_dirs():
        for f in sorted(d.rglob("*.sql.yaml")):
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict) and data.get("engine"):
                out.append({**data, "_path": str(f)})
    return out


def _declared_functions(t: dict) -> set[str]:
    return {str(f).lower() for f in (t.get("read_functions") or []) if isinstance(f, str)}


def vetted_read_functions() -> frozenset[str]:
    """The generic allow-list plus every `read_functions` a SHIPPED template declares."""
    out = set(READ_FUNCTIONS)
    for t in load_templates():
        out |= {f for f in _declared_functions(t) if _FN_NAME.match(f)}
    return frozenset(out)


def called_functions(sql: str) -> list[str]:
    """Lower-case names of every function a statement calls (string literals and quoted
    identifiers removed first; keywords and `AS <type>(` / `AS alias(` are not calls)."""
    body = _QUOTED_ID_RX.sub(" q ", _STRING_RX.sub(" '' ", sql or ""))
    out: list[str] = []
    for m in _CALL_RX.finditer(body):
        name = m.group(1).lower()
        if name in _PAREN_WORDS:
            continue
        before = re.findall(r"[A-Za-z_]+", body[:m.start()])
        if before and before[-1].lower() == "as":
            continue                              # CAST(x AS NVARCHAR(MAX)) / AS k(a, b)
        out.append(name)
    return out


def check_statement(name: str, sql: str, allowed: frozenset[str] | set[str], *,
                    fragment: bool = False) -> list[str]:
    """The read-only checks for ONE statement: single statement, starts as a read, no write
    keyword, every function on the allow-list."""
    problems: list[str] = []
    body = (sql or "").strip().rstrip(";")
    if ";" in body:
        problems.append(f"{name}: more than one statement")
    if not fragment and not _READ_START.match(body):
        problems.append(f"{name}: not a read (must start SELECT / WITH / SHOW / DESCRIBE)")
    hit = _WRITE_WORDS.search(_STRING_RX.sub("''", body))
    if hit:
        problems.append(f"{name}: names a write keyword ({hit.group(1)})")
    bad = sorted({f for f in called_functions(body) if f not in allowed})
    if bad:
        problems.append(f"{name}: calls function(s) not on the read-only allow-list {bad}")
    return problems


def _sql_texts(t: dict) -> dict[str, str]:
    texts = {k: t[k] for k in (*REQUIRED, *OPTIONAL_SQL) if isinstance(t.get(k), str)}
    for i, alt in enumerate(t.get("keys_alternatives") or []):
        if isinstance(alt, dict) and isinstance(alt.get("sql"), str):
            texts[f"keys_alternatives[{i}]"] = alt["sql"]
    for i, pr in enumerate((t.get("detect") or {}).get("probes") or []):
        if isinstance(pr, dict) and isinstance(pr.get("sql"), str):
            texts[f"detect.probes[{i}]"] = pr["sql"]
    ros = t.get("read_only_session") or {}
    if isinstance(ros, dict) and isinstance(ros.get("check"), str):
        texts["read_only_session.check"] = ros["check"]
    return texts


def allowed_functions(t: dict, *, origin: str = "rules") -> frozenset[str]:
    """What `t` may call: the vetted list, plus its OWN declarations unless a model drafted it."""
    allowed = set(vetted_read_functions())
    if origin != "model":
        allowed |= {f for f in _declared_functions(t) if _FN_NAME.match(f)}
    return frozenset(allowed)


def validate_template(t: dict, *, origin: str = "rules") -> list[str]:
    """Problems with a dialect template; empty = well-formed. Checks shape, read-only statements
    (keywords AND a function allow-list — R-022 f1 #4), placeholders, quoting, detection regexes,
    the optional read-only session statement and the honesty fields (verified needs evidence;
    anything else must not claim it). `origin="model"`: the template's own `read_functions` are
    ignored (a model cannot extend the allow-list)."""
    problems: list[str] = []
    for k in ("name", "engine"):
        if not isinstance(t.get(k), str) or not re.fullmatch(r"[a-z][a-z0-9_]*", t.get(k) or ""):
            problems.append(f"{k!r} must be a lower-case identifier")
    status = t.get("status")
    if status not in STATUSES:
        problems.append(f"status must be one of {STATUSES}, got {status!r}")
    if status in ("verified", "engine_tested") and not str(t.get("verified") or "").strip():
        problems.append(f"status {status!r} needs a `verified` evidence line")
    if status == "draft" and not str(t.get("unverified") or "").strip():
        problems.append("a draft template must say what is unverified (`unverified`)")
    for k in REQUIRED:
        if not isinstance(t.get(k), str) or not t[k].strip():
            problems.append(f"missing catalogue query {k!r}")
    if bool(t.get("table_stats")) != bool(t.get("table_stats_column")):
        problems.append("table_stats and table_stats_column go together")
    q = str(t.get("quote") or '"')
    if len(q) not in (1, 2) or q[0].isalnum():
        problems.append(f"quote must be one or two non-alphanumeric characters, got {q!r}")
    mcp = t.get("mcp") or {}
    if str(mcp.get("rows") or "auto") not in ("auto", "json", "repr", "csv"):
        problems.append("mcp.rows must be auto | json | repr | csv")
    if not isinstance(mcp.get("error_prefixes") or [], list):
        problems.append("mcp.error_prefixes must be a list")
    rf = t.get("read_functions")
    if rf is not None and (not isinstance(rf, list) or not all(
            isinstance(f, str) and _FN_NAME.match(f.lower()) for f in rf)):
        problems.append("read_functions must be a list of function names")
    ros = t.get("read_only_session")
    if ros is not None:
        if not isinstance(ros, dict) or not isinstance(ros.get("sql"), str) \
                or not isinstance(ros.get("check"), str) or not ros.get("expect"):
            problems.append("read_only_session needs sql, check and expect")
        else:
            if not _SESSION_RX.match(ros["sql"]):
                problems.append("read_only_session.sql must be one SET/PRAGMA that makes the "
                                "session read-only")
            try:
                re.compile(str(ros["expect"]))
            except re.error as exc:
                problems.append(f"read_only_session.expect is not a regex: {exc}")
    probes = (t.get("detect") or {}).get("probes") or []
    if not probes:
        problems.append("detect.probes: at least one version probe is needed")
    for i, pr in enumerate(probes):
        try:
            re.compile(str((pr or {}).get("match") or ""))
        except re.error as exc:
            problems.append(f"detect.probes[{i}].match is not a regex: {exc}")
        if not (pr or {}).get("match"):
            problems.append(f"detect.probes[{i}] has no match")
    allowed = allowed_functions(t, origin=origin)
    for name, sql in _sql_texts(t).items():
        body = sql.strip().rstrip(";")
        fragment = name in ("table_stats_column", "table_stats_range")
        problems += check_statement(name, body, allowed, fragment=fragment)
        for m in _FIELD_RX.finditer(body):
            if m.group(1) not in _FIELDS:
                problems.append(f"{name}: unknown placeholder {{{m.group(1)}}}")
            if m.group(2) not in _FILTERS:
                problems.append(f"{name}: unknown filter |{m.group(2)}")
        try:
            render_sql(body, _DUMMY, t)
        except (TemplateError, ValueError, KeyError) as exc:
            problems.append(f"{name}: does not render ({exc})")
    return problems


def template_digest(t: dict) -> str:
    """sha256 of a template's content (keys starting `_` excluded) — what a probe and an
    approval are bound to (R-022 f1 #4)."""
    body = {k: v for k, v in t.items() if not str(k).startswith("_")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


# --- the SQL tool ---------------------------------------------------------------------------------
class SqlTool:
    """One read-only SQL tool on an open MCP session. Every call is timed and logged; a failure is
    returned (never raised) so the probe can report it and move on."""

    def __init__(self, client, tool: str, arg: str, *, extra_args: Optional[dict] = None,
                 error_prefixes: Optional[list[str]] = None) -> None:
        self.client, self.tool, self.arg = client, tool, arg
        self.extra_args = dict(extra_args or {})
        self.error_prefixes = list(error_prefixes or [])
        self.log: list[dict] = []

    def run(self, sql: str, dialect: Optional[dict] = None) -> tuple[bool, Any, str]:
        """(ok, rows | error text, format seen: json | repr | csv | '')."""
        t0 = time.monotonic()
        rec: dict = {"sql": sql[:500]}
        try:
            res = self.client.call_tool(self.tool, {self.arg: sql, **self.extra_args})
            text = getattr(res, "text", "") or ""
            prefixes = {"mcp": {"error_prefixes": [*self.error_prefixes,
                                                   *(((dialect or {}).get("mcp") or {})
                                                     .get("error_prefixes") or [])]}}
            if getattr(res, "is_error", False) or is_error_text(text, prefixes):
                rec.update(ok=False, error=text.strip()[:300])
                return False, text.strip()[:300], ""
            rows = parse_rows(text, dialect)
            fmt = _format_of(text, dialect) if rows else ""     # "[]" is both JSON and repr
            rec.update(ok=True, rows=len(rows), format=fmt)
            return True, rows, fmt
        except Exception as exc:                                     # noqa: BLE001
            rec.update(ok=False, error=f"{type(exc).__name__}: {str(exc)[:300]}")
            return False, rec["error"], ""
        finally:
            rec["seconds"] = round(time.monotonic() - t0, 3)
            self.log.append(rec)


def _format_of(text: str, dialect: Optional[dict]) -> str:
    fmt = str(((dialect or {}).get("mcp") or {}).get("rows") or "auto")
    if fmt != "auto":
        return fmt
    try:
        json.loads(text or "null")
        return "json"
    except ValueError:
        return "repr"


_SQL_ARGS = ("sql", "query", "statement", "sql_query")


def find_sql_tool(tools: list, templates: list[dict], *, plan=None) -> Optional[tuple[str, str]]:
    """(tool, argument) that runs SQL: the plan's tabular step if there is one; else a tool whose
    schema takes one string argument named like SQL — preferring names the templates hint at.
    Write-capable tools are never chosen."""
    from ..plans import is_write_tool

    if plan is not None:
        for s in getattr(plan, "steps", []) or []:
            if s.strategy == "tabular" and s.sql is not None:
                return s.sql.tool, s.sql.arg
    hinted = {h for t in templates for h in (t.get("detect") or {}).get("tool_hints") or []}
    cands = []
    for t in tools:
        if is_write_tool(t):
            continue
        props = ((t.input_schema or {}).get("properties") or {})
        arg = next((a for a in _SQL_ARGS if a in props), None)
        if arg:
            cands.append((t.name not in hinted, t.name, arg))
    return (sorted(cands)[0][1], sorted(cands)[0][2]) if cands else None


# --- detection ------------------------------------------------------------------------------------
def detect_engine(sql: SqlTool, tool_names: set[str], templates: list[dict]) -> dict:
    """Rules first. Each template's version probes run (identical SQL once); a probe whose first
    value matches the template's regex is DECISIVE. Tool-name hints only order and break ties;
    alone they are a weak signal, reported as such. Returns {engine, template, method, evidence,
    probes, candidates}."""
    def hint_score(t: dict) -> int:
        return len(set((t.get("detect") or {}).get("tool_hints") or []) & tool_names)

    ordered = sorted(templates, key=lambda t: (-hint_score(t),
                                               -int((t.get("detect") or {}).get("priority") or 0),
                                               t.get("engine")))
    seen: dict[str, dict] = {}
    for t in ordered:
        for pr in (t.get("detect") or {}).get("probes") or []:
            q = str(pr.get("sql") or "")
            if q and q not in seen:
                # R-022 f1 #4: a version probe is a statement like any other — refused before
                # it reaches the source unless it passes the read-only checks.
                bad = check_statement("detect.probe", q, allowed_functions(t))
                if bad:
                    seen[q] = {"sql": q, "ok": False, "value": None,
                               "error": f"refused before running: {'; '.join(bad)}"[:200]}
                    continue
                ok, rows, _ = sql.run(q, t)
                first = None
                if ok and rows:
                    first = next(iter(rows[0].values()), None)
                seen[q] = {"sql": q, "ok": ok, "value": None if first is None else str(first),
                           "error": None if ok else str(rows)[:200]}
    matched = []
    for t in ordered:
        for pr in (t.get("detect") or {}).get("probes") or []:
            got = seen.get(str(pr.get("sql") or ""))
            if got and got["ok"] and got["value"] is not None \
                    and re.search(str(pr.get("match")), got["value"]):
                matched.append((t, f"{pr['sql']!r} returned {got['value'][:80]!r}, matching "
                                   f"{pr['match']!r}"))
                break
    out = {"probes": list(seen.values()), "candidates": [t["engine"] for t, _ in matched]}
    if matched:
        best = sorted(matched, key=lambda m: (-hint_score(m[0]),
                                              -int((m[0].get("detect") or {})
                                                   .get("priority") or 0)))[0]
        ev = [best[1]]
        if hint_score(best[0]):
            ev.append(f"tool names {sorted(set(best[0]['detect']['tool_hints']) & tool_names)}")
        if len(matched) > 1:
            ev.append(f"also matched {[t['engine'] for t, _ in matched if t is not best[0]]}; "
                      f"chose by tool hints, then priority")
        return {**out, "engine": best[0]["engine"], "template": best[0], "method": "probe",
                "evidence": ev}
    hinted = [t for t in ordered if hint_score(t)]
    top = [t for t in hinted if hint_score(t) == hint_score(hinted[0])] if hinted else []
    if len(top) == 1:
        return {**out, "engine": top[0]["engine"], "template": top[0], "method": "tool_names",
                "evidence": [f"no version probe matched; tool names "
                             f"{sorted(set(top[0]['detect']['tool_hints']) & tool_names)} "
                             f"suggest {top[0]['engine']} (a WEAK signal — the live probe decides)"]}
    return {**out, "engine": None, "template": None, "method": "none",
            "evidence": ["no version probe matched and tool names do not single out an engine"]}


# --- the model path -------------------------------------------------------------------------------
class DraftedDialect(BaseModel):
    """What the model drafts: a dialect template, field for field."""

    model_config = ConfigDict(extra="ignore")
    engine: str = Field(description="lower-case engine id, e.g. 'mysql'")
    title: str = ""
    version_probe_sql: str = Field(description="a SELECT returning the version as column v")
    version_match: str = Field(description="regex the version value matches for this engine")
    quote: str = Field('"', description="identifier quote: '\"', '`' or '[]'")
    list_tables: str
    describe: str
    keys: str
    count: str
    row_estimate: str
    select: str
    select_page: str
    column_stats: str
    column_range: str
    aggregate: str
    keys_alternative: Optional[str] = Field(None, description="another catalogue for keys")
    notes: str = ""


def _reference_template() -> dict:
    """The vetted template shown to the model as the shape to follow (highest-status, then
    priority — no engine named here)."""
    rank = {s: i for i, s in enumerate(STATUSES)}
    ts = sorted(load_templates(), key=lambda t: (rank.get(t.get("status"), 9),
                                                 -int((t.get("detect") or {})
                                                      .get("priority") or 0)))
    if not ts:
        return {}
    return {k: ts[0][k] for k in ("engine", "quote", *REQUIRED) if k in ts[0]}


def model_draft(harness, *, tool: Optional[Any], detection: dict) -> dict:
    """The model drafts a template from the engine's self-description (what every version probe
    answered or refused with, and the SQL tool's description). Status is `draft`, origin model."""
    system = {"role": "system", "content": (
        "You write a READ-ONLY SQL dialect template for a database engine, used to catalogue it. "
        "Every statement must be a single SELECT (or WITH/SHOW). Use these placeholders only: "
        "{schema|literal} {table|literal} as string values, {schema|ident} {table|ident} "
        "{column|ident} as identifiers, {limit|int} {offset|int}, and {order_by} {group_cols} "
        "{measures} raw. Result columns MUST be aliased exactly: list_tables → table_schema, "
        "table_name; describe → column_name, data_type; keys → column_name, constraint_type "
        "('PRIMARY KEY' | 'FOREIGN KEY'), ref_schema, ref_table, ref_column; count and "
        "row_estimate → n; column_stats → n, non_null, distinct_n, avg_len, max_len, avg_spaces; "
        "column_range → lo, hi. Follow the reference template's shape; change only what this "
        "engine needs. If the engine's catalogue may hide keys from a read-only role, give the "
        "other catalogue as keys_alternative.")}
    self_description = {
        "sql_tool": ({"name": getattr(tool, "name", ""),
                      "description": (getattr(tool, "description", "") or "")[:600]}
                     if tool is not None else None),
        "version_probes": detection.get("probes")}
    # R-022 f1 #3: the tool description and every probe answer / error are SOURCE-controlled
    # text. They are screened deterministically first; only a clean `allow` reaches the model.
    from ..mapping import screen_for_model

    send, rec = screen_for_model(self_description)
    if not send:
        fired = sorted({str(f.get("rule")) for f in rec.get("fired") or []})
        raise DialectError(f"the engine's self-description was not sent to the model: the screen "
                           f"decided {rec.get('decision')!r}"
                           f"{' with masking' if rec.get('redactions') else ''} (rules {fired}); "
                           f"write the template by hand")
    user = {"role": "user", "content": json.dumps({
        "engine_self_description": self_description,
        "reference_template": _reference_template()}, sort_keys=True, default=str)}
    result = harness.complete_structured(DRAFT_TASK_CLASS, [system, user], DraftedDialect, seed=7)
    d: DraftedDialect = result.parsed
    engine = re.sub(r"[^a-z0-9_]", "_", (d.engine or "engine").lower()).strip("_") or "engine"
    if not engine[0].isalpha():
        engine = "e_" + engine
    tpl = {"name": f"{engine}_drafted", "engine": engine, "title": d.title or engine,
           "status": "draft",
           "unverified": ("model-drafted from the engine's self-description; only the live probe "
                          "below has run it" + (f" — model notes: {d.notes}" if d.notes else "")),
           "detect": {"priority": 0, "tool_hints": [],
                      "probes": [{"sql": d.version_probe_sql, "match": d.version_match}]},
           "mcp": {"rows": "auto", "error_prefixes": ["Error:"]},
           "quote": d.quote or '"'}
    for k in REQUIRED:
        tpl[k] = getattr(d, k)
    if d.keys_alternative:
        tpl["keys_alternatives"] = [{"name": "model_alternative",
                                     "note": "the model's second catalogue for keys",
                                     "sql": d.keys_alternative}]
    return tpl


# --- the live probe -------------------------------------------------------------------------------
#: Queries that answer ONE row (a count, a statistic): an empty answer is a failure, not a pass
#: (R-022 f1 #13).
SCALAR = ("count", "row_estimate", "column_stats", "column_range")
#: Every statistic the combined statement emits per column, compared with the per-column
#: reference statements (R-022 f1 #15).
_STATS_COMPARED = ("non_null", "distinct_n", "avg_len", "max_len", "avg_spaces")


def _wellformed(name: str, rows: list[dict]) -> Optional[str]:
    need = RESULT_COLUMNS.get(name)
    if name in SCALAR and len(rows) != 1:
        return f"expected exactly one row, got {len(rows)}"
    if not need or not rows:
        return None
    have = {str(k).lower() for k in rows[0]}
    missing = sorted(need - have)
    return f"result lacks column(s) {missing} (has {sorted(have)[:12]})" if missing else None


def _lower(rows: list[dict]) -> list[dict]:
    return [{str(k).lower(): v for k, v in r.items()} for r in rows]


def _same(a: Any, b: Any) -> bool:
    """Two measured statistics agree: equal as numbers (tolerant of Decimal/float/text forms and
    float rounding), else equal as text; two NULLs agree."""
    if a is None or b is None:
        return a is None and b is None
    try:
        fa, fb = float(a), float(b)
        return abs(fa - fb) <= max(1e-6, 1e-9 * max(abs(fa), abs(fb)))
    except (TypeError, ValueError):
        return str(a) == str(b)


def statements_of(template: dict) -> list[str]:
    """The names of every statement a template carries that the tabular stage or crawl can run —
    what "fully probed" is measured against."""
    names = [k for k in (*REQUIRED, *OPTIONAL_SQL) if isinstance(template.get(k), str)
             and k not in ("table_stats_column", "table_stats_range")]
    names += [f"keys_alternatives[{i}]" for i, a in enumerate(template.get("keys_alternatives")
                                                              or [])
              if isinstance(a, dict) and a.get("sql")]
    return names


def read_only_session(sql: SqlTool, tpl: dict) -> dict:
    """Switch the probe's session to READ ONLY when the template declares how, and CHECK it took
    (R-022 f1 #4). Returns {enforced_by, detail}: `database session` only when the check query
    answered the expected value; otherwise the statement allow-list is the only guard — said."""
    ros = tpl.get("read_only_session") or {}
    if not ros:
        return {"enforced_by": "statement allow-list only",
                "detail": "the template declares no read-only session statement"}
    if not _SESSION_RX.match(str(ros.get("sql") or "")):
        return {"enforced_by": "statement allow-list only",
                "detail": "read_only_session.sql is not a read-only SET/PRAGMA; not run"}
    # INT5: the CHECK is the proof; the statement is only the means. A server that already runs
    # every statement read-only (postgres-mcp `--access-mode=restricted` wraps each one in
    # `BEGIN TRANSACTION READ ONLY` and its validator refuses any SET) rejects the statement, yet
    # the database itself answers the check `on`. So a refused statement no longer ends the
    # attempt: the check still runs, and only ITS answer, from the database, can say "database
    # session". A server that refuses the statement and is NOT read-only still answers `off` and
    # stays "statement allow-list only" — nothing about the check was relaxed.
    ok, res, _ = sql.run(str(ros["sql"]), tpl)
    set_error = None if ok else str(res)[:200]
    ok, rows, _ = sql.run(str(ros.get("check") or ""), tpl)
    val = str(next(iter(rows[0].values()))) if ok and rows else ""
    if ok and val and re.search(str(ros.get("expect")), val):
        if set_error is None:
            return {"enforced_by": "database session",
                    "detail": f"{ros['sql']!r} took effect ({ros['check']!r} -> {val!r})"}
        return {"enforced_by": "database session",
                "detail": f"the server refused {ros['sql']!r} ({set_error}) but already runs "
                          f"every statement read-only: {ros['check']!r} -> {val!r}"}
    if set_error is not None:
        return {"enforced_by": "statement allow-list only",
                "detail": f"the session statement failed: {set_error}; the session check "
                          f"answered {val or rows!r}, not {ros.get('expect')!r}"}
    return {"enforced_by": "statement allow-list only",
            "detail": f"the session check answered {val or rows!r}, not {ros.get('expect')!r}"}


def probe_template(sql: SqlTool, template: dict, *, max_tables: int = 25,
                   schemas: Optional[list[str]] = None, origin: str = "rules"
                   ) -> tuple[dict, dict]:
    """Run every catalogue query of `template` read-only through `sql`. Returns (report, the
    template as probed — its `keys` swapped for an alternative when that one sees more keys).

    R-022 f1: every statement is re-checked against the read-only rules (keywords + function
    allow-list) immediately before it is sent; the session is switched to read-only first where
    the template says how; single-row queries must answer one row; `report["unprobed"]` names
    every statement that never ran (an approval requires none)."""
    if int(max_tables) < 1:
        raise DialectError("max_tables must be at least 1: a probe that reads no table proves "
                           "nothing")
    tpl = dict(template)
    policy = load_policy(dialect=tpl)
    allowed = allowed_functions(tpl, origin=origin)
    q: dict[str, dict] = {}
    notes: list[str] = []
    formats: set[str] = set()
    ro = read_only_session(sql, tpl)

    def send(key: str, stmt: str, *, fragment_ok: bool = False) -> tuple[bool, Any]:
        bad = check_statement(key, stmt, allowed)
        if bad:
            return False, f"refused before running: {'; '.join(bad)}"
        ok, rows, fmt = sql.run(stmt, tpl)
        if fmt:
            formats.add(fmt)
        return ok, rows

    def run(name: str, ctx: dict, *, record: Optional[str] = None) -> Optional[list[dict]]:
        key = record or name
        text = tpl.get(name) if not name.startswith("keys_alternatives") else None
        if name.startswith("keys_alternatives["):
            i = int(name[len("keys_alternatives["):-1])
            text = (tpl.get("keys_alternatives") or [])[i].get("sql")
        if not text:
            q.setdefault(key, {"ok": False, "error": "no template"})
            return None
        try:
            stmt = render_sql(text, ctx, tpl)
        except (TemplateError, ValueError, KeyError) as exc:
            q[key] = {"ok": False, "error": f"does not render: {exc}"}
            return None
        ok, rows = send(key, stmt)
        entry = q.setdefault(key, {"ok": True, "calls": 0, "rows": 0})
        entry["calls"] = entry.get("calls", 0) + 1
        if not ok:
            entry.update(ok=False, error=str(rows)[:300])
            return None
        rows = _lower(rows)
        bad = _wellformed("keys" if name.startswith("keys_alt") else name, rows)
        if bad:
            entry.update(ok=False, error=bad)
            return None
        entry["rows"] = entry.get("rows", 0) + len(rows)
        return rows

    tables_rows = run("list_tables", {}) or []
    all_tables = [(str(r.get("table_schema") or ""), str(r.get("table_name")))
                  for r in tables_rows if r.get("table_name")]
    if schemas:
        all_tables = [t for t in all_tables if t[0] in set(schemas)]
    probed = all_tables[:max_tables]
    if len(all_tables) > len(probed):
        notes.append(f"probed the first {len(probed)} of {len(all_tables)} tables")
    variants = ["keys"] + [f"keys_alternatives[{i}]"
                           for i in range(len(tpl.get("keys_alternatives") or []))]
    per_table: list[dict] = []
    key_counts = {v: {"primary_key_columns": 0, "foreign_keys": 0, "tables_with_pk": 0,
                      "ok": True} for v in variants}
    first_temporal = None
    first_cols: list[dict] = []
    table_cols: list[tuple[dict, list[dict]]] = []
    for schema, table in probed:
        ctx = {"schema": schema, "table": table}
        cols = run("describe", ctx) or []
        entry = {"table": f"{schema}.{table}" if schema else table, "columns": len(cols),
                 "keys": {}}
        for v in variants:
            rows = run(v, ctx, record=v)
            if rows is None:
                key_counts[v]["ok"] = False
                continue
            pk = [r["column_name"] for r in rows
                  if str(r.get("constraint_type")).upper() == "PRIMARY KEY"]
            fk = [{"column": r["column_name"], "ref_table": r.get("ref_table"),
                   "ref_column": r.get("ref_column")} for r in rows
                  if str(r.get("constraint_type")).upper() == "FOREIGN KEY"]
            key_counts[v]["primary_key_columns"] += len(pk)
            key_counts[v]["foreign_keys"] += len(fk)
            key_counts[v]["tables_with_pk"] += bool(pk)
            entry["keys"][v] = {"primary_key": pk, "foreign_keys": fk}
        per_table.append(entry)
        table_cols.append((ctx, list(cols)))
        if len(per_table) == 1:
            first_cols = list(cols)
        if first_temporal is None:
            for c in cols:
                if type_class(c.get("data_type"), policy) == "temporal":
                    first_temporal = (ctx, str(c.get("column_name")))
                    break
    # Cheap per-table queries on the first table (enough to prove each renders and runs).
    if probed:
        ctx = {"schema": probed[0][0], "table": probed[0][1]}
        names = [str(c.get("column_name")) for c in first_cols]
        counted = run("count", ctx)
        run("row_estimate", ctx)
        if names:
            first = names[0]
            ordq = dialect_quote(tpl, first)
            run("select_page", {**ctx, "order_by": ordq, "limit": 1, "offset": 0})
            run("select", {**ctx, "order_by": ordq, "limit": 1})
            run("aggregate", {**ctx, "group_cols": ordq, "measures": ""})
        # R-022 f1 #15 / f2 #17: one representative column of EVERY type class found across ALL
        # probed tables (not only the first), each measured by the per-column reference
        # statements in its own table …
        seen_cls: set[str] = set()
        groups: list[tuple[dict, list[tuple[str, str]]]] = []
        for tctx, tcols in table_cols:
            reps: list[tuple[str, str]] = []
            for c in tcols:
                cls = type_class(c.get("data_type"), policy)
                if cls not in seen_cls:
                    seen_cls.add(cls)
                    reps.append((str(c.get("column_name")), cls))
            if reps:
                groups.append((tctx, reps))
        checks: list[tuple[dict, list[tuple[str, str]], dict, dict]] = []
        for tctx, reps in groups:
            ref_stats: dict[str, Optional[dict]] = {}
            ref_range: dict[str, Optional[dict]] = {}
            for col, cls in reps:
                got = run("column_stats", {**tctx, "column": col})
                ref_stats[col] = got[0] if got else None
                if cls in ("temporal", "numeric"):
                    got = run("column_range", {**tctx, "column": col})
                    ref_range[col] = got[0] if got else None
            checks.append((tctx, reps, ref_stats, ref_range))
        # … and the combined statement must AGREE with them on every statistic it emits, run
        # once per table that contributed a type class.
        if checks and tpl.get("table_stats") and tpl.get("table_stats_column"):
            calls, compared, verdict = 0, [], None
            for tctx, reps, ref_stats, ref_range in checks:
                cnt = counted if tctx == ctx else run("count", tctx)
                res = _probe_combined(send, tpl, tctx, reps, ref_stats, ref_range, cnt)
                calls += int(res.get("calls") or 0)
                if not res.get("ok"):
                    verdict = {**res, "table": _ref_of(tctx)}
                    break
                compared += [f"{_ref_of(tctx)}.{c}" for c in res.get("columns_compared") or []]
            q["table_stats"] = ({**verdict, "calls": calls} if verdict is not None else
                                {"ok": True, "calls": calls, "rows": calls,
                                 "columns_compared": compared,
                                 "type_classes": sorted(seen_cls)})
    if first_temporal is None and probed and first_cols and "column_range" not in q:
        # no temporal (or numeric) column in the probed tables: the range statement still has to
        # run once to count as probed — on the first column (MIN/MAX of any type)
        notes.append("no temporal column in the probed tables: the date-range probe "
                     "(column_range) ran on the first column instead")
        run("column_range", {"schema": probed[0][0], "table": probed[0][1],
                             "column": str(first_cols[0].get("column_name"))})
    elif first_temporal and "column_range" not in q:
        run("column_range", {**first_temporal[0], "column": first_temporal[1]})
    # Keys: keep the catalogue that SEES the most (the read-only-role lesson).
    def seen(v: str) -> int:
        k = key_counts[v]
        return k["primary_key_columns"] + k["foreign_keys"] if k["ok"] else -1

    best = max(variants, key=lambda v: (seen(v), v == "keys"))
    keys_used = "keys"
    if best != "keys" and seen(best) > max(seen("keys"), 0):
        i = int(best[len("keys_alternatives["):-1])
        alt = (tpl.get("keys_alternatives") or [])[i]
        old = {"name": "primary", "note": "the template's original keys catalogue",
               "sql": tpl["keys"]}
        notes.append(
            f"KEYS HIDDEN: the template's keys catalogue saw {max(seen('keys'), 0)} key "
            f"column(s){'' if key_counts['keys']['ok'] else ' (it failed)'}; the alternative "
            f"{alt.get('name')!r} saw {seen(best)}. This role cannot read keys through the first "
            f"catalogue — the draft now uses {alt.get('name')!r} ({alt.get('note') or ''}).")
        tpl["keys"] = alt["sql"]
        tpl["keys_alternatives"] = [old] + [a for j, a in enumerate(
            tpl.get("keys_alternatives") or []) if j != i]
        keys_used = f"alternative {alt.get('name')!r}"
    elif probed and any(0 <= seen(v) < seen("keys") for v in variants):
        for v in variants:
            if 0 <= seen(v) < seen("keys"):
                alt = (tpl.get("keys_alternatives") or [])[int(v[len("keys_alternatives["):-1])]
                notes.append(f"the alternative keys catalogue {alt.get('name')!r} saw only "
                             f"{seen(v)} key column(s) against {seen('keys')}: it hides keys "
                             f"from this role, which is why the template does not use it "
                             f"({alt.get('note') or 'no note'})")
    elif probed and seen("keys") == 0 and max(seen(v) for v in variants) <= 0:
        notes.append("NO KEYS VISIBLE through any catalogue the template carries: either the "
                     "schema declares none, or this role cannot see them — no foreign-key edge "
                     "will be built. Check the role's catalogue privileges.")
    for v in variants:
        if v in q and not key_counts[v]["ok"]:
            q[v]["ok"] = False
    chosen = key_counts[best if keys_used != "keys" else "keys"]
    for t in per_table:
        t["primary_key"] = (t["keys"].get(best if keys_used != "keys" else "keys") or {}) \
            .get("primary_key", [])
        t["foreign_keys"] = (t["keys"].get(best if keys_used != "keys" else "keys") or {}) \
            .get("foreign_keys", [])
    n_cols = sum(t["columns"] for t in per_table)
    failed = sorted(k for k, v in q.items() if not v.get("ok"))
    # which statements of the ORIGINAL template never ran (the keys swap renames, not adds)
    unprobed = sorted(k for k in statements_of(template)
                      if not (q.get(k) or {}).get("calls"))
    summary = (f"found {len(all_tables)} tables, {n_cols} columns, "
               f"{chosen['foreign_keys']} key relationships "
               f"({chosen['tables_with_pk']} tables with a primary key)"
               + (f" across the {len(probed)} probed" if len(probed) < len(all_tables) else "")
               + (f"; FAILED: {', '.join(failed)}" if failed else "; every catalogue query ran"))
    report = {"summary": summary, "tables": len(all_tables), "tables_probed": len(probed),
              "columns": n_cols, "key_relationships": chosen["foreign_keys"],
              "tables_with_primary_key": chosen["tables_with_pk"], "keys_catalogue": keys_used,
              "keys_by_catalogue": key_counts, "queries": q, "failed": failed,
              "unprobed": unprobed, "read_only": ro,
              "rows_format": sorted(formats), "per_table": per_table, "notes": notes}
    return report, tpl


def _ref_of(ctx: dict) -> str:
    return f"{ctx.get('schema')}.{ctx.get('table')}" if ctx.get("schema") else str(ctx["table"])


def _probe_combined(send, tpl: dict, ctx: dict, reps: list[tuple[str, str]],
                    ref_stats: dict, ref_range: dict, counted: Optional[list[dict]]) -> dict:
    """Run the combined `table_stats` statement over the representative columns and compare
    EVERY statistic it emits with the per-column reference answers (R-022 f1 #15)."""
    try:
        parts = []
        for j, (col, cls) in enumerate(reps):
            cctx = {**ctx, "column": col, "p": f"c{j}"}
            parts.append(render_sql(tpl["table_stats_column"], cctx, tpl))
            if cls in ("temporal", "numeric") and tpl.get("table_stats_range"):
                parts.append(render_sql(tpl["table_stats_range"], cctx, tpl))
        stmt = render_sql(tpl["table_stats"], {**ctx, "measures": "".join(
            ", " + p.strip() for p in parts)}, tpl)
    except (TemplateError, ValueError, KeyError) as exc:
        return {"ok": False, "error": f"does not render: {exc}"}
    ok, rows = send("table_stats", stmt)
    if not ok or not rows:
        return {"ok": False, "calls": 1, "error": str(rows)[:300] if not ok else "no row"}
    ts = _lower(rows)[0]
    if counted and not _same(ts.get("n"), counted[0].get("n")):
        return {"ok": False, "calls": 1, "error": "combined statistics disagree with the "
                                                  "per-column statement (row count n)"}
    for j, (col, cls) in enumerate(reps):
        ref = ref_stats.get(col)
        if ref is None:
            return {"ok": False, "calls": 1, "error": f"no per-column reference for {col!r}"}
        for k in _STATS_COMPARED:
            if f"c{j}_{k}" in ts and not _same(ts.get(f"c{j}_{k}"), ref.get(k)):
                return {"ok": False, "calls": 1, "error": (
                    f"combined statistics disagree with the per-column statement ({col!r} {k}: "
                    f"{ts.get(f'c{j}_{k}')!r} vs {ref.get(k)!r})")}
        if f"c{j}_non_null" not in ts or f"c{j}_distinct_n" not in ts:
            return {"ok": False, "calls": 1, "error": f"combined row lacks c{j}_non_null / "
                                                      f"c{j}_distinct_n"}
        rng = ref_range.get(col)
        if rng is not None and tpl.get("table_stats_range"):
            for k in ("lo", "hi"):
                if not _same(ts.get(f"c{j}_{k}"), rng.get(k)):
                    return {"ok": False, "calls": 1, "error": (
                        f"combined statistics disagree with the per-column statement ({col!r} "
                        f"{k}: {ts.get(f'c{j}_{k}')!r} vs {rng.get(k)!r})")}
    return {"ok": True, "calls": 1, "rows": 1, "columns_compared": [c for c, _ in reps]}


#: A probe failure of one of these blocks approval; the tabular stage cannot run without them.
#: R-022 f2 #11: EVERY required statement (`select`, `aggregate` and `row_estimate` included —
#: drift probing, facts aggregation and sizing read them). `keys` is judged separately: it may be
#: replaced by an alternative catalogue that ran. Warnings are left for OPTIONAL_SQL only.
BLOCKING = tuple(k for k in REQUIRED if k != "keys")


def _failed_required(probe: dict) -> list[str]:
    """The required statements a probe report says failed (`keys` only when it was kept)."""
    failed = set(probe.get("failed") or [])
    out = [k for k in BLOCKING if k in failed]
    if "keys" in failed and probe.get("keys_catalogue", "keys") == "keys":
        out.append("keys")
    return out


# --- proposal / approval --------------------------------------------------------------------------
class DialectProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    status: str = "proposed"            # proposed | approved
    origin: str = "rules"               # rules | model
    engine: Optional[str] = None
    template: dict = Field(default_factory=dict)
    detection: dict = Field(default_factory=dict)
    probe: dict = Field(default_factory=dict)
    problems: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sql_tool: Optional[str] = None
    sql_arg: Optional[str] = None
    created_at: str = ""
    approved_at: Optional[str] = None
    approved_by: Optional[str] = None
    #: {principal_id, authenticated_by, principal_type, dev_only, at, template_sha256} (R-022 f1)
    approval: Optional[dict] = None


def dialect_path(source_id: str, root: Optional[Path] = None) -> Path:
    from ..connections import connection_path

    return connection_path(source_id, root).with_name(f"{source_id}.dialect.yaml")


def save_proposal(p: DialectProposal, root: Optional[Path] = None) -> Path:
    path = dialect_path(p.source_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(p.model_dump(mode="json"), sort_keys=False,
                                   allow_unicode=True), encoding="utf-8")
    return path


def load_proposal(source_id: str, root: Optional[Path] = None) -> Optional[DialectProposal]:
    path = dialect_path(source_id, root)
    if not path.is_file():
        return None
    return DialectProposal.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def approval_log_path(source_id: str, root: Optional[Path] = None) -> Path:
    return dialect_path(source_id, root).with_name(f"{source_id}.dialect.approvals.jsonl")


def _record_digest(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k != "record_digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     default=str).encode("utf-8")).hexdigest()


def _logged_digests(source_id: str, root: Optional[Path]) -> set[str]:
    p = approval_log_path(source_id, root)
    out: set[str] = set()
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("record_digest") == _record_digest(rec) \
                and rec.get("source_id") == source_id \
                and str(rec.get("principal_id") or "").strip() \
                and str(rec.get("authenticated_by") or "").strip():
            out.add(str(rec.get("template_sha256")))
    return out


def approval_problems(p: DialectProposal) -> list[str]:
    """Why this proposal cannot be APPROVED as it stands on disk (R-022 f1 #4, #13): recorded
    problems; a template that no longer validates (keywords, function allow-list, shape); a
    template that is not byte-for-byte the one the live probe ran (edited after probing); a probe
    that read no table or left a statement unrun."""
    out = list(p.problems)
    out += [f"template: {x}" for x in validate_template(p.template, origin=p.origin)]
    sha = template_digest(p.template)
    probe = p.probe or {}
    if not probe:
        out.append("never live-probed")
    elif probe.get("template_sha256") != sha:
        out.append("the template was changed after the live probe (its digest does not match "
                   "the probed one): re-run draft_dialect")
    if probe and int(probe.get("tables_probed") or 0) < 1:
        out.append("the probe read no table: nothing proves the per-table statements run")
    if probe and probe.get("unprobed"):
        out.append(f"statements never run by the probe: {probe['unprobed']}")
    # R-022 f2 #11: a required statement that failed is never approvable, whatever `problems` says
    bad = _failed_required(probe) if probe and not probe.get("skipped") else []
    if bad:
        out.append(f"required statements failed the probe: {bad}")
    # R-022 f2 #3: a SELECT plus a function allow-list does not prove the probe cannot write (an
    # overloaded or user-defined function). Approval needs the DATABASE to have enforced read-only
    # for the probe's session, verified by the template's own check.
    ro = _read_only_problem(p.template, probe, p.origin)
    if ro:
        out.append(ro)
    return out


def _vetted_sessions() -> list[dict]:
    """The `read_only_session` blocks the SHIPPED (bundled, human-reviewed) templates declare."""
    from .. import paths

    bundled = paths.bundled_profiles_dir()
    out = []
    for t in load_templates():
        try:
            Path(t.get("_path") or "").resolve().relative_to(bundled.resolve())
        except ValueError:
            continue
        if isinstance(t.get("read_only_session"), dict):
            out.append({k: t["read_only_session"].get(k) for k in ("sql", "check", "expect")})
    return out


def _read_only_problem(template: dict, probe: dict, origin: str) -> Optional[str]:
    """Why the probe's read-only guarantee is not the DATABASE's (R-022 f2 #3), or None.

    * the template must declare `read_only_session` (a SET/PRAGMA plus a check of its effect);
    * a MODEL-drafted template may only carry one a shipped template declares, verbatim — a model
      could otherwise pair a harmless statement with a check that always answers the expectation;
    * the probe must report that the check answered the expected value (`database session`)."""
    ros = (template or {}).get("read_only_session")
    if not isinstance(ros, dict) or not ros.get("sql"):
        return ("the template declares no read_only_session: nothing makes the DATABASE refuse "
                "writes during the probe (a SELECT can call a side-effecting function); this "
                "engine's dialect cannot be approved from a draft")
    if origin == "model" and {k: ros.get(k) for k in ("sql", "check", "expect")} \
            not in _vetted_sessions():
        return ("a model-drafted read_only_session is not one a shipped template declares: its "
                "check is not trusted to prove the session is read-only")
    enforced = ((probe or {}).get("read_only") or {}).get("enforced_by")
    if enforced != "database session":
        return (f"the probe's session was not verified read-only by the database "
                f"(enforced_by: {enforced!r}; "
                f"{((probe or {}).get('read_only') or {}).get('detail')})")
    return None


def approved_dialect(source_id: str, root: Optional[Path] = None) -> Optional[dict]:
    """The APPROVED probed template for this connection, or None. Unapproved drafts are ignored,
    and so is an approval that does not verify at RUN time (R-022 f1 #4): no authenticated
    approval record, a template that no longer hashes to the approved digest (edited since), a
    digest the append-only approvals log does not hold, or a template that no longer validates."""
    p = load_proposal(source_id, root)
    if p is None or p.status != "approved":
        return None
    a = p.approval or {}
    sha = template_digest(p.template)
    if not str(a.get("principal_id") or "").strip() or \
            not str(a.get("authenticated_by") or "").strip():
        return None
    if a.get("template_sha256") != sha or sha not in _logged_digests(source_id, root):
        return None
    if validate_template(p.template, origin=p.origin):
        return None
    if _read_only_problem(p.template, p.probe, p.origin):
        return None                                        # R-022 f2 #3, at run time too
    return {k: v for k, v in p.template.items() if not k.startswith("_")}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def approve_dialect(source_id: str, *, approved_by: Any = None, principal: Any = None,
                    root: Optional[Path] = None) -> DialectProposal:
    """An AUTHENTICATED principal approves the probed draft (R-022 f1 #1): `principal` (or a
    principal object passed as `approved_by`) must pass `plans.require_authenticated_approver` —
    a typed name is refused. Refused too when `approval_problems` finds anything: problems, a
    template edited after the probe or failing the read-only checks, or a probe that was not
    complete. The approval records principal, time and the template digest, and is appended to
    `<source>.dialect.approvals.jsonl` (append-only)."""
    from ..plans import ApprovalRefused, require_authenticated_approver

    try:
        who = require_authenticated_approver(principal if principal is not None else approved_by,
                                             what="dialect approval")
    except ApprovalRefused as exc:
        raise DialectError(str(exc)) from None
    p = load_proposal(source_id, root)
    if p is None:
        raise DialectError(f"no dialect draft for {source_id!r}; run draft_dialect first")
    problems = approval_problems(p)
    if problems:
        raise DialectError(f"the dialect draft for {source_id!r} has problems and cannot be "
                           f"approved: {problems}")
    at = _now()
    record = {**who, "at": at, "template_sha256": template_digest(p.template)}
    rec = {"action": "approve", "source_id": source_id, **record}
    rec["record_digest"] = _record_digest(rec)
    log = approval_log_path(source_id, root)
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as fh:                # append-only: never rewritten
        fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
    p = p.model_copy(update={"status": "approved", "approved_at": at,
                             "approved_by": who["principal_id"], "approval": record})
    p.warnings.extend(_point_plan_at(p, root))
    save_proposal(p, root)
    return p


def _point_plan_at(p: DialectProposal, root: Optional[Path]) -> list[str]:
    """The tabular stage reads the approved draft directly. The core crawl (graph rows) resolves
    a dialect by NAME, so when the approved template is exactly a bundled one, the plan's tabular
    step is pointed at that name; otherwise say what still uses the plan's dialect."""
    from ..plans import PlanError, load_plan, save_plan, sql_dialect

    try:
        plan = load_plan(p.source_id, root)
    except PlanError:
        return []
    name = str(p.template.get("name") or "")
    try:
        bundled = {k: v for k, v in sql_dialect(name).items() if not k.startswith("_")}
    except PlanError:
        bundled = None
    mine = {k: v for k, v in p.template.items() if not k.startswith("_")}
    steps, changed = [], False
    for s in plan.steps:
        if s.strategy == "tabular" and s.sql is not None and s.sql.dialect != name \
                and bundled == mine:
            s = s.model_copy(update={"sql": s.sql.model_copy(update={"dialect": name})})
            changed = True
        steps.append(s)
    if changed:
        save_plan(plan.model_copy(update={
            "steps": steps, "notes": plan.notes + [
                f"tabular dialect set to {name!r} by the approved dialect draft "
                f"({p.approved_by}, {p.approved_at})"]}), root)
        return [f"the plan's tabular step now names dialect {name!r}"]
    if bundled != mine and any(s.strategy == "tabular" for s in plan.steps):
        return ["the approved template differs from any bundled dialect: the tabular stage uses "
                "it; the core graph crawl still renders the plan's named dialect — publish the "
                "template under profiles/ (WALNUT_CRAWL_PROFILES_DIR) to use it there too"]
    return []


def draft_dialect(source_id: str, *, client=None, harness=None, root: Optional[Path] = None,
                  sql_tool: Optional[str] = None, sql_arg: Optional[str] = None,
                  templates: Optional[list[dict]] = None, max_tables: int = 25,
                  schemas: Optional[list[str]] = None, save: bool = True,
                  progress: Optional[Callable[[dict], None]] = None) -> DialectProposal:
    """Detect → pick (or model-draft) → live-probe → save PROPOSED. See the module docstring."""
    from ..client import ReadOnlyClient, open_client
    from ..connections import load_connection
    from ..plans import PlanError, load_plan

    conn = load_connection(source_id, root)
    if getattr(conn, "purpose", "ingest") != "ingest":
        raise DialectError(f"connection {source_id!r} is purpose={conn.purpose!r}; a dialect is "
                           f"drafted only over a read-only ingest connection")
    known = templates if templates is not None else load_templates()
    emit = progress or (lambda _e: None)
    try:
        plan = load_plan(source_id, root)
    except PlanError:
        plan = None
    error_prefixes = sorted({p for t in known for p in ((t.get("mcp") or {})
                                                         .get("error_prefixes") or [])})

    def _with(c) -> DialectProposal:
        ro = ReadOnlyClient(c, plan=plan)
        tools = ro.list_tools()
        found = (sql_tool, sql_arg or "sql") if sql_tool else find_sql_tool(tools, known,
                                                                             plan=plan)
        if not found:
            raise DialectError(f"no read-only SQL tool on {source_id!r} "
                               f"(tools: {[t.name for t in tools]})")
        tool_name, arg = found
        extra = {}
        if plan is not None:
            for s in plan.steps:
                if s.sql is not None and s.sql.tool == tool_name:
                    extra = dict(s.sql.extra_args)
        sql = SqlTool(ro, tool_name, arg, extra_args=extra, error_prefixes=error_prefixes)
        t0 = time.monotonic()
        det = detect_engine(sql, {t.name for t in tools}, known)
        emit({"stage": "detect", "engine": det["engine"], "method": det["method"],
              "seconds": round(time.monotonic() - t0, 3)})
        problems: list[str] = []
        warnings: list[str] = []
        origin = "rules"
        if det["template"] is not None:
            template = {k: v for k, v in det["template"].items() if not k.startswith("_")}
            if template.get("status") != "verified":
                warnings.append(f"template {template.get('name')!r} is "
                                f"{template.get('status')}: "
                                f"{template.get('unverified') or template.get('verified')}")
        elif harness is not None:
            origin = "model"
            tinfo = next((t for t in tools if t.name == tool_name), None)
            template = model_draft(harness, tool=tinfo, detection=det)
            warnings.append("model-drafted template: a human must read every statement before "
                            "approving it")
            if save:
                # P16: the model's output reaches disk BEFORE the fragile live probe; marked
                # unprobed, so it cannot be approved until the probe has run.
                save_proposal(DialectProposal(
                    source_id=source_id, origin="model", engine=template.get("engine"),
                    template=template, detection={k: v for k, v in det.items()
                                                  if k != "template"},
                    problems=["not yet live-probed"], warnings=list(warnings),
                    sql_tool=tool_name, sql_arg=arg, created_at=_now()), root)
        else:
            raise DialectError(f"no dialect template matches {source_id!r} "
                               f"({'; '.join(det['evidence'])}) and no model harness was given "
                               f"to draft one")
        invalid = [f"template: {p}" for p in validate_template(template, origin=origin)]
        problems += invalid
        if invalid:
            # R-022 f1 #4: a template that fails the read-only checks (a write keyword, a function
            # off the allow-list, a malformed statement) is NEVER live-probed — a model-drafted
            # statement reaches the source only after it passed them.
            prop = DialectProposal(
                source_id=source_id, origin=origin, engine=template.get("engine"),
                template=template, detection={k: v for k, v in det.items() if k != "template"},
                probe={"summary": "NOT PROBED: the template failed the read-only checks",
                       "skipped": True, "tables": 0, "tables_probed": 0, "failed": [],
                       "unprobed": statements_of(template), "queries": {},
                       "statements": len(sql.log)},
                problems=[*problems, "not live-probed: fix the template first"],
                warnings=warnings, sql_tool=tool_name, sql_arg=arg, created_at=_now())
            return prop
        t0 = time.monotonic()
        report, probed = probe_template(sql, template, max_tables=max_tables, schemas=schemas,
                                        origin=origin)
        report["seconds"] = round(time.monotonic() - t0, 3)
        report["statements"] = len(sql.log)
        emit({"stage": "probe", "summary": report["summary"], "seconds": report["seconds"]})
        if origin == "model":
            # The model's own version probe must identify the engine it drafted for.
            pr = probed["detect"]["probes"][0]
            ok, rows, _ = sql.run(pr["sql"], probed)
            val = str(next(iter(rows[0].values()))) if ok and rows else ""
            if not (ok and val and re.search(pr["match"], val)):
                problems.append(f"the drafted version probe {pr['sql']!r} did not match its own "
                                f"regex {pr['match']!r} (got {val or rows!r})")
        for name in BLOCKING:
            if name in report["failed"]:
                problems.append(f"probe: {name} failed — {report['queries'][name].get('error')}")
        if not report["tables"]:
            problems.append("probe: list_tables found no table")
        if "keys" in report["failed"] and report["keys_catalogue"] == "keys":
            problems.append(f"probe: keys failed — {report['queries']['keys'].get('error')}")
        if "table_stats" in report["failed"]:
            warnings.append("combined profiling (table_stats) failed the probe and was removed; "
                            "profiling will use one statement per column")
            for k in OPTIONAL_SQL:
                probed.pop(k, None)
        if not report.get("tables_probed"):
            problems.append("probe: no table was probed (nothing proves the per-table "
                            "statements run)")
        if report.get("unprobed"):
            problems.append(f"probe: statements never ran: {report['unprobed']}")
        ro_problem = _read_only_problem(probed, report, origin)
        if ro_problem:
            # R-022 f2 #3: reported here, enforced by `approval_problems` (the probe itself ran
            # clean; what is missing is the database's own guarantee)
            warnings.append(f"NOT APPROVABLE: {ro_problem}")
        warnings += report["notes"]
        # the approval is bound to EXACTLY this template (R-022 f1 #4)
        report["template_sha256"] = template_digest(probed)
        prop = DialectProposal(
            source_id=source_id, origin=origin, engine=probed.get("engine"), template=probed,
            detection={k: v for k, v in det.items() if k != "template"}, probe=report,
            problems=problems, warnings=warnings, sql_tool=tool_name, sql_arg=arg,
            created_at=_now())
        return prop

    if client is not None:
        prop = _with(client)
    else:
        with open_client(conn) as c:
            prop = _with(c)
    if conn.auto_approve:
        # Codex round 3 (Critical): a connection flag is not a person. The flag is read, ignored
        # and reported; a dialect is approved only by `approve_dialect(approved_by=<principal>)`.
        prop = prop.model_copy(update={"warnings": [*prop.warnings, (
            "auto_approve on the connection was ignored: a dialect is approved only by a named "
            "person")]})
    if save:
        save_proposal(prop, root)
    return prop


__all__ = ["draft_dialect", "approve_dialect", "approved_dialect", "load_proposal",
           "approval_problems", "template_digest", "check_statement", "called_functions",
           "READ_FUNCTIONS", "vetted_read_functions", "allowed_functions", "statements_of",
           "read_only_session", "approval_log_path",
           "dialect_path", "DialectProposal", "DialectError", "load_templates",
           "validate_template", "detect_engine", "probe_template", "model_draft", "SqlTool",
           "find_sql_tool", "DraftedDialect", "RESULT_COLUMNS", "REQUIRED", "DRAFT_TASK_CLASS"]

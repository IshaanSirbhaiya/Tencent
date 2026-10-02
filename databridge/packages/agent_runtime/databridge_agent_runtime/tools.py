"""The tool layer: what an agent may call, admitted call by call against its compiled grants.

Every call goes through `ToolLayer.call`, which decides in this order and records the decision:

1. the name parses as a §5.6 tool                                       else ``unknown_tool``
2. it is not a write — writes exist only as declared ACTIONS            else ``write_is_an_action``
3. the workflow lists it (behaviour half)                               else ``not_in_workflow``
4. the compiled grant set holds its capability (governance half —
   `GrantedAuthority.require`; there is no other path to a grant)       else ``no_grant``
5. a ``source.query`` read is a single read-only statement              else ``sql_not_read_only``

A refused call is never executed, never retried on the model's behalf, and is surfaced as reduced
coverage (P12). A permitted call returns observations whose every value carries a pointer; a live
source result is captured first (§5.2) so its pointer has a ``capture_digest``.

Subject kinds (the pack's ``subject_kinds``) come back marked ``candidate`` with
``requires_human_selection: true``: the agent can find a person, it cannot decide who they are
(CLAUDE.md hard constraint 1).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence, runtime_checkable

from databridge_agentdef import AmbientAuthorityRefused, GrantedAuthority
from databridge_core import SourcePointer
from databridge_workflow_spec import SpecError, WorkflowSpec, parse_tool

from .capture import capture_live_read, document_identity
from .classify import classify_tool as _classify_one
from .classify import plan_read_tools
from .shape import describe_schema, shape_args
from .subtask_shape import delegate_holdings, describe_subtask, shape_subtask, summarise_error
from .mapping import (
    DELEGATE_SOURCE,
    FACTS_SOURCE,
    GRAPH_SOURCE,
    READ_ANY_CAPABILITY,
    RETRIEVE_SOURCE,
    ConnectionAdvert,
    MappingError,
    ToolAdvert as _ToolAdvert,
    capability_for_tool,
    source_id_for,
)

#: How much of one result the model sees. The capture holds all of it; truncation is SAID.
OBSERVATION_CHARS = 4000
#: How many rows of a facts / delegate result the planner is shown. Rows beyond it are counted,
#: never registered: a ref exists only for what the planner saw (R-021 #13).
SHOWN_ROWS = 200


# ================================================================================================
# protocols — injectable, so tests and batch 2b can swap the real pieces in
# ================================================================================================
@dataclass(frozen=True)
class GatewayResult:
    """One live source result, transport-free."""

    text: str = ""
    data: Optional[bytes] = None
    mime: Optional[str] = None
    uri: Optional[str] = None
    is_error: bool = False
    structured: Any = None


@runtime_checkable
class SourceGateway(Protocol):
    """Live MCP sources. `McpSourceGateway` is the real one; tests inject fakes."""

    def advertise(self, connection: str) -> ConnectionAdvert: ...
    def call_tool(self, connection: str, tool: str, arguments: dict) -> GatewayResult: ...
    def query(self, connection: str, sql: str) -> GatewayResult: ...


@runtime_checkable
class Retriever(Protocol):
    """§5.5: ``search(query, *, k, sources, kinds, mode) -> list[Passage]``."""

    def search(self, query: str, *, k: int = 8, sources: Optional[Sequence[str]] = None,
               kinds: Optional[Sequence[str]] = None, mode: str = "hybrid") -> list: ...


@runtime_checkable
class FactsStore(Protocol):
    """A connection's read-only facts store (S2): exact counting / listing by SQL.

    ``query`` returns an object shaped like `databridge_mcp_ingest.tabular.count.CountAnswer`
    (``columns``, ``rows``, ``row_count``, ``truncated``, ``extract_date``, ``citation``,
    ``pointers`` — one ``query_cell`` pointer per returned cell, row-major)."""

    def available(self, connection: str) -> bool: ...
    def query(self, connection: str, sql: str, *, params: Any = None,
              question: Optional[str] = None) -> Any: ...


class McpFactsStore:
    """`FactsStore` over `databridge_mcp_ingest.tabular.count.count_query` (opened read-only,
    ``PRAGMA query_only``, SQLite authorizer — and this runtime's own read-only check first)."""

    def __init__(self) -> None:
        from databridge_mcp_ingest.tabular import count as _count   # noqa: PLC0415
        from databridge_mcp_ingest.tabular import facts as _facts   # noqa: PLC0415
        self._count = _count
        self._facts = _facts

    def available(self, connection: str) -> bool:
        return self._facts.facts_path(connection).is_file()

    def query(self, connection: str, sql: str, *, params: Any = None,
              question: Optional[str] = None) -> Any:
        return self._count.count_query(connection, sql, params=params, question=question)


def default_facts() -> Optional[FactsStore]:
    try:
        return McpFactsStore()
    except Exception:                                        # noqa: BLE001 - mcp_ingest absent
        return None


class RetrievalModuleRetriever:
    """Adapter over `databridge_retrieval.search` (G, §5.5). Import is lazy and optional."""

    def __init__(self, search: Optional[Callable[..., list]] = None) -> None:
        if search is None:
            import databridge_retrieval  # type: ignore  # noqa: PLC0415
            search = databridge_retrieval.search
        self._search = search

    def search(self, query: str, *, k: int = 8, sources: Optional[Sequence[str]] = None,
               kinds: Optional[Sequence[str]] = None, mode: str = "hybrid") -> list:
        return list(self._search(query, k=k, sources=list(sources) if sources else None,
                                 kinds=list(kinds) if kinds else None, mode=mode))


def default_retriever() -> Optional[Retriever]:
    try:
        return RetrievalModuleRetriever()
    except Exception:                                        # noqa: BLE001 - not built yet
        return None


def classify_tools(tools: Sequence[Any], *, approved_reads: Sequence[str] = (),
                   denied: Sequence[str] = (), write_only: bool = False
                   ) -> tuple[_ToolAdvert, ...]:
    """Read or write for each advertised MCP tool, by THE shared rule (`classify.classify_tool`,
    which the English compiler's catalogue also calls — E2E F-10). Deny by default: a tool is a
    READ only when nothing says it writes (name verb, ``destructiveHint``, ``readOnlyHint: false``,
    a declared deny list, a write-only connection) AND something vouches for it — the server's
    ``readOnlyHint: true``, a HUMAN-APPROVED crawl plan that uses it as a read step
    (``approved_reads``), or a read verb from the closed `READ_VERB` allow-list in its name. A tool
    nothing vouches for is a write: only ever a declared, human-gated ACTION."""
    vouched = set(approved_reads)
    deny = set(denied)
    out = []
    for t in tools:
        name = str(getattr(t, "name", ""))
        c = _classify_one(name, read_only=getattr(t, "read_only", None),
                          destructive=getattr(t, "destructive", None),
                          approved_read=name in vouched, denied=name in deny,
                          write_only=write_only)
        schema = getattr(t, "input_schema", None)
        out.append(_ToolAdvert(name=name, writes=c.writes,
                               description=getattr(t, "description", "") or "",
                               input_schema=dict(schema) if isinstance(schema, dict) and schema
                               else None, basis=c.basis))
    return tuple(out)


class McpSourceGateway:
    """`SourceGateway` over `databridge_mcp_ingest` (§5.4). One client per connection per gateway.

    * ``advertise`` lists tools live and classifies each as read or write with `classify_tools`:
      **deny by default** — a tool is a READ only when nothing says it writes (name verb,
      ``destructiveHint``, ``readOnlyHint: false``, a declared deny list) AND something vouches for
      it: the server's ``readOnlyHint: true``, or a HUMAN-APPROVED crawl plan that uses it as a read
      step. A tool with an innocuous name and no annotations is a write (R-017 t02 finding 1). SQL is
      available when the connection's crawl plan has a tabular step (``sql.tool`` / ``sql.arg``).
    * ``call_tool`` / ``query`` execute; the runtime, not this class, decides whether they may.

    Classification is defence in depth, not the boundary: a server can still lie with
    ``readOnlyHint: true``. The boundary is a read-only credential on the connection itself, which
    is deployment configuration (R-017, deferred).
    """

    def __init__(self, *, sources_root: Optional[Path] = None) -> None:
        from databridge_mcp_ingest import client as _client          # noqa: PLC0415
        from databridge_mcp_ingest import connections as _conns      # noqa: PLC0415
        self._client_mod = _client
        self._conns = _conns
        self._root = sources_root
        self._open: dict[str, tuple[Any, Any]] = {}

    def _client(self, connection: str):
        if connection not in self._open:
            conn = self._conns.load_connection(connection, self._root)
            opener = getattr(self._client_mod, "open_client", None)
            if opener is None:
                raise RuntimeError("databridge_mcp_ingest.client has no open_client")
            cm = opener(conn)
            self._open[connection] = (cm, cm.__enter__())
        return self._open[connection][1]

    def close(self) -> None:
        for cm, _ in self._open.values():
            try:
                cm.__exit__(None, None, None)
            except Exception:                                # noqa: BLE001
                pass
        self._open.clear()

    def _plan(self, connection: str):
        try:
            from databridge_mcp_ingest.plans import load_plan   # noqa: PLC0415
            return load_plan(connection, self._root)
        except Exception:                                    # noqa: BLE001 - no plan
            return None

    def _sql_spec(self, connection: str):
        plan = self._plan(connection)
        for step in (plan.steps if plan is not None else []):
            if step.sql is not None:
                return step.sql
        return None

    def _denied(self, connection: str, plan: Any) -> set[str]:
        denied = set(getattr(plan, "deny_tools", None) or ())
        try:
            conn = self._conns.load_connection(connection, self._root)
            denied |= set(getattr(getattr(conn, "scope", None), "deny_tools", None) or ())
        except Exception:                                    # noqa: BLE001 - no scope, no extra
            pass
        return denied

    def _write_only(self, connection: str) -> bool:
        """``purpose: agent_write`` — a WRITE-ONLY connection: every tool on it is a write."""
        try:
            conn = self._conns.load_connection(connection, self._root)
        except Exception:                                    # noqa: BLE001 - no declaration
            return False
        return getattr(conn, "purpose", "ingest") == "agent_write"

    def read_error_prefixes(self, connection: str) -> tuple[str, ...]:
        """Text a failed call on ``connection`` starts with when the server reports failure as
        ordinary text (``isError`` false): what the connection's PLAN's profiles and SQL dialect
        declare (`databridge_mcp_ingest.client._declared_error_prefixes`). Only with a plan: a
        document may legitimately begin with the same word."""
        plan = self._plan(connection)
        if plan is None:
            return ()
        out: list[str] = []
        try:
            from databridge_mcp_ingest.client import _declared_error_prefixes  # noqa: PLC0415
            out += list(_declared_error_prefixes(plan))
        except Exception:                                    # noqa: BLE001
            pass
        if self._sql_spec(connection) is not None:
            out += list(_dialect_error_prefixes())
        return tuple(dict.fromkeys(p for p in out if p))

    def write_error_prefixes(self, connection: str) -> tuple[str, ...]:
        """For a WRITE, every error prefix any bundled profile or SQL dialect declares, whatever
        the connection's plan (a write connection usually has none). Conservative on purpose: a
        false failure leaves the action approved for a person to retry; a false success tells the
        approver something happened that did not (E2E F-02)."""
        out = list(self.read_error_prefixes(connection)) + list(_dialect_error_prefixes())
        try:
            from databridge_mcp_ingest.plans import load_profiles   # noqa: PLC0415
            for prof in load_profiles():
                declared = (getattr(prof, "match", None) or {}).get("error_text_prefix")
                out += [str(d) for d in (declared if isinstance(declared, list) else [declared])
                        if d]
        except Exception:                                    # noqa: BLE001
            pass
        return tuple(dict.fromkeys(p for p in out if p))

    def advertise(self, connection: str) -> ConnectionAdvert:
        try:
            tools = self._client(connection).list_tools()
        except Exception as e:                               # noqa: BLE001
            return ConnectionAdvert(connection=connection, reachable=False, problem=str(e))
        plan = self._plan(connection)
        write_only = self._write_only(connection)
        approved_reads = plan_read_tools(plan)          # the SAME function the compiler uses
        sql_tool = None
        for step in (plan.steps if plan is not None else []):
            if step.sql is not None:
                sql_tool = step.sql.tool
                break
        if write_only:
            sql_tool = None                          # a write-only connection is never read
        out = classify_tools(tools, approved_reads=approved_reads,
                             denied=self._denied(connection, plan), write_only=write_only)
        try:
            declared = self._conns.connection_path(connection, self._root).as_posix()
        except Exception:                                    # noqa: BLE001
            declared = f"var/sources/{connection}.yaml"
        return ConnectionAdvert(connection=connection, tools=out, sql=sql_tool is not None,
                                declared_in=declared, sql_tool=sql_tool)

    @staticmethod
    def _result(res: Any, uri: Optional[str],
                error_prefixes: Sequence[str] = ()) -> GatewayResult:
        blob = res.first_blob() if hasattr(res, "first_blob") else None
        text = getattr(res, "text", "") or ""
        is_error = bool(getattr(res, "is_error", False)) or             _starts_with_any(text, error_prefixes)
        return GatewayResult(text=text,
                             data=(blob.data if blob is not None else None),
                             mime=(blob.mime if blob is not None else None),
                             uri=uri, is_error=is_error,
                             # R-021 #12: MCP structuredContent, when the client keeps it
                             structured=getattr(res, "structured", None))

    def call_tool(self, connection: str, tool: str, arguments: dict) -> GatewayResult:
        res = self._client(connection).call_tool(tool, dict(arguments))
        return self._result(res, None, self.read_error_prefixes(connection))

    def query(self, connection: str, sql: str) -> GatewayResult:
        spec = self._sql_spec(connection)
        if spec is None:
            return GatewayResult(is_error=True, text=f"connection {connection!r} has no SQL tool "
                                                     f"in its crawl plan")
        args = {**dict(spec.extra_args or {}), spec.arg: sql}
        res = self._client(connection).call_tool(spec.tool, args)
        return self._result(res, None, self.read_error_prefixes(connection))


def _starts_with_any(text: str, prefixes: Sequence[str]) -> bool:
    head = (text or "").lstrip()
    return any(p and head.startswith(p) for p in prefixes)


def _dialect_error_prefixes() -> tuple[str, ...]:
    """Every ``mcp.error_prefixes`` the bundled SQL dialect templates declare (data, not code)."""
    try:
        from databridge_mcp_ingest.tabular.dialect_draft import load_templates  # noqa: PLC0415
        return tuple(dict.fromkeys(str(p) for t in load_templates()
                                   for p in ((t.get("mcp") or {}).get("error_prefixes") or [])
                                   if p))
    except Exception:                                        # noqa: BLE001 - absent: none
        return ()


def default_gateway() -> Optional[SourceGateway]:
    try:
        return McpSourceGateway()
    except Exception:                                        # noqa: BLE001 - mcp_ingest absent
        return None


# ================================================================================================
# SQL: a read tool may only read
# ================================================================================================
#
# A STRICT ALLOW-LIST, not a blacklist (R-017 t02 finding 2: `SELECT setval('s', 999)` began with
# SELECT, held no blacklisted word, and wrote). The statement is tokenised in ONE pass so a comment
# marker inside a string, or a quote inside a comment, cannot hide code from the checks that follow:
#
# * characters whose meaning differs by dialect in a way that could hide code from this tokeniser
#   are refused outright: backslash (MySQL / E'' string escapes), ``$`` (Postgres dollar quotes,
#   parameters), ``#`` (MySQL comments), ``[`` ``]`` (T-SQL identifiers), nested or executable
#   comments (``/*!``, ``/*+``);
# * one statement (a single trailing ``;`` is tolerated), headed ``SELECT`` or ``WITH`` — EXPLAIN
#   is refused because ``EXPLAIN ANALYZE`` executes its statement;
# * no word that writes, locks or changes session state (``INTO``, ``FOR UPDATE`` …);
# * every function call is a bare name on `SQL_READ_FUNCTIONS`. A schema-qualified or quoted call
#   (``public.lower(``, ``"setval"(``) is refused: an allow-listed NAME can be shadowed.
#
# A false refusal is reported coverage; a false admission is an unapproved write. This is still a
# parser, so it is defence in depth: the boundary is a read-only database principal on the
# connection (deployment configuration — R-017, deferred).
_SQL_READ_HEAD = frozenset({"select", "with"})
_SQL_FORBIDDEN_CHARS = {"\\": "a backslash", "$": "'$'", "#": "'#'", "[": "'['", "]": "']'",
                        "`": "a backtick",
                        # R-021 #8: `@x` is a MySQL user variable / T-SQL variable — reading one
                        # is session state, `SELECT @x := 1` WRITES one. Refused outside strings.
                        "@": "'@' (a session or user variable)"}
#: Assignment operators: `SELECT @x := 1` (MySQL), `x := 1` (PL/pgSQL). A read assigns nothing.
_SQL_ASSIGNMENT = re.compile(r":=")
_SQL_WRITE_WORD = re.compile(
    r"\b(insert|update|delete|merge|upsert|create|drop|alter|truncate|grant|revoke|copy|call|"
    r"execute|exec|attach|detach|vacuum|pragma|lock|reindex|into|outfile|dumpfile|procedure)\b",
    re.I)
_SQL_LOCKING = re.compile(r"\bfor\s+(?:no\s+key\s+)?(?:update|share|key\s+share)\b", re.I)
#: Words that are followed by ``(`` in ordinary read SQL without being a function call.
_SQL_PAREN_KEYWORDS = frozenset({
    "in", "exists", "as", "from", "join", "over", "filter", "any", "all", "some", "values", "on",
    "using", "and", "or", "not", "when", "then", "else", "select", "where", "by", "lateral",
    "union", "intersect", "except", "with", "is", "between", "like", "ilike", "case", "end",
    "having", "limit", "offset", "within", "group", "partition", "order", "distinct", "rows",
    "range", "array", "row", "recursive", "materialized",
    # type names that take a precision: CAST(x AS numeric(10, 2))
    "numeric", "decimal", "varchar", "char", "character", "varying", "timestamp", "timestamptz",
    "time", "bit", "float"})
#: Functions a read may call: pure, standard, and common across Postgres / SQLite / MySQL.
#: Anything else (``setval``, ``nextval``, ``pg_advisory_lock``, ``lo_import``, ``dblink_exec``,
#: ``set_config``, ``pg_sleep`` …) is refused until a person adds it here, with a test.
SQL_READ_FUNCTIONS = frozenset({
    "count", "sum", "avg", "min", "max", "coalesce", "nullif", "ifnull", "iif", "greatest", "least",
    "lower", "upper", "length", "char_length", "character_length", "substr", "substring", "trim",
    "ltrim", "rtrim", "replace", "concat", "concat_ws", "left", "right", "position", "instr",
    "strpos", "split_part", "lpad", "rpad", "initcap", "reverse", "repeat", "printf", "format",
    "round", "abs", "floor", "ceil", "ceiling", "sign", "mod", "power", "sqrt", "trunc",
    "cast", "extract", "date_part", "date_trunc", "to_char", "to_date", "to_timestamp",
    "to_number", "date", "time", "datetime", "julianday", "strftime", "age", "now",
    "current_date", "current_timestamp", "make_date", "interval",
    "string_agg", "array_agg", "group_concat", "json_agg", "jsonb_agg", "json_object_agg",
    "jsonb_object_agg", "json_build_object", "jsonb_build_object", "json_extract",
    "jsonb_extract_path_text", "json_extract_path_text", "total", "bool_and", "bool_or", "every",
    "row_number", "rank", "dense_rank", "percent_rank", "cume_dist", "ntile", "lag", "lead",
    "first_value", "last_value", "nth_value", "percentile_cont", "percentile_disc", "mode",
    "stddev", "stddev_pop", "stddev_samp", "variance", "var_pop", "var_samp", "corr",
    "regexp_replace", "regexp_matches", "regexp_like", "unnest", "generate_series", "typeof"})
_SQL_CALL = re.compile(r"(\"q\"|[A-Za-z_][A-Za-z0-9_]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)*)\s*\(")


def _sql_tokenise(sql: str) -> tuple[Optional[str], str]:
    """``(body, "")`` with every string replaced by ``''``, every quoted identifier by ``"q"`` and
    every comment by a space — or ``(None, why)``. One pass, so quoting and commenting cannot hide
    each other."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in _SQL_FORBIDDEN_CHARS:
            return None, f"it contains {_SQL_FORBIDDEN_CHARS[ch]}, which this check does not admit"
        if ch == "'" or ch == '"':
            j = i + 1
            while True:
                k = sql.find(ch, j)
                if k < 0:
                    return None, "an unterminated quoted string or identifier"
                if k + 1 < n and sql[k + 1] == ch:          # doubled quote = an escaped quote
                    j = k + 2
                    continue
                break
            out.append("''" if ch == "'" else '"q"')
            i = k + 1
            continue
        if sql.startswith("--", i):
            k = sql.find("\n", i)
            i = n if k < 0 else k
            out.append(" ")
            continue
        if sql.startswith("/*", i):
            k = sql.find("*/", i + 2)
            if k < 0:
                return None, "an unterminated comment"
            inner = sql[i + 2:k]
            if "/*" in inner or inner[:1] in ("!", "+"):
                return None, "a nested or executable comment"
            out.append(" ")
            i = k + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out), ""


def sql_is_read_only(sql: str) -> tuple[bool, str]:
    """``(True, "")`` for one read-only statement, else ``(False, why)``. Conservative on purpose:
    a false refusal is reported coverage; a false admission is an unapproved write."""
    body, why = _sql_tokenise(sql or "")
    if body is None:
        return False, why
    body = body.strip()
    if body.endswith(";"):
        body = body[:-1].strip()
    if not body:
        return False, "empty statement"
    if ";" in body:
        return False, "more than one statement"
    lead = re.match(r"[A-Za-z]+", body.lstrip("( \t\r\n"))
    head = lead.group(0).lower() if lead else body[:1]
    if head not in _SQL_READ_HEAD:
        return False, f"statement starts with {head.upper()!r}, not SELECT or WITH"
    m = _SQL_WRITE_WORD.search(body)
    if m:
        return False, f"statement contains {m.group(1).upper()!r}"
    if _SQL_ASSIGNMENT.search(body):
        return False, "statement assigns a value (':='), which changes session state"
    m = _SQL_LOCKING.search(body)
    if m:
        return False, f"statement takes a row lock ({' '.join(m.group(0).upper().split())})"
    for call in _SQL_CALL.finditer(body):
        name = re.sub(r"\s+", "", call.group(1)).lower()
        if name in _SQL_PAREN_KEYWORDS:
            continue
        if name == '"q"' or "." in name:
            return False, (f"it calls a quoted or schema-qualified function ({call.group(1)!r}); "
                           f"only bare allow-listed functions are admitted")
        if name not in SQL_READ_FUNCTIONS:
            return False, (f"it calls {name}(), which is not on the read-only function allow-list "
                           f"(SQL_READ_FUNCTIONS)")
    return True, ""


#: A string that opens a read statement (``SELECT 1`` has no second keyword `_sql_like_args` needs).
_SQL_READ_LEAD = re.compile(r"\s*\(*\s*(?:select|with)\b", re.I)

#: A string argument that opens with one of these AND carries a second SQL keyword is a SQL
#: statement, whichever tool and argument name carry it (a search phrase rarely does both).
_SQL_STATEMENT_HEADS = frozenset({
    "select", "with", "insert", "update", "delete", "merge", "upsert", "replace", "create", "drop",
    "alter", "truncate", "grant", "revoke", "copy", "call", "exec", "execute", "do", "begin", "set",
    "lock", "vacuum", "explain", "attach", "detach", "pragma", "analyze", "table", "values"})
_SQL_SECOND_WORD = re.compile(
    r"\b(from|into|set|table|values|where|select|returning|index|view|function|procedure|"
    r"database|schema|sequence|join|limit|order\s+by|group\s+by)\b", re.I)


def _sql_like_args(args: Any) -> list[str]:
    """Every string in ``args`` (recursively) that reads as a SQL statement."""
    out: list[str] = []
    if isinstance(args, str):
        text = re.sub(r"^[\s(]+", "", args)
        lead = re.match(r"[A-Za-z]+", text)
        rest = text[lead.end():] if lead else ""
        if lead and lead.group(0).lower() in _SQL_STATEMENT_HEADS and \
                (_SQL_SECOND_WORD.search(rest) or ";" in rest):
            out.append(args)
    elif isinstance(args, dict):
        for v in args.values():
            out.extend(_sql_like_args(v))
    elif isinstance(args, (list, tuple)):
        for v in args:
            out.extend(_sql_like_args(v))
    return out


# ================================================================================================
# pointers the model can cite
# ================================================================================================
@dataclass
class Evidence:
    ref: str
    pointer: SourcePointer
    text: str                     # what this pointer grounds — used to check claims against
    locator: Optional[str] = None
    approx: bool = False
    subject_candidate: bool = False
    #: For a candidate (person-kind) pointer: the names / ids that identify the candidate. A claim
    #: that names one of them must cite a candidate ref (answer contract, FED item 4).
    identifiers: tuple = ()
    #: For a candidate: its kind as the pack vocabulary names it (``person``) — reported in the run
    #: outcome's ``candidates`` (N-12).
    subject_kind: Optional[str] = None
    #: Ids (tokens with letters AND digits: ``C-1003``) in the arguments of the query/read that
    #: returned this pointer (P-12). A CLAIM citing the result may name them ("the query for
    #: C-1003 returned no rows"); they never ground a WRITTEN value (`_evidence_text` omits them).
    query_ids: tuple = ()
    #: For an AGGREGATE query result (Q-05): the refs of the source rows its groups were computed
    #: over (`query_rows`). A claim citing the result is cited to these rows as well, and what
    #: they hold grounds it, so the citation names WHICH rows a count or sum covers.
    row_refs: tuple = ()


def pointer_id(pointer: SourcePointer) -> str:
    """A pointer's STABLE id: ``ptr:sha256:<digest of its canonical locator>`` (adapter
    `pointer_digest`). What a pointer-valued argument (a ``source_pointer`` column) is given in
    place of a run-local citation ref, which means nothing outside the run (N-05)."""
    from databridge_adapter import pointer_digest            # noqa: PLC0415
    return f"ptr:sha256:{pointer_digest(pointer)}"


_ID_TOKEN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_\-]*[A-Za-z0-9])?")


def query_ids(args: Any) -> list[str]:
    """Identifier tokens (letters AND digits, e.g. ``C-1003``, ``INV-2026-0057``) in a call's
    argument VALUES — keys are structure. Bare numbers and words are not ids."""
    out: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, Mapping):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            for tok in _ID_TOKEN.findall(v):
                if any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok)                         and tok not in out:
                    out.append(tok)
    walk(args)
    return out


class PointerRegistry:
    """Assigns short refs (``p1``, ``p2`` …) to pointers returned in this run. The model cites refs;
    a ref the registry did not issue is, by construction, not a pointer.

    With ``track_disclosure`` (an agent run), a ref counts only once it has been DISCLOSED to the
    planner: `disclose` is called with the refs that appear in the observations actually sent to
    the model (R-021 #2 / #13). `get` answers only for disclosed refs, so a ref the model guessed
    before seeing it (a "future" ref minted by a tool call in the same turn), or the ref of a row
    that was registered but never shown, is not a pointer. Without it (the federated service,
    which registers and checks in one step) every issued ref counts.
    """

    def __init__(self, *, track_disclosure: bool = False) -> None:
        self._by_key: dict[str, Evidence] = {}
        self._by_ref: dict[str, Evidence] = {}
        self._disclosed: Optional[set[str]] = set() if track_disclosure else None

    def disclose(self, refs: Any) -> None:
        """These refs (issued by this registry) have now been shown to the planner."""
        if self._disclosed is not None:
            self._disclosed.update(r for r in (str(x) for x in refs) if r in self._by_ref)

    def _visible(self, ev: Evidence) -> bool:
        return self._disclosed is None or ev.ref in self._disclosed

    def add(self, pointer: SourcePointer, text: str, *, locator: Optional[str] = None,
            approx: bool = False, subject_candidate: bool = False,
            identifiers: Sequence[Optional[str]] = (),
            subject_kind: Optional[str] = None) -> str:
        key = json.dumps(pointer.model_dump(mode="json"), sort_keys=True)
        ids = tuple(dict.fromkeys(str(i).strip() for i in identifiers or ()
                                  if i and str(i).strip()))
        ev = self._by_key.get(key)
        if ev is not None:
            if text and text not in ev.text:
                ev.text = f"{ev.text}\n{text}"
            ev.subject_candidate = ev.subject_candidate or subject_candidate
            ev.identifiers = tuple(dict.fromkeys((*ev.identifiers, *ids)))
            ev.subject_kind = ev.subject_kind or subject_kind
            return ev.ref
        ref = f"p{len(self._by_ref) + 1}"
        ev = Evidence(ref=ref, pointer=pointer, text=text or "", locator=locator, approx=approx,
                      subject_candidate=subject_candidate, identifiers=ids,
                      subject_kind=subject_kind if subject_candidate else None)
        self._by_key[key] = ev
        self._by_ref[ref] = ev
        return ref

    def annotate_query(self, pointer: SourcePointer, ids: Sequence[str]) -> None:
        """Record the ids of the query that returned ``pointer`` on its evidence (P-12)."""
        if not ids:
            return
        ev = self._by_key.get(json.dumps(pointer.model_dump(mode="json"), sort_keys=True))
        if ev is not None:
            ev.query_ids = tuple(dict.fromkeys((*ev.query_ids, *ids)))

    def candidates(self) -> list[Evidence]:
        """Every DISCLOSED candidate (person-kind) evidence that carries identifiers."""
        return [ev for ev in self._by_ref.values()
                if ev.subject_candidate and ev.identifiers and self._visible(ev)]

    def get(self, ref: str) -> Optional[Evidence]:
        """The evidence behind ``ref`` — only if it was issued AND disclosed to the planner."""
        ev = self._by_ref.get(str(ref).strip().strip("[]^"))
        return ev if ev is not None and self._visible(ev) else None

    def __len__(self) -> int:
        return len(self._by_ref)


# ================================================================================================
# one call, decided and recorded
# ================================================================================================
@dataclass
class ToolOutcome:
    tool: str
    args: dict
    permitted: bool
    reason_code: str
    detail: str
    capability: Optional[str]
    grant_id: Optional[str]
    observation: Any = None
    pointers: list[SourcePointer] = field(default_factory=list)
    origin: str = "graph"                 # "graph" (history) | "live_source"
    ms: int = 0
    ok: bool = True                       # permitted AND the tool answered

    def policy_decision(self) -> dict:
        return {"decision": "permit" if self.permitted else "refuse",
                "reason_code": self.reason_code, "detail": self.detail,
                "capability": self.capability, "grant_id": self.grant_id}


class ToolLayer:
    """The only way the planner reaches data."""

    def __init__(self, *, spec: WorkflowSpec, authority: GrantedAuthority, registry: PointerRegistry,
                 store: Any = None, retriever: Optional[Retriever] = None,
                 gateway: Optional[SourceGateway] = None, capture_root: Optional[Path] = None,
                 vocabulary: Any = None, facts: Optional[FactsStore] = None,
                 federation: Any = None, screen: Any = None) -> None:
        self.spec = spec
        #: The ingestion screen's config (`databridge_mcp_ingest.screen.ScreenConfig`); loaded
        #: lazily with ingestion's own `load_screen()` when not injected (E2E F-13).
        self._screen_cfg = screen
        self._screen_problem: Optional[str] = None
        self._overrides: dict[str, set] = {}
        self.federation = federation
        self.authority = authority
        self.registry = registry
        self.store = store
        self.retriever = retriever
        self.gateway = gateway
        self.capture_root = capture_root
        self.facts = facts
        self.scope = frozenset(spec.sources)
        self._vocab = vocabulary
        self.vocab_problem: Optional[str] = None
        self._declared = frozenset(spec.tools)
        self._read_any = {parse_tool(t)["connection"] for t in spec.tools
                          if parse_tool(t)["verb"] == "read" and not parse_tool(t)["tool"]}
        self._adverts: dict[str, ConnectionAdvert] = {}

    def _advert(self, connection: str) -> ConnectionAdvert:
        if connection not in self._adverts:
            assert self.gateway is not None
            self._adverts[connection] = self.gateway.advertise(connection)
        return self._adverts[connection]

    # -- admission -----------------------------------------------------------------------------
    def admit(self, tool: str) -> ToolOutcome:
        def refuse(code: str, detail: str, cap: Optional[str] = None) -> ToolOutcome:
            return ToolOutcome(tool=tool, args={}, permitted=False, reason_code=code, detail=detail,
                               capability=cap, grant_id=None, ok=False)
        try:
            parts = parse_tool(tool)
        except SpecError as e:
            return refuse("unknown_tool", str(e))
        if parts["verb"] == "write":
            return refuse("write_is_an_action",
                          f"{tool!r} is a write. Writes happen only as declared actions with an "
                          f"automation mode; a read-only agent has none")
        listed = tool in self._declared or (
            parts["verb"] == "read" and parts["connection"] in self._read_any)
        if not listed:
            return refuse("not_in_workflow", f"{tool!r} is not one of this agent's tools")
        try:
            cap = capability_for_tool(tool)
        except MappingError as e:
            return refuse("unknown_tool", str(e))
        assert cap is not None
        candidates = [cap]
        if parts["verb"] == "read" and parts["tool"]:
            candidates.append(f"{source_id_for(parts['connection'])}.{READ_ANY_CAPABILITY}")
        for c in candidates:
            try:
                grant = self.authority.require(c)
            except AmbientAuthorityRefused:
                continue
            return ToolOutcome(tool=tool, args={}, permitted=True, reason_code="granted",
                               detail=f"granted by {grant.ref} for {grant.purpose}",
                               capability=c, grant_id=grant.grant_id)
        return refuse("no_grant", f"no compiled grant covers {cap!r}; widening needs a new "
                                  f"declaration version", cap)

    # -- execution -----------------------------------------------------------------------------
    def _sql_read_route(self, tool: str, args: dict) -> Optional[tuple[str, Any]]:
        """E2E run 4 Q-02: a ``source.read`` of the connection's OWN SQL tool (``execute_sql``,
        vouched as a read by an approved crawl plan) is routed through the checked query path —
        ``(connection, statement)`` — or ``(connection, why)`` with ``why`` a `ToolOutcome`
        refusal when its arguments hold no single statement. ``None``: not the SQL tool."""
        try:
            parts = parse_tool(tool)
        except SpecError:
            return None
        if parts["family"] != "source" or parts["verb"] != "read" or not parts["tool"] \
                or self.gateway is None:
            return None
        conn = str(parts["connection"])
        try:
            adv = self._advert(conn)
        except Exception:                                    # noqa: BLE001 - not described
            return None
        if not adv.sql_tool or parts["tool"] != adv.sql_tool:
            return None
        stmts = list(dict.fromkeys(
            [*_sql_like_args(args),
             *(v for v in args.values() if isinstance(v, str) and _SQL_READ_LEAD.match(v))]))
        return conn, (stmts[0] if len(stmts) == 1 else len(stmts))

    def call(self, tool: str, args: Optional[dict]) -> ToolOutcome:
        args = dict(args or {})
        routed = self._sql_read_route(tool, args)
        if routed is not None:
            return self._call_sql_read(tool, args, *routed)
        out = self.admit(tool)
        out.args = args
        if not out.permitted:
            return out
        return self._execute(tool, args, out)

    def _call_sql_read(self, tool: str, args: dict, conn: str, stmt: Any) -> ToolOutcome:
        """The SQL tool reached through ``source.read`` runs as ``source.query:<conn>``: admitted by
        the query grant (or, for an agent compiled before Q-02, by the vouched read grant of that
        tool), and the statement passes the SAME strict read-only check (`sql_is_read_only`) before
        the SAME gateway path runs it. Read-only never writes: a statement that is not one
        read-only query is refused, exactly as through ``source.query``."""
        query = f"source.query:{conn}"
        out = self.admit(query)
        if not out.permitted:
            out = self.admit(tool)
        out.tool = tool
        out.args = dict(args)
        if not out.permitted:
            return out
        if not isinstance(stmt, str):
            out.ok = False
            out.observation = {
                "error": f"not called: {tool!r} executes SQL, so it runs as {query} with ONE "
                         f"read-only statement in its arguments; found {stmt}",
                "args_invalid": True, "takes": "{sql: one SELECT or WITH statement}"}
            return out
        out.args = {"sql": stmt}
        out.detail = (f"{out.detail}; {tool!r} executes SQL, so it ran as {query} (the statement "
                      f"passed the read-only check)")
        return self._execute(query, {"sql": stmt}, out, note=f"run as {query}: {tool!r} executes "
                                                             f"SQL, so the statement was checked "
                                                             f"read-only first")

    def _execute(self, tool: str, args: dict, out: ToolOutcome,
                 note: Optional[str] = None) -> ToolOutcome:
        parts = parse_tool(tool)
        t0 = time.perf_counter()
        try:
            if parts["family"] == GRAPH_SOURCE:
                self._graph(parts["op"] or "", args, out)
            elif parts["family"] == FACTS_SOURCE:
                self._facts(parts["connection"] or "", args, out)
            elif parts["family"] == DELEGATE_SOURCE:
                self._delegate(parts["connection"] or "", args, out)
            elif parts["family"] == RETRIEVE_SOURCE:
                self._retrieve(args, out)
            else:
                self._source(parts, args, out)
        except Exception as e:                               # noqa: BLE001 - reported, not raised
            out.ok = False
            out.observation = {"error": f"the tool failed: {type(e).__name__}: {e}"}
        if parts["family"] in (FACTS_SOURCE, "source") and out.pointers:
            ids = query_ids(out.args)                        # P-12: what the query was ABOUT
            for p in out.pointers:
                self.registry.annotate_query(p, ids)
        if note and isinstance(out.observation, dict):
            out.observation = {"routed": note, **out.observation}
        out.ms = int((time.perf_counter() - t0) * 1000)
        return out

    # graph ------------------------------------------------------------------------------------
    def _in_scope(self, p: SourcePointer) -> bool:
        return p.source_id in self.scope

    def _is_subject(self, kind: str) -> bool:
        """Whether ``kind`` is a subject (person-like) kind. FAILS CLOSED (R-017 t02 finding 5):
        when the vocabulary cannot be loaded or cannot answer, the node is marked a candidate —
        over-marking an organisation costs a notice; under-marking a person drops the only signal
        that who they are is a human's call."""
        vocab = self._vocab
        if vocab is None:
            try:
                from databridge_graph.packs import active_vocabulary   # noqa: PLC0415
                vocab = active_vocabulary()
            except Exception:                                # noqa: BLE001 - fail closed
                self.vocab_problem = "the pack vocabulary could not be loaded"
                return True
        try:
            return bool(vocab.is_subject(str(kind)))
        except Exception as e:                               # noqa: BLE001 - fail closed
            self.vocab_problem = f"the vocabulary could not classify kind {kind!r}: {e}"
            return True

    def _node_view(self, node: Any, out: ToolOutcome, withheld: list[int]) -> Optional[dict]:
        kind = str(getattr(node.kind, "value", node.kind))
        subject = self._is_subject(kind)
        text = " ".join([node.label, node.canonical_id or ""] +
                        [f"{k}: {v}" for k, v in node.attrs.items()])
        ids = (node.label, node.canonical_id) if subject else ()
        refs = []
        for p in node.sources:
            if not self._in_scope(p):
                withheld[0] += 1
                continue
            refs.append(self.registry.add(p, text, subject_candidate=subject,
                                          identifiers=ids, subject_kind=kind))
            out.pointers.append(p)
        attrs = {}
        for k, v in node.attrs.items():
            p = node.attr_sources.get(k)
            if p is None or not self._in_scope(p):
                withheld[0] += 1
                continue
            ref = self.registry.add(p, f"{node.label} {k}: {v}", subject_candidate=subject,
                                    identifiers=ids, subject_kind=kind)
            out.pointers.append(p)
            attrs[k] = {"value": v, "ref": ref}
        if not refs and not attrs:
            return None                                      # nothing in this agent's scope
        view = {"node": node.id, "kind": kind, "label": node.label, "refs": refs, "attrs": attrs}
        if subject:
            view["candidate"] = True
            view["requires_human_selection"] = True
        return view

    def _edge_view(self, edge: Any, out: ToolOutcome, withheld: list[int]) -> Optional[dict]:
        refs = []
        text = f"{edge.src_id} {edge.kind} {edge.dst_id}"
        for p in edge.sources:
            if not self._in_scope(p):
                withheld[0] += 1
                continue
            refs.append(self.registry.add(p, text))
            out.pointers.append(p)
        if not refs:
            return None
        return {"edge": edge.id, "kind": str(getattr(edge.kind, "value", edge.kind)),
                "src": edge.src_id, "dst": edge.dst_id, "refs": refs}

    def _graph(self, op: str, args: dict, out: ToolOutcome) -> None:
        out.origin = "graph"
        if self.store is None:
            out.ok = False
            out.observation = {"error": "no graph store is connected"}
            return
        from databridge_graph import query as gq                   # noqa: PLC0415
        from databridge_graph.store import row_to_node             # noqa: PLC0415
        withheld = [0]
        if op == "search":
            q = str(args.get("query") or args.get("q") or "")
            k = max(1, min(int(args.get("k", 5)), 25))
            nodes = gq.link_entities(self.store, q, top_k=k)
            views = [v for v in (self._node_view(n, out, withheld) for n in nodes) if v]
            out.observation = {"nodes": views}
        elif op == "node":
            row = self.store.open_node_row(str(args.get("node_id") or args.get("id") or ""))
            view = self._node_view(row_to_node(self.store, row), out, withheld) if row else None
            out.observation = {"node": view}
        elif op == "neighbors":
            depth = max(1, min(int(args.get("depth", 1)), 2))
            sub = gq.neighbors(self.store, str(args.get("node_id") or args.get("id") or ""),
                               depth=depth)
            out.observation = {
                "nodes": [v for v in (self._node_view(n, out, withheld) for n in sub.nodes) if v],
                "edges": [v for v in (self._edge_view(e, out, withheld) for e in sub.edges) if v]}
        elif op == "path":
            gp = gq.path(self.store, str(args.get("src") or args.get("src_id") or ""),
                         str(args.get("dst") or args.get("dst_id") or ""))
            steps = []
            for s in gp.steps:
                ev = self._edge_view(s.edge, out, withheld)
                # R-021 #7: the destination is classified through the pack vocabulary like any
                # other node (a person reached by a path is a CANDIDATE), never shown as a bare
                # label. A node with no in-scope evidence breaks the path.
                to = self._node_view(s.to_node, out, withheld)
                if ev is None or to is None:     # a path through out-of-scope evidence is no path
                    steps = []
                    break
                steps.append({"edge": ev, "to": to})
            out.observation = {"found": bool(gp.found and steps), "steps": steps}
        else:
            out.ok = False
            out.observation = {"error": f"graph.{op} is not a graph operation"}
            return
        if withheld[0]:
            out.observation["withheld_out_of_scope"] = withheld[0]

    # retrieval -------------------------------------------------------------------------------
    def _retrieve(self, args: dict, out: ToolOutcome) -> None:
        out.origin = "graph"                  # history (the passage store), not a live read
        if self.retriever is None:
            out.ok = False
            out.observation = {"error": "retrieval is not available in this deployment"}
            return
        if not self.scope:
            out.ok = False
            out.observation = {"error": "this agent declares no sources to search"}
            return
        q = str(args.get("query") or args.get("q") or "")
        k = max(1, min(int(args.get("k", 8)), 25))
        passages = self.retriever.search(q, k=k, sources=sorted(self.scope),
                                         kinds=args.get("kinds"), mode=str(args.get("mode",
                                                                                    "hybrid")))
        views = []
        excluded = {"no_pointer": 0, "out_of_scope": 0}      # counted, never silent (R-017 #12)
        for p in passages:
            get = (p.get if isinstance(p, dict) else lambda key, d=None, _p=p: getattr(_p, key, d))
            raw_ptr = get("pointer")
            if raw_ptr is None:
                excluded["no_pointer"] += 1                  # no pointer, not evidence
                continue
            ptr = raw_ptr if isinstance(raw_ptr, SourcePointer) else \
                SourcePointer.model_validate(raw_ptr)
            if not self._in_scope(ptr):
                excluded["out_of_scope"] += 1
                continue
            text = str(get("text", "") or "")
            locator = get("locator")
            approx = bool(get("approx_locator", False))
            ref = self.registry.add(ptr, text, locator=locator, approx=approx)
            out.pointers.append(ptr)
            views.append({"ref": ref, "source": ptr.source_id, "doc": get("doc_id"),
                          "locator": (f"approx. {locator}" if approx and locator else locator),
                          "text": text[:1200]})
        out.observation = {"passages": views}
        if excluded["out_of_scope"]:
            out.observation["withheld_out_of_scope"] = excluded["out_of_scope"]
        if excluded["no_pointer"]:
            out.observation["excluded_no_pointer"] = excluded["no_pointer"]

    # facts store (S2) ------------------------------------------------------------------------
    def _facts(self, conn: str, args: dict, out: ToolOutcome) -> None:
        """Exact SQL over ``conn``'s read-only facts store — how counting and listing questions
        are answered (never by retrieval, never by live SQL). The statement passes this runtime's
        strict read-only check BEFORE the store's own read-only connection sees it; every cell
        comes back with its ``query_cell`` pointer and the ``[SQL] … extracted <date>`` citation."""
        out.origin = "graph"                  # a dated snapshot, not a live read
        if self.facts is None:
            out.ok = False
            out.observation = {"error": "no facts store is available in this deployment"}
            return
        sql = str(args.get("sql") or "")
        ok, why = sql_is_read_only(sql)
        if not ok:
            out.permitted, out.ok = False, False
            out.reason_code = "sql_not_read_only"
            out.detail = f"facts.query is read-only; {why}"
            out.observation = None
            return
        params = args.get("params")
        if isinstance(params, list):
            params = tuple(params)
        try:
            ans = self.facts.query(conn, sql, params=params,
                                   question=(str(args["question"]) if args.get("question")
                                             else None))
        except Exception as e:                               # noqa: BLE001 - reported
            out.ok = False
            out.observation = {"error": f"the facts query failed: {type(e).__name__}: {e}"}
            return
        columns = list(getattr(ans, "columns", []) or [])
        rows = [list(r) for r in (getattr(ans, "rows", []) or [])]
        pointers = list(getattr(ans, "pointers", []) or [])
        citation = str(getattr(ans, "citation", "") or "")
        # R-021 #1: a cell without a pointer is not evidence, and a store that returns one has
        # broken its contract (one pointer per cell): the WHOLE result fails, nothing is shown.
        n_cells = sum(min(len(columns), len(r)) for r in rows)
        if len(pointers) < n_cells or any(p is None for p in pointers[:n_cells]):
            out.ok = False
            out.observation = {"error": f"the facts store returned {n_cells} cell(s) but "
                                        f"{sum(1 for p in pointers[:n_cells] if p is not None)} "
                                        f"source pointer(s); a value without a pointer is not "
                                        f"evidence, so nothing from this result is shown"}
            return
        shown_rows = []
        withheld = 0
        k = 0
        for i, row in enumerate(rows):
            if i >= SHOWN_ROWS:
                break                   # R-021 #13: a row the planner is not shown gets no ref
            cells = []
            for c, v in zip(columns, row):
                ptr = pointers[k]
                k += 1
                if not self._in_scope(ptr):
                    # R-021 #1: the value goes with its pointer — never shown without it
                    withheld += 1
                    cells.append({"column": c, "value": None, "withheld": "out_of_scope"})
                    continue
                ref = self.registry.add(ptr, f"{c}: {v}",       # the cell, nothing else
                                        locator=f"row {i}, {c}")
                out.pointers.append(ptr)
                cells.append({"column": c, "value": v, "ref": ref})
            shown_rows.append(cells)
        obs: dict = {"citation": citation, "extract_date": getattr(ans, "extract_date", None),
                     "columns": columns, "row_count": getattr(ans, "row_count", len(rows)),
                     "rows": shown_rows}
        if getattr(ans, "truncated", False) or len(rows) > len(shown_rows):
            obs["truncated"] = (f"showing {len(shown_rows)} of {len(rows)} row(s)"
                                + ("; the store capped the result" if getattr(ans, "truncated",
                                                                               False) else ""))
        if withheld:
            obs["withheld_out_of_scope"] = withheld
        out.observation = obs

    # delegated sub-queries (FED) -------------------------------------------------------------
    def _delegate(self, adapter_agent: str, args: dict, out: ToolOutcome) -> None:
        """Ask an adapter agent's delegate one typed sub-query (``databridge_federated.SubTask``).

        Read-only by construction: a delegate has no write path. The CALLER's admission applies —
        the delegate reads a connection only if it is one of this agent's declared sources (and a
        live read only with that connection's own read grant); a refused connection comes back as
        coverage in the observation, and the run reports it as reduced coverage. The adapter agent
        is the one the TOOL NAME names, never one the model puts in the arguments."""
        from .federation import ToolLayerAdmission                  # noqa: PLC0415

        out.origin = "graph"                  # the governed stores, unless the delegate says live
        if self.federation is None:
            out.ok = False
            out.observation = {"error": "delegated querying is not available in this deployment"}
            return
        if not self.federation.available(adapter_agent):
            out.ok = False
            out.observation = {"error": f"no adapter agent {adapter_agent!r} can answer delegated "
                                        f"queries here"}
            return
        raw = args.get("subtask") if "subtask" in args else             {k: v for k, v in args.items() if k != "answers"}
        if isinstance(raw, Mapping):
            raw = dict(raw)
            lifted = raw.pop("answers", None)
        else:
            lifted = None
        # AUTO2 / FED3: the user's answers to the federated planner's questions (``fed:<sha1>``
        # ids, recorded when the agent was compiled) travel with the sub-task. Additive: passed
        # only to a federated service whose `ask_delegate` takes ``answers``.
        answers, ignored, note = _delegate_answers(args.get("answers") or lifted,
                                                   self.federation.ask_delegate)
        # E2E run 3 P-02: the sub-task is shaped to, and checked against, the SubTask contract
        # BEFORE it is sent (N-11's rule for MCP tools). One that still does not fit is not asked;
        # the planner is told what is wrong in plain words, and it counts as a failed call.
        subtask, shaped, problems = shape_subtask(raw, adapter_agent)
        out.args = {**args, "subtask": subtask} if "subtask" in args else             {**subtask, **({"answers": args["answers"]} if "answers" in args else {})}
        if problems:
            out.ok = False
            out.observation = {
                "error": f"not asked: the sub-task does not fit the delegated-query contract "
                         f"({'; '.join(problems)})",
                "args_invalid": True, "takes": describe_subtask(),
                **({"arguments_shaped": shaped} if shaped else {}),
                **({"delegate_holds": held} if (held := delegate_holdings(
                    self.federation, adapter_agent)) else {})}
            return
        res = self.federation.ask_delegate(adapter_agent, subtask,
                                           admission=ToolLayerAdmission(self, adapter_agent),
                                           **({"answers": answers} if answers and not note
                                              else {}))
        refused = getattr(res, "method", None) == "refused"      # admission: coverage, below
        if getattr(res, "error", None) and not refused:
            out.ok = False
            out.observation = {"error": summarise_error(res.error),
                               **({"arguments_shaped": shaped} if shaped else {}),
                               **({"delegate_holds": held} if (held := delegate_holdings(
                                   self.federation, adapter_agent)) else {})}
            return
        live = bool(getattr(res, "live", False))
        cfg = self._screen() if live else None
        if live and cfg is None:
            # R-021 #10: a delegated LIVE read passes the same screen as a direct one - no screen,
            # nothing shown (fail closed).
            out.origin = "live_source"
            out.observation = {"withheld_by_screen": {
                "decision": "unscreened", "rules": [],
                "reason": f"the ingestion screen could not be loaded ({self._screen_problem}); a "
                          f"delegated live read is never shown unscreened"}}
            return
        withheld = 0
        no_ptr = 0
        unverified = 0
        screened_rows: list[dict] = []
        rows_view = []
        all_rows = list(getattr(res, "rows", []) or [])
        for row in all_rows[:SHOWN_ROWS]:        # R-021 #13: rows beyond the cap get no ref
            raw_cells = dict(getattr(row, "cells", {}) or {})
            masks: dict = {}
            if live:
                # R-021 #10: screened exactly like a direct SQL row - any verdict but `allow`
                # withholds the row; string values are masked before anything registers them.
                blob = json.dumps({n: getattr(gv, "value", None) for n, gv in raw_cells.items()},
                                  sort_keys=True, default=str)
                v = _screen_text(blob, cfg)
                if v.decision != "allow":
                    screened_rows.append({"row": getattr(row, "row_id", None),
                                          "decision": v.decision,
                                          "rules": [f.get("rule") for f in v.fired]})
                    continue
                masks = {n: _mask_value(getattr(gv, "value", None), cfg)
                         for n, gv in raw_cells.items()}
            cells = {}
            for name, gv in raw_cells.items():
                ptr = getattr(gv, "pointer", None)
                value = masks[name] if live else getattr(gv, "value", None)
                if ptr is None:
                    no_ptr += 1                  # R-021 #1: no pointer, no value
                    cells[name] = {"value": None, "withheld": "no_pointer"}
                    continue
                if not self._in_scope(ptr):
                    withheld += 1                # R-021 #1: never a value without its pointer
                    cells[name] = {"value": None, "withheld": "out_of_scope"}
                    continue
                if live and not self._capture_verified(getattr(ptr, "capture_digest", None)):
                    unverified += 1              # R-021 #10: live evidence must be re-verifiable
                    cells[name] = {"value": None, "withheld": "unverified_live_capture"}
                    continue
                ref = self.registry.add(ptr, f"{name}: {value}")
                out.pointers.append(ptr)
                cells[name] = {"value": value, "ref": ref}
            ents = []
            for e in list(getattr(row, "entities", []) or []):
                ptr = getattr(e, "pointer", None)
                if ptr is None:
                    no_ptr += 1
                    continue
                if not self._in_scope(ptr):
                    withheld += 1
                    continue
                # R-021 #7: the delegate's own flag is not trusted alone - the kind is classified
                # through the pack vocabulary too (fail closed); either one makes a candidate.
                subject = bool(getattr(e, "subject", False)) or self._is_subject(
                    str(getattr(e, "kind", "") or ""))
                ref = self.registry.add(ptr, f"{e.label} {e.canonical_id or ''}".strip(),
                                        subject_candidate=subject,
                                        identifiers=(e.label, e.canonical_id) if subject else (),
                                        subject_kind=str(getattr(e, "kind", "") or "") or None)
                out.pointers.append(ptr)
                view = {"node": e.node_id, "kind": e.kind, "label": e.label,
                        "canonical_id": e.canonical_id, "ref": ref}
                if subject:
                    view["candidate"] = True
                    view["requires_human_selection"] = True
                ents.append(view)
            rows_view.append({"row": row.row_id, "cells": cells, "entities": ents})
        cov = getattr(res, "coverage", None)
        skipped = [dict(s) if isinstance(s, dict) else s.model_dump()
                   for s in (getattr(cov, "skipped", None) or [])]
        obs: dict = {"adapter_agent": adapter_agent, "rows": rows_view,
                     **({"arguments_shaped": shaped} if shaped else {}),
                     "row_count": len(all_rows),
                     "sources_read": list(getattr(cov, "sources_read", None) or []),
                     "method": getattr(res, "method", None)}
        if skipped:
            obs["delegate_skipped"] = skipped
        if answers and not note:
            obs["answers_passed"] = sorted(answers)
        if ignored or note:
            obs["answers_not_passed"] = note or (f"{len(ignored)} answer id(s) are not federated "
                                                 f"question ids (fed:<sha1>): {ignored[:5]}")
        if getattr(cov, "truncated", False) or len(all_rows) > SHOWN_ROWS:
            obs["truncated"] = ("the delegate capped its result, or more rows came back than are "
                                "shown; the answer may be incomplete")
        if live:
            out.origin = "live_source"
        if withheld:
            obs["withheld_out_of_scope"] = withheld
        if no_ptr:
            obs["excluded_no_pointer"] = no_ptr
        if unverified:
            obs["excluded_unverified_capture"] = unverified
        if screened_rows:
            decisions = sorted({str(w["decision"]) for w in screened_rows})
            obs["withheld_by_screen"] = {
                "decision": "/".join(decisions), "rows": len(screened_rows),
                "rules": sorted({str(r) for w in screened_rows for r in w["rules"] if r}),
                "reason": f"{len(screened_rows)} delegated live row(s) withheld by the ingestion "
                          f"screen"}
        out.observation = obs

    def _capture_verified(self, sha: Optional[str]) -> bool:
        """A live pointer's ``capture_digest`` names a blob in a capture store whose bytes hash to
        it AND whose metadata verifies (`read_captured_meta`) - the checks `/evidence` makes.
        Looked up where this run captures, where the federated service captures, and the default
        store."""
        if not sha or not isinstance(sha, str) or len(sha) < 8:
            return False
        import hashlib                                                  # noqa: PLC0415
        from . import paths as _paths                                   # noqa: PLC0415
        roots = [self.capture_root, getattr(self.federation, "capture_root", None)]
        try:
            roots.append(_paths.capture_dir())
        except Exception:                                    # noqa: BLE001 - no default store
            pass
        from .capture import read_captured_meta                         # noqa: PLC0415
        for root in roots:
            if root is None:
                continue
            blob = Path(root) / sha[:2] / sha
            try:
                if not (blob.is_file() and hashlib.sha256(blob.read_bytes()).hexdigest() == sha):
                    continue
                # W1: the metadata must verify too (ingest's get_meta, digest required) — a
                # capture whose .json was swapped, edited or stripped is not evidence.
                return read_captured_meta(sha, Path(root)) is not None
            except Exception:                                # noqa: BLE001 - corrupt: refused
                return False
        return False

    # live sources ----------------------------------------------------------------------------
    def _source(self, parts: dict, args: dict, out: ToolOutcome) -> None:
        out.origin = "live_source"
        self._shaped: list[str] = []
        conn = parts["connection"]
        if self.gateway is None:
            out.ok = False
            out.observation = {"error": "live sources are not connected in this deployment"}
            return
        if parts["verb"] == "query":
            sql = str(args.get("sql") or "")
            ok, why = sql_is_read_only(sql)
            if not ok:
                out.permitted, out.ok = False, False
                out.reason_code = "sql_not_read_only"
                out.detail = f"source.query is a read tool; {why}. A write is a declared action."
                out.observation = None
                return
            res = self.gateway.query(conn, sql)
            uri = f"mcp://{conn}/sql?{_short_hash(sql)}"
        else:
            args = dict(args)
            tool = parts["tool"] or str(args.pop("tool", "") or "")
            # Checked on EVERY read, not only for the `source.read:<conn>` wildcard: a read grant
            # never reaches a tool the source itself says writes, whatever the name suggests.
            adv = self._advert(conn)
            t = adv.tool(tool)
            if t is None or t.writes:
                out.permitted, out.ok = False, False
                out.reason_code = "not_an_advertised_read"
                out.detail = (f"{tool!r} is not an advertised read tool of {conn!r}; writes are "
                              f"declared actions")
                return
            # N-11: the arguments are shaped to, and checked against, the tool's own inputSchema
            # BEFORE anything is sent. A call that still does not fit is not made; the planner is
            # told the schema in plain words (and it counts as a failed call).
            schema = getattr(t, "input_schema", None)
            args, shaped = shape_args(args, schema)
            out.args = dict(args)
            from .runtime import args_schema_problems          # noqa: PLC0415 - one checker
            problems = args_schema_problems(args, schema)
            if problems:
                out.ok = False
                out.observation = {
                    "error": f"not called: these arguments do not fit {tool!r}'s input schema "
                             f"({'; '.join(problems)})",
                    "args_invalid": True, "takes": describe_schema(schema),
                    **({"arguments_shaped": shaped} if shaped else {})}
                return
            self._shaped = shaped
            # SQL cannot be smuggled past the read-only check by calling the SQL tool (or any
            # tool) through source.read with a statement in its arguments (R-017 t02 finding 2).
            if adv.sql_tool and tool == adv.sql_tool:
                out.permitted, out.ok = False, False
                out.reason_code = "sql_not_read_only"
                out.detail = (f"{tool!r} executes SQL; call source.query:{conn} so the statement "
                              f"is checked, not source.read")
                return
            for stmt in _sql_like_args(args):
                ok, why = sql_is_read_only(stmt)
                if not ok:
                    out.permitted, out.ok = False, False
                    out.reason_code = "sql_not_read_only"
                    out.detail = (f"an argument of {tool!r} is a SQL statement that is not a "
                                  f"read-only query: {why}. A write is a declared action.")
                    return
            res = self.gateway.call_tool(conn, tool, args)
            retry = _source_relative(args, schema) if res.is_error else None
            if retry is not None:
                # FIN-11: the planner addressed the source's tree from "/" ("/" or "/2026"), and
                # the source refused it. A source's paths are relative to ITS root, so the same
                # read is sent ONCE more with the path relative to that root — same tool, same
                # grant, still a read; the first refusal is kept on the step either way.
                again = self.gateway.call_tool(conn, tool, retry[0])
                if not again.is_error:
                    args, res = retry[0], again
                    out.args = dict(args)
                    self._shaped = [*self._shaped, retry[1]]
            uri = res.uri or str(args.get("path") or args.get("uri") or "") or \
                f"mcp://{conn}/{tool}?{_short_hash(json.dumps(args, sort_keys=True, default=str))}"
        if res.is_error:
            out.ok = False
            out.observation = {"error": (res.text or "the source returned an error")[:500]}
            return
        # --- the ingestion screen, BEFORE anything reaches the model (E2E F-13 / F-15) ---------
        cfg = self._screen()
        if cfg is None:
            out.observation = {"withheld_by_screen": {
                "decision": "unscreened", "rules": [],
                "reason": f"the ingestion screen could not be loaded ({self._screen_problem}); a "
                          f"live read is never shown unscreened"}}
            return
        raw_text = res.text or ""
        withheld_rows: list[dict] = []
        if parts["verb"] == "query":
            text, masked, withheld_rows = _screen_sql_result(raw_text, cfg)
        else:
            text = raw_text
            if not text and res.data:
                text, why = _extract_text(res.data, name=str(args.get("path") or uri),
                                          mime=res.mime, source_id=conn)
                if text is None:
                    out.ok = False
                    out.observation = {"error": f"the result could not be read as text: {why}"}
                    return
            # an owner override covers ONE item at ONE content digest (R-022 build cross-track 3):
            # the digest of exactly the bytes this read returned, as ingestion captures them
            digest = hashlib.sha256(res.data if res.data else raw_text.encode("utf-8")).hexdigest()
            verdict = _screen_text(text, cfg, override_allow=_override_allows(
                self._overrides_for(conn), document_identity(conn, uri), digest))
            if verdict.decision != "allow":
                out.observation = {"withheld_by_screen": {
                    "decision": verdict.decision,
                    "rules": [f.get("rule") for f in verdict.fired],
                    "reason": verdict.rationale, "reverse": verdict.reverse}}
                return
            masked = verdict.masked_text
        if not text and withheld_rows:
            decisions = sorted({str(w["decision"]) for w in withheld_rows})
            out.observation = {"withheld_by_screen": {
                "decision": "/".join(decisions), "rules": sorted({str(r) for w in withheld_rows
                                                                  for r in w["rules"] if r}),
                "reason": "the whole result was withheld by the ingestion screen"}}
            return
        # R-021 #6: the capture store holds exactly what the planner may see. A SQL result is its
        # KEPT rows with every value masked; a file read keeps the source's own bytes only when the
        # screen masked nothing — otherwise the MASKED text is what is captured (never the original
        # bytes, never an unmasked .txt). The pointer then covers the captured (masked) text.
        if parts["verb"] == "query" or masked != text:
            text = masked
            raw = masked.encode("utf-8")
            mime = "application/json" if parts["verb"] == "query" else "text/plain"
        else:
            raw = res.data if res.data else raw_text.encode("utf-8")
            mime = res.mime
        sha, ptr = capture_live_read(connection=conn, uri=uri, text=text, data=raw,
                                     mime=mime, root=self.capture_root)
        ref = self.registry.add(ptr, masked, locator=uri)      # document_id is hashed (P4)
        out.pointers.append(ptr)
        shown = masked[:OBSERVATION_CHARS]
        obs = {"ref": ref, "source": conn, "capture_digest": sha, "text": shown}
        if parts["verb"] != "query" and self._shaped:
            obs["arguments_shaped"] = list(self._shaped)
        if len(masked) > len(shown):
            obs["truncated"] = f"showing {len(shown)} of {len(masked)} characters; the capture " \
                               f"holds all of them"
        if withheld_rows:
            decisions = sorted({str(w["decision"]) for w in withheld_rows})
            obs["withheld_by_screen"] = {
                "decision": "/".join(decisions), "rows": len(withheld_rows),
                "rules": sorted({str(r) for w in withheld_rows for r in w["rules"] if r}),
                "reason": f"{len(withheld_rows)} row(s) withheld by the ingestion screen"}
        if parts["verb"] == "query":
            behind = self._rows_behind(conn, sql, masked, ref, cfg)
            if behind:
                obs["rows_behind"] = behind
        out.observation = obs

    def _rows_behind(self, conn: str, sql: str, result_text: str, result_ref: str,
                     cfg: Any) -> Optional[dict]:
        """Q-05: for an aggregate over one table, read the rows each group was computed over
        (`query_rows.expansion_for` - the same table and condition, checked read-only, screened and
        captured like any live read), register each listed row as citable at ITS span of the
        capture, and attach their refs to the aggregate's evidence. None when nothing is mapped;
        a dict with ``not_mapped`` when a mapping was attempted and could not be made exactly."""
        from . import query_rows as qr                                  # noqa: PLC0415
        exp = qr.expansion_for(sql)
        if exp is None:
            return None
        try:
            result_rows = json.loads(result_text)
        except ValueError:
            return None
        if not isinstance(result_rows, list) or not result_rows or \
                not all(isinstance(r, dict) for r in result_rows):
            return None
        ok, why = sql_is_read_only(exp.sql)
        if not ok:
            return {"not_mapped": f"the rows behind this result were not read: {why}"}
        res = self.gateway.query(conn, exp.sql)
        if res.is_error:
            return {"not_mapped": "the rows behind this result could not be read: "
                                  f"{summarise_error(res.text or 'error')[:200]}"}
        _, masked, withheld = _screen_sql_result(res.text or "", cfg)
        try:
            rows = json.loads(masked) if masked else []
        except ValueError:
            return None
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            return None
        if len(rows) + len(withheld) > qr.MAX_ROWS:
            return {"not_mapped": f"more than {qr.MAX_ROWS} rows are behind this result; they are "
                                  f"not listed"}
        members = qr.group_members(result_rows, rows, exp.group_by)
        if members is None:
            return {"not_mapped": "a group column is not in the result under its own name, so "
                                  "its rows cannot be paired exactly"}
        text, spans = qr.row_spans(rows)
        uri = f"mcp://{conn}/sql?{_short_hash(exp.sql)}"
        sha, whole = capture_live_read(connection=conn, uri=uri, text=text,
                                       data=text.encode("utf-8"), mime="application/json",
                                       root=self.capture_root)
        key = qr.key_column(rows)
        refs: dict[int, str] = {}
        groups = []
        for g, idx in zip(result_rows, members):
            listed = []
            for j in idx[:qr.ROWS_PER_GROUP]:
                if j not in refs:
                    a, b = spans[j]
                    ptr = SourcePointer(source_id=conn, kind="doc_span",
                                        document_id=whole.document_id, char_start=a, char_end=b,
                                        capture_digest=sha, trust=whole.trust)
                    refs[j] = self.registry.add(ptr, text[a:b], locator=uri)
                listed.append({"ref": refs[j], **({key: rows[j][key]} if key else {})})
            groups.append({"group": {c: next((v for k, v in g.items()
                                               if str(k).lower() == c.lower()), None)
                                     for c in exp.group_by},
                           "row_count": len(idx), "rows": listed,
                           **({"rows_not_listed": len(idx) - len(listed)}
                              if len(idx) > len(listed) else {})})
        ev = self.registry._by_ref.get(result_ref)
        if ev is not None:
            ev.row_refs = tuple(dict.fromkeys((*ev.row_refs, *refs.values())))
        out = {"what": "the source rows each result row was computed over (same table and "
                       "condition); cite a row's ref to name it", "capture_digest": sha,
               "groups": groups}
        if withheld:
            out["withheld_by_screen"] = len(withheld)
        return out

    # the ingestion screen -----------------------------------------------------------------------
    def _screen(self) -> Any:
        if self._screen_cfg is None and self._screen_problem is None:
            try:
                from databridge_mcp_ingest.screen import load_screen   # noqa: PLC0415 - one screen
                self._screen_cfg = load_screen()
            except Exception as e:                           # noqa: BLE001 - fail closed
                self._screen_problem = f"{type(e).__name__}: {e}"
        return self._screen_cfg

    def _overrides_for(self, conn: str) -> Any:
        """Items an AUTHENTICATED owner approved out of the review band, each at the content
        digests it approved — ingestion's own `OverrideSet` (its `.allows(id, digest)`)."""
        if conn not in self._overrides:
            try:
                from databridge_mcp_ingest.screen import load_overrides   # noqa: PLC0415
                self._overrides[conn] = load_overrides(conn)
            except Exception:                                # noqa: BLE001 - none: stricter
                self._overrides[conn] = set()
        return self._overrides[conn]


#: A federated planner question id: ``fed:`` + the sha1 hex of the whitespace-collapsed question
#: (`databridge_automation.federated_question_id`).
_FED_QUESTION_ID = re.compile(r"^fed:[0-9a-f]{40}$")


def _delegate_answers(raw: Any, ask: Any) -> tuple[dict, list, Optional[str]]:
    """``(answers, ignored ids, why none can be passed)`` for a delegated sub-task. Only
    ``{fed:<sha1>: text}`` entries are answers; anything else is ignored and said. A federated
    service whose ``ask_delegate`` takes no ``answers`` gets none, and the planner is told."""
    if not isinstance(raw, dict) or not raw:
        return {}, [], None
    answers = {str(k): " ".join(str(v).split())[:2000] for k, v in raw.items()
               if _FED_QUESTION_ID.match(str(k)) and str(v or "").strip()}
    ignored = [str(k)[:80] for k in raw if str(k) not in answers]
    if not answers:
        return {}, ignored, None
    import inspect                                                      # noqa: PLC0415
    try:
        params = inspect.signature(ask).parameters
    except (TypeError, ValueError):
        params = {}
    takes = "answers" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD
                                       for p in params.values())
    return answers, ignored, (None if takes else
                              "this federated service does not take answers yet; the sub-task "
                              "was asked without them")


def _override_allows(overrides: Any, item_id: str, digest: Optional[str]) -> bool:
    """True only when an override covers THIS item at THIS content digest. A bare set of ids (no
    `.allows`) binds no content, so it admits nothing (fail closed)."""
    allows = getattr(overrides, "allows", None)
    return bool(callable(allows) and allows(item_id, digest))


def _screen_text(text: str, cfg: Any, *, override_allow: bool = False) -> Any:
    from databridge_mcp_ingest.screen import screen_text      # noqa: PLC0415 - imported, not copied
    return screen_text(text, cfg, override_allow=override_allow)


def _mask_value(value: Any, cfg: Any) -> Any:
    """A string value with the screen's redactions applied; other values unchanged."""
    if not isinstance(value, str):
        return value
    from databridge_mcp_ingest.screen import apply_masks      # noqa: PLC0415
    return apply_masks(value, cfg.redactions)[0]


#: A path written from the top of a tree: one or more ``/`` or ``\`` and then a relative rest.
#: A Windows drive path (``C:\…``) and a UNC / network path (``\\host``, ``//host``) are real
#: absolute addresses and never match.
_ROOTED = re.compile(r"^(?![\\/]{2})[\\/]+(?P<rest>[^:]*)$")


def _source_relative(args: dict, schema: Any) -> Optional[tuple[dict, str]]:
    """``(arguments, note)`` with the ONE string argument the tool's schema declares that is
    written from the top of a tree (``/``, ``/2026``) made relative to the source's root (``.``,
    ``2026``); None when there is no such argument or more than one (never guessed). Reads only
    the schema and the value: no tool, server or domain is named here (FIN-11)."""
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return None
    hits = [(k, _ROOTED.match(v)) for k, v in args.items()
            if isinstance(v, str) and k in props and "string" in _prop_types(props.get(k))]
    hits = [(k, m) for k, m in hits if m is not None]
    if len(hits) != 1:
        return None
    k, m = hits[0]
    rest = m.group("rest").strip().rstrip("\\/") or "."
    shaped = {**args, k: rest}
    return shaped, (f"'{k}' {args[k]!r} was refused by the source; it was sent again as "
                    f"{rest!r}, relative to the source's own root")


def _prop_types(prop: Any) -> list[str]:
    t = prop.get("type") if isinstance(prop, dict) else None
    return [t] if isinstance(t, str) else [x for x in (t or []) if isinstance(x, str)]


def _extract_text(data: bytes, *, name: str, mime: Optional[str],
                  source_id: str) -> tuple[Optional[str], str]:
    """Bytes -> text with INGESTION's extractor (`databridge_mcp_ingest.extract.extract_text`), so a
    live ``read_bytes`` reads a file exactly as ingestion would (E2E F-15)."""
    try:
        from databridge_mcp_ingest.extract import ExtractionFailed, extract_text  # noqa: PLC0415
    except Exception as e:                                   # noqa: BLE001
        return None, f"the ingestion extractor is not available ({type(e).__name__})"
    try:
        return extract_text(data, name=name, mime=mime, source_id=source_id).text, ""
    except ExtractionFailed as e:
        return None, str(e)
    except Exception as e:                                   # noqa: BLE001 - reported
        return None, f"{type(e).__name__}: {e}"


def _screen_sql_result(raw: str, cfg: Any) -> tuple[str, str, list[dict]]:
    """``(text, masked, withheld_rows)`` for one live SQL result.

    Rows are parsed with ingestion's safe reader (`tabular.pyrepr.loads_rows`: JSON, or the
    Python repr postgres-mcp answers with) and rendered CANONICALLY - ISO dates, plain decimals,
    exactly the digits the server wrote (E2E F-06). Each row then passes the screen as ingestion's
    `build.table_rows` screens it. A row is KEPT only when the verdict is ``allow``: ``block`` AND
    ``review`` rows are withheld and counted with their decision and rule ids, exactly as a
    document read withholds every verdict but ``allow`` (R-021 #9 - a row has no item id, so no
    owner override can lift it). Every string value of a kept row is masked with the screen's
    redactions. A result that is not rows is screened as text, under the same rule."""
    from databridge_mcp_ingest.screen import apply_masks        # noqa: PLC0415
    try:
        from databridge_mcp_ingest.tabular.pyrepr import loads_rows   # noqa: PLC0415
        rows = loads_rows(raw)
    except Exception:                                        # noqa: BLE001 - not rows: as text
        rows = None
    if rows is None:
        v = _screen_text(raw, cfg)
        if v.decision != "allow":
            return "", "", [{"row": None, "decision": v.decision,
                             "rules": [f.get("rule") for f in v.fired]}]
        return raw, v.masked_text, []
    kept: list[dict] = []
    masked_rows: list[dict] = []
    withheld: list[dict] = []
    for i, row in enumerate(rows):
        v = _screen_text(json.dumps(row, sort_keys=True, default=str), cfg)
        if v.decision != "allow":
            withheld.append({"row": i, "decision": v.decision,
                             "rules": [f.get("rule") for f in v.fired]})
            continue
        kept.append(row)
        masked_rows.append({k: (apply_masks(x, cfg.redactions)[0] if isinstance(x, str) else x)
                            for k, x in row.items()})
    return (json.dumps(kept, ensure_ascii=False, default=str),
            json.dumps(masked_rows, ensure_ascii=False, default=str), withheld)


def _short_hash(s: str) -> str:
    import hashlib                                                  # noqa: PLC0415
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


__all__ = ["pointer_id", "Evidence", "GatewayResult", "SourceGateway", "Retriever", "RetrievalModuleRetriever",
           "McpSourceGateway", "default_gateway", "default_retriever", "sql_is_read_only",
           "SQL_READ_FUNCTIONS", "classify_tools", "FactsStore", "McpFactsStore",
           "default_facts",
           "Evidence", "PointerRegistry", "ToolOutcome", "ToolLayer", "OBSERVATION_CHARS"]

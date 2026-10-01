"""Crawl plans — RULES FIRST (bundled YAML profiles matched by tool-name signature), MODEL SECOND.

A `CrawlPlan` is what a run executes: one or more `StepSpec`s, each a `documents`, `resources` or
`tabular` strategy written in the template/parser language of `parse.py`. It is saved as YAML next
to the connection (`<id>.plan.yaml`) with `status: proposed`; a run refuses anything not
`approved` — and only a named person approves (`approve_plan`); the connection's
`auto_approve` flag is read, ignored and reported in the plan's notes.

**Rules first (P16).** Every YAML file in `profiles/` (plus `WALNUT_CRAWL_PROFILES_DIR`) declares a
tool-name signature. A profile whose signature the server's `tools/list` satisfies contributes its
step. Profiles sharing a `group` are alternatives — the highest `priority` wins.

**Model second.** Only when no profile matches, the harness is asked (`PLAN_TASK_CLASS`) to map the
server's tools onto the same step shape from their JSON schemas. The draft is VALIDATED against the
actual tool list — an invented tool, a missing required argument, or a tool that writes is a
recorded problem, and a plan with problems cannot be approved.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import paths
from .client import ToolInfo
from .connections import SourceConnection, connection_path
from .parse import template_fields

#: The routed task class for model-proposed plans. `rule_proposal` is the existing class for
#: "propose a mapping for a human to approve" (config/models.yaml); a dedicated `plan` class is a
#: config change, not a code change.
PLAN_TASK_CLASS = "rule_proposal"

#: A tool whose name says it changes something is never part of a crawl, whoever proposed it.
#: (Duplicated verbatim in `databridge_automation.catalogue.WRITE_VERB`, and a test there keeps the
#: two equal — widen both together. The crawl gate uses the wider `writes_by_name` below.)
WRITE_VERB = re.compile(
    r"(?:^|[_\-.])(write|edit|create|delete|remove|move|rename|update|insert|drop|upload|send|"
    r"post|patch|put|set|append|truncate|alter|grant|revoke|kill|exec_write)(?:[_\-.]|$)", re.I)

#: Further verbs seen on real servers that write but pass WRITE_VERB — e.g. server-memory's
#: `add_observations` (no annotations at all). Used by the ingestion gate in addition to WRITE_VERB.
WRITE_VERB_EXTRA = re.compile(
    r"(?:^|[_\-.])(add|clear|reset|toggle|save|store|replace|import|merge|publish|submit|"
    r"archive|purge|destroy|restore|assign|invite|share|approve|reject|cancel|trigger)"
    r"(?:[_\-.]|$)", re.I)


_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def words_of(name: str) -> str:
    """`createGoogleDoc` -> `create_Google_Doc`. The verb patterns need a separator around the
    verb, so a camelCase name (`createFolder`, `deleteItem`, `shareFile`) is split first."""
    return _CAMEL.sub("_", name)


def writes_by_name(name: str) -> bool:
    words = words_of(name)
    return bool(WRITE_VERB.search(words) or WRITE_VERB_EXTRA.search(words))


def is_write_tool(tool: "ToolInfo") -> bool:
    """A tool is treated as able to change the source when its NAME says so, or its annotations
    do (`destructiveHint: true` or `readOnlyHint: false`). The name wins over a readOnlyHint of
    true: annotations are the server's claim, and the claim is untrusted (§4)."""
    return (writes_by_name(tool.name) or tool.destructive is True
            or tool.read_only is False)


# --- generic executors (FXJ3, Codex f2 #1) -------------------------------------------------------
#: A tool whose ARGUMENTS choose what it does (`execute_sql`, `query`, `run_command`, `eval`) is a
#: generic executor: no name, annotation or vouch can make it a read, because the next call's
#: argument decides. It is callable during a crawl ONLY as a tabular step's `sql` tool, and only
#: with a statement that passes `read_only_sql_refusal` (checked again on every call by
#: `client.ReadOnlyClient`).
_EXECUTOR_WORD = re.compile(
    r"(?:^|[_\-.])(exec|execute|executes|run|runs|query|queries|sql|eval|evaluate|shell|command|"
    r"cmd|script|statement|invoke)(?:[_\-.]|$)", re.I)
#: Argument names that carry a statement / program for an executor (checked at call time).
EXECUTOR_ARGS = frozenset({"sql", "query", "statement", "stmt", "q", "command", "cmd", "code",
                           "script", "expression", "program"})
#: The subset that, in a tool's SCHEMA, marks it an executor whatever its name (`query` / `q` are
#: left out: a search tool's search text is commonly called that).
_EXECUTOR_SCHEMA_ARGS = EXECUTOR_ARGS - {"query", "q"}


def is_generic_executor(tool: Any) -> bool:
    """True for a tool whose arguments choose what it does: by NAME (`execute_sql`, `runQuery`,
    `query`) or by SCHEMA (it takes a `sql` / `statement` / `command` / `code` ... argument)."""
    name = tool if isinstance(tool, str) else getattr(tool, "name", "")
    if _EXECUTOR_WORD.search(words_of(str(name))):
        return True
    props = ((getattr(tool, "input_schema", None) or {}).get("properties") or {}) \
        if not isinstance(tool, str) else {}
    return any(str(k).lower() in _EXECUTOR_SCHEMA_ARGS for k in props)


#: The CLOSED grammar a crawl's SQL must fit. A statement is ONE `SELECT` / `WITH … SELECT` /
#: `SHOW <name>`, or exactly the session guard that makes the session READ ONLY. Function calls
#: are limited to this closed list of side-effect-free functions — a user-defined function or a
#: stored procedure can write, so an unknown function is refused. Every bundled dialect template
#: is held to it by a test (`test_fxj3_sources.py`), so a new dialect that needs a function adds
#: it HERE, on purpose, in review.
READ_ONLY_SQL_FUNCTIONS = frozenset({
    "count", "count_big", "sum", "avg", "min", "max", "cast", "length", "len", "char_length",
    "replace", "replaceall", "lengthutf8", "nullif", "coalesce", "lower", "upper", "trim", "unnest",
    "tostring", "uniqexact", "nullable", "lowcardinality", "nvarchar", "varchar", "char",
    "schema_name", "version", "sqlite_version", "current_version", "current_schema",
    "current_database", "pragma_table_info", "pragma_foreign_key_list", "duckdb_constraints",
    "duckdb_tables", "str", "string", "text", "abs", "round", "substr", "substring",
    "object_id", "object_name", "typeof", "format_type", "array_length", "getsetting"})
#: Words that never appear in a read: writes, DDL, privileges, session/transaction control,
#: locking, procedure calls, external I/O.
_SQL_FORBIDDEN = frozenset({
    "insert", "update", "delete", "merge", "upsert", "drop", "create", "alter", "truncate",
    "rename", "grant", "revoke", "copy", "call", "exec", "execute", "do", "lock", "vacuum",
    "analyze", "analyse", "reindex", "cluster", "comment", "refresh", "import", "export",
    "attach", "detach", "load", "install", "into", "outfile", "dumpfile", "listen", "notify",
    "unlisten", "prepare", "deallocate", "discard", "reset", "set", "begin", "commit",
    "rollback", "savepoint", "release", "declare", "move", "close", "checkpoint",
    "returning", "handler", "pragma", "use", "kill", "shutdown", "optimize", "repair",
    "replace_into", "security", "owner", "procedure", "function", "trigger", "sequence"})
#: Words that may precede `(` without being a function call.
_SQL_PAREN_KEYWORDS = frozenset({
    "in", "exists", "as", "on", "and", "or", "not", "over", "filter", "within", "lateral",
    "from", "join", "where", "select", "values", "using", "when", "then", "else", "case",
    "by", "having", "is", "like", "between", "all", "any", "some", "with", "distinct",
    "union", "except", "intersect", "ordinality", "partition", "limit", "offset", "group",
    "top"})
#: The one statement that is not a SELECT but only NARROWS the session (a dialect's
#: `read_only_session.sql`), compared after whitespace/case normalisation.
_READ_ONLY_SESSION_SQL = frozenset({
    "set session characteristics as transaction read only",
    "set transaction read only", "set session transaction read only",
    "set default_transaction_read_only = on",
    "pragma query_only = on", "pragma query_only = 1", "pragma query_only = true",
    "set readonly = 1"})                    # ClickHouse (FXJ4 cross-track)


def _strip_sql(sql: str) -> Optional[str]:
    """Comments removed, string literals and quoted identifiers replaced by a neutral token. None
    for something the closed grammar cannot read (an unterminated quote, dollar quoting)."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            if j < 0:
                return None
            i = j + 2
            out.append(" ")
            continue
        if c == "$":
            return None                     # dollar-quoting / positional params: not this grammar
        if c in "'\"`[":
            close = {"'": "'", '"': '"', "`": "`", "[": "]"}[c]
            j = i + 1
            while True:
                k = sql.find(close, j)
                if k < 0:
                    return None
                if close in "'\"" and sql.startswith(close * 2, k):
                    j = k + 2               # doubled quote = an escaped quote
                    continue
                break
            out.append(" lit " if c == "'" else " ident ")
            i = k + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def read_only_sql_refusal(sql: Any) -> Optional[str]:
    """None when `sql` fits the closed read-only grammar above, else WHY not (plain words). It
    is a CLOSED allow-list, so it is strict on purpose: anything it cannot prove is a read is
    refused."""
    if not isinstance(sql, str) or not sql.strip():
        return "the statement is empty or not text"
    norm = " ".join(sql.strip().rstrip(";").split()).lower()
    if norm in _READ_ONLY_SESSION_SQL:
        return None
    body = _strip_sql(sql)
    if body is None:
        return "the statement has an unterminated quote/comment or dollar quoting"
    body = body.strip()
    if body.endswith(";"):
        body = body[:-1]
    if ";" in body:
        return "more than one statement"
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", body)
    if not words:
        return "the statement has no keyword"
    first = words[0].lower()
    if first == "show":
        if len(words) == 2 and re.fullmatch(r"\s*show\s+[A-Za-z_][A-Za-z0-9_]*\s*", body, re.I):
            return None
        return "only `SHOW <setting>` is a read"
    if first not in ("select", "with"):
        return f"a crawl statement must be a SELECT (it starts with {words[0]!r})"
    if "select" not in (w.lower() for w in words):
        return "a WITH statement must end in a SELECT"
    bad = sorted({w.lower() for w in words if w.lower() in _SQL_FORBIDDEN})
    if bad:
        return f"the statement uses {bad}, which a read never needs"
    for m in re.finditer(r"([A-Za-z_][A-Za-z0-9_$]*)(\s*\.\s*[A-Za-z_][A-Za-z0-9_$]*)*\s*\(",
                         body):
        call = m.group(0)
        fn = re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", call)[-1].lower()
        before = body[:m.start()].rstrip()
        prev = re.findall(r"[A-Za-z_][A-Za-z0-9_$]*$", before)
        if "." not in call and fn in _SQL_PAREN_KEYWORDS:
            continue
        if prev and prev[0].lower() == "as":
            continue                        # `AS k(attnum, ord)`: a column alias list
        if fn not in READ_ONLY_SQL_FUNCTIONS:
            return (f"the statement calls {fn!r}, which is not on the closed list of "
                    f"side-effect-free functions (a function or procedure can write)")
    return None


class PlanError(ValueError):
    pass


class ApprovalRefused(PlanError):
    """An approval offered by something that is not an authenticated principal (a bare name, a
    dict, an edited YAML field, a hand-built `Approver`). Plain words; never echoes a secret."""


def require_authenticated_approver(obj: Any, *, what: str = "approval") -> dict:
    """The SHARED approval gate for mcp_ingest (plans, mappings, dialects, screen overrides,
    holds). Returns ``{principal_id, authenticated_by, principal_type, dev_only}`` for an
    AUTHENTICATED principal, else raises `ApprovalRefused` (fail closed).

    Authenticated means exactly one of:
      * an agent_runtime `Approver` for which `is_issued_principal()` is true (its seal was
        minted by a registered `Authenticator`); or
      * an instance of a type the application's authentication layer registered through
        adapter_agents `trust_principal_type` (the gateway's principal object).
    A string, a dict, ``None`` or any object that merely carries a ``principal_id`` is a claim,
    not an authentication. Machine actors are refused (a person decides).

    Both packages are imported lazily (inside this call), so importing `plans` never cycles
    through adapter_agents / agent_runtime, and either being absent narrows — never widens — what
    is accepted."""
    if obj is None or isinstance(obj, (str, bytes, dict)):
        raise ApprovalRefused(
            f"{what} REFUSED: a {type(obj).__name__} is not an authenticated principal; pass the "
            f"principal the application's authentication layer issued (hard constraint 4)")
    try:
        from databridge_adapter_agents.adapt import (AdaptationRefused,  # noqa: PLC0415
                                                     principal_of)
    except Exception:                                                # noqa: BLE001
        principal_of = None                                          # type: ignore[assignment]
        AdaptationRefused = ()                                       # type: ignore[assignment]
    if principal_of is not None:
        try:
            got = principal_of(obj, what)
        except AdaptationRefused as exc:                             # type: ignore[misc]
            raise ApprovalRefused(str(exc)) from None
    else:
        # adapter_agents absent: only a sealed agent_runtime Approver can be verified here.
        try:
            from databridge_agent_runtime import is_issued_principal  # noqa: PLC0415
        except Exception:                                            # noqa: BLE001
            raise ApprovalRefused(f"{what} REFUSED: no authentication layer is available to "
                                  f"verify the principal") from None
        if not is_issued_principal(obj):
            raise ApprovalRefused(f"{what} REFUSED: this principal was not issued by a "
                                  f"registered authenticator")
        pid = str(getattr(obj, "principal_id", "") or "").strip()
        if not pid:
            raise ApprovalRefused(f"{what} REFUSED: the principal carries no principal_id")
        got = {"principal_id": pid, "authenticated_by": str(obj.authenticated_by),
               "principal_type": type(obj).__name__}
    got["dev_only"] = bool(getattr(obj, "dev_only", False))
    return got


def require_delegated_approver(obj: Any, *, what: str = "delegated approval") -> dict:
    """The gate for a DELEGATED approval (INT3): an adapter agent's `auto_additive` policy, which
    the principal who ACTIVATED the agent delegated. Returns ``{principal_id: <the delegator>,
    authenticated_by: "delegated:<policy digest>", principal_type, delegated_by, policy_digest,
    agent_id, adaptation_id, scope, dev_only}``, else raises `ApprovalRefused`.

    Only an adapter_agents `DelegatedApproval` that module MINTED passes (`delegation_of`). It is
    deliberately NOT accepted by `require_authenticated_approver`, so a delegation can never
    approve a crawl plan, a dialect, a screen override or a hold. The one caller that takes it is
    `mapping.approve_delegated_additive`, which additionally proves the change additive."""
    if obj is None or isinstance(obj, (str, bytes, dict)):
        raise ApprovalRefused(f"{what} REFUSED: a {type(obj).__name__} is not a delegation")
    try:
        from databridge_adapter_agents.adapt import (AdaptationRefused,  # noqa: PLC0415
                                                     delegation_of)
    except Exception:                                                # noqa: BLE001
        raise ApprovalRefused(f"{what} REFUSED: no adapter-agent delegation layer is available "
                              f"to verify it") from None
    try:
        return delegation_of(obj, what)
    except AdaptationRefused as exc:
        raise ApprovalRefused(str(exc)) from None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CallSpec(_Strict):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    parse: dict[str, Any] = Field(default_factory=lambda: {"mode": "lines"})


class PageSpec(_Strict):
    """How a listing tool pages. After each call `regex` is searched (multiline) in its output; a
    match's `token` group is passed as argument `arg` on the next call, until no match. A repeated
    token or more than `max_pages` pages is an ERROR (recorded; the enumeration is incomplete) —
    never a silent truncation (trap 1)."""
    arg: str
    regex: str
    max_pages: int = 1000


class ListSpec(CallSpec):
    recurse: bool = True
    dir_values: list[str] = Field(default_factory=lambda: ["dir", "directory", "folder"])
    root_arg_default: Optional[str] = None
    """Root to list when neither scope.roots nor a roots step supplies one (e.g. "" or "/")."""
    item_mime: Optional[str] = None
    """Mime for listed items whose name has no extension and whose listing gives no mime (e.g. a
    knowledge-graph entity read back as JSON). NOTE: honoured once `crawl.enumerate_documents`
    falls back to it (`entry mime or step.list.item_mime`) — until then such items are ledgered
    `excluded: no_extractor`, which is recorded, not silent."""
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


class SqlSpec(_Strict):
    tool: str
    arg: str = "sql"
    dialect: str = "information_schema"
    parse: dict[str, Any] = Field(default_factory=lambda: {"mode": "json_rows"})
    extra_args: dict[str, Any] = Field(default_factory=dict)


class StepSpec(_Strict):
    id: str
    strategy: Literal["documents", "resources", "tabular"]
    roots: Optional[CallSpec] = None
    list: Optional[ListSpec] = None
    read: Optional[ReadSpec] = None
    uri_prefix: Optional[str] = None
    sql: Optional[SqlSpec] = None

    def tools(self) -> list[str]:
        names = []
        for spec in (self.roots, self.list):
            if spec is not None:
                names.append(spec.tool)
        if self.read is not None:
            names.extend(t for t in (self.read.text_tool, self.read.binary_tool) if t)
        if self.sql is not None:
            names.append(self.sql.tool)
        return names


class Profile(_Strict):
    id: str
    title: str = ""
    description: str = ""
    group: Optional[str] = None
    priority: int = 0
    verified: str = ""
    """How this profile's output formats were confirmed (e.g. "read from the published package
    2026.8.31"), or empty when they were NOT — a matcher is only as good as its evidence."""
    match: dict[str, Any] = Field(default_factory=dict)
    step: dict[str, Any]

    @property
    def deny_tools(self) -> list[str]:
        """Tools this server offers that an ingestion connection must never call
        (`match.deny_tools`). Enforced: plan validation refuses them, and `ReadOnlyClient`
        refuses them at call time."""
        return [str(t) for t in (self.match.get("deny_tools") or [])]

    @property
    def fallback(self) -> bool:
        """A generic profile (`match.fallback: true`) applies only when no specific one did."""
        return bool(self.match.get("fallback"))

    def matches(self, tool_names: set[str], has_resources: bool) -> bool:
        need = set(self.match.get("all_tools") or [])
        anyof = set(self.match.get("any_tools") or [])
        if not need and not anyof and not self.match.get("resources"):
            return False
        if not need <= tool_names:
            return False
        if anyof and not (anyof & tool_names):
            return False
        if self.match.get("resources") and not has_resources:
            return False
        return True


class CrawlPlan(_Strict):
    source_id: str
    status: Literal["proposed", "approved"] = "proposed"
    origin: Literal["rules", "model", "manual"] = "rules"
    profiles: list[str] = Field(default_factory=list)
    steps: list[StepSpec] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    tools_seen: list[str] = Field(default_factory=list)
    tools_digest: str = ""
    step_tool_digests: dict[str, str] = Field(default_factory=dict)
    """`tool -> tool_digest` for every tool the steps call, as listed when the plan was proposed
    and re-taken from the live server at approval (FXJ3, Codex f2 #2). `ReadOnlyClient` re-lists
    the live tools before its first call and refuses a step tool whose description, schema or
    annotations no longer match — a tool the server changed after approval is not the tool that
    was approved. (Per tool, so a tool ADDED elsewhere on the server does not stop the crawl.)"""
    tool_classes: dict[str, str] = Field(default_factory=dict)
    """`tool -> "read:<basis>" | "write:<basis>" | "unclassified:<why>"` for every tool the steps
    call, classified against the LIVE tool list when the plan was proposed (`classify_plan_tools`).
    `approve_plan` refuses any step tool that is not `read:` here — fail closed (Codex round 3)."""
    deny_tools: list[str] = Field(default_factory=list)
    """Declared never-call tools (from the matched profiles' `match.deny_tools`)."""
    pagination: list[str] = Field(default_factory=list)
    """Listing tools that page (by schema or by a next-cursor seen in the sample call)."""
    probe: dict[str, Any] = Field(default_factory=dict)
    """What the validation sample call saw (model-proposed and generic plans)."""
    created_at: str = ""
    approved_at: Optional[str] = None
    approved_by: Optional[str] = None
    approval: dict[str, Any] = Field(default_factory=dict)
    """The AUTHENTICATED approval (FXI3, Codex f1 #1): `{principal_id, authenticated_by,
    principal_type, dev_only, at, plan_digest, tools_digest}`. `approved_by` is only its
    principal id, kept for readers; the proof is this record AND its twin in the append-only
    `<id>.plan.approvals.jsonl` (`verify_plan_approval`)."""


# --- profiles -----------------------------------------------------------------------------------
def load_profiles(extra_dir: Optional[Path] = None) -> list[Profile]:
    dirs = [paths.bundled_profiles_dir()]
    extra = extra_dir or paths.extra_profiles_dir()
    if extra is not None:
        dirs.append(extra)
    out: list[Profile] = []
    for d in dirs:
        if not d.is_dir():
            continue
        # One directory per source family (`profiles/<source>/`), each owned by one workstream.
        # Profiles end `.profile.yaml`; SQL dialects beside them end `.sql.yaml`.
        for f in sorted(d.rglob("*.profile.yaml")):
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            try:
                prof = Profile.model_validate(data)
                StepSpec.model_validate({"id": prof.id, **prof.step})
            except ValidationError as exc:
                raise PlanError(f"crawl profile {f} is malformed: {exc}") from exc
            out.append(prof)
    return out


def sql_dialect(name: str) -> dict:
    """A named set of SQL templates (`<name>.sql.yaml` anywhere under a profiles directory)."""
    dirs = [paths.bundled_profiles_dir()]
    extra = paths.extra_profiles_dir()
    if extra is not None:
        dirs.append(extra)
    for d in dirs:
        hits = sorted(d.rglob(f"{name}.sql.yaml")) if d.is_dir() else []
        if hits:
            return yaml.safe_load(hits[0].read_text(encoding="utf-8")) or {}
    raise PlanError(f"unknown SQL dialect {name!r} (no {name}.sql.yaml under the profiles dirs)")


def match_profiles(tools: list[ToolInfo], has_resources: bool,
                   profiles: Optional[list[Profile]] = None) -> list[Profile]:
    names = {t.name for t in tools}
    matched = [p for p in (profiles if profiles is not None else load_profiles())
               if p.matches(names, has_resources)]
    specific = [p for p in matched if not p.fallback]
    matched = specific or matched
    best: dict[str, Profile] = {}
    loose: list[Profile] = []
    for p in matched:
        if p.group is None:
            loose.append(p)
        elif p.group not in best or p.priority > best[p.group].priority:
            best[p.group] = p
    return sorted(loose + list(best.values()), key=lambda p: p.id)


# --- validation ---------------------------------------------------------------------------------
_PAGE_ARG = re.compile(r"^(cursor|page|pages?_?token|next_?(page_?)?(token|cursor)|offset|after|"
                       r"continuation(_?token)?|start_?cursor|page_?(number|num|index))$", re.I)


def _parse_fields(parse: dict) -> Optional[set[str]]:
    """Fields a listing parser yields per entry, or None when it cannot be known statically."""
    mode = (parse or {}).get("mode", "lines")
    if mode == "lines":
        out: set[str] = set()
        for pat in parse.get("patterns") or []:
            try:
                out |= set(re.compile(pat.get("regex", "")).groupindex)
            except re.error:
                continue
            out |= set((pat.get("set") or {}).keys())
        return out
    if mode == "json":
        fields = parse.get("fields")
        return set(fields) if fields else None
    if mode == "tree":
        return {"name", "rel", "type"}
    return None


def _schema_type(schema: dict) -> set[str]:
    t = (schema or {}).get("type")
    if isinstance(t, list):
        return set(t)
    return {t} if t else set()


def _arg_problems(step_id: str, tool: str, args: dict, info: ToolInfo,
                  available: Optional[set[str]]) -> list[str]:
    problems: list[str] = []
    schema = info.input_schema or {}
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    missing = sorted(required - set(args))
    if missing:
        problems.append(f"step {step_id!r}: tool {tool!r} requires {missing}, which the plan does "
                        f"not supply")
    if props and schema.get("additionalProperties") is not True:
        unknown = sorted(set(args) - set(props))
        if unknown:
            problems.append(f"step {step_id!r}: tool {tool!r} has no argument(s) {unknown} "
                            f"(its schema offers {sorted(props)})")
    for name, value in args.items():
        want = _schema_type(props.get(name) or {})
        if "array" in want and not isinstance(value, list):
            problems.append(f"step {step_id!r}: {tool}.{name} must be an array; the plan passes "
                            f"{value!r} (write it as [{value!r}])")
        elif want and want <= {"object"} and not isinstance(value, dict):
            problems.append(f"step {step_id!r}: {tool}.{name} must be an object; the plan passes "
                            f"{value!r}")
    if available is not None:
        unknown_fields = sorted(template_fields(args) - available)
        if unknown_fields:
            problems.append(f"step {step_id!r}: tool {tool!r} arguments use {unknown_fields}, "
                            f"which the listing never provides (it provides {sorted(available)})")
    return problems


def validate_steps(steps: list[StepSpec], tools: list[ToolInfo], *,
                   has_resources: bool, supports_resources: bool = True,
                   deny_tools: Optional[list[str]] = None) -> list[str]:
    """Problems that would make a plan unsafe or unrunnable. Empty list ⇒ approvable.

    Checked statically against the LIVE tool list: every tool exists; none can write (by name or
    annotation); every required argument is supplied; no argument the schema does not offer;
    array/object arguments are shaped as such; every `{field}` a template uses is one the step
    actually provides (roots → list: {path, id}; list → read: the parser's fields + path/id/name).
    The live sample call (`probe_plan`) checks the rest."""
    by_name = {t.name: t for t in tools}
    problems: list[str] = []
    if not steps:
        problems.append("the plan has no steps")
    for step in steps:
        for tool in step.tools():
            info = by_name.get(tool)
            if info is None:
                problems.append(f"step {step.id!r}: tool {tool!r} is not offered by this server")
                continue
            if is_write_tool(info) or tool in set(deny_tools or ()):
                problems.append(f"step {step.id!r}: tool {tool!r} can change the source; a crawl "
                                f"only reads (refused)")
        item_fields: Optional[set[str]] = None
        if step.list is not None:
            parsed = _parse_fields(step.list.parse)
            item_fields = None if parsed is None else parsed | {"path", "id", "name"}
        calls: list[tuple[str, dict, Optional[set[str]]]] = []
        if step.roots:
            calls.append((step.roots.tool, step.roots.args, set()))
        if step.list:
            calls.append((step.list.tool, step.list.args, {"path", "id"}))
        if step.read:
            if step.read.text_tool:
                calls.append((step.read.text_tool, step.read.text_args, item_fields))
            if step.read.binary_tool:
                calls.append((step.read.binary_tool, step.read.binary_args, item_fields))
            if step.read.resource_uri and item_fields is not None:
                unknown = sorted(template_fields(step.read.resource_uri) - item_fields)
                if unknown:
                    problems.append(f"step {step.id!r}: read.resource_uri uses {unknown}, which "
                                    f"the listing never provides")
        if step.sql:
            calls.append((step.sql.tool, {step.sql.arg: "", **step.sql.extra_args}, None))
        for tool, args, available in calls:
            info = by_name.get(tool)
            if info is None:
                continue
            problems.extend(_arg_problems(step.id, tool, args, info, available))
        if step.strategy == "documents":
            if step.list is None or step.read is None or not (step.read.text_tool
                                                               or step.read.binary_tool
                                                               or step.read.resource_uri):
                problems.append(f"step {step.id!r}: a documents step needs list + read tools")
            if step.read is not None and step.read.resource_uri and not supports_resources:
                problems.append(f"step {step.id!r}: reads items as resources, which this route "
                                f"cannot do — connect the server directly (via: direct)")
        elif step.strategy == "resources":
            if not supports_resources:
                problems.append(f"step {step.id!r}: this route cannot list resources "
                                f"(MCPJungle proxies tools only)")
            elif not has_resources:
                problems.append(f"step {step.id!r}: the server lists no resources")
        elif step.strategy == "tabular":
            if step.sql is None:
                problems.append(f"step {step.id!r}: a tabular step needs an sql block")
            else:
                try:
                    sql_dialect(step.sql.dialect)
                except PlanError as exc:
                    problems.append(f"step {step.id!r}: {exc}")
    return problems


def pagination_params(steps: list[StepSpec], tools: list[ToolInfo]) -> list[str]:
    """`tool.param` for every listing tool whose schema takes a page cursor the plan never sets —
    the plan reads the FIRST page only, which is partial coverage (P7) and must be said."""
    by_name = {t.name: t for t in tools}
    out = []
    for step in steps:
        for spec in (step.roots, step.list):
            if spec is None or spec.tool not in by_name:
                continue
            props = (by_name[spec.tool].input_schema or {}).get("properties") or {}
            followed = {spec.page.arg} if getattr(spec, "page", None) is not None else set()
            out.extend(f"{spec.tool}.{p}" for p in props
                       if _PAGE_ARG.match(p) and p not in spec.args and p not in followed)
    return sorted(set(out))


# --- the model path -----------------------------------------------------------------------------
class DraftStep(BaseModel):
    strategy: Literal["documents", "resources", "tabular"]
    list_tool: Optional[str] = None
    list_args: dict[str, Any] = Field(default_factory=dict)
    list_parse_mode: Literal["lines", "json", "tree"] = "lines"
    line_regex: Optional[str] = None
    items_path: str = ""
    name_field: str = "name"
    id_field: Optional[str] = None
    type_field: Optional[str] = None
    dir_value: Optional[str] = None
    recurse: bool = False
    root: Optional[str] = None
    """What to pass as {path} to the first listing call when the connection declares no roots.
    Left empty for a flat listing tool that takes no path."""
    item_mime: Optional[str] = None
    read_tool: Optional[str] = None
    read_args: dict[str, Any] = Field(default_factory=dict)
    binary_read_tool: Optional[str] = None
    binary_read_args: dict[str, Any] = Field(default_factory=dict)
    sql_tool: Optional[str] = None
    sql_arg: Optional[str] = None
    rationale: str = ""


class PlanDraft(BaseModel):
    steps: list[DraftStep] = Field(default_factory=list)


def _draft_to_step(i: int, d: DraftStep) -> StepSpec:
    sid = f"model-{i}"
    if d.strategy == "resources":
        return StepSpec(id=sid, strategy="resources")
    if d.strategy == "tabular":
        return StepSpec(id=sid, strategy="tabular",
                        sql=SqlSpec(tool=d.sql_tool or "", arg=d.sql_arg or "sql"))
    if d.list_parse_mode == "lines":
        regex = d.line_regex or r"^(?P<name>.+)$"
        parse = {"mode": "lines", "patterns": [{"regex": regex}]}
    elif d.list_parse_mode == "tree":
        parse = {"mode": "tree"}
    else:
        fields = {"name": d.name_field}
        if d.id_field:
            fields["id"] = d.id_field
        if d.type_field:
            fields["type"] = d.type_field
        parse = {"mode": "json", "items_path": d.items_path, "fields": fields}
    root = d.root
    if root is None and not template_fields(d.list_args):
        root = ""                    # a flat listing: one call, no path to walk from
    return StepSpec(
        id=sid, strategy="documents",
        list=ListSpec(tool=d.list_tool or "", args=dict(d.list_args), parse=parse,
                      recurse=d.recurse, root_arg_default=root, item_mime=d.item_mime,
                      dir_values=[d.dir_value] if d.dir_value else ["dir", "directory", "folder"]),
        read=ReadSpec(text_tool=d.read_tool, text_args=dict(d.read_args),
                      binary_tool=d.binary_read_tool, binary_args=dict(d.binary_read_args)))


_MIME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}/[A-Za-z0-9][A-Za-z0-9.+_-]{0,95}$")


def planner_catalogue(tools: list[ToolInfo], resources: Optional[list] = None, *,
                      screen=None) -> tuple[list[dict], dict, list[str]]:
    """What the PLANNER model may see, screened first (Codex f1 #4; the shared rule: untrusted
    source metadata is screened by `screen.py` before any model sees it).

    * a tool that can write is not offered (it cannot pick what it never sees);
    * every other tool's name + description + schema is screened as ONE text by the deployment's
      screen (`screen.load_screen` / `screen_text`): `block` OR `review` withholds the tool from
      the model entirely (review-band content never reaches a model); `allow` passes the MASKED
      description;
    * resources are summarised WITHOUT identifiers: a count and the well-formed mime types. No
      uri or name reaches the model, so a blocked path (`blocked/payroll/…`) cannot either.
    Returns (catalogue, resource_summary, notes) — every withheld tool is named in `notes` with
    the rules that fired (recorded on the plan), never silently dropped."""
    from .screen import load_screen, screen_text

    cfg = screen if screen is not None else load_screen()
    catalogue: list[dict] = []
    notes: list[str] = []
    for t in tools:
        label = repr(t.name[:80])
        if is_write_tool(t):
            # FXJ3 (Codex f2 #9): recorded, not silently dropped
            notes.append(f"tool {label} withheld from the planner model: it can change the "
                         f"source (by name or annotation)")
            continue
        text = "\n".join([t.name, t.description or "",
                          json.dumps(t.input_schema or {}, sort_keys=True, default=str)])
        res = screen_text(text, cfg)
        if res.decision != "allow":
            notes.append(f"tool {label} withheld from the planner model: screen "
                         f"{res.decision} (rules {[f['rule'] for f in res.fired]})")
            continue
        # FXJ3 (Codex f2 #9): every field is screened ON ITS OWN and only the MASKED output is
        # sent — the description, and every key and string inside the schema (descriptions,
        # defaults, enums, examples). A name or schema KEY the screen would change cannot be
        # masked without breaking the plan, so that tool is withheld instead.
        name_res = screen_text(t.name, cfg)
        desc = screen_text(t.description or "", cfg)
        schema, schema_fired, keys_changed = _screen_json(t.input_schema or {}, cfg, screen_text)
        fired = [f["rule"] for r in (name_res, desc) for f in r.fired] + schema_fired
        if name_res.decision != "allow" or desc.decision != "allow" or keys_changed \
                or name_res.masked_text != t.name or schema is None:
            notes.append(f"tool {label} withheld from the planner model: its name, description "
                         f"or schema carries screened content (rules {sorted(set(fired))})")
            continue
        catalogue.append({"name": t.name, "description": desc.masked_text[:400],
                          "input_schema": schema,
                          "annotations": {"readOnlyHint": t.read_only,
                                          "destructiveHint": t.destructive}})
    mimes: set[str] = set()
    dropped = 0
    for r in resources or []:
        m = getattr(r, "mime", None)
        if not m:
            continue
        m = str(m)
        got = screen_text(m, cfg)
        if _MIME_RE.match(m) and got.decision == "allow" and got.masked_text == m:
            mimes.add(m)
        else:
            dropped += 1
    if dropped:
        notes.append(f"{dropped} resource mime type(s) withheld from the planner model "
                     f"(malformed or screened)")
    summary = {"count": len(resources or []), "mime_types": sorted(mimes)[:20]}
    return catalogue, summary, notes


def _screen_json(value: Any, cfg, screen_text) -> tuple[Any, list[str], bool]:
    """(masked copy, rules fired, a KEY changed) — every string value replaced by its masked
    text; a value the screen does not `allow` makes the whole schema `None` (withheld)."""
    fired: list[str] = []
    keys_changed = False

    def walk(v):
        nonlocal keys_changed
        if isinstance(v, dict):
            out = {}
            for k, x in v.items():
                kr = screen_text(str(k), cfg)
                fired.extend(f["rule"] for f in kr.fired)
                if kr.decision != "allow" or kr.masked_text != str(k):
                    keys_changed = True
                out[k] = walk(x)
            return out
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, str):
            r = screen_text(v, cfg)
            fired.extend(f["rule"] for f in r.fired)
            if r.decision != "allow":
                raise _Withheld()
            return r.masked_text
        return v

    try:
        return walk(value), fired, keys_changed
    except _Withheld:
        return None, fired, keys_changed


class _Withheld(Exception):
    pass


def _model_plan(tools: list[ToolInfo], has_resources: bool, harness,
                resources: Optional[list] = None) -> tuple[list[StepSpec], list[str]]:
    catalogue, resource_summary, screened_notes = planner_catalogue(tools, resources)
    system = {"role": "system", "content": (
        "You plan a READ-ONLY crawl of an MCP server so its content can be ingested. Use ONLY the "
        "tools listed. Never choose a tool that writes, edits, moves or deletes. Return steps:\n"
        "- strategy 'documents': a list tool that enumerates items (list_args may use {path}), how "
        "to parse its text output (lines with a regex having named groups name/id/type, json with "
        "items_path + field names, or tree), and a read tool whose args use {path}, {id} or "
        "{name} (an array argument is written as a list, e.g. [\"{name}\"]). Set item_mime when "
        "items have no file extension (e.g. application/json). Set root to '' for a flat listing "
        "tool that takes no path.\n"
        "- strategy 'resources': when the server exposes MCP resources.\n"
        "- strategy 'tabular': when a tool runs SQL; give sql_tool and the argument name sql_arg.\n"
        "Explain each step in `rationale`.")}
    user = {"role": "user", "content": json.dumps(
        {"tools": catalogue, "server_lists_resources": has_resources,
         "resources": resource_summary}, sort_keys=True)}
    result = harness.complete_structured(PLAN_TASK_CLASS, [system, user], PlanDraft, seed=7)
    draft: PlanDraft = result.parsed
    steps, notes = [], list(screened_notes)
    for i, d in enumerate(draft.steps):
        try:
            steps.append(_draft_to_step(i, d))
            if d.rationale:
                notes.append(f"model-{i}: {d.rationale}")
        except ValidationError as exc:
            notes.append(f"model-{i}: draft step could not be expressed ({exc.errors()[0]['msg']})")
    return steps, notes


# --- persistence --------------------------------------------------------------------------------
def plan_path(source_id: str, root: Optional[Path] = None) -> Path:
    return connection_path(source_id, root).with_name(f"{source_id}.plan.yaml")


def save_plan(plan: CrawlPlan, root: Optional[Path] = None) -> Path:
    p = plan_path(plan.source_id, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(plan.model_dump(mode="json"), sort_keys=False), encoding="utf-8")
    return p


def load_plan(source_id: str, root: Optional[Path] = None) -> CrawlPlan:
    p = plan_path(source_id, root)
    if not p.is_file():
        raise PlanError(f"no plan for source {source_id!r}; propose one first")
    return CrawlPlan.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")) or {})


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def tools_digest(tools: list[ToolInfo]) -> str:
    """Digest of EVERYTHING a plan decision rests on (Codex f1 #3): each tool's name, description,
    input schema and both annotations. A server that flips `readOnlyHint`, or rewrites a
    description the planner read, changes the digest and so invalidates an approval."""
    blob = json.dumps(sorted([t.name, t.description, t.input_schema, t.read_only, t.destructive]
                             for t in tools), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def tool_digest(t: ToolInfo) -> str:
    """Digest of ONE tool (name, description, schema, both annotations) — `step_tool_digests`."""
    blob = json.dumps([t.name, t.description, t.input_schema, t.read_only, t.destructive],
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def step_tool_digests(steps: list[StepSpec], tools: list[ToolInfo]) -> dict[str, str]:
    by_name = {t.name: t for t in tools}
    return {name: tool_digest(by_name[name]) for step in steps for name in step.tools()
            if name in by_name}


def _shared_classify():
    """The ONE read/write rule (`databridge_agent_runtime.classify.classify_tool`, E2E F-10), or
    — when that package is not importable — a fail-closed local stand-in: a write by deny list,
    name or annotation; a read only on `readOnlyHint: true`; everything else unclassified."""
    try:
        from databridge_agent_runtime.classify import classify_tool   # noqa: PLC0415
        return classify_tool, "shared"
    except Exception:                                                # noqa: BLE001
        pass

    def local(name, *, read_only=None, destructive=None, denied=False, **_):
        from types import SimpleNamespace

        if denied:
            return SimpleNamespace(writes=True, basis="denied")
        if writes_by_name(name):
            return SimpleNamespace(writes=True, basis="name")
        if destructive is True or read_only is False:
            return SimpleNamespace(writes=True, basis="annotation")
        # no read verb list here: without the shared classifier nothing but a person vouches
        return SimpleNamespace(writes=True, basis="unknown")
    return local, "local"


def classify_plan_tools(steps: list[StepSpec], tools: list[ToolInfo],
                        deny: Optional[list[str]] = None) -> dict[str, str]:
    """Classify every tool the steps call against the live tool list (see `tool_classes`), with
    the SHARED rule (`databridge_agent_runtime.classify_mcp_tool`), fail closed.

    FXI3 (Codex f1 #2): a server's annotations are its CLAIM. A claim that the tool WRITES
    (`destructiveHint: true`, `readOnlyHint: false`) is believed — it only narrows. A claim that it
    only READS (`readOnlyHint: true`) is NOT evidence on its own: it is withheld from the
    classifier, so a read needs the closed read-verb allow-list (or a human vouching for it by
    name at approval). And no tool is blessed for its ROLE: the SQL tool of a tabular step
    (`execute_sql`, which says only that it RUNS something) is `unclassified:unknown` like any
    other tool nothing vouches for — the approving person must vouch for it explicitly."""
    classify, origin = _shared_classify()
    by_name = {t.name: t for t in tools}
    denied = set(deny or ())
    out: dict[str, str] = {}
    for step in steps:
        for name in step.tools():
            info = by_name.get(name)
            if info is None:
                out[name] = "unclassified:not_offered"
                continue
            c = classify(name, read_only=(False if info.read_only is False else None),
                         destructive=info.destructive, denied=name in denied)
            if c.writes and c.basis == "unknown":
                claim = ("; the server claims readOnlyHint=true, which is not a grant"
                         if info.read_only is True else "")
                out[name] = f"unclassified:unknown ({origin} classifier{claim})"
            else:
                out[name] = f"{'write' if c.writes else 'read'}:{c.basis}"
    return out


def plan_from_tools(conn: SourceConnection, tools: list[ToolInfo], *, has_resources: bool,
                    supports_resources: bool = True, harness=None,
                    profiles: Optional[list[Profile]] = None,
                    resources: Optional[list] = None) -> CrawlPlan:
    """Build (not save) a plan: specific profiles first; a generic (fallback) profile — e.g. the
    resources crawl for any server that lists resources — next; the model only if neither matched.
    """
    matched = match_profiles(tools, has_resources, profiles)
    notes: list[str] = []
    if matched:
        steps = [StepSpec.model_validate({"id": p.id, **p.step}) for p in matched]
        origin = "rules"
        for p in matched:
            if not p.verified:
                notes.append(f"profile {p.id!r}: output formats NOT verified against a live server")
            if p.fallback:
                notes.append(f"profile {p.id!r} is generic (no server-specific profile matched); "
                             f"its sample call is checked before approval")
    elif harness is not None:
        steps, notes = _model_plan(tools, has_resources, harness, resources)
        origin = "model"
        notes.insert(0, "no bundled crawl profile matched this server; the plan was proposed "
                        "by the model and needs review")
    else:
        steps, origin = [], "rules"
        notes.append("no bundled crawl profile matched and no model is configured; write the plan "
                     "by hand (origin: manual) or configure a model")
    deny = sorted({t for p in matched for t in p.deny_tools})
    problems = validate_steps(steps, tools, has_resources=has_resources,
                              supports_resources=supports_resources, deny_tools=deny)
    pages = pagination_params(steps, tools)
    for p in pages:
        notes.append(f"{p}: the listing tool pages and this plan reads its FIRST page only — "
                     f"coverage may be partial")
    return CrawlPlan(source_id=conn.id, origin=origin, profiles=[p.id for p in matched],
                     steps=steps, problems=problems, notes=notes, pagination=pages,
                     deny_tools=deny,
                     tool_classes=classify_plan_tools(steps, tools, deny),
                     tools_seen=sorted(t.name for t in tools), tools_digest=tools_digest(tools),
                     step_tool_digests=step_tool_digests(steps, tools),
                     created_at=_now())


# --- the live sample call -----------------------------------------------------------------------
_NEXT_PAGE = re.compile(r'"(nextCursor|next_cursor|nextPageToken|next_page_token|'
                        r'continuation_?[Tt]oken)"\s*:\s*"[^"]+"|"(has_?[Mm]ore)"\s*:\s*true')


class _Recording:
    """Pass-through client that keeps each tool's raw text output (for pagination markers)."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.supports_resources = getattr(inner, "supports_resources", True)
        self.outputs: list[tuple[str, str]] = []

    def call_tool(self, name, arguments):
        result = self._inner.call_tool(name, arguments)
        self.outputs.append((name, result.text))
        return result

    def list_tools(self):
        return self._inner.list_tools()

    def list_resources(self):
        return self._inner.list_resources()

    def read_resource(self, uri):
        return self._inner.read_resource(uri)


def _empty_json_listing(raw: str, step: StepSpec) -> bool:
    """True when a JSON listing's `items_path` reaches an EMPTY list — an empty source, not a
    parser that missed (which reaches nothing, or something that is not a list)."""
    from .parse import ParseError, loads_structured

    if step.list is None or step.list.parse.get("mode") not in ("json", "json_rows"):
        return False
    try:
        cur = loads_structured(raw)
    except ParseError:
        return False
    for part in [p for p in str(step.list.parse.get("items_path") or "").split(".") if p]:
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return isinstance(cur, list) and not cur


def probe_plan(client, plan: CrawlPlan, conn: SourceConnection, *,
               sample: int = 3) -> tuple[list[str], list[str], dict]:
    """Run each step's FIRST listing call and read ONE item through the session, read-only.

    Problems (block approval): the listing errors; the listing returned text but the parser found
    no items (a wrong regex / items_path); an item lacks an id/name; the read call cannot be built
    from a listed item, errors, or returns nothing; the listing reports a next page the plan
    cannot follow. Notes: an empty source; items the scope or extractors would not collect.
    Blocked/excluded scope is respected — the probe never reads an item the declared scope
    blocks or excludes by pattern. Returns (problems, notes, record)."""
    import itertools

    from .client import McpClientError, ReadOnlyClient
    from .crawl import (CrawlError, decide, enumerate_documents, enumerate_resources,
                        fetch_document)
    from .parse import ParseError, TemplateError

    problems: list[str] = []
    notes: list[str] = []
    pagination: set[str] = set()
    record: dict = {"steps": []}
    rec_client = _Recording(ReadOnlyClient(client, plan=plan))
    for step in plan.steps:
        srec: dict = {"step": step.id, "strategy": step.strategy, "items": 0, "sample": [],
                      "read": None}
        record["steps"].append(srec)
        if step.strategy == "tabular":
            srec["skipped"] = "tabular steps are validated by their SQL dialect"
            continue
        # FXI3: a sample call is still a CALL. A tool nothing classifies as a read is never
        # called before a person vouches for it: an unclassified LISTING tool leaves the step
        # unprobed; an unclassified READ tool leaves only the sample read unmade. Both are said.
        classes = plan.tool_classes or {}

        def _unread(names) -> list[str]:
            return sorted({t for t in names if t and classes
                           and not str(classes.get(t, "")).startswith("read:")})

        unread_list = _unread([s.tool for s in (step.roots, step.list) if s is not None])
        unread_read = _unread([step.read.text_tool, step.read.binary_tool]
                              if step.read is not None else [])
        if unread_list:
            srec["skipped"] = f"not probed: {unread_list} not classified as reads"
            notes.append(f"step {step.id!r}: NOT probed — listing tool(s) {unread_list} are not "
                         f"classified as reads; the approver must vouch for them by name, and "
                         f"the plan's first run is its first test")
            continue
        rec_client.outputs.clear()
        try:
            if step.strategy == "resources":
                it = enumerate_resources(rec_client, step, conn.id, scope=conn.scope)
            else:
                it = enumerate_documents(rec_client, step, conn.scope, conn.id)
            items = list(itertools.islice(it, sample))
        except (McpClientError, CrawlError, TemplateError, ParseError, ValueError) as exc:
            problems.append(f"step {step.id!r}: the sample listing failed: {exc}")
            continue
        srec["items"] = len(items)
        srec["sample"] = [{"uri": i.uri, "name": i.name, "mime": i.mime} for i in items]
        raw = "\n".join(text for _, text in rec_client.outputs)
        page = (_NEXT_PAGE.search(raw) if step.list is not None and step.list.page is None
                else None)
        if page:
            problems.append(f"step {step.id!r}: the listing reports a next page "
                            f"({page.group(0)[:60]}) but the plan reads one page only — "
                            f"coverage would be partial")
            pagination.add(f"{step.list.tool}:next-page")
        if not items:
            if raw.strip() and not _empty_json_listing(raw, step):
                problems.append(f"step {step.id!r}: the listing returned output but the parser "
                                f"found no items (check the regex / items_path): "
                                f"{raw.strip()[:160]!r}")
            else:
                notes.append(f"step {step.id!r}: the sample listing returned no items (an empty "
                             f"source?)")
            continue
        missing_id = [i.uri for i in items if not (i.uri and (i.name or i.ctx.get("id")))]
        if missing_id:
            problems.append(f"step {step.id!r}: listed items carry no id/name field: "
                            f"{missing_id[:2]}")
            continue
        target = None
        for item in items:
            d = decide(item, conn.scope, collected_so_far=0, holds=False)
            if d.outcome == "blocked" or d.rule.startswith(("exclude:", "not_included")):
                continue
            target = (item, d)
            break
        if target is None:
            notes.append(f"step {step.id!r}: every sampled item is out of the declared scope; no "
                         f"sample read was made")
            continue
        item, d = target
        if d.outcome != "collect":
            notes.append(f"step {step.id!r}: sampled items would be {d.outcome} ({d.rule}); "
                         f"reverse: {d.reverse}")
        if unread_read:
            srec["read"] = {"skipped": f"read tool(s) {unread_read} not classified as reads"}
            notes.append(f"step {step.id!r}: no sample READ was made — tool(s) {unread_read} "
                         f"are not classified as reads (the approver must vouch for them by "
                         f"name; the listing was probed)")
            continue
        try:
            data, mime = fetch_document(rec_client, step, item)
        except TemplateError as exc:
            problems.append(f"step {step.id!r}: the read call cannot be built from a listed item: "
                            f"{exc}")
            continue
        except (McpClientError, CrawlError, ValueError) as exc:
            problems.append(f"step {step.id!r}: the sample read of {item.uri!r} failed: {exc}")
            continue
        srec["read"] = {"uri": item.uri, "bytes": len(data), "mime": mime}
        if not data:
            problems.append(f"step {step.id!r}: the sample read of {item.uri!r} returned nothing")
    if pagination:
        record["pagination"] = sorted(pagination)
    return problems, notes, record


def propose_plan(source_id: str, *, harness=None, client=None, root: Optional[Path] = None,
                 profiles: Optional[list[Profile]] = None, probe: Optional[bool] = None
                 ) -> CrawlPlan:
    """Connect, list tools/resources, propose a plan, save it (status `proposed`, always).

    A plan no server-specific profile vouches for (model-proposed, or a generic fallback profile)
    is also PROBED through the same session (`probe_plan`) before it can be approved;
    `probe=True/False` forces it on/off."""
    from .client import open_client
    from .connections import load_connection

    conn = load_connection(source_id, root)
    if getattr(conn, "purpose", "ingest") != "ingest":
        raise PlanError(f"connection {source_id!r} is declared purpose={conn.purpose!r}: a "
                        f"write-capable connection is never crawled, so it gets no crawl plan. "
                        f"Declare a separate purpose=ingest connection with read-only access")
    known = profiles if profiles is not None else load_profiles()

    def _with(c) -> CrawlPlan:
        tools = c.list_tools()
        try:
            write_tool_advert(source_id, root=root, tools=list(tools))
        except OSError:                                              # an advert is best-effort
            pass
        supports = getattr(c, "supports_resources", True)
        resources = list(c.list_resources()) if supports else []
        plan = plan_from_tools(conn, tools, has_resources=bool(resources),
                               supports_resources=supports, harness=harness, profiles=known,
                               resources=resources)
        generic = plan.origin == "model" or any(p.fallback for p in known
                                                if p.id in plan.profiles)
        if (probe if probe is not None else generic) and plan.steps and not plan.problems:
            p_problems, p_notes, rec = probe_plan(c, plan, conn)
            plan = plan.model_copy(update={
                "problems": plan.problems + p_problems, "notes": plan.notes + p_notes,
                "probe": rec,
                "pagination": sorted(set(plan.pagination) | set(rec.get("pagination", [])))})
        return plan

    if client is not None:
        plan = _with(client)
    else:
        with open_client(conn) as c:
            plan = _with(c)
    if conn.auto_approve:
        # Codex round 3 (Critical): a connection flag is not a person, so it never approves. The
        # field stays readable (old connection files load) and its being ignored is reported.
        plan = plan.model_copy(update={"notes": [*plan.notes, (
            "auto_approve on the connection was ignored: a plan is approved only by a named, "
            "authenticated person (approve_plan)")]})
    save_plan(plan, root)
    return plan


def _call_role_tools(steps: list[StepSpec]) -> set[str]:
    """Tools a step calls with arguments the PLAN fixes (roots / list / read) — not its SQL tool."""
    out: set[str] = set()
    for step in steps:
        out |= {s.tool for s in (step.roots, step.list) if s is not None}
        if step.read is not None:
            out |= {t for t in (step.read.text_tool, step.read.binary_tool) if t}
    return out


def plan_tool_refusals(plan: CrawlPlan, vouched: Optional[set[str]] = None,
                       executors: Optional[set[str]] = None) -> list[str]:
    """Why each step tool may not be approved: a WRITE (by name or declared deny list — checked
    again here because a plan file can be edited after it was proposed — or by its recorded
    class), or UNCLASSIFIED (no recorded class: an edited or legacy plan; re-propose it so the
    live tool list classifies the tool). Empty list: every step tool is a classified read.

    FXJ3 (Codex f2 #1): a GENERIC EXECUTOR (`is_generic_executor`, plus `executors` — the live
    tools whose schema marks them) may appear ONLY as a tabular step's `sql` tool, where every
    statement is held to `read_only_sql_refusal` on every call. Used anywhere else (as a list /
    read / roots tool, with arguments that choose what it does) it is refused, vouched or not."""
    out: list[str] = []
    denied = set(plan.deny_tools or ())
    call_role = _call_role_tools(plan.steps)
    execs = set(executors or ())
    for step in plan.steps:
        for tool in step.tools():
            cls = (plan.tool_classes or {}).get(tool)
            if tool in denied or writes_by_name(tool):
                out.append(f"step {step.id!r}: tool {tool!r} is a write "
                           f"({'denied' if tool in denied else 'name'})")
            elif (tool in execs or is_generic_executor(tool)) and tool in call_role:
                out.append(f"step {step.id!r}: tool {tool!r} is a generic executor (its "
                           f"arguments choose what it does); it can only be a tabular step's "
                           f"sql tool, where every statement must be a read — nobody can vouch "
                           f"for it as a read")
            elif cls is None:
                out.append(f"step {step.id!r}: tool {tool!r} is unclassified (not classified "
                           f"against the live tool list; re-propose the plan)")
            elif cls.startswith("unclassified:unknown") and tool in (vouched or set()):
                continue                    # the approving person vouched for it BY NAME
            elif not cls.startswith("read:"):
                kind = "a write" if cls.startswith("write:") else "unclassified"
                hint = (" — nothing vouches for it; the approving person may vouch for it as a "
                        "read by name (vouch_read_tools)") if kind == "unclassified" else ""
                out.append(f"step {step.id!r}: tool {tool!r} is {kind} ({cls}){hint}")
    return out


def tool_advert_path(source_id: str, root: Optional[Path] = None) -> Path:
    return connection_path(source_id, root).with_name(f"{source_id}.tools.yaml")


def write_tool_advert(source_id: str, *, client=None, root: Optional[Path] = None,
                      tools: Optional[list[ToolInfo]] = None) -> Path:
    """Write the connection's LIVE `tools/list` (names, descriptions, schemas, annotations —
    no tool is called) to `<id>.tools.yaml`, where the automation catalogue reads it (E2E F-08:
    a write connection is never crawled, so no plan ever recorded its tools, and the English
    compiler could not see them). Annotations are recorded as the server's CLAIM; every reader
    classifies with the shared rule. Works for `purpose: agent_write` connections too."""
    if tools is None:
        if client is not None:
            tools = list(client.list_tools())
        else:
            from .client import open_client
            from .connections import load_connection

            with open_client(load_connection(source_id, root)) as c:
                tools = list(c.list_tools())
    p = tool_advert_path(source_id, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"source_id": source_id, "listed_at": _now(),
                                 "tools_digest": tools_digest(tools),
                                 "tools": [t.as_record() for t in tools]}, sort_keys=False),
                 encoding="utf-8")
    return p


def advertise_write_tools(source_ids: Optional[list[str]] = None, *,
                          root: Optional[Path] = None, client=None) -> list[dict]:
    """The post-registration step for WRITE connections (Codex f1 #9, E2E F-08): for every
    `purpose: agent_write` connection among `source_ids` (default: all registered), list its live
    tools and write `<id>.tools.yaml`, where the automation catalogue reads them. No tool is
    called. Each connection gets an explicit result `{id, ok, path, error}` — a failure is a
    registration result the caller reports, never swallowed. `client` (tests) is used for every
    listed connection instead of opening a session."""
    from .connections import list_connections, load_connection

    conns = ([load_connection(i, root) for i in source_ids] if source_ids is not None
             else list_connections(root))
    out: list[dict] = []
    for conn in conns:
        if getattr(conn, "purpose", "ingest") != "agent_write":
            continue
        try:
            path = write_tool_advert(conn.id, client=client, root=root)
            out.append({"id": conn.id, "ok": True, "path": str(path), "error": None})
        except Exception as exc:                                     # noqa: BLE001
            from .secret_scan import redact

            out.append({"id": conn.id, "ok": False, "path": None,
                        "error": redact(f"{type(exc).__name__}: {exc}")[:500]})
    return out


def plan_approval_digest(plan: CrawlPlan) -> str:
    """Digest of what an approval approves: the steps, the classes the approver saw, the declared
    deny list, the live tool digest, the source and its profiles. NOT notes, status or approval
    fields (a gateway may stamp binding notes after approving). Editing any approved field of the
    YAML changes this digest, so the stored approval no longer covers it."""
    body = {"source_id": plan.source_id, "profiles": list(plan.profiles),
            "steps": [s.model_dump(mode="json") for s in plan.steps],
            "tool_classes": dict(sorted((plan.tool_classes or {}).items())),
            "deny_tools": sorted(plan.deny_tools or ()), "tools_digest": plan.tools_digest}
    if plan.step_tool_digests:                   # FXJ3 (f2 #2): part of what was approved
        body["step_tool_digests"] = dict(sorted(plan.step_tool_digests.items()))
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


# --- the approval log: append-only AND hash-chained (FXJ3, Codex f2 #6) -------------------------
#: Fields of a log line that are the chain's, not the approval's.
_CHAIN_FIELDS = ("prev", "hash")
GENESIS = "genesis"


def _canon(rec: dict) -> bytes:
    return json.dumps(rec, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")


def _line_hash(rec: dict) -> str:
    """The hash a chained line carries: sha256 over its canonical content WITHOUT `hash`."""
    return hashlib.sha256(_canon({k: v for k, v in rec.items() if k != "hash"})).hexdigest()


def append_chained(path: Path, rec: dict) -> dict:
    """Append ONE record to a hash-chained, append-only JSONL log and return it as written.

    Each line carries `prev` (the previous line's `hash`, or the sha256 of the previous line's raw
    bytes for a legacy unchained line, or `genesis`) and `hash` (`_line_hash`). Opened in append
    mode, flushed and fsynced; never rewritten. Without a signing key this is tamper-EVIDENT, not
    tamper-proof: an edit, deletion or insertion anywhere but the tail breaks the chain
    (`read_chained` refuses the whole log); a writer who rewrites every line from the edited one
    to the end is not detected — that needs a key (founder decision, R-022)."""
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    prev = GENESIS
    if path.is_file():
        lines = [ln for ln in path.read_bytes().splitlines() if ln.strip()]
        if lines:
            try:
                last = json.loads(lines[-1])
            except json.JSONDecodeError:
                last = None
            prev = (last.get("hash") if isinstance(last, dict) and last.get("hash")
                    else hashlib.sha256(lines[-1]).hexdigest())
    out = {k: v for k, v in rec.items() if k not in _CHAIN_FIELDS}
    out["prev"] = prev
    out["hash"] = _line_hash(out)
    with open(path, "ab") as fh:
        fh.write(_canon(out) + b"\n")
        fh.flush()
        os.fsync(fh.fileno())
    return out


def read_chained(path: Path, *, what: str = "approval log") -> list[dict]:
    """Every CHAINED record of `path`, after verifying the whole chain. A line that does not
    parse, a `hash` that does not recompute or a `prev` that is not the previous line's hash raises
    `PlanError` — no record of a broken log is trusted. Legacy lines written before the chain
    existed (no `hash`) are skipped: they are never evidence."""
    if not path.is_file():
        return []
    out: list[dict] = []
    prev_expected = GENESIS
    for n, raw in enumerate([ln for ln in path.read_bytes().splitlines() if ln.strip()], 1):
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PlanError(f"{path.name} line {n} is not a record ({exc}); the {what} is "
                            f"damaged and no approval in it is trusted until it is repaired"
                            ) from None
        if not isinstance(rec, dict):
            raise PlanError(f"{path.name} line {n} is not a record; the {what} is damaged")
        if "hash" not in rec:
            prev_expected = hashlib.sha256(raw).hexdigest()           # legacy: never evidence
            continue
        if rec.get("prev") != prev_expected or rec.get("hash") != _line_hash(rec):
            raise PlanError(f"{path.name} line {n}: the {what}'s hash chain is broken (a line was "
                            f"edited, removed or inserted); no approval in it is trusted until "
                            f"a person re-approves")
        prev_expected = rec["hash"]
        out.append(rec)
    return out


def plan_approvals_path(source_id: str, root: Optional[Path] = None) -> Path:
    return connection_path(source_id, root).with_name(f"{source_id}.plan.approvals.jsonl")


def _append_approval(source_id: str, rec: dict, root: Optional[Path]) -> dict:
    """Append-only AND hash-chained (`append_chained`). Returns the line as written."""
    return append_chained(plan_approvals_path(source_id, root), rec)


def _approval_log(source_id: str, root: Optional[Path]) -> list[dict]:
    return read_chained(plan_approvals_path(source_id, root), what="plan approval log")


#: Log-line fields that are not part of the approval record itself.
_LOG_ONLY = ("event", "source_id", "prev", "hash")


def verify_plan_approval(plan: CrawlPlan, *, root: Optional[Path] = None) -> dict:
    """Prove a plan is approved: `status: approved`, an `approval` record made by an
    authenticated principal, whose `plan_digest` is the digest of the plan AS IT IS NOW, and an
    IDENTICAL record — every field: principal, authenticator, principal type, `dev_only`, time,
    digests, vouches — in the hash-chained approval log (FXJ3, Codex f2 #6). Returns the LOGGED
    record (not the YAML's copy); raises `ApprovalRefused` / `PlanError` otherwise. An edited YAML
    field — a `dev_only: false`, a widened `vouched_read_tools` — is never an approval."""
    if plan.status != "approved":
        raise PlanError(f"plan for {plan.source_id!r} is {plan.status}, not approved")
    rec = dict(plan.approval or {})
    if not rec.get("principal_id") or not rec.get("authenticated_by"):
        raise ApprovalRefused(f"plan for {plan.source_id!r} carries no authenticated approval "
                              f"(status/approved_by are typed fields, not proof); approve it "
                              f"with approve_plan(principal=...)")
    now = plan_approval_digest(plan)
    if rec.get("plan_digest") != now:
        raise ApprovalRefused(f"plan for {plan.source_id!r} was changed after it was approved "
                              f"(approved digest {str(rec.get('plan_digest'))[:12]}..., now "
                              f"{now[:12]}...); propose and approve it again")
    if rec.get("principal_id") != (plan.approved_by or "").strip():
        raise ApprovalRefused(f"plan for {plan.source_id!r}: approved_by does not match the "
                              f"authenticated approval")
    want = _canon({k: v for k, v in rec.items() if k != "log_hash"})
    for r in _approval_log(plan.source_id, root):
        if r.get("event") != "plan_approved" or r.get("source_id") != plan.source_id:
            continue
        logged = {k: v for k, v in r.items() if k not in _LOG_ONLY}
        if _canon(logged) == want and rec.get("log_hash") in (None, r.get("hash")):
            return logged
    raise ApprovalRefused(f"plan for {plan.source_id!r}: no IDENTICAL approval is in the "
                          f"append-only, hash-chained approval log "
                          f"({plan_approvals_path(plan.source_id, root).name}); an approval field "
                          f"edited after approval is not an approval")


def _live_tools(conn: SourceConnection, client) -> list[ToolInfo]:
    if client is not None:
        return list(client.list_tools())
    from .client import open_client

    with open_client(conn) as c:
        return list(c.list_tools())


def _needs_resources(plan: CrawlPlan) -> bool:
    return any(s.strategy == "resources" or (s.read is not None and s.read.resource_uri)
               for s in plan.steps)


def _live_resources(c) -> tuple[bool, list]:
    """(route supports resources, the live resource list) — through the SAME session as the
    tools. A failed listing raises: a resources plan is never approved against an unobserved
    listing (Codex f2 #12)."""
    supports = bool(getattr(c, "supports_resources", True))
    return supports, (list(c.list_resources()) if supports else [])


def _live_state(conn: SourceConnection, client, plan: CrawlPlan
                ) -> tuple[list[ToolInfo], bool, Optional[list]]:
    """Tools, resource support and (for a plan that reads resources) the resource list, all from
    ONE live session. Resources are listed only when a step needs them."""
    if not _needs_resources(plan):
        tools = _live_tools(conn, client)
        supports = bool(getattr(client, "supports_resources", True)) if client is not None \
            else True
        return tools, supports, None
    if client is not None:
        tools = _live_tools(conn, client)
        supports, resources = _live_resources(client)
        return tools, supports, resources
    from .client import open_client

    with open_client(conn) as c:
        tools = _live_tools(conn, c)
        supports, resources = _live_resources(c)
    return tools, supports, resources


def approve_plan(source_id: str, *, principal: Any = None, approved_by: Any = None,
                 root: Optional[Path] = None, vouch_read_tools: Optional[list[str]] = None,
                 client: Any = None) -> CrawlPlan:
    """Approve the plan on disk — ONLY for an authenticated principal (FXI3, Codex f1 #1).

    `principal` is the principal the application's authentication layer issued (a sealed
    agent-runtime `Approver`, or the gateway's registered principal type); it is checked by
    `require_authenticated_approver` BEFORE anything is read or written. For backwards
    compatibility the principal object may be passed as `approved_by`; a STRING there is only
    accepted as a cross-check (it must equal the principal's id) and never approves on its own.

    Approval is decided against the LIVE server (Codex f1 #3): the tools are re-listed, the plan's
    `tools_digest` (names, descriptions, schemas, annotations) must still match, the steps are
    re-validated and every step tool is re-classified NOW — a class edited into the YAML is
    discarded. A tool nothing vouches for passes only if the approver names it in
    `vouch_read_tools`; a write is never vouchable, and a GENERIC EXECUTOR only as a tabular
    step's sql tool, whose every statement `ReadOnlyClient` holds to the read-only grammar (Codex
    f2 #1). A plan that reads RESOURCES is validated against the resources listed NOW through the
    same session (Codex f2 #12). The approval is stored on the plan (`approval`) and appended to
    the hash-chained `<id>.plan.approvals.jsonl`, bound to `plan_approval_digest`."""
    from .connections import load_connection

    subject = principal if principal is not None else approved_by
    who = require_authenticated_approver(subject, what=f"approval of the crawl plan for "
                                                       f"{source_id!r}")
    if isinstance(approved_by, str) and approved_by.strip() \
            and approved_by.strip() != who["principal_id"]:
        raise ApprovalRefused(f"approval of the crawl plan for {source_id!r} REFUSED: the name "
                              f"given ({approved_by.strip()!r}) is not the authenticated "
                              f"principal ({who['principal_id']!r})")
    plan = load_plan(source_id, root)
    conn = load_connection(source_id, root)
    if getattr(conn, "purpose", "ingest") != "ingest":
        raise PlanError(f"connection {source_id!r} is purpose={conn.purpose!r}; it is never "
                        f"crawled, so no crawl plan can be approved for it")
    if plan.problems:
        raise PlanError(f"plan for {source_id!r} has problems and cannot be approved: "
                        f"{plan.problems}")
    if not plan.steps:
        raise PlanError(f"plan for {source_id!r} has no steps")
    try:
        tools, supports, resources = _live_state(conn, client, plan)
    except PlanError:
        raise
    except Exception as exc:                                         # noqa: BLE001
        from .secret_scan import redact

        raise PlanError(f"plan for {source_id!r} cannot be approved: the live server could not "
                        f"be listed ({redact(str(exc))[:300]}); nothing was approved") from None
    live_digest = tools_digest(tools)
    if plan.tools_digest != live_digest:
        raise PlanError(f"plan for {source_id!r} cannot be approved: the server's tools "
                        f"(names, descriptions, schemas or annotations) changed since it was "
                        f"proposed; propose it again")
    deny = sorted(set(plan.deny_tools or ()) | set(conn.scope.deny_tools or ()))
    plan = plan.model_copy(update={"tool_classes": classify_plan_tools(plan.steps, tools, deny),
                                   "deny_tools": deny,
                                   "step_tool_digests": step_tool_digests(plan.steps, tools)})
    vouched = {str(t) for t in (vouch_read_tools or []) if t}
    executors = {t.name for t in tools if is_generic_executor(t)}
    refused = plan_tool_refusals(plan, vouched, executors)
    if refused:
        raise PlanError(f"plan for {source_id!r} cannot be approved (fail closed): "
                        f"{'; '.join(refused)}")
    problems = validate_steps(plan.steps, tools,
                              has_resources=bool(resources) if resources is not None else True,
                              supports_resources=supports, deny_tools=deny)
    if problems:
        raise PlanError(f"plan for {source_id!r} does not validate against the live server: "
                        f"{problems}")
    classes = dict(plan.tool_classes)
    for t in vouched:
        if str(classes.get(t, "")).startswith("unclassified:unknown"):
            guarded = "sql_guarded:" if (t in executors or is_generic_executor(t)) else ""
            classes[t] = f"read:{guarded}vouched_by:{who['principal_id']}"
    at = _now()
    plan = plan.model_copy(update={"tool_classes": classes, "status": "approved",
                                   "approved_at": at, "approved_by": who["principal_id"]})
    rec = {**who, "at": at, "plan_digest": plan_approval_digest(plan),
           "tools_digest": live_digest, "vouched_read_tools": sorted(vouched)}
    if resources is not None:
        rec["resources"] = {"supports": supports, "count": len(resources)}
    written = _append_approval(source_id, {"event": "plan_approved", "source_id": source_id,
                                           **rec}, root)
    plan = plan.model_copy(update={"approval": {**rec, "log_hash": written["hash"]}})
    save_plan(plan, root)
    resolve_step_holds(plan, principal=subject, root=root)
    return plan


_PLAN_APPROVAL_LISTENERS: list = []


def add_plan_approval_listener(fn) -> Any:
    """Register `fn(source_id, replanned_step_ids, principal) -> Any`, called when a plan approved
    WITH an authenticated principal drops or changes steps of the plan the source last ran. The
    adapter agents register here (their manager validates the principal and decides every
    adaptation whose held steps were all re-planned); registering the same function twice is a
    no-op. Returns a remover."""
    if fn not in _PLAN_APPROVAL_LISTENERS:
        _PLAN_APPROVAL_LISTENERS.append(fn)

    def remove() -> None:
        if fn in _PLAN_APPROVAL_LISTENERS:
            _PLAN_APPROVAL_LISTENERS.remove(fn)
    return remove


_PLAN_HOOK_INSTALLED = False


def install_plan_approval_hook(manager: Any = None) -> bool:
    """Route plan approvals that re-plan held steps to the adapter agents
    (`databridge_adapter_agents.install_plan_approval_hook`). Once per process; False while the
    adapter agents offer no such hook (the holds then stand until approve_adaptation)."""
    global _PLAN_HOOK_INSTALLED
    if _PLAN_HOOK_INSTALLED:
        return True
    try:
        from databridge_adapter_agents import install_plan_approval_hook as _install
    except ImportError:
        return False
    _PLAN_HOOK_INSTALLED = bool(_install(manager))
    return _PLAN_HOOK_INSTALLED


def _last_run_plan(source_id: str) -> Optional[dict]:
    """The plan the source's most recent run executed (`plan.snapshot.yaml`), or None."""
    from .records import list_run_dirs

    for run_dir in reversed(list_run_dirs(source_id)):
        snap = run_dir / "plan.snapshot.yaml"
        if snap.is_file():
            try:
                return yaml.safe_load(snap.read_text(encoding="utf-8")) or None
            except Exception:                                        # noqa: BLE001
                return None
    return None


def replanned_steps(plan: CrawlPlan, root: Optional[Path] = None) -> list[str]:
    """Step ids of the plan the source last ran (`plan.snapshot.yaml` of its latest run — the
    plan in force when a drift was seen) that this plan DROPS or CHANGES. Re-approving the same
    step does not re-plan it. With no earlier run there is nothing to compare against: `[]`."""
    before = _last_run_plan(plan.source_id)
    if before is None:
        return []
    old = {s.get("id"): s for s in (before.get("steps") or []) if isinstance(s, dict)}
    new = {s.id: s.model_dump(mode="json") for s in plan.steps}
    return sorted(sid for sid in old if sid not in new or new[sid] != old[sid])


def matching_step_holds(plan: CrawlPlan, root: Optional[Path] = None) -> dict[str, list[str]]:
    """The holds-file step holds this plan answers: `{adaptation_id: [step ids]}` for each
    adaptation whose EVERY held step is re-planned (`replanned_steps`). Informational — the
    adapter agent decides from its own records, which also cover a whole-connection hold."""
    from .records import load_holds

    holds = load_holds(plan.source_id, root)
    replanned = set(replanned_steps(plan, root)) if holds.steps else set()
    out: dict[str, list[str]] = {}
    for aid in sorted(set(holds.steps.values())):
        held = sorted(s for s, a in holds.steps.items() if a == aid)
        if held and set(held) <= replanned:
            out[aid] = held
    return out


def resolve_step_holds(plan: CrawlPlan, *, principal: Any,
                       root: Optional[Path] = None) -> list[Any]:
    """Hand the steps this approved plan re-planned (`replanned_steps`) to the registered
    listeners — the adapter agents — with the authenticated principal. The agent decides (it
    validates the principal; an adaptation whose every held step was re-planned is recorded
    approved, its hold released and its baseline advanced); nothing here edits the holds file.
    Returns what each listener returned; with no principal, no listener or nothing re-planned,
    `[]` — the holds stand."""
    if principal is None:
        return []
    replanned = replanned_steps(plan, root)
    if not replanned:
        return []
    return [fn(plan.source_id, replanned, principal) for fn in list(_PLAN_APPROVAL_LISTENERS)]


__all__ = ["CrawlPlan", "StepSpec", "CallSpec", "ListSpec", "PageSpec", "ReadSpec", "SqlSpec",
           "Profile", "words_of",
           "PlanDraft", "DraftStep", "PlanError", "PLAN_TASK_CLASS", "load_profiles",
           "match_profiles", "validate_steps", "plan_from_tools", "propose_plan", "approve_plan",
           "add_plan_approval_listener", "matching_step_holds", "resolve_step_holds",
           "replanned_steps",
           "install_plan_approval_hook",
           "load_plan", "save_plan", "plan_path", "sql_dialect", "template_fields",
           "is_write_tool", "probe_plan", "pagination_params", "WRITE_VERB",
           "WRITE_VERB_EXTRA", "writes_by_name", "classify_plan_tools", "plan_tool_refusals",
           "write_tool_advert", "tool_advert_path", "require_authenticated_approver",
           "ApprovalRefused", "verify_plan_approval", "plan_approval_digest",
           "plan_approvals_path", "planner_catalogue", "advertise_write_tools",
           "is_generic_executor", "read_only_sql_refusal", "READ_ONLY_SQL_FUNCTIONS",
           "EXECUTOR_ARGS", "tool_digest", "step_tool_digests", "append_chained",
           "read_chained"]

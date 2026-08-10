"""Generated-SQL safety: sqlglot AST allow-list (09_FINAL_PRD §15.5, §15.11).

The injection-prevention wall. SELECT-only grammar: no DDL/DML/DCL, no INTO, no
multi-statement (stacked statements), tables checked against the linked schema, LIMIT
auto-injected. Stacked statements and comment-smuggling that tries to run DDL are
unparseable into the allowed grammar → refused BEFORE execution. (NL is never
interpolated into SQL — the model emits the whole statement, which is validated here.)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import sqlglot
from sqlglot import exp

from ..router.types import Refusal

DEFAULT_LIMIT = 1000

# Top-level statement types that are read-only SELECT grammar.
_ALLOWED_TOP = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)

# Any of these ANYWHERE in the tree → refuse (write/DDL/DCL/side-effecting).
_FORBIDDEN = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
    exp.TruncateTable, exp.Command, exp.Set, exp.Use, exp.Copy, exp.Pragma, exp.Into,
)

# Side-effecting / file / exec functions (spec §15.5 line 397: "no system functions
# outside an allow-list"). The statement-level _FORBIDDEN set already blocks ATTACH/COPY/
# PRAGMA-as-statements; this closes the *function* surface that a wider dialect set (OD-1)
# would expose. Pragmatic deny-list (not an exhaustive allow-list, which would false-reject
# legitimate aggregates and hurt EV-SQL); xp_/sp_ prefixes catch MSSQL extended procedures.
_FORBIDDEN_FUNCS = frozenset({
    "load_extension", "readfile", "writefile", "load_file",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_sleep", "lo_export",
    "lo_import", "dblink", "dblink_exec", "sys_exec", "sys_eval", "benchmark", "sleep",
})
_FORBIDDEN_FUNC_PREFIXES = ("xp_", "sp_")


def _func_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return (node.name or "").lower()
    if isinstance(node, exp.Func):
        try:
            return node.sql_name().lower()
        except Exception:  # noqa: BLE001
            return type(node).__name__.lower()
    return ""


@dataclass(frozen=True)
class SafetyResult:
    ok: bool
    safe_sql: Optional[str] = None
    refusal: Optional[Refusal] = None


def _refuse(code: str, message: str) -> SafetyResult:
    return SafetyResult(ok=False, refusal=Refusal(code=code, message=message))


def validate_sql(sql: str, *, allowed_tables: set[str], dialect: str = "",
                 default_limit: int = DEFAULT_LIMIT) -> SafetyResult:
    """Validate generated SQL against the allow-list; return a LIMIT-injected safe SQL or a
    typed Refusal. ``allowed_tables`` is the linked schema slice's table set."""
    try:
        statements = [s for s in sqlglot.parse(sql, dialect=dialect or None) if s is not None]
    except Exception as e:  # noqa: BLE001
        return _refuse("SQL_PARSE_ERROR", f"unparseable SQL: {e}")

    if len(statements) != 1:                               # stacked statements (§15.11)
        return _refuse("SQL_MULTI_STATEMENT",
                       f"{len(statements)} statements — only a single SELECT is allowed")
    tree = statements[0]

    if not isinstance(tree, _ALLOWED_TOP):
        return _refuse("SQL_NOT_SELECT", f"top-level {type(tree).__name__} is not a SELECT")

    for node in tree.walk():
        node = node[0] if isinstance(node, tuple) else node
        if isinstance(node, _FORBIDDEN):
            return _refuse("SQL_FORBIDDEN", f"forbidden node {type(node).__name__} (no DDL/DML/DCL)")
        fname = _func_name(node)
        if fname and (fname in _FORBIDDEN_FUNCS or fname.startswith(_FORBIDDEN_FUNC_PREFIXES)):
            return _refuse("SQL_FORBIDDEN_FUNCTION", f"forbidden system function {fname}()")

    # CTE aliases reference the WITH body, not base tables — exclude them from the existence
    # check so a legit `WITH recent AS (...) SELECT * FROM recent` isn't false-refused.
    cte_names = {(c.alias or "").lower() for c in tree.find_all(exp.CTE) if c.alias}
    used = {t.name for t in tree.find_all(exp.Table) if t.name}
    allowed_lower = {a.lower() for a in allowed_tables}
    unknown = {u for u in used if u.lower() not in allowed_lower and u.lower() not in cte_names}
    if unknown:
        return _refuse("SQL_UNKNOWN_TABLE",
                       f"tables not in the linked schema: {sorted(unknown)}")

    if not tree.args.get("limit") and isinstance(tree, exp.Select):
        tree = tree.limit(default_limit)
    return SafetyResult(ok=True, safe_sql=tree.sql(dialect=dialect or None))

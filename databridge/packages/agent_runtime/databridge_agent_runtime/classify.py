"""ONE read/write classifier for MCP tools — used by the runtime AND the English compiler (E2E F-10).

Before this module the two disagreed: the runtime (`tools.classify_tools`) called every tool with
no annotation a write unless an approved crawl plan used it, while the compiler
(`databridge_automation.catalogue.classify_tool`) read the verb in its name. postgres-mcp publishes
no annotations, so the compiler granted ``list_schemas`` as a read and the runtime refused the very
grant at run time (``capability_is_a_write``). Both now call `classify_tool` below.

The rule, fail closed, first match wins:

1. the connection is WRITE-ONLY (``purpose: agent_write``)             -> write  (``purpose``)
2. the tool is on a declared deny list (plan / scope ``deny_tools``)   -> write  (``denied``)
3. its NAME says it writes — the shared ingest verb rule
   (`databridge_mcp_ingest.plans.writes_by_name`; the pattern-equal copy
   below only when ingest is absent), camelCase split first              -> write  (``name``)
4. ``destructiveHint: true`` or ``readOnlyHint: false``                 -> write  (``annotation``)
5. ``readOnlyHint: true``                                               -> read   (``annotation``)
6. a HUMAN-APPROVED crawl plan uses it as a read step                   -> read   (``plan``)
7. its name carries a read verb (`READ_VERB`) and no verb that RUNS
   something (`EXEC_VERB`: ``execute_sql`` says nothing about writing)  -> read   (``name``)
8. nothing vouches for it                                              -> write  (``unknown``)

A name is read evidence only through the closed `READ_VERB` allow-list — never as "not a write
verb". An unknown tool (``frobnicate``, ``sync_now``, ``execute_sql``) is a write: it can only ever
be a declared, human-gated action, never a read a model reaches on its own. `execute_sql` becomes a
read only on a connection whose approved plan routes it as the SQL step — and the tool layer then
refuses it through ``source.read`` and admits it only through ``source.query``'s strict SQL
allow-list. No server is named here: the rule is the same for every connection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

#: Pattern-equal copies of `databridge_mcp_ingest.plans.WRITE_VERB` / `WRITE_VERB_EXTRA`, used only
#: when ingest is not importable (tests in both packages keep them equal).
WRITE_VERB = re.compile(
    r"(?:^|[_\-.])(write|edit|create|delete|remove|move|rename|update|insert|drop|upload|send|"
    r"post|patch|put|set|append|truncate|alter|grant|revoke|kill|exec_write)(?:[_\-.]|$)", re.I)
WRITE_VERB_EXTRA = re.compile(
    r"(?:^|[_\-.])(add|clear|reset|toggle|save|store|replace|import|merge|publish|submit|"
    r"archive|purge|destroy|restore|assign|invite|share|approve|reject|cancel|trigger)"
    r"(?:[_\-.]|$)", re.I)

#: A name that says it RUNS something says nothing about whether it writes.
EXEC_VERB = re.compile(r"(?:^|[_\-.])(exec|execute|run|eval|invoke|call|perform|apply|do)"
                       r"(?:[_\-.]|$)", re.I)

#: A name that says it only LOOKS. An allow-list, not the complement of the write verbs.
READ_VERB = re.compile(
    r"(?:^|[_\-.])(read|get|list|search|query|select|find|fetch|describe|show|view|count|"
    r"lookup|browse|tree|stat|stats|info|head|tail|download|inspect|peek|preview|explain|schema|"
    r"ls|cat|grep|glob|check|exists|resolve|ping|status|health|whoami)(?:[_\-.]|$)", re.I)

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

#: The bases a classification can rest on (recorded on `ToolAdvert.basis` / `ToolRef.basis`).
BASES = ("purpose", "denied", "name", "annotation", "plan", "unknown")


def words_of(name: str) -> str:
    """``createFolder`` -> ``create_Folder``: verb patterns need a separator around the verb."""
    return _CAMEL.sub("_", str(name))


def _ingest_writes_by_name():
    try:
        from databridge_mcp_ingest.plans import writes_by_name   # noqa: PLC0415 - the one rule
        return writes_by_name
    except Exception:                                        # noqa: BLE001 - absent: the copy
        return None


def writes_by_name(name: str) -> bool:
    """The shared ingest name rule (imported when present), on the name as written AND split."""
    shared = _ingest_writes_by_name()
    for n in {str(name), words_of(name)}:
        if shared is not None and shared(n):
            return True
        if WRITE_VERB.search(n) or WRITE_VERB_EXTRA.search(n):
            return True
    return False


def reads_by_name(name: str) -> bool:
    w = words_of(name)
    return bool(READ_VERB.search(w)) and not EXEC_VERB.search(w)


@dataclass(frozen=True)
class Classification:
    writes: bool
    basis: str


def classify_tool(name: str, *, read_only: Optional[bool] = None,
                  destructive: Optional[bool] = None, approved_read: bool = False,
                  denied: bool = False, write_only: bool = False) -> Classification:
    """Read or write for one tool — the module docstring's rule, in order."""
    if write_only:
        return Classification(True, "purpose")
    if denied:
        return Classification(True, "denied")
    if writes_by_name(name):
        return Classification(True, "name")
    if destructive is True or read_only is False:
        return Classification(True, "annotation")
    if read_only is True:
        return Classification(False, "annotation")
    if approved_read:
        return Classification(False, "plan")
    if reads_by_name(name):
        return Classification(False, "name")
    return Classification(True, "unknown")


def _get(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def plan_read_tools(plan: Any) -> set[str]:
    """The tools a HUMAN-APPROVED crawl plan uses as read steps (rule 6) — from a
    `databridge_mcp_ingest.plans.CrawlPlan` or the same plan as raw YAML. A plan that is not
    ``approved`` vouches for nothing. Mirrors `CrawlPlan.Step.tools()` (roots, list, read, sql)."""
    if plan is None or _get(plan, "status") != "approved":
        return set()
    out: set[str] = set()
    for step in _get(plan, "steps") or []:
        tools = getattr(step, "tools", None)
        if callable(tools):
            out.update(str(t) for t in tools() if t)
            continue
        for key in ("roots", "list"):
            spec = _get(step, key)
            if spec is not None and _get(spec, "tool"):
                out.add(str(_get(spec, "tool")))
        read = _get(step, "read")
        if read is not None:
            out.update(str(_get(read, k)) for k in ("text_tool", "binary_tool") if _get(read, k))
        sql = _get(step, "sql")
        if sql is not None and _get(sql, "tool"):
            out.add(str(_get(sql, "tool")))
    return out


def plan_denied_tools(plan: Any, connection: Any = None) -> set[str]:
    """Declared deny lists: the plan's ``deny_tools`` and the connection scope's (rule 2)."""
    out = {str(t) for t in (_get(plan, "deny_tools") or []) if t} if plan is not None else set()
    scope = _get(connection, "scope") if connection is not None else None
    if scope is not None:
        out |= {str(t) for t in (_get(scope, "deny_tools") or []) if t}
    return out


__all__ = ["Classification", "classify_tool", "writes_by_name", "reads_by_name", "words_of",
           "plan_read_tools", "plan_denied_tools",
           "WRITE_VERB", "WRITE_VERB_EXTRA", "READ_VERB", "EXEC_VERB", "BASES"]

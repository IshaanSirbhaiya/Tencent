"""Delegates — each adapter agent answers a `SubTask` over ITS sources, read-only, by rules.

The method is chosen by rule, never by a model (P16):

* **tabular** connection (a facts store exists) → exact SQL on the read-only facts store through
  `count_query`. When the sub-task aggregates, the aggregate is PUSHED DOWN: ONE statement filters
  and groups in SQL and returns, per group, the row count and the contributing ``(key, value)``
  pairs; the final sum/avg/min/max is taken in `Decimal` here (SQLite's SUM is binary floating
  point — not exact). Each pushed-down value's pointer derives from the ``table_cell`` pointers of
  EXACTLY the contributing cells (row key × column, bound to the facts snapshot). The same
  statement proves the row key is unique; if it is not, a cell cannot be named, and no value is
  emitted. Without an aggregate it returns the rows, one `query_cell` pointer per cell.
* **documents** connection → graph entities (bi-temporal: ``valid_time`` filters a node's own
  GROUNDED date attribute, ``system_time`` reads the graph AS BELIEVED at the window's end) +
  passage search for ``text contains``; each row is one passage (its ``doc_span`` pointer) of a
  document node in window, with the entities that document links to (each with the EDGE's
  pointer). Only nodes and edges whose provenance the CALLER may read are seen.
* **live** — only when the sub-task asks for ``freshness: live``: one checked read-only statement
  through the `SourceGateway`, captured (§5.2) before it is cited.

Admission is the CALLER's (`ReadAdmission`): a connection the caller may not read is skipped and
reported in coverage — never silently dropped, never read anyway. Anything read but unusable (no
pointer, unknown date, non-numeric, NULL) is counted in ``coverage.excluded``. Delegates never
write. A delegate given a ``cancel`` event stops at its next checkpoint once it is set (a timed-out
sub-task), before any further read or capture.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

from databridge_core import GroundedValue, SourcePointer
from databridge_core.provenance import SnapshotIdentity, Trust
from databridge_agent_runtime import sql_is_read_only

from .catalogue import Catalogue, ConnectionEntry, FactsTable, key_proof_sql
from .contracts import (
    DATE_FIELD,
    SELF_VIA,
    TEXT_FIELD,
    Aggregate,
    DelegateCoverage,
    EntityRef,
    ResultRow,
    SkippedRead,
    SubResult,
    SubTask,
)
from .pointers import derived_pointer

#: Pairs inside one pushed-down group are LENGTH-PREFIXED, so no source character can be mistaken
#: for structure (R-021 r2 #7): ``<len>:<key>+<len>:<value>`` for a value, ``<len>:<key>-`` for
#: SQL NULL, pairs concatenated with nothing between them. Every boundary is computed from a
#: length, never searched for, so a key or value may hold any character. (Only functions on the
#: read-only allow-list are used: ``length``, ``cast``, ``group_concat``.)
_DIGITS = re.compile(r"[0-9]+")
#: The whole-table key proof travels as one extra column of a plain read; it is never a cell.
KEY_PROOF_COL = "_fed_key_dupes"
#: Passage search depth for ``text contains``. Ranked search is never exhaustive: every result
#: built on it is reported as possibly incomplete.
PASSAGE_K = 200

Admits = Callable[[str], bool]


class DelegateError(ValueError):
    """The sub-task cannot be answered by this delegate as written."""


class DelegateCancelled(DelegateError):
    """The sub-task's deadline passed; the delegate stopped before its next read."""


def _checkpoint(cancel: Any) -> None:
    if cancel is not None and cancel.is_set():
        raise DelegateCancelled("cancelled: the sub-task's deadline passed before this read")


def to_decimal(v: Any) -> Optional[Decimal]:
    """Exact decimal of a stored number. Floats go through ``repr`` (their shortest round-trip
    form), never through binary expansion. ``None`` for NULL or anything that is not a number."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return v if v.is_finite() else None
    try:
        d = Decimal(repr(v)) if isinstance(v, float) else Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _pb(adapter_agent: str, principal: str, method: str, run_id: Optional[str]) -> dict:
    return {"agent": principal, "adapter_agent": adapter_agent, "agent_version": None,
            "task_class": None, "model": None, "by": "rules", "method": method,
            "prompt_version": None, "run_id": run_id,
            "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")}


# ================================================================================================
# tabular — exact SQL on the facts store
# ================================================================================================
def _where(task: SubTask, table: FactsTable, today: dt.date) -> tuple[list[str], list[Any]]:
    cols = set(table.columns)
    clauses: list[str] = []
    params: list[Any] = []
    for f in task.filters:
        if f.field not in cols:
            raise DelegateError(f"{table.name} has no column {f.field!r}")
        c = _q(f.field)
        if f.op == "contains":
            clauses.append(f"instr(lower(CAST({c} AS TEXT)), ?) > 0")
            params.append(str(f.value).lower())
        elif f.op == "in":
            clauses.append(f"{c} IN ({', '.join('?' for _ in f.value)})")
            params.extend(f.value)
        elif f.op == "is_null":
            clauses.append(f"{c} IS NULL")
        elif f.op == "not_null":
            clauses.append(f"{c} IS NOT NULL")
        else:
            op = {"eq": "=", "ne": "<>", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}[f.op]
            clauses.append(f"{c} {op} ?")
            params.append(f.value)
    tw = task.time_window
    if tw is not None:
        if tw.axis == "system_time":
            raise DelegateError("the facts store is one dated snapshot; a system_time window "
                                "cannot be read from it (ask for valid_time on a date column)")
        if not tw.field or tw.field not in cols:
            raise DelegateError(f"a valid_time window on {table.name} must name one of its date "
                                f"columns (got {tw.field!r})")
        lo, hi = tw.bounds(today)
        clauses.append(f"{_q(tw.field)} >= ? AND {_q(tw.field)} < ?")
        params.extend([lo.isoformat(), hi.isoformat()])
    return clauses, params


def _check(sql: str) -> None:
    ok, why = sql_is_read_only(sql)
    if not ok:                                               # a builder bug, never user input
        raise DelegateError(f"generated SQL failed the read-only check: {why}")


#: A word of a text value: a run of letters or digits (case-folded). Punctuation separates.
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
#: At most this many distinct stored values are fetched when a text value is looked for.
_RESOLVE_CANDIDATES = 50


def _words(v: Any) -> list[str]:
    return [w.casefold() for w in _WORD.findall(str(v))]


def _contains_words(stored: Any, wanted: list[str]) -> bool:
    """True when ``wanted`` occurs in ``stored`` as a CONTIGUOUS run of whole words (case-folded):
    'Pellucid Freightways' is in 'Pellucid Freightways Ltd'; 'Pell' is not; 'Freightways Pellucid'
    is not."""
    have = _words(stored)
    n = len(wanted)
    return n > 0 and any(have[i:i + n] == wanted for i in range(len(have) - n + 1))


def _is_empty(result: SubResult) -> bool:
    if result.method == "facts_sql_rows":
        return not result.rows
    # pushed down: no group at all, or every group's every value is zero or unknown
    return all(gv.value in (0, None) for r in result.rows for gv in r.cells.values())


def resolve_text_filters(task: SubTask, table: FactsTable, *, facts: Any, conn: str,
                         cancel: Any = None) -> tuple[Optional[SubTask], list[str], list[str]]:
    """E2E3 P-01. A sub-task read NOTHING, and one of its ``eq`` filters compares a column with a
    text value (a name, as the user wrote it) that the column never holds exactly. Rule, never a
    model, never a fuzzy score:

    * the column's stored values that contain EVERY word of the value, contiguously and in order,
      as whole words (case-folded) are found with one read-only statement;
    * exactly ONE such distinct value ⇒ the filter is read as that value, and the reading is
      returned as a sentence (shown to the asker as an assumption);
    * two or more ⇒ nothing is substituted (choosing would be a guess): the candidates are
      returned as an UNCERTAIN sentence, so the empty result is never shown as "none";
    * none ⇒ nothing changes.

    Returns ``(task to re-run or None, resolved sentences, uncertain sentences)``."""
    filters = list(task.filters)
    resolved: list[str] = []
    uncertain: list[str] = []
    changed = False
    for i, f in enumerate(task.filters):
        if f.op != "eq" or not isinstance(f.value, str) or to_decimal(f.value) is not None:
            continue
        wanted = _words(f.value)
        if not wanted or f.field not in table.columns:
            continue
        c = _q(f.field)
        exact = f"SELECT COUNT(*) AS n FROM {_q(table.name)} WHERE {c} = ?"
        _check(exact)
        _checkpoint(cancel)
        got = list(getattr(facts.query(conn, exact, params=(f.value,)), "rows", []) or [])
        if got and got[0][0]:
            continue                          # the value IS stored: not this filter's doing
        # narrow by the longest word in SQL, decide by whole words in Python
        probe = max(wanted, key=len)
        sql = (f"SELECT DISTINCT CAST({c} AS TEXT) AS v FROM {_q(table.name)} "
               f"WHERE instr(lower(CAST({c} AS TEXT)), ?) > 0 ORDER BY v LIMIT ?")
        _check(sql)
        _checkpoint(cancel)
        ans = facts.query(conn, sql, params=(probe, _RESOLVE_CANDIDATES + 1))
        hits = sorted({str(r[0]) for r in (getattr(ans, "rows", []) or [])
                       if r and r[0] is not None and _contains_words(r[0], wanted)})
        if len(hits) == 1:
            filters[i] = f.model_copy(update={"value": hits[0]})
            changed = True
            resolved.append(f"{table.name}.{f.field} = {f.value!r} matched no stored value "
                            f"exactly; it was read as {hits[0]!r}, the only stored value that "
                            f"contains all of its words")
        elif hits:
            shown = ", ".join(repr(h) for h in hits[:5])
            uncertain.append(f"{table.name}.{f.field} = {f.value!r} matched no stored value "
                             f"exactly, and {len(hits)} stored values contain all of its words "
                             f"({shown}{', ...' if len(hits) > 5 else ''}); none was chosen, so "
                             f"this empty result does not show that nothing qualifies")
    return (task.model_copy(update={"filters": tuple(filters)}) if changed else None,
            resolved, uncertain)


def _fold_text(v: Any) -> str:
    """A text value with letter case and spacing taken out: what two spellings of ONE stored
    value share (``'WATCHLIST'``, ``'Watchlist'``, ``' watchlist '``)."""
    return " ".join(str(v).casefold().split())


def fold_case_variants(task: SubTask, table: FactsTable, *, facts: Any, conn: str,
                       cancel: Any = None) -> tuple[Optional[SubTask], list[str], int]:
    """FIN-12. An ``eq`` filter on a text column compares one SPELLING, and a column that stores
    one value in several spellings (``WATCHLIST`` 25 rows, ``Watchlist`` 5, ``watchlist`` 3)
    then answers for a fraction of the rows that hold the value. Rule, never a model:

    * the column's stored values equal to the filter value once letter case and spacing are
      taken out (`_fold_text`) are found with one read-only statement;
    * when that finds a spelling other than the filter's own, the filter reads ALL of them
      (``in``), and the reading is returned as a sentence naming every spelling (shown to the
      asker as an assumption);
    * nothing else is matched: no substring, no word overlap, no distance — ``watch`` is not
      ``watchlist``. The row key is never folded (a key names one row, spelled exactly), and the
      caller does not fold a sub-task that reaches a person (`service.case_foldable`).

    Returns ``(task to read instead, or None; sentences; statements issued)``."""
    filters = list(task.filters)
    said: list[str] = []
    changed = False
    probes = 0
    for i, f in enumerate(task.filters):
        if f.op != "eq" or not isinstance(f.value, str) or to_decimal(f.value) is not None:
            continue
        if f.field not in table.columns or f.field == table.key or not _fold_text(f.value):
            continue
        c = _q(f.field)
        # SQLite's lower() folds ASCII only, so the bound value is folded the same way; Python's
        # casefold then decides, over this narrowed set
        sql = (f"SELECT DISTINCT CAST({c} AS TEXT) AS v FROM {_q(table.name)} "
               f"WHERE lower(trim(CAST({c} AS TEXT))) = ? ORDER BY v LIMIT ?")
        _check(sql)
        _checkpoint(cancel)
        probe = "".join(ch.lower() if ch.isascii() else ch for ch in f.value.strip())
        ans = facts.query(conn, sql, params=(probe, _RESOLVE_CANDIDATES + 1))
        probes += 1
        want = _fold_text(f.value)
        spellings = sorted({str(r[0]) for r in (getattr(ans, "rows", []) or [])
                            if r and r[0] is not None and _fold_text(r[0]) == want})
        if not spellings or spellings == [f.value]:
            continue
        filters[i] = f.model_copy(update={"op": "in", "value": tuple(spellings)}) \
            if len(spellings) > 1 else f.model_copy(update={"value": spellings[0]})
        changed = True
        shown = ", ".join(repr(s) for s in spellings)
        said.append(f"{table.name}.{f.field} = {f.value!r} was read as every stored spelling of "
                    f"that value that differs from it only in letter case or spacing ({shown})")
    return (task.model_copy(update={"filters": tuple(filters)}) if changed else None), said,         probes


def _group_spellings(result: SubResult, task: SubTask, table: Optional[FactsTable]) -> list[str]:
    """FIN-12: groups whose keys are one value spelled differently (``SGD`` and ``sgd``). They are
    shown as the source stores them — never merged by a guess — and said, so a total split across
    spellings is never read as two different values."""
    if task.aggregate is None or not task.aggregate.group_by:
        return []
    out: list[str] = []
    for g in task.aggregate.group_by:
        seen: dict[str, set[str]] = {}
        for r in result.rows:
            gv = r.cells.get(g)
            if gv is not None and isinstance(gv.value, str):
                seen.setdefault(_fold_text(gv.value), set()).add(gv.value)
        for spellings in seen.values():
            if len(spellings) > 1:
                where = f"{table.name}." if table is not None else ""
                out.append(f"{where}{g} stores one value in several spellings "
                           f"({', '.join(repr(s) for s in sorted(spellings))}); each spelling is "
                           f"a separate group here, so its total is split across them")
    return out


def run_tabular(task: SubTask, entry: ConnectionEntry, *, facts: Any, today: dt.date,
                principal: str, run_id: Optional[str], cancel: Any = None,
                resolve_names: bool = False, fold_case: bool = False) -> SubResult:
    """Exact SQL on the facts store. With ``resolve_names`` an EMPTY read whose text ``eq``
    filter names no stored value is retried once through `resolve_text_filters` (E2E3 P-01).
    With ``fold_case`` a text ``eq`` filter reads every spelling of its value that differs only
    in letter case or spacing (`fold_case_variants`, FIN-12)."""
    folded: list[str] = []
    probes = 0
    table0 = entry.table(task.entity_kinds[0] if task.entity_kinds else "")
    if fold_case and table0 is not None and task.filters:
        widened, folded, probes = fold_case_variants(task, table0, facts=facts,
                                             conn=entry.connection, cancel=cancel)
        if widened is not None:
            task = widened
    result = _run_tabular_resolving(task, entry, facts=facts, today=today, principal=principal,
                                    run_id=run_id, cancel=cancel, resolve_names=resolve_names)
    split = _group_spellings(result, task, table0)
    if not folded and not split and not probes:
        return result
    cov = result.coverage.model_copy(update={
        "resolved": (*folded, *result.coverage.resolved),
        "uncertain": (*result.coverage.uncertain, *split)})
    return result.model_copy(update={"coverage": cov,
                                     "queries": result.queries + probes})


def _run_tabular_resolving(task: SubTask, entry: ConnectionEntry, *, facts: Any, today: dt.date,
                           principal: str, run_id: Optional[str], cancel: Any = None,
                           resolve_names: bool = False) -> SubResult:
    first = _run_tabular(task, entry, facts=facts, today=today, principal=principal,
                         run_id=run_id, cancel=cancel)
    if not resolve_names or not _is_empty(first) or not task.filters:
        return first
    table = entry.table(task.entity_kinds[0] if task.entity_kinds else "")
    if table is None:
        return first
    again, resolved, uncertain = resolve_text_filters(task, table, facts=facts,
                                                      conn=entry.connection, cancel=cancel)
    if again is None:
        if not uncertain:
            return first
        cov = first.coverage.model_copy(update={"uncertain": (*first.coverage.uncertain,
                                                              *uncertain)})
        return first.model_copy(update={"coverage": cov})
    second = _run_tabular(again, entry, facts=facts, today=today, principal=principal,
                          run_id=run_id, cancel=cancel)
    cov = second.coverage.model_copy(update={
        "resolved": (*second.coverage.resolved, *resolved),
        "uncertain": (*second.coverage.uncertain, *uncertain),
        "notes": (*first.coverage.notes, *second.coverage.notes)})
    return second.model_copy(update={"coverage": cov,
                                     "queries": first.queries + second.queries})


def _run_tabular(task: SubTask, entry: ConnectionEntry, *, facts: Any, today: dt.date,
                 principal: str, run_id: Optional[str], cancel: Any = None) -> SubResult:
    conn = entry.connection
    tname = task.entity_kinds[0] if task.entity_kinds else ""
    table = entry.table(tname)
    if table is None:
        raise DelegateError(f"{conn} has no facts table {tname!r}")
    if table.key is None:
        raise DelegateError(f"{conn}/{table.name} has no column measured to name exactly one row "
                            f"(unique, never NULL), so its rows cannot be named or cited cell by "
                            f"cell; nothing is read from it")
    clauses, params = _where(task, table, today)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    t0 = time.perf_counter()
    excluded: list[str] = []
    if task.aggregate is not None:
        agg = task.aggregate
        for g in agg.group_by:
            if g not in table.columns:
                raise DelegateError(f"{table.name} has no column {g!r}")
        if agg.field and agg.field not in table.columns:
            raise DelegateError(f"{table.name} has no column {agg.field!r}")
        vfield = agg.field or table.key
        gsel = "".join(f"{_q(g)}, " for g in agg.group_by)
        kt, vt = f"CAST({_q(table.key)} AS TEXT)", f"CAST({_q(vfield)} AS TEXT)"
        value = (f"CASE WHEN {_q(vfield)} IS NULL THEN '-' "
                 f"ELSE '+' || length({vt}) || ':' || {vt} END")
        pair = f"length({kt}) || ':' || {kt} || {value}"
        # the SAME statement proves the row key names exactly one row (else no cell is citable)
        dupes = f"{key_proof_sql(table.name, table.key)} AS key_dupes"
        sql = (f"SELECT {gsel}COUNT(*) AS row_count, {dupes}, "
               f"group_concat({pair}, '') AS pairs FROM {_q(table.name)}{where}"
               + (f" GROUP BY {', '.join(_q(g) for g in agg.group_by)}" if agg.group_by else "")
               + (f" ORDER BY {', '.join(_q(g) for g in agg.group_by)}" if agg.group_by else ""))
        _check(sql)
        _checkpoint(cancel)
        ans = facts.query(conn, sql, params=tuple(params))
        rows, excluded = _pushdown_rows(task, table, ans, conn,
                                        predicate=f"{sql} -- params {params}")
        method = "facts_sql_pushdown"
    else:
        wanted = [c for c in dict.fromkeys([table.key, *task.fields]) if c]
        for c in wanted:
            if c not in table.columns:
                raise DelegateError(f"{table.name} has no column {c!r}")
        if KEY_PROOF_COL in table.columns:
            raise DelegateError(f"{table.name} has a column named {KEY_PROOF_COL!r}, which this "
                                f"reader reserves for its key proof")
        # the SAME statement re-proves the key over the whole table (R-021 r2 #10)
        sql = (f"SELECT {', '.join(_q(c) for c in wanted)}, "
               f"{key_proof_sql(table.name, table.key)} AS {KEY_PROOF_COL} "
               f"FROM {_q(table.name)}{where} ORDER BY {_q(table.key)} LIMIT ?")
        _check(sql)
        _checkpoint(cancel)
        ans = facts.query(conn, sql, params=tuple(params) + (task.limit + 1,))
        rows, excluded = _plain_rows(ans, conn, table, limit=task.limit)
        method = "facts_sql_rows"
    truncated = bool(getattr(ans, "truncated", False))
    if method == "facts_sql_rows" and len(list(ans.rows)) > task.limit:
        truncated = True                  # judged on the RAW rows: an excluded row still counts
    snap = None
    ptrs = list(getattr(ans, "pointers", []) or [])
    if ptrs:
        snap = ptrs[0].snapshot
    notes = [f"{method}: {getattr(ans, 'citation', '')}"]
    queries = 1
    if snap is None and method == "facts_sql_rows":
        # an EMPTY read returns no cell, so no snapshot: count the same selection once, so an
        # empty cohort's zero is still bound to the facts file it was read from (R-021 r2 #6)
        csql = f"SELECT COUNT(*) AS n FROM {_q(table.name)}{where}"
        _check(csql)
        _checkpoint(cancel)
        cans = facts.query(conn, csql, params=tuple(params))
        queries += 1
        cptrs = list(getattr(cans, "pointers", []) or [])
        crow = list(getattr(cans, "rows", []) or [])
        if cptrs and crow and crow[0][0] == 0:
            snap = cptrs[0].snapshot
            notes.append(f"empty selection, proven by: {getattr(cans, 'citation', '')}")
    return SubResult(subtask_id=task.id, adapter_agent=task.adapter_agent, connection=conn,
                     method=method, rows=tuple(rows),
                     coverage=DelegateCoverage(sources_read=(conn,), truncated=truncated,
                                               notes=tuple(notes), excluded=tuple(excluded)),
                     produced_by=_pb(task.adapter_agent, principal, method, run_id),
                     timings={"ms": int((time.perf_counter() - t0) * 1000)}, queries=queries,
                     snapshot=snap, key_field=table.key)


def _cell_pointers(ans: Any) -> dict[tuple[int, str], SourcePointer]:
    cols = list(getattr(ans, "columns", []) or [])
    out = {}
    for k, p in enumerate(getattr(ans, "pointers", []) or []):
        out[(k // max(1, len(cols)), cols[k % len(cols)])] = p
    return out


def _plain_rows(ans: Any, conn: str, table: FactsTable, *, limit: Optional[int] = None
                ) -> tuple[list[ResultRow], list[str]]:
    """Rows whose every cell has its pointer and whose key names exactly one row.

    * A row with a value but no pointer is NOT returned with a hole in it (it could still be
      counted or linked): it is excluded, and counted.
    * The key is VERIFIED, never assumed (R-021 r2 #10): the statement's whole-table proof column
      must read 0, and within the result a NULL or repeated key excludes every row carrying it -
      two rows sharing an id would merge in pointers, digests and link evidence."""
    cols = list(ans.columns)
    ptr = _cell_pointers(ans)
    out: list[ResultRow] = []
    excluded: list[str] = []
    rows = [list(r) for r in ans.rows][:limit]      # a (limit+1)th row only signals truncation
    if table.key is None or table.key not in cols:
        return [], ([f"{conn}/{table.name}: {len(rows)} row(s) have no verified row key and "
                     f"were not used"] if rows else [])
    kidx = cols.index(table.key)
    if KEY_PROOF_COL in cols:
        pidx = cols.index(KEY_PROOF_COL)
        if rows and {r[pidx] for r in rows} != {0}:
            return [], [f"{conn}/{table.name}: the row key {table.key!r} does not name exactly "
                        f"one row (duplicate or NULL keys); {len(rows)} row(s) were not used"]
    keys = [None if r[kidx] is None else str(r[kidx]) for r in rows]
    seen: dict[Optional[str], int] = {}
    for k in keys:
        seen[k] = seen.get(k, 0) + 1
    bad = sorted(str(k) for k, n in seen.items() if k is None or n > 1)
    ungrounded = 0
    unkeyed = 0
    for i, r in enumerate(rows):
        if keys[i] is None or seen[keys[i]] > 1:
            unkeyed += 1
            continue
        cells = {}
        complete = True
        for c, v in zip(cols, r):
            if c == KEY_PROOF_COL:
                continue
            p = ptr.get((i, c))
            if p is None:                          # no pointer, no value (hard constraint 2)
                complete = False
                break
            cells[c] = GroundedValue(value=v, pointer=p)
        if not complete:
            ungrounded += 1
            continue
        out.append(ResultRow(row_id=f"{conn}:{table.name}:{keys[i]}", cells=cells))
    if ungrounded:
        excluded.append(f"{conn}/{table.name}: {ungrounded} row(s) came back without a pointer "
                        f"for every cell and were not used")
    if unkeyed:
        excluded.append(f"{conn}/{table.name}: {unkeyed} row(s) share or lack a row key "
                        f"{table.key!r} ({', '.join(bad[:5])}) and were not used")
    return out, excluded


def _source_cell(conn: str, table: FactsTable, column: str, key: str,
                 snapshot: Optional[SnapshotIdentity]) -> SourcePointer:
    """The facts-store cell (row key × column) a pushed-down value was read from, bound to the
    snapshot the statement ran against. The key was proven unique by the same statement."""
    if snapshot is not None and snapshot.query_hash is not None:
        # the STORED cell's snapshot is the facts file itself, not the statement that found it
        snapshot = SnapshotIdentity(source_id=snapshot.source_id, as_of=snapshot.as_of,
                                    content_hash=snapshot.content_hash,
                                    schema_hash=snapshot.schema_hash)
    return SourcePointer(source_id=conn, kind="table_cell", table=table.name, column=column,
                         row_id=key, snapshot=snapshot,
                         capture_digest=snapshot.content_hash if snapshot else None,
                         trust=Trust.UNTRUSTED)


def _pushdown_rows(task: SubTask, table: FactsTable, ans: Any, conn: str, *,
                   predicate: str) -> tuple[list[ResultRow], list[str]]:
    agg = task.aggregate
    assert agg is not None
    cols = list(ans.columns)
    ptrs = list(getattr(ans, "pointers", []) or [])
    snap = ptrs[0].snapshot if ptrs else None
    vfield = agg.field or table.key
    out: list[ResultRow] = []
    excluded: list[str] = []
    for r in ans.rows:
        rec = dict(zip(cols, r))
        group_desc = ", ".join(f"{g} = {rec[g]!r}" for g in agg.group_by) or "all rows"
        where = f"{conn}/{table.name} [{group_desc}]"
        n = int(rec["row_count"])
        pairs = _decode_pairs(rec.get("pairs"))
        if int(rec.get("key_dupes") or 0) != 0:
            excluded.append(f"{where}: the row key {table.key!r} does not name exactly one row "
                            f"(duplicate or NULL keys), so the {n} contributing cell(s) cannot "
                            f"be cited; no value is given")
            continue
        if pairs is None or len(pairs) != n or len({k for k, _v in pairs}) != n:
            excluded.append(f"{where}: the contributing cells could not be read back one per row "
                            f"({len(pairs or [])} of {n}); no value is given")
            continue
        keys = [k for k, _v in pairs]
        nulls = [k for k, v in pairs if v is None]
        present = [(k, v) for k, v in pairs if v is not None]
        value: Any = None
        inputs: list[SourcePointer]
        row_ids: list[str]
        if agg.op == "count" and agg.field is None:
            value = n
            inputs = [_source_cell(conn, table, table.key, k, snap) for k in keys]
            row_ids = keys
        elif agg.op == "count":
            value = len(present)
            inputs = [_source_cell(conn, table, vfield, k, snap) for k, _v in present]
            row_ids = [k for k, _v in present]
            if nulls:
                excluded.append(f"{where}: {len(nulls)} row(s) have NULL {vfield} and are not "
                                f"counted by count({vfield})")
        else:
            nums = [(k, to_decimal(v)) for k, v in present]
            bad = [(k, v) for (k, v), (_k, d) in zip(present, nums) if d is None]
            if bad:
                excluded.append(f"{where}: {len(bad)} non-numeric {vfield} value(s) (e.g. row "
                                f"{bad[0][0]!r} = {bad[0][1]!r}); no {agg.op} is given rather "
                                f"than one computed over the numeric values only")
                nums = []                  # the group stays in the table; its value is unknown
                present = []
            if nulls:
                excluded.append(f"{where}: {len(nulls)} row(s) have NULL {vfield} and did not "
                                f"contribute to {agg.op}({vfield})")
            vals = [d for _k, d in nums if d is not None]
            if vals:
                if agg.op == "sum":
                    value = sum(vals, Decimal(0))
                elif agg.op == "avg":
                    value = sum(vals, Decimal(0)) / Decimal(len(vals))
                elif agg.op == "min":
                    value = min(vals)
                else:
                    value = max(vals)
            inputs = [_source_cell(conn, table, vfield, k, snap) for k, _v in present]
            row_ids = [k for k, _v in present]
        cells: dict[str, GroundedValue] = {}
        for g in agg.group_by:
            gp = derived_pointer(source_id=conn, derivation=f"GROUP KEY {g}",
                                 inputs=[_source_cell(conn, table, g, k, snap) for k in keys],
                                 row_ids=keys, predicate=predicate, snapshot=snap)
            if gp is not None:
                cells[g] = GroundedValue(value=rec[g], pointer=gp)
        if value is not None:
            dp = derived_pointer(source_id=conn, derivation=f"{agg.op.upper()}({agg.field or '*'})"
                                 f" over the pushed-down group [{group_desc}]",
                                 inputs=inputs, row_ids=row_ids, predicate=predicate,
                                 snapshot=snap)
            if dp is not None:
                cells[agg.label()] = GroundedValue(value=value, pointer=dp)
        count_label = Aggregate(op="count").label()
        if agg.op != "count" and count_label not in cells and n == len(keys):
            # FIN-12: the same statement already counted the group's rows (``COUNT(*)``) and read
            # back every row key, so "how many, and what total" is answered by ONE read: the count
            # is stated beside the total, cited to the key cell of every row it counted. Before
            # this the count was computed and thrown away, and the planner dropped the separate
            # count read it had planned.
            cp = derived_pointer(source_id=conn, derivation=f"COUNT(*) over the pushed-down group "
                                 f"[{group_desc}]",
                                 inputs=[_source_cell(conn, table, table.key, k, snap)
                                         for k in keys],
                                 row_ids=keys, predicate=predicate, snapshot=snap)
            if cp is not None:
                cells[count_label] = GroundedValue(value=n, pointer=cp)
        rid = f"{conn}:{table.name}:group:" + "|".join(str(rec[g]) for g in agg.group_by)
        out.append(ResultRow(row_id=rid, cells=cells))
    return out, excluded


def _take(raw: str, i: int) -> tuple[Optional[str], int]:
    """One ``<len>:<text>`` field at ``i``: (text, next index), or (None, i) if malformed."""
    m = _DIGITS.match(raw, i)
    if m is None or m.end() >= len(raw) or raw[m.end()] != ":":
        return None, i
    start = m.end() + 1
    end = start + int(m.group(0))
    if end > len(raw):
        return None, i
    return raw[start:end], end


def _decode_pairs(raw: Any) -> Optional[list[tuple[str, Optional[str]]]]:
    """``[(key, value | None)]`` from the length-prefixed group, or ``None`` if ANY part is
    malformed or anything is left over (then nothing in the group is cited). ``None`` as a value
    is SQL NULL - never a string."""
    if raw is None or raw == "":
        return []
    raw = str(raw)
    out: list[tuple[str, Optional[str]]] = []
    i = 0
    while i < len(raw):
        key, i = _take(raw, i)
        if key is None or i >= len(raw):
            return None
        mark, i = raw[i], i + 1
        if mark == "-":
            out.append((key, None))
        elif mark == "+":
            val, j = _take(raw, i)
            if val is None:
                return None
            out.append((key, val))
            i = j
        else:
            return None
    return out


def encode_pair(key: str, value: Optional[str]) -> str:
    """The Python mirror of the SQL pair encoding (tests and tools build groups with it)."""
    k = str(key)
    return f"{len(k)}:{k}-" if value is None else f"{len(k)}:{k}+{len(str(value))}:{value}"


# ================================================================================================
# documents — graph entities + passages
# ================================================================================================
def _parse_dt(v: Any) -> Optional[dt.date]:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _kind(x: Any) -> str:
    return str(getattr(x.kind, "value", x.kind))


def admitted_source(sources: Any, admits: Admits) -> Optional[SourcePointer]:
    """The first of ``sources`` the caller may read, else None (the item is not visible)."""
    for p in sources or ():
        if admits(p.source_id):
            return p
    return None


def _contains(outer: Any, inner: list[SourcePointer]) -> bool:
    """True when one of ``outer``'s pointers is the CONTAINER of one of ``inner``'s: the same
    source and document, and a span that strictly encloses the inner span (or no span at all, i.e.
    the whole document). Decided from provenance alone — no kind name (E2E N-02 co-mentions)."""
    for q in inner:
        if not q.document_id:
            continue
        for p in outer or ():
            if p.source_id != q.source_id or p.document_id != q.document_id:
                continue
            if p.char_start is None or p.char_end is None:
                return True
            if q.char_start is None or q.char_end is None:
                continue
            if p.char_start <= q.char_start and q.char_end <= p.char_end and \
                    (p.char_start, p.char_end) != (q.char_start, q.char_end):
                return True
    return False


def _nodes_for(task: SubTask, store: Any, today: dt.date, admits: Admits, cancel: Any = None
               ) -> tuple[list, list, str]:
    """(nodes, edges, how) — the graph as the time window asks for it, restricted to nodes and
    edges whose provenance the caller may read (an edge also needs both ends visible). A set
    ``cancel`` stops it before EACH store read (R-021 r2 #11)."""
    from databridge_graph import query as gq                   # noqa: PLC0415
    from databridge_graph.store import row_to_edge, row_to_node  # noqa: PLC0415

    tw = task.time_window
    if tw is not None and tw.axis == "system_time":
        _lo, hi = tw.bounds(today)
        at = dt.datetime.combine(hi, dt.time.min, tzinfo=dt.timezone.utc) - \
            dt.timedelta(microseconds=1)
        _checkpoint(cancel)
        snap = gq.as_of(store, valid_time=at, system_time=at)
        nodes, edges = snap.nodes, snap.edges
        how = f"graph as believed at {at.isoformat()} (system_time)"
    else:
        _checkpoint(cancel)
        nodes = [row_to_node(store, r) for r in store.fetchall(
            "SELECT * FROM nodes WHERE system_to IS NULL")]
        _checkpoint(cancel)                   # a deadline that passed during the node read
        edges = [row_to_edge(store, r) for r in store.fetchall(
            "SELECT * FROM edges WHERE system_to IS NULL")]
        how = "current graph"
    nodes = [n for n in nodes if admitted_source(n.sources, admits) is not None]
    visible = {n.id for n in nodes}
    edges = [e for e in edges if e.src_id in visible and e.dst_id in visible
             and admitted_source(e.sources, admits) is not None]
    return nodes, edges, how


def run_documents(task: SubTask, entry: ConnectionEntry, *, store: Any, retriever: Any,
                  catalogue: Catalogue, today: dt.date, principal: str,
                  run_id: Optional[str], admits: Optional[Admits] = None,
                  cancel: Any = None) -> SubResult:
    """``admits(source_id)`` is the CALLER's read scope over graph provenance; without one only
    this connection's own evidence is visible (fail closed)."""
    conn = entry.connection
    t0 = time.perf_counter()
    if store is None:
        raise DelegateError("no graph store is connected, so documents cannot be read")
    if not catalogue.vocabulary_loaded:
        raise DelegateError("the ontology vocabulary is not loaded; documents are not read "
                            "without it (the closed ontology fails closed)")
    unknown = [k for k in task.entity_kinds if k not in catalogue.node_kinds]
    if unknown:
        raise DelegateError(f"unknown entity kind(s) {unknown}")
    bad_edges = [e for e in task.link_edges if e not in catalogue.edge_kinds]
    if bad_edges:
        raise DelegateError(f"unknown edge kind(s) {bad_edges}")
    scope: Admits = admits if admits is not None else (lambda sid: sid == conn)
    text_terms = [str(f.value).lower() for f in task.filters
                  if f.field == TEXT_FIELD and f.op == "contains"]
    other = [f for f in task.filters if not (f.field == TEXT_FIELD and f.op == "contains")]
    for f in other:
        if f.field != DATE_FIELD or f.op not in ("lt", "le", "gt", "ge", "eq", "ne"):
            raise DelegateError(f"documents support `text contains` and comparisons on `date`; "
                                f"not {f.describe()}")
        if _parse_dt(f.value) is None:
            raise DelegateError(f"{f.describe()}: the value is not a date")
    nodes, edges, how = _nodes_for(task, store, today, scope, cancel)
    _checkpoint(cancel)
    kinds = set(task.entity_kinds)
    tw = task.time_window
    lo_hi = tw.bounds(today) if (tw is not None and tw.axis == "valid_time") else None
    date_field = tw.field if (tw is not None and tw.field) else DATE_FIELD
    needs_date = lo_hi is not None or bool(other)
    notes = [f"read the {how}"]
    excluded: list[str] = []

    def node_date(n) -> tuple[Optional[dt.date], Optional[SourcePointer], Any]:
        """The node's OWN grounded date attribute — never a substitute field (not valid_from,
        not another attribute). Absent or unparseable ⇒ unknown."""
        if date_field in n.attrs and date_field in n.attr_sources:
            p = n.attr_sources[date_field]
            if scope(p.source_id):
                d = _parse_dt(n.attrs[date_field])
                return d, (p if d is not None else None), n.attrs[date_field]
        return None, None, None

    candidates = []
    undated = 0
    for n in nodes:
        kind = _kind(n)
        if kinds and kind not in kinds:
            continue
        mine = [p for p in n.sources if p.source_id == conn]
        if not mine:
            continue
        d, dptr, draw = node_date(n)
        if needs_date and d is None:
            undated += 1                      # unknown date: excluded and COUNTED, never guessed
            continue
        if lo_hi is not None and not (lo_hi[0] <= d < lo_hi[1]):   # type: ignore[operator]
            continue
        if all(_compare(d, f.op, _parse_dt(f.value)) for f in other):
            candidates.append((n, kind, mine, d, dptr, draw))
    if undated:
        excluded.append(f"{conn}: {undated} document node(s) have no grounded {date_field!r} "
                        f"and were excluded from the date window (an unknown date is never "
                        f"guessed)")
    by_doc: dict[str, list] = {}
    for c in candidates:
        for p in c[2]:
            if p.document_id:
                by_doc.setdefault(p.document_id, []).append(c)
    edge_ok = set(task.link_edges)
    node_by_id = {n.id: n for n in nodes}
    touching: dict[str, list] = {}
    for e in edges:
        touching.setdefault(e.src_id, []).append(e)
        touching.setdefault(e.dst_id, []).append(e)

    def neighbours(nid: str) -> list[tuple[Any, str, SourcePointer]]:
        """(node, edge kind, edge pointer) over the declared, admitted edges."""
        out = []
        for e in touching.get(nid, []):
            ek = _kind(e)
            if ek not in catalogue.edge_kinds or (edge_ok and ek not in edge_ok):
                continue
            o = node_by_id.get(e.dst_id if e.src_id == nid else e.src_id)
            ptr = admitted_source(e.sources, scope)
            if o is not None and ptr is not None:
                out.append((o, ek, ptr))
        return out

    def ref(o, via: str, ptr: SourcePointer, path: tuple = ()) -> EntityRef:
        okind = _kind(o)
        return EntityRef(node_id=o.id, kind=okind, label=o.label, canonical_id=o.canonical_id,
                         subject=catalogue.is_subject(okind), via=via, pointer=ptr, path=path)

    def entities(n, mine: list[SourcePointer]) -> tuple[EntityRef, ...]:
        """What a row is about (E2E N-02: before this, a row's OWN identity was never offered, so
        an organisation row holding canonical id C-1001 linked to nothing):

        * the row's own node (``via: self``; evidence: the node's own pointer in this connection);
        * every node joined to it by one declared edge (evidence: the edge's pointer);
        * CO-MENTIONS: a node joined by a declared edge to a node that CONTAINS this row — one
          whose own evidence is the same document this row was read from (a meeting note that
          mentions the meeting AND the customer). Evidence: both edges. Provenance decides
          containment, never a kind name, so no domain is known here.
        Each node once, at its most direct association. A subject kind stays a candidate."""
        out: dict[str, EntityRef] = {n.id: ref(n, SELF_VIA, mine[0])}
        hops = neighbours(n.id)
        for o, ek, ptr in hops:
            out.setdefault(o.id, ref(o, ek, ptr))
        for d, ek1, p1 in hops:
            if not _contains(d.sources, mine):
                continue
            for x, ek2, p2 in neighbours(d.id):
                if x.id != n.id:
                    out.setdefault(x.id, ref(x, f"{ek1}/{ek2}", p2, (p1,)))
        return tuple(out.values())

    def date_cell(dptr, draw) -> dict[str, GroundedValue]:
        return {DATE_FIELD: GroundedValue(value=draw, pointer=dptr)} if dptr is not None else {}

    rows: list[ResultRow] = []
    truncated = False
    exhaustive = True
    skipped: list[SkippedRead] = []
    if text_terms:
        if retriever is None:
            raise DelegateError("passage search is not available, so `text contains` cannot be "
                                "answered")
        _checkpoint(cancel)
        passages = list(retriever.search(text_terms[0], k=PASSAGE_K, sources=[conn],
                                         mode="keyword"))
        # ranked retrieval is never an exhaustive scan: a match it did not return was never
        # considered, whatever the page size
        exhaustive = False
        truncated = len(passages) >= PASSAGE_K
        unplaced = 0
        for ps in passages:
            get = (ps.get if isinstance(ps, dict) else lambda k, d=None, _p=ps: getattr(_p, k, d))
            raw = get("pointer")
            if raw is None:
                continue
            ptr = raw if isinstance(raw, SourcePointer) else SourcePointer.model_validate(raw)  # trust-gate: exempt — re-validates a serialised pointer; its label travels in it
            text = str(get("text", "") or "")
            if ptr.source_id != conn or not all(t in text.lower() for t in text_terms):
                continue                      # exact containment, not "similar"
            homes = by_doc.get(ptr.document_id or "", [])
            if not homes:
                unplaced += 1
                continue
            for (n, kind, mine, d, dptr, draw) in homes:
                cells = {TEXT_FIELD: GroundedValue(value=text, pointer=ptr),
                         "document": GroundedValue(value=n.label, pointer=mine[0]),
                         **date_cell(dptr, draw)}
                rows.append(ResultRow(row_id=f"{conn}:{n.id}:{get('chunk_id', ptr.char_start)}",
                                      cells=cells, entities=entities(n, mine)))
        if unplaced:
            excluded.append(f"{conn}: {unplaced} matching passage(s) belong to no "
                            f"{sorted(kinds) or 'graph'} node in the window and were not used")
        notes.append(f"passage search for {text_terms[0]!r} (keyword, k={PASSAGE_K}), exact "
                     f"containment of {text_terms}; ranked retrieval — not an exhaustive scan")
    else:
        for (n, kind, mine, d, dptr, draw) in candidates:
            cells = {"document": GroundedValue(value=n.label, pointer=mine[0]),
                     **date_cell(dptr, draw)}
            rows.append(ResultRow(row_id=f"{conn}:{n.id}", cells=cells,
                                  entities=entities(n, mine)))
    if len(rows) > task.limit:
        rows, truncated = rows[:task.limit], True
    return SubResult(subtask_id=task.id, adapter_agent=task.adapter_agent, connection=conn,
                     method="graph_passages", rows=tuple(rows),
                     coverage=DelegateCoverage(sources_read=(conn,), skipped=tuple(skipped),
                                               truncated=truncated, notes=tuple(notes),
                                               excluded=tuple(excluded), exhaustive=exhaustive),
                     produced_by=_pb(task.adapter_agent, principal, "graph_passages", run_id),
                     timings={"ms": int((time.perf_counter() - t0) * 1000)}, queries=0)


def _compare(a: Any, op: str, b: Any) -> bool:
    if a is None or b is None:
        return False
    return {"lt": a < b, "le": a <= b, "gt": a > b, "ge": a >= b, "eq": a == b,
            "ne": a != b}[op]


# ================================================================================================
# live — only when freshness is asked for
# ================================================================================================
def run_live(task: SubTask, entry: ConnectionEntry, *, gateway: Any, today: dt.date,
             principal: str, run_id: Optional[str], capture_root: Any = None,
             cancel: Any = None, screen: Any = None) -> SubResult:
    """One checked read-only statement through the gateway, SCREENED, then captured before it is
    cited. Every returned value carries the capture's pointer. The statement carries EVERY filter
    it was given (the gateway binds no parameters, so a filter that needs a value is refused, not
    dropped) and fetches ``limit + 1`` rows so a capped result is reported as truncated.

    INT2 (R-021 follow-up 2): each row passes the pack screen ingestion and the agent runtime use
    (`databridge_mcp_ingest.screen`, imported, not copied; ``screen`` injects its config, else
    `load_screen()`). A row is kept only on ``allow``; ``block`` and ``review`` rows are withheld
    and recorded in coverage; every string of a kept row is masked; ONLY the masked kept rows are
    captured. No screen, no read (fail closed)."""
    from databridge_agent_runtime import capture_live_read          # noqa: PLC0415

    conn = entry.connection
    if gateway is None:
        raise DelegateError("live sources are not connected in this deployment")
    table = entry.table(task.entity_kinds[0]) if task.entity_kinds else None
    if table is None or task.aggregate is not None:
        raise DelegateError("a live read answers a row query on a known table")
    if table.key is None:
        raise DelegateError(f"{conn}/{table.name} has no column measured to name exactly one row; "
                            f"its live rows cannot be named")
    clauses, params = _where(task, table, today)
    if params:
        raise DelegateError("a live read takes no filter values (the gateway has no parameter "
                            "binding); read stored facts instead, or drop the filters")
    wanted = [c for c in dict.fromkeys([table.key, *task.fields]) if c]
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (f"SELECT {', '.join(_q(c) for c in wanted)} FROM {_q(table.name)}{where} "
           f"ORDER BY {_q(table.key)} LIMIT {int(task.limit) + 1}")
    _check(sql)
    t0 = time.perf_counter()
    _checkpoint(cancel)
    res = gateway.query(conn, sql)
    if getattr(res, "is_error", False):
        raise DelegateError(f"the live source returned an error: {(res.text or '')[:200]}")
    _checkpoint(cancel)                        # a timed-out read is never captured afterwards
    import hashlib                                                  # noqa: PLC0415
    uri = f"mcp://{conn}/sql?{hashlib.sha256(sql.encode()).hexdigest()[:16]}"
    try:
        data = json.loads(res.text or "[]")
    except ValueError:
        raise DelegateError("the live source's result is not a JSON row list; nothing is cited "
                            "from it") from None
    if not isinstance(data, list):
        raise DelegateError("the live source's result is not a row list; nothing is cited")
    data, screened_out = _screen_live_rows(data, screen)
    captured = json.dumps(data, ensure_ascii=False, default=str)
    _sha, ptr = capture_live_read(connection=conn, uri=uri, text=captured,
                                  data=captured.encode("utf-8"), mime="application/json",
                                  root=capture_root)
    rows = []
    malformed = 0
    incomplete: dict[str, int] = {}
    good: list[dict] = []
    for rec in data:
        if not isinstance(rec, dict):
            malformed += 1
            continue
        missing = [c for c in wanted if c not in rec]
        if missing:
            # a column the statement asked for is absent: a schema gap, never a silent None
            # (an explicit JSON null is a NULL value and is kept)
            for c in missing:
                incomplete[c] = incomplete.get(c, 0) + 1
            continue
        good.append(rec)
    truncated = len(good) > task.limit
    good = good[:task.limit]
    counts: dict[Optional[str], int] = {}
    for rec in good:
        k = None if rec[table.key] is None else str(rec[table.key])
        counts[k] = counts.get(k, 0) + 1
    unkeyed = sorted(str(k) for k, n in counts.items() if k is None or n > 1)
    for rec in good:
        k = None if rec[table.key] is None else str(rec[table.key])
        if k is None or counts[k] > 1:
            continue                      # a row id must name exactly one row (R-021 r2 #10)
        cells = {c: GroundedValue(value=rec[c], pointer=ptr) for c in wanted}
        rows.append(ResultRow(row_id=f"{conn}:{table.name}:{k}", cells=cells))
    excluded = []
    if screened_out:
        by_decision: dict[str, list[str]] = {}
        for w in screened_out:
            by_decision.setdefault(w["decision"], []).extend(r for r in w["rules"] if r)
        excluded.append(f"{conn}/{table.name}: live row(s) withheld by the ingestion screen, never "
                        f"captured: " + "; ".join(
                            f"{sum(1 for w in screened_out if w['decision'] == d)} {d} "
                            f"(rules {sorted(set(r))})" for d, r in sorted(by_decision.items())))
    if malformed:
        excluded.append(f"{conn}: {malformed} live result item(s) were not rows and were not used")
    if incomplete:
        excluded.append(f"{conn}/{table.name}: live row(s) missing requested column(s) were not "
                        f"used: " + ", ".join(f"{c} missing in {n}" for c, n in
                                               sorted(incomplete.items())))
    if unkeyed:
        excluded.append(f"{conn}/{table.name}: live row(s) sharing or lacking the row key "
                        f"{table.key!r} ({', '.join(unkeyed[:5])}) were not used")
    return SubResult(subtask_id=task.id, adapter_agent=task.adapter_agent, connection=conn,
                     method="live", rows=tuple(rows), live=True,
                     coverage=DelegateCoverage(
                         sources_read=(conn,), truncated=truncated,
                         notes=(f"live read, captured {ptr.capture_digest}",),
                         excluded=tuple(excluded)),
                     produced_by=_pb(task.adapter_agent, principal, "live", run_id),
                     timings={"ms": int((time.perf_counter() - t0) * 1000)}, queries=1,
                     key_field=table.key)


def _screen_live_rows(data: list, screen: Any) -> tuple[list, list[dict]]:
    """``(kept_masked_items, withheld)``: each item through ingestion's screen, as the agent
    runtime's `_screen_sql_result` does. A non-row item is screened as text too, so nothing
    unscreened is captured; kept rows have every string value masked."""
    try:
        from databridge_mcp_ingest.screen import (apply_masks, load_screen,   # noqa: PLC0415
                                                   screen_text)
        cfg = screen if screen is not None else load_screen()
    except Exception as e:                                   # noqa: BLE001 - fail closed
        raise DelegateError(f"the ingestion screen could not be loaded ({type(e).__name__}: {e}); "
                            f"a live read is never captured unscreened") from None
    kept: list = []
    withheld: list[dict] = []
    for i, item in enumerate(data):
        v = screen_text(json.dumps(item, sort_keys=True, default=str), cfg)
        if v.decision != "allow":
            withheld.append({"row": i, "decision": v.decision,
                             "rules": [f.get("rule") for f in v.fired]})
            continue
        if isinstance(item, dict):
            kept.append({k: (apply_masks(x, cfg.redactions)[0] if isinstance(x, str) else x)
                         for k, x in item.items()})
        elif isinstance(item, str):
            kept.append(apply_masks(item, cfg.redactions)[0])
        else:
            kept.append(item)
    return kept, withheld


__all__ = ["DelegateError", "DelegateCancelled", "run_tabular", "run_documents", "run_live",
           "resolve_text_filters",
           "to_decimal", "admitted_source", "PASSAGE_K"]

"""The rows behind an aggregate query result (E2E run 4, Q-05).

A grouped count (``SELECT status, COUNT(*) FROM invoices WHERE customer_id = 'C-1001' GROUP BY
status``) answers with group rows: ``{status: open, n: 1}``. A claim citing that result was cited
to the SQL result's span and never to the invoice rows it counted, so a reader could not see WHICH
rows the 1 is. This module derives, from the statement itself, the read that returns exactly the
rows each group was computed over:

    SELECT * FROM <the same single table> [WHERE <the same condition>] LIMIT <cap + 1>

and pairs every result group with its contributing rows by the GROUP BY columns' values. Nothing
about any table, column or domain is known here: the statement's own clauses decide, and anything
this cannot settle exactly (a join, a subquery, a set operation, a window function, a group key
that is an expression, an aliased group column, more rows than the cap) is NOT mapped — the result
is then cited as before, at its own span. A wrong mapping would be a false citation; no mapping is
only a coarser one.

The derived statement passes the SAME strict read-only check as any other (`sql_is_read_only`, in
`tools.py`) before it is sent, and its result passes the same ingestion screen and capture.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

#: The most contributing rows read for one aggregate. Beyond it nothing is mapped (said, not cut).
MAX_ROWS = 500
#: Rows listed (and registered as citable) per group — the `sample_row_ids` cap (§17.1).
ROWS_PER_GROUP = 20

_AGGREGATE = re.compile(r"\b(count|sum|avg|min|max|total)\s*\(", re.I)
_WINDOW = re.compile(r"\bover\s*\(", re.I)
_SET_OP = re.compile(r"\b(union|intersect|except)\b", re.I)
_IDENT = re.compile(r'^(?:[A-Za-z_][A-Za-z0-9_]*|"[^"]+")(?:\s*\.\s*(?:[A-Za-z_][A-Za-z0-9_]*|"[^"]+"))?$')
#: Clause heads that end the clause before them (found at paren depth 0 only).
_CLAUSE_RE = re.compile(r"\b(from|where|group\s+by|having|order\s+by|limit|offset|window|fetch)\b",
                        re.I)
#: An aggregate restricted by its own FILTER computes over fewer rows than its group.
_FILTER = re.compile(r"\bfilter\s*\(", re.I)


@dataclass(frozen=True)
class Expansion:
    sql: str                       # the contributing-rows read
    group_by: tuple[str, ...]      # result column names that key a group ('' -> one group)


def _mask(sql: str) -> str:
    """``sql`` with the inside of every string / quoted identifier and every comment blanked, the
    SAME length — so a keyword found in the mask is found at its position in ``sql``."""
    out = list(sql)
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"'):
            j = i + 1
            while j < n:
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:
                        j += 2
                        continue
                    break
                j += 1
            for k in range(i + 1, min(j, n)):
                out[k] = "x"
            i = j + 1
            continue
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


def _top_level(masked: str) -> list[tuple[int, str, int]]:
    """``[(start, clause, end_of_head)]`` of every clause head at paren depth 0, in order."""
    out: list[tuple[int, str, int]] = []
    for m in _CLAUSE_RE.finditer(masked):
        before = masked[:m.start()]
        if before.count("(") - before.count(")") == 0:
            out.append((m.start(), " ".join(m.group(1).lower().split()), m.end()))
    return out


def _split_commas(text: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts]


def _column_name(item: str) -> str:
    last = re.split(r"\s*\.\s*", item.strip())[-1]
    return last[1:-1] if last.startswith('"') and last.endswith('"') else last


def expansion_for(sql: str) -> Optional[Expansion]:
    """The contributing-rows read for an aggregate over ONE table, or None when this cannot say
    exactly which rows each group was computed over."""
    body = (sql or "").strip()
    while body.endswith(";"):
        body = body[:-1].rstrip()
    masked = _mask(body)
    if not re.match(r"\s*select\b", masked, re.I) or _SET_OP.search(masked):
        return None
    clauses = _top_level(masked)
    names = [c for _, c, _ in clauses]
    if "from" not in names or len(set(names)) != len(names) or names[0] != "from":
        return None                                     # one of each clause; nothing before FROM
    if any(c in names for c in ("window", "fetch")):
        return None
    heads = {c: (p, e) for p, c, e in clauses}

    def span(c: str) -> tuple[int, int]:
        start = heads[c][1]
        later = [p for p, _, _ in clauses if p > heads[c][0]]
        return start, (min(later) if later else len(body))

    sel = masked[len(re.match(r"\s*select", masked, re.I).group(0)):heads["from"][0]]
    if not _AGGREGATE.search(sel) or _WINDOW.search(sel) or _FILTER.search(sel):
        return None
    f0, f1 = span("from")
    from_masked = masked[f0:f1]
    if "(" in from_masked or "," in from_masked or re.search(r"\bjoin\b", from_masked, re.I):
        return None                                     # one table, nothing derived
    table = body[f0:f1].strip()
    if not table:
        return None
    where = ""
    if "where" in heads:
        w0, w1 = span("where")
        where = body[w0:w1].strip()
        if not where:
            return None
    group: list[str] = []
    if "group by" in heads:
        g0, g1 = span("group by")
        for item in _split_commas(body[g0:g1]):
            if not _IDENT.match(item):
                return None                             # an expression or an ordinal: not exact
            group.append(_column_name(item))
    elif "having" in heads:
        return None
    read = f"SELECT * FROM {table}" + (f" WHERE {where}" if where else "") + \
        f" LIMIT {MAX_ROWS + 1}"
    return Expansion(sql=read, group_by=tuple(group))


def _key(v: Any) -> str:
    return json.dumps(v, sort_keys=True, default=str)


def row_spans(rows: list[dict]) -> tuple[str, list[tuple[int, int]]]:
    """``(text, spans)``: ``text`` is exactly how `_screen_sql_result` renders the kept rows
    (``json.dumps(rows, ensure_ascii=False, default=str)``) and ``spans[i]`` the characters of row
    ``i`` in it."""
    text = json.dumps(rows, ensure_ascii=False, default=str)
    spans: list[tuple[int, int]] = []
    at = 1
    for r in rows:
        s = json.dumps(r, ensure_ascii=False, default=str)
        spans.append((at, at + len(s)))
        at += len(s) + 2
    if any(text[a:b] != json.dumps(r, ensure_ascii=False, default=str)
           for (a, b), r in zip(spans, rows)):
        raise ValueError("row spans do not match the rendered rows")
    return text, spans


def key_column(rows: list[dict]) -> Optional[str]:
    """The first column whose values are present, scalar and distinct on every row — what names a
    row in a citation. None when no column does (the rows are then cited without a key)."""
    if not rows:
        return None
    for col in rows[0]:
        vals = [r.get(col) for r in rows]
        if all(v is not None and not isinstance(v, (dict, list)) and str(v).strip() != ""
               for v in vals) and len({_key(v) for v in vals}) == len(vals):
            return str(col)
    return None


def group_members(result_rows: list[dict], rows: list[dict],
                  group_by: tuple[str, ...]) -> Optional[list[list[int]]]:
    """For each result row, the indexes of ``rows`` in its group — or None when a group column is
    not in the result under its own name (aliased away), so a group cannot be keyed exactly."""
    if not group_by:
        return [list(range(len(rows)))] * len(result_rows) if len(result_rows) == 1 else None
    out: list[list[int]] = []
    for g in result_rows:
        gk = {str(k).lower(): v for k, v in g.items()}
        if any(c.lower() not in gk for c in group_by):
            return None
        members = []
        for j, r in enumerate(rows):
            rk = {str(k).lower(): v for k, v in r.items()}
            if all(c.lower() in rk and _key(rk[c.lower()]) == _key(gk[c.lower()])
                   for c in group_by):
                members.append(j)
        out.append(members)
    return out


__all__ = ["Expansion", "MAX_ROWS", "ROWS_PER_GROUP", "expansion_for", "row_spans", "key_column",
           "group_members"]

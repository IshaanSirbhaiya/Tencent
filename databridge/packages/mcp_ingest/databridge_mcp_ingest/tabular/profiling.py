"""Read-only profiling of a tabular source (principles §4, "Relational DB": profile FIRST).

Per table, measured through the approved plan's SQL tool and the dialect's templates:
row count, columns + type class, per-column non-null / distinct / average length and words,
prose-vs-numeric, date ranges of temporal columns, primary and foreign keys, and whether the
table is a HISTORY SNAPSHOT (a rule declared in the policy — name signal + corroboration, P2).

Every statement is a template from `<dialect>.sql.yaml`; the engine composes nothing else. Values
come back through `pyrepr` (Python-repr rows from postgres-mcp, or JSON), typed — or CSV when the
dialect declares `mcp.rows: csv`.

Column statistics are measured with ONE statement per table (`table_stats`, built from the
dialect's `table_stats_column` / `table_stats_range` fragments) when the dialect has it (W4). If
the combined statement fails once, the run falls back to the per-column templates
(`column_stats`, `column_range`) for the rest of the run and says so in `runner.notes`; the
per-column path is the reference the combined one must agree with (tests/test_w4_*).
"""
from __future__ import annotations

import csv
import io
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..client import McpClientError
from ..parse import render as _render
from ..plans import StepSpec, sql_dialect
from .policy import type_class
from .pyrepr import loads_rows, to_text


class TabularError(RuntimeError):
    pass


@dataclass
class SqlRunner:
    """Calls the plan's SQL tool with a rendered dialect template; counts calls and time."""

    client: Any
    step: StepSpec
    dialect: dict = field(default_factory=dict)
    calls: int = 0
    seconds: float = 0.0
    combined: bool = True
    """Use the dialect's one-statement-per-table `table_stats` when it has one. Turned off for the
    rest of the run by the first failure of that statement (recorded in `notes`)."""
    notes: list = field(default_factory=list)
    #: Opaque values (`<memory at 0x…>`) the parser replaced by None across this runner's calls
    #: (R-022 f1 #7) — the facts stage records how many each table lost.
    opaque_values: int = 0

    def __post_init__(self) -> None:
        if self.step.sql is None:
            raise TabularError(f"step {self.step.id!r} has no sql block")
        if not self.dialect:
            self.dialect = sql_dialect(self.step.sql.dialect)

    def has(self, name: str) -> bool:
        return bool(self.dialect.get(name))

    def quote(self, name: str) -> str:
        """Quote an identifier with the dialect's rule (`quote:`; ANSI double quotes if unset)."""
        return dialect_quote(self.dialect, name)

    def sql(self, name: str, ctx: dict) -> str:
        tpl = self.dialect.get(name)
        if not tpl:
            raise TabularError(f"dialect {self.step.sql.dialect!r} has no {name!r} template")
        return render_sql(tpl, ctx, self.dialect)

    def run(self, sql: str, *, typed: bool = True) -> list[dict]:
        assert self.step.sql is not None
        t0 = time.monotonic()
        try:
            res = self.client.call_tool(self.step.sql.tool,
                                        {self.step.sql.arg: sql, **self.step.sql.extra_args})
        finally:
            self.calls += 1
            self.seconds += time.monotonic() - t0
        text = getattr(res, "text", "")
        if getattr(res, "is_error", False) or is_error_text(text, self.dialect):
            raise McpClientError(f"{self.step.sql.tool} returned an error: {text[:300]}")
        stats: dict = {}
        rows = parse_rows(text, self.dialect, typed=typed, stats=stats)
        self.opaque_values += int(stats.get("opaque", 0))
        return rows

    def rows(self, name: str, ctx: dict, *, typed: bool = True) -> list[dict]:
        return self.run(self.sql(name, ctx), typed=typed)


def quote_ident(name: str) -> str:
    """The `ident` filter's rule (parse.render), for engine-built column lists."""
    return '"' + str(name).replace('"', '""') + '"'


def dialect_quote(dialect: Optional[dict], name: str) -> str:
    """Quote an identifier by the dialect's `quote:` — one character (open = close, e.g. '"' or
    '`') or two (open, close, e.g. '[]'). The closing character is escaped by doubling it."""
    q = str((dialect or {}).get("quote") or '"')
    open_, close = (q[0], q[-1]) if len(q) in (1, 2) else ('"', '"')
    return open_ + str(name).replace(close, close * 2) + close


_IDENT_FIELD = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\|ident\}")


def render_sql(template: str, ctx: dict, dialect: Optional[dict] = None) -> str:
    """parse.render with the dialect's identifier quoting. For the default ANSI quote this IS
    parse.render; otherwise each `{x|ident}` is rendered from a pre-quoted value (raw), so a
    MySQL/BigQuery template gets backticks and SQL Server brackets — still only identifiers the
    server reported, quoted by rule, never engine-composed text."""
    q = str((dialect or {}).get("quote") or '"')
    if q == '"':
        return str(_render(template, ctx))
    extra: dict = {}

    def sub(m: "re.Match") -> str:
        key = m.group(1)
        if key in ctx:
            extra[f"__q_{key}"] = dialect_quote(dialect, ctx[key])
            return "{__q_" + key + "|raw}"
        return m.group(0)            # parse.render reports the missing field

    return str(_render(_IDENT_FIELD.sub(sub, template), {**ctx, **extra}))


def is_error_text(text: str, dialect: Optional[dict]) -> bool:
    """A server that reports a failed statement as ordinary text (`Error: …`, isError false) —
    declared per dialect under `mcp.error_prefixes`."""
    prefixes = ((dialect or {}).get("mcp") or {}).get("error_prefixes") or []
    head = (text or "").lstrip()
    return any(p and head.startswith(p) for p in prefixes)


def parse_rows(text: str, dialect: Optional[dict] = None, *, typed: bool = True,
               stats: Optional[dict] = None) -> list[dict]:
    """Rows as the dialect's MCP server returns them: `mcp.rows` json | repr | auto (JSON, else a
    safe Python literal) — or csv (a header line, then one line per row; values stay text)."""
    fmt = str(((dialect or {}).get("mcp") or {}).get("rows") or "auto").lower()
    if fmt == "csv":
        body = (text or "").strip()
        if not body:
            return []
        return [dict(r) for r in csv.DictReader(io.StringIO(body))]
    return loads_rows(text, typed=typed, stats=stats)


def _int_or_none(v: Any) -> Optional[int]:
    """A measured integer, or None when the answer did not carry one (never 0 by default)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else None


def _num(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def detect_history(profiles: list[dict], policy: dict) -> None:
    """Mark history-snapshot tables in place: a NAME signal corroborated by a snapshot column or
    by a sibling table holding the same columns minus the snapshot ones (P2: two signals)."""
    rules = policy.get("history_snapshot") or {}
    name_rx = [re.compile(p, re.I) for p in rules.get("name_patterns") or []]
    snap_cols = {c.lower() for c in rules.get("snapshot_columns") or []}
    colsets = {p["ref"]: {c["name"].lower() for c in p["columns"]} for p in profiles}
    for p in profiles:
        name_hit = next((rx.pattern for rx in name_rx if rx.search(p["table"])), None)
        cols = colsets[p["ref"]]
        col_hit = sorted(cols & snap_cols)
        sibling = None
        core = cols - snap_cols
        for other, ocols in colsets.items():
            if other != p["ref"] and core and ocols - snap_cols == core and len(ocols) < len(cols):
                sibling = other
                break
        signals = [s for s in (name_hit and f"name matches {name_hit!r}",
                               col_hit and f"snapshot column(s) {col_hit}",
                               sibling and f"same columns as {sibling} plus snapshot columns")
                   if s]
        is_hist = bool(name_hit) and (bool(col_hit) or sibling is not None)
        p["history_snapshot"] = {"is": is_hist, "signals": signals, "base_table": sibling,
                                 "rule": "history_snapshot (name signal + corroboration)"}


def profile_table(runner: SqlRunner, schema: str, table: str, policy: dict) -> dict:
    ctx = {"schema": schema, "table": table}
    columns = [{"name": r.get("column_name"), "type": to_text(r.get("data_type"))}
               for r in runner.rows("describe", ctx)]
    keys = runner.rows("keys", ctx) if runner.has("keys") else []
    pk = [k["column_name"] for k in keys
          if str(k.get("constraint_type")).upper() == "PRIMARY KEY"]
    fks = [{"column": k["column_name"], "ref_schema": k.get("ref_schema"),
            "ref_table": k.get("ref_table"), "ref_column": k.get("ref_column")}
           for k in keys if str(k.get("constraint_type")).upper() == "FOREIGN KEY"]
    prose_rule = policy.get("prose") or {}
    for col in columns:
        col["class"] = type_class(col["type"], policy)
    measure_problems: list[str] = []
    combined = table_stats(runner, ctx, columns, policy)
    if combined is not None:
        row_count: Optional[int] = combined[0]
    else:
        # R-022 f1 #7: an empty or malformed count answer is UNKNOWN (None), never 0 rows
        counted = runner.rows("count", ctx)
        row_count = _int_or_none(next(iter(counted[0].values()), None)) \
            if len(counted) == 1 and counted[0] else None
        if row_count is None:
            measure_problems.append(f"count returned {len(counted)} row(s) "
                                    f"{counted[:1]!r} — the row count is unknown")
    for i, col in enumerate(columns):
        if combined is not None:
            stats = combined[1][i]
        else:
            got = runner.rows("column_stats", {**ctx, "column": col["name"]})
            stats = got[0] if len(got) == 1 else {}
        non_null = _int_or_none(stats.get("non_null"))
        distinct = _int_or_none(stats.get("distinct_n"))
        if non_null is None or distinct is None:
            measure_problems.append(f"column {col['name']!r}: non_null / distinct_n not "
                                    f"measured — left unknown")
        col["non_null"] = non_null
        col["distinct"] = distinct
        avg_len = _num(stats.get("avg_len"))
        avg_sp = _num(stats.get("avg_spaces"))
        col["avg_chars"] = round(avg_len, 1) if avg_len is not None else None
        col["max_chars"] = int(stats["max_len"]) if stats.get("max_len") is not None else None
        col["avg_words"] = round(avg_sp + 1, 1) if avg_sp is not None and non_null else None
        col["unique"] = bool(row_count is not None and row_count > 0 and non_null == row_count
                             and distinct == row_count)
        col["prose"] = bool(col["class"] == "text" and avg_len is not None
                            and avg_len >= float(prose_rule.get("min_avg_chars", 40))
                            and (col["avg_words"] or 0) >= float(prose_rule.get("min_avg_words", 6)))
        col["range"] = None
        if col["class"] in ("temporal", "numeric") and non_null:
            if combined is not None and "lo" in stats:
                rng = stats
            else:
                rng = (runner.rows("column_range", {**ctx, "column": col["name"]}) or [{}])[0]
            col["range"] = {"lo": to_text(rng.get("lo")), "hi": to_text(rng.get("hi"))}
    if runner.opaque_values:
        measure_problems.append(f"{runner.opaque_values} opaque value(s) replaced by None so far "
                                f"in this run (the server printed an object, not a value)")
    date_ranges = {c["name"]: c["range"] for c in columns
                   if c["class"] == "temporal" and c["range"]}
    return {"schema": schema, "table": table,
            "ref": f"{schema}.{table}" if schema else table,
            "columns": columns, "primary_key": pk, "foreign_keys": fks,
            "row_count": row_count, "date_ranges": date_ranges,
            "prose_columns": [c["name"] for c in columns if c["prose"]],
            "numeric_columns": [c["name"] for c in columns if c["class"] == "numeric"],
            "measure_problems": measure_problems}


_STAT_KEYS = ("non_null", "distinct_n", "avg_len", "max_len", "avg_spaces", "lo", "hi")


def table_stats(runner: SqlRunner, ctx: dict, columns: list[dict], policy: dict
                ) -> Optional[tuple[int, list[dict]]]:
    """Every column's statistics in ONE statement per table (chunked at
    `profiling.max_columns_per_statement`). Returns (row_count, [per-column stats, in the
    per-column templates' key names]) or None when the dialect has no combined template or it
    failed — the caller then uses the per-column path. The first failure turns combined profiling
    off for the rest of the run: one wasted statement, not one per table."""
    if not (runner.combined and runner.has("table_stats") and runner.has("table_stats_column")):
        return None
    if not columns:
        return None
    width = max(1, int((policy.get("profiling") or {}).get("max_columns_per_statement") or 40))
    out: list[dict] = [dict() for _ in columns]
    row_count: Optional[int] = None
    try:
        for start in range(0, len(columns), width):
            stop = min(start + width, len(columns))
            parts: list[str] = []
            for i in range(start, stop):
                col = columns[i]
                cctx = {**ctx, "column": col["name"], "p": f"c{i}"}
                parts.append(runner.sql("table_stats_column", cctx))
                if col["class"] in ("temporal", "numeric") and runner.has("table_stats_range"):
                    parts.append(runner.sql("table_stats_range", cctx))
            measures = "".join(", " + p.strip() for p in parts)
            rows = runner.run(runner.sql("table_stats", {**ctx, "measures": measures}))
            if not rows:
                raise TabularError("table_stats returned no row")
            # Engines that fold unquoted aliases to upper case (Snowflake) → compare lower case.
            row = {str(k).lower(): v for k, v in rows[0].items()}
            n = _int_or_none(row.get("n"))
            if n is None:
                raise TabularError("table_stats row carries no measured n")
            if row_count is not None and n != row_count:
                raise TabularError("table_stats chunks disagree on the row count")
            row_count = n
            for i in range(start, stop):
                pre = f"c{i}_"
                got = {k[len(pre):]: v for k, v in row.items() if k.startswith(pre)}
                if _int_or_none(got.get("non_null")) is None or \
                        _int_or_none(got.get("distinct_n")) is None:
                    raise TabularError(f"table_stats row lacks {pre}non_null / {pre}distinct_n")
                out[i] = {k: got[k] for k in _STAT_KEYS if k in got}
    except Exception as exc:                                         # noqa: BLE001
        # Any failure of the OPTIONAL combined statement (a dialect that cannot run it, a server
        # that refuses its size, a replayed session that never recorded it) falls back to the
        # proven per-column templates. Said, not swallowed.
        runner.combined = False
        runner.notes.append(f"combined profiling (table_stats) failed on "
                            f"{ctx.get('schema')}.{ctx.get('table')} — "
                            f"{type(exc).__name__}: {str(exc)[:200]}; per-column statements "
                            f"used for the rest of the run")
        return None
    return int(row_count or 0), out


def profile_source(runner: SqlRunner, tables: list[tuple[str, str]], policy: dict) -> list[dict]:
    """Profile every (schema, table); then mark history snapshots across the set."""
    out = [profile_table(runner, schema, table, policy) for schema, table in tables]
    detect_history(out, policy)
    return out


__all__ = ["SqlRunner", "TabularError", "profile_table", "profile_source", "detect_history",
           "quote_ident", "dialect_quote", "render_sql", "parse_rows", "is_error_text",
           "table_stats"]

"""NL→SQL generation + self-correction (09_FINAL_PRD §15.4/§15.6).

generate: task_class ``sql_generation`` over the linked schema slice + dynamic few-shots.
Fixer (≤2, task_class ``sql_revision``): syntax/runtime errors fed back verbatim.
Reviser (≤1): semantic sanity (zero-row suspicion, NULL-heavy aggregates).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from databridge_core import AnnotatedSchema
from databridge_harness import Harness

from .linking import SchemaSlice
from .query_library import ConfirmedQueryLibrary


class _Sql(BaseModel):
    sql: str


def _fewshot_block(library: Optional[ConfirmedQueryLibrary], question: str,
                   slice_hash: str) -> str:
    if not library:
        return ""
    shots = library.retrieve(question, slice_hash)
    if not shots:
        return ""
    return "\nConfirmed examples (reuse the style):\n" + "\n".join(
        f"  Q: {s.question}\n  SQL: {s.sql}" for s in shots)


def generate_sql(harness: Harness, question: str, slice: SchemaSlice,
                 schema: AnnotatedSchema, *, library: Optional[ConfirmedQueryLibrary] = None) -> str:
    sys = ("You write ONE read-only SQL SELECT statement answering the question, using ONLY "
           "the tables/columns below. No DDL/DML, no multiple statements, no comments. "
           "Return ONLY the SQL.")
    user = (f"Schema:\n{slice.schema_text(schema)}\n"
            f"{_fewshot_block(library, question, slice.slice_hash)}\n\nQuestion: {question}")
    return harness.complete_structured(
        "sql_generation", [{"role": "system", "content": sys},
                           {"role": "user", "content": user}], _Sql, strict=True).parsed.sql


def fix_sql(harness: Harness, sql: str, error: str, slice: SchemaSlice,
            schema: AnnotatedSchema) -> str:
    """Fixer (§15.6): feed the error back verbatim, ask for a corrected SELECT."""
    sys = ("The SQL failed. Fix it — return ONE corrected read-only SELECT, same intent, "
           "using only the listed tables/columns. Return ONLY the SQL.")
    user = (f"Schema:\n{slice.schema_text(schema)}\n\nFailed SQL:\n{sql}\n\n"
            f"Error (verbatim):\n{error}")
    return harness.complete_structured(
        "sql_revision", [{"role": "system", "content": sys},
                         {"role": "user", "content": user}], _Sql, strict=True).parsed.sql


def detect_semantic_issues(rows: list[dict], question: str) -> list[str]:
    """Cheap semantic sanity checks before the Reviser is invoked (§15.6)."""
    issues: list[str] = []
    if not rows:
        issues.append("zero rows returned — suspicious for a question expecting data")
        return issues
    total = sum(len(r) for r in rows)
    nulls = sum(1 for r in rows for v in r.values() if v is None)
    if total and nulls / total > 0.5:
        issues.append("more than half of the returned values are NULL")
    return issues


def revise_sql(harness: Harness, sql: str, question: str, issues: list[str],
               slice: SchemaSlice, schema: AnnotatedSchema) -> str:
    """Reviser (§15.6, ≤1): semantic revision given the detected issues."""
    sys = ("The SQL ran but the result looks semantically off. Revise it to better answer "
           "the question (check GROUP BY vs the 'per X' intent, joins, filters). Return ONE "
           "read-only SELECT, ONLY the SQL.")
    user = (f"Schema:\n{slice.schema_text(schema)}\n\nQuestion: {question}\n\nSQL:\n{sql}\n\n"
            f"Issues: {issues}")
    return harness.complete_structured(
        "sql_revision", [{"role": "system", "content": sys},
                         {"role": "user", "content": user}], _Sql, strict=True).parsed.sql

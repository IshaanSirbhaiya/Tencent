"""NL→SQL lifecycle orchestrator (09_FINAL_PRD §15.4) + training-serving reuse (§15.12).

NL question → linking → generate → sqlglot safety → EXPLAIN/timeout/cap → execute
(read-only) → Fixer loop (≤2) → Reviser (≤1) → result + SQL-as-provenance (a `derived`
SourcePointer whose ``derivation`` IS the query, verifiable by re-running it). A confirmed
(question, schema-slice, SQL) triple becomes a PipelineArtifact; `infer` re-executes the
SAME SQL with NO LLM call.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Engine

from databridge_core import AnnotatedSchema, SourcePointer, content_hash
from databridge_harness import HarnessError

from ..artifacts import AdapterRef, PipelineArtifact
from ..router.types import Refusal
from ..rules.spec import TransformSpec
from .cost import cost_guard
from .linking import SchemaSlice, link_schema
from .nl2sql import detect_semantic_issues, fix_sql, generate_sql, revise_sql
from .query_library import ConfirmedQueryLibrary
from .safety import validate_sql
from .snapshot import sql_snapshot
from .sources import read_sql


@dataclass
class NLResult:
    question: str
    slice: SchemaSlice
    sql: Optional[str] = None
    rows: list[dict] = field(default_factory=list)
    provenance: Optional[SourcePointer] = None
    refusal: Optional[Refusal] = None
    fixer_attempts: int = 0
    revised: bool = False

    @property
    def ok(self) -> bool:
        return self.refusal is None


def answer_question(
    harness,
    engine: Engine,
    schema: AnnotatedSchema,
    question: str,
    *,
    library: Optional[ConfirmedQueryLibrary] = None,
    max_fix: int = 2,
    max_revise: int = 1,
    source_id: Optional[str] = None,
) -> NLResult:
    sid = source_id or f"{engine.dialect.name}:{engine.url.database or 'db'}"
    slc = link_schema(schema, question)
    allowed = set(slc.tables)
    dialect = engine.dialect.name

    try:
        sql = generate_sql(harness, question, slc, schema, library=library)
    except HarnessError as e:        # model never produced valid SQL → honest refusal, not a crash
        return NLResult(question=question, slice=slc,
                        refusal=Refusal(code="SQL_GENERATION_FAILED", message=str(e)))
    safe = validate_sql(sql, allowed_tables=allowed, dialect=dialect)
    if not safe.ok:                                        # injection / non-SELECT → REFUSE
        return NLResult(question=question, slice=slc, sql=sql, refusal=safe.refusal)
    sql = safe.safe_sql

    cost = cost_guard(engine, sql)
    if not cost.ok:
        return NLResult(question=question, slice=slc, sql=sql,
                        refusal=Refusal(code="SQL_COST_CEILING", message=cost.detail))

    # execute + Fixer loop (≤max_fix); a safety re-check guards every revision.
    # enforce_readonly=False is deliberate: the SELECT-only allow-list (validate_sql) is the
    # write-prevention wall in this path; the credential privilege-probe (§15.7) belongs at
    # ingest, and the in-process demo engine is writable (probe would false-flag it).
    attempts = 0
    rows: list[dict] = []
    while True:
        try:
            rows = read_sql(engine, query=sql, enforce_readonly=False, source_id=sid).records()
            break
        except Exception as e:  # noqa: BLE001
            if attempts >= max_fix:
                return NLResult(question=question, slice=slc, sql=sql, fixer_attempts=attempts,
                                refusal=Refusal(code="SQL_EXEC_FAILED", message=str(e)))
            attempts += 1
            try:
                fixed = fix_sql(harness, sql, str(e), slc, schema)
            except HarnessError as ge:   # the fixer itself couldn't generate → give up honestly
                return NLResult(question=question, slice=slc, sql=sql, fixer_attempts=attempts,
                                refusal=Refusal(code="SQL_EXEC_FAILED", message=str(ge)))
            safe = validate_sql(fixed, allowed_tables=allowed, dialect=dialect)
            if not safe.ok:
                return NLResult(question=question, slice=slc, sql=fixed, fixer_attempts=attempts,
                                refusal=safe.refusal)
            sql = safe.safe_sql

    # Reviser (≤max_revise): semantic sanity
    revised = False
    if max_revise > 0:
        issues = detect_semantic_issues(rows, question)
        if issues:
            try:
                cand = revise_sql(harness, sql, question, issues, slc, schema)
            except HarnessError:
                cand = sql                                 # revision unavailable → keep original
            safe = validate_sql(cand, allowed_tables=allowed, dialect=dialect)
            if safe.ok:
                try:
                    rows = read_sql(engine, query=safe.safe_sql, enforce_readonly=False,
                                    source_id=sid).records()
                    sql, revised = safe.safe_sql, True
                except Exception:  # noqa: BLE001
                    pass                                   # keep the original result

    snap = sql_snapshot(sid, sql, rows)
    provenance = SourcePointer(
        source_id=sid, kind="derived", derivation=sql, predicate=sql,
        snapshot=snap, row_count=len(rows), rows_digest=content_hash(rows))
    return NLResult(question=question, slice=slc, sql=sql, rows=rows, provenance=provenance,
                    fixer_attempts=attempts, revised=revised)


# --- training-serving reuse (§15.12) --------------------------------------------
def confirm_query(library: ConfirmedQueryLibrary, result: NLResult) -> None:
    """Persist a confirmed (question, schema-slice, SQL) triple for few-shot reuse."""
    if result.ok and result.sql:
        library.add(result.question, result.slice.slice_hash, result.sql)


def build_query_artifact(result: NLResult, *, artifact_id: str, approved_by: str) -> PipelineArtifact:
    """The confirmed query as a PipelineArtifact (C07 format) — the serving record."""
    return PipelineArtifact(
        artifact_id=artifact_id, direction="s2s",
        source_adapter=AdapterRef(name="sql"), target_adapter=AdapterRef(name="rows"),
        transform=TransformSpec(language="sql", expression=result.sql or ""),
        parser_config={"question": result.question, "slice_hash": result.slice.slice_hash},
        approved_by=approved_by, created_at=datetime.now(timezone.utc),
    ).with_checksum()


def infer_query(artifact: PipelineArtifact, engine: Engine) -> list[dict]:
    """Serving path (§15.12): re-execute the SAME stored SQL — NO LLM call."""
    return read_sql(engine, query=artifact.transform.expression,
                    enforce_readonly=False).records()

"""The annotator — the moat (06_PRD §2 Stages B & C).

Deterministic short-circuits run FIRST (P3): airtight T06 hints (PK→entity_id, FK→
foreign_key, ISO→timestamp_*, bool→boolean_flag) are resolved with NO tokens and passed
to the LLM as KNOWN. The LLM (task_class ``schema_annotation``, per-table, strict) only
classifies the rest, into the CLOSED vocabulary. A cross-table pass (``cross_table_inference``)
names relationships and sets the domain; label discovery runs over the whole schema.

Every annotation's confidence is BLENDED (confidence.py), never the raw self-report, and
``provenance`` records who decided (deterministic | llm | human). Output is an unconfirmed
``AnnotatedSchema`` (``confirmed_by=None``) — not usable downstream until a human confirms.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("databridge.semantic.annotator")

from pydantic import BaseModel

from databridge_core import (
    AnnotatedSchema,
    ColumnAnnotation,
    RelationshipAnnotation,
    SemanticType,
    TableAnnotation,
)
from databridge_ingest import SchemaExtract, TableMeta
from databridge_harness import Harness, StructuredOutputError

from .confidence import blended_confidence, confidence_band, dtype_compatible
from .corrections import CorrectionStore, context_key
from .label_discovery import discover_labels
from .models import SchemaGraphReport, TableStructure

CLOSED_VOCAB = [t.value for t in SemanticType]
_IDENTITY_TYPES = {SemanticType.ENTITY_ID, SemanticType.FOREIGN_KEY}
_TS_TYPES = {SemanticType.TIMESTAMP_CREATED, SemanticType.TIMESTAMP_EVENT}


# --- LLM response models (Stage B / Stage C) ------------------------------------
class _ColumnGuess(BaseModel):
    column: str
    semantic_type: SemanticType
    description: str = ""
    is_pii: bool = False
    pii_kind: str = "none"
    ml_role: str = "feature"
    self_confidence: float = 0.7


class _StageBResult(BaseModel):
    concept: str
    columns: list[_ColumnGuess] = []


class _RelGuess(BaseModel):
    src_table: str
    dst_table: str
    via: str
    name: str


class _StageCResult(BaseModel):
    domain: str = "unknown"
    relationships: list[_RelGuess] = []


def _ml_role_for(t: SemanticType) -> str:
    if t in _IDENTITY_TYPES:
        return "identifier"
    if t in _TS_TYPES:
        return "timestamp"
    return "feature"


def _deterministic_annotation(col_name: str, prelabel: SemanticType) -> ColumnAnnotation:
    return ColumnAnnotation(
        column=col_name,
        semantic_type=prelabel,
        description=f"{prelabel.value} (resolved by deterministic rule)",
        ml_role=_ml_role_for(prelabel),
        confidence=1.0,
        confidence_sources={"rule": "airtight", "band": "auto"},
        provenance="deterministic",
    )


def _stage_b_messages(table: TableMeta, structure: TableStructure,
                      known: dict[str, SemanticType], todo: list[str],
                      few_shots: list) -> list[dict]:
    hint_lines = []
    for h in structure.column_hints:
        if not h.airtight and h.prelabel is not None and h.column in todo:
            hint_lines.append(f"  - {h.column}: maybe {h.prelabel.value} ({h.rationale})")
    sample_rows = table.sample_rows[:10]
    known_lines = [f"  - {c}: {t.value}" for c, t in known.items()]
    fewshot = ""
    if few_shots:
        fewshot = "\nPrior corrections (learn from these):\n" + "\n".join(
            f"  - {c.input_context.get('column')}: corrected to {c.corrected.semantic_type.value}"
            for c in few_shots
        )
    sys = (
        "You annotate database columns with a CLOSED vocabulary of semantic types. "
        f"Use ONLY these types: {', '.join(CLOSED_VOCAB)}. If a column fits none, use "
        "'unknown'. Do NOT contradict the already-known columns. Return one entry per "
        "column listed under 'Columns to classify', with a one-sentence description and "
        "a self_confidence in [0,1]."
    )
    user = (
        f"Table: {table.name} (structural role: {structure.role.value})\n"
        f"Already known (do not change):\n" + ("\n".join(known_lines) or "  (none)") + "\n"
        f"Structural hints:\n" + ("\n".join(hint_lines) or "  (none)") + "\n"
        f"Columns to classify: {', '.join(todo) or '(none)'}\n"
        f"Column metas: " + "; ".join(
            f"{c.name}:{c.dtype}(nulls={c.null_fraction},uniq={c.unique_count})"
            for c in table.columns if c.name in todo
        ) + "\n"
        f"Up to 10 sample rows: {sample_rows}{fewshot}"
    )
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


def annotate_table(
    harness: Harness,
    table: TableMeta,
    structure: TableStructure,
    *,
    corrections: Optional[CorrectionStore] = None,
    domain: str = "unknown",
) -> TableAnnotation:
    """Stage B: deterministic short-circuit, then per-table LLM classification + blend."""
    airtight = {h.column: h.prelabel for h in structure.column_hints
                if h.airtight and h.prelabel is not None}
    todo = [c.name for c in table.columns if c.name not in airtight]

    few_shots = []
    if corrections is not None and todo:
        ck = context_key(table.name, todo[0], "")
        few_shots = corrections.retrieve_similar(ck, domain)

    msgs = _stage_b_messages(table, structure, airtight, todo, few_shots)
    try:
        result = harness.complete_structured(
            "schema_annotation", msgs, _StageBResult, strict=True,
        ).parsed
    except StructuredOutputError as err:
        # The model could not produce schema-valid output (common on small local models).
        # Degrade to "route every unresolved column to a human" — never crash the schema.
        # WARN so a persistent annotator/schema bug is distinguishable from model weakness
        # (a 100%-fallback table is a red flag, not just a "hard schema").
        logger.warning("Stage-B fallback for table %s: %d column(s) → unknown/human-review "
                       "(model could not satisfy schema: %s)", table.name, len(todo), err)
        result = _StageBResult(concept=structure.role.value, columns=[])

    guesses = {g.column: g for g in result.columns}
    by_name: dict[str, ColumnAnnotation] = {}
    for c in table.columns:
        if c.name in airtight:
            by_name[c.name] = _deterministic_annotation(c.name, airtight[c.name])
            continue
        g = guesses.get(c.name)
        if g is None:                                   # LLM omitted it → unknown, route to human
            by_name[c.name] = ColumnAnnotation(
                column=c.name, semantic_type=SemanticType.UNKNOWN,
                description="not classified by the model", confidence=0.0,
                confidence_sources={"band": "review"}, provenance="llm")
            continue
        prelabel = next((h.prelabel for h in structure.column_hints if h.column == c.name), None)
        conf = blended_confidence(g.self_confidence, g.semantic_type,
                                  structural_prelabel=prelabel, dtype=c.dtype,
                                  null_fraction=c.null_fraction)
        by_name[c.name] = ColumnAnnotation(
            column=c.name, semantic_type=g.semantic_type, description=g.description,
            is_pii=g.is_pii, pii_kind=_norm_pii(g.pii_kind), ml_role=_norm_ml(g.ml_role),
            confidence=conf,
            confidence_sources={
                "self_report": g.self_confidence,
                "structural_agreement": bool(prelabel == g.semantic_type),
                "dtype_compatible": dtype_compatible(g.semantic_type, c.dtype),
                "band": confidence_band(conf),
            },
            provenance="llm")

    columns = [by_name[c.name] for c in table.columns]
    return TableAnnotation(table=table.name, concept=result.concept,
                           role=structure.role, columns=columns,
                           summary=f"{result.concept} ({structure.role.value})")


def _norm_pii(v: str) -> str:
    allowed = {"email", "phone", "name", "national_id", "address", "none"}
    return v if v in allowed else "none"


def _norm_ml(v: str) -> str:
    allowed = {"feature", "label_candidate", "timestamp", "identifier", "ignore"}
    return v if v in allowed else "feature"


def _stage_c(harness: Harness, extract: SchemaExtract,
             tables: list[TableAnnotation],
             relationships: list[RelationshipAnnotation]) -> _StageCResult:
    summaries = "\n".join(f"  - {t.table}: {t.concept} [{t.role.value}]" for t in tables)
    fks = "\n".join(f"  - {r.src_table}.{r.via} → {r.dst_table} ({r.cardinality})"
                    for r in relationships)
    sys = ("You reason across a database schema. Name each relationship as a short verb "
           "phrase (e.g. 'user PLACES order') and classify the business domain. Return "
           "domain and one entry per foreign key.")
    user = f"Tables:\n{summaries}\nForeign keys:\n{fks}"
    try:
        return harness.complete_structured(
            "cross_table_inference",
            [{"role": "system", "content": sys}, {"role": "user", "content": user}],
            _StageCResult, strict=True,
        ).parsed
    except StructuredOutputError:
        return _StageCResult(domain="unknown", relationships=[])


def annotate_schema(
    harness: Harness,
    extract: SchemaExtract,
    report: SchemaGraphReport,
    *,
    corrections: Optional[CorrectionStore] = None,
    version: int = 1,
) -> AnnotatedSchema:
    """Full annotation: Stage B per table → Stage C cross-table → label discovery.

    Returns an UNCONFIRMED AnnotatedSchema (confirmed_by=None) — the confirmation flow
    (confirmation.py) is the only path to a usable, confirmed schema.
    """
    tables: list[TableAnnotation] = []
    for t in extract.tables:
        structure = report.structure(t.name)
        tables.append(annotate_table(harness, t, structure, corrections=corrections))

    relationships = list(report.relationships)
    stage_c = _stage_c(harness, extract, tables, relationships)
    names = {(r.src_table, r.dst_table, r.via): r.name for r in stage_c.relationships}
    relationships = [
        r.model_copy(update={"name": names.get((r.src_table, r.dst_table, r.via), r.name)})
        for r in relationships
    ]

    labels = discover_labels(harness, extract, tables)

    return AnnotatedSchema(
        source_id=extract.source_id, version=version, tables=tables,
        relationships=relationships, label_candidates=labels,
        domain=stage_c.domain, created_at=datetime.now(timezone.utc),
        confirmed_by=None,
    )

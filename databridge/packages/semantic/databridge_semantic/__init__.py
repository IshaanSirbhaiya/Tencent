"""databridge_semantic — the moat: schema graph + LLM annotator + confirmation.

PROMPT 3 ships T06 (deterministic schema-graph builder). PROMPT 4 adds the LLM
annotator, confidence blending, corrections store, and label discovery (T07).
"""
from __future__ import annotations

from .models import (
    AnnotatedSchema,
    ColumnAnnotation,
    ColumnHint,
    LabelCandidate,
    RelationshipAnnotation,
    SchemaGraphReport,
    SemanticType,
    TableAnnotation,
    TableRole,
    TableStructure,
)
from .schema_graph import (
    build_digraph,
    build_report,
    classify_column,
    infer_cardinality,
    infer_structural_role,
)
from .annotator import annotate_schema, annotate_table
from .confidence import blended_confidence, confidence_band, dtype_compatible, second_model_vote
from .confirmation import ConfirmationSession, MandatoryReviewPending
from .corrections import Correction, CorrectionStore, redact_value
from .label_discovery import discover_labels, plausibility_check

__all__ = [
    # models (T06 structural)
    "SchemaGraphReport",
    "TableStructure",
    "ColumnHint",
    # re-exported core annotation models
    "AnnotatedSchema",
    "ColumnAnnotation",
    "TableAnnotation",
    "RelationshipAnnotation",
    "LabelCandidate",
    "SemanticType",
    "TableRole",
    # T06 builder
    "build_report",
    "build_digraph",
    "classify_column",
    "infer_structural_role",
    "infer_cardinality",
    # T07 annotator + confidence + confirmation + corrections + labels
    "annotate_schema",
    "annotate_table",
    "blended_confidence",
    "confidence_band",
    "dtype_compatible",
    "second_model_vote",
    "ConfirmationSession",
    "MandatoryReviewPending",
    "Correction",
    "CorrectionStore",
    "redact_value",
    "discover_labels",
    "plausibility_check",
]

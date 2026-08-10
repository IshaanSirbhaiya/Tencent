"""extract/ — U→S extraction core (C15, 09_FINAL_PRD §11). The critical-path subsystem.

Pipeline: pre_extract (§11.1) → span_selection (§11.2) → pydantic_extract (§11.2) →
grounding (§11.4, the load-bearing wall) → confidence (§11.5) → ExtractionResult.
"""
from __future__ import annotations

from .confidence import (
    CrossFieldViolation,
    band_of,
    blend_field_confidence,
    validate_cross_fields,
)
from .excel_irregular import (
    KVVerdict,
    SheetRouting,
    extract_sheet,
    looks_like_key_value,
    route_workbook,
    sheet_to_document,
)
from .extractor import ExtractionResult, FieldResult, extract
from .grounding import (
    CONFUSION_MAP,
    FUZZY_THRESHOLD,
    OCR_THRESHOLD,
    GroundingOutcome,
    GroundingSource,
    ground_field,
)
from .pre_extract import PreExtractResult, pre_extract
from .span_selection import SelectedSpans, lexical_scorer, select_spans
from .target_model import (
    CompiledTarget,
    CrossFieldRule,
    FieldSpec,
    compile_target,
    from_json_schema,
    from_pydantic,
    from_sql_ddl,
)

__all__ = [
    "extract", "ExtractionResult", "FieldResult",
    "looks_like_key_value", "sheet_to_document", "extract_sheet", "route_workbook",
    "KVVerdict", "SheetRouting",
    "compile_target", "from_json_schema", "from_pydantic", "from_sql_ddl",
    "CompiledTarget", "FieldSpec", "CrossFieldRule",
    "pre_extract", "PreExtractResult",
    "select_spans", "SelectedSpans", "lexical_scorer",
    "ground_field", "GroundingSource", "GroundingOutcome",
    "CONFUSION_MAP", "FUZZY_THRESHOLD", "OCR_THRESHOLD",
    "blend_field_confidence", "band_of", "validate_cross_fields", "CrossFieldViolation",
]

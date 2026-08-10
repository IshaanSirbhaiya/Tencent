"""Rule validation (09_FINAL_PRD §10.2): syntactic (parse) + semantic (sample run).

Runs BEFORE human confirm and BEFORE full execution. Syntactic: the expression parses
and every referenced coercion name exists. Semantic: a ≤50-row sample run that checks the
row-count law, quarantine accounting, and (if a target schema is supplied) that every
required target field is produced — plus E3 distribution checks the caller can add.
"""
from __future__ import annotations

from typing import Optional

import jsonata
import sqlglot

from ..frame import Frame
from ..validators import QuarantineAccounting, RowCountLaw, ValidationResult
from .coercions import COERCIONS
from .engine import execute_transform
from .spec import TransformSpec

SAMPLE_SIZE = 50


def validate_syntactic(spec: TransformSpec) -> tuple[bool, str]:
    """Parse the expression and confirm coercion names are known."""
    unknown = [c.name for c in spec.coercions if c.name not in COERCIONS]
    if unknown:
        return False, f"unknown coercion name(s): {unknown}"
    try:
        if spec.language == "jsonata":
            jsonata.Jsonata(spec.expression)        # constructing compiles/validates
        else:
            sqlglot.parse_one(spec.expression)
    except Exception as e:  # noqa: BLE001
        return False, f"{spec.language} expression does not parse: {e}"
    return True, "ok"


def validate_semantic(
    spec: TransformSpec,
    sample: Frame,
    *,
    target_fields: Optional[list[str]] = None,
    cardinality: str = "1:1",
) -> list[ValidationResult]:
    """Run on a bounded sample and return the validator results (caller applies policy)."""
    sub = Frame(rows=sample.rows[:SAMPLE_SIZE], name=sample.name, source_id=sample.source_id)
    # validation INSPECTS — it reports the quarantine count via the policy engine; it does
    # not itself fail the run on threshold (that gate fires at execute time).
    out, q = execute_transform(spec, sub, enforce_threshold=False)
    results = [
        RowCountLaw().validate(source_rows=len(sub.rows), output_rows=len(out.rows),
                               quarantined=len(q), cardinality=cardinality),
        QuarantineAccounting().validate(source_rows=len(sub.rows),
                                        output_rows=len(out.rows), quarantined=len(q)),
    ]
    if target_fields is not None:
        produced = set(out.columns())
        missing = [f for f in target_fields if f not in produced]
        results.append(ValidationResult(
            "target_schema", not missing, "dataset", "hard_fail",
            "all target fields produced" if not missing else f"missing fields: {missing}"))
    return results

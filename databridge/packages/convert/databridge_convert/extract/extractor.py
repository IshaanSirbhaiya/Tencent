"""U→S extraction orchestrator (C15, 09_FINAL_PRD §11).

Ties the pipeline together for ONE document against ONE target schema:

    pre_extract (lock 30–60% at zero tokens)          §11.1
      → span_selection (relevant blocks for the LLM)  §11.2
      → pydantic_extract (strict structured LLM)      §11.2
      → grounding (THE WALL — every value must appear in source)  §11.4
      → confidence (blend + bands + cross-field validation)       §11.5
      → ExtractionResult (typed record as GroundedValues + per-doc outcome)

Per-doc outcome ∈ {ok, partial, quarantined} (§11.7): a failed REQUIRED field quarantines the
record; a failed OPTIONAL field is nulled+flagged and the record is `partial`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from databridge_core import GroundedValue, SourcePointer
from databridge_harness import Harness

from ..ir import DocumentRecord
from .confidence import CrossFieldViolation, band_of, blend_field_confidence, validate_cross_fields
from .grounding import GroundingOutcome, GroundingSource, ground_field
from .pre_extract import pre_extract
from .pydantic_extract import extract_record
from .span_selection import select_spans
from .target_model import CompiledTarget, FieldSpec, coerce_value, compile_target


@dataclass
class FieldResult:
    name: str
    value: Any
    grounded: Optional[GroundedValue]
    confidence: float
    band: str
    passed: bool
    locked: bool
    reason: str = ""

    @property
    def pointer(self) -> Optional[SourcePointer]:
        return self.grounded.pointer if self.grounded else None


@dataclass
class ExtractionResult:
    target: str
    outcome: str                                  # "ok" | "partial" | "quarantined"
    fields: dict[str, FieldResult] = field(default_factory=dict)
    violations: list[CrossFieldViolation] = field(default_factory=list)
    pre_extract_coverage: float = 0.0
    quarantine_reasons: list[str] = field(default_factory=list)
    ladder_used: str = ""

    def record(self) -> dict:
        """The typed record as plain values (None where a field failed grounding)."""
        return {n: (fr.value if fr.passed else None) for n, fr in self.fields.items()}

    def grounded_record(self) -> dict[str, GroundedValue]:
        return {n: fr.grounded for n, fr in self.fields.items() if fr.grounded is not None}


def _ground_one(fs: FieldSpec, value: Any, source: GroundingSource, *, locked: bool,
                self_report: Optional[float], ocr_word_conf: Optional[float]) -> FieldResult:
    # ground against the RAW value (type-agnostic), then emit the value coerced to the field's
    # declared type so a `number` field is a number, not the captured text.
    outcome = ground_field(fs, value, source)
    out_value = coerce_value(value, fs.grounding)
    if not outcome.passed:
        return FieldResult(name=fs.name, value=out_value, grounded=None, confidence=0.0,
                           band="review", passed=False, locked=locked, reason=outcome.reason)
    conf = blend_field_confidence(outcome.score, locked=locked, self_report=self_report,
                                  ocr_word_conf=ocr_word_conf)
    gv = GroundedValue(value=out_value, pointer=outcome.pointer, confidence=conf) \
        if outcome.pointer is not None else None
    return FieldResult(name=fs.name, value=out_value, grounded=gv, confidence=conf,
                       band=band_of(conf), passed=True, locked=locked)


def extract(
    doc: DocumentRecord, schema: Any, harness: Harness, *,
    name: Optional[str] = None, seed: Optional[int] = 7, source_id: Optional[str] = None,
    self_report: float = 0.9,
) -> ExtractionResult:
    """Extract a typed record from ``doc`` against ``schema`` (Pydantic|JSON-Schema|SQL-DDL)."""
    compiled = compile_target(schema, name=name)
    source = GroundingSource(doc)

    # §11.1 deterministic first pass → locked fields
    pre = pre_extract(doc, compiled)
    known = pre.known_values()

    # §11.2 span selection + strict structured LLM extraction
    spans = select_spans(doc, compiled)
    draft = extract_record(harness, spans, compiled, known, seed=seed, source_id=source_id)
    record = draft.record

    # OCR per-word confidence (if any) → average, fed into the blend
    ocr_conf = _ocr_word_conf(doc)

    # §11.4 grounding wall + §11.5 confidence, per scalar field
    res = ExtractionResult(target=compiled.name, outcome="ok",
                           pre_extract_coverage=pre.coverage, ladder_used=draft.ladder_used)
    quarantine = False
    for fs in compiled.scalar_fields:
        locked = fs.name in pre.locked
        value = record.get(fs.name)
        if value is None:
            # missing field: a required missing field quarantines (§11.2); optional → flagged
            res.fields[fs.name] = FieldResult(fs.name, None, None, 0.0, "review",
                                              passed=False, locked=False,
                                              reason="missing after extraction")
            if fs.required:
                quarantine = True
                res.quarantine_reasons.append(f"required field {fs.name!r} missing")
            continue
        fr = _ground_one(fs, value, source, locked=locked,
                         self_report=None if locked else self_report,
                         ocr_word_conf=ocr_conf)
        res.fields[fs.name] = fr
        if not fr.passed and fs.required:
            quarantine = True
            res.quarantine_reasons.append(f"required field {fs.name!r} failed grounding "
                                          f"(possible fabrication)")

    # repeating records: prefer DETERMINISTIC table rows (§11.1) over the LLM, then ground each.
    for rf in compiled.repeating_fields:
        if not record.get(rf.name) and pre.table_rows:
            mapped = _rows_to_items(pre.table_rows, rf)
            if mapped:
                record[rf.name] = mapped
    _ground_repeating(res, compiled, record, source, self_report)

    # §11.5 cross-field validation
    res.violations = validate_cross_fields(record, compiled)

    if quarantine:
        res.outcome = "quarantined"
    elif (any(not fr.passed for fr in res.fields.values()) or res.violations):
        res.outcome = "partial"
    return res


def _ground_repeating(res: ExtractionResult, compiled: CompiledTarget, record: dict,
                      source: GroundingSource, self_report: float) -> None:
    for rf in compiled.repeating_fields:
        items = record.get(rf.name) or []
        if not isinstance(items, list):
            continue
        for n, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            for sub in rf.item_fields:
                val = item.get(sub.name)
                if val is None:
                    continue
                fr = _ground_one(sub, val, source, locked=False,
                                 self_report=self_report, ocr_word_conf=_ocr_word_conf(source.doc))
                res.fields[f"{rf.name}[{n}].{sub.name}"] = fr
                if not fr.passed and sub.required:
                    res.outcome = "quarantined"
                    res.quarantine_reasons.append(
                        f"line item {rf.name}[{n}].{sub.name} failed grounding")


def _rows_to_items(table_rows: list[dict], rf: FieldSpec) -> list[dict]:
    """Map deterministically-parsed table rows to a repeating field's item dicts by matching
    item field names to table columns (case-insensitive, underscore-insensitive) — §11.1
    'tables → records directly' for the line-item case (H3)."""
    import re as _re
    def norm(s: str) -> str:
        return _re.sub(r"[^a-z0-9]+", "", str(s).lower())
    sub_names = {sf.name: norm(sf.name) for sf in rf.item_fields}
    items: list[dict] = []
    for row in table_rows:
        col_by_norm = {norm(k): k for k in row}
        item = {}
        for sname, nsname in sub_names.items():
            if nsname in col_by_norm:
                item[sname] = row[col_by_norm[nsname]]
        if item:
            items.append(item)
    return items


def _ocr_word_conf(doc: DocumentRecord) -> Optional[float]:
    confs = doc.source_meta.get("ocr_word_confs")
    if not confs:
        return None
    flat = [c for line in confs for c in line] if confs and isinstance(confs[0], list) else confs
    return (sum(flat) / len(flat)) if flat else None

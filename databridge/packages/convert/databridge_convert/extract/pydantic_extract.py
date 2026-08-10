"""LLM structured extraction (C15, 09_FINAL_PRD §11.2).

``harness.complete_structured(response_model=TargetRecordModel, strict)`` over the selected
spans. Pre-extracted fields are passed as **KNOWN** with an explicit instruction not to
contradict them (§11.1 lock); repeating records, multi-page joins (the spans already carry
cross-page text via span selection), and nested objects come for free from the compiled
Pydantic model. The model controls SYNTAX; grounding (next stage) enforces that the source
controls SEMANTICS — this stage is NOT trusted until grounded.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from databridge_harness import Harness

from .span_selection import SelectedSpans
from .target_model import CompiledTarget

TASK_CLASS = "document_extraction"

_SYSTEM = (
    "You extract structured fields from a document into the given schema. RULES:\n"
    "1. Use ONLY information present in the document text. If a field is absent, return null "
    "(or omit it) — NEVER invent or guess a value.\n"
    "2. The KNOWN values below were resolved deterministically and are AUTHORITATIVE — copy "
    "them exactly; do not contradict or 'improve' them.\n"
    "3. Copy values verbatim from the text (you may normalise obvious formatting); every value "
    "you emit will be checked against the source and dropped if it does not appear there.\n"
    "Return ONLY JSON matching the schema."
)


@dataclass
class ExtractionDraft:
    record: dict                     # the parsed record (may include nulls)
    ladder_used: str
    retries: int
    raw: str
    event: Any = None


def _known_block(known: dict) -> str:
    if not known:
        return "KNOWN values: (none resolved deterministically)"
    return "KNOWN values (authoritative — do not contradict):\n" + json.dumps(known, default=str)


def build_messages(spans: SelectedSpans, compiled: CompiledTarget, known: dict) -> list[dict]:
    field_hint = ", ".join(
        f"{f.name}" + (f" ({f.description})" if f.description else "")
        for f in compiled.scalar_fields)
    user = (f"Target fields: {field_hint}\n\n{_known_block(known)}\n\n"
            f"--- DOCUMENT ---\n{spans.text}\n--- END DOCUMENT ---")
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


def extract_record(
    harness: Harness, doc_text_spans: SelectedSpans, compiled: CompiledTarget,
    known: dict, *, seed: Optional[int] = 7, source_id: Optional[str] = None,
) -> ExtractionDraft:
    """Run the strict structured LLM extraction. ``known`` (locked pre-extract fields) is both
    injected into the prompt AND merged over the result so a model that drops/contradicts a
    locked field cannot lose it."""
    messages = build_messages(doc_text_spans, compiled, known)
    result = harness.complete_structured(
        TASK_CLASS, messages, response_model=compiled.model, strict=True,
        seed=seed, source_id=source_id)
    record = result.parsed.model_dump(mode="json")
    # LOCK enforcement: deterministic values always win over the LLM (§11.1).
    for k, v in known.items():
        record[k] = v
    return ExtractionDraft(record=record, ladder_used=result.ladder_used,
                           retries=result.retries, raw=result.raw, event=result.event)

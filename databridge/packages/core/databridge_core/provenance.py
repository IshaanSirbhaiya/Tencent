"""Provenance — the legal system of the codebase.

Every value DataBridge emits is a ``GroundedValue`` carrying a ``SourcePointer`` back
to where it came from. P4: an untraceable value is a bug, not a feature. Making
provenance a wrapper TYPE (not a side-channel log) means the type checker enforces it —
a converter that returns a raw ``str`` instead of a ``GroundedValue`` fails review.

This is the EXTENDED v2 ``SourcePointer`` (02_TRD §A.3 core kinds + 09_FINAL_PRD §17.1
additive kinds: query_cell, workbook_cell, ocr_bbox, email_part, generated_claim, and
the derived-aggregation compression). Built v2 directly per PROMPT 1 because every later
package depends on it and a v1→v2 migration would touch everything.

All models are frozen (immutability prevents pointer mutation after creation).
Consult the ``databridge-ir-conventions`` and ``databridge-provenance-check`` skills
before changing this file.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator

# The closed set of pointer kinds. Core (02_TRD §A.3) + additive v2 (09_FINAL_PRD §17.1).
SourcePointerKind = Literal[
    "table_cell",
    "doc_span",
    "transcript_span",
    "code_node",
    "derived",
    "query_cell",
    "workbook_cell",
    "ocr_bbox",
    "email_part",
    "generated_claim",
]

# Per-kind required locator fields. A pointer is valid iff every listed field is non-None.
# ``workbook_cell`` and ``derived`` carry extra "one-of" rules checked in the validator.
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "table_cell": ("table", "column", "row_id"),
    "doc_span": ("document_id", "char_start", "char_end"),
    "transcript_span": ("document_id", "char_start", "char_end", "timestamp_ms", "segment_idx"),
    "code_node": ("file_path", "ast_node_id"),
    "query_cell": ("snapshot", "query_hash", "row_ordinal", "column"),
    "ocr_bbox": ("media_id", "page", "x0", "y0", "x1", "y1", "ocr_conf"),
    "email_part": ("message_id", "part"),
    "generated_claim": ("claim_id", "facts"),
    "workbook_cell": ("workbook_sha", "sheet"),
    "derived": ("derivation",),
}


class SnapshotIdentity(BaseModel):
    """Identity of the exact source snapshot a value was read from (09_FINAL_PRD §17.2).

    Every IR carries one; artifacts pin ``schema_hash`` and declare snapshot requirements.
    """

    model_config = ConfigDict(frozen=True)

    source_id: str
    as_of: datetime
    content_hash: str
    schema_hash: str
    query_hash: Optional[str] = None


class SourcePointer(BaseModel):
    """Where a value came from. Exactly one locator family is set per ``kind``.

    The ``@model_validator`` enforces, per kind, that the required locator fields are
    present — so constructing a pointer of the wrong shape fails LOUDLY at construction
    time (the property test in tests/ exercises every kind).
    """

    model_config = ConfigDict(frozen=True)

    source_id: str
    kind: SourcePointerKind

    # --- table_cell ---
    table: Optional[str] = None
    column: Optional[str] = None            # also used by query_cell
    row_id: Optional[str] = None

    # --- doc_span / transcript_span ---
    document_id: Optional[str] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    timestamp_ms: Optional[int] = None      # transcripts
    segment_idx: Optional[int] = None       # transcript_span (§17.1 addition)

    # --- code_node ---
    file_path: Optional[str] = None
    ast_node_id: Optional[str] = None

    # --- query_cell (§17.1) ---
    snapshot: Optional[SnapshotIdentity] = None   # also used by derived-compression
    query_hash: Optional[str] = None
    row_ordinal: Optional[int] = None

    # --- workbook_cell (§14.19, §17.1) ---
    workbook_sha: Optional[str] = None
    sheet: Optional[str] = None
    cell: Optional[str] = None              # "B7"
    cell_range: Optional[str] = None        # spec key "range", e.g. "A2:D90"

    # --- ocr_bbox (§17.1) ---
    media_id: Optional[str] = None
    page: Optional[int] = None
    x0: Optional[float] = None
    y0: Optional[float] = None
    x1: Optional[float] = None
    y1: Optional[float] = None
    ocr_conf: Optional[float] = None

    # --- email_part (§17.1) — provenance chains email→attachment→page→span ---
    message_id: Optional[str] = None
    part: Optional[str] = None              # "body" | "header:<name>" | "attachment:<idx>"
    child: Optional["SourcePointer"] = None

    # --- generated_claim (§17.1) ---
    claim_id: Optional[str] = None
    facts: Optional[list["SourcePointer"]] = None   # the cited fact pointers

    # --- derived (core + §17.1 aggregation compression) ---
    derived_from: Optional[list["SourcePointer"]] = None
    derivation: Optional[str] = None        # human-readable rule, e.g. "SUM over 7d window"
    agg_window: Optional[str] = None
    predicate: Optional[str] = None         # compression: the re-runnable predicate
    row_count: Optional[int] = None
    sample_row_ids: Optional[list[str]] = None     # ≤20
    rows_digest: Optional[str] = None

    @model_validator(mode="after")
    def _check_required_for_kind(self) -> "SourcePointer":
        required = _REQUIRED_FIELDS.get(self.kind, ())
        missing = [f for f in required if getattr(self, f) is None]
        if missing:
            raise ValueError(
                f"SourcePointer(kind={self.kind!r}) missing required field(s): "
                f"{', '.join(missing)}"
            )
        # one-of rules
        if self.kind == "workbook_cell" and self.cell is None and self.cell_range is None:
            raise ValueError("workbook_cell requires one of `cell` or `cell_range`")
        if self.kind == "generated_claim" and not self.facts:
            raise ValueError("generated_claim requires a non-empty `facts` list")
        if self.kind == "derived":
            if not self.derived_from and self.predicate is None:
                raise ValueError(
                    "derived requires either `derived_from` (pointers) or `predicate` "
                    "(aggregation compression)"
                )
            if not self.derived_from and self.predicate is not None:
                # Compression path (§17.1) must be VERIFIABLE — re-run predicate against
                # the snapshot and compare the digest — so the full bundle is mandatory.
                missing = [f for f in ("snapshot", "row_count", "rows_digest")
                           if getattr(self, f) is None]
                if missing:
                    raise ValueError(
                        "derived compression (predicate set, no per-row pointers) requires "
                        f"{', '.join(missing)} to stay verifiable"
                    )
        if self.sample_row_ids is not None and len(self.sample_row_ids) > 20:
            raise ValueError("derived compression `sample_row_ids` is capped at 20 (§17.1)")
        return self


class GroundedValue(BaseModel):
    """A value that knows where it came from. The universal currency of DataBridge.

    ``confidence`` 1.0 = directly copied; <1.0 = inferred (drives P6 confidence routing).
    """

    model_config = ConfigDict(frozen=True)

    value: Any
    pointer: SourcePointer
    confidence: float = 1.0


# Resolve the self-referential forward refs (child / facts / derived_from).
SourcePointer.model_rebuild()
GroundedValue.model_rebuild()

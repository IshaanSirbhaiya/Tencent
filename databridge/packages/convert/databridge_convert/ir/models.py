"""Canonical Intermediate Representations (09_FINAL_PRD §7).

The IR layer is the answer to N×N explosion: A adapters in + B out = A+B
implementations, not A×B, because every adapter speaks only to an IR. **Every IR carries
provenance natively** — an adapter that constructs an IR without pointers fails
CONSTRUCTION, not validation. We enforce that by making ``snapshot`` (the SnapshotIdentity
anchor: source_id + content_hash + schema_hash) a REQUIRED field on every IR; row/cell/
node-level pointers derive from it (snapshot + ordinal/PK).

This is the reference implementation for the ``databridge-ir-conventions`` skill.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal, Optional, Union

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, model_validator

from databridge_core import SemanticType, SnapshotIdentity, SourcePointer

JsonValue = Union[str, int, float, Decimal, bool, None, list, dict]


# --- RowBatch (tables; Arrow-backed) --------------------------------------------
class RowBatch(BaseModel):
    """Tabular IR backed by an Apache Arrow RecordBatch. ``snapshot`` is the provenance
    anchor (required); row-level pointers are derived from snapshot + row ordinal/PK."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    data: pa.RecordBatch
    snapshot: SnapshotIdentity                       # REQUIRED provenance anchor
    column_provenance: dict[str, SourcePointer] = {}  # per-column default pointer
    column_semantics: dict[str, SemanticType] = {}    # per-column SemanticType when annotated

    @property
    def columns(self) -> list[str]:
        return self.data.schema.names

    @property
    def num_rows(self) -> int:
        return self.data.num_rows

    def records(self) -> list[dict]:
        return self.data.to_pylist()

    @classmethod
    def from_records(cls, rows: list[dict], snapshot: SnapshotIdentity,
                     **kw) -> "RowBatch":
        cols: list[str] = []
        for r in rows:
            for k in r:
                if k not in cols:
                    cols.append(k)
        batch = pa.RecordBatch.from_pydict({c: [r.get(c) for r in rows] for c in cols}) \
            if cols else pa.RecordBatch.from_pydict({})
        return cls(data=batch, snapshot=snapshot, **kw)


# --- TypedRecord (JSON-like trees, types preserved) -----------------------------
class TypedRecord(BaseModel):
    """JSON-like tree with types preserved. ``pointer_map`` is sparse; the ROOT pointer is
    mandatory — enforced via the required ``snapshot`` anchor (root → snapshot)."""

    model_config = ConfigDict(frozen=True)

    root: JsonValue
    snapshot: SnapshotIdentity                       # REQUIRED (the mandatory root pointer)
    json_schema_ref: Optional[str] = None
    pointer_map: dict[str, SourcePointer] = {}       # JsonPath → pointer

    def records(self) -> list[dict]:
        """Records view for the engine: a list root is the rows; a dict root is one row."""
        if isinstance(self.root, list):
            return [r for r in self.root if isinstance(r, dict)]
        if isinstance(self.root, dict):
            return [self.root]
        return []


# --- DocumentRecord (Markdown + structural blocks) ------------------------------
class Block(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["Heading", "Paragraph", "Table", "ListBlock", "Code", "Image", "PageBreak"]
    text: str = ""
    level: Optional[int] = None                      # Heading level
    rows: Optional[list[list[str]]] = None           # Table cells
    media_id: Optional[str] = None                   # Image → MediaRecord ref
    lang: Optional[str] = None                        # Code fence language


class PageSpan(BaseModel):
    model_config = ConfigDict(frozen=True)
    page: int
    char_start: int
    char_end: int


class CharIndex(BaseModel):
    model_config = ConfigDict(frozen=True)
    spans: dict[int, tuple[int, int]] = {}           # block index → (char_start, char_end)


class DocumentRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    document_id: str
    sha256: str
    snapshot: SnapshotIdentity                       # REQUIRED
    blocks: list[Block] = []
    char_index: CharIndex = CharIndex()
    page_map: Optional[list[PageSpan]] = None
    source_meta: dict = {}                            # headers for email, etc.
    # Per-block provenance (additive, mirrors RowBatch.column_provenance). OCR blocks carry
    # an ``ocr_bbox`` pointer (media_id, page, x0..y1, ocr_conf); email-body/attachment blocks
    # may carry an ``email_part`` pointer. Sparse: only blocks with a non-default pointer.
    block_provenance: dict[int, SourcePointer] = {}  # block index → SourcePointer


# --- WorkbookRecord (Excel; NOT a DocumentRecord, §14) --------------------------
class HeaderGuess(BaseModel):
    model_config = ConfigDict(frozen=True)
    row_idx: int = 0
    confidence: float = 0.0


class SheetRecord(BaseModel):
    """One worksheet. ``cells`` holds the TYPED cached values (calamine); per-cell §14
    flags live in the additive sidecars below so the value grid stays consumable by
    writers/converters while the audit detail is preserved (C10)."""

    model_config = ConfigDict(frozen=True)
    name: str
    visibility: Literal["visible", "hidden", "very_hidden"] = "visible"
    used_range: str = ""
    cells: list[list[Any]] = []                      # typed cell values (cached for formulas)
    row_origin: int = 0                               # 0-based abs row of cells[0][0] (used-range top)
    col_origin: int = 0                               # 0-based abs col of cells[0][0]
    merged_ranges: list[str] = []
    named_ranges_local: dict = {}
    tables: list[str] = []                           # ListObjects
    header_inference: HeaderGuess = HeaderGuess()
    # --- §14 additive sidecars (C10; cellref like "B7" → detail) -------------------
    formula_cells: dict[str, str] = {}               # §14.5  cellref → formula text (=has_formula)
    error_cells: dict[str, str] = {}                 # §14.12 cellref → error code (#N/A, …)
    comments: dict[str, str] = {}                    # §14 fidelity table: comments sidecar
    hidden_rows: list[int] = []                       # §14.10 0-based row indices hidden
    hidden_cols: list[int] = []                       # §14.10 0-based col indices hidden
    ambiguous_date_cols: list[int] = []              # §14.6 numeric-might-be-date (NOT converted)
    warnings: list[str] = []                          # sheet-level audit warnings


class WorkbookMeta(BaseModel):
    model_config = ConfigDict(frozen=True)
    author: Optional[str] = None
    created: Optional[str] = None
    modified: Optional[str] = None
    app: Optional[str] = None
    has_macros: bool = False


class WorkbookRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    workbook_id: str
    sha256: str
    snapshot: SnapshotIdentity                       # REQUIRED
    sheets: list[SheetRecord] = []
    named_ranges_global: dict = {}
    metadata: WorkbookMeta = WorkbookMeta()
    epoch: Literal["1900", "1904"] = "1900"          # §14.6 date epoch from workbook props
    warnings: list[str] = []                          # workbook-level (hidden sheets, epoch…)
    loss_report: list[str] = []                       # §14 fidelity losses (macros, styles…)


# --- TranscriptRecord -----------------------------------------------------------
class Segment(BaseModel):
    model_config = ConfigDict(frozen=True)
    speaker: str = ""
    t_start_ms: int = 0
    t_end_ms: int = 0
    text: str = ""
    char_start: int = 0
    char_end: int = 0


class TranscriptRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    transcript_id: str
    snapshot: SnapshotIdentity                       # REQUIRED
    segments: list[Segment] = []


# --- MediaRecord ----------------------------------------------------------------
class MediaRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    media_id: str
    sha256: str
    snapshot: SnapshotIdentity                       # REQUIRED
    kind: Literal["image", "page_raster"]
    width: int
    height: int
    page_no: Optional[int] = None


# --- GraphRecord ----------------------------------------------------------------
class GraphRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)
    nodes: RowBatch
    edges: RowBatch
    node_id_col: str
    src_col: str
    dst_col: str

    @property
    def snapshot(self) -> SnapshotIdentity:
        return self.nodes.snapshot                   # provenance via its node/edge RowBatches


# --- ConversationSet (internal SFT form) ----------------------------------------
class Turn(BaseModel):
    model_config = ConfigDict(frozen=True)
    role: str
    content: str
    facts_used: list[SourcePointer] = []             # turns carry citations


class Conversation(BaseModel):
    model_config = ConfigDict(frozen=True)
    turns: list[Turn] = []


class ConversationSet(BaseModel):
    model_config = ConfigDict(frozen=True)
    snapshot: SnapshotIdentity                       # REQUIRED
    conversations: list[Conversation] = []


# --- ReportIR (§13.4) -----------------------------------------------------------
class Claim(BaseModel):
    model_config = ConfigDict(frozen=True)
    text: str
    char_start: int = 0
    char_end: int = 0
    fact_pointers: list[SourcePointer] = []          # frozen post-approval


class ReportIR(BaseModel):
    model_config = ConfigDict(frozen=True)
    snapshot: SnapshotIdentity                       # REQUIRED
    blocks: list[Block] = []
    claims: list[Claim] = []


# --- DatasetManifest ------------------------------------------------------------
class ColumnSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    dtype: str = ""
    semantic_type: Optional[SemanticType] = None


class DatasetManifest(BaseModel):
    model_config = ConfigDict(frozen=True)
    snapshot: SnapshotIdentity                       # REQUIRED
    rows: int = 0
    columns: list[ColumnSpec] = []
    splits: Optional[dict] = None
    label_spec: Optional[str] = None                 # LabelCandidateRef
    lineage: Optional[str] = None                    # LineageRef
    scorecard: Optional[dict] = None                 # EvalRef

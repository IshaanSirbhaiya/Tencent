"""Pydantic request/response models (D-FE-7).

Every gateway response is a Pydantic model; backend objects that are NOT Pydantic
(NLResult dataclass, QuarantineStore.report()/audit() dicts) are wrapped here so the wire
shape is `model_dump(mode="json")` end-to-end. Backend objects that ARE Pydantic
(AnnotatedSchema, SchemaExtract, PipelineArtifact) ride as nested `dict` fields already
serialized with model_dump — keeping the top level a typed model.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


# -- ingest / schema ---------------------------------------------------------------
class IngestRequest(BaseModel):
    source_url: str
    source_type: str = "fixture"


class IngestResponse(BaseModel):
    job_id: str
    source_id: str
    status: str = "ready"


# -- company brain (T08) -----------------------------------------------------------
class BrainQueryRequest(BaseModel):
    question: str
    as_of: Optional[str] = None


class BrainIngestRequest(BaseModel):
    source_path: str = "demo_sample.txt"
    text: Optional[str] = None        # inline content (demo); falls back to the demo sample


class BrainPathRequest(BaseModel):
    from_entity: str
    to_entity: str


# -- annotate / confirm / finalize -------------------------------------------------
class QueueCounts(BaseModel):
    review: int
    spot_check: int
    auto: int


class AnnotateRequest(BaseModel):
    schema_extract: dict


class AnnotateResponse(BaseModel):
    draft: dict                       # AnnotatedSchema.model_dump(mode="json"), UNCONFIRMED
    queue_counts: QueueCounts


class Decision(BaseModel):
    table: str
    column: str
    accepted_type: str                # SemanticType value (accept = re-assert same type)


class ConfirmRequest(BaseModel):
    draft: dict
    decisions: list[Decision] = []


class ConfirmResponse(BaseModel):
    draft: dict
    queue_counts: QueueCounts
    remaining_mandatory: int


class FinalizeRequest(BaseModel):
    draft: dict
    confirmed_by: str
    decisions: list[Decision] = []


class FinalizeResponse(BaseModel):
    schema_: dict                     # CONFIRMED AnnotatedSchema; serialized as "schema"

    model_config = {"populate_by_name": True}

    def model_dump(self, **kw):       # emit the field as "schema" on the wire
        d = super().model_dump(**kw)
        if "schema_" in d:
            d["schema"] = d.pop("schema_")
        return d


# -- convert -----------------------------------------------------------------------
class ConvertRequest(BaseModel):
    source_b64: str
    source_format: str
    target_format: str
    transform_spec: dict


class QuarantineView(BaseModel):
    quarantined: int
    by_reason: dict[str, int] = {}
    rate: float = 0.0
    over_threshold: bool = False
    records: list[dict] = []          # per-row payload + reason_code + detail (Panel 4 needs these)
    #: The threshold the converter APPLIED to decide `over_threshold`, and its rule, when the
    #: producer reports them (R-021 Data-page #3). Optional and never defaulted here: an absent
    #: value means "not reported", which the page says, rather than a number this model guessed.
    threshold: Optional[float] = None
    threshold_rule: Optional[str] = None


class ConvertResponse(BaseModel):
    result_b64: str
    quarantine: QuarantineView
    artifact_id: str


# -- ask (replay-first) ------------------------------------------------------------
class RefusalView(BaseModel):
    code: str
    message: str
    nearest_alternative: Optional[str] = None


class AskRequest(BaseModel):
    question: str
    confirmed_schema_id: str = "fintech"


class AskResponse(BaseModel):
    sql: Optional[str] = None
    result: list[dict] = []
    provenance: Optional[dict] = None     # the derived SourcePointer (SQL-as-provenance)
    linked_tables: list[str] = []
    refusal: Optional[RefusalView] = None
    replayed: bool = True                 # always true in the demo (no live LLM)


# -- convert sample / suggested ----------------------------------------------------
class ConvertSampleResponse(BaseModel):
    source_b64: str
    source_format: str
    target_format: str
    transform_spec: dict
    preview: str


class SuggestedResponse(BaseModel):
    questions: list[str]
    injection: str


# -- artifacts ---------------------------------------------------------------------
class ArtifactReplayResponse(BaseModel):
    result_b64: str
    deterministic: bool
    checksum_ok: bool


class ArtifactSummary(BaseModel):
    artifact_id: str
    direction: str
    transform_language: str
    created_at: Optional[str] = None
    checksum: str


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactSummary]

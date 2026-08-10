"""Core shared types: closed vocabularies + the semantic-annotation data model.

The enums are CLOSED sets — that is what makes L3 constrained decoding and E1 evaluation
possible (02_TRD Task 1). When a real column fits nothing, classify it ``unknown`` and
route to a human (P6) rather than widening the vocabulary ad hoc; widening a vocabulary
requires an ADR (P3/P6), because every consumer (L3 grammars, E1 sets, coercion menus,
ML templates) is coupled to the closed set.

The annotation models follow 06_PRD_SEMANTIC_LAYER §3. The FULL ``AnnotatedSchema``
(including ``LabelCandidate``) lives here so packages/semantic (Task 7) only POPULATES
it — it does not redefine it.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class SemanticType(str, Enum):
    """What a column MEANS (02_TRD Task 1). 15 real types + ``unknown``."""

    ENTITY_ID = "entity_id"
    FOREIGN_KEY = "foreign_key"
    TIMESTAMP_CREATED = "timestamp_created"
    TIMESTAMP_EVENT = "timestamp_event"
    AMOUNT_MONEY = "amount_money"
    AMOUNT_COUNT = "amount_count"
    STATUS_CATEGORICAL = "status_categorical"
    FREE_TEXT = "free_text"
    GEOGRAPHIC_LOCATION = "geographic_location"
    PERSONAL_IDENTIFIER = "personal_identifier"
    BOOLEAN_FLAG = "boolean_flag"
    SCORE_RATING = "score_rating"
    DURATION_SECONDS = "duration_seconds"
    URL = "url"
    JSON_BLOB = "json_blob"
    UNKNOWN = "unknown"            # ALWAYS routes to a human (06_PRD §4: blended conf → 0.0)


class TableRole(str, Enum):
    """A table's structural role in the schema graph (02_TRD Task 1, 06_PRD §3).

    Exactly the five roles the specs define — no ``unknown`` member. Unlike
    ``SemanticType`` (where 02_TRD mandates ``unknown``), table-role uncertainty is
    carried by the annotation's ``confidence``, not by widening this closed vocabulary.
    Adding a role requires an ADR (P3/P6).
    """

    ENTITY = "entity"
    EVENT = "event"
    RELATIONSHIP = "relationship"
    LOOKUP = "lookup"
    LOG = "log"


PiiKind = Literal["email", "phone", "name", "national_id", "address", "none"]
MlRole = Literal["feature", "label_candidate", "timestamp", "identifier", "ignore"]
AnnotationProvenance = Literal["deterministic", "llm", "llm+vote", "human"]
Cardinality = Literal["1:1", "N:1", "M:N"]


class ColumnAnnotation(BaseModel):
    """Per-column semantic annotation (06_PRD §3).

    ``confidence`` is the BLENDED value (06_PRD §4), never the raw model self-report.
    ``provenance`` answers "who decided" and drives E1 eval slicing.
    """

    model_config = ConfigDict(frozen=True)

    column: str
    semantic_type: SemanticType
    description: str                                   # one human-readable sentence
    is_pii: bool = False
    pii_kind: PiiKind = "none"
    ml_role: MlRole = "feature"
    confidence: float = 0.0
    confidence_sources: dict = {}                      # {model: .9, structural_agreement: +.05}
    provenance: AnnotationProvenance = "llm"


class TableAnnotation(BaseModel):
    """Per-table annotation. ``summary`` is what Stage C cross-table inference sees."""

    model_config = ConfigDict(frozen=True)

    table: str
    concept: str                                       # "financial transaction"
    role: TableRole
    columns: list[ColumnAnnotation] = []
    summary: str = ""                                  # ≤2 sentences


class RelationshipAnnotation(BaseModel):
    """A named relationship between two tables (06_PRD §3)."""

    model_config = ConfigDict(frozen=True)

    src_table: str
    dst_table: str
    via: str
    name: str                                          # "user PLACES order"
    cardinality: Cardinality
    declared: bool                                     # FK constraint vs heuristic-proposed
    confidence: float = 0.0


class LabelCandidate(BaseModel):
    """A business outcome usable as an ML label (06_PRD §5 — the P5 mechanism).

    ``caveats`` carries the time-lag warning that downstream leakage detection enforces
    (training inside a label's maturation window is a silent-leakage bug to REFUSE).
    """

    model_config = ConfigDict(frozen=True)

    name: str                                          # "fraud", "churn"
    outcome_description: str
    derivation_sql: str                                # human-reviewable pseudocode/SQL
    source_tables: list[str] = []
    positive_rate_estimate: Optional[float] = None
    caveats: list[str] = []
    status: Literal["proposed", "confirmed", "rejected"] = "proposed"


class AnnotatedSchema(BaseModel):
    """The versioned, human-confirmed map of what a source's data MEANS (06_PRD §3).

    ``confirmed_by`` is the type-level P6 gate: downstream consumers REFUSE an
    unconfirmed schema in execute/infer modes. ``confirmed_by is None`` ⇒ not yet
    usable. Versions are append-only — never mutate a published version.
    """

    model_config = ConfigDict(frozen=True)

    source_id: str
    version: int
    tables: list[TableAnnotation] = []
    relationships: list[RelationshipAnnotation] = []
    label_candidates: list[LabelCandidate] = []
    domain: str = "unknown"
    created_at: Optional[datetime] = None
    confirmed_by: Optional[str] = None                 # human principal; None = unusable

    @property
    def is_confirmed(self) -> bool:
        """True only when a human principal has confirmed this version (P6)."""
        return self.confirmed_by is not None


# --- Naming reconciliation -------------------------------------------------------
# 02_TRD Task 1 names these AnnotatedColumn/AnnotatedTable; 06_PRD §3 names them
# ColumnAnnotation/TableAnnotation. Same models — expose both names so either spec's
# vocabulary resolves and packages/semantic (Task 7) can import without duplication.
AnnotatedColumn = ColumnAnnotation
AnnotatedTable = TableAnnotation

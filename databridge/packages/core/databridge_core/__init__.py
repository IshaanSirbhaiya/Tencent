"""databridge_core — shared types, provenance, config, and events.

The dependency root of the monorepo: ``core ← everything`` (ADR-0001). Zero LLM/vendor
imports live here. Import the public surface from this package, not from submodules.
"""
from __future__ import annotations

from .config import (
    ConfigError,
    UnknownTaskClass,
    active_profile,
    load_model_config,
    resolve_model,
)
from .events import PipelineEvent, content_hash
from .provenance import (
    Absent,
    Cell,
    DISCLOSING_CELL_TYPES,
    Denial,
    GroundedValue,
    MissingReason,
    PartialDisclosure,
    SnapshotIdentity,
    SourcePointer,
    SourcePointerKind,
    TransformRecord,
    Transformed,
    Trust,
    Withheld,
    WithholdingRecord,
    assert_cell_states_disjoint,
    assert_withheld_disjoint,
    effective_trust,
    trust_from_parents,
    trust_lub,
)
from .types import (
    AnnotatedColumn,
    AnnotatedSchema,
    AnnotatedTable,
    AnnotationProvenance,
    Cardinality,
    ColumnAnnotation,
    LabelCandidate,
    MlRole,
    PiiKind,
    RelationshipAnnotation,
    SemanticType,
    TableAnnotation,
    TableRole,
)

__all__ = [
    # provenance
    "SourcePointer",
    "SourcePointerKind",
    "GroundedValue",
    "SnapshotIdentity",
    # provenance — trust lattice (spine D3)
    "Trust",
    "trust_from_parents",
    "trust_lub",
    "effective_trust",
    # provenance — withholding, the third cell state (spine D4)
    "WithholdingRecord",
    "Denial",
    "Withheld",
    "Absent",
    # provenance — transformation, the FOURTH cell state (spine O1b)
    "TransformRecord",
    "PartialDisclosure",
    "Transformed",
    "Cell",
    "DISCLOSING_CELL_TYPES",
    "MissingReason",
    "assert_withheld_disjoint",
    "assert_cell_states_disjoint",
    # types — enums
    "SemanticType",
    "TableRole",
    # types — semantic annotation model (06_PRD §3)
    "ColumnAnnotation",
    "TableAnnotation",
    "RelationshipAnnotation",
    "LabelCandidate",
    "AnnotatedSchema",
    "AnnotatedColumn",
    "AnnotatedTable",
    # types — literals
    "PiiKind",
    "MlRole",
    "AnnotationProvenance",
    "Cardinality",
    # config
    "resolve_model",
    "load_model_config",
    "active_profile",
    "ConfigError",
    "UnknownTaskClass",
    # events
    "PipelineEvent",
    "content_hash",
]

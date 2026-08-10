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
    GroundedValue,
    SnapshotIdentity,
    SourcePointer,
    SourcePointerKind,
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

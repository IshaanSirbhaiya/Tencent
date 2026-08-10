"""databridge_convert — the conversion engine (Pillar 2).

PROMPT 5 ships the S→S rule engine: coercions, validators, quarantine, the
propose-validate-confirm-execute lifecycle, and PipelineArtifacts (C05/C06/C07). The
canonical IR layer + adapters (C01–C04) land in PROMPT 6 and re-wire the engine.
"""
from __future__ import annotations

from .artifacts import (
    ArtifactStore,
    FittedRef,
    PipelineArtifact,
    audit,
    build_s2s_artifact,
    execute,
    infer,
    preview,
    replay,
)
from .frame import Frame
from .ir import DocumentRecord, RowBatch, TypedRecord
from .quarantine import QuarantineStore, QuarantineThresholdExceeded
from .router import (
    REGISTRY,
    AdapterInfo,
    ConversionRequest,
    Refusal,
    RoutingDecision,
    TargetSpec,
    UnroutableConversion,
    convert,
    generate_matrix,
    register_adapter,
    route,
)
from .rules.coercions import COERCIONS, CoercionError, apply_coercion
from .rules.engine import execute_transform
from .rules.proposer import propose_transform
from .rules.spec import CoercionUse, TransformSpec
from .rules.validator import validate_semantic, validate_syntactic
from .validators import ValidationProfile, ValidationResult

__all__ = [
    # data + coercions
    "Frame",
    "COERCIONS",
    "CoercionError",
    "apply_coercion",
    "QuarantineStore",
    "QuarantineThresholdExceeded",
    # IR layer (C01)
    "RowBatch",
    "TypedRecord",
    "DocumentRecord",
    # router + adapters (C02)
    "convert",
    "route",
    "generate_matrix",
    "AdapterInfo",
    "register_adapter",
    "REGISTRY",
    "TargetSpec",
    "ConversionRequest",
    "RoutingDecision",
    "Refusal",
    "UnroutableConversion",
    # rule engine (C06)
    "TransformSpec",
    "CoercionUse",
    "execute_transform",
    "propose_transform",
    "validate_syntactic",
    "validate_semantic",
    # validators (C05)
    "ValidationProfile",
    "ValidationResult",
    # artifacts (C07)
    "PipelineArtifact",
    "FittedRef",
    "ArtifactStore",
    "build_s2s_artifact",
    "execute",
    "preview",
    "replay",
    "infer",
    "audit",
]

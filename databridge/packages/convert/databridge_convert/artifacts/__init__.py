"""PipelineArtifact v1 (09_FINAL_PRD §18) — one contract, all directions; the
train/serve-skew killer.

Immutable (frozen): changes create version n+1 with ``supersedes``. **NO PICKLE** — fitted
params go to safetensors (arrays) + params.json (scalars/vocab); unsafe deserialisation is
a declared §21 attack class. One artifact supports all five lifecycles: preview, bulk
execute, replay (determinism), single-record infer (serving path — exists from day one),
and audit (render the artifact as a human-readable report). s2s needs no fitted params yet,
but the FittedRef contract is built now.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from databridge_core import content_hash

from ..frame import Frame
from ..quarantine import QuarantineStore
from ..rules.coercions import apply_coercion
from ..rules.engine import execute_transform
from ..rules.spec import CoercionUse, TransformSpec


# --- reference types (§18) ------------------------------------------------------
class AdapterRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    semver: str = "0.0.0"


class SemanticSchemaRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    source_id: str
    version: int = 1
    confirmed_by: Optional[str] = None


class FittedRef(BaseModel):
    """Fitted params: safetensors for arrays + params.json for scalars/vocab. NEVER pickle."""
    model_config = ConfigDict(frozen=True)
    safetensors_path: Optional[str] = None
    params_json_path: Optional[str] = None


class SnapshotReq(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_hash: str = ""
    note: str = ""


class SchemaRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    fields: list[str] = []
    hash: str = ""


class EvalRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    scorecard: dict = {}


class CompatRules(BaseModel):
    model_config = ConfigDict(frozen=True)
    auto_migrate: list[str] = []     # drift classes allowed to auto-migrate (§20)


class MigrationRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    from_version: int
    to_version: int
    note: str = ""


class PipelineArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: str
    version: int = 1
    supersedes: Optional[str] = None
    direction: Literal["s2s", "u2s", "s2u", "u2u"]
    source_adapter: AdapterRef
    target_adapter: AdapterRef
    source_schema_hash: str = ""
    target_schema_hash: str = ""
    semantic_schema: Optional[SemanticSchemaRef] = None
    transform: TransformSpec
    coercions: list[CoercionUse] = []
    parser_config: dict = {}
    model_routing: dict = {}                 # task_class→model snapshot AT CONFIRMATION
    prompt_template_hashes: dict = {}
    validation_rules: dict = {}              # ValidationProfile overrides
    fitted_params: Optional[FittedRef] = None
    input_snapshot_requirements: Optional[SnapshotReq] = None
    output_schema: Optional[SchemaRef] = None
    eval_scorecard: Optional[EvalRef] = None
    approved_by: Optional[str] = None
    created_at: Optional[datetime] = None
    code_commit: str = ""
    dependency_lock_hash: str = ""
    checksum: str = ""                        # sha256 over canonical serialisation
    compatibility: CompatRules = CompatRules()
    migration_history: list[MigrationRecord] = []

    def _canonical(self) -> dict:
        d = self.model_dump(mode="json")
        d.pop("checksum", None)
        return d

    def with_checksum(self) -> "PipelineArtifact":
        return self.model_copy(update={"checksum": content_hash(self._canonical())})

    def verify_checksum(self) -> bool:
        return self.checksum == content_hash(self._canonical())


def build_s2s_artifact(
    transform: TransformSpec,
    *,
    artifact_id: str,
    source_adapter: str,
    target_adapter: str,
    approved_by: str,
    model_routing: Optional[dict] = None,
    created_at: Optional[datetime] = None,
) -> PipelineArtifact:
    """Construct a confirmed s2s artifact and stamp its checksum."""
    art = PipelineArtifact(
        artifact_id=artifact_id, direction="s2s",
        source_adapter=AdapterRef(name=source_adapter),
        target_adapter=AdapterRef(name=target_adapter),
        transform=transform, coercions=list(transform.coercions),
        model_routing=model_routing or {}, approved_by=approved_by,
        created_at=created_at or datetime.now(timezone.utc),
    )
    return art.with_checksum()


# --- the five lifecycles (§18) --------------------------------------------------
def execute(artifact: PipelineArtifact, frame: Frame) -> tuple[Frame, QuarantineStore]:
    """Bulk execute the confirmed transform."""
    return execute_transform(artifact.transform, frame)


def preview(artifact: PipelineArtifact, frame: Frame, n: int = 5) -> Frame:
    """Run on a small sample for human preview (inspection — no threshold gate)."""
    out, _ = execute_transform(artifact.transform,
                               Frame(rows=frame.rows[:n], name=frame.name),
                               enforce_threshold=False)
    return out


def replay(artifact: PipelineArtifact, frame: Frame) -> Frame:
    """Re-execute for the determinism eval (EV-DET) — must be byte-identical to execute."""
    out, _ = execute_transform(artifact.transform, frame)
    return out


def infer(artifact: PipelineArtifact, record: dict) -> dict:
    """The serving path: apply the IDENTICAL transform to a single record (exists day one)."""
    out, q = execute_transform(artifact.transform, Frame(rows=[record], name="infer"),
                               enforce_threshold=False)
    if not out.rows:
        reason = q.records[0].detail if q.records else "no output"
        raise ValueError(f"infer produced no record (quarantined: {reason})")
    return out.rows[0]


def audit(artifact: PipelineArtifact) -> dict:
    """Render the artifact as a human-readable report (stub — full §12 template later)."""
    return {
        "artifact_id": artifact.artifact_id, "version": artifact.version,
        "direction": artifact.direction,
        "transform_language": artifact.transform.language,
        "coercions": [{"name": c.name, "columns": c.columns} for c in artifact.coercions],
        "unmappable": artifact.transform.unmappable,
        "approved_by": artifact.approved_by,
        "checksum_ok": artifact.verify_checksum(),
        "fitted_params": "none" if artifact.fitted_params is None else "present (safetensors)",
    }


# --- store (no pickle) ----------------------------------------------------------
class ArtifactStore:
    """Persist/load artifacts as JSON (+ safetensors for fitted arrays). NEVER pickle."""

    def save(self, artifact: PipelineArtifact, directory: str | Path) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "artifact.json"
        path.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load(self, directory: str | Path) -> PipelineArtifact:
        path = Path(directory) / "artifact.json"
        art = PipelineArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        if not art.verify_checksum():
            raise ValueError(f"artifact checksum mismatch in {directory} — possible drift/tamper")
        return art

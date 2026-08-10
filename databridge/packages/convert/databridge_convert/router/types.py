"""Routing types (09_FINAL_PRD §9). Every refusal is a typed Refusal{code,message,
nearest_alternative} — testable, and the frontend renders it verbatim."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class TargetSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    modality: Literal["structured", "document", "conversation", "dataset", "graph", "report"]
    format: str
    schema_ref: Optional[str] = None
    options: dict = {}


class ConversionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    source_ref: str
    target: TargetSpec
    mode: Literal["plan", "preview", "execute", "infer", "validate"]
    execution: Literal["batch", "streaming"] = "batch"
    artifact_id: Optional[str] = None
    as_of: Optional[datetime] = None


class Refusal(BaseModel):
    model_config = ConfigDict(frozen=True)
    code: str
    message: str
    nearest_alternative: Optional[str] = None


class RoutingDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    direction: Literal["s2s", "u2s", "s2u", "u2u"]
    subsystem: str
    needs_semantic_confirmation: bool = False
    needs_generation: bool = False
    lossiness: Literal["lossless", "lossy"] = "lossless"
    loss_items: list[str] = []
    needs_human_confirmation: bool = False
    refusals: list[Refusal] = []


class UnroutableConversion(Exception):
    """Raised when routing produces refusals (§9)."""

    def __init__(self, refusals: list[Refusal]):
        self.refusals = refusals
        super().__init__("; ".join(f"{r.code}: {r.message}" for r in refusals))

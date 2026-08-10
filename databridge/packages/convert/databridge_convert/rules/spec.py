"""Transform spec — the declarative, diffable, replayable mapping artifact (§10.2).

The LLM proposes one of these ONCE (plan mode); the engine executes it deterministically
forever after (P3). Both JSONata (tree reshaping) and DuckDB SQL (relational ops) are
representable; coercions are referenced BY NAME so the artifact records exactly what ran.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class CoercionUse(BaseModel):
    """A named coercion applied to specific columns (the artifact records name + params)."""

    model_config = ConfigDict(frozen=True)

    name: str
    params: dict = {}
    columns: list[str] = []


class TransformSpec(BaseModel):
    """A confirmed S→S mapping. ``unmappable`` is the proposer's honesty channel —
    fields it could not map (surfaced for human review, never silently dropped)."""

    model_config = ConfigDict(frozen=True)

    language: Literal["jsonata", "sql"]
    expression: str
    coercions: list[CoercionUse] = []
    unmappable: list[str] = []

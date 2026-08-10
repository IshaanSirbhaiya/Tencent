"""The proposer (09_FINAL_PRD §10.2 plan mode) — the LLM proposes a mapping ONCE.

Constrained to: the operation catalogue + coercion NAMES + an ``unmappable[]`` honesty
channel. It proposes; deterministic validation + human confirm gate it; the engine
executes it forever after. task_class ``rule_proposal`` (routed in config/models.yaml).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from databridge_harness import Harness

from .coercions import COERCIONS
from .spec import CoercionUse, TransformSpec


class _ProposalResult(BaseModel):
    language: Literal["jsonata", "sql"]
    expression: str
    coercions: list[CoercionUse] = []
    unmappable: list[str] = []


def propose_transform(
    harness: Harness,
    *,
    source_sample: list[dict],
    target_fields: list[str],
    source_id: str = "src",
) -> TransformSpec:
    """Ask the model for a single declarative mapping (JSONata or DuckDB SQL)."""
    sys = (
        "You propose ONE declarative data mapping. Choose 'jsonata' (tree reshaping) or "
        "'sql' (DuckDB, relational). The expression maps source records to the target "
        f"fields. Reference coercions ONLY by these names: {sorted(COERCIONS)}. List any "
        "source field you cannot map under 'unmappable' — never silently drop. No "
        "arbitrary math: unit conversions use the named unit_convert coercion only."
    )
    user = (f"Source sample (records): {source_sample[:10]}\n"
            f"Target fields: {target_fields}")
    res = harness.complete_structured(
        "rule_proposal",
        [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        _ProposalResult, strict=True,
    ).parsed
    return TransformSpec(language=res.language, expression=res.expression,
                         coercions=res.coercions, unmappable=res.unmappable)

"""Routing + the convert orchestrator (09_FINAL_PRD §8/§9).

Any (source, target) pair routes via IRs — no direct format-to-format code path exists.
``convert`` is the spine: source adapter → IR → (engine over a Frame bridge) → target IR
→ target adapter. Refusals are fail-loud and typed.
"""
from __future__ import annotations

from typing import Optional

from databridge_core import SnapshotIdentity

from ..frame import Frame
from ..quarantine import QuarantineStore
from ..rules.engine import execute_transform
from ..rules.spec import TransformSpec
from .registry import REGISTRY, AdapterRegistry
from .types import ConversionRequest, RoutingDecision, Refusal, TargetSpec, UnroutableConversion

# modality → conversion-direction side (structured-ish = "s", unstructured-ish = "u")
_SIDE = {"structured": "s", "dataset": "s", "graph": "s",
         "document": "u", "conversation": "u", "report": "u"}


def _direction(src_modality: str, tgt_modality: str) -> str:
    return f"{_SIDE.get(src_modality, 's')}2{_SIDE.get(tgt_modality, 's')}"


def route(source_format: str, target: TargetSpec, *, mode: str = "execute",
          registry: AdapterRegistry = REGISTRY) -> RoutingDecision:
    """Resolve adapters, compute direction + lossiness, and collect typed refusals (§9)."""
    refusals: list[Refusal] = []
    src = registry.get(source_format)
    tgt = registry.get(target.format)

    if src is None:
        refusals.append(Refusal(code="UNKNOWN_FORMAT",
                                message=f"no adapter for source format {source_format!r}",
                                nearest_alternative=", ".join(registry.formats())))
    elif not src.can_read:
        refusals.append(Refusal(code="UNSUPPORTED_READ",
                                message=f"format {source_format!r} cannot be read"))
    if tgt is None:
        refusals.append(Refusal(code="UNKNOWN_FORMAT",
                                message=f"no adapter for target format {target.format!r}",
                                nearest_alternative=", ".join(registry.formats())))
    elif not tgt.can_write:
        refusals.append(Refusal(code="UNSUPPORTED_WRITE",
                                message=f"format {target.format!r} cannot be written"))

    if refusals or src is None or tgt is None:
        return RoutingDecision(direction="s2s", subsystem="rules", refusals=refusals)

    direction = _direction(src.modality, tgt.modality)
    lossy = src.lossiness == "lossy" or tgt.lossiness == "lossy"
    loss_items = (src.limits if src.lossiness == "lossy" else []) + \
                 (tgt.limits if tgt.lossiness == "lossy" else [])
    if lossy and target.options.get("require_lossless"):
        refusals.append(Refusal(code="LOSSY_REFUSED",
                                message=f"{source_format}→{target.format} is lossy",
                                nearest_alternative="drop require_lossless or pick a lossless target"))
    return RoutingDecision(
        direction=direction, subsystem="rules",
        lossiness="lossy" if lossy else "lossless", loss_items=loss_items,
        refusals=refusals,
    )


def _ir_to_frame(ir: object) -> Frame:
    if hasattr(ir, "records"):
        return Frame(rows=ir.records(), name="ir")
    raise TypeError(f"IR {type(ir).__name__} has no records() view for the engine")


def _frame_to_ir(frame: Frame, ir_name: str, snapshot: SnapshotIdentity) -> object:
    from ..ir import RowBatch, TypedRecord
    if ir_name == "RowBatch":
        return RowBatch.from_records(frame.rows, snapshot)
    if ir_name == "TypedRecord":
        return TypedRecord(root=frame.rows, snapshot=snapshot)
    raise ValueError(f"no frame→IR bridge for {ir_name!r}")


def convert(
    source_bytes: bytes,
    source_format: str,
    target: TargetSpec,
    *,
    transform: TransformSpec,
    registry: AdapterRegistry = REGISTRY,
) -> tuple[bytes, RoutingDecision, QuarantineStore]:
    """Full route: source bytes → IR → engine → target IR → target bytes."""
    decision = route(source_format, target, registry=registry)
    if decision.refusals:
        raise UnroutableConversion(decision.refusals)

    src = registry.get(source_format)
    tgt = registry.get(target.format)
    ir_in = src.read(source_bytes)
    frame = _ir_to_frame(ir_in)
    out_frame, q = execute_transform(transform, frame)
    ir_out = _frame_to_ir(out_frame, tgt.ir, ir_in.snapshot)
    return tgt.write(ir_out), decision, q

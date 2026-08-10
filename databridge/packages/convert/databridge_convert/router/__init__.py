"""router/ — IR-mediated routing + the convert orchestrator (C02, 09_FINAL_PRD §9)."""
from __future__ import annotations

from .matrix import generate_matrix
from .registry import REGISTRY, AdapterInfo, AdapterRegistry, register_adapter
from .router import convert, route
from .types import (
    ConversionRequest,
    Refusal,
    RoutingDecision,
    TargetSpec,
    UnroutableConversion,
)

__all__ = [
    "AdapterInfo", "AdapterRegistry", "REGISTRY", "register_adapter",
    "route", "convert", "generate_matrix",
    "ConversionRequest", "TargetSpec", "RoutingDecision", "Refusal", "UnroutableConversion",
]

"""rules/ — the structured→structured rule engine (C05/C06)."""
from __future__ import annotations

from .coercions import COERCIONS, CoercionError, apply_coercion

__all__ = ["COERCIONS", "CoercionError", "apply_coercion"]

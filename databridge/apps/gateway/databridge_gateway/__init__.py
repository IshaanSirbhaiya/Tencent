"""databridge_gateway — the FastAPI gateway behind the DataBridge frontend.

The frontend is a pure render layer (D-FE-2); this gateway owns ALL business logic and is
the single enforcement point for refusals, airgap, and DB safety. It wraps the same package
functions the CLI imports. The Phase-A demo is fully airgapped and replay-first (D-FE-4):
the only model surface is a deterministic FakeBackend — no hosted model is constructible.
"""
from __future__ import annotations

from .main import create_app

__all__ = ["create_app"]

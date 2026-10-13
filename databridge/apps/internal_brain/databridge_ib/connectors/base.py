"""The SourceConnector protocol (plan §4.3) — the seam between a platform and the Internal Brain.

One implementation per platform. Each talks to the platform's HTTP API through an injected
``httpx.Client`` whose ``base_url`` is either the mock platform (``apps/mock_platforms``) or the
real service — the SAME code path, so the mock is a stand-in, not a fork.

Rules every implementation follows:
* ``acl()`` compiles the platform's NATIVE permission model into an ``AclExpr``. A configuration
  outside the documented supported subset compiles to ``nobody(<reason>)`` — fail closed.
* ``check_access()`` asks the SOURCE (Gate 2). Any error, timeout, 429 or ambiguity returns
  ``verdict="forbidden"`` or ``"unverifiable"`` — both deny. It never consults the IB index.
* ``parse_webhook()`` verifies signature + timestamp window first and raises ``WebhookRejected``
  otherwise; events only INVALIDATE — the connector re-fetches truth, never trusts the payload.
* No method ever receives the asker's identity from a request body; ``user`` comes from the
  authenticated principal resolved by the app.
"""
from __future__ import annotations

from typing import Optional, Protocol, Sequence, runtime_checkable

from ..models import AccessCheck, AclExpr, ChangeBatch, ChangeEvent, DirectoryUser, SourceDoc


class WebhookRejected(Exception):
    """Bad signature, stale timestamp or replayed delivery id."""


class ConnectorError(Exception):
    """Transport/API failure. Callers treat it as DENY (Gate 2) or retry later (ingest)."""


@runtime_checkable
class SourceConnector(Protocol):
    platform: str

    def list_changes(self, cursor: Optional[str]) -> ChangeBatch:
        """Everything created/updated/deleted since ``cursor`` (None = full crawl), plus ACL changes.
        Implementations page through the API until exhausted and return the new cursor; the caller
        commits the cursor only after the batch is processed (Codex H19)."""
        ...

    def fetch(self, doc_id: str) -> Optional[SourceDoc]:
        """Current content of one document, or None if it no longer exists (→ VANISHED)."""
        ...

    def acl(self, doc_id: str) -> AclExpr:
        """The document's effective read policy, compiled from native objects (fail-closed)."""
        ...

    def check_access(self, user: DirectoryUser, doc_id: str) -> AccessCheck:
        """Gate 2: ask the source whether ``user`` may read ``doc_id`` NOW, and its version."""
        ...

    def parse_webhook(self, headers: dict[str, str], body: bytes) -> Sequence[ChangeEvent]:
        ...

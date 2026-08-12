"""databridge-outbox — the send gate.

The agent drafts. A human approves. A separate, model-free process sends. This package is the first
two of those three and holds none of the third: there is no transport here and no model here, and
``tests/test_outbox_cannot_send.py`` asserts both against the source rather than against a promise
in a docstring.

Read ``models.py`` first — the recipient rule is stated there in full, and it is the sharpest thing
in the design: an address may come from a committed evidence-ledger row a named human attested to,
or from a human typing it, and from nowhere else.

**Why ``recipients`` is imported lazily.** It is the one module here that reaches into
``databridge_intake``, which in turn pulls in ``databridge_harness`` — the model routing layer. The
dispatcher imports this package to read the outbox and needs none of that, and "the dispatcher has
no model in the loop" is a claim worth being able to prove from the module graph rather than from a
source scan alone. Eager re-exports would have made it false the moment anything imported
``databridge_outbox``. ``tests/test_outbox_cannot_send.py`` pins it, and the lazy names below still
import on first use, so callers see no difference.
"""
from __future__ import annotations

import importlib
from typing import Any

from .drafting import build_outbound_draft
from .models import (
    APPROVED,
    DRAFTED,
    REJECTED,
    SENT,
    Channel,
    DraftRefused,
    DraftStatus,
    NotApproved,
    OutboundDraft,
    OutboxEntry,
    OutboxError,
    Recipient,
    RecipientOrigin,
    RecipientRefused,
    draft_id_for,
)
from .monitors import (
    Condition,
    Monitor,
    MonitorError,
    Monitors,
    Observation,
    load_monitors,
    monitors_path,
)
# Re-exported under a different name ON PURPOSE. `from .monitors import monitors` would bind the
# accessor over the SUBMODULE of the same name, so `databridge_outbox.monitors` would stop being
# the module and `import databridge_outbox.monitors as m` would silently hand back a function.
# (`packages/intake` has the same `rules.py`/`rules()` shape and the same trap.)
from .monitors import monitors as clinic_monitors
from .store import (
    ALLOWED_NEXT,
    DuplicateDraft,
    IllegalTransition,
    OutboxStore,
    UnknownDraft,
)

#: name -> submodule, resolved on first attribute access. See the module docstring.
_LAZY: dict[str, str] = {
    "ConflictingSelections": "recipients",
    "NoAddressOnFile": "recipients",
    "NoCommittedSelection": "recipients",
    "recipient_from_committed_ledger": "recipients",
    "recipient_typed_by_human": "recipients",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f".{module_name}", __name__), name)


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "ALLOWED_NEXT",
    "APPROVED",
    "Channel",
    "Condition",
    "ConflictingSelections",
    "DRAFTED",
    "DraftRefused",
    "DraftStatus",
    "DuplicateDraft",
    "IllegalTransition",
    "Monitor",
    "MonitorError",
    "Monitors",
    "NoAddressOnFile",
    "NoCommittedSelection",
    "NotApproved",
    "Observation",
    "OutboundDraft",
    "OutboxEntry",
    "OutboxError",
    "OutboxStore",
    "REJECTED",
    "Recipient",
    "RecipientOrigin",
    "RecipientRefused",
    "SENT",
    "UnknownDraft",
    "build_outbound_draft",
    "draft_id_for",
    "clinic_monitors",
    "load_monitors",
    "monitors_path",
    "recipient_from_committed_ledger",
    "recipient_typed_by_human",
]

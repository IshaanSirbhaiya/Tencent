"""One helper for turning a caught exception into a response, used everywhere on this boundary.

`phase5-gw-evidence.md` #12, `phase5-gw-graph-trace.md` #9 and `phase5-gw-orchestrator.md` #8 each
found the same MEDIUM independently: an error path built its refusal message with `str(exc)`, so a
caller who could trigger the right failure got back a raw exception message — a filesystem path
(`verify_fleet.EvidenceFleet.status`'s `working_copy`), a SQL fragment, a dependency's own error
text, or an internal type name. None of that is for an HTTP caller; all of it is useful in the
server log, which is where `log.exception(...)` already sends it, in full, on every call site that
matters.

`safe_detail` is the one place that decision is made: log the real exception (with a stack trace,
via `log.exception`) and return a fixed, caller-facing sentence that never repeats `str(exc)`. Call
sites keep their own refusal `code` — this only replaces the free-text `message`.
"""
from __future__ import annotations

import logging
from typing import Optional


def safe_detail(log: logging.Logger, *, context: str, exc: Exception,
                 hint: Optional[str] = None) -> str:
    """Log `exc` in full (with a stack trace) and return a message safe to put in an HTTP body.

    `context` is a short, code-authored description of what was being attempted ("the write did
    not complete", "the adapter could not complete verification") — it never repeats `str(exc)`.
    `hint`, if given, is also code-authored (never an f-string over `exc`) and is appended for a
    caller who needs to know what to do next, not what broke.
    """
    log.exception(context)
    message = f"{context}; see server logs for the detail"
    if hint:
        message = f"{message}. {hint}"
    return message

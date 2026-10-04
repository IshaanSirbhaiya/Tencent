"""No exception text in anything a person reads (R-028 #6; E2E run 5 W-08).

A failure inside a run reaches the answer, the coverage notes, the planner's observations and the
run record as a FIXED sentence plus an opaque reference (``ERR-<hex>``); the exception itself goes
to the server log under that reference. `scrub` is the net under every public string the runtime
assembles: a sentence that still carries an exception's ``ClassName: text`` shape is replaced.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Optional

log = logging.getLogger("databridge.agent_runtime")

#: the shape `str(exc)` gives an exception named in text: ``SomethingError: …`` / ``…Exception(``
_EXC_TEXT = re.compile(r"\b[A-Z]\w*(?:Error|Exception|Exit|Interrupt)\b\s*[:(]")


def error_ref(context: str, exc: Optional[BaseException] = None, **detail: Any) -> str:
    """Log a failure in full under a fresh reference; return the reference (never the text)."""
    ref = f"ERR-{uuid.uuid4().hex[:10]}"
    if exc is not None:
        log.error("[%s] %s %s", ref, context, detail or "", exc_info=exc)
    else:
        log.error("[%s] %s %s", ref, context, detail or "")
    return ref


def failed(sentence: str, context: str, exc: Optional[BaseException] = None,
           **detail: Any) -> str:
    """``"<sentence> (reference ERR-…)"``, the exception logged under that reference."""
    return f"{sentence} (reference {error_ref(context, exc, **detail)})"


def carries_exception_text(text: Any) -> bool:
    return bool(_EXC_TEXT.search(str(text or "")))


def scrub(text: Any, context: str = "a public message carried exception text") -> str:
    """``text`` unchanged, unless it carries an exception's text: then a fixed sentence and a
    reference, with the original in the server log."""
    s = str(text or "")
    if not carries_exception_text(s):
        return s
    ref = error_ref(context, None, original=s)
    return f"a step failed; its detail is in the server log (reference {ref})"


__all__ = ["carries_exception_text", "error_ref", "failed", "scrub"]

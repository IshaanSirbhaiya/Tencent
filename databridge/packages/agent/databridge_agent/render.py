"""The answer is rendered by CODE. The model chooses which values appear; it writes none of them.

``REMEDIATION.md`` Shape E: *model-authored prose where a typed structure belongs.* The old loop
asked the model for ``{"answer": "<prose>"}`` and then screened the prose against a phrase
blacklist. Two findings fell straight out of that:

* **R-10** — the blacklist is bypassable, and unfixably so. "Proceed with candidate 2; their
  demographics correspond to the visitor" matches nothing in ``_IDENTITY_ASSERTIONS`` and never
  will, because there is no finite list of ways to say a thing in English. A blacklist over
  free text is a guess about phrasing dressed up as a control.
* **R-09** — most emitted values needed no pointer. ``check_answer_values`` screened only
  NRIC-shaped strings, so "Coverage is active until 31 December" went out with zero citations.

Both disappear if the model never authors the sentence. Here it does not. It returns *references*
— tool, row number, field name — and this module resolves each one against the ``ToolResult`` the
loop actually recorded, printing the value beside the pointer that value carries. Three properties
follow mechanically rather than by inspection:

1. **Every value in the answer has its own pointer**, because a value with no pointer cannot exist
   in a ``ToolRow`` (``results.py``) and a reference that resolves to ``None`` is rendered under
   *Not found* instead.
2. **Identity wording is ours.** ``NOTES`` is a closed catalogue of code-authored sentences;
   ``AgentAnswer.notes`` may contain nothing else. When a candidate-scoped tool was read, the
   fixed human-selection sentence is appended by code — not requested from the model, not
   paraphrasable by it, and present whether or not the model cooperates.
3. **Injected prose does not reach staff.** A value whose text is instruction-shaped is replaced by
   a fixed redaction line that keeps the citation, so a reviewer can still click through to the
   document without the payload being reproduced in the clinic's own UI.

What is lost: fluency. The answer is a list of values, not a paragraph. That is the trade this
project makes everywhere else too — ``unresolved`` over a plausible default, candidates over a
match — and it is the right one on a surface where a wrong sentence with a valid-looking citation
is the worst available outcome.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Iterable

from databridge_core import SourcePointer

from .frame import untrusted
from .guardrails import looks_like_injection

# How much of a free-text value is printed. A value longer than this is a document excerpt, and a
# report is not a document viewer — the pointer is there to open the source.
MAX_VALUE_CHARS = 240

REDACTED_VALUE = "[withheld — this value contains text addressed to an automated reader]"

# --- the closed set of code-authored sentences ------------------------------------
# `AgentAnswer.notes` holds KEYS from this table, never text. A note the model could phrase is a
# note the model could rephrase, which is exactly R-10.
HUMAN_SELECTION = "human_selection"
INJECTION_SEEN = "injection_seen"
REFS_NOT_RETURNED = "refs_not_returned"
NOTHING_ANSWERED = "nothing_answered"

NOTES: dict[str, str] = {
    HUMAN_SELECTION: (
        "Identity is decided in person. The rows above are registry candidates, not a "
        "determination: a named member of clinic staff checks the NRIC, FIN or passport against "
        "the person at the counter and selects one. Nothing in this report is a ranking, a score "
        "or a recommendation, and this system has no way to make that choice."),
    INJECTION_SEEN: (
        "At least one value read for this answer contains text addressed to an automated reader — "
        "an instruction inside a clinical document. It was not followed, and it is not reproduced "
        "above. The citation still resolves, so a human can open the source and see it in place."),
    REFS_NOT_RETURNED: (
        "Some values this report was asked to carry were not returned by any tool. They are "
        "listed as not found rather than filled in."),
    NOTHING_ANSWERED: (
        "Nothing in the clinic graph answered that. I would rather say so than fill the gap."),
}

HEADING_VALUES = "What the clinic graph holds:"
HEADING_UNRESOLVED = "Asked for, not found:"


def note_text(key: str) -> str:
    """The fixed sentence for a note key. Raises on an unknown key — see ``NOTES``."""
    try:
        return NOTES[key]
    except KeyError:
        raise KeyError(
            f"unknown note {key!r}: staff-facing wording comes from this table and from nowhere "
            f"else, so that identity language cannot be authored by a model (R-10). "
            f"Known: {sorted(NOTES)}") from None


def cite(pointer: SourcePointer) -> str:
    """A short, human-readable rendering of one pointer — enough to find the evidence.

    Deliberately not the pointer's full JSON: this is prose for a member of staff, and the
    machine-readable pointer travels beside it on ``AgentAnswer.fields``.
    """
    if pointer.kind == "doc_span" and pointer.document_id is not None:
        return f"{pointer.document_id} chars {pointer.char_start}–{pointer.char_end}"
    if pointer.kind == "derived":
        return f"derived: {pointer.derivation}"
    if pointer.kind == "table_cell":
        return f"{pointer.table}.{pointer.column} row {pointer.row_id}"
    if pointer.kind == "ocr_bbox":
        return f"{pointer.media_id} page {pointer.page}"
    return f"{pointer.source_id} ({pointer.kind})"


def present(value: Any) -> tuple[str, bool]:
    """Render one value for staff. Returns ``(text, was_redacted)``.

    Text values pass through ``frame.untrusted`` — the same neutraliser used on the way *in* to the
    model — because the answer is a second place attacker-controlled text is rendered, and a role
    marker or a bidi override is no more welcome in a clinic UI than in a prompt.
    """
    if value is None:
        return "not found", False
    if isinstance(value, bool):
        return ("yes" if value else "no"), False
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat(), False
    if isinstance(value, str):
        if looks_like_injection(value):
            return REDACTED_VALUE, True
        text = untrusted(value).strip()
        if len(text) > MAX_VALUE_CHARS:
            text = text[:MAX_VALUE_CHARS].rstrip() + " […]"
        return text or "(empty)", False
    return str(value), False


def render(fields: Iterable[Any], unresolved: Iterable[str], notes: Iterable[str]) -> str:
    """Compose the staff-facing answer. Every character comes from a tool value or from ``NOTES``.

    ``fields`` are ``agent.GroundedField`` objects — passed structurally rather than imported, so
    this module stays free of the loop and can be exercised on its own.
    """
    fields = list(fields)
    unresolved = list(unresolved)
    notes = list(notes)
    lines: list[str] = []

    if fields:
        lines.append(HEADING_VALUES)
        for field in fields:
            text, _ = present(field.value)
            lines.append(f"- {field.key}: {text}  [source: {cite(field.source)}]")
    if unresolved:
        if lines:
            lines.append("")
        lines.append(HEADING_UNRESOLVED)
        lines.extend(f"- {key}" for key in unresolved)
    for key in notes:
        if lines:
            lines.append("")
        lines.append(note_text(key))
    return "\n".join(lines).strip()


__all__ = [
    "HEADING_UNRESOLVED",
    "HEADING_VALUES",
    "HUMAN_SELECTION",
    "INJECTION_SEEN",
    "MAX_VALUE_CHARS",
    "NOTES",
    "NOTHING_ANSWERED",
    "REDACTED_VALUE",
    "REFS_NOT_RETURNED",
    "cite",
    "note_text",
    "present",
    "render",
]

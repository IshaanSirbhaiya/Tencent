"""The public contract — the ONLY shape of this package that may cross an HTTP boundary.

The review's finding, in its own words: *"the safe renderer is not the serialized API contract."*
``AgentAnswer.answer`` is a property, so ``model_dump()`` and FastAPI's serializer omit it
entirely; ``AgentAnswer.tool_calls`` is a field, so they emit the model's raw arguments, exception
strings, adapter notes and **unredacted row values**. A route returning ``AgentAnswer`` therefore
publishes everything ``render.present`` withholds — injected instructions included — and none of
what it produces. Everything ``render.py`` does would be bypassed by a page simply returning the
object it was handed, and nothing in the type would have said so.

So the safe view is a type, not a convention:

* ``answer`` is a **stored string**, rendered by ``render.render`` when this object is built. It
  serializes, because it is a field.
* Values appear as ``AnswerValue``: the key, the **rendered** text (``render.present`` — length
  capped, control characters neutralised, an instruction-shaped value replaced by the fixed
  redaction line), and its pointer. The raw value is not carried, so there is no field through
  which an injected sentence reaches a browser.
* ``notes`` are KEYS into ``render.NOTES``, plus their fixed text. Both halves are code-authored.
* The audit trail appears as ``reads``: a tool name and a status from a closed set. No arguments,
  no exception text, no adapter note, no rows. The full record stays on ``AgentAnswer`` for logs
  and tests, where a human reads it, and does not travel to a page.

``model_config`` is ``extra="forbid"`` and every model here is frozen, so a route cannot staple an
extra field on at the last minute — the same reasoning as ``AgentAnswer``'s own configuration.

``test_response.py`` asserts the exact key set of each of these types. That test is the contract:
if a field is added here, that test fails until somebody writes down that the new field is safe to
publish.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from databridge_core import SourcePointer

from . import render

if TYPE_CHECKING:                                     # pragma: no cover - typing only
    from .agent import AgentAnswer

#: Statuses `ClinicAgent._run_tool` can record. Repeated here so the public surface has a closed
#: vocabulary of its own rather than passing through whatever the loop happens to write.
READ_STATUSES = ("ok", "unknown_tool", "repeated", "schema_violation", "ungrounded_argument",
                 "error", "not_read")


class AnswerValue(BaseModel):
    """One value in the answer, rendered for display and carrying its own pointer.

    ``text`` rather than ``value``: what a page prints must be what the renderer produced, not the
    raw value with the rendering left as an exercise. ``redacted`` says which of the two happened,
    so a UI can style it as a withheld value rather than as data.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    tool: str
    row: int
    field: str
    scope: str
    text: str
    redacted: bool
    citation: str
    source: SourcePointer


class ReadRecord(BaseModel):
    """One tool call, reduced to what is safe to publish: which tool, and how it went.

    ``tool`` is the name of a REGISTERED read, or — when the call named no registered tool — the
    fixed code-authored string ``render.UNKNOWN_TOOL_NAME``. The model's own spelling never
    appears: `Alice_Tan` is a valid identifier, so character-filtering a model-supplied name
    publishes model-authored text with an identifier's shape. ``status`` is checked against
    ``READ_STATUSES`` for the same reason.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str
    status: str


class AgentNote(BaseModel):
    """A code-authored sentence, by key. Both halves come from ``render.NOTES``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    text: str


class AgentResponse(BaseModel):
    """What a route returns. Nothing here was authored by a model.

    Read the field list as the answer to "what does the page get to see?" — and note what is
    absent: the model's plan, its reasons, its arguments, adapter notes, exception messages, and
    the raw text of any value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The staff member's own question, echoed back **through the same renderer every tool value
    #: goes through** — neutralised, length-capped, and replaced by the fixed redaction line if it
    #: is instruction-shaped. It is caller-supplied text, and a route that serialised it verbatim
    #: would be the one field on this type that reaches a page unscreened.
    question: str
    #: The rendered report. A stored field, not a property, which is the whole point of this type.
    answer: str
    refused: bool = False
    refusal_code: str = ""
    #: True whenever a candidate-returning capability was read. A page must show the
    #: human-selection note (it is already in ``answer``) and must not offer a "confirm" action of
    #: its own — identity is settled at the counter, by a person (CLAUDE.md §Hard constraints 1).
    requires_human_selection: bool = False
    values: tuple[AnswerValue, ...] = ()
    unresolved: tuple[str, ...] = ()
    notes: tuple[AgentNote, ...] = ()
    reads: tuple[ReadRecord, ...] = ()
    model_calls: int = 0


def to_response(answer: "AgentAnswer") -> AgentResponse:
    """Project an ``AgentAnswer`` onto the public contract. The only supported route response.

    Deliberately a projection rather than a subset view: every field is copied across explicitly,
    so adding a field to ``AgentAnswer`` — including one carrying model text — cannot widen what a
    page receives without somebody editing this function.
    """
    question, _ = render.present(answer.question)
    values = []
    for field in answer.fields:
        text, redacted = render.present(field.value)
        values.append(AnswerValue(
            key=field.key, tool=field.tool, row=field.row, field=field.field,
            scope=field.scope, text=text, redacted=redacted,
            citation=render.cite(field.source), source=field.source))
    return AgentResponse(
        question=question,
        answer=answer.answer,
        refused=answer.refused,
        refusal_code=answer.refusal_code,
        requires_human_selection=render.HUMAN_SELECTION in answer.notes,
        values=tuple(values),
        unresolved=answer.unresolved,
        notes=tuple(AgentNote(key=key, text=render.note_text(key)) for key in answer.notes),
        reads=tuple(ReadRecord(tool=render.public_name(call.requested, call.registered),
                               status=call.status if call.status in READ_STATUSES else "error")
                    for call in answer.tool_calls),
        model_calls=answer.model_calls,
    )


__all__ = [
    "READ_STATUSES",
    "AgentNote",
    "AgentResponse",
    "AnswerValue",
    "ReadRecord",
    "to_response",
]

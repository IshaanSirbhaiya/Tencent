"""What a read adapter hands back — one pointer per VALUE, never one per result.

Split out of ``tools.py`` so ``sealed.py`` (which produces these) and ``tools.py`` (which declares
the tools that expose them) can both import them without a cycle.

**Why this shape.** The previous ``ToolResult`` carried ``rows: tuple[dict, ...]`` beside a pooled
``citations: tuple[SourcePointer, ...]``, and refused construction only when there were rows and no
pointers at all. That is Shape A from ``REMEDIATION.md`` §1 — *one pointer laundering many values*.
A row of six fields with one traversal pointer produced six values that all looked cited, and the
agent then rendered them into a sentence a member of staff would act on.

The fix is the one the graph layer already made: a pointer **per value**
(``GraphNode.attr_sources``, ``databridge_graph/schema.py``), the same three rules enforced at
construction, and an ``unresolved`` channel naming what was asked for and not found
(``databridge_intake.history._Grounded.field_sources`` is the same shape one layer up). This is
deliberately the *third* implementation of one convention and not a third convention: the field
names (``value_sources``, ``unresolved``, ``source_for``) match the two that already exist, so a
reader who knows one knows all three.

Values are **scalars only**. A nested dict or list would re-open the hole one level down — a
sub-object of five values under one pointer is exactly what this file exists to prevent — so it is
refused at construction rather than screened later.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from databridge_core import SourcePointer

from .frame import untrusted

# What a value may be. Scalars, because a container would hide values from the per-value wall.
SCALAR_TYPES = (str, int, float, bool, dt.datetime, dt.date)


def _values_are_grounded(values: dict, value_sources: dict, what: str) -> None:
    """The per-value grounding wall — the same three rules as ``_attrs_are_grounded``.

    1. a **non-None** value carries its own pointer;
    2. a **None** value carries none — a citation beside "not found" reads as though the absence
       were established;
    3. a pointer may not name a value that is not there, which is how a citation outlives the value
       it was attached to and later vouches for a different one.
    """
    uncited = sorted(k for k, v in values.items() if v is not None and k not in value_sources)
    if uncited:
        raise ValueError(
            f"{what} REFUSED: value(s) {uncited} carry a value with no SourcePointer of their own. "
            f"Ground each one, or set it to None and let `unresolved` name it "
            f"(CLAUDE.md §Hard constraints 2; same wall as GraphNode.attr_sources)")
    cited_but_absent = sorted(k for k in value_sources if k not in values)
    if cited_but_absent:
        raise ValueError(
            f"{what} REFUSED: pointer(s) for value(s) {cited_but_absent} that do not exist")
    cited_but_none = sorted(k for k in value_sources if values.get(k) is None)
    if cited_but_none:
        raise ValueError(
            f"{what} REFUSED: value(s) {cited_but_none} are None but carry a SourcePointer — a "
            f"citation for an absent value")


class ToolRow(BaseModel):
    """One row of a tool's answer: values, and the pointer for each one.

    ``unresolved`` is derived rather than stored, so it cannot drift from the values it describes.
    """

    model_config = ConfigDict(frozen=True)

    values: dict[str, Any] = Field(default_factory=dict)
    value_sources: dict[str, SourcePointer] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _grounded(self) -> "ToolRow":
        bad = sorted(k for k, v in self.values.items()
                     if v is not None and not isinstance(v, SCALAR_TYPES))
        if bad:
            raise ValueError(
                f"ToolRow REFUSED: value(s) {bad} are containers. A nested object carries several "
                f"values under one pointer, which is the laundering this type exists to prevent — "
                f"flatten it into named scalars, each with its own pointer.")
        _values_are_grounded(self.values, self.value_sources, "ToolRow")
        return self

    @property
    def unresolved(self) -> list[str]:
        """Names asked for and not found. Never guessed, never defaulted (Shape D)."""
        return sorted(k for k, v in self.values.items() if v is None)

    def source_for(self, field: str) -> Optional[SourcePointer]:
        return self.value_sources.get(field)

    @classmethod
    def from_one_source(cls, source: SourcePointer, **values: Any) -> "ToolRow":
        """Every named value read from the SAME span.

        Use only when that is literally true — a single sentence on a chit letter really is the
        source of the issuer code and the package code printed in it. It is a convenience for the
        honest case, not a way around the wall: the caller names the one pointer and takes
        responsibility for it, which is what ``clinic_seed`` does when it builds ``attr_sources``.
        """
        present = {k: v for k, v in values.items() if v is not None}
        return cls(values=dict(values), value_sources={k: source for k in present})


class ToolResult(BaseModel):
    """One tool's answer.

    ``note`` is written by the ADAPTER — our code — never by the model, and never composed from
    document text. It carries the things a row cannot: "nothing is blocked today", or the fixed
    statement that a candidate list is not a determination.
    """

    model_config = ConfigDict(frozen=True)

    tool: str
    rows: tuple[ToolRow, ...] = ()
    note: str = ""

    @property
    def citations(self) -> tuple[SourcePointer, ...]:
        """Every pointer any value in this result carries, de-duplicated, in row order.

        Derived — there is no independent pooled list that could contain a pointer belonging to no
        value, which is how a citation ends up vouching for something it never saw.
        """
        seen: list[SourcePointer] = []
        for row in self.rows:
            for key in sorted(row.value_sources):
                pointer = row.value_sources[key]
                if pointer not in seen:
                    seen.append(pointer)
        return tuple(seen)

    @property
    def unresolved(self) -> tuple[str, ...]:
        return tuple(f"[{i}].{k}" for i, row in enumerate(self.rows) for k in row.unresolved)

    def value_at(self, row: int, field: str) -> tuple[Any, Optional[SourcePointer]]:
        """One value and its pointer, or ``(None, None)`` if this result does not hold it.

        The loop resolves the model's references through here, so a reference to something the
        tools never returned yields nothing rather than inventing it.
        """
        if row < 0 or row >= len(self.rows):
            return None, None
        return self.rows[row].values.get(field), self.rows[row].source_for(field)

    def as_data(self, row_offset: int = 0) -> str:
        """The rows as JSON, for the framed observation the model sees.

        Row indices are included because the model answers by REFERENCE — it names
        ``{"tool": ..., "row": 0, "field": "blocked_on"}`` and never types a value — so the indices
        are part of the contract rather than decoration (see ``agent.AnswerPlan``).

        ``row_offset`` keeps those indices unique when one tool is called twice in a turn: the
        loop concatenates a tool's rows in call order, and a reference has to land on the row the
        model was actually shown rather than on whichever call happened to be last.

        JSON, not prose: a sentence inside a JSON string value reads to a model as *the value of a
        field*, which is what it is.
        """
        # Each text value is neutralised BEFORE it is JSON-encoded. `frame.untrusted` runs again
        # over the finished payload, but by then a newline inside a value has become two
        # characters and a line-anchored pattern no longer sees a line — so a document could
        # imitate a role header simply by being inside a JSON string (R-33).
        payload: dict[str, Any] = {
            "tool": self.tool,
            "rows": [{"row": i + row_offset,
                      "values": {k: untrusted(v) if isinstance(v, str) else v
                                 for k, v in r.values.items()},
                      "unresolved": r.unresolved}
                     for i, r in enumerate(self.rows)],
        }
        if self.note:
            payload["note"] = self.note
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

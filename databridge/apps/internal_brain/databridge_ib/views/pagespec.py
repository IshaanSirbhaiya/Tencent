"""The JSON shapes Claude may return for a page (views/composer.py), and the decision.

The composer is free in layout and look (which blocks, in what order, with what headings, which
tone and accent colour), never in content: every sentence, cell, number and date it proposes is
tagged with the source it comes from and checked against that source (views/pagecheck.py). Styles
are closed enums (no colour codes, no CSS, no HTML), so a request like "a table in black" becomes
``style.tone = "black"`` and our components decide what black looks like.

The page is returned as plain JSON (its schema is in the prompt: as a strict output grammar it is
too large to compile) and read leniently: ``read_page`` validates every block on its own, so one
block in an unexpected shape is dropped, not the page; texts and lists over their caps are cut and
an unknown tone or accent becomes the default. The small decision uses the strict structured output
format (``json_schema``).
"""
from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

Accent = Literal["blue", "indigo", "teal", "green", "amber", "orange", "red", "pink", "purple",
                 "gray", "black"]
Tone = Literal["default", "black", "white", "tinted"]
ACCENTS = set(Accent.__args__)
TONES = set(Tone.__args__)


class _S(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PItem(_S):
    """One sentence (or a short phrase) copied or closely restated from ONE source line."""
    text: str
    tags: list[str]


class PStyle(_S):
    tone: Tone = "default"
    accent: Accent = "blue"


class PHero(_S):
    type: Literal["hero"]
    title: str = ""
    eyebrow: str = ""
    summary: list[PItem] = []


class PStat(_S):
    label: str
    value: str
    tag: str


class PStats(_S):
    type: Literal["stats"]
    heading: str = ""
    items: list[PStat]


class PProse(_S):
    type: Literal["prose"]
    heading: str = ""
    items: list[PItem]


class PPerson(_S):
    name: str
    role: str = ""
    items: list[PItem]


class PPeople(_S):
    type: Literal["people"]
    heading: str = ""
    people: list[PPerson]


class PCell(_S):
    text: str = ""
    tags: list[str] = []


class PTable(_S):
    type: Literal["table"]
    heading: str = ""
    style: PStyle = PStyle()
    columns: list[str]
    rows: list[list[PCell]]


class PCard(_S):
    title: str = ""
    items: list[PItem]


class PCards(_S):
    type: Literal["cards"]
    heading: str = ""
    cards: list[PCard]


class PCallout(_S):
    type: Literal["callout"]
    heading: str = ""
    tone: Literal["info", "warning", "success"] = "info"
    items: list[PItem]


class PWhen(_S):
    when: str
    text: str
    tags: list[str]


class PTimeline(_S):
    type: Literal["timeline"]
    heading: str = ""
    items: list[PWhen]


class PSteps(_S):
    type: Literal["steps"]
    heading: str = ""
    items: list[PItem]


class PPoint(_S):
    x: str
    value: float
    raw: str
    tag: str


class PChart(_S):
    type: Literal["chart"]
    heading: str = ""
    kind: Literal["bar", "line"] = "bar"
    series_name: str = ""
    points: list[PPoint]


class PPick(_S):
    """Blocks our code fills from the sources: the chat threads, every source under its headings."""
    type: Literal["conversation", "sections"]
    heading: str = ""


PBlock = Annotated[Union[PHero, PStats, PProse, PPeople, PTable, PCards, PCallout, PTimeline,
                         PSteps, PChart, PPick], Field(discriminator="type")]


class PPage(_S):
    title: str
    accent: Accent = "blue"
    blocks: list[PBlock]


class PDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    render: Literal["page", "chat"]
    title: str
    reason: str


# ------------------------------------------------------------------------------------------------
_DROP = {"minLength", "maxLength", "minItems", "maxItems", "pattern", "title", "default",
         "discriminator", "examples"}


def json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """A plain JSON schema: $refs inlined, constraint keywords dropped, every object closed and
    every property required (property NAMES are kept: "title" is one)."""
    raw = model.model_json_schema()
    defs = raw.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k == "properties":
                    out[k] = {name: walk(sub) for name, sub in v.items()}
                elif k not in _DROP and k != "$defs":
                    out[k] = walk(v)
            if "oneOf" in out:
                out["anyOf"] = out.pop("oneOf")
            if out.get("type") == "object" and "properties" in out:
                out["additionalProperties"] = False
                out["required"] = list(out["properties"])
            if "const" in out and "type" not in out:
                out["type"] = "string"
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(raw)


def prompt_schema() -> str:
    """The page's JSON schema as text for the prompt (documentation; ``read_page`` validates)."""
    return json.dumps(json_schema(PPage), separators=(",", ":"))


_BLOCK = TypeAdapter(PBlock)
_LIST_CAP = {"summary": 4, "items": 16, "people": 30, "cards": 12, "columns": 8, "rows": 40,
             "points": 12, "tags": 3, "blocks": 10}
_STR_CAP = {"text": 400, "value": 40, "when": 40, "x": 40, "raw": 60}


def _trim(node: Any, key: str = "") -> Any:
    """Every list cut to its cap; every string cut (a sentence to 400, a label to 80)."""
    if isinstance(node, dict):
        return {k: _trim(v, k) for k, v in node.items()}
    if isinstance(node, list):
        return [_trim(v, key) for v in node[:_LIST_CAP.get(key, 40)]]
    if isinstance(node, str):
        return node[:_STR_CAP.get(key, 80)]
    return node


def read_page(text: str) -> tuple[Optional[dict[str, Any]], int]:
    """``(page, blocks_dropped)`` from Claude's reply: one JSON object, optionally in one ```json
    fence. ``page`` is ``{"title", "accent", "blocks": [valid blocks as dicts]}``; None when the
    reply is not a JSON object or no block is valid."""
    s = (text or "").strip()
    m = re.match(r"^```(?:json)?\s*\n(.*)\n```$", s, re.S)
    if m:
        s = m.group(1).strip()
    try:
        raw = json.loads(s)
    except ValueError:
        return None, 0
    if not isinstance(raw, dict):
        return None, 0
    raw = _trim(raw)
    blocks, dropped = [], 0
    for b in raw.get("blocks") or []:
        if isinstance(b, dict) and isinstance(b.get("style"), dict):
            st = b["style"]
            b = {**b, "style": {"tone": st.get("tone") if st.get("tone") in TONES else "default",
                                "accent": st.get("accent") if st.get("accent") in ACCENTS
                                else "blue"}}
        try:
            blocks.append(_BLOCK.validate_python(b).model_dump(mode="json"))
        except (ValidationError, TypeError, ValueError):
            dropped += 1
    if not blocks:
        return None, dropped
    accent = raw.get("accent") if raw.get("accent") in ACCENTS else "blue"
    return {"title": str(raw.get("title") or ""), "accent": accent, "blocks": blocks}, dropped


__all__ = ["PPage", "PDecision", "PBlock", "PItem", "PStyle", "json_schema", "prompt_schema",
           "read_page", "Accent", "Tone", "ACCENTS", "TONES"]

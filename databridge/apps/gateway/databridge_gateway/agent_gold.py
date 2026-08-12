"""The deterministic planner behind `POST /intelligence/ask` when there is no API key.

`CLAUDE.md`: *LLM mode is auto-detected — a real model when a key is set, otherwise a deterministic
gold backend so tests and an offline demo always pass.* Every other surface in this repo honours
that with a `FakeBackend` (`databridge_evals.e_intake.gold_harness`, `llm.detect_llm`), and the
agent has to as well: the whole suite runs under `DATABRIDGE_FORCE_GOLD=1`, and so does the demo.

**This is a backend, not a second agent.** It plugs into the same `Harness` behind the same
`agent_reasoning` task class, so the agent loop, the argument policy, the frame, the renderer and
the guardrails are byte-for-byte the code that runs against a real model. What it replaces is only
the two judgement calls the model makes:

* **the plan step** — call each registered tool once, in catalogue order, then stop;
* **the answer step** — report every value the tools actually returned, ordered by how well each
  tool's declared purpose overlaps the question that was asked.

Both are decisions about *presentation*. Neither can widen what the agent may reach: the registry
is sealed before this is ever called, the tools take no arguments, and a reference to a value no
tool returned is recorded as unanswered rather than filled in (`agent._compose`).

**It reads the conversation the way the model does** — through the frame. Tool results are parsed
out of `FRAME_OPEN … FRAME_CLOSE` blocks, which is why a poisoned value reaches this planner
exactly as neutralised as it reaches a hosted model, and why the injection suite exercises the
same path in both modes.

A note on what "gold" means here, since the word is used differently across this repo: this is not
a recorded answer keyed to a fixture. It is a rule, and it produces a real answer over whatever the
graph currently holds — which is what makes an offline demo honest rather than staged.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from databridge_agent import MAX_REPORTED_VALUES
from databridge_agent.frame import ANSWER_INSTRUCTION, FRAME_CLOSE, FRAME_OPEN
from databridge_harness import FakeBackend, Harness

_FRAME = re.compile(re.escape(FRAME_OPEN) + r"[ \t]*kind=(?P<kind>\S+)\n(?P<body>.*?)\n"
                    + re.escape(FRAME_CLOSE), re.S)
_WORD = re.compile(r"[a-z0-9_]{3,}")

# Words that appear in every clinic question and would make every tool look equally relevant.
_STOPWORDS = frozenset({
    "the", "and", "for", "are", "was", "were", "what", "which", "who", "how", "many", "much",
    "this", "that", "these", "those", "from", "with", "has", "have", "had", "our", "your", "not",
    "still", "any", "all", "into", "over", "about", "please", "can", "you", "does", "did", "there",
})


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOPWORDS}


def _frames(messages: list[dict]) -> list[tuple[str, str]]:
    """Every `(kind, body)` the conversation carries, in order. The model's view, exactly."""
    out: list[tuple[str, str]] = []
    for message in messages:
        content = message.get("content") or ""
        for match in _FRAME.finditer(content):
            out.append((match.group("kind"), match.group("body")))
    return out


def _catalogue(messages: list[dict]) -> list[dict]:
    """The tool specs, read back out of the system turn `ToolRegistry.catalogue` wrote.

    Read from the message rather than from a captured registry so this planner cannot see a tool
    the model was not shown — the two must plan over the same closed set or the audit trail stops
    describing the run.
    """
    for message in messages:
        if message.get("role") != "system":
            continue
        for line in (message.get("content") or "").splitlines():
            line = line.strip()
            if not line.startswith("["):
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, list) and all(isinstance(t, dict) and "name" in t
                                                for t in parsed):
                return parsed
    return []


def _question(messages: list[dict]) -> str:
    for kind, body in _frames(messages):
        if kind == "question":
            return body
    return ""


def _plan(messages: list[dict]) -> str:
    """Next unread tool, in catalogue order; `null` once every one has been read."""
    read = {kind.split(":", 1)[1] for kind, _ in _frames(messages)
            if kind.startswith("tool_result:")}
    for spec in _catalogue(messages):
        name = spec.get("name")
        if name and name not in read:
            return json.dumps({"tool": name, "arguments": {},
                               "reason": "read every structural tool once, in catalogue order"})
    return json.dumps({"tool": None, "arguments": {},
                       "reason": "every structural read has been made"})


def _refs_by_tool(messages: list[dict]) -> list[tuple[str, list[dict]]]:
    """`(tool, refs)` per framed tool result, in call order. A `None` value is never referenced."""
    collected: list[tuple[str, list[dict]]] = []
    for kind, body in _frames(messages):
        if not kind.startswith("tool_result:"):
            continue
        try:
            payload = json.loads(body)
        except ValueError:                       # a body the frame mangled is not a report source
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
            continue
        tool = payload.get("tool") or kind.split(":", 1)[1]
        refs = [{"tool": tool, "row": row.get("row", 0), "field": field}
                for row in payload["rows"] if isinstance(row, dict)
                and isinstance(row.get("values"), dict)
                for field in sorted(row["values"]) if row["values"][field] is not None]
        collected.append((tool, refs))
    return collected


def _allocate(needs: list[int], budget: int) -> list[int]:
    """Split `budget` across tools so that **no tool is silently dropped**.

    A report is capped (`MAX_REPORTED_VALUES`), and the obvious rule — most relevant tool first,
    truncate the tail — answers half of a two-part question and says nothing about the half it
    dropped. Asking "which issuers sent the most, and what is still blocked?" and getting only
    issuers, with no note that the rest was cut, is the failure this exists to prevent.

    So: water-filling. Smallest need first, each tool guaranteed an equal share of what is left,
    and whatever a modest tool does not use flows to the ones that need more.
    """
    order = sorted(range(len(needs)), key=lambda i: (needs[i], i))
    allocation = [0] * len(needs)
    remaining = max(0, budget)
    for position, index in enumerate(order):
        share = remaining // (len(order) - position)
        allocation[index] = min(needs[index], share)
        remaining -= allocation[index]
    return allocation


def _report(messages: list[dict]) -> str:
    """Every value the tools returned, most relevant tool first, within a fair share of the cap.

    Ordering is the one judgement made here, and it is made over the tool's DECLARED purpose —
    text this repo wrote and screened at registration — never over the values themselves. A
    document's text cannot promote itself up the report by being interesting.
    """
    question = _tokens(_question(messages))
    relevance = {spec.get("name", ""): len(question & _tokens(
        f"{spec.get('name', '')} {spec.get('description', '')}"))
        for spec in _catalogue(messages)}

    collected = _refs_by_tool(messages)
    order = sorted(range(len(collected)),
                   key=lambda i: (-relevance.get(collected[i][0], 0), i))
    allocation = _allocate([len(collected[i][1]) for i in order], MAX_REPORTED_VALUES)

    refs: list[dict] = []
    for position, index in enumerate(order):
        refs.extend(collected[index][1][:allocation[position]])
    return json.dumps({"report": refs,
                       "reason": "every value the structural reads returned, most relevant "
                                 "tool first, within a fair share of the report cap"})


def gold_planner(*, model: str = "", messages: Optional[list[dict]] = None,
                 guided_json: Any = None, **_: Any) -> str:
    """The `FakeBackend` callable. Which step we are on is read off the last instruction."""
    messages = messages or []
    last = (messages[-1].get("content") if messages else "") or ""
    return _report(messages) if last.strip() == ANSWER_INSTRUCTION.strip() else _plan(messages)


def agent_gold_harness() -> Harness:
    """A `Harness` over the deterministic planner, routed through the real `agent_reasoning` line.

    `cache=None`: the planner is a pure function of the conversation and the conversation grows
    every step, so a cache would only ever be a way for one answer to leak into the next.
    """
    return Harness(FakeBackend(gold_planner), profile="cloud", cache=None)


__all__ = ["agent_gold_harness", "gold_planner"]

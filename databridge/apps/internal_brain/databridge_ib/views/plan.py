"""Which blocks a view shows, read from the person's own instruction by rules (no model).

The instruction is the asker's own words ("a table of owners and dates, then the Slack thread").
Each block kind has a short list of trigger words (English and 简体中文); the blocks are shown in
the order their first trigger appears. No trigger, or only a "report" word, is the automatic layout:
every block the cited sources can actually fill. A "report" word with named blocks puts the key
facts first ("a report with a timeline" opens with what matters). The sources block always comes
last, once: a view never shows a value without saying where it came from.

The instruction never reaches a source, a ledger record or a check; it only picks and orders
blocks (and, when a model is connected, is shown to the model as the layout request).
"""
from __future__ import annotations

import re
from typing import Iterable

#: every block a view can hold, in the automatic layout's order
BLOCKS = ("facts", "table", "chart", "steps", "timeline", "conversation", "sections", "sources")
#: the blocks a view picks by itself when the instruction names none
AUTO = ("facts", "table", "chart", "steps", "timeline", "conversation", "sources")

MAX_INSTRUCTION = 300

_TRIGGERS: dict[str, tuple[str, ...]] = {
    "facts": ("summary", "summarise", "summarize", "overview", "key facts", "key points", "highlights",
              "brief", "tl;dr", "tldr", "bullet", "bullets", "takeaways", "main points",
              "摘要", "总结", "要点", "概述"),
    "table": ("table", "tables", "tabular", "column", "columns", "rows", "grid", "spreadsheet",
              "compare", "comparison", "matrix", "表格", "对比", "表"),
    "chart": ("chart", "charts", "graph", "graphs", "plot", "bar", "bars", "visual", "visualise",
              "visualize", "trend", "图表", "图"),
    "steps": ("steps", "step by step", "checklist", "procedure", "how to", "runbook", "process",
              "instructions", "步骤", "流程", "清单"),
    "timeline": ("timeline", "chronology", "chronological", "dates", "date", "when", "history",
                 "sequence", "schedule", "deadline", "deadlines", "时间线", "时间", "日期"),
    "conversation": ("conversation", "conversations", "convo", "convos", "chat", "chats", "thread",
                     "threads", "discussion", "discussions", "messages", "who said", "slack",
                     "对话", "聊天", "讨论"),
    "sections": ("by source", "by document", "each document", "per document", "each source",
                 "every document", "all documents", "in detail", "details", "detailed", "sections",
                 "full report", "everything", "分节", "详细", "所有文件"),
    "sources": ("sources", "references", "citations", "links", "来源", "引用"),
}
#: words that ask for a report: with named blocks, the key facts open it
_REPORT = ("report", "brief me", "briefing", "one-pager", "dashboard", "报告", "简报")
_CJK = re.compile(r"[㐀-鿿豈-﫿]")


def clean_instruction(text: str) -> str:
    """One line, at most ``MAX_INSTRUCTION`` characters, control characters removed."""
    s = " ".join(str(text or "").split())
    s = "".join(ch for ch in s if ch.isprintable())
    return s[:MAX_INSTRUCTION]


def _positions(text: str, word: str) -> Iterable[int]:
    if _CJK.search(word):
        start = text.find(word)
        while start >= 0:
            yield start
            start = text.find(word, start + 1)
        return
    for m in re.finditer(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text):
        yield m.start()


def plan(instruction: str) -> tuple[list[str], bool]:
    """``(blocks, explicit)``. ``explicit`` is False for the automatic layout (no block named)."""
    text = clean_instruction(instruction).lower()
    first: dict[str, int] = {}
    for kind, words in _TRIGGERS.items():
        hits = [p for w in words for p in _positions(text, w)]
        if hits:
            first[kind] = min(hits)
    named = sorted((k for k in first if k != "sources"), key=lambda k: (first[k], BLOCKS.index(k)))
    if not named:
        return list(AUTO), False
    if "facts" not in named and any(True for w in _REPORT for _p in _positions(text, w)):
        named.insert(0, "facts")
    return [*named, "sources"], True


#: a request that wants structure the chat cannot hold: a page (the rules' decision without Claude)
_PAGE = re.compile(
    r"(?<![a-z0-9])(report|overview|briefing|brief me|dashboard|one-pager|everything|"
    r"each (?:employee|person|member|engineer)|every (?:employee|person|member)|all (?:the )?"
    r"(?:employees|people|members|staff|documents|tickets|issues)|who (?:did|worked|works)|"
    r"table|tabular|chart|graph|timeline|checklist|compare|comparison|by document|"
    r"in (?:black|white|blue|green|red|dark)|as a (?:page|list|grid))(?![a-z0-9])"
    r"|报告|总览|概览|表格|图表|时间线|所有|每个|页面")


def wants_page(text: str) -> bool:
    """The rules' answer to "page or chat?": the person's own words ask for structure."""
    return bool(_PAGE.search(clean_instruction(text).lower()))


def worth_asking(text: str) -> bool:
    """Worth a model call to decide: long enough to need structure, or asking for it outright.
    A short question ("What is the SG FX fee?") stays in the chat without one."""
    return len(clean_instruction(text).split()) >= 7 or wants_page(text)


__all__ = ["plan", "clean_instruction", "wants_page", "worth_asking", "BLOCKS", "AUTO",
           "MAX_INSTRUCTION"]

"""Project scope for explicit new-lead onboarding briefs.

A self-introduction and a list of requested sections are not separate retrieval subjects.
Only this explicit intent uses project-scoped retrieval; ordinary questions keep their existing
ranking. This helper never grants access: both permission gates still apply.
"""
from __future__ import annotations

import re
from dataclasses import replace

from .index import tokens

_ROLE = re.compile(r"\b(?:new|incoming)\s+(?:product|project|team)\s+(?:lead|head|manager)\b", re.I)
_BRIEF = re.compile(r"\bonboarding\b|\bbring me (?:up to speed|up to context)\b", re.I)
_PROJECT = re.compile(r"\b[Pp]roject\s+([A-Z][A-Za-z0-9_-]{1,40})\b")


def project_topic(question: str) -> str | None:
    if not (_ROLE.search(question or "") and _BRIEF.search(question or "")):
        return None
    found = _PROJECT.search(question)
    return found.group(1) if found else None


def in_project(doc: dict | None, topic: str) -> bool:
    """A project is named by its title or source container, not a passing body mention."""
    doc = doc or {}
    subject = set(tokens(topic))
    heading = set(tokens(str(doc.get("title", "")) + " " + str(doc.get("container_name", ""))))
    return bool(subject) and subject <= heading


def extractive_brief(passages, topic: str) -> str:
    """Prefer complete source sentences to headings in the brief's short chat introduction."""
    from .narrate import extractive_answer
    clean = []
    for passage in passages:
        lines = [line for line in passage.text.splitlines()
                 if line.rstrip().endswith((".", "!", "?"))
                 and not re.match(r"\s*(?:pointers|subject|from|to|open question):", line, re.I)]
        clean.append(replace(passage, text="\n".join(lines)))
    return extractive_answer(clean, f"{topic} {topic} merchant onboarding status pilot team blocker")

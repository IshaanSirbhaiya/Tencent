"""Subject scoping for briefing requests that name a project. Deterministic, no model.

A person new to a piece of work often opens with who they are and then lists the sections they
want ("Hi, I'm <name>, the new lead for Project <X>. Bring me up to speed: purpose, status, team,
decisions, blockers, timeline ..."). The retrieval query splits a multi-part question into clauses
and lets each clause find its own evidence (``index.clauses``). Read literally, every section word
("status", "decisions", "timeline") and the self-introduction become retrieval subjects on their
own, so the brief fills up with unrelated work that happens to share a word.

``scope(question)`` rewrites ONLY that shape of question: an explicit briefing request
(onboarding, handover, "bring me up to speed", "catch me up") that names its subject as
``Project <Name>`` / ``<Name> project``. The self-introduction is dropped and the subject name is
added to every remaining clause, so each section is asked about the named project. Any other
question is returned unchanged.

What this does NOT do:
- it never widens access: the rewritten text is only a ranking query over the asker's permitted
  corpus (Gate 1), and every candidate still goes through Gate 2;
- it never names a principal: identity comes from the signed session, not from "I'm <name>";
- it knows no domain or project: the subject is read from the person's own words. If the subject
  occurs in none of the asker's permitted chunks, every clause names an anchor with no permitted
  match (``index.anchors``) and nothing is released, the same outcome as any unanswerable question.
"""
from __future__ import annotations

import re

from typing import Any, Iterable

from .index import _CLAUSE, query_terms, tokens

#: an explicit request for a briefing, not a narrow factual question
_BRIEFING = re.compile(
    r"\b(?:onboard(?:ing)?|handover|hand-over|hand over|up to speed|catch me up|new\s+(?:\w+\s+){0,2}"
    r"(?:lead|owner|manager|member|joiner))\b", re.I)
#: the subject, as the person names it: "Project Atlas", "the Atlas project"
_SUBJECT = re.compile(
    r"\b(?i:project|programme|program|initiative)\s+([A-Z][A-Za-z0-9-]{1,40})\b"
    r"|\b([A-Z][A-Za-z0-9-]{1,40})\s+(?i:project|programme|program|initiative)\b")
#: capitalised words that are not a name ("This project", "Our programme")
_NOT_A_NAME = frozenset("the this that these those our my your their his her its each every which "
                        "what whose any some new current".split())
#: a leading greeting and self-introduction, up to the end of its sentence
_INTRO = re.compile(r"^\s*(?:(?:hi|hello|hey|good\s+\w+)\b[\s,!.]*)?"
                    r"(?:i'm|i\s+am|im|this\s+is|my\s+name\s+is)\b[^.!?\n]*[.!?\n]\s*", re.I)


#: a section list often follows a colon ("an onboarding brief: purpose, status, ...")
_SENTENCE = re.compile(r"[:.!](?=\s|$)")


def subject(question: str) -> str:
    """The project a briefing request names, or ``""``."""
    text = question or ""
    if not _BRIEFING.search(text):
        return ""
    for m in _SUBJECT.finditer(text):
        name = m.group(1) or m.group(2) or ""
        if name and name.lower() not in _NOT_A_NAME:
            return name
    return ""


def sections(question: str) -> list[str]:
    """The requested sections of a briefing on a named project, each scoped to it
    ("Atlas status", "Atlas decisions", ...); ``[]`` for any other question."""
    name = subject(question)
    if not name:
        return []
    body = _INTRO.sub("", question, count=1)
    parts = [p.strip(" .:!") for c in _CLAUSE.split(body) for p in _SENTENCE.split(c)]
    return [f"{name} {p}" for p in parts if p and query_terms(p)] or [name]


def scope(question: str) -> str:
    """The retrieval query for ``question``: scoped to its named project when it is a briefing
    request, otherwise ``question`` unchanged."""
    return ", ".join(sections(question)) or question


def mentions(index: Any, doc_id: str, name: str) -> bool:
    """Whether a document is ABOUT ``name``: its title or its place (space, project, channel,
    drive) names it. A passing mention in the body ("does not apply to Atlas") is not enough."""
    row = index.doc(doc_id) or {}
    want = set(tokens(name))
    have = set(tokens(" ".join(str(row.get(k) or "") for k in ("title", "container_name"))))
    return bool(want) and want <= have


def within(question: str, hits: Iterable[Any], index: Any) -> list:
    """For a briefing on a named project, keep only hits (anything with ``doc_id``) whose document
    names that project; any other question keeps every hit. This only narrows the asker's own
    permitted candidates, so it can never admit a document Gate 1 or Gate 2 would refuse. Plain doc
    id strings are accepted too."""
    hits = list(hits)
    name = subject(question)
    if not name:
        return hits
    seen: dict[str, bool] = {}
    out = []
    for h in hits:
        d = h if isinstance(h, str) else h.doc_id
        if d not in seen:
            seen[d] = mentions(index, d, name)
        if seen[d]:
            out.append(h)
    return out


__all__ = ["scope", "sections", "subject", "mentions", "within"]

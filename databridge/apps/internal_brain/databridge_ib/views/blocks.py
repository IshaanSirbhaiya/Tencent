"""The blocks of a view, filled ONLY from the evidence (deliverables/evidence.py): no value is ever
written by a model or by this code. A block either copies a source's own lines (verbatim, after
NFKC) with their tag, or reuses a deliverable builder whose every cell and point is checked
(deliverables/build.py, verify.py). A block the sources cannot fill is ``None``; the service then
shows a "missing" block saying so, never an empty or invented one.

Model-proposed blocks (only when a model is connected) go through the SAME checks as deliverables:
sentences through ``check_sentence`` against their own tags, tables through ``check_table``, charts
through ``check_chart``; headings and titles must be made of words the person wrote or the sources
contain. A block that fails is dropped and counted.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from ..deliverables import build as dbuild
from ..deliverables.evidence import Evidence, Unit, nfkc
from ..deliverables.verify import (Check, _nums, check_sentence, check_short_text, label_tokens,
                                   unsafe_text, words)
from ..index import STOPWORDS, tokens

MAX_FACTS = 6
MAX_STEPS = 12
MAX_TIMELINE = 10
MAX_MESSAGES = 12
MAX_SECTION_ITEMS = 10
MAX_HEADING = 60
MAX_TITLE = 120

#: lines that are document furniture, not content ("Document ref: …", "[ref …]")
_FURNITURE = re.compile(r"^\[?\s*(?:document\s+)?ref\b", re.I)
_SPEAKER = re.compile(r"^([a-z0-9][a-z0-9._-]{1,40}):\s+(\S.*)$")
_STEP = re.compile(r"^(?:step\s*\d{1,2}\s*[:.)-]|\d{1,2}[.)]\s)", re.I)
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september",
           "october", "november", "december")
_MON = "|".join(m[:3] for m in _MONTHS)
_DATE = re.compile(r"\b(?:(\d{1,2})\s+(" + _MON + r")[a-z]*\.?\s+(\d{4})|(\d{4})-(\d{2})-(\d{2}))\b",
                   re.I)


def _order(tags) -> list[str]:
    return sorted(tags, key=lambda t: int(t[1:]) if t[1:].isdigit() else 999)


def _shown(u: Unit) -> bool:
    """A unit worth showing as content: not furniture, not unsafe, not empty."""
    t = u.text.strip()
    return bool(t) and len(t) >= 3 and not _FURNITURE.match(t) and unsafe_text(t) is None


def is_heading(u: Unit) -> bool:
    """A source's own section heading: a short line with no sentence punctuation (``Owners``,
    ``Rollback``, ``Claim deadline``)."""
    t = u.text.strip()
    return (u.row is None and 0 < len(t) <= 48 and len(t.split()) <= 6
            and not t.endswith((".", "!", "?", ":", ",", ";")) and not _SPEAKER.match(t)
            and not _STEP.match(t) and any(ch.isalpha() for ch in t))


def _terms(*texts: str) -> set[str]:
    return {t for x in texts for t in tokens(x)} - STOPWORDS


def _score(text: str, terms: set[str]) -> int:
    toks = set(tokens(text))
    return 3 * len(terms & toks) + (2 if any(ch.isdigit() for ch in text) else 0)


def _prose(evidence: Mapping[str, Evidence], title_of: Mapping[str, str]):
    """``(tag, position, unit)`` for every content sentence (no heading, no table row, not the
    document's own title repeated as its first line)."""
    for tag in _order(evidence):
        title = nfkc(title_of.get(tag, "")).strip().lower()
        for i, u in enumerate(evidence[tag].units):
            if u.row is not None or not _shown(u) or is_heading(u):
                continue
            if i == 0 and title and u.text.strip().lower() == title:
                continue
            yield tag, i, u


# ------------------------------------------------------------------------------------------------
# deterministic blocks (no model)
# ------------------------------------------------------------------------------------------------
def facts(evidence: Mapping[str, Evidence], title_of: Mapping[str, str], terms: set[str]
          ) -> Optional[dict[str, Any]]:
    """The cited sources' own sentences that share the most words with the question and the
    instruction (numbers count a little), verbatim, in source order."""
    cands = [(-_score(u.text, terms), int(tag[1:]) if tag[1:].isdigit() else 999, i, tag, u.text)
             for tag, i, u in _prose(evidence, title_of)
             if 12 <= len(u.text) <= 300 and not u.text.rstrip().endswith("?")]
    if not cands:
        return None
    picked = sorted(sorted(cands)[:MAX_FACTS], key=lambda c: (c[1], c[2]))
    return {"type": "facts", "items": [{"text": text, "tags": [tag]}
                                       for _s, _n, _i, tag, text in picked]}


def table(evidence: Mapping[str, Evidence], question: str) -> Optional[dict[str, Any]]:
    """A source's own table, copied cell by cell; else the quoted-facts table. Both are the
    deliverable builders (every cell tagged, checked)."""
    t = dbuild.source_table(evidence) or dbuild.quoted_table(evidence, question)
    return {"type": "table", "table": t} if t else None


def chart(evidence: Mapping[str, Evidence]) -> Optional[dict[str, Any]]:
    """A chart of one numeric column of a cited source table (deliverables ``source_chart``: every
    point passes the number checks)."""
    got = dbuild.source_chart(evidence)
    if got is None:
        return None
    c, title, tag = got
    return {"type": "chart", "title": title, "chart": {**c, "from_tag": tag}}


def steps(evidence: Mapping[str, Evidence]) -> Optional[dict[str, Any]]:
    """Numbered steps written in the sources (``Step 1: …``, ``2) …``), in source order."""
    items = [{"text": u.text, "tags": [tag]}
             for tag in _order(evidence) for u in evidence[tag].units
             if u.row is None and _STEP.match(u.text.strip()) and _shown(u)]
    return {"type": "steps", "items": items[:MAX_STEPS]} if len(items) >= 2 else None


def _date_key(text: str) -> Optional[tuple[int, int, int]]:
    m = _DATE.search(text)
    if not m:
        return None
    try:
        if m.group(1):
            mon = next(i for i, x in enumerate(_MONTHS, 1) if x.startswith(m.group(2).lower()[:3]))
            return int(m.group(3)), mon, int(m.group(1))
        return int(m.group(4)), int(m.group(5)), int(m.group(6))
    except (StopIteration, ValueError):
        return None


def timeline(evidence: Mapping[str, Evidence], cites: Mapping[str, Any],
             title_of: Mapping[str, str], terms: set[str]) -> Optional[dict[str, Any]]:
    """Two kinds of entry, oldest first: each cited document on the date OUR records give for its
    current version (with its most relevant sentence), and every source sentence that names a
    date itself (the date shown is the source's own text)."""
    entries: list[tuple[tuple[int, int, int], int, dict[str, Any]]] = []
    best: dict[str, tuple[int, str]] = {}
    for tag, _i, u in _prose(evidence, title_of):
        s = _score(u.text, terms)
        if 12 <= len(u.text) <= 300 and (tag not in best or s > best[tag][0]):
            best[tag] = (s, u.text)
        m = _DATE.search(u.text)
        key = _date_key(u.text)
        if m and key and len(u.text) <= 300:
            entries.append((key, 0, {"when": m.group(0), "dated_by": "source", "text": u.text,
                                     "tags": [tag]}))
    for tag in _order(evidence):
        snap = cites.get(tag)
        raw = str(getattr(snap, "updated_at", "") or "")[:10]
        try:
            d = datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            continue
        text = best.get(tag, (0, ""))[1]
        entries.append(((d.year, d.month, d.day), 1, {
            "when": raw, "dated_by": "record", "label": nfkc(title_of.get(tag, "")),
            "platform": str(getattr(snap, "platform", "") or ""), "text": text, "tags": [tag]}))
    if len(entries) < 2:
        return None
    entries.sort(key=lambda e: (e[0], e[1]))
    return {"type": "timeline", "items": [e for _k, _o, e in entries][-MAX_TIMELINE:]}


def conversation(evidence: Mapping[str, Evidence], cites: Mapping[str, Any],
                 title_of: Mapping[str, str]) -> Optional[dict[str, Any]]:
    """The chat lines of the cited sources (``handle: text``), one thread per source, a sentence
    without a speaker joined to the line before it. The speaker shown is the source's own handle."""
    threads = []
    for tag in _order(evidence):
        msgs: list[dict[str, Any]] = []
        for u in evidence[tag].units:
            if u.row is not None or not _shown(u):
                continue
            m = _SPEAKER.match(u.text.strip())
            if m:
                msgs.append({"speaker": m.group(1), "text": m.group(2), "tags": [tag]})
            elif msgs and len(msgs[-1]["text"]) + len(u.text) < 600:
                msgs[-1]["text"] += " " + u.text.strip()
        if msgs:
            snap = cites.get(tag)
            threads.append({"tag": tag, "title": nfkc(title_of.get(tag, "")),
                            "platform": str(getattr(snap, "platform", "") or ""),
                            "place": nfkc(str(getattr(snap, "container_name", "") or "")),
                            "messages": msgs[:MAX_MESSAGES]})
    return {"type": "conversation", "threads": threads} if threads else None


def sections(evidence: Mapping[str, Evidence], cites: Mapping[str, Any],
             title_of: Mapping[str, str]) -> Optional[dict[str, Any]]:
    """Every cited source as its own section, under its own headings, verbatim. A source table
    is kept as a table."""
    docs = []
    for tag in _order(evidence):
        ev = evidence[tag]
        snap = cites.get(tag)
        parts: list[dict[str, Any]] = []
        n = 0
        for i, u in enumerate(ev.units):
            if u.row is not None or not _shown(u):
                continue
            if i == 0 and u.text.strip().lower() == nfkc(title_of.get(tag, "")).strip().lower():
                continue
            if is_heading(u):
                parts.append({"heading": u.text.strip(), "items": []})
                continue
            if n >= MAX_SECTION_ITEMS:
                continue
            if not parts:
                parts.append({"heading": "", "items": []})
            parts[-1]["items"].append(u.text)
            n += 1
        parts = [p for p in parts if p["items"]]
        tbl = None
        if ev.csv is not None:
            tbl = dbuild.source_table({tag: ev})
        if parts or tbl:
            docs.append({"tag": tag, "title": nfkc(title_of.get(tag, "")),
                         "platform": str(getattr(snap, "platform", "") or ""),
                         "place": nfkc(str(getattr(snap, "container_name", "") or "")),
                         "updated": str(getattr(snap, "updated_at", "") or "")[:10],
                         "parts": parts, "table": tbl})
    return {"type": "sections", "docs": docs} if docs else None


def build_rules(kind: str, evidence: Mapping[str, Evidence], cites: Mapping[str, Any],
                title_of: Mapping[str, str], question: str, terms: set[str]
                ) -> Optional[dict[str, Any]]:
    if kind == "facts":
        return facts(evidence, title_of, terms)
    if kind == "table":
        return table(evidence, question)
    if kind == "chart":
        return chart(evidence)
    if kind == "steps":
        return steps(evidence)
    if kind == "timeline":
        return timeline(evidence, cites, title_of, terms)
    if kind == "conversation":
        return conversation(evidence, cites, title_of)
    if kind == "sections":
        return sections(evidence, cites, title_of)
    return None


# ------------------------------------------------------------------------------------------------
# a model's proposal, checked
# ------------------------------------------------------------------------------------------------
def checked_label(text: str, allowed_words: set[str], *, limit: int) -> Optional[str]:
    """A heading or title the model wrote: short, safe, and every word token one the person wrote
    or the cited sources contain. Otherwise None (the UI then shows its own fixed heading)."""
    t = nfkc(text).strip()
    if not t or len(t) > limit or t.endswith(":") or unsafe_text(t):
        return None
    toks = label_tokens(t)
    return t if toks and set(toks) <= allowed_words else None


def _sentences(items: Sequence[Mapping[str, Any]], evidence, check: Check, limit: int
               ) -> list[dict[str, Any]]:
    out = []
    for s in list(items)[:limit]:
        got = check_sentence(str(s.get("text", "")), s.get("tags") or [], evidence, check,
                             max_words=40)
        if got is not None:
            out.append(got)
    return out


def check_model_block(block: Mapping[str, Any], evidence: Mapping[str, Evidence],
                      cites: Mapping[str, Any], title_of: Mapping[str, str], question: str,
                      terms: set[str], allowed: set[str], check: Check
                      ) -> Optional[dict[str, Any]]:
    """One block of a model's view, checked; None when nothing in it passes."""
    kind = block.get("type")
    heading = checked_label(str(block.get("heading") or ""), allowed, limit=MAX_HEADING)
    out: Optional[dict[str, Any]] = None
    if kind in ("facts", "steps"):
        items = _sentences(block.get("items") or [], evidence, check,
                           MAX_FACTS if kind == "facts" else MAX_STEPS)
        min_n = 1 if kind == "facts" else 2
        out = {"type": kind, "items": items} if len(items) >= min_n else None
    elif kind == "timeline":
        items = []
        for it in list(block.get("items") or [])[:MAX_TIMELINE]:
            tags = [str(x) for x in (it.get("tags") or []) if str(x) in evidence]
            when = nfkc(str(it.get("when", ""))).strip()
            hay = [u for x in tags for u in evidence[x].units]
            got = check_sentence(str(it.get("text", "")), tags, evidence, check, max_words=40)
            if got is None:
                continue
            if not when or len(when) > 40 or not check_short_text(when, hay):
                # kept by the sentence check: it and its values move to dropped (fix #7)
                check.retract("label", len(_nums(got["text"])))
                continue
            items.append({"when": when, "dated_by": "source", **got})
        out = {"type": "timeline", "items": items} if len(items) >= 2 else None
    elif kind == "table":
        spec = block.get("table") or {}
        t = dbuild.check_table(spec, evidence, check, source="model")
        out = {"type": "table", "table": t} if t else None
    elif kind == "chart":
        spec = block.get("chart") or {}
        c = dbuild.model_chart(spec, evidence, check)
        if c is not None:
            title = checked_label(str(spec.get("title") or ""), allowed, limit=MAX_HEADING)
            out = {"type": "chart", "title": title or "", "chart": c}
    elif kind in ("conversation", "sections"):
        out = build_rules(kind, evidence, cites, title_of, question, terms)
    if out is not None and heading:
        out["heading"] = heading
    return out


def allowed_words(evidence: Mapping[str, Evidence], *texts: str) -> set[str]:
    """Every word a model-written heading or title may use: the person's own question and
    instruction, and the cited units."""
    return set(words(" ".join([*texts, *(u.text for ev in evidence.values() for u in ev.units)])))


def block_tags(block: Mapping[str, Any]) -> list[str]:
    """Every tag a block uses, in first-use order."""
    kind = block.get("type")
    out: list[str] = []
    if kind in ("facts", "steps", "timeline"):
        out = [t for it in block["items"] for t in it["tags"]]
    elif kind == "table":
        out = dbuild.content_tags("table", block["table"])
    elif kind == "chart":
        out = dbuild.content_tags("chart", block["chart"])
    elif kind == "conversation":
        out = [th["tag"] for th in block["threads"]]
    elif kind == "sections":
        out = [d["tag"] for d in block["docs"]]
    return list(dict.fromkeys(out))


__all__ = ["facts", "table", "chart", "steps", "timeline", "conversation", "sections",
           "build_rules", "check_model_block", "checked_label", "allowed_words", "block_tags",
           "is_heading"]

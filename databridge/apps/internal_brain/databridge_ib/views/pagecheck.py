"""Claude's page proposal (pagespec.PPage), checked block by block against the evidence. Pure.

What a value has to satisfy to be shown (the deliverables rules, verify.py):

* a sentence (hero summary, prose, a person's contributions, cards, callouts, steps, timeline text)
  passes ``check_sentence`` against the units of ITS OWN tags: every number by key and unit marker,
  every identifier and capitalised name as a whole word, nothing addressed to an AI, no URL;
* a table cell's words all occur in the units of its tags and each of its numbers lies on a number
  span there (an untagged cell may only hold words the person wrote or the sources contain, and no
  digits: a name or a status copied as a label);
* a stat's value occurs in a unit of its tag, word-bounded, and its label is made of allowed words;
* a timeline date occurs in the units of its tags; a chart passes ``check_chart``;
* a person's name is made of words the sources contain (or the display name of a handle that
  speaks in them); a role, a heading, a title or a column label is made of allowed words, else it
  is dropped and the component shows its own fixed label.

Styles are closed enums. A block with nothing left is dropped. Every drop is counted in ``Check``.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence

from databridge_core.value_forms import value_spans

from ..deliverables.evidence import Evidence, nfkc
from ..deliverables.verify import (Check, _value_supported, check_chart, check_sentence,
                                   check_short_text, label_tokens, norm, unsafe_text, words)
from . import blocks as vblocks

LIMIT = 80
#: the plain words of page structure a heading or title may use besides the person's own words and
#: the sources' words ("People", "Key dates", "Open questions"): structure, never content
PAGE_WORDS = frozenset("""
overview summary report brief briefing status project projects people team teams who what when
where why how timeline dates date key facts risks risk blockers blocker open questions question
next steps step contributions contribution owner owners roles role details decisions decision
progress sources source documents conversations conversation discussion discussions numbers
metrics highlights plan plans milestones milestone issues issue tickets ticket work done notes
updates update latest glance current today recent activity at a an and of the by for in on to with
""".split())


def _label(text: str, allowed: set[str], check: Check) -> str:
    got = vblocks.checked_label(str(text or ""), allowed, limit=LIMIT)
    if got is None and str(text or "").strip():
        check.names_dropped += 1
    return got or ""


def _items(items: Sequence[Mapping[str, Any]], evidence, check: Check) -> list[dict[str, Any]]:
    out = []
    for it in items or []:
        got = check_sentence(str(it.get("text", "")), it.get("tags") or [], evidence, check,
                             max_words=45)
        if got is not None:
            out.append(got)
    return out


def _hay(tags: Sequence[str], evidence) -> tuple[str, list]:
    """The text of the tags' units. A CSV row is read as its cells (" | " between them), so a
    cell's number is not glued to its neighbour by a comma ("Growth,0.35%" reads as a grouping)."""
    units = [u for t in dict.fromkeys(tags) if t in evidence for u in evidence[t].units]
    return norm("\n".join(" | ".join(u.cells) if u.cells else u.text for u in units)), units


def _cell(cell: Mapping[str, Any], evidence, allowed: set[str], check: Check) -> dict[str, Any]:
    text = nfkc(str(cell.get("text", ""))).strip()
    tags = [str(t) for t in dict.fromkeys(cell.get("tags") or [])]
    empty = {"text": "", "tags": [], "numeric": False}
    if not text:
        return empty
    check.items_proposed += 1
    if unsafe_text(text):
        check.drop("unsafe")
        return empty
    toks = label_tokens(text)
    spans = [s for s in value_spans(text)]
    nums = [s for s in spans if s.kind == "num"]
    check.values_proposed += len(nums)
    if not tags:                                    # a label copied as is: words only, no digits
        if toks and set(toks) <= allowed and not any(ch.isdigit() for ch in text):
            check.items_kept += 1
            return {"text": text, "tags": [], "numeric": False}
        check.drop("untagged", name=True)
        return empty
    if any(t not in evidence for t in tags):
        check.drop("tag")
        return empty
    hay, _units = _hay(tags, evidence)
    hay_words = set(words(hay))
    if not set(toks) <= hay_words or any(not _value_supported(text, s, hay) for s in spans):
        check.drop("value" if nums else "name", value=bool(nums), name=not nums)
        return empty
    check.items_kept += 1
    check.values_kept += len(nums)
    return {"text": text, "tags": tags, "numeric": bool(nums) and len(toks) <= 2}


def check_page(page: Mapping[str, Any], evidence: Mapping[str, Evidence],
               cites: Mapping[str, Any], title_of: Mapping[str, str], question: str,
               terms: set[str], allowed: set[str], names: set[str], check: Check
               ) -> tuple[list[dict[str, Any]], str, str]:
    """``(blocks, title, accent)``: the blocks that still hold something after the checks."""
    out: list[dict[str, Any]] = []
    accent = str(page.get("accent") or "blue")
    for b in page.get("blocks") or []:
        kind = b.get("type")
        heading = _label(b.get("heading", ""), allowed, check)
        got: Optional[dict[str, Any]] = None
        if kind == "hero":
            summary = _items(b.get("summary") or [], evidence, check)
            title = _label(b.get("title", ""), allowed, check)
            if title or summary:
                got = {"type": "hero", "title": title, "eyebrow": _label(b.get("eyebrow", ""),
                                                                         allowed, check),
                       "summary": summary, "accent": accent}
        elif kind in ("prose", "steps"):
            items = _items(b.get("items") or [], evidence, check)
            if items:
                got = {"type": kind, "heading": heading, "items": items}
        elif kind == "callout":
            items = _items(b.get("items") or [], evidence, check)
            if items:
                got = {"type": "callout", "heading": heading, "tone": b.get("tone", "info"),
                       "items": items}
        elif kind == "stats":
            items = []
            for st in b.get("items") or []:
                check.items_proposed += 1
                check.values_proposed += 1
                tag, value = str(st.get("tag", "")), nfkc(str(st.get("value", ""))).strip()
                hay, _u = _hay([tag], evidence)
                ok = (tag in evidence and value and not unsafe_text(value)
                      and re.search(r"(?<![\w.])" + re.escape(norm(value)) + r"(?![\w])", hay)
                      and all(_value_supported(value, s, hay) for s in value_spans(value)))
                label = vblocks.checked_label(str(st.get("label", "")), allowed, limit=LIMIT)
                if not ok or not label:
                    check.drop("value" if not ok else "label", value=not ok, name=bool(ok))
                    continue
                check.items_kept += 1
                check.values_kept += 1
                items.append({"label": label, "value": value, "tags": [tag]})
            if items:
                got = {"type": "stats", "heading": heading, "items": items}
        elif kind == "people":
            people = []
            for p in b.get("people") or []:
                items = _items(p.get("items") or [], evidence, check)
                name = nfkc(str(p.get("name", ""))).strip()
                ntoks = label_tokens(name)
                if not items or not name or not ntoks or not set(ntoks) <= (allowed | names):
                    if name:
                        check.names_dropped += 1
                    continue
                people.append({"name": name, "role": vblocks.checked_label(
                    str(p.get("role", "")), allowed, limit=LIMIT) or "", "items": items})
            if people:
                got = {"type": "people", "heading": heading, "people": people}
        elif kind == "cards":
            cards = []
            for c in b.get("cards") or []:
                items = _items(c.get("items") or [], evidence, check)
                if items:
                    cards.append({"title": _label(c.get("title", ""), allowed, check),
                                  "items": items})
            if cards:
                got = {"type": "cards", "heading": heading, "cards": cards}
        elif kind == "table":
            cols = []
            for i, col in enumerate(b.get("columns") or [], 1):
                cols.append(_label(col, allowed, check) or f"Column {i}")
            rows = []
            for r in b.get("rows") or []:
                cells = [_cell(c, evidence, allowed, check) for c in list(r)[:len(cols)]]
                cells += [{"text": "", "tags": [], "numeric": False}] * (len(cols) - len(cells))
                if any(c["text"] for c in cells):
                    rows.append(cells)
            if rows:
                st = b.get("style") or {}
                got = {"type": "table", "heading": heading,
                       "style": {"tone": st.get("tone", "default"),
                                 "accent": st.get("accent", accent)},
                       "table": {"source": "model", "columns": cols, "rows": rows,
                                 "truncated": False}}
        elif kind == "timeline":
            items = []
            for it in b.get("items") or []:
                tags = [str(t) for t in (it.get("tags") or []) if str(t) in evidence]
                got_s = check_sentence(str(it.get("text", "")), tags, evidence, check,
                                       max_words=45)
                if got_s is None:
                    continue
                when = nfkc(str(it.get("when", ""))).strip()
                _h, units = _hay(tags, evidence)
                if not when or not check_short_text(when, units):
                    check.items_kept = max(0, check.items_kept - 1)
                    check.drop("label")
                    continue
                items.append({"when": when, "dated_by": "source", **got_s})
            if items:
                got = {"type": "timeline", "heading": heading, "items": items}
        elif kind == "chart":
            spec = {"type": b.get("kind", "bar"), "x_label": "", "y_label": "",
                    "series": [{"name": b.get("series_name", ""), "points": b.get("points") or []}]}
            c = check_chart(spec, evidence, check)
            if c is not None:
                got = {"type": "chart", "heading": heading, "title": heading, "chart": c}
        elif kind in ("conversation", "sections"):
            got = vblocks.build_rules(kind, evidence, cites, title_of, question, terms)
            if got is not None and heading:
                got["heading"] = heading
        if got is not None:
            out.append(got)
    title = _label(page.get("title", ""), allowed, check)
    return out, title, accent


def page_tags(block: Mapping[str, Any]) -> list[str]:
    """Every tag a page block uses, in first-use order."""
    kind = block.get("type")
    out: list[str] = []
    if kind == "hero":
        out = [t for it in block["summary"] for t in it["tags"]]
    elif kind == "stats":
        out = [t for it in block["items"] for t in it["tags"]]
    elif kind == "people":
        out = [t for p in block["people"] for it in p["items"] for t in it["tags"]]
    elif kind == "cards":
        out = [t for c in block["cards"] for it in c["items"] for t in it["tags"]]
    elif kind in ("prose", "callout"):
        out = [t for it in block["items"] for t in it["tags"]]
    else:
        out = vblocks.block_tags(block)
    return list(dict.fromkeys(out))


__all__ = ["check_page", "page_tags"]

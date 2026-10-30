"""Deliverable content: the deterministic builders (no model) and the checked conversion of what a
model proposed. Every builder's output goes through the SAME checks as a model's (Codex review
#13: deterministic output is not exempt), and ``recheck`` re-runs them on stored content against
the sources as they are now (export after a revocation or an edit: Opus review #12)."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional, Sequence

from databridge_core.value_forms import value_spans

from .evidence import Evidence, nfkc
from .verify import (Check, cell_numeric, check_cell, check_chart, check_sentence,
                     check_short_text, unsafe_text, words)

MAX_COLS = 6
MAX_ROWS = 20
QUOTED_ROWS = 8
FIXED_TITLE = {"table": "Table from your sources", "chart": "Chart from your sources",
               "analysis": "Analysis from your sources"}
_BANNED_TITLE = re.compile(r"\b(?:comprehensive|comparative|overview|analysis of|insights?|"
                           r"leveraging|deep dive)\b", re.I)


def _order(tags) -> list[str]:
    return sorted(tags, key=lambda t: int(t[1:]) if t[1:].isdigit() else 999)


def sentence_case_ok(title: str) -> bool:
    """Sentence case: after the first word, a capitalised word is an acronym (all capitals) only."""
    ws = title.split()
    return all(not (w[:1].isupper() and not w.isupper() and any(c.islower() for c in w[1:]))
               for w in ws[1:])


def checked_title(title: str, kind: str, evidence: Mapping[str, Evidence], tags,
                  check: Optional[Check]) -> str:
    """The model's title when it is short, plain, sentence case and made of words from the cited
    units; else the fixed title for the kind (Fable review §6.4: titles are the loudest AI tell)."""
    t = nfkc(title).strip()
    hay = [u for x in _order(tags) if x in evidence for u in evidence[x].units]
    if (t and len(t) <= 60 and not t.endswith(":") and not _BANNED_TITLE.search(t)
            and sentence_case_ok(t) and check_short_text(t, hay)):
        return t
    if t and check is not None:
        check.names_dropped += 1
    return FIXED_TITLE[kind]


# ------------------------------------------------------------------------------------------------
# deterministic builders
# ------------------------------------------------------------------------------------------------
def _first_csv(evidence: Mapping[str, Evidence]) -> Optional[tuple[str, Evidence]]:
    for tag in _order(evidence):
        ev = evidence[tag]
        if ev.csv is not None and len(ev.csv.rows) >= 2:
            return tag, ev
    return None


def source_table(evidence: Mapping[str, Evidence]) -> Optional[dict[str, Any]]:
    """A CSV-shaped block of a cited source, copied cell by cell, every cell tagged (verbatim
    after NFKC). Wider than 6 columns or longer than 20 rows: cut at whole columns and rows, and
    ``truncated`` says so. A cell that is not safe to show (a link, text addressed to an AI) is
    left empty."""
    got = _first_csv(evidence)
    if got is None:
        return None
    tag, ev = got
    header = list(ev.csv.header)
    truncated = len(header) > MAX_COLS or len(ev.csv.rows) > MAX_ROWS
    cols = header[:MAX_COLS]
    rows = []
    for r in ev.csv.rows[:MAX_ROWS]:
        cells = []
        for c in list(r)[:MAX_COLS]:
            text = nfkc(c)
            if unsafe_text(text):
                text = ""
            cells.append({"text": text, "tags": [tag] if text else [],
                          "numeric": bool(text) and cell_numeric(text)})
        rows.append(cells)
    return {"source": "source_table", "from_tag": tag, "columns": [nfkc(c) for c in cols],
            "rows": rows, "truncated": truncated}


def quoted_table(evidence: Mapping[str, Evidence], question: str = "") -> Optional[dict[str, Any]]:
    """No table in the sources: the cited sources' own sentences that share the most words with
    the question (verbatim), with the numbers each one states. Never the answer's sentences."""
    q = set(words(question))
    scored = []
    for tag in _order(evidence):
        for i, u in enumerate(evidence[tag].units):
            if u.row is not None or len(u.text) > 200 or len(u.text) < 12 or unsafe_text(u.text):
                continue
            overlap = len(q & set(words(u.text)))
            has_num = any(s.kind == "num" for s in value_spans(u.text))
            scored.append((-(3 * overlap + (2 if has_num else 0)), int(tag[1:]), i, tag, u.text))
    if not scored:
        return None
    picked = sorted(sorted(scored)[:QUOTED_ROWS], key=lambda r: (r[1], r[2]))
    rows = []
    for _s, _n, _i, tag, text in picked:
        nums = "; ".join(s.raw + ("%" if text[s.end:s.end + 1] == "%" else "")
                         for s in value_spans(text) if s.kind in ("num", "tok", "id"))
        rows.append([{"text": text, "tags": [tag], "numeric": False},
                     {"text": nums, "tags": [tag] if nums else [], "numeric": False}])
    return {"source": "cited_facts", "columns": ["What it says", "Values"], "rows": rows,
            "truncated": False}


def _parse_value(text: str) -> Optional[Any]:
    nums = [s for s in value_spans(text) if s.kind == "num"]
    if len(nums) != 1:
        return None
    try:
        d = Decimal(nums[0].key)
    except InvalidOperation:
        return None
    return int(d) if d == d.to_integral_value() and "." not in nums[0].key else float(d)


def source_chart(evidence: Mapping[str, Evidence]) -> Optional[tuple[dict[str, Any], str, str]]:
    """``(chart, title, tag)`` drawn from a source table with no model: the column whose cells each
    hold exactly one number of one unit, one bar per row that has one, labelled by the first
    column (two when the first repeats). Every point then passes the SAME number checks as a
    model's; fewer than 2 survivors is no chart."""
    got = _first_csv(evidence)
    if got is None:
        return None
    tag, ev = got
    header = list(ev.csv.header)
    best = None
    for c in range(1, len(header)):
        vals = [(r, r[c]) for r in ev.csv.rows if c < len(r) and _parse_value(nfkc(r[c])) is not None]
        if len(vals) >= 2 and (best is None or len(vals) > len(best[1])):
            best = (c, vals)
    if best is None:
        return None
    c, vals = best
    for width in (1, 2):
        xs = [" ".join(nfkc(x) for x in r[:width]) for r, _ in vals]
        if len(set(x.lower() for x in xs)) == len(xs):
            break
    else:
        return None
    # the series name: words every cell shares after its number ("FX margin"), else the header
    rests = []
    for _r, cell in vals:
        t = nfkc(cell)
        n = next(s for s in value_spans(t) if s.kind == "num")
        rests.append(re.sub(r"^[\s%]+", "", t[n.end:]).strip(" ,;"))
    name = rests[0] if rests[0] and all(r == rests[0] for r in rests) else nfkc(header[c])
    points = [{"x": x, "value": _parse_value(nfkc(cell)), "raw": nfkc(cell), "tag": tag}
              for x, (_r, cell) in zip(xs, vals)]
    spec = {"type": "bar", "x_label": nfkc(header[0]) if width == 1 else "", "y_label": name,
            "series": [{"name": name, "points": points}]}
    chart = check_chart(spec, evidence, Check())
    if chart is None:
        return None
    by = nfkc(header[0]) if width == 1 else ""
    title = f"{name} by {by.lower()}" if by else name
    title = title[:1].upper() + title[1:]
    return chart, (title if check_short_text(title, list(ev.units)) else FIXED_TITLE["chart"]), tag


# ------------------------------------------------------------------------------------------------
# what a model proposed, checked
# ------------------------------------------------------------------------------------------------
def model_chart(spec: Mapping[str, Any], evidence: Mapping[str, Evidence], check: Check
                ) -> Optional[dict[str, Any]]:
    return check_chart(spec, evidence, check)


def check_table(content: Mapping[str, Any], evidence: Mapping[str, Evidence], check: Check,
                *, source: str) -> Optional[dict[str, Any]]:
    """Table cells bound to their ROW (the first cell): each cell in one unit with the row label.
    A failing label drops the row; a failing cell becomes empty; a failing column label becomes
    "Column n". No row with a kept cell besides its label: None."""
    rows_in = content.get("rows") or []
    used = {t for r in rows_in for c in r for t in (c.get("tags") or [])}
    hay = [u for t in _order(used) if t in evidence for u in evidence[t].units]
    cols = []
    for i, col in enumerate(content.get("columns") or [], 1):
        col = nfkc(str(col))
        if check_short_text(col, hay):
            cols.append(col)
        else:
            check.names_dropped += 1
            cols.append(f"Column {i}")
    rows = []
    for r in rows_in:
        if not r:
            continue
        label = r[0]
        lab = check_cell(str(label.get("text", "")), label.get("tags") or [], "", evidence, check,
                         is_label=True)
        if lab is None or not lab["text"]:
            for c in r[1:]:
                if str(c.get("text", "")).strip():
                    check.drop("row", value=cell_numeric(str(c.get("text", ""))))
            continue
        out = [lab]
        quoted = source == "cited_facts"            # a quoted sentence and the values it states
        for c in r[1:]:
            got = check_cell(str(c.get("text", "")), c.get("tags") or [], lab["text"], evidence,
                             check, is_label=quoted)
            out.append(got if got is not None else {"text": "", "tags": [], "numeric": False})
        if quoted or any(c["text"] for c in out[1:]):
            rows.append(out)
    if not rows:
        return None
    return {"source": source, "columns": cols, "rows": rows,
            "truncated": bool(content.get("truncated")),
            **({"from_tag": content["from_tag"]} if content.get("from_tag") else {})}


def check_analysis(content: Mapping[str, Any], evidence: Mapping[str, Evidence], check: Check
                   ) -> Optional[dict[str, Any]]:
    sections = []
    for sec in content.get("sections") or []:
        kept = []
        for s in (sec.get("sentences") or [])[:4]:
            got = check_sentence(str(s.get("text", "")), s.get("tags") or [], evidence, check)
            if got is not None:
                kept.append(got)
        if kept:
            sections.append({"heading": sec.get("heading"), "sentences": kept})
    return {"sections": sections} if sections else None


def content_tags(kind: str, content: Optional[Mapping[str, Any]]) -> list[str]:
    """Every tag the content uses, in first-use order."""
    if not content:
        return []
    out: list[str] = []
    if kind == "chart":
        out = [p["tag"] for s in content["series"] for p in s["points"]]
    elif kind == "table":
        out = [t for r in content["rows"] for c in r for t in c["tags"]]
    elif kind == "analysis":
        out = [t for sec in content["sections"] for s in sec["sentences"] for t in s["tags"]]
    return list(dict.fromkeys(out))


def recheck(kind: str, content: Mapping[str, Any], evidence: Mapping[str, Evidence]
            ) -> tuple[Optional[dict[str, Any]], int]:
    """The stored content checked again against ``evidence`` as it is now (documents no longer
    openable are simply absent). ``(content | None, items dropped)``."""
    check = Check()
    if kind == "chart":
        out = check_chart(content, evidence, check)
    elif kind == "table":
        out = check_table(content, evidence, check, source=str(content.get("source")))
    else:
        out = check_analysis(content, evidence, check)
    return out, check.items_dropped


__all__ = ["source_table", "quoted_table", "source_chart", "model_chart", "check_table",
           "check_analysis", "checked_title", "content_tags", "recheck", "FIXED_TITLE"]

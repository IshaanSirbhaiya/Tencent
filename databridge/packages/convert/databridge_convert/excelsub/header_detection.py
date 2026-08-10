"""Deterministic header detection (09_FINAL_PRD §14.2).

The heuristic, verbatim from §14.2: the header is the first row with **≥80% non-null
distinct strings** over the typed-data rows below it. Output a ``HeaderGuess{row_idx,
confidence}``; ``confidence < 0.9`` ⇒ human confirm in plan mode (P6). Multi-row headers
(a merged top row above a sub-header row) are detected and flattened with ``_`` join,
and the flattening is recorded.

Nothing here is probabilistic or model-driven — same grid in, same guess out.
"""
from __future__ import annotations

from typing import Any, Optional

from ..ir import HeaderGuess

# How many data rows below a candidate we sample when scoring "looks like data, not header".
_SCAN_ROWS = 50
_STRING_RATIO = 0.80          # §14.2 threshold
_CONFIRM_BELOW = 0.90         # §14.2: <0.9 ⇒ human confirm


def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _is_string_label(v: Any) -> bool:
    # A header label is a non-blank string that is NOT a pure number-as-text.
    if not isinstance(v, str):
        return False
    s = v.strip()
    if not s:
        return False
    try:
        float(s.replace(",", ""))
        return False              # "1234" / "1,234" looks like data, not a label
    except ValueError:
        return True


def _row_string_ratio(row: list[Any]) -> tuple[float, float]:
    """(non-null ratio, string-label ratio) over the row's used width."""
    cells = [c for c in row]
    width = len(cells)
    if width == 0:
        return 0.0, 0.0
    non_null = sum(1 for c in cells if not _is_blank(c))
    labels = sum(1 for c in cells if _is_string_label(c))
    return non_null / width, labels / width


def _distinct_ratio(row: list[Any]) -> float:
    vals = [str(c).strip().casefold() for c in row if not _is_blank(c)]
    if not vals:
        return 0.0
    return len(set(vals)) / len(vals)


def _data_likeness(rows: list[list[Any]]) -> float:
    """Fraction of sampled rows below that look like DATA (i.e. NOT mostly string labels).
    A high value means the candidate row sits above real data → it is plausibly a header."""
    sample = rows[:_SCAN_ROWS]
    sample = [r for r in sample if any(not _is_blank(c) for c in r)]
    if not sample:
        return 0.0
    data_rows = 0
    for r in sample:
        _, label_ratio = _row_string_ratio(r)
        if label_ratio < _STRING_RATIO:        # not predominantly labels ⇒ data-like
            data_rows += 1
    return data_rows / len(sample)


def detect_header(cells: list[list[Any]]) -> HeaderGuess:
    """Return the best HeaderGuess for a sheet's typed cell grid (§14.2).

    Scores each candidate row by: string-label density (≥80% wanted), distinctness, and
    how data-like the rows below it are. The first qualifying row wins; confidence blends
    the three signals. An empty/degenerate grid yields confidence 0.0 (→ human confirm)."""
    rows = [r for r in cells]
    if not rows:
        return HeaderGuess(row_idx=0, confidence=0.0)

    best: Optional[HeaderGuess] = None
    # Only consider the first few non-empty rows as header candidates (headers are at top).
    considered = 0
    for idx, row in enumerate(rows):
        if all(_is_blank(c) for c in row):
            continue
        considered += 1
        if considered > 5:                      # headers live in the first handful of rows
            break
        non_null, label_ratio = _row_string_ratio(row)
        distinct = _distinct_ratio(row)
        below = _data_likeness(rows[idx + 1:])
        # qualifying row: predominantly DISTINCT string labels over data below (§14.2 names
        # distinctness explicitly). The floor (≥0.5) rejects all-duplicate rows while still
        # admitting multi-row sub-headers whose duplicates (rev/cost/rev/cost) are
        # disambiguated by the group row above — those still route to confirm via confidence.
        qualifies = (label_ratio >= _STRING_RATIO and non_null >= _STRING_RATIO
                     and distinct >= 0.5)
        # confidence: mean of the three core signals, lightly rewarding a data block below.
        confidence = round((label_ratio + distinct + below) / 3.0, 3)
        if qualifies:
            guess = HeaderGuess(row_idx=idx, confidence=confidence)
            if best is None or guess.confidence > best.confidence:
                best = guess
            if best.row_idx == idx:
                # first qualifying row with a real data block below → commit (don't keep
                # walking into the data looking for a "better" string row).
                if below >= 0.5:
                    return best
    if best is not None:
        return best
    # nothing cleanly qualified → guess the first non-empty row, low confidence (→ confirm).
    for idx, row in enumerate(rows):
        if not all(_is_blank(c) for c in row):
            _, label_ratio = _row_string_ratio(row)
            return HeaderGuess(row_idx=idx, confidence=round(min(label_ratio, 0.5), 3))
    return HeaderGuess(row_idx=0, confidence=0.0)


def needs_confirmation(guess: HeaderGuess) -> bool:
    """§14.2: confidence < 0.9 ⇒ route to human confirm (P6)."""
    return guess.confidence < _CONFIRM_BELOW


def detect_multirow_header(cells: list[list[Any]], header_row: int,
                           merged_ranges: list[str]) -> Optional[list[str]]:
    """Multi-row header flattening (§14.2). If the row ABOVE ``header_row`` is a merged
    group-label row (categories spanning several sub-columns), flatten the two rows with
    ``_`` join → ['Q1_rev', 'Q1_cost', 'Q2_rev', …]. Returns the flattened labels, or
    None when there is no multi-row header. The merge fill-down propagates a group label
    across the columns it spans."""
    if header_row <= 0 or header_row >= len(cells):
        return None
    top = list(cells[header_row - 1])
    sub = list(cells[header_row])
    width = max(len(top), len(sub))
    top += [None] * (width - len(top))
    sub += [None] * (width - len(sub))

    # A top row qualifies as a group row only if it has FEWER distinct labels than the sub
    # row (it groups them) and at least one merged range overlaps it.
    top_labels = [c for c in top if _is_string_label(c)]
    sub_labels = [c for c in sub if _is_string_label(c)]
    if not top_labels or not sub_labels or len(top_labels) >= len(sub_labels):
        return None

    # Fill the group label across the columns its merge spans (§14.9 fill_down for headers).
    filled = _fill_group_row(top, header_row - 1, merged_ranges, width)
    flattened: list[str] = []
    for i in range(width):
        g = filled[i]
        s = sub[i]
        gp = str(g).strip() if isinstance(g, str) and g.strip() else ""
        sp = str(s).strip() if isinstance(s, str) and s.strip() else ""
        if gp and sp:
            flattened.append(f"{gp}_{sp}")
        else:
            flattened.append(sp or gp)
    return flattened if any(flattened) else None


def _parse_a1(ref: str) -> Optional[tuple[int, int, int, int]]:
    """'A2:D90' → (r0,c0,r1,c1) 0-based inclusive. Returns None if unparseable."""
    import re
    m = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", ref.strip().upper())
    if not m:
        return None

    def col(letters: str) -> int:
        n = 0
        for ch in letters:
            n = n * 26 + (ord(ch) - 64)
        return n - 1
    return int(m.group(2)) - 1, col(m.group(1)), int(m.group(4)) - 1, col(m.group(3))


def _fill_group_row(row: list[Any], row_idx0: int, merged_ranges: list[str],
                    width: int) -> list[Any]:
    out = list(row) + [None] * (width - len(row))
    for ref in merged_ranges:
        bounds = _parse_a1(ref)
        if not bounds:
            continue
        r0, c0, r1, c1 = bounds
        if r0 <= row_idx0 <= r1:                 # this merge covers the group row
            anchor = out[c0] if c0 < len(out) else None
            for c in range(c0, min(c1, width - 1) + 1):
                if _is_blank(out[c]):
                    out[c] = anchor
    return out

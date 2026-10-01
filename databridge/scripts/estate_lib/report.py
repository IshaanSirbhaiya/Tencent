"""Human-readable output. A demo audience reads this, not a JSON blob."""

from __future__ import annotations

import sys
from typing import Iterable, Sequence

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"

_COLOUR = sys.stdout.isatty()


def enable_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def c(text: str, colour: str) -> str:
    return f"{colour}{text}{RESET}" if _COLOUR else text


def verdict(text: str) -> str:
    good = {"OK", "clean", "loaded", "present"}
    bad = {"MISMATCH", "VIOLATION", "OFF-DECLARED-RATE", "FAILED", "missing"}
    if text in good:
        return c(text, GREEN)
    if text in bad:
        return c(text, RED)
    return c(text, YELLOW) if text else text


def human_bytes(n: int | None) -> str:
    if n is None:
        return "-"
    step = 1024.0
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < step or unit == "GB":
            return f"{value:,.0f} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= step
    return f"{value:,.1f} GB"


def rule(title: str = "", width: int = 96) -> None:
    if title:
        pad = max(3, width - len(title) - 3)
        print(c(f"── {title} " + "─" * pad, CYAN))
    else:
        print(c("─" * width, DIM))


def heading(title: str) -> None:
    print()
    print(c(title, BOLD))


def _visible_len(s: str) -> int:
    out, i = 0, 0
    while i < len(s):
        if s[i] == "\033":
            j = s.find("m", i)
            i = len(s) if j < 0 else j + 1
            continue
        out += 1
        i += 1
    return out


def table(headers: Sequence[str], rows: Iterable[Sequence[str]], right: Sequence[int] = ()) -> None:
    rows = [[str(x) for x in r] for r in rows]
    if not rows:
        print(c("  (nothing)", DIM))
        return
    widths = [_visible_len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], _visible_len(cell))

    def fmt(cells: Sequence[str]) -> str:
        parts = []
        for i, cell in enumerate(cells):
            pad = widths[i] - _visible_len(cell)
            parts.append(" " * pad + cell if i in right else cell + " " * pad)
        return "  " + "  ".join(parts).rstrip()

    print(c(fmt(headers), DIM))
    print(c("  " + "  ".join("-" * w for w in widths), DIM))
    for r in rows:
        print(fmt(r))


def bullet(text: str, marker: str = "•") -> None:
    print(f"    {c(marker, DIM)} {text}")

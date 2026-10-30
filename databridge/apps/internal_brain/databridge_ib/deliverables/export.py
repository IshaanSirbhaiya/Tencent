"""Exports, built on the server from the STORED, CHECKED content only (never from anything a
browser sends). CSV for tables and charts. File names are built from fixed parts only.

The formula guard (OWASP CSV injection; Opus review #14): a cell whose first non-space character
is ``= + - @``, a tab, CR or LF, or a full-width ``＝ ＋ － ＠`` (also after leading spaces,
no-break spaces or other Unicode spaces) gets a leading ``'``, except a plain number such as
``-0.5%``. It applies to every cell, the Sources block included.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Any, Mapping, Sequence


CSV_CAP = 256 * 1024
_TRIGGER = re.compile(r"^[\s\u00a0\u1680\u2000-\u200b\u202f\u205f\u3000\ufeff]*[=+\-@\t\r\n\uff1d\uff0b\uff0d\uff20]")
_PLAIN_NUMBER = re.compile(r"^-?\d[\d,]*(?:\.\d+)?%?$")
FILENAME_RE = re.compile(r"^walnut-[a-z]+-[0-9a-f]{8}-\d{8}\.(?:pptx|csv)$")
CONTENT_TYPES = {"csv": "text/csv; charset=utf-8",
                 "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}


def safe_link(url: Any) -> str:
    """Only an http(s) link from our own citation metadata; anything else is left out."""
    u = str(url or "")
    return u if re.match(r"^https?://[^\s\"<>]+$", u) else ""


def guard(cell: Any) -> str:
    s = "" if cell is None else str(cell)
    if _TRIGGER.match(s) and not _PLAIN_NUMBER.match(s.strip()):
        return "'" + s
    return s


def filename(kind: str, req_id: str, when: datetime, ext: str) -> str:
    """``walnut-<kind>-<req_id[:8]>-<yyyymmdd>.<ext>``; a request id that is not hex gives
    ``00000000`` (never an error, never the id itself)."""
    head = (req_id or "")[:8].lower()
    hex8 = head if re.fullmatch(r"[0-9a-f]{8}", head) else "00000000"
    name = f"walnut-{kind}-{hex8}-{when:%Y%m%d}.{ext}"
    if not FILENAME_RE.match(name):                 # fixed parts only: this cannot fail
        name = f"walnut-file-00000000-{when:%Y%m%d}.{ext}"
    return name


def content_disposition(name: str) -> str:
    assert FILENAME_RE.match(name)
    return f'attachment; filename="{name}"'


def _markers(tags: Sequence[str], number: Mapping[str, int]) -> str:
    return " ".join(f"[{number[t]}]" for t in dict.fromkeys(tags) if t in number)


def build_csv(kind: str, content: Mapping[str, Any], citations: Sequence[Mapping[str, Any]]) -> bytes:
    number = {c["tag"]: c["n"] for c in citations}
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")

    def row(cells):
        w.writerow([guard(c) for c in cells])

    if kind == "table":
        row(list(content["columns"]) + ["Sources"])
        for r in content["rows"]:
            row([c["text"] for c in r] + [_markers([t for c in r for t in c["tags"]], number)])
    elif kind == "chart":
        row(["Series", "Label", "Value as written", "Value", "Unit", "Source"])
        unit = content.get("currency") or content.get("unit") or ""
        for s in content["series"]:
            for p in s["points"]:
                row([s["name"], p["x"], p["raw"], p["value"], unit, _markers([p["tag"]], number)])
    else:
        raise ValueError("kind")
    w.writerow([])
    row(["Source", "Title", "Platform", "Place", "Version", "Checked at", "Link"])
    seen = set()
    for c in citations:
        if c["doc_id"] in seen:
            continue
        seen.add(c["doc_id"])
        row([f"[{c['n']}]", c.get("title", ""), c.get("platform", ""), c.get("container_name", ""),
             c.get("version", ""), c.get("checked_at", ""), safe_link(c.get("deep_link", ""))])
    return ("﻿" + buf.getvalue()).encode("utf-8")


__all__ = ["guard", "filename", "content_disposition", "build_csv", "CSV_CAP", "FILENAME_RE",
           "CONTENT_TYPES"]

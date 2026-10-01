"""Shared helpers for packages/retrieval tests (a uniquely named module, so importing it never
collides with another package's `conftest` when suites are collected together)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def write_capture(root: Path, text: str, meta: dict) -> str:
    """Write one item in the frozen §5.2 layout; returns its sha256."""
    data = text.encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    d = root / sha[:2]
    d.mkdir(parents=True, exist_ok=True)
    (d / sha).write_bytes(data)
    (d / f"{sha}.txt").write_text(text, encoding="utf-8", newline="")
    (d / f"{sha}.json").write_text(json.dumps(meta), encoding="utf-8")
    return sha


SENTENCES = [
    "The widget assembly line reported a steady output across every shift this quarter.",
    "Maintenance of the widget press is scheduled for the first weekend of each month.",
    "Operators log each widget batch with its lot number and the inspector's initials.",
    "A widget that fails inspection is quarantined and reviewed by the quality lead.",
    "Training on the new widget jig is mandatory for every operator on the floor.",
]


def prose(n_chars: int, salt: str = "") -> str:
    parts, i = [], 0
    while sum(len(p) + 1 for p in parts) < n_chars:
        s = SENTENCES[i % len(SENTENCES)]
        parts.append(s + (f" Note {salt}{i}." if salt else ""))
        if i % 4 == 3:
            parts.append("\n")
        i += 1
    return " ".join(parts)

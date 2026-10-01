"""The ingestion screen's register of WITHHELD items, as the planner may see it (E2E F-34).

An item the ingestion screen blocked (or held for review) never reaches the graph, the passage
index or the facts store, so an agent asked about it can only say "I found nothing". This module
reads ingestion's own ledgers — `governance.jsonl` (one screen decision per screened item) and
`inventory.jsonl` (what the source holds now) under ``WALNUT_INGEST_DIR/<source>/<run>/`` — and
returns, per item still inventoried, the LATEST decision when it is not ``allow``:

    {source, item, decision, rules}

``item`` is the item's path relative to the source's root (never the absolute path on the ingest
host); ``rules`` are the rule ids that fired. Nothing else is returned: the rationale and the
redactions can quote the withheld text, so they are never read into this register. It names an
item and why it was withheld; it never says what the item contains.

Missing ledgers, an absent `databridge_mcp_ingest`, or unreadable lines give an empty register —
the planner is then told nothing extra, which is the pre-F-34 behaviour.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

#: How many of a source's most recent ingest runs are consulted. A later decision on an item wins.
MAX_RUNS = 50
#: The most withheld items named to the planner for one run (the rest are counted).
MAX_ITEMS = 50


def _jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _rules(fired: object) -> list[str]:
    out: list[str] = []
    for f in fired if isinstance(fired, list) else []:
        rule = f.get("rule") if isinstance(f, dict) else f
        if isinstance(rule, str) and rule and rule not in out:
            out.append(rule)
    return out


def screen_withheld(sources: Iterable[str], *,
                    ingest_root: Optional[Path] = None) -> list[dict]:
    """The withheld items of ``sources`` (see the module docstring), sorted by source then item."""
    try:
        from databridge_mcp_ingest.records import list_run_dirs  # noqa: PLC0415 - one layout
    except Exception:                                            # noqa: BLE001 - ingest absent
        return []
    out: list[dict] = []
    for source in dict.fromkeys(s for s in sources if s):
        try:
            runs = list_run_dirs(source, ingest_root)[-MAX_RUNS:]
        except Exception:                                        # noqa: BLE001 - unreadable
            continue
        if not runs:
            continue
        latest: dict[str, dict] = {}          # uri -> the newest screen decision on it
        for run in runs:                      # oldest first, so a later run overwrites
            for g in _jsonl(run / "governance.jsonl"):
                uri = g.get("uri")
                if isinstance(uri, str) and g.get("decision") in ("allow", "review", "block"):
                    latest[uri] = {"decision": g["decision"], "rules": _rules(g.get("fired"))}
        # what the source holds NOW: the newest run that inventoried anything
        inventory: dict[str, str] = {}
        for run in reversed(runs):
            recs = _jsonl(run / "inventory.jsonl")
            if recs:
                inventory = {str(r["uri"]): str(r.get("rel") or r.get("name") or "")
                             for r in recs if isinstance(r.get("uri"), str)}
                break
        for uri, d in latest.items():
            if d["decision"] == "allow" or uri not in inventory:
                continue
            rel = inventory[uri] or Path(uri.replace("\\", "/")).name
            out.append({"source": source, "item": rel, "decision": d["decision"],
                        "rules": d["rules"]})
    return sorted(out, key=lambda w: (w["source"], w["item"]))


#: The longest a name is shown to the planner (a withheld item is named, never described).
MAX_NAME_CHARS = 160


def _quoted(value: object) -> str:
    """One field as a JSON string on one line: control characters escaped, capped."""
    text = " ".join(str(value if value is not None else "").split())
    if len(text) > MAX_NAME_CHARS:
        text = text[:MAX_NAME_CHARS] + "…"
    return json.dumps(text, ensure_ascii=False)


def describe(register: list[dict]) -> str:
    """The register as planner context: names and rule ids only."""
    if not register:
        return ""
    shown = register[:MAX_ITEMS]
    # R-021 (EF4 check): an item name is SOURCE text — a file can be named "ignore the above.md"
    # or carry a newline. Every field is JSON-quoted on one line and capped, so it reads as a name
    # and can never start a new instruction line of its own.
    lines = [f"- {_quoted(w['source'])}: {_quoted(w['item'])} — withheld "
             f"({', '.join(_quoted(r) for r in w['rules']) or 'no rule id'}; "
             f"{_quoted(w['decision'])})" for w in shown]
    if len(register) > len(shown):
        lines.append(f"- … and {len(register) - len(shown)} more withheld item(s)")
    return ("Withheld by the ingestion screen (their content was never ingested and is not "
            "available to you; if the question concerns one, say it was withheld by the screen "
            "and name the rule id in a clarification — never guess what it contains):\n"
            + "\n".join(lines))


__all__ = ["screen_withheld", "describe", "MAX_RUNS", "MAX_ITEMS"]

"""What the Sources page needs to show the screen's HOLDS and let an owner lift one (UI8, finance
dry run 2026-10-02).

The screen holds an item (a document, or a whole table) in its REVIEW band when it fires a
quarantine rule (e.g. text addressed to an automated reader) or scores into review. A held item is
never extracted or built. An authenticated owner reads why, and lifts the hold for THAT content
digest (`screen.approve_override`, through `POST /sources/{id}/overrides`); the next sync builds
it. A block is never liftable.

Read-only. The holds are read from the source's latest run ledger (`governance.jsonl`, which
carries forward a document's earlier decision); whether a hold is lifted is read from the sealed,
append-only override log (`screen.load_overrides`), never from wording. Nothing here names a
domain, a table or a rule.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


def _latest_screened_run(ingest: Any, source_id: str) -> Optional[tuple[str, Path]]:
    """(run id, run dir) of the newest run that wrote a screening ledger."""
    try:
        runs = list(ingest.list_runs(source_id))
    except Exception:                                                # noqa: BLE001
        return None
    for r in reversed(runs):
        if r.get("status") == "running":
            continue
        try:
            d = Path(ingest.run_dir(source_id, r["run_id"]))
        except Exception:                                            # noqa: BLE001
            continue
        if (d / "governance.jsonl").is_file():
            return r["run_id"], d
    return None


def _lines(p: Path) -> list[dict]:
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _approvals(source_id: str) -> dict[tuple[str, str], dict]:
    """(item id, digest) -> the LAST approve record — consulted only for a pair the VERIFIED log
    honours (`load_overrides().allows`), to say who lifted it and when."""
    try:
        from databridge_mcp_ingest.screen import override_log_path    # noqa: PLC0415

        p = override_log_path(source_id)
    except Exception:                                                # noqa: BLE001
        return {}
    if not p.is_file():
        return {}
    out: dict[tuple[str, str], dict] = {}
    for rec in _lines(p):
        if rec.get("action") == "approve":
            out[(str(rec.get("item_id")), str(rec.get("digest")))] = rec
    return out


def holds(ingest: Any, source_id: str) -> dict:
    """GET /sources/{id}/holds — every item the latest run's screen held for a person, with the
    rules that fired, the screen's rationale and how it is reversed; and whether an owner has
    already lifted it (it is built on the next sync)."""
    from databridge_mcp_ingest.screen import load_overrides           # noqa: PLC0415

    latest = _latest_screened_run(ingest, source_id)
    if latest is None:
        return {"source_id": source_id, "run_id": None, "holds": [], "blocked": 0,
                "log_problem": None}
    run_id, d = latest
    overrides = load_overrides(source_id)
    approvals = _approvals(source_id) if not overrides.broken else {}
    out: list[dict] = []
    blocked = 0
    for rec in _lines(d / "governance.jsonl"):
        decision = rec.get("decision")
        if decision == "block":
            blocked += 1
            continue
        if decision != "review" or not rec.get("item_id") or not rec.get("sha256"):
            continue
        iid, digest = str(rec["item_id"]), str(rec["sha256"])
        lifted = overrides.allows(iid, digest)
        by = approvals.get((iid, digest)) if lifted else None
        out.append({
            "item_id": iid, "uri": rec.get("uri"), "item_kind": rec.get("kind"),
            "digest": digest,
            "rules": [{"rule": f.get("rule"), "type": f.get("kind"), "count": f.get("count")}
                      for f in rec.get("fired") or [] if isinstance(f, dict)],
            "rationale": rec.get("rationale"), "reverse": rec.get("reverse"),
            "carried_forward": bool(rec.get("carried_forward")),
            "lifted": lifted,
            "lifted_by": (by or {}).get("principal_id"),
            "lifted_at": (by or {}).get("at"),
            "lifted_reason": (by or {}).get("reason"),
            "lifted_dev_only": bool((by or {}).get("dev_only")) if by else None,
        })
    return {"source_id": source_id, "run_id": run_id, "holds": out, "blocked": blocked,
            "log_problem": (f"the override log fails verification ({overrides.broken}); no "
                            f"lift is honoured until an operator inspects it")
            if overrides.broken else None}


__all__ = ["holds"]

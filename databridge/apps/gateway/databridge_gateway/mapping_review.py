"""What the Sources page needs to REVIEW a table mapping (LINK7, finance dry run 2026-10-02).

* **Proposed links (C-3).** `mapping.propose_links` proposes a link where a column no foreign key
  declares holds, by measurement, the keys of another mapped table (in this source or another). A
  link is a proposal until a signed-in person approves it (`mapping.approve_link`). This module
  lists them with their EVIDENCE — the column pair, the overlap counts and a few matched keys read
  from the captured rows. Approving and rejecting a link are the engine's own
  (`mapping.approve_link` / `mapping.reject_link`); both record the decider and how they signed in.
* **Tables that need approval again.** TAB6 re-proposes an approved table when a declared foreign
  key gains its edge (and the engine drops an approval that no longer verifies). Such a table is
  `proposed` again; the page must say "needs your approval again" and why, not show it like a
  table nobody has ever looked at. "Was approved" is read from the append-only approvals log, not
  from wording.

Nothing here names a domain, a table or a kind. A link into a PERSON kind (`is_person_kind`, the
pack's subject kinds) is never offered for approval and shows no sample keys: those would be
person-record keys (hard constraint 1).
"""
from __future__ import annotations

from typing import Any, Optional

#: How many matched keys a proposal shows as evidence.
SAMPLE_KEYS = 5
#: Rationale lines the mapping engine writes when it takes an approval back (or does not honour
#: one). Shown as the REASON; whether the table needs approval again is decided from the log.
_REAPPROVAL_MARKERS = ("re-proposed", "approval NOT honoured")
_REVOKED_MARKER = " revoked by "


def _vocab():
    from databridge_graph.packs import active_vocabulary            # noqa: PLC0415

    return active_vocabulary()


def link_target(link: dict) -> str:
    return f"{link.get('source')}:{link.get('table')}"


def _snapshots_of(source_id: str, cache: dict) -> dict:
    if source_id not in cache:
        from databridge_mcp_ingest.mapping import captured_tables     # noqa: PLC0415

        try:
            cache[source_id] = captured_tables(source_id)
        except Exception:                                            # noqa: BLE001
            cache[source_id] = {}
    return cache[source_id]


def link_evidence(source_id: str, table: str, link: dict, vocab, cache: dict) -> dict:
    """The proposal's own counts, plus what the captured rows show: how many DISTINCT values
    matched, how many keys the target has, and up to `SAMPLE_KEYS` matched keys. Never sample keys
    for a link into a person kind."""
    from databridge_mcp_ingest.mapping import _key_values, is_person_kind  # noqa: PLC0415

    out: dict[str, Any] = {"matched": link.get("matched"), "with_value": link.get("with_value"),
                           "distinct_matched": None, "target_keys": None, "samples": None,
                           "samples_note": None}
    if is_person_kind(vocab, link.get("target_kind")):
        out["samples_note"] = ("not shown: the target kind stands for people, so its keys are "
                               "person-record keys")
        return out
    mine = _snapshots_of(source_id, cache).get(table)
    theirs = _snapshots_of(str(link.get("source")), cache).get(str(link.get("table")))
    if not mine or not theirs:
        out["samples_note"] = ("not shown: the rows of one side are not captured on this gateway "
                               "(they are kept from the last verified sync)")
        return out
    kv = _key_values(theirs)
    if kv is None or kv[0] != link.get("key"):
        out["samples_note"] = "not shown: the target's key is no longer the one proposed"
        return out
    col = link.get("column")
    vals = {str(r.get(col)) for r in mine.get("rows") or [] if r.get(col) is not None}
    hit = sorted(vals & kv[1])
    out.update(distinct_matched=len(hit), target_keys=len(kv[1]), samples=hit[:SAMPLE_KEYS])
    return out


def _last_approval(entries: dict) -> Optional[dict]:
    recs = [r for rs in (entries or {}).values() for r in rs]
    return max(recs, key=lambda r: str(r.get("at") or ""), default=None)


def reapproval(source_id: str, table: str, tm: Any, vocab, logged: dict) -> Optional[dict]:
    """{reasons, previously_approved_by, previously_approved_at, dev_only} when the table was
    approved by a person before (the hash-chained log holds an approval for it) and is not
    approved now — or is marked approved but the approval no longer verifies. None otherwise."""
    from databridge_mcp_ingest.mapping import approval_problem       # noqa: PLC0415

    entries = logged.get(table) or {}
    if tm.status == "unmapped" or not entries:
        return None
    problem = None
    if tm.status == "approved":
        problem = approval_problem(source_id, table, tm, vocab, logged=logged)
        if problem is None:
            return None
    last = _last_approval(entries) or {}
    reasons = [r for r in tm.rationale or []
               if r.startswith(_REAPPROVAL_MARKERS) or _REVOKED_MARKER in r]
    if problem:
        reasons.append(f"approval not honoured: {problem}")
    if not reasons:
        reasons = [f"the mapping changed after {last.get('principal_id') or 'a person'} approved "
                   f"it; the version approved is not the one proposed now"]
    return {"reasons": reasons, "previously_approved_by": last.get("principal_id"),
            "previously_approved_at": last.get("at"), "dev_only": bool(last.get("dev_only"))}


def _own_captures(source_id: str, cache: dict) -> dict:
    """{table ref: snapshot} of every table the source's state holds a capture for — INCLUDING a
    table not yet built (`graph: false`, e.g. a mapping awaiting approval): a split is reviewed
    BEFORE it is approved, from the rows the last sync captured. Read-only."""
    key = ("own", source_id)
    if key in cache:
        return cache[key]
    out: dict[str, dict] = {}
    try:
        import json                                                    # noqa: PLC0415

        from databridge_mcp_ingest import capture                      # noqa: PLC0415
        from databridge_mcp_ingest.runner import load_state            # noqa: PLC0415

        for entry in load_state(source_id).values():
            sha = (entry or {}).get("content_hash")
            if not sha:
                continue
            try:
                data = json.loads(capture.get_bytes(sha))
            except Exception:                                        # noqa: BLE001
                continue
            if isinstance(data, dict) and data.get("table") and isinstance(data.get("rows"), list):
                ref = (f"{data['schema']}.{data['table']}" if data.get("schema")
                       else str(data["table"]))
                out[ref] = data
    except Exception:                                                # noqa: BLE001
        out = {}
    cache[key] = out
    return out


def _mask_rules():
    try:
        from databridge_mcp_ingest.screen import load_screen         # noqa: PLC0415

        return load_screen().redactions
    except Exception:                                                # noqa: BLE001
        return None


def split_review(source_id: str, table: str, tm: Any, vocab, cache: dict) -> Optional[dict]:
    """What a person approves when they approve a table that carries a FIN-04 kind split: the
    discriminator column, each value -> kind with the rows that hold it, and the rows that stay
    the subject (values the split does not bind, values bound to the subject itself, NULL or
    blank, and values the screen masks — a masked value never moves a row, as the build does).

    Counts are measured on the rows the last sync captured, grouped by the engine's own
    `split_value`. A value the screen would mask is counted but never shown. Nothing here decides
    anything: the split is the engine's proposal and is approved with the table."""
    from databridge_mcp_ingest.mapping import is_person_kind, split_value  # noqa: PLC0415

    split = getattr(tm, "split", None)
    if not isinstance(split, dict) or not split.get("column"):
        return None
    col, default = str(split["column"]), str(split.get("default") or "")
    bound: dict[str, str] = {str(k): str(v) for k, v in (split.get("kinds") or {}).items()}

    def _subject(k: str) -> bool:
        try:
            return bool(vocab.is_subject(k))
        except Exception:                                            # noqa: BLE001
            return False

    kinds = sorted({default, *bound.values()} - {""})
    kind_info = [{"kind": k, "subject": _subject(k), "person": is_person_kind(vocab, k),
                  "is_default": k == default} for k in kinds]
    snap = _own_captures(source_id, cache).get(table)
    counts: Optional[dict[str, int]] = None
    nulls = masked = total = 0
    if snap is not None:
        rules = _mask_rules()
        counts = {}
        for r in snap.get("rows") or []:
            total += 1
            v = r.get(col)
            if v is None or not str(v).strip():
                nulls += 1
                continue
            if isinstance(v, str) and rules:
                from databridge_mcp_ingest.screen import apply_masks    # noqa: PLC0415

                if apply_masks(v, rules)[1]:
                    masked += 1
                    continue
            n = split_value(v)
            counts[n] = counts.get(n, 0) + 1
    values = []
    for v in sorted(set(bound) | set(counts or {})):
        k = bound.get(v, default)
        values.append({"value": v, "kind": k, "bound": v in bound,
                       "rows": (counts or {}).get(v, 0) if counts is not None else None,
                       "stays_subject": k == default})
    moved = [x for x in values if not x["stays_subject"]]
    return {
        "column": col, "default": default, "default_is_subject": _subject(default),
        "default_is_person": is_person_kind(vocab, default), "kinds": kind_info,
        "values": values,
        "rows_total": total if snap is not None else None,
        "rows_moved": (sum(x["rows"] for x in moved) if snap is not None else None),
        "rows_stay": ({"bound_to_subject": sum(x["rows"] for x in values
                                              if x["stays_subject"] and x["bound"]),
                       "unrecognised": sum(x["rows"] for x in values if not x["bound"]),
                       "null_or_blank": nulls, "masked": masked}
                      if snap is not None else None),
        "counts_note": (None if snap is not None else
                        "row counts not shown: no capture of this table is held on this gateway "
                        "yet (they appear after the next sync captures it)"),
        "status": tm.status,
        "approved_with_table": True,
    }


def review(source_id: str, vocab=None) -> dict:
    """GET /sources/{id}/mapping/review — every link proposal (with evidence), every table that
    needs a person's approval again, and every table's per-row kind split (FIN-04 / ID7) with the
    rows each value moves."""
    from databridge_mcp_ingest.mapping import (is_person_kind, load_mapping,  # noqa: PLC0415
                                               logged_approvals)

    vocab = vocab if vocab is not None else _vocab()
    m = load_mapping(source_id)
    logged = logged_approvals(source_id)
    cache: dict = {}
    links: list[dict] = []
    again: dict[str, dict] = {}
    splits: dict[str, dict] = {}
    for table, tm in sorted(m.tables.items()):
        r = reapproval(source_id, table, tm, vocab, logged)
        if r:
            again[table] = r
        sp = split_review(source_id, table, tm, vocab, cache)
        if sp:
            splits[table] = sp
        for lk in tm.links or []:
            person = is_person_kind(vocab, lk.get("target_kind"))
            edge = lk.get("edge") or {}
            links.append({
                "table": table, "column": lk.get("column"), "target": link_target(lk),
                "target_source": lk.get("source"), "target_table": lk.get("table"),
                "target_key": lk.get("key"), "target_kind": lk.get("target_kind"),
                "status": lk.get("status"), "edge_kind": edge.get("kind"),
                "why": lk.get("why"), "basis": lk.get("basis"),
                "alternatives": lk.get("alternatives") or [],
                "person_target": person,
                "approvable": lk.get("status") == "proposed" and bool(edge) and not person,
                "decided_by": lk.get("approved_by") or lk.get("rejected_by"),
                "decided_at": lk.get("at"),
                "decided_dev_only": lk.get("decided_dev_only"),
                "reject_reason": lk.get("reject_reason"),
                "table_status": tm.status,
                "evidence": link_evidence(source_id, table, lk, vocab, cache),
            })
    return {"source_id": source_id, "links": links, "reapproval": again, "splits": splits}


def _find(tm: Any, column: str, target: Optional[str]) -> list[dict]:
    return [lk for lk in tm.links or [] if lk.get("column") == column
            and (target is None or link_target(lk) == target)]


def person_target_refusal(source_id: str, table: str, column: str, target: Optional[str],
                          vocab) -> Optional[str]:
    """The refusal for a link into a person kind, checked BEFORE the engine is asked (the engine
    refuses it as well)."""
    from databridge_mcp_ingest.mapping import is_person_kind, load_mapping  # noqa: PLC0415

    tm = load_mapping(source_id).tables.get(table)
    if tm is None:
        return None
    for lk in _find(tm, column, target):
        if is_person_kind(vocab, lk.get("target_kind")):
            return (f"{table}.{column} -> {link_target(lk)} points at {lk.get('target_kind')!r}, a "
                    f"kind that stands for people: an inferred link never points at a person "
                    f"record (hard constraint 1)")
    return None

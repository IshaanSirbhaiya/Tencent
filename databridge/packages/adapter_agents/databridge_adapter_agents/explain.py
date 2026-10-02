"""The adapter agent EXPLAINS a source's proposed mapping (FEAT8, founder design 2026-10-02).

For every table the mapper proposed, the agent says in plain words what it thinks the table is —
the entity kind, whether its rows stand for people, its keys and its relationships — and WHY,
citing evidence that points into the captured bytes (`SourcePointer`, kind ``table_cell``, bound to
the capture digest the discovery recorded).

**Advisory only.** Nothing here changes a mapping.

**Facts, not prose (Codex R-027 AAX f1 #2).** Every sentence shown is a PREDECLARED fact, computed
from the mapper's proposal, the captured schema and the pack vocabulary, and rendered here by a
fixed template. Each fact carries its ``basis``:

* ``capture`` — the fact is established by the captured table (a declared primary key, a declared
  foreign key, the label / identifier / discriminator columns): it cites sample cells OF THAT
  COLUMN, bound to the fact, each with a pointer. A capture fact with no citable cell is not
  offered at all.
* ``mapper`` / ``vocabulary`` — the fact is the mapper's PROPOSAL (the kind, an inferred link, the
  mapper's own reasons) or the vocabulary's declaration (people / a protected subject). No cell
  establishes such a fact, so it cites none and is shown as what it is: a proposal for a person
  to approve, never as evidenced.

The model (through the harness, `EXPLAIN_TASK_CLASS` from ``config/models.yaml``) writes NO
words: it may only choose which offered facts best explain the table, by id, in order. An id that
was never offered is dropped and counted; the people / protected-subject fact is always shown,
whatever the model chose. Nothing the model returns is displayed verbatim.

What reaches the model is screened (`mapping.screen_for_model`); a table whose payload is not a
clean ``allow`` is explained by rules only. A table whose rows are PEOPLE or a protected SUBJECT
never sends a value (Codex R-027 AAX f1 #3), only column names; a value the screen masks is shown
masked; a row the screen holds is never sampled.

**Bound to the discovery (Codex R-027 AAX f1 #5).** The evidence is read from the capture digest
the capture-only discovery recorded for each table (`runner.latest_discovery`), never from
whatever the source's state holds now. A table the discovery did not capture, or whose capture no
longer hashes to its digest, gets no evidence and a ``capture_problem``.

With no harness, or when the model call fails, every table is explained from the facts in their
fixed order (``mode: "rules"``), with the same evidence and pointers.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

#: the models.yaml task class the explanation is routed through (data -> plain words)
EXPLAIN_TASK_CLASS = "data_to_text"
#: sampled rows per table offered as evidence
SAMPLE_ROWS = 3
#: supporting facts the model may choose per table
MAX_REASONS = 4
#: fact types that are ALWAYS shown (never left to the model's choice)
MANDATORY = ("people", "subject")


# --- the model's answer: fact ids only, never words -----------------------------------------------
class _TableChoice(BaseModel):
    table: str
    reasons: list[str] = Field(default_factory=list)


class _Answer(BaseModel):
    tables: list[_TableChoice] = Field(default_factory=list)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _pointer(source_id: str, table: str, column: str, row_id: str, sha: str) -> dict:
    """The pipeline's own cell pointer (`build.cell_pointer`), as JSON."""
    from databridge_mcp_ingest.build import cell_pointer  # noqa: PLC0415

    return cell_pointer(source_id, table, column, row_id, sha).model_dump(mode="json",
                                                                          exclude_none=True)


def discovery_captures(discovery: Optional[dict]) -> tuple[dict[str, tuple[dict, str]],
                                                           dict[str, str]]:
    """({table ref: (snapshot, capture sha)}, {table ref: problem}) from the capture digests the
    discovery RECORDED (fail closed: a missing or altered capture yields a problem, no snapshot).
    Read-only."""
    from databridge_mcp_ingest import capture  # noqa: PLC0415

    caps: dict[str, tuple[dict, str]] = {}
    problems: dict[str, str] = {}
    for ref, entry in sorted(((discovery or {}).get("tables") or {}).items()):
        sha = (entry or {}).get("sha256")
        if not sha:
            problems[ref] = "the discovery recorded no capture digest for this table"
            continue
        try:
            data = json.loads(capture.get_bytes(sha))             # verifies sha256(bytes) == sha
        except Exception as exc:                                     # noqa: BLE001
            problems[ref] = (f"the capture {sha[:12]} the discovery recorded is missing or "
                             f"altered ({type(exc).__name__})")
            continue
        if not (isinstance(data, dict) and isinstance(data.get("rows"), list)):
            problems[ref] = f"the capture {sha[:12]} is not a table snapshot"
            continue
        caps[ref] = (data, sha)
    return caps, problems


def _screen():
    try:
        from databridge_mcp_ingest.screen import load_screen  # noqa: PLC0415

        return load_screen()
    except Exception:                                                # noqa: BLE001
        return None


def _shown(value: Any, cfg) -> tuple[Optional[str], bool]:
    """(the value as shown, masked?). A value the screen masks is shown masked; with no screen
    configuration nothing is shown at all (fail closed)."""
    if value is None:
        return None, False
    text = str(value)
    if cfg is None:
        return None, True
    from databridge_mcp_ingest.screen import apply_masks  # noqa: PLC0415

    masked, red = apply_masks(text, cfg.redactions)
    return (masked, bool(red))


def _row_allowed(row: dict, cfg) -> bool:
    """Only rows the screen ALLOWS are sampled (a held row is never shown here)."""
    if cfg is None:
        return False
    from databridge_mcp_ingest.screen import screen_text  # noqa: PLC0415

    return screen_text(json.dumps(row, sort_keys=True, default=str), cfg).decision == "allow"


def _edge_target(e: dict) -> Optional[str]:
    """The table an edge's foreign key names (`col -> table`), else its `ref` table."""
    fk = e.get("fk")
    if isinstance(fk, str) and "->" in fk:
        return fk.split("->", 1)[1].strip()
    ref = e.get("ref") or {}
    return ref.get("table") if isinstance(ref, dict) else None


def _end_kind(end: Any) -> Optional[str]:
    return end.split(":", 1)[0] if isinstance(end, str) and ":" in end else None


def table_facts(table: str, tm: Any, snap: dict, vocab) -> dict:
    """What the MAPPER proposed for one table, as facts (never the model's): kind, people or not,
    keys, relationships, split. Computed from the mapping and the vocabulary."""
    from databridge_mcp_ingest.mapping import is_person_kind  # noqa: PLC0415

    node = tm.node or {}
    kind = node.get("kind")
    decl = vocab.node_kinds.get(kind) if kind else None
    rels = []
    for e in tm.edges or []:
        src_k, dst_k = _end_kind(e.get("src")), _end_kind(e.get("dst"))
        col = (e.get("evidence") or [None])[0] if not tm.association else None
        rels.append({"edge_kind": e.get("kind"), "column": col,
                     "fk": e.get("fk"), "target_table": _edge_target(e),
                     "from_kind": src_k, "to_kind": dst_k,
                     "declared": bool(e.get("fk")),
                     "direction": ("into this table" if e.get("reversed") else "out of this table")})
    return {
        "table": table, "status": tm.status, "origin": tm.origin, "kind": kind,
        "kind_description": getattr(decl, "description", None) if decl else None,
        "binds_to": getattr(getattr(decl, "binds_to", None), "value", None) if decl else None,
        "is_subject": bool(kind and vocab.is_subject(kind)),
        "is_person": bool(kind and is_person_kind(vocab, kind)),
        "association": bool(tm.association),
        "keys": {"primary_key": list(snap.get("primary_key") or []),
                 "canonical_id": node.get("canonical_id"),
                 "candidate_identifier": node.get("candidate_identifier"),
                 "label": node.get("label")},
        "relationships": rels,
        "split": dict(tm.split) if tm.split else None,
        "columns": [c.get("name") for c in snap.get("columns") or [] if c.get("name")],
        "rows_captured": len(snap.get("rows") or []),
        "rationale": list(tm.rationale or []),
    }


def _protected(facts: dict) -> bool:
    """Rows that stand for people or a protected subject: no value of theirs reaches a model."""
    return bool(facts.get("is_person") or facts.get("is_subject"))


def _sampled(snap: dict, cfg) -> list[tuple[str, dict]]:
    pk = list(snap.get("primary_key") or [])
    from databridge_mcp_ingest.build import _render_pk  # noqa: PLC0415  (the build's row id)

    out: list[tuple[str, dict]] = []
    for ordinal, row in enumerate(snap.get("rows") or []):
        if len(out) >= SAMPLE_ROWS:
            break
        if not isinstance(row, dict) or not _row_allowed(row, cfg):
            continue
        if any(_shown(row.get(c), cfg)[1] for c in pk):
            continue                  # a key the screen masks is never written out, as in the build
        rid = _render_pk(row, pk)
        out.append((rid if rid is not None else f"#{ordinal}", row))
    return out


def table_explanation_facts(source_id: str, table: str, facts: dict, snap: dict, sha: str,
                            cfg) -> tuple[list[dict], list[dict]]:
    """(facts offered, evidence) for one table. Every fact has an id, a type, its rendered
    sentence, its ``basis`` and the ids of the evidence BOUND to it; every evidence item names
    the ONE fact it supports (``fact``) and carries a cell pointer into the capture. A
    ``capture`` fact is offered only with at least one cell of its own column."""
    sampled = _sampled(snap, cfg) if sha else []
    evidence: list[dict] = []
    offered: list[dict] = []

    def cells(fid: str, what: str, columns: list[str], note: str, *, rows: int) -> list[str]:
        ids: list[str] = []
        for column in columns:
            for rid, row in sampled[:rows]:
                if column not in row:
                    continue
                shown, masked = _shown(row.get(column), cfg)
                eid = f"e{len(evidence) + 1}"
                evidence.append({"id": eid, "fact": fid, "what": what, "column": column,
                                 "note": note, "row_id": rid, "value": shown, "masked": masked,
                                 "pointer": _pointer(source_id, table, column, rid, sha)})
                ids.append(eid)
        return ids

    def fact(ftype: str, text: str, basis: str, *, what: str = "", columns: tuple = (),
             note: str = "", rows: int = 1) -> None:
        fid = f"f{len(offered) + 1}"
        ids: list[str] = []
        if basis == "capture":
            ids = cells(fid, what or ftype, list(columns), note, rows=rows)
            if not ids:
                return                               # nothing captured establishes it: not offered
        offered.append({"id": fid, "type": ftype, "text": text, "basis": basis,
                        "evidence": ids})

    kind = facts.get("kind")
    tname = facts["table"]
    # -- what the mapper proposes (a proposal; no cell establishes a kind) --
    if facts.get("association"):
        fact("kind", f"The mapper proposes {tname} as a join table: each row links two records "
                     f"rather than standing for a thing of its own.", "mapper")
    elif kind:
        fact("kind", f"The mapper proposes {tname} as a table of {kind} records "
                     f"({facts.get('kind_description') or 'a kind in the pack vocabulary'}).",
             "mapper")
    else:
        fact("kind", f"No kind in the vocabulary fits {tname}; it stays unmapped.", "mapper")
    # -- what the vocabulary declares about that kind --
    if kind and facts.get("is_person"):
        fact("people", f"The vocabulary declares {kind} rows to stand for people, so they are "
                       f"never merged; a candidate match is only ever shown to a person.",
             "vocabulary")
    elif kind and facts.get("is_subject"):
        fact("subject", f"The vocabulary declares {kind} a protected subject, so its rows are "
                        f"never merged; a candidate match is only ever shown to a person.",
             "vocabulary")
    # -- what the captured table establishes --
    keys = facts["keys"]
    pk = list(keys.get("primary_key") or [])
    if pk:
        fact("primary_key", f"Each row is identified by its declared primary key "
                            f"({', '.join(pk)}).", "capture", columns=tuple(pk),
             note="a column of the table's declared primary key")
    if keys.get("canonical_id"):
        c = keys["canonical_id"]
        fact("canonical_id", f"{c} is the proposed identifier of each {kind}; records sharing "
                             f"it are the same {kind} only under an identity rule a person "
                             f"approves.", "capture", columns=(c,),
             note=f"{c} is the mapping's proposed identifier")
    if keys.get("candidate_identifier"):
        c = keys["candidate_identifier"]
        fact("candidate_identifier", f"{c} is a candidate identifier: a possible match is shown "
                                     f"to a person, never merged.", "capture", columns=(c,),
             note=f"{c} is the mapping's candidate identifier")
    label_cols = re.findall(r"\{([^{}]+)\}", str(keys.get("label") or ""))[:2]
    if label_cols:
        fact("label", f"Each record is named from {', '.join(label_cols)}.", "capture",
             columns=tuple(label_cols), note="a column the record's name is made from",
             rows=SAMPLE_ROWS)
    declared = {fk.get("column"): fk for fk in snap.get("foreign_keys") or [] if fk.get("column")}
    for r in facts["relationships"]:
        col, target = r.get("column"), r.get("target_table")
        fk = declared.get(col) if col else None
        if fk is not None:
            ref = (f"{fk.get('ref_schema')}.{fk.get('ref_table')}" if fk.get("ref_schema")
                   else str(fk.get("ref_table")))
            fact("relationship", f"{col} is a declared foreign key to {ref}, so each row links "
                                 f"to it as '{r.get('edge_kind')}' ({r.get('direction')}).",
                 "capture", columns=(col,), note=f"{col} is a declared foreign key to {ref}")
        else:
            fact("relationship", f"The mapper proposes a '{r.get('edge_kind')}' link to "
                                 f"{target or 'another table'} ({r.get('direction')}); no foreign "
                                 f"key declares it.", "mapper")
    split = facts.get("split") or {}
    if split.get("column"):
        moved = sorted({k for k in (split.get("kinds") or {}).values()
                        if k != split.get("default")})
        fact("split", f"{split['column']} decides each row's kind: some values become "
                      f"{', '.join(moved) or 'another kind'}; anything else stays "
                      f"{split.get('default')}.", "capture", columns=(split["column"],),
             note=f"{split['column']} decides each row's kind (the proposed split)",
             rows=SAMPLE_ROWS)
    # Codex R-027 AAX f2 #4: the mapper's free-prose rationale is NEVER a fact - it is not a
    # template, and it may carry source text. Every sentence here is one of the fixed templates.
    return offered, evidence


def _digest(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                                      .encode()).hexdigest()


def _claim(f: dict) -> dict:
    return {"text": f["text"], "evidence": list(f["evidence"]), "fact": f["id"],
            "type": f["type"], "basis": f["basis"]}


def _rules_explanation(offered: list[dict]) -> tuple[dict, list[dict]]:
    """The deterministic explanation: the kind as the summary, every other fact in order."""
    summary = next(f for f in offered if f["type"] == "kind")
    return _claim(summary), [_claim(f) for f in offered if f is not summary]


def select_facts(choice: list[str], offered: list[dict]) -> tuple[list[dict], int]:
    """(the reasons shown, ids dropped) from the model's CHOICE of fact ids. An id never offered
    (or the summary's, or a repeat) is dropped and counted. The mandatory facts (people / a
    protected subject) come first whatever was chosen. Every sentence is the fact's own."""
    by_id = {f["id"]: f for f in offered}
    summary_id = next(f["id"] for f in offered if f["type"] == "kind")
    out: list[dict] = [f for f in offered if f["type"] in MANDATORY]
    seen = {f["id"] for f in out} | {summary_id}
    dropped = chosen = 0
    for fid in choice:
        f = by_id.get(fid)
        if f is None or fid == summary_id:
            dropped += 1
            continue
        if fid in seen or chosen >= MAX_REASONS:
            continue                       # a mandatory fact the model also chose, or past the cap
        seen.add(fid)
        chosen += 1
        out.append(f)
    return [_claim(f) for f in out], dropped


def _mask_all(obj: Any, cfg) -> Any:
    """Every string in `obj` through the screen's masks (fail closed: no screen, no string)."""
    if isinstance(obj, str):
        if cfg is None:
            return ""
        from databridge_mcp_ingest.screen import apply_masks  # noqa: PLC0415

        return apply_masks(obj, cfg.redactions)[0]
    if isinstance(obj, dict):
        return {k: _mask_all(v, cfg) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_all(v, cfg) for v in obj]
    return obj


def _model_payload(facts: dict, offered: list[dict], evidence: list[dict], alias: str,
                   cfg) -> dict:
    """What the model may see for one table, addressed by its controlled `alias` (Codex R-027
    AAX f2 #4):

    * rows that stand for people or a protected subject: CONTROLLED IDENTIFIERS ONLY - the
      alias, the vocabulary's own kind and flags, fact ids / types / bases and evidence ids. No
      table or column name, no sentence, no value (f1 #3);
    * any other table: the facts and evidence with their sentences, column names and unmasked
      values - every string masked by the screen.
    """
    if _protected(facts):
        return {"table": alias, "kind": facts.get("kind"), "is_person": facts["is_person"],
                "is_subject": facts["is_subject"], "association": facts["association"],
                "facts": [{k: f[k] for k in ("id", "type", "basis", "evidence")}
                          for f in offered],
                "evidence": [{"id": e["id"], "fact": e["fact"], "what": e["what"]}
                             for e in evidence]}
    ev = [{"id": e["id"], "fact": e["fact"], "what": e["what"], "column": e["column"],
           **({} if e["masked"] else {"value": e["value"]})}
          for e in evidence]
    return _mask_all({"table": alias, "name": facts["table"], "kind": facts.get("kind"),
                      "is_person": facts["is_person"], "is_subject": facts["is_subject"],
                      "association": facts["association"],
                      "facts": [{k: f[k] for k in ("id", "type", "text", "basis", "evidence")}
                                for f in offered], "evidence": ev}, cfg)


def explain_mapping(source_id: str, mapping: Any, vocab, *, harness: Any = None,
                    agent_id: Optional[str] = None, discovery: Optional[dict] = None,
                    captures: Optional[dict[str, tuple[dict, str]]] = None,
                    screen_cfg: Any = None) -> dict:
    """The agent's explanation of every proposed, approved or unmapped table of `mapping`.
    Advisory: returns a record; changes nothing. Evidence is read from the captures the
    `discovery` recorded (or, for a caller that holds them, `captures`). Only a table the
    discovery's screen ALLOWED is ever sent to a model (Codex R-027 AAX f2 #3); a caller passing
    `captures` without a discovery vouches that they were allowed."""
    from databridge_mcp_ingest.mapping import mapping_digest, screen_for_model  # noqa: PLC0415

    cfg = screen_cfg if screen_cfg is not None else _screen()
    if captures is not None:
        caps, cap_problems = dict(captures), {}
    else:
        caps, cap_problems = discovery_captures(discovery)
    tables: list[dict] = []
    payloads: dict[str, dict] = {}
    offered_by: dict[str, list[dict]] = {}
    alias_of: dict[str, str] = {}
    screened = {t: (e or {}).get("decision")
                for t, e in ((discovery or {}).get("tables") or {}).items()}
    for table, tm in sorted((mapping.tables or {}).items()):
        alias_of[table] = f"T{len(alias_of) + 1}"
        snap, sha = caps.get(table, ({}, ""))
        facts = table_facts(table, tm, snap, vocab)
        offered, evidence = table_explanation_facts(source_id, table, facts, snap, sha, cfg)
        offered_by[table] = offered
        summary, reasons = _rules_explanation(offered)
        problem = None if sha else (cap_problems.get(table) or
                                    "the discovery captured no snapshot of this table")
        entry = {**{k: v for k, v in facts.items() if k != "rationale"},
                 "mapping_digest": mapping_digest(tm), "capture_sha": sha or None,
                 "capture_problem": problem, "facts": offered, "evidence": evidence,
                 "summary": summary, "reasons": reasons, "explained_by": "rules",
                 "model_dropped": 0, "sent_to_model": False, "not_sent_reason": None,
                 # the controlled name the model knows this table by (never the source's name)
                 "model_alias": alias_of[table]}
        if not evidence:
            entry["not_sent_reason"] = (problem or "no captured row may be cited (a row the "
                                                   "screen holds is never sampled); the facts "
                                                   "are shown from the rules")
        elif harness is None:
            entry["not_sent_reason"] = "no model is configured; explained from the mapper's rules"
        elif discovery is not None and screened.get(table) not in ("allow", "unchanged"):
            entry["not_sent_reason"] = (f"the discovery's screen decided "
                                        f"{screened.get(table)!r} on this table; only allowed "
                                        f"material reaches a model, so it is explained by the "
                                        f"rules")
        else:
            payload = _model_payload(facts, offered, evidence, alias_of[table], cfg)
            send, rec = screen_for_model(payload, screen_cfg=cfg)
            if send:
                payloads[table] = payload
            else:
                entry["not_sent_reason"] = (f"the screen decided {rec.get('decision')!r} on this "
                                            f"table's material; explained from the rules only")
        tables.append(entry)
    model_name: Optional[str] = None
    model_error: Optional[str] = None
    if payloads:
        system = {"role": "system", "content": (
            "You help explain a proposed database-to-ontology mapping to the person who will "
            "approve it. For each table you are given FACTS, each with an id, a sentence and the "
            "evidence ids bound to it. Choose up to four fact ids that best explain the table "
            "to that person, most useful first. Answer with fact ids only: you write no words, "
            "you do not decide the mapping, and an id you were not given is discarded.")}
        user = {"role": "user", "content": json.dumps({"tables": list(payloads.values())},
                                                      sort_keys=True, default=str)}
        try:
            result = harness.complete_structured(EXPLAIN_TASK_CLASS, [system, user], _Answer,
                                                 seed=7)
            answer: _Answer = result.parsed
            event = getattr(result, "event", None)
            model_name = (event.get("model") if isinstance(event, dict)
                          else getattr(event, "model", None)) or "model"
        except Exception as exc:                                     # noqa: BLE001
            answer = _Answer()
            model_error = f"{type(exc).__name__}: {exc}"[:300]
        got: dict[str, _TableChoice] = {}
        for t in answer.tables:
            got.setdefault(t.table, t)                 # keyed by the ALIAS the model was given
        for entry in tables:
            if entry["table"] not in payloads:
                continue
            entry["sent_to_model"] = True
            t = got.get(alias_of[entry["table"]])
            if t is None:
                entry["not_sent_reason"] = ("the model returned nothing for this table; "
                                            "explained from the rules"
                                            if model_error is None else
                                            f"the model call failed ({model_error}); explained "
                                            f"from the rules")
                continue
            reasons, dropped = select_facts(list(t.reasons), offered_by[entry["table"]])
            entry["model_dropped"] = dropped
            if any(r["type"] not in MANDATORY for r in reasons):
                entry["explained_by"] = "model"
                entry["reasons"] = reasons
    return {
        "source_id": source_id, "generated_at": _now(), "advisory": True,
        "note": ("The agent's explanation is advice. Each sentence is a fact computed from the "
                 "mapper's proposal, the captured table or the vocabulary; the model only chose "
                 "which facts to show. The mapping is approved by a person; nothing here "
                 "approves or builds anything."),
        "produced_by": {"agent": f"adapter_agent:{agent_id}" if agent_id else "adapter_agent",
                        "task_class": EXPLAIN_TASK_CLASS, "model": model_name,
                        "model_error": model_error,
                        "mode": "model" if any(t["explained_by"] == "model" for t in tables)
                        else "rules"},
        "discovery": ({"run_id": discovery.get("run_id"),
                       "plan_digest": discovery.get("plan_digest")} if discovery else None),
        "mapping_digest": _digest({t["table"]: t["mapping_digest"] for t in tables}),
        "tables": tables,
    }


__all__ = ["EXPLAIN_TASK_CLASS", "MANDATORY", "discovery_captures", "explain_mapping",
           "select_facts", "table_explanation_facts", "table_facts"]

"""Table → node kind, foreign key → edge kind: a mapping PROPOSED for approval, then applied.

**Rules first (P16)**, each tried in order, each leaving its reason in `rationale`:

1. the table's name (or its singular) IS a kind of the active vocabulary;
2. the table has a column named after an identifier a SUBJECT kind declares (e.g. `email`), and
   that column is measured unique and non-null in the rows read → that subject kind;
3. the table's name (last word, singular) is listed under a Layer-0 META-kind in the tabular
   policy's `meta_kind_hints`, and exactly one vocabulary kind binds to that meta-kind (or one is
   named like it).

**Model second**: tables no rule could map are offered to the harness (`MAPPING_TASK_CLASS`) with
the vocabulary; the answer is validated against it. A table nobody could map stays UNMAPPED — its
rows are captured, counted and in the facts store, and the report says they are not in the graph.

Per mapped table the proposal names: the node kind, a label template, the primary key (row
identity), a **canonical_id column** (the exact-merge key a document mention can meet — a domain,
a natural reference such as an invoice number; measured unique; NEVER on a subject kind), the
attribute columns, and one edge per foreign key: the most specific
declared edge kind admitting the two kinds, trying the key's direction first and the reverse
(`referenced → row`) when only the reverse is explicitly declared.

**Subjects are never merged (hard constraint 1, R-022 f2 #1).** A SUBJECT kind's row carries no
exact-merge key at all: its declared identifier (an email) is recorded as `candidate_identifier`,
which only ever OFFERS another record holding the same value as a candidate a person selects. The
row is re-sighted by its own row id and by nothing else.

Saved as `<id>.mapping.yaml` beside the connection, per table `status: proposed|approved|unmapped`.
Only approved tables are built, and only an AUTHENTICATED principal approves (`approve_mapping`,
R-022 f1 #1): each table records its approver, time and the digest of what was approved, every
approval is appended to `<id>.mapping.approvals.jsonl`, and the mapping is re-validated against the
closed vocabulary at approval AND at run time (`effective_mapping`). The connection's
`auto_approve` flag is read but ignored, and the proposal says so.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .build import FK_PREFIX, ROW_KEY, _admits
from .connections import connection_path

MAPPING_TASK_CLASS = "schema_annotation"
_LABEL_NAMES = ("name", "full_name", "display_name", "title", "label", "subject", "summary",
                "description")
_IDENTITY_FIELDS = {"label", "canonical_id"}
MAX_ATTRS = 25


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TableMapping(_Strict):
    status: Literal["proposed", "approved", "unmapped"] = "proposed"
    origin: Literal["rules", "model", "manual"] = "rules"
    node: Optional[dict[str, Any]] = None
    edges: list[dict[str, Any]] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
    #: WHO approved THIS table and WHAT (R-022 f1 #1): {principal_id, authenticated_by,
    #: principal_type, dev_only, at, digest}. `digest` is `mapping_digest` of node + edges at
    #: approval; an approved table whose node/edges no longer hash to it is not approved.
    approval: Optional[dict[str, Any]] = None
    #: the table's SCHEMA the mapping was made against (R-022 f2 #9): its column names and its
    #: foreign-key columns. Every column the mapping names must be one of them; both are covered
    #: by the approval digest, so an approval is bound to that schema.
    columns: Optional[list[str]] = None
    fk_columns: Optional[list[str]] = None


class NodeSpecModel(_Strict):
    """The STRICT shape of a mapping's `node` (R-022 f1 #5) — checked at approval and at run
    time; a hand-edited YAML cannot smuggle an unknown key past it."""
    kind: str
    label: str
    sources: list[str] = Field(min_length=1)
    attrs: dict[str, str] = Field(default_factory=dict)
    #: the EXACT-MERGE key (non-subject kinds only, R-022 f2 #1)
    canonical_id: Optional[str] = None
    #: a SUBJECT kind's declared identifier column: it only ever proposes a CANDIDATE link a
    #: person selects; it never merges and is never the node's canonical id (R-022 f2 #1)
    candidate_identifier: Optional[str] = None


class EdgeSpecModel(_Strict):
    kind: str
    src: str
    dst: str
    evidence: list[str] = Field(default_factory=list)
    fk: Optional[str] = None
    reversed: bool = False


class SourceMapping(_Strict):
    source_id: str
    tables: dict[str, TableMapping] = Field(default_factory=dict)
    updated_at: str = ""
    approved_by: Optional[str] = None


def _policy() -> dict:
    from .tabular.policy import load_policy

    return load_policy()


def _forms(name: str) -> list[str]:
    n = name.lower()
    out = [n]
    if n.endswith("ies"):
        out.append(n[:-3] + "y")
    if n.endswith(("ses", "xes", "ches", "shes")):
        out.append(n[:-2])
    if n.endswith("s"):
        out.append(n[:-1])
    return out


def kind_for_table(table: str, vocab) -> Optional[str]:
    names = set(vocab.node_kinds)
    return next((f for f in _forms(table) if f in names), None)


def _columns(snap: dict) -> list[str]:
    return [c["name"] for c in snap.get("columns") or [] if c.get("name")]


def _measured_unique(snap: dict, column: str) -> tuple[bool, str]:
    prof = {c.get("name"): c for c in snap.get("columns") or []}.get(column) or {}
    if "unique" in prof and "distinct" in prof:
        n = snap.get("row_count_total")
        if prof["unique"]:
            return True, f"profiled unique and non-null over all {n} rows"
        return False, (f"profiled {prof.get('distinct')} distinct / {prof.get('non_null')} "
                       f"non-null of {n} rows")
    rows = snap.get("rows") or []
    if not rows:
        return False, "no rows read"
    vals = [r.get(column) for r in rows]
    if any(v is None or str(v).strip() == "" for v in vals):
        return False, "has empty values"
    if len({str(v) for v in vals}) != len(vals):
        return False, "has repeated values"
    total = snap.get("row_count_total")
    scope = (f"all {len(rows)} rows" if total in (None, len(rows))
             else f"the {len(rows)} of {total} rows read")
    return True, f"measured unique and non-null over {scope}"


def _subject_kind(snap: dict, vocab) -> tuple[Optional[str], str]:
    cols = {c.lower(): c for c in _columns(snap)}
    for kind in sorted(getattr(vocab, "subject_kinds", {}) or {}):
        for ident in vocab.identifiers_for(kind):
            col = cols.get(ident.lower())
            if col is None:
                continue
            ok, why = _measured_unique(snap, col)
            if ok:
                return kind, (f"column {col!r} is identifier {ident!r} of subject kind "
                              f"{kind!r} ({why})")
    return None, ""


def _meta_hint_kind(table: str, vocab, policy: dict) -> tuple[Optional[str], str]:
    from databridge_graph.meta import NodeMetaKind

    last = re.split(r"[_\-\s.]+", table.lower())[-1]
    forms = set(_forms(last))
    for meta_name, words in (policy.get("meta_kind_hints") or {}).items():
        if not forms & {w.lower() for w in words or []}:
            continue
        try:
            meta = NodeMetaKind(meta_name)
        except ValueError:
            continue
        bound = vocab.nodes_binding_to(meta)
        if len(bound) == 1:
            return bound[0], (f"table name {table!r} is a common name for the {meta_name!r} "
                              f"meta-kind; {bound[0]!r} is the only kind bound to it")
        if meta_name in bound:
            return meta_name, (f"table name {table!r} is a common name for the {meta_name!r} "
                               f"meta-kind; kind {meta_name!r} is named like it")
        return None, (f"table name {table!r} hints meta-kind {meta_name!r} but {len(bound)} "
                      f"kinds bind to it {list(bound)} — left for the model / a human")
    return None, ""


def resolve_kind(table: str, snap: dict, vocab, policy: Optional[dict] = None
                 ) -> tuple[Optional[str], str]:
    """(kind, why) by the three rules, or (None, why-not)."""
    policy = policy if policy is not None else _policy()
    k = kind_for_table(table, vocab)
    if k:
        return k, f"table name {table!r} matches kind {k!r}"
    k, why = _subject_kind(snap, vocab)
    if k:
        return k, why
    k, why2 = _meta_hint_kind(table, vocab, policy)
    if k:
        return k, why2
    return None, why2 or f"no node kind in the active vocabulary matches table {table!r}"


def other_table_keys(snapshots: dict[str, dict], tref: str) -> dict[str, tuple[str, set]]:
    """E2E run-3 (GW4 cross-track): every OTHER captured table's single primary key, as
    {other table: (its key column, the key values read)}. A column of this table that holds one of
    these is a REFERENCE to that table's row (declared as a foreign key or not), never this row's
    own identity."""
    out: dict[str, tuple[str, set]] = {}
    for other, snap in (snapshots or {}).items():
        if other == tref:
            continue
        pk = list(snap.get("primary_key") or [])
        if len(pk) != 1:
            continue
        vals = {str(r.get(pk[0])) for r in snap.get("rows") or [] if r.get(pk[0]) is not None}
        out[other] = (pk[0], vals)
    return out


def _references_other_key(snap: dict, column: str,
                          others: Optional[dict[str, tuple[str, set]]]) -> Optional[str]:
    """The other table whose key `column` holds — by the key column's own name, or because every
    value read is one of that table's key values — else None."""
    vals = {str(r.get(column)) for r in snap.get("rows") or [] if r.get(column) is not None}
    for other, (key_col, keys) in sorted((others or {}).items()):
        if column.lower() == str(key_col).lower() or (vals and keys and vals <= keys):
            return other
    return None


def _canonical_column(snap: dict, kind: str, vocab, pk: list[str], fk_cols: set[str],
                      policy: dict, others: Optional[dict[str, tuple[str, set]]] = None
                      ) -> tuple[Optional[str], str]:
    cols = _columns(snap)
    lower = {c.lower(): c for c in cols}
    types = {c["name"]: str(c.get("type") or "").lower() for c in snap.get("columns") or []}
    if vocab.is_subject(kind):
        # R-022 f2 #1: a subject is NEVER given an exact-merge key — not even a declared, unique
        # identifier. Its rows are re-sighted by their own row id only.
        return None, (f"{kind!r} is a subject kind: no canonical_id — its rows are never merged "
                      f"with any other record (hard constraint 1)")
    # E2E run 2 N-16: the table's BUSINESS KEY comes first — a single, non-numeric primary key
    # (customer_id "C-1001", invoice_no "INV-2026-0041") is how documents name the row. A
    # contact-like column (a domain, an email) is a fact ABOUT the row, not its key: picking it
    # first left every DB customer beside its document twin.
    if len(pk) == 1 and pk[0] not in fk_cols:
        t = types.get(pk[0], "")
        if not re.search(r"int|serial|numeric|decimal|real|double|float", t):
            ok, why = _measured_unique(snap, pk[0])
            if ok:
                return pk[0], (f"canonical_id = {pk[0]!r} (the table's business key: its text "
                               f"primary key; {why})")
    skipped: list[str] = []
    for word in policy.get("canonical_columns") or []:
        for c in cols:
            cl = c.lower()
            if c in fk_cols or not (cl == word or cl.endswith("_" + word)):
                continue
            ref = _references_other_key(snap, c, others)
            if ref is not None:
                # a row's canonical id comes from its OWN table's key: a column holding another
                # table's key names THAT row (a follow-up task's invoice_no is the invoice's key)
                if c not in skipped:
                    skipped.append(c)
                continue
            ok, why = _measured_unique(snap, c)
            if ok:
                return c, f"canonical_id = {c!r} (natural identifier by name; {why})"
    note = (f"; {skipped} hold another table's key (a reference, not this row's identity)"
            if skipped else "")
    return None, ("no natural identifier column: canonical_id is the source-qualified row key "
                  f"(joins to other sources only through edges){note}")


def _candidate_identifier(snap: dict, kind: str, vocab) -> tuple[Optional[str], str]:
    """A SUBJECT kind's declared identifier column, used ONLY to offer candidate links that a
    person selects (R-022 f2 #1). Never a merge key."""
    if not vocab.is_subject(kind):
        return None, ""
    lower = {c.lower(): c for c in _columns(snap)}
    for ident in vocab.identifiers_for(kind):
        col = lower.get(str(ident).lower())
        if col:
            return col, (f"candidate_identifier = {col!r} (declared identifier of {kind!r}): a "
                         f"record with the same value is offered as a CANDIDATE for a person to "
                         f"select, never merged")
    return None, ""


def _label_template(columns: list[str], skip: set[str], pk: list[str], types: dict
                    ) -> tuple[str, str]:
    lower = {c.lower(): c for c in columns}
    if "first_name" in lower and "last_name" in lower:
        return "{" + lower["first_name"] + "} {" + lower["last_name"] + "}", \
               "label = first_name + last_name"
    for want in _LABEL_NAMES:
        if want in lower and lower[want] not in skip:
            return "{" + lower[want] + "}", f"label column {lower[want]!r} (by name)"
    for c in columns:
        if c.lower().endswith("_name") and c not in skip:
            return "{" + c + "}", f"label column {c!r} (by name)"
    if len(pk) == 1 and not re.search(r"int|serial", str(types.get(pk[0], "")).lower()):
        return "{" + pk[0] + "}", f"no name-like column; label is the text key {pk[0]!r}"
    return "{" + ROW_KEY + "}", "no name-like column; label is the row identity"


def _table_ref(schema: Optional[str], table: str) -> str:
    return f"{schema}.{table}" if schema else table


def _explicit(decl, end: str, kind: str) -> bool:
    return kind in tuple(getattr(decl, end) or ())


_STOP = frozenset({"a", "an", "the", "is", "by", "or", "and", "of", "to", "for", "in", "on",
                   "id", "with", "its", "any", "one", "who", "that", "this"})


def _roots(text: str) -> set[str]:
    """Crude word roots (suffix-stripped, >= 3 chars) — enough to hear `account_owner` in
    "owns", or `manager_id` in `manages`. A tie-breaker between DECLARED candidates, never a
    reason to pick an undeclared one."""
    out = set()
    for w in re.findall(r"[a-z]+", text.lower()):
        if w in _STOP:
            continue
        for suf in ("ing", "ers", "er", "ed", "es", "s"):
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                w = w[: -len(suf)]
                break
        if len(w) >= 3:
            out.add(w)
    return out


def _hint_score(hint_roots: set[str], name: str, decl) -> int:
    words = _roots(name.replace("_", " ") + " " + str(getattr(decl, "description", "") or ""))
    return sum(1 for h in hint_roots for w in words if w.startswith(h) or h.startswith(w))


def _fk_edge(vocab, kind: str, ref_kind: str, hint: str, policy: dict
             ) -> tuple[Optional[str], bool, str]:
    """(edge_kind, reversed, why).

    1. forward (row → referenced) with BOTH endpoints declared; else
    2. the reverse (referenced → row) with both declared, when the key's own name matches that
       declaration (`account_owner` ~ `manages` "owns"); else
    3. forward through a wildcard endpoint (the reverse named in the rationale for review); else
    4. the reverse with both declared.
    Within a step, meta-kinds are tried in the policy's preference order; among candidates of
    one meta-kind the column name's words decide (`account_owner` ~ "owns"), then the most
    specific declaration (fewest endpoint kinds), then the name."""
    from databridge_graph.meta import EdgeMetaKind

    metas = tuple(policy.get("fk_edge_metas") or ("part_of", "references"))
    hint_roots = _roots(hint.replace("_", " "))

    def best(src: str, dst: str, need_explicit: bool) -> tuple[Optional[str], str]:
        for meta in metas:
            try:
                names = vocab.edges_binding_to(EdgeMetaKind(meta))
            except ValueError:
                continue
            cands = []
            for name in names:
                decl = vocab.edge(name)
                explicit = _explicit(decl, "src", src) and _explicit(decl, "dst", dst)
                if need_explicit and not explicit:
                    continue
                if not (_admits(decl, "src", src) and _admits(decl, "dst", dst)):
                    continue
                spec = len(tuple(decl.src or ())) + len(tuple(decl.dst or ()))
                cands.append((-_hint_score(hint_roots, name, decl), spec, name))
            if cands:
                cands.sort()
                h, _, name = cands[0]
                why = " (column words match its declaration)" if h < 0 else ""
                if len(cands) > 1:
                    why += f"; other {meta} candidates: {[c[2] for c in cands[1:]]}"
                return name, why
        return None, ""

    ek, why = best(kind, ref_kind, True)
    if ek:
        return ek, False, f"{ek!r} declares {kind} -> {ref_kind}{why}"
    rev, rwhy = best(ref_kind, kind, True)
    if rev and "column words match" in rwhy:
        return rev, True, (f"{rev!r} declares {ref_kind} -> {kind} and the key's name matches it "
                           f"(read in reverse: no edge declares {kind} -> {ref_kind}){rwhy}")
    ek, why = best(kind, ref_kind, False)
    if ek:
        extra = (f"; {rev!r} declares the reverse {ref_kind} -> {kind} — choose it on review "
                 f"if the key means that") if rev else ""
        return ek, False, (f"{ek!r} admits {kind} -> {ref_kind} through a wildcard endpoint"
                           f"{why}{extra}")
    if rev:
        return rev, True, (f"{rev!r} declares {ref_kind} -> {kind} (read in reverse: nothing "
                           f"admits {kind} -> {ref_kind}){rwhy}")
    return None, False, f"no declared edge kind admits {kind} -> {ref_kind} in either direction"


def _table_mapping(tref: str, snap: dict, kind: str, kinds: dict, vocab, *, origin: str,
                   why: str, label_column: Optional[str] = None,
                   policy: Optional[dict] = None,
                   others: Optional[dict[str, tuple[str, set]]] = None) -> TableMapping:
    policy = policy if policy is not None else _policy()
    columns = _columns(snap)
    types = {c["name"]: c.get("type") for c in snap.get("columns") or []}
    pk = list(snap.get("primary_key") or [])
    fks = list(snap.get("foreign_keys") or [])
    fk_cols = {f["column"] for f in fks}
    rationale = [why]
    if label_column and label_column in columns:
        label, lwhy = "{" + label_column + "}", f"label column {label_column!r} ({origin})"
    else:
        label, lwhy = _label_template(columns, set(pk) | fk_cols, pk, types)
    rationale.append(lwhy)
    if not pk:
        rationale.append("no primary key: row identity is a hash of the row's values")
    canonical, cwhy = _canonical_column(snap, kind, vocab, pk, fk_cols, policy, others)
    rationale.append(cwhy)
    candidate, kwhy = _candidate_identifier(snap, kind, vocab)
    if kwhy:
        rationale.append(kwhy)
    attrs = {c: c for c in columns
             if c not in pk and c not in fk_cols and c.lower() not in _IDENTITY_FIELDS}
    if len(attrs) > MAX_ATTRS:
        rationale.append(f"{len(attrs)} attribute columns; first {MAX_ATTRS} kept")
        attrs = dict(list(attrs.items())[:MAX_ATTRS])
    edges = []
    for fk in fks:
        ref = _table_ref(fk.get("ref_schema"), fk.get("ref_table") or "")
        ref_kind = kinds.get(ref) or kind_for_table(fk.get("ref_table") or "", vocab)
        if ref_kind is None:
            rationale.append(f"FK {fk['column']} -> {ref}: referenced table has no kind; no edge")
            continue
        stem = fk["column"].lower().removesuffix("_id")
        ek, rev, ewhy = _fk_edge(vocab, kind, ref_kind, stem, policy)
        if ek is None:
            rationale.append(f"FK {fk['column']} -> {ref}: {ewhy}; no edge")
            continue
        row_end = f"{kind}:{{{ROW_KEY}}}"
        ref_end = f"{ref_kind}:{{{FK_PREFIX}{fk['column']}}}"
        edges.append({"kind": ek, "src": ref_end if rev else row_end,
                      "dst": row_end if rev else ref_end,
                      "evidence": [fk["column"]], "fk": f"{fk['column']} -> {ref}",
                      "reversed": rev})
        rationale.append(f"FK {fk['column']} -> {ref}: edge {ewhy} — review")
    node = {"kind": kind, "label": label, "sources": pk or [ROW_KEY], "attrs": attrs}
    if canonical:
        node["canonical_id"] = canonical
    if candidate:
        node["candidate_identifier"] = candidate
    return TableMapping(status="proposed", origin=origin, node=node, edges=edges,
                        rationale=rationale, columns=columns, fk_columns=sorted(fk_cols))


def propose_rules(snapshots: dict[str, dict], vocab,
                  context: Optional[dict[str, dict]] = None) -> dict[str, TableMapping]:
    """`context`: every table captured from the source (default: `snapshots`), so a table first
    seen on a later sync still knows the other tables' keys."""
    policy = _policy()
    ctx = context if context is not None else snapshots
    resolved = {tref: resolve_kind(s.get("table") or tref, s, vocab, policy)
                for tref, s in snapshots.items()}
    kinds = {tref: (None if (snapshots[tref].get("history_snapshot") or {}).get("is") else k)
             for tref, (k, _) in resolved.items()}
    out: dict[str, TableMapping] = {}
    for tref, snap in snapshots.items():
        kind, why = resolved[tref]
        hist = snap.get("history_snapshot") or {}
        if hist.get("is"):
            out[tref] = TableMapping(status="unmapped", rationale=[
                f"history snapshot ({'; '.join(hist.get('signals') or [])}): excluded from the "
                f"graph by rule; map it by hand to include it"])
            continue
        if kind is None:
            out[tref] = TableMapping(status="unmapped", rationale=[why])
            continue
        out[tref] = _table_mapping(tref, snap, kind, kinds, vocab, origin="rules", why=why,
                                   policy=policy, others=other_table_keys(ctx, tref))
    return out


class _ModelTable(BaseModel):
    table: str
    node_kind: Optional[str] = None
    label_column: Optional[str] = None
    rationale: str = ""


class _ModelMapping(BaseModel):
    tables: list[_ModelTable] = Field(default_factory=list)


def screen_for_model(payload: Any, *, screen_cfg=None) -> tuple[bool, dict]:
    """Deterministic screen of UNTRUSTED source metadata before a model sees it (R-022 f1 #3).
    Returns (send, record): only an `allow` verdict with nothing masked is sent — a masked
    identifier would no longer name the table, and `review`/`block` material never reaches a
    model without an approval. `record` is the screen's own verdict record (rules fired)."""
    from .screen import load_screen, screen_text

    text = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True,
                                                               default=str)
    verdict = screen_text(text, screen_cfg if screen_cfg is not None else load_screen())
    rec = verdict.as_record()
    send = verdict.decision == "allow" and not verdict.redactions
    return send, rec


def propose_model(snapshots: dict[str, dict], unmapped: list[str], current: dict,
                  vocab, harness, *, screen_cfg=None,
                  context: Optional[dict[str, dict]] = None) -> dict[str, TableMapping]:
    """The model's proposals for tables no rule could map.

    Every table's untrusted metadata (its name and column names) is SCREENED first (R-022 f1 #3):
    a table whose metadata is not a clean `allow` is never sent — it stays unmapped with the
    screen's decision and rules in its rationale. The answer is then built in TWO passes (#16):
    every valid kind is collected first, so a foreign key to a table the model mapped later in its
    answer still gets its edge — the result does not depend on the order of the answer."""
    if not unmapped or harness is None:
        return {}
    out: dict[str, TableMapping] = {}
    sendable: list[str] = []
    for t in unmapped:
        meta = {"table": t, "columns": [c.get("name") for c in snapshots[t].get("columns") or []]}
        send, rec = screen_for_model(meta, screen_cfg=screen_cfg)
        if send:
            sendable.append(t)
            continue
        fired = sorted({str(f.get("rule")) for f in rec.get("fired") or []})
        out[t] = TableMapping(status="unmapped", origin="model", rationale=[
            f"not offered to the model: the screen decided {rec.get('decision')!r}"
            f"{' with masking' if rec.get('redactions') else ''} on this table's metadata "
            f"(rules {fired}); map it by hand"])
    if not sendable:
        return out
    kinds = [{"kind": n, "binds_to": d.binds_to.value, "description": d.description}
             for n, d in sorted(vocab.node_kinds.items())]
    tables = [{"table": t, "columns": [c.get("name") for c in snapshots[t].get("columns") or []]}
              for t in sendable]
    system = {"role": "system", "content": (
        "Map each database table to ONE node kind from the closed vocabulary, or null when none "
        "fits. Never invent a kind. Pick the column that names each row as label_column.")}
    user = {"role": "user", "content": json.dumps({"vocabulary": kinds, "tables": tables},
                                                  sort_keys=True)}
    try:
        draft: _ModelMapping = harness.complete_structured(
            MAPPING_TASK_CLASS, [system, user], _ModelMapping, seed=7).parsed
    except Exception as exc:                                         # noqa: BLE001
        out.update({t: TableMapping(status="unmapped", origin="model", rationale=[
            f"model mapping failed ({type(exc).__name__}); map by hand"]) for t in sendable})
        return out
    # pass 1: the valid answers (first answer per table wins; a table answered twice is a
    # conflict only if the kinds differ — then nothing is taken for it)
    answers: dict[str, _ModelTable] = {}
    conflicted: set[str] = set()
    for t in draft.tables:
        if t.table not in sendable:
            continue
        prev = answers.get(t.table)
        if prev is not None and prev.node_kind != t.node_kind:
            conflicted.add(t.table)
        answers.setdefault(t.table, t)
    known = {tref: (m.node or {}).get("kind") for tref, m in current.items()}
    for name in sorted(answers):
        t = answers[name]
        if name not in conflicted and t.node_kind and t.node_kind in vocab.node_kinds:
            known[name] = t.node_kind
    # pass 2: build every mapping against the complete set of kinds
    for name in sorted(answers):
        t = answers[name]
        if name in conflicted:
            out[name] = TableMapping(status="unmapped", origin="model", rationale=[
                "the model answered this table twice with different kinds (refused)"])
            continue
        if not t.node_kind or t.node_kind not in vocab.node_kinds:
            out[name] = TableMapping(status="unmapped", origin="model", rationale=[
                f"model proposed {t.node_kind!r}, which is not in the vocabulary (refused)"
                if t.node_kind else "model found no fitting kind"])
            continue
        out[name] = _table_mapping(name, snapshots[name], t.node_kind, known, vocab,
                                   origin="model", label_column=t.label_column,
                                   others=other_table_keys(context if context is not None
                                                           else snapshots, name),
                                   why=f"model: {t.rationale or 'kind proposed'}")
    return out


# --- persistence ---------------------------------------------------------------------------------
def mapping_path(source_id: str, root: Optional[Path] = None) -> Path:
    return connection_path(source_id, root).with_name(f"{source_id}.mapping.yaml")


def load_mapping(source_id: str, root: Optional[Path] = None) -> SourceMapping:
    p = mapping_path(source_id, root)
    if not p.is_file():
        return SourceMapping(source_id=source_id)
    return SourceMapping.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")) or {})


def save_mapping(m: SourceMapping, root: Optional[Path] = None) -> Path:
    p = mapping_path(m.source_id, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    m = m.model_copy(update={"updated_at": dt.datetime.now(dt.timezone.utc)
                             .isoformat(timespec="seconds")})
    p.write_text(yaml.safe_dump(m.model_dump(mode="json"), sort_keys=False), encoding="utf-8")
    return p


def ensure_mapping(source_id: str, snapshots: dict[str, dict], vocab, *, harness=None,
                   auto_approve: bool = False, root: Optional[Path] = None) -> SourceMapping:
    """Propose entries for tables the mapping does not know yet; keep every existing decision.

    `auto_approve` is accepted and IGNORED (Codex round 3, Critical): a connection flag is not a
    person, so it can never make a mapping count as approved. Approval is `approve_mapping` by a
    named, authenticated principal. The flag is reported in each new proposal's rationale.

    An entry left `unmapped` by the rules or the model (not by a human) is re-proposed by the
    rules + the policy fallback on every call (E2E F-23: a table first seen empty, or one the
    agents write to, otherwise stays out of the graph forever). A `manual` entry is a decision and
    is kept. The model is asked only about tables it has not seen before.

    The RETURNED mapping is the effective one (R-022 f1 #1/#5): an `approved` table whose approval
    does not verify — no authenticated record, node/edges changed since, not in the append-only
    log, or no longer valid against `vocab` — comes back `proposed` and is not built."""
    return effective_mapping(_ensure_mapping(source_id, snapshots, vocab, harness=harness,
                                             auto_approve=auto_approve, root=root), vocab, root,
                             snapshots=snapshots)


def _ensure_mapping(source_id: str, snapshots: dict[str, dict], vocab, *, harness=None,
                    auto_approve: bool = False, root: Optional[Path] = None) -> SourceMapping:
    m = load_mapping(source_id, root)
    # R-022 f2 #9: an entry proposed before mappings carried a schema snapshot gets the table's
    # CURRENT columns, so its references can be checked at approval. Never an approved entry (its
    # approval digest would change under it).
    backfill = {t: m.tables[t].model_copy(update={
        "columns": _columns(s), "fk_columns": sorted(
            f.get("column") for f in s.get("foreign_keys") or [] if f.get("column"))})
        for t, s in snapshots.items()
        if t in m.tables and m.tables[t].columns is None and m.tables[t].status != "approved"
        and m.tables[t].node is not None}
    # R-022 f2 #1: an entry written before subjects lost their merge key — a SUBJECT node with a
    # canonical_id — is never honoured again. It is re-proposed with that identifier moved to
    # `candidate_identifier` (status `proposed`, approval dropped), so a person re-approves it.
    for t, tm in m.tables.items():
        node = dict(tm.node or {})
        try:
            subject = bool(node.get("kind")) and bool(vocab.is_subject(node["kind"]))
        except Exception:                                            # noqa: BLE001
            subject = False
        if subject and node.get("canonical_id") is not None:
            ident = node.pop("canonical_id")
            if node.get("candidate_identifier") is None:
                node["candidate_identifier"] = ident
            backfill[t] = (backfill.get(t) or tm).model_copy(update={
                "node": node, "status": "proposed", "approval": None, "rationale": [
                    *tm.rationale, f"re-proposed: subject kind {node['kind']!r} no longer carries "
                                   f"an exact-merge key; {ident!r} is now candidate_identifier "
                                   f"(candidates only, never merged) — approve it again"]})
    if backfill:
        m = m.model_copy(update={"tables": {**m.tables, **backfill}})
        save_mapping(m, root)
    new = {t: s for t, s in snapshots.items() if t not in m.tables}
    retry = {t: s for t, s in snapshots.items()
             if t in m.tables and m.tables[t].status == "unmapped"
             and m.tables[t].origin != "manual"
             and not (s.get("history_snapshot") or {}).get("is")}
    if not new and not retry:
        return m
    proposed: dict[str, TableMapping] = {}
    if new:
        proposed = propose_rules(new, vocab, context=snapshots)
        unmapped = [t for t, tm in proposed.items() if tm.status == "unmapped"]
        proposed.update(propose_model(new, unmapped, {**m.tables, **proposed}, vocab, harness,
                                      context=snapshots))
    if retry:
        again = propose_rules(retry, vocab, context=snapshots)
        proposed.update({t: tm for t, tm in again.items() if tm.status == "proposed"})
        still = {t: m.tables[t] for t in retry if t not in proposed}
    else:
        still = {}
    known = {**m.tables, **proposed}
    fallback = _fallback_proposals(snapshots, {**{t: tm for t, tm in proposed.items()
                                                  if tm.status == "unmapped"}, **still},
                                   known, vocab)
    proposed.update(fallback)
    if auto_approve:
        note = ("auto_approve on the connection was ignored: a mapping is approved only by a "
                "named person (approve --mapping)")
        proposed = {t: (tm.model_copy(update={"rationale": [*tm.rationale, note]})
                        if tm.status == "proposed" else tm) for t, tm in proposed.items()}
    if not proposed:
        return m
    m = m.model_copy(update={"tables": {**m.tables, **proposed}})
    save_mapping(m, root)
    return m


def _fallback_proposals(snapshots: dict[str, dict], unmapped: dict[str, TableMapping],
                        known: dict[str, TableMapping], vocab) -> dict[str, TableMapping]:
    """The policy's `unmapped_fallback_meta_kind` proposal for tables nothing else could map —
    PROPOSED only, with the earlier reasons kept. History snapshots are never offered."""
    from databridge_graph.meta import NodeMetaKind

    policy = _policy()
    meta_name = policy.get("unmapped_fallback_meta_kind")
    if not meta_name or not unmapped:
        return {}
    try:
        bound = list(vocab.nodes_binding_to(NodeMetaKind(meta_name)))
    except ValueError:
        return {}
    kind = meta_name if meta_name in bound else (bound[0] if len(bound) == 1 else None)
    if kind is None:
        return {}
    kinds = {t: (tm.node or {}).get("kind") for t, tm in known.items()}
    out: dict[str, TableMapping] = {}
    for tref, tm in unmapped.items():
        snap = snapshots.get(tref)
        if snap is None or (snap.get("history_snapshot") or {}).get("is"):
            continue
        why = (f"fallback: {'; '.join(tm.rationale) or 'no kind matched'} — proposed as "
               f"{kind!r}, the kind bound to the {meta_name!r} meta-kind "
               f"(policy unmapped_fallback_meta_kind); review before approving")
        out[tref] = _table_mapping(tref, snap, kind, kinds, vocab, origin="rules", why=why,
                                   policy=policy, others=other_table_keys(snapshots, tref))
    return out


# --- validation + approval (R-022 f1 #1, #5) -------------------------------------------------------
class MappingApprovalRefused(ValueError):
    """An approval without an authenticated principal, or of a mapping that does not validate."""


_ENDPOINT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def mapping_digest(tm: TableMapping) -> str:
    """sha256 of what an approval covers: the table's node and edges, canonical JSON — and the
    schema snapshot (`columns`, `fk_columns`) when the mapping carries one (R-022 f2 #9), so an
    approval is bound to the schema it was checked against."""
    data: dict[str, Any] = {"node": tm.node, "edges": tm.edges}
    if tm.columns is not None:
        data["columns"] = sorted(tm.columns)
        data["fk_columns"] = sorted(tm.fk_columns or [])
    body = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


_TEMPLATE_FIELD = re.compile(r"\{([^{}]*)\}")


def reference_problems(table: str, tm: TableMapping, *, columns: Optional[list[str]] = None,
                       fk_columns: Optional[list[str]] = None) -> list[str]:
    """Every column the mapping NAMES that the table does not have (R-022 f2 #9): the label
    template's fields, `sources`, attribute columns, `canonical_id`, `candidate_identifier`, each
    edge's evidence columns and each `{walnut_fk_<col>}` endpoint (which must be a foreign-key
    column). Checked against `columns`/`fk_columns` when given (the LIVE schema), else against the
    snapshot the mapping carries. No schema at all is itself a problem: nothing could be checked."""
    cols = columns if columns is not None else tm.columns
    fks = fk_columns if fk_columns is not None else tm.fk_columns
    if cols is None:
        return [f"{table}: the mapping carries no schema snapshot, so the columns it names cannot "
                f"be checked; re-propose it from a profile"]
    have, fk_have = set(cols) | {ROW_KEY}, set(fks or [])
    node = tm.node or {}
    named: list[tuple[str, str]] = [("label", f) for f in _TEMPLATE_FIELD.findall(
        str(node.get("label") or ""))]
    named += [("sources", c) for c in node.get("sources") or []]
    named += [(f"attr {a!r}", c) for a, c in (node.get("attrs") or {}).items()]
    for key in ("canonical_id", "candidate_identifier"):
        if node.get(key) is not None:
            named.append((key, node[key]))
    problems = [f"{table}: {where} names column {col!r}, which the table does not have"
                for where, col in named if str(col) not in have]
    for i, e in enumerate(tm.edges or []):
        for col in (e or {}).get("evidence") or []:
            if str(col) not in have:
                problems.append(f"{table}: edge {i} evidence names column {col!r}, which the "
                                f"table does not have")
        for side in ("src", "dst"):
            m = _ENDPOINT.match(str((e or {}).get(side) or ""))
            if m and m.group(2).startswith(FK_PREFIX):
                col = m.group(2)[len(FK_PREFIX):]
                if col not in fk_have:
                    problems.append(f"{table}: edge {i} {side} reads foreign key {col!r}, which "
                                    f"is not a foreign-key column of the table")
    return problems


def validate_table_mapping(table: str, tm: TableMapping, vocab, *,
                           check_references: bool = False) -> list[str]:
    """Problems with one table's mapping against the ACTIVE closed vocabulary; empty = valid.

    Structure (strict node/edge models), the node kind, every edge kind, both endpoint kinds, the
    declared admissibility of each edge (`src`/`dst` of its declaration), that each edge has the
    table's own row at one end, and — for a SUBJECT kind — that `canonical_id` (the exact-merge
    key) is one of the kind's DECLARED identifiers. Run at approval AND at run time."""
    problems: list[str] = []
    if tm.node is None:
        return [f"{table}: no node to approve"]
    try:
        node = NodeSpecModel.model_validate(tm.node)
    except Exception as exc:                                         # noqa: BLE001
        return [f"{table}: node is malformed ({str(exc).splitlines()[0][:200]})"]
    if node.kind not in vocab.node_kinds:
        problems.append(f"{table}: node kind {node.kind!r} is not in the active vocabulary")
    try:
        is_subject = bool(vocab.is_subject(node.kind))
    except Exception:                                                # noqa: BLE001
        is_subject = False
    if is_subject and node.canonical_id is not None:
        # R-022 f2 #1: no exact-merge key of ANY kind on a subject — a declared identifier goes
        # in `candidate_identifier`, which only proposes candidates a person selects
        problems.append(f"{table}: subject kind {node.kind!r} cannot carry a canonical_id "
                        f"({node.canonical_id!r}): subject rows are never merged; put a declared "
                        f"identifier in candidate_identifier")
    if node.candidate_identifier is not None:
        if not is_subject:
            problems.append(f"{table}: candidate_identifier is for subject kinds only "
                            f"({node.kind!r} is not one)")
        else:
            idents = {str(i).lower() for i in vocab.identifiers_for(node.kind)}
            if node.candidate_identifier.lower() not in idents:
                problems.append(f"{table}: candidate_identifier {node.candidate_identifier!r} is "
                                f"not a declared identifier of subject kind {node.kind!r} "
                                f"{sorted(idents)}")
    own = f"{node.kind}:{{{ROW_KEY}}}"
    for i, raw in enumerate(tm.edges or []):
        try:
            e = EdgeSpecModel.model_validate(raw)
        except Exception as exc:                                     # noqa: BLE001
            problems.append(f"{table}: edge {i} is malformed ({str(exc).splitlines()[0][:200]})")
            continue
        if e.kind not in vocab.edge_kinds:
            problems.append(f"{table}: edge kind {e.kind!r} is not in the active vocabulary")
            continue
        ends: dict[str, str] = {}
        for side, text in (("src", e.src), ("dst", e.dst)):
            m = _ENDPOINT.match(text)
            if m is None:
                problems.append(f"{table}: edge {i} {side} {text!r} is not '<kind>:{{column}}'")
                continue
            kind, fld = m.group(1), m.group(2)
            if kind not in vocab.node_kinds:
                problems.append(f"{table}: edge {i} {side} kind {kind!r} is not in the "
                                f"vocabulary")
            if fld != ROW_KEY and not fld.startswith(FK_PREFIX):
                problems.append(f"{table}: edge {i} {side} names {fld!r}, neither the row key "
                                f"nor a foreign-key column")
            ends[side] = kind
        if own not in (e.src, e.dst):
            problems.append(f"{table}: edge {i} has neither end on this table's own row ({own})")
        try:
            decl = vocab.edge(e.kind)
        except Exception:                                            # noqa: BLE001
            decl = None
        if decl is not None and len(ends) == 2 and not (
                _admits(decl, "src", ends["src"]) and _admits(decl, "dst", ends["dst"])):
            problems.append(f"{table}: edge kind {e.kind!r} does not admit "
                            f"{ends['src']} -> {ends['dst']}")
    if check_references:
        problems += reference_problems(table, tm)
    return problems


def approval_log_path(source_id: str, root: Optional[Path] = None) -> Path:
    return mapping_path(source_id, root).with_name(f"{source_id}.mapping.approvals.jsonl")


def _record_digest(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k != "record_digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     default=str).encode("utf-8")).hexdigest()


#: Fields of a mapping approval-log line that are the LOG's, not the approval record's.
_LOG_ONLY = ("action", "source_id", "table", "record_digest", "prev", "hash")


def _append_approval(source_id: str, rec: dict, root: Optional[Path]) -> None:
    """Append-only AND hash-chained: the SAME log `plans.py` keeps (`plans.append_chained`, FXJ3;
    INT5 for R-022 sources cross-track 3). Each line also keeps its own `record_digest`."""
    from .plans import append_chained

    rec = {**rec, "record_digest": _record_digest(rec)}
    append_chained(approval_log_path(source_id, root), rec)


def _canon_record(rec: dict) -> str:
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), default=str)


def logged_approvals(source_id: str, root: Optional[Path] = None) -> dict[str, dict[str, list]]:
    """{table: {digest: [the approval record as logged, ...]}} of approvals in the append-only,
    hash-chained log (`plans.read_chained`). A log whose chain does not verify — a line edited,
    removed or inserted — honours NOTHING (fail closed); a legacy line written before the chain
    existed is never evidence; a line whose own digest, source or principal does not verify is
    skipped. Membership (`digest in out[table]`) is what it always was; the records let a caller
    compare the WHOLE approval, not only its digest."""
    from .plans import PlanError, read_chained

    out: dict[str, dict[str, list]] = {}
    try:
        lines = read_chained(approval_log_path(source_id, root), what="mapping approval log")
    except PlanError:
        return out
    for rec in lines:
        body = {k: v for k, v in rec.items() if k not in ("prev", "hash")}
        if body.get("record_digest") != _record_digest(body):
            continue
        if rec.get("source_id") != source_id or not str(rec.get("principal_id") or "").strip() \
                or not str(rec.get("authenticated_by") or "").strip():
            continue
        approval = {k: v for k, v in rec.items() if k not in _LOG_ONLY}
        out.setdefault(str(rec.get("table")), {}).setdefault(str(rec.get("digest")), []) \
            .append(approval)
    return out


def _whole_record_logged(a: dict, entries) -> bool:
    """Is the approval record `a` (as the mapping file carries it) IDENTICAL — every field — to
    one the chained log holds for its digest? A plain set of digests (an older caller) carries no
    records, so only membership can be checked there."""
    if not isinstance(entries, dict):
        return True
    want = _canon_record(a)
    return any(_canon_record(r) == want for r in entries.get(str(a.get("digest")), ()))


def approval_problem(source_id: str, table: str, tm: TableMapping, vocab, *,
                     logged: Optional[dict] = None,
                     root: Optional[Path] = None,
                     live: Optional[dict] = None) -> Optional[str]:
    """Why an `approved` table does NOT count as approved at run time, or None when it does: the
    approval record is missing / not by an authenticated principal, the node or edges changed
    since (digest), the append-only log does not hold it, or the mapping no longer validates
    against the active vocabulary."""
    if tm.status != "approved":
        return None
    a = tm.approval or {}
    if not str(a.get("principal_id") or "").strip() or \
            not str(a.get("authenticated_by") or "").strip():
        return "no authenticated approval record (a YAML `status: approved` is not an approval)"
    digest = mapping_digest(tm)
    if a.get("digest") != digest:
        return "the node/edges changed after approval (digest mismatch)"
    logged = logged if logged is not None else logged_approvals(source_id, root)
    if digest not in logged.get(table, set()):
        return "the approval is not in the append-only approvals log"
    if not _whole_record_logged(a, logged.get(table, {})):
        # INT5 (R-022 sources cross-track 3): the WHOLE record, not two or three fields
        return ("the approval record differs from the one in the hash-chained approvals log (a "
                "field was edited after approval)")
    problems = validate_table_mapping(table, tm, vocab)
    if problems:
        return "no longer valid against the active vocabulary: " + "; ".join(problems)
    if live is not None:
        # R-022 f2 #9: every column it names must still exist in the table as profiled NOW
        refs = reference_problems(table, tm, columns=_columns(live), fk_columns=[
            f.get("column") for f in live.get("foreign_keys") or [] if f.get("column")])
        if refs:
            return "it names columns the live table does not have: " + "; ".join(refs)
    return None


def effective_mapping(m: SourceMapping, vocab, root: Optional[Path] = None,
                      snapshots: Optional[dict[str, dict]] = None) -> SourceMapping:
    """The mapping as the BUILD may use it: every `approved` table whose approval does not verify
    (`approval_problem`) is returned as `proposed`, the reason in its rationale. With `snapshots`
    (the tables as profiled now), an approved table naming a column the live table no longer has
    is not honoured either (R-022 f2 #9). The file on disk is not changed."""
    logged = logged_approvals(m.source_id, root)
    tables = {}
    changed = False
    for t, tm in m.tables.items():
        why = approval_problem(m.source_id, t, tm, vocab, logged=logged, root=root,
                               live=(snapshots or {}).get(t))
        if why:
            changed = True
            tm = tm.model_copy(update={"status": "proposed", "rationale": [
                *tm.rationale, f"approval NOT honoured: {why}; approve it again"]})
        tables[t] = tm
    return m.model_copy(update={"tables": tables}) if changed else m


def approve_mapping(source_id: str, *, approved_by: Any = None, principal: Any = None,
                    tables: Optional[list[str]] = None, vocab=None,
                    root: Optional[Path] = None) -> SourceMapping:
    """An AUTHENTICATED principal approves proposed tables (R-022 f1 #1).

    `principal` (or, for compatibility, a principal OBJECT passed as `approved_by`) must pass
    `plans.require_authenticated_approver`; a bare name is refused. Every table to approve is
    re-validated against the active vocabulary first (#5) — one invalid table refuses the whole
    call, naming each problem. Each approved table records its own approver, time and the digest
    of the node + edges approved (Bob approving a later table never re-attributes Alice's), and
    every approval is appended to `<source>.mapping.approvals.jsonl` (append-only)."""
    from .plans import ApprovalRefused, require_authenticated_approver

    who_obj = principal if principal is not None else approved_by
    try:
        who = require_authenticated_approver(who_obj, what="mapping approval")
    except ApprovalRefused as exc:
        raise MappingApprovalRefused(str(exc)) from None
    if vocab is None:
        from databridge_graph.packs import active_vocabulary

        vocab = active_vocabulary()
    m = load_mapping(source_id, root)
    logged = logged_approvals(source_id, root)
    # proposed tables, and approved ones whose approval no longer verifies (approve it again)
    todo = [t for t, tm in m.tables.items() if (tables is None or t in tables) and (
        tm.status == "proposed" or (tm.status == "approved" and approval_problem(
            source_id, t, tm, vocab, logged=logged, root=root)))]
    problems = [p for t in todo for p in validate_table_mapping(t, m.tables[t], vocab,
                                                                check_references=True)]
    if problems:
        raise MappingApprovalRefused(f"mapping for {source_id!r} cannot be approved: "
                                     f"{'; '.join(problems)}")
    at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    updated = dict(m.tables)
    for t in todo:
        tm = m.tables[t]
        record = {**who, "at": at, "digest": mapping_digest(tm)}
        _append_approval(source_id, {"action": "approve", "source_id": source_id, "table": t,
                                     **record}, root)
        updated[t] = tm.model_copy(update={"status": "approved", "approval": record})
    m = m.model_copy(update={"tables": updated,
                             "approved_by": who["principal_id"] if todo else m.approved_by})
    save_mapping(m, root)
    return m


def _edge_key(e: dict) -> str:
    return json.dumps(e, sort_keys=True, default=str, separators=(",", ":"))


#: the one scope a delegation may carry (adapter_agents `DelegatedApproval.scope`)
DELEGATION_SCOPE = "additive_within_declared_scope"


def _writer_targets() -> tuple[set[str], Optional[str]]:
    """{schema.table, table} of every table a pack WRITER SPEC lets an agent write to, or
    (set(), why) when the specs cannot be read — the caller then fails closed."""
    try:
        from . import paths
        from .tabular.writer import validate_writer_spec

        out: set[str] = set()
        for f in sorted(Path(paths.packs_dir()).glob("*/writers/*.writer.yaml")):
            spec = validate_writer_spec(yaml.safe_load(f.read_text(encoding="utf-8")) or {})
            tbl = str(spec.get("table") or "")
            out.add(_table_ref(spec.get("schema"), tbl).lower())
            out.add(tbl.lower())
        return out, None
    except Exception as exc:                                         # noqa: BLE001
        return set(), f"{type(exc).__name__}: {str(exc)[:160]}"


def delegation_scope_problems(source_id: str, table: str, who: dict, *,
                              root: Optional[Path] = None) -> list[str]:
    """Why a delegation may NOT touch this table at all (R-022 f2 #2), empty when it may:
      * the delegation record is not sealed to the one delegable scope and a policy digest;
      * the table is outside the connection's DECLARED scope (`tables_include`/`tables_exclude`),
        or there is no declared connection to check it against;
      * the table is a WRITE target an agent writes to (a pack writer spec): mapping agent-written
        rows into the graph is a person's decision, never a delegation's."""
    problems: list[str] = []
    if who.get("scope") != DELEGATION_SCOPE:
        problems.append(f"{table}: the delegation's scope {who.get('scope')!r} is not "
                        f"{DELEGATION_SCOPE!r}")
    if not str(who.get("policy_digest") or "").startswith("sha256:") or \
            who.get("authenticated_by") != f"delegated:{who.get('policy_digest')}":
        problems.append(f"{table}: the delegation is not bound to a policy digest")
    from .connections import load_connection
    from .crawl import Item, _decide_scope

    try:
        conn = load_connection(source_id, root)
    except Exception as exc:                                         # noqa: BLE001
        problems.append(f"{table}: no declared connection {source_id!r} to check the scope "
                        f"against ({type(exc).__name__})")
    else:
        schema, _, name = table.rpartition(".")
        item = Item(source_id=source_id, step_id="delegation", kind="table", uri=table,
                    name=name, rel=table, mime="application/json",
                    ctx={"schema": schema, "table": name})
        d = _decide_scope(item, conn.scope, collected_so_far=0)
        if d.outcome != "collect":
            problems.append(f"{table}: outside the declared scope of {source_id!r} ({d.rule})")
    targets, why = _writer_targets()
    if why is not None:
        problems.append(f"{table}: the pack writer specs cannot be read ({why}), so it cannot "
                        f"be shown not to be a write target")
    elif table.lower() in targets or table.rpartition(".")[2].lower() in targets:
        problems.append(f"{table}: is a WRITE target (a pack writer spec); mapping what agents "
                        f"write is approved by a person, never by delegation")
    return problems


def additive_problems(source_id: str, table: str, new: TableMapping, vocab, *,
                      current: Optional[TableMapping], logged: Optional[dict] = None,
                      root: Optional[Path] = None) -> list[str]:
    """Why ``new`` is NOT a purely additive change of the table's mapping in force (empty: it is).

    Additive means nothing already built can be read differently:
      * a table with no mapping in force (absent or ``unmapped``) may be mapped, unless its kind
        is a SUBJECT kind (subject-touching is always a person's decision, hard constraint 1);
      * a table whose mapping is in force must hold a VERIFIED approval (`approval_problem`), and
        the new node must equal it except for NEW attributes (every existing attribute kept,
        reading the same column), and every existing edge must be kept; new edges may be added.
    A table merely ``proposed`` (held, or never approved) has no approval to extend: refused."""
    problems: list[str] = []
    node = new.node or {}
    kind = node.get("kind")
    try:
        subject = bool(kind) and bool(vocab.is_subject(kind))
    except Exception:                                                # noqa: BLE001
        subject = True                                               # cannot tell: fail closed
    if subject:
        problems.append(f"{table}: maps to subject kind {kind!r}; a subject-touching change is "
                        f"approved by a person, never by delegation")
    if node.get("candidate_identifier") is not None:
        problems.append(f"{table}: names a subject identifier; approved by a person only")
    if current is None or current.status == "unmapped" or current.node is None:
        # R-022 f2 #2: a NEW table may add only its OWN rows. An exact-merge key would fold its
        # rows onto records already in the graph — that re-reads existing nodes, so it is not
        # additive and a person decides it.
        if node.get("canonical_id") is not None:
            problems.append(f"{table}: a new table with an exact-merge key (canonical_id "
                            f"{node['canonical_id']!r}) would merge its rows onto existing "
                            f"records; not additive")
        return problems
    if current.status != "approved":
        return problems + [f"{table}: its mapping is {current.status!r}, not approved; a "
                           f"delegation extends an approval, it never makes one"]
    why = approval_problem(source_id, table, current, vocab, logged=logged, root=root)
    if why:
        return problems + [f"{table}: the mapping in force is not verifiably approved ({why})"]
    old = dict(current.node)
    old_attrs, new_attrs = dict(old.pop("attrs", {}) or {}), dict(node.get("attrs") or {})
    rest = {k: v for k, v in node.items() if k != "attrs"}
    if rest != old:
        changed = sorted(k for k in set(rest) | set(old) if rest.get(k) != old.get(k))
        problems.append(f"{table}: changes node field(s) {changed}; only new attributes are "
                        f"additive")
    for a, col in sorted(old_attrs.items()):
        if new_attrs.get(a) != col:
            problems.append(f"{table}: attribute {a!r} (column {col!r}) is removed or re-pointed")
    kept = {_edge_key(e) for e in new.edges or []}
    for e in current.edges or []:
        if _edge_key(e) not in kept:
            problems.append(f"{table}: removes or changes edge {e.get('kind')!r}")
    # R-022 f2 #2/#9: the schema the approval was bound to may only GROW
    if current.columns is not None:
        gone = sorted(set(current.columns) - set(new.columns or []))
        if gone:
            problems.append(f"{table}: the schema loses column(s) {gone}; not additive")
        gone_fk = sorted(set(current.fk_columns or []) - set(new.fk_columns or []))
        if gone_fk:
            problems.append(f"{table}: the schema loses foreign key(s) {gone_fk}; not additive")
    return problems


def approve_delegated_additive(source_id: str, table: str, entry: dict, *, delegation: Any,
                               vocab=None, root: Optional[Path] = None) -> SourceMapping:
    """Write AND approve one table's mapping under a DELEGATED approval (INT3): an adapter agent's
    ``auto_additive`` policy, delegated by the principal who activated the agent.

    Accepted only when (1) ``delegation`` passes `plans.require_delegated_approver` (a minted
    adapter-agent `DelegatedApproval`, never a name or a user principal) and its record is sealed
    to the one delegable scope and a policy digest, (2) the table is inside the connection's
    declared scope and is not a write target (`delegation_scope_problems`, R-022 f2 #2), (3) the
    entry validates against the active vocabulary and names only columns of its schema snapshot,
    and (4) the change is ADDITIVE against the approved mapping in force (`additive_problems`): a
    subject is never touched, a new table never carries an exact-merge key, and the schema only
    grows. Anything else raises `MappingApprovalRefused` and writes nothing:
    the caller holds the change for a person. The log line records ``authenticated_by:
    "delegated:<policy digest>"`` and ``delegated_by``, so a delegated approval is always
    distinguishable from a person's."""
    from .plans import ApprovalRefused, require_delegated_approver

    try:
        who = require_delegated_approver(delegation, what=f"delegated mapping approval of "
                                                         f"{table!r}")
    except ApprovalRefused as exc:
        raise MappingApprovalRefused(str(exc)) from None
    if vocab is None:
        from databridge_graph.packs import active_vocabulary

        vocab = active_vocabulary()
    raw = {k: v for k, v in dict(entry or {}).items() if k not in ("status", "approval")}
    try:
        new = TableMapping.model_validate({**raw, "status": "proposed"})
    except Exception as exc:                                         # noqa: BLE001
        raise MappingApprovalRefused(f"{table}: the entry is malformed "
                                     f"({str(exc).splitlines()[0][:200]})") from None
    m = load_mapping(source_id, root)
    logged = logged_approvals(source_id, root)
    problems = delegation_scope_problems(source_id, table, who, root=root)
    problems += validate_table_mapping(table, new, vocab, check_references=True)
    problems += additive_problems(source_id, table, new, vocab, current=m.tables.get(table),
                                  logged=logged, root=root)
    if problems:
        raise MappingApprovalRefused(f"delegated approval of {source_id!r}/{table} REFUSED (a "
                                     f"person must approve it): {'; '.join(problems)}")
    at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    record = {**who, "at": at, "digest": mapping_digest(new)}
    _append_approval(source_id, {"action": "approve", "source_id": source_id, "table": table,
                                 **record}, root)
    tables = {**m.tables, table: new.model_copy(update={"status": "approved",
                                                        "approval": record})}
    m = m.model_copy(update={"tables": tables})
    save_mapping(m, root)
    return m


def set_table_mapping(source_id: str, table: str, *, edited_by: str, node: Optional[dict] = None,
                      edges: Optional[list[dict]] = None, vocab=None,
                      root: Optional[Path] = None) -> SourceMapping:
    """A human edit of one table's mapping (origin `manual`, status `proposed` until approved).
    The node kind and every edge kind are validated against the vocabulary — an edit cannot
    smuggle an off-ontology kind past the closed vocabulary."""
    if not edited_by or not edited_by.strip():
        raise ValueError("an edit needs a name (edited_by)")
    if vocab is None:
        from databridge_graph.packs import active_vocabulary

        vocab = active_vocabulary()
    m = load_mapping(source_id, root)
    cur = m.tables.get(table) or TableMapping(status="unmapped")
    new_node = node if node is not None else cur.node
    new_edges = edges if edges is not None else cur.edges
    if new_node is not None and new_node.get("kind") not in vocab.node_kinds:
        raise ValueError(f"kind {new_node.get('kind')!r} is not in the active vocabulary")
    for e in new_edges or []:
        if e.get("kind") not in vocab.edge_kinds:
            raise ValueError(f"edge kind {e.get('kind')!r} is not in the active vocabulary")
    tm = TableMapping(status="proposed" if new_node else "unmapped", origin="manual",
                      node=new_node, edges=list(new_edges or []),
                      rationale=[*cur.rationale, f"edited by {edited_by.strip()}"],
                      columns=cur.columns, fk_columns=cur.fk_columns)
    m = m.model_copy(update={"tables": {**m.tables, table: tm}})
    save_mapping(m, root)
    return m


__all__ = ["SourceMapping", "TableMapping", "propose_rules", "propose_model", "ensure_mapping",
           "approve_mapping", "MappingApprovalRefused", "validate_table_mapping",
           "mapping_digest", "effective_mapping", "approval_problem", "logged_approvals",
           "approval_log_path", "screen_for_model", "NodeSpecModel", "EdgeSpecModel",
           "set_table_mapping", "load_mapping", "save_mapping", "mapping_path",
           "reference_problems", "additive_problems", "approve_delegated_additive",
           "delegation_scope_problems", "DELEGATION_SCOPE",
           "kind_for_table", "resolve_kind", "MAPPING_TASK_CLASS"]

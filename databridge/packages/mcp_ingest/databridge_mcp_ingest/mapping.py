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

from databridge_core.decision_ledger import (DecisionLedger, DecisionRefused, actor_of,
                                             atomic_write_text)

from .build import FK_PREFIX, KIND_KEY, ROW_KEY, _admits
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
    #: a JOIN table (`is_association`): NO node — each row is an edge between the two rows it
    #: references (its `edges` name only foreign-key endpoints)
    association: bool = False
    #: C-3: PROPOSED links — a column no foreign key declares whose values are measured keys of
    #: another table (this source or another). Outside the approval digest: a proposal changes
    #: nothing; `approve_link` turns one into an edge (with `ref`) that the table's approval covers.
    links: list[dict[str, Any]] = Field(default_factory=list)
    #: FIN-04 per-row KIND SPLIT (`propose_split`): {column, kinds: {normalised value: kind},
    #: default}. A row whose discriminator value (normalised by `split_value`) is bound in `kinds`
    #: becomes a node of THAT kind; any other value — unrecognised, NULL, masked — becomes
    #: `default`, which is the node kind and, when the split names a subject kind at all, a
    #: subject (the safe side: never merged). Covered by the approval digest when present.
    split: Optional[dict[str, Any]] = None


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
    #: an APPROVED link (C-3): the table and key the `{walnut_fk_<column>}` endpoint names, in
    #: `source` — a reference no foreign key declares
    ref: Optional[dict[str, Optional[str]]] = None
    #: a join table's edge: its other columns, as edge attributes (attribute name -> column)
    attrs: dict[str, str] = Field(default_factory=dict)


class SplitSpecModel(_Strict):
    """The STRICT shape of a mapping's per-row kind split (FIN-04)."""
    column: str
    kinds: dict[str, str] = Field(default_factory=dict)
    default: str


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
        hints = {w.lower() for w in words or []}
        # FIN-04: an abbreviation of a hint word (`org` for `organisation`) is the same hint — a
        # prefix of three or more letters, never a substring
        if not (forms & hints or any(len(f) >= 3 and w.startswith(f)
                                     for f in forms for w in hints)):
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
        fit, why = _ontology_fit(table, bound, vocab, exclude=meta_name)
        if fit is not None:
            return fit, (f"table name {table!r} is a common name for the {meta_name!r} "
                         f"meta-kind; of the {len(bound)} kinds bound to it, {why}")
        return None, (f"table name {table!r} hints meta-kind {meta_name!r} but {len(bound)} "
                      f"kinds bind to it {list(bound)} — left for the model / a human")
    return None, ""


def _kind_words(vocab, kind: str, exclude: str = "") -> tuple[set[str], set[str]]:
    """(strong, weak) word roots of a kind AS THE PACK DECLARES IT: its name and meta-kind
    (strong), its description (weak). `exclude` drops a strong word every candidate shares (the
    meta-kind they were all found through)."""
    d = vocab.node_kinds[kind]
    meta = getattr(getattr(d, "binds_to", None), "value", "") or ""
    drop = _roots(exclude.replace("_", " ")) if exclude else set()
    strong = _roots(f"{kind} {meta}".replace("_", " ")) - drop
    weak = _roots(str(getattr(d, "description", "") or ""))
    return strong, weak


def _word_hit(root: str, words: set[str]) -> bool:
    return any(w == root or (len(root) >= 3 and w.startswith(root)) for w in words)


def _fit_score(roots: set[str], strong: set[str], weak: set[str]) -> int:
    """How well some words fit a kind's declaration: 2 per word naming the kind or its meta-kind,
    1 per word its description uses (a word may abbreviate: `org` fits `organisation`)."""
    return sum(2 if _word_hit(r, strong) else 1 if _word_hit(r, weak) else 0 for r in roots)


def _ontology_fit(text: str, kinds, vocab, *, exclude: str = ""
                  ) -> tuple[Optional[str], str]:
    """(the ONE kind among `kinds` whose declaration the words of `text` fit best, why) — or
    (None, why) when no word fits or two kinds fit equally. The pack's own wording decides; no
    word list lives here."""
    roots = _roots(text.replace("_", " ").replace(".", " "))
    scores = {k: _fit_score(roots, *_kind_words(vocab, k, exclude)) for k in kinds
              if k in vocab.node_kinds}
    best = max(scores.values(), default=0)
    top = sorted(k for k, s in scores.items() if s == best)
    if best <= 0:
        return None, f"no word of {text!r} fits any of {sorted(scores)}"
    if len(top) > 1:
        return None, f"{top} fit {text!r} equally (score {best})"
    return top[0], (f"{top[0]!r} fits its words best by the pack's declarations (score {best}; "
                    f"{ {k: s for k, s in sorted(scores.items())} })")


# --- FIN-04: a per-row kind split (one table, people AND organisations) -------------------------
_KIND_REF = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")


def split_value(value: Any) -> str:
    """A discriminator value as a split binds it: NFKC, case-folded, every run of punctuation or
    whitespace one space (`ORGANISATION`, `organisation` and ` Organisation ` are one value)."""
    import unicodedata

    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return " ".join(re.findall(r"[^\W_]+", text))


def split_candidates(vocab, kind: str) -> list[str]:
    """The kinds a row of `kind` may be split into: those the pack's declarations cross-reference
    with it (`kind`'s description names them, or theirs names `kind`) — the ontology's own
    "use X for …" pointers. Never a list here."""
    decl = vocab.node_kinds.get(kind)
    if decl is None:
        return []
    own = set(_KIND_REF.findall(str(getattr(decl, "description", "") or "")))
    out = []
    for k, d in vocab.node_kinds.items():
        if k == kind:
            continue
        if k in own or kind in _KIND_REF.findall(str(getattr(d, "description", "") or "")):
            out.append(k)
    return sorted(out)


def _split_policy(policy: dict) -> tuple[int, int]:
    sp = policy.get("kind_split") or {}
    return int(sp.get("max_distinct", 12)), int(sp.get("min_rows_per_value", 2))


def propose_split(snap: dict, kind: str, vocab, policy: Optional[dict] = None
                  ) -> tuple[Optional[dict], list[str]]:
    """(split, rationale): FIN-04 — a SUBJECT table whose rows are of more than one declared kind,
    told apart by a categorical column.

    Only a subject kind is split (`default` stays the subject: any value not bound — unrecognised,
    NULL, masked — is the safe side and is never merged). For each low-cardinality text column
    (not a key, not a foreign key), every distinct NORMALISED value (`split_value`) is fitted to
    the subject and to the kinds the pack cross-references with it (`split_candidates`) by the
    pack's own words (`_fit_score`): a value goes to the ONE kind it fits best; a tie that
    includes the subject, or no fit at all, stays the subject. The column that sends the most rows
    to another kind is proposed; two columns tying is no proposal. A PROPOSAL like any mapping: it
    is covered by the table's approval digest and built only once a person approves it."""
    policy = policy if policy is not None else _policy()
    try:
        if not vocab.is_subject(kind):
            return None, []
    except Exception:                                                # noqa: BLE001
        return None, []
    cands = [k for k in split_candidates(vocab, kind) if k in vocab.node_kinds]
    if not cands:
        return None, []
    max_distinct, min_rows = _split_policy(policy)
    pk = set(snap.get("primary_key") or [])
    fk_cols = {f.get("column") for f in snap.get("foreign_keys") or []}
    types = {c.get("name"): str(c.get("type") or "") for c in snap.get("columns") or []}
    rows = snap.get("rows") or []
    words = {k: _kind_words(vocab, k) for k in [kind, *cands]}
    best: list[tuple[int, str, dict, dict]] = []
    for col in _columns(snap):
        if col in pk or col in fk_cols or _NUMERIC_TYPE.search(types.get(col, "")):
            continue
        counts: dict[str, int] = {}
        for r in rows:
            v = r.get(col)
            if v is None or not str(v).strip():
                continue
            counts[split_value(v)] = counts.get(split_value(v), 0) + 1
        if not 2 <= len(counts) <= max_distinct or sum(counts.values()) < min_rows * len(counts):
            continue
        bound: dict[str, str] = {}
        basis: dict[str, str] = {}
        for value in sorted(counts):
            roots = _roots(value)
            scores = {k: _fit_score(roots, *words[k]) for k in words}
            top = max(scores.values())
            winners = sorted(k for k, s in scores.items() if s == top)
            if top <= 0:
                basis[value] = f"fits no declared kind; stays {kind!r}"
                continue
            if len(winners) == 1:
                bound[value] = winners[0]
                basis[value] = f"-> {winners[0]!r} (fit {scores})"
            elif kind in winners:
                bound[value] = kind
                basis[value] = f"-> {kind!r}: ties {winners}, the subject is the safe side"
            else:
                basis[value] = f"ties {winners}; stays {kind!r} (the safe side)"
        moved = sum(n for v, n in counts.items() if bound.get(v, kind) != kind)
        if moved:
            best.append((moved, col, bound, {v: f"{counts[v]} row(s) {basis[v]}"
                                             for v in sorted(counts)}))
    if not best:
        return None, []
    best.sort(key=lambda b: (-b[0], b[1]))
    if len(best) > 1 and best[1][0] == best[0][0]:
        return None, [f"kind split not proposed: columns {best[0][1]!r} and {best[1][1]!r} "
                      f"would each move {best[0][0]} row(s) to another kind; a person chooses"]
    moved, col, bound, basis = best[0]
    split = {"column": col, "kinds": bound, "default": kind}
    note = (f"kind split on {col!r} (PROPOSED, FIN-04): {moved} row(s) are another declared kind "
            f"by the pack's own words; any other value (unrecognised, NULL, masked) stays "
            f"{kind!r}, a subject, never merged — {basis}")
    return split, [note]


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


def fk_hint_roots(column: str, *names: str) -> set[str]:
    """The word roots of a key column that can say WHICH relationship it is (FIN-05).

    A key named only after what it references (`<kind>_id` → a row of that kind, `<table>_id` →
    a row of that table) says THAT it references, never in which role: the words of the referenced
    kind, the row's own kind and the referenced table are removed. What is left (`owner` in
    `owner_rm_code`) is evidence for a declaration."""
    stem = column.lower().removesuffix("_id")
    roots = _roots(stem.replace("_", " "))
    noise: set[str] = set()
    for n in names:
        if n:
            last = str(n).rsplit(".", 1)[-1]
            noise |= _roots(last.replace("_", " "))
            for f in _forms(last):
                noise |= _roots(f.replace("_", " "))
    return {r for r in roots if not any(r.startswith(x) or x.startswith(r) for x in noise)}


def _fk_edge(vocab, kind: str, ref_kind: str, hint: str, policy: dict, *,
             hint_roots: Optional[set[str]] = None) -> tuple[Optional[str], bool, str]:
    """(edge_kind, reversed, why).

    Every candidate (every meta-kind in `fk_edge_metas`, both directions, explicit or through a
    wildcard endpoint) is ranked in ONE list (FIN-05: returning the first meta-kind with any
    candidate let a role edge win before the key's words or direction were looked at):

    1. first, a declaration naming BOTH kinds in the key's own direction (row -> referenced), or
       in reverse when the key's own words match it (`fk_hint_roots`: `owner` in `owner_rm_code`
       ~ `manages` "owns"; the referenced kind's own name never counts — a key named
       `<kind>_id` says THAT it references that kind, not in which role) — by those words, then
       direction, then the policy's meta-kind order;
    2. otherwise everything else — an explicit declaration read in reverse that the key does not
       name, or one admitting the kinds through a wildcard — by the meta-kind's place in
       `fk_edge_metas` (participation and containment before roles: a role read in reverse is
       never assumed from a key that does not name it), explicit before wildcard;
    then the most specific declaration (fewest endpoint kinds), then the name.
    """
    from databridge_graph.meta import EdgeMetaKind

    metas = tuple(policy.get("fk_edge_metas") or ("part_of", "references"))
    if hint_roots is None:
        hint_roots = _roots(hint.replace("_", " "))

    ranked = []
    for direction, (src, dst) in enumerate(((kind, ref_kind), (ref_kind, kind))):
        for order, meta in enumerate(metas):
            try:
                names = vocab.edges_binding_to(EdgeMetaKind(meta))
            except ValueError:
                continue
            for name in names:
                decl = vocab.edge(name)
                if not (_admits(decl, "src", src) and _admits(decl, "dst", dst)):
                    continue
                explicit = _explicit(decl, "src", src) and _explicit(decl, "dst", dst)
                h = _hint_score(hint_roots, name, decl)
                spec = len(tuple(decl.src or ())) + len(tuple(decl.dst or ()))
                # tier 0: a declaration naming both kinds in the key's own direction, or in
                # reverse when the key's words name it; tier 1: everything else, by meta order
                first = explicit and (direction == 0 or h > 0)
                key = ((0, -h, direction, order) if first else (1, 0, 0, order))
                ranked.append((*key, 0 if explicit else 1, direction, spec, name, h, explicit))
    ranked = sorted(set(ranked))
    if not ranked:
        return None, False, (f"no declared edge kind admits {kind} -> {ref_kind} in either "
                             f"direction")
    top = ranked[0]
    order, direction, name, hscore, explicit = top[3], top[5], top[7], top[8], top[9]
    h = -hscore
    how = "declares" if explicit else "admits (through a wildcard endpoint)"
    ranked = [(c[8], c[5], c[3], c[6], c[7]) for c in ranked]
    rev = direction == 1
    src, dst = (ref_kind, kind) if rev else (kind, ref_kind)
    why = (" (column words match its declaration)" if h < 0 else
           f" (no column word names a role; meta-kind {metas[order]!r} is ranked by "
           f"fk_edge_metas)")
    if rev:
        why += f" (read in reverse: the key is on the {kind} row)"
    others = [f"{c[4]}{' (reverse)' if c[1] else ''}" for c in ranked[1:]]
    if others:
        why += f"; other candidates: {others} — choose one on review if the key means that"
    return name, rev, f"{name!r} {how} {src} -> {dst}{why}"


#: The rationale prefix of a declared FK whose referenced table had no kind when it was proposed:
#: the edge is re-resolved once every table's kind is known (`_resolve_pending_fks`, FIN-06).
FK_NO_KIND = "referenced table has no kind"


def fk_edges(snap: dict, kind: str, kinds: dict, vocab, policy: Optional[dict] = None
             ) -> tuple[list[dict], list[str]]:
    """(edges, rationale lines): one edge per DECLARED foreign key of the table, by `_fk_edge`.
    A key whose referenced table has no kind (yet), or whose kinds no declaration admits, gets NO
    edge and a rationale line saying so — it is counted per run (`build.completeness`), never
    silently dropped."""
    policy = policy if policy is not None else _policy()
    edges: list[dict] = []
    notes: list[str] = []
    for fk in snap.get("foreign_keys") or []:
        col = fk.get("column")
        if not col:
            continue
        ref = _table_ref(fk.get("ref_schema"), fk.get("ref_table") or "")
        ref_kind = kinds.get(ref) or kind_for_table(fk.get("ref_table") or "", vocab)
        if ref_kind is None:
            notes.append(f"FK {col} -> {ref}: {FK_NO_KIND}; no edge")
            continue
        roots = fk_hint_roots(col, kind, ref_kind, fk.get("ref_table") or "")
        ek, rev, ewhy = _fk_edge(vocab, kind, ref_kind, col, policy, hint_roots=roots)
        if ek is None:
            notes.append(f"FK {col} -> {ref}: {ewhy}; no edge")
            continue
        row_end = f"{kind}:{{{ROW_KEY}}}"
        ref_end = f"{ref_kind}:{{{FK_PREFIX}{col}}}"
        edges.append({"kind": ek, "src": ref_end if rev else row_end,
                      "dst": row_end if rev else ref_end,
                      "evidence": [col], "fk": f"{col} -> {ref}", "reversed": rev})
        notes.append(f"FK {col} -> {ref}: edge {ewhy} — review")
    return edges, notes


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
    # C-1 (2026-10-02 dry run): a foreign-key column is ALSO kept as an attribute, so the reference
    # value is on the node even where no edge could be built (never silently dropped)
    for c in columns:
        if c in fk_cols and c not in pk and c.lower() not in _IDENTITY_FIELDS:
            attrs[c] = c
    edges, fk_notes = fk_edges(snap, kind, kinds, vocab, policy)
    rationale.extend(fk_notes)
    node = {"kind": kind, "label": label, "sources": pk or [ROW_KEY], "attrs": attrs}
    if canonical:
        node["canonical_id"] = canonical
    if candidate:
        node["candidate_identifier"] = candidate
    split, split_notes = propose_split(snap, kind, vocab, policy)
    rationale.extend(split_notes)
    return TableMapping(status="proposed", origin=origin, node=node, edges=edges,
                        rationale=rationale, columns=columns, fk_columns=sorted(fk_cols),
                        split=split)


def is_association(snap: dict) -> bool:
    """A JOIN (association) table, by STRUCTURE only — never by its name: two or more declared
    foreign keys, and a primary key made of foreign-key columns only. Its rows say "this row of A
    is related to that row of B"; they become EDGES between the referenced rows, not nodes (the
    same rule as the completeness audit, `scripts/e2e/completeness.py`)."""
    fk_cols = {f.get("column") for f in snap.get("foreign_keys") or []
               if f.get("column") and f.get("ref_table")}
    pk = set(snap.get("primary_key") or [])
    return len(fk_cols) >= 2 and bool(pk) and pk <= fk_cols


def association_mapping(tref: str, snap: dict, kinds: dict, vocab, *, origin: str = "rules",
                        policy: Optional[dict] = None) -> TableMapping:
    """A join table's mapping (MAT6 / FIN completeness): NO node; one edge per row between the two
    rows it references — the key's foreign keys, in key order — of the declared edge kind
    `_fk_edge` ranks first for the two kinds (the table's own name words as the hint; never the
    kinds' names). Every other column of the row becomes an attribute of the edge, and the edge's
    evidence is the join row's own two key cells. Unmapped, with the reason, when a referenced
    table has no kind yet or no declaration admits the pair."""
    policy = policy if policy is not None else _policy()
    columns = _columns(snap)
    pk = list(snap.get("primary_key") or [])
    fks = {f["column"]: f for f in snap.get("foreign_keys") or []
           if f.get("column") and f.get("ref_table")}
    pair = [fks[c] for c in pk if c in fks][:2]
    if len(pair) < 2:
        pair = [fks[c] for c in columns if c in fks][:2]
    rationale = [f"association (join) table: its key {pk} is made of foreign keys only, so each "
                 f"row is an EDGE between the rows it references, not a node"]
    ends = []
    for fk in pair:
        ref = _table_ref(fk.get("ref_schema"), fk.get("ref_table") or "")
        k = kinds.get(ref) or kind_for_table(fk.get("ref_table") or "", vocab)
        if k is None:
            return TableMapping(status="unmapped", origin=origin, rationale=[
                *rationale, f"FK {fk['column']} -> {ref}: {FK_NO_KIND}; the join waits for it"],
                columns=columns, fk_columns=sorted(fks), association=True)
        ends.append((fk["column"], ref, k))
    (ca, ra, ka), (cb, rb, kb) = ends
    table = str(snap.get("table") or tref)
    roots = fk_hint_roots(table, ka, kb, ra, rb)
    ek, rev, why = _fk_edge(vocab, ka, kb, table, policy, hint_roots=roots)
    if ek is None:
        return TableMapping(status="unmapped", origin=origin,
                            rationale=[*rationale, f"{why}; no edge"], columns=columns,
                            fk_columns=sorted(fks), association=True)
    a_end, b_end = f"{ka}:{{{FK_PREFIX}{ca}}}", f"{kb}:{{{FK_PREFIX}{cb}}}"
    attrs = {c: c for c in columns if c not in (ca, cb) and c.lower() not in _IDENTITY_FIELDS}
    edge = {"kind": ek, "src": b_end if rev else a_end, "dst": a_end if rev else b_end,
            "evidence": [ca, cb], "fk": f"{ca} -> {ra}; {cb} -> {rb}", "reversed": rev}
    if attrs:
        edge["attrs"] = attrs
    rationale.append(f"each row: edge {why} — review")
    return TableMapping(status="proposed", origin=origin, node=None, edges=[edge],
                        rationale=rationale, columns=columns, fk_columns=sorted(fks),
                        association=True)


def propose_rules(snapshots: dict[str, dict], vocab,
                  context: Optional[dict[str, dict]] = None,
                  known_kinds: Optional[dict[str, Optional[str]]] = None
                  ) -> dict[str, TableMapping]:
    """`context`: every table captured from the source (default: `snapshots`), so a table first
    seen on a later sync still knows the other tables' keys. `known_kinds`: the kinds the
    mapping already gives OTHER tables, so a foreign key (or a join) into one of them resolves.

    A JOIN table (`is_association`, by structure) is mapped to edges between the rows it
    references (`association_mapping`), never to a node of its own."""
    policy = _policy()
    ctx = context if context is not None else snapshots
    assoc = {t for t, s in snapshots.items() if is_association(s)}
    resolved = {tref: resolve_kind(s.get("table") or tref, s, vocab, policy)
                for tref, s in snapshots.items() if tref not in assoc}
    kinds = {**{t: k for t, k in (known_kinds or {}).items() if k},
             **{tref: (None if (snapshots[tref].get("history_snapshot") or {}).get("is") else k)
                for tref, (k, _) in resolved.items()}}
    out: dict[str, TableMapping] = {}
    for tref, snap in snapshots.items():
        if tref in assoc:
            out[tref] = association_mapping(tref, snap, kinds, vocab, policy=policy)
            continue
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


def mapping_lock(source_id: str, root: Optional[Path] = None, *,
                 timeout: Optional[float] = None):
    """THE lock every writer of ``source_id``'s mapping takes (R-028 #5): cross-process and
    re-entrant within a thread (`plans.file_lock`, the ONE implementation in
    `databridge_core.decision_ledger`). Every read -> verify -> log -> save of a mapping runs under
    it, so two approvals or a proposal refresh can never overwrite one another, and the approval
    log never disagrees with the mapping it approved.

    Global lock order (R-028 standard 8): source -> ingest run -> plan -> MAPPING -> design ->
    ledger append. Never take the plan lock while holding this one."""
    from .plans import file_lock

    p = mapping_path(source_id, root)
    return file_lock(p.with_name(f"{p.name}.lock"), timeout=timeout)


def save_mapping(m: SourceMapping, root: Optional[Path] = None) -> Path:
    """Write the mapping whole or not at all (a same-directory temporary file, fsynced, then
    `os.replace`), under `mapping_lock`: a reader never sees half a mapping (R-028 #5)."""
    p = mapping_path(m.source_id, root)
    m = m.model_copy(update={"updated_at": dt.datetime.now(dt.timezone.utc)
                             .isoformat(timespec="seconds")})
    text = yaml.safe_dump(m.model_dump(mode="json"), sort_keys=False)
    with mapping_lock(m.source_id, root):
        atomic_write_text(p, text)
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
    with mapping_lock(source_id, root):
        return _ensure_mapping_locked(source_id, snapshots, vocab, harness=harness,
                                      auto_approve=auto_approve, root=root)


def _ensure_mapping_locked(source_id: str, snapshots: dict[str, dict], vocab, *, harness=None,
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
    m = _with_pending_fks(m, snapshots, vocab, root)
    new = {t: s for t, s in snapshots.items() if t not in m.tables}
    retry = {t: s for t, s in snapshots.items()
             if t in m.tables and m.tables[t].status == "unmapped"
             and m.tables[t].origin != "manual"
             and not (s.get("history_snapshot") or {}).get("is")}
    if not new and not retry:
        return m
    proposed: dict[str, TableMapping] = {}
    known_kinds = {t: (tm.node or {}).get("kind") for t, tm in m.tables.items()
                   if tm.status != "unmapped"}
    if new:
        proposed = propose_rules(new, vocab, context=snapshots, known_kinds=known_kinds)
        # a join table is never offered to the model as a node: it waits for its targets' kinds
        unmapped = [t for t, tm in proposed.items() if tm.status == "unmapped"
                    and not tm.association]
        proposed.update(propose_model(new, unmapped, {**m.tables, **proposed}, vocab, harness,
                                      context=snapshots))
    if retry:
        again = propose_rules(retry, vocab, context=snapshots, known_kinds=known_kinds)
        proposed.update({t: tm for t, tm in again.items() if tm.status == "proposed"})
        still = {t: m.tables[t] for t in retry if t not in proposed}
    else:
        still = {}
    known = {**m.tables, **proposed}
    fallback = _fallback_proposals(snapshots, {**{t: tm for t, tm in proposed.items()
                                                  if tm.status == "unmapped"
                                                  and not tm.association},
                                               **{t: tm for t, tm in still.items()
                                                  if not is_association(snapshots[t])}},
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
    # FIN-06: a table proposed by the rules BEFORE the model gave the table it references a kind
    # gets its edge now that every kind is known
    return _with_pending_fks(m, snapshots, vocab, root)


def _with_pending_fks(m: SourceMapping, snapshots: dict[str, dict], vocab,
                      root: Optional[Path]) -> SourceMapping:
    got = resolve_pending_fks(m.tables, snapshots, vocab)
    got.update(resolve_pending_splits({**m.tables, **got}, snapshots, vocab))
    if not got:
        return m
    m = m.model_copy(update={"tables": {**m.tables, **got}})
    save_mapping(m, root)
    return m


def resolve_pending_fks(tables: dict[str, TableMapping], snapshots: dict[str, dict], vocab,
                        policy: Optional[dict] = None) -> dict[str, TableMapping]:
    """FIN-06 / C-1: every DECLARED foreign key becomes an edge once the table it references has
    a kind — whatever order the rules, the model and the fallback mapped the tables in.

    For each rules/model entry (never a `manual` one: a person's edit is a decision) whose
    declared FK has no edge, the edge is proposed again against the kinds of EVERY table now
    mapped. A `proposed` entry simply gains the edge (and the FK attribute). An `approved` entry
    is RE-PROPOSED with it — its approval was for the mapping without the edge, so a person
    approves the change (the rationale says what changed); nothing is approved on anyone's
    behalf. Returns only the entries that changed."""
    policy = policy if policy is not None else _policy()
    kinds = {t: (tm.node or {}).get("kind") for t, tm in tables.items()
             if tm.status != "unmapped" and tm.node}
    out: dict[str, TableMapping] = {}
    for t, tm in tables.items():
        snap = snapshots.get(t)
        if (tm.status == "unmapped" and tm.origin != "manual" and snap is not None
                and is_association(snap)):
            # a join table waiting for the kinds of the rows it joins
            got = association_mapping(t, snap, kinds, vocab, policy=policy)
            if got.status == "proposed":
                out[t] = got
            continue
        if tm.node is None or tm.status == "unmapped" or tm.origin == "manual":
            continue
        if snap is None:
            continue
        have = {c for e in tm.edges or [] for c in (e or {}).get("evidence") or []}
        declared = [f.get("column") for f in snap.get("foreign_keys") or [] if f.get("column")]
        missing = [c for c in declared if c not in have]
        if not missing:
            continue
        found, notes = fk_edges(snap, tm.node["kind"], kinds, vocab, policy)
        add = [e for e in found if e["evidence"][0] in missing]
        if not add:
            continue
        cols = {e["evidence"][0] for e in add}
        node = dict(tm.node)
        attrs = dict(node.get("attrs") or {})
        pk = set(snap.get("primary_key") or [])
        for c in sorted(cols):
            if c not in pk and c.lower() not in _IDENTITY_FIELDS:
                attrs.setdefault(c, c)
        node["attrs"] = attrs
        kept = [r for r in tm.rationale
                if not any(r.startswith(f"FK {c} -> ") for c in cols)]
        added = [n for n in notes if any(n.startswith(f"FK {c} -> ") for c in cols)]
        update: dict[str, Any] = {"node": node, "edges": [*(tm.edges or []), *add],
                                  "rationale": [*kept, *added]}
        if tm.status == "approved":
            update.update(status="proposed", approval=None)
            update["rationale"] = [*update["rationale"], (
                f"re-proposed: declared foreign key(s) {sorted(cols)} now have an edge (the "
                f"referenced table was given a kind after this table was mapped) — approve it "
                f"again")]
        out[t] = tm.model_copy(update=update)
    return out


def resolve_pending_splits(tables: dict[str, TableMapping], snapshots: dict[str, dict], vocab,
                           policy: Optional[dict] = None) -> dict[str, TableMapping]:
    """FIN-04: a rules/model entry of a SUBJECT kind, mapped before kind splits existed, whose rows
    `propose_split` now finds to be of more than one declared kind, gains the split. A `proposed`
    entry simply gains it; an `approved` one is RE-PROPOSED with it (its approval covered the
    table without the split, so a person approves the change — nothing is approved on anyone's
    behalf). A `manual` entry is a person's decision and is never touched. Returns only the
    entries that changed."""
    policy = policy if policy is not None else _policy()
    out: dict[str, TableMapping] = {}
    for t, tm in tables.items():
        snap = snapshots.get(t)
        if snap is None or tm.node is None or tm.split is not None or tm.status == "unmapped" \
                or tm.origin == "manual" or tm.association:
            continue
        split, notes = propose_split(snap, tm.node.get("kind") or "", vocab, policy)
        if split is None:
            continue
        update: dict[str, Any] = {"split": split, "rationale": [*tm.rationale, *notes]}
        if tm.status == "approved":
            update.update(status="proposed", approval=None)
            update["rationale"] = [*update["rationale"], (
                f"re-proposed: the rows of this {tm.node.get('kind')!r} table are of more than "
                f"one declared kind (kind split on {split['column']!r}) — approve it again")]
        out[t] = tm.model_copy(update=update)
    return out


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
    """An approval without an authenticated principal, or of a mapping that does not validate.
    ``code`` is the R-028 standard refusal code when one applies (else ``None``)."""

    def __init__(self, message: str = "", code: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code


_ENDPOINT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def mapping_digest(tm: TableMapping) -> str:
    """sha256 of what an approval covers: the table's node and edges, canonical JSON — and the
    schema snapshot (`columns`, `fk_columns`) when the mapping carries one (R-022 f2 #9), so an
    approval is bound to the schema it was checked against."""
    data: dict[str, Any] = {"node": tm.node, "edges": tm.edges}
    if tm.columns is not None:
        data["columns"] = sorted(tm.columns)
        data["fk_columns"] = sorted(tm.fk_columns or [])
    if tm.split is not None:
        # FIN-04: which rows become which kind is part of what a person approves (absent, the
        # digest is byte-identical to before, so no existing approval changes)
        data["split"] = tm.split
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
    # an approved link (C-3) reads its column like a foreign key, through the edge's `ref`
    fk_have |= {c for e in tm.edges or [] if (e or {}).get("ref")
                for c in (e or {}).get("evidence") or []}
    node = tm.node or {}
    named: list[tuple[str, str]] = [("label", f) for f in _TEMPLATE_FIELD.findall(
        str(node.get("label") or ""))]
    named += [("sources", c) for c in node.get("sources") or []]
    named += [(f"attr {a!r}", c) for a, c in (node.get("attrs") or {}).items()]
    for key in ("canonical_id", "candidate_identifier"):
        if node.get(key) is not None:
            named.append((key, node[key]))
    if isinstance(tm.split, dict) and tm.split.get("column") is not None:
        named.append(("kind split", tm.split["column"]))
    problems = [f"{table}: {where} names column {col!r}, which the table does not have"
                for where, col in named if str(col) not in have]
    for i, e in enumerate(tm.edges or []):
        for a, col in ((e or {}).get("attrs") or {}).items():
            if str(col) not in have:
                problems.append(f"{table}: edge {i} attr {a!r} names column {col!r}, which the "
                                f"table does not have")
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
    if tm.node is None and tm.association:
        return _validate_association(table, tm, vocab, check_references=check_references)
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
    if tm.split is not None:
        problems += split_problems(table, tm, vocab)
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
        if e.ref is not None:
            if not (e.ref.get("source") and e.ref.get("table")) or not e.evidence:
                problems.append(f"{table}: edge {i} is a link whose ref names no source/table "
                                f"or whose evidence names no column")
            other = e.dst if e.src == own else e.src
            okind = other.split(":", 1)[0]
            if is_subject_kind(vocab, okind):
                problems.append(f"{table}: edge {i} is a link to subject kind {okind!r}; an "
                                f"inferred link never points at a subject or a person (hard "
                                f"constraint 1)")
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


def split_problems(table: str, tm: TableMapping, vocab) -> list[str]:
    """A kind split's problems (FIN-04): its shape; `default` is the node kind; every bound kind
    is in the vocabulary; every bound value is in normalised form (`split_value`), so a hand edit
    cannot bind a spelling no row is ever looked up by; and — HARD CONSTRAINT 1 — when the split
    names a subject kind anywhere, the default (every unrecognised, NULL or masked value) is a
    subject: a row nobody recognised is never put on the side that merges."""
    if tm.association or tm.node is None:
        return [f"{table}: a kind split needs a node (a join table has none)"]
    try:
        sp = SplitSpecModel.model_validate(tm.split)
    except Exception as exc:                                         # noqa: BLE001
        return [f"{table}: kind split is malformed ({str(exc).splitlines()[0][:200]})"]
    problems: list[str] = []
    kind = (tm.node or {}).get("kind")
    if sp.default != kind:
        problems.append(f"{table}: kind split default {sp.default!r} is not the node kind "
                        f"{kind!r}")
    for value, k in sorted(sp.kinds.items()):
        if k not in vocab.node_kinds:
            problems.append(f"{table}: kind split binds {value!r} to {k!r}, which is not in the "
                            f"active vocabulary")
        if split_value(value) != value:
            problems.append(f"{table}: kind split value {value!r} is not normalised "
                            f"({split_value(value)!r})")

    def subject(k: str) -> bool:
        try:
            return bool(vocab.is_subject(k))
        except Exception:                                            # noqa: BLE001
            return True                                              # cannot tell: fail closed

    if any(subject(k) for k in {sp.default, *sp.kinds.values()}) and not subject(sp.default):
        problems.append(f"{table}: kind split names a subject kind but its default "
                        f"{sp.default!r} is not one: an unrecognised row would leave the "
                        f"protected side (hard constraint 1)")
    return problems


def split_kinds(tm) -> list[str]:
    """Every kind a mapping's rows may become: the node kind, plus each kind its split binds."""
    node = (tm.get("node") if isinstance(tm, dict) else tm.node) or {}
    split = (tm.get("split") if isinstance(tm, dict) else tm.split) or {}
    base = node.get("kind")
    out = [base] if base else []
    for k in sorted(set((split.get("kinds") or {}).values())):
        if k not in out:
            out.append(k)
    return out


def _validate_association(table: str, tm: TableMapping, vocab, *,
                          check_references: bool = False) -> list[str]:
    """A join table's mapping: no node, at least one edge, and every edge a declared kind whose
    BOTH endpoints are foreign-key columns of the table, admitted by its declaration."""
    problems: list[str] = []
    if not tm.edges:
        problems.append(f"{table}: an association mapping with no edge")
    if tm.split is not None:
        problems.append(f"{table}: a join table has no node, so it cannot carry a kind split")
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
            if m is None or not m.group(2).startswith(FK_PREFIX):
                problems.append(f"{table}: edge {i} {side} {text!r} is not "
                                f"'<kind>:{{{FK_PREFIX}<column>}}' (a join row's edge joins the "
                                f"rows it references)")
                continue
            if m.group(1) not in vocab.node_kinds:
                problems.append(f"{table}: edge {i} {side} kind {m.group(1)!r} is not in the "
                                f"vocabulary")
            ends[side] = m.group(1)
        if e.ref is not None:
            problems.append(f"{table}: edge {i}: a join table's edge cannot be a link")
        decl = vocab.edge(e.kind) if e.kind in vocab.edge_kinds else None
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
_LOG_ONLY = ("action", "source_id", "table", "record_digest", "seq", "prev", "hash", "seal")


def mapping_ledger(source_id: str, root: Optional[Path] = None) -> DecisionLedger:
    """The mapping approval log as an R-028 decision ledger (`databridge_core.decision_ledger`):
    sealed under the deployment approval key, hash-chained, anchored, verified before every
    append and read. A log written before R-028 (unsealed) honours nothing and is superseded by
    the next approval."""
    return DecisionLedger(approval_log_path(source_id, root), what="mapping approval log")


def _append_approval(source_id: str, rec: dict, root: Optional[Path]) -> dict:
    """Append one decision through the decision ledger. Each line also keeps its own
    `record_digest`. A log that does not verify refuses (`MappingApprovalRefused`,
    `LEDGER_INTEGRITY_FAILED`) and nothing is written."""
    rec = {**rec, "record_digest": _record_digest(rec)}
    try:
        return mapping_ledger(source_id, root).append(rec)
    except DecisionRefused as exc:
        raise MappingApprovalRefused(exc.message, code=exc.code) from None


def _canon_record(rec: dict) -> str:
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), default=str)


def logged_approvals(source_id: str, root: Optional[Path] = None) -> dict[str, dict[str, list]]:
    """{table: {digest: [the approval record as logged, ...]}} of approvals in the append-only,
    hash-chained log (`plans.read_chained`). A log whose chain does not verify — a line edited,
    removed or inserted — honours NOTHING (fail closed); a legacy line written before the chain
    existed is never evidence; a line whose own digest, source or principal does not verify is
    skipped. Membership (`digest in out[table]`) is what it always was; the records let a caller
    compare the WHOLE approval, not only its digest."""
    out: dict[str, dict[str, list]] = {}
    try:
        lines = mapping_ledger(source_id, root).read()
    except DecisionRefused:
        return out
    for rec in lines:
        if rec.get("action") != "approve":
            continue                                   # a link decision is not a table approval
        body = {k: v for k, v in rec.items() if k not in ("seq", "prev", "hash", "seal")}
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
    with mapping_lock(source_id, root):
        return _approve_mapping_locked(source_id, approved_by=approved_by, principal=principal,
                                       tables=tables, vocab=vocab, root=root)


def _approve_mapping_locked(source_id: str, *, approved_by: Any = None, principal: Any = None,
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
        raise MappingApprovalRefused(str(exc), code=exc.code) from None
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
    if new.split != (current.split if current is not None else None):
        # FIN-04: which rows leave (or join) a subject kind is a person's decision
        problems.append(f"{table}: adds, changes or removes a kind split; approved by a person "
                        f"only")
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
    with mapping_lock(source_id, root):
        return _approve_delegated_additive_locked(source_id, table, entry, delegation=delegation,
                                                  vocab=vocab, root=root)


def _approve_delegated_additive_locked(source_id: str, table: str, entry: dict, *, delegation: Any,
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
        raise MappingApprovalRefused(str(exc), code=exc.code) from None
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
    record = {**who, **actor_of(who), "at": at, "digest": mapping_digest(new)}
    _append_approval(source_id, {"action": "approve", "source_id": source_id, "table": table,
                                 **record}, root)
    tables = {**m.tables, table: new.model_copy(update={"status": "approved",
                                                        "approval": record})}
    m = m.model_copy(update={"tables": tables})
    save_mapping(m, root)
    return m


def set_table_mapping(source_id: str, table: str, *, edited_by: str, node: Optional[dict] = None,
                      edges: Optional[list[dict]] = None, vocab=None,
                      root: Optional[Path] = None,
                      split: Any = ...) -> SourceMapping:
    with mapping_lock(source_id, root):
        return _set_table_mapping_locked(source_id, table, edited_by=edited_by, node=node,
                                         edges=edges, vocab=vocab, root=root, split=split)


def _set_table_mapping_locked(source_id: str, table: str, *, edited_by: str, node: Optional[dict] = None,
                              edges: Optional[list[dict]] = None, vocab=None,
                              root: Optional[Path] = None,
                              split: Any = ...) -> SourceMapping:
    """A human edit of one table's mapping (origin `manual`, status `proposed` until approved).
    The node kind and every edge kind are validated against the vocabulary — an edit cannot
    smuggle an off-ontology kind past the closed vocabulary. `split` (FIN-04) replaces the
    per-row kind split (None removes it; omitted, it is kept) and is validated like the rest."""
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
    new_split = cur.split if split is ... else split
    tm = TableMapping(status="proposed" if new_node else "unmapped", origin="manual",
                      node=new_node, edges=list(new_edges or []),
                      rationale=[*cur.rationale, f"edited by {edited_by.strip()}"],
                      columns=cur.columns, fk_columns=cur.fk_columns, links=list(cur.links),
                      split=new_split if new_node else None)
    if tm.split is not None:
        bad = split_problems(table, tm, vocab)
        if bad:
            raise ValueError("; ".join(bad))
    m = m.model_copy(update={"tables": {**m.tables, table: tm}})
    save_mapping(m, root)
    return m


# --- C-3: proposed links where no foreign key is declared --------------------------------------------
def is_subject_kind(vocab, kind: Optional[str]) -> bool:
    """THE subject predicate for linking, matching and withholding (R-028 standard 7):
    `vocab.is_subject(kind)`. Persons are a subset of subjects. An inferred link never points at a
    subject record, and no subject is ever matched except as a per-record, human-selected
    candidate. A vocabulary that cannot tell fails CLOSED (a subject)."""
    if not kind:
        return False
    try:
        return bool(vocab.is_subject(str(kind)))
    except Exception:                                                # noqa: BLE001
        return True                                                  # cannot tell: fail closed


def is_person_kind(vocab, kind: Optional[str]) -> bool:
    """A kind whose records stand for PEOPLE: never the target of an inferred link and never merged
    (hard constraint 1). The pack says which: a vocabulary exposing `is_person(kind)` (the
    organisation/person split, FIN-04) is asked; otherwise EVERY subject kind counts as one —
    the conservative reading until the pack draws the line.

    R-028: this is for DISPLAY (which subjects are people). Linking, matching and withholding use
    `is_subject_kind`, of which persons are a subset."""
    if not kind:
        return False
    probe = getattr(vocab, "is_person", None)
    try:
        if callable(probe):
            return bool(probe(str(kind)))
        return bool(vocab.is_subject(str(kind)))
    except Exception:                                                # noqa: BLE001
        return True                                                  # cannot tell: fail closed


_NUMERIC_TYPE = re.compile(r"int|serial|numeric|decimal|real|double|float|number|money", re.I)


def _key_values(snap: dict) -> Optional[tuple[str, set[str]]]:
    """(key column, its values) of a table a link may point INTO: a single primary key, not
    numeric (a surrogate 1, 2, 3 overlaps every other surrogate by accident), whose values read
    are not all digits. None otherwise."""
    pk = list(snap.get("primary_key") or [])
    if len(pk) != 1:
        return None
    types = {c.get("name"): str(c.get("type") or "") for c in snap.get("columns") or []}
    if _NUMERIC_TYPE.search(types.get(pk[0], "")):
        return None
    vals = {str(r.get(pk[0])) for r in snap.get("rows") or [] if r.get(pk[0]) is not None}
    if not vals or all(v.strip().isdigit() for v in vals):
        return None
    return pk[0], vals


def propose_links(source_id: str, snapshots: dict[str, dict], tables: dict[str, TableMapping],
                  vocab, *, others: Optional[dict[str, dict[str, tuple[dict, str]]]] = None,
                  policy: Optional[dict] = None) -> dict[str, list[dict]]:
    """{table: [link proposal, ...]} — C-3: a column no foreign key declares whose values are,
    by MEASUREMENT, keys of another table (in this source, or — `others` {source: {table:
    (snapshot, kind)}} — in another source): a reference column holding the other system's key.

    A proposal names the column, the table and key it points into, how many of the column's
    values matched (`matched` of `with_value`), and the declared edge kind it would become
    (`_fk_edge`, the same ranking a declared key gets) — or why none can. It is a PROPOSAL: it
    changes nothing until a person approves it (`approve_link`). It never points at a SUBJECT
    kind (`is_subject_kind`, R-028; persons are subjects) and it never merges anything: two rows describing one organisation
    stay two nodes joined by an edge (a merge on a declared business key is a person's merge
    rule). Thresholds are policy (`link_proposals`). No table, column or kind is named here."""
    policy = policy if policy is not None else _policy()
    lp = policy.get("link_proposals") or {}
    min_matched = int(lp.get("min_matched", 3))
    min_ratio = float(lp.get("min_ratio", 0.8))
    targets: list[tuple[str, str, str, set[str], str]] = []   # (source, table, key, keys, kind)
    for t, snap in snapshots.items():
        tm = tables.get(t)
        kind = (tm.node or {}).get("kind") if tm is not None and tm.status != "unmapped" \
            else None
        kv = _key_values(snap)
        if kind and kv:
            targets.append((source_id, t, kv[0], kv[1], kind))
    for other, tabs in sorted((others or {}).items()):
        if other == source_id:
            continue
        for t, (snap, kind) in sorted(tabs.items()):
            kv = _key_values(snap)
            if kind and kv:
                targets.append((other, t, kv[0], kv[1], kind))
    out: dict[str, list[dict]] = {}
    for t, snap in sorted(snapshots.items()):
        tm = tables.get(t)
        if tm is None or tm.status == "unmapped" or not tm.node:
            continue
        kind = tm.node["kind"]
        pk = set(snap.get("primary_key") or [])
        declared = {f.get("column") for f in snap.get("foreign_keys") or []}
        have = {c for e in tm.edges or [] for c in (e or {}).get("evidence") or []}
        types = {c.get("name"): str(c.get("type") or "") for c in snap.get("columns") or []}
        rows = snap.get("rows") or []
        found: list[dict] = []
        for col in _columns(snap):
            if col in pk or col in declared or col in have or _NUMERIC_TYPE.search(
                    types.get(col, "")):
                continue
            vals = [str(r.get(col)) for r in rows if r.get(col) is not None]
            if len(vals) < min_matched:
                continue
            hits = []
            for (tsrc, ttab, tkey, keys, tkind) in targets:
                if tsrc == source_id and ttab == t and tkey == col:
                    continue
                matched = sum(1 for v in vals if v in keys)
                if matched >= min_matched and matched / len(vals) >= min_ratio:
                    hits.append((matched / len(vals), matched, tsrc, ttab, tkey, tkind))
            if not hits:
                continue
            hits.sort(key=lambda h: (-h[0], -h[1], h[2], h[3]))
            ratio, matched, tsrc, ttab, tkey, tkind = hits[0]
            link = {"column": col, "source": tsrc, "table": ttab, "key": tkey,
                    "target_kind": tkind, "matched": matched, "with_value": len(vals),
                    "basis": "value overlap with the target's key (measured on the rows read)",
                    "status": "proposed", "edge": None}
            tied = [f"{h[2]}:{h[3]}" for h in hits[1:] if (h[0], h[1]) == (ratio, matched)]
            if tied:
                link["alternatives"] = tied
            if is_subject_kind(vocab, tkind):
                link.update(status="not_proposed", why=(
                    f"the target kind {tkind!r} is a subject: an inferred link never points at "
                    f"a subject or a person record (hard constraint 1); a person who knows the "
                    f"match records it per record through the subject's verified channel"))
                found.append(link)
                continue
            roots = fk_hint_roots(col, kind, tkind, ttab)
            ek, rev, ewhy = _fk_edge(vocab, kind, tkind, col, policy, hint_roots=roots)
            if ek is None:
                same = (" — the rows may describe the same thing; folding them together is a "
                        "merge rule on a declared business key, which a person decides"
                        if tkind == kind else "")
                link.update(status="not_proposed", why=f"{ewhy}{same}")
            else:
                row_end = f"{kind}:{{{ROW_KEY}}}"
                ref_end = f"{tkind}:{{{FK_PREFIX}{col}}}"
                link["edge"] = {"kind": ek, "src": ref_end if rev else row_end,
                                "dst": row_end if rev else ref_end, "reversed": rev}
                link["why"] = (f"{matched} of {len(vals)} values of {col!r} are keys of "
                               f"{tsrc}:{ttab}.{tkey}; edge {ewhy}")
            found.append(link)
        if found:
            out[t] = found
    return out


def _link_id(link: dict) -> tuple:
    return (link.get("column"), link.get("source"), link.get("table"), link.get("key"))


def merge_link_proposals(tables: dict[str, TableMapping], proposals: dict[str, list[dict]]
                         ) -> dict[str, TableMapping]:
    """The tables whose `links` change: new proposals are added, a decided link (approved or
    rejected by a person) keeps its decision, and a proposal the data no longer supports is
    dropped unless decided. Links are outside the approval digest: proposing one never touches
    an approval."""
    out: dict[str, TableMapping] = {}
    for t, tm in tables.items():
        current = list(tm.links or [])
        decided = {_link_id(lk): lk for lk in current
                   if lk.get("status") in ("approved", "rejected")}
        fresh = [lk for lk in proposals.get(t, []) if _link_id(lk) not in decided]
        merged = [*decided.values(), *fresh]
        key = lambda lk: (str(lk.get("column")), str(lk.get("source")), str(lk.get("table")))
        merged.sort(key=key)
        if json.dumps(merged, sort_keys=True, default=str) != json.dumps(
                sorted(current, key=key), sort_keys=True, default=str):
            out[t] = tm.model_copy(update={"links": merged})
    return out


def captured_tables(source_id: str) -> dict[str, dict]:
    """{table: snapshot} of every table the source's LAST verified state holds (its captures),
    read-only — the other side of a cross-source link proposal."""
    from . import capture
    from .runner import load_state

    out: dict[str, dict] = {}
    try:
        state = load_state(source_id)
    except Exception:                                                # noqa: BLE001
        return out
    for entry in state.values():
        sha = (entry or {}).get("content_hash")
        if not sha or (entry or {}).get("graph") is False:
            continue
        try:
            data = json.loads(capture.get_bytes(sha))
        except Exception:                                            # noqa: BLE001
            continue
        if isinstance(data, dict) and data.get("table") and isinstance(data.get("rows"), list):
            ref = _table_ref(data.get("schema"), data["table"])
            out[ref] = data
    return out


def link_context(source_id: str, root: Optional[Path] = None, vocab=None
                 ) -> dict[str, dict[str, tuple[dict, str]]]:
    """{other source: {table: (snapshot, kind)}} for every OTHER ingest source whose mapping maps
    a table to a kind that is not a subject kind — what a cross-source link may point into."""
    from .connections import list_connections

    out: dict[str, dict[str, tuple[dict, str]]] = {}
    for conn in list_connections(root):
        if conn.id == source_id or getattr(conn, "purpose", "ingest") != "ingest":
            continue
        try:
            m = load_mapping(conn.id, root)
        except Exception:                                            # noqa: BLE001
            continue
        kinds = {t: (tm.node or {}).get("kind") for t, tm in m.tables.items()
                 if tm.status != "unmapped" and tm.node}
        kinds = {t: k for t, k in kinds.items() if k and not is_subject_kind(vocab, k)}
        if not kinds:
            continue
        snaps = captured_tables(conn.id)
        got = {t: (snaps[t], k) for t, k in kinds.items() if t in snaps}
        if got:
            out[conn.id] = got
    return out


def refresh_link_proposals(source_id: str, snapshots: dict[str, dict], vocab, *,
                           root: Optional[Path] = None,
                           others: Optional[dict] = None) -> SourceMapping:
    with mapping_lock(source_id, root):
        return _refresh_link_proposals_locked(source_id, snapshots, vocab, root=root,
                                              others=others)


def _refresh_link_proposals_locked(source_id: str, snapshots: dict[str, dict], vocab, *,
                                   root: Optional[Path] = None,
                                   others: Optional[dict] = None) -> SourceMapping:
    """Propose (C-3) links for the source's tables and save them in the mapping. `others`
    defaults to every other ingest source's captured, mapped tables (`link_context`)."""
    m = load_mapping(source_id, root)
    if others is None:
        others = link_context(source_id, root, vocab)
    props = propose_links(source_id, snapshots, m.tables, vocab, others=others)
    changed = merge_link_proposals({t: m.tables[t] for t in snapshots if t in m.tables}, props)
    if not changed:
        return m
    m = m.model_copy(update={"tables": {**m.tables, **changed}})
    save_mapping(m, root)
    return m


def approve_link(source_id: str, table: str, column: str, *, principal: Any,
                 target: Optional[str] = None, vocab=None,
                 root: Optional[Path] = None) -> SourceMapping:
    with mapping_lock(source_id, root):
        return _approve_link_locked(source_id, table, column, principal=principal, target=target,
                                    vocab=vocab, root=root)


def _approve_link_locked(source_id: str, table: str, column: str, *, principal: Any,
                         target: Optional[str] = None, vocab=None,
                         root: Optional[Path] = None) -> SourceMapping:
    """A named, AUTHENTICATED person approves one proposed link (C-3): it becomes an edge of the
    table's mapping carrying its `ref` ({source, table, key}), so the build renders the
    referenced row's identity in THAT source. Refused when the link is not a proposal with an
    edge, or when its target is a person kind (re-checked against the active vocabulary).

    The table's mapping changes, so its approval must cover the change: a table whose approval
    VERIFIES is re-approved by this same person (logged like any approval, digest over the new
    edges); a table not yet approved keeps `proposed` with the edge in it, for the usual
    approval. Nothing is approved on anyone else's behalf. `target` ("source:table") picks one
    link when a column has several."""
    from .plans import ApprovalRefused, require_authenticated_approver

    try:
        who = require_authenticated_approver(principal, what="link approval")
    except ApprovalRefused as exc:
        raise MappingApprovalRefused(str(exc), code=exc.code) from None
    if vocab is None:
        from databridge_graph.packs import active_vocabulary

        vocab = active_vocabulary()
    m = load_mapping(source_id, root)
    tm = m.tables.get(table)
    if tm is None or not tm.node:
        raise MappingApprovalRefused(f"{table}: no mapped table to link from")
    cands = [lk for lk in tm.links or [] if lk.get("column") == column and (
        target is None or f"{lk.get('source')}:{lk.get('table')}" == target)]
    if len(cands) != 1:
        raise MappingApprovalRefused(f"{table}.{column}: {len(cands)} link(s) match; name one "
                                     f"with target='source:table'")
    link = cands[0]
    if link.get("status") != "proposed" or not link.get("edge"):
        raise MappingApprovalRefused(f"{table}.{column}: the link is {link.get('status')!r}"
                                     f"{'' if link.get('edge') else ' with no edge kind'}; "
                                     f"only a proposal with an edge can be approved")
    if is_subject_kind(vocab, link.get("target_kind")):
        raise MappingApprovalRefused(f"{table}.{column}: the link points at subject kind "
                                     f"{link.get('target_kind')!r}; refused (hard constraint 1)")
    e = link["edge"]
    edge = {"kind": e["kind"], "src": e["src"], "dst": e["dst"], "evidence": [column],
            "fk": f"{column} -> {link['source']}:{link['table']} (approved link)",
            "reversed": bool(e.get("reversed")),
            "ref": {"source": link["source"], "table": link["table"], "key": link.get("key")}}
    logged = logged_approvals(source_id, root)
    was_approved = tm.status == "approved" and approval_problem(
        source_id, table, tm, vocab, logged=logged, root=root) is None
    node = dict(tm.node)
    attrs = dict(node.get("attrs") or {})
    attrs.setdefault(column, column)
    node["attrs"] = attrs
    at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    # LINK7 cross-track 1: HOW the approver signed in is kept on the link (links sit outside the
    # approval digest, so this changes no approval)
    links = [({**lk, "status": "approved", "approved_by": who["principal_id"], "at": at,
               **_decider(who)} if lk is link else lk) for lk in tm.links]
    new = tm.model_copy(update={"node": node, "edges": [*(tm.edges or []), edge],
                                "links": links, "status": "proposed", "approval": None,
                                "rationale": [*tm.rationale, (
                                    f"link {column} -> {link['source']}:{link['table']} "
                                    f"approved by {who['principal_id']}")]})
    problems = validate_table_mapping(table, new, vocab, check_references=True)
    if problems:
        raise MappingApprovalRefused(f"{table}: the link cannot be approved: "
                                     f"{'; '.join(problems)}")
    _log_link_decision(source_id, table, link, "approve", who, at, root)
    if was_approved:
        record = {**who, "at": at, "digest": mapping_digest(new)}
        _append_approval(source_id, {"action": "approve", "source_id": source_id,
                                     "table": table, **record}, root)
        new = new.model_copy(update={"status": "approved", "approval": record})
    m = m.model_copy(update={"tables": {**m.tables, table: new}})
    save_mapping(m, root)
    return m


def _decider(who: dict) -> dict:
    return {"decided_dev_only": bool(who.get("dev_only")),
            "decided_authenticated_by": who.get("authenticated_by"), "actor": actor_of(who)}


def _log_link_decision(source_id: str, table: str, link: dict, decision: str, who: dict,
                       at: str, root: Optional[Path]) -> dict:
    """A link decision is a decision: it is appended to the mapping's decision ledger with the
    actor object (R-028 #2/#7). It is not a table approval (`logged_approvals` reads only
    ``action: approve``)."""
    return _append_approval(source_id, {
        "action": f"link_{decision}", "source_id": source_id, "table": table,
        "link": {k: link.get(k) for k in ("column", "source", "table", "key", "target_kind")},
        **actor_of(who), "at": at}, root)


def reject_link(source_id: str, table: str, column: str, *, principal: Any,
                target: Optional[str] = None, reason: str = "",
                root: Optional[Path] = None) -> SourceMapping:
    with mapping_lock(source_id, root):
        return _reject_link_locked(source_id, table, column, principal=principal, target=target,
                                   reason=reason, root=root)


def _reject_link_locked(source_id: str, table: str, column: str, *, principal: Any,
                        target: Optional[str] = None, reason: str = "",
                        root: Optional[Path] = None) -> SourceMapping:
    """A named, AUTHENTICATED person rejects one PROPOSED link (C-3; LINK7 cross-track 1, moved
    here from the gateway). The decision is kept on the link — who, how they signed in (the dev
    token is marked), when, why — and `merge_link_proposals` keeps a decided link, so the next
    sync never proposes it again. Nothing else in the mapping changes; no approval is touched.
    `target` ("source:table") picks one link when a column has several."""
    from .plans import ApprovalRefused, require_authenticated_approver

    try:
        who = require_authenticated_approver(principal, what="link rejection")
    except ApprovalRefused as exc:
        raise MappingApprovalRefused(str(exc), code=exc.code) from None
    m = load_mapping(source_id, root)
    tm = m.tables.get(table)
    if tm is None:
        raise MappingApprovalRefused(f"{table}: not in the mapping")
    cands = [lk for lk in tm.links or [] if lk.get("column") == column and (
        target is None or f"{lk.get('source')}:{lk.get('table')}" == target)]
    if len(cands) != 1:
        raise MappingApprovalRefused(f"{table}.{column}: {len(cands)} link(s) match; name one "
                                     f"with target='source:table'")
    link = cands[0]
    if link.get("status") != "proposed":
        raise MappingApprovalRefused(f"{table}.{column}: the link is {link.get('status')!r}; "
                                     f"only a proposal can be rejected")
    at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    _log_link_decision(source_id, table, link, "reject", who, at, root)
    links = [({**lk, "status": "rejected", "rejected_by": who["principal_id"], "at": at,
               **_decider(who), "reject_reason": reason.strip() or None}
              if lk is link else lk) for lk in tm.links]
    m = m.model_copy(update={"tables": {**m.tables, table: tm.model_copy(update={
        "links": links})}})
    save_mapping(m, root)
    return m


__all__ = ["SourceMapping", "TableMapping", "propose_rules", "propose_model", "ensure_mapping",
           "approve_mapping", "MappingApprovalRefused", "validate_table_mapping",
           "mapping_digest", "effective_mapping", "approval_problem", "logged_approvals",
           "approval_log_path", "screen_for_model", "NodeSpecModel", "EdgeSpecModel",
           "set_table_mapping", "load_mapping", "save_mapping", "mapping_path",
           "reference_problems", "additive_problems", "approve_delegated_additive",
           "delegation_scope_problems", "DELEGATION_SCOPE",
           "kind_for_table", "resolve_kind", "MAPPING_TASK_CLASS", "fk_edges", "fk_hint_roots",
           "resolve_pending_fks", "propose_links", "merge_link_proposals", "refresh_link_proposals",
           "approve_link", "reject_link", "link_context", "captured_tables", "is_person_kind",
           "is_subject_kind", "mapping_lock", "mapping_ledger",
           "FK_NO_KIND",
           "is_association", "association_mapping", "propose_split", "split_value",
           "split_candidates", "split_problems", "split_kinds", "resolve_pending_splits",
           "KIND_KEY", "SplitSpecModel"]

"""Idempotent bi-temporal upsert + entity resolution (T08, 04_PRD §6, §7).

`upsert_node` resolves an incoming entity against the open graph and either:
  * CORROBORATES an existing version (same canonical_id, or fuzzy name+kind ≥ 0.92) by appending
    its SourcePointers — the corroboration signal (04_PRD §3) — OR
  * SUPERSEDES it when attrs changed: close the prior version (ADR-0003 invalidate-don't-delete)
    and open a new one, OR
  * INSERTS a fresh node.

Resolution order (04_PRD §6 Stage-1, deterministic — no LLM):
  1. exact match on ``canonical_id`` (email / domain / employee-id),
  2. fuzzy token-set match on ``label`` within the same ``kind`` (ratio ≥ 0.92) as fallback.

Anti-fabrication (P4): the incoming node/edge is a ``GraphNode``/``GraphEdge`` whose validator
already refused any ungrounded value, so nothing ungrounded can reach this layer.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import timedelta
from difflib import SequenceMatcher
from typing import Optional
from collections.abc import Sequence

from databridge_core import SourcePointer

from .packs import Vocabulary, active_vocabulary
from .schema import IDENTITY_BEARING_FIELDS, GraphEdge, GraphNode
from .store import GraphStore, row_to_edge, row_to_node

FUZZY_THRESHOLD = 0.92


# --- token-set ratio (size-AWARE; stdlib-only via difflib) ------------------------
_TOKEN = re.compile(r"[a-z0-9]+")
# legal-form folding (04_PRD §6): a company's legal suffix must not block a real match, so
# "Coral Harbour Pte Ltd" / "Coral Harbour Pte Limited" / "Coral Harbour" all normalise to the
# same core token set.
# Spelled-out forms are folded too (else "corp" folds but "corporation" doesn't → asymmetry).
_SUFFIXES = {"inc", "incorporated", "ltd", "limited", "llc", "corp", "corporation", "co",
             "company", "pte", "plc", "gmbh", "sa", "ag", "nv", "bv", "holdings", "group"}


def _tokens(s: str) -> set[str]:
    return {t for t in _TOKEN.findall(s.lower()) if t not in _SUFFIXES}


def token_set_ratio(a: str, b: str) -> float:
    """Order-insensitive, SIZE-AWARE fuzzy similarity in [0,1].

    Blends Jaccard over token sets (penalises a SUBSET match — "Bank" is NOT "Bank of America")
    with a char-level ratio of the sorted token strings (typo tolerance). Equal token sets → 1.0;
    a one-word name swallowed by a longer name scores low. This deliberately rejects the rapidfuzz
    ``token_set_ratio`` leniency where an intersection equal to one side returns 1.0 — for ENTITY
    RESOLUTION that lenient subset match is silent false-merge (a distinct entity absorbed)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb or not (ta & tb):
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    seq = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return 0.5 * jaccard + 0.5 * seq


@dataclass
class UpsertOutcome:
    id: str
    action: str              # "inserted" | "corroborated" | "superseded"
    matched_by: str = ""     # "id" | "canonical" | "fuzzy" | ""
    requires_human_selection: bool = False
    """True whenever this upsert touched a **subject** kind (spine D4b).

    Record linkage on a declared identifier is not an identity determination, and nothing in this
    module writes one: a determination is an edge bound to an `ATTESTED_META_KIND`, and
    `attestation.py` refuses to build one without a named human. This flag exists so a caller
    reading an outcome cannot mistake "the store linked these rows" for "we know who this is" —
    the distinction the whole constraint rests on, and the one a bare `action="corroborated"`
    silently loses.

    **`bool`, not `Literal[True]` — and that is deliberate, not the gap it looks like at a
    glance.** Review R-010 finding 2 read this field against `ResolutionResult.requires_human_selection`
    (`Literal[True]`, `packages/intake`) and asked why this one permits `False`. The two fields
    share a name and nothing else: `ResolutionResult` answers "who is the person at the counter?" —
    CLAUDE.md's hard constraint 1, always `True` because that question is NEVER answered by code.
    `UpsertOutcome` answers "did this write touch a **subject** kind?", asked of every upsert
    including ones over an ADDRESS or a PAYMENT, where "no" is the honest, informative answer and
    a `Literal[True]` would make the field a constant. Disposition: FALSE POSITIVE. See
    `wiki/40-reviews/R-010-graph-seed-dispositions.md`.
    """


# Kinds for which NAME SIMILARITY MUST NEVER MERGE TWO NODES.
#
# Merging by name is right for organisations — "Coral Harbour Pte Ltd" and "Coral Harbour Pte
# Limited" are one company. It is catastrophic for a SUBJECT: the entity whose identity the
# system may never auto-confirm (spine D4b). Fuzzy-merging two subjects collapses different people
# into one node, and merging an extracted document mention into a registry row is the machine
# deciding whose record this is — precisely the determination that is reserved for a named human
# (CLAUDE.md §Hard constraints 1; ADR-0009).
#
# A subject therefore merges ONLY on an exact canonical_id (one of the identifiers its pack
# declares). Absent that, a new node is inserted and the duplicate is visible — which is the safe
# direction: two nodes staff can tell apart beats one node that silently fused two people.
#
# **This set is COMPUTED, never written down here.** It is the union of the enabled packs'
# `subject_kinds` (spine D4b), so the engine enforces the rule without naming the entity it
# protects, and a pack that declares no subject kind gets no identity-resolution capability at all
# rather than silently getting an unguarded one. Call it per resolution, not at import: the
# deployment binding can change (tests bind a pack per case) and a module-level frozenset would
# freeze whichever packs happened to be bound when this module was first imported.
def never_fuzzy_merge_kinds(vocab: Optional[Vocabulary] = None) -> frozenset[str]:
    """Kind names name similarity must never merge, derived from the enabled packs (spine D4b).

    Pass the ``vocab`` you already hold when you have one: a caller that resolves the active
    vocabulary once and threads it through cannot be handed two different answers within one
    resolution, which is the failure the removal of the constant below was about.
    """
    v = vocab if vocab is not None else active_vocabulary()
    return v.never_fuzzy_merge


# `NEVER_FUZZY_MERGE` USED to resolve here through a module `__getattr__`, on the reasoning that
# the name is what the ADRs and the reviews call this set. Review (2026-08-16, finding A5) showed
# the reasoning was wrong in a way that matters:
#
#     from databridge_graph.merge import NEVER_FUZZY_MERGE
#
# A module `__getattr__` CANNOT make a from-import dynamic. Python evaluates the attribute exactly
# once and binds the returned frozenset into the importing module, so the importer holds whichever
# packs happened to be bound at ITS import time — for the life of the process, across every later
# `bind_deployment`. Import under one pack, bind another, and the consumer protects the wrong
# subject while believing it is protected: a direct route to the one merge this system forbids.
#
# So the name is GONE, not fixed. `never_fuzzy_merge_kinds()` is a call and cannot be snapshotted
# by accident, and `test_never_fuzzy_merge_is_not_importable.py` fails if a module-level constant
# of that shape reappears anywhere in the package.


def resolve_existing(store: GraphStore, incoming: GraphNode,
                     vocab: Optional[Vocabulary] = None, *, id_only: bool = False):
    """Return (open_row, matched_by) for the existing node the incoming one resolves to, else
    (None, "").

    Resolved against ONE vocabulary snapshot, taken here and threaded down: asking the deployment
    binding twice inside a single resolution is how a node gets exact-merged under one answer and
    fuzzy-guarded under another.
    """
    v = vocab if vocab is not None else active_vocabulary()
    is_subject = v.is_subject(str(incoming.kind))
    # 0) the SAME NODE, by id. `id` is `f"{kind}:{slug}"` and is computed by whoever wrote it, so
    #    two upserts of one id are two sightings of one record — matching them is bookkeeping, not
    #    an identity judgement, and no similarity is involved.
    #
    #    Without this step, re-upserting a SUBJECT that carries no `canonical_id` inserted a
    #    duplicate row EVERY TIME: the exact-canonical branch has nothing to match on, and step 2
    #    is (correctly) unreachable for a subject. So the one operation the module documents as
    #    idempotent was not, for exactly the kinds it protects most carefully — found when the
    #    map-driven estate build produced three new rows per re-load for organisations sourced from
    #    a free-text field, which deliberately has no identifier to use as a canonical id.
    #
    #    It is checked FIRST because it is the strongest signal there is. `canonical_id` says "these
    #    two records name the same thing"; an equal id says "this IS that record".
    #
    #    Review R-010 finding 1 asked whether this step (and the canonical step below) lets the
    #    module "automatically merge subject records" in the sense CLAUDE.md constraint 1 forbids.
    #    Verdict: this step's protection is exactly as strong as `incoming.id`'s uniqueness, which
    #    THIS FUNCTION cannot verify — it can only trust that whoever computed the id used a
    #    genuinely 1:1 column. That trust is not misplaced by accident: `estate_seed._build_node`
    #    is the one caller that computes ids at scale from untrusted data, and it now REFUSES
    #    (rather than silently fusing) a subject-kind collision where two rows with DIFFERENT raw
    #    keys normalise to the same id — see its `raw_keys` guard and
    #    `test_slug_collision_cannot_silently_fuse_two_different_subjects`. With that guard in
    #    place, an id match reaching this function for a subject is provably two sightings of one
    #    declared key, which is bookkeeping, not a determination — the same reasoning the canonical
    #    branch below already documents for A4. Record linkage on a source's own declared,
    #    per-attribute-grounded identifier (id match or canonical match) is therefore NOT the
    #    identity verification CLAUDE.md reserves for a named human: that determination is a
    #    `RESOLVED_TO` edge behind an attestation (`attestation.py`), a code path this module has
    #    no way to reach (`_build_edge` refuses to construct one). What this function may never do
    #    — and does not — is let SIMILARITY stand in for a declared identifier: `never_fuzzy_merge`
    #    below is exactly that line.
    same = store.open_node_row(incoming.id)
    if same is not None:
        return same, "id"
    if id_only:
        # The caller minted `incoming.id` as a MENTION id (the extractor's subject path, review
        # R-019 t06 C1): only a re-sighting of that exact mention may match. No canonical and no
        # similarity step — a subject mention read from prose is never resolved to another record.
        return None, ""
    # 1) exact canonical match (strongest signal — a declared identifier IS the identity).
    #
    #    For a SUBJECT, the identifier must be grounded BY ITS OWN POINTER before it may decide
    #    that two records are the same person. Review (2026-08-16, finding A4): `canonical_id` is
    #    a top-level field, so it escaped the per-attribute wall entirely — an invented identifier
    #    needed no citation of its own and could drive an exact identity merge while the
    #    entity-level `sources` vouched for something else entirely. Merging two subject records
    #    IS the determination reserved for a named human (CLAUDE.md §Hard constraints 1), so an
    #    ungrounded identifier does not get to make it: no match, a new node is inserted, and the
    #    duplicate stays visible. Two rows staff can tell apart beats one row that fused two people.
    if incoming.canonical_id and is_subject and not incoming.identity_field_is_grounded(
            "canonical_id"):
        return None, ""
    if incoming.canonical_id:
        row = store.find_by_canonical(incoming.kind, incoming.canonical_id)
        if row is not None:
            return row, "canonical"
    # A SUBJECT never reaches the similarity step, by TWO independent rules: the pack's
    # `never_fuzzy_merge` set, and `is_subject` itself (review R-019 t06 C1 — the pack loader forces
    # the flag True today, but a guard that holds only while one flag is set is one guard, not a
    # wall). Either one alone keeps a subject out.
    if is_subject or str(incoming.kind) in never_fuzzy_merge_kinds(v):
        return None, ""
    # 2) fuzzy name+kind fallback within the same kind
    #
    # `open_node_candidates_of_kind` is a LEAN projection of the exact same rows
    # `open_nodes_of_kind` would return (same WHERE, same ORDER BY) — just `(rowid_pk, label,
    # canonical_id)` instead of every column, since scoring never reads `attrs_json` or the
    # temporal columns. At estate scale that is the difference between materialising ~9 columns
    # (one a JSON blob) and 3 small ones on every one of O(n) candidates per insert — the
    # candidate SET, and therefore which node can win, is unchanged; only the cost of scanning it
    # is. Only the eventual winner is hydrated to a full row, once, via `node_row_by_rowid`.
    best_rowid, best_score = None, 0.0
    for row in store.open_node_candidates_of_kind(incoming.kind):
        # a different confirmed canonical_id on both sides means DIFFERENT entities — never fuzzy-merge
        if incoming.canonical_id and row["canonical_id"] and row["canonical_id"] != incoming.canonical_id:
            continue
        score = token_set_ratio(incoming.label, row["label"])
        # strict '>' keeps the FIRST (lowest rowid) candidate on ties → deterministic merge target
        if score > best_score:
            best_rowid, best_score = row["rowid_pk"], score
    if best_rowid is not None and best_score >= FUZZY_THRESHOLD:
        return store.node_row_by_rowid(best_rowid), "fuzzy"
    return None, ""


#: The private name callers used before INT5 (mcp_ingest's build, R-022 build cross-track 2c).
_resolve_existing = resolve_existing


def _token_seq(s: str) -> list[str]:
    """The ORDERED, legal-form-folded tokens of a name (the sequence twin of `_tokens`)."""
    return [t for t in _TOKEN.findall(str(s or "").lower()) if t not in _SUFFIXES]


@dataclass
class NameJoin:
    """A mention whose words are the LEADING words of exactly one keyed record's own grounded name.

    `row` is that record's open row; `attr` / `pointer` name the record's own attribute (and its
    pointer, from the record's own source data) whose value is the name matched."""
    row: object
    attr: str
    pointer: SourcePointer
    name: str


def _grounded_names(node: GraphNode) -> list[tuple[str, str, SourcePointer]]:
    """(attr, value, pointer) for each name the node's OWN source data grounds: its label, when
    the label carries a pointer of its own or an attribute grounded by its own pointer holds
    exactly the label's text. A label nothing of the node's own grounds is not a name to match."""
    out: list[tuple[str, str, SourcePointer]] = []
    if node.label and "label" in node.attr_sources:
        out.append(("label", node.label, node.attr_sources["label"]))
    for key, value in node.attrs.items():
        if isinstance(value, str) and value == node.label and key in node.attr_sources:
            out.append((key, value, node.attr_sources[key]))
    return out


def name_prefix_join(store: GraphStore, incoming: GraphNode,
                     vocab: Optional[Vocabulary] = None) -> Optional[NameJoin]:
    """E2E run-3 P-05: the ONE open record of `incoming`'s kind that carries a declared business
    key (`canonical_id`) and whose own grounded name BEGINS with the mention's words ("Corriedale",
    "Corriedale Grocers" -> the record named "Corriedale Grocers Co-operative"), else None.

    This is a CANDIDATE-grade join, never an identity determination, so it is fenced hard:
      * never for a subject kind or a `never_fuzzy_merge` kind (hard constraint 1) — those stay
        candidates for a named human;
      * never for a mention that already carries its own canonical id (an exact key decides);
      * the matched name must be grounded by the record's OWN pointer (`_grounded_names`);
      * whole tokens, in order, from the first: a mention is never matched mid-name;
      * exactly one keyed record may match, and NO other open record of the kind may begin with
        the mention's words unless its own name is itself a leading part of the winner's (another
        sighting of the same name). Anything else is ambiguous and nothing is joined.
    No name, pattern or list is written down: the names compared are the source's own."""
    v = vocab if vocab is not None else active_vocabulary()
    kind = str(incoming.kind)
    if v.is_subject(kind) or kind in never_fuzzy_merge_kinds(v) or incoming.canonical_id:
        return None
    want = _token_seq(incoming.label)
    if not want:
        return None
    # SQL prefilter only narrows the scan (a token may be spelled with punctuation around it);
    # the decision is the token comparison below
    like = f"%{want[0]}%"
    rows = store.fetchall(
        "SELECT * FROM nodes WHERE kind = ? AND system_to IS NULL AND valid_to IS NULL "
        "AND lower(label) LIKE ? ORDER BY rowid_pk", (kind, like))

    def begins(seq: list[str]) -> bool:
        return len(seq) >= len(want) and seq[:len(want)] == want

    keyed: list[tuple[object, str, str, SourcePointer]] = []
    others: list[list[str]] = []
    for row in rows:
        if row["id"] == incoming.id:
            continue
        if row["canonical_id"]:
            node = row_to_node(store, row)
            hits = [(a, val, p) for a, val, p in _grounded_names(node) if begins(_token_seq(val))]
            if hits:
                keyed.append((row, *hits[0]))
                continue
        seq = _token_seq(row["label"])
        if begins(seq):
            others.append(seq)
    if len({r["id"] for r, *_ in keyed}) != 1:
        return None
    row, attr, name, ptr = keyed[0]
    target = _token_seq(name)
    if any(seq != target[:len(seq)] for seq in others):
        return None                               # another record also begins so: ambiguous
    return NameJoin(row=row, attr=attr, pointer=ptr, name=name)


def upsert_node(store: GraphStore, incoming: GraphNode,
                vocab: Optional[Vocabulary] = None, *, id_only: bool = False) -> UpsertOutcome:
    """Idempotent: re-applying the same node CORROBORATES (no duplicate version).

    ``vocab`` may be passed by a caller that already resolved the active vocabulary, so one
    request cannot straddle two deployment bindings.

    ``id_only=True`` restricts resolution to step 0 (the exact same id): a caller that minted a
    MENTION id — the extractor, for a subject kind — gets idempotent re-sighting and nothing else.
    It can never be canonical- or fuzzy-matched into another record.
    """
    if not incoming.sources:                                  # P4 (also enforced by validator)
        raise ValueError("upsert_node REFUSED: ungrounded node")
    v = vocab if vocab is not None else active_vocabulary()
    subject = v.is_subject(str(incoming.kind))
    row, matched_by = resolve_existing(store, incoming, v, id_only=id_only)
    if row is None:
        store.add_node(incoming)
        return UpsertOutcome(id=incoming.id, action="inserted",
                             requires_human_selection=subject)

    existing = row_to_node(store, row)
    if not _conflicts(existing, incoming):
        # corroboration: append only NEW pointers (idempotent — same pointer twice is a no-op).
        # The existing (possibly richer) node is preserved — an extracted MENTION of a seed entity
        # (attrs={}) must NOT clobber the seed's role/email/valid_from (that would wipe the demo's
        # provenance view). Only a real fact conflict supersedes (below).
        new_ptrs = _new_pointers(existing.sources, incoming.sources)
        store.append_sources(existing.id, new_ptrs)
        return UpsertOutcome(id=existing.id, action="corroborated", matched_by=matched_by,
                             requires_human_selection=subject)

    # facts changed → supersede (ADR-0003): close the prior open version, open a new one that
    # KEEPS the resolved id (so edges keep pointing at it) and unions provenance. ``at`` is forced
    # strictly AFTER the prior version's system_from so the superseded version has a NON-zero
    # system interval (otherwise [S, S) is unqueryable — "what we believed before" vanishes).
    at = store.now()
    if at <= existing.system_from:
        at = existing.system_from + timedelta(microseconds=1)
    # Review R-010 finding 8: this used to be `incoming.model_copy(update={id, canonical_id,
    # sources, system_from})` — every OTHER field, including `attrs`/`attr_sources`, came from
    # `incoming` untouched. That silently dropped every fact `existing` carried that `incoming`
    # did not re-state (a real conflict on ONE field wiped every OTHER grounded attribute the
    # entity had), and it could re-open the new version with an UNGROUNDED `canonical_id` — the
    # A4 hole one level up — whenever `incoming` carried none of its own (the field, not its
    # pointer, was preserved; the citation for it was not). Fixed by merging forward: `incoming`'s
    # non-None values win (that IS the conflict this branch exists to record), a key `incoming`
    # is silent about (absent from its `attrs`) keeps `existing`'s value AND pointer, and a key
    # `incoming` explicitly re-asked and got nothing for (`None`) lands in `unresolved` rather
    # than reviving a stale value — it never overwrites a value `existing` already had, because
    # "not found this time" is not "no longer true" (`test_reextraction_corroborates_and_preserves_seed_attrs`'s
    # rule, extended to the supersede path it did not previously cover).
    merged_attrs = dict(existing.attrs)
    merged_attr_sources = dict(existing.attr_sources)
    for key, value in incoming.attrs.items():
        if value is None:
            if key not in merged_attrs:
                merged_attrs[key] = None            # a newly-asked, still-unanswered question
            continue                                # never let "not found here" erase a fact
        merged_attrs[key] = value
        if key in incoming.attr_sources:
            merged_attr_sources[key] = incoming.attr_sources[key]
    # `canonical_id` / `label` are grounded through `attr_sources` like an attribute but live
    # OUTSIDE `attrs` (`IDENTITY_BEARING_FIELDS`), so the loop above never touches their pointers;
    # carry `incoming`'s forward when it supplied one (a fresher, re-grounded identity claim),
    # else keep `existing`'s (already seeded into `merged_attr_sources` above) rather than losing
    # it — the specific A4 regression finding 8 named.
    #
    # INT5 (R-022 build cross-track 2a): the LABEL is the one exception. A merged node keeps the
    # label it has unless `incoming` grounds its own label by a pointer of its own
    # (`_label_wins`): a surface form another document happens to use ("CORAL HARBOUR") no longer
    # relabels the node, and a relabel that does happen carries the pointer that grounds it.
    label_wins = _label_wins(existing, incoming)
    for id_field in IDENTITY_BEARING_FIELDS:
        if id_field == "label" and not label_wins:
            continue
        if id_field in incoming.attr_sources:
            merged_attr_sources[id_field] = incoming.attr_sources[id_field]
    merged = incoming.model_copy(update={
        "id": existing.id,
        "label": incoming.label if label_wins else existing.label,
        "canonical_id": incoming.canonical_id or existing.canonical_id,
        "attrs": merged_attrs,
        "attr_sources": merged_attr_sources,
        "sources": tuple(existing.sources) + _new_pointers(existing.sources, incoming.sources),
        "system_from": at,
    })
    closed = store.replace_node(merged, at=at, valid_to=incoming.valid_from)   # ATOMIC close+insert
    assert closed == 1, f"supersede expected exactly one open version of {existing.id!r}, closed {closed}"
    return UpsertOutcome(id=existing.id, action="superseded", matched_by=matched_by,
                         requires_human_selection=subject)


def _canon_val(v) -> str:
    # canonicalise one attr value (existing comes back from json.loads → a date is already a str;
    # incoming is native) so comparison is roundtrip-stable, never an endless false "changed" loop.
    return json.dumps(v, sort_keys=True, default=str)


def _conflicts(existing: GraphNode, incoming: GraphNode) -> bool:
    """A REAL fact conflict (→ supersede): a different label grounded by its own pointer, a
    canonical id found for the first time (INT5, `_label_wins` / `_new_canonical`), a shared attr
    key whose value
    changed, OR a NEW grounded attr `incoming` carries that `existing` does not. An incoming node
    carrying FEWER attrs (an extracted mention of a richer seed node) is NOT a conflict — it
    corroborates, preserving the existing node's attrs/valid_from. A genuinely changed value (role
    VP→CEO) still supersedes (ADR-0003 invalidate-don't-delete).

    The third clause is review R-010 finding 8's other half: before it, a genuinely NEW grounded
    fact (an `incoming` attr key `existing` had never seen — an `email` a second document adds,
    say) satisfied neither half of the old check (`k in existing.attrs` is False for a new key), so
    it took the CORROBORATE branch, which only appends `sources` pointers and never touches
    `attrs` — the new fact was accepted as evidence the entity was seen again and then silently
    thrown away. Treating a new non-None key as a conflict routes it through supersede instead,
    where `upsert_node`'s merge step (below) is what actually carries it into the node's `attrs`."""
    if _label_wins(existing, incoming):
        return True
    if _new_canonical(existing, incoming):
        return True
    for key, value in incoming.attrs.items():
        if value is None:
            continue
        if key not in existing.attrs:
            return True
        if _canon_val(existing.attrs[key]) != _canon_val(value):
            return True
    return False


def _label_wins(existing: GraphNode, incoming: GraphNode) -> bool:
    """INT5 (R-022 build cross-track 2a): a DIFFERENT label replaces the node's only when
    `incoming` grounds it by a pointer of its own. An ungrounded surface form is a sighting
    (its pointers are appended), not a relabel."""
    return existing.label != incoming.label and incoming.identity_field_is_grounded("label")


def _new_canonical(existing: GraphNode, incoming: GraphNode) -> bool:
    """INT5 (R-022 build cross-track 2b): a canonical id found for the FIRST time — `existing`
    has none, `incoming` carries one grounded by its own pointer — is a new fact, so it takes the
    supersede path that carries it (the corroborate path only appends pointers, which dropped the
    key: the N-16 table-side join recorded it only when a row also brought a new attribute). An
    ungrounded identifier is never carried this way."""
    return (bool(incoming.canonical_id) and not existing.canonical_id
            and incoming.identity_field_is_grounded("canonical_id"))


def _new_pointers(existing: Sequence[SourcePointer],
                  incoming: Sequence[SourcePointer]) -> tuple[SourcePointer, ...]:
    """Pointers in ``incoming`` that ``existing`` does not already carry, order preserved.

    Returns a TUPLE because `GraphNode.sources` / `GraphEdge.sources` are tuples — they were
    made immutable on 2026-08-16 after review found that `frozen=True` is shallow and a
    validated node's grounding could be emptied with `.clear()`. Accepts any Sequence so
    callers may still pass a list.
    """
    seen = {sp.model_dump_json() for sp in existing}
    out, added = [], set()
    for sp in incoming:
        key = sp.model_dump_json()
        if key not in seen and key not in added:
            out.append(sp)
            added.add(key)
    return tuple(out)


def _edge_conflicts(existing: GraphEdge, incoming: GraphEdge) -> bool:
    """A REAL fact conflict on an edge, mirroring `_conflicts` for nodes minus the label check
    (an edge has none): a shared attr key whose value changed, or a NEW grounded attr key.
    `incoming` carrying fewer/no attrs is NOT a conflict — that is the common relationship-
    existence-fact case (no attrs at all) and stays a corroboration."""
    for key, value in incoming.attrs.items():
        if value is None:
            continue
        if key not in existing.attrs or _canon_val(existing.attrs[key]) != _canon_val(value):
            return True
    return False


def upsert_edge(store: GraphStore, incoming: GraphEdge) -> UpsertOutcome:
    """Edge upsert: same (src, dst, kind) open edge ⇒ corroborate if nothing about it changed,
    supersede (ADR-0003 invalidate-don't-delete) if an attr did, else insert.

    Review R-010 finding 7: this always corroborated, on the reasoning ("the demo edges are
    relationship-existence facts") that an attr-valued edge conflict does not arise in practice.
    That reasoning was never enforced in code — an edge WITH attrs that then changed silently
    corroborated too, which discarded `incoming`'s attrs and their provenance entirely (`sources`
    is unioned; `attrs`/`attr_sources` never was) and left the OLD value the queryable belief with
    no way to ask "what did we believe before" (the bi-temporal invariant this store otherwise
    guarantees for nodes). Mirrors `upsert_node`'s conflict/merge shape now that it applies.
    """
    if not incoming.sources:
        raise ValueError("upsert_edge REFUSED: ungrounded edge")
    # `find_open_edge` is an indexed exact-triple lookup (`ix_edges_open_triple`), not a scan of
    # every open edge — see its docstring. This used to be `for row in store.open_edges(): if
    # (row["src_id"] == ... and ...): ...`, which materialised the full open-edge set on EVERY
    # insert and made a 52,747-edge estate build O(n^2) (~1.4B row comparisons).
    row = store.find_open_edge(incoming.src_id, incoming.dst_id, incoming.kind.value)
    if row is None:
        store.add_edge(incoming)
        return UpsertOutcome(id=incoming.id, action="inserted")

    existing = row_to_edge(store, row)
    if not _edge_conflicts(existing, incoming):
        new_ptrs = _new_pointers(existing.sources, incoming.sources)
        store.append_sources(row["id"], new_ptrs)
        return UpsertOutcome(id=row["id"], action="corroborated", matched_by="triple")

    # facts changed → supersede, same merge-forward shape as `upsert_node`: `incoming`'s non-None
    # values win, a key it is silent about keeps `existing`'s value AND pointer.
    at = store.now()
    if at <= existing.system_from:
        at = existing.system_from + timedelta(microseconds=1)
    merged_attrs = dict(existing.attrs)
    merged_attr_sources = dict(existing.attr_sources)
    for key, value in incoming.attrs.items():
        if value is None:
            if key not in merged_attrs:
                merged_attrs[key] = None
            continue
        merged_attrs[key] = value
        if key in incoming.attr_sources:
            merged_attr_sources[key] = incoming.attr_sources[key]
    merged = incoming.model_copy(update={
        "id": existing.id,
        "attrs": merged_attrs,
        "attr_sources": merged_attr_sources,
        "sources": tuple(existing.sources) + _new_pointers(existing.sources, incoming.sources),
        "system_from": at,
    })
    closed = store.close_edge(existing.id, at=at, valid_to=incoming.valid_from)
    assert closed == 1, f"supersede expected exactly one open version of {existing.id!r}, closed {closed}"
    store.add_edge(merged)
    return UpsertOutcome(id=existing.id, action="superseded", matched_by="triple")

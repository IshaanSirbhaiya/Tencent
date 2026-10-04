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
import unicodedata
from dataclasses import dataclass, field
from datetime import timedelta
from difflib import SequenceMatcher
from typing import Optional
from collections.abc import Sequence

from databridge_core import SourcePointer

from .packs import IdentityRule, Vocabulary, active_vocabulary
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
    # IDN (E2E run 5 F-04): a kind whose records are natural persons is treated exactly as a
    # subject here, declared or not (`Vocabulary.person_kinds`): no rule, no similarity, no
    # ungrounded key ever decides that two records are one person.
    is_subject = v.is_subject(str(incoming.kind)) or v.is_person(str(incoming.kind))
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
    # FIN-04: a record an approved identity rule ALREADY merged into another (`consolidate_identity`)
    # re-sights the survivor, not a fresh copy of itself. `identity_rule_for` is None for every
    # subject kind, so no subject can reach this branch.
    #
    # IDN (E2E run 5 W-02): the same for a mention `reconcile_keyless` joined to its keyed record
    # after the record arrived. Never for a person: nothing ever absorbs one.
    # Only a MENTION can have been absorbed that way (a table row is written `id_only` and is
    # never a keyless mention), so a row upsert does not pay for the lookup.
    rule = v.identity_rule_for(str(incoming.kind))
    if not is_subject and (rule is not None or not id_only):
        survivor = _absorbed_into(store, incoming)
        if survivor is not None:
            return survivor, "absorbed"
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
    # FIN-04: a kind whose pack declares identity rules merges on the rules a named human has
    # APPROVED for this deployment (a business key, an exact normalised name), and on the default
    # name-similarity step only when the pack says `similarity_merge: true`.
    if rule is not None:
        row, how = _rule_match(store, incoming, rule, approved_rule_types(rule, v))
        if row is not None:
            return row, how
        if not rule.similarity_merge:
            return None, ""
    # FIN-07: a DOCUMENT is never one with another because their titles look alike. Two filings
    # titled "Annual Report for the financial year ended 31 December 2023" from two companies are
    # two documents; a document is identified by its source item (an id the extractor scopes to
    # the item) or by a declared, grounded identifier (the canonical step above), never by name.
    if v.is_document_kind(str(incoming.kind)):
        return None, ""
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
    if (v.is_subject(kind) or v.is_person(kind) or kind in never_fuzzy_merge_kinds(v)
            or incoming.canonical_id):
        return None
    # IDN (D-004): a kind whose pack turned name similarity OFF (`similarity_merge: false`) is
    # joined only by the rules a named human approved. A prefix join folds legal forms ("... Ltd"
    # leads "... LLC"), which is exactly the near-name-twin join D-004 forbids for such a kind.
    rule = v.identity_rule_for(kind)
    if rule is not None and not rule.similarity_merge:
        return None
    if v.is_document_kind(kind):
        return None                        # FIN-07: a document is never one with another by name
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


#: `matched_by` values that came from an approved identity rule (FIN-04), not from an id or key.
RULE_MATCHES = frozenset({"business_key", "exact_name", "absorbed"})

#: The attribute a survivor carries listing the record ids an identity rule merged into it. Its
#: pointer is a derivation over the absorbed record's own pointer, so it is grounded like any value.
MERGED_IDS_ATTR = "merged_record_ids"

_NAME_WORD = re.compile(r"[^\w]+", re.UNICODE)


def normalise_legal_name(name: object) -> str:
    """The EXACT-name key (FIN-04): Unicode-folded, case-folded, punctuation and whitespace
    collapsed. Nothing else: the legal form is KEPT ("… LLC" is not "… Ltd"), no token is dropped
    or reordered, and no similarity is involved. "Pte. Ltd." and "PTE LTD" are one spelling."""
    text = unicodedata.normalize("NFKC", str(name or "")).casefold().replace("_", " ")
    return " ".join(_NAME_WORD.sub(" ", text).split())


def _key_value(value: object) -> str:
    """A business-key value as compared: exact, but blind to case and surrounding whitespace."""
    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip().casefold()


def approved_rule_types(rule: IdentityRule, vocab: Vocabulary) -> frozenset[str]:
    """The rule types a named human has approved for ``rule.kind`` in this deployment. Empty when
    none is approved (the default): a declared rule merges nothing on its own. Fails CLOSED: an
    unreadable state file raises rather than reading as 'nothing approved' or 'all approved'."""
    from .ontology_state import approved_identity_rules                # noqa: PLC0415

    approved = approved_identity_rules(vocab.packs)
    return frozenset(t for t in rule.rule_types if (rule.kind, t) in approved)


def _business_keys(node: GraphNode, rule: IdentityRule) -> dict[str, str]:
    """The node's declared business keys, each grounded by its OWN pointer, normalised."""
    out = {}
    for key in rule.business_keys:
        value = _key_value(node.attrs.get(key))
        if value and key in node.attr_sources:
            out[key] = value
    return out


def _keys_conflict(a: dict[str, str], b: dict[str, str]) -> bool:
    """Two records that each carry the SAME business key with DIFFERENT values are two
    organisations, whatever their names say (the near-name twins of a register)."""
    return any(k in b and b[k] != v for k, v in a.items())


def _open_of_kind(store: GraphStore, kind: str, where: str = "", params: tuple = ()):
    return store.fetchall(
        "SELECT * FROM nodes WHERE kind = ? AND system_to IS NULL AND valid_to IS NULL "
        f"{where} ORDER BY rowid_pk", (kind, *params))


def _rule_match(store: GraphStore, incoming: GraphNode, rule: IdentityRule,
                approved: frozenset[str]):
    """(row, how) for the ONE open record an approved rule says ``incoming`` is, else (None, "").
    Two or more matching records is ambiguous and matches nothing."""
    kind = str(incoming.kind)
    mine = _business_keys(incoming, rule)
    if "business_key" in approved:
        for key, value in mine.items():
            rows = [r for r in _open_of_kind(
                        store, kind, "AND instr(lower(attrs_json), ?) > 0", (value,))
                    if r["id"] != incoming.id]
            hits = [r for r in rows
                    if _business_keys(row_to_node(store, r), rule).get(key) == value]
            if len({r["id"] for r in hits}) == 1:
                return hits[0], "business_key"
            if hits:
                return None, ""
    if "exact_name" in approved:
        want = normalise_legal_name(incoming.label)
        if want:
            first = want.split()[0]
            hits = []
            for r in _open_of_kind(store, kind, "AND instr(lower(label), ?) > 0", (first,)):
                if r["id"] == incoming.id or normalise_legal_name(r["label"]) != want:
                    continue
                if _keys_conflict(mine, _business_keys(row_to_node(store, r), rule)):
                    continue
                hits.append(r)
            if len({r["id"] for r in hits}) == 1:
                return hits[0], "exact_name"
    return None, ""


def _absorbed_into(store: GraphStore, incoming: GraphNode):
    """The ONE open record of ``incoming``'s kind that lists ``incoming.id`` among the records an
    identity rule merged into it, else None (two would be ambiguous: None)."""
    hits = []
    for r in _open_of_kind(store, str(incoming.kind), "AND instr(attrs_json, ?) > 0",
                           (MERGED_IDS_ATTR,)):
        try:
            merged = json.loads(r["attrs_json"] or "{}").get(MERGED_IDS_ATTR) or []
        except ValueError:
            continue
        if incoming.id in merged:
            hits.append(r)
    return hits[0] if len(hits) == 1 else None


@dataclass
class ConsolidationReport:
    """What `consolidate_identity` did, per kind: every merge (survivor, absorbed records, the
    rule types that joined them) and every group it REFUSED, with the reason."""
    merged: list[dict] = field(default_factory=list)
    refused: list[dict] = field(default_factory=list)
    kinds: dict[str, list[str]] = field(default_factory=dict)

    def as_record(self) -> dict:
        return {"merged": self.merged, "refused": self.refused, "kinds": self.kinds}


def consolidate_identity(store: GraphStore, vocab: Optional[Vocabulary] = None, *,
                         approved: Optional[dict[str, frozenset[str]]] = None
                         ) -> ConsolidationReport:
    """Apply the APPROVED identity rules (FIN-04) to the records already in the store: every group
    of open records of one rule-bearing kind that an approved rule says is one organisation
    becomes ONE node. Run it after a rule is approved, and after an ingest run.

    ``approved`` maps kind -> approved rule types; omitted, it is read from the ontology state
    (`ontology_state.approved_identity_rules`), which is the only place approvals live.

    What it will not do:
      * touch a subject kind — `Vocabulary.identity_rule_for` has no rule for one, so a person is
        never merged (CLAUDE.md hard constraint 1);
      * join two records that carry the same business key with different values, however equal
        their names (a name shared by two registered companies merges nothing in that group);
      * re-point an attested determination edge — a record carrying one is refused, listed.

    The survivor is the record with a canonical id and the most business keys (then the oldest);
    absorbed records are CLOSED, never deleted (ADR-0003), their open edges re-pointed, their
    attributes the survivor lacks carried over with their own pointers, and their ids listed in
    the survivor's `merged_record_ids`, so a later sighting of one re-sights the survivor.
    """
    from .meta import ATTESTED_META_KINDS                               # noqa: PLC0415

    v = vocab if vocab is not None else active_vocabulary()
    report = ConsolidationReport()
    for kind, rule in sorted(v.identity_rules.items()):
        if v.is_subject(kind) or v.is_person(kind):   # unreachable by construction; a wall anyway
            continue
        types = (approved or {}).get(kind) if approved is not None else approved_rule_types(rule, v)
        types = frozenset(t for t in (types or ()) if t in rule.rule_types)
        report.kinds[kind] = sorted(types)
        if not types:
            continue
        rows = _open_of_kind(store, kind)
        nodes = {r["id"]: row_to_node(store, r) for r in rows}
        rowid = {r["id"]: r["rowid_pk"] for r in rows}
        keys = {i: _business_keys(n, rule) for i, n in nodes.items()}
        parent = {i: i for i in nodes}
        comp_keys = {i: dict(k) for i, k in keys.items()}
        why: dict[str, set] = {i: set() for i in nodes}

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union_all(ids, how):
            roots = sorted({find(i) for i in ids}, key=lambda r: rowid[r])
            merged_keys: dict[str, str] = {}
            for r in roots:
                if _keys_conflict(merged_keys, comp_keys[r]):
                    return False
                merged_keys.update(comp_keys[r])
            head = roots[0]
            for r in roots[1:]:
                parent[r] = head
                why[head] |= why[r]
            comp_keys[head] = merged_keys
            if len(roots) > 1:
                why[head].add(how)
            return True

        if "business_key" in types:
            for key in rule.business_keys:
                groups: dict[str, list[str]] = {}
                for i, k in keys.items():
                    if key in k:
                        groups.setdefault(k[key], []).append(i)
                for value, ids in sorted(groups.items()):
                    if len(ids) > 1 and not union_all(ids, "business_key"):
                        report.refused.append({"kind": kind, "rule": "business_key",
                                               "key": key, "records": sorted(ids),
                                               "reason": "the records carry a different value "
                                                         "for another business key"})
        if "exact_name" in types:
            groups = {}
            for i, n in nodes.items():
                name = normalise_legal_name(n.label)
                if name:
                    groups.setdefault(name, []).append(i)
            for name, ids in sorted(groups.items()):
                if len(ids) > 1 and not union_all(ids, "exact_name"):
                    report.refused.append({"kind": kind, "rule": "exact_name",
                                           "records": sorted(ids),
                                           "reason": "the name is carried by records with "
                                                     "different business keys: two "
                                                     "organisations share it, so none is joined"})
        components: dict[str, list[str]] = {}
        for i in nodes:
            components.setdefault(find(i), []).append(i)
        for root, ids in sorted(components.items()):
            if len(ids) < 2:
                continue
            attested = [i for i in ids for e in store.edges_touching(i)
                        if e["system_to"] is None and _edge_is_attested(e, v, ATTESTED_META_KINDS)]
            if attested:
                report.refused.append({"kind": kind, "records": sorted(ids),
                                       "reason": f"{sorted(set(attested))} carry an attested "
                                                 f"determination edge; a rule never re-points "
                                                 f"what a human attested"})
                continue
            survivor = min(ids, key=lambda i: (nodes[i].canonical_id is None,
                                               -len(keys[i]), rowid[i]))
            absorbed = sorted((i for i in ids if i != survivor), key=lambda i: rowid[i])
            _absorb(store, nodes[survivor], [nodes[i] for i in absorbed], rule)
            report.merged.append({"kind": kind, "survivor": survivor, "absorbed": absorbed,
                                  "rules": sorted(why[root])})
    return report


#: How `reconcile_keyless` cites a join: the same rules a mention meets at write time, applied
#: once the keyed record exists, so the graph no longer depends on which source was read first.
RECONCILE_DERIVATION = ("joined after its keyed record arrived, by the rule the mention would have "
                        "met had the record been there when it was read ({how}); a candidate "
                        "join by name, not by key")


def _pointer_leaf_kinds(p: SourcePointer, depth: int = 0) -> set[str]:
    if p.kind == "derived" and depth < 8:
        out: set[str] = set()
        for q in p.derived_from or ():
            out |= _pointer_leaf_kinds(q, depth + 1)
        return out
    return {str(p.kind)}


def _is_prose_mention(node: GraphNode) -> bool:
    """Every pointer grounding the node itself is a document span (directly or under a
    derivation): a name read from prose, not a row a source declared."""
    leaves: set[str] = set()
    for p in node.sources:
        leaves |= _pointer_leaf_kinds(p)
    return leaves == {"doc_span"}


def reconcile_keyless(store: GraphStore, vocab: Optional[Vocabulary] = None) -> ConsolidationReport:
    """IDN (E2E run 5 W-02): make cross-source joins independent of the ORDER sources were read.

    A document read before the register it names leaves keyless mention nodes ("Tamsinbay Water
    Authority", "Corriedale") that the register's keyed rows, written `id_only`, never meet: the
    join the mention would have made at write time (`resolve_existing`'s name-similarity step, or
    `name_prefix_join`) had nothing keyed to make it against. Run 4 read the database first and
    joined; run 5 read the documents first and did not. This applies those SAME rules now, to
    every keyless mention, against the keyed records now present, and absorbs the mention into
    the ONE record they select (`_absorb`: closed, never deleted; edges re-pointed; the mention's
    id listed in `merged_record_ids`, so a later sighting re-finds the record; its spans cited as
    derived pointers naming the surface form).

    What it will not do:
      * touch a subject or person kind (hard constraint 1) or a document kind (FIN-07);
      * apply a rule the pack turned off: a kind whose identity rule says
        `similarity_merge: false` is joined only by `consolidate_identity`'s APPROVED rules;
      * absorb a table row (only a node every pointer of which is a document span) or join a
        mention to a record without a declared key;
      * choose: a mention two records score equally for, or that the two rules send to different
        records, is listed as refused and left alone;
      * re-point an attested determination edge (the mention is refused, listed).
    """
    from .meta import ATTESTED_META_KINDS                               # noqa: PLC0415

    v = vocab if vocab is not None else active_vocabulary()
    report = ConsolidationReport()
    kinds = sorted({r["kind"] for r in store.fetchall(
        "SELECT DISTINCT kind FROM nodes WHERE system_to IS NULL AND valid_to IS NULL "
        "AND canonical_id IS NULL")})
    for kind in kinds:
        if (kind not in v.node_kinds or v.is_subject(kind) or v.is_person(kind)
                or kind in never_fuzzy_merge_kinds(v) or v.is_document_kind(kind)):
            continue
        rule = v.identity_rule_for(kind)
        if rule is not None and not rule.similarity_merge:
            continue
        keyed = _open_of_kind(store, kind, "AND canonical_id IS NOT NULL")
        if not keyed:
            continue
        index: dict[str, set[int]] = {}
        for i, r in enumerate(keyed):
            for t in _tokens(r["label"] or ""):
                index.setdefault(t, set()).add(i)
        joined: dict[str, list[GraphNode]] = {}
        survivors: dict[str, GraphNode] = {}
        for r in _open_of_kind(store, kind, "AND canonical_id IS NULL"):
            node = row_to_node(store, r)
            if not _is_prose_mention(node):
                continue
            targets: dict[str, str] = {}
            # the similarity step, exactly as `resolve_existing` scores it, over keyed records
            pool = set().union(*(index.get(t, set()) for t in _tokens(node.label or "")))
            best, best_ids = 0.0, set()
            for i in pool:
                score = token_set_ratio(node.label, keyed[i]["label"])
                if score > best:
                    best, best_ids = score, {keyed[i]["id"]}
                elif score == best:
                    best_ids.add(keyed[i]["id"])
            if best >= FUZZY_THRESHOLD:
                if len(best_ids) > 1:
                    report.refused.append({"kind": kind, "records": sorted([node.id, *best_ids]),
                                           "rule": "similarity", "reason": "the mention scores "
                                           "equally for more than one keyed record; none chosen"})
                    continue
                targets[next(iter(best_ids))] = "name similarity"
            hit = name_prefix_join(store, node, v)
            if hit is not None:
                targets.setdefault(hit.row["id"], "leading words of the record's own name")
            if len(targets) != 1:
                if len(targets) > 1:
                    report.refused.append({"kind": kind, "records": sorted([node.id, *targets]),
                                           "rule": "similarity/prefix", "reason": "the rules "
                                           "select different records; none chosen"})
                continue
            if any(e["system_to"] is None and _edge_is_attested(e, v, ATTESTED_META_KINDS)
                   for e in store.edges_touching(node.id)):
                report.refused.append({"kind": kind, "records": [node.id],
                                       "reason": "carries an attested determination edge; a "
                                                 "join never re-points what a human attested"})
                continue
            target, how = next(iter(targets.items()))
            if target not in survivors:
                survivors[target] = row_to_node(store, store.open_node_row(target))
            joined.setdefault(target, []).append((node, how))
        for target, pairs in sorted(joined.items()):
            hows = sorted({h for _, h in pairs})
            _absorb(store, survivors[target], [n for n, _ in pairs],
                    derivation=RECONCILE_DERIVATION.format(how="; ".join(hows)),
                    cite_as_mentions=True)
            report.merged.append({"kind": kind, "survivor": target,
                                  "absorbed": [n.id for n, _ in pairs],
                                  "rules": hows, "status": "candidate_join",
                                  "reverse": "a person restores the absorbed mention (it is "
                                             "closed, not deleted); its spans are unchanged"})
        report.kinds[kind] = ["similarity", "prefix"]
    return report


def _edge_is_attested(row, vocab: Vocabulary, attested_metas) -> bool:
    decl = vocab.edge_kinds.get(str(row["kind"]))
    return decl is not None and decl.binds_to in attested_metas


def _absorb(store: GraphStore, survivor: GraphNode, absorbed: list[GraphNode],
            rule: Optional[IdentityRule] = None, *, derivation: Optional[str] = None,
            cite_as_mentions: bool = False) -> None:
    """Close each absorbed record, re-point its open edges at the survivor, and open ONE new
    version of the survivor carrying their pointers and the attributes it lacked.

    ``cite_as_mentions`` (IDN, `reconcile_keyless`): an absorbed MENTION's spans say its surface
    form, not the survivor's name, so each is cited as a `derived` pointer naming both ("mention
    of <label>: the text says <surface>"), exactly as a write-time join cites them (E2E run-2
    N-17): no node shows a label that a pointer beside it does not hold."""
    from .store import row_to_edge                                      # noqa: PLC0415

    if derivation is None:
        assert rule is not None, "an absorption names the rule or the derivation behind it"
        derivation = (f"identity rule for {rule.kind!r} declared by pack {rule.pack!r} and "
                      f"approved by a named human (ontology state): these records are one record")

    at = store.now()
    floor = max([survivor.system_from, *(n.system_from for n in absorbed)])
    if at <= floor:
        at = floor + timedelta(microseconds=1)
    attrs = dict(survivor.attrs)
    attr_sources = dict(survivor.attr_sources)
    sources = tuple(survivor.sources)
    merged_ids = list(attrs.get(MERGED_IDS_ATTR) or [])
    basis = []
    for node in absorbed:
        for key, value in node.attrs.items():
            if key == MERGED_IDS_ATTR or value is None or key in attrs or attrs.get(key) is not None:
                continue
            if key in node.attr_sources:
                attrs[key] = value
                attr_sources[key] = node.attr_sources[key]
        node_ptrs = tuple(node.sources)
        if cite_as_mentions and (normalise_legal_name(node.label)
                                 != normalise_legal_name(survivor.label)):
            node_ptrs = tuple(SourcePointer(
                source_id=p.source_id, kind="derived", derived_from=[p], trust=p.trust,
                derivation=(f"mention of {survivor.label!r} (key {survivor.canonical_id!r}): "
                            f"the text says {node.label!r}; {derivation}"))
                for p in node.sources)
        sources = sources + _new_pointers(sources, node_ptrs)
        for mid in [node.id, *(node.attrs.get(MERGED_IDS_ATTR) or [])]:
            if mid not in merged_ids and mid != survivor.id:
                merged_ids.append(mid)
        basis.append(node.sources[0])
    prior = survivor.attr_sources.get(MERGED_IDS_ATTR)
    if prior is not None:
        basis.insert(0, prior)
    attrs[MERGED_IDS_ATTR] = merged_ids
    attr_sources[MERGED_IDS_ATTR] = SourcePointer(
        source_id=basis[0].source_id, kind="derived", derived_from=basis,
        derivation=derivation, trust=basis[0].trust)
    merged = survivor.model_copy(update={"attrs": attrs, "attr_sources": attr_sources,
                                         "sources": sources, "system_from": at})
    closed = store.replace_node(merged, at=at)
    assert closed == 1, f"consolidation expected one open version of {survivor.id!r}"
    for node in absorbed:
        for row in store.edges_touching(node.id):
            if row["system_to"] is not None:
                continue
            edge = row_to_edge(store, row)
            src = survivor.id if edge.src_id == node.id else edge.src_id
            dst = survivor.id if edge.dst_id == node.id else edge.dst_id
            store.close_edge(edge.id, at=at)
            upsert_edge(store, edge.model_copy(update={
                "id": f"{src}|{edge.kind.value}|{dst}", "src_id": src, "dst_id": dst,
                "system_from": at}))
        store.close_node(node.id, at=at)


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
    if matched_by in RULE_MATCHES:
        # FIN-04: a record recognised by an approved identity rule is ANOTHER record of the same
        # organisation, not a new statement of the survivor's identity. The survivor keeps its own
        # canonical id and label (with their pointers): the incoming record's are still on file in
        # its own pointers, which are appended below.
        incoming = incoming.model_copy(update={
            "canonical_id": existing.canonical_id, "label": existing.label,
            "attr_sources": {k: p for k, p in incoming.attr_sources.items()
                             if k not in IDENTITY_BEARING_FIELDS}})
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

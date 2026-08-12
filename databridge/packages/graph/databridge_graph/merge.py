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

from databridge_core import SourcePointer

from .schema import EdgeKind, GraphEdge, GraphNode, NodeKind
from .store import GraphStore, row_to_node

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
    matched_by: str = ""     # "canonical" | "fuzzy" | ""


# Kinds for which NAME SIMILARITY MUST NEVER MERGE TWO NODES.
#
# Merging by name is right for organisations — "Coral Harbour Pte Ltd" and "Coral Harbour Pte
# Limited" are one employer. It is catastrophic for patients. The registry holds THREE patients
# named "Loh Wei Ming" and two named "Devi Hui Min"; fuzzy-merging them collapses different people
# into one node, and merging an extracted document mention into a registry row is the machine
# deciding whose record this is — precisely the determination that is reserved for a human, in
# person (CLAUDE.md §Hard constraints 1; ADR-0009).
#
# A PATIENT therefore merges ONLY on an exact canonical_id (the NRIC/FIN/passport, or the derived
# patient_id). Absent that, a new node is inserted and the duplicate is visible — which is the
# safe direction: two nodes staff can tell apart beats one node that silently fused two people.
NEVER_FUZZY_MERGE = frozenset({NodeKind.PATIENT})


def _resolve_existing(store: GraphStore, incoming: GraphNode):
    """Return (open_row, matched_by) for the existing node the incoming one resolves to, else
    (None, "")."""
    # 1) exact canonical match (strongest signal — an NRIC or an issuer code IS the identity)
    if incoming.canonical_id:
        row = store.find_by_canonical(incoming.kind, incoming.canonical_id)
        if row is not None:
            return row, "canonical"
    if incoming.kind in NEVER_FUZZY_MERGE:
        return None, ""
    # 2) fuzzy name+kind fallback within the same kind
    best, best_score = None, 0.0
    for row in store.open_nodes_of_kind(incoming.kind):
        # a different confirmed canonical_id on both sides means DIFFERENT entities — never fuzzy-merge
        if incoming.canonical_id and row["canonical_id"] and row["canonical_id"] != incoming.canonical_id:
            continue
        score = token_set_ratio(incoming.label, row["label"])
        # strict '>' keeps the FIRST (lowest rowid) candidate on ties → deterministic merge target
        if score > best_score:
            best, best_score = row, score
    if best is not None and best_score >= FUZZY_THRESHOLD:
        return best, "fuzzy"
    return None, ""


def upsert_node(store: GraphStore, incoming: GraphNode) -> UpsertOutcome:
    """Idempotent: re-applying the same node CORROBORATES (no duplicate version)."""
    if not incoming.sources:                                  # P4 (also enforced by validator)
        raise ValueError("upsert_node REFUSED: ungrounded node")
    row, matched_by = _resolve_existing(store, incoming)
    if row is None:
        store.add_node(incoming)
        return UpsertOutcome(id=incoming.id, action="inserted")

    existing = row_to_node(store, row)
    if not _conflicts(existing, incoming):
        # corroboration: append only NEW pointers (idempotent — same pointer twice is a no-op).
        # The existing (possibly richer) node is preserved — an extracted MENTION of a seed entity
        # (attrs={}) must NOT clobber the seed's role/email/valid_from (that would wipe the demo's
        # provenance view). Only a real fact conflict supersedes (below).
        new_ptrs = _new_pointers(existing.sources, incoming.sources)
        store.append_sources(existing.id, new_ptrs)
        return UpsertOutcome(id=existing.id, action="corroborated", matched_by=matched_by)

    # facts changed → supersede (ADR-0003): close the prior open version, open a new one that
    # KEEPS the resolved id (so edges keep pointing at it) and unions provenance. ``at`` is forced
    # strictly AFTER the prior version's system_from so the superseded version has a NON-zero
    # system interval (otherwise [S, S) is unqueryable — "what we believed before" vanishes).
    at = store.now()
    if at <= existing.system_from:
        at = existing.system_from + timedelta(microseconds=1)
    merged = incoming.model_copy(update={
        "id": existing.id,
        "canonical_id": incoming.canonical_id or existing.canonical_id,
        "sources": existing.sources + _new_pointers(existing.sources, incoming.sources),
        "system_from": at,
    })
    closed = store.replace_node(merged, at=at, valid_to=incoming.valid_from)   # ATOMIC close+insert
    assert closed == 1, f"supersede expected exactly one open version of {existing.id!r}, closed {closed}"
    return UpsertOutcome(id=existing.id, action="superseded", matched_by=matched_by)


def _canon_val(v) -> str:
    # canonicalise one attr value (existing comes back from json.loads → a date is already a str;
    # incoming is native) so comparison is roundtrip-stable, never an endless false "changed" loop.
    return json.dumps(v, sort_keys=True, default=str)


def _conflicts(existing: GraphNode, incoming: GraphNode) -> bool:
    """A REAL fact conflict (→ supersede): a different label, or a shared attr key whose value
    changed. An incoming node carrying FEWER attrs (an extracted mention of a richer seed node) is
    NOT a conflict — it corroborates, preserving the existing node's attrs/valid_from. A genuinely
    changed value (role VP→CEO) still supersedes (ADR-0003 invalidate-don't-delete)."""
    if existing.label != incoming.label:
        return True
    return any(k in existing.attrs and _canon_val(existing.attrs[k]) != _canon_val(v)
               for k, v in incoming.attrs.items())


def _new_pointers(existing: list[SourcePointer], incoming: list[SourcePointer]) -> list[SourcePointer]:
    seen = {sp.model_dump_json() for sp in existing}
    out, added = [], set()
    for sp in incoming:
        key = sp.model_dump_json()
        if key not in seen and key not in added:
            out.append(sp)
            added.add(key)
    return out


def upsert_edge(store: GraphStore, incoming: GraphEdge) -> UpsertOutcome:
    """Edge upsert: same (src, dst, kind) open edge ⇒ corroborate; else insert. (Conflict
    superseding for attr-valued edges is the §7 newest-wins rule; the demo edges are
    relationship-existence facts, so corroboration is the active path.)"""
    if not incoming.sources:
        raise ValueError("upsert_edge REFUSED: ungrounded edge")
    for row in store.open_edges():
        if (row["src_id"] == incoming.src_id and row["dst_id"] == incoming.dst_id
                and row["kind"] == incoming.kind.value):
            existing_sources = store.sources_for(row["id"])
            new_ptrs = _new_pointers(existing_sources, incoming.sources)
            store.append_sources(row["id"], new_ptrs)
            return UpsertOutcome(id=row["id"], action="corroborated", matched_by="triple")
    store.add_edge(incoming)
    return UpsertOutcome(id=incoming.id, action="inserted")

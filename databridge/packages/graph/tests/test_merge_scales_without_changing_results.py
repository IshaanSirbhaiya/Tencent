"""merge.py performance rewrite (2026-08-16) — must not change behaviour, only speed.

Both hot paths in `merge.py` used to materialise a full Python-side scan on EVERY insert:

  * `upsert_edge` did `for row in store.open_edges(): ...` — every OPEN edge, on every edge
    upsert. At estate scale (52,747 edges) that is O(n^2): ~1.4 BILLION row comparisons, and a
    full `load_government_seed` run reached 38,000/46,346 nodes in 35 minutes and never started
    on edges.
  * `_resolve_existing`'s fuzzy fallback (step 2) did `for row in store.open_nodes_of_kind(kind):
    ...` — every open node of that kind, on every node upsert with no id/canonical match.

The fix (`GraphStore.find_open_edge`, backed by the partial index `ix_edges_open_triple`, and
`GraphStore.open_node_candidates_of_kind` + `node_row_by_rowid`, a lean projection of the same
candidate set) is a SPEED change. This file is the proof it is not also a BEHAVIOUR change:

  * `test_*_equivalence_old_vs_new` replays an identical, non-trivial sequence of node/edge
    upserts through the OLD (pre-rewrite, linear-scan) resolution logic — reproduced verbatim
    below from the working tree as it stood before the rewrite, reusing every helper the rewrite
    did NOT touch (`_conflicts`, `_new_pointers`, `row_to_node`, `never_fuzzy_merge_kinds`,
    `token_set_ratio`) — and through the current `merge.upsert_node` / `merge.upsert_edge`, on two
    independent stores under an identical injected clock, and asserts the two stores end up
    byte-for-byte identical: same node/edge/source rows, same `UpsertOutcome` at every step.
  * `test_find_open_edge_uses_an_index` pins the index via `EXPLAIN QUERY PLAN`.
  * `test_edge_upsert_at_scale_completes_quickly` (marked `slow`) is the regression trap: enough
    edges that the OLD O(n^2) scan would visibly not finish, asserting the CURRENT code finishes
    inside a generous bound.
"""
from __future__ import annotations

import datetime as dt
import time

import pytest

from databridge_core import SourcePointer
from databridge_graph import (
    EdgeKind,
    GraphEdge,
    GraphNode,
    GraphStore,
    NodeKind,
    UpsertOutcome,
    row_to_node,
    upsert_edge,
    upsert_node,
)
from databridge_graph.merge import (
    FUZZY_THRESHOLD,
    _conflicts,
    _new_pointers,
    never_fuzzy_merge_kinds,
    token_set_ratio,
)
from databridge_graph.packs import active_vocabulary
from databridge_graph.schema import node_id

_UTC = dt.timezone.utc
_T0 = dt.datetime(2026, 1, 1, tzinfo=_UTC)
_LEARN = dt.datetime(2026, 8, 1, tzinfo=_UTC)


def _ptr(src="d"):
    return SourcePointer(source_id=src, kind="doc_span", document_id=src, char_start=0, char_end=5)


def _store():
    # A CONSTANT clock, shared by both stores under comparison: `upsert_node`'s supersede branch
    # calls `store.now()`, and the equivalence assertion compares `system_from`/`system_to`
    # verbatim, so both runs must see the exact same clock values at the exact same points in the
    # sequence (which they do, being pure functions of "how many supersedes has this id had").
    return GraphStore(clock=lambda: _LEARN)


def _org(label, ident, canonical=None, attrs=None, src="d"):
    attrs = attrs or {}
    return GraphNode(id=node_id(NodeKind.ORG, ident), kind=NodeKind.ORG, label=label,
                     canonical_id=canonical, attrs=attrs,
                     attr_sources={k: _ptr(src) for k, v in attrs.items() if v is not None},
                     sources=[_ptr(src)], valid_from=_T0, system_from=_LEARN)


def _patient(label, ident, canonical=None, grounded=True, src="d"):
    # PATIENT is `healthcare`'s subject kind (never_fuzzy_merge, spine D4b). `grounded=False`
    # exercises the A4 rule: an un-cited canonical_id must not be allowed to drive an exact merge.
    attr_sources = {"canonical_id": _ptr(src)} if (canonical and grounded) else {}
    return GraphNode(id=node_id(NodeKind.PATIENT, ident), kind=NodeKind.PATIENT, label=label,
                     canonical_id=canonical, attr_sources=attr_sources,
                     sources=[_ptr(src)], valid_from=_T0, system_from=_LEARN)


def _edge(ident, src_id, dst_id, kind, src="d"):
    return GraphEdge(id=f"edge:{ident}", src_id=src_id, dst_id=dst_id, kind=kind,
                     sources=[_ptr(src)], valid_from=_T0, system_from=_LEARN)


# --- the OLD (pre-2026-08-16) resolution logic, reproduced verbatim -------------------------
# Exactly what `_resolve_existing` step 2 and `upsert_edge` did before the index/lean-projection
# rewrite: `store.open_nodes_of_kind(...)` / `store.open_edges()`, scanned in a Python `for`. Every
# OTHER branch (id match, exact canonical, the A4/A5 subject rules, conflict/supersede/corroborate)
# is untouched by the rewrite, so it is reused directly from `merge.py` rather than re-typed here —
# re-typing it would let this "old" reference quietly drift from what was actually shipped.
def _resolve_existing_old(store, incoming, vocab=None):
    v = vocab if vocab is not None else active_vocabulary()
    is_subject = v.is_subject(str(incoming.kind)) or v.is_person(str(incoming.kind))
    same = store.open_node_row(incoming.id)
    if same is not None:
        return same, "id"
    if is_subject:
        # R-029 Codex round 1 (IDN): a person record is never joined to another, by any key
        return None, ""
    if incoming.canonical_id and is_subject and not incoming.identity_field_is_grounded(
            "canonical_id"):
        return None, ""
    if incoming.canonical_id:
        row = store.find_by_canonical(incoming.kind, incoming.canonical_id)
        if row is not None:
            return row, "canonical"
    if str(incoming.kind) in never_fuzzy_merge_kinds(v):
        return None, ""
    best, best_score = None, 0.0
    for row in store.open_nodes_of_kind(incoming.kind):        # <-- the O(n) scan being replaced
        if incoming.canonical_id and row["canonical_id"] and row["canonical_id"] != incoming.canonical_id:
            continue
        score = token_set_ratio(incoming.label, row["label"])
        if score > best_score:
            best, best_score = row, score
    if best is not None and best_score >= FUZZY_THRESHOLD:
        return best, "fuzzy"
    return None, ""


def _upsert_node_old(store, incoming, vocab=None):
    if not incoming.sources:
        raise ValueError("upsert_node REFUSED: ungrounded node")
    v = vocab if vocab is not None else active_vocabulary()
    subject = v.is_subject(str(incoming.kind)) or v.is_person(str(incoming.kind))
    row, matched_by = _resolve_existing_old(store, incoming, v)
    if row is None:
        store.add_node(incoming)
        return UpsertOutcome(id=incoming.id, action="inserted", requires_human_selection=subject)

    existing = row_to_node(store, row)
    if not _conflicts(existing, incoming):
        new_ptrs = _new_pointers(existing.sources, incoming.sources)
        store.append_sources(existing.id, new_ptrs)
        return UpsertOutcome(id=existing.id, action="corroborated", matched_by=matched_by,
                             requires_human_selection=subject)

    at = store.now()
    if at <= existing.system_from:
        at = existing.system_from + dt.timedelta(microseconds=1)
    merged = incoming.model_copy(update={
        "id": existing.id,
        "canonical_id": incoming.canonical_id or existing.canonical_id,
        "sources": tuple(existing.sources) + _new_pointers(existing.sources, incoming.sources),
        "system_from": at,
    })
    closed = store.replace_node(merged, at=at, valid_to=incoming.valid_from)
    assert closed == 1
    return UpsertOutcome(id=existing.id, action="superseded", matched_by=matched_by,
                         requires_human_selection=subject)


def _upsert_edge_old(store, incoming):
    if not incoming.sources:
        raise ValueError("upsert_edge REFUSED: ungrounded edge")
    for row in store.open_edges():                             # <-- the O(n) scan being replaced
        if (row["src_id"] == incoming.src_id and row["dst_id"] == incoming.dst_id
                and row["kind"] == incoming.kind.value):
            existing_sources = store.sources_for(row["id"])
            new_ptrs = _new_pointers(existing_sources, incoming.sources)
            store.append_sources(row["id"], new_ptrs)
            return UpsertOutcome(id=row["id"], action="corroborated", matched_by="triple")
    store.add_edge(incoming)
    return UpsertOutcome(id=incoming.id, action="inserted")


# --- a non-trivial, deterministic sequence exercising every branch --------------------------
def _node_sequence():
    """A fixed list of `GraphNode`s, applied in order. Exercises: fresh insert, fuzzy corroborate
    (org, no canonical), exact-canonical corroborate, a real conflict (supersede), an idempotent
    re-apply of the identical object (id match), distinct canonical ids that must NOT fuzzy-merge,
    and a subject kind (patient) whose look-alike labels must stay two nodes — grounded and
    ungrounded canonical_id both included, per the A4 rule."""
    coral1 = _org("Coral Harbour Pte Ltd", "coral-1", src="doc1")
    coral2 = _org("Coral Harbour Pte Limited", "coral-2", src="doc2")       # fuzzy -> coral1
    coral3 = _org("CORAL HARBOUR PTE LTD", "coral-3", src="doc3")           # fuzzy -> coral1
    everwell_a = _org("Everwell Insurance Group", "ew-a", canonical="EVWPA", src="doc4")
    everwell_b = _org("Everwell Insurance Group", "ew-b", canonical="EVWME", src="doc5")  # distinct payer
    everwell_a2 = _org("Everwell Insurance Group", "ew-a2", canonical="EVWPA", src="doc6")  # canonical -> everwell_a
    northstar = _org("Northstar Life Assurance", "ns-1", attrs={"tier": "gold"}, src="doc7")
    northstar_changed = _org("Northstar Life Assurance", "ns-2",
                             attrs={"tier": "platinum"}, src="doc8")         # fuzzy match, real conflict -> supersede
    meridian = _org("Meridian Life", "mer-1", src="doc9")
    northwind = _org("Northstar Life", "nw-1", src="doc10")                 # only "Life" overlaps -> distinct
    bank = _org("Bank", "bank-1", src="doc11")
    bank_of_america = _org("Bank of America", "boa-1", src="doc12")         # subset -> distinct
    patient_a1 = _patient("Loh Wei Ming", "p-a1", src="doc13")
    patient_a2 = _patient("Loh Wei Ming", "p-a2", src="doc14")              # same label, subject -> distinct
    patient_b1 = _patient("Devi Hui Min", "p-b1", canonical="S1234567A",
                          grounded=True, src="doc15")
    patient_b2 = _patient("Devi Hui Min", "p-b2", canonical="S1234567A",
                          grounded=True, src="doc16")                       # grounded canonical -> STILL distinct (R-029)
    patient_c1 = _patient("Ravi Kumar", "p-c1", canonical="FAKE999X",
                          grounded=False, src="doc17")                      # ungrounded canonical -> A4, distinct
    patient_c2 = _patient("Ravi Kumar", "p-c2", canonical="FAKE999X",
                          grounded=False, src="doc18")
    return [
        coral1, coral2, coral3,
        everwell_a, everwell_b, everwell_a2,
        northstar, northstar_changed,
        meridian, northwind,
        bank, bank_of_america,
        patient_a1, patient_a2,
        patient_b1, patient_b2,
        patient_c1, patient_c2,
        coral1,                                                            # exact re-apply -> id match
    ]


def _edge_sequence(nodes_by_ident):
    n = nodes_by_ident
    triples = [
        (n["p-a1"].id, n["coral-1"].id, EdgeKind.EMPLOYED_BY),
        (n["p-a2"].id, n["coral-1"].id, EdgeKind.EMPLOYED_BY),
        (n["p-b1"].id, n["ew-a"].id, EdgeKind.EMPLOYED_BY),
        (n["coral-1"].id, n["ns-1"].id, EdgeKind.REFERENCES),
        (n["coral-1"].id, n["ns-1"].id, EdgeKind.REFERENCES),              # duplicate triple -> corroborate
        (n["ew-a"].id, n["mer-1"].id, EdgeKind.REFERENCES),
        (n["mer-1"].id, n["ew-a"].id, EdgeKind.REFERENCES),                # reversed src/dst -> a DIFFERENT edge
    ]
    return [_edge(i, s, d, k, src=f"edoc{i}") for i, (s, d, k) in enumerate(triples)]


def _sortkey(t):
    """Sort by fields that are NEVER None (id/entity_id + a temporal or content column), so a
    `None` elsewhere in the tuple (canonical_id, valid_to, field, ...) can't blow up `sorted()`'s
    `<` comparison — while the actual equality check below still compares the full tuple, `None`s
    included."""
    return tuple("" if x is None else str(x) for x in t)


def _dump(store: GraphStore):
    """Every row in the store, as plain tuples, sorted — excludes only `rowid_pk` (an incidental
    SQLite autoincrement, not a claim the equivalence test needs to make)."""
    nodes = sorted((
        tuple(r[c] for c in ("id", "kind", "label", "canonical_id", "attrs_json",
                             "valid_from", "valid_to", "system_from", "system_to"))
        for r in store.fetchall("SELECT * FROM nodes")), key=_sortkey)
    edges = sorted((
        tuple(r[c] for c in ("id", "src_id", "dst_id", "kind", "weight", "attrs_json",
                             "valid_from", "valid_to", "system_from", "system_to",
                             "attestation_json", "attestation_id"))
        for r in store.fetchall("SELECT * FROM edges")), key=_sortkey)
    sources = sorted((
        tuple(r[c] for c in ("entity_id", "field", "version_pk", "source_pointer_json"))
        for r in store.fetchall("SELECT * FROM sources")), key=_sortkey)
    return nodes, edges, sources


def test_node_upsert_equivalence_old_vs_new():
    sequence = _node_sequence()

    old_store, new_store = _store(), _store()
    old_outcomes = [_upsert_node_old(old_store, n) for n in sequence]
    new_outcomes = [upsert_node(new_store, n) for n in sequence]

    assert [(o.id, o.action, o.matched_by, o.requires_human_selection) for o in old_outcomes] == \
           [(o.id, o.action, o.matched_by, o.requires_human_selection) for o in new_outcomes]
    assert _dump(old_store) == _dump(new_store)

    # sanity: the sequence actually exercised every branch it claims to (a passing equivalence
    # test over a sequence that degenerated to all-inserts would prove nothing)
    actions = {o.matched_by for o in new_outcomes}
    assert actions >= {"id", "canonical", "fuzzy", ""}
    # 8 distinct orgs (coral*3->1, everwell_a+a2->1, everwell_b, northstar*2->1 supersede,
    # meridian, northwind, bank, bank_of_america) + 5 distinct patients (a1, a2, b1+b2->1,
    # c1, c2 — a subject NEVER fuzzy-merges) — and, since R-029 Codex round 1, b1 and b2 stay
    # two as well: a grounded identifier proposes a candidate, it never joins two people.
    patients = [n for n in _node_sequence() if active_vocabulary().is_person(str(n.kind))]
    orgs = 8
    assert new_store.counts()["nodes"] == orgs + len({n.id for n in patients}),         "orgs merge (fuzzy/canonical); patients never do"


def test_edge_upsert_equivalence_old_vs_new():
    nodes = _node_sequence()
    by_ident = {n.id.split(":", 1)[1]: n for n in nodes}
    edges = _edge_sequence(by_ident)

    old_store, new_store = _store(), _store()
    for n in nodes:
        _upsert_node_old(old_store, n)
        upsert_node(new_store, n)
    old_outcomes = [_upsert_edge_old(old_store, e) for e in edges]
    new_outcomes = [upsert_edge(new_store, e) for e in edges]

    assert [(o.id, o.action, o.matched_by) for o in old_outcomes] == \
           [(o.id, o.action, o.matched_by) for o in new_outcomes]
    assert _dump(old_store) == _dump(new_store)
    assert {o.action for o in new_outcomes} == {"inserted", "corroborated"}


# --- index pin ---------------------------------------------------------------------------------
def test_find_open_edge_uses_an_index():
    s = _store()
    plan = s.conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM edges WHERE src_id = ? AND dst_id = ? AND kind = ? "
        "AND system_to IS NULL ORDER BY rowid_pk LIMIT 1",
        ("a", "b", "c")).fetchall()
    detail = " | ".join(row["detail"] for row in plan)
    assert "SEARCH edges USING INDEX ix_edges_open_triple" in detail, detail
    assert "SCAN edges" not in detail, detail


def test_open_node_candidates_of_kind_uses_an_index():
    s = _store()
    plan = s.conn.execute(
        "EXPLAIN QUERY PLAN SELECT rowid_pk, label, canonical_id FROM nodes WHERE kind = ? "
        "AND system_to IS NULL ORDER BY rowid_pk", ("org",)).fetchall()
    detail = " | ".join(row["detail"] for row in plan)
    assert "SEARCH nodes USING INDEX ix_nodes_kind_open" in detail, detail
    assert "SCAN nodes" not in detail, detail


def test_open_node_candidates_of_kind_matches_full_projection():
    """The lean candidate projection must return the exact same rows (same set, same order) as
    `open_nodes_of_kind` — only fewer columns. If it ever diverges, the fuzzy match target could
    silently change."""
    s = _store()
    for i, label in enumerate(["Coral Harbour Pte Ltd", "Coral Harbour Pte Limited",
                               "Everwell Insurance Group", "Northstar Life Assurance"]):
        upsert_node(s, _org(label, f"cand-{i}", src=f"doc{i}"))
    full = s.open_nodes_of_kind(NodeKind.ORG)
    lean = s.open_node_candidates_of_kind(NodeKind.ORG)
    assert [r["rowid_pk"] for r in full] == [r["rowid_pk"] for r in lean]
    assert [(r["label"], r["canonical_id"]) for r in full] == \
           [(r["label"], r["canonical_id"]) for r in lean]


# --- the regression trap -------------------------------------------------------------------
@pytest.mark.slow
def test_edge_upsert_at_scale_completes_quickly():
    """Enough edges that the OLD `for row in store.open_edges(): ...` scan is visibly not an
    option: 12,000 distinct edges means the old code's LAST insert alone scans ~12,000 open edges,
    and the sum over the whole run is ~72 MILLION row comparisons in Python — multiple minutes,
    empirically, in this environment. The indexed `find_open_edge` (`ix_edges_open_triple`) turns
    every one of those into an O(log n) search, so the whole run comfortably fits a bound a
    quadratic implementation could never hit at this size."""
    s = _store()
    n_edges = 12_000
    # `upsert_edge` never checks that `src_id`/`dst_id` name a real node for a non-attested kind
    # like REFERENCES (only a DETERMINATION edge is checked against the graph, in
    # `_screen_determination`) — so bare id strings, with no node behind them, exercise exactly
    # the code path under test without spending the run's time budget on 12,000 full `GraphNode`
    # constructions, which is setup cost this test is not about.
    #
    # `src_id = f"org:scale-{i}"` is distinct for every `i`, so the (src, dst) TRIPLE is
    # guaranteed distinct for every one of the 12,000 edges below — this is 12,000 genuine
    # INSERTS, the old code's worst case (every insert scans every already-open edge; none of
    # them is short-circuited by an early corroboration match).
    ids = [f"org:scale-{i}" for i in range(n_edges)]

    t0 = time.perf_counter()
    for i in range(n_edges):
        src_id = ids[i]
        dst_id = ids[(i + 1) % n_edges]
        upsert_edge(s, _edge(f"scale-{i}", src_id, dst_id, EdgeKind.REFERENCES, src=f"sd{i}"))
    elapsed = time.perf_counter() - t0

    assert s.counts()["edges"] == n_edges
    # generous: the indexed version finishes in well under a minute; a quadratic scan at this
    # size would not finish in a reasonable CI timeout at all.
    assert elapsed < 60, f"edge upsert at scale took {elapsed:.1f}s — regressed to a full scan?"

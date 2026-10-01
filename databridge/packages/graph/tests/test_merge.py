"""merge.py — idempotent upsert, canonical/fuzzy resolution, supersession.

The subject of these tests is ORG nodes, deliberately. Entity merging is the right behaviour for
organisations — the clinic's own fixtures print "Coral Harbour Pte Ltd", "CORAL HARBOUR PTE
LIMITED" and "CORAL HARBOUR PTE LTD" across three documents, and they are one employer. It is the
WRONG behaviour for patients, which is why `resolve.py` matches registry names exactly with no
fuzzy tolerance and never merges two people. Testing the merge path on orgs keeps that distinction
visible in the test suite rather than only in prose.
"""
from __future__ import annotations

import datetime as dt

from databridge_core import SourcePointer
from databridge_graph import (
    GraphNode,
    GraphStore,
    NodeKind,
    token_set_ratio,
    upsert_node,
)
from databridge_graph.schema import node_id

_UTC = dt.timezone.utc
_T0 = dt.datetime(2026, 1, 1, tzinfo=_UTC)
_LEARN = dt.datetime(2026, 8, 1, tzinfo=_UTC)

CORAL = "org:coral-harbour"


def _ptr(src="d"):
    return SourcePointer(source_id=src, kind="doc_span", document_id=src, char_start=0, char_end=5)


def _org(label, canonical=None, attrs=None, src="d"):
    # Every attribute gets the same pointer the node is grounded on. A test helper is exactly
    # where the per-attribute wall would otherwise be quietly worked around, so it grounds them
    # properly rather than passing a bag of uncited values.
    return GraphNode(id=node_id(NodeKind.ORG, (canonical or label).lower()),
                     kind=NodeKind.ORG, label=label, canonical_id=canonical,
                     attrs=attrs or {},
                     attr_sources={k: _ptr(src) for k, v in (attrs or {}).items()
                                   if v is not None},
                     sources=[_ptr(src)], valid_from=_T0, system_from=_LEARN)


def _store():
    return GraphStore(clock=lambda: _LEARN)


def test_token_set_ratio_ranks_real_matches_high():
    assert token_set_ratio("Coral Harbour Pte Ltd", "Coral Harbour Pte Limited") >= 0.92
    assert token_set_ratio("Coral Harbour Pte Ltd", "Coral Harbour") >= 0.92
    assert token_set_ratio("Meridian Life", "Northstar Life") < 0.92     # only "Life" overlaps


def test_token_set_ratio_rejects_subset_false_merge():
    # a short name must NOT be absorbed by a longer one that merely contains it (size-aware
    # metric, not rapidfuzz's subset leniency).
    assert token_set_ratio("Bank", "Bank of America") < 0.92
    assert token_set_ratio("Everwell", "Everwell Insurance Group Corporate Solutions") < 0.92


def test_subset_label_does_not_false_merge():
    s = _store()
    upsert_node(s, _org("Everwell"))
    upsert_node(s, _org("Everwell Insurance Group Corporate Solutions"))
    assert s.counts()["nodes"] == 2               # distinct entities kept apart


def test_exact_canonical_corroborates():
    """The same org seen in a second document adds provenance, not a new version.

    `_org` derives the node id from `canonical or label`, so two sightings sharing a canonical id
    also share an id — and since 2026-08-16 `_resolve_existing` matches an equal id FIRST, which
    would report `matched_by="id"` and never reach the canonical branch. The canonical branch is
    what this test is about, so the second sighting is given a DIFFERENT id (the same company
    printed with a different legal suffix, which is exactly how the fixtures spell it) and the
    canonical id is the only thing that can join them. The LABEL is left unchanged, because a
    changed label is a real fact conflict and supersedes rather than corroborating — a different
    property, tested by `test_a_changed_label_supersedes_rather_than_silently_rewriting`.
    """
    s = _store()
    upsert_node(s, _org("Coral Harbour Pte Ltd", "coral-harbour", src="chit_01"))
    second = _org("Coral Harbour Pte Ltd", "coral-harbour", src="chit_03")
    second = second.model_copy(update={"id": "org:coral-harbour-second-sighting"})
    o = upsert_node(s, second)
    assert o.action == "corroborated" and o.matched_by == "canonical"
    assert s.counts()["nodes"] == 1 and s.counts()["sources"] == 2


def test_re_applying_the_very_same_node_corroborates_by_id():
    """Idempotency's floor, and it must not depend on a `canonical_id`.

    `id` is `f"{kind}:{slug}"` and is computed by whoever wrote it, so two upserts of one id are
    two sightings of one record. Before this was checked first, a SUBJECT kind carrying no
    canonical id inserted a duplicate row on EVERY re-apply — the exact-canonical branch had
    nothing to match on and the fuzzy fallback is (correctly) unreachable for a subject, so the
    one operation this module documents as idempotent was not, for the kinds it protects most.
    """
    s = _store()
    node = _org("Some Body", src="chit_01")
    assert upsert_node(s, node).action == "inserted"
    again = upsert_node(s, node)
    assert again.action == "corroborated" and again.matched_by == "id"
    assert s.counts()["nodes"] == 1


def test_a_changed_label_supersedes_rather_than_silently_rewriting():
    """A different printed spelling on the same canonical id is a CHANGED FACT, so it opens a new
    bi-temporal version and closes the old one (ADR-0003, invalidate-don't-delete). "What did this
    document call them?" stays answerable — which is why `clinic_seed` merges the three Coral
    Harbour spellings into one node before upserting rather than upserting each in turn."""
    s = _store()
    upsert_node(s, _org("Coral Harbour Pte Ltd", "coral-harbour", src="chit_01"))
    # INT5 (R-022 build cross-track 2a, deliberate): a spelling is a changed fact only when the
    # incoming label is grounded by a pointer of ITS OWN. Ungrounded, it is one more sighting —
    # the node keeps the label it has, and the sighting's pointer is appended (so "what did this
    # document call them?" is still answered by that pointer). See test_int5_merge.py.
    seen = upsert_node(s, _org("CORAL HARBOUR PTE LTD", "coral-harbour", src="chit_02"))
    assert seen.action == "corroborated"
    assert s.open_node_row(CORAL)["label"] == "Coral Harbour Pte Ltd"
    relabel = _org("CORAL HARBOUR PTE LIMITED", "coral-harbour", src="chit_03")
    relabel = relabel.model_copy(update={"attr_sources": {**relabel.attr_sources,
                                                          "label": _ptr("chit_03")}})
    o = upsert_node(s, relabel)
    assert o.action == "superseded"
    assert len(s.all_node_versions(CORAL)) == 2


def test_canonical_supersede_on_changed_facts():
    s = _store()
    upsert_node(s, _org("Coral Harbour", "coral-harbour", attrs={"kind": "employer"}))
    o = upsert_node(s, _org("Coral Harbour", "coral-harbour", attrs={"kind": "insurer"}))
    assert o.action == "superseded"
    # one OPEN version (the new one), one closed (the old) — invalidate-don't-delete (ADR-0003)
    assert s.counts()["nodes"] == 1
    assert len(s.all_node_versions(CORAL)) == 2
    assert s.open_node_row(CORAL)["attrs_json"].find("insurer") >= 0


def test_supersede_old_version_has_nonzero_system_interval():
    # even under a PINNED clock the superseded version's system interval is non-zero, so "what we
    # believed before the correction" stays queryable.
    s = _store()
    upsert_node(s, _org("Coral Harbour", "coral-harbour", attrs={"kind": "employer"}))
    upsert_node(s, _org("Coral Harbour", "coral-harbour", attrs={"kind": "insurer"}))
    versions = s.all_node_versions(CORAL)
    old = next(v for v in versions if v["system_to"] is not None)
    assert old["system_from"] < old["system_to"]          # strictly non-zero interval


def test_reextraction_corroborates_and_preserves_seed_attrs():
    # re-extracting a seeded org from a second document yields EMPTY attrs and a later valid_from;
    # that must CORROBORATE (preserving the seed's attrs and valid_from), not supersede-and-wipe.
    import json as _json

    s = _store()
    upsert_node(s, _org("Coral Harbour", "coral-harbour",
                        attrs={"kind": "employer", "issuer_code": None}))
    poor = GraphNode(id=node_id(NodeKind.ORG, "coral-harbour"), kind=NodeKind.ORG,
                     label="Coral Harbour", canonical_id="coral-harbour", attrs={},
                     sources=[_ptr("chit_09")],
                     valid_from=dt.datetime(2026, 8, 1, tzinfo=_UTC), system_from=_LEARN)
    o = upsert_node(s, poor)
    assert o.action == "corroborated"
    row = s.open_node_row(CORAL)
    assert _json.loads(row["attrs_json"]) == {"kind": "employer", "issuer_code": None}
    assert row["valid_from"].startswith("2026-01-01")                                 # not bumped
    assert s.counts()["nodes"] == 1 and s.counts()["sources"] == 2                    # grew


def test_idempotent_with_non_json_native_attrs():
    # attrs that aren't JSON-native (a date) must still compare equal on re-upsert, or the node
    # supersedes itself forever.
    import datetime as _dt
    s = _store()
    n = _org("Meridian Life Assurance", "MRDEB", attrs={"since": _dt.date(2026, 1, 1)})
    upsert_node(s, n)
    o = upsert_node(s, n)
    assert o.action == "corroborated" and s.counts()["nodes"] == 1


def test_fuzzy_merge_without_canonical():
    s = _store()
    upsert_node(s, _org("Coral Harbour Pte Ltd"))
    o = upsert_node(s, _org("Coral Harbour Pte Limited"))
    assert o.matched_by == "fuzzy" and s.counts()["nodes"] == 1


def test_distinct_canonical_never_fuzzy_merges():
    """Two issuers with near-identical names but different codes are different payers — billing
    the wrong one is a real-money error, so an explicit canonical id always beats name similarity.
    """
    s = _store()
    upsert_node(s, _org("Everwell Insurance Group", "EVWPA"))
    upsert_node(s, _org("Everwell Insurance Group", "EVWME"))
    assert s.counts()["nodes"] == 2                          # different payers, kept apart


def test_idempotent_reupsert_is_noop_on_count():
    s = _store()
    n = _org("Northstar Life Assurance", "NSTNBU")
    upsert_node(s, n)
    upsert_node(s, n)
    upsert_node(s, n)
    assert s.counts()["nodes"] == 1 and s.counts()["sources"] == 1   # same pointer not duplicated

"""merge.py — idempotent upsert, canonical/fuzzy resolution, supersession (T08 §6/§7)."""
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
_T0 = dt.datetime(2024, 1, 1, tzinfo=_UTC)
_LEARN = dt.datetime(2024, 6, 1, tzinfo=_UTC)


def _ptr(src="d"):
    return SourcePointer(source_id=src, kind="doc_span", document_id=src, char_start=0, char_end=5)


def _person(label, canonical=None, attrs=None, src="d"):
    return GraphNode(id=node_id(NodeKind.PERSON, (canonical or label).lower()),
                     kind=NodeKind.PERSON, label=label, canonical_id=canonical,
                     attrs=attrs or {}, sources=[_ptr(src)], valid_from=_T0, system_from=_LEARN)


def _store():
    return GraphStore(clock=lambda: _LEARN)


def test_token_set_ratio_ranks_real_matches_high():
    assert token_set_ratio("Acme Corp", "Acme Corporation") >= 0.92
    assert token_set_ratio("Acme Corp", "Acme Inc") >= 0.92          # legal suffixes folded
    assert token_set_ratio("Sarah Chen", "David Chen") < 0.92        # only surname overlaps


def test_token_set_ratio_rejects_subset_false_merge():
    # the BLOCKER the review caught: a short name must NOT be absorbed by a longer one that
    # merely contains it (size-aware metric, not rapidfuzz subset-leniency).
    assert token_set_ratio("Bank", "Bank of America") < 0.92
    assert token_set_ratio("Apple", "Apple Computer Systems") < 0.92


def test_subset_label_does_not_false_merge():
    s = _store()
    upsert_node(s, _person("Bank"))               # PERSON kind reused just for the merge path
    upsert_node(s, _person("Bank of America"))
    assert s.counts()["nodes"] == 2               # distinct entities kept apart


def test_exact_canonical_corroborates():
    s = _store()
    upsert_node(s, _person("Sarah Chen", "sarah@x.com", src="email"))
    o = upsert_node(s, _person("Sarah Chen", "sarah@x.com", src="hr"))   # same facts, new source
    assert o.action == "corroborated" and o.matched_by == "canonical"
    assert s.counts()["nodes"] == 1 and s.counts()["sources"] == 2


def test_canonical_supersede_on_changed_facts():
    s = _store()
    upsert_node(s, _person("Sarah Chen", "sarah@x.com", attrs={"role": "VP"}))
    o = upsert_node(s, _person("Sarah Chen", "sarah@x.com", attrs={"role": "CEO"}))
    assert o.action == "superseded"
    # one OPEN version (the new one), one closed (the old) — invalidate-don't-delete (ADR-0003)
    assert s.counts()["nodes"] == 1
    assert len(s.all_node_versions("person:sarah@x.com")) == 2
    assert s.open_node_row("person:sarah@x.com")["attrs_json"].find("CEO") >= 0


def test_supersede_old_version_has_nonzero_system_interval():
    # even under a PINNED clock the superseded version's system interval is non-zero, so "what we
    # believed before the correction" stays queryable (review HIGH 2 — zero-width [S,S) bug)
    s = _store()
    upsert_node(s, _person("Sarah Chen", "sarah@x.com", attrs={"role": "VP"}))
    upsert_node(s, _person("Sarah Chen", "sarah@x.com", attrs={"role": "CEO"}))
    versions = s.all_node_versions("person:sarah@x.com")
    old = next(v for v in versions if v["system_to"] is not None)
    assert old["system_from"] < old["system_to"]          # strictly non-zero interval


def test_extracted_mention_corroborates_and_preserves_seed_attrs():
    # a drag-drop fixture re-extracts a seed person with EMPTY attrs + a later valid_from; that must
    # CORROBORATE (preserve the seed's role/email/valid_from), not supersede-and-wipe them.
    import json as _json

    s = _store()
    upsert_node(s, _person("Sarah Chen", "sarah@x.com", attrs={"role": "CEO", "email": "sarah@x.com"}))
    poor = GraphNode(id=node_id(NodeKind.PERSON, "sarah@x.com"), kind=NodeKind.PERSON,
                     label="Sarah Chen", canonical_id="sarah@x.com", attrs={}, sources=[_ptr("doc2")],
                     valid_from=dt.datetime(2024, 6, 1, tzinfo=_UTC), system_from=_LEARN)
    o = upsert_node(s, poor)
    assert o.action == "corroborated"
    row = s.open_node_row("person:sarah@x.com")
    assert _json.loads(row["attrs_json"]) == {"role": "CEO", "email": "sarah@x.com"}  # preserved
    assert row["valid_from"].startswith("2024-01-01")                                 # not bumped
    assert s.counts()["nodes"] == 1 and s.counts()["sources"] == 2                    # provenance grew


def test_idempotent_with_non_json_native_attrs():
    # attrs that aren't JSON-native (a date) must still compare equal on re-upsert (no endless
    # supersede loop) — review MEDIUM 3
    import datetime as _dt
    s = _store()
    n = _person("Tom Walsh", "tom@x.com", attrs={"hired": _dt.date(2024, 1, 1)})
    upsert_node(s, n)
    o = upsert_node(s, n)
    assert o.action == "corroborated" and s.counts()["nodes"] == 1


def test_fuzzy_merge_without_canonical():
    s = _store()
    upsert_node(s, _person("Acme Corp"))
    o = upsert_node(s, _person("Acme Corporation"))
    assert o.matched_by == "fuzzy" and s.counts()["nodes"] == 1


def test_distinct_canonical_never_fuzzy_merges():
    s = _store()
    upsert_node(s, _person("Sarah Chen", "sarah@x.com"))
    upsert_node(s, _person("Sarah Chen", "sarah@y.com"))   # same name, DIFFERENT email
    assert s.counts()["nodes"] == 2                          # different identities, kept apart


def test_idempotent_reupsert_is_noop_on_count():
    s = _store()
    n = _person("Tom Walsh", "tom@x.com")
    upsert_node(s, n)
    upsert_node(s, n)
    upsert_node(s, n)
    assert s.counts()["nodes"] == 1 and s.counts()["sources"] == 1   # same pointer not duplicated

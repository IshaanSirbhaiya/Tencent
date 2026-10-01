"""Closes `wiki/40-reviews/raw/phase4-graph-index.md` — the reverse pointer index and the
bi-temporal `GraphStore` beneath it — against the codex review: 5 Critical, 4 High, 2 Medium.

Dispositions (fixed vs dismissed, with reasons) are in `wiki/40-reviews/R-011-graph-index-
dispositions.md`. One test per finding fixed here, each written to fail on the pre-fix code —
the same discipline `test_ptr_index.py` already uses.

Two properties this module exists to protect, load-bearing across every fix below:

1. The index never says "nothing affected" when it means "I do not know" — `Answer.KNOWN`,
   `Answer.UNKNOWN` (a coverage gap, with an exact count) and `Answer.UNAVAILABLE` (a row that
   could not be READ, with an exact count) stay distinct, and only `KNOWN` + empty renders as
   "nothing cites this". See `test_the_three_answer_states_stay_distinct` at the end of this file.
2. `cascade()` returns ledger-bound records and never mutates them — a structural flag
   (`ledger_bound`), never a `kind` string comparison. See `test_ledger_bound_records_are_still_
   never_mutated_after_every_fix`.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from databridge_core import SourcePointer, Trust
from databridge_graph import (
    Answer,
    EdgeKind,
    GraphEdge,
    GraphNode,
    GraphStore,
    NodeKind,
    row_to_node,
)
from databridge_graph.query import as_of

_UTC = dt.timezone.utc
_T0 = dt.datetime(2024, 1, 1, tzinfo=_UTC)
_LEARN = dt.datetime(2024, 6, 1, tzinfo=_UTC)
_LATER = dt.datetime(2024, 9, 1, tzinfo=_UTC)


def _cell(src="registry", column="dob", row_id="R1", **kw):
    return SourcePointer(source_id=src, kind="table_cell", table="subjects", column=column,
                         row_id=row_id, **kw)


def _span(src="doc1"):
    return SourcePointer(source_id=src, kind="doc_span", document_id=src, char_start=0,
                         char_end=5)


def _store(path: str = ":memory:", clock=None):
    return GraphStore(path, clock=clock or (lambda: _LEARN))


def _node(nid="test:a", label="A", *, sources=None, attrs=None, attr_sources=None,
         system_from=_LEARN):
    return GraphNode(id=nid, kind=NodeKind.TEST, label=label,
                     sources=list(sources or [_span()]), attrs=dict(attrs or {}),
                     attr_sources=dict(attr_sources or {}),
                     valid_from=_T0, system_from=system_from)


# =================================================================================================
# Finding 1 (CRITICAL) — GraphStore._close / supersede_attr fabricated valid_to from wall-clock
# =================================================================================================
def test_close_node_does_not_fabricate_valid_to_from_detection_time():
    """`at` is when the SYSTEM stopped believing a fact; it is not evidence of when the fact
    itself stopped being true. Closing without an explicit `valid_to` must leave the column as it
    was (`NULL` — "unknown"), never default it to the detection instant."""
    clock = {"t": _LEARN}
    s = _store(clock=lambda: clock["t"])
    s.add_node(_node("test:a", sources=[_cell()]))
    clock["t"] = _LATER

    s.close_node("test:a", at=_LATER)                     # no valid_to given

    row = s.fetchone("SELECT valid_to FROM nodes WHERE id = 'test:a'")
    assert row["valid_to"] is None, (
        "valid_to must stay unknown, not silently become the detection timestamp")


def test_cascade_does_not_fabricate_valid_to_either():
    """The cascade is the real caller of `close_node`/`supersede_attr` with no `valid_to` in
    hand — this is where the fabrication actually reached production behaviour."""
    clock = {"t": _LEARN}
    s = _store(clock=lambda: clock["t"])
    s.add_node(_node("test:a", sources=[_cell()]))
    s.add_node(_node("test:b", attrs={"dob": "1990-01-01"}, attr_sources={"dob": _cell()}))
    clock["t"] = _LATER

    s.reverse_index.cascade("drift_material", source_id="registry")

    assert s.fetchone("SELECT valid_to FROM nodes WHERE id = 'test:a'")["valid_to"] is None
    # test:b's OLD (now-closed) version was superseded via supersede_attr -> replace_node; its
    # closing half must not fabricate valid_to either.
    old = s.fetchone(
        "SELECT valid_to FROM nodes WHERE id = 'test:b' AND system_to IS NOT NULL")
    assert old["valid_to"] is None


def test_replace_node_honours_an_explicit_valid_to_when_the_caller_actually_knows_one():
    """The fix must not remove the ABILITY to record a real valid_to — only the fabricated
    default. `merge.py` always supplies one; this is the same call shape."""
    clock = {"t": _LEARN}
    s = _store(clock=lambda: clock["t"])
    s.add_node(_node("test:a", sources=[_cell()]))
    real_change = _T0 + dt.timedelta(days=10)
    updated = _node("test:a", sources=[_cell()]).model_copy(
        update={"system_from": _LATER, "valid_from": real_change})
    s.replace_node(updated, at=_LATER, valid_to=real_change)

    old = s.fetchone(
        "SELECT valid_to FROM nodes WHERE id = 'test:a' AND system_to IS NOT NULL")
    assert dt.datetime.fromisoformat(old["valid_to"]).replace(microsecond=0) == \
        real_change.replace(microsecond=0)


# =================================================================================================
# Finding 2 (CRITICAL) — cascade mutated by entity_id alone, not the version a pointer cites
# =================================================================================================
def test_cascade_closes_only_the_version_the_pointer_actually_cites():
    """The store does not forbid a second open version sharing an id (`_open_version_pk`'s own
    docstring says so). A cascade scoped to one pointer must close ONLY the version that pointer
    is bound to — not every open row that happens to share the entity_id."""
    clock = {"t": _LEARN}
    s = _store(clock=lambda: clock["t"])
    drifted, other = _cell(row_id="R1"), _cell(row_id="R2")
    s.add_node(_node("test:a", sources=[drifted]))
    v1 = s.fetchone("SELECT rowid_pk FROM nodes WHERE id = 'test:a'")["rowid_pk"]
    # A second, independent open version of the SAME id — a torn entity, which the store allows.
    s.add_node(_node("test:a", sources=[other]))
    v2 = s.fetchone("SELECT MAX(rowid_pk) m FROM nodes WHERE id = 'test:a'")["m"]
    assert v1 != v2
    clock["t"] = _LATER

    report = s.reverse_index.cascade("drift_material", pointer=drifted)
    assert report.nodes_closed == 1

    closed = {r["rowid_pk"]: r["system_to"]
             for r in s.fetchall("SELECT rowid_pk, system_to FROM nodes WHERE id = 'test:a'")}
    assert closed[v1] is not None, "the version the drifted pointer cites must be closed"
    assert closed[v2] is None, (
        "a DIFFERENT open version must not be invalidated just for sharing the entity_id")


def test_cascade_supersedes_the_bound_versions_attribute_not_whichever_is_open():
    """Same defect, attribute-precise: `supersede_attr` used to reach for `open_node_row`
    ("whichever version is open now"), which on a torn entity can be a DIFFERENT version than
    the one the drifted pointer's `field` citation is bound to."""
    clock = {"t": _LEARN}
    s = _store(clock=lambda: clock["t"])
    drifted, other = _cell(row_id="R1"), _cell(row_id="R2")
    s.add_node(_node("test:a", attrs={"dob": "1990-01-01"}, attr_sources={"dob": drifted}))
    v1 = s.fetchone("SELECT rowid_pk FROM nodes WHERE id = 'test:a'")["rowid_pk"]
    s.add_node(_node("test:a", attrs={"dob": "2000-01-01"}, attr_sources={"dob": other}))
    v2 = s.fetchone("SELECT MAX(rowid_pk) m FROM nodes WHERE id = 'test:a'")["m"]
    clock["t"] = _LATER

    report = s.reverse_index.cascade("drift_material", pointer=drifted)
    assert report.attributes_superseded == 1

    v2_row = s.fetchone("SELECT attrs_json, system_to FROM nodes WHERE rowid_pk = ?", (v2,))
    assert v2_row["system_to"] is None, "v2 was never cited by the drifted pointer"
    assert json.loads(v2_row["attrs_json"])["dob"] == "2000-01-01", (
        "v2's own value must be untouched")
    v1_row = s.fetchone("SELECT system_to FROM nodes WHERE rowid_pk = ?", (v1,))
    assert v1_row["system_to"] is not None, "v1 (the actually-cited version) must be superseded"


# =================================================================================================
# Finding 3 (CRITICAL) — migration rewrote historical attrs_json in place
# =================================================================================================
def test_migration_never_rewrites_stored_attrs_json_only_the_typed_view_narrows():
    """The old migration blanked uncited attributes IN STORAGE for every row, including CLOSED,
    historical ones — an UPDATE where invalidate-never-delete requires nothing be touched at all.
    The fix narrows only the TYPED view (`row_to_node`), transiently, at read time."""
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "legacy.db")
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE nodes (rowid_pk INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL,"
            " kind TEXT NOT NULL, label TEXT NOT NULL, canonical_id TEXT,"
            " attrs_json TEXT NOT NULL DEFAULT '{}', valid_from TEXT NOT NULL, valid_to TEXT,"
            " system_from TEXT NOT NULL, system_to TEXT);"
            "CREATE TABLE edges (rowid_pk INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL,"
            " src_id TEXT NOT NULL, dst_id TEXT NOT NULL, kind TEXT NOT NULL,"
            " weight REAL NOT NULL DEFAULT 1.0, attrs_json TEXT NOT NULL DEFAULT '{}',"
            " valid_from TEXT NOT NULL, valid_to TEXT, system_from TEXT NOT NULL,"
            " system_to TEXT);"
            "CREATE TABLE sources (entity_id TEXT NOT NULL, source_pointer_json TEXT NOT NULL);")
        # An OPEN legacy row with an entity-level-only citation (no per-field pointer for `dob`).
        conn.execute(
            "INSERT INTO nodes(id, kind, label, attrs_json, valid_from, system_from) VALUES "
            "('test:a', 'test', 'A', '{\"dob\": \"1990-01-01\"}', "
            "'2024-01-01T00:00:00.000000Z', '2024-06-01T00:00:00.000000Z')")
        # A CLOSED, historical legacy row — exactly what the old migration also rewrote.
        conn.execute(
            "INSERT INTO nodes(id, kind, label, attrs_json, valid_from, valid_to, system_from, "
            "system_to) VALUES ('test:old', 'test', 'OLD', '{\"dob\": \"1975-05-05\"}', "
            "'2020-01-01T00:00:00.000000Z', '2024-01-01T00:00:00.000000Z', "
            "'2020-01-01T00:00:00.000000Z', '2024-01-01T00:00:00.000000Z')")
        conn.execute("INSERT INTO sources VALUES ('test:a', ?)", (_cell().model_dump_json(),))
        # `test:old` needs its own entity-level citation too — CLAUDE.md hard constraint 2 (no
        # GraphNode without a SourcePointer) applies to a closed, historical row exactly as much
        # as to an open one; this legacy store just never had per-FIELD pointers for `dob`.
        conn.execute("INSERT INTO sources VALUES ('test:old', ?)",
                     (_cell(row_id="OLD").model_dump_json(),))
        conn.commit()
        before = conn.execute(
            "SELECT rowid_pk, attrs_json FROM nodes ORDER BY rowid_pk").fetchall()
        conn.close()

        s = _store(path)                       # opening runs `_migrate()`
        try:
            after = s.fetchall("SELECT rowid_pk, attrs_json FROM nodes ORDER BY rowid_pk")
            assert [tuple(r) for r in before] == [
                (r["rowid_pk"], r["attrs_json"]) for r in after], (
                "the raw stored bytes must be byte-for-byte untouched — invalidate, never delete")

            live = row_to_node(s, s.open_node_row("test:a"))
            assert live.attrs["dob"] is None and "dob" in live.unresolved, (
                "the typed view still narrows an uncited value to unresolved")

            historical = row_to_node(s, s.fetchone("SELECT * FROM nodes WHERE id = 'test:old'"))
            assert historical.attrs["dob"] is None, (
                "a closed legacy row must still be constructible, narrowed the same way"
            )
        finally:
            s.close()                          # release the file before the tempdir is removed


# =================================================================================================
# Finding 4 (CRITICAL) — upsert_pointer's cache hit skipped the worse-trust merge
# =================================================================================================
def test_cache_hit_still_merges_the_worse_trust_within_one_write():
    """Two `SourcePointer`s sharing a locator identity (trust is excluded from that identity by
    design) but disagreeing on trust, cited in the SAME write: the cache used to return the
    FIRST-seen pointer_id without recomputing the merge, so a later untrusted claim on the same
    cell was silently dropped whenever the trusted one was seen first."""
    s = _store()
    s.add_node(_node("test:a", sources=[_span()]))
    trusted = _cell(trust=Trust.TRUSTED)
    untrusted = _cell(trust=Trust.UNTRUSTED)             # same locator identity as `trusted`
    assert trusted.source_id == untrusted.source_id

    # Both pointers cited in ONE call, so they share one `upsert_pointer` cache.
    s.reverse_index.record_emission("determination", "D-1", [trusted, untrusted])

    row = s.fetchone("SELECT trust FROM ptr_index WHERE source_id = 'registry'")
    assert row["trust"] == Trust.UNTRUSTED.value, (
        "the worse label must win regardless of which pointer the cache saw first")


def test_cache_hit_still_walks_parents_so_an_ancestors_trust_can_be_downgraded_too():
    """The same bug, one level up: a cache hit used to `return` before ever reaching
    `parents_of`, so a derived pointer's ancestor never got a second chance to merge its trust
    within the same write.

    Both `derived_*` pointers declare `trust=UNTRUSTED` (conservative — `SourcePointer`'s own
    trust-laundering wall refuses TRUSTED whenever the effective parent trust is not TRUSTED, so
    a pointer citing `parent_untrusted` could never legally claim TRUSTED itself). `derived_from`
    folds to the PARENT's locator identity, not its trust, so `derived_1` and `derived_2` are the
    same top-level locator identity too — meaning the top-level merge is a no-op here, and only
    the parent recursion can be what downgrades `parent_trusted`'s row.
    """
    s = _store()
    s.add_node(_node("test:a", sources=[_span()]))
    parent_trusted = _cell(trust=Trust.TRUSTED)
    parent_untrusted = _cell(trust=Trust.UNTRUSTED)      # same locator as parent_trusted
    derived_1 = SourcePointer(source_id="calc", kind="derived", derivation="A",
                              derived_from=[parent_trusted], trust=Trust.UNTRUSTED)
    derived_2 = SourcePointer(source_id="calc", kind="derived", derivation="A",
                              derived_from=[parent_untrusted], trust=Trust.UNTRUSTED)
    assert derived_1.source_id == derived_2.source_id

    s.reverse_index.record_emission("determination", "D-2", [derived_1, derived_2])

    parent_row = s.fetchone("SELECT trust FROM ptr_index WHERE source_id = 'registry'")
    assert parent_row["trust"] == Trust.UNTRUSTED.value, (
        "the parent's worse label must still be recorded even though the child pointer was a "
        "cache hit on its second occurrence")


# =================================================================================================
# Finding 5 (CRITICAL) — _close did not advance past system_from, unlike supersede_attr
# =================================================================================================
def test_close_node_advances_past_system_from_to_avoid_a_zero_width_interval():
    """`at <= system_from` (a fixed clock, or a cascade racing the write it is invalidating) must
    not produce `[S, S)`, which no `as_of()` can ever select — the row becomes permanently
    unreachable, exactly what invalidate-never-delete exists to prevent."""
    s = _store(clock=lambda: _LEARN)
    s.add_node(_node("test:a", sources=[_cell()], system_from=_LEARN))

    n = s.close_node("test:a", at=_LEARN)     # same instant as system_from; valid_to left unknown
    assert n == 1

    row = s.fetchone("SELECT system_from, system_to FROM nodes WHERE id = 'test:a'")
    assert row["system_to"] > row["system_from"], (
        "a [S, S) system interval is permanently unreachable by any as_of() query")
    snap = as_of(s, _LEARN, _LEARN)
    assert any(n.id == "test:a" for n in snap.nodes), (
        "the belief must remain reachable at the instant it was true")


# =================================================================================================
# Finding 6 (HIGH) — cascade committed row-by-row instead of as one transaction
# =================================================================================================
def test_a_cascade_that_fails_partway_leaves_no_trace(monkeypatch):
    """An exception partway through the mutation phase (a malformed `attrs_json` row, here
    simulated directly) used to leave everything committed UP TO that point durably in place,
    with no way to tell afterwards which pointers the cascade had actually reached. The whole
    mutation phase is now one transaction: it lands completely, or not at all."""
    clock = {"t": _LEARN}
    s = _store(clock=lambda: clock["t"])
    s.add_node(_node("test:a", sources=[_cell(row_id="R1")]))
    s.add_node(_node("test:b", attrs={"dob": "1990-01-01"},
                     attr_sources={"dob": _cell(row_id="R2")}))
    clock["t"] = _LATER
    # Corrupt test:b's attrs_json AFTER it was written (a real bit of storage corruption) so that
    # supersede_attr's own `row_to_node` call raises partway through the cascade's second item.
    s.conn.execute("UPDATE nodes SET attrs_json = 'not json' WHERE id = 'test:b'")
    s.conn.commit()

    with pytest.raises(Exception):
        s.reverse_index.cascade("drift_material", source_id="registry")

    # test:a's close must NOT have survived if test:b's supersession failed — the transaction is
    # the whole cascade, not each individual close.
    row = s.fetchone("SELECT system_to FROM nodes WHERE id = 'test:a'")
    assert row["system_to"] is None, (
        "a partial cascade must not leave test:a's close durably committed on its own")


# =================================================================================================
# Finding 7 (HIGH) — record_emission let a ledger-bound emission's citations grow after the fact
# =================================================================================================
def test_a_second_call_against_a_ledger_bound_emission_is_refused_even_with_identical_payload():
    s = _store()
    s.add_node(_node("test:a", sources=[_cell()]))
    index = s.reverse_index
    index.record_emission("ledger_entry", "seq-1", [_cell()], ledger_bound=True)

    with pytest.raises(ValueError, match="immutable"):
        index.record_emission("ledger_entry", "seq-1", [_cell()], ledger_bound=True)

    ptrs_before = s.fetchall(
        "SELECT COUNT(*) c FROM emission_pointer ep JOIN emission e ON e.emission_id = "
        "ep.emission_id WHERE e.ref = 'seq-1'")[0]["c"]
    with pytest.raises(ValueError):
        index.record_emission("ledger_entry", "seq-1", [_cell(), _cell(row_id="R2")],
                              ledger_bound=True, plan_hash="different-hash")
    ptrs_after = s.fetchall(
        "SELECT COUNT(*) c FROM emission_pointer ep JOIN emission e ON e.emission_id = "
        "ep.emission_id WHERE e.ref = 'seq-1'")[0]["c"]
    assert ptrs_after == ptrs_before, "a refused call must not have added any citation rows"


def test_a_non_ledger_bound_emission_may_still_accrue_citations():
    """The fix must not over-correct: a LIVE (non-ledger) emission is allowed to grow — a
    materialised view can legitimately be built up across more than one call."""
    s = _store()
    s.add_node(_node("test:a", sources=[_cell()]))
    index = s.reverse_index
    index.record_emission("dataset", "plan-1", [_cell()])
    index.record_emission("dataset", "plan-1", [_cell(row_id="R2")])   # not ledger_bound
    count = s.fetchall(
        "SELECT COUNT(*) c FROM emission_pointer ep JOIN emission e ON e.emission_id = "
        "ep.emission_id WHERE e.ref = 'plan-1'")[0]["c"]
    assert count == 2


# =================================================================================================
# Finding 8 (HIGH) — attr_sources_for collapsed rows nondeterministically
# =================================================================================================
def test_an_exact_version_pointer_wins_over_a_legacy_null_version_pointer_for_the_same_field():
    s = _store()
    s.add_node(_node("test:a", attrs={"dob": "1990-01-01"}, attr_sources={"dob": _cell()}))
    version_pk = s.fetchone("SELECT rowid_pk FROM nodes WHERE id = 'test:a'")["rowid_pk"]
    # Simulate a pointer written before per-version tracking existed: same field, version_pk NULL.
    legacy = _cell(row_id="LEGACY")
    s.conn.execute(
        "INSERT INTO sources(entity_id, field, version_pk, source_pointer_json) "
        "VALUES ('test:a', 'dob', NULL, ?)", (legacy.model_dump_json(),))
    s.conn.commit()

    got = s.attr_sources_for("test:a", version_pk=version_pk)
    assert got["dob"].row_id == "R1", "the exact-version pointer must win, not whichever SQLite ordered last"


def test_all_versions_true_with_two_real_versions_citing_the_same_field_is_refused():
    s = _store()
    s.add_node(_node("test:a", attrs={"dob": "1990-01-01"}, attr_sources={"dob": _cell(row_id="R1")}))
    updated = _node("test:a", attrs={"dob": "2000-01-01"},
                    attr_sources={"dob": _cell(row_id="R2")}).model_copy(
        update={"system_from": _LATER})
    s.replace_node(updated, at=_LATER, valid_to=_LATER)

    with pytest.raises(ValueError, match="two different versions"):
        s.attr_sources_for("test:a", all_versions=True)


# =================================================================================================
# Finding 9 (HIGH) — corrupt attrs_json rendered identically to an absent value, report stayed KNOWN
# =================================================================================================
def test_corrupt_attrs_json_downgrades_the_report_to_unavailable_not_a_clean_known():
    s = _store()
    s.add_node(_node("test:a", attrs={"dob": "1990-01-01"}, attr_sources={"dob": _cell()}))
    s.conn.execute("UPDATE nodes SET attrs_json = 'not json' WHERE id = 'test:a'")
    s.conn.commit()

    report = s.reverse_index.impact_of(pointer=_cell())
    assert report.answer is Answer.UNAVAILABLE, (
        "a report describing an unreadable row must not present itself as a clean, complete KNOWN")
    assert report.unreadable_entities == 1
    assert report.nodes[0].readable is False
    assert report.nodes[0].value is None                   # unknown, not (mis-reported as) absent


# =================================================================================================
# Finding 10 (MEDIUM) — ix_ptr_binding_natural was UNIQUE, so bind_pointer's INSERT OR REPLACE
# silently evicted a DIFFERENT source_rowid's binding on a natural-key collision
# =================================================================================================
def test_two_sources_rows_citing_the_identical_locator_both_stay_indexed():
    """A duplicate `SourcePointer` in `node.sources` — two different `sources` rows landing on
    the same `(pointer_id, entity_kind, version_pk, field)` tuple. Neither binding may be
    silently evicted, or the anti-join `unindexed_pointer_count()` reports a gap backfill can
    never close."""
    s = _store()
    p = _span()
    s.add_node(_node("test:a", sources=[p, p]))            # the SAME pointer, twice

    assert s.reverse_index.unindexed_pointer_count() == 0, (
        "both sources rows citing the same locator must be indexed — neither evicts the other")
    bindings = s.fetchall("SELECT COUNT(*) c FROM ptr_binding")[0]["c"]
    sources_rows = s.fetchall("SELECT COUNT(*) c FROM sources")[0]["c"]
    assert bindings == sources_rows == 2


def test_the_natural_key_index_is_not_unique():
    s = _store()
    rows = s.fetchall("PRAGMA index_list(ptr_binding)")
    natural = [r for r in rows if r["name"] == "ix_ptr_binding_natural"]
    assert natural and natural[0]["unique"] == 0


# =================================================================================================
# Finding 11 (MEDIUM) — replace_node's INSERT sat outside its own try/except
# =================================================================================================
def test_replace_node_rolls_back_the_close_if_the_insert_fails(monkeypatch):
    """A failure in the INSERT half of a supersession used to leave the UPDATE (the close) of the
    old version pending, uncommitted, on the shared connection — the next unrelated commit on
    that connection would publish a version closed with no replacement."""
    s = _store(clock=lambda: _LEARN)
    s.add_node(_node("test:a", sources=[_cell()]))

    class _FlakyConn:
        """`sqlite3.Connection.execute` is a read-only C-level attribute — it cannot be
        monkeypatched on the connection object itself — so this wraps the real connection and
        forwards everything except one specific INSERT, which fails to simulate a constraint/I-O
        error partway through `replace_node`."""

        def __init__(self, real):
            self._real = real

        def execute(self, sql, *a, **kw):
            if sql.startswith("INSERT INTO nodes("):
                raise sqlite3.OperationalError("simulated failure")
            return self._real.execute(sql, *a, **kw)

        def __getattr__(self, name):
            return getattr(self._real, name)

    monkeypatch.setattr(s, "conn", _FlakyConn(s.conn))
    updated = _node("test:a", sources=[_cell()]).model_copy(update={"system_from": _LATER})
    with pytest.raises(sqlite3.OperationalError):
        s.replace_node(updated, at=_LATER, valid_to=_LATER)
    monkeypatch.undo()

    # A totally unrelated later write commits the connection...
    s.add_node(_node("test:unrelated", sources=[_cell(row_id="R9")]))

    # ...and test:a must still have EXACTLY ONE open version: the ORIGINAL, untouched. If the
    # close from the failed replace had leaked through uncommitted-but-pending, this unrelated
    # commit would have published it with no replacement row.
    open_rows = s.fetchall("SELECT rowid_pk FROM nodes WHERE id = 'test:a' AND system_to IS NULL")
    assert len(open_rows) == 1
    assert s.open_node_row("test:a")["valid_from"] is not None


# =================================================================================================
# The two load-bearing properties — must survive every fix above
# =================================================================================================
def test_the_three_answer_states_stay_distinct():
    s = _store()
    s.add_node(_node("test:a", sources=[_cell()]))

    clean = s.reverse_index.impact_of(pointer=_cell(src="never-cited"))
    assert clean.answer is Answer.KNOWN and clean.nodes == ()          # KNOWN + empty = "nothing"

    s.conn.execute("INSERT INTO sources(entity_id, source_pointer_json) VALUES (?, ?)",
                   ("test:ghost", _span(src="bypassed").model_dump_json()))
    s.conn.commit()
    gap = s.reverse_index.impact_of(pointer=_cell())
    assert gap.answer is Answer.UNKNOWN and gap.unindexed_pointers == 1

    s2 = _store()
    s2.add_node(_node("test:b", attrs={"dob": "1990-01-01"}, attr_sources={"dob": _cell()}))
    s2.conn.execute("UPDATE nodes SET attrs_json = 'not json' WHERE id = 'test:b'")
    s2.conn.commit()
    unreadable = s2.reverse_index.impact_of(pointer=_cell())
    assert unreadable.answer is Answer.UNAVAILABLE and unreadable.unreadable_entities == 1

    assert {clean.answer, gap.answer, unreadable.answer} == {
        Answer.KNOWN, Answer.UNKNOWN, Answer.UNAVAILABLE}, "all three states must be reachable and distinct"


def test_ledger_bound_records_are_still_never_mutated_after_every_fix():
    clock = {"t": _LEARN}
    s = _store(clock=lambda: clock["t"])
    s.add_node(_node("test:a", sources=[_cell()]))
    index = s.reverse_index
    index.record_emission("ledger_entry", "seq-9", [_cell()], ledger_bound=True)
    clock["t"] = _LATER

    report = index.cascade("drift_material", source_id="registry")
    assert report.ledger_entries_untouched == 1
    row = s.fetchone("SELECT stale_since, stale_cause FROM emission WHERE ref = 'seq-9'")
    assert row["stale_since"] is None and row["stale_cause"] is None
    assert [e.ref for e in report.impact.ledger_bound] == ["seq-9"]

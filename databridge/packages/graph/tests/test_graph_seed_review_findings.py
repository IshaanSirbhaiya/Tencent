"""One test per finding, `wiki/40-reviews/raw/phase4-graph-seed.md` (R-010) — the estate->graph
builder (`estate_seed.py`) and the merge/upsert layer (`merge.py`), the most identity-sensitive
code in the repository. See `wiki/40-reviews/R-010-graph-seed-dispositions.md` for the full
per-finding disposition, including the two recorded as FALSE POSITIVE with reasons rather than
"fixed" here (findings 2's `UpsertOutcome.requires_human_selection`, and finding 1's verdict that
the real defect lives in `estate_seed`'s slug-collision path rather than in `merge._resolve_existing`
itself).

Each test is written to FAIL on the pre-fix code and PASS after — the finding number is named in
every test's docstring so a disposition can be checked against its proof directly.
"""
from __future__ import annotations

import datetime as dt
import json as _json
from pathlib import Path

import pytest

from databridge_core import SourcePointer, Trust
from databridge_graph.estate_seed import (
    BuildReport,
    DatasetSpec,
    EdgeSpec,
    NodeSpec,
    SeedMap,
    SeedMapError,
    _build_edge,
    _build_node,
    load_seed_map,
    read_fixed_width,
    read_json_pages,
    read_sql_insert,
    resolve_incoming,
)
from databridge_graph.kinds import EdgeKind, NodeKind
from databridge_graph.merge import UpsertOutcome, upsert_edge, upsert_node
from databridge_graph.packs import active_vocabulary, deployment
from databridge_graph.schema import GraphEdge, GraphNode, node_id
from databridge_graph.store import GraphStore, row_to_node

_UTC = dt.timezone.utc
_T0 = dt.datetime(2026, 1, 1, tzinfo=_UTC)


def _ptr(src: str = "d", column: str = "x", row: str = "1") -> SourcePointer:
    return SourcePointer(source_id=src, kind="table_cell", table="t", column=column, row_id=row)


def _seed(pack: str = "healthcare") -> SeedMap:
    return SeedMap(pack=pack, root=Path("."), system_time=_T0, default_valid_from=_T0,
                  datasets=())


def _dataset(**overrides) -> DatasetSpec:
    base = dict(id="ds", reader="csv", table="t", paths=(), trust=Trust.TRUSTED, row_key="")
    base.update(overrides)
    return DatasetSpec(**base)


# ======================================================================================
# Findings 1 & 3 — slug collisions in `estate_seed._build_node`
# ======================================================================================
def test_slug_collision_between_two_different_subjects_is_refused_not_fused():
    """Findings 1 & 3. Two rows for DIFFERENT people whose raw keys differ only by punctuation
    ('AB.1' vs 'AB-1') slug to the IDENTICAL node id. Before the fix, `_build_node` treated the
    second row exactly like a re-sighting of the first (first-writer-wins, append sources) — for a
    SUBJECT kind that fuses two different people's evidence onto one node without `merge.upsert_node`
    (or a human) ever being asked, which is the automated identity merge CLAUDE.md constraint 1
    forbids. The second row must be refused and counted, not silently absorbed."""
    with deployment("healthcare"):
        vocab = active_vocabulary()
        seed = _seed()
        dataset = _dataset(table="registry.patients")
        spec = NodeSpec(kind="patient", key="{code}", label="{name}", sources=("code",))
        nodes: dict = {}
        raw_keys: dict = {}
        report = BuildReport()

        _build_node(spec, {"code": "AB.1", "name": "Alice Tan"}, "r1", dataset, seed, "src.csv",
                   vocab, nodes, raw_keys, report)
        _build_node(spec, {"code": "AB-1", "name": "Ben Ong"}, "r2", dataset, seed, "src.csv",
                   vocab, nodes, raw_keys, report)          # "AB-1" slugs to the SAME id as "AB.1"

        assert len(nodes) == 1
        (only,) = nodes.values()
        assert only.label == "Alice Tan", "Ben's row must never have touched Alice's node"
        assert len(only.sources) == 1, "Ben's evidence must not be absorbed into Alice's provenance"
        assert report.skipped_nodes_slug_collision.get("ds:patient") == 1
        assert report.slug_collisions.get("ds:patient") == 1


def test_slug_collision_between_two_org_rows_still_corroborates_but_is_counted():
    """Finding 3, the non-subject half. Fusing two ORG rows is the safe direction this module's
    own docstring already argues for ("Coral Harbour Pte Ltd" across documents is one company) —
    that must NOT change. What must change is visibility: the collision is now counted even though
    it is still allowed to corroborate."""
    with deployment("healthcare"):
        vocab = active_vocabulary()
        seed = _seed()
        dataset = _dataset(table="registry.orgs")
        spec = NodeSpec(kind="org", key="{code}", label="{name}", sources=("code",))
        nodes: dict = {}
        raw_keys: dict = {}
        report = BuildReport()

        _build_node(spec, {"code": "AB.1", "name": "Coral Harbour"}, "r1", dataset, seed,
                   "src.csv", vocab, nodes, raw_keys, report)
        _build_node(spec, {"code": "AB-1", "name": "Coral Harbour Pte Ltd"}, "r2", dataset, seed,
                   "src.csv", vocab, nodes, raw_keys, report)

        assert len(nodes) == 1                        # non-subject: still the safe, allowed fuse
        (only,) = nodes.values()
        assert len(only.sources) == 2                 # but BOTH rows' evidence is kept, visibly
        assert report.slug_collisions.get("ds:org") == 1
        assert report.skipped_nodes_slug_collision == {}, "never refused for a non-subject kind"


def test_the_pipeline_end_to_end_never_fuses_two_different_subjects_on_a_slug_collision():
    """Finding 1's verdict, proved rather than asserted: `merge._resolve_existing`'s id-match step
    is exactly as strong as `incoming.id`'s uniqueness, and THAT function has no way to verify it —
    it is `estate_seed._build_node`'s job (the guard proved above). Wired together the way a real
    `load_pack_graph` call runs them, the pipeline never lets two different subjects reach the
    store as one node, which is what makes the id-match step's trust in `_build_node` well-founded
    rather than a hole one level up."""
    with deployment("healthcare"):
        vocab = active_vocabulary()
        seed = _seed()
        dataset = _dataset(table="registry.patients")
        spec = NodeSpec(kind="patient", key="{code}", label="{name}", sources=("code", "name"))
        nodes: dict = {}
        raw_keys: dict = {}
        report = BuildReport()
        _build_node(spec, {"code": "AB.1", "name": "Alice Tan"}, "r1", dataset, seed, "src.csv",
                   vocab, nodes, raw_keys, report)
        _build_node(spec, {"code": "AB-1", "name": "Ben Ong"}, "r2", dataset, seed, "src.csv",
                   vocab, nodes, raw_keys, report)
        assert len(nodes) == 1

        store = GraphStore(clock=lambda: _T0)
        for node in nodes.values():
            upsert_node(store, node)
        assert store.counts()["nodes"] == 1
        (only_id,) = nodes.keys()
        assert store.open_node_row(only_id)["label"] == "Alice Tan"


# ======================================================================================
# Finding 2 — UpsertOutcome.requires_human_selection: FALSE POSITIVE, pinned against regression
# ======================================================================================
def test_upsert_outcome_requires_human_selection_is_a_bool_not_literal_true():
    """Finding 2 — disposition FALSE POSITIVE (see R-010). This field answers "did this upsert
    touch a SUBJECT kind?", asked of every upsert including non-subject ones (an address, a
    payment), where `False` is the correct, informative answer — not
    `ResolutionResult.requires_human_selection` (`packages/intake`, `Literal[True]`), the field
    CLAUDE.md hard constraint 1 actually governs, despite the shared name. Pinned here so a future
    "fix" does not make this field `Literal[True]` and therefore meaningless."""
    out = UpsertOutcome(id="org:x", action="inserted")
    assert out.requires_human_selection is False


# ======================================================================================
# Finding 4 — `resolve_incoming` emitting derived values with false provenance
# ======================================================================================
def test_resolve_incoming_grounds_computed_label_and_matched_on_with_derived_pointers():
    """Finding 4. `label` concatenates TWO nodes' labels; `matched_on` names the SELECTOR COLUMN,
    not the value read from it — neither string appears in any single cell. Before the fix both
    were cited with an ordinary pointer whose bytes did not match what was emitted (`matched_on`'s
    pointer cited the DECISIVE VALUE's cell while the emitted string was the column's NAME). Both
    must be `derived`, naming the rule and keeping the real citations as `derived_from`."""
    with deployment("finance"):
        store = GraphStore(clock=lambda: _T0)
        party = GraphNode(
            id=node_id(NodeKind.PARTY, "p1"), kind=NodeKind.PARTY, label="Acme Trading Pte Ltd",
            attrs={"country_code": "SG", "party_type": "organisation"},
            attr_sources={"country_code": _ptr("cb", "country_code", "p1"),
                         "party_type": _ptr("cb", "party_type", "p1")},
            sources=[_ptr("cb", "party_id", "p1")], valid_from=_T0, system_from=_T0)
        store.add_node(party)

        entry = GraphNode(
            id=node_id(NodeKind.WATCHLIST_ENTRY, "w1"), kind=NodeKind.WATCHLIST_ENTRY,
            label="Acme Trading Pte Ltd", canonical_id="W1",
            attrs={"subject_type": "ENTITY", "country": "SG"},
            attr_sources={"subject_type": _ptr("wl", "subject_type", "w1"),
                         "country": _ptr("wl", "country", "w1"),
                         "canonical_id": _ptr("wl", "watchlist_id", "w1")},
            sources=[_ptr("wl", "watchlist_id", "w1"), _ptr("wl", "name", "w1")],
            valid_from=_T0, system_from=_T0)
        store.add_node(entry)

        report = resolve_incoming(store, "finance", dataset_id="wl_watchlist")
        assert report["matched"] == 1

        rows = store.fetchall("SELECT * FROM nodes WHERE kind='screening_result'")
        assert len(rows) == 1
        result = row_to_node(store, rows[0])

        assert result.attrs["matched_on"] == "country"          # the SELECTOR COLUMN's name
        matched_ptr = result.attr_sources["matched_on"]
        assert matched_ptr.kind == "derived" and matched_ptr.derivation
        # its DERIVATION cites the cell holding the decisive VALUE, not a claim that the cell
        # literally contains the string "country"
        assert any(p.column == "country" and p.source_id == "wl" for p in matched_ptr.derived_from)

        label_ptr = result.attr_sources["label"]
        assert label_ptr.kind == "derived" and label_ptr.derivation
        label_sources = {p.source_id for p in label_ptr.derived_from}
        assert "wl" in label_sources and "cb" in label_sources, (
            "the combined label must cite BOTH the incoming row's evidence and the candidate's")


# ======================================================================================
# Finding 5 — a fabricated fallback label, now derived and grounded
# ======================================================================================
def test_label_fallback_is_derived_and_grounded_not_fabricated():
    """Finding 5. `_label` correctly returns `None` when its template names a NULL column;
    `_build_node` falls back to the node's own KEY as the label. Before the fix that fallback
    carried no pointer at all — a value with no source (hard constraint 2). It must now be a
    `derived` value citing the node's own grounding."""
    with deployment("healthcare"):
        vocab = active_vocabulary()
        seed = _seed()
        dataset = _dataset(table="registry.orgs")
        spec = NodeSpec(kind="org", key="{code}", label="{missing_col}", sources=("code",))
        nodes: dict = {}
        raw_keys: dict = {}
        report = BuildReport()

        _build_node(spec, {"code": "AB-1", "missing_col": None}, "r1", dataset, seed, "src.csv",
                   vocab, nodes, raw_keys, report)

        (node,) = nodes.values()
        assert node.label == "ab-1"                              # the slugified key
        ptr = node.attr_sources["label"]
        assert ptr.kind == "derived" and ptr.derivation
        assert ptr.derived_from, "a derived pointer with nothing to derive from cites nothing"
        assert report.labels_derived_from_key == 1


# ======================================================================================
# Finding 6 — malformed `valid_from` no longer silently defaults
# ======================================================================================
def test_malformed_valid_from_skips_the_node_rather_than_defaulting():
    """Finding 6. A row's OWN `valid_from` value is present but not a parseable timestamp — the
    old code swallowed the parse error and silently substituted the map's fallback date, a value
    the row never contained. The node must be skipped and counted, not dated with a guess; a
    genuinely MISSING `valid_from` (the other half) still legitimately uses the fallback."""
    with deployment("healthcare"):
        vocab = active_vocabulary()
        seed = _seed()
        dataset = _dataset(table="registry.orgs")
        spec = NodeSpec(kind="org", key="{code}", label="{code}", sources=("code",),
                        valid_from="opened_on")
        nodes: dict = {}
        raw_keys: dict = {}
        report = BuildReport()

        _build_node(spec, {"code": "AB-1", "opened_on": "not-a-date"}, "r1", dataset, seed,
                   "src.csv", vocab, nodes, raw_keys, report)
        assert nodes == {}
        assert report.skipped_nodes_bad_valid_from.get("ds:org") == 1

        _build_node(spec, {"code": "AB-2", "opened_on": None}, "r2", dataset, seed, "src.csv",
                   vocab, nodes, raw_keys, report)
        assert len(nodes) == 1, "a MISSING valid_from must still fall back to the map's default"


# ======================================================================================
# Finding 7 — `upsert_edge` now invalidates rather than silently ignoring a changed attr
# ======================================================================================
def test_upsert_edge_supersedes_on_a_real_attr_conflict_rather_than_silently_corroborating():
    """Finding 7. An edge with attrs that then CHANGE used to always corroborate — `incoming`'s
    attrs and their provenance were discarded entirely, and the OLD value stayed the queryable
    belief with no way to ask "what did we believe before" (the bi-temporal guarantee this store
    otherwise makes for nodes). Must supersede (ADR-0003), exactly like `upsert_node`."""
    with deployment("healthcare"):
        store = GraphStore(clock=lambda: _T0)
        e1 = GraphEdge(id="edge:e1", src_id="org:a", dst_id="org:b", kind=EdgeKind.REFERENCES,
                       attrs={"status": "pending"},
                       attr_sources={"status": _ptr("d1", "status", "1")},
                       sources=[_ptr("d1", "x", "1")], valid_from=_T0, system_from=_T0)
        assert upsert_edge(store, e1).action == "inserted"

        e2 = GraphEdge(id="edge:e1", src_id="org:a", dst_id="org:b", kind=EdgeKind.REFERENCES,
                       attrs={"status": "confirmed"},
                       attr_sources={"status": _ptr("d2", "status", "2")},
                       sources=[_ptr("d2", "y", "2")], valid_from=_T0,
                       system_from=_T0 + dt.timedelta(days=1))
        out = upsert_edge(store, e2)
        assert out.action == "superseded"

        open_row = store.find_open_edge("org:a", "org:b", "references")
        assert _json.loads(open_row["attrs_json"])["status"] == "confirmed"
        closed = store.fetchone(
            "SELECT COUNT(*) c FROM edges WHERE id=? AND system_to IS NOT NULL", ("edge:e1",))["c"]
        assert closed == 1, "the OLD belief must stay queryable, not be overwritten in place"


def test_upsert_edge_with_no_attr_conflict_still_only_ever_corroborates():
    """Sanity for finding 7's fix: the ordinary case (no attrs, or a re-applied identical edge)
    must NOT start superseding — that would defeat idempotency and regress
    `test_edge_upsert_equivalence_old_vs_new`."""
    with deployment("healthcare"):
        store = GraphStore(clock=lambda: _T0)
        e = GraphEdge(id="edge:e2", src_id="org:a", dst_id="org:b", kind=EdgeKind.REFERENCES,
                     sources=[_ptr("d1", "x", "1")], valid_from=_T0, system_from=_T0)
        assert upsert_edge(store, e).action == "inserted"
        again = GraphEdge(id="edge:e2b", src_id="org:a", dst_id="org:b", kind=EdgeKind.REFERENCES,
                          sources=[_ptr("d2", "y", "2")], valid_from=_T0, system_from=_T0)
        out = upsert_edge(store, again)
        assert out.action == "corroborated"
        assert store.fetchone(
            "SELECT COUNT(*) c FROM edges WHERE system_to IS NOT NULL")["c"] == 0


# ======================================================================================
# Finding 8 — `upsert_node` no longer loses grounded facts or provenance on supersede
# ======================================================================================
def test_supersede_preserves_existing_attrs_the_incoming_node_is_silent_about():
    """Finding 8. A real conflict on ONE attr used to wipe every OTHER attr `existing` had that
    `incoming` did not re-state — a full overwrite dressed as a targeted correction."""
    with deployment("healthcare"):
        store = GraphStore(clock=lambda: _T0)
        n1 = GraphNode(id=node_id(NodeKind.ORG, "acme"), kind=NodeKind.ORG, label="Acme",
                       attrs={"role": "VP", "office": "SG"},
                       attr_sources={"role": _ptr("d1", "role", "1"),
                                    "office": _ptr("d1", "office", "1")},
                       sources=[_ptr("d1", "x", "1")], valid_from=_T0, system_from=_T0)
        upsert_node(store, n1)

        n2 = n1.model_copy(update={
            "attrs": {"role": "CEO"},
            "attr_sources": {"role": _ptr("d2", "role", "2")},
            "sources": [_ptr("d2", "y", "2")],
        })
        out = upsert_node(store, n2)
        assert out.action == "superseded"
        attrs = _json.loads(store.open_node_row(n1.id)["attrs_json"])
        assert attrs == {"role": "CEO", "office": "SG"}, "the correction must not erase 'office'"


def test_a_new_grounded_attr_on_an_existing_node_is_no_longer_silently_dropped():
    """Finding 8, the other half. A genuinely NEW attr key (`existing` never saw it at all) used
    to satisfy neither half of the old `_conflicts` check, so it took the CORROBORATE branch —
    which only appends `sources` pointers and never touches `attrs`. The new, grounded fact simply
    vanished."""
    with deployment("healthcare"):
        store = GraphStore(clock=lambda: _T0)
        n1 = GraphNode(id=node_id(NodeKind.ORG, "acme2"), kind=NodeKind.ORG, label="Acme",
                       attrs={}, sources=[_ptr("d1", "x", "1")], valid_from=_T0, system_from=_T0)
        upsert_node(store, n1)

        n2 = n1.model_copy(update={
            "attrs": {"email": "info@acme.example"},
            "attr_sources": {"email": _ptr("d2", "email", "2")},
            "sources": [_ptr("d2", "y", "2")],
        })
        out = upsert_node(store, n2)
        assert out.action == "superseded", "a new grounded fact is a conflict, not a corroboration"
        attrs = _json.loads(store.open_node_row(n1.id)["attrs_json"])
        assert attrs == {"email": "info@acme.example"}


def test_supersede_preserves_a_grounded_canonical_id_the_incoming_node_omits():
    """Finding 8, `canonical_id`. Superseding used to keep `existing.canonical_id`'s STRING but
    not its POINTER when `incoming` supplied none of its own — reintroducing, on the very path
    meant to correct a node, the ungrounded-identity-field hole review finding A4 closed."""
    with deployment("healthcare"):
        store = GraphStore(clock=lambda: _T0)
        n1 = GraphNode(id=node_id(NodeKind.PATIENT, "p1"), kind=NodeKind.PATIENT, label="Devi",
                       canonical_id="S1234567A", attrs={"role": "outpatient"},
                       attr_sources={"canonical_id": _ptr("d1", "nric", "1"),
                                    "role": _ptr("d1", "role", "1")},
                       sources=[_ptr("d1", "x", "1")], valid_from=_T0, system_from=_T0)
        upsert_node(store, n1)

        n2 = n1.model_copy(update={
            "canonical_id": None,
            "attrs": {"role": "inpatient"},
            "attr_sources": {"role": _ptr("d2", "role", "2")},
            "sources": [_ptr("d2", "y", "2")],
        })
        out = upsert_node(store, n2)
        assert out.action == "superseded"
        node = row_to_node(store, store.open_node_row(n1.id))
        assert node.canonical_id == "S1234567A"
        assert node.identity_field_is_grounded("canonical_id"), (
            "the identity field's OWN pointer must survive a supersede it did not participate in")


# ======================================================================================
# Finding 9 — `read_sql_insert`: single-line and multi-tuple `INSERT`s
# ======================================================================================
def test_read_sql_insert_handles_a_single_line_insert(tmp_path):
    """Finding 9. `INSERT INTO t (id, name) VALUES (1, 'Alice');`, entirely on ONE physical line,
    is an ordinary shape for a generated dump. The old code matched the header and unconditionally
    moved to the NEXT line, discarding everything after `VALUES` — this yielded zero rows."""
    sql = tmp_path / "dump.sql"
    sql.write_text("INSERT INTO t (id, name) VALUES (1, 'Alice');\n", encoding="utf-8")
    assert list(read_sql_insert(sql)) == [{"id": 1, "name": "Alice"}]


def test_read_sql_insert_handles_multiple_tuples_on_one_line(tmp_path):
    """Finding 9, the other half: `VALUES (1,'a'),(2,'b');` on one line must yield TWO rows, not
    the one the old single-tuple-per-cycle parse produced."""
    sql = tmp_path / "dump2.sql"
    sql.write_text(
        "INSERT INTO t (id, name) VALUES (2, 'Bob'), (3, 'Cara O''Neil');\n", encoding="utf-8")
    assert list(read_sql_insert(sql)) == [
        {"id": 2, "name": "Bob"}, {"id": 3, "name": "Cara O'Neil"}]


def test_read_sql_insert_still_handles_a_tuple_spanning_multiple_lines(tmp_path):
    """Regression guard: the common multi-line generated-dump shape must keep working."""
    sql = tmp_path / "dump3.sql"
    sql.write_text(
        "INSERT INTO t (id, name) VALUES\n(1,\n'Alice'),\n(2, 'Bob');\n", encoding="utf-8")
    assert list(read_sql_insert(sql)) == [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]


# ======================================================================================
# Finding 10 — `read_json_pages`: malformed collections fail loudly
# ======================================================================================
def test_read_json_pages_raises_on_a_missing_records_key(tmp_path):
    """Finding 10. A missing `data` key used to read as an empty page — indistinguishable from a
    page that genuinely has zero records."""
    p = tmp_path / "page1.json"
    p.write_text(_json.dumps({"meta": {}}), encoding="utf-8")
    with pytest.raises(SeedMapError):
        list(read_json_pages(p))


def test_read_json_pages_raises_when_records_key_is_not_a_list(tmp_path):
    p = tmp_path / "page2.json"
    p.write_text(_json.dumps({"data": {"not": "a list"}}), encoding="utf-8")
    with pytest.raises(SeedMapError):
        list(read_json_pages(p))


def test_read_json_pages_raises_on_a_non_object_record(tmp_path):
    p = tmp_path / "page3.json"
    p.write_text(_json.dumps({"data": [{"a": 1}, "not-an-object"]}), encoding="utf-8")
    with pytest.raises(SeedMapError):
        list(read_json_pages(p))


def test_read_json_pages_still_accepts_a_genuinely_empty_page(tmp_path):
    p = tmp_path / "page4.json"
    p.write_text(_json.dumps({"data": []}), encoding="utf-8")
    assert list(read_json_pages(p)) == []


# ======================================================================================
# Finding 11 — dotted-path columns outside `{...}` templates
# ======================================================================================
def test_dotted_columns_ground_sources_attrs_and_canonical_id():
    """Finding 11. `_render`/`_label` already resolved a dotted path (`person.email`) inside a
    `{...}` template; grounding (`spec.sources`), attribute extraction and `canonical_id` used
    plain `row.get(column)` instead, so a map naming a dotted column in any of THOSE positions got
    `None` back even though the value was genuinely present — a node skipped as ungrounded, or an
    attribute landing in `unresolved`, for data that was right there."""
    with deployment("healthcare"):
        vocab = active_vocabulary()
        seed = _seed()
        dataset = _dataset(table="registry.orgs")
        spec = NodeSpec(kind="org", key="{person.id}", label="{person.id}",
                        sources=("person.id",), canonical_id="person.email",
                        attrs={"email": "person.email"})
        nodes: dict = {}
        raw_keys: dict = {}
        report = BuildReport()
        row = {"person": {"id": "P1", "email": "p1@example.com"}}
        _build_node(spec, row, "r1", dataset, seed, "src.json", vocab, nodes, raw_keys, report)

        (node,) = nodes.values()
        assert len(node.sources) == 1, "the dotted grounding column must have resolved"
        assert node.attrs["email"] == "p1@example.com"
        assert "email" in node.attr_sources, "a present dotted value must not land in unresolved"
        assert node.canonical_id == "p1@example.com"
        assert "canonical_id" in node.attr_sources


def test_dotted_columns_ground_edge_evidence_too():
    """Finding 11, the edge half: `spec.evidence` had the same plain-`row.get` bug."""
    with deployment("healthcare"):
        vocab = active_vocabulary()
        seed = _seed()
        dataset = _dataset(table="registry.links")
        spec = EdgeSpec(kind="references", src="org:{a}", dst="org:{b}", evidence=("link.ref",))
        edges: dict = {}
        report = BuildReport()
        row = {"a": "x", "b": "y", "link": {"ref": "R1"}}
        _build_edge(spec, row, "r1", dataset, seed, "src.json", vocab, edges, report)
        (edge,) = edges.values()
        assert len(edge.sources) == 1


# ======================================================================================
# Finding 12 — `read_fixed_width`: strict decoding, opt-in zero-is-absent
# ======================================================================================
def test_read_fixed_width_decodes_strictly_rather_than_masking_bad_bytes(tmp_path):
    """Finding 12, decoding half. `errors="replace"` used to turn invalid bytes into U+FFFD and
    cite the cell as though the bytes matched — a silent corruption stamped with a pointer that
    vouches for text nobody wrote."""
    p = tmp_path / "legacy.dat"
    p.write_bytes(b"AB" + b"\xff\xfe" + b"CD\n")
    layout = [{"name": "a", "start": 1, "end": 2}, {"name": "b", "start": 3, "end": 4}]
    with pytest.raises(UnicodeDecodeError):
        list(read_fixed_width(p, layout=layout))


def test_read_fixed_width_all_zero_is_absent_is_now_opt_in_per_field(tmp_path):
    """Finding 12, all-zero half. Treating an all-zero field as absent used to be BLANKET, for
    every field regardless of what it holds — a domain assumption embedded in engine code that
    also discarded a legitimate all-zero VALUE for any field where one is meaningful. Defaults to
    the old behaviour (`True`) so today's one real user (`dob_cymd`) is unaffected without a map
    edit, but a field can now opt OUT."""
    p = tmp_path / "legacy2.dat"
    p.write_text("00009999\n", encoding="utf-8")
    default_layout = [{"name": "dob", "start": 1, "end": 4}, {"name": "code", "start": 5, "end": 8}]
    row = next(read_fixed_width(p, layout=default_layout))
    assert row["dob"] is None                          # unchanged default behaviour
    assert row["code"] == "9999"

    opt_out_layout = [{"name": "dob", "start": 1, "end": 4, "zero_is_absent": False}]
    row2 = next(read_fixed_width(p, layout=opt_out_layout))
    assert row2["dob"] == "0000", "a field that opts out must keep its source-present zero value"


# ======================================================================================
# Finding 13 (High) — `load_seed_map` path containment
# ======================================================================================
def test_load_seed_map_refuses_a_sibling_directory_sharing_a_string_prefix(tmp_path):
    """Finding 13. `fixtures-escape` starts with the same characters as `fixtures` but is a
    SIBLING directory, not a descendant. A string-prefix containment test (`str.startswith`)
    passes it; a path-component test (`Path.is_relative_to`) correctly refuses it."""
    pack_dir = tmp_path / "rtpack"
    (pack_dir / "fixtures").mkdir(parents=True)
    (pack_dir / "fixtures-escape").mkdir()
    (pack_dir / "fixtures-escape" / "evil.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (pack_dir / "seed_map.yaml").write_text(
        "pack: rtpack\n"
        "fixtures_root: fixtures\n"
        'system_time: "2026-01-01T00:00:00Z"\n'
        'default_valid_from: "2026-01-01T00:00:00Z"\n'
        "datasets:\n"
        "  - id: escape\n"
        "    reader: csv\n"
        "    table: t\n"
        '    row_key: "{a}"\n'
        '    path: "../fixtures-escape/evil.csv"\n',
        encoding="utf-8")
    with pytest.raises(SeedMapError, match="outside its"):
        load_seed_map("rtpack", root=tmp_path)

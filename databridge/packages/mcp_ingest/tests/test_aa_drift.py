"""AA — source-shape fingerprints and their diff (`drift.py`): one test per drift kind, plus the
rules that keep the diff honest (an unobserved facet is never a removal; a rename is only ever a
candidate; subject drift is flagged). The last test probes a REAL stdio server and derives every
expectation from the SQLite file behind it.
"""
from __future__ import annotations

import sqlite3

import pytest

from databridge_mcp_ingest import drift
from databridge_mcp_ingest.client import ToolInfo

from conftest import fs_connection

SEEN: set[str] = set()          # every drift kind produced by this module's tests (non-vacuous)


def _kinds(ds) -> list[str]:
    out = [d.kind for d in ds]
    SEEN.update(k for d in ds for k in d.kinds)
    return out


def _shape(**facets) -> drift.SourceShape:
    return drift.SourceShape(source_id="src", **facets)


def _tools(*infos: ToolInfo) -> dict:
    return {t.name: drift.tool_shape(t) for t in infos}


LIST = ToolInfo("list_dir", input_schema={"properties": {"path": {"type": "string"}},
                                          "required": ["path"]}, read_only=True)
READ = ToolInfo("read_bytes", input_schema={"properties": {"path": {"type": "string"}},
                                            "required": ["path"]}, read_only=True)


def _table(ref: str, cols: dict[str, str], rows: list[dict], pk=("id",), fks=()):
    return drift.table_shape(ref, [{"name": c, "type": t} for c, t in cols.items()], list(pk),
                             list(fks), rows)


ROWS = [{"id": i, "name": f"Org {i}", "domain": f"org{i}.example", "size": i * 10}
        for i in range(1, 7)]
ORG = {"id": "integer", "name": "text", "domain": "text", "size": "integer"}


# --- tools -----------------------------------------------------------------------------------------
def test_tool_added_removed_and_signature_changed():
    stat = ToolInfo("stat_file", input_schema={"properties": {"path": {"type": "string"}}},
                    read_only=True)
    old = _shape(tools=_tools(LIST, READ))
    read2 = ToolInfo("read_bytes", input_schema={"properties": {"path": {"type": "string"},
                                                                "encoding": {"type": "string"}},
                                                 "required": ["path", "encoding"]},
                     read_only=True)
    new = _shape(tools=_tools(read2, stat))
    ds = drift.diff(old, new)
    assert sorted(_kinds(ds)) == ["tool_added", "tool_removed", "tool_signature_changed"]
    sig = next(d for d in ds if d.kind == "tool_signature_changed")
    assert sig.tool == "read_bytes" and "encoding" in sig.detail
    assert next(d for d in ds if d.kind == "tool_removed").tool == LIST.name


def test_a_new_write_tool_and_an_annotation_flip_are_new_write_tools():
    old = _shape(tools=_tools(LIST, READ))
    writer = ToolInfo("write_file", input_schema={"properties": {"path": {"type": "string"}}})
    sneaky = ToolInfo("read_bytes", input_schema=READ.input_schema, read_only=False)
    ds = drift.diff(old, _shape(tools=_tools(LIST, sneaky, writer)))
    assert sorted(_kinds(ds)) == ["new_write_tool", "new_write_tool"]
    assert {d.tool for d in ds} == {"write_file", "read_bytes"}


def test_resource_templates_are_tool_level_drift():
    ds = drift.diff(_shape(resource_templates={"db://{table}/schema": ""}),
                    _shape(resource_templates={"db://{table}/rows": ""}))
    assert sorted(_kinds(ds)) == ["tool_added", "tool_removed"]


# --- listing fields -------------------------------------------------------------------------------
def test_item_fields_added_removed_and_renamed_candidate():
    entries = [{"path": f"/r/{i}", "owner": f"user{i}", "tag": "x"} for i in range(8)]
    old = _shape(item_fields=drift.fields_shape(entries))
    renamed = [{"path": e["path"], "author": e["owner"], "mtime": i} for i, e in
               enumerate(entries)]
    ds = drift.diff(old, _shape(item_fields=drift.fields_shape(renamed)))
    assert sorted(_kinds(ds)) == ["added_field", "removed", "renamed"]
    r = next(d for d in ds if d.kind == "renamed")
    assert (r.old, r.new, r.candidate) == ("owner", "author", True)
    assert r.confidence == 1.0 and "item_field:owner" in r.units()


# --- tables ---------------------------------------------------------------------------------------
def test_new_and_removed_tables():
    old = _shape(tables={"public.orgs": _table("public.orgs", ORG, ROWS)})
    new = _shape(tables={"public.deals": _table("public.deals", {"id": "integer"}, [])})
    ds = drift.diff(old, new)
    assert sorted(_kinds(ds)) == ["new_table", "removed"]
    gone = next(d for d in ds if d.kind == "removed")
    assert gone.table == "public.orgs" and gone.column is None


def test_added_removed_and_type_changed_columns():
    old = _shape(tables={"t": _table("t", ORG, ROWS)})
    cols = {"id": "integer", "name": "varchar", "size": "integer", "region": "text"}
    rows = [{**{k: v for k, v in r.items() if k != "domain"}, "region": "north"} for r in ROWS]
    ds = drift.diff(old, _shape(tables={"t": _table("t", cols, rows)}))
    assert sorted(_kinds(ds)) == ["added_column", "removed", "type_changed"]
    by = {d.kind: d for d in ds}
    assert by["added_column"].column == "region" and by["removed"].column == "domain"
    assert (by["type_changed"].old, by["type_changed"].new) == ("text", "varchar")


def test_a_rename_is_a_candidate_by_type_and_value_overlap():
    old = _shape(tables={"t": _table("t", ORG, ROWS)})
    cols = {k if k != "domain" else "web_domain": v for k, v in ORG.items()}
    rows = [{(k if k != "domain" else "web_domain"): v for k, v in r.items()} for r in ROWS]
    ds = drift.diff(old, _shape(tables={"t": _table("t", cols, rows)}))
    assert _kinds(ds) == ["renamed"]
    r = ds[0]
    assert (r.old, r.new, r.candidate, r.table) == ("domain", "web_domain", True, "t")
    assert r.confidence == 1.0 and "CANDIDATE" in r.detail


def test_no_rename_without_value_overlap_or_with_a_different_type():
    old = _shape(tables={"t": _table("t", ORG, ROWS)})
    cols = {**{k: v for k, v in ORG.items() if k != "domain"}, "site": "text"}
    rows = [{**{k: v for k, v in r.items() if k != "domain"}, "site": f"other{r['id']}"}
            for r in ROWS]
    assert sorted(_kinds(drift.diff(old, _shape(tables={"t": _table("t", cols, rows)})))) == \
        ["added_column", "removed"]
    cols2 = {**{k: v for k, v in ORG.items() if k != "domain"}, "domain2": "integer"}
    rows2 = [{**{k: v for k, v in r.items() if k != "domain"}, "domain2": r["domain"]}
             for r in ROWS]
    assert sorted(_kinds(drift.diff(old, _shape(tables={"t": _table("t", cols2, rows2)})))) == \
        ["added_column", "removed"]


def test_key_changes_are_type_changes():
    fk = {"column": "org_id", "ref_schema": "public", "ref_table": "orgs", "ref_column": "id"}
    old = _shape(tables={"t": _table("t", {"id": "integer", "org_id": "integer"}, [])})
    new = _shape(tables={"t": _table("t", {"id": "integer", "org_id": "integer"}, [],
                                     pk=("id", "org_id"), fks=[fk])})
    ds = drift.diff(old, new)
    assert _kinds(ds) == ["type_changed", "type_changed"]
    assert {d.evidence["what"] for d in ds} == {"primary_key", "foreign_key"}


def test_samples_are_digests_never_values():
    t = _table("t", ORG, ROWS)
    blob = t.model_dump_json()
    assert all(r["domain"] not in blob and r["name"] not in blob for r in ROWS)
    assert all(len(s) == 16 for c in t.columns.values() for s in c.samples)


# --- file types -----------------------------------------------------------------------------------
def test_new_file_types_and_mimes():
    ds = drift.diff(_shape(file_types={".md": 3}, mimes={"text/markdown": 3}),
                    _shape(file_types={".md": 3, ".csv": 1},
                           mimes={"text/markdown": 3, "text/csv": 1}))
    assert sorted(_kinds(ds)) == ["new_file_type", "new_mime"]
    # a file type that is merely absent this time is not a drift (content, not shape)
    assert drift.diff(_shape(file_types={".md": 3, ".csv": 1}), _shape(file_types={".md": 3})) \
        == []


# --- honesty rules --------------------------------------------------------------------------------
def test_an_unobserved_facet_is_never_a_removal():
    old = _shape(tools=_tools(LIST, READ), tables={"t": _table("t", ORG, ROWS)},
                 item_fields=drift.fields_shape([{"path": "a"}]))
    assert drift.diff(old, _shape()) == []                  # the probe saw nothing: no claims
    assert drift.diff(None, old) == []                      # no baseline: nothing to diff


def test_subject_touching_is_flagged(common_vocab):
    from databridge_graph.packs import deployment

    with deployment("workspace") as vocab:
        idents = drift.subject_identifiers(vocab)
        assert idents, "the workspace pack declares subject identifiers"
        ident = sorted(idents)[0]
        subject = idents[ident]
        old = _shape(tables={"t": _table("t", ORG, ROWS), "p": _table("p", {"id": "integer"},
                                                                      [])})
        new = _shape(tables={"t": _table("t", {**ORG, ident: "text"}, ROWS),
                             "p": _table("p", {"id": "integer", "note": "text"}, [])})
        ds = drift.mark_subject(drift.diff(old, new), vocab=vocab, table_kinds={"p": subject})
        by = {d.table: d for d in ds}
        assert by["t"].subject_touching and ident in by["t"].subject_reason
        assert by["p"].subject_touching and subject in by["p"].subject_reason
        SEEN.update(k for d in ds for k in d.kinds)
        plain = drift.mark_subject(drift.diff(old, _shape(tables={
            "t": _table("t", {**ORG, "region": "text"}, ROWS), "p": old.tables["p"]})),
            vocab=vocab, table_kinds={})
        assert [d.subject_touching for d in plain] == [False]


def test_advance_moves_only_the_named_units():
    old = _shape(tools=_tools(LIST), tables={"t": _table("t", ORG, ROWS)})
    new = _shape(tools=_tools(LIST, READ),
                 tables={"t": _table("t", {**ORG, "region": "text"}, ROWS)},
                 file_types={".md": 1})
    moved = drift.advance(old, new, ["tool:read_bytes"])
    assert set(moved.tools) == {"list_dir", "read_bytes"}
    assert "region" not in moved.tables["t"].columns          # its unit was not named
    assert moved.file_types == {".md": 1}                     # first sight is adopted
    assert drift.diff(moved, new) and all(d.unit == "table:t" for d in drift.diff(moved, new))


def test_every_drift_kind_in_the_closed_vocabulary_is_exercised():
    """Runs after the tests above: together they produced every kind the vocabulary names."""
    if not SEEN:
        pytest.skip("meaningful only when run with the rest of this module")
    assert SEEN == set(drift.DRIFT_KINDS), sorted(set(drift.DRIFT_KINDS) - SEEN)


# --- live probe -----------------------------------------------------------------------------------
def test_probe_of_a_real_server_matches_its_database(walnut_env, corpus, fixture_db, monkeypatch):
    import databridge_mcp_ingest as mi

    monkeypatch.setenv("WALNUT_MCP_POOL", "0")
    root, _ = corpus
    db, _ = fixture_db
    mi.register_connection(fs_connection("both", root, db=db))
    assert mi.propose_plan("both").status == "approved"
    shape = drift.probe_source("both")
    assert shape.problems == []
    with sqlite3.connect(db) as raw:
        tables = [r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        want = {f"public.{t}": {r[1]: (r[2] or "text").lower()
                                for r in raw.execute(f'PRAGMA table_info("{t}")')}
                for t in tables}
    assert {ref: {c: v.type for c, v in t.columns.items()} for ref, t in shape.tables.items()} \
        == want
    assert "query" in shape.tools and shape.tools["write_file"].writes
    assert shape.item_fields and {"path", "name"} <= set(shape.item_fields)
    # probing twice, unchanged, is not drift
    assert drift.diff(shape, drift.probe_source("both")) == []

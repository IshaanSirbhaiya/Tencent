"""FXJ4 — Codex SECOND (final) review of mcp_ingest tabular + drift (R-022 f2): mapping, drift,
cards and the pipeline. One test (or more) per finding, each asserting the rule the code now
enforces with a non-vacuous check beside it. Synthetic data only; no network, no model."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import test_principal
from test_fxi4_tabular_drift import _prose_source, _snap
from test_w4_dialects import EngineClient, sqlite_exec

pytestmark = pytest.mark.real_approval


@pytest.fixture
def ws_vocab():
    from databridge_graph.packs import deployment

    with deployment("workspace") as vocab:
        yield vocab


@pytest.fixture
def facts_env(walnut_env, tmp_path, monkeypatch):
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(tmp_path / "facts"))
    monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(tmp_path / "passages"))
    return walnut_env


def _subject_and_ident(vocab):
    kind = next(k for k in sorted(vocab.node_kinds) if vocab.is_subject(k)
                and vocab.identifiers_for(k))
    return kind, vocab.identifiers_for(kind)[0]


def _delegation(delegated_by="Owner Person"):
    # INT5: minted from a sealed record (AA3); an unsealed delegation is now refused by design.
    from test_int3_integration import _delegation as sealed

    return sealed(delegated_by, adaptation_id="ad_fxj4")


def _register(source_id, **scope):
    from databridge_mcp_ingest import register_connection

    register_connection({"id": source_id, "transport": {"type": "stdio", "command": "uvx"},
                         "scope": scope})


# === #1 subject identities are never merged ======================================================
def test_1_a_subject_table_gets_no_merge_key_only_a_candidate_identifier(ws_vocab):
    from databridge_mcp_ingest.mapping import propose_rules

    kind, ident = _subject_and_ident(ws_vocab)
    snap = _snap(kind, ["id", "name", ident])            # the identifier: unique, non-null
    tm = propose_rules({kind: snap}, ws_vocab)[kind]
    assert tm.status == "proposed" and tm.node["kind"] == kind            # non-vacuous
    assert "canonical_id" not in tm.node                                  # no exact-merge key
    assert tm.node["candidate_identifier"] == ident
    # a non-subject keeps its natural exact-merge key (the rule is about subjects only)
    org = propose_rules({"invoice": _snap("invoice", ["id", "invoice_no", "title"])},
                        ws_vocab)["invoice"]
    assert org.node.get("canonical_id") is not None


def test_1_the_card_offers_a_subject_identifier_only_as_a_candidate(ws_vocab):
    from databridge_mcp_ingest.tabular.cards import card_text

    kind, ident = _subject_and_ident(ws_vocab)
    profile = {"ref": f"public.{kind}", "row_count": 3, "columns": [], "primary_key": ["id"]}
    text = card_text("s1", profile, None, extracted_at="2026-10-01", facts_file=None,
                     mapping={"status": "approved",
                              "node": {"kind": kind, "candidate_identifier": ident}})
    assert "CANDIDATE" in text and "never merged" in text
    assert "join to these rows" not in text


# === #2 delegation: sealed scope, in declared scope, never subjects, never writes ===============
def _approved_invoice(source_id, vocab):
    from databridge_mcp_ingest.mapping import (SourceMapping, _table_mapping, approve_mapping,
                                               save_mapping)

    tm = _table_mapping("invoice", _snap("invoice", ["id", "invoice_no", "title"]), "invoice",
                        {}, vocab, origin="rules", why="test")
    save_mapping(SourceMapping(source_id=source_id, tables={"invoice": tm}))
    approve_mapping(source_id, principal=test_principal("Owner Person"), vocab=vocab)


def _new_table_entry(vocab, table, cols, *, canonical=True):
    from databridge_mcp_ingest.mapping import _table_mapping

    tm = _table_mapping(table, _snap(table, cols), "invoice", {}, vocab, origin="rules",
                        why="test")
    entry = tm.model_dump(mode="json")
    if not canonical:
        entry["node"].pop("canonical_id", None)
    return entry


def test_2_a_delegated_new_table_may_not_carry_an_exact_merge_key(walnut_env, ws_vocab):
    from databridge_mcp_ingest.mapping import (MappingApprovalRefused,
                                               approve_delegated_additive, load_mapping)

    _register("g1")
    _approved_invoice("g1", ws_vocab)
    entry = _new_table_entry(ws_vocab, "invoice_archive", ["id", "invoice_no", "title"])
    assert entry["node"].get("canonical_id") is not None                 # non-vacuous
    with pytest.raises(MappingApprovalRefused, match="exact-merge key"):
        approve_delegated_additive("g1", "invoice_archive", entry, delegation=_delegation(),
                                   vocab=ws_vocab)
    assert "invoice_archive" not in load_mapping("g1").tables             # nothing written
    # the same table WITHOUT a merge key adds only its own rows: delegable
    plain = _new_table_entry(ws_vocab, "invoice_archive", ["id", "invoice_no", "title"],
                             canonical=False)
    m = approve_delegated_additive("g1", "invoice_archive", plain, delegation=_delegation(),
                                   vocab=ws_vocab)
    assert m.tables["invoice_archive"].status == "approved"


def test_2_a_delegation_stays_inside_the_declared_scope(walnut_env, ws_vocab):
    from databridge_mcp_ingest.mapping import (MappingApprovalRefused,
                                               approve_delegated_additive)

    _register("g2", tables_exclude=["secret_*"])
    _approved_invoice("g2", ws_vocab)
    entry = _new_table_entry(ws_vocab, "secret_ledger", ["id", "title"], canonical=False)
    with pytest.raises(MappingApprovalRefused, match="outside the declared scope"):
        approve_delegated_additive("g2", "secret_ledger", entry, delegation=_delegation(),
                                   vocab=ws_vocab)
    # no declared connection at all: the scope cannot be checked, so it is refused
    from databridge_mcp_ingest.mapping import SourceMapping, save_mapping

    save_mapping(SourceMapping(source_id="g2none", tables={}))
    with pytest.raises(MappingApprovalRefused, match="no declared connection"):
        approve_delegated_additive("g2none", "ledger", _new_table_entry(
            ws_vocab, "ledger", ["id", "title"], canonical=False), delegation=_delegation(),
            vocab=ws_vocab)


def test_2_a_delegation_never_maps_a_write_target(walnut_env, ws_vocab):
    from databridge_mcp_ingest.mapping import (MappingApprovalRefused, _writer_targets,
                                               approve_delegated_additive)
    from databridge_mcp_ingest.tabular.writer import load_writer_spec

    spec = load_writer_spec()                         # derived from the pack, not written here
    ref = f"{spec['schema']}.{spec['table']}"
    targets, why = _writer_targets()
    assert why is None and ref.lower() in targets                         # non-vacuous
    _register("g3")
    _approved_invoice("g3", ws_vocab)
    cols = [c["name"] for c in spec["columns"]]
    entry = _new_table_entry(ws_vocab, ref, cols, canonical=False)
    with pytest.raises(MappingApprovalRefused, match="WRITE target"):
        approve_delegated_additive("g3", ref, entry, delegation=_delegation(), vocab=ws_vocab)


def test_2_the_delegation_record_must_carry_the_sealed_scope_and_digest(walnut_env):
    from databridge_mcp_ingest.mapping import DELEGATION_SCOPE, delegation_scope_problems

    _register("g4")
    good = {"scope": DELEGATION_SCOPE, "policy_digest": "sha256:abc",
            "authenticated_by": "delegated:sha256:abc"}
    assert delegation_scope_problems("g4", "t", good) == []               # non-vacuous
    assert delegation_scope_problems("g4", "t", {**good, "scope": "anything"})
    assert delegation_scope_problems("g4", "t", {**good, "authenticated_by": "jwt"})


def test_2_a_delegated_change_may_not_shrink_the_approved_schema(walnut_env, ws_vocab):
    from databridge_mcp_ingest.mapping import (MappingApprovalRefused,
                                               approve_delegated_additive, load_mapping)

    _register("g5")
    _approved_invoice("g5", ws_vocab)
    entry = load_mapping("g5").tables["invoice"].model_dump(mode="json")
    entry["columns"] = [c for c in entry["columns"] if c != "title"] + ["region"]
    entry["node"]["attrs"] = {**entry["node"]["attrs"], "region": "region"}
    with pytest.raises(MappingApprovalRefused, match="loses column.*not additive"):
        approve_delegated_additive("g5", "invoice", entry, delegation=_delegation(),
                                   vocab=ws_vocab)


# === #9 a mapping may name only columns its table has ===========================================
@pytest.mark.parametrize("edit,where", [
    (lambda n, e: n.update(sources=["missing"]), "sources"),
    (lambda n, e: n["attrs"].update(x="missing"), "attr"),
    (lambda n, e: n.update(label="{missing}"), "label"),
    (lambda n, e: e.append({"kind": "references", "src": "invoice:{walnut_row_key}",
                            "dst": "organisation:{walnut_fk_missing}"}), "foreign key"),
])
def test_9_approval_refuses_a_mapping_naming_a_column_the_table_lacks(walnut_env, ws_vocab, edit,
                                                                      where):
    import yaml

    from databridge_mcp_ingest.mapping import (MappingApprovalRefused, SourceMapping,
                                               _table_mapping, approve_mapping, mapping_path,
                                               save_mapping)

    tm = _table_mapping("invoice", _snap("invoice", ["id", "invoice_no", "title"]), "invoice",
                        {}, ws_vocab, origin="rules", why="test")
    save_mapping(SourceMapping(source_id="r9", tables={"invoice": tm}))
    p = mapping_path("r9")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    edit(data["tables"]["invoice"]["node"], data["tables"]["invoice"]["edges"])
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(MappingApprovalRefused, match=f"{where}.*(names column|foreign-key)"):
        approve_mapping("r9", principal=test_principal(), vocab=ws_vocab)


def test_9_an_approval_is_bound_to_its_schema_and_rechecked_against_the_live_table(walnut_env,
                                                                                   ws_vocab):
    from databridge_mcp_ingest.mapping import (SourceMapping, _table_mapping, approve_mapping,
                                               effective_mapping, mapping_digest, save_mapping)

    snap = _snap("invoice", ["id", "invoice_no", "title"])
    tm = _table_mapping("invoice", snap, "invoice", {}, ws_vocab, origin="rules", why="test")
    assert mapping_digest(tm) != mapping_digest(tm.model_copy(update={"columns": ["id"]}))
    save_mapping(SourceMapping(source_id="r9b", tables={"invoice": tm}))
    m = approve_mapping("r9b", principal=test_principal(), vocab=ws_vocab)
    live_ok = effective_mapping(m, ws_vocab, snapshots={"invoice": snap})
    assert live_ok.tables["invoice"].status == "approved"                 # non-vacuous
    gone = _snap("invoice", ["id", "invoice_no"])                         # `title` dropped
    eff = effective_mapping(m, ws_vocab, snapshots={"invoice": gone})
    assert eff.tables["invoice"].status == "proposed"
    assert "live table does not have" in eff.tables["invoice"].rationale[-1]


# === #4 / #12 / #13 / #14 cards ==================================================================
def test_4_a_card_the_screen_does_not_allow_is_held_not_captured_or_indexed(facts_env, tmp_path,
                                                                            monkeypatch, ws_vocab):
    from databridge_mcp_ingest.screen import ScreenResult, approve_override
    from databridge_mcp_ingest.tabular import cards as cards_mod
    from databridge_mcp_ingest.tabular import pipeline

    db = _prose_source(tmp_path, "c4")
    real = pipeline.screen_text
    monkeypatch.setattr(pipeline, "screen_text", lambda text, cfg: ScreenResult(
        "review", 1.0, [{"rule": "card_signal"}], [], "", "signal", "approve")
        if text.startswith("# Schema card") else real(text, cfg))
    indexed: list = []
    monkeypatch.setattr(cards_mod, "index_items", lambda items, sid: indexed.extend(items) or {
        "indexed": len(items), "indexed_ids": [i["item_id"] for i in items]})
    rep = pipeline.run_tabular("c4", client=EngineClient(sqlite_exec(db)[0]), vocab=ws_vocab)
    assert "schema_card" not in [i["kind"] for i in indexed] and rep["cards"] == 0
    assert "prose" in [i["kind"] for i in indexed]                        # non-vacuous
    run_dir = Path(pipeline.source_dir("c4")) / rep["run_id"]
    held = [json.loads(x) for x in (run_dir / "cards.held.jsonl").read_text().splitlines()]
    [card] = [h for h in held if h.get("kind") == "schema_card"]
    approve_override("c4", card["item_id"], digest=card["digest"], principal=test_principal(),
                     reason="reviewed")
    indexed.clear()
    rep = pipeline.run_tabular("c4", client=EngineClient(sqlite_exec(db)[0]), vocab=ws_vocab)
    assert rep["cards"] == 1 and "schema_card" in [i["kind"] for i in indexed]


def test_12_a_retrieval_outage_is_not_recorded_as_indexed(facts_env, tmp_path, monkeypatch,
                                                         ws_vocab):
    from databridge_mcp_ingest.tabular import cards as cards_mod
    from databridge_mcp_ingest.tabular import pipeline

    db = _prose_source(tmp_path, "c12")
    real_index = cards_mod.index_items
    monkeypatch.setitem(__import__("sys").modules, "databridge_retrieval", None)
    out = real_index([{"sha256": "x", "uri": "u", "item_id": "i", "title": "t"}], "c12")
    assert out["status"] == "unavailable" and out["indexed_ids"] == []
    pipeline.run_tabular("c12", client=EngineClient(sqlite_exec(db)[0]), vocab=ws_vocab)
    state_p = pipeline.source_dir("c12") / "tabular.index_state.json"
    assert json.loads(state_p.read_text(encoding="utf-8")) == {}         # nothing recorded
    # retrieval back: everything is indexed now (unchanged content is NOT skipped)
    indexed: list = []
    monkeypatch.setattr(cards_mod, "index_items", lambda items, sid: indexed.extend(items) or {
        "indexed": len(items), "indexed_ids": [i["item_id"] for i in items]})
    pipeline.run_tabular("c12", client=EngineClient(sqlite_exec(db)[0]), vocab=ws_vocab)
    assert {i["kind"] for i in indexed} == {"schema_card", "prose"}


def test_13_cards_render_the_effective_mapping_not_a_forged_approval(facts_env, tmp_path,
                                                                    monkeypatch, ws_vocab):
    import yaml

    from databridge_mcp_ingest.mapping import mapping_path
    from databridge_mcp_ingest.tabular import cards as cards_mod
    from databridge_mcp_ingest.tabular import pipeline

    db = _prose_source(tmp_path, "c13")
    monkeypatch.setattr(cards_mod, "index_items", lambda items, sid: {"indexed_ids": []})
    pipeline.run_tabular("c13", client=EngineClient(sqlite_exec(db)[0]), vocab=ws_vocab)
    p = mapping_path("c13")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    [entry] = [e for t, e in data["tables"].items() if t.endswith("notes")]
    assert entry.get("node"), "a node was proposed for `notes`"           # non-vacuous
    entry["status"] = "approved"                                          # forged: no record
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    seen: list = []
    real = cards_mod.card_text
    monkeypatch.setattr(cards_mod, "card_text",
                        lambda *a, **kw: seen.append(kw.get("mapping")) or real(*a, **kw))
    pipeline.run_tabular("c13", client=EngineClient(sqlite_exec(db)[0]), vocab=ws_vocab)
    [m] = seen
    assert m["status"] == "proposed" and "NOT honoured" in m["rationale"][-1]


def test_14_an_unmeasured_row_count_does_not_crash_a_mapped_card():
    from databridge_mcp_ingest.tabular.cards import card_text

    profile = {"ref": "public.invoice", "row_count": None, "columns": [],
               "primary_key": ["id"]}
    text = card_text("s14", profile, None, extracted_at="2026-10-01", facts_file=None,
                     mapping={"status": "approved", "node": {"kind": "invoice"}},
                     graph_row_cap=100)
    assert "NOT MEASURED" in text and "UNKNOWN" in text


# === #7 / #16 / #18 drift =========================================================================
def _table(ref, cols, pk=("id",)):
    from databridge_mcp_ingest.drift import table_shape

    return table_shape(ref, [{"name": c, "type": "text"} for c in cols], list(pk), [])


def test_7_advancing_a_unit_refuses_to_absorb_an_unapproved_drift():
    from databridge_mcp_ingest.drift import DriftAdvanceRefused, SourceShape, advance, diff

    old = SourceShape(source_id="d7", tables={"t": _table("t", ["id", "email", "name"])})
    new = SourceShape(source_id="d7", tables={"t": _table("t", ["id", "name", "region"],
                                                          pk=("name",))})
    drifts = diff(old, new)
    added = [d for d in drifts if d.kind == "added_column"]
    assert added and len(drifts) > len(added)                             # non-vacuous
    with pytest.raises(DriftAdvanceRefused, match="nobody approved"):
        advance(old, new, ["table:t"], approved_drifts=added)
    # every drift of the unit approved: it moves
    moved = advance(old, new, ["table:t"], approved_drifts=drifts)
    assert moved.tables["t"].primary_key == ["name"]


def test_16_a_scope_exclusion_keeps_its_rule_and_reversal(walnut_env):
    from databridge_mcp_ingest.connections import SourceConnection
    from databridge_mcp_ingest.drift import SourceShape, probe_shape
    from databridge_mcp_ingest.plans import CrawlPlan, SqlSpec, StepSpec
    from test_fxi4_tabular_drift import _CannedSql

    conn = SourceConnection.model_validate({"id": "dr", "transport": {
        "type": "stdio", "command": "uvx"}, "scope": {"tables_exclude": ["db.c"]}})
    plan = CrawlPlan(source_id="dr", status="approved", origin="manual", steps=[
        StepSpec(id="t", strategy="tabular", sql=SqlSpec(tool="query", dialect="mysql"))])
    shape = probe_shape(conn, plan, _CannedSql(["a", "c"]))
    [ex] = shape.tables_out_of_scope
    assert ex["uri"] == "db.c" and ex["rule"] == "tables_exclude:db.c" and ex["reverse"]
    # a baseline written before this read its bare URIs as "rule not recorded", not invented
    legacy = SourceShape.model_validate({"source_id": "dr", "tables_out_of_scope": ["db.c"]})
    assert legacy.tables_out_of_scope == [{"uri": "db.c", "rule": None, "reverse": None}]


def test_18_a_resource_template_whose_mime_changed_is_drift():
    from databridge_mcp_ingest.drift import DRIFT_KINDS, SourceShape, diff

    old = SourceShape(source_id="d18", resource_templates={"file:///{path}": "text/plain"})
    same = SourceShape(source_id="d18", resource_templates={"file:///{path}": "text/plain"})
    new = SourceShape(source_id="d18", resource_templates={"file:///{path}": "application/pdf"})
    assert diff(old, same) == []                                          # non-vacuous
    [d] = diff(old, new)
    assert d.kind in DRIFT_KINDS and d.unit == "resource_template:file:///{path}"
    assert d.old == {"mime": "text/plain"} and d.new == {"mime": "application/pdf"}


# === E2E run 2 N-16: a non-subject row joins its document twin by its BUSINESS KEY ===============
def test_n16_db_rows_join_their_document_twins_by_the_business_key(tmp_path, ws_vocab):
    """Reproduces E2E-2026-10-01 run 2 N-16 (cross-source entities 6 -> 3): the customers table
    has a `domain` column, and the name rule picked it as the exact-merge key over the text
    primary key `customer_id` — so the DB customer never met the document organisation, which
    the document knows by its customer id. The expectation is DERIVED: every document twin
    whose canonical id is a business-key value of a table row must be joined by that row."""
    import datetime as dt

    from databridge_core.provenance import SourcePointer
    from databridge_graph.merge import upsert_node
    from databridge_graph.schema import GraphNode, NodeKind, node_id
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest.build import build_tables
    from databridge_mcp_ingest.mapping import propose_rules

    customers = {"schema": "public", "table": "customers", "primary_key": ["customer_id"],
                 "foreign_keys": [],
                 "columns": [{"name": "customer_id", "type": "character varying"},
                             {"name": "name", "type": "text"},
                             {"name": "domain", "type": "text"}],
                 "rows": [{"customer_id": "C-1001", "name": "Pellucid Freightways Ltd",
                           "domain": "pellucid-freightways.example"},
                          {"customer_id": "C-1002", "name": "Corriedale Grocers",
                           "domain": "corriedale.example"}]}
    invoices = {"schema": "public", "table": "invoices", "primary_key": ["invoice_no"],
                "foreign_keys": [{"column": "customer_id", "ref_schema": "public",
                                  "ref_table": "customers", "ref_column": "customer_id"}],
                "columns": [{"name": "invoice_no", "type": "character varying"},
                            {"name": "customer_id", "type": "character varying"}],
                "rows": [{"invoice_no": "INV-2026-0041", "customer_id": "C-1001"}]}
    snaps = {"public.customers": customers, "public.invoices": invoices}
    store = GraphStore(str(tmp_path / "g.db"))
    now = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    twins = {"public.customers": ("organisation", "Pellucid Freightways Ltd", "C-1001"),
             "public.invoices": ("invoice", "INV-2026-0041", "INV-2026-0041")}
    for kind, label, canon in twins.values():
        ptr = SourcePointer(source_id="files", kind="doc_span", document_id="d1", char_start=0,
                            char_end=len(label), capture_digest="0" * 64)
        upsert_node(store, GraphNode(id=node_id(NodeKind(kind), label.lower()),
                                     kind=NodeKind(kind), label=label, canonical_id=canon,
                                     attr_sources={"canonical_id": ptr}, sources=(ptr,),
                                     valid_from=now, system_from=now), ws_vocab)
    maps = propose_rules(snaps, ws_vocab)
    tables = [(s, f"sha-{t}", maps[t].model_dump()) for t, s in snaps.items()]
    result = build_tables(store, ws_vocab, source_id="crm", tables=tables,
                          retrieved_at=dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc),
                          mask_cfg=None)
    # derived: a twin is expected to join when its canonical id is the row's business key —
    # the table's single text primary key
    expected = {t for t, (_, _, canon) in twins.items()
                if any(r[snaps[t]["primary_key"][0]] == canon for r in snaps[t]["rows"])}
    assert len(expected) == 2                                             # non-vacuous
    joined = {m["kind"] for m in result["merged"]}
    assert joined == {twins[t][0] for t in expected}, (result["merged"], {
        t: maps[t].node.get("canonical_id") for t in snaps})
    for t in expected:
        assert maps[t].node["canonical_id"] == snaps[t]["primary_key"][0]


def test_1_a_legacy_subject_merge_key_is_re_proposed_as_a_candidate(walnut_env, ws_vocab):
    from databridge_mcp_ingest.mapping import (SourceMapping, TableMapping, ensure_mapping,
                                               load_mapping, save_mapping)

    kind, ident = _subject_and_ident(ws_vocab)
    snap = _snap(kind, ["id", "name", ident])
    legacy = TableMapping(status="approved", node={"kind": kind, "label": "{name}",
                                                   "sources": ["id"], "attrs": {},
                                                   "canonical_id": ident},
                          approval={"principal_id": "p", "authenticated_by": "x"})
    save_mapping(SourceMapping(source_id="lg1", tables={kind: legacy}))
    eff = ensure_mapping("lg1", {kind: snap}, ws_vocab)
    tm = load_mapping("lg1").tables[kind]
    assert tm.status == "proposed" and tm.approval is None
    assert "canonical_id" not in tm.node and tm.node["candidate_identifier"] == ident
    assert eff.tables[kind].status == "proposed"

"""EF3 — fixes for E2E-2026-10-01 (F-03, F-18, F-19, F-23) and Codex round 3 (auto_approve,
approve_plan fail-closed) in MCP ingestion. Offline; every expectation is derived from the inputs.
"""
from __future__ import annotations

import datetime as dt

import pytest

SOURCE = "crm_db"


@pytest.fixture
def workspace_vocab():
    from databridge_graph.packs import deployment

    with deployment("workspace") as vocab:
        yield vocab


@pytest.fixture
def store(tmp_path):
    from databridge_graph.store import GraphStore

    return GraphStore(str(tmp_path / "graph.db"))


def _doc_node(kind: str, key: str, label: str, canonical: str, *, source="files", doc="d1"):
    """A node as the document path would have written it: a span pointer, a grounded canonical."""
    from databridge_core.provenance import SourcePointer
    from databridge_graph.schema import GraphNode, NodeKind, node_id

    ptr = SourcePointer(source_id=source, kind="doc_span", document_id=doc, char_start=0,
                        char_end=len(label), capture_digest="0" * 64)
    now = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    return GraphNode(id=node_id(NodeKind(kind), key), kind=NodeKind(kind), label=label,
                     canonical_id=canonical, attr_sources={"canonical_id": ptr}, sources=(ptr,),
                     valid_from=now, system_from=now)


def _snapshots():
    customers = {"schema": "public", "table": "customers", "primary_key": ["customer_id"],
                 "foreign_keys": [],
                 "columns": [{"name": "customer_id", "type": "text"},
                             {"name": "name", "type": "text"}],
                 "rows": [{"customer_id": "C-1001", "name": "Pellucid Freightways Ltd"},
                          {"customer_id": "C-1002", "name": "Corriedale Grocers"}]}
    invoices = {"schema": "public", "table": "invoices", "primary_key": ["invoice_no"],
                "foreign_keys": [{"column": "customer_id", "ref_schema": "public",
                                  "ref_table": "customers", "ref_column": "customer_id"}],
                "columns": [{"name": "invoice_no", "type": "text"},
                            {"name": "customer_id", "type": "text"},
                            {"name": "status", "type": "text"}],
                "rows": [{"invoice_no": "INV-0041", "customer_id": "C-1001", "status": "paid"},
                         {"invoice_no": "INV-0063", "customer_id": "C-1002", "status": "open"}]}
    contacts = {"schema": "public", "table": "contacts", "primary_key": ["contact_id"],
                "foreign_keys": [{"column": "customer_id", "ref_schema": "public",
                                  "ref_table": "customers", "ref_column": "customer_id"}],
                "columns": [{"name": "contact_id", "type": "integer"},
                            {"name": "full_name", "type": "text"},
                            {"name": "email", "type": "text"},
                            {"name": "customer_id", "type": "text"}],
                "rows": [{"contact_id": 1, "full_name": "Rowan Achterberg",
                          "email": "rowan@pellucid.example", "customer_id": "C-1001"},
                         {"contact_id": 2, "full_name": "Ines Morrow",
                          "email": "ines@corriedale.example", "customer_id": "C-1002"}]}
    return {"public.customers": customers, "public.invoices": invoices,
            "public.contacts": contacts}


def _mapped(snaps, vocab):
    from databridge_mcp_ingest.mapping import propose_rules

    maps = propose_rules(snaps, vocab)
    out = []
    for tref, snap in snaps.items():
        tm = maps[tref]
        assert tm.status == "proposed" and tm.node, (tref, tm.rationale)
        out.append((snap, f"sha-{tref}", tm.model_dump()))
    return out


def _build(store, vocab, tables, mask_cfg=None):
    from databridge_mcp_ingest.build import build_tables

    return build_tables(store, vocab, source_id=SOURCE, tables=tables,
                        retrieved_at=dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc),
                        mask_cfg=mask_cfg)


def _open_edges_dangling(store) -> list[str]:
    return [r["id"] for r in store.open_edges()
            if store.open_node_row(r["src_id"]) is None or store.open_node_row(r["dst_id"]) is None]


# --- F-03 ----------------------------------------------------------------------------------------
def test_f03_rows_merged_onto_document_nodes_keep_their_edges(store, workspace_vocab):
    from databridge_graph.merge import upsert_node

    snaps = _snapshots()
    # the document path already knows the customer and one invoice by their natural ids
    for n in (_doc_node("organisation", "pellucid freightways ltd", "Pellucid Freightways Ltd",
                        "C-1001"),
              _doc_node("invoice", "inv-0041", "INV-0041", "INV-0041")):
        upsert_node(store, n, workspace_vocab)
    tables = _mapped(snaps, workspace_vocab)
    edge_specs = sum(len(tm["edges"]) for _, _, tm in tables)
    assert edge_specs > 0                                                   # non-vacuous
    result = _build(store, workspace_vocab, tables)
    assert _open_edges_dangling(store) == []
    assert result["dangling_edges"] == 0
    # every FK edge survived: one edge per row per mapped FK
    expected = sum(len(s["rows"]) for s, _, tm in tables for _ in tm["edges"])
    assert result["edges"] == expected == len(store.open_edges())
    # the merged DB customer's invoices are attached to the DOCUMENT node's id
    doc_org = "organisation:pellucid freightways ltd"
    assert any(doc_org in (r["src_id"], r["dst_id"]) for r in store.open_edges())
    assert result["merged"], "the merges are reported"


def test_f03_a_later_sync_resolves_a_reference_to_a_row_merged_in_an_earlier_run(
        store, workspace_vocab):
    from databridge_graph.merge import upsert_node

    snaps = _snapshots()
    upsert_node(store, _doc_node("organisation", "pellucid freightways ltd",
                                 "Pellucid Freightways Ltd", "C-1001"), workspace_vocab)
    tables = _mapped(snaps, workspace_vocab)
    _build(store, workspace_vocab, tables)
    # next sync: only invoices changed (a new invoice), customers unchanged and not rebuilt
    inv_snap, _, inv_tm = next(t for t in tables if t[0]["table"] == "invoices")
    changed = {**inv_snap, "rows": inv_snap["rows"] + [
        {"invoice_no": "INV-0070", "customer_id": "C-1001", "status": "open"}]}
    result = _build(store, workspace_vocab, [(changed, "sha-2", inv_tm)])
    assert result["dangling_edges"] == 0
    assert _open_edges_dangling(store) == []
    doc_org = "organisation:pellucid freightways ltd"
    new_edges = [r for r in store.open_edges() if "inv-0070" in r["id"]]
    assert new_edges and all(doc_org in (r["src_id"], r["dst_id"]) for r in new_edges)


def test_f03_dangling_edges_are_counted_truthfully(store, workspace_vocab):
    snaps = _snapshots()
    # invoices alone: every customer FK points at a row nobody has built
    inv = [t for t in _mapped(snaps, workspace_vocab) if t[0]["table"] == "invoices"]
    result = _build(store, workspace_vocab, inv)
    expected = sum(len(s["rows"]) for s, _, tm in inv for _ in tm["edges"])
    assert expected > 0
    assert result["dangling_edges"] == expected and result["edges"] == 0
    assert _open_edges_dangling(store) == []


def test_f03_a_subject_row_is_never_auto_merged_it_becomes_a_candidate(store, workspace_vocab):
    """person is the workspace subject kind: a DB contact whose declared identifier equals a
    document person's is linked only as a CANDIDATE a human selects."""
    from databridge_graph.merge import upsert_node

    snaps = _snapshots()
    doc_person = _doc_node("person", "rowan achterberg", "Rowan Achterberg",
                           "rowan@pellucid.example")
    upsert_node(store, doc_person, workspace_vocab)
    assert workspace_vocab.is_subject("person")
    tables = _mapped(snaps, workspace_vocab)
    contacts_tm = next(tm for s, _, tm in tables if s["table"] == "contacts")
    assert contacts_tm["node"]["kind"] == "person"
    # R-022 f2 #1: a subject has no exact-merge key; email only proposes candidates
    assert contacts_tm["node"].get("canonical_id") is None
    assert contacts_tm["node"].get("candidate_identifier") == "email"
    result = _build(store, workspace_vocab, tables)
    persons = [r for r in store.open_nodes() if r["kind"] == "person"]
    assert len(persons) == 1 + len(snaps["public.contacts"]["rows"])        # nothing fused
    cands = result["candidate_links"]
    assert [c["candidate"] for c in cands] == [doc_person.id]
    c = cands[0]
    assert c["requires_human_selection"] is True and c["status"] == "proposed"
    assert "rowan@pellucid.example" not in repr(c)                         # never the raw value
    assert not any(m["kind"] == "person" for m in result["merged"])


# --- F-18 ----------------------------------------------------------------------------------------
def _contact_rows(store, vocab):
    from databridge_graph.store import row_to_node
    from databridge_mcp_ingest.screen import load_screen

    snaps = _snapshots()
    tables = [t for t in _mapped(snaps, vocab) if t[0]["table"] in ("contacts", "customers")]
    result = _build(store, vocab, tables, mask_cfg=load_screen())
    nodes = [row_to_node(store, r) for r in store.open_nodes() if r["kind"] == "person"]
    return snaps, result, nodes


def test_f18_a_masked_value_points_at_a_derivation_not_the_raw_cell(store, workspace_vocab):
    snaps, result, nodes = _contact_rows(store, workspace_vocab)
    masked = {(r["row_id"], r["column"]) for r in result["redactions"]}
    assert masked, "the screen masks the contact emails"                   # non-vacuous
    raw = {str(r["contact_id"]): r for r in snaps["public.contacts"]["rows"]}
    checked = 0
    for n in nodes:
        for attr, ptr in n.attr_sources.items():
            if attr not in n.attrs or ptr.kind == "derived":
                continue
            # a table_cell pointer must hold EXACTLY the value shown beside it
            assert ptr.kind == "table_cell"
            assert str(raw[ptr.row_id][ptr.column]) == str(n.attrs[attr]), (attr, ptr)
        email_ptr = n.attr_sources.get("email")
        if email_ptr is not None and n.attrs.get("email") not in (None,) and \
                n.attrs["email"] not in {r["email"] for r in raw.values()}:
            assert email_ptr.kind == "derived"
            assert "mask" in (email_ptr.derivation or "")
            assert [p.kind for p in email_ptr.derived_from] == ["table_cell"]
            checked += 1
    assert checked == len(masked)


def test_f18_a_row_key_canonical_id_is_derived_not_the_pk_cell(store, workspace_vocab):
    snaps, result, nodes = _contact_rows(store, workspace_vocab)
    # R-022 f2 #1: a subject (contacts -> person) has NO exact-merge key at all, so every
    # contact's canonical id is its row key (masked or not, the email is never an identity)
    assert result["canonical_masked"] == [] and nodes                     # non-vacuous
    for n in nodes:
        ptr = n.attr_sources["canonical_id"]
        assert n.canonical_id.startswith(f"{SOURCE}/public.contacts/")
        assert ptr.kind == "derived" and "row identity" in (ptr.derivation or "")
        assert {p.column for p in ptr.derived_from} == {"contact_id"}


# --- F-23 + Codex (a) on mappings ----------------------------------------------------------------
def _follow_up_snapshot(rows=()):
    return {"schema": "public", "table": "follow_up_tasks", "primary_key": ["task_id"],
            "foreign_keys": [],
            "columns": [{"name": "task_id", "type": "bigint"},
                        {"name": "customer_id", "type": "text"},
                        {"name": "title", "type": "text"}],
            "rows": list(rows)}


@pytest.mark.real_approval
def test_f23_a_table_no_kind_fits_gets_a_fallback_PROPOSAL_not_an_approval(
        walnut_env, workspace_vocab):
    from databridge_graph.meta import NodeMetaKind
    from databridge_mcp_ingest.mapping import ensure_mapping, load_mapping
    from databridge_mcp_ingest.tabular.policy import load_policy

    meta = load_policy().get("unmapped_fallback_meta_kind")
    assert meta, "the fallback is policy, not code"
    bound = workspace_vocab.nodes_binding_to(NodeMetaKind(meta))
    expected = meta if meta in bound else (bound[0] if len(bound) == 1 else None)
    assert expected is not None
    snap = _follow_up_snapshot([{"task_id": 1, "customer_id": "C-1001", "title": "Chase"}])
    m = ensure_mapping(SOURCE, {"public.follow_up_tasks": snap}, workspace_vocab,
                       auto_approve=True)
    tm = m.tables["public.follow_up_tasks"]
    assert tm.status == "proposed" and tm.node["kind"] == expected
    assert any("fallback" in r for r in tm.rationale)
    assert load_mapping(SOURCE).tables["public.follow_up_tasks"].status == "proposed"


def test_f23_an_automatic_unmapped_entry_is_re_proposed_a_human_one_is_kept(
        walnut_env, workspace_vocab):
    from databridge_mcp_ingest.mapping import (SourceMapping, TableMapping, ensure_mapping,
                                               save_mapping)

    save_mapping(SourceMapping(source_id=SOURCE, tables={
        "public.follow_up_tasks": TableMapping(status="unmapped", origin="model",
                                               rationale=["model found no fitting kind"]),
        "public.notes": TableMapping(status="unmapped", origin="manual",
                                     rationale=["edited by dana"])}))
    snaps = {"public.follow_up_tasks": _follow_up_snapshot(),
             "public.notes": {**_follow_up_snapshot(), "table": "notes"}}
    m = ensure_mapping(SOURCE, snaps, workspace_vocab)
    assert m.tables["public.follow_up_tasks"].status == "proposed"
    assert m.tables["public.notes"].status == "unmapped"                    # a human decided


@pytest.mark.real_approval
def test_codex_a_auto_approve_never_approves_a_mapping_and_says_so(walnut_env, workspace_vocab):
    from databridge_mcp_ingest.mapping import ensure_mapping

    snaps = _snapshots()
    m = ensure_mapping(SOURCE, snaps, workspace_vocab, auto_approve=True)
    assert {t.status for t in m.tables.values()} == {"proposed"}
    assert m.approved_by is None
    assert all(any("auto_approve" in r and "ignored" in r for r in t.rationale)
               for t in m.tables.values())


# --- Codex (a)/(b) on crawl plans ----------------------------------------------------------------
def _tools(**annot):
    from databridge_mcp_ingest.client import ToolInfo

    schema = {"type": "object", "properties": {"path": {"type": "string"}},
              "required": ["path"]}
    names = ["list_directory", "read_text_file", "frobnicate", "delete_file"]
    return [ToolInfo(name=n, input_schema=schema, **annot.get(n, {})) for n in names]


def _docs_step(list_tool="list_directory", read_tool="read_text_file"):
    from databridge_mcp_ingest.plans import StepSpec

    return StepSpec.model_validate({
        "id": "files", "strategy": "documents",
        "list": {"tool": list_tool, "args": {"path": "{path}"}},
        "read": {"text_tool": read_tool, "text_args": {"path": "{path}"}}})


def _save_plan_from(steps, tools):
    from types import SimpleNamespace

    from databridge_mcp_ingest.plans import plan_from_tools, save_plan

    plan = plan_from_tools(SimpleNamespace(id="fs"), tools, has_resources=False, profiles=[])
    plan = plan.model_copy(update={"steps": steps, "problems": []})
    from databridge_mcp_ingest.plans import classify_plan_tools

    plan = plan.model_copy(update={"tool_classes": classify_plan_tools(steps, tools,
                                                                        plan.deny_tools)})
    save_plan(plan)
    return plan


def _approve(tools=None, **kw):
    """R-022 f1 #1/#3 (FXI3): an approval needs an AUTHENTICATED principal and is decided
    against the live server — here a fake one listing `tools`."""
    from conftest import test_principal
    from databridge_mcp_ingest.connections import connection_path, register_connection
    from databridge_mcp_ingest.plans import approve_plan

    if not connection_path("fs").exists():
        register_connection({"id": "fs", "transport": {"type": "stdio", "command": "x"}})
    live = list(tools if tools is not None else _tools())

    class _Live:
        supports_resources = False

        def list_tools(self):
            return live

    return approve_plan("fs", principal=test_principal("Dana"), client=_Live(), **kw)


def test_codex_b_a_read_only_plan_is_approvable(walnut_env):
    _save_plan_from([_docs_step()], _tools())
    assert _approve().status == "approved"


@pytest.mark.parametrize("list_tool,annot,why", [
    ("frobnicate", {}, "unclassified"),                       # nothing vouches for it
    ("delete_file", {}, "write"),                             # a write verb
    ("list_directory", {"list_directory": {"destructive": True}}, "write"),   # annotation
])
def test_codex_b_approve_refuses_write_and_unclassified_tools(walnut_env, list_tool, annot, why):
    from databridge_mcp_ingest.plans import PlanError, approve_plan, load_plan

    _save_plan_from([_docs_step(list_tool=list_tool)], _tools(**annot))
    with pytest.raises(PlanError, match=why):
        _approve(_tools(**annot))
    assert load_plan("fs").status == "proposed"


def test_codex_b_a_plan_edited_to_call_a_tool_it_never_classified_is_refused(walnut_env):
    from databridge_mcp_ingest.plans import PlanError, classify_plan_tools, load_plan, save_plan

    plan = _save_plan_from([_docs_step()], _tools())
    save_plan(plan.model_copy(update={"steps": [_docs_step(read_tool="frobnicate")]}))
    with pytest.raises(PlanError, match="unclassified"):
        _approve()
    # a legacy / edited plan file's classes are never trusted: FXI3 (Codex f1 #3) re-classifies
    # every step tool against the LIVE server at approval, so a file claiming a class for
    # `frobnicate` is still refused, and a file with no classes is judged on the live list alone
    save_plan(plan.model_copy(update={"steps": [_docs_step(read_tool="frobnicate")],
                                      "tool_classes": {"frobnicate": "read:annotation"}}))
    with pytest.raises(PlanError, match="unclassified"):
        _approve()
    assert load_plan("fs").status == "proposed"
    save_plan(plan.model_copy(update={"tool_classes": {}}))
    assert _approve().tool_classes == classify_plan_tools(plan.steps, _tools())


@pytest.mark.real_approval
def test_codex_a_auto_approve_never_approves_a_plan(walnut_env):
    from databridge_mcp_ingest.client import ToolInfo
    from databridge_mcp_ingest.connections import register_connection
    from databridge_mcp_ingest.plans import propose_plan

    register_connection({"id": "fs", "transport": {"type": "stdio", "command": "x"},
                         "auto_approve": True})

    class _Fake:
        supports_resources = False

        def list_tools(self):
            return _tools()

        def list_resources(self):
            return []

    plan = propose_plan("fs", client=_Fake(), profiles=[])
    assert plan.status == "proposed" and plan.approved_by is None
    assert any("auto_approve" in n and "ignored" in n for n in plan.notes)


def test_codex_b_the_approver_may_vouch_for_an_unknown_read_by_name_never_a_write(walnut_env):
    from databridge_mcp_ingest.plans import PlanError, approve_plan, load_plan

    _save_plan_from([_docs_step(list_tool="frobnicate")], _tools())
    plan = _approve(vouch_read_tools=["frobnicate"])
    assert plan.status == "approved"
    assert load_plan("fs").tool_classes["frobnicate"] == "read:vouched_by:Dana"
    _save_plan_from([_docs_step(list_tool="delete_file")], _tools())
    with pytest.raises(PlanError, match="write"):
        _approve(vouch_read_tools=["delete_file"])


# --- F-16: verify_run uses the extractor's span rule (case + whitespace fold), not `in` ---------
def _pointer_problems(label: str, text: str, start: int, end: int) -> list[str]:
    import json as _json

    from databridge_mcp_ingest import capture, runner

    sha = "f" * 64
    capture.put_text(sha, text)
    run_dir = runner.source_dir("vsrc") / "r1"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "pointers.jsonl").write_text(_json.dumps(
        {"entity": "organisation:x", "kind": "doc_span", "sha": sha, "start": start,
         "end": end, "label": label}) + "\n", encoding="utf-8")
    return [p for p in runner.verify_run("vsrc", "r1")["problems"] if "pointer" in p]


def test_f16_a_span_broken_across_lines_verifies(walnut_env):
    text = "Agreement with Pellucid\nFreightways   Ltd, signed.\n"
    s = text.index("Pellucid")
    e = text.index("Ltd") + len("Ltd")
    assert "Pellucid Freightways Ltd" not in text[s:e]                  # the old rule fails it
    assert _pointer_problems("Pellucid Freightways Ltd", text, s, e) == []


def test_f16_a_span_that_does_not_hold_the_label_still_fails(walnut_env):
    text = "Agreement with Pellucid Freight, signed.\n"
    s = text.index("Pellucid")
    assert _pointer_problems("Pellucid Freightways Ltd", text, s, s + len("Pellucid Freight"))


# --- F-31: a decided (blocked / review) item is not re-extracted while unchanged ----------------
def test_f31_an_unchanged_blocked_item_is_not_re_extracted(walnut_env, corpus, common_vocab,
                                                           fake_harness):
    from pathlib import Path

    import databridge_mcp_ingest as mi
    from conftest import fs_connection
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest.ledger import read_jsonl

    root, _ = corpus
    mi.register_connection(fs_connection("f31", root))
    mi.propose_plan("f31")
    db = walnut_env["WALNUT_GRAPH_DB_PATH"]
    db.parent.mkdir(parents=True, exist_ok=True)

    def run():
        rep = mi.run_ingest("f31", store=GraphStore(str(db)), harness=fake_harness)
        assert rep.status == "ok", rep.message
        d = Path(rep.run_dir)
        return {n: read_jsonl(d / f"{n}.jsonl") for n in ("extracted", "governance", "unchanged")}

    first = run()
    decided = {r["item_id"] for r in first["governance"] if r["decision"] in ("block", "review")}
    assert decided                                                        # non-vacuous
    second = run()
    assert not decided & {r["item_id"] for r in second["extracted"]}
    carried = {r["item_id"]: r for r in second["governance"] if r["item_id"] in decided}
    assert set(carried) == decided                                        # still counted
    assert all(r.get("carried_forward") for r in carried.values())
    assert decided <= {r["item_id"] for r in second["unchanged"]}
    # an owner override makes the item eligible again: it is re-screened. Since R-022 #2 an
    # override is an AUTHENTICATED principal's approval of the item's content digest, never a
    # `by:` name in a YAML file.
    from databridge_agent_runtime import register_authenticator
    from databridge_mcp_ingest.screen import approve_override
    iid = sorted(decided)[0]
    sha = {r["item_id"]: r.get("sha256") for r in first["extracted"]}[iid]
    approve_override("f31", iid, digest=sha,
                     principal=register_authenticator("test:ef3").issue("dana"),
                     reason="owner reviewed the item")
    third = run()
    assert iid in {r["item_id"] for r in third["extracted"]}


# --- F-33: report.json and refused.jsonl say what produced them (§5.8) --------------------------
@pytest.mark.parametrize("with_model", [True, False])
def test_f33_report_and_refusals_carry_produced_by(walnut_env, corpus, common_vocab,
                                                   fake_harness, with_model):
    import json as _json
    from pathlib import Path

    import databridge_mcp_ingest as mi
    from conftest import fs_connection
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest.ledger import read_jsonl

    root, _ = corpus
    mi.register_connection(fs_connection("f33", root))
    mi.propose_plan("f33")
    db = walnut_env["WALNUT_GRAPH_DB_PATH"]
    db.parent.mkdir(parents=True, exist_ok=True)
    rep = mi.run_ingest("f33", store=GraphStore(str(db)),
                        harness=fake_harness if with_model else None)
    d = Path(rep.run_dir)
    body = _json.loads((d / "report.json").read_text(encoding="utf-8"))
    pb = body.get("produced_by")
    assert pb and pb["component"] == "databridge_mcp_ingest" and pb["run_id"] == d.name
    stamps = [s for r in read_jsonl(d / "build.jsonl") for s in r.get("produced_by") or []]
    assert sorted(_json.dumps(s, sort_keys=True) for s in pb["models_used"]) == \
        sorted({_json.dumps(s, sort_keys=True) for s in stamps}) and \
        (bool(stamps) is with_model)
    refused = read_jsonl(d / "refused.jsonl")
    if not with_model:
        assert refused                                                    # non-vacuous
    assert all(r.get("produced_by") for r in refused)


# --- F-23: a changed table emits item_changed even when its rows are not (yet) in the graph -----
@pytest.mark.real_approval
def test_f23_a_changed_unbuilt_table_emits_item_changed(walnut_env, corpus, fixture_db,
                                                        common_vocab, monkeypatch):
    import sqlite3

    monkeypatch.setenv("WALNUT_MCP_POOL", "0")      # no pooled server holding the sqlite file

    import databridge_mcp_ingest as mi
    from conftest import fs_connection
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest.ledger import read_jsonl

    root, _ = corpus
    db, counts = fixture_db
    mi.register_connection({**fs_connection("f23", root, db=db), "auto_approve": False})
    plan = mi.propose_plan("f23")
    unknown = [t for t, c in plan.tool_classes.items() if c.startswith("unclassified")]
    from conftest import test_principal

    mi.approve_plan("f23", principal=test_principal("Dana"), vouch_read_tools=unknown)
    gdb = walnut_env["WALNUT_GRAPH_DB_PATH"]
    gdb.parent.mkdir(parents=True, exist_ok=True)
    events_path = walnut_env["WALNUT_EVENTS_PATH"]

    def run():
        before = len(read_jsonl(events_path)) if events_path.is_file() else 0
        rep = mi.run_ingest("f23", store=GraphStore(str(gdb)), harness=None)
        assert rep.status == "ok", rep.message
        from pathlib import Path
        tables = {r["item_id"]: r["uri"] for r in read_jsonl(Path(rep.run_dir) / "inventory.jsonl")
                  if r.get("kind") == "table" and r.get("item_id")}
        assert tables                                                     # non-vacuous
        return [e for e in read_jsonl(events_path)[before:] if e.get("item_id") in tables]

    first = run()                                  # no mapping approved: nothing built
    assert {e["type"] for e in first} <= {"item_added"}
    added = {e["uri"] for e in first}
    assert len(added) == len(counts)                                      # every table sighted
    c = sqlite3.connect(db, timeout=30)
    try:
        c.execute("INSERT INTO audit_log VALUES (2, '2026-09-02', 'agent wrote a row')")
        c.commit()
    finally:
        c.close()
    second = run()
    changed = [e for e in second if e["type"] == "item_changed"]
    assert len(changed) == 1 and "audit_log" in changed[0]["uri"]
    assert all(e.get("attrs", {}).get("graph") is False for e in changed)

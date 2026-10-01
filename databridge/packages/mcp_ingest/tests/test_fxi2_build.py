"""FXI2 — Codex first review of the mcp_ingest BUILD track (R-022-mcp-ingest-build).

One test group per finding (#1 … #14), plus the materialise cross-track item (a recorded digest of
the extracted text). Offline; every expectation is derived from the inputs, never written down.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from types import SimpleNamespace

import pytest
import yaml

from databridge_mcp_ingest import paths
from databridge_mcp_ingest.build import (CANON_KEY, FK_PREFIX, LISTING_TABLE, ROW_KEY,
                                         ProseBuilder, build_tables, document_kind,
                                         edge_kind_choice, edge_kind_for, table_rows)
from databridge_mcp_ingest.screen import (OverrideRefused, apply_masks, approve_override,
                                          load_overrides, load_screen, mandatory_rules,
                                          override_log_path, parse_screen, revoke_override,
                                          row_digest, row_ref, screen_text, ScreenError)

SOURCE = "crm_db"
NOW = dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc)


@pytest.fixture
def workspace_vocab():
    from databridge_graph.packs import deployment

    with deployment("workspace") as vocab:
        yield vocab


@pytest.fixture
def store(tmp_path):
    from databridge_graph.store import GraphStore

    return GraphStore(str(tmp_path / "graph.db"))


@pytest.fixture
def owner():
    """A principal ISSUED by a registered authenticator — what the gateway's auth layer hands on."""
    from databridge_agent_runtime import register_authenticator

    return register_authenticator("test:fxi2").issue("dana.owner")


def _default_screen():
    return load_screen(packs=[])


# --- #3: the credential gate is engine policy -----------------------------------------------------
PGP = "-----BEGIN PGP PRIVATE KEY BLOCK-----\nlQOYBF...\n"


def test_03_a_pack_reusing_a_reserved_id_with_a_narrower_pattern_cannot_disable_it(tmp_path):
    default = next(r for r in mandatory_rules() if r.pattern.search(PGP))
    narrow = "-----BEGIN RSA PRIVATE KEY-----"
    f = tmp_path / "screen.yaml"
    f.write_text(yaml.safe_dump({"version": 1, "decisive": [
        {"id": default.id, "pattern": narrow}]}), encoding="utf-8")
    cfg = load_screen(path=f)
    got = screen_text(PGP, cfg)
    assert got.decision == "block" and got.fired[0]["rule"] == default.id
    # the pack's own rule is kept beside it (additive), and the conflict is noted
    assert any(r.id == f"{default.id}.pack" for r in cfg.decisive)
    assert any("reserved" in n for n in cfg.notes)


def test_03_every_mandatory_rule_survives_every_shipped_pack_screen():
    shipped = sorted((paths.DATABRIDGE_ROOT / "packs").glob("*/screen.yaml"))
    assert shipped                                                          # non-vacuous
    for f in shipped:
        cfg = load_screen(path=f)
        pairs = {(r.id, r.pattern.pattern) for r in cfg.decisive}
        assert {(r.id, r.pattern.pattern) for r in mandatory_rules()} <= pairs, f


def test_03_a_hand_built_config_without_decisive_rules_still_blocks_credentials():
    cfg = parse_screen({"version": 1, "decisive": [], "signals": []}, "hand-built")
    assert cfg.decisive == []
    for rule in mandatory_rules():
        sample = {"private_key_block": "-----BEGIN PRIVATE KEY-----",
                  "cloud_access_key_id": "AKIA" + "Q" * 16}.get(rule.id)
        if sample is None:
            continue
        assert rule.pattern.search(sample)
        assert screen_text(f"x {sample} y", cfg).decision == "block", rule.id


# --- #11: duplicate declarations are not corroboration -------------------------------------------
def test_11_duplicate_signal_ids_or_patterns_are_refused():
    sig = {"id": "s", "pattern": "confidential", "weight": 1}
    with pytest.raises(ScreenError):
        parse_screen({"signals": [sig, dict(sig)]}, "t")
    with pytest.raises(ScreenError):
        parse_screen({"signals": [sig, {**sig, "id": "s2"}]}, "t")


def test_11_block_counts_distinct_signal_identities():
    cfg = parse_screen({"thresholds": {"block": 2, "review": 1, "min_signals_to_block": 2},
                        "signals": [{"id": "a", "pattern": "alpha", "weight": 2}]}, "t")
    # one signal with weight >= block threshold is still ONE signal: review, not block
    assert screen_text("alpha", cfg).decision == "review"


# --- #12: masking is tracked by span, never inferred from the value ------------------------------
def test_12_values_holding_star_or_leading_bracket_are_masked():
    cfg = parse_screen({"redactions": [
        {"id": "acct", "pattern": r"ACCT-[\w*]+", "replacement": "[ACCT]"},
        {"id": "ref", "pattern": r"\[REF-\d+\]", "replacement": "[R]"}]}, "t")
    for raw in ("ACCT-ab*cd", "[REF-12345]"):
        masked, red = apply_masks(f"see {raw} now", cfg.redactions)
        assert raw not in masked and len(masked) == len(f"see {raw} now") and red


def test_12_a_span_an_earlier_rule_masked_is_not_masked_again():
    cfg = parse_screen({"redactions": [
        {"id": "email", "pattern": r"[a-z]+@[a-z]+\.example", "replacement": "[EMAIL]"},
        {"id": "token", "pattern": r"\[EMAIL\]\**", "replacement": "[T]"}]}, "t")
    text = "mail ann@one.example now"
    masked, red = apply_masks(text, cfg.redactions)
    assert [r["rule"] for r in red] == ["email"] and len(masked) == len(text)


# --- #2: overrides need an authenticated principal -----------------------------------------------
def test_02_a_yaml_by_name_is_not_an_approval(walnut_env):
    root = paths.sources_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{SOURCE}.overrides.yaml").write_text(
        "allow:\n  - item_id: abc\n    by: admin\n", encoding="utf-8")
    got = load_overrides(SOURCE)
    assert "abc" not in got and got.ignored_legacy == 1


@pytest.mark.parametrize("who", [None, "admin", {"principal_id": "admin"}])
def test_02_approve_override_refuses_anything_but_an_issued_principal(walnut_env, who):
    with pytest.raises(OverrideRefused):
        approve_override(SOURCE, "item", digest="0" * 64, principal=who, reason="ok")
    assert not override_log_path(SOURCE).exists()


def test_02_a_hand_built_approver_is_refused(walnut_env):
    from databridge_agent_runtime import Approver

    with pytest.raises(OverrideRefused):
        approve_override(SOURCE, "item", digest="0" * 64,
                         principal=Approver("dana.owner", "test:fxi2"), reason="ok")


def test_02_an_approval_covers_exactly_the_content_digest_it_names(walnut_env, owner):
    d1, d2 = hashlib.sha256(b"v1").hexdigest(), hashlib.sha256(b"v2").hexdigest()
    rec = approve_override(SOURCE, "item", digest=d1, principal=owner, reason="pricing is public")
    assert rec["principal_id"] == owner.principal_id and rec["digest"] == d1 and rec["at"]
    got = load_overrides(SOURCE)
    assert "item" in got and got.allows("item", d1) and not got.allows("item", d2)
    revoke_override(SOURCE, "item", digest=d1, principal=owner, reason="withdrawn")
    assert not load_overrides(SOURCE).allows("item", d1)
    lines = override_log_path(SOURCE).read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["action"] for x in lines] == ["approve", "revoke"]   # append-only


def test_02_an_edited_approval_line_is_not_honoured(walnut_env, owner):
    d = hashlib.sha256(b"v1").hexdigest()
    approve_override(SOURCE, "item", digest=d, principal=owner, reason="ok")
    p = override_log_path(SOURCE)
    rec = json.loads(p.read_text(encoding="utf-8"))
    rec["item_id"] = "other"                                 # edited after the fact
    p.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    got = load_overrides(SOURCE)
    assert "other" not in got and got.rejected == 1


# --- #1: review-band rows are withheld unless approved --------------------------------------------
def _notes_table(rows):
    return {"schema": "public", "table": "notes", "primary_key": ["note_id"], "foreign_keys": [],
            "rows": rows}


def _notes_mapping():
    return {"node": {"kind": "organisation", "label": "{title}", "attrs": {}}, "edges": []}


def _review_text(cfg) -> str:
    """A title the default screen puts in the REVIEW band (derived, not assumed)."""
    for text in ("Total contract value schedule", "Salary review notes"):
        if screen_text(text, cfg).decision == "review":
            return text
    raise AssertionError("no review-band sample for this screen")


def test_01_a_review_row_is_never_built_without_an_approval(walnut_env, store, workspace_vocab):
    cfg = _default_screen()
    rows = [{"note_id": "N1", "title": _review_text(cfg)}, {"note_id": "N2", "title": "Hello"}]
    out = build_tables(store, workspace_vocab, source_id=SOURCE,
                       tables=[(_notes_table(rows), "a" * 64, _notes_mapping())],
                       retrieved_at=NOW, mask_cfg=cfg, screen_cfg=cfg)
    assert out["rows_review"] == 1 and out["nodes"] == len(rows) - 1
    withheld = out["blocked_rows"][0]
    assert withheld["decision"] == "review" and "approve_override" in withheld["reverse"]
    labels = {r["label"] for r in store.open_nodes()}
    assert rows[0]["title"] not in labels and "Hello" in labels


def test_01_an_authenticated_approval_admits_that_row_and_only_that_content(
        walnut_env, store, workspace_vocab, owner):
    cfg = _default_screen()
    row = {"note_id": "N1", "title": _review_text(cfg)}
    tref = "public.notes"
    approve_override(SOURCE, row_ref(SOURCE, tref, "N1"), digest=row_digest(row),
                     principal=owner, reason="owner approved")
    ov = load_overrides(SOURCE)
    out = build_tables(store, workspace_vocab, source_id=SOURCE,
                       tables=[(_notes_table([row]), "a" * 64, _notes_mapping())],
                       retrieved_at=NOW, mask_cfg=cfg, screen_cfg=cfg, overrides=ov)
    assert out["rows_review"] == 0 and out["nodes"] == 1
    changed = {**row, "title": row["title"] + " (revised)"}
    assert screen_text(changed["title"], cfg).decision == "review"
    out2 = build_tables(store, workspace_vocab, source_id=SOURCE,
                        tables=[(_notes_table([changed]), "b" * 64, _notes_mapping())],
                        retrieved_at=NOW, mask_cfg=cfg, screen_cfg=cfg, overrides=ov)
    assert out2["rows_review"] == 1 and out2["nodes"] == 0


# --- #7: a masked key never becomes a graph identity ---------------------------------------------
def test_07_a_masked_primary_key_holds_the_row(walnut_env, store, workspace_vocab):
    cfg = _default_screen()
    rows = [{"email": "ann@one.example", "name": "One"},
            {"email": "bob@two.example", "name": "Two"}]
    assert all(apply_masks(r["email"], cfg.redactions)[1] for r in rows)   # they are masked
    snap = {"schema": "public", "table": "people", "primary_key": ["email"], "foreign_keys": [],
            "rows": rows}
    tm = {"node": {"kind": "organisation", "label": "{name}", "canonical_id": "email",
                   "attrs": {}}, "edges": []}
    out = build_tables(store, workspace_vocab, source_id=SOURCE, tables=[(snap, "c" * 64, tm)],
                       retrieved_at=NOW, mask_cfg=cfg, screen_cfg=cfg)
    assert out["rows_held"] == len(rows) and out["nodes"] == 0
    dump = json.dumps(out, default=str) + json.dumps(
        [dict(r) for r in store.fetchall("SELECT * FROM nodes")], default=str)
    for r in rows:
        assert r["email"] not in dump


def test_07_a_masked_foreign_key_yields_no_referenced_identity(walnut_env):
    cfg = _default_screen()
    snap = {"schema": "public", "table": "orders", "primary_key": ["order_id"],
            "foreign_keys": [{"column": "owner_email", "ref_table": "people"}],
            "rows": [{"order_id": 7, "owner_email": "ann@one.example"}]}
    withheld: list = []
    rows = list(table_rows(snap, source_id=SOURCE, sha="d" * 64, mask_cfg=cfg, redactions=[],
                           blocked_rows=[], screen_cfg=cfg, withheld_fks=withheld))
    assert len(rows) == 1 and f"{FK_PREFIX}owner_email" not in rows[0]
    assert withheld and withheld[0]["column"] == "owner_email"
    assert "ann@one.example" not in json.dumps(rows[0], default=str)


# --- #10: a NULL cell still has a pointer --------------------------------------------------------
def test_10_every_present_column_including_null_gets_a_cell_pointer():
    snap = {"schema": "", "table": "t", "primary_key": ["id"], "foreign_keys": [],
            "rows": [{"id": 1, "note": None}]}
    (row,) = table_rows(snap, source_id=SOURCE, sha="e" * 64, mask_cfg=None, redactions=[],
                        blocked_rows=[])
    for column in snap["rows"][0]:
        ptr = row.pointer_for(column)
        assert ptr is not None and ptr.kind == "table_cell" and ptr.column == column
    assert row["note"] is None


# --- #6: F-03 across runs, with the DEFAULT (derived row-key) sources -------------------------
def _f03_tables(vocab):
    from databridge_mcp_ingest.mapping import propose_rules

    customers = {"schema": "public", "table": "customers", "primary_key": ["customer_id"],
                 "foreign_keys": [],
                 "columns": [{"name": "customer_id", "type": "text"},
                             {"name": "name", "type": "text"}],
                 "rows": [{"customer_id": "C-1001", "name": "Pellucid Freightways Ltd"}]}
    invoices = {"schema": "public", "table": "invoices", "primary_key": ["invoice_no"],
                "foreign_keys": [{"column": "customer_id", "ref_schema": "public",
                                  "ref_table": "customers", "ref_column": "customer_id"}],
                "columns": [{"name": "invoice_no", "type": "text"},
                            {"name": "customer_id", "type": "text"}],
                "rows": [{"invoice_no": "INV-0041", "customer_id": "C-1001"}]}
    snaps = {"public.customers": customers, "public.invoices": invoices}
    maps = propose_rules(snaps, vocab)
    out = {}
    for tref, snap in snaps.items():
        tm = maps[tref].model_dump()
        # the DEFAULTS: sources and label are the derived row-key pointer, no cell attrs — the
        # row's own cells then sit ONLY inside `derived_from` (the Codex #6 scenario)
        tm["node"].pop("sources", None)
        tm["node"]["label"] = None
        tm["node"]["attrs"] = {}
        out[tref] = (snap, f"sha-{tref}", tm)
    return out


def _doc_org(store, vocab, key="pellucid freightways ltd", canonical="C-1001"):
    from databridge_core.provenance import SourcePointer
    from databridge_graph.merge import upsert_node
    from databridge_graph.schema import GraphNode, NodeKind, node_id

    ptr = SourcePointer(source_id="files", kind="doc_span", document_id="d1", char_start=0,
                        char_end=5, capture_digest="0" * 64)
    doc = GraphNode(id=node_id(NodeKind("organisation"), key), kind=NodeKind("organisation"),
                    label=key.title(), canonical_id=canonical,
                    attr_sources={"canonical_id": ptr}, sources=(ptr,), valid_from=NOW,
                    system_from=NOW)
    upsert_node(store, doc, vocab)
    return doc


def _row_key_record(row_id="C-1001"):
    """What a row merged in an EARLIER run leaves on the node it merged onto when its only pointer
    is the default one: the DERIVED row-key pointer, the row's cell only inside `derived_from`."""
    from databridge_mcp_ingest.build import cell_pointer, derived_pointer

    return derived_pointer("row identity = 'crm_db/public.customers/' + primary key (customer_id)",
                           [cell_pointer(SOURCE, "public.customers", "customer_id", row_id,
                                         "1" * 64)])


def test_06_a_later_sync_finds_a_merged_row_through_its_derived_row_key(store, workspace_vocab):
    doc = _doc_org(store, workspace_vocab)
    store.append_sources(doc.id, [_row_key_record()])
    stored = [json.loads(r["source_pointer_json"]) for r in store.fetchall(
        "SELECT source_pointer_json FROM sources WHERE entity_id = ?", (doc.id,))]
    assert not any(p.get("kind") == "table_cell" for p in stored)    # only nested: the scenario
    inv = _f03_tables(workspace_vocab)["public.invoices"]
    assert inv[2]["edges"]                                                # non-vacuous
    out = build_tables(store, workspace_vocab, source_id=SOURCE, tables=[inv],
                       retrieved_at=NOW, mask_cfg=None)
    assert out["dangling_edges"] == 0 and out["edges"] == len(inv[2]["edges"])
    assert any(doc.id in (r["src_id"], r["dst_id"]) for r in store.open_edges())


def test_06_a_real_earlier_merge_still_resolves(store, workspace_vocab):
    doc = _doc_org(store, workspace_vocab)
    tables = _f03_tables(workspace_vocab)
    first = build_tables(store, workspace_vocab, source_id=SOURCE,
                         tables=[tables["public.customers"]], retrieved_at=NOW, mask_cfg=None)
    assert [m["into"] for m in first["merged"]] == [doc.id]              # merged in run 1
    inv = tables["public.invoices"]
    second = build_tables(store, workspace_vocab, source_id=SOURCE, tables=[inv],
                          retrieved_at=NOW, mask_cfg=None)
    assert second["dangling_edges"] == 0 and second["edges"] == len(inv[2]["edges"])


def test_06_two_nodes_citing_the_referenced_row_is_ambiguous_and_refused(store,
                                                                          workspace_vocab):
    a = _doc_org(store, workspace_vocab)
    b = _doc_org(store, workspace_vocab, key="pellucid holdings", canonical="PH-1")
    for n in (a, b):
        store.append_sources(n.id, [_row_key_record()])
    inv = _f03_tables(workspace_vocab)["public.invoices"]
    out = build_tables(store, workspace_vocab, source_id=SOURCE, tables=[inv],
                       retrieved_at=NOW, mask_cfg=None)
    assert out["edges"] == 0 and out["dangling_edges"] == len(inv[2]["edges"])
    assert out["ambiguous_endpoints"]
    assert set(out["ambiguous_endpoints"][0]["candidates"]) == {a.id, b.id}


# --- #5: the anchor's metadata cites the listing, and resolves ------------------------------------
def test_05_anchor_label_and_canonical_id_resolve_to_the_listing(walnut_env, store,
                                                                 workspace_vocab):
    from databridge_graph.store import row_to_node
    from databridge_mcp_ingest import capture
    from databridge_mcp_ingest.crawl import item_identity

    doc_kind, _ = document_kind(workspace_vocab)
    assert doc_kind is not None
    uri, title, text = "drive://root/contracts/a.pdf", "Contract A", "Nothing about titles here."
    iid = item_identity("files", uri)
    b = ProseBuilder(store, None, source_id="files", vocab=workspace_vocab, doc_kind=doc_kind,
                     screen=_default_screen())
    out = b.build(item_id=iid, sha="f" * 64, raw_text=text, masked_text=text, title=title,
                  uri=uri, retrieved_at=NOW)
    node = row_to_node(store, store.open_node_row(out.anchor_id))
    assert title not in text                                        # the Codex scenario

    def cell_value(p):
        snap = json.loads(capture.get_bytes(p.capture_digest))
        (row,) = [r for r in snap["rows"] if r[snap["primary_key"][0]] == p.row_id]
        return row[p.column]

    label = node.attr_sources["label"]
    assert label.kind == "table_cell" and label.table == LISTING_TABLE
    assert cell_value(label) == node.label == title
    canon = node.attr_sources["canonical_id"]
    basis = {p.column: cell_value(p) for p in canon.derived_from}
    assert basis["item_id"] == item_identity(basis["source"], basis["uri"]) == iid
    assert node.canonical_id == f"mcp:{basis['source']}:{basis['item_id']}"
    ledgered = [p for p in out.pointers if p["kind"] == "table_cell"]
    assert ledgered and ledgered[0]["row_id"] == iid


# --- #14: ambiguous ontology bindings are refused -------------------------------------------------
def test_14_two_document_kinds_without_a_choice_is_refused():
    vocab = SimpleNamespace(nodes_binding_to=lambda meta: ["memo", "report"])
    kind, note = document_kind(vocab)
    assert kind is None and "AMBIGUOUS" in note
    assert document_kind(vocab, "report")[0] == "report"
    # the one declared resolution: the kind named for the meta-kind itself
    from databridge_graph.meta import NodeMetaKind

    generic = NodeMetaKind.DOCUMENT.value
    named = SimpleNamespace(nodes_binding_to=lambda meta: ["invoice", generic])
    assert document_kind(named)[0] == generic


def _edge_vocab(decls: dict):
    return SimpleNamespace(edges_binding_to=lambda meta: list(decls),
                           edge=lambda name: decls[name])


def test_14_equally_specific_reference_edges_are_refused_not_ordered():
    tie = {"cites": SimpleNamespace(src=("doc",), dst=("org",)),
           "mentions": SimpleNamespace(src=("doc",), dst=("org",))}
    assert edge_kind_choice(_edge_vocab(tie), ("references",), "doc", "org") == \
        (None, sorted(tie))
    assert edge_kind_for(_edge_vocab(tie), ("references",), "doc", "org") is None
    specific = {"cites": SimpleNamespace(src=("doc",), dst=("org",)),
                "mentions": SimpleNamespace(src=("*",), dst=("*",))}
    assert edge_kind_choice(_edge_vocab(specific), ("references",), "doc", "org") == \
        ("cites", [])


# --- #4: resource scope sees the path, not the display name --------------------------------------
class _Resources:
    def __init__(self, entries):
        from databridge_mcp_ingest.client import ResourceInfo

        self._r = [ResourceInfo(uri=u, name=n, mime="application/pdf") for u, n in entries]

    def list_resources(self):
        return list(self._r)


@pytest.mark.parametrize("uri", ["drive://root/private/secret.pdf",
                                 "drive://root/private%2Fsecret.pdf",
                                 "drive://root/public/../private/secret.pdf"])
def test_04_a_blocked_path_blocks_a_resource_however_its_uri_is_spelled(uri):
    from databridge_mcp_ingest.connections import Scope
    from databridge_mcp_ingest.crawl import decide, enumerate_resources

    scope = Scope(roots=["drive://root"], blocked=["private/**"])
    step = SimpleNamespace(id="r", uri_prefix="")
    (item,) = enumerate_resources(_Resources([(uri, "secret.pdf")]), step, "s", scope=scope)
    assert item.rel == "private/secret.pdf"
    assert decide(item, scope, collected_so_far=0, holds=False).outcome == "blocked"


def test_04_a_uri_that_climbs_out_of_its_root_is_excluded():
    from databridge_mcp_ingest.connections import Scope
    from databridge_mcp_ingest.crawl import decide, enumerate_resources

    scope = Scope(roots=["drive://root"])
    step = SimpleNamespace(id="r", uri_prefix="")
    (item,) = enumerate_resources(_Resources([("drive://root/../other/x.pdf", "x.pdf")]), step,
                                  "s", scope=scope)
    got = decide(item, scope, collected_so_far=0, holds=False)
    assert got.outcome == "excluded" and got.rule == "unresolvable_path"


def test_04_without_roots_the_prefix_is_the_root():
    from databridge_mcp_ingest.connections import Scope
    from databridge_mcp_ingest.crawl import decide, enumerate_resources

    scope = Scope(blocked=["private/**"])
    step = SimpleNamespace(id="r", uri_prefix="drive://root/")
    (item,) = enumerate_resources(_Resources([("drive://root/private/s.pdf", "s.pdf")]), step,
                                  "s", scope=scope)
    assert item.rel == "private/s.pdf"
    assert decide(item, scope, collected_so_far=0, holds=False).outcome == "blocked"


# --- #8: MIME-only binary documents ---------------------------------------------------------------
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_08_an_extensionless_item_declared_by_mime_is_fetched_as_binary():
    from databridge_mcp_ingest.client import ContentPart, ToolResult
    from databridge_mcp_ingest.crawl import Item, fetch_document

    calls = []

    class Client:
        def call_tool(self, name, args):
            calls.append(name)
            return ToolResult(parts=(ContentPart(kind="blob", data=b"PK\x03\x04", mime=DOCX),))

    read = SimpleNamespace(resource_uri=None, binary_tool="read_bin", text_tool="read_text",
                           binary_extensions=[".docx"], binary_args={"id": "{id}"},
                           text_args={"id": "{id}"})
    step = SimpleNamespace(strategy="tools", read=read)
    item = Item(source_id="s", step_id="d", kind="document", uri="gdrive://abc",
                name="Kick-off notes", rel="Kick-off notes", mime=DOCX, ctx={"id": "abc"})
    data, mime = fetch_document(Client(), step, item)
    assert calls == ["read_bin"] and data.startswith(b"PK") and mime == DOCX


def test_08_office_conversion_names_the_stream_by_the_declared_mime(monkeypatch):
    from databridge_mcp_ingest import extract

    pytest.importorskip("docling")
    seen = []

    class Conv:
        def convert(self, stream):
            seen.append(stream.name)
            return SimpleNamespace(document=SimpleNamespace(export_to_markdown=lambda: "ok"))

    monkeypatch.setattr(extract, "_office_converter", lambda: Conv())
    got = extract.extract_text(b"PK\x03\x04", name="Kick-off notes", mime=DOCX)
    assert got.text == "ok" and seen == ["item" + extract.ext_of_mime(DOCX)]
    assert seen[0].endswith(".docx")


# --- #9: identifier quoting follows the dialect ---------------------------------------------------
def test_09_fetch_table_quotes_identifiers_by_the_dialect():
    from databridge_mcp_ingest.client import ContentPart, ToolResult
    from databridge_mcp_ingest.crawl import Item, fetch_table
    from databridge_mcp_ingest.plans import sql_dialect

    dialect = sql_dialect("mysql")
    q = dialect.get("quote")
    assert q and q != '"'                                           # non-vacuous: not ANSI
    sent = []

    class Client:
        def call_tool(self, name, args):
            sql = args["sql"]
            sent.append(sql)
            if "LIMIT" in sql and "ORDER BY" in sql and "SELECT *" in sql:
                rows = [{"id": 1}]
            elif "COUNT(" in sql.upper() and "information_schema" not in sql.lower():
                rows = [{"n": 1}]
            elif "constraint" in sql.lower() or "key_column_usage" in sql.lower():
                rows = [{"column_name": "id", "constraint_type": "PRIMARY KEY"}]
            else:
                rows = [{"column_name": "id", "data_type": "int"}]
            return ToolResult(parts=(ContentPart(kind="text", text=json.dumps(rows)),))

    sqlspec = SimpleNamespace(tool="q", arg="sql", extra_args={}, parse={"mode": "json_rows"},
                              dialect="mysql")
    step = SimpleNamespace(sql=sqlspec)
    item = Item(source_id="s", step_id="t", kind="table", uri="shop.orders", name="orders",
                rel="shop.orders", ctx={"schema": "shop", "table": "orders"})
    _, snap = fetch_table(Client(), step, item, limit=5)
    select = next(s for s in sent if "SELECT *" in s)
    open_, close = (q[0], q[-1])
    assert f"ORDER BY {open_}id{close}" in select and '"id"' not in select
    assert f"{open_}orders{close}" in select
    assert snap["primary_key"] == ["id"]


# --- #13: UTF-16 with a BOM is text ----------------------------------------------------------------
def test_13_bom_marked_utf16_text_is_extracted():
    from databridge_mcp_ingest.extract import ExtractionFailed, extract_text

    body = "Quarterly notes\nline two"
    for data in (body.encode("utf-16"), b"\xfe\xff" + body.encode("utf-16-be")):
        assert extract_text(data, name="notes.txt").text == body
    with pytest.raises(ExtractionFailed):
        extract_text(b"abc\x00def", name="notes.txt")


# --- cross-track (materialise R-021 round 2): the extracted text's digest -----------------------
def test_xt_extracted_text_carries_its_own_sha256_which_a_caller_cannot_set():
    from databridge_mcp_ingest.extract import Extracted, extract_text, text_digest

    got = extract_text("Olá".encode("utf-8"), name="a.md")
    assert got.text_sha256 == hashlib.sha256(got.text.encode("utf-8")).hexdigest()
    assert text_digest(got.text) == got.text_sha256
    assert Extracted("a", "text", (), "forged").text_sha256 == text_digest("a")


def test_xt_a_tampered_conversion_cache_entry_is_not_used(tmp_path, monkeypatch):
    from databridge_mcp_ingest import extract

    monkeypatch.setenv("WALNUT_CACHE_DIR", str(tmp_path / "cache"))
    answers = iter(["first", "second"])
    monkeypatch.setattr(extract, "extract_text",
                        lambda data, **kw: extract.Extracted(next(answers), "docling:fake"))
    sha = "ab" * 32
    got, hit = extract.extract_text_cached(b"%PDF", sha=sha, name="x.pdf")
    assert (got.text, hit) == ("first", False)
    path = extract.convert_cache_path(sha, "pdf")
    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["text_sha256"] == extract.text_digest("first")
    assert extract.extract_text_cached(b"%PDF", sha=sha, name="x.pdf")[1] is True
    rec["text"] = "tampered"
    path.write_text(json.dumps(rec), encoding="utf-8")
    got2, hit2 = extract.extract_text_cached(b"%PDF", sha=sha, name="x.pdf")
    assert (got2.text, hit2) == ("second", False)


# --- cross-track (FXI1 R-022-runner): the table build ledgers derived pointers + item_id --------
def test_xt_table_pointers_ledger_derived_pointers_with_item_id_and_all_resolve(
        walnut_env, store, workspace_vocab):
    from databridge_mcp_ingest import capture
    from databridge_mcp_ingest.runner import _PointerResolver

    cfg = _default_screen()
    snap = {"schema": "public", "table": "customers", "primary_key": ["customer_id"],
            "foreign_keys": [],
            "rows": [{"customer_id": "C-1", "name": "Quillon", "email": "ann@one.example",
                      "note": None}]}
    sha = capture.put_bytes(json.dumps(snap, sort_keys=True).encode("utf-8"))
    tm = {"node": {"kind": "organisation", "label": "{name}",
                   "attrs": {"email": "{email}", "note": "{note}"}}, "edges": []}
    out = build_tables(store, workspace_vocab, source_id=SOURCE, tables=[(snap, sha, tm)],
                       retrieved_at=NOW, mask_cfg=cfg, screen_cfg=cfg,
                       item_ids={sha: "item-customers"})
    ptrs = out["pointers"]
    def leaves(p):
        return [p] if p["kind"] != "derived" else [x for b in p["basis"] for x in leaves(b)]

    assert any(p["kind"] == "derived" for p in ptrs)                      # non-vacuous
    assert {x["kind"] for p in ptrs for x in leaves(p)} == {"table_cell"}
    assert all(p.get("item_id") == "item-customers" for p in ptrs)
    assert all(p.get("basis") for p in ptrs if p["kind"] == "derived")
    resolver = _PointerResolver()
    assert all(resolver.resolves(p) for p in ptrs), [p for p in ptrs if not resolver.resolves(p)]
    # every pointer the node carries in the store is in the ledger
    node_ptrs = store.sources_for(store.open_nodes()[0]["id"])
    assert len([p for p in ptrs if p["entity"] == store.open_nodes()[0]["id"]]) >= len(node_ptrs)

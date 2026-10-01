"""FXJ2 — Codex SECOND (final) review of the mcp_ingest BUILD track (f2-mi-build, R-022 round 2).

One group per finding (#1 … #7). Offline; every expectation is derived from the inputs.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from databridge_mcp_ingest import capture, paths
from databridge_mcp_ingest.build import (LISTING_TABLE, ProseBuilder, build_tables,
                                         derived_pointer, document_kind)
from databridge_mcp_ingest.screen import (OverrideRefused, _record_digest, apply_masks,
                                          approve_override, load_overrides, load_screen,
                                          override_log_path, revoke_override, screen_text)

SOURCE = "crm_db"
NOW = dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def approval_key(tmp_path, monkeypatch):
    """Every test here seals with its own key file, outside the sources directory."""
    monkeypatch.delenv("WALNUT_APPROVAL_KEY", raising=False)
    kp = tmp_path / "keys" / "approval.key"
    monkeypatch.setenv("WALNUT_APPROVAL_KEY_FILE", str(kp))
    return kp


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
    from databridge_agent_runtime import register_authenticator

    return register_authenticator("test:fxj2").issue("dana.owner")


def _screen():
    return load_screen(packs=[])


def _lines(p):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _write(p, recs):
    p.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs), encoding="utf-8")


# --- #1: a stored approval is sealed and chained --------------------------------------------------
def _digest(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def test_f2_01_an_appended_forgery_with_a_recomputed_digest_is_not_an_approval(walnut_env, owner):
    d = _digest("v1")
    approve_override(SOURCE, "real", digest=d, principal=owner, reason="ok")
    assert load_overrides(SOURCE).allows("real", d)                    # non-vacuous
    p = override_log_path(SOURCE)
    (legit,) = _lines(p)
    forged = {**legit, "item_id": "forged", "seq": legit["seq"] + 1,
              "prev": legit["record_digest"], "seal": "0" * 64}
    forged["record_digest"] = _record_digest(forged)             # what the f1 check accepted
    _write(p, [legit, forged])
    got = load_overrides(SOURCE)
    assert not got.allows("forged", d) and got.broken
    assert not got.allows("real", d)                       # a tampered log honours nothing


def test_f2_01_an_unsealed_line_is_ignored_and_the_chain_still_verifies(walnut_env, owner):
    d = _digest("v1")
    approve_override(SOURCE, "real", digest=d, principal=owner, reason="ok")
    p = override_log_path(SOURCE)
    (legit,) = _lines(p)
    unsealed = {k: v for k, v in legit.items() if k not in ("seal", "seq", "prev")}
    unsealed["item_id"] = "forged"
    unsealed["record_digest"] = _record_digest(unsealed)
    _write(p, [legit, unsealed])
    got = load_overrides(SOURCE)
    assert got.allows("real", d) and not got.allows("forged", d)
    assert got.rejected == 1 and not got.broken


def test_f2_01_a_removed_line_breaks_the_chain(walnut_env, owner):
    d = _digest("v1")
    approve_override(SOURCE, "a", digest=d, principal=owner, reason="ok")
    approve_override(SOURCE, "b", digest=d, principal=owner, reason="ok")
    revoke_override(SOURCE, "b", digest=d, principal=owner, reason="withdrawn")
    p = override_log_path(SOURCE)
    recs = _lines(p)
    assert [r["seq"] for r in recs] == list(range(1, len(recs) + 1))
    _write(p, [recs[0], recs[2]])                          # drop the middle line
    got = load_overrides(SOURCE)
    assert got.broken and not got.allows("a", d)
    with pytest.raises(OverrideRefused):                   # a broken log is not extended
        approve_override(SOURCE, "c", digest=d, principal=owner, reason="ok")


def test_f2_01_a_line_sealed_under_another_key_is_refused(walnut_env, owner, approval_key):
    d = _digest("v1")
    approve_override(SOURCE, "real", digest=d, principal=owner, reason="ok")
    approval_key.write_text("f" * 64, encoding="utf-8")    # the key the line was sealed with is gone
    assert not load_overrides(SOURCE).allows("real", d)


def test_f2_01_a_key_file_inside_the_sources_directory_is_refused(walnut_env, owner, monkeypatch):
    monkeypatch.setenv("WALNUT_APPROVAL_KEY_FILE", str(paths.sources_dir() / "approval.key"))
    with pytest.raises(OverrideRefused):
        approve_override(SOURCE, "x", digest=_digest("v"), principal=owner, reason="ok")
    assert not override_log_path(SOURCE).exists()


# --- #2: tool-listed document names are canonicalised before scope matching ----------------------
def _listing_step():
    lst = SimpleNamespace(tool="list_dir", args={"path": "{path}"}, parse=None, uri="",
                          recurse=True, dir_values=["dir"], meta_fields=[], page=None,
                          root_arg_default=None, item_mime=None)
    return SimpleNamespace(id="d", list=lst, roots=None)


def _enumerate(monkeypatch, listing: dict, scope):
    from databridge_mcp_ingest import crawl

    monkeypatch.setattr(crawl, "_list_pages", lambda client, spec, args: [args["path"]])
    monkeypatch.setattr(crawl, "parse_output", lambda text, parse: listing.get(text, []))
    dirs: list = []
    items = list(crawl.enumerate_documents(None, _listing_step(), scope, "s", on_dir=dirs.append))
    return items, dirs


@pytest.mark.parametrize("name", ["private%2Fsecret.pdf", "x/../private/secret.pdf",
                                  "x\\..\\private\\secret.pdf"])
def test_f2_02_a_blocked_path_blocks_a_listed_document_however_its_name_is_spelled(
        monkeypatch, name):
    from databridge_mcp_ingest.connections import Scope
    from databridge_mcp_ingest.crawl import decide

    scope = Scope(roots=["/srv/root"], blocked=["private/**"])
    listing = {"/srv/root": [{"name": name, "type": "file"}, {"name": "ok.pdf", "type": "file"}]}
    items, _ = _enumerate(monkeypatch, listing, scope)
    got = {i.name: decide(i, scope, collected_so_far=0, holds=False).outcome for i in items}
    assert got == {name: "blocked", "ok.pdf": "collect"}


def test_f2_02_include_must_hold_for_every_spelling(monkeypatch):
    from databridge_mcp_ingest.connections import Scope
    from databridge_mcp_ingest.crawl import decide

    scope = Scope(roots=["/srv/root"], include=["public/**"])
    listing = {"/srv/root": [{"name": "public/..%2Fprivate%2Fsecret.pdf", "type": "file"},
                             {"name": "public/a.pdf", "type": "file"}]}
    items, _ = _enumerate(monkeypatch, listing, scope)
    got = {i.name: decide(i, scope, collected_so_far=0, holds=False).outcome for i in items}
    assert got == {"public/..%2Fprivate%2Fsecret.pdf": "excluded", "public/a.pdf": "collect"}


def test_f2_02_a_directory_whose_name_resolves_to_a_blocked_one_is_never_entered(monkeypatch):
    from databridge_mcp_ingest.connections import Scope

    scope = Scope(roots=["/srv/root"], blocked=["private/**"])
    inside = {"name": "secret.pdf", "type": "file"}
    listing = {"/srv/root": [{"name": "x%2F..%2Fprivate", "type": "dir"}],
               "/srv/root/x%2F..%2Fprivate": [inside]}         # what entering it would list
    items, dirs = _enumerate(monkeypatch, listing, scope)
    assert items == [] and [d["outcome"] for d in dirs] == ["blocked"]


@pytest.mark.parametrize("entry", [{"name": "..", "type": "file"},
                                   {"name": "../../etc/passwd.txt", "type": "file"},
                                   {"name": "ok.pdf", "type": "file",
                                    "path": "/srv/root/private/secret.pdf"},
                                   {"name": "ok.pdf", "type": "file", "path": "/elsewhere/ok.pdf"}])
def test_f2_02_a_name_or_path_that_leaves_its_directory_is_excluded(monkeypatch, entry):
    from databridge_mcp_ingest.connections import Scope
    from databridge_mcp_ingest.crawl import decide

    scope = Scope(roots=["/srv/root"])
    good = {"name": "fine.pdf", "type": "file", "path": "/srv/root/fine.pdf"}
    items, _ = _enumerate(monkeypatch, {"/srv/root": [entry, good]}, scope)
    got = [(i.name, decide(i, scope, collected_so_far=0, holds=False)) for i in items]
    assert [(n, d.outcome, d.rule) for n, d in got] == [
        (entry["name"], "excluded", "unresolvable_path"), ("fine.pdf", "collect", "")]


# --- #3: nested chunk-relative pointers are re-minted against the document ------------------------
class _Harness:
    pass


def test_f2_03_a_derived_attribute_pointer_is_remapped_at_every_depth_and_ledgered(
        walnut_env, store, workspace_vocab):
    from databridge_core import SourcePointer
    from databridge_graph.kinds import NodeKind
    from databridge_graph.schema import GraphNode, node_id
    from databridge_graph.store import row_to_node
    from databridge_mcp_ingest.runner import _PointerResolver

    name = "Quillon Ltd"
    text = ("plain words " * 170) + name + (" more words" * 40)
    sha = hashlib.sha256(text.encode()).hexdigest()
    capture.put_text(sha, text)
    doc_kind, _ = document_kind(workspace_vocab)
    b = ProseBuilder(store, _Harness(), source_id="files", vocab=workspace_vocab,
                     doc_kind=doc_kind, screen=_screen())
    plan = b.prepare(item_id="item-1", sha=sha, raw_text=text, masked_text=text, title="T",
                     uri="files://T", retrieved_at=NOW)
    at = text.index(name)
    job = next(j for j in plan.jobs if j.start <= at and at + len(name) <= j.end and j.start)
    local = at - job.start

    def span(s, e):
        return SourcePointer(source_id="item-1", kind="doc_span", document_id="item-1",
                             char_start=s, char_end=e)

    kind = NodeKind("organisation")
    node = GraphNode(id=node_id(kind, "quillon-ltd"), kind=kind, label=name,
                     canonical_id=name,
                     attr_sources={"canonical_id": derived_pointer(
                         "canonical id = the grounded name", [span(local, local + len(name))])},
                     sources=(span(local, local + len(name)),), valid_from=NOW,
                     system_from=store.now())
    empty = SimpleNamespace(entities=[], relations=[], refused=[], produced_by=None,
                            subject_candidates=[])
    full = SimpleNamespace(entities=[node], relations=[], refused=[], produced_by=None,
                           subject_candidates=[])
    out = b.apply(plan, [((full if j is job else empty), 1.0) for j in plan.jobs])
    stored = row_to_node(store, store.open_node_row(node.id))
    (leaf,) = stored.attr_sources["canonical_id"].derived_from
    assert (leaf.char_start, leaf.char_end) == (at, at + len(name))
    assert text[leaf.char_start:leaf.char_end] == name
    attr = [p for p in out.pointers if p.get("attr") == "canonical_id"]
    assert attr and attr[0]["kind"] == "derived" and attr[0]["basis"][0]["start"] == at
    resolver = _PointerResolver()
    assert all(resolver.resolves(p) for p in out.pointers)


def test_f2_03_a_pointer_kind_the_prose_path_never_mints_is_refused(walnut_env, store,
                                                                     workspace_vocab):
    from databridge_core import SourcePointer
    from databridge_graph.kinds import NodeKind
    from databridge_graph.schema import GraphNode, node_id
    from databridge_mcp_ingest.build import BuildError

    text = "plain words " * 20 + "Quillon Ltd"
    b = ProseBuilder(store, _Harness(), source_id="files", vocab=workspace_vocab,
                     doc_kind=document_kind(workspace_vocab)[0], screen=_screen())
    plan = b.prepare(item_id="item-2", sha="e" * 64, raw_text=text, masked_text=text, title="T",
                     uri="files://T", retrieved_at=NOW)
    cell = SourcePointer(source_id="x", kind="table_cell", table="t", column="c", row_id="1")
    kind = NodeKind("organisation")
    node = GraphNode(id=node_id(kind, "q"), kind=kind, label="Quillon Ltd", sources=(cell,),
                     valid_from=NOW, system_from=store.now())
    res = SimpleNamespace(entities=[node], relations=[], refused=[], produced_by=None,
                          subject_candidates=[])
    with pytest.raises(BuildError):
        b.apply(plan, [(res, 1.0)])
    assert store.open_node_row(node.id) is None                     # rolled back, never written


# --- #4: listing metadata is screened before it becomes a label -----------------------------------
def _anchor(store, vocab, title):
    from databridge_graph.store import row_to_node
    from databridge_mcp_ingest.crawl import item_identity

    uri = "drive://root/a.txt"
    iid = item_identity("files", uri)
    b = ProseBuilder(store, None, source_id="files", vocab=vocab,
                     doc_kind=document_kind(vocab)[0], screen=_screen())
    text = "Nothing sensitive in the body."
    out = b.build(item_id=iid, sha=hashlib.sha256(text.encode()).hexdigest(), raw_text=text,
                  masked_text=text, title=title, uri=uri, retrieved_at=NOW)
    return out, row_to_node(store, store.open_node_row(out.anchor_id)), iid


def _cell_value(p):
    snap = json.loads(capture.get_bytes(p.capture_digest))
    (row,) = [r for r in snap["rows"] if r[snap["primary_key"][0]] == p.row_id]
    return row[p.column]


def test_f2_04_a_title_carrying_a_credential_never_becomes_a_label(walnut_env, store,
                                                                   workspace_vocab):
    secret = "AKIA" + "Q" * 16
    title = f"keys {secret}.txt"
    assert screen_text(title, _screen()).decision == "block"            # derived, not assumed
    out, node, iid = _anchor(store, workspace_vocab, title)
    assert secret not in node.label and node.label.startswith("document ")
    label = node.attr_sources["label"]
    assert label.kind == "derived"
    (leaf,) = label.derived_from
    assert leaf.table == LISTING_TABLE and _cell_value(leaf) == iid
    assert node.label == f"document {_cell_value(leaf)[:12]}"
    assert any("LISTING_METADATA_BLOCK" in r for r in out.refused)
    assert secret not in json.dumps(out.pointers) + json.dumps(out.refused)


def test_f2_04_a_masked_title_is_labelled_masked_with_a_derived_pointer(walnut_env, store,
                                                                        workspace_vocab):
    email = "ann@one.example"
    title = f"Notes from {email}"
    cfg = _screen()
    assert screen_text(title, cfg).decision == "allow" and apply_masks(title, cfg.redactions)[1]
    _, node, _ = _anchor(store, workspace_vocab, title)
    assert email not in node.label and len(node.label) == len(title)
    label = node.attr_sources["label"]
    assert label.kind == "derived" and "masked" in label.derivation
    assert _cell_value(label.derived_from[0]) == title


def test_f2_04_a_plain_title_is_unchanged(walnut_env, store, workspace_vocab):
    _, node, _ = _anchor(store, workspace_vocab, "Quarterly plan")
    assert node.label == "Quarterly plan" and node.attr_sources["label"].kind == "table_cell"


# --- #5 / #7: keyless tables ----------------------------------------------------------------------
def _keyless(rows):
    return {"schema": "public", "table": "contacts", "primary_key": [], "foreign_keys": [],
            "rows": rows}


_TM = {"node": {"kind": "organisation", "label": "{name}", "attrs": {}}, "edges": []}


def test_f2_05_a_keyless_row_with_a_masked_value_is_held(walnut_env, store, workspace_vocab):
    cfg = _screen()
    rows = [{"name": "One", "email": "ann@one.example"}, {"name": "Two", "email": None}]
    assert apply_masks(rows[0]["email"], cfg.redactions)[1]
    out = build_tables(store, workspace_vocab, source_id=SOURCE,
                       tables=[(_keyless(rows), "a" * 64, _TM)], retrieved_at=NOW,
                       mask_cfg=cfg, screen_cfg=cfg)
    held = [b for b in out["blocked_rows"] if b["decision"] == "held"]
    assert [b["rule"] for b in held] == ["KEYLESS_ROW_MASKED"] and held[0]["columns"] == ["email"]
    assert out["nodes"] == 1                                # the unmasked keyless row still builds
    assert rows[0]["email"] not in json.dumps(out, default=str) + json.dumps(
        [dict(r) for r in store.fetchall("SELECT * FROM nodes")], default=str)


def test_f2_07_an_empty_row_is_held_and_recorded(walnut_env, store, workspace_vocab):
    cfg = _screen()
    rows = [{}, {"name": "Two"}]
    out = build_tables(store, workspace_vocab, source_id=SOURCE,
                       tables=[(_keyless(rows), "a" * 64, _TM)], retrieved_at=NOW,
                       mask_cfg=cfg, screen_cfg=cfg)
    assert [b["rule"] for b in out["blocked_rows"]] == ["NO_POINTER_COLUMNS"]
    assert out["rows_held"] == 1 and out["nodes"] == len(rows) - 1


# --- #6: concurrent conversions of the same bytes ------------------------------------------------
def test_f2_06_two_workers_converting_the_same_bytes_both_succeed(tmp_path, monkeypatch):
    from databridge_mcp_ingest import extract

    monkeypatch.setenv("WALNUT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(extract, "extract_text",
                        lambda data, **kw: extract.Extracted("converted", "docling:fake"))
    gate = threading.Barrier(2, timeout=10)
    real_replace = extract.os.replace
    tmps: list = []

    def replace(src, dst):
        tmps.append(str(src))
        try:
            gate.wait()                      # both writers have written their temp file
        except threading.BrokenBarrierError:
            pass
        return real_replace(src, dst)

    monkeypatch.setattr(extract.os, "replace", replace)
    errors: list = []
    results: list = []

    def work():
        try:
            results.append(extract.extract_text_cached(b"%PDF", sha="cd" * 32, name="x.pdf"))
        except Exception as exc:                                     # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    assert not errors, errors
    assert len(results) == 2 and all(r[0].text == "converted" for r in results)
    assert len(set(tmps)) == 2                                   # one temp file per writer
    assert extract.extract_text_cached(b"%PDF", sha="cd" * 32, name="x.pdf")[1] is True


# --- cross-track (FXJ4 mapping): a subject's `candidate_identifier` proposes, never merges --------
def _doc_person(store, vocab, email):
    from databridge_core import SourcePointer
    from databridge_graph.kinds import NodeKind
    from databridge_graph.merge import upsert_node
    from databridge_graph.schema import GraphNode, node_id

    ptr = SourcePointer(source_id="files", kind="doc_span", document_id="d", char_start=0,
                        char_end=5, capture_digest="a" * 64)
    kind = NodeKind("person")
    n = GraphNode(id=node_id(kind, "rowan"), kind=kind, label="Rowan", canonical_id=email,
                  attr_sources={"canonical_id": ptr}, sources=(ptr,), valid_from=NOW,
                  system_from=store.now())
    return upsert_node(store, n, vocab).id


@pytest.mark.parametrize("masked", [False, True])
def test_xt_a_subject_candidate_identifier_offers_a_candidate_and_never_merges(
        walnut_env, store, workspace_vocab, masked):
    email = "rowan@pellucid.example"
    assert workspace_vocab.is_subject("person")
    doc_id = _doc_person(store, workspace_vocab, email)
    snap = {"schema": "public", "table": "contacts", "primary_key": ["contact_id"],
            "foreign_keys": [], "rows": [{"contact_id": 1, "name": "Rowan", "email": email}]}
    tm = {"node": {"kind": "person", "label": "{name}", "candidate_identifier": "email",
                   "attrs": {}}, "edges": []}
    cfg = _screen() if masked else None
    out = build_tables(store, workspace_vocab, source_id=SOURCE, tables=[(snap, "a" * 64, tm)],
                       retrieved_at=NOW, mask_cfg=cfg)
    persons = [r for r in store.open_nodes() if r["kind"] == "person"]
    assert len(persons) == 2 and not out["merged"]                     # nothing fused
    row_node = next(r for r in persons if r["id"] != doc_id)
    assert row_node["canonical_id"] != email                         # never an identity
    if masked:
        assert out["candidate_links"] == [] and out["candidate_masked"]
    else:
        (c,) = out["candidate_links"]
        assert c["candidate"] == doc_id and c["requires_human_selection"] is True
        assert email not in repr(c)


# --- E2E run 2 N-17: a merged mention never leaves a label its pointer does not ground ------------
def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _merge_scenario(store, vocab):
    """An organisation already in the graph as 'Larkspur Analytics Ltd' (a table cell); a document
    mentions it as 'Larkspur Analytics', which the graph's merge rule folds onto that node."""
    from databridge_core import SourcePointer
    from databridge_graph.kinds import NodeKind
    from databridge_graph.merge import upsert_node
    from databridge_graph.schema import GraphNode, node_id

    kind = NodeKind("organisation")
    cell = SourcePointer(source_id="db", kind="table_cell", table="public.orgs", column="name",
                         row_id="1", capture_digest="b" * 64)
    seed = GraphNode(id=node_id(kind, "larkspur analytics ltd"), kind=kind,
                     label="Larkspur Analytics Ltd", sources=(cell,), valid_from=NOW,
                     system_from=store.now())
    upsert_node(store, seed, vocab)
    surface = "Larkspur Analytics"
    text = f"A note about {surface} and its People & Culture team, with plans for the year."
    sha = hashlib.sha256(text.encode()).hexdigest()
    capture.put_text(sha, text)
    b = ProseBuilder(store, _Harness(), source_id="files", vocab=vocab,
                     doc_kind=document_kind(vocab)[0], screen=_screen())
    plan = b.prepare(item_id="item-n17", sha=sha, raw_text=text, masked_text=text, title="Note",
                     uri="files://note.txt", retrieved_at=NOW)
    (job,) = plan.jobs
    at = text.index(surface)
    span = SourcePointer(source_id="item-n17", kind="doc_span", document_id="item-n17",
                         char_start=at - job.start, char_end=at - job.start + len(surface))
    mention = GraphNode(id=node_id(kind, "larkspur analytics"), kind=kind, label=surface,
                        sources=(span,), valid_from=NOW, system_from=store.now())
    team = "People & Culture"
    t_at = text.index(team) - job.start
    other = GraphNode(id=node_id(kind, "people & culture"), kind=kind, label=team,
                      sources=(SourcePointer(source_id="item-n17", kind="doc_span",
                                             document_id="item-n17", char_start=t_at,
                                             char_end=t_at + len(team)),),
                      valid_from=NOW, system_from=store.now())
    from databridge_graph.kinds import EdgeKind
    from databridge_graph.schema import GraphEdge

    rel = GraphEdge(id=f"{other.id}|part_of|{mention.id}", src_id=other.id, dst_id=mention.id,
                    kind=EdgeKind("part_of"),
                    sources=(SourcePointer(source_id="item-n17", kind="doc_span",
                                           document_id="item-n17", char_start=span.char_start,
                                           char_end=t_at + len(team)),),
                    valid_from=NOW, system_from=store.now())
    res = SimpleNamespace(entities=[mention, other], relations=[rel], refused=[],
                          produced_by=None, subject_candidates=[])
    return b.apply(plan, [(res, 1.0)]), seed, text


def test_n17_every_doc_span_on_a_merged_node_and_its_edges_grounds_the_label(
        walnut_env, store, workspace_vocab):
    from databridge_graph.store import row_to_node

    out, seed, text = _merge_scenario(store, workspace_vocab)
    node = row_to_node(store, store.open_node_row(seed.id))
    assert any(e["dst_id"] == seed.id for e in store.open_edges())       # it did merge + link
    # the label the node already had (grounded by its own cell) is kept, never relabelled to a
    # surface form its other pointers do not say
    assert node.label == seed.label
    checked = 0
    for p in node.sources:
        if p.kind == "doc_span":
            assert _norm(node.label) in _norm(text[p.char_start:p.char_end]), p
            checked += 1
    assert any(e["src_id"] != out.anchor_id for e in store.open_edges())   # the relation too
    for e in store.open_edges():
        if seed.id not in (e["dst_id"], e["src_id"]):
            continue
        for p in store.sources_for(e["id"]):
            if p.kind == "doc_span":
                assert _norm(node.label) in _norm(text[p.char_start:p.char_end]), p
            checked += 1
    assert checked                                                       # non-vacuous
    # the mention is still cited — as a derivation over the span that says it
    mentions = [p for p in node.sources if p.kind == "derived"]
    assert mentions and text[mentions[0].derived_from[0].char_start:
                             mentions[0].derived_from[0].char_end] == "Larkspur Analytics"


def test_xt_graph_ids_name_every_written_id_and_each_has_a_ledgered_pointer(
        walnut_env, store, workspace_vocab):
    from databridge_mcp_ingest.runner import _PointerResolver

    out, seed, _ = _merge_scenario(store, workspace_vocab)
    written = {r["id"] for r in store.open_nodes()} - {""} | {e["id"] for e in store.open_edges()}
    assert set(out.graph_ids) == written
    ledgered = {p["entity"] for p in out.pointers}
    assert written <= ledgered
    resolver = _PointerResolver()
    assert all(resolver.resolves(p) for p in out.pointers)


# --- E2E run 2 N-16: a document's mention of a declared business key joins its DB twin ----------
_INV = "INV-2026-0041"


def _prose_mention(store, vocab, kind_name, surface, item="item-n16"):
    from databridge_core import SourcePointer
    from databridge_graph.kinds import NodeKind
    from databridge_graph.schema import GraphNode, node_id

    text = f"Please settle invoice {surface} by the end of the month."
    sha = hashlib.sha256(text.encode()).hexdigest()
    capture.put_text(sha, text)
    b = ProseBuilder(store, _Harness(), source_id="files", vocab=vocab,
                     doc_kind=document_kind(vocab)[0], screen=_screen())
    plan = b.prepare(item_id=item, sha=sha, raw_text=text, masked_text=text, title="Reminder",
                     uri=f"files://{item}.txt", retrieved_at=NOW)
    (job,) = plan.jobs
    at = text.index(surface) - job.start
    kind = NodeKind(kind_name)
    node = GraphNode(id=node_id(kind, surface.lower()), kind=kind, label=surface,
                     sources=(SourcePointer(source_id=item, kind="doc_span", document_id=item,
                                            char_start=at, char_end=at + len(surface)),),
                     valid_from=NOW, system_from=store.now())
    res = SimpleNamespace(entities=[node], relations=[], refused=[], produced_by=None,
                          subject_candidates=[])
    return b.apply(plan, [(res, 1.0)]), text


def _invoice_table(store, vocab, label="{invoice_no}"):
    snap = {"schema": "public", "table": "invoices", "primary_key": ["invoice_id"],
            "foreign_keys": [], "rows": [{"invoice_id": 41, "invoice_no": _INV,
                                          "amount": "1200.00"}]}
    sha = capture.put_bytes(json.dumps(snap, sort_keys=True).encode("utf-8"))
    tm = {"node": {"kind": "invoice", "label": label, "canonical_id": "invoice_no",
                   "attrs": {"amount": "amount"}}, "edges": []}
    return build_tables(store, vocab, source_id="larkspur_db", tables=[(snap, sha, tm)],
                        retrieved_at=NOW, mask_cfg=None)


def _invoices(store):
    return [r for r in store.open_nodes() if r["kind"] == "invoice"]


def test_n16_a_document_mention_of_a_declared_key_joins_the_db_row_db_first(
        walnut_env, store, workspace_vocab):
    from databridge_graph.store import row_to_node

    # a row label nothing like the mention, so no similarity rule can be what joins them
    _invoice_table(store, workspace_vocab, label="Invoice {invoice_id}")
    out, text = _prose_mention(store, workspace_vocab, "invoice", _INV)
    (inv,) = _invoices(store)
    node = row_to_node(store, inv)
    assert node.canonical_id == _INV
    # the document's sighting is on the ONE invoice node, and every pointer still resolves
    leaves = [q for p in node.sources for q in ([p] if p.kind != "derived"
                                                 else p.derived_from)]
    assert any(p.kind == "doc_span" and text[p.char_start:p.char_end] == _INV for p in leaves)
    from databridge_mcp_ingest.runner import _PointerResolver

    assert all(_PointerResolver().resolves(p) for p in out.pointers)


def test_n16_a_db_row_joins_the_document_mention_of_its_key_document_first(
        walnut_env, store, workspace_vocab):
    from databridge_graph.store import row_to_node

    _prose_mention(store, workspace_vocab, "invoice", _INV)
    assert len(_invoices(store)) == 1                                 # the mention alone
    result = _invoice_table(store, workspace_vocab)
    (inv,) = _invoices(store)
    _n = row_to_node(store, inv)
    assert _n.canonical_id == _INV and _n.attrs.get("amount") == "1200.00"
    assert [m["matched_by"] for m in result["merged"]] == ["declared_key"]


def test_n16_a_subject_mention_never_gets_a_merge_key(walnut_env, store, workspace_vocab):
    """A person whose grounded text equals another person's declared identifier stays its own
    mention: subjects never get a merge key (hard constraint 1)."""
    from databridge_core import SourcePointer
    from databridge_graph.kinds import NodeKind
    from databridge_graph.merge import upsert_node
    from databridge_graph.schema import GraphNode, node_id

    email = "rowan@pellucid.example"
    kind = NodeKind("person")
    cell = SourcePointer(source_id="db", kind="table_cell", table="t", column="email",
                         row_id="1", capture_digest="b" * 64)
    upsert_node(store, GraphNode(id=node_id(kind, "db-rowan"), kind=kind, label=email,
                                 canonical_id=email, attr_sources={"canonical_id": cell},
                                 sources=(cell,), valid_from=NOW, system_from=store.now()),
                workspace_vocab, id_only=True)
    _prose_mention(store, workspace_vocab, "person", email)
    persons = [r for r in store.open_nodes() if r["kind"] == "person"]
    assert len(persons) == 2
    assert [r["canonical_id"] for r in persons if r["id"] != node_id(kind, "db-rowan")] == [None]

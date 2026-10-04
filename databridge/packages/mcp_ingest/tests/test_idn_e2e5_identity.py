"""IDN — E2E run 5 identity findings, on BOTH estates (workspace Larkspur and finance).

* **W-02 / W-07** (High, regression from run 4): cross-source joins depended on which source was
  read first. Run 4 built the database while the files were still being read, so most document
  mentions met the keyed customer rows and joined. Run 5 read every document first: "Tamsinbay
  Water Authority", "Leak Detection Discovery", "Corriedale" and "Pellucid" became keyless nodes
  the keyed rows (written `id_only`) never met, and the founder's question lost Corriedale.
  The fix re-applies, after each run, the rules a mention meets at write time (`merge.reconcile_
  keyless`). Here both orders are built and must reach the same keyed records.
* **F-04** (High, hard constraint 1): finance CRM contacts and relationship managers absorbed email
  mentions BY NAME. Here every person mention, in either order, stays its own document-scoped
  node and no registry person cites a document span.
* **F-08**: one node per document, from the source item, never the title.

Every input is the estates' own data (tables read through the real capture path, documents read
from the fixture folders), every mapping is the RULES' proposal for that data, and every
expectation is derived from the data: a document names a record when the record's own name (or,
for a kind whose pack allows name similarity, its leading words) appears in the text. A fake model
reports exactly those spans, so the real extractor, the real document builder, the real table
builder and the real post-run step are what is under test.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import zipfile
from pathlib import Path

import pytest

import estates

NOW = dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc)
FIX = estates.FIXTURES
WS_FILES = FIX / "workspace_demo" / "files"
FIN = FIX / "packs" / "finance"

# per estate: org tables (table, name columns), person tables, document folders -> document kind
SPEC = {
    "workspace": {
        "orgs": {"public.customers": ("name",), "public.projects": ("name",)},
        "people": {"public.contacts": ("full_name",), "public.employees": ("full_name",)},
        "docs": [(WS_FILES, "document")],
    },
    "finance": {
        "orgs": {"crm.account_org": ("org_name",)},
        "people": {"crm.contact": ("first_name", "last_name"),
                   "crm.relationship_manager": ("full_name",)},
        "docs": [(FIN / "mailbox" / "messages", "message"), (FIN / "filings", "filing"),
                 (FIN / "credit_memos", "credit_memo")],
    },
}
DOC_CAP = 12                    # documents per folder (those naming a person first, sorted)
KEYLESS = "keyless"              # expectation: this surface must NOT reach a keyed record


# --- the test's own statements of the rules (never imported from the code under test) ----------
_SUFFIXES = {"inc", "incorporated", "ltd", "limited", "llc", "corp", "corporation", "co",
             "company", "pte", "plc", "gmbh", "sa", "ag", "nv", "bv", "holdings", "group"}


def _words(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in _SUFFIXES]


def _docx_text(path: Path) -> str:
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    paras = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
    return "\n".join("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)) for p in paras)


def _read(path: Path) -> str | None:
    if path.suffix == ".docx":
        return _docx_text(path)
    if path.suffix in (".md", ".txt", ".eml"):
        return path.read_text(encoding="utf-8", errors="replace")
    return None


def _occurs(text: str, surface: str):
    if surface not in text:
        return []
    return [m.start() for m in re.finditer(rf"(?<!\w){re.escape(surface)}(?!\w)", text)]


# --- estates -------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def estate_data(tmp_path_factory):
    out = {}
    for name, spec in SPEC.items():
        d = tmp_path_factory.mktemp(f"idn_{name}")
        db = estates.build_estate(name, d / "e.sqlite")
        client = estates.engine_client(db)
        snaps = {t: estates.snapshot(client, *t.split(".")) for t in
                 [*spec["orgs"], *spec["people"]]}
        db.close()
        out[name] = snaps
    return out


@pytest.fixture(params=tuple(SPEC))
def est(request, estate_data, tmp_path, monkeypatch, walnut_env):
    from databridge_graph import ontology_state
    from databridge_graph.packs import clear_caches, deployment

    name = request.param
    monkeypatch.setenv(ontology_state.STATE_ENV, str(tmp_path / "ontology_state.json"))
    clear_caches()
    with deployment(estates.ESTATES[name]["pack"]) as vocab:
        for kind, rule in vocab.identity_rules.items():
            for t in rule.rule_types:                      # a named human approves them
                ontology_state.record_identity_rule_decision(
                    pack=rule.pack, kind=kind, rule=t, principal="reviewer@walnut.example",
                    vocab=vocab)
        yield {"name": name, "vocab": vocab, "snaps": estate_data[name], "spec": SPEC[name],
               "tmp": tmp_path}
    clear_caches()


def _names(snap: dict, cols: tuple[str, ...]) -> dict[str, str]:
    """{row key: the record's own name} for a table."""
    pk = snap["primary_key"][0]
    out = {}
    for r in snap["rows"]:
        name = " ".join(str(r[c]) for c in cols if r.get(c))
        if name.strip():
            out[str(r[pk])] = name
    return out


def _mappings(est) -> dict:
    """The RULES' proposal for each table, approved (as a person would)."""
    from databridge_mcp_ingest.mapping import propose_rules

    snaps = est["snaps"]
    props = propose_rules(snaps, est["vocab"])
    out = {}
    for t, tm in props.items():
        assert tm.status != "unmapped" and tm.node, (t, tm.rationale)
        out[t] = {**tm.model_dump(mode="json"), "status": "approved"}
    return out


class _Model:
    """A fake model that reports, in each chunk, every place one of `surfaces` is written."""

    def __init__(self, surfaces: list[tuple[str, str]]):
        self.surfaces = surfaces

    def complete_structured(self, task, messages, schema, seed=None, source_id=None):
        text = messages[-1]["content"].split("DOCUMENT TEXT:\n", 1)[1]
        ents, taken = [], []
        for surface, kind in sorted(self.surfaces, key=lambda s: -len(s[0])):
            for at in _occurs(text, surface):
                end = at + len(surface)
                if any(a < end and at < b for a, b in taken):
                    continue                       # inside a longer name already reported
                taken.append((at, end))
                ents.append({"name": surface, "kind": kind, "char_start": at, "char_end": end})

        class R:
            parsed = schema.model_validate({"entities": ents, "relations": []})
        return R()


def _expected(est, mappings) -> tuple[list, dict]:
    """(surfaces to report, {(kind, surface): the set of record keys it must reach, None for a
    person (never joined), or KEYLESS for a short form a pack without name similarity must
    leave alone)}."""
    vocab, out, expect = est["vocab"], [], {}
    full: dict[tuple, set] = {}
    for table, cols in est["spec"]["orgs"].items():
        kind = mappings[table]["node"]["kind"]
        for key, name in _names(est["snaps"][table], cols).items():
            full.setdefault((kind, name), set()).add(key)
    for (kind, name), keys in full.items():
        out.append((name, kind))
        expect[(kind, name)] = frozenset(keys)
    for (kind, name), keys in full.items():
        rule = vocab.identity_rule_for(kind)
        prefix_ok = rule is None or rule.similarity_merge
        w = name.split()
        for k in range(1, len(w)):
            short = " ".join(w[:k])
            lead = _words(short)
            if not lead or (kind, short) in expect:
                continue
            rivals = {kk for (kd, nn), ks in full.items() if kd == kind
                      and _words(nn)[:len(lead)] == lead for kk in ks}
            if rivals != keys or len(keys) != 1:
                continue                                    # ambiguous: no expectation
            out.append((short, kind))
            expect[(kind, short)] = frozenset(keys) if prefix_ok else KEYLESS
    for table, cols in est["spec"]["people"].items():
        kind = mappings[table]["node"]["kind"]
        for name in _names(est["snaps"][table], cols).values():
            if (kind, name) not in expect:
                out.append((name, kind))
            expect[(kind, name)] = None
    return out, expect


def _documents(est, surfaces) -> list[tuple[str, str, str]]:
    """(item id, text, document kind) for each fixture document naming at least one record."""
    vocab = est["vocab"]
    people = [s for s, k in surfaces if vocab.is_person(k)]
    docs = []
    for folder, kind in est["spec"]["docs"]:
        found = []
        for p in sorted(folder.rglob("*")):
            text = _read(p) if p.is_file() else None
            if text and any(_occurs(text, s) for s, _ in surfaces):
                names_person = any(_occurs(text, s) for s in people)
                found.append((not names_person, p.relative_to(FIX).as_posix(), text, kind))
        docs += [(i, t, k) for _, i, t, k in sorted(found)[:DOC_CAP]]
    return docs


def _build_docs(store, est, docs, surfaces):
    from databridge_mcp_ingest import capture
    from databridge_mcp_ingest.build import ProseBuilder
    from databridge_mcp_ingest.screen import load_screen

    model, outs = _Model(surfaces), []
    for item, text, kind in docs:
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        capture.put_text(sha, text)
        b = ProseBuilder(store, model, source_id="docs", vocab=est["vocab"], doc_kind=kind,
                         screen=load_screen(packs=[]))
        outs.append((item, text, b.build(item_id=item, sha=sha, raw_text=text, masked_text=text,
                                         title=item, uri=f"files://{item}", retrieved_at=NOW)))
    return outs


def _build_tables(store, est, mappings):
    from databridge_mcp_ingest import capture
    from databridge_mcp_ingest.build import build_tables

    tables = []
    for t, tm in mappings.items():
        sha = capture.put_bytes(json.dumps(est["snaps"][t], sort_keys=True,
                                           default=str).encode("utf-8"))
        tables.append((est["snaps"][t], sha, tm))
    return build_tables(store, est["vocab"], source_id="registers", tables=tables,
                        retrieved_at=NOW, mask_cfg=None)


def _current(store, nid: str):
    """The open node `nid` is now: itself, or the ONE record that absorbed it."""
    row = store.open_node_row(nid)
    if row is not None:
        return row
    hits = [r for r in store.fetchall(
        "SELECT * FROM nodes WHERE system_to IS NULL AND valid_to IS NULL "
        "AND instr(attrs_json, ?) > 0", (json.dumps(nid),))
        if nid in (json.loads(r["attrs_json"] or "{}").get("merged_record_ids") or [])]
    assert len(hits) <= 1, nid
    return hits[0] if hits else None


def _leaf_kinds(p, depth=0):
    if p.kind == "derived" and depth < 8:
        return [k for q in p.derived_from or () for k in _leaf_kinds(q, depth + 1)]
    return [p.kind]


def _leaf_spans(p, depth=0):
    if p.get("kind") == "derived" and depth < 8:
        return [s for b in p.get("basis") or [] for s in _leaf_spans(b, depth + 1)]
    return [(p.get("start"), p.get("end"))]


def _reached(store, outs, surfaces) -> dict:
    """{(item, start, kind, surface): the open node that mention is now}."""
    want = {}
    for item, text, out in outs:
        spans = {}
        for p in out.pointers:
            for s, e in _leaf_spans(p):
                spans.setdefault((s, e), set()).add(p["entity"])
        for surface, kind in surfaces:
            for at in _occurs(text, surface):
                ids = {i for i in spans.get((at, at + len(surface)), ()) if "|" not in i
                       and i.split(":", 1)[0] == kind}
                if ids:
                    assert len(ids) == 1, (item, surface, ids)
                    want[(item, at, kind, surface)] = _current(store, ids.pop())
    return want


_RUNS: dict = {}


def _run(est, order: str, *, fresh: bool = False):
    """One build of the estate in `order`, shared by the tests that only READ it (built once
    per estate and order; `fresh` for a test that writes to it)."""
    key = (est["name"], order)
    if not fresh and key in _RUNS:
        return _RUNS[key]
    out = _run_once(est, order)
    if not fresh:
        _RUNS[key] = out
    return out


def _run_once(est, order: str):
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest.build import consolidate_identity_after_run

    store = GraphStore(str(est["tmp"] / f"{order}.db"))
    assert not store.open_nodes()
    mappings = _mappings(est)
    surfaces, expect = _expected(est, mappings)
    docs = _documents(est, surfaces)
    assert docs                                                          # non-vacuous
    recs = []
    if order == "tables_first":
        _build_tables(store, est, mappings)
        recs.append(consolidate_identity_after_run(store, est["vocab"]))
    outs = _build_docs(store, est, docs, surfaces)
    recs.append(consolidate_identity_after_run(store, est["vocab"]))
    if order == "docs_first":
        _build_tables(store, est, mappings)
        recs.append(consolidate_identity_after_run(store, est["vocab"]))
    return store, outs, surfaces, expect, recs


# --- W-02 / W-07 ---------------------------------------------------------------------------------
def test_w02_joins_do_not_depend_on_which_source_is_read_first(est):
    results = {}
    for order in ("tables_first", "docs_first"):
        store, outs, surfaces, expect, _ = _run(est, order)
        reached = _reached(store, outs, surfaces)
        got = {}
        for (item, at, kind, surface), row in reached.items():
            keys = expect[(kind, surface)]
            if keys is None or keys == KEYLESS:
                continue                                    # people / D-004: their own tests
            assert row is not None, (order, item, surface)
            assert row["canonical_id"] in keys, (order, item, surface, row["id"],
                                                 row["canonical_id"])
            got[(item, at, surface)] = row["canonical_id"]
        assert got, order                                   # non-vacuous
        results[order] = got
    assert results["tables_first"] == results["docs_first"]
    if est["name"] == "workspace":
        # the very records run 5 lost: every customer and project a document names is reached,
        # including the founder's LA-012 customer by its short forms
        keys = set(results["docs_first"].values())
        named = {k for (kind, s), ks in _expected(est, _mappings(est))[1].items()
                 if ks not in (None, KEYLESS) and any(_occurs(t, s) for _, t, _ in
                                                      _documents(est, [(s, kind)])) for k in ks}
        assert keys == named and len(keys) >= 5


def test_w02_no_keyless_twin_is_left_beside_a_keyed_record_in_either_order(est):
    from databridge_graph.store import row_to_node

    for order in ("tables_first", "docs_first"):
        store, outs, surfaces, expect, recs = _run(est, order)
        keyed_names = {(k, s) for (k, s), key in expect.items()
                       if key not in (None, KEYLESS)}
        left = [r for r in store.open_nodes() if not r["canonical_id"]
                and (r["kind"], r["label"]) in keyed_names]
        assert not left, (order, [r["id"] for r in left])
        # every join keeps the record's own name and key, and cites the mention's own spans
        survivors = {m["survivor"] for rec in recs for m in
                     (rec.get("reconciled") or {}).get("merged") or []}
        for sid in survivors:
            node = row_to_node(store, store.open_node_row(sid))
            assert node.canonical_id and node.label
            assert all(p.kind in ("table_cell", "doc_span", "derived") for p in node.sources)
            joined = [p for p in node.sources if p.kind == "derived"
                      and "joined after its keyed record arrived" in (p.derivation or "")]
            # the mention's own spans are on the record (as they say it, or as a derivation
            # naming the surface form when it differs from the record's name)
            assert any("doc_span" in _leaf_kinds(p) for p in node.sources)
            assert all(k == "doc_span" for p in joined for k in _leaf_kinds(p))
        if order == "docs_first" and est["name"] == "workspace":
            assert survivors                                # the reconciliation did the joining


def test_w02_a_document_read_again_after_the_join_re_finds_the_record(est):
    store, outs, surfaces, expect, _ = _run(est, "docs_first", fresh=True)
    before = {r["id"] for r in store.open_nodes()}
    docs = [(item, text, kind) for (item, text, out), (_, _, kind) in
            zip(outs, _documents(est, surfaces))][:5]
    again = _build_docs(store, est, docs, surfaces)
    after = {r["id"] for r in store.open_nodes()}
    assert after == before, sorted(after ^ before)[:10]
    assert again


def test_d004_a_kind_without_similarity_is_joined_only_by_its_approved_rules(est):
    """Finance `legal_entity` says `similarity_merge: false`: a document's SHORT name for a
    company never joins it, in either order (a prefix join folds legal forms, which is the
    near-name-twin join D-004 forbids); its exact legal name does, under the approved rule."""
    vocab = est["vocab"]
    kinds = [k for k, r in vocab.identity_rules.items() if not r.similarity_merge]
    if not kinds:
        pytest.skip(f"{est['name']}: no kind turns name similarity off")
    for order in ("tables_first", "docs_first"):
        store, outs, surfaces, expect, _ = _run(est, order)
        reached = _reached(store, outs, surfaces)
        shorts = [(k, row) for k, row in reached.items() if expect[(k[2], k[3])] == KEYLESS]
        assert shorts, order                                # non-vacuous: short names are read
        for (item, at, kind, surface), row in shorts:
            assert row is not None and not row["canonical_id"], (order, item, surface,
                                                                 row["id"])


# --- F-04 ------------------------------------------------------------------------------------------
def test_f04_a_person_named_in_a_document_is_never_joined_to_a_register_person(est):
    vocab = est["vocab"]
    for order in ("tables_first", "docs_first"):
        store, outs, surfaces, expect, _ = _run(est, order)
        reached = _reached(store, outs, surfaces)
        people = [(k, row) for k, row in reached.items() if expect[(k[2], k[3])] is None]
        assert people, order                                # non-vacuous: people are named
        for (item, at, kind, surface), row in people:
            assert vocab.is_subject(kind) and vocab.is_person(kind)
            assert row is not None and row["kind"] == kind
            assert "@" in row["id"], (order, item, surface, row["id"])   # a mention, scoped
            assert not row["canonical_id"]
        # no register person cites a document span
        for kind in {k for (_, _, k, _), _ in people}:
            for r in store.fetchall("SELECT id FROM nodes WHERE kind = ? AND system_to IS NULL "
                                    "AND id NOT LIKE '%@%'", (kind,)):
                kinds = {p.kind for p in store.sources_for(r["id"])}
                assert "doc_span" not in kinds, (order, r["id"])
    if est["name"] == "finance":
        # the run-5 example: an email names a person three CRM contacts share a name with
        names = _names(est["snaps"]["crm.contact"], ("first_name", "last_name"))
        dup = {n for n in names.values() if list(names.values()).count(n) > 1}
        assert any(surface in dup for (_, _, _, surface), _ in people)


# --- F-08 ------------------------------------------------------------------------------------------
def test_f08_one_node_per_document_never_one_per_title(est, tmp_path):
    """Documents sharing a title stay separate: each item is anchored by its own identity (item,
    uri, digest) when the connection names its document kind, and a document-kind mention with no
    identifier of its own is scoped to its item. Finance binds several document kinds, so a
    connection that names none gets no anchor at all (refused as AMBIGUOUS, not guessed)."""
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest.build import document_kind

    vocab = est["vocab"]
    store = GraphStore(str(tmp_path / "f08.db"))
    titled: dict[str, list] = {}
    for folder, kind in est["spec"]["docs"]:
        for p in sorted(folder.rglob("*")):
            text = _read(p) if p.is_file() else None
            m = re.search(r"^#\s+(.+)$", text or "", re.M)
            if m:
                titled.setdefault((kind, m.group(1).strip()), []).append(
                    (p.relative_to(FIX).as_posix(), text, kind))
    # the most-shared title whose document the screen admits (a held document is never built)
    for (kind, title), docs in sorted(titled.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(docs) < 2:
            # no two documents of this estate share a title: the same file filed twice (two
            # items, two uris) is the case it holds, and must stay two documents as well
            item, text, k = docs[0]
            docs = [(item, text, k), (f"copy/{item}", text, k)]
        docs = docs[:6]
        outs = _build_docs(store, est, docs, [(title, kind)])
        if all(o.entities for _, _, o in outs):
            break
    else:
        pytest.fail("no titled document of the estate is admitted by the screen")
    anchors = {o.anchor_id for _, _, o in outs}
    assert None not in anchors and len(anchors) == len(docs)
    title_nodes = [r for r in store.open_nodes() if r["kind"] == kind and r["label"] == title]
    assert len(title_nodes) == len(docs)
    for r in store.open_nodes():
        if vocab.is_document_kind(r["kind"]):
            items = {p.document_id for p in store.sources_for(r["id"]) if p.document_id}
            assert len(items) <= 1, r["id"]
    bound = vocab.nodes_binding_to(__import__("databridge_graph.meta", fromlist=["x"])
                                   .NodeMetaKind.DOCUMENT)
    chosen, note = document_kind(vocab)
    if len(bound) > 1 and "document" not in bound:
        assert chosen is None and "AMBIGUOUS" in note

"""ING4 — E2E run 3, P-05 (residual of N-16): a document's SHORT name for a keyed record joins it.

The live failure: the CRM's customers table (business key `customer_id`) was built first; the
meeting notes then said "Corriedale Grocers" and "Corriedale", which carried no canonical id and
were too short for the fuzzy rule, so they became their own organisation nodes and the cross-source
answer never reached C-1002 (LA-012, 15300).

Every input here is the synthetic workspace fixture the E2E ran on (the customers INSERT and the
meeting / project documents); every expectation is derived from those inputs. Offline.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from databridge_mcp_ingest import capture
from databridge_mcp_ingest.build import ProseBuilder, build_tables, document_kind
from databridge_mcp_ingest.screen import load_screen

NOW = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
FIX = Path(__file__).resolve().parents[3] / "fixtures" / "workspace_demo"


@pytest.fixture
def workspace_vocab():
    from databridge_graph.packs import deployment

    with deployment("workspace") as vocab:
        yield vocab


@pytest.fixture
def store(tmp_path):
    from databridge_graph.store import GraphStore

    return GraphStore(str(tmp_path / "graph.db"))


# --- fixture data, read from the files the E2E ingested --------------------------------------------
def _customers() -> list[dict]:
    sql = (FIX / "db" / "01_schema_and_data.sql").read_text(encoding="utf-8")
    cols_s, body = re.search(r"INSERT INTO customers \(([^)]*)\) VALUES(.*?);", sql, re.S).groups()
    cols = [c.strip() for c in cols_s.split(",")]
    rows = [dict(zip(cols, re.findall(r"'([^']*)'", t)))
            for t in re.findall(r"\(([^()]*)\)", body)]
    assert rows and all(r.get("customer_id") and r.get("name") for r in rows)
    return rows


def _docx_text(path: Path) -> str:
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    paras = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
    return "\n".join("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)) for p in paras)


def _documents() -> list[tuple[str, str]]:
    return [("steering", _docx_text(FIX / "files" / "meetings" /
                                    "2026-08-19_steering_corriedale.docx")),
            ("notes", (FIX / "files" / "projects" / "demand-forecasting" / "notes.md")
             .read_text(encoding="utf-8")),
            ("kickoff", (FIX / "files" / "meetings" / "2026-07-14_kickoff_pellucid.txt")
             .read_text(encoding="utf-8"))]


def _short_forms(text: str, customers: list[dict]) -> list[tuple[str, int, dict]]:
    """(surface, offset, customer) for every place a document names a customer by the LEADING
    words of its name and stops there (the next word of the name does not follow)."""
    out = []
    for c in customers:
        words = c["name"].split()
        for k in range(1, len(words)):
            surface = " ".join(words[:k])
            for m in re.finditer(rf"(?<!\w){re.escape(surface)}(?!\w)", text):
                if not text[m.end():].startswith(" " + words[k]):
                    out.append((surface, m.start(), c))
    return out


def _build_customers(store, vocab, customers):
    snap = {"schema": "public", "table": "customers", "primary_key": ["customer_id"],
            "foreign_keys": [], "rows": customers}
    sha = capture.put_bytes(json.dumps(snap, sort_keys=True).encode("utf-8"))
    tm = {"node": {"kind": "organisation", "label": "{name}", "canonical_id": "customer_id",
                   "attrs": {"name": "name", "domain": "domain", "sector": "sector",
                             "city": "city"}}, "edges": []}
    return build_tables(store, vocab, source_id="larkspur_db", tables=[(snap, sha, tm)],
                        retrieved_at=NOW, mask_cfg=None)


class _Harness:
    pass


def _build_doc(store, vocab, item, text, mentions: list[tuple[str, int, str]]):
    """Apply one document whose extractor found `mentions` = (surface, offset, kind) — the shape
    the live extractor produced, one node per surface form, id from the surface."""
    from databridge_core import SourcePointer
    from databridge_graph.kinds import NodeKind
    from databridge_graph.schema import GraphNode, node_id

    sha = hashlib.sha256(text.encode()).hexdigest()
    capture.put_text(sha, text)
    b = ProseBuilder(store, _Harness(), source_id="larkspur-files", vocab=vocab,
                     doc_kind=document_kind(vocab)[0], screen=load_screen(packs=[]))
    plan = b.prepare(item_id=item, sha=sha, raw_text=text, masked_text=text, title=item,
                     uri=f"files://{item}", retrieved_at=NOW)
    by_job: dict[int, list] = {}
    for surface, at, kind_name in mentions:
        j = next(i for i, jb in enumerate(plan.jobs)
                 if jb.start <= at and at + len(surface) <= jb.end)
        kind = NodeKind(kind_name)
        local = at - plan.jobs[j].start
        nid = node_id(kind, surface.lower())
        node = GraphNode(id=nid, kind=kind, label=surface,
                         sources=(SourcePointer(source_id=item, kind="doc_span",
                                                document_id=item, char_start=local,
                                                char_end=local + len(surface)),),
                         valid_from=NOW, system_from=store.now())
        by_job.setdefault(j, [])
        if all(n.id != nid for n in by_job[j]):
            by_job[j].append(node)
    res = [(SimpleNamespace(entities=by_job.get(i, []), relations=[], refused=[],
                            produced_by=None, subject_candidates=[]), 1.0)
           for i in range(len(plan.jobs))]
    return b.apply(plan, res)


def _orgs(store):
    return [r for r in store.open_nodes() if r["kind"] == "organisation"]


def _mention_targets(store, anchor_id):
    return {e["dst_id"] for e in store.open_edges() if e["src_id"] == anchor_id}


# --- the repro -------------------------------------------------------------------------------------
def test_p05_short_names_in_documents_join_the_keyed_customer_db_first(
        walnut_env, store, workspace_vocab):
    from databridge_graph.store import row_to_node
    from databridge_mcp_ingest.runner import _PointerResolver

    customers = _customers()
    _build_customers(store, workspace_vocab, customers)
    key_of = {r["canonical_id"]: r["id"] for r in _orgs(store)}
    assert set(key_of) == {c["customer_id"] for c in customers}

    checked = 0
    for item, text in _documents():
        forms = _short_forms(text, customers)
        if not forms:
            continue
        out = _build_doc(store, workspace_vocab, item, text,
                         [(s, at, "organisation") for s, at, _ in forms])
        assert all(_PointerResolver().resolves(p) for p in out.pointers)
        reached = _mention_targets(store, out.anchor_id)
        for surface, at, cust in forms:
            want = key_of[cust["customer_id"]]
            assert want in reached, (item, surface)
            node = row_to_node(store, store.open_node_row(want))
            # the join is cited over the exact span that says the short form
            leaves = [q for p in node.sources if p.kind == "derived" for q in p.derived_from]
            assert any(text[q.char_start:q.char_end] == surface for q in leaves), surface
            # the record keeps its own name and key; the short form never relabels it
            assert node.label == cust["name"] and node.canonical_id == cust["customer_id"]
            checked += 1
        # nothing was left behind as a separate, keyless organisation for these surfaces
        assert not [r for r in _orgs(store) if not r["canonical_id"]]
    assert checked >= 3                                                  # non-vacuous
    # the very records the E2E missed: the C-1002 short forms reached C-1002
    assert any(f[2]["customer_id"] == "C-1002" for _, t in _documents()
               for f in _short_forms(t, customers))


def test_p05_every_name_join_is_recorded_and_its_pointers_resolve(walnut_env, store,
                                                                   workspace_vocab):
    from databridge_mcp_ingest.runner import _PointerResolver

    customers = _customers()
    _build_customers(store, workspace_vocab, customers)
    item, text = _documents()[0]
    forms = _short_forms(text, customers)
    assert forms
    out = _build_doc(store, workspace_vocab, item, text,
                     [(s, at, "organisation") for s, at, _ in forms])
    assert out.name_joins and all(j["status"] == "candidate_join" for j in out.name_joins)
    joined_surfaces = {j["surface"] for j in out.name_joins}
    assert joined_surfaces == {s for s, _, _ in forms}
    written = {r["id"] for r in store.open_nodes()} | {e["id"] for e in store.open_edges()}
    assert set(out.graph_ids) <= written and {p["entity"] for p in out.pointers} >= set(
        out.graph_ids)
    assert all(_PointerResolver().resolves(p) for p in out.pointers)


def test_p05_a_stale_keyless_mention_node_from_an_earlier_build_does_not_capture_the_join(
        walnut_env, store, workspace_vocab):
    """The live graph already held `organisation:corriedale grocers` from before the fix; a
    re-build of the document must still reach the keyed record."""
    customers = _customers()
    item, text = _documents()[0]
    forms = _short_forms(text, customers)
    _build_doc(store, workspace_vocab, item, text, [(s, at, "organisation") for s, at, _ in forms])
    stale = {r["id"] for r in _orgs(store)}
    assert stale and not any(r["canonical_id"] for r in _orgs(store))
    _build_customers(store, workspace_vocab, customers)
    key_of = {r["canonical_id"]: r["id"] for r in _orgs(store) if r["canonical_id"]}
    out = _build_doc(store, workspace_vocab, item + "-v2", text,
                     [(s, at, "organisation") for s, at, _ in forms])
    reached = _mention_targets(store, out.anchor_id)
    assert {key_of[c["customer_id"]] for _, _, c in forms} <= reached
    assert not reached & stale


# --- the fences --------------------------------------------------------------------------------------
def test_p05_a_subject_short_name_is_never_joined(walnut_env, store, workspace_vocab):
    """A person (a subject kind) named by the leading words of a keyed person's name stays its
    own mention (hard constraint 1)."""
    from databridge_core import SourcePointer
    from databridge_graph.kinds import NodeKind
    from databridge_graph.merge import name_prefix_join, upsert_node
    from databridge_graph.schema import GraphNode, node_id

    assert workspace_vocab.is_subject("person")
    full = "Rowan Achterberg"
    kind = NodeKind("person")
    cell = SourcePointer(source_id="db", kind="table_cell", table="public.contacts",
                         column="full_name", row_id="1", capture_digest="b" * 64)
    key = SourcePointer(source_id="db", kind="table_cell", table="public.contacts",
                        column="contact_id", row_id="1", capture_digest="b" * 64)
    upsert_node(store, GraphNode(id=node_id(kind, "db-1"), kind=kind, label=full,
                                 canonical_id="P-1", attrs={"full_name": full},
                                 attr_sources={"full_name": cell, "canonical_id": key},
                                 sources=(key,), valid_from=NOW, system_from=store.now()),
                workspace_vocab, id_only=True)
    short = full.split()[0]
    text = f"Spoke to {short} about the depot."
    out = _build_doc(store, workspace_vocab, "p", text, [(short, text.index(short), "person")])
    persons = [r for r in store.open_nodes() if r["kind"] == "person"]
    assert len(persons) == 2 and not out.name_joins
    probe = GraphNode(id=node_id(kind, "probe"), kind=kind, label=short,
                      sources=(cell,), valid_from=NOW, system_from=store.now())
    assert name_prefix_join(store, probe, workspace_vocab) is None


def _keyed_org(store, vocab, key, name):
    from databridge_core import SourcePointer
    from databridge_graph.kinds import NodeKind
    from databridge_graph.merge import upsert_node
    from databridge_graph.schema import GraphNode, node_id

    kind = NodeKind("organisation")
    cell = SourcePointer(source_id="db", kind="table_cell", table="public.orgs", column="name",
                         row_id=key, capture_digest="c" * 64)
    kcell = cell.model_copy(update={"column": "org_id"})
    upsert_node(store, GraphNode(id=node_id(kind, f"db-{key}"), kind=kind, label=name,
                                 canonical_id=key, attrs={"name": name},
                                 attr_sources={"name": cell, "canonical_id": kcell},
                                 sources=(kcell,), valid_from=NOW, system_from=store.now()),
                vocab)


def _probe(store, label):
    from databridge_core import SourcePointer
    from databridge_graph.kinds import NodeKind
    from databridge_graph.schema import GraphNode, node_id

    kind = NodeKind("organisation")
    ptr = SourcePointer(source_id="d", kind="doc_span", document_id="d", char_start=0,
                        char_end=len(label), capture_digest="d" * 64)
    return GraphNode(id=node_id(kind, label.lower()), kind=kind, label=label, sources=(ptr,),
                     valid_from=NOW, system_from=store.now())


def test_p05_fences_ambiguity_mid_name_and_a_competing_name(walnut_env, store, workspace_vocab):
    from databridge_graph.merge import name_prefix_join, upsert_node

    name = _customers()[1]["name"]                         # a fixture customer's own name
    words = name.split()
    _keyed_org(store, workspace_vocab, "K-1", name)
    # the positive control, so every refusal below is meaningful
    hit = name_prefix_join(store, _probe(store, words[0]), workspace_vocab)
    assert hit is not None and hit.row["canonical_id"] == "K-1" and hit.attr == "name"
    # a mid-name word is never a match
    assert name_prefix_join(store, _probe(store, words[1]), workspace_vocab) is None
    # a different record ALSO beginning with those words makes the short form ambiguous —
    # keyed ...
    _keyed_org(store, workspace_vocab, "K-2", f"{words[0]} Holdings Trust")
    assert name_prefix_join(store, _probe(store, words[0]), workspace_vocab) is None
    # ... the longer form still decides between them
    two = " ".join(words[:2])
    assert name_prefix_join(store, _probe(store, two), workspace_vocab).row["canonical_id"] == "K-1"


def test_p05_a_keyless_competing_record_blocks_the_join(walnut_env, store, workspace_vocab):
    from databridge_graph.merge import name_prefix_join, upsert_node

    name = _customers()[1]["name"]
    first = name.split()[0]
    _keyed_org(store, workspace_vocab, "K-1", name)
    upsert_node(store, _probe(store, f"{first} Dairies"), workspace_vocab)
    assert name_prefix_join(store, _probe(store, first), workspace_vocab) is None


def test_p05_a_name_the_record_does_not_ground_by_its_own_pointer_is_never_matched(
        walnut_env, store, workspace_vocab):
    from databridge_core import SourcePointer
    from databridge_graph.kinds import NodeKind
    from databridge_graph.merge import name_prefix_join, upsert_node
    from databridge_graph.schema import GraphNode, node_id

    name = _customers()[1]["name"]
    kind = NodeKind("organisation")
    kcell = SourcePointer(source_id="db", kind="table_cell", table="public.orgs",
                          column="org_id", row_id="K-9", capture_digest="c" * 64)
    upsert_node(store, GraphNode(id=node_id(kind, "db-k9"), kind=kind, label=name,
                                 canonical_id="K-9", attr_sources={"canonical_id": kcell},
                                 sources=(kcell,), valid_from=NOW, system_from=store.now()),
                workspace_vocab)
    assert name_prefix_join(store, _probe(store, name.split()[0]), workspace_vocab) is None


# --- GW4 cross-track: a row's canonical id comes from its OWN table's key -------------------------
def _invoices_snap() -> dict:
    sql = (FIX / "db" / "01_schema_and_data.sql").read_text(encoding="utf-8")
    cols_s, body = re.search(r"INSERT INTO invoices \(([^)]*)\) VALUES(.*?);", sql, re.S).groups()
    cols = [c.strip() for c in cols_s.split(",")]
    rows = [dict(zip(cols, [v.strip().strip("'") for v in t.split(",")]))
            for t in re.findall(r"\(([^()]*)\)", body)]
    pk = re.search(r"CREATE TABLE invoices \((.*?)\);", sql, re.S).group(1)
    key = next(ln.split()[0] for ln in pk.splitlines() if "PRIMARY KEY" in ln)
    assert rows and all(r.get(key) for r in rows)
    return {"schema": "public", "table": "invoices", "primary_key": [key], "foreign_keys": [],
            "columns": [{"name": c, "type": "text"} for c in cols], "rows": rows}


def _follow_ups_snap(invoice_keys: list[str]) -> dict:
    """The agent-writer table (profiles/postgres/agent_writer.sql): a BIGSERIAL key, and an
    `invoice_no` column with NO declared foreign key. Two tasks may name the same invoice."""
    ddl = (Path(__file__).resolve().parents[1] / "databridge_mcp_ingest" / "profiles" /
           "postgres" / "agent_writer.sql").read_text(encoding="utf-8")
    body = re.search(r"CREATE TABLE IF NOT EXISTS public\.follow_up_tasks \((.*?)\);", ddl,
                     re.S).group(1)
    cols = [ln.split()[0] for ln in body.splitlines() if ln.strip()]
    types = {ln.split()[0]: ln.split()[1].lower() for ln in body.splitlines() if ln.strip()}
    key = next(ln.split()[0] for ln in body.splitlines() if "PRIMARY KEY" in ln)
    ref = next(c for c in cols if c != key and c in _invoices_snap()["primary_key"])
    rows = []
    for i, inv in enumerate(invoice_keys, 1):
        rows.append({c: None for c in cols} | {key: i, ref: inv, "title": f"Follow up {inv} #{i}",
                                               "customer_id": "C-1002", "created_by": "agent",
                                               "created_at": "2026-10-01T08:50:00Z"})
    return {"schema": "public", "table": "follow_up_tasks", "primary_key": [key],
            "foreign_keys": [], "columns": [{"name": c, "type": types[c]} for c in cols],
            "rows": rows}, ref


def test_gw4_a_column_holding_another_tables_key_is_never_this_rows_canonical_id(
        walnut_env, workspace_vocab):
    from databridge_mcp_ingest.mapping import _fallback_proposals, propose_rules

    inv = _invoices_snap()
    one = inv["rows"][1][inv["primary_key"][0]]
    fu, ref = _follow_ups_snap([one])                          # ONE row: profiled "unique"
    snaps = {"public.invoices": inv, "public.follow_up_tasks": fu}
    # a later sync: the follow-up table is NEW, the invoices table is already mapped
    proposed = propose_rules({"public.follow_up_tasks": fu}, workspace_vocab, context=snaps)
    tm = proposed["public.follow_up_tasks"]
    if tm.status == "unmapped":
        tm = _fallback_proposals(snaps, {"public.follow_up_tasks": tm},
                                 propose_rules({"public.invoices": inv}, workspace_vocab),
                                 workspace_vocab)["public.follow_up_tasks"]
    assert tm.node is not None                                  # non-vacuous: it was mapped
    assert (tm.node or {}).get("canonical_id") != ref
    assert any("another table's key" in r for r in tm.rationale)
    # the invoices table keeps its own key
    own = propose_rules(snaps, workspace_vocab)["public.invoices"]
    assert (own.node or {}).get("canonical_id") == inv["primary_key"][0]


def test_gw4_two_follow_ups_for_one_invoice_never_fuse_and_never_touch_the_invoice(
        walnut_env, store, workspace_vocab):
    from databridge_mcp_ingest.mapping import _fallback_proposals, propose_rules

    inv = _invoices_snap()
    one = inv["rows"][1][inv["primary_key"][0]]
    # as live: the mapping is proposed when the table holds ONE task (so any column profiles
    # unique), and a second task for the same invoice arrives on a later sync
    fu1, ref = _follow_ups_snap([one])
    fu, _ = _follow_ups_snap([one, one])
    rules = propose_rules({"public.invoices": inv, "public.follow_up_tasks": fu1},
                          workspace_vocab)
    snaps = {"public.invoices": inv, "public.follow_up_tasks": fu}
    tms = {**rules, **_fallback_proposals(
        {"public.invoices": inv, "public.follow_up_tasks": fu1},
        {t: m for t, m in rules.items() if m.status == "unmapped"}, rules,
        workspace_vocab)}
    tables = []
    for tref in ("public.invoices", "public.follow_up_tasks"):
        snap, tm = snaps[tref], tms[tref]
        assert tm.node is not None
        sha = capture.put_bytes(json.dumps(snap, sort_keys=True, default=str).encode("utf-8"))
        tables.append((snap, sha, {"node": tm.node, "edges": []}))
    build_tables(store, workspace_vocab, source_id="larkspur_db", tables=tables,
                 retrieved_at=NOW, mask_cfg=None)
    fu_kind = tms["public.follow_up_tasks"].node["kind"]
    inv_kind = tms["public.invoices"].node["kind"]
    tasks = [r for r in store.open_nodes() if r["kind"] == fu_kind
             and "follow" in r["id"].replace("_", "-")]
    assert len(tasks) == len(fu["rows"]) == 2                  # two tasks, two nodes
    assert all(r["canonical_id"] != one for r in tasks)
    invoices = [r for r in store.open_nodes() if r["kind"] == inv_kind]
    assert len(invoices) == len(inv["rows"])

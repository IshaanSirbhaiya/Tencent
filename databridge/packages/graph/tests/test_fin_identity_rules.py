"""FIN-04 and FIN-07 (finance dry run, 2026-10-02; wiki/20-decisions/D-004).

FIN-04: an ORGANISATION's records may be recognised as one record -- on a declared business key,
or on an exact normalised legal name -- but only under a merge rule a named human approved. A
PERSON is never merged. Both are pack declarations (`identity_rules` / `subject_kinds`).

FIN-07: a DOCUMENT is never one with another because their titles match; its identity is its
source item.

Every test runs over TWO estates -- the finance fixtures and the workspace (Larkspur) fixtures --
and derives its expectation from the estate's own data. The workspace pack declares no identity
rule, so its estate binds a copy of the workspace pack with one added (on `organisation`, keyed by
the customers table's `domain`).
"""
from __future__ import annotations

import csv
import datetime as dt
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from databridge_convert.ir.models import Block, DocumentRecord
from databridge_core import SnapshotIdentity, SourcePointer
from databridge_graph import merge as merge_mod
from databridge_graph import ontology_state
from databridge_graph.extractor import extract_graph, gold_extraction_handler
from databridge_graph.identity import resolve_entity
from databridge_graph.merge import (MERGED_IDS_ATTR, consolidate_identity, resolve_existing,
                                    upsert_node)
from databridge_graph.packs import (DEFAULT_PACKS_ROOT, IDENTITY_RULE_TYPES, PackError,
                                    build_vocabulary, clear_caches, deployment, load_pack)
from databridge_graph.schema import GraphNode, NodeKind
from databridge_graph.store import GraphStore
from databridge_harness import FakeBackend, Harness

ROOT = Path(__file__).resolve().parents[3]
FIN = ROOT / "fixtures" / "packs" / "finance"
WS = ROOT / "fixtures" / "workspace_demo"
T0 = dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc)
PRINCIPAL = "reviewer@walnut.example"


# --- reading the fixtures (no expectation is written down: everything comes from these) --------
def _sql_rows(path: Path, table: str) -> list[dict]:
    """Rows of every `INSERT INTO <table> (...) VALUES (...), ...;` in ``path``."""
    text = path.read_text(encoding="utf-8")
    out: list[dict] = []
    for m in re.finditer(rf"INSERT INTO {re.escape(table)} \(([^)]*)\) VALUES", text):
        cols = [c.strip() for c in m.group(1).split(",")]
        i, n = m.end(), len(text)
        row, val, depth = [], None, 0
        while i < n:
            ch = text[i]
            if ch == "'":
                j, buf = i + 1, []
                while True:
                    if text[j] == "'" and j + 1 < n and text[j + 1] == "'":
                        buf.append("'")
                        j += 2
                    elif text[j] == "'":
                        break
                    else:
                        buf.append(text[j])
                        j += 1
                val, i = "".join(buf), j + 1
                continue
            if ch == "(":
                depth, row, val = 1, [], None
            elif ch in ",)" and depth:
                row.append(val)
                val = None
                if ch == ")":
                    depth = 0
                    out.append(dict(zip(cols, row)))
            elif ch == ";" and not depth:
                break
            elif depth and not ch.isspace():
                j = i
                while j < n and text[j] not in ",)":
                    j += 1
                tok = text[i:j].strip()
                val = None if tok.upper() == "NULL" else tok
                i = j
                continue
            i += 1
    return out


def norm(name: str) -> str:
    """The test's own statement of 'exact after normalisation': NFKC, casefold, every run of
    non-word characters one space."""
    t = unicodedata.normalize("NFKC", name).casefold().replace("_", " ")
    return " ".join(re.sub(r"[^\w]+", " ", t).split())


@dataclass
class Record:
    id: str
    label: str
    source: str
    table: str
    row: str
    keys: dict = field(default_factory=dict)


@dataclass
class Estate:
    name: str
    pack: str
    org_kind: str
    person_kind: str
    key: str
    orgs: list
    persons: list
    texts: dict
    non_matches: list = field(default_factory=list)   # (record ids A, record ids B)


def _finance() -> Estate:
    parties = []
    for f in sorted((FIN / "core_banking" / "seed").glob("01_party_*.sql")):
        parties += _sql_rows(f, "core_banking.party")
    orgs, persons = [], []
    for r in parties:
        rec = Record(id=r["party_id"], label=r["legal_name"], source="finance_core_banking",
                     table="core_banking.party", row=r["party_id"],
                     keys={"registration_no": r["registration_no"]} if r["registration_no"] else {})
        kind = (r["party_type"] or "").casefold()
        (orgs if kind in ("organisation", "org") else persons).append(rec)
    for f in sorted((FIN / "crm" / "seed").glob("02_account_org_*.sql")):
        for r in _sql_rows(f, "crm.account_org"):
            orgs.append(Record(id=r["crm_org_id"], label=r["org_name"], source="finance_crm",
                               table="crm.account_org", row=r["crm_org_id"]))
    texts = {str(p.relative_to(FIN)): p.read_text(encoding="utf-8")
             for d in ("filings", "credit_memos") for p in sorted((FIN / d).rglob("*.md"))}
    by_ent: dict[str, set] = {}
    with open(FIN / "ground_truth" / "entity_map.csv", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            by_ent.setdefault(r["entity_id"], set()).add(r["record_id"])
    non = []
    with open(FIN / "ground_truth" / "non_matches.csv", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            non.append((by_ent.get(r["entity_id_a"], set()), by_ent.get(r["entity_id_b"], set())))
    return Estate("finance", "finance", "legal_entity", "party", "registration_no", orgs,
                  persons, texts, non)


def _workspace() -> Estate:
    sql = WS / "db" / "01_schema_and_data.sql"
    orgs = [Record(id=r["customer_id"], label=r["name"], source="workspace_db", table="customers",
                   row=r["customer_id"], keys={"domain": r["domain"]})
            for r in _sql_rows(sql, "customers")]
    persons = [Record(id=r["employee_id"], label=r["full_name"], source="workspace_db",
                      table="employees", row=r["employee_id"])
               for r in _sql_rows(sql, "employees")]
    persons += [Record(id=f"contact-{i}", label=r["full_name"], source="workspace_db",
                       table="contacts", row=str(i))
                for i, r in enumerate(_sql_rows(sql, "contacts"), 1)]
    texts = {str(p.relative_to(WS)): p.read_text(encoding="utf-8")
             for p in sorted((WS / "files").rglob("*")) if p.suffix in (".md", ".txt")}
    return Estate("workspace", "workspace", "organisation", "person", "domain", orgs, persons,
                  texts)


ESTATES = {"finance": _finance, "workspace": _workspace}


@pytest.fixture(params=sorted(ESTATES))
def estate(request, tmp_path, monkeypatch):
    """The estate, bound, with an empty ontology state of its own."""
    est = ESTATES[request.param]()
    monkeypatch.setenv(ontology_state.STATE_ENV, str(tmp_path / "state.json"))
    if est.pack == "workspace":
        root = tmp_path / "packs"
        (root / "workspace").mkdir(parents=True)
        text = (DEFAULT_PACKS_ROOT / "workspace" / "ontology.yaml").read_text(encoding="utf-8")
        text += ("\nidentity_rules:\n  - kind: organisation\n    business_keys: [domain]\n"
                 "    exact_name: true\n    similarity_merge: false\n")
        (root / "workspace" / "ontology.yaml").write_text(text, encoding="utf-8")
        monkeypatch.setenv("DATABRIDGE_PACKS_ROOT", str(root))
    clear_caches()
    with deployment(est.pack) as v:
        assert v.identity_rule_for(est.org_kind) is not None
        assert v.is_subject(est.person_kind) and v.identity_rule_for(est.person_kind) is None
        yield est, v
    clear_caches()


# --- building the graph the way the ingest paths do --------------------------------------------
def _cell(rec: Record, column: str) -> SourcePointer:
    return SourcePointer(source_id=rec.source, kind="table_cell", table=rec.table, column=column,
                         row_id=rec.row)


def _row_node(est: Estate, rec: Record, kind: str) -> GraphNode:
    attrs = {k: v for k, v in rec.keys.items()}
    attr_sources = {k: _cell(rec, k) for k in attrs}
    attr_sources.update(label=_cell(rec, "name"), canonical_id=_cell(rec, "id"))
    return GraphNode(id=f"{kind}:{rec.source}/{rec.table}/{rec.id}".lower(), kind=NodeKind(kind),
                     label=rec.label, canonical_id=f"{rec.source}/{rec.table}/{rec.id}",
                     attrs=attrs, attr_sources=attr_sources, sources=[_cell(rec, "id")],
                     valid_from=T0, system_from=T0)


def _mention(kind: str, doc: str, text: str, name: str, subject: bool) -> GraphNode:
    s = text.index(name)
    ptr = SourcePointer(source_id=doc, kind="doc_span", document_id=doc, char_start=s,
                        char_end=s + len(name))
    nid = (f"{kind}:{name.lower()}@{doc}#{s}-{s + len(name)}" if subject
           else f"{kind}:{name.lower()}")              # the extractor's two id shapes
    return GraphNode(id=nid, kind=NodeKind(kind), label=name, sources=[ptr], valid_from=T0,
                     system_from=T0)


def _build(est: Estate, v, store: GraphStore) -> dict:
    """Rows, then every exact document mention of a row's name. Returns record id -> node id."""
    where = {}
    for rec in est.orgs:
        where[rec.id] = upsert_node(store, _row_node(est, rec, est.org_kind), v, id_only=True).id
    for rec in est.persons:
        upsert_node(store, _row_node(est, rec, est.person_kind), v, id_only=True)
    for recs, kind, subject in ((est.orgs, est.org_kind, False),
                                (est.persons, est.person_kind, True)):
        for name in sorted({r.label for r in recs}):
            for doc, text in est.texts.items():
                if name in text:
                    upsert_node(store, _mention(kind, doc, text, name, subject), v,
                                id_only=subject)
    return where


def _now_at(store: GraphStore, node_id: str) -> str:
    """The open node a record's original id lives in now (itself, or the survivor that lists it)."""
    if store.open_node_row(node_id) is not None:
        return node_id
    hits = [r["id"] for r in store.open_nodes()
            if node_id in (merge_mod.json.loads(r["attrs_json"] or "{}").get(MERGED_IDS_ATTR) or [])]
    assert len(hits) == 1, (node_id, hits)
    return hits[0]


def _nodes_by_name(store: GraphStore, est: Estate) -> dict[str, set]:
    out: dict[str, set] = {}
    for r in store.open_nodes():
        if r["kind"] == est.org_kind:
            out.setdefault(norm(r["label"]), set()).add(r["id"])
    return out


def _approve(est: Estate, v, *rules: str) -> None:
    for rule in rules:
        ontology_state.record_identity_rule_decision(
            pack=est.pack, kind=est.org_kind, rule=rule, principal=PRINCIPAL, vocab=v)


def _name_groups(est: Estate) -> dict[str, list[Record]]:
    groups: dict[str, list[Record]] = {}
    for r in est.orgs:
        groups.setdefault(norm(r.label), []).append(r)
    return groups


def _key_values(est: Estate, recs) -> set:
    return {norm(r.keys[est.key]) for r in recs if r.keys.get(est.key)}


# --- FIN-04 -----------------------------------------------------------------------------------
def test_a_declared_rule_merges_nothing_until_a_human_approves_it(estate):
    est, v = estate
    store = GraphStore()
    where = _build(est, v, store)
    before = store.counts()
    report = consolidate_identity(store, v)
    assert report.merged == [] and store.counts() == before
    # non-vacuous: the estate HAS a name its records share, and today it names several nodes
    nodes_by_name: dict[str, set] = {}
    for r in store.open_nodes():
        if r["kind"] == est.org_kind:
            nodes_by_name.setdefault(norm(r["label"]), set()).add(r["id"])
    shared = [g for name, g in _name_groups(est).items() if len(nodes_by_name.get(name, ())) > 1
              and len(_key_values(est, g)) <= 1]
    assert shared, "the estate has no organisation name carried by two records"
    assert resolve_entity(store, shared[0][0].label).candidates
    assert all(where[r.id] == _now_at(store, where[r.id]) for g in shared for r in g)


def test_approved_rules_make_one_record_per_organisation(estate):
    est, v = estate
    store = GraphStore()
    where = _build(est, v, store)
    before = _nodes_by_name(store, est)
    _approve(est, v, "business_key", "exact_name")
    report = consolidate_identity(store, v)
    assert report.merged, "nothing merged: the approved rules did not run"
    after = _nodes_by_name(store, est)
    final = {r.id: _now_at(store, where[r.id]) for r in est.orgs}
    # one name, no conflicting key -> one node; a name two keyed records disagree on -> apart
    joined = 0
    for name, recs in _name_groups(est).items():
        keys = _key_values(est, recs)
        if len(keys) <= 1:
            assert len({final[r.id] for r in recs}) == 1, name
            assert len(after[name]) == 1, (name, after[name])
            joined += len(before[name]) > 1
        else:
            by_key = {}
            for r in recs:
                if r.keys.get(est.key):
                    by_key.setdefault(norm(r.keys[est.key]), set()).add(final[r.id])
            assert len({frozenset(s) for s in by_key.values()}) == len(by_key), name
    assert joined, "no name group was joined (vacuous)"
    # one key value -> one node
    by_value: dict[str, set] = {}
    for r in est.orgs:
        if r.keys.get(est.key):
            by_value.setdefault(norm(r.keys[est.key]), set()).add(final[r.id])
    assert all(len(s) == 1 for s in by_value.values())
    # no node holds two different values of the key
    members: dict[str, set] = {}
    for r in est.orgs:
        if r.keys.get(est.key):
            members.setdefault(final[r.id], set()).add(norm(r.keys[est.key]))
    assert all(len(s) == 1 for s in members.values())
    # the founder's requirement: asking by the name of an organisation two SOURCES hold resolves to
    # ONE node, which cites both
    cross = [g for name, g in _name_groups(est).items()
             if len({r.source for r in g}) > 1 or any(name == norm(n) for t in est.texts.values()
                                                      for n in [g[0].label] if n in t)]
    cross = [g for g in cross if len(_key_values(est, g)) <= 1]
    assert cross, "no organisation is held by two sources (vacuous)"
    for g in cross:
        match = resolve_entity(store, g[0].label)
        assert match.node_id == final[g[0].id] and not match.candidates, (g[0].label, match)
    if est.name == "finance":
        fair = [g for g in cross if norm(g[0].label) == norm("Fairmont Reach Property LLC")]
        assert fair and resolve_entity(store, "Fairmont Reach Property LLC").node_id
    # the ground truth's near-name twins never share a node
    for a, b in est.non_matches:
        fa = {final[r] for r in a if r in final}
        fb = {final[r] for r in b if r in final}
        assert not (fa & fb)


def test_persons_are_never_merged_and_stay_candidates(estate):
    est, v = estate
    store = GraphStore()
    _build(est, v, store)
    persons_before = {r["id"] for r in store.open_nodes() if r["kind"] == est.person_kind}
    _approve(est, v, "business_key", "exact_name")
    consolidate_identity(store, v)
    persons_after = {r["id"] for r in store.open_nodes() if r["kind"] == est.person_kind}
    assert persons_after == persons_before
    # a person's name read from a document and held by a register row: candidates, never one
    named = [r for r in est.persons if any(r.label in t for t in est.texts.values())]
    assert named, "no person of the estate is named in its documents (vacuous)"
    match = resolve_entity(store, named[0].label)
    assert match.node_id is None and len(match.candidates) >= 2
    # and an approval can never be recorded for a subject kind
    with pytest.raises(ValueError):
        ontology_state.record_identity_rule_decision(
            pack=est.pack, kind=est.person_kind, rule="exact_name", principal=PRINCIPAL, vocab=v)
    out = upsert_node(store, _row_node(est, named[0], est.person_kind), v, id_only=True)
    assert out.requires_human_selection is True


def test_an_approved_rule_joins_a_new_mention_at_upsert_time(estate):
    est, v = estate
    rec = next(r for r in est.orgs if any(r.label in t for t in est.texts.values()))
    doc, text = next((d, t) for d, t in est.texts.items() if rec.label in t)
    store = GraphStore()
    row = upsert_node(store, _row_node(est, rec, est.org_kind), v, id_only=True)
    before = upsert_node(store, _mention(est.org_kind, doc, text, rec.label, False), v)
    assert before.id != row.id and before.action == "inserted"          # inert rule
    store2 = GraphStore()
    row2 = upsert_node(store2, _row_node(est, rec, est.org_kind), v, id_only=True)
    _approve(est, v, "exact_name")
    after = upsert_node(store2, _mention(est.org_kind, doc, text, rec.label, False), v)
    assert (after.id, after.matched_by) == (row2.id, "exact_name")
    assert store2.open_node_row(row2.id)["label"] == rec.label


def test_a_merged_record_re_sighted_later_lands_on_its_survivor(estate):
    est, v = estate
    store = GraphStore()
    where = _build(est, v, store)
    _approve(est, v, "business_key", "exact_name")
    report = consolidate_identity(store, v)
    absorbed = sorted(a for m in report.merged for a in m["absorbed"])
    assert absorbed, "nothing was absorbed (vacuous)"
    for aid in absorbed:
        last = store.all_node_versions(aid)[-1]
        again = merge_mod.row_to_node(store, last).model_copy(update={"system_to": None})
        out = upsert_node(store, again, v, id_only=True)
        assert out.matched_by == "absorbed" and out.id == _now_at(store, aid), aid
        assert store.open_node_row(aid) is None


def test_a_revoked_rule_stops_merging(estate):
    est, v = estate
    _approve(est, v, "exact_name")
    rule = v.identity_rule_for(est.org_kind)
    assert merge_mod.approved_rule_types(rule, v) == {"exact_name"}
    ontology_state.record_identity_rule_decision(pack=est.pack, kind=est.org_kind,
                                                 rule="exact_name", principal=PRINCIPAL,
                                                 decision="revoked", vocab=v)
    assert merge_mod.approved_rule_types(rule, v) == frozenset()
    with pytest.raises(ValueError):
        ontology_state.record_identity_rule_decision(pack=est.pack, kind=est.org_kind,
                                                     rule="exact_name", principal="  ", vocab=v)
    assert ontology_state.IDENTITY_RULE_TYPES == IDENTITY_RULE_TYPES


def _pack_copy(tmp_path: Path, name: str, extra: str) -> Path:
    root = tmp_path / "rootcopy"
    (root / name).mkdir(parents=True, exist_ok=True)
    text = (DEFAULT_PACKS_ROOT / name / "ontology.yaml").read_text(encoding="utf-8")
    (root / name / "ontology.yaml").write_text(text.replace("\r\n", "\n") + extra,
                                               encoding="utf-8")
    return root


@pytest.mark.parametrize("pack", ["finance", "workspace"])
def test_the_loader_refuses_an_identity_rule_on_a_subject(tmp_path, pack):
    subject = sorted(load_pack(pack).subject_kinds)[0]
    extra = ("\n" if pack == "finance" else "\nidentity_rules:\n") + (
        f"  - kind: {subject}\n    exact_name: true\n    similarity_merge: false\n")
    if pack == "finance":                     # finance already has an identity_rules list
        extra = extra.replace("\n  - kind", "  - kind", 1)
    root = _pack_copy(tmp_path, pack, extra)
    with pytest.raises(PackError, match="SUBJECT"):
        load_pack(pack, root, use_cache=False)


def test_a_rule_clashing_with_another_packs_subject_is_refused(tmp_path):
    root = _pack_copy(tmp_path, "finance", "")
    rule_kind = sorted(load_pack("finance", root, use_cache=False).identity_rules)[0]
    (root / "clashing").mkdir()
    (root / "clashing" / "ontology.yaml").write_text(
        f"pack: clashing\nversion: '1'\nnode_kinds:\n  - name: {rule_kind}\n    binds_to: "
        f"organisation\nedge_kinds: []\nsubject_kinds:\n  - kind: {rule_kind}\n    identifiers: "
        f"[x]\n    attestation: verified_channel\n    grant_type: G\n", encoding="utf-8")
    with pytest.raises(PackError, match="clash"):
        build_vocabulary(["finance", "clashing"], root)
    assert build_vocabulary(["finance"], root).identity_rules           # control


# --- FIN-07 -----------------------------------------------------------------------------------
def _doc(text: str, item: str, digest: str) -> DocumentRecord:
    snap = SnapshotIdentity(source_id=item, as_of=T0, content_hash=digest, schema_hash="s")
    return DocumentRecord(document_id=item, sha256=digest, snapshot=snap,
                          blocks=[Block(kind="Paragraph", text=text)],
                          source_meta={"capture_digest": digest, "uri": item})


def _same_titled_items(est: Estate, v):
    """(document kind, title, [(item, text, issuer-or-None)]) for a title two items share."""
    from databridge_graph.meta import NodeMetaKind
    doc_kind = v.nodes_binding_to(NodeMetaKind.DOCUMENT)
    if est.name == "finance":
        titles: dict[str, list] = {}
        for item, text in est.texts.items():
            m = re.search(r"^\*\*(.+?)\*\*\s*$", text, re.M)
            head = re.search(r"^# (.+)$", text, re.M)
            if m and head and item.startswith("filings"):
                titles.setdefault(m.group(1), []).append((item, text, head.group(1).strip()))
        title, items = max(titles.items(), key=lambda kv: len({i[2] for i in kv[1]}))
        return "filing", title, items
    # workspace: the longest string two of its files both carry, read as a title
    best = None
    items = list(est.texts.items())
    for i, (a, ta) in enumerate(items):
        for b, tb in items[i + 1:]:
            for line in ta.splitlines():
                for name in [r.label for r in est.orgs] + [line.strip()]:
                    if len(name) > 12 and name in ta and name in tb and set(name) - set("|-: "):
                        if best is None or len(name) > len(best[0]):
                            best = (name, [(a, ta, None), (b, tb, None)])
    assert doc_kind and best
    return doc_kind[0], best[0], best[1]


def test_documents_sharing_a_title_stay_separate_documents(estate):
    est, v = estate
    kind, title, items = _same_titled_items(est, v)
    assert len(items) >= 2 and v.is_document_kind(kind)
    issuer_edge = "issued_by" if "issued_by" in v.edge_kinds else None
    store = GraphStore()
    ids = set()
    for item, text, issuer in items:
        s = text.index(title)
        ents = [{"name": title, "kind": kind, "char_start": s, "char_end": s + len(title)}]
        rels = []
        if issuer and issuer_edge and issuer in text:
            i = text.index(issuer)
            ents.append({"name": issuer, "kind": est.org_kind, "char_start": i,
                         "char_end": i + len(issuer)})
            lo, hi = min(i, s), max(i + len(issuer), s + len(title))
            rels.append({"src_name": issuer, "dst_name": title, "rel_type": issuer_edge,
                         "evidence_char_start": lo, "evidence_char_end": hi})
        h = Harness(FakeBackend(gold_extraction_handler(ents, rels)), profile="cloud")
        res = extract_graph(_doc(text, item, f"sha-{item}"), h, store=store)
        ids |= {u.id for u in res.upserts
                if (store.open_node_row(u.id) or {"kind": None})["kind"] == kind}
    docs = [r for r in store.open_nodes() if r["kind"] == kind]
    assert len(docs) == len(items) == len(ids)
    if est.name == "finance":
        issuers = {i[2] for i in items}
        assert len(issuers) > 1, "the shared title is not shared across companies (vacuous)"
        for d in docs:
            linked = {e["src_id"] for e in store.edges_touching(d["id"])
                      if e["system_to"] is None and e["kind"] == issuer_edge}
            assert len(linked) == 1, (d["id"], linked)
    # the same item read again is the same document
    item, text, _ = items[0]
    s = text.index(title)
    h = Harness(FakeBackend(gold_extraction_handler(
        [{"name": title, "kind": kind, "char_start": s, "char_end": s + len(title)}], [])),
        profile="cloud")
    extract_graph(_doc(text, item, f"sha-{item}"), h, store=store)
    assert len([r for r in store.open_nodes() if r["kind"] == kind]) == len(items)


def test_a_document_is_never_merged_by_title_similarity(estate):
    est, v = estate
    kind, title, items = _same_titled_items(est, v)
    store = GraphStore()
    first = _mention(kind, items[0][0], items[0][1], title, False)
    store.add_node(first)
    other = _mention(kind, items[1][0], items[1][1], title, False)
    other = other.model_copy(update={"id": other.id + "@" + items[1][0]})
    assert resolve_existing(store, other, v) == (None, "")
    # control: a non-document, non-rule, non-subject kind with the same label still fuzzy-merges
    plain = sorted(k for k in v.node_kinds if not v.is_subject(k) and not v.is_document_kind(k)
                   and v.identity_rule_for(k) is None
                   and v.node_kinds[k].binds_to.value in ("entity", "organisation"))[0]
    store.add_node(_mention(plain, items[0][0], items[0][1], title, False))
    probe = _mention(plain, items[1][0], items[1][1], title, False)
    probe = probe.model_copy(update={"id": probe.id + "@x"})
    assert resolve_existing(store, probe, v)[1] == "fuzzy"


def test_a_name_never_joins_records_whose_business_keys_disagree(estate):
    """A third record carrying A's exact name and B's business key (two registered companies, one
    name) joins B by its key and is never joined to A by its name."""
    est, v = estate
    keyed = [r for r in est.orgs if r.keys.get(est.key)]
    a, b = keyed[0], next(r for r in keyed if norm(r.keys[est.key]) != norm(keyed[0].keys[est.key])
                          and norm(r.label) != norm(keyed[0].label))
    third = Record(id=f"{a.id}-x", label=a.label.upper(), source="another_register",
                   table="registry", row="1", keys={est.key: b.keys[est.key]})
    store = GraphStore()
    ids = {r.id: upsert_node(store, _row_node(est, r, est.org_kind), v, id_only=True).id
           for r in (a, b, third)}
    _approve(est, v, "business_key", "exact_name")
    report = consolidate_identity(store, v)
    assert _now_at(store, ids[third.id]) == _now_at(store, ids[b.id])
    assert _now_at(store, ids[a.id]) != _now_at(store, ids[b.id])
    assert any(r["rule"] == "exact_name" for r in report.refused)
    # and at upsert time: the name names A, the key names B -- the key decides, never the name
    fresh = GraphStore()
    upsert_node(fresh, _row_node(est, a, est.org_kind), v, id_only=True)
    out = upsert_node(fresh, _row_node(est, third, est.org_kind), v)
    assert out.id != _row_node(est, a, est.org_kind).id
    # a record joined by the KEY does not rename or re-key the record it joins
    keyed_store = GraphStore()
    b_id = upsert_node(keyed_store, _row_node(est, b, est.org_kind), v, id_only=True).id
    joined = upsert_node(keyed_store, _row_node(est, third, est.org_kind), v)
    assert (joined.id, joined.matched_by) == (b_id, "business_key")
    row = keyed_store.open_node_row(b_id)
    assert (row["label"], row["canonical_id"]) == (b.label, _row_node(est, b, est.org_kind).canonical_id)

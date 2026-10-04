"""ID7 — the live graph benefits from FIN-04 (finance dry run, 2026-10-02; wiki/20-decisions/D-004).

1. **Per-row kind split.** A table that holds people AND organisations (finance
   `core_banking.party`, its `party_type` spelled five ways) gets a PROPOSED split: rows whose
   normalised discriminator value fits an organisation kind become that kind; every other row —
   unrecognised, NULL, masked — stays the subject (the safe side, never merged). The binding is
   the approved mapping's, proposed from the column's values and the pack's own words.
2. **Organisation kinds.** A CRM organisation table maps to the organisation kind the pack's
   declarations fit best; a document mention is extracted against an ontology that offers it.
3. **Consolidation after each run.** The pack's APPROVED identity rules merge what a run wrote;
   unapproved rules merge nothing; the merges are in the run report. A person is never merged.

Every test runs on TWO estates — finance (`fixtures/packs/finance`) and workspace (Larkspur,
`fixtures/workspace_demo`) — and derives its expectation from the estate's data. The workspace
pack declares no person/organisation alternatives and no identity rule, so its estate binds a copy
of the workspace pack that does (one sentence cross-referencing `organisation` from `person`, and
the identity rule GRAPH6's tests use), and its mixed table is assembled from its OWN rows
(employees + customers in one table, with an entry-type column).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
import sys
import unicodedata
from pathlib import Path

import pytest

import estates
import conftest
from conftest import fs_connection

_principal = conftest.test_principal          # a named, authenticated test human

ESTATE_NAMES = ("finance", "workspace")
NOW = dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc)


# --- the test's OWN statements of the rules (never imported from the code under test) ------------
def norm_value(v) -> str:
    t = unicodedata.normalize("NFKC", str(v)).casefold()
    return " ".join(re.findall(r"[^\W_]+", t))


def norm_name(name: str) -> str:
    t = unicodedata.normalize("NFKC", name).casefold().replace("_", " ")
    return " ".join(re.sub(r"[^\w]+", " ", t).split())


# --- estates -------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def estate_snaps(tmp_path_factory):
    out = {}
    for name in ESTATE_NAMES:
        d = tmp_path_factory.mktemp(f"id7_{name}")
        db = estates.build_estate(name, d / "e.sqlite")
        out[name] = estates.snapshots(db, name)
        db.close()
    return out


def _directory(snaps: dict) -> dict:
    """Workspace's mixed table, from its own rows: every employee (a person) and every customer
    (an organisation) in ONE table, the entry type spelled several ways (as finance spells it)."""
    emp, cust = snaps["public.employees"], snaps["public.customers"]
    spell_p, spell_o = ("Person", "PERSON", "person"), ("ORGANISATION", "organisation", "Org")
    rows = [{"entry_id": r["employee_id"], "entry_type": spell_p[i % 3], "name": r["full_name"]}
            for i, r in enumerate(emp["rows"])]
    rows += [{"entry_id": r["customer_id"], "entry_type": spell_o[i % 3], "name": r["name"]}
             for i, r in enumerate(cust["rows"])]
    snap = copy.deepcopy(emp)
    snap.update(table="persons", rows=rows, row_count_total=len(rows), primary_key=["entry_id"],
                foreign_keys=[], columns=[{"name": c, "type": "TEXT"}
                                          for c in ("entry_id", "entry_type", "name")])
    for k in ("profile", "history_snapshot"):
        snap.pop(k, None)
    return snap


@pytest.fixture(params=ESTATE_NAMES)
def est(request, estate_snaps, tmp_path, monkeypatch):
    """{name, vocab, snaps, mixed, org_table, person_kind, org_kind, is_org(row)}, bound, with an
    empty ontology state of its own."""
    from databridge_graph import ontology_state
    from databridge_graph.packs import DEFAULT_PACKS_ROOT, clear_caches, deployment

    name = request.param
    monkeypatch.setenv(ontology_state.STATE_ENV, str(tmp_path / "ontology_state.json"))
    snaps = dict(estate_snaps[name])
    if name == "workspace":
        root = tmp_path / "packs"
        (root / "workspace").mkdir(parents=True)
        text = (DEFAULT_PACKS_ROOT / "workspace" / "ontology.yaml").read_text(encoding="utf-8")
        line = next(ln for ln in text.splitlines()
                    if ln.strip().startswith("description: A natural"))
        text = text.replace(line, line + " Use `organisation` for a company.")
        text += ("\nidentity_rules:\n  - kind: organisation\n    business_keys: [domain]\n"
                 "    exact_name: true\n    similarity_merge: false\n")
        (root / "workspace" / "ontology.yaml").write_text(text, encoding="utf-8")
        monkeypatch.setenv("DATABRIDGE_PACKS_ROOT", str(root))
        snaps["public.persons"] = _directory(snaps)
        mixed, org_table = "public.persons", "public.customers"
        orgs = {r["customer_id"] for r in snaps["public.customers"]["rows"]}
        is_org = lambda r: r["entry_id"] in orgs                     # noqa: E731
        pk = "entry_id"
    else:
        mixed, org_table = "core_banking.party", "crm.account_org"
        # the data's own tell: a company has an incorporation date, a person a date of birth
        is_org = lambda r: r["incorporation_date"] is not None       # noqa: E731
        pk = "party_id"
    clear_caches()
    with deployment(estates.ESTATES[name]["pack"]) as vocab:
        rules_kinds = [k for k in vocab.identity_rules]
        assert len(rules_kinds) == 1
        yield {"name": name, "vocab": vocab, "snaps": snaps, "mixed": mixed,
               "org_table": org_table, "org_kind": rules_kinds[0], "is_org": is_org, "pk": pk}
    clear_caches()


def _approved(tm) -> dict:
    return {**tm.model_dump(mode="json"), "status": "approved"}


def _store(tmp_path, name):
    from databridge_graph.store import GraphStore

    return GraphStore(str(tmp_path / f"{name}.db"))


# === 1. the split is PROPOSED from the column's values and the pack's words ========================
def test_a_mixed_table_is_proposed_a_split_by_the_ontology(est):
    from databridge_mcp_ingest.mapping import propose_rules, split_candidates

    vocab, snaps = est["vocab"], est["snaps"]
    tm = propose_rules({est["mixed"]: snaps[est["mixed"]]}, vocab, context=snaps)[est["mixed"]]
    person = tm.node["kind"]
    assert vocab.is_subject(person) and est["org_kind"] in split_candidates(vocab, person)
    split = tm.split
    assert split is not None, tm.rationale
    assert split["default"] == person and tm.status == "proposed"
    rows = snaps[est["mixed"]]["rows"]
    col = split["column"]
    # every spelling of one value is ONE binding, and the binding is keyed by normalised value
    spellings = {}
    for r in rows:
        spellings.setdefault(norm_value(r[col]), set()).add(r[col])
    assert set(split["kinds"]) <= set(spellings) and max(map(len, spellings.values())) > 1
    # the split agrees with the data's own tell, row by row: organisations -> the org kind,
    # everything else stays the subject
    for r in rows:
        got = split["kinds"].get(norm_value(r[col]), split["default"])
        assert got == (est["org_kind"] if est["is_org"](r) else person), (r[est["pk"]], r[col])
    assert any(est["is_org"](r) for r in rows) and not all(est["is_org"](r) for r in rows)
    # the rationale says what was proposed, value by value
    assert any("kind split" in line and col in line for line in tm.rationale)


def test_no_split_without_the_packs_cross_reference(est):
    """The split is the pack's to allow: a subject kind whose declarations name no alternative is
    never split, whatever its rows say (non-vacuous: the same rows ARE split when it does)."""
    from databridge_mcp_ingest.mapping import propose_split, split_candidates

    vocab, snap = est["vocab"], est["snaps"][est["mixed"]]
    person = next(k for k in vocab.subject_kinds if split_candidates(vocab, k))
    split, _ = propose_split(snap, person, vocab)
    assert split is not None
    # the same vocabulary with every cross-reference removed from the descriptions
    import dataclasses

    plain = {k: dataclasses.replace(d, description=re.sub(r"`[^`]*`", "x", d.description or ""))
             for k, d in vocab.node_kinds.items()}
    stripped = dataclasses.replace(vocab, node_kinds=plain)
    assert split_candidates(stripped, person) == []
    assert propose_split(snap, person, stripped) == (None, [])
    # and never a non-subject: an organisation table is never split
    assert propose_split(snap, est["org_kind"], vocab) == (None, [])


def test_validation_keeps_unrecognised_rows_on_the_protected_side(est):
    from databridge_mcp_ingest.mapping import (propose_rules, validate_table_mapping)

    vocab, snaps = est["vocab"], est["snaps"]
    tm = propose_rules({est["mixed"]: snaps[est["mixed"]]}, vocab, context=snaps)[est["mixed"]]
    assert validate_table_mapping(est["mixed"], tm, vocab, check_references=True) == []
    person, org = tm.node["kind"], est["org_kind"]
    flipped = tm.model_copy(update={"node": {**tm.node, "kind": org}, "split": {
        **tm.split, "default": org, "kinds": {v: person for v in tm.split["kinds"]}}})
    probs = validate_table_mapping(est["mixed"], flipped, vocab)
    assert any("protected side" in p for p in probs), probs
    raw = next(r[tm.split["column"]] for r in snaps[est["mixed"]]["rows"]
               if norm_value(r[tm.split["column"]]) != r[tm.split["column"]])
    for bad, why in (({**tm.split, "kinds": {raw: org}}, "not normalised"),
                     ({**tm.split, "kinds": {"x": "no_such_kind"}}, "not in the active"),
                     ({**tm.split, "default": org}, "is not the node kind"),
                     ({**tm.split, "column": "no_such_column"}, "does not have")):
        probs = validate_table_mapping(est["mixed"], tm.model_copy(update={"split": bad}), vocab,
                                       check_references=True)
        assert any(why in p for p in probs), (why, probs)


@pytest.mark.real_approval
def test_the_split_is_approved_like_any_mapping(est, walnut_env):
    from databridge_mcp_ingest.mapping import (SourceMapping, additive_problems, approval_problem,
                                               approve_mapping, load_mapping, mapping_digest,
                                               propose_rules, resolve_pending_splits,
                                               save_mapping)

    vocab, snaps = est["vocab"], est["snaps"]
    t = est["mixed"]
    tm = propose_rules({t: snaps[t]}, vocab, context=snaps)[t]
    # an approved mapping WITHOUT the split (made before splits existed) is re-proposed with it
    old = tm.model_copy(update={"split": None})
    save_mapping(SourceMapping(source_id="s", tables={t: old}))
    approve_mapping("s", principal=_principal("analyst"), vocab=vocab)
    m = load_mapping("s")
    assert m.tables[t].status == "approved"
    # the digest of a mapping without a split is what it always was (no approval moves)
    body = {"node": old.node, "edges": old.edges, "columns": sorted(old.columns),
            "fk_columns": sorted(old.fk_columns or [])}
    import hashlib
    assert mapping_digest(old) == hashlib.sha256(json.dumps(
        body, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    got = resolve_pending_splits(m.tables, {t: snaps[t]}, vocab)
    assert got[t].split == tm.split and got[t].status == "proposed" and got[t].approval is None
    # a person's own (manual) entry is never touched
    assert resolve_pending_splits({t: m.tables[t].model_copy(update={"origin": "manual"})},
                                  {t: snaps[t]}, vocab) == {}
    save_mapping(load_mapping("s").model_copy(update={"tables": got}))
    approve_mapping("s", principal=_principal("analyst"), vocab=vocab)
    m = load_mapping("s")
    assert m.tables[t].status == "approved" and m.tables[t].split == tm.split
    assert approval_problem("s", t, m.tables[t], vocab) is None
    # the split is inside the approval: editing it after approval un-approves the table
    edited = m.tables[t].model_copy(update={"split": {**tm.split, "kinds": {}}})
    assert "digest" in approval_problem("s", t, edited, vocab)
    # and a delegation can never add, change or remove one
    probs = additive_problems("s", t, edited, vocab, current=m.tables[t])
    assert any("kind split" in p for p in probs)


# === 2. organisation tables map to the organisation kind by ontology fit ======================
def test_an_organisation_table_maps_to_the_kind_its_words_fit(est):
    from databridge_graph.meta import NodeMetaKind
    from databridge_mcp_ingest.mapping import propose_rules

    vocab, snaps = est["vocab"], est["snaps"]
    t = est["org_table"]
    tm = propose_rules({t: snaps[t]}, vocab, context=snaps)[t]
    assert tm.status == "proposed" and tm.node["kind"] == est["org_kind"], tm.rationale
    assert not vocab.is_subject(tm.node["kind"])
    bound = vocab.nodes_binding_to(NodeMetaKind.ORGANISATION)
    assert est["org_kind"] in bound
    if len(bound) > 1:
        # chosen among several organisation kinds, by the pack's own words, and it says so
        assert "fits its words best" in tm.rationale[0], tm.rationale


def test_documents_are_offered_the_organisation_kind_with_its_description(est):
    from databridge_graph.extractor import extraction_vocabulary

    ev = extraction_vocabulary()
    assert est["org_kind"] in ev.node_kinds
    desc = est["vocab"].node_kinds[est["org_kind"]].description
    assert desc and ev.descriptions.get(est["org_kind"]) == desc


# === 1+3. the build splits rows; references meet them; consolidation merges organisations ========
def _maps(est) -> dict:
    from databridge_mcp_ingest.mapping import propose_rules

    snaps = est["snaps"]
    tabs = {t: snaps[t] for t in (est["mixed"], est["org_table"])}
    return {t: _approved(tm) for t, tm in propose_rules(tabs, est["vocab"],
                                                        context=snaps).items()}


def _open_of(store, kind):
    return store.fetchall("SELECT * FROM nodes WHERE kind = ? AND system_to IS NULL AND "
                          "valid_to IS NULL", (kind,))


def _referrer(est) -> tuple[str, dict, str]:
    """A table with a declared FK into the mixed table, from the estate: finance's facilities
    (`party_id`); workspace's contacts, whose `customer_id` is re-pointed at the directory (it
    holds directory keys — the customers it lists)."""
    snaps = est["snaps"]
    if est["name"] == "finance":
        t = "core_banking.facility"
        return t, snaps[t], next(f["column"] for f in snaps[t]["foreign_keys"]
                                 if f["ref_table"] == "party")
    s = copy.deepcopy(snaps["public.contacts"])
    s["foreign_keys"] = [{**f, "ref_table": "persons", "ref_column": "entry_id"}
                         if f["column"] == "customer_id" else f for f in s["foreign_keys"]]
    return "public.contacts", s, "customer_id"


@pytest.mark.parametrize("same_run", (True, False))
def test_the_build_splits_rows_and_references_meet_them(est, walnut_env, tmp_path, same_run):
    from databridge_mcp_ingest.build import build_tables
    from databridge_mcp_ingest.mapping import propose_rules

    vocab, snaps = est["vocab"], est["snaps"]
    maps = _maps(est)
    mixed = est["mixed"]
    split = maps[mixed]["split"]
    person, org = split["default"], est["org_kind"]
    rt, rsnap, col = _referrer(est)
    kinds = {mixed: person}
    rmap = _approved(propose_rules({rt: rsnap}, vocab, known_kinds=kinds)[rt])
    assert any(col in e["evidence"] for e in rmap["edges"])
    store = _store(tmp_path, f"split_{same_run}")
    rows = snaps[mixed]["rows"]
    if same_run:
        out = build_tables(store, vocab, source_id="src", retrieved_at=NOW, mask_cfg=None,
                           tables=[(snaps[mixed], "sha-m", maps[mixed]), (rsnap, "sha-r", rmap)])
    else:
        build_tables(store, vocab, source_id="src", retrieved_at=NOW, mask_cfg=None,
                     tables=[(snaps[mixed], "sha-m", maps[mixed])])
        out = build_tables(store, vocab, source_id="src", retrieved_at=NOW, mask_cfg=None,
                           tables=[(rsnap, "sha-r", rmap)], kind_splits={mixed: split},
                           context={("src", mixed): snaps[mixed]})
    # one node per row, of the kind the data says
    n_org = sum(1 for r in rows if est["is_org"](r))
    ours = lambda kind: [r for r in _open_of(store, kind)  # noqa: E731
                         if str(r["canonical_id"]).startswith(f"src/{mixed}/")]
    assert len(ours(org)) == n_org > 0
    assert len(ours(person)) == len(rows) - n_org > 0
    # each node cites WHY it is that kind: the discriminator cell, through the approved split
    # (an org row's ids are the source row's; its `walnut_kind` never becomes an attribute)
    assert all("walnut_kind" not in json.loads(r["attrs_json"] or "{}") for r in ours(org))
    # every reference into the split table met its row, whatever kind the row became
    keys = {str(r[est["pk"]]): est["is_org"](r) for r in rows}
    want = [r[col] for r in rsnap["rows"] if r[col] is not None and str(r[col]) in keys]
    rec = next(r for r in out["completeness"] if r["table"] == rt)
    ref = next(f for f in rec["foreign_keys"] if f["column"] == col)
    assert ref["edges_built"] == len(want) > 0 and out["kind_not_admitted"] == []
    ekind = next(e["kind"] for e in rmap["edges"] if col in e["evidence"])
    ends = {r["src_id"] for r in store.fetchall(
        "SELECT src_id, dst_id FROM edges WHERE kind = ? AND system_to IS NULL", (ekind,))} | {
        r["dst_id"] for r in store.fetchall(
            "SELECT src_id, dst_id FROM edges WHERE kind = ? AND system_to IS NULL", (ekind,))}
    assert any(e.startswith(f"{org}:") for e in ends) == any(keys[str(v)] for v in want)
    assert any(e.startswith(f"{org}:") for e in ends)


def test_unrecognised_null_and_masked_values_stay_the_subject(est, walnut_env, tmp_path):
    """Rows the approved split does not recognise — a value it never bound, no value, a value the
    screen masks — are built as the SUBJECT (the safe side), never as the organisation kind."""
    from databridge_mcp_ingest.build import KIND_KEY, ROW_KEY, _row_kind, build_tables

    vocab, snaps = est["vocab"], est["snaps"]
    maps = _maps(est)
    mixed = est["mixed"]
    split = maps[mixed]["split"]
    person, col, pk = split["default"], split["column"], est["pk"]
    orgs = [r for r in snaps[mixed]["rows"] if est["is_org"](r)][:2]
    assert len(orgs) == 2
    known = set(split["kinds"])
    odd = next(w for w in ("zzunbound", "zzunbound2") if norm_value(w) not in known)
    extra = [{**orgs[0], pk: f"{orgs[0][pk]}-X1", col: odd},
             {**orgs[1], pk: f"{orgs[1][pk]}-X2", col: None}]
    snap = copy.deepcopy(snaps[mixed])
    snap["rows"] = extra
    store = _store(tmp_path, "odd")
    build_tables(store, vocab, source_id="src", retrieved_at=NOW, mask_cfg=None,
                 tables=[(snap, "sha-odd", maps[mixed])])
    kinds = {r["kind"] for r in store.fetchall("SELECT kind FROM nodes WHERE system_to IS NULL")}
    assert kinds == {person}
    # a masked discriminator: the default, cited from the row identity (never the raw value)
    from databridge_core import SourcePointer
    cell = SourcePointer(source_id="s", kind="table_cell", table=mixed, column=col, row_id="1")
    rk = SourcePointer(source_id="s", kind="table_cell", table=mixed, column=pk, row_id="1")
    org_value = next(v for v, k in split["kinds"].items() if k != person)
    kind, ptr = _row_kind(split, {col: org_value}, {ROW_KEY: "x"}, {col: cell, ROW_KEY: rk},
                          {col})
    assert kind == person and "masked" in ptr.derivation
    kind, _ = _row_kind(split, {col: org_value}, {ROW_KEY: "x"}, {col: cell, ROW_KEY: rk}, set())
    assert kind != person and KIND_KEY == "walnut_kind"


def test_a_reference_the_edge_kind_does_not_admit_for_the_split_kind_is_not_built(
        est, walnut_env, tmp_path):
    """A reference into a split table whose edge's declaration admits the subject kind but NOT
    the kind a row was split into is never written under a kind it was not declared for: it is
    counted `kind_not_admitted`, and the other rows' edges are built."""
    from databridge_mcp_ingest.build import build_tables

    vocab, snaps = est["vocab"], est["snaps"]
    maps = _maps(est)
    mixed = est["mixed"]
    split = maps[mixed]["split"]
    person, org = split["default"], est["org_kind"]
    # a declared edge kind admitting `person` at one end and some kind X at the other, but not
    # `org` where `person` is: found in the vocabulary, never named
    from databridge_graph.meta import ATTESTED_META_KINDS
    from databridge_mcp_ingest.build import _admits

    case = None
    for name, d in sorted(vocab.edge_kinds.items()):
        if d.binds_to in ATTESTED_META_KINDS:
            continue                                # never built from a row at all
        for other in sorted(vocab.node_kinds):
            if other in (person, org) or vocab.is_subject(other):
                continue
            if _admits(d, "src", person) and _admits(d, "dst", other) and \
                    not _admits(d, "src", org):
                case = (name, other)
                break
        if case:
            break
    if case is None:
        # finance declares `legal_entity` wherever `party` is a (non-attested) endpoint, so no
        # reference can be dropped this way there: asserted, and the mechanism is the other
        # estate's to show (its pack has such an edge)
        assert est["name"] == "finance"
        assert all(_admits(d, side, org) for d in vocab.edge_kinds.values()
                   if d.binds_to not in ATTESTED_META_KINDS for side in ("src", "dst")
                   if _admits(d, side, person))
        return
    ekind, other = case
    rows = snaps[mixed]["rows"]
    ref = {"schema": snaps[mixed].get("schema"), "table": "links", "primary_key": ["link_id"],
           "foreign_keys": [{"column": "member", "ref_schema": snaps[mixed].get("schema"),
                             "ref_table": snaps[mixed]["table"], "ref_column": est["pk"]}],
           "columns": [{"name": "link_id", "type": "TEXT"}, {"name": "member", "type": "TEXT"}],
           "rows": [{"link_id": f"L-{i}", "member": r[est["pk"]]} for i, r in enumerate(rows)],
           "row_count_total": len(rows)}
    tref = f"{ref['schema']}.links" if ref["schema"] else "links"
    lmap = {"status": "approved", "node": {"kind": other, "label": "{link_id}",
                                           "sources": ["link_id"], "attrs": {}},
            "edges": [{"kind": ekind, "src": f"{person}:{{walnut_fk_member}}",
                       "dst": f"{other}:{{walnut_row_key}}", "evidence": ["member"],
                       "fk": "member", "reversed": True}],
            "columns": ["link_id", "member"], "fk_columns": ["member"]}
    store = _store(tmp_path, "admit")
    out = build_tables(store, vocab, source_id="src", retrieved_at=NOW, mask_cfg=None,
                       tables=[(snaps[mixed], "sha-m", maps[mixed]), (ref, "sha-l", lmap)])
    n_org = sum(1 for r in rows if est["is_org"](r))
    assert len(out["kind_not_admitted"]) == n_org > 0
    rec = next(r for r in out["completeness"] if r["table"] == tref)
    fk = rec["foreign_keys"][0]
    assert fk["edges_built"] == len(rows) - n_org > 0


def _approve_rules(est, *rules):
    from databridge_graph import ontology_state

    for rule in rules:
        ontology_state.record_identity_rule_decision(
            pack=estates.ESTATES[est["name"]]["pack"], kind=est["org_kind"], rule=rule,
            principal="reviewer@walnut.example", vocab=est["vocab"])


def test_consolidation_merges_only_under_an_approved_rule_and_never_a_person(
        est, walnut_env, tmp_path):
    from databridge_mcp_ingest.build import build_tables, consolidate_identity_after_run

    vocab, snaps = est["vocab"], est["snaps"]
    maps = _maps(est)
    store = _store(tmp_path, "consolidate")
    if est["name"] == "workspace":
        # the people the directory repeats, from their own table: two records, one name each
        from databridge_mcp_ingest.mapping import propose_rules
        t = "public.employees"
        maps[t] = _approved(propose_rules({t: snaps[t]}, vocab, context=snaps)[t])
    for t in maps:
        build_tables(store, vocab, source_id=f"src_{t.split('.')[0]}", retrieved_at=NOW,
                     mask_cfg=None, tables=[(snaps[t], f"sha-{t}", maps[t])])
    person, org = maps[est["mixed"]]["split"]["default"], est["org_kind"]
    persons_before = {r["id"] for r in _open_of(store, person)}
    by_name: dict[str, set] = {}
    for r in _open_of(store, org):
        by_name.setdefault(norm_name(r["label"]), set()).add(r["id"])
    shared = {n for n, ids in by_name.items() if len(ids) > 1}
    assert shared, "no organisation name is carried by two records (vacuous)"
    # declared but NOT approved: nothing merges, and the record says which kinds were looked at
    rec = consolidate_identity_after_run(store, vocab)
    assert rec["status"] == "ran" and rec["merged"] == [] and rec["kinds"] == {org: []}
    _approve_rules(est, "exact_name")
    rec = consolidate_identity_after_run(store, vocab)
    assert rec["merged"] and rec["kinds"] == {org: ["exact_name"]}
    assert all(m["kind"] == org for m in rec["merged"])
    after: dict[str, set] = {}
    for r in _open_of(store, org):
        after.setdefault(norm_name(r["label"]), set()).add(r["id"])
    for n in shared:
        refused = any(set(g["records"]) & by_name[n] for g in rec["refused"])
        assert len(after[n]) == 1 or refused, n
    assert sum(len(m["absorbed"]) for m in rec["merged"]) == rec["absorbed"] > 0
    # a person is NEVER merged: every subject node is still open, none listed anywhere
    assert {r["id"] for r in _open_of(store, person)} == persons_before
    absorbed = {a for m in rec["merged"] for a in m["absorbed"]}
    assert not absorbed & persons_before
    # non-vacuous for persons: two people of the estate share a name and stay two
    names: dict[str, set] = {}
    for r in _open_of(store, person):
        names.setdefault(norm_name(r["label"]), set()).add(r["id"])
    assert any(len(ids) > 1 for ids in names.values())


def test_an_unreadable_approval_state_merges_nothing(est, walnut_env, tmp_path, monkeypatch):
    from databridge_graph import ontology_state
    from databridge_mcp_ingest.build import build_tables, consolidate_identity_after_run

    vocab, snaps = est["vocab"], est["snaps"]
    maps = _maps(est)
    store = _store(tmp_path, "bad_state")
    build_tables(store, vocab, source_id="src", retrieved_at=NOW, mask_cfg=None,
                 tables=[(snaps[est["org_table"]], "sha", maps[est["org_table"]])])
    before = store.counts()
    bad = tmp_path / "broken_state.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv(ontology_state.STATE_ENV, str(bad))
    rec = consolidate_identity_after_run(store, vocab)
    assert rec["status"] == "refused" and rec["merged"] == [] and store.counts() == before


# === end to end: real syncs; the organisation becomes one node with pointers from each source ===
def _estate_profile(tmp_path, name):
    import yaml
    from databridge_mcp_ingest.plans import sql_dialect

    d = tmp_path / "profiles"
    d.mkdir(exist_ok=True)
    dialect = dict(sql_dialect("sqlite"))
    dialect["name"] = f"estate_{name}"
    dialect["list_tables"] = " UNION ALL ".join(
        f"SELECT '{s}' AS table_schema, name AS table_name FROM {s}.sqlite_master "
        f"WHERE type = 'table'" for s in estates.ESTATES[name]["schemas"]) + " ORDER BY 1, 2"
    (d / f"estate_{name}.sql.yaml").write_text(yaml.safe_dump(dialect), encoding="utf-8")
    (d / f"estate_{name}.profile.yaml").write_text(yaml.safe_dump({
        "id": f"estate-{name}", "title": "test estate", "priority": 100,
        "verified": "test fixture", "match": {"all_tools": ["query"]},
        "step": {"strategy": "tabular", "sql": {"tool": "query", "arg": "sql",
                                                "dialect": f"estate_{name}",
                                                "parse": {"mode": "json_rows"}}}}),
        encoding="utf-8")
    return d


def _doc_harness(org_names: set[str], person_names: set[str], org_kind: str, person_kind: str):
    """A stand-in model: in the text it is SENT, every name of an organisation row (as the kind
    the ontology it was offered calls a company) and of a person row (as the subject kind), at
    the offsets where it occurs. The prompt it was offered is recorded."""
    from databridge_harness import FakeBackend, Harness

    calls: list = []

    def handler(*, model, messages, guided_json) -> str:
        calls.append(messages)
        last = messages[-1]["content"]
        if not last.startswith("DOCUMENT TEXT:\n"):
            return json.dumps({})
        text = last[len("DOCUMENT TEXT:\n"):]
        ents = []
        for names, kind in ((org_names, org_kind), (person_names, person_kind)):
            for n in sorted(names):
                i = text.find(n)
                if i >= 0:
                    ents.append({"name": n, "kind": kind, "char_start": i,
                                 "char_end": i + len(n)})
        return json.dumps({"entities": ents, "relations": []})

    h = Harness(FakeBackend(handler), profile="cloud")
    h.calls = calls                                                  # type: ignore[attr-defined]
    return h


def _pointers(store, node_id) -> list[dict]:
    return [json.loads(r["source_pointer_json"]) for r in store.fetchall(
        "SELECT s.source_pointer_json FROM sources s WHERE s.entity_id = ?", (node_id,))]


def _leaves(p: dict):
    if p.get("kind") == "derived":
        for b in p.get("derived_from") or []:
            yield from _leaves(b)
    else:
        yield p


def test_e2e_finance_one_organisation_node_from_banking_crm_and_documents(
        walnut_env, tmp_path, monkeypatch):
    """Finance, through the real runs: the SQL estate (core banking + CRM), then the documents
    that name the company. Before the rule is approved the company is several nodes; after a
    named human approves the exact-legal-name rule, the next run's consolidation makes it ONE
    `legal_entity` node citing core banking, CRM and the documents — and two people who share a
    name are still two people."""
    import databridge_mcp_ingest as mi
    from databridge_graph import ontology_state
    from databridge_graph.packs import clear_caches, deployment
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest import register_connection

    monkeypatch.setenv(ontology_state.STATE_ENV, str(tmp_path / "ontology_state.json"))
    monkeypatch.setenv("WALNUT_CRAWL_PROFILES_DIR", str(_estate_profile(tmp_path, "finance")))
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(tmp_path / "facts"))
    monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(tmp_path / "passages"))
    db = estates.build_estate("finance", tmp_path / "e.sqlite")
    parties = [dict(r) for r in db.execute("SELECT * FROM core_banking.party")]
    crm = [dict(r) for r in db.execute("SELECT * FROM crm.account_org")]
    fin = estates.FIXTURES / "packs" / "finance"
    target = next(r["legal_name"] for r in parties
                  if r["incorporation_date"] is not None and r["registration_no"]
                  and any(c["org_name"] == r["legal_name"] for c in crm)
                  and sum(1 for p in (fin / "filings").rglob("*.md")
                          if r["legal_name"] in p.read_text(encoding="utf-8")) >= 2
                  and sum(1 for p in parties if norm_name(p["legal_name"]) ==
                          norm_name(r["legal_name"])) == 1)
    assert target == "Fairmont Reach Property LLC"      # the dry run's company, found by the data
    docs = tmp_path / "docs"
    docs.mkdir()
    n_docs = 0
    for d in ("filings", "credit_memos"):
        for p in sorted((fin / d).rglob("*.md")):
            text = p.read_text(encoding="utf-8")
            if target in text:
                (docs / p.name).write_text(text, encoding="utf-8")
                n_docs += 1
    assert n_docs >= 2
    orgs = {r["legal_name"] for r in parties if r["incorporation_date"] is not None}
    people = {r["legal_name"] for r in parties if r["date_of_birth"] is not None}
    twins = {n for n in people
             if sum(1 for r in parties if r["date_of_birth"] is not None
                    and norm_name(r["legal_name"]) == norm_name(n)) > 1}
    assert twins                                        # two people of the book share a name
    clear_caches()
    with deployment("finance") as vocab:
        store = GraphStore(str(tmp_path / "g.db"))
        client = estates.engine_client(db)
        register_connection({"id": "fin_sql", "transport": {"type": "stdio", "command": "uvx"},
                             "scope": {"tables_include": ["crm.account_org",
                                                          "crm.relationship_manager",
                                                          "core_banking.party",
                                                          "core_banking.facility"]},
                             "auto_approve": True})
        mi.propose_plan("fin_sql", client=client)
        rep1 = mi.run_ingest("fin_sql", store=store, client=client, vocab=vocab)
        assert rep1.status in ("ok", "unverified"), (rep1.status, rep1.message)
        # the estate plants text addressed to an automated reader in some tables: since R-029
        # (E2E run 5 F-01) the screen holds those ROWS, one by one, and the rest of each table
        # builds — the table itself is allowed, the planted rows are withheld and listed
        from databridge_mcp_ingest.runner import read_jsonl
        gov = read_jsonl(Path(rep1.run_dir) / "governance.jsonl")
        assert any(r["uri"] == "core_banking.party" and r.get("kind") == "table"
                   and r["decision"] == "allow" for r in gov)
        assert any(r.get("kind") == "table_row" and r.get("table") == "core_banking.party"
                   and r["decision"] == "review" for r in gov)
        from databridge_mcp_ingest.mapping import load_mapping
        m = load_mapping("fin_sql")
        assert m.tables["core_banking.party"].split["kinds"]
        assert m.tables["crm.account_org"].node["kind"] == "legal_entity"
        ident1 = rep1.counts["identity_consolidation"]
        assert ident1["kinds"] == {"legal_entity": []} and ident1["merged"] == 0
        named = lambda: [r for r in _open_of(store, "legal_entity")  # noqa: E731
                         if norm_name(r["label"]) == norm_name(target)]
        assert len(named()) == 2                         # core banking + CRM, not yet one

        register_connection(fs_connection("fin_docs", docs))
        harness = _doc_harness(orgs, people, "legal_entity", "party")
        mi.propose_plan("fin_docs")
        rep2 = mi.run_ingest("fin_docs", store=store, harness=harness, vocab=vocab)
        assert rep2.status in ("ok", "unverified"), (rep2.status, rep2.message)
        # the model was OFFERED the organisation kind, with the pack's description of it
        prompts = " ".join(m["content"] for c in harness.calls for m in c
                           if m.get("role") == "system")
        assert "legal_entity" in prompts and "COMPANY" in prompts
        # the documents' mentions are written, and nothing merges yet
        assert rep2.counts["identity_consolidation"]["merged"] == 0
        assert any(leaf.get("kind") == "doc_span" for r in named()
                   for p in _pointers(store, r["id"]) for leaf in _leaves(p))
        assert len(named()) >= 2

        # a named human approves the exact-legal-name rule; the next run consolidates
        ontology_state.record_identity_rule_decision(
            pack="finance", kind="legal_entity", rule="exact_name",
            principal="reviewer@walnut.example", vocab=vocab)
        rep3 = mi.run_ingest("fin_sql", store=store, client=client, vocab=vocab)
        assert rep3.status in ("ok", "unverified"), (rep3.status, rep3.message)
        assert rep3.verify["ok"], rep3.verify["problems"]
        # the run that WROTE the absorbed records still verifies: their pointers live on the node
        # that absorbed them
        from databridge_mcp_ingest.runner import verify_run
        again = verify_run("fin_sql", rep1.run_id, store=store)
        assert again["ok"], again["problems"]
        assert again["counts"]["store_provenance"]["graph_ids"] > 0
        ident = rep3.counts["identity_consolidation"]
        assert ident["kinds"] == {"legal_entity": ["exact_name"]} and ident["absorbed"] > 0
        report = json.loads((Path(rep3.run_dir) / "report.json").read_text(encoding="utf-8"))
        assert report["counts"]["identity_consolidation"]["merges"]
        [node] = named()
        leaves = [leaf for p in _pointers(store, node["id"]) for leaf in _leaves(p)]
        tables = {leaf.get("table") for leaf in leaves if leaf.get("kind") == "table_cell"}
        assert {"core_banking.party", "crm.account_org"} <= tables
        assert any(leaf.get("kind") == "doc_span" for leaf in leaves)
        # PERSONS: never merged — every individual row is its own open `party` node
        withheld = {b["row_id"] for b in read_jsonl(Path(rep1.run_dir) / "governance_chunks.jsonl")
                    if b.get("table") == "core_banking.party"}
        built = [r for r in parties if r["party_id"] not in withheld]
        party_nodes = [r for r in _open_of(store, "party")
                       if str(r["canonical_id"]).startswith("fin_sql/core_banking.party/")]
        assert len(party_nodes) == sum(1 for r in built if r["date_of_birth"] is not None) > 0
        assert all(next(p for p in built if f"/{p['party_id']}" in str(r["canonical_id"]))[
            "date_of_birth"] is not None for r in party_nodes)
        twins_built = [n for n in twins if sum(1 for r in built if r["date_of_birth"] and
                                                norm_name(r["legal_name"]) == norm_name(n)) > 1]
        assert twins_built
        for n in twins_built:
            assert len([r for r in party_nodes if norm_name(r["label"]) == norm_name(n)]) > 1
        assert all(m["kind"] == "legal_entity" for m in ident["merges"])
    clear_caches()
    db.close()


# === LINK7 cross-track 1: reject_link lives in the engine; the decider's sign-in is recorded ======
def _link_case(est):
    """A declared FK whose target is not a person kind, with its declaration dropped: the link
    the engine proposes in its place (TAB6's C-3 construction), from the estate."""
    from databridge_mcp_ingest.mapping import is_person_kind, propose_rules

    vocab, snaps = est["vocab"], est["snaps"]
    rules = propose_rules(snaps, vocab)
    for t, s in sorted(snaps.items()):
        if not rules.get(t) or not rules[t].node or len(s["rows"]) > 5000:
            continue
        for fk in s.get("foreign_keys") or []:
            ref = f"{fk['ref_schema']}.{fk['ref_table']}" if fk.get("ref_schema") \
                else fk["ref_table"]
            if ref != t and rules.get(ref) and rules[ref].node and \
                    not is_person_kind(vocab, rules[ref].node["kind"]):
                mine = copy.deepcopy(s)
                mine["foreign_keys"] = [f for f in mine["foreign_keys"]
                                        if f["column"] != fk["column"]]
                return t, fk["column"], ref, mine, rules[ref].node["kind"]
    raise AssertionError("no declared FK into a non-person kind")


@pytest.mark.real_approval
def test_a_link_is_rejected_in_the_engine_and_the_decision_is_kept(est, walnut_env):
    from databridge_mcp_ingest.mapping import (MappingApprovalRefused, SourceMapping,
                                               approve_link, load_mapping, propose_rules,
                                               refresh_link_proposals, reject_link, save_mapping)

    vocab, snaps = est["vocab"], est["snaps"]
    t, col, target, mine, tkind = _link_case(est)
    others = {"right": {target: (snaps[target], tkind)}}
    for sid in ("rej", "app"):
        save_mapping(SourceMapping(source_id=sid, tables=propose_rules({t: mine}, vocab)))
        m = refresh_link_proposals(sid, {t: mine}, vocab, others=others)
        assert [lk["status"] for lk in m.tables[t].links if lk["column"] == col] == ["proposed"]
    with pytest.raises(MappingApprovalRefused):
        reject_link("rej", t, col, principal="a bare name", vocab=None) \
            if False else reject_link("rej", t, col, principal="a bare name")
    before = load_mapping("rej").tables[t]
    m = reject_link("rej", t, col, principal=_principal("analyst"), reason=" not the same row ")
    [lk] = [x for x in m.tables[t].links if x["column"] == col]
    assert (lk["status"], lk["rejected_by"], lk["reject_reason"]) == (
        "rejected", "analyst", "not the same row")
    assert lk["decided_dev_only"] is False and lk["decided_authenticated_by"]
    # nothing else changed: the table's node, edges, status and approval are as they were
    after = m.tables[t]
    assert (after.node, after.edges, after.status, after.approval) == (
        before.node, before.edges, before.status, before.approval)
    with pytest.raises(MappingApprovalRefused):
        reject_link("rej", t, col, principal=_principal("analyst"))     # only a proposal
    # the next sync's proposals keep the decision
    m = refresh_link_proposals("rej", {t: mine}, vocab, others=others)
    assert [x["status"] for x in m.tables[t].links if x["column"] == col] == ["rejected"]
    # an approval records how its decider signed in too
    m = approve_link("app", t, col, principal=_principal("analyst"), vocab=vocab)
    [lk] = [x for x in m.tables[t].links if x["column"] == col]
    assert lk["status"] == "approved" and lk["decided_dev_only"] is False
    assert lk["decided_authenticated_by"] == lk.get("decided_authenticated_by") and \
        lk["decided_authenticated_by"]

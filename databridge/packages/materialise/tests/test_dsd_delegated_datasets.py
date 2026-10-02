"""DSD — datasets built through delegation to the per-source adapter agents, on BOTH estates.

The founder's flow, end to end, over REAL fixture data (`dsd_estates.py`: the workspace and the
finance estates, each with a real facts store, a mapping approved by an authenticated person, two
adapter agents and captured documents in a graph):

1. describe the dataset in English → a TABLE DESIGN is proposed (a scripted model behind the real
   harness, and the offline rules), validated against the measured catalogue;
2. a person edits and approves it (authenticated principals only; the digest binds);
3. the build delegates each part to the adapter agent holding it: database parts as pushed-down
   SQL (`WHERE … IN (…)`, grouped aggregates), documents through the graph;
4. the output is a RAW table: every grounded cell's pointer re-reads LIVE; time is columns, never
   a filter; persons are never merged; the grain is one row per base row.

Every expectation is computed from the estates' own rows and documents, never written down.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

import dsd_estates as de

CASES = {
    "workspace": {
        "request": "invoice data to predict which invoices stay open, with the meeting notes, "
                   "to train XGBoost",
        "grain": "invoices",
        "persons_request": "customer data with their account owner",
        "persons_grain": "customers",
        "no_label_request": "invoice data to predict fraud, to train XGBoost",
    },
    "finance": {
        "request": "facility data for watchlist prediction, with the credit memos, to train "
                   "XGBoost",
        "grain": "facility",
        "persons_request": "account data with the party holding it",
        "persons_grain": "account",
        "no_label_request": "facility data to predict fraud, to train XGBoost",
    },
}


@pytest.fixture(scope="module", params=sorted(CASES))
def estate(request, tmp_path_factory):
    from databridge_graph.packs import deployment

    mp = pytest.MonkeyPatch()
    name = request.param
    with deployment(de.ESTATES[name]["pack"]):
        e = de.build_estate(name, tmp_path_factory.mktemp(f"dsd_{name}"), mp)
        e.case = CASES[name]
        yield e
        e.store.close()
    mp.undo()


@pytest.fixture(autouse=True)
def _env(estate, monkeypatch):
    """Each test sees its estate's locations (the module fixture set them once)."""
    for env, sub in {"WALNUT_FACTS_DIR": "facts", "WALNUT_ADAPTER_AGENTS_DIR": "adapter_agents",
                     "WALNUT_RUNS_DIR": "runs", "WALNUT_CAPTURE_DIR": "capture",
                     "WALNUT_PASSAGES_DIR": "passages", "WALNUT_SOURCES_DIR": "sources",
                     "WALNUT_MATERIALISE_DIR": "materialise"}.items():
        monkeypatch.setenv(env, str(estate.root / sub))
    from databridge_graph.packs import deployment
    with deployment(estate.cfg["pack"]):
        yield


# ------------------------------------------------------------------------------------------------
# helpers — expectations computed from the estate
# ------------------------------------------------------------------------------------------------
def _cat_table(cat: dict, name: str) -> dict:
    return next(t for t in cat["tables"] if t["table"] == name)


def _rarest(rows: list[dict], col: str):
    counts = Counter(r[col] for r in rows if r[col] is not None)
    return sorted(counts.items(), key=lambda kv: (kv[1], str(kv[0])))[0][0]


def _model_answer(estate, grain: str):
    """What the scripted model returns: a draft built from the catalogue it was SHOWN — the
    grain, its label (the categorical column the request's target value lives in), every time
    column, one numeric column, one column per approved link — plus a NON-time filter, a TIME
    filter (which must not be applied) and a link that is not approved (which must be asked)."""
    rows = estate.rows[grain]

    def answer(cat: dict) -> dict:
        t = _cat_table(cat, grain)
        label = next(c for c, vs in t["categorical_values"].items()
                     if any(w in " ".join(vs).lower() for w in ("open", "watchlist")))
        tcol = t["time_columns"][0]
        keep = [r for r in rows if r[label] != _rarest(rows, label)]
        dates = sorted(str(r[tcol]) for r in keep if r[tcol] is not None)
        cols = [{"name": t["row_key"], "field": t["row_key"], "role": "key"},
                {"name": label, "field": label, "role": "label"},
                *[{"name": c, "field": c, "role": "feature"} for c in t["time_columns"]],
                {"name": t["numeric_columns"][0], "field": t["numeric_columns"][0]}]
        links = []
        for lk in t["approved_links_from_here"]:
            links.append({"id": lk["id"], "kind": lk["kind"], "from_field": lk["from_field"],
                          "target": lk["target"], "to_field": lk["to_field"],
                          "entity_kind": lk["entity_kind"]})
            if lk["kind"] == "lookup":
                far = _cat_table(cat, lk["target"]["entity"])
                f = next(c for c in far["columns"] if c != lk["to_field"])
                cols.append({"name": f"{lk['id']}_{f}", "via": lk["id"], "field": f})
            elif lk["kind"] == "aggregate":
                cols.append({"name": f"{lk['id']}_n", "via": lk["id"], "field": "*",
                             "aggregate": "count"})
            else:
                cols.append({"name": f"{lk['id']}_n", "via": lk["id"], "field": "document",
                             "aggregate": "count"})
        links.append({"id": "bogus", "kind": "lookup", "from_field": t["row_key"],
                      "target": {"adapter_agent": t["adapter_agent"],
                                 "connection": t["connection"], "entity": grain},
                      "to_field": t["row_key"]})
        cols.append({"name": "bogus_col", "via": "bogus", "field": t["row_key"]})
        return {"grain": {"adapter_agent": t["adapter_agent"], "connection": t["connection"],
                          "entity": grain},
                "grain_text": f"one row per {grain}",
                "columns": cols, "links": links,
                "filters": [{"field": label, "op": "ne", "value": _rarest(rows, label)},
                            # would keep only the LATEST of the rows the other filter keeps
                            {"field": tcol, "op": "ge", "value": dates[-1]}],
                "label": {"column": label, "definition": f"{label} as recorded"}}
    return answer


def _proposed(estate, *, grain=None, request=None, model=True):
    grain = grain or estate.case["grain"]
    harness, calls = de.scripted_designer(_model_answer(estate, grain)) if model else (None, [])
    m = estate.materialiser(harness=harness)
    svc = estate.service()
    doc = m.propose_design(request or estate.case["request"], by="staff", service=svc)
    return m, svc, doc, calls


def _approved_build(estate, *, grain=None, request=None, model=True):
    """propose (model) → edit (drop the unapproved link) → approve → build."""
    from databridge_federated import CatalogueAdmission
    m, svc, doc, _calls = _proposed(estate, grain=grain, request=request, model=model)
    d = doc["design"]
    if d["questions"]:
        doc = m.edit_design(doc["id"], {"columns": [c for c in d["columns"]
                                                    if c["via"] != "bogus"],
                                        "answers": {q["id"]: "removed the unapproved link"
                                                    for q in doc["question_items"]}},
                            by="staff", service=svc, rev=doc["rev"])
    doc = m.approve_design(doc["id"], digest=doc["digest"], principal=de.issued_principal(),
                           by="staff")
    estate.facts.calls.clear()
    res = m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc,
                         admission=CatalogueAdmission("staff"))
    return m, svc, doc, res


def _csv(m, res) -> tuple[list[str], list[dict]]:
    name = next(f for f, r in res["file_roles"].items() if r == "dataset" and f.endswith(".csv"))
    with (m.out_root / res["plan_hash"] / name).open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    header = list(rows[0].keys()) if rows else []
    return header, rows


def _receipt(m, res) -> dict:
    return m.receipt(res["plan_hash"])


# ================================================================================================
# 1. design proposal → edit → approve
# ================================================================================================
def test_the_design_is_proposed_edited_and_approved(estate):
    from databridge_materialise.delegated import DesignRefused, RECORD_TIME
    m, svc, doc, calls = _proposed(estate)
    assert calls, "the design model was asked through the harness"
    d = doc["design"]
    grain = estate.case["grain"]
    assert d["grain"]["entity"] == grain and d["grain"]["adapter_agent"] == estate.cfg["db_agent"]
    assert d["grain_key"] == estate.keys[grain]
    # the unapproved link is ASKED about, not used
    assert any("bogus" in q for q in d["questions"])
    assert "bogus" not in {lk["id"] for lk in d["links"]}
    # every link used is an APPROVED one, with the approver named from the mapping
    approver = estate.mapping.tables[next(t for t in estate.mapping.tables
                                          if t.endswith(f".{grain}"))].approval["principal_id"]
    db_links = [lk for lk in d["links"] if lk["kind"] != "documents"]
    assert db_links and all(lk["approved_by"] == approver for lk in d["links"])
    # time is METADATA: every temporal column is a time column; the time FILTER was not applied
    temporal = {c for c in de_cat(estate, svc).table_info(grain).temporal}
    roles = {c["name"]: c["role"] for c in d["columns"] if c["via"] is None}
    assert temporal and all(roles[c] == "time" for c in temporal if c in roles)
    assert all(f["field"] not in temporal for f in d["filters"]) and len(d["filters"]) == 1
    assert any("NOT applied" in a for a in d["assumptions"])
    assert roles[RECORD_TIME] == "time"
    # each column says WHEN its value became known
    for c in d["columns"]:
        assert c["when_known"], c["name"]
        if c["via"] is None and c["field"] in temporal:
            assert c["when_known"].startswith("event time")
        elif c["via"] is None and c["field"] not in (RECORD_TIME,):
            assert c["when_known"].startswith("unknown")
    # a design with questions cannot be approved
    with pytest.raises(DesignRefused) as e:
        m.approve_design(doc["id"], digest=doc["digest"], principal=de.issued_principal(),
                         by="staff")
    assert e.value.code == "DESIGN_HAS_QUESTIONS"
    # EDIT: drop the bogus column, rename one, remove one, add one from the grain's columns
    cols = [c for c in d["columns"] if c["via"] != "bogus"]
    renamed = next(c for c in cols if c["role"] == "feature" and c["via"] is None)
    removed = next(c for c in cols if c["via"] is not None)
    base_cols = de_cat(estate, svc).table_info(grain).columns
    added = next(c for c in base_cols if c not in {x["field"] for x in cols if x["via"] is None})
    new_cols = [({**c, "name": f"{c['name']}_renamed"} if c is renamed else c)
                for c in cols if c is not removed] + [{"name": added, "field": added}]
    edited = m.edit_design(doc["id"], {"columns": new_cols, "answers": {
        q["id"]: "the unapproved link is removed" for q in doc["question_items"]}},
        by="staff", service=svc, rev=doc["rev"])
    e2 = edited["design"]
    names = [c["name"] for c in e2["columns"]]
    assert not e2["questions"], e2["questions"]
    assert f"{renamed['name']}_renamed" in names and renamed["name"] not in names
    assert removed["name"] not in names and added in names
    assert edited["digest"] != doc["digest"] and edited["status"] == "edited"
    assert edited["approval"] is None
    # approve: an unauthenticated principal is refused; a stale digest is refused; then approved
    with pytest.raises(DesignRefused) as e:
        m.approve_design(doc["id"], digest=edited["digest"], principal="staff", by="staff")
    assert e.value.code == "APPROVER_NOT_AUTHENTICATED"
    with pytest.raises(DesignRefused) as e:
        m.approve_design(doc["id"], digest=doc["digest"], principal=de.issued_principal(),
                         by="staff")
    assert e.value.code == "DIGEST_MISMATCH"
    who = de.issued_principal("Rui Approver (dsd tests)")
    ok = m.approve_design(doc["id"], digest=edited["digest"], principal=who, by="staff")
    assert ok["status"] == "approved" and ok["approval"]["digest"] == edited["digest"]
    assert ok["approval"]["principal_id"] == "Rui Approver (dsd tests)"
    assert ok["approval"]["authenticated_by"]
    assert [h["action"] for h in ok["history"]] == ["proposed", "edited", "approved"]
    # the label is a REAL column of the grain
    assert e2["label"]["column"] in base_cols


class _CatView:
    def __init__(self, cat, grain):
        self.cat, self.grain = cat, grain

    def table_info(self, grain):
        return next(t for t in self.cat.tables.values() if t.table == grain)


def de_cat(estate, svc):
    from databridge_materialise.delegated import build_design_catalogue
    return _CatView(build_design_catalogue(svc, vocab=estate.vocab,
                                           facts_dir=estate.root / "facts"), None)


def test_the_offline_rules_propose_a_design_and_never_invent_a_label(estate):
    """No model: the rules choose the grain from the request's words and the label from a
    categorical value the request names; a request whose target no source records gets NO label
    (said plainly), never an invented one."""
    _m, svc, doc, _ = _proposed(estate, model=False)
    d = doc["design"]
    grain = estate.case["grain"]
    assert d["grain"]["entity"] == grain and not d["questions"]
    lab = d["label"]["column"]
    want = {w for w in estate.case["request"].lower().replace(",", " ").split()}
    assert any(str(v).lower() in want for v in {r[lab] for r in estate.rows[grain]})
    _m, _svc, doc2, _ = _proposed(estate, model=False, request=estate.case["no_label_request"])
    d2 = doc2["design"]
    assert d2["label"] is None
    assert any(a.startswith("NO LABEL") for a in d2["assumptions"])
    every = {str(v).lower() for r in estate.rows[grain] for v in r.values()} | \
        {c.lower() for c in estate.rows[grain][0]}
    assert "fraud" not in every                      # the premise: no source records it


# ================================================================================================
# 2. the build delegates to each source, with SQL pushdown
# ================================================================================================
def test_the_build_delegates_each_part_with_pushdown(estate):
    m, svc, doc, res = _approved_build(estate)
    d = doc["design"]
    grain = estate.case["grain"]
    calls = list(estate.facts.calls)
    rec = _receipt(m, res)["receipt"]
    agents = {s["adapter_agent"] for s in rec["sources"]}
    assert {estate.cfg["db_agent"], estate.cfg["docs_agent"]} <= agents
    base_sql = [s for s, _p in calls if f'FROM "{grain}"' in s and "_fed_key_dupes" in s]
    assert len(base_sql) == 1
    flt = d["filters"][0]
    assert f'"{flt["field"]}" <> ?' in base_sql[0]
    temporal = de_cat(estate, svc).table_info(grain).temporal
    where = base_sql[0].split(" WHERE ", 1)[1]
    assert not any(f'"{t}"' in where for t in temporal), "time is never a filter"
    base_keys = {r[estate.keys[grain]] for r in estate.rows[grain] if r[flt["field"]] !=
                 flt["value"]}
    linked = [lk for lk in d["links"] if lk["kind"] != "documents"]
    assert linked
    base_rows = [r for r in estate.rows[grain] if r[estate.keys[grain]] in base_keys]
    for lk in linked:
        t = lk["target"]["entity"]
        # the statement's OWN read of t is after its last `FROM "t"` (the key proof before it is a
        # scalar count in the select list; it returns one number, never rows)
        stmts = [(s, p) for s, p in calls if f'FROM "{t}"' in s]
        assert stmts, f"{t}: the delegate was not asked"
        wanted = {str(r[lk["from_field"]]) for r in base_rows if r[lk["from_field"]] is not None}
        for s, p in stmts:
            main = s.rsplit(f'FROM "{t}"', 1)[1].lstrip()
            # never a full scan: every read of the linked table is filtered by IN (base values)
            assert main.startswith("WHERE") and f'"{lk["to_field"]}" IN (' in main, s
            vals = list(p)[:-1] if "LIMIT ?" in main else list(p)
            assert vals and set(map(str, vals)) <= wanted
            if lk["kind"] == "aggregate":
                assert "GROUP BY" in main
        assert set().union(*[set(map(str, (list(p)[:-1] if "LIMIT ?" in s else list(p))))
                             for s, p in stmts]) == wanted
    assert any(lk["kind"] == "documents" for lk in d["links"])
    docs_src = [s for s in rec["sources"] if s["adapter_agent"] == estate.cfg["docs_agent"]]
    assert docs_src and all(s["method"] == "graph_passages" and not s["error"] for s in docs_src)
    # the grain: one row per base row matching the NON-time filter, nothing dropped or fanned out
    header, rows = _csv(m, res)
    key = d["grain_key"]
    assert len(rows) == len(base_keys) == len({r[key] for r in rows})
    assert {r[key] for r in rows} == {str(k) for k in base_keys}


# ================================================================================================
# 3. every cell's pointer resolves (live), and the values are the sources'
# ================================================================================================
def test_every_cell_pointer_resolves_live_and_says_the_source_value(estate):
    from databridge_core import SourcePointer
    from databridge_materialise.platform import _raw_text
    m, svc, doc, res = _approved_build(estate)
    pc = res["pointer_check"]
    assert pc["all_resolved"] and pc["all_match"] and pc["grounded_cells"] > 0
    header, rows = _csv(m, res)
    d = doc["design"]
    side = (m.out_root / res["plan_hash"] / _receipt(m, res)["receipt"]["pointer_manifest"]["uri"])
    recs = [json.loads(x) for x in side.read_text(encoding="utf-8").splitlines()]
    assert len(recs) == len(rows) * len(header)
    reader = m.reader(principal="staff")
    grounded = [r for r in recs if r["state"] == "grounded"]
    assert len(grounded) == pc["grounded_cells"]
    kinds = Counter()
    for r in grounded:
        p = SourcePointer.model_validate(r["pointer"])
        kinds[p.kind] += 1
        got = reader.resolve(p)
        assert got is not None, r
        want = rows[r["row"]][r["column"]]
        if p.kind == "doc_span":
            assert want.casefold() in str(got).casefold()
        else:
            assert _raw_text(got) == want, (r["column"], got, want)
    assert kinds["query_cell"] and kinds["derived"]
    # the values are the SOURCE's: lookups and counts recomputed from the estate's rows
    grain = estate.case["grain"]
    key = d["grain_key"]
    by_key = {str(r[key]): r for r in estate.rows[grain]}
    for lk in d["links"]:
        cols = [c for c in d["columns"] if c["via"] == lk["id"]]
        for c in cols:
            for row in rows:
                src = by_key[row[key]]
                if lk["kind"] == "aggregate" and c["aggregate"] == "count":
                    t = lk["target"]["entity"]
                    n = sum(1 for x in estate.rows[t]
                            if str(x[lk["to_field"]]) == str(src[lk["from_field"]]))
                    assert row[c["name"]] == str(n)
                elif lk["kind"] == "documents" and c["aggregate"] == "count":
                    n = sum(1 for dd in estate.docs if str(src[lk["from_field"]]) in
                            dd["entity_ids"] and dd["node_id"].split(":")[0] in lk["doc_kinds"])
                    assert row[c["name"]] == ("" if n == 0 else str(n))
    # a changed source no longer vouches: the facts file moves, the pointers stop resolving
    facts = estate.root / "facts" / f"{estate.db}_facts.sqlite"
    before = facts.read_bytes()
    try:
        import sqlite3
        con = sqlite3.connect(facts)
        con.execute(f'UPDATE "{grain}" SET "{key}" = "{key}" || \'\'')
        con.execute("CREATE TABLE _tamper (x)")
        con.commit()
        con.close()
        fresh = m.reader(principal="staff")
        p = SourcePointer.model_validate(next(r for r in grounded if r["pointer"]["kind"] ==
                                              "query_cell")["pointer"])
        assert fresh.resolve(p) is None
        agg = next((r for r in grounded if r["pointer"]["kind"] == "derived"
                    and '"aggregate"' in (r["pointer"].get("predicate") or "")), None)
        if agg is not None:
            assert fresh.resolve(SourcePointer.model_validate(agg["pointer"])) is None
    finally:
        facts.write_bytes(before)
    assert hashlib.sha256(facts.read_bytes()).hexdigest() == hashlib.sha256(before).hexdigest()


# ================================================================================================
# 4. time is present as columns and not filtered
# ================================================================================================
def test_time_is_columns_never_a_filter(estate):
    from databridge_materialise.delegated import RAW_STATEMENT, RECORD_TIME
    m, svc, doc, res = _approved_build(estate)
    d = doc["design"]
    grain = estate.case["grain"]
    header, rows = _csv(m, res)
    temporal = [c for c in de_cat(estate, svc).table_info(grain).temporal if c in header]
    assert temporal and RECORD_TIME in header
    assert {r[RECORD_TIME] for r in rows} == {de.EXTRACTED_AT}
    # the model asked to keep only rows on/after the latest date: NOT applied, so every row the
    # NON-time filter keeps is in the table, with its own date (computed from the estate)
    tf = next(f for f in d["filters"])
    tcol = next(c for c in temporal)
    keep = [r for r in estate.rows[grain] if r[tf["field"]] != tf["value"]]
    cut = max(str(r[tcol]) for r in keep if r[tcol] is not None)
    got = sorted(r[tcol] for r in rows if r[tcol])
    assert got == sorted(str(r[tcol]) for r in keep if r[tcol] is not None)
    assert any(v < cut for v in got), "a time filter would have dropped these rows"
    rec = _receipt(m, res)["receipt"]
    assert rec["statement"] == RAW_STATEMENT == "raw extract; time as metadata; not leakage-filtered"
    assert set(rec["time_columns"]) >= {RECORD_TIME, *temporal}
    assert all(c["when_known"] for c in rec["columns"])
    assert not any(f["field"] in temporal for f in rec["filters_applied"])
    # a raw extract is served (no leakage gate in this mode) — and says so
    csv_name = next(f for f, r in res["file_roles"].items() if r == "dataset")
    assert m.file_path(res["plan_hash"], csv_name) is not None
    assert res["mode"] == "raw_delegated"


# ================================================================================================
# 5. an unapproved design is refused
# ================================================================================================
def test_an_unapproved_design_is_refused(estate):
    from databridge_federated import CatalogueAdmission
    from databridge_materialise.delegated import DesignRefused
    m, svc, doc, _ = _proposed(estate, model=False)
    adm = CatalogueAdmission("staff")
    with pytest.raises(DesignRefused) as e:
        m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc, admission=adm)
    assert e.value.code == "DESIGN_NOT_APPROVED"
    ok = m.approve_design(doc["id"], digest=doc["digest"], principal=de.issued_principal(),
                          by="staff")
    # a different digest than the approved one is refused
    with pytest.raises(DesignRefused) as e:
        m.build_design(doc["id"], digest="0" * 64, by="staff", service=svc, admission=adm)
    assert e.value.code == "DIGEST_MISMATCH"
    # an edit after approval clears it: the old digest no longer builds
    cols = ok["design"]["columns"][:-2]
    m.edit_design(doc["id"], {"columns": cols}, by="staff", service=svc, rev=ok["rev"])
    with pytest.raises(DesignRefused) as e:
        m.build_design(doc["id"], digest=ok["digest"], by="staff", service=svc, admission=adm)
    assert e.value.code == "DESIGN_NOT_APPROVED"
    # a revision changed on disk after approval no longer hashes to its digest
    doc2 = m.design(doc["id"], by="staff")
    ok2 = m.approve_design(doc["id"], digest=doc2["digest"], principal=de.issued_principal(),
                           by="staff")
    p = m.out_root / "designs" / doc["id"] / f"rev-{ok2['rev']}.json"
    before = p.read_bytes()
    raw = json.loads(before)
    raw["design"]["request"] = raw["design"]["request"] + " (edited on disk)"
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DesignRefused) as e:
        m.build_design(doc["id"], digest=ok2["digest"], by="staff", service=svc, admission=adm)
    assert e.value.code == "DESIGN_INTEGRITY_FAILED"
    p.write_bytes(before)
    # another principal cannot build (or see) it
    with pytest.raises(DesignRefused) as e:
        m.build_design(doc["id"], digest=ok2["digest"], by="someone.else", service=svc,
                       admission=CatalogueAdmission("someone.else"))
    assert e.value.code == "DESIGN_NOT_FOUND"


# ================================================================================================
# 6. an unauthenticated caller is refused
# ================================================================================================
def test_an_unauthenticated_approver_is_refused(estate):
    from databridge_materialise.delegated import DesignRefused

    class Claims:                                   # carries a name; authenticates nothing
        principal_id = "staff"
        authenticated_by = "gateway"

    m, svc, doc, _ = _proposed(estate, model=False)
    for fake in ("staff", {"principal_id": "staff"}, None, Claims()):
        with pytest.raises(DesignRefused) as e:
            m.approve_design(doc["id"], digest=doc["digest"], principal=fake, by="staff")
        assert e.value.code == "APPROVER_NOT_AUTHENTICATED"
    assert m.design(doc["id"], by="staff")["status"] == "proposed"


# ================================================================================================
# 7. persons are not merged; the grain never fans out
# ================================================================================================
def test_persons_are_never_merged(estate):
    from databridge_materialise.delegated import CANDIDATE_REASON, IDENTIFIER_REASON
    grain = estate.case["persons_grain"]
    m, svc, doc, res = _approved_build(estate, grain=grain,
                                       request=estate.case["persons_request"], model=False)
    d = doc["design"]
    assert d["grain"]["entity"] == grain
    cand = [lk for lk in d["links"] if lk["candidate"]]
    assert cand, "the estate's grain links to a person-kind record"
    header, rows = _csv(m, res)
    key = d["grain_key"]
    assert len(rows) == len(estate.rows[grain]) == len({r[key] for r in rows})
    side = (m.out_root / res["plan_hash"] / _receipt(m, res)["receipt"]["pointer_manifest"]["uri"])
    recs = [json.loads(x) for x in side.read_text(encoding="utf-8").splitlines()]
    by_cell = {(r["row"], r["column"]): r for r in recs}
    from databridge_graph.packs import active_vocabulary
    from databridge_mcp_ingest.mapping import split_value
    v = active_vocabulary()
    for lk in cand:
        if lk["kind"] != "lookup":
            for c in [c for c in d["columns"] if c["via"] == lk["id"]]:
                assert c["aggregate"] == "count"     # people are counted, never aggregated
            continue
        t = lk["target"]["entity"]
        target = {str(r[lk["to_field"]]): r for r in estate.rows[t]}
        sp = lk.get("split")
        person_values = []
        reasons = Counter()
        for i, row in enumerate(rows):
            src = next(r for r in estate.rows[grain] if str(r[key]) == row[key])
            far = target.get(str(src[lk["from_field"]]))
            if far is None:
                continue
            kind = ((sp["kinds"].get(split_value(far[sp["column"]])) or sp["default"])
                    if sp else None)
            is_person = (not sp) or v.is_subject(kind)
            for c in [c for c in d["columns"] if c["via"] == lk["id"]]:
                rec = by_cell[(i, c["name"])]
                if is_person:
                    # withheld: as a candidate, or (an identifier column) masked first
                    assert rec["state"] == "withheld" and rec["reason_code"] in (
                        CANDIDATE_REASON, IDENTIFIER_REASON)
                    reasons[rec["reason_code"]] += 1
                    assert row[c["name"]] == ""
                    if far.get(c["field"]) not in (None, ""):
                        person_values.append(str(far[c["field"]]))
                else:
                    assert rec["state"] in ("grounded", "absent")
        assert person_values, "the estate has person rows behind this link"
        assert reasons[CANDIDATE_REASON] > 0
    rec = _receipt(m, res)["receipt"]
    assert "never merged" in rec["persons"]


def test_a_fanned_out_grain_is_refused(estate, monkeypatch):
    """The approved grain is one row per base row: a base read that repeats a key is refused, not
    shipped as duplicates."""
    from databridge_federated import CatalogueAdmission
    from databridge_materialise.delegated import DesignRefused
    m, svc, doc, _ = _proposed(estate, model=False)
    doc = m.approve_design(doc["id"], digest=doc["digest"], principal=de.issued_principal(),
                           by="staff")
    real = svc.run_subtask

    def doubled(task, **kw):
        r = real(task, **kw)
        if task.id == "base" and r.rows:
            return r.model_copy(update={"rows": (*r.rows, r.rows[0])})
        return r
    monkeypatch.setattr(svc, "run_subtask", doubled)
    with pytest.raises(DesignRefused) as e:
        m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc,
                       admission=CatalogueAdmission("staff"))
    # the independent COUNT proof catches it first (R-027 f1 #4) ...
    assert e.value.code == "BASE_UNPROVEN"
    # ... and the assembly's own grain check is the backstop behind it
    import databridge_materialise.delegated as dl
    monkeypatch.setattr(dl, "_prove_base", lambda *a, **k: None)
    with pytest.raises(DesignRefused) as e:
        m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc,
                       admission=CatalogueAdmission("staff"))
    assert e.value.code == "GRAIN_VIOLATED"


def test_an_ungranted_source_is_never_read(estate):
    """The caller's admission decides: with only the documents connection granted, the base is not
    read and nothing is built."""
    from databridge_federated import CatalogueAdmission
    from databridge_materialise.delegated import DesignRefused
    m, svc, doc, _ = _proposed(estate, model=False)
    doc = m.approve_design(doc["id"], digest=doc["digest"], principal=de.issued_principal(),
                           by="staff")
    estate.facts.calls.clear()
    with pytest.raises(DesignRefused) as e:
        m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc,
                       admission=CatalogueAdmission("staff", {estate.cfg["docs"]}))
    assert e.value.code == "BASE_NOT_READ" and "not admitted" in e.value.message
    assert not [s for s, _p in estate.facts.calls if "_fed_key_dupes" in s]

"""E2E run 5 — the DSD findings (F-10, W-10, W-11), each reproduced on BOTH estates.

F-10 (High) a raw dataset over the finance transactions (24,132 rows) could only be built for a
            368-row slice: every base over the facts store's per-statement ceiling (1,000 rows)
            was refused ``BASE_TOO_LARGE`` "more than 5000", and a person's time filter was
            dropped.
W-10 (Low)  a delegated build was refused by the reverse index (its instant was passed as text),
            the design named no model, and the ``built`` event carried no actor object.
W-11 (Low)  ``/materialise`` refused a plain-table request with the class-name code
            ``PlanNeedsInput`` and no route to the raw-design flow that builds it.

The estates are `dsd_estates.py`'s, each with a base LARGER than the real facts store's
per-statement ceiling (`count.MAX_ROWS`), read through the real crawl, facts store and approved
mapping: finance adds its own ``core_banking.txn`` (the run-5 table itself); workspace adds
synthetic invoices (computed below) to its own. Every expectation is derived from those rows.
"""
from __future__ import annotations

import csv

import pytest

import dsd_estates as de

#: workspace: synthetic invoices on the estate's own customers and projects, so the base is
#: larger than the store ceiling. Computed by SQL from the estate's rows — no value is listed.
_WS_EXTRA = """
WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 1250)
INSERT INTO public.invoices (invoice_no, customer_id, project_id, issued_on, due_on, net_gbp,
                             vat_gbp, total_gbp, status)
SELECT printf('INV-SYN-%05d', i), p.customer_id, p.project_id,
       date('2025-01-01', '+' || (i % 600) || ' days'),
       date('2025-01-31', '+' || (i % 600) || ' days'),
       100 + i, 20 + (i % 7), 120 + i + (i % 7),
       CASE i % 3 WHEN 0 THEN 'open' WHEN 1 THEN 'paid' ELSE 'void' END
FROM n JOIN (SELECT project_id, customer_id, row_number() OVER (ORDER BY project_id) - 1 AS k,
                    count(*) OVER () AS m FROM public.projects) p ON p.k = i % p.m;
"""

CASES = {
    "workspace": {"grain": "invoices", "build": {"extra_sql": _WS_EXTRA},
                  "request": "invoice data to predict fraud, to train XGBoost",
                  "plain_request": "Export all the employees with their salary as a CSV "
                                   "dataset."},
    "finance": {"grain": "txn", "build": {"tables": ["core_banking.txn"]},
                "request": "I need payments data for fraud, to train XGBoost.",
                "plain_request": "Export all the parties with their salary as a CSV dataset."},
}


@pytest.fixture(scope="module", params=sorted(CASES))
def big(request, tmp_path_factory):
    from databridge_graph.packs import deployment

    mp = pytest.MonkeyPatch()
    name = request.param
    with deployment(de.ESTATES[name]["pack"]):
        e = de.build_estate(name, tmp_path_factory.mktemp(f"e2e5_{name}"), mp,
                            **CASES[name]["build"])
        e.case = CASES[name]
        yield e
        e.store.close()
    mp.undo()


@pytest.fixture(autouse=True)
def _env(big, monkeypatch):
    for env, sub in {"WALNUT_FACTS_DIR": "facts", "WALNUT_ADAPTER_AGENTS_DIR": "adapter_agents",
                     "WALNUT_RUNS_DIR": "runs", "WALNUT_CAPTURE_DIR": "capture",
                     "WALNUT_PASSAGES_DIR": "passages", "WALNUT_SOURCES_DIR": "sources",
                     "WALNUT_MATERIALISE_DIR": "materialise"}.items():
        monkeypatch.setenv(env, str(big.root / sub))
    monkeypatch.delenv("WALNUT_DSD_MAX_BASE_ROWS", raising=False)
    monkeypatch.delenv("WALNUT_DSD_PAGE_ROWS", raising=False)
    from databridge_graph.packs import deployment
    with deployment(big.cfg["pack"]):
        yield


def _adm():
    from databridge_federated import CatalogueAdmission
    return CatalogueAdmission("staff")


def _table(cat: dict, name: str) -> dict:
    return next(t for t in cat["tables"] if t["table"] == name)


def _answer(big, *, filters=(), grain=None):
    """The scripted model's draft, from the catalogue it was SHOWN: the grain's key, one numeric,
    one time and one categorical column, and one column through each approved LOOKUP — no label
    (the request's target, fraud, is recorded nowhere)."""
    grain = grain or big.case["grain"]

    def answer(cat: dict) -> dict:
        t = _table(cat, grain)
        key = t["row_key"]
        picks = [*[c for c in t["numeric_columns"] if c != key][:1], *t["time_columns"][:1],
                 *sorted(c for c in t["categorical_values"] if c != key)[:1]]
        cols = [{"name": key, "field": key, "role": "key"},
                *[{"name": c, "field": c} for c in dict.fromkeys(picks)]]
        links = []
        for lk in t["approved_links_from_here"]:
            if lk["kind"] != "lookup":
                continue
            far = _table(cat, lk["target"]["entity"])
            masked = set(far["identifier_columns (masked)"])
            f = next((c for c in far["columns"] if c != lk["to_field"] and c not in masked),
                     None)
            if f is None:
                continue
            links.append({"id": lk["id"], "kind": "lookup", "from_field": lk["from_field"],
                          "target": lk["target"], "to_field": lk["to_field"]})
            cols.append({"name": f"{lk['id']}_{f}", "via": lk["id"], "field": f})
        return {"grain": {"adapter_agent": t["adapter_agent"], "connection": t["connection"],
                          "entity": grain},
                "grain_text": f"one row per {grain}", "columns": cols, "links": links,
                "filters": list(filters), "label": None}
    return answer


def _approved(big, *, edit=None, grain=None):
    harness, _calls = de.scripted_designer(_answer(big, grain=grain))
    m = big.materialiser(harness=harness)
    svc = big.service()
    doc = m.propose_design(big.case["request"], by="staff", service=svc)
    if edit is not None:
        doc = m.edit_design(doc["id"], edit(doc), by="staff", service=svc, rev=doc["rev"])
    assert not doc["design"]["questions"], doc["design"]["questions"]
    doc = m.approve_design(doc["id"], digest=doc["digest"], principal=de.issued_principal(),
                           by="staff")
    big.facts.calls.clear()
    return m, svc, doc


def _csv_rows(m, res) -> list[dict]:
    name = next(f for f, r in res["file_roles"].items() if r == "dataset" and f.endswith(".csv"))
    with (m.out_root / res["plan_hash"] / name).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _ceiling() -> int:
    from databridge_mcp_ingest.tabular.count import MAX_ROWS
    return MAX_ROWS


# ================================================================================================
# F-10: the whole table builds — paged, every safety property kept
# ================================================================================================
def test_f10_a_base_larger_than_the_store_ceiling_builds_whole(big):
    grain = big.case["grain"]
    source = big.rows[grain]
    key = big.keys[grain]
    assert len(source) > _ceiling(), "non-vacuous: the base is over the store's statement ceiling"
    m, svc, doc = _approved(big)
    res = m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc,
                         admission=_adm())
    rows = _csv_rows(m, res)
    # the grain: exactly one row per source row, every key once
    assert len(rows) == len(source)
    assert sorted(r[key] for r in rows) == sorted(str(r[key]) for r in source) or \
        Counter(r[key] for r in rows).most_common(1)[0][1] == 1
    rec = m.receipt(res["plan_hash"])["receipt"]
    assert rec["rows"]["emitted"] == rec["rows"]["base_rows_counted"] == len(source)
    assert rec["rows"]["base_pages"] > 1
    # every base page is a pushed-down, keyset-paged statement on the proven row key
    base_sql = [(s, p) for s, p in big.facts.calls
                if f'FROM "{grain}"' in s and "_fed_key_dupes" in s]
    assert len(base_sql) == rec["rows"]["base_pages"]
    assert all(f'ORDER BY "{key}" LIMIT ?' in s for s, _p in base_sql)
    assert all(f'"{key}" > ?' in s for s, _p in base_sql[1:])
    assert f'"{key}" > ?' not in base_sql[0][0]
    # a pointer per cell, every one re-read live at publication
    check = res["pointer_check"]
    assert check["all_resolved"] and check["all_match"]
    assert check["grounded_cells"] >= len(source) * 2
    # typed joins kept: every lookup column joined (or said why not), never fanned out
    d = doc["design"]
    for c in [c for c in d["columns"] if c["via"]]:
        col = next(x for x in rec["columns"] if x["name"] == c["name"])
        assert col["grounded"] + col["absent"] + col["withheld"] == len(source)
        assert sum(col["unmatched"].values()) <= col["absent"] + col["withheld"]
    # the pins the publication re-checked name the store the pages read
    assert set(rec["sources_pinned"]) == {big.db}


def test_f10_the_bound_is_honest_configurable_and_checked_before_reading(big, monkeypatch):
    from databridge_materialise.delegated import DesignRefused, MAX_BASE_ROWS_ENV
    grain = big.case["grain"]
    n = len(big.rows[grain])
    monkeypatch.setenv(MAX_BASE_ROWS_ENV, str(n - 1))
    m, svc, doc = _approved(big)
    with pytest.raises(DesignRefused) as e:
        m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc,
                       admission=_adm())
    assert e.value.code == "BASE_TOO_LARGE"
    msg = e.value.message
    assert str(n) in msg and str(n - 1) in msg and MAX_BASE_ROWS_ENV in msg
    assert "5000" not in msg
    # the refusal came from the source's COUNT: no row of the base was read
    assert not [s for s, _p in big.facts.calls if f'FROM "{grain}"' in s and "LIMIT ?" in s]
    # the bound is exactly the count: equal builds
    monkeypatch.setenv(MAX_BASE_ROWS_ENV, str(n))
    m2, svc2, doc2 = _approved(big)
    res = m2.build_design(doc2["id"], digest=doc2["digest"], by="staff", service=svc2,
                          admission=_adm())
    assert res["rows"]["emitted"] == n


def test_f10_a_page_that_moved_or_lost_a_row_is_refused(big, monkeypatch):
    """The paged read keeps the base proof: a page reading a row twice / losing one is caught by
    the independent COUNT (same number, same key set)."""
    from databridge_materialise.delegated import DesignRefused
    m, svc, doc = _approved(big)
    real = type(svc).run_subtask
    seen = {"pages": 0}

    def lossy(self, task, **kw):
        r = real(self, task, **kw)
        if task.id.startswith("base-p") and r.rows:
            seen["pages"] += 1
            return r.model_copy(update={"rows": r.rows[1:]})       # one row silently lost
        return r
    monkeypatch.setattr(type(svc), "run_subtask", lossy)
    with pytest.raises(DesignRefused) as e:
        m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc,
                       admission=_adm())
    assert seen["pages"] >= 1 and e.value.code == "BASE_UNPROVEN"


def test_f10_pages_read_from_two_snapshots_are_refused(big, monkeypatch):
    """Every page must come from the snapshot the first page read; a mix is never built."""
    from databridge_materialise.delegated import DesignRefused
    m, svc, doc = _approved(big)
    real = type(svc).run_subtask

    def moved(self, task, **kw):
        r = real(self, task, **kw)
        if task.id.startswith("base-p") and r.snapshot is not None:
            return r.model_copy(update={"snapshot": r.snapshot.model_copy(
                update={"content_hash": "0" * 64})})
        return r
    monkeypatch.setattr(type(svc), "run_subtask", moved)
    with pytest.raises(DesignRefused) as e:
        m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc,
                       admission=_adm())
    assert e.value.code == "SOURCE_MOVED"


def test_f10_a_lookup_read_cut_short_is_continued_never_read_as_orphans(big, monkeypatch):
    """A lookup chunk the store cuts short (as a per-statement ceiling does) is CONTINUED on the
    far table's key: the table equals the one built from uncut reads, cell for cell. The grain
    is the first non-person table (by name) with an approved, non-person lookup from it."""
    from databridge_materialise.delegated import build_design_catalogue
    cat = build_design_catalogue(big.service(), vocab=big.vocab, facts_dir=big.root / "facts")
    grain = sorted(t.table for t in cat.tables.values() if not t.subject and not t.split
                   and any(lk.kind == "lookup" and not lk.candidate
                          for lk in cat.links_from(t.ref)))[0]
    m, svc, doc = _approved(big, grain=grain)
    lookups = {lk["id"] for lk in doc["design"]["links"] if lk["kind"] == "lookup"}
    assert lookups, "non-vacuous: the design joins through a lookup"
    whole = _csv_rows(m, m.build_design(doc["id"], digest=doc["digest"], by="staff",
                                        service=svc, admission=_adm()))
    real = type(svc).run_subtask
    cut = {"n": 0}

    def ceiling(self, task, **kw):
        r = real(self, task, **kw)
        if task.id.split("-")[0] in lookups and "-p" not in task.id and len(r.rows) > 1:
            cut["n"] += 1
            return r.model_copy(update={"rows": r.rows[:1], "coverage": r.coverage.model_copy(
                update={"truncated": True})})
        return r
    monkeypatch.setattr(type(svc), "run_subtask", ceiling)
    m2, svc2, doc2 = _approved(big, grain=grain)
    res = m2.build_design(doc2["id"], digest=doc2["digest"], by="staff", service=svc2,
                          admission=_adm())
    assert cut["n"] >= 1
    assert _csv_rows(m2, res) == whole
    rec = m2.receipt(res["plan_hash"])["receipt"]
    joined = 0
    for c in [c for c in doc2["design"]["columns"] if c["via"] in lookups]:
        col = next(x for x in rec["columns"] if x["name"] == c["name"])
        assert not any("orphan" in why or "cut short" in why for why in col["unmatched"]),             col["unmatched"]
        joined += col["grounded"]
    assert joined > 0, "non-vacuous: lookup values were joined (not all withheld)"


def test_f10_a_persons_time_filter_is_applied_as_a_row_scope_never_dropped(big):
    grain = big.case["grain"]
    rows = big.rows[grain]
    svc = big.service()
    from databridge_materialise.delegated import build_design_catalogue
    cat = build_design_catalogue(svc, vocab=big.vocab, facts_dir=big.root / "facts")
    tcol = next(t for t in cat.tables.values() if t.table == grain).temporal[0]
    dates = sorted({str(r[tcol]) for r in rows if r[tcol] is not None})
    cut = dates[len(dates) // 2]                          # a cut computed from the rows
    keep = [r for r in rows if r[tcol] is not None and str(r[tcol]) >= cut]
    assert 0 < len(keep) < len(rows), "non-vacuous: the scope keeps some rows and drops some"
    m, svc, doc = _approved(big, edit=lambda d: {"filters": [{"field": tcol, "op": "ge",
                                                              "value": cut}]})
    d = doc["design"]
    assert {(f["field"], f["op"], f["value"]) for f in d["filters"]} == {(tcol, "ge", cut)}
    assert any("APPLIED as a row scope" in a for a in d["assumptions"])
    assert not any(f"{tcol} ge {cut!r} was NOT applied" in a for a in d["assumptions"])
    res = m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc,
                         admission=_adm())
    out = _csv_rows(m, res)
    assert len(out) == len(keep)
    assert all(r[tcol] >= cut for r in out)
    base_sql = [s for s, _p in big.facts.calls
                if f'FROM "{grain}"' in s and "_fed_key_dupes" in s]
    assert base_sql and all(f'"{tcol}" >= ?' in s for s in base_sql), "pushed down"
    rec = m.receipt(res["plan_hash"])["receipt"]
    assert {"field": tcol, "op": "ge", "value": cut} in rec["filters_applied"]


def test_f10_a_model_drafted_time_filter_is_said_and_never_silently_dropped(big):
    grain = big.case["grain"]
    svc = big.service()
    from databridge_materialise.delegated import build_design_catalogue
    cat = build_design_catalogue(svc, vocab=big.vocab, facts_dir=big.root / "facts")
    tcol = next(t for t in cat.tables.values() if t.table == grain).temporal[0]
    harness, _ = de.scripted_designer(_answer(big, filters=[{"field": tcol, "op": "ge",
                                                             "value": "2026-01-01"}]))
    m = big.materialiser(harness=harness)
    doc = m.propose_design(big.case["request"], by="staff", service=svc)
    notes = [a for a in doc["design"]["assumptions"] if f"{tcol} ge" in a]
    assert notes and "NOT applied" in notes[0] and "add it as a filter in an edit" in notes[0]


# ================================================================================================
# W-10: registered in the reverse index; the model and the actor are recorded
# ================================================================================================
def test_w10_the_build_is_registered_and_names_its_model_and_actor(big):
    from databridge_core.decision_ledger import actor_of
    m, svc, doc = _approved(big)
    assert doc["produced_by"]["model"] == de.HARNESS_CONFIG["aliases"]["designer"]["model"]
    assert doc["produced_by"]["task_class"] == "agentic_orchestration"
    who = de.issued_principal()
    actor = actor_of({"principal_id": "staff", "authenticated_by": who.authenticated_by,
                      "principal_type": "AuthenticatedPrincipal", "dev_only": False})
    res = m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc,
                         admission=_adm(), actor=actor)
    assert res["index_registered"]["registered"] is True, res["index_registered"]
    conn = big.store.conn
    row = conn.execute("SELECT emission_id FROM emission WHERE kind = ? AND ref = ?",
                       ("materialised_dataset", res["plan_hash"])).fetchone()
    assert row is not None
    n = conn.execute("SELECT COUNT(*) FROM emission_pointer WHERE emission_id = ?",
                     (row[0],)).fetchone()[0]
    assert n > 0
    built = [h for h in m.design(doc["id"], by="staff")["history"] if h["action"] == "built"]
    assert built
    from databridge_materialise.delegated import DesignStore
    events = DesignStore(m.out_root)._chain(doc["id"])[0]
    ev = [e for e in events if e.get("action") == "built"][-1]
    assert ev["actor"] == actor and ev["by"] == "staff"
    # a different person's actor is refused before anything is read
    from databridge_materialise.delegated import DesignRefused
    other = {**actor, "principal_id": "someone-else"}
    big.facts.calls.clear()
    with pytest.raises(DesignRefused):
        m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc,
                       admission=_adm(), actor=other)
    assert not big.facts.calls


# ================================================================================================
# W-11: /materialise routes a plain-table request to the raw-design flow, with a standard code
# ================================================================================================
def test_w11_a_plain_table_request_is_routed_to_the_raw_design_flow(big):
    from databridge_materialise.platform import RunRefused, RAW_DESIGN_ALTERNATIVE
    m = big.materialiser(harness=None)
    with pytest.raises(RunRefused) as e:
        m.run({"request": big.case["plain_request"]}, by="staff")
    r = e.value.refusal()
    assert r["code"] in ("PLAN_NEEDS_INPUT", "SHAPE_NOT_IMPLEMENTED")
    assert r["code"] == r["code"].upper() and r["reason_code"] in ("PlanNeedsInput",
                                                                     "ShapeNotImplemented")
    assert r["nearest_alternative"] == RAW_DESIGN_ALTERNATIVE
    assert "/materialise/designs" in r["nearest_alternative"]
    assert "Raw dataset by delegation" in r["message"]


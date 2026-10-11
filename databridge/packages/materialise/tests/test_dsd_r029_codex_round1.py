"""R-029 DSX — codex round 1 findings, each reproduced on BOTH estates before its fix.

1 (High) the COUNT and the paged base reads were not bound to one snapshot; a page with no
         snapshot passed, and the publication pins kept the last snapshot seen per connection.
2 (High) an INCOMPLETE linked read (rows skipped or excluded, not truncated) was read as complete:
         a missing lookup row became an "orphan reference", a missing group a grounded zero.
3 (High) aggregate chunks could come from different snapshots, and every group's pointer was
         labelled with the LAST chunk's snapshot; the resolver never checked the leaves.
4 (Low)  the build's actor object was optional outside the gateway route.

Same estates as `test_dsd_e2e5_fixes.py` (bases over the store ceiling, real crawl, facts store and
approved mapping). Pages and IN-chunks are made small (`WALNUT_DSD_PAGE_ROWS`) so every read here is
several statements. Expectations are derived from the estates' rows.
"""
from __future__ import annotations

import pytest

import dsd_estates as de
from test_dsd_e2e5_fixes import _adm, _csv_rows, _env, _table, big  # noqa: F401

PAGE = 3


@pytest.fixture(autouse=True)
def _small_pages(monkeypatch):
    monkeypatch.setenv("WALNUT_DSD_PAGE_ROWS", str(PAGE))


def _grain_with(big, kind: str) -> str:
    """The first non-person table (by name) with an approved, non-person link of ``kind``."""
    from databridge_materialise.delegated import build_design_catalogue
    cat = build_design_catalogue(big.service(), vocab=big.vocab, facts_dir=big.root / "facts")
    return sorted(t.table for t in cat.tables.values() if not t.subject and not t.split
                  and any(lk.kind == kind and not lk.candidate
                          for lk in cat.links_from(t.ref)))[0]


def _answer(grain: str):
    """A draft from the catalogue SHOWN: the key, one column per approved non-person lookup and
    a count per approved aggregate. No label."""
    def answer(cat: dict) -> dict:
        t = _table(cat, grain)
        cols = [{"name": t["row_key"], "field": t["row_key"], "role": "key"}]
        links = []
        for lk in t["approved_links_from_here"]:
            if lk["kind"] == "lookup" and not lk["candidate_only (person)"]:
                far = _table(cat, lk["target"]["entity"])
                masked = set(far["identifier_columns (masked)"])
                f = next((c for c in far["columns"] if c != lk["to_field"] and c not in masked),
                         None)
                if f is None:
                    continue
                cols.append({"name": f"{lk['id']}_{f}", "via": lk["id"], "field": f})
            elif lk["kind"] == "aggregate":
                cols.append({"name": f"{lk['id']}_n", "via": lk["id"], "field": "*",
                             "aggregate": "count"})
            else:
                continue
            links.append({"id": lk["id"], "kind": lk["kind"], "from_field": lk["from_field"],
                          "target": lk["target"], "to_field": lk["to_field"]})
        return {"grain": {"adapter_agent": t["adapter_agent"], "connection": t["connection"],
                          "entity": grain},
                "grain_text": f"one row per {grain}", "columns": cols, "links": links,
                "filters": [], "label": None}
    return answer


def _approved(big, grain: str):
    harness, _ = de.scripted_designer(_answer(grain))
    m = big.materialiser(harness=harness)
    svc = big.service()
    doc = m.propose_design("a plain table, to train XGBoost", by="staff", service=svc)
    assert not doc["design"]["questions"], doc["design"]["questions"]
    doc = m.approve_design(doc["id"], digest=doc["digest"], principal=de.issued_principal(),
                           by="staff")
    big.facts.calls.clear()
    return m, svc, doc


def _build(m, svc, doc):
    return m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc,
                          admission=_adm(), actor=de.build_actor())


def _wrap(monkeypatch, svc, change):
    real = type(svc).run_subtask

    def run(self, task, **kw):
        return change(task, real(self, task, **kw))
    monkeypatch.setattr(type(svc), "run_subtask", run)


def _moved(r):
    return r.model_copy(update={"snapshot": r.snapshot.model_copy(
        update={"content_hash": "0" * 64})})


# ------------------------------------------------------------------------------------------------
# 1. one snapshot: the count, every page, every read on the same connection
# ------------------------------------------------------------------------------------------------
def test_r1_count_and_pages_must_share_one_snapshot(big, monkeypatch):
    from databridge_materialise.delegated import DesignRefused
    grain = big.case["grain"]
    m, svc, doc = _approved(big, grain)
    # (a) the COUNT read another snapshot than the pages
    _wrap(monkeypatch, svc, lambda t, r: _moved(r) if t.id == "base-count" else r)
    with pytest.raises(DesignRefused) as e:
        _build(m, svc, doc)
    assert e.value.code == "SOURCE_MOVED"


def test_r1_a_page_without_a_snapshot_is_refused(big, monkeypatch):
    from databridge_materialise.delegated import DesignRefused
    m, svc, doc = _approved(big, big.case["grain"])
    _wrap(monkeypatch, svc, lambda t, r: r.model_copy(update={"snapshot": None})
          if t.id.startswith("base-p") else r)
    with pytest.raises(DesignRefused) as e:
        _build(m, svc, doc)
    assert e.value.code == "SOURCE_UNPROVEN"


def test_r1_a_linked_read_on_the_same_connection_from_another_snapshot_is_refused(
        big, monkeypatch):
    """Before: the pins kept the LAST snapshot per connection, so a lookup read at H2 replaced
    the base's H1 and the build published."""
    from databridge_materialise.delegated import DesignRefused
    grain = _grain_with(big, "lookup")
    m, svc, doc = _approved(big, grain)
    lookups = {lk["id"] for lk in doc["design"]["links"] if lk["kind"] == "lookup"}
    same = [lk for lk in doc["design"]["links"] if lk["kind"] == "lookup"
            and lk["target"]["connection"] == doc["design"]["grain"]["connection"]]
    assert same, "non-vacuous: a lookup on the base's own connection"
    _wrap(monkeypatch, svc, lambda t, r: _moved(r)
          if t.id.split("-")[0] in lookups and r.snapshot is not None else r)
    with pytest.raises(DesignRefused) as e:
        _build(m, svc, doc)
    assert e.value.code == "SOURCE_MOVED"


# ------------------------------------------------------------------------------------------------
# 2. an incomplete linked read is never complete: no false orphan, no false zero
# ------------------------------------------------------------------------------------------------
REASON = "test-store: 1 row(s) came back without a pointer for every cell and were not used"


def test_r2_a_lookup_read_with_an_unusable_row_is_not_an_orphan(big, monkeypatch):
    grain = _grain_with(big, "lookup")
    m, svc, doc = _approved(big, grain)
    lookups = {lk["id"] for lk in doc["design"]["links"] if lk["kind"] == "lookup"}
    hit = {"n": 0}

    def drop(t, r):
        if t.id.split("-")[0] in lookups and len(r.rows) > 0 and not hit["n"]:
            hit["n"] += 1
            return r.model_copy(update={"rows": r.rows[1:], "coverage": r.coverage.model_copy(
                update={"excluded": (*r.coverage.excluded, REASON)})})
        return r
    _wrap(monkeypatch, svc, drop)
    res = _build(m, svc, doc)
    assert hit["n"] == 1
    rec = m.receipt(res["plan_hash"])["receipt"]
    cols = [x for x in rec["columns"] if x["via"] in lookups]
    assert cols
    for col in cols:
        assert not any("orphan" in why for why in col["unmatched"]), col["unmatched"]
    assert any(REASON in why for col in cols for why in col["unmatched"])


def test_r2_a_lookup_read_that_skipped_a_source_fails_that_column_with_its_reason(
        big, monkeypatch):
    from databridge_federated.contracts import SkippedRead
    grain = _grain_with(big, "lookup")
    m, svc, doc = _approved(big, grain)
    lookups = {lk["id"] for lk in doc["design"]["links"] if lk["kind"] == "lookup"}
    why = "test-store: the far table was skipped (not admitted)"

    def skip(t, r):
        if t.id.split("-")[0] in lookups and r.rows:
            return r.model_copy(update={"rows": r.rows[1:], "coverage": r.coverage.model_copy(
                update={"skipped": (SkippedRead(reason=why),)})})
        return r
    _wrap(monkeypatch, svc, skip)
    res = _build(m, svc, doc)
    rec = m.receipt(res["plan_hash"])["receipt"]
    cols = [x for x in rec["columns"] if x["via"] in lookups]
    assert cols
    for col in cols:
        assert col["grounded"] == 0, "an incomplete read never stands for its selection"
        assert not any("orphan" in w for w in col["unmatched"])
        assert any(why in w for w in col["unmatched"]), col["unmatched"]


def test_r2_a_missing_group_of_an_incomplete_aggregate_is_never_a_zero(big, monkeypatch):
    grain = _grain_with(big, "aggregate")
    m, svc, doc = _approved(big, grain)
    aggs = {lk["id"] for lk in doc["design"]["links"] if lk["kind"] == "aggregate"}
    dropped: dict = {}

    def drop(t, r):
        lid = t.id.rsplit("-", 1)[0]
        if any(lid.startswith(f"{a}_") for a in aggs) and r.rows and not dropped:
            gone = r.rows[0]
            k = gone.cells[t.aggregate.group_by[0]].value
            dropped["key"] = k
            dropped["task"] = lid
            return r.model_copy(update={"rows": r.rows[1:], "coverage": r.coverage.model_copy(
                update={"excluded": (*r.coverage.excluded, REASON)})})
        return r
    _wrap(monkeypatch, svc, drop)
    res = _build(m, svc, doc)
    assert dropped, "non-vacuous: a group was dropped"
    rows = _csv_rows(m, res)
    d = doc["design"]
    from databridge_materialise.delegated import _tid
    agg_cols = [c for c in d["columns"] if c["via"] in aggs
                and _tid(f"{c['via']}_{c['name']}", 0).rsplit("-", 1)[0] == dropped["task"]]
    assert agg_cols
    lk_of = {lk["id"]: lk for lk in d["links"]}
    base = big.rows[grain]
    key = big.keys[grain]
    for c in agg_cols:
        frm = lk_of[c["via"]]["from_field"]
        hit_keys = {str(r[key]) for r in base if str(r[frm]) == str(dropped["key"])}
        assert hit_keys
        for r in rows:
            if r[key] in hit_keys:
                assert r[c["name"]] in ("", None), (c["name"], r[c["name"]])
    rec = m.receipt(res["plan_hash"])["receipt"]
    assert any(REASON in why for x in rec["columns"] if x["via"] in aggs
               for why in x["unmatched"])


# ------------------------------------------------------------------------------------------------
# 3. aggregate chunks: one snapshot, each pointer its own; the resolver checks the leaves
# ------------------------------------------------------------------------------------------------
def test_r3_aggregate_chunks_from_two_snapshots_are_refused(big, monkeypatch):
    from databridge_materialise.delegated import DesignRefused
    grain = _grain_with(big, "aggregate")
    m, svc, doc = _approved(big, grain)
    aggs = {lk["id"] for lk in doc["design"]["links"] if lk["kind"] == "aggregate"}
    seen = {"n": 0}

    def second_moved(t, r):
        lid = t.id.rsplit("-", 1)[0]
        if any(lid.startswith(f"{a}_") for a in aggs) and r.snapshot is not None:
            seen["n"] += 1
            return _moved(r) if seen["n"] == 2 else r
        return r
    _wrap(monkeypatch, svc, second_moved)
    with pytest.raises(DesignRefused) as e:
        _build(m, svc, doc)
    assert seen["n"] >= 2, "non-vacuous: the aggregate was read in several chunks"
    assert e.value.code == "SOURCE_MOVED"


def test_r3_the_resolver_checks_an_aggregate_pointers_leaves(big):
    from databridge_materialise.delegated import dsd_kind, resolve_dsd
    grain = _grain_with(big, "aggregate")
    m, svc, doc = _approved(big, grain)
    res = _build(m, svc, doc)
    # a grounded aggregate cell with leaves, from the build's own sidecar
    import json
    side = m.out_root / res["plan_hash"] / next(f for f, r in res["file_roles"].items()
                                                 if r == "pointers")
    from databridge_core import SourcePointer
    ptrs = []
    for line in side.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        p = rec.get("pointer")
        if p and p.get("kind") == "derived":
            sp = SourcePointer.model_validate(p)
            if dsd_kind(sp) == "aggregate" and sp.derived_from:
                ptrs.append(sp)
    assert ptrs, "non-vacuous: an aggregate pointer with leaves"
    p = ptrs[0]
    fd = big.root / "facts"
    ok = resolve_dsd(p, facts_dir=fd, resolve_leaf=lambda _x: True)
    assert ok is not None
    leaf = p.derived_from[0]
    # a leaf from another snapshot
    bad1 = p.model_copy(update={"derived_from": [leaf.model_copy(update={
        "snapshot": leaf.snapshot.model_copy(update={"content_hash": "0" * 64}),
        "capture_digest": "0" * 64}), *p.derived_from[1:]]})
    assert resolve_dsd(bad1, facts_dir=fd, resolve_leaf=lambda _x: True) is None
    # a leaf naming a row the selection does not hold
    bad2 = p.model_copy(update={"derived_from": [leaf.model_copy(update={"row_id": "no-such"}),
                                                 *p.derived_from[1:]]})
    assert resolve_dsd(bad2, facts_dir=fd, resolve_leaf=lambda _x: True) is None
    # an outer snapshot that is not the predicate's file
    bad3 = p.model_copy(update={"snapshot": p.snapshot.model_copy(
        update={"content_hash": "0" * 64})})
    assert resolve_dsd(bad3, facts_dir=fd, resolve_leaf=lambda _x: True) is None


# ------------------------------------------------------------------------------------------------
# 4. a build REQUIRES the actor object
# ------------------------------------------------------------------------------------------------
def test_r4_a_build_without_an_actor_is_refused_before_reading(big):
    from databridge_materialise.delegated import DesignRefused, DesignStore
    m, svc, doc = _approved(big, big.case["grain"])
    with pytest.raises(DesignRefused) as e:
        m.build_design(doc["id"], digest=doc["digest"], by="staff", service=svc,
                       admission=_adm())
    assert e.value.code == "APPROVER_NOT_AUTHENTICATED"
    assert not big.facts.calls
    with pytest.raises(DesignRefused):
        DesignStore(m.out_root).record_build(doc["id"], by="staff", build_id="x" * 64,
                                             rev=doc["rev"], digest=doc["digest"], actor=None)

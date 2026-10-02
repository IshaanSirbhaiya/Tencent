"""LINK7 (finance dry run, 2026-10-02): reviewing proposed links and re-proposed tables from the
Sources page.

TAB6 made the engine PROPOSE a link where a column no foreign key declares holds, by measurement,
the keys of another mapped table (`mapping.propose_links`), and RE-PROPOSE an approved table that
gains a foreign-key edge. These tests drive the gateway routes the page uses:

* `GET /sources/{id}/mapping/review` lists each link with its evidence (column pair, overlap
  counts, matched sample keys) and each table that needs approval AGAIN, with the reason;
* `POST /sources/{id}/links/approve|reject` decide one link as the signed-in principal — refused
  without a credential, and approve is refused for a link into a person kind.

Two estates throughout (the workspace/Larkspur fixture database and the finance fixture
databases), loaded through the real `crawl.fetch_table`. Every expectation is derived from the
estate's rows and the active pack's vocabulary, never written down.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from databridge_gateway import auth

from conftest import auth_headers
from test_platform_routes import _cmd, _mount, env  # noqa: F401  (fixture)

MI_TESTS = Path(__file__).resolve().parents[3] / "packages" / "mcp_ingest" / "tests"
if str(MI_TESTS) not in sys.path:
    sys.path.insert(0, str(MI_TESTS))

import estates  # noqa: E402

ESTATE_NAMES = ("workspace", "finance")
LEFT, RIGHT = "left_src", "right_src"


@pytest.fixture(scope="module")
def estate_snaps(tmp_path_factory):
    out = {}
    for name in ESTATE_NAMES:
        d = tmp_path_factory.mktemp(f"link7_{name}")
        db = estates.build_estate(name, d / "e.sqlite")
        try:
            out[name] = estates.snapshots(db, name)
        finally:
            db.close()
    return out


@pytest.fixture(params=ESTATE_NAMES)
def estate(request, env, monkeypatch, estate_snaps):
    """The REAL pack of the estate (the platform fixture's toy pack root is dropped)."""
    from databridge_graph.packs import clear_caches, deployment

    monkeypatch.delenv("DATABRIDGE_PACKS_ROOT", raising=False)
    clear_caches()
    name = request.param
    with deployment(estates.ESTATES[name]["pack"]) as vocab:
        yield {"name": name, "snaps": estate_snaps[name], "vocab": vocab, "env": env}
    clear_caches()


def _ref(fk):
    return f"{fk['ref_schema']}.{fk['ref_table']}" if fk.get("ref_schema") else fk["ref_table"]


def _fks(snap):
    return [f for f in snap.get("foreign_keys") or [] if f.get("column") and f.get("ref_table")]


def _strip_fk(snap, col):
    out = copy.deepcopy(snap)
    out["foreign_keys"] = [f for f in out["foreign_keys"] if f["column"] != col]
    return out


def _link_case(estate):
    """A declared FK whose target is mapped, not a person kind, keyed by a non-numeric single key,
    with enough values to be measured: stripping the declaration makes it a link PROPOSAL."""
    from databridge_mcp_ingest.mapping import _key_values, is_person_kind, propose_rules

    snaps, vocab = estate["snaps"], estate["vocab"]
    rules = propose_rules(snaps, vocab)
    for t, s in sorted(snaps.items()):
        if not rules[t].node or len(s["rows"]) > 5000:
            continue
        for fk in _fks(s):
            target = _ref(fk)
            if target == t or not (rules.get(target) and rules[target].node):
                continue
            if is_person_kind(vocab, rules[target].node["kind"]):
                continue
            if not _key_values(snaps[target]):
                continue
            return t, fk, target, rules
    raise AssertionError("no declared FK into a non-person, string-keyed table")  # non-vacuous


def _seed_link(estate, monkeypatch):
    """`LEFT` maps the table with the declaration stripped; `RIGHT` holds the target. The engine
    proposes the link (`refresh_link_proposals`), and the two sides' captured rows are what
    `captured_tables` returns."""
    from databridge_mcp_ingest import mapping
    from databridge_mcp_ingest.mapping import (SourceMapping, propose_rules,
                                               refresh_link_proposals, save_mapping)

    t, fk, target, rules = _link_case(estate)
    vocab, snaps = estate["vocab"], estate["snaps"]
    mine = {t: _strip_fk(snaps[t], fk["column"])}
    save_mapping(SourceMapping(source_id=LEFT, tables=propose_rules(mine, vocab)))
    others = {RIGHT: {target: (snaps[target], rules[target].node["kind"])}}
    m = refresh_link_proposals(LEFT, mine, vocab, others=others)
    [link] = [lk for lk in m.tables[t].links if lk["column"] == fk["column"]]
    assert link["status"] == "proposed" and link["edge"]                   # non-vacuous
    captured = {LEFT: mine, RIGHT: {target: snaps[target]}}
    monkeypatch.setattr(mapping, "captured_tables", lambda sid: captured.get(sid, {}))
    return t, fk, target


# --- the evidence ------------------------------------------------------------------------------
def test_the_review_lists_each_proposed_link_with_its_measured_evidence(estate, monkeypatch):
    t, fk, target = _seed_link(estate, monkeypatch)
    snaps, col = estate["snaps"], fk["column"]
    app, _, _ = _mount(estate["env"])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, LEFT).status_code == 200
        r = c.get(f"/sources/{LEFT}/mapping/review")
        assert r.status_code == 200, r.text
        [lk] = [x for x in r.json()["links"] if x["table"] == t and x["column"] == col]
    keys = {str(row[fk["ref_column"]]) for row in snaps[target]["rows"]}
    vals = [str(row[col]) for row in snaps[t]["rows"] if row[col] is not None]
    hit = sorted(set(vals) & keys)
    assert (lk["target_source"], lk["target_table"], lk["target_key"]) == \
        (RIGHT, target, fk["ref_column"])
    assert lk["status"] == "proposed" and lk["approvable"] and lk["edge_kind"]
    ev = lk["evidence"]
    assert ev["matched"] == sum(v in keys for v in vals) and ev["with_value"] == len(vals)
    assert ev["distinct_matched"] == len(hit) and ev["target_keys"] == len(keys)
    assert ev["samples"] == hit[:len(ev["samples"])] and 0 < len(ev["samples"]) <= 5
    with TestClient(app) as anon:                                       # read needs a credential
        assert anon.get(f"/sources/{LEFT}/mapping/review").status_code == 401


# --- approve ---------------------------------------------------------------------------------
def test_a_link_is_approved_only_by_the_signed_in_principal(estate, monkeypatch):
    from databridge_mcp_ingest.mapping import load_mapping

    t, fk, target = _seed_link(estate, monkeypatch)
    app, _, _ = _mount(estate["env"])
    body = {"table": t, "column": fk["column"], "approved_by": "mallory"}
    with TestClient(app) as anon:
        assert anon.post(f"/sources/{LEFT}/links/approve", json=body).status_code == 401
    assert load_mapping(LEFT).tables[t].links[0]["status"] == "proposed"   # nothing changed
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, LEFT).status_code == 200
        r = c.post(f"/sources/{LEFT}/links/approve", json=body)
        assert r.status_code == 200, r.text
        again = c.post(f"/sources/{LEFT}/links/approve", json=body)      # no longer a proposal
        assert again.status_code == 409 and again.json()["refusal"]["code"] == "LINK_NOT_APPROVABLE"
        review = c.get(f"/sources/{LEFT}/mapping/review").json()
    tm = load_mapping(LEFT).tables[t]
    [lk] = [x for x in tm.links if x["column"] == fk["column"]]
    assert lk["status"] == "approved" and lk["approved_by"] == "analyst-7"   # never the body name
    assert lk["decided_dev_only"] is False
    [edge] = [e for e in tm.edges if e.get("ref")]
    assert edge["ref"] == {"source": RIGHT, "table": target, "key": fk["ref_column"]}
    [row] = [x for x in review["links"] if x["column"] == fk["column"]]
    assert row["status"] == "approved" and not row["approvable"]
    assert row["decided_by"] == "analyst-7"


@pytest.fixture()
def dev_auth(monkeypatch):
    monkeypatch.setenv("WALNUT_DEV_AUTH", "1")
    auth.reset_auth_state_for_tests()
    yield {"Authorization": f"Bearer {auth.DEV_TOKEN}"}
    monkeypatch.delenv("WALNUT_DEV_AUTH", raising=False)
    auth.reset_auth_state_for_tests()


def test_a_link_decided_with_the_dev_token_is_marked(estate, monkeypatch, dev_auth):
    from databridge_mcp_ingest.mapping import load_mapping

    t, fk, _ = _seed_link(estate, monkeypatch)
    app, _, _ = _mount(estate["env"])
    with TestClient(app, headers=dev_auth) as c:
        assert _cmd(c, LEFT).status_code == 200
        r = c.post(f"/sources/{LEFT}/links/approve", json={"table": t, "column": fk["column"]})
        assert r.status_code == 200, r.text
        [row] = [x for x in c.get(f"/sources/{LEFT}/mapping/review").json()["links"]
                 if x["column"] == fk["column"]]
    [lk] = [x for x in load_mapping(LEFT).tables[t].links if x["column"] == fk["column"]]
    assert lk["approved_by"] == auth.DEV_PRINCIPAL_ID and lk["decided_dev_only"] is True
    assert lk["decided_authenticated_by"] == auth.DEV_MECHANISM
    assert row["decided_dev_only"] is True


# --- person kinds ------------------------------------------------------------------------------
def test_a_link_into_a_person_kind_is_refused_and_shows_no_keys(estate, monkeypatch):
    """Even a link FORGED into the mapping as an approvable proposal (status proposed, with an
    edge) is refused when its target kind stands for people — by the route and by the engine."""
    from databridge_mcp_ingest.mapping import is_person_kind, load_mapping, save_mapping

    t, fk, target = _seed_link(estate, monkeypatch)
    vocab = estate["vocab"]
    person = next(k for k in sorted(vocab.node_kinds) if is_person_kind(vocab, k))
    m = load_mapping(LEFT)
    tm = m.tables[t]
    lk = next(x for x in tm.links if x["column"] == fk["column"])
    forged = {**lk, "target_kind": person,
              "edge": {**lk["edge"], "dst": lk["edge"]["dst"].replace(
                  lk["target_kind"] + ":", person + ":"),
                  "src": lk["edge"]["src"].replace(lk["target_kind"] + ":", person + ":")}}
    save_mapping(m.model_copy(update={"tables": {**m.tables, t: tm.model_copy(update={
        "links": [forged if x is lk else x for x in tm.links]})}}))
    app, _, _ = _mount(estate["env"])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, LEFT).status_code == 200
        r = c.post(f"/sources/{LEFT}/links/approve", json={"table": t, "column": fk["column"]})
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "LINK_TARGETS_PERSON"
        [row] = [x for x in c.get(f"/sources/{LEFT}/mapping/review").json()["links"]
                 if x["column"] == fk["column"]]
    assert row["person_target"] and not row["approvable"]
    assert row["evidence"]["samples"] is None and row["evidence"]["samples_note"]
    after = load_mapping(LEFT).tables[t]
    assert not any(e.get("ref") for e in after.edges)                   # no edge was added
    # the ENGINE refuses it as well (defence in depth: the route's pre-check is not the only gate)
    from databridge_mcp_ingest.mapping import MappingApprovalRefused, approve_link
    from databridge_gateway.auth import AuthenticatedPrincipal
    with pytest.raises(MappingApprovalRefused, match="person kind"):
        approve_link(LEFT, t, fk["column"], vocab=vocab,
                     principal=AuthenticatedPrincipal(principal_id="analyst-7"))


# --- reject ------------------------------------------------------------------------------------
def test_a_link_is_rejected_by_the_principal_and_stays_rejected(estate, monkeypatch):
    from databridge_mcp_ingest.mapping import load_mapping, refresh_link_proposals

    t, fk, target = _seed_link(estate, monkeypatch)
    app, _, _ = _mount(estate["env"])
    body = {"table": t, "column": fk["column"], "reason": "different identifier space"}
    with TestClient(app) as anon:
        assert anon.post(f"/sources/{LEFT}/links/reject", json=body).status_code == 401
    before = load_mapping(LEFT).tables[t]
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, LEFT).status_code == 200
        r = c.post(f"/sources/{LEFT}/links/reject", json={**body, "rejected_by": "mallory"})
        assert r.status_code == 200, r.text
        again = c.post(f"/sources/{LEFT}/links/reject", json=body)
        assert again.status_code == 409 and again.json()["refusal"]["code"] == "LINK_NOT_REJECTABLE"
    tm = load_mapping(LEFT).tables[t]
    [lk] = [x for x in tm.links if x["column"] == fk["column"]]
    assert lk["status"] == "rejected" and lk["rejected_by"] == "analyst-7"
    assert lk["reject_reason"] == body["reason"] and lk["decided_dev_only"] is False
    assert (tm.node, tm.edges, tm.status, tm.approval) == \
        (before.node, before.edges, before.status, before.approval)       # nothing else moved
    # the next sync's proposals keep the person's decision
    snaps, vocab = estate["snaps"], estate["vocab"]
    from databridge_mcp_ingest.mapping import propose_rules
    rules = propose_rules(snaps, vocab)
    m = refresh_link_proposals(LEFT, {t: _strip_fk(snaps[t], fk["column"])}, vocab,
                               others={RIGHT: {target: (snaps[target],
                                                        rules[target].node["kind"])}})
    [lk] = [x for x in m.tables[t].links if x["column"] == fk["column"]]
    assert lk["status"] == "rejected"


# --- tables that need approval again -------------------------------------------------------------
def test_a_reproposed_table_needs_approval_again_with_the_reason(estate):
    """An approved table that was missing a declared FK edge is RE-PROPOSED by the engine
    (`resolve_pending_fks`); the review says it needs approval again, by whom it was approved and
    why. A table nobody approved is not listed (non-vacuous), and approving it again clears it."""
    from databridge_mcp_ingest.mapping import (SourceMapping, load_mapping, propose_rules,
                                               resolve_pending_fks, save_mapping)

    snaps, vocab = estate["snaps"], estate["vocab"]
    rules = propose_rules(snaps, vocab)
    t, edge = next((t, e) for t, tm in sorted(rules.items()) if tm.node
                   for e in tm.edges if e.get("fk"))
    target = next(_ref(f) for f in _fks(snaps[t]) if f["column"] == edge["evidence"][0])
    other = next(u for u, tm in sorted(rules.items()) if tm.node and u not in (t, target))
    tables = {t: rules[t].model_copy(update={"edges": [e for e in rules[t].edges
                                                       if e is not edge]}),
              target: rules[target], other: rules[other]}
    save_mapping(SourceMapping(source_id=LEFT, tables=tables))
    app, _, _ = _mount(estate["env"])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, LEFT).status_code == 200
        ok = c.post(f"/sources/{LEFT}/mapping/approve", json={"tables": [t]})
        assert ok.status_code == 200, ok.text
        assert c.get(f"/sources/{LEFT}/mapping/review").json()["reapproval"] == {}
        m = load_mapping(LEFT)
        got = resolve_pending_fks(m.tables, snaps, vocab)
        assert t in got and got[t].status == "proposed"                   # the engine re-proposed
        save_mapping(m.model_copy(update={"tables": {**m.tables, **got}}))
        again = c.get(f"/sources/{LEFT}/mapping/review").json()["reapproval"]
        assert set(again) == {t}                         # not `other`: it was never approved
        assert again[t]["previously_approved_by"] == "analyst-7"
        assert any(r.startswith("re-proposed") for r in again[t]["reasons"])
        ok = c.post(f"/sources/{LEFT}/mapping/approve", json={"tables": [t]})
        assert ok.status_code == 200, ok.text
        assert c.get(f"/sources/{LEFT}/mapping/review").json()["reapproval"] == {}

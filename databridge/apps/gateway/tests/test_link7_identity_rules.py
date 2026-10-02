"""LINK7 for GRAPH6 (FIN-04, `wiki/20-decisions/D-004`): approving a pack's declared identity rule
from the app.

`GET /ontology/identity-rules` lists every bound pack's declared rules with their decision state
and a preview of what approving each would merge (the engine's own `consolidate_identity`, run on
a copy). `POST /ontology/identity-rules/decide` records the decision for the AUTHENTICATED session
principal and, on approval, consolidates the graph.

Two estates, built the way GRAPH6's own tests build them (`packages/graph/tests/
test_fin_identity_rules.py`): the finance fixtures (core banking parties + CRM organisations +
filings/memos) and the workspace fixtures (customers, employees, contacts, files; its pack copy
declares a rule on `organisation`, as GRAPH6's test does). Expectations come from the fixtures.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from databridge_gateway import auth

from conftest import auth_headers
from test_platform_routes import _mount, env  # noqa: F401  (fixture)

GRAPH_TESTS = Path(__file__).resolve().parents[3] / "packages" / "graph" / "tests"
if str(GRAPH_TESTS) not in sys.path:
    sys.path.insert(0, str(GRAPH_TESTS))

import test_fin_identity_rules as fx  # noqa: E402

ESTATE_NAMES = sorted(fx.ESTATES)


@pytest.fixture(params=ESTATE_NAMES)
def estate(request, env, tmp_path, monkeypatch):
    from databridge_graph import ontology_state
    from databridge_graph.packs import DEFAULT_PACKS_ROOT, clear_caches, deployment
    from databridge_graph.store import GraphStore

    est = fx.ESTATES[request.param]()
    monkeypatch.setenv(ontology_state.STATE_ENV, str(tmp_path / "ontology_state.json"))
    monkeypatch.delenv("DATABRIDGE_PACKS_ROOT", raising=False)
    if est.pack == "workspace":
        root = tmp_path / "ws_packs"
        (root / "workspace").mkdir(parents=True)
        text = (DEFAULT_PACKS_ROOT / "workspace" / "ontology.yaml").read_text(encoding="utf-8")
        text += ("\nidentity_rules:\n  - kind: organisation\n    business_keys: [domain]\n"
                 "    exact_name: true\n    similarity_merge: false\n")
        (root / "workspace" / "ontology.yaml").write_text(text, encoding="utf-8")
        monkeypatch.setenv("DATABRIDGE_PACKS_ROOT", str(root))
    clear_caches()
    with deployment(est.pack) as v:
        store = GraphStore()
        where = fx._build(est, v, store)
        app, _, _ = _mount(env)
        app.state.graph_store = store
        yield {"est": est, "vocab": v, "store": store, "where": where, "app": app}
    clear_caches()


def _rule(body: dict, kind: str) -> dict:
    [r] = [x for x in body["identity_rules"] if x["kind"] == kind]
    return r


def test_the_listing_shows_each_declared_rule_and_a_preview_that_changes_nothing(estate):
    est, v, store, app = estate["est"], estate["vocab"], estate["store"], estate["app"]
    with TestClient(app) as anon:
        assert anon.get("/ontology/identity-rules").status_code == 401
    before = store.counts()
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        r = c.get("/ontology/identity-rules")
        assert r.status_code == 200, r.text
        body = r.json()
    assert store.counts() == before                                     # a preview writes nothing
    declared = v.identity_rule_for(est.org_kind)
    rule = _rule(body, est.org_kind)
    assert rule["pack"] == est.pack and rule["business_keys"] == list(declared.business_keys)
    assert [t["rule"] for t in rule["rules"]] == list(declared.rule_types)
    assert all(t["state"] == "not_approved" for t in rule["rules"])
    # every subject kind is absent: a person can carry no rule
    assert not any(v.is_subject(x["kind"]) for x in body["identity_rules"])
    # the exact-name preview names the groups the fixtures say share a name and no conflicting key
    [exact] = [t for t in rule["rules"] if t["rule"] == "exact_name"]
    p = exact["preview"]
    nodes = fx._nodes_by_name(store, est)
    shared = [name for name, g in fx._name_groups(est).items()
              if len(nodes.get(name, ())) > 1 and len(fx._key_values(est, g)) <= 1]
    assert shared and p["groups"] > 0 and p["sample_groups"]                 # non-vacuous
    for g in p["sample_groups"]:
        assert len({fx.norm(x) for x in g["labels"]}) == 1, g             # one name per group


def test_a_decision_needs_an_authenticated_principal(estate):
    from databridge_gateway.auth import AuthenticatedPrincipal, require_principal

    est, app = estate["est"], estate["app"]
    body = {"pack": est.pack, "kind": est.org_kind, "rule": "exact_name"}
    with TestClient(app) as anon:
        assert anon.post("/ontology/identity-rules/decide", json=body).status_code == 401
    # a blank principal (a session that names nobody) is refused by the shared approval gate
    app.dependency_overrides[require_principal] = lambda: AuthenticatedPrincipal(principal_id="  ")
    try:
        with TestClient(app) as c:
            r = c.post("/ontology/identity-rules/decide", json=body)
            assert r.status_code in (403, 409) and "refusal" in r.json(), r.text
    finally:
        app.dependency_overrides.clear()
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        rules = _rule(c.get("/ontology/identity-rules").json(), est.org_kind)["rules"]
    assert all(t["state"] == "not_approved" for t in rules)               # nothing recorded


def test_a_rule_on_a_subject_kind_or_an_undeclared_rule_is_refused(estate):
    est, v, app = estate["est"], estate["vocab"], estate["app"]
    assert v.is_subject(est.person_kind)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        for body in ({"pack": est.pack, "kind": est.person_kind, "rule": "exact_name"},
                     {"pack": est.pack, "kind": est.person_kind, "rule": "business_key"}):
            r = c.post("/ontology/identity-rules/decide", json=body)
            assert r.status_code == 409 and r.json()["refusal"]["code"] == "IDENTITY_RULE_REFUSED"
        other = next(p for p in ("finance", "workspace", "healthcare") if p != est.pack)
        r = c.post("/ontology/identity-rules/decide",
                   json={"pack": other, "kind": est.org_kind, "rule": "exact_name"})
        assert r.status_code == 409                                       # not this pack's rule


def test_approving_the_rules_merges_organisations_and_never_people(estate):
    from databridge_graph.identity import resolve_entity

    est, v, store, app, where = (estate[k] for k in ("est", "vocab", "store", "app", "where"))
    persons_before = {r["id"] for r in store.open_nodes() if r["kind"] == est.person_kind}
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        for rule in v.identity_rule_for(est.org_kind).rule_types:
            listing = _rule(c.get("/ontology/identity-rules").json(), est.org_kind)
            [entry] = [t for t in listing["rules"] if t["rule"] == rule]
            r = c.post("/ontology/identity-rules/decide",
                       json={"pack": est.pack, "kind": est.org_kind, "rule": rule,
                             "principal": "mallory"})
            assert r.status_code == 200, r.text
            out = r.json()
            assert out["decision"]["principal"] == "analyst-7"            # never the body's name
            assert out["decision"]["dev_only"] is False
            merged = [m for m in out["consolidation"]["merged"] if m["kind"] == est.org_kind]
            # the preview was the engine's own answer
            assert len(merged) == entry["preview"]["groups"], rule
        final = _rule(c.get("/ontology/identity-rules").json(), est.org_kind)
    assert all(t["state"] == "approved" and t["decided_by"] == "analyst-7"
               for t in final["rules"])
    # an organisation two sources hold (two systems, or a system and a document) resolves to ONE
    # node — GRAPH6's own definition of the case
    cross = [g for g in fx._name_groups(est).values()
             if (len({r.source for r in g}) > 1 or any(g[0].label in t for t in est.texts.values()))
             and len(fx._key_values(est, g)) <= 1]
    assert cross                                                          # non-vacuous
    for g in cross:
        match = resolve_entity(store, g[0].label)
        assert match.node_id == fx._now_at(store, where[g[0].id]) and not match.candidates
    if est.name == "finance":
        match = resolve_entity(store, "Fairmont Reach Property LLC")
        assert match.node_id and not match.candidates
    # people: untouched, and a person's name still returns candidates
    assert {r["id"] for r in store.open_nodes() if r["kind"] == est.person_kind} == persons_before
    named = [r for r in est.persons if any(r.label in t for t in est.texts.values())]
    assert named
    match = resolve_entity(store, named[0].label)
    assert match.node_id is None and len(match.candidates) >= 2


@pytest.fixture()
def dev_auth(monkeypatch):
    monkeypatch.setenv("WALNUT_DEV_AUTH", "1")
    auth.reset_auth_state_for_tests()
    yield {"Authorization": f"Bearer {auth.DEV_TOKEN}"}
    monkeypatch.delenv("WALNUT_DEV_AUTH", raising=False)
    auth.reset_auth_state_for_tests()


def test_a_decision_made_with_the_dev_token_is_marked(estate, dev_auth):
    est, app = estate["est"], estate["app"]
    with TestClient(app, headers=dev_auth) as c:
        r = c.post("/ontology/identity-rules/decide",
                   json={"pack": est.pack, "kind": est.org_kind, "rule": "exact_name"})
        assert r.status_code == 200, r.text
        assert r.json()["decision"]["dev_only"] is True
        [t] = [x for x in _rule(c.get("/ontology/identity-rules").json(), est.org_kind)["rules"]
               if x["rule"] == "exact_name"]
    assert t["state"] == "approved" and t["dev_only"] is True
    assert t["decided_by"] == auth.DEV_PRINCIPAL_ID

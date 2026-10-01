"""Review R-017 (Codex gpt-5.6-sol, track t05 — ontology): one test per finding that was real.

Every test runs against a PRIVATE pack tree in `tmp_path` (``DATABRIDGE_PACKS_ROOT``) and a
private state/audit path (``WALNUT_ONTOLOGY_STATE_PATH``) — the repo's own ``packs/`` and
``.databridge/`` are never touched. Kind names, meta-kinds and principals are DERIVED (from the
pack under test, the meta-model and the conftest token directory), never spelled as expectations.

Findings covered (see ``wiki/40-reviews/R-017-t05-ontology.md``):

1. YAML injection through the approver identity or any user string written into ``ontology.yaml``
2. an agent claiming ``origin: owner``; non-owner principals deciding or editing vocabulary
3. hard-REFUSED proposals converted into approvable escalations
4. non-atomic pack writes (truncate-then-write) and line-ending churn
5. an approved proposal leaving the bound deployment vocabulary unbuildable
6. identity-protected kinds reactivated without explicit confirmation
7. subject declarations approved as ordinary nodes
8. state persistence non-atomic; corruption failing OPEN
9. decisions attributed to a request-supplied name instead of the signed-in principal
§5.8: agent-origin proposals record the originating agent and model in the audit
"""
from __future__ import annotations

import json

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import AUTH_HEADERS, TEST_TOKENS, auth_headers
from databridge_gateway.ontology_routes import register_ontology_routes

#: Whoever `AUTH_HEADERS` authenticates as — configured as the ONLY ontology owner below.
OWNER = TEST_TOKENS[AUTH_HEADERS["Authorization"].split(" ", 1)[1]]
#: Any other principal in the conftest directory: authenticated, but NOT an owner.
NON_OWNER = sorted(p for p in TEST_TOKENS.values() if p != OWNER)[0]

PACK_YAML = """\
# packs/workspace — R-017 test copy. This comment must survive every write.
pack: workspace
title: Workspace (test)
version: "0.1"

node_kinds:
  - name: person
    binds_to: entity
    description: A natural person.

  - name: organisation
    binds_to: organisation
    description: A company.

  - name: document
    binds_to: document
    description: A captured file.

edge_kinds:
  - name: works_for
    binds_to: role_of
    src: [person]
    dst: [organisation]
    description: A person works for an organisation.

subject_kinds:
  - kind: person
    identifiers: [email, employee_id]
    never_fuzzy_merge: true
    attestation: verified_channel
    grant_type: PersonReadGrant
"""

#: A second pack on disk that shares no kind name with `workspace` at baseline.
ANNEX_YAML = """\
# packs/annex — R-017 test copy.
pack: annex
title: Annex (test)
version: "0.1"

node_kinds:
  - name: vessel
    binds_to: entity
    description: A ship.

edge_kinds:
  - name: moored_at
    binds_to: at
    src: [vessel]
    dst: [vessel]
    description: A ship alongside another.
"""

GROUNDED = [{"source_id": "ops", "kind": "table_cell", "table": "t", "column": "c",
             "row_id": "r1", "trust": "trusted"}]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from databridge_graph.packs import clear_caches

    root = tmp_path / "packs"
    (root / "workspace").mkdir(parents=True)
    (root / "annex").mkdir(parents=True)
    # Written in BINARY so the file's line endings are exactly LF, whatever the platform.
    (root / "workspace" / "ontology.yaml").write_bytes(PACK_YAML.encode("utf-8"))
    (root / "annex" / "ontology.yaml").write_bytes(ANNEX_YAML.encode("utf-8"))
    (root / "deployment.yaml").write_text("enabled_packs:\n  - workspace\n", encoding="utf-8")
    state = tmp_path / "state" / "ontology_state.json"
    monkeypatch.setenv("DATABRIDGE_PACKS_ROOT", str(root))
    monkeypatch.setenv("WALNUT_ONTOLOGY_STATE_PATH", str(state))
    monkeypatch.setenv("WALNUT_INGEST_DIR", str(tmp_path / "ingest"))
    monkeypatch.setenv("WALNUT_ONTOLOGY_OWNERS", OWNER)
    monkeypatch.delenv("WALNUT_ONTOLOGY_AUDIT_PATH", raising=False)
    monkeypatch.delenv("DATABRIDGE_PACKS", raising=False)
    clear_caches()
    yield {"root": root, "ws": root / "workspace" / "ontology.yaml",
           "annex": root / "annex" / "ontology.yaml", "state": state,
           "audit": state.with_name("ontology_audit.jsonl")}
    clear_caches()


def _app() -> FastAPI:
    app = FastAPI()
    register_ontology_routes(app)
    return app


@pytest.fixture()
def owner(env):
    with TestClient(_app(), headers=AUTH_HEADERS) as c:
        yield c


@pytest.fixture()
def agent(env):
    with TestClient(_app(), headers=auth_headers(NON_OWNER)) as c:
        yield c


def _audit(env) -> list[dict]:
    if not env["audit"].is_file():
        return []
    return [json.loads(ln) for ln in env["audit"].read_text(encoding="utf-8").splitlines() if ln]


def _ordinary_meta(axis: str = "node") -> str:
    """A meta-kind the evaluator lets auto-approve for a grounded proposal — derived, not named."""
    from databridge_graph.meta import EdgeMetaKind, NodeMetaKind
    from databridge_gateway.ontology_routes import _evidence_pointers, guardrail

    ev = _evidence_pointers(tuple(GROUNDED))
    metas = NodeMetaKind if axis == "node" else EdgeMetaKind
    ok = [m.value for m in metas
          if guardrail(axis=axis, binds_to=m.value, name="harbour", pack="workspace",
                       proposed_by="t", evidence=ev).auto_approvable]
    assert ok, "no meta-kind is auto-approvable here; the tests below would prove nothing"
    return ok[0]


def _propose(client, **kw) -> dict:
    body = {"axis": "node", "name": "harbour", "binds_to": _ordinary_meta(),
            "proposed_by": "an adapter agent", **kw}
    r = client.post("/ontology/proposals", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# --- finding 1: YAML injection ---------------------------------------------------------------
INJECTION = "Alice\n  - name: unreviewed\n    binds_to: entity\n  #"


def test_an_approver_label_carrying_a_newline_cannot_inject_a_kind(owner, env):
    proposed = _propose(owner)
    before = env["ws"].read_bytes()
    r = owner.post(f"/ontology/proposals/{proposed['id']}/approve",
                   json={"decided_by": INJECTION})
    assert r.status_code == 422, r.text
    assert r.json()["refusal"]["code"] == "BAD_TEXT"
    assert env["ws"].read_bytes() == before
    names = {n["name"] for n in yaml.safe_load(env["ws"].read_text("utf-8"))["node_kinds"]}
    assert "unreviewed" not in names
    pending = {p["id"] for p in owner.get("/ontology").json()["pending_proposals"]}
    assert proposed["id"] in pending                       # nothing was decided


def test_a_principal_id_carrying_a_newline_cannot_inject_a_kind(env, monkeypatch):
    """The principal id is operator config, but it reaches the YAML comment too — the sink itself
    refuses a control character, whoever supplied it."""
    from databridge_gateway import auth

    evil_token = "test-token-r017-evil"
    monkeypatch.setenv("WALNUT_API_TOKENS", json.dumps({**TEST_TOKENS, evil_token: INJECTION}))
    monkeypatch.setenv("WALNUT_ONTOLOGY_OWNERS", json.dumps([OWNER, INJECTION]))
    auth.reset_auth_state_for_tests()
    try:
        with TestClient(_app(), headers={"Authorization": f"Bearer {evil_token}"}) as c:
            proposed = _propose(c)
            before = env["ws"].read_bytes()
            r = c.post(f"/ontology/proposals/{proposed['id']}/approve", json={})
        assert r.status_code == 503, r.text
        assert r.json()["refusal"]["code"] == "PACK_WRITE_FAILED"
        assert env["ws"].read_bytes() == before
    finally:
        monkeypatch.setenv("WALNUT_API_TOKENS", json.dumps(TEST_TOKENS))
        auth.reset_auth_state_for_tests()


def test_user_text_round_trips_through_the_pack_file_exactly(owner, env):
    """Quotes, a colon, a hash, DEL and a BOM in a description are written as one YAML scalar and
    read back identically — and the parsed file differs from before by exactly that entry."""
    desc = 'He said "yes": #1 — ok\x7fand﻿end'
    before = yaml.safe_load(env["ws"].read_text("utf-8"))
    proposed = _propose(owner, description=desc)
    r = owner.post(f"/ontology/proposals/{proposed['id']}/approve", json={"decided_by": "N. A."})
    assert r.status_code == 200, r.text
    after = yaml.safe_load(env["ws"].read_text("utf-8"))
    added = [n for n in after["node_kinds"] if n["name"] == proposed["name"]]
    assert added == [{"name": proposed["name"], "binds_to": proposed["binds_to"],
                      "description": desc}]
    after["node_kinds"] = [n for n in after["node_kinds"] if n["name"] != proposed["name"]]
    assert after == before                                   # nothing else moved
    assert "# packs/workspace" in env["ws"].read_text("utf-8")


# --- finding 2: origin and authority come from the principal ----------------------------------
def test_an_agent_cannot_claim_owner_origin_to_auto_apply(owner, agent, env):
    assert owner.put("/ontology/auto_approve", json={"enabled": True}).status_code == 200
    before = env["ws"].read_bytes()
    r = agent.post("/ontology/proposals", json={
        "axis": "node", "name": "harbour", "binds_to": _ordinary_meta(),
        "proposed_by": "an agent", "origin": "owner", "evidence": GROUNDED})
    assert r.status_code == 403, r.text
    assert r.json()["refusal"]["code"] == "ORIGIN_NOT_PERMITTED"
    assert env["ws"].read_bytes() == before
    assert owner.get("/ontology").json()["pending_proposals"] == []
    # Control: the same body from the configured owner DOES auto-apply.
    ok = owner.post("/ontology/proposals", json={
        "axis": "node", "name": "harbour", "binds_to": _ordinary_meta(),
        "proposed_by": "the owner", "origin": "owner", "evidence": GROUNDED})
    assert ok.json()["status"] == "approved"


def _owner_only_routes(app: FastAPI) -> list[str]:
    """Every /ontology write route except the two any authenticated principal may use — derived
    from the app, so a new write route is covered the day it is added."""
    open_to_all = {("POST", "/ontology/proposals"), ("PUT", "/ontology/auto_approve")}
    out = []
    for route in app.routes:
        path = getattr(route, "path", "")
        for method in sorted(getattr(route, "methods", None) or ()):
            if (path.startswith("/ontology") and method in {"POST", "PUT", "PATCH", "DELETE"}
                    and (method, path) not in open_to_all):
                out.append(f"{method} {path}")
    return out


def test_a_non_owner_principal_cannot_decide_or_edit_the_vocabulary(agent, env):
    app = agent.app
    routes = _owner_only_routes(app)
    assert any("approve" in r for r in routes) and any("/kinds" in r for r in routes)
    before = env["ws"].read_bytes()
    for spec in routes:
        method, path = spec.split(" ", 1)
        r = agent.request(method, path.replace("{proposal_id}", "prop-0001"), json={})
        assert r.status_code == 403, (spec, r.text)
        assert r.json()["refusal"]["code"] == "ONTOLOGY_OWNER_REQUIRED", spec
    assert env["ws"].read_bytes() == before


def test_owners_unconfigured_means_no_owner_except_the_local_dev_principal(env, monkeypatch):
    from databridge_gateway import auth

    monkeypatch.delenv("WALNUT_ONTOLOGY_OWNERS")
    with TestClient(_app(), headers=AUTH_HEADERS) as c:
        r = c.post("/ontology/kinds/deprecate", json={"axis": "node", "kind": "document"})
        assert r.status_code == 403 and r.json()["refusal"]["code"] == "ONTOLOGY_OWNER_REQUIRED"
    monkeypatch.setenv("WALNUT_DEV_AUTH", "1")
    auth.reset_auth_state_for_tests()
    try:
        with TestClient(_app(), headers={"Authorization": f"Bearer {auth.DEV_TOKEN}"}) as c:
            r = c.post("/ontology/kinds/deprecate", json={"axis": "node", "kind": "document"})
            assert r.status_code == 200, r.text
    finally:
        monkeypatch.delenv("WALNUT_DEV_AUTH")
        auth.reset_auth_state_for_tests()


# --- finding 3: REFUSED stays refused ---------------------------------------------------------
def _conflicting_proposal() -> dict:
    """A node kind `annex` lacks but the BOUND `workspace` declares under another meta-kind."""
    from databridge_graph.meta import NodeMetaKind
    from databridge_graph.packs import load_pack

    ws = load_pack("workspace")
    name = sorted(k for k in ws.node_kinds if k not in ws.subject_kinds)[0]
    other = next(m.value for m in NodeMetaKind if m is not ws.node_kinds[name].binds_to)
    return {"pack": "annex", "axis": "node", "name": name, "binds_to": other,
            "proposed_by": "an adapter agent"}


def test_a_refused_proposal_is_refused_not_escalated(owner, env):
    before = env["annex"].read_bytes()
    r = owner.post("/ontology/proposals", json=_conflicting_proposal())
    assert r.status_code == 422, r.text
    assert r.json()["refusal"]["code"] == "PROPOSAL_REFUSED"
    assert owner.get("/ontology", params={"pack": "annex"}).json()["pending_proposals"] == []
    assert env["annex"].read_bytes() == before


def test_a_legacy_escalated_record_the_engine_now_refuses_cannot_be_approved(owner, env):
    prop = _conflicting_proposal()
    record = {**prop, "id": "prop-0001", "status": "escalated", "src": [], "dst": [],
              "description": "", "declares_subject": False, "origin": "agent",
              "decided_by": None, "decided_at": None}
    env["state"].parent.mkdir(parents=True, exist_ok=True)
    env["state"].write_text(json.dumps({"proposals": [record]}), encoding="utf-8")
    before = env["annex"].read_bytes()
    r = owner.post("/ontology/proposals/prop-0001/approve", json={})
    assert r.status_code == 409, r.text
    assert r.json()["refusal"]["code"] == "PROPOSAL_REFUSED"
    assert env["annex"].read_bytes() == before
    saved = json.loads(env["state"].read_text("utf-8"))["proposals"][0]
    assert saved["status"] == "refused" and saved["decided_by"] is None


# --- finding 4: atomic pack writes ------------------------------------------------------------
def test_a_failed_replace_leaves_the_pack_byte_identical_and_no_temp_files(owner, env,
                                                                         monkeypatch):
    from databridge_graph import ontology_state

    before = env["ws"].read_bytes()
    listing = sorted(p.name for p in env["ws"].parent.iterdir())

    def _boom(*a, **kw):
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(ontology_state.os, "replace", _boom)
    r = owner.post("/ontology/kinds", json={"axis": "node", "name": "harbour",
                                            "binds_to": _ordinary_meta()})
    assert r.status_code == 422, r.text
    assert r.json()["refusal"]["code"] == "PACK_WRITE_FAILED"
    assert env["ws"].read_bytes() == before
    assert sorted(p.name for p in env["ws"].parent.iterdir()) == listing


def test_a_write_keeps_the_files_own_line_endings(owner, env):
    assert b"\r\n" not in env["ws"].read_bytes()             # fixture wrote LF
    r = owner.post("/ontology/kinds", json={"axis": "node", "name": "harbour",
                                            "binds_to": _ordinary_meta()})
    assert r.status_code == 200, r.text
    raw = env["ws"].read_bytes()
    assert b"harbour" in raw and b"\r\n" not in raw


# --- finding 5: the bound deployment must still build ----------------------------------------
def test_an_approval_that_would_break_the_bound_vocabulary_is_rolled_back(owner, env,
                                                                         monkeypatch):
    """Simulate an evaluator gap (the guardrail says approvable) and prove the COMMIT still refuses
    a write after which the deployment's bound vocabulary no longer builds."""
    from databridge_gateway import ontology_routes
    from databridge_graph.packs import active_vocabulary, clear_caches

    (env["root"] / "deployment.yaml").write_text(
        "enabled_packs:\n  - workspace\n  - annex\n", encoding="utf-8")
    clear_caches()
    assert set(active_vocabulary().packs) == {"workspace", "annex"}
    monkeypatch.setattr(ontology_routes, "guardrail", lambda **kw: ontology_routes.
                        GuardrailVerdict(auto_approvable=False, outcome="escalated"))
    proposed = owner.post("/ontology/proposals", json=_conflicting_proposal()).json()
    before = env["annex"].read_bytes()
    r = owner.post(f"/ontology/proposals/{proposed['id']}/approve", json={})
    assert r.status_code == 503, r.text
    assert r.json()["refusal"]["code"] == "PACK_WRITE_FAILED"
    assert env["annex"].read_bytes() == before
    clear_caches()
    assert set(active_vocabulary().packs) == {"workspace", "annex"}   # still builds


# --- finding 6: reactivating an identity-protected kind needs confirmation -------------------
def test_reactivating_a_subject_kind_needs_identity_confirmation(owner, env):
    from databridge_graph.packs import load_pack

    subject = sorted(load_pack("workspace").subject_kinds)[0]
    body = {"axis": "node", "kind": subject}
    assert owner.post("/ontology/kinds/deprecate", json={**body, "confirm_identity": True}
                      ).status_code == 200
    r = owner.post("/ontology/kinds/reactivate", json=body)
    assert r.status_code == 422, r.text
    assert r.json()["refusal"]["code"] == "KIND_NOT_EDITABLE"
    row = next(k for k in owner.get("/ontology").json()["node_kinds"] if k["kind"] == subject)
    assert row["deprecated"] is True                          # still deprecated
    ok = owner.post("/ontology/kinds/reactivate", json={**body, "confirm_identity": True})
    assert ok.status_code == 200, ok.text


# --- finding 7: subject declarations cannot be approved as ordinary nodes ---------------------
def test_a_stored_subject_declaring_proposal_cannot_be_approved(owner, env):
    record = {"id": "prop-0001", "pack": "workspace", "axis": "node", "name": "next_of_kin",
              "binds_to": "entity", "src": [], "dst": [], "description": "",
              "declares_subject": True, "status": "escalated", "origin": "agent",
              "proposed_by": "an adapter agent", "decided_by": None, "decided_at": None}
    env["state"].parent.mkdir(parents=True, exist_ok=True)
    env["state"].write_text(json.dumps({"proposals": [record]}), encoding="utf-8")
    before = env["ws"].read_bytes()
    r = owner.post("/ontology/proposals/prop-0001/approve", json={})
    assert r.status_code == 409, r.text
    assert env["ws"].read_bytes() == before


# --- finding 8: state is atomic and fails CLOSED ----------------------------------------------
def test_an_unreadable_state_file_fails_closed_and_is_never_overwritten(owner, env):
    env["state"].parent.mkdir(parents=True, exist_ok=True)
    env["state"].write_bytes(b'{"deprecated_kinds": [ {"pack": "workspace", "ax')
    damaged = env["state"].read_bytes()
    for method, path, body in (("GET", "/ontology", None),
                               ("POST", "/ontology/kinds/deprecate",
                                {"axis": "node", "kind": "document"}),
                               ("POST", "/ontology/proposals",
                                {"axis": "node", "name": "harbour",
                                 "binds_to": "entity", "proposed_by": "x"}),
                               ("PUT", "/ontology/auto_approve", {"enabled": True})):
        r = owner.request(method, path, json=body)
        assert r.status_code == 503, (path, r.text)
        assert r.json()["refusal"]["code"] == "ONTOLOGY_STATE_UNREADABLE", path
    assert env["state"].read_bytes() == damaged


def test_a_failed_state_write_leaves_the_previous_state_intact(owner, env, monkeypatch):
    from databridge_graph import ontology_state

    assert owner.put("/ontology/auto_approve", json={"enabled": False}).status_code == 200
    before = env["state"].read_bytes()

    def _boom(*a, **kw):
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(ontology_state.os, "replace", _boom)
    r = owner.post("/ontology/kinds/deprecate", json={"axis": "node", "kind": "document"})
    assert r.status_code == 503, r.text
    assert env["state"].read_bytes() == before
    assert not [p for p in env["state"].parent.iterdir() if p.suffix == ".tmp"]


# --- finding 9 + §5.8: attribution --------------------------------------------------------------
def test_a_decision_is_attributed_to_the_signed_in_principal_only(owner, env):
    proposed = _propose(owner)
    r = owner.post(f"/ontology/proposals/{proposed['id']}/reject",
                   json={"decided_by": "someone else"})
    assert r.status_code == 200, r.text
    assert r.json()["decided_by"] == OWNER
    assert r.json()["decided_by_label"] == "someone else"
    line = [a for a in _audit(env) if a["action"] == "reject_proposal"][-1]
    assert line["principal"] == OWNER and line["by"] == OWNER


def test_an_agent_origin_proposal_records_the_agent_and_model(agent, env):
    produced = {"agent": "adapter_agent:files", "model": "fake-model-r017",
                "task_class": "extract"}
    record = _propose(agent, origin="adapter_agent", produced_by=produced)
    assert record["produced_by"] == produced
    assert record["proposed_by_principal"] == NON_OWNER
    line = [a for a in _audit(env) if a["action"] == "propose"][-1]
    assert line["principal"] == NON_OWNER
    assert (line["agent"], line["model"]) == (produced["agent"], produced["model"])
    bad = agent.post("/ontology/proposals", json={
        "axis": "node", "name": "quay", "binds_to": "entity", "proposed_by": "x",
        "produced_by": {"agent": "a\nb"}})
    assert bad.status_code == 422 and bad.json()["refusal"]["code"] == "BAD_TEXT"

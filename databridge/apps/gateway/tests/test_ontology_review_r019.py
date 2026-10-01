"""Review R-019 (Codex gpt-5.6-sol high, batch 2, track ontology-r2): one test per real finding.

Every test runs against a PRIVATE pack tree and PRIVATE state/audit paths (the fixtures imported
from ``test_ontology_review_r017``) — the repo's own ``packs/`` and ``.databridge/`` are never
touched. Kind names and meta-kinds are derived from the pack under test and the meta-model.

1. guardrail vocabulary failures FAIL CLOSED (no fallback to another vocabulary)
2. validated evidence pointers are KEPT on the proposal, in the audit, and at approval
3. approval + pack application are crash-consistent (``applying`` → ``approved``, resumable)
4. a rename is ONE atomic change (and a crash between pack and state is resumable)
5. the audit journal: durable intent BEFORE a change, fsynced, linked by op_id
6. strict state validation (see also the graph package's test for extraction)
7. an unreadable deployment binding fails the commit
8. (graph package) atomic replacement keeps file metadata
10. a failed reload hook is not reported as success
11. approved-proposal provenance is keyed by (axis, name)
"""
from __future__ import annotations

import json

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import AUTH_HEADERS
from test_ontology_review_r017 import (  # noqa: F401 — fixtures are used by name
    GROUNDED, _app, _audit, _ordinary_meta, agent, env, owner)


def _state(env) -> dict:
    return json.loads(env["state"].read_text("utf-8"))


def _kinds(path, block="node_kinds") -> list[str]:
    return [e["name"] for e in yaml.safe_load(path.read_text("utf-8"))[block]]


# --- 1. the guardrail fails CLOSED ------------------------------------------------------------
def _conflicting_subject_setup(env):
    """Bound `annex` declares `person` as an ORDINARY node (under another meta-kind); the unbound
    `workspace` declares it a SUBJECT. Their union cannot build — the exact case the old fallback judged against the
    deployment vocabulary alone, where `person` is not a subject."""
    from databridge_graph.packs import build_vocabulary, clear_caches, load_pack

    from databridge_graph.meta import NodeMetaKind

    subject = sorted(load_pack("workspace").subject_kinds)[0]
    theirs = load_pack("workspace").node_kinds[subject].binds_to
    meta = next(m.value for m in NodeMetaKind if m is not theirs)
    annex = env["annex"].read_text("utf-8").replace(
        "node_kinds:\n",
        f"node_kinds:\n  - name: {subject}\n    binds_to: {meta}\n    description: plain.\n\n", 1)
    env["annex"].write_bytes(annex.encode("utf-8"))
    (env["root"] / "deployment.yaml").write_text("enabled_packs:\n  - annex\n", encoding="utf-8")
    clear_caches()
    try:
        build_vocabulary(("annex", "workspace"))
    except Exception:                                                 # noqa: BLE001
        pass
    else:                                                             # pragma: no cover
        pytest.skip("the packs merged, so the fallback path is not reachable here")
    return subject


def test_a_union_that_cannot_build_never_falls_back_to_a_vocabulary_without_the_subject(env):
    from databridge_gateway.ontology_routes import _evidence_pointers, guardrail
    from databridge_graph.meta import EdgeMetaKind, ATTESTED_META_KINDS, CLAIM_META_KINDS
    subject = _conflicting_subject_setup(env)
    locked = {m.value for m in ATTESTED_META_KINDS} | {m.value for m in CLAIM_META_KINDS}
    metas = [m.value for m in EdgeMetaKind if m.value not in locked]
    ev = _evidence_pointers(tuple(GROUNDED))
    verdicts = [guardrail(axis="edge", binds_to=m, src=(subject,), dst=(subject,),
                          name="tied_to", pack="workspace", proposed_by="t", evidence=ev)
                for m in metas]
    assert verdicts, "no ordinary edge meta-kind — the test would prove nothing"
    assert not any(v.auto_approvable for v in verdicts), [v.outcome for v in verdicts]
    assert all("subject_touching" in v.classes for v in verdicts
               if v.outcome == "escalated")


def test_an_unreadable_binding_raises_instead_of_answering(env, monkeypatch):
    from databridge_gateway import ontology_routes
    from databridge_graph import packs

    def _boom(*a, **k):
        raise packs.PackError("binding unreadable (simulated)")

    monkeypatch.setattr(packs, "configured_packs", _boom)
    with pytest.raises(ontology_routes.GuardrailVocabularyUnavailable):
        ontology_routes.guardrail(axis="node", binds_to="entity", name="harbour",
                                  pack="workspace", proposed_by="t")


def test_subjects_that_cannot_be_established_do_not_lift_the_identity_gate(owner, env,
                                                                            monkeypatch):
    """Deprecating a relation used to compute its subjects with `except: subjects = set()` — an
    unavailable vocabulary read as "touches no subject" and skipped confirm_identity."""
    from databridge_gateway import ontology_routes

    def _boom(pack):
        raise ontology_routes.GuardrailVocabularyUnavailable("simulated")

    monkeypatch.setattr(ontology_routes, "_guardrail_vocabs", _boom)
    edge = _kinds(env["ws"], "edge_kinds")[0]
    r = owner.post("/ontology/kinds/deprecate", json={"axis": "edge", "kind": edge})
    assert r.status_code == 503, r.text
    assert r.json()["refusal"]["code"] == "GUARDRAIL_VOCABULARY_UNAVAILABLE"
    assert "deprecated_kinds" not in (_state(env) if env["state"].exists() else {}) or not \
        _state(env)["deprecated_kinds"]


# --- 2. evidence is kept ----------------------------------------------------------------------
def test_validated_evidence_is_stored_served_audited_and_used_at_approval(owner, agent, env,
                                                                           monkeypatch):
    from databridge_core import SourcePointer
    from databridge_gateway import ontology_routes

    expected = [SourcePointer.model_validate(p).model_dump(mode="json") for p in GROUNDED]
    r = agent.post("/ontology/proposals", json={
        "axis": "node", "name": "harbour", "binds_to": _ordinary_meta(),
        "proposed_by": "an adapter agent", "evidence": GROUNDED})
    assert r.status_code == 200, r.text
    record = r.json()
    assert record["evidence"] == expected and expected
    pending = owner.get("/ontology").json()["pending_proposals"]
    assert [p["evidence"] for p in pending if p["id"] == record["id"]] == [expected]
    line = [a for a in _audit(env) if a["action"] == "propose"][-1]
    assert line["evidence"] == expected

    seen: list[tuple] = []
    real = ontology_routes.guardrail

    def _spy(**kw):
        seen.append(tuple(kw.get("evidence") or ()))
        return real(**kw)

    monkeypatch.setattr(ontology_routes, "guardrail", _spy)
    ok = owner.post(f"/ontology/proposals/{record['id']}/approve", json={})
    assert ok.status_code == 200, ok.text
    assert seen and [p.model_dump(mode="json") for p in seen[-1]] == expected


# --- 3. approval is crash-consistent ----------------------------------------------------------
def _pending(client) -> dict:
    r = client.post("/ontology/proposals", json={
        "axis": "node", "name": "harbour", "binds_to": _ordinary_meta(),
        "proposed_by": "an adapter agent", "evidence": GROUNDED})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"
    return r.json()


def _status_during_write(env, monkeypatch) -> list[str]:
    """Record the proposal's STORED status at the instant the pack write runs."""
    from databridge_gateway import ontology_routes

    real = ontology_routes.append_kind_to_pack
    seen: list[str] = []

    def _spy(*a, **k):
        seen.append(_state(env)["proposals"][-1]["status"])
        return real(*a, **k)

    monkeypatch.setattr(ontology_routes, "append_kind_to_pack", _spy)
    return seen


def test_an_approval_is_durable_as_applying_before_the_pack_is_touched(owner, env, monkeypatch):
    """A crash during the write therefore leaves `applying` (resumable), never `approved` with
    no kind in the file — which could not be approved again (PROPOSAL_ALREADY_DECIDED)."""
    proposed = _pending(owner)
    seen = _status_during_write(env, monkeypatch)
    r = owner.post(f"/ontology/proposals/{proposed['id']}/approve", json={})
    assert r.status_code == 200, r.text
    assert seen == ["applying"]
    assert r.json()["status"] == "approved"


def test_an_auto_approved_proposal_is_applying_until_its_write_verifies(owner, env, monkeypatch):
    assert owner.put("/ontology/auto_approve", json={"enabled": True}).status_code == 200
    seen = _status_during_write(env, monkeypatch)
    r = owner.post("/ontology/proposals", json={
        "axis": "node", "name": "harbour", "binds_to": _ordinary_meta(),
        "proposed_by": "the owner", "origin": "owner", "evidence": GROUNDED})
    assert r.status_code == 200, r.text
    assert seen == ["applying"] and r.json()["status"] == "approved"


def _crashed_mid_approval(owner, env, *, written: bool) -> dict:
    """The on-disk state a crash leaves: the decision stored as `applying`, and the pack write
    either not yet made or already committed."""
    from databridge_gateway import ontology_routes

    proposed = _pending(owner)
    state = _state(env)
    rec = next(p for p in state["proposals"] if p["id"] == proposed["id"])
    rec.update(status="applying", decided_by="someone-earlier", decided_at="2026-10-01T00:00:00")
    env["state"].write_text(json.dumps(state), encoding="utf-8")
    if written:
        ontology_routes.append_kind_to_pack(
            "workspace", "node", {"name": rec["name"], "binds_to": rec["binds_to"]},
            by="someone-earlier", at="2026-10-01T00:00:00", root=env["root"])
    return rec


def test_a_crash_before_the_pack_write_is_resumed_by_approving_again(owner, env):
    rec = _crashed_mid_approval(owner, env, written=False)
    assert rec["name"] not in _kinds(env["ws"])
    assert rec["id"] in {p["id"] for p in owner.get("/ontology").json()["pending_proposals"]}
    r = owner.post(f"/ontology/proposals/{rec['id']}/approve", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    assert r.json()["decided_by"] == "someone-earlier"       # the decider of record is kept
    assert _kinds(env["ws"]).count(rec["name"]) == 1


def test_a_crash_after_the_pack_write_is_resumed_without_writing_twice(owner, env):
    rec = _crashed_mid_approval(owner, env, written=True)
    written = env["ws"].read_bytes()
    r = owner.post(f"/ontology/proposals/{rec['id']}/approve", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved" and r.json()["already_present"] is True
    assert env["ws"].read_bytes() == written                 # idempotent: nothing re-written


# --- 4. rename is one atomic change -----------------------------------------------------------
def _second_relation_on(env, node: str) -> None:
    """Give the pack a second relation touching ``node`` so a rename has two to widen."""
    text = env["ws"].read_text("utf-8")
    edge_meta = yaml.safe_load(text)["edge_kinds"][0]["binds_to"]
    text = text.replace("\nsubject_kinds:", (
        f"\n  - name: supplies\n    binds_to: {edge_meta}\n    src: [{node}]\n"
        f"    dst: [document]\n    description: second relation.\n\nsubject_kinds:"), 1)
    env["ws"].write_bytes(text.encode("utf-8"))


def _renamable(env) -> tuple[str, list[str]]:
    from databridge_graph.packs import clear_caches, load_pack

    clear_caches()
    pack = load_pack("workspace")
    node = next(n for n in sorted(pack.node_kinds) if n not in pack.subject_kinds
                and sum(n in (*e.src, *e.dst) for e in pack.edge_kinds.values()) >= 2)
    return node, [e for e, d in sorted(pack.edge_kinds.items()) if node in (*d.src, *d.dst)]


def test_a_rename_that_fails_on_a_later_relation_changes_nothing(owner, env, monkeypatch):
    from databridge_gateway import ontology_routes

    _second_relation_on(env, "organisation")
    node, relations = _renamable(env)
    assert len(relations) >= 2
    before = env["ws"].read_bytes()
    real = ontology_routes._rewrite_list_key
    calls = {"n": 0}

    def _fail_on_second_relation(lines, entry, key, values):
        calls["n"] += 1
        if calls["n"] > 2:                                   # relation 1 = 2 calls (src, dst)
            raise ontology_routes.PackWriteError("second relation cannot be widened (simulated)")
        return real(lines, entry, key, values)

    monkeypatch.setattr(ontology_routes, "_rewrite_list_key", _fail_on_second_relation)
    r = owner.post("/ontology/kinds/rename", json={
        "axis": "node", "kind": node, "new_name": "company", "confirm_identity": True})
    assert r.status_code == 422, r.text
    assert r.json()["refusal"]["code"] == "PACK_WRITE_FAILED"
    assert env["ws"].read_bytes() == before                  # no new kind, no widened relation
    row = next(k for k in owner.get("/ontology").json()["node_kinds"] if k["kind"] == node)
    assert row["deprecated"] is False


def test_a_rename_interrupted_between_pack_and_state_is_finished_by_repeating_it(owner, env,
                                                                                monkeypatch):
    from databridge_gateway import ontology_routes
    from databridge_graph.ontology_state import OntologyStateError

    _second_relation_on(env, "organisation")
    node, relations = _renamable(env)
    real = ontology_routes.save_state

    def _refuse_the_deprecation(state):
        if any(d.get("renamed_to") for d in state.get("deprecated_kinds", [])):
            raise OntologyStateError("disk full (simulated)")
        real(state)

    monkeypatch.setattr(ontology_routes, "save_state", _refuse_the_deprecation)
    body = {"axis": "node", "kind": node, "new_name": "company", "confirm_identity": True}
    r = owner.post("/ontology/kinds/rename", json=body)
    assert r.status_code == 503, r.text
    assert r.json()["refusal"]["code"] == "RENAME_INCOMPLETE"
    view = owner.get("/ontology").json()
    assert [op["op_id"] for op in view["incomplete_operations"]] == [r.json()["refusal"]["op_id"]]
    written = env["ws"].read_bytes()
    monkeypatch.setattr(ontology_routes, "save_state", real)
    ok = owner.post("/ontology/kinds/rename", json=body)
    assert ok.status_code == 200, ok.text
    assert env["ws"].read_bytes() == written                 # the pack was not written again
    view = owner.get("/ontology").json()
    assert view["incomplete_operations"] == []
    assert next(k for k in view["node_kinds"] if k["kind"] == node)["deprecated"] is True
    assert _kinds(env["ws"]).count("company") == 1
    for rel in relations:
        entry = next(e for e in view["edge_kinds"] if e["kind"] == rel)
        assert "company" in entry["from"] + entry["to"]


# --- 5. the audit journal ---------------------------------------------------------------------
def test_an_unwritable_audit_changes_nothing(owner, env, monkeypatch):
    blocked = env["state"].parent / "audit_is_a_directory"
    blocked.mkdir(parents=True)
    monkeypatch.setenv("WALNUT_ONTOLOGY_AUDIT_PATH", str(blocked))
    before = env["ws"].read_bytes()
    r = owner.post("/ontology/kinds", json={"axis": "node", "name": "harbour",
                                            "binds_to": _ordinary_meta()})
    assert r.status_code == 503, r.text
    assert r.json()["refusal"]["code"] == "AUDIT_UNAVAILABLE"
    assert env["ws"].read_bytes() == before


def test_audit_lines_are_fsynced(env, monkeypatch):
    from databridge_gateway import ontology_routes

    calls: list[int] = []
    real = ontology_routes.os.fsync
    monkeypatch.setattr(ontology_routes.os, "fsync", lambda fd: (calls.append(fd), real(fd)))
    ontology_routes.append_audit({"action": "probe", "pack": "workspace"})
    assert calls


def test_an_intent_without_a_completion_is_served_as_incomplete(owner, env):
    from databridge_gateway import ontology_routes

    op = ontology_routes.audit_intent("define", pack="workspace", kind="harbour")
    entries = owner.get("/ontology/audit").json()["entries"]
    assert [(e["op_id"], e.get("incomplete")) for e in entries] == [(op, True)]


# --- 6. strict state (gateway side) -----------------------------------------------------------
@pytest.mark.parametrize("state", [
    {"auto_approve": {"enabled": "false"}},
    {"deprecated_kinds": [{"pack": "workspace", "axis": "node"}]},
    {"deprecated_kinds": [{"pack": "workspace", "axis": "node", "kind": "document",
                           "reactivated": "no"}]},
    {"proposals": [{"id": "prop-0001", "status": "approved-ish", "pack": "workspace",
                    "axis": "node", "name": "x", "binds_to": "entity"}]},
    {"proposals": [{"id": "p", "status": "pending", "pack": "workspace", "axis": "node",
                    "name": "x", "binds_to": "entity"}] * 2},
])
def test_a_wrongly_typed_state_record_fails_closed(owner, env, state):
    env["state"].parent.mkdir(parents=True, exist_ok=True)
    env["state"].write_text(json.dumps(state), encoding="utf-8")
    damaged = env["state"].read_bytes()
    r = owner.get("/ontology")
    assert r.status_code == 503, r.text
    assert r.json()["refusal"]["code"] == "ONTOLOGY_STATE_UNREADABLE"
    assert owner.put("/ontology/auto_approve", json={"enabled": False}).status_code == 503
    assert env["state"].read_bytes() == damaged


def test_the_real_operator_state_shape_still_validates(env):
    """Control: records exactly as the gateway writes them pass (so the check is not vacuous
    in the other direction — refusing everything)."""
    with TestClient(_app(), headers=AUTH_HEADERS) as c:
        assert c.put("/ontology/auto_approve", json={"enabled": False}).status_code == 200
        assert c.post("/ontology/kinds/deprecate",
                      json={"axis": "node", "kind": "document"}).status_code == 200
        assert c.get("/ontology").status_code == 200


# --- 7. an unreadable binding fails the commit ------------------------------------------------
def test_a_binding_that_cannot_be_read_fails_the_commit_and_restores(env, monkeypatch):
    from databridge_gateway import ontology_routes
    from databridge_graph import packs

    before = env["ws"].read_bytes()

    def _boom(*a, **k):
        raise packs.PackError("deployment.yaml half-written (simulated)")

    monkeypatch.setattr(packs, "configured_packs", _boom)
    with pytest.raises(ontology_routes.PackWriteError):
        ontology_routes.owner_add_entries(
            "workspace", [("node_kinds", {"name": "harbour", "binds_to": _meta_unpatched()})],
            comment="probe", root=env["root"])
    assert env["ws"].read_bytes() == before


def _meta_unpatched() -> str:
    from databridge_graph.meta import NodeMetaKind

    return next(iter(NodeMetaKind)).value


# --- 10. a failed reload is not success -------------------------------------------------------
def test_a_failing_reload_hook_is_reported_not_swallowed(env):
    app = _app()

    def _broken():
        raise RuntimeError("store would not reopen (simulated)")

    app.state.reload_graph = _broken
    with TestClient(app, headers=AUTH_HEADERS) as c:
        r = c.post("/ontology/kinds", json={"axis": "node", "name": "harbour",
                                            "binds_to": _ordinary_meta()})
    assert r.status_code == 503, r.text
    body = r.json()["refusal"]
    assert body["code"] == "VOCABULARY_RELOAD_FAILED"
    assert body["committed"] is True and body["result"]["kind"] == "harbour"
    assert "harbour" in _kinds(env["ws"])                   # honest: it WAS committed


# --- 11. provenance keyed by (axis, name) -----------------------------------------------------
def test_approving_a_node_does_not_mark_a_same_named_edge_as_proposed(env):
    from databridge_gateway.ontology_routes import _pack_view

    edge = _kinds(env["ws"], "edge_kinds")[0]
    state = {"proposals": [{"id": "prop-0001", "status": "approved", "pack": "workspace",
                            "axis": "node", "name": edge}]}
    view = _pack_view("workspace", state)
    row = next(e for e in view["edge_kinds"] if e["kind"] == edge)
    assert row["source"] == "predefined"
    # Control: an approved EDGE of that name is marked.
    state["proposals"][0]["axis"] = "edge"
    row = next(e for e in _pack_view("workspace", state)["edge_kinds"] if e["kind"] == edge)
    assert row["source"] == "agent-proposed"

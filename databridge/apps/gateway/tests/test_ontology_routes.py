"""`/ontology` — the pack's node/edge library, the growth proposals, and the refusal.

**The test this module exists for is `test_a_determination_kind_can_never_be_auto_approved`.**
`PHASE_PLAN.md` Phase 4 gives the growth protocol a toggle that disables the human gate. Two
classes of proposal must sit outside that toggle whatever it is set to, and the refusal has to be
at the API boundary — a guardrail in a React component is a guardrail a `curl` walks around:

* a **determination kind** — an edge binding to a meta-kind in `ATTESTED_META_KINDS`, the shape an
  identity determination is written on;
* a **subject-touching kind** — anything naming a declared subject (spine D4b) at either end, or
  asking to be one.

Everything here runs against a COPY of a pack in `tmp_path`, so an approval genuinely writes YAML
and the repo's own packs are never touched.
"""
from __future__ import annotations

import shutil

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import AUTH_HEADERS, TEST_TOKENS

#: The principal `AUTH_HEADERS` authenticates as — DERIVED from the conftest token directory, never
#: spelled here. It is configured as the ontology owner in the fixture (review R-017 finding 2:
#: owners come from operator configuration, never from a request body).
OWNER = TEST_TOKENS[AUTH_HEADERS["Authorization"].split(" ", 1)[1]]
from databridge_gateway.ontology_routes import register_ontology_routes


@pytest.fixture()
def packs(tmp_path, monkeypatch):
    """A private pack tree: the real healthcare pack copied, bound alone."""
    from databridge_graph.packs import DEFAULT_PACKS_ROOT, clear_caches

    root = tmp_path / "packs"
    (root / "healthcare").mkdir(parents=True)
    for name in ("ontology.yaml", "purposes.yaml"):
        shutil.copy(DEFAULT_PACKS_ROOT / "healthcare" / name, root / "healthcare" / name)
    (root / "deployment.yaml").write_text("enabled_packs:\n  - healthcare\n", encoding="utf-8")
    monkeypatch.setenv("DATABRIDGE_PACKS_ROOT", str(root))
    monkeypatch.setenv("WALNUT_ONTOLOGY_STATE_PATH", str(tmp_path / "ontology_state.json"))
    monkeypatch.setenv("WALNUT_INGEST_DIR", str(tmp_path / "ingest"))
    monkeypatch.setenv("WALNUT_ONTOLOGY_OWNERS", OWNER)
    monkeypatch.delenv("WALNUT_ONTOLOGY_AUDIT_PATH", raising=False)
    monkeypatch.delenv("DATABRIDGE_PACKS", raising=False)
    clear_caches()
    yield root
    clear_caches()


@pytest.fixture()
def client(packs):
    app = FastAPI()
    register_ontology_routes(app)
    # Every write route requires an authenticated principal (R-015); this client carries one.
    with TestClient(app, headers=AUTH_HEADERS) as c:
        yield c


def _ontology(client):
    r = client.get("/ontology")
    assert r.status_code == 200, r.text
    return r.json()


# --- 1. the library reads the pack ---------------------------------------------------
def test_the_library_is_the_pack_and_not_a_copy_of_it(client, packs):
    from databridge_graph.packs import load_pack

    body = _ontology(client)
    pack = load_pack("healthcare", packs)
    assert {k["kind"] for k in body["node_kinds"]} == set(pack.node_kinds)
    assert {k["kind"] for k in body["edge_kinds"]} == set(pack.edge_kinds)
    assert body["version"] == pack.version


def test_a_subject_kind_is_not_editable_and_shows_the_identifiers_it_is_attested_by(client):
    body = _ontology(client)
    subjects = [k for k in body["node_kinds"] if k["subject"]]
    assert subjects, "the pack under test declares a subject; without one this proves nothing"
    for kind in subjects:
        assert kind["editable"] is False
        assert kind["attestation"]
        assert "attested identifiers" in kind["identity"]


def test_a_determination_edge_is_marked_as_carrying_the_identity_rule(client):
    from databridge_graph.meta import ATTESTED_META_KINDS

    attested = {m.value for m in ATTESTED_META_KINDS}
    body = _ontology(client)
    determinations = [e for e in body["edge_kinds"] if e["binds_to"] in attested]
    assert determinations
    for edge in determinations:
        assert edge["editable"] is False
        assert edge["carries_identity_rule"] is True


def test_the_payload_says_what_auto_approve_can_never_cover(client):
    body = _ontology(client)
    assert body["auto_approve"] == {"enabled": False, "set_by": None, "set_at": None}
    assert len(body["auto_approve_never_covers"]) == 3
    assert body["pending_proposals"] == []


# --- 2. THE refusal ------------------------------------------------------------------
def _enable_auto_approve(client):
    r = client.put("/ontology/auto_approve", json={"enabled": True, "set_by": "ops.lead"})
    assert r.status_code == 200, r.text
    assert r.json()["auto_approve"]["enabled"] is True


def test_a_determination_kind_can_never_be_auto_approved(client, packs):
    """The Done-when. Auto-approve is ON, and the proposal is escalated anyway."""
    from databridge_graph.meta import ATTESTED_META_KINDS

    _enable_auto_approve(client)
    meta = sorted(m.value for m in ATTESTED_META_KINDS)[0]
    r = client.post("/ontology/proposals", json={
        "axis": "edge", "name": "settles_identity_of", "binds_to": meta,
        "src": ["document"], "dst": ["visit"], "proposed_by": "core_banking adapter agent"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "escalated"
    assert body["auto_approvable"] is False
    assert "determination" in body["guardrail_classes"]
    assert body["auto_approve_was_enabled"] is True
    assert body["decided_by"] is None
    assert meta in body["escalation_reason"]

    # And nothing reached the pack file.
    text = (packs / "healthcare" / "ontology.yaml").read_text(encoding="utf-8")
    assert "settles_identity_of" not in text


def test_a_subject_touching_kind_can_never_be_auto_approved(client, packs):
    from databridge_graph.packs import active_vocabulary

    _enable_auto_approve(client)
    subject = sorted(active_vocabulary().subject_kinds)[0]
    r = client.post("/ontology/proposals", json={
        "axis": "edge", "name": "shares_household_with", "binds_to": "references",
        "src": [subject], "dst": [subject], "proposed_by": "an adapter agent"})
    body = r.json()
    assert body["status"] == "escalated"
    assert "subject_touching" in body["guardrail_classes"]
    assert subject in body["escalation_reason"]
    assert "shares_household_with" not in (packs / "healthcare" / "ontology.yaml").read_text(
        encoding="utf-8")


def test_a_node_asking_to_be_a_subject_can_never_be_auto_approved(client, packs):
    """Stronger than escalation since R-017 finding 7: a proposal cannot carry a subject's
    identifiers/attestation/grant type, so approving one could only write an UNPROTECTED node.
    It is refused and pointed at the owner's identity-confirmed subject declaration."""
    _enable_auto_approve(client)
    before = (packs / "healthcare" / "ontology.yaml").read_bytes()
    r = client.post("/ontology/proposals", json={
        "axis": "node", "name": "next_of_kin", "binds_to": "entity",
        "declares_subject": True, "proposed_by": "an adapter agent"})
    assert r.status_code == 422, r.text
    assert r.json()["refusal"]["code"] == "SUBJECT_DECLARATION_NOT_PROPOSABLE"
    assert _ontology(client)["pending_proposals"] == []
    assert (packs / "healthcare" / "ontology.yaml").read_bytes() == before


def test_a_claim_kind_can_never_be_auto_approved(client):
    from databridge_graph.meta import CLAIM_META_KINDS

    _enable_auto_approve(client)
    meta = sorted(m.value for m in CLAIM_META_KINDS)[0]
    r = client.post("/ontology/proposals", json={
        "axis": "edge", "name": "possibly_concerns", "binds_to": meta,
        "src": ["document"], "dst": ["visit"], "proposed_by": "an adapter agent"})
    body = r.json()
    assert body["status"] == "escalated"
    assert "identity_claim" in body["guardrail_classes"]


def test_the_guardrail_is_computed_from_the_meta_model_not_from_a_list():
    """If the meta-model grows a second attested kind, the refusal must cover it the same day."""
    from databridge_graph.meta import ATTESTED_META_KINDS
    from databridge_gateway.ontology_routes import guardrail

    for meta in ATTESTED_META_KINDS:
        verdict = guardrail(axis="edge", binds_to=meta.value)
        assert verdict.auto_approvable is False
        assert "determination" in verdict.classes


# --- 3. the ordinary path ------------------------------------------------------------
def test_an_ordinary_proposal_waits_for_a_human_while_the_gate_is_on(client, packs):
    """`auto_approvable: True` and `status: pending` — approvable in principle, waiting in fact.

    The proposal must be GROUNDED to reach that state. An evidence-free proposal is `escalated`
    instead, which is a different answer to a different question: the gate being on is why an
    approvable proposal waits; missing evidence is why an unapprovable one cannot proceed at all.
    """
    r = client.post("/ontology/proposals", json={
        "axis": "node", "name": "referral_source", "binds_to": "organisation",
        "description": "Where a document originated.", "proposed_by": "an adapter agent",
        "evidence": GROUNDED_EVIDENCE})
    body = r.json()
    assert body["status"] == "pending"
    assert body["auto_approvable"] is True
    assert "referral_source" not in (packs / "healthcare" / "ontology.yaml").read_text(
        encoding="utf-8")
    assert _ontology(client)["pending_proposals"][0]["id"] == body["id"]


def test_an_approval_names_a_human_and_writes_the_pack_yaml(client, packs):
    proposed = client.post("/ontology/proposals", json={
        "axis": "node", "name": "referral_source", "binds_to": "organisation",
        "description": "Where a document originated.", "proposed_by": "an adapter agent"}).json()

    # The approver of record is the SIGNED-IN principal (R-017 finding 9); the body's name is a
    # display label kept beside it, and it cannot name someone else as the decider.
    approved = client.post(f"/ontology/proposals/{proposed['id']}/approve",
                           json={"decided_by": "N. Aisyah", "reason": "matches the source"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["decided_by"] == OWNER
    assert approved.json()["decided_by_label"] == "N. Aisyah"

    path = packs / "healthcare" / "ontology.yaml"
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    added = [n for n in parsed["node_kinds"] if n["name"] == "referral_source"]
    assert added and added[0]["binds_to"] == "organisation"
    # Attribution goes into the file, not only into the operator state — the principal, never the
    # request-supplied label (untrusted text is never written into YAML).
    assert f"approved by {OWNER}" in path.read_text(encoding="utf-8")
    assert "N. Aisyah" not in path.read_text(encoding="utf-8")
    # The comments that carry the pack's reasoning survive the write.
    assert "# packs/healthcare" in path.read_text(encoding="utf-8")
    assert "referral_source" in {k["kind"] for k in _ontology(client)["node_kinds"]}



#: Evidence for a proposal that is genuinely approvable. A growth proposal carrying no pointers
#: into the data that motivated it is escalated by `growth.evaluate_proposal`
#: (`Trigger.EVIDENCE_UNGROUNDED`) — an ungrounded schema change is one no reviewer can check.
#:
#: These two tests used to pass WITHOUT it, because the gateway's own guardrail reimplemented the
#: escalation rules and checked three of the engine's ten triggers. They were asserting the
#: permissiveness, not the policy. The guardrail now delegates, so a proposal must be grounded to
#: be approvable — which is the behaviour the growth protocol was designed to have.
GROUNDED_EVIDENCE = [{
    "source_id": "core_banking",
    "kind": "table_cell",
    "table": "party",
    "column": "referral_channel",
    "row_id": "P-000123",
    "trust": "trusted",
}]


def test_an_auto_approved_ordinary_proposal_is_attributed_to_whoever_disabled_the_gate(client,
                                                                                       packs):
    _enable_auto_approve(client)
    body = client.post("/ontology/proposals", json={
        "axis": "node", "name": "referral_source", "binds_to": "organisation",
        "proposed_by": "the owner, on /ontology", "origin": "owner",
        "evidence": GROUNDED_EVIDENCE}).json()
    assert body["status"] == "approved"
    assert body["decided_by"] == OWNER               # the owner principal who proposed it
    assert OWNER in body["decision_basis"]           # under the gate that principal opened
    assert "referral_source" in (packs / "healthcare" / "ontology.yaml").read_text(encoding="utf-8")


def test_turning_the_gate_off_records_who_turned_it_off(client):
    """The setter of record is the signed-in principal, whatever label the body carries."""
    r = client.put("/ontology/auto_approve", json={"enabled": True, "set_by": "someone else"})
    assert r.status_code == 200, r.text
    assert r.json()["auto_approve"]["set_by"] == OWNER
    assert r.json()["auto_approve"]["set_by_label"] == "someone else"
    bare = client.put("/ontology/auto_approve", json={"enabled": False})
    assert bare.json()["auto_approve"]["set_by"] == OWNER


# --- 4. refusals ---------------------------------------------------------------------
def test_a_proposal_nobody_can_be_attributed_to_is_refused(client):
    r = client.post("/ontology/proposals", json={
        "axis": "node", "name": "referral_source", "binds_to": "organisation"})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "PROPOSER_REQUIRED"


def test_a_meta_kind_the_model_does_not_have_is_refused(client):
    r = client.post("/ontology/proposals", json={
        "axis": "node", "name": "thing", "binds_to": "invented_meta_kind",
        "proposed_by": "an adapter agent"})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "BAD_META_KIND"


def test_a_kind_the_pack_already_declares_is_refused(client):
    existing = _ontology(client)["node_kinds"][0]["kind"]
    r = client.post("/ontology/proposals", json={
        "axis": "node", "name": existing, "binds_to": "entity",
        "proposed_by": "an adapter agent"})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "KIND_ALREADY_DECLARED"


def test_a_kind_name_that_would_make_an_ambiguous_node_id_is_refused(client):
    r = client.post("/ontology/proposals", json={
        "axis": "node", "name": "Referral Source", "binds_to": "entity",
        "proposed_by": "an adapter agent"})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "BAD_KIND_NAME"


def test_a_proposal_cannot_be_decided_twice(client):
    proposed = client.post("/ontology/proposals", json={
        "axis": "node", "name": "referral_source", "binds_to": "organisation",
        "proposed_by": "an adapter agent"}).json()
    client.post(f"/ontology/proposals/{proposed['id']}/approve", json={"decided_by": "N. Aisyah"})
    again = client.post(f"/ontology/proposals/{proposed['id']}/reject",
                        json={"decided_by": "someone else"})
    assert again.status_code == 422
    assert again.json()["refusal"]["code"] == "PROPOSAL_ALREADY_DECIDED"


def test_an_escalated_proposal_is_still_approvable_by_a_named_human(client, packs):
    """Escalation is a gate, not a ban — otherwise the growth protocol has no human path."""
    from databridge_graph.packs import active_vocabulary

    subject = sorted(active_vocabulary().subject_kinds)[0]
    proposed = client.post("/ontology/proposals", json={
        "axis": "edge", "name": "shares_household_with", "binds_to": "references",
        "src": [subject], "dst": [subject], "proposed_by": "an adapter agent"}).json()
    assert proposed["status"] == "escalated"
    approved = client.post(f"/ontology/proposals/{proposed['id']}/approve",
                           json={"decided_by": "N. Aisyah"})
    assert approved.status_code == 200, approved.text
    assert "shares_household_with" in (packs / "healthcare" / "ontology.yaml").read_text(
        encoding="utf-8")


# --- 5. listing packs, and the meta-kind catalogue -----------------------------------
def test_ontology_packs_lists_what_is_on_disk_and_what_is_bound(client, packs):
    r = client.get("/ontology/packs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "healthcare" in body["packs"]
    assert body["enabled"] == ["healthcare"]
    assert body["default"] == "healthcare"


def test_ontology_serves_the_meta_kind_catalogue_computed_from_the_meta_model(client):
    from databridge_graph.meta import ATTESTED_META_KINDS, CLAIM_META_KINDS

    body = _ontology(client)
    edge_meta = {m["value"]: m for m in body["meta_kinds"]["edge"]}
    for meta in ATTESTED_META_KINDS:
        assert edge_meta[meta.value]["attested"] is True
    for meta in CLAIM_META_KINDS:
        assert edge_meta[meta.value]["claim"] is True
    node_meta = {m["value"]: m for m in body["meta_kinds"]["node"]}
    assert node_meta["entity"]["subject_eligible"] is True
    assert node_meta["document"]["subject_eligible"] is False


# --- 6. removal: deprecate, never delete ----------------------------------------------
def test_a_locked_kind_cannot_be_deprecated(client):
    body = _ontology(client)
    subject = next(k for k in body["node_kinds"] if k["subject"])
    r = client.post("/ontology/kinds/deprecate", json={
        "axis": "node", "kind": subject["kind"], "by": "N. Aisyah"})
    assert r.status_code == 422, r.text
    assert r.json()["refusal"]["code"] == "KIND_NOT_EDITABLE"


def test_deprecating_an_ordinary_kind_requires_a_named_human_and_never_touches_the_pack_file(
        client, packs):
    body = _ontology(client)
    ordinary = next(k for k in body["node_kinds"] if k["editable"] and not k["subject"])
    before = (packs / "healthcare" / "ontology.yaml").read_text(encoding="utf-8")

    r = client.post("/ontology/kinds/deprecate",
                    json={"axis": "node", "kind": ordinary["kind"], "by": "N. Aisyah",
                          "reason": "superseded by a better kind"})
    assert r.status_code == 200, r.text
    assert r.json()["deprecated"] is True
    # The named human is the signed-in principal; the body's name is only a label.
    assert r.json()["deprecated_by"] == OWNER
    assert r.json()["deprecated_by_label"] == "N. Aisyah"

    # The pack file is byte-for-byte unchanged: deprecation is operator state, not a pack edit.
    assert (packs / "healthcare" / "ontology.yaml").read_text(encoding="utf-8") == before

    after = _ontology(client)
    entry = next(k for k in after["node_kinds"] if k["kind"] == ordinary["kind"])
    assert entry["deprecated"] is True
    assert entry["deprecated_by"] == OWNER
    # It is still declared — reading it back still works, which is the entire point.
    assert entry["kind"] in {k["kind"] for k in after["node_kinds"]}

    twice = client.post("/ontology/kinds/deprecate",
                        json={"axis": "node", "kind": ordinary["kind"], "by": "someone else"})
    assert twice.status_code == 422
    assert twice.json()["refusal"]["code"] == "ALREADY_DEPRECATED"


def test_a_proposal_cannot_extend_a_deprecated_node_kind(client):
    body = _ontology(client)
    ordinary = next(k for k in body["node_kinds"] if k["editable"] and not k["subject"])
    client.post("/ontology/kinds/deprecate",
                json={"axis": "node", "kind": ordinary["kind"], "by": "N. Aisyah"})

    r = client.post("/ontology/proposals", json={
        "axis": "edge", "name": "touches_deprecated", "binds_to": "references",
        "src": [ordinary["kind"]], "dst": [ordinary["kind"]], "proposed_by": "an adapter agent"})
    assert r.status_code == 422, r.text
    assert r.json()["refusal"]["code"] == "ENDPOINT_DEPRECATED"


def test_reactivating_a_deprecated_kind_lifts_the_refusal(client):
    body = _ontology(client)
    ordinary = next(k for k in body["node_kinds"] if k["editable"] and not k["subject"])
    client.post("/ontology/kinds/deprecate",
                json={"axis": "node", "kind": ordinary["kind"], "by": "N. Aisyah"})

    r = client.post("/ontology/kinds/reactivate",
                    json={"axis": "node", "kind": ordinary["kind"], "by": "N. Aisyah"})
    assert r.status_code == 200, r.text
    assert r.json()["deprecated"] is False
    assert r.json()["reactivated_by"] == OWNER

    after = _ontology(client)
    entry = next(k for k in after["node_kinds"] if k["kind"] == ordinary["kind"])
    assert entry["deprecated"] is False

    not_deprecated_again = client.post("/ontology/kinds/reactivate",
                                       json={"axis": "node", "kind": ordinary["kind"],
                                             "by": "N. Aisyah"})
    assert not_deprecated_again.status_code == 422
    assert not_deprecated_again.json()["refusal"]["code"] == "NOT_DEPRECATED"


def test_deprecating_an_unknown_kind_is_refused(client):
    r = client.post("/ontology/kinds/deprecate",
                    json={"axis": "node", "kind": "not_a_real_kind", "by": "N. Aisyah"})
    assert r.status_code == 404
    assert r.json()["refusal"]["code"] == "UNKNOWN_KIND"


def test_a_write_that_breaks_the_pack_is_rolled_back(client, packs, monkeypatch):
    """A half-written ontology is a vocabulary the engine cannot bind."""
    from databridge_gateway import ontology_routes

    path = packs / "healthcare" / "ontology.yaml"
    before = path.read_text(encoding="utf-8")

    def _broken(entry, *a, **kw):
        return "  - name: [this is not a kind name\n"

    # Approvals now go through the verified owner writer (R-017 findings 1/4/5), which renders
    # entries with `render_entry`; break that and the write must be refused with the file intact.
    monkeypatch.setattr(ontology_routes, "render_entry", _broken)
    proposed = client.post("/ontology/proposals", json={
        "axis": "node", "name": "referral_source", "binds_to": "organisation",
        "proposed_by": "an adapter agent"}).json()
    r = client.post(f"/ontology/proposals/{proposed['id']}/approve",
                    json={"decided_by": "N. Aisyah"})
    assert r.status_code == 503
    assert r.json()["refusal"]["code"] == "PACK_WRITE_FAILED"
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("origin", [None, "agent", "adapter_agent"])
def test_an_agent_suggested_kind_is_never_auto_applied_even_with_auto_approve_on(client, packs,
                                                                                 origin):
    """Founder's rule: kinds SUGGESTED by agents or adapter agents are always proposals awaiting a
    human. The same grounded, ordinary proposal that auto-applies for the owner (test above) stays
    pending for any other origin — including one that names no origin at all."""
    _enable_auto_approve(client)
    before = (packs / "healthcare" / "ontology.yaml").read_text(encoding="utf-8")
    body = {"axis": "node", "name": "referral_source", "binds_to": "organisation",
            "proposed_by": "adapter agent: files", "evidence": GROUNDED_EVIDENCE}
    if origin is not None:
        body["origin"] = origin
    r = client.post("/ontology/proposals", json=body)
    assert r.status_code == 200, r.text
    record = r.json()
    assert record["status"] == "pending"
    assert record["auto_approvable"] is True        # non-vacuous: only the origin held it back
    assert record["auto_approve_bypassed"]
    assert (packs / "healthcare" / "ontology.yaml").read_text(encoding="utf-8") == before
    pending = {p["id"] for p in _ontology(client)["pending_proposals"]}
    assert record["id"] in pending

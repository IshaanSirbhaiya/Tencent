"""The neutral `/determination/*` surface, and the `/intake/*` alias it kept.

Phase 1B gave the platform's determination surface a domain-free prefix. The vertical prefix still
answers, because the frontend, the e2e specs and every bookmark use it — so the two must be the
SAME handler, not two implementations that drift. These tests pin three things:

1. every path served under one prefix is served under the other, and answers identically;
2. the confirmation gate is exactly as strict on the new prefix — `IDENTITY_NOT_VERIFIED`,
   candidate membership, the append-only ledger. A new front door is the classic way a gate ends
   up with an unguarded second entrance;
3. the subject-id field name is read from the ACTIVE PACK, not written into the gateway.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import AUTH_HEADERS

from databridge_gateway import create_app
from databridge_gateway.intake_routes import (DETERMINATION_PREFIX, LEGACY_PREFIX,
                                              subject_id_fields)

STAFF = "staff:front_desk_01"


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        yield c


@pytest.fixture(scope="module")
def chit_text():
    from databridge_intake.paths import fixtures_dir

    return (fixtures_dir() / "chits" / "chit_02_everwell_voucher.txt").read_text(encoding="utf-8")


@pytest.fixture
def uploaded(client, chit_text, request):
    """One fresh determination per test — the id is derived from the document."""
    document_id = f"{request.node.name}__chit_02.txt"
    r = client.post(f"{DETERMINATION_PREFIX}/upload",
                    json={"document_id": document_id, "text": chit_text})
    assert r.status_code == 200, r.text
    return r.json()


# --- the two prefixes are one surface ---------------------------------------------------


def _paths(app, prefix: str) -> set[tuple[str, frozenset]]:
    return {(r.path[len(prefix):], frozenset(r.methods))
            for r in app.routes
            if getattr(r, "path", "").startswith(prefix + "/")
            or getattr(r, "path", "") == prefix}


def test_every_path_is_served_under_both_prefixes(client):
    app = client.app
    neutral, legacy = _paths(app, DETERMINATION_PREFIX), _paths(app, LEGACY_PREFIX)
    assert neutral, "no /determination routes are registered"
    assert neutral == legacy, (
        "the two prefixes diverge — a route was added to one and not the other:\n"
        f"only neutral: {sorted(neutral - legacy)}\nonly legacy: {sorted(legacy - neutral)}")


def test_only_the_neutral_prefix_is_documented(client):
    """The alias must not become the name of the thing."""
    schema = client.get("/openapi.json").json()
    documented = {p for p in schema["paths"] if p.startswith(LEGACY_PREFIX)}
    assert not documented, f"the compatibility alias appears in the OpenAPI schema: {documented}"
    assert any(p.startswith(DETERMINATION_PREFIX) for p in schema["paths"])
    # Nor may a vertical spelling be documented on the neutral prefix: one name per operation.
    assert f"{DETERMINATION_PREFIX}/{{intake_id}}/select-subject" in schema["paths"]
    assert f"{DETERMINATION_PREFIX}/{{intake_id}}/select-patient" not in schema["paths"]


@pytest.mark.parametrize("suffix", ["", "/fixtures"])
def test_the_alias_answers_identically(client, suffix):
    a = client.get(f"{DETERMINATION_PREFIX}{suffix}")
    b = client.get(f"{LEGACY_PREFIX}{suffix}")
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json()


def test_a_determination_uploaded_on_one_prefix_is_readable_on_the_other(client, uploaded):
    intake_id = uploaded["intake_id"]
    a = client.get(f"{DETERMINATION_PREFIX}/{intake_id}")
    b = client.get(f"{LEGACY_PREFIX}/{intake_id}")
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json()


# --- the gate, on the new front door ----------------------------------------------------


def test_select_refuses_without_an_attestation_on_the_neutral_route(client, uploaded):
    intake_id = uploaded["intake_id"]
    candidate = uploaded["identity"]["candidates"][0]["patient_id"]
    r = client.post(f"{DETERMINATION_PREFIX}/{intake_id}/select-subject", json={
        "subject_id": candidate, "staff_id": STAFF, "attested_in_person": False})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "IDENTITY_NOT_VERIFIED"


def test_confirm_refuses_without_an_attestation_on_the_neutral_route(client, uploaded):
    intake_id = uploaded["intake_id"]
    before = client.get(f"{DETERMINATION_PREFIX}/{intake_id}/ledger").json()["rows"]
    r = client.post(f"{DETERMINATION_PREFIX}/{intake_id}/confirm",
                    json={"staff_id": STAFF, "attested_in_person": False})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "IDENTITY_NOT_VERIFIED"
    assert client.get(f"{DETERMINATION_PREFIX}/{intake_id}/ledger").json()["rows"] == before


def test_an_off_list_subject_is_still_refused(client, uploaded):
    intake_id = uploaded["intake_id"]
    r = client.post(f"{DETERMINATION_PREFIX}/{intake_id}/select-subject", json={
        "subject_id": "pat:not-a-candidate", "staff_id": STAFF, "attested_in_person": True})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "NOT_A_CANDIDATE"


def test_the_card_still_declares_identity_unverified(uploaded):
    assert uploaded["identity"]["requires_human_selection"] is True
    assert uploaded["identity"]["verified"] is False


# --- the subject-id field name comes from the pack --------------------------------------


def test_the_accepted_subject_id_fields_are_the_packs_subject_kinds():
    from databridge_graph.packs import active_vocabulary

    expected = {f"{kind}_id" for kind in active_vocabulary().subject_kinds}
    assert set(subject_id_fields()) == expected
    assert expected, "the enabled packs declare no subject kind"


def test_the_neutral_and_pack_spellings_select_the_same_candidate(client, chit_text, request):
    """Both spellings reach the same selection. Run on two separate determinations so neither
    test is observing the other's committed state."""
    candidate_by_id = {}
    for i, (field, prefix) in enumerate([("subject_id", DETERMINATION_PREFIX),
                                         ("patient_id", LEGACY_PREFIX)]):
        doc = f"{request.node.name}__{i}.txt"
        card = client.post(f"{prefix}/upload",
                           json={"document_id": doc, "text": chit_text}).json()
        intake_id = card["intake_id"]
        candidate = card["identity"]["candidates"][0]["patient_id"]
        r = client.post(f"{prefix}/{intake_id}/select-subject", json={
            field: candidate, "staff_id": STAFF, "attested_in_person": True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["selected_subject_id"] == body["selected_patient_id"] == candidate
        candidate_by_id[field] = body["selected_subject_id"]
    assert candidate_by_id["subject_id"] == candidate_by_id["patient_id"]


def test_a_field_no_pack_declares_is_refused_not_ignored(client, uploaded):
    """Silently dropping an unrecognised id field would read as 'no id supplied' — which on this
    surface means selecting nobody, quietly."""
    intake_id = uploaded["intake_id"]
    r = client.post(f"{DETERMINATION_PREFIX}/{intake_id}/select-subject", json={
        "customer_id": "whoever", "staff_id": STAFF, "attested_in_person": True})
    assert r.status_code == 422, r.text


def test_sending_the_id_twice_is_refused(client, uploaded):
    intake_id = uploaded["intake_id"]
    candidate = uploaded["identity"]["candidates"][0]["patient_id"]
    r = client.post(f"{DETERMINATION_PREFIX}/{intake_id}/select-subject", json={
        "subject_id": candidate, "patient_id": candidate,
        "staff_id": STAFF, "attested_in_person": True})
    assert r.status_code == 422, r.text


def test_no_subject_id_at_all_is_refused(client, uploaded):
    intake_id = uploaded["intake_id"]
    r = client.post(f"{DETERMINATION_PREFIX}/{intake_id}/select-subject", json={
        "staff_id": STAFF, "attested_in_person": True})
    assert r.status_code == 422, r.text

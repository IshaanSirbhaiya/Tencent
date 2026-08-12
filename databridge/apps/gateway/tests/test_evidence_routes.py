"""``GET /evidence/trace/{entity_id}`` — standalone tests (T-37, `PHASE_7_BRAIN_AND_INTELLIGENCE.md`
§3B).

Its own tiny FastAPI app is built here, over the SAME real clinic graph seed and a real
`EvidenceLedger`, so the assertions exercise real data shapes rather than a mock.

Two findings dominate this file:

* **R-26** — a `FOR_PATIENT` edge rendered as an established relationship with no committed row
  behind it, so a candidate a human had REJECTED still appeared as the document's patient. The
  tests below pin both halves: the selected candidate is a relationship, the other one is a claim.
* **R-28** — graph evidence was served with no authorisation root, so an abandoned
  pre-confirmation intake was readable by id. The tests pin the boundary: `coverage_rules.yaml`
  configuration is readable with an empty ledger; anything touching a patient is not.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from databridge_gateway.evidence_routes import register_evidence_routes
from databridge_graph import NodeKind
from databridge_graph.clinic_seed import fresh_clinic_store
from databridge_graph.node_sheet import IDENTITY_CAVEAT
from databridge_intake.ledger import EvidenceLedger, subject_fingerprint_for
from databridge_intake.models import ConfirmationState
from databridge_intake.paths import fixtures_dir
from databridge_intake.pipeline import run_intake

CHIT_01 = "chit_01_meridian_referral.txt"  # matches the graph seed's document_id exactly
# The two fixtures the seed resolves to TWO registry candidates each — the whole point of R-26 is
# what happens to the candidate the human does not pick, and it needs a document that has one.
CHIT_03 = "chit_03_everwell_group_invitation.txt"
CHIT_10 = "chit_10_northstar_second_referral.txt"  # claims the same patient as chit_01


def _chit_text(name: str) -> str:
    return (fixtures_dir() / "chits" / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def store():
    # The real seed: ten chit fixtures, the real registry, the real coverage rules.
    return fresh_clinic_store()


@pytest.fixture()
def app(store):
    application = FastAPI()
    application.state.brain = SimpleNamespace(store=store)
    application.state.ledger = EvidenceLedger()
    application.state.intakes = {}
    register_evidence_routes(application)
    return application


@pytest.fixture()
def client(app):
    return TestClient(app)


def confirm(app, document_id: str, *, candidate_index: int = 0,
            staff: str = "staff:evidence_test", remember_intake: bool = True) -> dict:
    """Run a chit through the real pipeline, attest, select, commit — the exact sequence
    `/intake/*` performs, so the ledger rows these tests read are the shape a real confirmation
    produces.

    ``remember_intake`` mirrors the two states the gateway can be in: the draft still held in
    ``app.state.intakes`` (this process did the upload), or gone (the process restarted, and only
    the durable ledger remains).
    """
    text = _chit_text(document_id)
    draft = run_intake(text, document_id=document_id)
    assert draft.resolution.candidates, "fixture must resolve at least one registry candidate"
    patient_id = draft.resolution.candidates[candidate_index].patient_id

    state = ConfirmationState(intake_id=draft.intake_id)
    state = state.attest_identity_checked(staff, dt.datetime.now(dt.timezone.utc))
    # The digest binding the attestation to THIS reading of the document and THIS candidate
    # list (R-17) — derived from the draft, exactly as `/intake/{id}/select-patient` derives it.
    subject = subject_fingerprint_for(draft.record, draft.resolution, patient_id)
    state = state.select_patient(patient_id, subject_fingerprint=subject)

    if remember_intake:
        app.state.intakes[draft.intake_id] = {"draft": draft, "text": text}
    entries = app.state.ledger.commit(draft.record, state, subject_fingerprint=subject)
    assert entries, "commit must write at least one evidence row"
    return {"draft": draft, "patient_id": patient_id, "entries": entries,
            "candidates": [c.patient_id for c in draft.resolution.candidates],
            "staff": staff}


def for_patient_edge_id(document_id: str, patient_id: str) -> str:
    return f"document:{document_id}|for_patient|patient:{patient_id}"


# --- unknown entity -----------------------------------------------------------------------


def test_unknown_entity_id_is_a_404_refusal(client):
    r = client.get("/evidence/trace/nothing:like-this")
    assert r.status_code == 404
    assert r.json()["refusal"]["code"] == "UNKNOWN_ENTITY"


def test_a_malformed_ledger_id_is_refused_not_guessed(client):
    r = client.get("/evidence/trace/ledger:not-a-number")
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "BAD_ENTITY_ID"


# --- R-28: the authorisation root ---------------------------------------------------------


def test_an_unconfirmed_intakes_document_is_refused_by_id(client):
    """R-28. The graph seed is built from intakes NOBODY has confirmed. Reading one by id used to
    return its extracted values, its pointers and every patient it named — which is precisely the
    abandoned pre-confirmation draft this project promises is not a record yet.

    This also subsumes the R-26 case of a two-candidate document with no committed row: the page
    asserts no patient relationship because it asserts nothing at all.
    """
    r = client.get(f"/evidence/trace/document:{CHIT_03}")
    assert r.status_code == 403, r.text
    body = r.json()
    assert body["refusal"]["code"] == "AWAITING_HUMAN_CONFIRMATION"
    # "not yet confirmed" is a DIFFERENT fact from "does not exist", and the caller must be able
    # to tell them apart without parsing prose.
    assert body["refusal"]["code"] != "UNKNOWN_ENTITY"
    assert "confirm" in body["refusal"]["message"].lower()
    # Nothing leaked around the refusal.
    assert "node" not in body and "sources" not in body and "relationships" not in body


def test_an_unconfirmed_patient_node_is_refused_by_id(client, store):
    """A registry row nobody has selected in person is not this page's to hand out."""
    row = store.open_nodes_of_kind(NodeKind.PATIENT)[0]
    r = client.get(f"/evidence/trace/{row['id']}")
    assert r.status_code == 403
    assert r.json()["refusal"]["code"] == "AWAITING_HUMAN_CONFIRMATION"


def test_a_for_patient_edge_on_an_unconfirmed_document_is_refused(client, store):
    edge_row = next(e for e in store.open_edges() if e["kind"] == "for_patient")
    r = client.get(f"/evidence/trace/{edge_row['id']}")
    assert r.status_code == 403
    assert r.json()["refusal"]["code"] == "AWAITING_HUMAN_CONFIRMATION"


def test_a_coverage_rules_pointer_is_readable_with_an_empty_ledger(client, app, store):
    """The other side of the R-28 boundary. A coverage_rule node is grounded in
    `coverage_rules.yaml` (a `table_cell` pointer) — app configuration, identical for every
    patient, carrying no patient data. There is no confirmation that could gate it, refusing it
    would make the page useless for explaining a billing determination, and it protects nobody.
    """
    assert app.state.ledger.count() == 0, "this test is only meaningful with an empty ledger"
    rule_rows = store.open_nodes_of_kind(NodeKind.COVERAGE_RULE)
    assert rule_rows
    r = client.get(f"/evidence/trace/{rule_rows[0]['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["node"]["kind"] == "coverage_rule"
    assert body["authorisation"]["basis"] == "static_configuration"
    assert body["sources"]
    for src in body["sources"]:
        assert src["document_id"] is None
        assert src["document"] is None
        assert src["ingestion_route"] == "config"


def test_a_node_carrying_any_document_evidence_is_not_treated_as_configuration(client, store):
    """Mixed evidence fails closed. `org:coral-harbour` is grounded in chit `doc_span`s, not in
    `coverage_rules.yaml`, so it is patient-facing document evidence and needs a committed row —
    the config exemption is about WHERE the evidence came from, never about the node's kind."""
    row = store.open_node_row("org:coral-harbour")
    assert row is not None, "seed changed: this test needs a document-grounded org node"
    r = client.get("/evidence/trace/org:coral-harbour")
    assert r.status_code == 403
    assert r.json()["refusal"]["code"] == "AWAITING_HUMAN_CONFIRMATION"


def test_authorisation_survives_a_process_restart(client, app):
    """The gate reads the LEDGER, not `app.state.intakes`.

    The intakes map is process memory and empties on restart; the ledger does not. A gate that
    consulted only the map would refuse every confirmation the clinic made before the last
    restart — so the document a committed row confirms is taken from the row's own pointer.
    """
    confirm(app, CHIT_01, remember_intake=False)
    assert app.state.intakes == {}
    r = client.get(f"/evidence/trace/document:{CHIT_01}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["authorisation"]["basis"] == "committed_ledger_row"
    assert body["ledger_entries"]
    # …and with no draft in memory the ingestion channel is honestly unknown, never defaulted.
    for src in body["sources"]:
        assert src["ingestion_route"] == "unknown"
        assert src["intake_id"] is None
        assert src["document_id"] == CHIT_01
        assert src["document"]["id"] == f"document:{CHIT_01}"
    assert "sources_for()" in body["limitation"]
    assert "EVERY SourcePointer ever attached" not in body["limitation"], (
        "the version-pooling limitation was fixed; the notice must not still claim it")


def test_a_committed_ledger_row_is_its_own_authorisation_root(client, app):
    committed = confirm(app, CHIT_01)
    row = app.state.ledger.entries(committed["draft"].intake_id)[0]
    r = client.get(f"/evidence/trace/ledger:{row['rowid_pk']}")
    assert r.status_code == 200
    assert r.json()["authorisation"]["basis"] == "committed_ledger_row"


# --- R-26: a claim is not a relationship --------------------------------------------------


def test_the_rejected_candidate_is_never_an_established_relationship(client, app, store):
    """**The finding, exactly.** chit_03 resolves to TWO registry candidates. A human checks the
    person in front of them and selects candidate A. Candidate B is the one they REJECTED — and
    the evidence page used to show B as the document's patient, on the one page whose entire
    purpose is to show that this system does not determine identity.
    """
    committed = confirm(app, CHIT_03, candidate_index=0)
    candidates = committed["candidates"]
    assert len(candidates) == 2, "seed changed: this test needs a two-candidate document"
    selected, rejected = committed["patient_id"], candidates[1]
    assert selected != rejected

    body = client.get(f"/evidence/trace/document:{CHIT_03}").json()

    established = {r["other_id"] for r in body["relationships"] if r["kind"] == "for_patient"}
    claimed = {c["other_id"] for c in body["identity_claims"]}
    assert established == {f"patient:{selected}"}
    assert claimed == {f"patient:{rejected}"}
    assert f"patient:{rejected}" not in established

    # The rejected candidate is SHOWN, not hidden — hiding it is its own kind of lie on an
    # evidence page, and the candidate count is exactly what a reviewer needs.
    claim = body["identity_claims"][0]
    assert claim["established"] is False
    assert claim["established_by"] == []
    assert "candidate" in claim["candidate_phrase"]
    assert claim["sources"], "a claim still carries the span it was read from"

    # …and the established one names the rows that established it.
    relation = next(r for r in body["relationships"] if r["kind"] == "for_patient")
    assert relation["established"] is True
    assert relation["established_by"], "an established identity edge must name its ledger rows"
    assert relation["candidate_phrase"] is None


def test_the_identity_caveat_is_the_graphs_own_wording(client, app):
    """`/evidence` and `node_sheet.node_markdown` describe the same FOR_PATIENT edges. Two
    surfaces describing the same fact differently is how one of them ends up softer."""
    confirm(app, CHIT_03, candidate_index=0)
    body = client.get(f"/evidence/trace/document:{CHIT_03}").json()
    assert body["identity_claims"]
    assert body["identity_caveat"] == IDENTITY_CAVEAT


def test_no_caveat_is_emitted_when_there_is_no_unsettled_claim(client, app):
    """The caveat means something only if its absence does too: a renderer must be able to treat
    "caveat present" as "there is an identity here no human has settled"."""
    confirm(app, CHIT_01)
    body = client.get(f"/evidence/trace/document:{CHIT_01}").json()
    assert body["identity_claims"] == []
    assert body["identity_caveat"] is None


def test_tracing_the_rejected_candidates_edge_does_not_return_an_edge(client, app):
    """Not merely a flag on an edge: the unsettled case has a shape of its own, so a caller that
    reads `edge` cannot pick up an identity assertion by accident."""
    committed = confirm(app, CHIT_03, candidate_index=0)
    rejected = committed["candidates"][1]
    r = client.get(f"/evidence/trace/{for_patient_edge_id(CHIT_03, rejected)}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity_type"] == "identity_claim"
    assert body["edge"] is None
    assert body["claim"]["established"] is False
    assert body["claim"]["patient_id"] == rejected
    assert body["ledger_entries"] == []
    assert body["identity_caveat"] == IDENTITY_CAVEAT
    # The evidence for the claim is still there — this is a refusal to ASSERT, not a refusal to
    # show. `requires_human_verification` is the graph's own invariant, cited.
    assert body["sources"]
    assert body["claim"]["attrs"].get("requires_human_verification") is True
    assert "requires_human_verification" in body["claim"]["attribute_sources"]


def test_tracing_the_selected_candidates_edge_is_an_established_relationship(client, app):
    committed = confirm(app, CHIT_03, candidate_index=0)
    r = client.get(f"/evidence/trace/{for_patient_edge_id(CHIT_03, committed['patient_id'])}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity_type"] == "edge"
    assert body["claim"] is None
    assert body["edge"]["established"] is True
    assert body["edge"]["kind"] == "for_patient"
    assert body["ledger_entries"], (
        "an edge joining the committed document and the committed patient must surface the "
        "ledger rows that attested exactly that pairing")
    assert all(e["selected_patient_id"] == committed["patient_id"]
               for e in body["ledger_entries"])
    assert body["identity_caveat"] is None


def test_a_patient_confirmed_on_one_document_is_only_a_claim_on_another(client, app, store):
    """The same defect seen from the patient's side. `pat:4e487c99807b` is claimed by chit_01 AND
    chit_10. Confirming chit_10 says who chit_10 is about; it says nothing about chit_01, and the
    page must not let one confirmation vouch for a second document's identity claim."""
    committed = confirm(app, CHIT_10)
    patient_id = committed["patient_id"]
    body = client.get(f"/evidence/trace/patient:{patient_id}").json()
    assert body["node"]["kind"] == "patient"

    established = {r["other_id"] for r in body["relationships"] if r["kind"] == "for_patient"}
    claimed = {c["other_id"] for c in body["identity_claims"]}
    if f"document:{CHIT_01}" not in claimed | established:
        pytest.skip("seed changed: this patient is no longer claimed by two documents")
    assert established == {f"document:{CHIT_10}"}
    assert claimed == {f"document:{CHIT_01}"}
    assert body["identity_caveat"] == IDENTITY_CAVEAT


def test_non_identity_relationships_are_unaffected(client, app):
    """R-26 is about identity. A document's ISSUED / REQUESTS_TEST / AUTHORISES edges are the
    graph's ordinary structure and must keep rendering as relationships, or the fix would have
    emptied the page instead of correcting it."""
    confirm(app, CHIT_03)
    body = client.get(f"/evidence/trace/document:{CHIT_03}").json()
    kinds = {r["kind"] for r in body["relationships"]}
    assert kinds - {"for_patient"}, "the document must still show its non-identity relationships"
    assert all(c["kind"] == "for_patient" for c in body["identity_claims"])


def test_the_api_shape_lets_the_ui_render_a_claim_distinctly_from_a_relationship(client, app):
    """The frontend assertion, made at the level this repo can reach it.

    `apps/frontend` has no unit-test runner (Playwright e2e only, and that needs both servers
    up), so what is pinned here is the CONTRACT `components/evidence/trace-chain.tsx` renders
    from: claims arrive in their own list, never in `relationships`; every claim says
    `established: false`; and the caveat the UI prints above them comes from the response rather
    than from a copy in the TypeScript.
    """
    confirm(app, CHIT_03, candidate_index=0)
    body = client.get(f"/evidence/trace/document:{CHIT_03}").json()
    ids = {r["id"] for r in body["relationships"]}
    for claim in body["identity_claims"]:
        assert claim["id"] not in ids, "a claim must never also appear as a relationship"
        assert claim["established"] is False
        assert claim["candidate_phrase"]
    assert all(r["established"] is True for r in body["relationships"])
    assert body["identity_caveat"]


# --- the full chain: intake -> selection -> commit -> ledger trace --------------------------


def test_document_trace_shows_the_real_ingestion_channel(client, app):
    committed = confirm(app, CHIT_01)
    body = client.get(f"/evidence/trace/document:{CHIT_01}").json()
    for src in body["sources"]:
        assert src["ingestion_route"] == "upload"          # run_intake's default channel
        assert src["intake_id"] == committed["draft"].intake_id
    assert body["ledger_entries"], "the committed rows for this document must show up"
    assert all(e["confirmed_by"] == "staff:evidence_test" for e in body["ledger_entries"])
    assert all(e["confirmed_at"] for e in body["ledger_entries"])


def test_the_self_citation_pointer_resolves_against_the_real_document_text(client, app):
    """The anti-hallucination proof: the pointer's span is real text in the real document."""
    confirm(app, CHIT_01)
    body = client.get(f"/evidence/trace/document:{CHIT_01}").json()
    text = _chit_text(CHIT_01)
    pointer = body["sources"][0]["pointer"]
    assert pointer["kind"] == "doc_span"
    span = text[pointer["char_start"]:pointer["char_end"]]
    assert span  # non-empty: the whole-document self-citation


def test_patient_node_trace_shows_the_rows_committed_for_that_patient(client, app):
    committed = confirm(app, CHIT_01)
    r = client.get(f"/evidence/trace/patient:{committed['patient_id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["node"]["kind"] == "patient"
    assert body["ledger_entries"]
    assert all(e["selected_patient_id"] == committed["patient_id"]
               for e in body["ledger_entries"])


def test_ledger_entry_trace_by_row_id(app, client):
    # `EvidenceEntry` (the return type of `ledger.commit`) carries no rowid — look the row up the
    # same way the route does, via the ledger's own read path, to get a real one.
    committed = confirm(app, CHIT_01)
    stored = app.state.ledger.entries(committed["draft"].intake_id)
    assert stored
    row = stored[0]

    r = client.get(f"/evidence/trace/ledger:{row['rowid_pk']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity_type"] == "ledger_entry"
    assert body["ledger_entries"][0]["rowid"] == row["rowid_pk"]
    assert body["ledger_entries"][0]["confirmed_by"] == "staff:evidence_test"
    assert body["ledger_entries"][0]["field_path"] == row["field_path"]
    # The pointer behind this one committed field, shown raw (the whole point of the page).
    assert body["sources"]
    assert body["sources"][0]["pointer"]["kind"] in {
        "doc_span", "table_cell", "transcript_span", "code_node", "derived",
        "query_cell", "workbook_cell", "ocr_bbox", "email_part", "generated_claim"}


def test_ledger_entry_trace_of_an_unknown_row_is_404(client):
    r = client.get("/evidence/trace/ledger:999999")
    assert r.status_code == 404
    assert r.json()["refusal"]["code"] == "UNKNOWN_ENTITY"


def test_a_traced_node_cites_each_attribute_separately(client, app):
    """Shape A at the API boundary. The route returned `attrs` beside one pooled pointer list, so
    a caller reading the response — a person, the UI, or a model — could take any citation in that
    list as covering any value in the dict. Sex, nationality and date of birth are the concrete
    case named in the review: the node is grounded on name and ID pointers alone."""
    committed = confirm(app, CHIT_01)
    trace = client.get(f"/evidence/trace/patient:{committed['patient_id']}").json()
    node = trace["node"]
    assert node["attrs"], "no attributes to check"
    for key, value in node["attrs"].items():
        if value is None:
            assert key in node["unresolved"], f"{key} is absent but not named as unresolved"
        else:
            assert key in node["attribute_sources"], f"{key} is returned with no pointer of its own"
            assert node["attribute_sources"][key]["kind"], f"{key}'s pointer has no kind"

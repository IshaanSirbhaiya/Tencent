"""``GET /evidence/trace/{entity_id}`` — standalone tests (T-37, `PHASE_7_BRAIN_AND_INTELLIGENCE.md`
§3B).

`evidence_routes.py` is not wired into `create_app()` (that one-line registration is left for the
gateway owner — see the module docstring). Its own tiny FastAPI app is built here instead, over the
SAME real clinic graph seed and a real `EvidenceLedger`, so the assertions exercise real data
shapes rather than a mock.
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
from databridge_intake.ledger import EvidenceLedger
from databridge_intake.models import ConfirmationState
from databridge_intake.paths import fixtures_dir
from databridge_intake.pipeline import run_intake

CHIT_01 = "chit_01_meridian_referral.txt"  # matches the graph seed's document_id exactly


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


# --- unknown entity -----------------------------------------------------------------------


def test_unknown_entity_id_is_a_404_refusal(client):
    r = client.get("/evidence/trace/nothing:like-this")
    assert r.status_code == 404
    assert r.json()["refusal"]["code"] == "UNKNOWN_ENTITY"


def test_a_malformed_ledger_id_is_refused_not_guessed(client):
    r = client.get("/evidence/trace/ledger:not-a-number")
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "BAD_ENTITY_ID"


# --- graph node: a document, with nobody's intake in this process --------------------------


def test_document_node_trace_shows_unknown_ingestion_route_when_no_intake_matches(client):
    r = client.get(f"/evidence/trace/document:{CHIT_01}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity_type"] == "node"
    assert body["node"]["kind"] == "document"
    assert body["sources"], "a seeded document must carry at least its self-citation pointer"
    for src in body["sources"]:
        # Nobody has run this document through /intake/upload in THIS process (app.state.intakes
        # is empty) — the honest answer is "unknown", never a default like "upload".
        assert src["ingestion_route"] == "unknown"
        assert src["document_id"] == CHIT_01
        assert src["document"]["id"] == f"document:{CHIT_01}"
    assert "sources_for()" in body["limitation"]


def test_the_self_citation_pointer_resolves_against_the_real_document_text(client):
    """The anti-hallucination proof: the pointer's span is real text in the real document."""
    r = client.get(f"/evidence/trace/document:{CHIT_01}")
    body = r.json()
    text = _chit_text(CHIT_01)
    pointer = body["sources"][0]["pointer"]
    assert pointer["kind"] == "doc_span"
    span = text[pointer["char_start"]:pointer["char_end"]]
    assert span  # non-empty: the whole-document self-citation


def test_a_config_pointer_is_reported_as_config_not_unknown(client, store):
    # A coverage_rule node is grounded in coverage_rules.yaml (a table_cell pointer), never
    # ingested through any patient-facing channel.
    rule_rows = store.open_nodes_of_kind(NodeKind.COVERAGE_RULE)
    assert rule_rows
    entity_id = rule_rows[0]["id"]
    r = client.get(f"/evidence/trace/{entity_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["node"]["kind"] == "coverage_rule"
    assert body["sources"]
    for src in body["sources"]:
        assert src["document_id"] is None
        assert src["document"] is None
        assert src["ingestion_route"] == "config"


# --- graph edge ----------------------------------------------------------------------------


def test_for_patient_edge_trace_carries_its_own_pointers(client, store):
    edge_row = next(e for e in store.open_edges() if e["kind"] == "for_patient")
    r = client.get(f"/evidence/trace/{edge_row['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity_type"] == "edge"
    assert body["edge"]["kind"] == "for_patient"
    assert body["sources"]
    # No commit has happened anywhere in this test app yet.
    assert body["ledger_entries"] == []


# --- the full chain: intake -> selection -> commit -> ledger trace --------------------------


@pytest.fixture()
def committed(app, store):
    """Runs chit_01 through the real pipeline, attests, selects, and commits — end to end,
    exactly the sequence `/intake/*` performs, so the ledger rows this test inspects are the
    same shape a real confirmation would produce."""
    text = _chit_text(CHIT_01)
    draft = run_intake(text, document_id=CHIT_01)
    assert draft.resolution.candidates, "fixture must resolve at least one registry candidate"
    patient_id = draft.resolution.candidates[0].patient_id

    state = ConfirmationState(intake_id=draft.intake_id)
    state = state.attest_identity_checked("staff:evidence_test", dt.datetime.now(dt.timezone.utc))
    state = state.select_patient(patient_id)

    app.state.intakes[draft.intake_id] = {"draft": draft, "text": text}
    entries = app.state.ledger.commit(draft.record, state)
    assert entries, "commit must write at least one evidence row"
    return {"draft": draft, "patient_id": patient_id, "entries": entries}


def test_document_trace_now_shows_the_real_ingestion_channel(client, committed):
    r = client.get(f"/evidence/trace/document:{CHIT_01}")
    body = r.json()
    for src in body["sources"]:
        assert src["ingestion_route"] == "upload"          # run_intake's default channel
        assert src["intake_id"] == committed["draft"].intake_id
    assert body["ledger_entries"], "the committed rows for this document must show up"
    assert all(e["confirmed_by"] == "staff:evidence_test" for e in body["ledger_entries"])
    assert all(e["confirmed_at"] for e in body["ledger_entries"])


def test_patient_node_trace_shows_the_rows_committed_for_that_patient(client, committed):
    patient_entity = f"patient:{committed['patient_id']}"
    r = client.get(f"/evidence/trace/{patient_entity}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["node"]["kind"] == "patient"
    assert body["ledger_entries"]
    assert all(e["selected_patient_id"] == committed["patient_id"]
              for e in body["ledger_entries"])


def test_for_patient_edge_trace_now_carries_the_committed_rows_too(client, store, committed):
    doc_id = f"document:{CHIT_01}"
    pat_id = f"patient:{committed['patient_id']}"
    edge_id = f"{doc_id}|for_patient|{pat_id}"
    row = next((e for e in store.open_edges() if e["id"] == edge_id), None)
    if row is None:
        pytest.skip("this fixture's top candidate did not yield a FOR_PATIENT edge in the seed")
    r = client.get(f"/evidence/trace/{edge_id}")
    body = r.json()
    assert body["entity_type"] == "edge"
    assert body["ledger_entries"], (
        "an edge joining the committed document and the committed patient must surface the "
        "ledger rows that attested exactly that pairing")


def test_ledger_entry_trace_by_row_id(app, client, committed):
    # `EvidenceEntry` (the return type of `ledger.commit`) carries no rowid — look the row up the
    # same way the route does, via the ledger's own read path, to get a real one.
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

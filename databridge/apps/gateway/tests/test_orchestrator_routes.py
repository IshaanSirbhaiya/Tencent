"""`/orchestrate` — the main agent, reached over HTTP, against the real finance estate.

Nothing here is mocked: `POST /orchestrate/ask` builds a real `Orchestrator` over two real
adapters (`core_banking`, `mailbox`), each admitted by a real `PolicyEngine` built from
`fixtures/packs/finance/roles.yaml`. The two identities the request may act as are the estate's own
demonstration case — a credit analyst reaches `core_banking` and is refused on `mailbox`
(compliance-only); a compliance officer reaches both — so this suite exercises the orchestrator's
answered/refused split for real rather than asserting it against a stub.

No `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` is required: with neither set the fleet falls back to the
deterministic `_GoldPlanner` (`orchestrator_fleet.py`), so this suite never depends on a live model
(repo rule) and stays fast.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

#: databridge/apps/gateway/tests -> databridge/
DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]
for _p in ("packages/adapter", "packages/core", "packages/graph", "packages/orchestrator",
          "packages/harness"):
    _path = str(DATABRIDGE_ROOT / _p)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from databridge_gateway.orchestrator_fleet import dispose_orchestrator_fleet  # noqa: E402
from databridge_gateway.orchestrator_routes import register_orchestrator_routes  # noqa: E402
from databridge_gateway.trace_routes import register_trace_routes  # noqa: E402

from conftest import auth_headers  # noqa: E402


@pytest.fixture()
def app(monkeypatch):
    # This suite exercises the FIXTURE fleet, which is now opt-in (the default fleet is the
    # registered connections — see test_orchestrator_connections.py).
    monkeypatch.setenv("WALNUT_FLEET", "finance_fixture")
    a = FastAPI()
    register_orchestrator_routes(a)
    register_trace_routes(a)
    yield a
    dispose_orchestrator_fleet(a)


@pytest.fixture()
def client(app):
    # R-015: `on_behalf_of` no longer selects the grant — the authenticated identity does, via
    # `Authorization: Bearer <token>`. The default here authenticates as `analyst-7`; tests that
    # need the other demo persona pass `headers=auth_headers("fcc-3")` per request.
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        yield c


# =======================================================================================
# the fleet
# =======================================================================================
def test_fleet_status_reports_both_adapters(client):
    body = client.get("/orchestrate/fleet").json()
    assert body["available"] is True
    source_ids = {s["source_id"] for s in body["sources"]}
    assert source_ids == {"core_banking", "mailbox"}
    # The payload names the SIGNED-IN identity only — never the directory as a menu (R-015).
    assert body["principal"]["id"] == "analyst-7"
    assert set(body["principals"]) == {"analyst-7"}
    assert body["can_ask"] is True
    # Suggested questions are derived from what the adapters describe, never a literal list.
    described = {e for s in body["sources"] for e in s["entities"]}
    assert body["suggested_questions"], "derivation produced nothing — the check below is vacuous"
    for q in body["suggested_questions"]:
        assert any(e.replace("_", " ") in q for e in described), q


def test_fleet_status_as_the_other_persona_names_only_them(client):
    body = client.get("/orchestrate/fleet", headers=auth_headers("fcc-3")).json()
    assert set(body["principals"]) == {"fcc-3"}


def test_a_signed_in_identity_without_a_grant_cannot_ask(client):
    body = client.get("/orchestrate/fleet", headers=auth_headers("staff")).json()
    assert body["can_ask"] is False and body["ask_disabled_reason"]


# =======================================================================================
# the happy path — real cells, real pointers
# =======================================================================================
def test_analyst_gets_a_grounded_answer_from_core_banking(client):
    r = client.post("/orchestrate/ask", json={
        "question": "what is the facility limit and drawn amount"})
    assert r.status_code == 200
    body = r.json()
    core = next(s for s in body["sources"] if s["source_id"] == "core_banking")
    assert core["status"] == "answered"
    assert core["cells"], "the seeded facility rows must come back as grounded cells"
    grounded = [c for c in core["cells"] if c["state"] == "grounded"]
    assert grounded
    for cell in grounded:
        assert cell["pointer"] is not None
        assert cell["pointer"]["source_id"] == "core_banking"
        assert cell["trust"] is not None
    assert body["trace_id"]


# =======================================================================================
# refusal — Withheld with a reason code, never a missing row
# =======================================================================================
def test_analyst_is_refused_on_the_mailbox_not_silently_dropped(client):
    r = client.post("/orchestrate/ask", json={
        "question": "what does the email subject and claimed from address say"})
    assert r.status_code == 200
    body = r.json()
    mailbox = next((s for s in body["sources"] if s["source_id"] == "mailbox"), None)
    assert mailbox is not None, "a refused source is still REPORTED, never omitted"
    assert mailbox["status"] == "refused"
    assert mailbox["cells"] == []
    assert mailbox["denial"] is not None
    assert mailbox["denial"]["reason_code"]
    assert mailbox["denial"]["policy_id"]


def test_compliance_officer_can_read_the_mailbox(client):
    # A DIFFERENT authenticated identity, not a body field (R-015) — this is the exact request
    # shape the finding named: `{"on_behalf_of": "fcc-3"}` from an unauthenticated caller used to
    # return compliance-officer disclosure. It no longer parses at all (AskBody forbids the
    # field); the only way to act as fcc-3 now is to hold fcc-3's own credential.
    r = client.post("/orchestrate/ask", json={
        "question": "what does the email subject and claimed from address say"},
        headers=auth_headers("fcc-3"))
    assert r.status_code == 200
    body = r.json()
    mailbox = next(s for s in body["sources"] if s["source_id"] == "mailbox")
    assert mailbox["status"] == "answered"
    grounded = [c for c in mailbox["cells"] if c["state"] == "grounded"]
    assert grounded
    assert all(c["trust"] == "untrusted" for c in grounded), (
        "the finance estate declares untrusted_fields: ['*'] on mailbox; the label must survive "
        "the trip to JSON")


# =======================================================================================
# input refusals — never a client-chosen purpose or grant
# =======================================================================================
def test_unknown_principal_is_refused(client):
    # Authenticated (holds a valid token, "staff") but not one of THIS estate's known adapter
    # identities — `orchestrator_fleet.PRINCIPALS` only knows analyst-7 / fcc-3. Still refused,
    # just no longer because a body field could be anything: now the AUTHENTICATED identity has
    # to be one the estate's directory recognises.
    r = client.post("/orchestrate/ask", json={"question": "anything"},
                    headers=auth_headers("staff"))
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "UNKNOWN_PRINCIPAL"


def test_the_unknown_principal_refusal_names_no_identity_that_holds_a_grant(client):
    """R-019 finding 11: the refusal used to end `available: ['analyst-7', 'fcc-3']` — the
    directory offered as a menu to exactly the caller who holds no grant in it."""
    from databridge_gateway.orchestrator_fleet import PRINCIPALS

    assert PRINCIPALS, "an empty directory would make the assertion below vacuous"
    r = client.post("/orchestrate/ask", json={"question": "anything"},
                    headers=auth_headers("staff"))
    message = r.json()["refusal"]["message"]
    leaked = [name for name in PRINCIPALS if name in message]
    assert leaked == [], f"the refusal disclosed grant-holding identities: {leaked}"


def test_empty_question_is_refused(client):
    r = client.post("/orchestrate/ask", json={"question": "   "})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "EMPTY_QUESTION"


def test_a_client_cannot_smuggle_its_own_purpose_or_grant(client):
    r = client.post("/orchestrate/ask", json={
        "question": "facility limit", "purpose": "anything.at.all", "grant_id": "forged"})
    assert r.status_code == 422, (
        "AskBody forbids extra fields — a purpose is a fact this deployment's directory decides "
        "from the named identity, never a field an HTTP body sets (spine D5)")


def test_a_client_cannot_smuggle_on_behalf_of_either(client):
    """R-015, the finding itself. `on_behalf_of` used to be exactly this field — the one an
    unauthenticated caller set to `"fcc-3"` and received compliance-officer disclosure. It is no
    longer part of the schema at all, so the attempt fails at the body, before any principal
    resolution runs."""
    r = client.post("/orchestrate/ask", json={"question": "anything", "on_behalf_of": "fcc-3"})
    assert r.status_code == 422


# =======================================================================================
# authentication — the R-015 gate itself
# =======================================================================================
def test_no_credential_is_refused_before_anything_else(app):
    """Fail closed: a caller with no `Authorization` header at all never reaches the fleet."""
    with TestClient(app) as bare:                      # no default headers on this client
        r = bare.post("/orchestrate/ask", json={"question": "anything"})
    assert r.status_code == 401
    assert r.json()["refusal"]["code"] == "UNAUTHENTICATED"


def test_a_wrong_token_is_refused_the_same_way(app):
    with TestClient(app) as bare:
        r = bare.post("/orchestrate/ask", json={"question": "anything"},
                      headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401
    assert r.json()["refusal"]["code"] == "UNAUTHENTICATED"


def test_fleet_status_also_requires_a_credential(app):
    with TestClient(app) as bare:
        r = bare.get("/orchestrate/fleet")
    assert r.status_code == 401


# =======================================================================================
# the trace — /trace shows a real run, refusal included
# =======================================================================================
def test_trace_records_a_real_run_answered_and_refused(client):
    r = client.post("/orchestrate/ask", json={
        "question": "facility limit and the email subject"})
    trace_id = r.json()["trace_id"]
    assert trace_id

    listed = client.get("/trace").json()
    assert listed["total_held"] >= 1
    assert any(t["request_id"] == trace_id for t in listed["traces"])

    detail = client.get(f"/trace/{trace_id}").json()
    kinds = {s["kind"] for s in detail["step_list"]}
    assert "principal" in kinds
    assert "capability" in kinds
    assert "policy" in kinds, "the mailbox refusal must show up as a recorded policy step"
    assert "cell" in kinds
    assert "ledger_append" in kinds
    assert "released" in kinds
    assert detail["view"]["branches"], "the rendered /trace view must show at least one branch"

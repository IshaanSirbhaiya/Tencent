"""`/federated/ask` (FED): one question across adapter agents, as a job or inline.

The synthetic world (a real facts store, a real graph store, fake passages) and the scripted
harness come from `packages/federated/tests/fed_testkit.py`; expectations are computed from its
fixture rows. No model and no network are reached.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import auth_headers

_KIT = Path(__file__).resolve().parents[3] / "packages" / "federated" / "tests"
for _p in (str(_KIT), str(_KIT.parent)):
    if _p not in sys.path:
        sys.path.append(_p)          # appended: never shadow this package's own `conftest`

from fed_testkit import (  # noqa: E402
    CRM,
    FOUNDER_PLAN,
    FOUNDER_Q,
    NOTES,
    env,  # noqa: F401 - fixture
    expected_founder_table,
    scripted_harness,
    world,  # noqa: F401 - fixture
)

from databridge_gateway import federated_routes  # noqa: E402
from databridge_gateway.platform_runtime import build_platform  # noqa: E402


import json as _json_mod

import pytest


@pytest.fixture(autouse=True)
def _operator_grants(monkeypatch):
    """The operator's federated grant directory for these tests (R-021 round 2, finding 1). A test
    about grants overrides it."""
    monkeypatch.setenv("WALNUT_FEDERATED_GRANTS", _json_mod.dumps({"analyst-7": "*", "staff": "*"}))


def _cite_all(payload):
    claims = []
    for row in payload["rows"]:
        refs = [row[c]["ref"] for c in payload["columns"] if (row.get(c) or {}).get("ref")]
        text = "; ".join(f"{c}: {row[c]['value']}" for c in payload["columns"]
                         if (row.get(c) or {}).get("ref"))
        claims.append({"text": text, "citations": refs})
    return {"claims": claims}


def _mount(world):
    app = FastAPI()
    app.state.graph_store = world.store
    rt = build_platform(app)
    rt.harness, _calls = scripted_harness(FOUNDER_PLAN, _cite_all)
    rt.retriever_factory = lambda: world.retriever
    app.state.platform = rt
    app.state.federated_factory = lambda: world.service(harness=rt.harness)
    federated_routes.register_federated_routes(app)
    return app


def test_ask_inline_returns_the_computed_table_and_contract(world):
    app = _mount(world)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        r = c.post("/federated/ask", json={"question": FOUNDER_Q, "wait": True, "by": "mallory"})
        assert r.status_code == 200, r.text
        body = r.json()
    assert {"answer_markdown", "table", "citations", "coverage", "plan", "timings",
            "run_id"} <= set(body)
    expected = expected_founder_table(world.invoices)
    got = {row["cells"]["account_owner"]["value"]:
           Decimal(row["cells"]["sum_amount:this_quarter"]["value"])
           for row in body["table"]["rows"]}
    assert got == {k: v["this_quarter"] for k, v in expected.items()}
    assert body["unsupported_claims"] == [] and body["citations"]
    assert FOUNDER_Q in body["answer_markdown"]
    rec = next(world.runs_dir.rglob(f"{body['run_id']}.json"))
    assert "analyst-7" in rec.parts                       # the principal, not the body's name


def test_ask_as_a_job_is_visible_only_to_whoever_asked(world):
    app = _mount(world)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        r = c.post("/federated/ask", json={"question": FOUNDER_Q})
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]
        job = c.get(f"/federated/jobs/{job_id}").json()
        assert job["status"] == "completed", job
        assert job["by"] == "analyst-7" and job["result"]["table"]["rows"]
        other = c.get(f"/federated/jobs/{job_id}", headers=auth_headers("fcc-3"))
        assert other.status_code == 404
    with TestClient(app) as anon:
        assert anon.post("/federated/ask", json={"question": FOUNDER_Q}).status_code == 401


def test_narrowed_sources_report_reduced_coverage(world):
    app = _mount(world)
    with TestClient(app, headers=auth_headers("staff")) as c:
        body = c.post("/federated/ask", json={"question": FOUNDER_Q, "wait": True,
                                              "sources": [CRM]}).json()
    assert body["answer_markdown"].startswith("> **Reduced coverage.**")
    assert any(s.get("connection") == NOTES for s in body["coverage"]["skipped"])
    assert body["table"] == {}


def test_empty_question_is_refused(world):
    app = _mount(world)
    with TestClient(app, headers=auth_headers("staff")) as c:
        r = c.post("/federated/ask", json={"question": "  ", "wait": True})
    assert r.status_code == 422 and r.json()["refusal"]["code"] == "EMPTY_QUESTION"


def test_an_empty_sources_list_grants_nothing(world):
    """R-021 #1: ``sources: []`` is an explicit empty allowlist, never "every source"."""
    app = _mount(world)
    with TestClient(app, headers=auth_headers("staff")) as c:
        body = c.post("/federated/ask", json={"question": FOUNDER_Q, "wait": True,
                                              "sources": []}).json()
    assert body["coverage"]["searched"] == [] and body["table"] == {}
    assert world.facts.calls == [] and world.retriever.calls == []
    assert {s.get("connection") for s in body["coverage"]["skipped"]} >= {CRM, NOTES}


# ------------------------------------------------------------------------------------------------
# R-021 round 2, finding 1 (Critical): the default gateway path is caller-admitted too. A
# principal reads only the connections an OPERATOR granted it (``WALNUT_FEDERATED_GRANTS``);
# ``sources`` can only narrow that grant, never widen it; no grant record means no read.
# ------------------------------------------------------------------------------------------------
def test_r2_no_grant_directory_means_a_non_dev_principal_reads_nothing(world, monkeypatch):
    monkeypatch.delenv(federated_routes.GRANTS_ENV, raising=False)
    app = _mount(world)
    with TestClient(app, headers=auth_headers("staff")) as c:
        r = c.post("/federated/ask", json={"question": FOUNDER_Q, "wait": True})
        job = c.post("/federated/ask", json={"question": FOUNDER_Q})
    assert r.status_code == 403 and r.json()["refusal"]["code"] == "FEDERATED_NOT_GRANTED"
    assert job.status_code == 403
    assert world.facts.calls == [] and world.retriever.calls == []


def test_r2_a_principal_absent_from_the_grants_is_refused(world, monkeypatch):
    import json as _json
    monkeypatch.setenv(federated_routes.GRANTS_ENV, _json.dumps({"analyst-7": [CRM]}))
    app = _mount(world)
    with TestClient(app, headers=auth_headers("staff")) as c:
        r = c.post("/federated/ask", json={"question": FOUNDER_Q, "wait": True})
    assert r.status_code == 403
    assert "analyst-7" not in r.text                  # names nobody else's grant
    assert world.facts.calls == [] and world.retriever.calls == []


def test_r2_omitting_sources_reads_only_the_granted_connections(world, monkeypatch):
    import json as _json
    monkeypatch.setenv(federated_routes.GRANTS_ENV, _json.dumps({"staff": [CRM]}))
    app = _mount(world)
    with TestClient(app, headers=auth_headers("staff")) as c:
        body = c.post("/federated/ask", json={"question": FOUNDER_Q, "wait": True}).json()
        wider = c.post("/federated/ask", json={"question": FOUNDER_Q, "wait": True,
                                               "sources": [CRM, NOTES]}).json()
    for b in (body, wider):                           # `sources` narrows; it never widens
        assert NOTES not in b["coverage"]["searched"]
        assert any(s.get("connection") == NOTES for s in b["coverage"]["skipped"])
    assert world.retriever.calls == []


def test_r2_a_wildcard_grant_reads_every_registered_connection(world, monkeypatch):
    import json as _json
    monkeypatch.setenv(federated_routes.GRANTS_ENV, _json.dumps({"staff": "*"}))
    app = _mount(world)
    with TestClient(app, headers=auth_headers("staff")) as c:
        body = c.post("/federated/ask", json={"question": FOUNDER_Q, "wait": True}).json()
    assert set(body["coverage"]["searched"]) == {CRM, NOTES}


def test_r2_grant_resolution_rules(monkeypatch):
    """Unset: only the dev principal keeps the default scope. Malformed: nobody is granted.
    `sources` narrows a grant and never widens it; an empty list grants nothing."""
    from databridge_gateway.auth import AuthenticatedPrincipal
    dev, staff = AuthenticatedPrincipal("analyst-7", dev_mode=True), AuthenticatedPrincipal("staff")
    monkeypatch.delenv(federated_routes.GRANTS_ENV, raising=False)
    assert federated_routes.granted_connections(dev) is None
    assert federated_routes.granted_connections(staff) is federated_routes._NOT_GRANTED
    monkeypatch.setenv(federated_routes.GRANTS_ENV, "{not json")
    assert federated_routes.granted_connections(dev) is federated_routes._NOT_GRANTED
    monkeypatch.setenv(federated_routes.GRANTS_ENV, _json_mod.dumps({"staff": [CRM]}))
    g = federated_routes.granted_connections(staff)
    assert federated_routes.admitted_scope(g, None) == {CRM}
    assert federated_routes.admitted_scope(g, [CRM, NOTES]) == {CRM}
    assert federated_routes.admitted_scope(g, []) == set()
    assert federated_routes.admitted_scope(None, [NOTES]) == {NOTES}


# ------------------------------------------------------------------------------------------------
# E2E N-03 — the planner's questions can be answered back on /federated/ask
# ------------------------------------------------------------------------------------------------
def test_n03_answers_with_an_id_that_is_not_a_question_id_are_refused(world):
    app = _mount(world)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        r = c.post("/federated/ask", json={"question": FOUNDER_Q, "wait": True,
                                           "answers": {"timezone": "UTC"}})
    assert r.status_code == 422 and r.json()["refusal"]["code"] == "BAD_ANSWERS"


def test_n03_answers_reach_the_service_inline_and_as_a_job(world):
    from databridge_federated.planner import question_id
    seen: list[dict] = []
    real = world.service

    class Recording:
        def __init__(self):
            self.svc = real(harness=None)

        def answer(self, question, **kw):
            seen.append(kw.get("answers") or {})
            return self.svc.answer(question, **kw)

    app = _mount(world)
    app.state.federated_factory = Recording
    qid = question_id("Which date should the comparison use?")
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        r = c.post("/federated/ask", json={"question": FOUNDER_Q, "wait": True,
                                           "answers": {qid: "the due date"}})
        assert r.status_code == 200, r.text
        assert r.json()["answers"] == {qid: "the due date"}
        job = c.post("/federated/ask", json={"question": FOUNDER_Q,
                                             "answers": {qid: "issue date"}}).json()
        assert c.get(f"/federated/jobs/{job['job_id']}").json()["status"] == "completed"
    assert seen == [{qid: "the due date"}, {qid: "issue date"}]

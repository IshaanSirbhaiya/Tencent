"""`/materialise` wired to the knowledge platform (ROADMAP Phase F).

The materialiser is attached the way the real gateway attaches it — `platform_runtime.
start_platform` — over a synthetic workspace in ``tmp_path`` (graph + facts + passages + capture,
every `WALNUT_*` location pointed there). No model and no network: with no harness the data agent
plans with the deterministic gold planner.

What is proven:

* every `/materialise` route refuses without a credential (the list is DERIVED from the app's
  routes and checked non-empty);
* boot attaches a materialiser, and `/materialise/plans` offers exactly the shapes with builders,
  listing the rest as not built with a reason;
* ``POST /materialise`` with English runs as a background job and yields a receipt whose every
  grounded cell's pointer re-reads from its source; the output files download, and only listed
  files do;
* a plan runs reproducibly (same ``content_hash``), a leaky plan is refused BY NAME as an answer
  rather than a crash, and a plan naming another principal is refused.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_DATABRIDGE = Path(__file__).resolve().parents[3]
# `packages/materialise` is on serve.py's path but not on the root pytest `pythonpath` (its own
# conftest bootstraps it when its suite runs); bootstrap it here the same way.
if str(_DATABRIDGE / "packages" / "materialise") not in sys.path:
    sys.path.insert(0, str(_DATABRIDGE / "packages" / "materialise"))

from conftest import AUTH_HEADERS  # noqa: E402

from databridge_gateway.materialise_routes import register_materialise_routes  # noqa: E402
from databridge_gateway.platform_runtime import PlatformRuntime, start_platform  # noqa: E402

FRAUD_REQUEST = "one row per invoice at issue time, label = overdue after 30 days"


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    from databridge_graph.packs import deployment
    from databridge_materialise.testing import build_workspace

    with deployment("workspace"):
        ws = build_workspace(tmp_path / "ws")
        monkeypatch.setenv("WALNUT_FACTS_DIR", str(ws.facts))
        monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(ws.passages))
        monkeypatch.setenv("WALNUT_CAPTURE_DIR", str(ws.capture))
        monkeypatch.setenv("WALNUT_MATERIALISE_DIR", str(tmp_path / "materialise"))
        yield ws
        ws.store.close()


@pytest.fixture()
def app(workspace):
    a = FastAPI()
    a.state.graph_store = workspace.store
    a.state.platform = PlatformRuntime()          # no harness, no engine: nothing external
    register_materialise_routes(a)
    asyncio.run(start_platform(a))
    return a


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


def _run(client, body) -> dict:
    r = client.post("/materialise", json=body, headers=AUTH_HEADERS)
    assert r.status_code == 202, r.text
    job = client.get(r.json()["poll"], headers=AUTH_HEADERS).json()
    assert job["status"] != "running", "TestClient runs background tasks before returning"
    return job


def _plan(client, **kw) -> dict:
    """An invoice plan pinned the way a client pins one (R-021 round 2, Finding 3): the live
    source pins at the plan's build instant, read from ``/materialise/catalogue?as_of=``."""
    from databridge_materialise.testing import invoice_plan

    plan = invoice_plan(principal="staff", **kw)
    r = client.get("/materialise/catalogue",
                   params={"as_of": plan.temporal.build_time.isoformat()}, headers=AUTH_HEADERS)
    assert r.status_code == 200, r.text
    sources = r.json()["sources"]
    assert sources, "non-vacuous: the workspace has sources to pin"
    body = plan.model_dump(mode="json")
    body["temporal"]["source_snapshots"] = sources
    return {"plan": body}


def test_every_materialise_route_requires_a_principal(client, app):
    routes = [(m, r.path) for r in app.routes if getattr(r, "path", "").startswith("/materialise")
              for m in sorted(getattr(r, "methods", ()) or ()) if m in ("GET", "POST")]
    assert len(routes) >= 7, "non-vacuous: the route list is derived from the app"
    for method, path in routes:
        url = (path.replace("{job_id}", "job-x").replace("{plan_hash}", "0" * 64)
               .replace("{name}", "receipt.json"))
        r = client.request(method, url, json={} if method == "POST" else None)
        assert r.status_code == 401, (method, path, r.status_code)


def test_boot_attaches_a_materialiser_offering_only_built_shapes(client):
    from databridge_materialise.platform import shape_status

    body = client.get("/materialise/plans", headers=AUTH_HEADERS).json()
    assert body["available"] is True
    status = shape_status()
    assert body["shapes"] == sorted(s["shape"] for s in status if s["built"])
    not_built = {s["shape"] for s in body["shapes_not_built"]}
    assert not_built == {s["shape"] for s in status if not s["built"]}
    assert {"graph_tensor", "conversational"} <= not_built
    assert all(s["reason"] for s in body["shapes_not_built"])


def test_the_catalogue_is_the_live_platform(client, workspace):
    cat = client.get("/materialise/catalogue", headers=AUTH_HEADERS).json()
    names = {e["name"] for e in cat["entities"]}
    assert {"invoice", "person"} <= names
    assert any(e["origin"] == "facts" for e in cat["entities"])
    assert any(e["origin"] == "passages" for e in cat["entities"])


def test_an_english_request_runs_as_a_job_and_yields_a_downloadable_receipt(client):
    job = _run(client, {"request": FRAUD_REQUEST})
    assert job["status"] == "succeeded", job
    assert job["by"] == "staff"
    assert [e["stage"] for e in job["events"]][-1] == "done"
    result = job["result"]
    assert result["shaped_by"] == "agent" and result["rows"]["emitted"] > 0
    check = result["pointer_check"]
    assert check["grounded_cells"] > 0 and check["all_resolved"] and check["all_match"]

    plan_hash = result["plan_hash"]
    receipt = client.get(f"/materialise/receipts/{plan_hash}", headers=AUTH_HEADERS).json()
    assert receipt["receipt"]["content_hash"] == result["content_hash"]
    data_file = next(f for f in receipt["files"] if f.endswith(".jsonl"))
    got = client.get(f"/materialise/receipts/{plan_hash}/files/{data_file}",
                     headers=AUTH_HEADERS)
    assert got.status_code == 200
    lines = [json.loads(x) for x in got.text.splitlines() if x.strip()]
    assert len(lines) == result["rows"]["emitted"]

    listed = client.get("/materialise/receipts", headers=AUTH_HEADERS).json()["receipts"]
    assert plan_hash in {r["plan_hash"] for r in listed}
    for bad in ("not-listed.txt", "..%2F..%2Fsecrets"):
        r = client.get(f"/materialise/receipts/{plan_hash}/files/{bad}", headers=AUTH_HEADERS)
        assert r.status_code == 404


def test_a_plan_reproduces_its_content_hash(client):
    a, b = _run(client, _plan(client)), _run(client, _plan(client))
    assert a["status"] == b["status"] == "succeeded"
    assert a["result"]["reproducible"] is True
    assert a["result"]["content_hash"] == b["result"]["content_hash"]


def test_a_leaky_plan_is_refused_by_name_not_crashed(client):
    job = _run(client, _plan(client, feature_axis="valid_time"))
    assert job["status"] == "refused"
    assert job["refusal"]["code"] == "FeatureAxisRefused"


def test_a_plan_naming_another_principal_is_refused(client):
    from databridge_materialise.testing import invoice_plan

    job = _run(client, {"plan": invoice_plan(principal="someone-else").model_dump(mode="json")})
    assert job["status"] == "refused" and job["refusal"]["code"] == "PRINCIPAL_MISMATCH"


@pytest.mark.parametrize("body", [{}, {"request": "x", "plan": {}}, {"request": "   "}])
def test_exactly_one_of_request_or_plan(client, body):
    r = client.post("/materialise", json=body, headers=AUTH_HEADERS)
    assert r.status_code == 422 and r.json()["refusal"]["code"] == "BAD_REQUEST"


# ------------------------------------------------------------------------------------------
# E2E run 2: N-08 (a downloadable pointer file) and N-09 (the withheld path, end to end)
# ------------------------------------------------------------------------------------------
def test_n08_the_pointer_file_downloads_and_n09_a_failed_record_withholds_it(client, tmp_path):
    """Publish through the API, download the per-cell pointer sidecar (role ``pointers``, named
    by the receipt's ``pointer_manifest.uri``), then make the run's RECORD fail its pointer check
    on disk (`testing.withhold_published_run`, the state the gate defends) and see the dataset and
    the pointers answer 409 ``DATASET_WITHHELD`` while the receipt and plan stay readable."""
    import os

    from databridge_materialise.testing import withhold_published_run

    job = _run(client, {"request": FRAUD_REQUEST})
    assert job["status"] == "succeeded", job
    plan_hash = job["result"]["plan_hash"]
    roles = job["result"]["file_roles"]
    pointers = [f for f, r in roles.items() if r == "pointers"]
    assert len(pointers) == 1, roles
    receipt = client.get(f"/materialise/receipts/{plan_hash}", headers=AUTH_HEADERS).json()
    assert receipt["receipt"]["pointer_manifest"]["uri"] == pointers[0]
    assert receipt["file_roles"][pointers[0]] == "pointers"

    url = f"/materialise/receipts/{plan_hash}/files/"
    got = client.get(url + pointers[0], headers=AUTH_HEADERS)
    assert got.status_code == 200
    lines = [json.loads(x) for x in got.text.splitlines() if x.strip()]
    grounded = [ln for ln in lines if ln["state"] == "grounded"]
    assert len(grounded) == receipt["receipt"]["cells"]["grounded"] > 0
    assert all(ln["pointer"]["source_id"] for ln in grounded)

    gated = [f for f, r in roles.items() if r in ("dataset", "pointers")]
    assert len(gated) == 2
    withhold_published_run(os.environ["WALNUT_MATERIALISE_DIR"], plan_hash, check="pointer")
    for f in gated:
        r = client.get(url + f, headers=AUTH_HEADERS)
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "DATASET_WITHHELD", (f, r.text)
    for f in ("receipt.json", "plan.yaml"):
        assert client.get(url + f, headers=AUTH_HEADERS).status_code == 200


# ------------------------------------------------------------------------------------------
# E2E run 3, P-10: a planner question is answered back by id
# ------------------------------------------------------------------------------------------
#: A request the offline planner cannot author (it names no outcome), and the requester's answer.
VAGUE_REQUEST = "a table of people"


def test_p10_a_planner_question_is_answered_back_with_the_same_request(client):
    asked = _run(client, {"request": VAGUE_REQUEST})
    assert asked["status"] == "refused" and asked["refusal"]["code"] == "PLAN_NEEDS_INPUT"
    assert asked["refusal"]["reason_code"] == "PlanNeedsInput"
    # E2E5 W-11: the refusal routes to the flow that builds a plain table
    assert "/materialise/designs" in asked["refusal"]["nearest_alternative"]
    items = asked["refusal"]["question_items"]
    assert len(items) == 1 and items[0]["text"] in asked["refusal"]["message"]
    qid = items[0]["id"]
    assert set(asked["refusal"]["answer_with"]["answers"]) == {qid}

    answered = _run(client, {"request": VAGUE_REQUEST, "answers": {qid: FRAUD_REQUEST}})
    assert answered["status"] == "succeeded", answered
    result = answered["result"]
    assert result["answers"] == {qid: FRAUD_REQUEST} and result["shape"] == "tabular"
    receipt = client.get(f"/materialise/receipts/{result['plan_hash']}",
                         headers=AUTH_HEADERS).json()
    assert receipt["platform"]["answers"] == {qid: FRAUD_REQUEST}
    check = result["pointer_check"]
    assert check["grounded_cells"] > 0 and check["all_resolved"] and check["all_match"]


@pytest.mark.parametrize("body", [
    {"request": VAGUE_REQUEST, "answers": {"not-an-id": "x"}},
    {"request": VAGUE_REQUEST, "answers": ["x"]},
    {"plan": {"x": 1}, "answers": {"mat:" + "0" * 40: "x"}},
])
def test_p10_malformed_answers_are_refused_at_the_edge_before_any_job(client, body):
    r = client.post("/materialise", json=body, headers=AUTH_HEADERS)
    assert r.status_code == 422 and r.json()["refusal"]["code"] == "BAD_REQUEST", r.text
    jobs = client.get("/materialise/jobs", headers=AUTH_HEADERS).json()["jobs"]
    assert jobs == [], "no job was started for a refused body"

"""`/materialise/designs` — DSD over HTTP, on BOTH fixture estates (workspace and finance).

Propose a table design in English → edit → approve (the signed-in principal OBJECT, never a name)
→ build by delegation → a raw table whose receipt says what it is, downloadable, every grounded
cell re-read live. Plus the refusals: no credential (every design route, derived from the app's
own routes), no operator grant, an unapproved digest, another principal's design.
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
for _p in (_DATABRIDGE / "packages" / "materialise",
           _DATABRIDGE / "packages" / "materialise" / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from conftest import AUTH_HEADERS, auth_headers  # noqa: E402
from databridge_gateway.public_errors import STANDARD_FOR  # noqa: E402  (R-028 #11)

from databridge_gateway.materialise_routes import (  # noqa: E402
    register_materialise_routes,
    set_materialiser,
)
from databridge_gateway.platform_runtime import (  # noqa: E402
    PlatformRuntime,
    register_gateway_authenticators,
    start_platform,
)

REQUESTS = {
    "workspace": "invoice data to predict which invoices stay open, with the meeting notes, to "
                 "train XGBoost",
    "finance": "facility data for watchlist prediction, with the credit memos, to train XGBoost",
}


@pytest.fixture(params=sorted(REQUESTS))
def world(request, tmp_path, monkeypatch):
    import dsd_estates as de
    from databridge_graph.packs import deployment

    name = request.param
    with deployment(de.ESTATES[name]["pack"]):
        e = de.build_estate(name, tmp_path / "estate", monkeypatch)
        monkeypatch.setenv("WALNUT_FEDERATED_GRANTS", json.dumps({"staff": "*",
                                                                  "analyst-7": "*"}))
        app = FastAPI()
        app.state.graph_store = e.store
        app.state.platform = PlatformRuntime()
        set_materialiser(app, e.materialiser())
        app.state.federated_factory = e.service
        register_materialise_routes(app)
        register_gateway_authenticators()
        asyncio.run(start_platform(app))
        with TestClient(app) as c:
            yield name, e, c
        e.store.close()


def _design_routes(app) -> list[tuple[str, str]]:
    out = []
    for r in app.routes:
        path = getattr(r, "path", "")
        if path.startswith("/materialise/designs"):
            for m in sorted(getattr(r, "methods", ()) or ()):
                out.append((m, path))
    return out


def test_every_design_route_refuses_without_a_credential(world):
    _name, _e, c = world
    routes = _design_routes(c.app)
    assert len(routes) >= 6
    for method, path in routes:
        url = path.replace("{design_id}", "dsg-000000000000").replace("{job_id}", "job-x")
        r = c.request(method, url, json={"request": "x", "digest": "0" * 64})
        assert r.status_code == 401, (method, path, r.status_code)


def test_propose_edit_approve_build_download(world):
    name, e, c = world
    r = c.post("/materialise/designs", json={"request": REQUESTS[name]}, headers=AUTH_HEADERS)
    assert r.status_code == 201, r.text
    doc = r.json()
    d = doc["design"]
    assert d["grain"]["adapter_agent"] == e.cfg["db_agent"] and not d["questions"]
    assert all(col["when_known"] for col in d["columns"])
    # another principal cannot see it
    assert c.get(f"/materialise/designs/{doc['id']}",
                 headers=auth_headers("analyst-7")).status_code == 404
    # an edit (drop one linked column) clears nothing yet approved, and changes the digest
    cols = [col for col in d["columns"] if col["via"] != d["links"][0]["id"]]
    r = c.put(f"/materialise/designs/{doc['id']}", json={"columns": cols, "rev": doc["rev"]},
              headers=AUTH_HEADERS)
    assert r.status_code == 200, r.text
    edited = r.json()
    assert edited["digest"] != doc["digest"] and edited["status"] == "edited"
    # compare-and-set: an edit of the superseded revision is refused; an edit names its revision
    r = c.put(f"/materialise/designs/{doc['id']}", json={"columns": cols, "rev": doc["rev"]},
              headers=AUTH_HEADERS)
    assert r.status_code == 409 and (r.json()["refusal"]["code"], r.json()["refusal"].get("reason_code")) == (STANDARD_FOR["STALE_REVISION"], "STALE_REVISION")
    assert c.put(f"/materialise/designs/{doc['id']}", json={"columns": cols},
                 headers=AUTH_HEADERS).status_code == 422
    # building before approval is refused synchronously
    r = c.post(f"/materialise/designs/{doc['id']}/build", json={"digest": edited["digest"]},
               headers=AUTH_HEADERS)
    assert r.status_code == 409 and r.json()["refusal"]["code"] == "DESIGN_NOT_APPROVED"
    # approving the OLD digest is refused; the current one is approved by the signed-in principal
    r = c.post(f"/materialise/designs/{doc['id']}/approve", json={"digest": doc["digest"]},
               headers=AUTH_HEADERS)
    assert r.status_code == 409 and (r.json()["refusal"]["code"], r.json()["refusal"].get("reason_code")) == (STANDARD_FOR["DIGEST_MISMATCH"], "DIGEST_MISMATCH")
    r = c.post(f"/materialise/designs/{doc['id']}/approve", json={"digest": edited["digest"]},
               headers=AUTH_HEADERS)
    assert r.status_code == 200, r.text
    ok = r.json()
    assert ok["approval"]["principal_id"] == "staff"
    assert ok["approval"]["authenticated_by"] == "gateway"
    r = c.post(f"/materialise/designs/{doc['id']}/build", json={"digest": ok["digest"]},
               headers=AUTH_HEADERS)
    assert r.status_code == 202, r.text
    job = c.get(r.json()["poll"], headers=AUTH_HEADERS).json()
    assert job["status"] == "succeeded", job
    res = job["result"]
    assert res["mode"] == "raw_delegated"
    assert res["statement"] == "raw extract; time as metadata; not leakage-filtered"
    pc = res["pointer_check"]
    assert pc["all_resolved"] and pc["all_match"] and pc["grounded_cells"] > 0
    rec = c.get(f"/materialise/receipts/{res['plan_hash']}", headers=AUTH_HEADERS).json()
    assert rec["receipt"]["design_digest"] == ok["digest"]
    assert {s["adapter_agent"] for s in rec["receipt"]["sources"]} >= {e.cfg["db_agent"]}
    roles = rec["file_roles"]
    data = [f for f, role in roles.items() if role == "dataset"]
    assert any(f.endswith(".csv") for f in data)
    for f, role in roles.items():
        if role in ("dataset", "pointers", "receipt", "plan"):
            got = c.get(f"/materialise/receipts/{res['plan_hash']}/files/{f}",
                        headers=AUTH_HEADERS)
            assert got.status_code == 200, (f, got.text)
    csv_name = next(f for f in data if f.endswith(".csv"))
    body = c.get(f"/materialise/receipts/{res['plan_hash']}/files/{csv_name}",
                 headers=AUTH_HEADERS).text.splitlines()
    assert len(body) - 1 == res["rows"]["emitted"]
    # the run is the caller's own
    assert c.get(f"/materialise/receipts/{res['plan_hash']}",
                 headers=auth_headers("analyst-7")).status_code == 404


def test_no_operator_grant_reads_nothing(world, monkeypatch):
    name, _e, c = world
    monkeypatch.setenv("WALNUT_FEDERATED_GRANTS", json.dumps({"someone-else": "*"}))
    r = c.post("/materialise/designs", json={"request": REQUESTS[name]}, headers=AUTH_HEADERS)
    assert r.status_code == 403 and r.json()["refusal"]["code"] == "DESIGN_NOT_GRANTED"
    assert c.get("/materialise/designs", headers=AUTH_HEADERS).json()["designs"] == []

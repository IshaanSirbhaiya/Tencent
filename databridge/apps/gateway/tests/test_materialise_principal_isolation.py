"""R-021 (first Codex review of the materialisation wiring): the gateway half.

* Finding 1 — jobs, receipts, plans and dataset files are visible only to the principal who
  created them; anyone else gets the same 404 a non-existent run gets.
* Finding 12 — the synchronous materialiser path always passes the signed-in principal, and a
  materialiser that cannot accept one is refused rather than run under its default identity.
* Data-page hand-over #1/#5 — every file carries a server-declared role, a dataset file is not
  served after a failed pointer check, and the receipt carries a server-side plan-hash check.
"""
from __future__ import annotations

import json

from conftest import AUTH_HEADERS, auth_headers  # noqa: F401

from databridge_gateway.materialise_routes import set_materialiser

# The platform-wired fixtures (synthetic workspace, booted runtime) are the other module's.
from test_materialise_platform_routes import _plan, _run, app, client, workspace  # noqa: F401

ALICE, BOB = AUTH_HEADERS, auth_headers("analyst-7")


def test_f1_another_principal_sees_none_of_alices_runs(client):
    job = _run(client, _plan(client))
    assert job["status"] == "succeeded", job
    ph = job["result"]["plan_hash"]
    data = next(f for f, r in job["result"]["file_roles"].items() if r == "dataset")

    # Alice sees her own run everywhere (non-vacuous).
    assert job["job_id"] in {j["job_id"] for j in
                             client.get("/materialise/jobs", headers=ALICE).json()["jobs"]}
    assert ph in {r["plan_hash"] for r in
                  client.get("/materialise/receipts", headers=ALICE).json()["receipts"]}
    assert client.get(f"/materialise/receipts/{ph}", headers=ALICE).status_code == 200
    assert client.get(f"/materialise/receipts/{ph}/files/{data}",
                      headers=ALICE).status_code == 200

    # Bob sees nothing of it: not listed, and 404 exactly like a run that does not exist.
    assert job["job_id"] not in {j["job_id"] for j in
                                 client.get("/materialise/jobs", headers=BOB).json()["jobs"]}
    assert client.get(f"/materialise/jobs/{job['job_id']}", headers=BOB).status_code == 404
    assert ph not in {r["plan_hash"] for r in
                      client.get("/materialise/receipts", headers=BOB).json()["receipts"]}
    assert ph not in {p.get("plan_hash") for p in
                      client.get("/materialise/plans", headers=BOB).json()["plans"]}
    for url in (f"/materialise/receipts/{ph}", f"/materialise/receipts/{ph}/files/{data}",
                f"/materialise/receipts/{ph}/files/receipt.json",
                f"/materialise/receipts/{ph}/files/plan.yaml"):
        r = client.get(url, headers=BOB)
        assert r.status_code == 404, (url, r.status_code)
        assert "refusal" in r.json()


def test_f12_a_materialiser_that_cannot_take_the_principal_is_refused(app, client):
    class Legacy:
        def plans(self):
            return []

        def run(self, plan):                       # no `by`: would run as its own identity
            raise AssertionError("must never be called")

    set_materialiser(app, Legacy())
    r = client.post("/materialise", json={"shape": "tabular"}, headers=ALICE)
    assert r.status_code == 501
    assert r.json()["refusal"]["code"] == "MATERIALISER_NOT_PRINCIPAL_AWARE"


def test_f12_the_synchronous_path_runs_as_the_signed_in_principal(app, client):
    class Sync:
        def plans(self):
            return []

        def run(self, plan, *, by):
            return {"plan_hash": "abc", "ran_as": by}

    set_materialiser(app, Sync())
    r = client.post("/materialise", json={"shape": "tabular"}, headers=BOB)
    assert r.status_code == 200 and r.json()["ran_as"] == "analyst-7"


def test_file_roles_and_the_plan_hash_check_are_served(client):
    job = _run(client, _plan(client))
    ph = job["result"]["plan_hash"]
    doc = client.get(f"/materialise/receipts/{ph}", headers=ALICE).json()
    assert set(doc["file_roles"]) == set(doc["files"])
    assert doc["file_roles"]["receipt.json"] == "receipt"
    assert doc["file_roles"]["plan.yaml"] == "plan"
    assert doc["plan_verification"] == {"plan_hash": ph, "recomputed": ph, "matches": True}
    listed = client.get("/materialise/receipts", headers=ALICE).json()["receipts"]
    assert next(r for r in listed if r["plan_hash"] == ph)["file_roles"] == doc["file_roles"]


def test_a_dataset_file_is_withheld_when_the_pointer_check_is_not_on_record(client, workspace,
                                                                            tmp_path):
    job = _run(client, _plan(client))
    ph = job["result"]["plan_hash"]
    data = next(f for f, r in job["result"]["file_roles"].items() if r == "dataset")
    receipt = tmp_path / "materialise" / ph / "receipt.json"
    doc = json.loads(receipt.read_text(encoding="utf-8"))
    doc["platform"].pop("pointer_check")                   # a run from before the gate
    receipt.write_text(json.dumps(doc), encoding="utf-8")
    r = client.get(f"/materialise/receipts/{ph}/files/{data}", headers=ALICE)
    assert r.status_code == 409 and r.json()["refusal"]["code"] == "DATASET_WITHHELD"
    assert client.get(f"/materialise/receipts/{ph}/files/receipt.json",
                      headers=ALICE).status_code == 200


def test_a_dataset_file_is_withheld_when_the_leak_check_contradicts_itself(client, workspace,
                                                                           tmp_path):
    job = _run(client, _plan(client))
    ph = job["result"]["plan_hash"]
    data = next(f for f, r in job["result"]["file_roles"].items() if r == "dataset")
    receipt = tmp_path / "materialise" / ph / "receipt.json"
    doc = json.loads(receipt.read_text(encoding="utf-8"))
    doc["receipt"]["missingness_leak_check"] = {"passed": True, "threshold": 0.05,
                                                "max_class_delta_observed": 0.5}
    receipt.write_text(json.dumps(doc), encoding="utf-8")
    r = client.get(f"/materialise/receipts/{ph}/files/{data}", headers=ALICE)
    assert r.status_code == 409 and "leak check" in r.json()["refusal"]["message"]


# =================================================================================================
# R-021 round 2
# =================================================================================================
def test_r2_f6_an_identical_request_from_another_principal_never_discloses_the_running_job(
        app, client, monkeypatch):
    """Finding 6: the busy-job key is per principal. Bob's identical body while Alice's job is
    running is Bob's own job — never a 409 carrying Alice's job id (an existence oracle)."""
    import databridge_gateway.materialise_routes as routes

    class Jobbed:
        def plans(self):
            return []

        def receipts(self, *, by=None):
            return []

        def run(self, body, *, by, progress=None):
            raise AssertionError("the job is held running; run is not reached")

    set_materialiser(app, Jobbed())
    monkeypatch.setattr(routes, "_run_job", lambda *a, **kw: None)    # jobs stay running
    body = {"request": "one row per invoice at issue time"}
    alice = client.post("/materialise", json=body, headers=ALICE)
    assert alice.status_code == 202, alice.text
    bob = client.post("/materialise", json=body, headers=BOB)
    assert bob.status_code == 202, bob.text
    assert bob.json()["job_id"] != alice.json()["job_id"]
    again = client.post("/materialise", json=body, headers=ALICE)
    assert again.status_code == 409
    assert again.json()["refusal"]["job_id"] == alice.json()["job_id"], (
        "non-vacuous: the busy answer names the caller's OWN running job")


def test_r2_f3_the_catalogue_serves_the_pins_a_plan_must_carry(client):
    """Finding 3: a submitted plan must pin every source it reads; the catalogue read at the
    plan's build instant serves exactly those pins, and a naive instant is refused."""
    from databridge_materialise.testing import BUILD_TIME

    r = client.get("/materialise/catalogue", params={"as_of": BUILD_TIME.isoformat()},
                   headers=ALICE)
    assert r.status_code == 200
    doc = r.json()
    assert doc["system_as_of"] == BUILD_TIME.isoformat()
    assert doc["sources"] and all(s["as_of"].startswith("2026-09-30") for s in doc["sources"])
    bad = client.get("/materialise/catalogue", params={"as_of": "2026-09-30T00:00:00"},
                     headers=ALICE)
    assert bad.status_code == 422 and bad.json()["refusal"]["code"] == "BAD_REQUEST"

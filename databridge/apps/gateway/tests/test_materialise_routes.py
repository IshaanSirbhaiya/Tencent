"""`/materialise` — the endpoint whose correct answer today is `501`.

A receipt is a set of measurements: rows resolved, pointers followed, cells withheld, verdicts
returned, under a plan hash that claims to be the dataset's identity. None of those can be measured
until the materialiser and an adapter fleet exist (`PHASE_PLAN.md` Phases 3 and 6). So the tests
guard the honesty rather than the feature:

1. a `POST` returns **501**, names the missing components, and returns **no receipt of any shape**;
2. `GET /materialise/plans` offers **no shapes**, because a shape in a picker is a shape the system
   claims it can produce;
3. registering a real materialiser flips both routes with no other change — the 501 is a plug, not
   a wall.
"""
from __future__ import annotations

import pytest
from conftest import AUTH_HEADERS
from fastapi import FastAPI
from fastapi.testclient import TestClient

from databridge_gateway.materialise_routes import (
    MISSING_COMPONENTS,
    register_materialise_routes,
    set_materialiser,
)

#: Anything that would make a refusal look like a receipt.
RECEIPT_SHAPED = ("plan_hash", "rows", "pointers", "withheld", "verdicts", "outputs", "receipt")


@pytest.fixture()
def app():
    a = FastAPI()
    register_materialise_routes(a)
    return a


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


def test_a_plan_is_refused_with_a_501_naming_what_is_missing(client):
    r = client.post("/materialise", json={"shape": "tabular", "entity": "party"}, headers=AUTH_HEADERS)
    assert r.status_code == 501
    refusal = r.json()["refusal"]
    assert refusal["code"] == "MATERIALISER_NOT_IMPLEMENTED"
    assert refusal["missing"] == list(MISSING_COMPONENTS)
    assert refusal["missing"], "a 501 that does not say what is missing is just a failure"


def test_the_refusal_contains_nothing_a_reader_could_mistake_for_a_receipt(client):
    body = client.post("/materialise", json={"shape": "tabular"}, headers=AUTH_HEADERS).json()
    keys = set(body["refusal"])
    assert not keys & set(RECEIPT_SHAPED)
    # The plan is echoed so the caller can see what was refused — that is not a receipt.
    assert body["refusal"]["plan_received"] == {"shape": "tabular"}


def test_no_shape_is_offered_that_nothing_can_execute(client):
    body = client.get("/materialise/plans", headers=AUTH_HEADERS).json()
    assert body["available"] is False
    assert body["plans"] == []
    assert body["shapes"] == []
    assert body["missing"] == list(MISSING_COMPONENTS)


def test_registering_a_materialiser_flips_both_routes(app, client):
    """The 501 is a plug: Phase 6 calls `set_materialiser` and nothing else changes."""

    class Fake:
        def plans(self):
            return [{"id": "p1", "shape": "tabular", "entity": "party"}]

        def run(self, plan, *, by):
            return {"plan_hash": "abc", "rows": 0, "echo": plan}

    set_materialiser(app, Fake())
    listed = client.get("/materialise/plans", headers=AUTH_HEADERS).json()
    assert listed["available"] is True
    assert listed["shapes"] == ["tabular"]
    ran = client.post("/materialise", json={"shape": "tabular"}, headers=AUTH_HEADERS)
    assert ran.status_code == 200
    assert ran.json()["plan_hash"] == "abc"


def test_a_materialiser_that_raises_issues_no_receipt(app, client):
    class Broken:
        def plans(self):
            return []

        def run(self, plan, *, by):
            raise RuntimeError("the source went away")

    set_materialiser(app, Broken())
    r = client.post("/materialise", json={}, headers=AUTH_HEADERS)
    assert r.status_code == 503
    assert r.json()["refusal"]["code"] == "MATERIALISATION_FAILED"
    assert set(r.json()["refusal"]) & set(RECEIPT_SHAPED) == set()

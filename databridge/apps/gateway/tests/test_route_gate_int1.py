"""INT1 item 2 — every route on the gateway requires a principal, except the named public ones.

Built from the app's OWN route list (the F-25 pattern in `test_gateway.py`, widened from main.py's
/brain routes to every module): a route added later without `Depends(require_principal)` fails
here until it is gated or deliberately named public below, with its reason.
"""
from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from conftest import AUTH_HEADERS
from databridge_gateway.auth import require_principal
from databridge_gateway.main import create_app

#: (method, path) -> why it is served anonymously. Keep this list short and every entry argued.
PUBLIC = {
    ("GET", "/health"): "liveness + which LLM backend answers; carries no user data",
    # `/evals/e1` and `/evals/brain` are no longer exempt: they were canned gold-backend scores
    # and are UNWIRED (R-019 round 2, main #3 — unwired, not deleted); they 404 for everyone.
}


@pytest.fixture(scope="module")
def app():
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:        # `with` runs the lifespan
        yield c.app


def _routes(app) -> list[tuple[str, APIRoute]]:
    return [(m, r) for r in app.routes if isinstance(r, APIRoute)
            for m in sorted(r.methods - {"HEAD", "OPTIONS"})]


def _requires_principal(dependant) -> bool:
    return dependant.call is require_principal or any(
        _requires_principal(d) for d in dependant.dependencies)


def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def test_the_public_list_names_only_routes_that_exist(app):
    """A stale allow-list entry would let a future route of that name through unexamined."""
    present = {(m, r.path) for m, r in _routes(app)}
    assert set(PUBLIC) <= present, sorted(set(PUBLIC) - present)


def test_every_route_on_the_app_declares_the_principal_dependency(app):
    gated = [(m, r) for m, r in _routes(app) if (m, r.path) not in PUBLIC]
    # non-vacuous: the check spans many route modules, not one
    assert len({r.endpoint.__module__ for _m, r in gated}) >= 10
    missing = [(m, r.path, r.endpoint.__module__) for m, r in gated
               if not _requires_principal(r.dependant)]
    assert missing == []


def test_every_route_on_the_app_refuses_an_anonymous_caller(app):
    anon = TestClient(app)                     # same booted app, no credential
    ungated = []
    checked = 0
    for method, route in _routes(app):
        if (method, route.path) in PUBLIC:
            continue
        resp = anon.request(method, _url(route.path), json={})
        checked += 1
        if resp.status_code != 401 or "refusal" not in resp.json():
            ungated.append((method, route.path, resp.status_code))
    assert checked >= len(_routes(app)) - len(PUBLIC)                  # every one was asked
    assert ungated == []


@pytest.mark.parametrize("path", ["/brain/export/sheets", "/intake", "/outbox", "/mcp/status",
                                  "/packs", "/ontology"])
def test_the_routes_int1_gated_still_answer_a_signed_in_caller(app, path):
    """Gating must not break them: a credential gets past the gate (whatever the route's own
    answer is, it is not the auth refusal)."""
    resp = TestClient(app, headers=AUTH_HEADERS).get(path)
    assert resp.status_code != 401, resp.text[:200]

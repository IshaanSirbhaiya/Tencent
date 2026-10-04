"""R-029 TIDY2 item 3: the identity-rules approval route speaks the standard refusals.

* A machine actor is refused with the standard ``MACHINE_ACTOR`` (CONS standard 3), never the old
  ``UNAUTHENTICATED_APPROVER``; a principal naming nobody is ``APPROVER_NOT_AUTHENTICATED``.
  Expectations derive from the core machine rule (`decision_ledger.is_machine_principal`).
* No exception text reaches a refusal (CONS standard 4): a fixed message and an ``error_ref``;
  the detail goes to the server log. Proven with a sentinel planted in the exception.
On both estates (the LINK7 fixture).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import auth_headers
from test_link7_identity_rules import _rule, estate  # noqa: F401  (fixture)
from test_platform_routes import env  # noqa: F401  (fixture)

SENTINEL = "SENTINEL-c0ffee C:\\server\\secret\\path.db OperationalError: no such table"


def _as(app, pid):
    from databridge_gateway.auth import AuthenticatedPrincipal, require_principal

    app.dependency_overrides[require_principal] = lambda: AuthenticatedPrincipal(principal_id=pid)


def test_a_machine_actor_is_refused_as_machine_actor(estate):  # noqa: F811
    from databridge_core.decision_ledger import MACHINE_PREFIXES, is_machine_principal

    est, app = estate["est"], estate["app"]
    body = {"pack": est.pack, "kind": est.org_kind, "rule": "exact_name"}
    machines = [f"{p}runner-1" if p.endswith(":") else p for p in MACHINE_PREFIXES]
    assert machines and all(is_machine_principal(m) for m in machines)      # non-vacuous
    try:
        for pid in machines:
            _as(app, pid)
            with TestClient(app) as c:
                r = c.post("/ontology/identity-rules/decide", json=body)
            assert r.status_code == 403, r.text
            assert r.json()["refusal"]["code"] == "MACHINE_ACTOR", (pid, r.text)
        _as(app, "  ")
        with TestClient(app) as c:
            r = c.post("/ontology/identity-rules/decide", json=body)
        assert r.status_code == 403
        assert r.json()["refusal"]["code"] == "APPROVER_NOT_AUTHENTICATED", r.text
    finally:
        app.dependency_overrides.clear()
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        rules = _rule(c.get("/ontology/identity-rules").json(), est.org_kind)["rules"]
    assert all(t["state"] == "not_approved" for t in rules)               # nothing recorded


def test_no_exception_text_in_any_refusal(estate, monkeypatch):  # noqa: F811
    from databridge_graph import ontology_state

    from databridge_gateway import identity_rules_routes as irr

    est, app = estate["est"], estate["app"]
    body = {"pack": est.pack, "kind": est.org_kind, "rule": "exact_name"}

    def bad_decision(**_k):
        raise ValueError(SENTINEL)

    monkeypatch.setattr(ontology_state, "record_identity_rule_decision", bad_decision)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        r = c.post("/ontology/identity-rules/decide", json=body)
    assert r.status_code == 409
    ref = r.json()["refusal"]
    assert ref["code"] == "IDENTITY_RULE_REFUSED" and ref["message"]
    assert ref["error_ref"].startswith("ERR-")
    assert "SENTINEL" not in r.text and "secret" not in r.text and "Error" not in r.text

    # the preview of one rule fails: its note is fixed, the rest of the listing still answers
    monkeypatch.setattr(irr, "preview", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError(SENTINEL)))
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        r = c.get("/ontology/identity-rules")
    assert r.status_code == 200
    notes = [t for x in r.json()["identity_rules"] for t in x["rules"] if t["preview_note"]]
    assert notes                                                           # non-vacuous
    assert all(t["preview_error_ref"].startswith("ERR-") for t in notes)
    assert "SENTINEL" not in r.text and "RuntimeError" not in r.text

    # the whole listing fails
    monkeypatch.setattr(irr, "listing", lambda _app: (_ for _ in ()).throw(OSError(SENTINEL)))
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        r = c.get("/ontology/identity-rules")
    assert r.status_code == 500
    ref = r.json()["refusal"]
    assert ref["code"] == "IDENTITY_RULES_UNREADABLE" and ref["error_ref"].startswith("ERR-")
    assert "SENTINEL" not in r.text and "OSError" not in r.text

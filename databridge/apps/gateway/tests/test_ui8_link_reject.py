"""UI8 (R-026 ID7 cross-track 2): the gateway's link routes are the ENGINE's decisions.

`POST /sources/{id}/links/reject` calls `mapping.reject_link` (ID7 moved it into the engine); the
gateway's own copy and its after-the-fact `mark_decider` are gone, because `mapping.approve_link`
and `mapping.reject_link` record the decider and how they signed in themselves.

Two estates (workspace/Larkspur and finance), through LINK7's own seeded link proposal.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import auth_headers
from test_link7_link_review import (LEFT, _seed_link, dev_auth, estate,  # noqa: F401
                                    estate_snaps)
from test_platform_routes import _cmd, _mount, env  # noqa: F401

from databridge_gateway import auth


def test_the_reject_route_is_the_engine_s_reject_link(estate, monkeypatch):
    """The route calls `mapping.reject_link` with the principal OBJECT (never a name), and its
    result is the engine's."""
    from databridge_mcp_ingest import mapping

    t, fk, _ = _seed_link(estate, monkeypatch)
    calls: list[dict] = []
    real = mapping.reject_link

    def spy(source_id, table, column, **kw):
        calls.append({"source_id": source_id, "table": table, "column": column, **kw})
        return real(source_id, table, column, **kw)

    monkeypatch.setattr(mapping, "reject_link", spy)
    app, _, _ = _mount(estate["env"])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, LEFT).status_code == 200
        r = c.post(f"/sources/{LEFT}/links/reject",
                   json={"table": t, "column": fk["column"], "reason": "not the same ids",
                         "rejected_by": "mallory"})
        assert r.status_code == 200, r.text
    [call] = calls
    assert (call["source_id"], call["table"], call["column"]) == (LEFT, t, fk["column"])
    assert getattr(call["principal"], "principal_id", None) == "analyst-7"   # the object
    assert call["reason"] == "not the same ids"
    [lk] = [x for x in mapping.load_mapping(LEFT).tables[t].links if x["column"] == fk["column"]]
    assert lk["status"] == "rejected" and lk["rejected_by"] == "analyst-7"
    assert lk["decided_authenticated_by"] and lk["decided_dev_only"] is False


def test_a_link_rejected_with_the_dev_token_is_marked_by_the_engine(estate, monkeypatch,
                                                                     dev_auth):
    from databridge_mcp_ingest.mapping import load_mapping

    t, fk, _ = _seed_link(estate, monkeypatch)
    app, _, _ = _mount(estate["env"])
    with TestClient(app, headers=dev_auth) as c:
        assert _cmd(c, LEFT).status_code == 200
        r = c.post(f"/sources/{LEFT}/links/reject", json={"table": t, "column": fk["column"]})
        assert r.status_code == 200, r.text
        [row] = [x for x in c.get(f"/sources/{LEFT}/mapping/review").json()["links"]
                 if x["column"] == fk["column"]]
    [lk] = [x for x in load_mapping(LEFT).tables[t].links if x["column"] == fk["column"]]
    assert lk["rejected_by"] == auth.DEV_PRINCIPAL_ID and lk["decided_dev_only"] is True
    assert lk["decided_authenticated_by"] == auth.DEV_MECHANISM
    assert row["decided_dev_only"] is True and row["status"] == "rejected"


def test_the_gateway_keeps_no_duplicate_of_the_engine_s_link_decisions():
    """Structural: the review module only READS; deciding a link is the engine's."""
    from databridge_gateway import mapping_review

    assert not hasattr(mapping_review, "reject_link")
    assert not hasattr(mapping_review, "mark_decider")

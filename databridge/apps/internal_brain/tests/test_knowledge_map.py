"""/ib/map lists only what the caller may open, is identical whether or not restricted documents
exist (P2), and is written to the access ledger before it is returned."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from databridge_ib import auth as authmod
from databridge_ib.app import Services, create_app
from databridge_ib.index import Index
from databridge_ib.ingest import Ingestor
from databridge_ib.pipeline import Pipeline

from test_mcp_server import _svc
from test_pipeline_core import CANARY, PRIVATE, PUBLIC, READERS, FakeConnector, MemAnswers, MemLedger


def _app(tmp_path, docs, readers, ledger=None):
    base = _svc(tmp_path)
    for uid in ("priya", "ken.ext"):                 # every caller here is an employee
        base.directory._users[uid] = base.directory.get(uid).model_copy(
            update={"roles": ("employee",)})
    conn = FakeConnector("slack", docs, readers)
    idx = Index(tmp_path / f"map-{len(docs)}.sqlite")
    for d in docs:
        idx.upsert(d, conn.acl(d.doc_id))
    led = ledger or MemLedger()
    pipe = Pipeline(index=idx, directory=base.directory, connectors={"slack": conn}, ledger=led,
                    answers=MemAnswers())
    svc = Services(cfg=base.cfg, directory=base.directory, index=idx, connectors={"slack": conn},
                   ledger=led, answers=MemAnswers(), pipeline=pipe,
                   ingestor=Ingestor(idx, {"slack": conn}, tmp_path / "c.json",
                                     directory=base.directory))
    svc.conn = conn
    return create_app(svc), svc


def _map(app, svc, sub):
    tok = authmod.mint(svc.cfg.token_key, sub, ["employee"])
    r = TestClient(app, headers={"Authorization": f"Bearer {tok}"}).get("/ib/map")
    return r


def _strip(body: dict) -> str:
    body = dict(body)
    body.pop("as_of")
    return json.dumps(body, sort_keys=True)


def test_map_lists_only_what_the_caller_can_open(tmp_path):
    app, svc = _app(tmp_path, [PUBLIC, PRIVATE], READERS)
    body = _map(app, svc, "jdoe").json()
    ids = [i["doc_id"] for p in body["platforms"] for c in p["containers"] for i in c["items"]]
    assert ids == ["slack:t1"]
    assert CANARY not in json.dumps(body) and "leads only" not in json.dumps(body)
    assert body["totals"] == {"items": 1, "containers": 1, "platforms": 1}
    full = _map(app, svc, "priya").json()
    assert full["totals"]["items"] == 2


def test_map_is_identical_with_and_without_restricted_docs(tmp_path):
    a, sa = _app(tmp_path / "a", [PUBLIC, PRIVATE], READERS)
    b, sb = _app(tmp_path / "b", [PUBLIC], {"slack:t1": READERS["slack:t1"]})
    for who in ("jdoe", "ken.ext"):
        assert _strip(_map(a, sa, who).json()) == _strip(_map(b, sb, who).json())


def test_map_view_is_ledgered_before_it_is_returned(tmp_path):
    app, svc = _app(tmp_path, [PUBLIC, PRIVATE], READERS)
    assert _map(app, svc, "jdoe").status_code == 200
    ev = [e for e in svc.ledger.events if getattr(e, "kind", "") == "map.viewed"]
    assert len(ev) == 1 and ev[0].sub == "jdoe" and ev[0].docs_listed == 1


def test_ledger_down_means_no_map(tmp_path):
    class Down(MemLedger):
        def append(self, ev):
            raise RuntimeError("ledger down")
    app, svc = _app(tmp_path, [PUBLIC, PRIVATE], READERS, ledger=Down())
    r = _map(app, svc, "jdoe")
    assert r.status_code == 503 and "t1" not in r.text


def test_revocation_with_a_lost_webhook_is_honoured_by_the_map(tmp_path):
    """The index still says jdoe may read t1, but the SOURCE no longer does: the live check wins."""
    app, svc = _app(tmp_path, [PUBLIC, PRIVATE], READERS)
    svc.conn.readers["slack:t1"].discard("jdoe")          # revoked at the source, index not told
    body = _map(app, svc, "jdoe").json()
    assert body["totals"] == {"items": 0, "containers": 0, "platforms": 0}
    assert "t1" not in json.dumps(body) and "migration status" not in json.dumps(body)


def test_map_event_records_items_actually_returned(tmp_path):
    app, svc = _app(tmp_path, [PUBLIC, PRIVATE], READERS)
    _map(app, svc, "priya")
    ev = [e for e in svc.ledger.events if getattr(e, "kind", "") == "map.viewed"][-1]
    assert ev.docs_listed == 2 and ev.items_returned == 2

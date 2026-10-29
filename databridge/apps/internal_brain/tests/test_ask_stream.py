"""B12 POST /ib/ask/stream (SSE): real stage events, the /ib/ask answer only after release (D-08),
no counts/ids/reasons in stage events (rule 4), the same ledger behaviour as /ib/ask."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from databridge_ib import auth as authmod
from databridge_ib.app import create_app
from databridge_ib.audit.ledger import LedgerUnavailable
from databridge_ib.pipeline import STAGES

from test_mcp_server import _svc

PERMITTED = "what is the database migration status and blockers?"
RESTRICTED = "what is the migration vendor contract budget overrun?"     # in a doc jdoe cannot read
MADE_UP = "what is the parking policy at the Lisbon office?"             # exists nowhere


def _app(tmp_path):
    svc = _svc(tmp_path)
    return create_app(svc), svc


def _as(app, svc, sub, roles=("employee",)):
    tok = authmod.mint(svc.cfg.token_key, sub, list(roles))
    return TestClient(app, headers={"Authorization": f"Bearer {tok}"})


def parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        ev = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        out.append((ev, data))
    return out


def _stream(c, q):
    r = c.post("/ib/ask/stream", json={"question": q})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    return parse_sse(r.text)


def _strip(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in ("request_id", "as_of")}


def test_event_order_and_stage_payloads(tmp_path):
    app, svc = _app(tmp_path)
    evs = _stream(_as(app, svc, "jdoe"), PERMITTED)
    names = [e for e, _ in evs]
    assert names == ["stage"] * len(STAGES) + ["answer", "done"]
    assert [d["name"] for e, d in evs if e == "stage"] == list(STAGES)
    for e, d in evs:
        if e == "stage":                    # names only: no counts, ids or reasons (rule 4)
            assert d == {"name": d["name"], "ok": True}
    assert evs[-1] == ("done", {})


def test_no_answer_text_before_release(tmp_path):
    app, svc = _app(tmp_path)
    evs = _stream(_as(app, svc, "jdoe"), PERMITTED)
    answer = next(d for e, d in evs if e == "answer")["answer"]
    i_ans = next(i for i, (e, _) in enumerate(evs) if e == "answer")
    i_rel = next(i for i, (e, d) in enumerate(evs) if e == "stage" and d["name"] == "released")
    assert i_rel < i_ans
    for _, d in evs[:i_ans]:
        assert answer[:20] not in json.dumps(d)


def test_answer_equals_the_ask_payload(tmp_path):
    app, svc = _app(tmp_path)
    c = _as(app, svc, "jdoe")
    plain = c.post("/ib/ask", json={"question": PERMITTED}).json()
    streamed = next(d for e, d in _stream(c, PERMITTED) if e == "answer")
    assert set(streamed) == set(plain)
    assert _strip(streamed) == _strip(plain)
    assert plain["citations"]                               # a real answer, not a refusal


def test_restricted_and_made_up_give_identical_event_sequences(tmp_path):
    app, svc = _app(tmp_path)
    c = _as(app, svc, "jdoe")
    a, b = _stream(c, RESTRICTED), _stream(c, MADE_UP)
    assert [(e, d) for e, d in a if e != "answer"] == [(e, d) for e, d in b if e != "answer"]
    ans = lambda evs: _strip(next(d for e, d in evs if e == "answer"))      # noqa: E731
    assert ans(a) == ans(b)
    # and the same sequence as a permitted answer: the stages never reveal the outcome
    p = _stream(c, PERMITTED)
    assert [(e, d) for e, d in p if e != "answer"] == [(e, d) for e, d in a if e != "answer"]


def test_ledger_records_match_ask(tmp_path):
    app, svc = _app(tmp_path)
    c = _as(app, svc, "jdoe")

    def records():
        return [(e.kind, e.data.get("channel")) for e in svc.ledger.events()]
    n0 = len(records())
    c.post("/ib/ask", json={"question": PERMITTED})
    n1 = len(records())
    _stream(c, PERMITTED)
    after = records()
    assert after[n0:n1] == after[n1:]
    assert [k for k, _ in after[n1:]] == ["request.accepted", "retrieval.decided", "answer.released"]


def test_ledger_down_is_an_error_event_and_nothing_released(tmp_path, monkeypatch):
    app, svc = _app(tmp_path)
    c = _as(app, svc, "jdoe")

    def refuse(_ev):
        raise LedgerUnavailable("X", "down")
    monkeypatch.setattr(svc.ledger, "append", refuse)
    evs = _stream(c, PERMITTED)
    assert evs == [("error", {"error": "temporarily unavailable"})]


def test_failure_at_release_sends_no_answer(tmp_path, monkeypatch):
    app, svc = _app(tmp_path)
    c = _as(app, svc, "jdoe")
    real = svc.ledger.append

    def fail_release(ev):
        if ev.kind == "answer.released":
            raise LedgerUnavailable("X", "down")
        return real(ev)
    monkeypatch.setattr(svc.ledger, "append", fail_release)
    evs = _stream(c, PERMITTED)
    assert [e for e, _ in evs][-1] == "error" and "answer" not in [e for e, _ in evs]
    assert "released" not in [d.get("name") for e, d in evs if e == "stage"]
    assert c.post("/ib/ask", json={"question": PERMITTED}).status_code == 503   # same as /ib/ask


def test_auth_and_roles_as_ask(tmp_path):
    app, svc = _app(tmp_path)
    assert TestClient(app).post("/ib/ask/stream", json={"question": PERMITTED}).status_code == 401
    svc.directory._users["ken.ext"] = svc.directory.get("ken.ext").model_copy(update={"roles": ()})
    nobody = _as(app, svc, "ken.ext", roles=())
    assert nobody.post("/ib/ask/stream", json={"question": PERMITTED}).status_code == 403
    jd = _as(app, svc, "jdoe")
    assert jd.post("/ib/ask/stream", json={"question": "x", "user": "priya"}).status_code == 422


def test_ask_without_on_stage_is_unchanged(tmp_path):
    app, svc = _app(tmp_path)
    seen = []
    r = svc.pipeline.ask(svc.directory.get("jdoe"), PERMITTED, on_stage=seen.append)
    assert seen == list(STAGES) and r.citations
    r2 = svc.pipeline.ask(svc.directory.get("jdoe"), PERMITTED)
    assert _strip(json.loads(r.model_dump_json())) == _strip(json.loads(r2.model_dump_json()))

    def boom(name):
        raise RuntimeError("client gone")
    r3 = svc.pipeline.ask(svc.directory.get("jdoe"), PERMITTED, on_stage=boom)
    assert _strip(json.loads(r3.model_dump_json())) == _strip(json.loads(r2.model_dump_json()))


def test_replay_switch_is_the_same_guarded_switch(tmp_path):
    """The stream honours the replay switch only under /ib/ask's conditions: here (no IB_REPLAY_KEY
    on the server, not loopback) it is a 403 before any stream starts, and nothing is recorded."""
    app, svc = _app(tmp_path)
    c = _as(app, svc, "jdoe")
    n = len(list(svc.ledger.events()))
    r = c.post("/ib/ask/stream", json={"question": PERMITTED},
               headers={"X-IB-Replay-Key": "x" * 32, "X-IB-Replay-At": "2026-10-01T00:00:00+00:00"})
    assert r.status_code == 403
    assert len(list(svc.ledger.events())) == n

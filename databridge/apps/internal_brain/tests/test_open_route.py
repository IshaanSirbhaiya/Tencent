"""``/ib/open/<token>``, round-4 review fixes (docs/finals/reviews/round4_codex_cloud_work.md A4, R1).

- after the write-ahead ``doc.viewed``, ANY error in the access check is byte-identical to the
  unknown-token 404 (differential test: a resolving token must not be told apart by a 503);
- a per-person rate limit like the sibling Act routes; only the first refusal per window is
  ledgered, and a refused click writes no ``doc.viewed``;
- a click does not compute an HMAC per indexed document (the token map is built once per index
  version).

Same world and helpers as test_act.py.
"""
from __future__ import annotations

import re

from databridge_ib import actions as actmod
from databridge_ib import authz

from test_act import _ask, _c, _events, _propose, _world

LINK = re.compile(r"Source \d+: (\S+)")
UNKNOWN = "/ib/open/" + "0" * 32


def _paths(p):
    return ["/ib/open/" + u.rsplit("/", 1)[1] for u in LINK.findall(p["description"])]


def _shape(r):
    return (r.status_code, r.content, r.headers.get("content-type"), r.headers.get("location"))


def test_an_access_check_error_is_the_unknown_token_404(tmp_path, monkeypatch):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    path = _paths(_propose(jd, _ask(jd)["request_id"]))[0]
    assert jd.get(path, follow_redirects=False).status_code == 303        # it resolves
    unknown = _shape(jd.get(UNKNOWN, follow_redirects=False))
    assert unknown[0] == 404

    def boom(*a, **k):
        raise RuntimeError("index.readable failed")
    monkeypatch.setattr(authz, "authorize", boom)
    assert _shape(jd.get(path, follow_redirects=False)) == unknown
    monkeypatch.undo()
    # the same through Gate 1 itself (index.readable raising inside authorize)
    monkeypatch.setattr(svc.index, "readable", boom)
    assert _shape(jd.get(path, follow_redirects=False)) == unknown
    # a source check that raises is the same again
    monkeypatch.undo()
    for conn in svc.connectors.values():
        monkeypatch.setattr(conn, "check_access", boom)
    assert _shape(jd.get(path, follow_redirects=False)) == unknown
    # each of those was audited first (write-ahead), under the resolved document
    assert len(_events(svc, "doc.viewed")) == 5


def test_open_is_rate_limited_per_person_and_ledgered_once_per_window(tmp_path):
    app, svc = _world(tmp_path)
    svc.actions._open_rate = actmod._Limiter(2, 60)
    jd, pr = _c(app, svc, "jdoe"), _c(app, svc, "priya")
    for _ in range(2):
        assert jd.get(UNKNOWN, follow_redirects=False).status_code == 404
    viewed = len(_events(svc, "doc.viewed"))
    for _ in range(6):
        r = jd.get(UNKNOWN, follow_redirects=False)
        assert r.status_code == 429 and r.json() == {"detail": "too many requests"}
    assert len(_events(svc, "doc.viewed")) == viewed                # refused: no view row
    rate = [f for f in _events(svc, "action.failed") if f.stage == "rate"]
    assert len(rate) == 1 and rate[0].reason_code == "open_rate_limited" and rate[0].sub == "jdoe"
    assert pr.get(UNKNOWN, follow_redirects=False).status_code == 404   # others unaffected


def test_a_click_does_not_hash_every_indexed_document(tmp_path, monkeypatch):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    path = _paths(_propose(jd, _ask(jd)["request_id"]))[0]
    assert jd.get(path, follow_redirects=False).status_code == 303
    calls = []
    real = actmod.open_token
    monkeypatch.setattr(actmod, "open_token", lambda k, d: calls.append(d) or real(k, d))
    for _ in range(3):
        assert jd.get(path, follow_redirects=False).status_code == 303
        assert jd.get(UNKNOWN, follow_redirects=False).status_code == 404
    assert calls == []                              # one dict lookup per click
    # an index change rebuilds the map once, and a removed document no longer resolves
    doc_id = svc.actions.resolve_open(path.rsplit("/", 1)[1])
    svc.index.delete(doc_id)
    assert jd.get(path, follow_redirects=False).status_code == 404
    assert len(calls) == len(svc.index.doc_ids())

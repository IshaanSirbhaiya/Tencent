"""Fixes from CodeBuddy's review of the finals backend (CB7, docs/tencent/reviews/codebuddy_finals_review.md).

F7: ``GET /ib/doc/{doc_id}`` had no cap on the id. An id that is empty or longer than
``docview.MAX_DOC_ID`` is now refused before any index or source lookup, with the SAME 404 as a
restricted or unknown id, and the attempt is still ledgered first (write-ahead).
"""
from __future__ import annotations

from databridge_ib import docview

from test_graph_doc import _c, _events, _world


def test_overlong_doc_id_is_the_same_404_and_still_ledgered(tmp_path):
    app, svc = _world(tmp_path)
    c = _c(app, svc, "jdoe")
    long_id = "slack:" + "a" * docview.MAX_DOC_ID
    rs = [c.get(f"/ib/doc/{d}") for d in ("slack:t2", "slack:nope", long_id)]
    assert {r.status_code for r in rs} == {404}
    assert len({r.content for r in rs}) == 1                     # one body for all three
    assert len({tuple(r.headers.items()) for r in rs}) == 1
    seen = [e.doc_id for e in _events(svc, "doc.viewed")]
    assert seen[:2] == ["slack:t2", "slack:nope"]
    assert seen[2] == long_id[:300]                              # recorded before the decision


def test_overlong_doc_id_never_reaches_the_index(tmp_path, monkeypatch):
    app, svc = _world(tmp_path)
    calls = []
    real = svc.index.readable

    def spy(*a, **kw):
        calls.append(a)
        return real(*a, **kw)

    monkeypatch.setattr(svc.index, "readable", spy)
    r = _c(app, svc, "jdoe").get("/ib/doc/" + "x" * (docview.MAX_DOC_ID + 1))
    assert r.status_code == 404
    assert calls == []


def test_a_permitted_doc_still_opens(tmp_path):
    app, svc = _world(tmp_path)
    r = _c(app, svc, "jdoe").get("/ib/doc/slack:t1")
    assert r.status_code == 200

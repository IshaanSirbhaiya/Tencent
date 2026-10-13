"""Confluence ``draft`` pages in the mock, as in Confluence Cloud's v2 API: a draft is left out of the
default page listing (``current`` + ``archived``), 404s on the default page GET, is returned only when
``status=draft`` is asked for, and has no read permission to check. The IB connector therefore never
indexes one, and Gate 2 calls it VANISHED.

The draft is the world's own (the fact whose page has ``status: draft``); nothing is hard-coded."""
from __future__ import annotations

import pytest

from test_mock_real_world import A, S, WORLD, _ok, env  # noqa: F401  (env is the shared fixture)

pytestmark = pytest.mark.skipif(not WORLD.exists(), reason=f"no generated world at {WORLD}")


@pytest.fixture(scope="module")
def draft(env):
    _c, w = env
    drafts = [pid for pid, p in w.pages.items() if p.get("status") == "draft"]
    assert drafts, "the world has no draft page"                       # non-vacuous
    return drafts[0]


def _crawl(c, params):
    url, got = "/confluence/wiki/api/v2/pages", []
    first = True
    while url:
        b = _ok(c.get(url, headers=S, params=params if first else None))
        first = False
        got += b["results"]
        nxt = b["_links"].get("next")
        url = "/confluence" + nxt if nxt else None
    return got


def test_draft_is_not_in_the_default_listing(env, draft):
    c, _w = env
    ids = {p["id"] for p in _crawl(c, {"body-format": "storage"})}
    assert draft not in ids and ids


def test_draft_is_listed_and_fetched_only_when_asked_for(env, draft):
    c, w = env
    listed = _crawl(c, {"status": "draft", "body-format": "storage"})
    assert [p["id"] for p in listed] == [draft]
    assert listed[0]["status"] == "draft"
    assert c.get(f"/confluence/wiki/api/v2/pages/{draft}", headers=S).status_code == 404
    got = _ok(c.get(f"/confluence/wiki/api/v2/pages/{draft}", headers=S,
                    params={"status": "draft", "body-format": "storage"}))
    assert got["status"] == "draft" and got["title"] == w.pages[draft]["title"]


def test_draft_has_no_permission_to_check(env, draft):
    c, w = env
    anyone = next(iter(w.users))
    r = c.post(f"/confluence/wiki/rest/api/content/{draft}/permission/check", headers=A,
               json={"subject": {"type": "user", "identifier": w.native("confluence", anyone)},
                     "operation": "read"})
    assert r.status_code == 404

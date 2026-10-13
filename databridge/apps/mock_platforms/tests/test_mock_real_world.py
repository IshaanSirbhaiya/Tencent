"""Smoke-load the generated world (A1's ``fixtures/enterprise/world.json``, or ``MOCK_SMOKE_WORLD``).

Skipped when no world exists. Crawls every list endpoint to exhaustion, hits every detail endpoint
(no 5xx anywhere), and cross-checks the mock's computed permissions against the fact manifest's
``permitted`` / ``denied`` personas — the expectations come from the world, never from this file.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mock_platforms.app import create_app
from mockplat_helpers import ADMIN, SECRET, SVC

_DEFAULT = Path(__file__).resolve().parents[3] / "fixtures" / "enterprise" / "world.json"
WORLD = Path(os.environ.get("MOCK_SMOKE_WORLD") or _DEFAULT)

pytestmark = pytest.mark.skipif(not WORLD.exists(), reason=f"no generated world at {WORLD}")

S = {"Authorization": f"Bearer {SVC}"}
A = {"Authorization": f"Bearer {ADMIN}"}


@pytest.fixture(scope="module")
def env():
    app = create_app(WORLD, webhook_sink=lambda *a: None, svc_token=SVC, admin_token=ADMIN,
                     webhook_secret=SECRET, webhook_url="")
    with TestClient(app) as c:
        yield c, app.state.world


def _ok(r):
    assert r.status_code == 200, (r.request.url, r.status_code, r.text[:300])
    return r.json()


def test_crawl_slack(env):
    c, w = env
    chans, cursor = [], ""
    while True:
        b = _ok(c.get("/slack/api/conversations.list", headers=S,
                      params={"types": "public_channel,private_channel", "cursor": cursor}))
        chans += b["channels"]
        cursor = b["response_metadata"]["next_cursor"]
        if not cursor:
            break
    assert {x["id"] for x in chans} == set(w.channels)
    n_threads = 0
    for ch in chans:
        cursor = ""
        while True:
            b = _ok(c.get("/slack/api/conversations.history", headers=S,
                          params={"channel": ch["id"], "cursor": cursor}))
            for m in b["messages"]:
                rep = _ok(c.get("/slack/api/conversations.replies", headers=S,
                                params={"channel": ch["id"], "ts": m["ts"]}))
                assert rep["ok"] and len(rep["messages"]) == m["reply_count"] + 1
                n_threads += 1
            cursor = b["response_metadata"]["next_cursor"]
            if not cursor:
                break
        assert _ok(c.get("/slack/api/conversations.members", headers=S,
                         params={"channel": ch["id"]}))["ok"]
    assert n_threads == len(w.threads)
    for uid in w.users:
        assert _ok(c.get("/slack/api/users.info", headers=S,
                         params={"user": w.native("slack", uid)}))["ok"]


def test_crawl_confluence(env):
    c, w = env
    url, got = "/confluence/wiki/api/v2/pages?body-format=storage", []
    while url:
        b = _ok(c.get(url, headers=S))
        got += b["results"]
        nxt = b["_links"].get("next")
        url = "/confluence" + nxt if nxt else None
    # the default listing returns what Confluence's does: current + archived (not trashed, not draft)
    from mock_platforms.confluence import DEFAULT_STATUSES
    live = {p for p, x in w.pages.items() if x.get("status", "current") in DEFAULT_STATUSES}
    assert {p["id"] for p in got} == live
    for pid in live:
        _ok(c.get(f"/confluence/wiki/api/v2/pages/{pid}/ancestors", headers=S))
        _ok(c.get(f"/confluence/wiki/rest/api/content/{pid}/restriction/byOperation/read",
                  headers=S))
    for s in w.spaces.values():
        _ok(c.get(f"/confluence/wiki/api/v2/spaces/{s['id']}/permissions", headers=S))


def test_crawl_jira(env):
    c, w = env
    keys, token = [], None
    while True:
        params = {"jql": "ORDER BY updated", "fields": "*all"}
        if token:
            params["nextPageToken"] = token
        b = _ok(c.get("/jira/rest/api/3/search/jql", headers=S, params=params))
        keys += [i["key"] for i in b["issues"]]
        if b["isLast"]:
            break
        token = b["nextPageToken"]
    assert sorted(keys) == sorted(w.issues)
    for key in w.projects:
        for role in _ok(c.get(f"/jira/rest/api/3/project/{key}/role", headers=S)):
            _ok(c.get(f"/jira/rest/api/3/project/{key}/role/{role}", headers=S))
        _ok(c.get(f"/jira/rest/api/3/project/{key}/permissionscheme", headers=S))
    for lid in w.levels:
        _ok(c.get("/jira/rest/api/3/issuesecurityschemes/level/member", headers=S,
                  params={"levelId": lid}))


def test_crawl_drive(env):
    c, w = env
    ids, token = [], None
    while True:
        params = {"includeItemsFromAllDrives": "true", "supportsAllDrives": "true"}
        if token:
            params["pageToken"] = token
        b = _ok(c.get("/drive/v3/files", headers=S, params=params))
        ids += [f["id"] for f in b["files"]]
        token = b.get("nextPageToken")
        if not token:
            break
    assert sorted(ids) == sorted([*w.files, *w.folders])
    for fid in ids:
        _ok(c.get(f"/drive/v3/files/{fid}/permissions", headers=S))
    for fid in w.files:
        assert c.get(f"/drive/v3/files/{fid}", params={"alt": "media"}, headers=S).status_code == 200


def _drive_grants(c, w, fid, uid):
    """Does any permission entry name this user (directly, via an expanded group, or via the
    verified company domain)? ``anyone`` never grants."""
    u = w.users[uid]
    if not u.get("active", True):
        return False
    perms = _ok(c.get(f"/drive/v3/files/{fid}/permissions", headers=S))["permissions"]
    for p in perms:
        if p["type"] == "user" and p["emailAddress"].lower() == w.native("drive", uid).lower():
            return True
        if p["type"] == "group" and (w.group_by_email(p["emailAddress"]) in w.user_groups(uid)):
            return True
        if (p["type"] == "domain" and u.get("kind", "member") == "member"
                and u["email"].rsplit("@", 1)[-1] == p["domain"]):
            return True
    return False


def _has_anyone(c, fid):
    perms = _ok(c.get(f"/drive/v3/files/{fid}/permissions", headers=S))["permissions"]
    return any(p["type"] == "anyone" for p in perms)


def test_facts_agree_with_mock_permissions(env):
    c, w = env
    checked = 0
    for f in w.raw.get("facts", []):
        plat, _, native = f["doc"].partition(":")
        for uid in [*f.get("permitted", []), *f.get("denied", [])]:
            if uid not in w.users:
                continue
            expected = uid in f["permitted"]
            if plat == "confluence":
                status = w.pages[native].get("status", "current")
                r = c.post(f"/confluence/wiki/rest/api/content/{native}/permission/check",
                           headers=A, json={"subject": {"type": "user",
                                                        "identifier": w.native(plat, uid)},
                                            "operation": "read"})
                if status == "trashed":
                    assert r.status_code == 404 and not expected
                    continue
                if status != "current":          # archived: readable natively, IB ⇒ VANISHED
                    assert not expected
                    continue
                got = _ok(r)["hasPermission"]
            elif plat == "jira":
                iid = w.issues[native]["id"]
                b = _ok(c.post("/jira/rest/api/3/permissions/check", headers=A, json={
                    "accountId": w.native(plat, uid),
                    "projectPermissions": [{"issues": [iid], "permissions": ["BROWSE_PROJECTS"]}]}))
                got = bool(b["projectPermissions"][0]["issues"])
            elif plat == "slack":
                ch = _ok(c.get("/slack/api/conversations.info", headers=S,
                               params={"channel": native.split(":")[0]}))["channel"]
                members = _ok(c.get("/slack/api/conversations.members", headers=S,
                                    params={"channel": ch["id"]}))["members"]
                info = _ok(c.get("/slack/api/users.info", headers=S,
                                 params={"user": w.native(plat, uid)}))["user"]
                full = not (info["is_restricted"] or info["is_ultra_restricted"]
                            or info["is_stranger"])
                got = (not info["deleted"]) and (
                    info["id"] in members or (not ch["is_private"] and full))
            elif plat == "drive":
                if _has_anyone(c, native):       # IB policy: an anyone-link file is denied
                    assert not expected
                    continue
                got = _drive_grants(c, w, native, uid)
            else:
                continue
            assert got is expected, (f["id"], f["doc"], uid, "expected", expected)
            checked += 1
    assert checked > 0

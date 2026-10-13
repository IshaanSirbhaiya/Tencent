"""Every admin mutation changes what the platform APIs return AND emits a correctly signed webhook."""
from __future__ import annotations

import json
import time
import uuid

from mockplat_helpers import SECRET, verify_signature

from mock_platforms.webhooks import WebhookEmitter, sign

CF_CHECK = "/confluence/wiki/rest/api/content/{}/permission/check"
TYPES = {"content.changed", "content.deleted", "acl.changed", "membership.changed", "user.changed"}


def _assert_signed(sink, n=1):
    """The last ``n`` deliveries are signed, fresh, uniquely identified and contract-shaped."""
    assert len(sink.calls) >= n
    out = []
    for platform, headers, body in sink.calls[-n:]:
        assert verify_signature(headers, body)
        assert not verify_signature(headers, body, secret="wrong-secret")
        assert abs(int(headers["X-IB-Timestamp"]) - time.time()) < 60
        uuid.UUID(headers["X-IB-Delivery"])
        payload = json.loads(body)
        assert set(payload) == {"platform", "type", "object_ids", "container", "user", "at"}
        assert payload["platform"] == platform and payload["type"] in TYPES
        out.append(payload)
    ids = [h["X-IB-Delivery"] for _, h, _ in sink.calls]
    assert len(ids) == len(set(ids))
    return out


def _cf_can(client, admin, page, account):
    return client.post(CF_CHECK.format(page), headers=admin, json={
        "subject": {"type": "user", "identifier": account}, "operation": "read"}).json()[
        "hasPermission"]


def test_sign_matches_contract_formula():
    import hashlib
    import hmac
    body = b'{"a":1}'
    assert sign("k", "123", "d-1", body) == "sha256=" + hmac.new(b"k", b'123.d-1.{"a":1}',
                                                                 hashlib.sha256).hexdigest()


def test_signature_binds_the_delivery_id():
    """Codex sec3 #8/#11: a captured delivery replayed under a NEW delivery id must not verify."""
    emitter = WebhookEmitter(SECRET)
    rec = emitter.emit("jira", "content.changed", ["PAY-1"], at="2026-10-13T00:00:00Z")
    headers, body = rec["headers"], rec["body"]
    assert verify_signature(headers, body)
    swapped = {**headers, "X-IB-Delivery": str(uuid.uuid4())}
    assert not verify_signature(swapped, body)
    assert sign(SECRET, headers["X-IB-Timestamp"], swapped["X-IB-Delivery"], body) != \
        headers["X-IB-Signature"]


def test_slack_member_remove_and_add(client, svc, admin, sink):
    url = "/slack/api/conversations.members"
    assert "U0JDOE" in client.get(url, params={"channel": "C0DBMIG"}, headers=svc).json()["members"]
    r = client.post("/admin/slack/channels/C0DBMIG/members/remove", json={"user": "jdoe"},
                    headers=admin)
    assert r.status_code == 200 and r.json()["changed"] is True
    assert "U0JDOE" not in client.get(url, params={"channel": "C0DBMIG"},
                                      headers=svc).json()["members"]
    [p] = _assert_signed(sink)
    assert p == {**p, "platform": "slack", "type": "membership.changed",
                 "object_ids": ["C0DBMIG"], "container": "C0DBMIG", "user": "jdoe"}
    # removing again is a no-op without a webhook
    n = len(sink.calls)
    assert client.post("/admin/slack/channels/C0DBMIG/members/remove", json={"user": "U0JDOE"},
                       headers=admin).json()["changed"] is False
    assert len(sink.calls) == n
    client.post("/admin/slack/channels/C0DBMIG/members/add", json={"user": "U0JDOE"},
                headers=admin)
    assert "U0JDOE" in client.get(url, params={"channel": "C0DBMIG"}, headers=svc).json()["members"]
    [p] = _assert_signed(sink)
    assert p["type"] == "membership.changed" and p["user"] == "jdoe"


def test_slack_new_thread_and_reply(client, svc, admin, sink):
    r = client.post("/admin/slack/threads", json={"channel": "C0DBMIG", "user": "priya.sre",
                                                  "text": "New blocker"}, headers=admin).json()
    ts = r["ts"]
    hist = client.get("/slack/api/conversations.history", params={"channel": "C0DBMIG"},
                      headers=svc).json()["messages"]
    assert hist[0]["ts"] == ts and hist[0]["text"] == "New blocker"
    assert hist[0]["user"] == "U0PRIYA"
    assert all(float(ts) > float(m["ts"]) for m in hist[1:])
    [p] = _assert_signed(sink)
    assert p["type"] == "content.changed" and p["object_ids"] == [f"C0DBMIG:{ts}"]
    assert p["container"] is None and p["user"] == "priya.sre"
    client.post("/admin/slack/threads", json={"channel": "C0DBMIG", "user": "jdoe",
                                              "text": "reply", "thread_ts": ts}, headers=admin)
    reps = client.get("/slack/api/conversations.replies", params={"channel": "C0DBMIG", "ts": ts},
                      headers=svc).json()["messages"]
    assert [m["text"] for m in reps] == ["New blocker", "reply"]
    [p] = _assert_signed(sink)
    assert p["object_ids"] == [f"C0DBMIG:{ts}"]


def test_confluence_edit_bumps_version_and_keeps_history(client, svc, admin, sink):
    before = client.get("/confluence/wiki/api/v2/pages/cf-1001", params={"body-format": "storage"},
                        headers=svc).json()
    r = client.post("/admin/confluence/pages/cf-1001/edit", json={"body": "v3 body"},
                    headers=admin)
    assert r.json()["version"] == before["version"]["number"] + 1
    after = client.get("/confluence/wiki/api/v2/pages/cf-1001", params={"body-format": "storage"},
                       headers=svc).json()
    assert after["body"]["storage"]["value"] == "v3 body"
    assert after["version"]["number"] == before["version"]["number"] + 1
    old = client.get("/confluence/wiki/api/v2/pages/cf-1001",
                     params={"body-format": "storage", "version": before["version"]["number"]},
                     headers=svc).json()
    assert old["body"]["storage"]["value"] == before["body"]["storage"]["value"]
    vers = client.get("/confluence/wiki/api/v2/pages/cf-1001/versions", headers=svc).json()
    assert [v["number"] for v in vers["results"]][:2] == [after["version"]["number"],
                                                          before["version"]["number"]]
    [p] = _assert_signed(sink)
    assert p["type"] == "content.changed" and p["object_ids"] == ["cf-1001"]


def test_confluence_create_page(client, svc, admin, sink):
    r = client.post("/admin/confluence/pages", json={"space": "payment-gateway",
                                                     "parent": "cf-1002", "title": "New child",
                                                     "body": "hello"}, headers=admin).json()
    pid = r["id"]
    page = client.get(f"/confluence/wiki/api/v2/pages/{pid}", params={"body-format": "storage"},
                      headers=svc).json()
    assert page["title"] == "New child" and page["version"]["number"] == 1
    assert page["parentId"] == "cf-1002" and page["spaceId"] == "sp-1"
    anc = client.get(f"/confluence/wiki/api/v2/pages/{pid}/ancestors", headers=svc).json()
    assert [a["id"] for a in anc["results"]] == ["cf-1001", "cf-1002"]
    # inherits the parent's restriction: jdoe is not in cf-1002's restriction
    assert _cf_can(client, admin, pid, "acc-jdoe") is False
    assert _cf_can(client, admin, pid, "acc-priya") is True
    [p] = _assert_signed(sink)
    assert p["type"] == "content.changed" and p["object_ids"] == [pid]


def test_confluence_restrict_parent_cascades(client, admin, sink, mini):
    assert _cf_can(client, admin, "cf-1001", "acc-jdoe") is True
    r = client.post("/admin/confluence/pages/cf-1001/restrict",
                    json={"users": ["acc-priya"], "groups": ["security"]}, headers=admin)
    assert r.status_code == 200
    assert _cf_can(client, admin, "cf-1001", "acc-jdoe") is False
    assert _cf_can(client, admin, "cf-1001", "acc-priya") is True
    assert _cf_can(client, admin, "cf-1001", "acc-sara") is True        # via group name
    [p] = _assert_signed(sink)
    live_desc = {x["id"] for x in mini["confluence"]["pages"]
                 if x.get("parent") in ("cf-1001", "cf-1002") and x["status"] != "trashed"}
    assert p["type"] == "acl.changed" and p["container"] == "payment-gateway"
    assert p["object_ids"][0] == "cf-1001" and set(p["object_ids"]) == {"cf-1001", *live_desc}
    restr = client.get("/confluence/wiki/rest/api/content/cf-1001/restriction/byOperation/read",
                       headers=admin).json()["restrictions"]
    assert [u["accountId"] for u in restr["user"]["results"]] == ["acc-priya"]
    assert [g["id"] for g in restr["group"]["results"]] == ["grp-security"]
    client.post("/admin/confluence/pages/cf-1001/unrestrict", headers=admin)
    assert _cf_can(client, admin, "cf-1001", "acc-jdoe") is True
    [p] = _assert_signed(sink)
    assert p["type"] == "acl.changed" and "cf-1003" in p["object_ids"]


def test_confluence_trash_extra(client, svc, admin, sink):
    client.post("/admin/confluence/pages/cf-2001/trash", headers=admin)
    assert client.get("/confluence/wiki/api/v2/pages/cf-2001", headers=svc).status_code == 404
    [p] = _assert_signed(sink)
    assert p["type"] == "content.deleted" and p["object_ids"] == ["cf-2001"]


def _jira_allowed(client, admin, account, ids):
    return set(client.post("/jira/rest/api/3/permissions/check", headers=admin, json={
        "accountId": account,
        "projectPermissions": [{"issues": ids, "permissions": ["BROWSE_PROJECTS"]}]}).json()[
        "projectPermissions"][0]["issues"])


def test_jira_edit_content_only(client, svc, admin, sink):
    client.post("/admin/jira/issues/PAY-1/edit", json={"summary": "Renamed", "comment": {
        "author": "jdoe", "body": "done"}}, headers=admin)
    f = client.get("/jira/rest/api/3/issue/PAY-1", headers=svc).json()["fields"]
    assert f["summary"] == "Renamed"
    assert f["comment"]["comments"][-1]["author"]["accountId"] == "acc-jdoe"
    [p] = _assert_signed(sink)
    assert p["type"] == "content.changed" and p["object_ids"] == ["PAY-1"]
    # the edit moved "updated", so incremental JQL picks it up
    hits = client.get("/jira/rest/api/3/search/jql",
                      params={"jql": f'updated >= "{f["updated"]}"'}, headers=svc).json()["issues"]
    assert "PAY-1" in [i["key"] for i in hits]


def test_jira_edit_security_level_emits_acl_changed(client, svc, admin, sink):
    assert 20001 in _jira_allowed(client, admin, "acc-priya", [20001])
    client.post("/admin/jira/issues/PAY-1/edit", json={"security_level": "10100"}, headers=admin)
    assert _jira_allowed(client, admin, "acc-priya", [20001]) == set()
    assert _jira_allowed(client, admin, "acc-jdoe", [20001]) == {20001}     # reporter in level
    content, acl = _assert_signed(sink, 2)
    assert content["type"] == "content.changed"
    assert acl == {**acl, "type": "acl.changed", "object_ids": ["PAY-1"], "container": "PAY"}
    assert client.post("/admin/jira/issues/PAY-1/edit", json={"bogus": 1},
                       headers=admin).status_code == 400


def test_drive_edit(client, svc, admin, sink):
    client.post("/admin/drive/files/df-1/edit", json={"content": "new text"}, headers=admin)
    assert client.get("/drive/v3/files/df-1", params={"alt": "media"}, headers=svc).text == "new text"
    assert client.get("/drive/v3/files/df-1", headers=svc).json()["version"] == "4"
    [p] = _assert_signed(sink)
    assert p["type"] == "content.changed" and p["object_ids"] == ["df-1"]


def test_drive_folder_permission_remove_cascades(client, svc, admin, sink):
    def principals(fid):
        return {x.get("emailAddress") for x in client.get(f"/drive/v3/files/{fid}/permissions",
                                                          headers=svc).json()["permissions"]}
    assert "grp-security@companya.example" in principals("df-1")
    r = client.post("/admin/drive/files/fd-1/permissions/remove", json={"permission_id": "p1"},
                    headers=admin)
    assert r.status_code == 200
    assert "grp-security@companya.example" not in principals("df-1")
    assert "grp-security@companya.example" in principals("df-5")         # its direct grant stays
    [p] = _assert_signed(sink)
    assert p["type"] == "acl.changed" and p["container"] == "0AEng"
    assert p["object_ids"][0] == "fd-1"
    assert {"df-1", "df-3", "df-5", "fd-2", "fd-3", "df-2"} <= set(p["object_ids"])
    # changes feed sees the affected items
    changes = client.get("/drive/v3/changes", params={"pageToken": "1"}, headers=svc).json()
    assert {"fd-1", "df-1"} <= {c["fileId"] for c in changes["changes"]}
    # removing a permission that is only inherited here is refused
    assert client.post("/admin/drive/files/df-1/permissions/remove",
                       json={"permission_id": "p2"}, headers=admin).status_code == 404


def test_drive_file_permission_remove(client, svc, admin, sink):
    client.post("/admin/drive/files/df-3/permissions/remove", json={"permission_id": "p10"},
                headers=admin)
    perms = client.get("/drive/v3/files/df-3/permissions", headers=svc).json()["permissions"]
    assert not [x for x in perms if x["type"] == "anyone"]
    [p] = _assert_signed(sink)
    assert p["object_ids"] == ["df-3"]


def test_deactivate_user(client, svc, admin, sink):
    assert _cf_can(client, admin, "cf-1001", "acc-jdoe") is True
    client.post("/admin/directory/users/jdoe/deactivate", headers=admin)
    info = client.get("/slack/api/users.info", params={"user": "U0JDOE"}, headers=svc).json()
    assert info["user"]["deleted"] is True
    assert _cf_can(client, admin, "cf-1001", "acc-jdoe") is False
    assert _jira_allowed(client, admin, "acc-jdoe", [20001]) == set()
    hooks = _assert_signed(sink, 4)
    assert {h["platform"]: h["object_ids"] for h in hooks} == {
        "slack": ["U0JDOE"], "confluence": ["acc-jdoe"], "jira": ["acc-jdoe"],
        "drive": ["jdoe@companya.example"]}
    assert all(h["type"] == "user.changed" and h["user"] == "jdoe" for h in hooks)


def test_reset_reloads_world(client, svc, admin, sink):
    client.post("/admin/slack/channels/C0DBMIG/members/remove", json={"user": "jdoe"},
                headers=admin)
    client.post("/admin/ratelimit", json={"platform": "drive", "on": True}, headers=admin)
    assert client.post("/admin/reset", headers=admin).status_code == 200
    members = client.get("/slack/api/conversations.members", params={"channel": "C0DBMIG"},
                         headers=svc).json()["members"]
    assert "U0JDOE" in members
    assert client.get("/drive/v3/files", headers=svc).status_code == 200


def test_admin_webhook_log(client, admin):
    client.post("/admin/drive/files/df-1/edit", json={"content": "x"}, headers=admin)
    log = client.get("/admin/webhooks", headers=admin).json()["deliveries"]
    assert log[-1]["type"] == "content.changed" and log[-1]["object_ids"] == ["df-1"]


def test_url_delivery_is_fire_and_forget():
    em = WebhookEmitter(SECRET, url="http://127.0.0.1:9", timeout_s=0.2)
    rec = em.emit("slack", "content.changed", ["C1:1.0"], at="2026-10-13T09:00:00+08:00")
    assert verify_signature(rec["headers"], rec["body"])
    em.close()

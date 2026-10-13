"""Slack Web API subset: shapes, native ids, cursor pagination, guest/Connect/deleted flags."""
from __future__ import annotations

S = "/slack/api"


def _native(mini, uid):
    return next(u for u in mini["directory"]["users"] if u["id"] == uid)["platform_ids"]["slack"]


def _collect(client, svc, url, key, **params):
    seen, cursor, pages = [], "", 0
    while True:
        r = client.get(url, params={**params, "cursor": cursor}, headers=svc)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        seen.extend(body[key])
        pages += 1
        cursor = body["response_metadata"]["next_cursor"]
        if not cursor:
            return seen, pages


def test_conversations_list_paginates_all_channels(client, svc, mini):
    chans, pages = _collect(client, svc, f"{S}/conversations.list", "channels",
                            types="public_channel,private_channel", limit=1)
    ids = [c["id"] for c in chans]
    assert sorted(ids) == sorted(c["id"] for c in mini["slack"]["channels"])
    assert len(ids) == len(set(ids))
    assert pages == len(ids)


def test_conversations_list_types_filter(client, svc, mini):
    r = client.get(f"{S}/conversations.list", params={"types": "public_channel"}, headers=svc)
    ids = {c["id"] for c in r.json()["channels"]}
    private = {c["id"] for c in mini["slack"]["channels"] if c["type"] in ("private", "connect")}
    assert ids and not ids & private
    r = client.get(f"{S}/conversations.list", params={"types": "private_channel"}, headers=svc)
    assert {c["id"] for c in r.json()["channels"]} == private


def test_channel_flags(client, svc, mini):
    r = client.get(f"{S}/conversations.list", params={"types": "public_channel,private_channel"},
                   headers=svc)
    by_id = {c["id"]: c for c in r.json()["channels"]}
    for c in mini["slack"]["channels"]:
        got = by_id[c["id"]]
        assert got["is_private"] == (c["type"] in ("private", "connect"))
        assert got["is_ext_shared"] == (c["type"] == "connect")
        assert got["is_archived"] == c["archived"]
        assert got["name"] == c["name"]


def test_exclude_archived(client, svc, mini):
    r = client.get(f"{S}/conversations.list", params={"exclude_archived": "true"}, headers=svc)
    archived = {c["id"] for c in mini["slack"]["channels"] if c["archived"]}
    assert archived and not {c["id"] for c in r.json()["channels"]} & archived


def test_members_are_native_ids_and_paginate(client, svc, mini):
    for ch in mini["slack"]["channels"]:
        members, _ = _collect(client, svc, f"{S}/conversations.members", "members",
                              channel=ch["id"], limit=1)
        assert members == [_native(mini, u) for u in ch["members"]]


def test_history_parents_newest_first_with_reply_counts(client, svc, mini):
    msgs, pages = _collect(client, svc, f"{S}/conversations.history", "messages",
                           channel="C0GEN", limit=1)
    threads = [t for t in mini["slack"]["threads"] if t["channel"] == "C0GEN"]
    assert pages == len(threads)
    assert [m["ts"] for m in msgs] == sorted((t["ts"] for t in threads), key=float, reverse=True)
    for m in msgs:
        t = next(t for t in threads if t["ts"] == m["ts"])
        assert m["reply_count"] == len(t["messages"]) - 1
        assert m["user"] == _native(mini, t["messages"][0]["user"])
        if len(t["messages"]) > 1:
            assert m["latest_reply"] == t["messages"][-1]["ts"]


def test_history_oldest_filter(client, svc):
    r = client.get(f"{S}/conversations.history",
                   params={"channel": "C0GEN", "oldest": "1728370100.000100"}, headers=svc)
    assert [m["ts"] for m in r.json()["messages"]] == ["1728370200.000100"]
    r = client.get(f"{S}/conversations.history",
                   params={"channel": "C0GEN", "oldest": "1728370100.000100",
                           "inclusive": "true"}, headers=svc)
    assert len(r.json()["messages"]) == 2


def test_replies_returns_whole_thread(client, svc, mini):
    t = next(t for t in mini["slack"]["threads"] if t["channel"] == "C0DBMIG")
    r = client.get(f"{S}/conversations.replies", params={"channel": "C0DBMIG", "ts": t["ts"]},
                   headers=svc)
    msgs = r.json()["messages"]
    assert [m["ts"] for m in msgs] == [m["ts"] for m in t["messages"]]
    assert [m["text"] for m in msgs] == [m["text"] for m in t["messages"]]
    assert all(m["thread_ts"] == t["ts"] for m in msgs)
    edited = [m for m, src in zip(msgs, t["messages"]) if src["edited"]]
    assert edited and all("edited" in m for m in edited)


def test_unknown_channel_and_thread_are_slack_errors(client, svc):
    r = client.get(f"{S}/conversations.members", params={"channel": "CNOPE"}, headers=svc)
    assert r.status_code == 200 and r.json() == {"ok": False, "error": "channel_not_found"}
    r = client.get(f"{S}/conversations.replies", params={"channel": "C0GEN", "ts": "1.000000"},
                   headers=svc)
    assert r.json() == {"ok": False, "error": "thread_not_found"}


def test_invalid_cursor(client, svc):
    r = client.get(f"{S}/conversations.list", params={"cursor": "garbage!!"}, headers=svc)
    assert r.json()["ok"] is False and r.json()["error"] == "invalid_cursor"


def test_users_info_flags(client, svc, mini):
    for u in mini["directory"]["users"]:
        nid = u["platform_ids"]["slack"]
        r = client.get(f"{S}/users.info", params={"user": nid}, headers=svc)
        got = r.json()["user"]
        assert got["id"] == nid
        assert got["deleted"] == (not u["active"])
        assert got["is_restricted"] == (u["kind"] == "guest")
        assert got["is_stranger"] == (u["kind"] == "connect")
        assert got["profile"]["email"] == u["email"]


def test_users_info_positive_and_negative_examples(client, svc):
    guest = client.get(f"{S}/users.info", params={"user": "U0GINA"}, headers=svc).json()["user"]
    assert guest["is_restricted"] is True and guest["is_stranger"] is False
    ext = client.get(f"{S}/users.info", params={"user": "U0KEN"}, headers=svc).json()["user"]
    assert ext["is_stranger"] is True and ext["is_restricted"] is False
    member = client.get(f"{S}/users.info", params={"user": "U0JDOE"}, headers=svc).json()["user"]
    assert not (member["is_restricted"] or member["is_stranger"] or member["deleted"])
    gone = client.get(f"{S}/users.info", params={"user": "U0OLD"}, headers=svc).json()["user"]
    assert gone["deleted"] is True
    r = client.get(f"{S}/users.info", params={"user": "U0NOBODY"}, headers=svc)
    assert r.json() == {"ok": False, "error": "user_not_found"}


def test_users_list_paginates(client, svc, mini):
    members, _ = _collect(client, svc, f"{S}/users.list", "members", limit=2)
    assert sorted(m["id"] for m in members) == sorted(
        u["platform_ids"]["slack"] for u in mini["directory"]["users"])

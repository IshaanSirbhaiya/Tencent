"""Slack connector (A3): plan §4.5 supported subset, Gate 2 fail-closed, crawl cursor, webhooks."""
from __future__ import annotations

import json
import time

import pytest

from databridge_ib import acl as A
from databridge_ib.connectors._http import DirectoryIndex, extract_links
from databridge_ib.connectors.base import SourceConnector, WebhookRejected
from databridge_ib.connectors.slack import SlackConnector, version_count, version_ts
from databridge_ib.models import AclNobody, UserKind

from fixtures_connectors import SECRET, Directory, FakeSlack, client_for, people, signed_headers

PUB = "slack:C0PUB:1728370000.000100"
PRIV = "slack:C0PRIV:1728370500.000200"
VEND = "slack:C0VEND:1728370600.000300"
OLD = "slack:C0OLD:1728300000.000100"


@pytest.fixture(params=[True, False], ids=["with-info", "list-only"])
def env(request):
    d = Directory(people())
    fake = FakeSlack(d, has_info=request.param)
    sleeps: list[float] = []
    conn = SlackConnector(client_for(fake, "svc-test"), d, SECRET, sleep=sleeps.append)
    return d, fake, conn, sleeps


def _ib_members(d, fake, cid):
    return {d.by_platform_id("slack", n).id for n in fake.channels[cid]["members"]}


def test_protocol_and_directory_seam(env):
    d, _, conn, _ = env
    assert isinstance(conn, SourceConnector)
    assert isinstance(d, DirectoryIndex)
    # a zero-arg provider is accepted too (so a reloaded directory is picked up)
    conn2 = SlackConnector(conn.client, lambda: d, SECRET)
    assert conn2.directory is d


# ---------------------------------------------------------------- documents
def test_fetch_thread_doc(env):
    d, fake, conn, _ = env
    doc = conn.fetch(PUB)
    msgs = fake.threads[("C0PUB", "1728370000.000100")]
    assert doc.doc_id == PUB and doc.kind == "thread" and doc.platform == "slack"
    assert doc.container == "C0PUB" and doc.container_name == fake.channels["C0PUB"]["name"]
    assert version_ts(doc.version) == max((m["ts"] for m in msgs), key=float)
    assert version_count(doc.version) == len(msgs)
    assert doc.deep_link == "https://companya.slack.com/archives/C0PUB/p1728370000000100"
    expected_lines = [f"{d.by_platform_id('slack', m['user']).id}: {m['text']}" for m in msgs]
    assert doc.text.splitlines() == expected_lines
    assert doc.title.startswith("#payments-eng: Migration status")
    assert doc.author == "jdoe"
    assert "jira:PAY-201" in doc.links and "confluence:cf-1001" in doc.links
    assert doc.created_at.timestamp() == pytest.approx(1728370000.0001)


def test_fetch_missing_thread_and_channel(env):
    _, _, conn, _ = env
    assert conn.fetch("slack:C0PUB:1111111111.000001") is None
    assert conn.fetch("slack:C0NOPE:1728370000.000100") is None
    assert conn.fetch("slack:bad") is None


# ---------------------------------------------------------------- ACL compile
def test_public_channel_acl(env):
    d, fake, conn, _ = env
    e = conn.acl(PUB)
    members = _ib_members(d, fake, "C0PUB")
    for u in d.all():
        expect = u.kind is UserKind.MEMBER or u.id in members
        assert A.allows(e, u) is expect, u.id
    assert A.allows(e, d.get("leo.ops"))              # full member, not in channel
    assert A.allows(e, d.get("gina.guest"))           # guest who IS in the channel
    assert not A.allows(e, d.get("ken.ext"))          # Connect user not in the channel


def test_private_channel_acl_is_members_only(env):
    d, fake, conn, _ = env
    e = conn.acl(PRIV)
    members = _ib_members(d, fake, "C0PRIV")
    assert members                                    # non-vacuous
    for u in d.all():
        assert A.allows(e, u) is (u.id in members), u.id


def test_connect_channel_acl_is_members_only(env):
    d, fake, conn, _ = env
    e = conn.acl(VEND)
    members = _ib_members(d, fake, "C0VEND")
    for u in d.all():
        assert A.allows(e, u) is (u.id in members), u.id
    assert not A.allows(e, d.get("leo.ops"))          # Connect channel is NOT workspace-wide


def test_archived_channel_still_readable_by_members(env):
    d, fake, conn, _ = env
    assert fake.channels["C0OLD"]["is_archived"]
    e = conn.acl(OLD)
    assert A.allows(e, d.get("priya.sre"))
    assert not A.allows(e, d.get("jdoe"))
    assert conn.fetch(OLD) is not None


def test_dm_and_unknown_channels_are_nobody(env):
    _, _, conn, _ = env
    assert isinstance(conn.acl("slack:D0DM123:1728370000.000100"), AclNobody)
    assert isinstance(conn.acl("slack:C0NOPE:1728370000.000100"), AclNobody)
    assert isinstance(conn.acl("not-a-doc"), AclNobody)


def test_unknown_native_members_are_dropped_not_granted(env):
    d, fake, conn, _ = env
    fake.channels["C0PRIV"]["members"].append("U0STRANGER")
    e = conn.acl(PRIV)
    assert A.allows(e, d.get("priya.sre"))
    assert not A.allows(e, d.get("leo.ops"))


# ---------------------------------------------------------------- Gate 2
def test_gate2_member_of_private_channel_verified(env):
    d, fake, conn, _ = env
    r = conn.check_access(d.get("priya.sre"), PRIV)
    assert r.verdict == "verified" and r.allowed
    assert version_ts(r.current_version) == fake.threads[("C0PRIV", "1728370500.000200")][-1]["ts"]
    assert r.current_version == conn.fetch(PRIV).version


def test_gate2_non_member_private_denied(env):
    d, _, conn, _ = env
    r = conn.check_access(d.get("leo.ops"), PRIV)
    assert r.verdict == "forbidden" and r.reason_code == "not_member" and not r.allowed


def test_gate2_public_full_member_allowed_without_membership(env):
    d, _, conn, _ = env
    assert conn.check_access(d.get("leo.ops"), PUB).verdict == "verified"


def test_gate2_guest_in_public_channel_not_member_denied(env):
    d, fake, conn, _ = env
    fake.channels["C0PUB"]["members"].remove(d.get("gina.guest").platform_ids["slack"])
    r = conn.check_access(d.get("gina.guest"), PUB)
    assert r.verdict == "forbidden" and r.reason_code == "guest_not_member"


def test_gate2_guest_flag_comes_from_source(env):
    """Even if the directory still says MEMBER, Slack's is_restricted flag wins."""
    d, fake, conn, _ = env
    fake.users[d.get("leo.ops").platform_ids["slack"]]["is_restricted"] = True
    r = conn.check_access(d.get("leo.ops"), PUB)
    assert r.verdict == "forbidden" and r.reason_code == "guest_not_member"


def test_gate2_connect_user_member_of_connect_channel(env):
    d, _, conn, _ = env
    assert conn.check_access(d.get("ken.ext"), VEND).verdict == "verified"
    assert conn.check_access(d.get("ken.ext"), PUB).reason_code == "guest_not_member"


def test_gate2_deactivated_denied(env):
    d, fake, conn, _ = env
    fake.users[d.get("priya.sre").platform_ids["slack"]]["deleted"] = True
    r = conn.check_access(d.get("priya.sre"), PRIV)
    assert r.verdict == "forbidden" and r.reason_code == "deactivated"


def test_gate2_revocation_is_immediate(env):
    d, fake, conn, _ = env
    assert conn.check_access(d.get("jdoe"), PRIV).allowed
    fake.channels["C0PRIV"]["members"].remove(d.get("jdoe").platform_ids["slack"])
    assert conn.check_access(d.get("jdoe"), PRIV).verdict == "forbidden"


def test_gate2_vanished_thread(env):
    d, fake, conn, _ = env
    del fake.threads[("C0PRIV", "1728370500.000200")]
    assert conn.check_access(d.get("priya.sre"), PRIV).verdict == "vanished"


@pytest.mark.parametrize("fault,endpoint,reason", [
    ("rate_limited", "users.info", "source_rate_limited"),
    ("rate_limited", "conversations.members", "source_rate_limited"),
    ("timeout", "conversations.replies", "source_timeout"),
    ("error", "conversations.members", "source_error"),
])
def test_gate2_any_source_failure_denies_without_retry(env, fault, endpoint, reason):
    d, fake, conn, sleeps = env
    getattr(fake, fault).add(endpoint)
    r = conn.check_access(d.get("priya.sre"), PRIV)
    assert r.verdict == "forbidden" and r.reason_code == reason and not r.allowed
    assert fake.calls.count(endpoint) == 1 and sleeps == []      # never waits out a 429


def test_gate2_drift_reports_current_version(env):
    d, fake, conn, _ = env
    old = conn.fetch(PRIV).version
    new_ts = fake.reply("C0PRIV", "1728370500.000200", "jdoe", "update")
    r = conn.check_access(d.get("priya.sre"), PRIV, known_version=old)
    assert r.verdict == "drift_material" and r.allowed and version_ts(r.current_version) == new_ts


def test_gate2_no_platform_identity_or_inactive(env):
    d, _, conn, _ = env
    u = d.get("priya.sre")
    assert conn.check_access(u.model_copy(update={"platform_ids": {}}), PRIV).verdict == "forbidden"
    assert conn.check_access(u.model_copy(update={"active": False}), PRIV).verdict == "forbidden"


# ---------------------------------------------------------------- crawl
def test_full_crawl_then_incremental(env):
    d, fake, conn, _ = env
    b1 = conn.list_changes(None)
    assert {x.doc_id for x in b1.docs} == {f"slack:{c}:{ts}" for (c, ts) in fake.threads}
    assert b1.deleted == () and b1.acl_changed == ()
    # nothing changed → nothing re-emitted
    b2 = conn.list_changes(b1.cursor)
    assert b2.docs == () and b2.deleted == () and b2.acl_changed == ()
    # a reply, a new thread, a deletion, a membership change
    fake.reply("C0PUB", "1728370100.000200", "jdoe", "follow-up")
    fake.add_thread("C0PUB", "1728379999.000900", [("jdoe", "brand new")])
    del fake.threads[("C0VEND", "1728370600.000300")]
    fake.channels["C0PRIV"]["members"].remove(d.get("jdoe").platform_ids["slack"])
    b3 = conn.list_changes(b2.cursor)
    assert {x.doc_id for x in b3.docs} == {"slack:C0PUB:1728370100.000200", "slack:C0PUB:1728379999.000900"}
    assert b3.deleted == (VEND,)
    assert set(b3.acl_changed) == {f"slack:C0PRIV:{ts}" for (c, ts) in fake.threads if c == "C0PRIV"}
    assert json.loads(b3.cursor)["channels"]["C0PRIV"]["members"] == sorted(fake.channels["C0PRIV"]["members"])


def test_crawl_channel_deleted_marks_all_threads(env):
    _, fake, conn, _ = env
    b1 = conn.list_changes(None)
    del fake.channels["C0OLD"]
    b2 = conn.list_changes(b1.cursor)
    assert b2.deleted == (OLD,)


def test_crawl_retries_429_honouring_retry_after(env):
    _, fake, conn, sleeps = env
    fake.rate_limit_once.add("conversations.list")
    b = conn.list_changes(None)
    assert b.docs and sleeps == [2.0]


def test_crawl_gives_up_with_connector_error(env):
    from databridge_ib.connectors.base import ConnectorError
    _, fake, conn, sleeps = env
    fake.rate_limited.add("conversations.history")
    with pytest.raises(ConnectorError):
        conn.list_changes(None)
    assert len(sleeps) >= 1


def test_bad_cursor_rejected(env):
    from databridge_ib.connectors.base import ConnectorError
    _, _, conn, _ = env
    with pytest.raises(ConnectorError):
        conn.list_changes("{nope")


# ---------------------------------------------------------------- webhooks
def _body(**kw):
    base = {"platform": "slack", "type": "content.changed", "object_ids": [], "container": None,
            "user": None, "at": "2026-10-13T01:00:00+00:00"}
    base.update(kw)
    return json.dumps(base).encode()


def test_webhook_content_maps_to_doc_ids(env):
    _, _, conn, _ = env
    body = _body(object_ids=["1728370500.000200", "C0PUB:1728370000.000100"], container="C0PRIV")
    [ev] = conn.parse_webhook(signed_headers(body), body)
    assert ev.type == "content.changed"
    assert ev.object_ids == (PRIV, PUB)


def test_webhook_membership_translates_user(env):
    d, _, conn, _ = env
    body = _body(type="membership.changed", container="C0PRIV", user=d.get("jdoe").platform_ids["slack"])
    [ev] = conn.parse_webhook(signed_headers(body), body)
    assert ev.container == "C0PRIV" and ev.user == "jdoe"
    body2 = _body(type="membership.changed", container="C0PRIV", user="jdoe")
    [ev2] = conn.parse_webhook(signed_headers(body2), body2)
    assert ev2.user == "jdoe"


def test_webhook_rejects_bad_signature_stale_and_replay(env):
    _, _, conn, _ = env
    body = _body(object_ids=["C0PUB:1728370000.000100"])
    bad = signed_headers(body, secret="wrong")
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(bad, body)
    stale = signed_headers(body, ts=int(time.time()) - 301)
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(stale, body)
    future = signed_headers(body, ts=int(time.time()) + 301)
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(future, body)
    h = signed_headers(body)
    conn.parse_webhook(h, body)
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(h, body)                          # replayed delivery id
    tampered = body.replace(b"C0PUB", b"C0PRIV")
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(signed_headers(body), tampered)   # body changed after signing
    with pytest.raises(WebhookRejected):
        conn.parse_webhook({}, body)


def test_webhook_rejects_wrong_platform_or_type(env):
    _, _, conn, _ = env
    for b in (_body(platform="jira"), _body(type="drop.tables"), b"not json"):
        with pytest.raises(WebhookRejected):
            conn.parse_webhook(signed_headers(b), b)


# ---------------------------------------------------------------- links
def test_links_only_supported_shapes():
    text = ("See PAY-201 and DBMIG-12, not pay-201 or X-1 or ABCDEFGHIJK-1. "
            "https://companya.atlassian.net/wiki/spaces/payment-gateway/pages/cf-1001/Gateway "
            "https://companya.atlassian.net.evil.com/wiki/spaces/x/pages/999 "
            "https://evil.example/wiki/spaces/x/pages/123 "
            "https://companya.slack.com/archives/C0DBMIG/p1728370000000100 "
            "https://companya.slack.com/archives/C0DBMIG/p1728370099000100?thread_ts=1728370000.000100 "
            "https://othertenant.slack.com/archives/C0X/p1728370000000100 "
            "https://docs.google.com/document/d/df-7/edit, "
            "https://drive.google.com/file/d/df-8/view https://docs.google.com.evil.io/document/d/zz/ "
            "http://example.com/some/path")
    links = extract_links(text)
    assert links == ("confluence:cf-1001", "slack:C0DBMIG:1728370000.000100", "drive:df-7",
                     "jira:PAY-201", "jira:DBMIG-12")


# ---------------------------------------------------------------- integration: apps/mock_platforms
_TC_TIMEOUT = "ignore:You should not use the 'timeout' argument"


@pytest.fixture()
def mock_env(tmp_path):
    mod = pytest.importorskip("mock_platforms.app", reason="mock_platforms (A2) not available")
    from fixtures_connectors import MockEnv
    env = MockEnv(tmp_path, mod.create_app)
    conn = SlackConnector(env.svc, env.directory, SECRET, sleep=lambda s: None)
    return env, conn


@pytest.mark.filterwarnings(_TC_TIMEOUT)
def test_mock_crawl_acl_and_gate2_agree(mock_env):
    env, conn = mock_env
    batch = conn.list_changes(None)
    expected = {f"slack:{t['channel']}:{t['ts']}" for t in env.world["slack"]["threads"]}
    assert {d.doc_id for d in batch.docs} == expected
    pairs = 0
    for doc in batch.docs:
        expr = conn.acl(doc.doc_id)
        for u in env.directory.all():
            g1 = A.allows(expr, u)
            g2 = conn.check_access(u, doc.doc_id)
            assert g1 == g2.allowed, (doc.doc_id, u.id, g2.reason_code)
            pairs += g1
    assert pairs > 0                                       # non-vacuous
    d = env.directory
    assert not conn.check_access(d.get("ken.ext"), PUB).allowed          # Connect user, not a member
    assert conn.check_access(d.get("gina.guest"), PUB).allowed           # guest who is a member
    assert conn.check_access(d.get("leo.ops"), PUB).allowed              # full member
    assert not conn.check_access(d.get("leo.ops"), VEND).allowed         # Connect channel: members only


@pytest.mark.filterwarnings(_TC_TIMEOUT)
def test_mock_revocation_webhook_and_recrawl(mock_env):
    env, conn = mock_env
    d = env.directory
    b1 = conn.list_changes(None)
    assert conn.check_access(d.get("jdoe"), PRIV).allowed
    env.admin_post("/admin/slack/channels/C0PRIV/members/remove", {"user": "jdoe"})
    platform, headers, body = env.hooks[-1]
    [ev] = conn.parse_webhook(headers, body)
    assert ev.type == "membership.changed" and ev.container == "C0PRIV" and ev.user == "jdoe"
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(headers, body)                                  # replay
    r = conn.check_access(d.get("jdoe"), PRIV)
    assert r.verdict == "forbidden" and r.reason_code == "not_member"
    assert not A.allows(conn.acl(PRIV), d.get("jdoe"))
    b2 = conn.list_changes(b1.cursor)
    assert PRIV in b2.acl_changed


@pytest.mark.filterwarnings(_TC_TIMEOUT)
def test_mock_new_thread_webhook_maps_to_doc(mock_env):
    env, conn = mock_env
    out = env.admin_post("/admin/slack/threads", {"channel": "C0PUB", "user": "jdoe",
                                                  "text": "New thread about DBMIG-12"})
    _, headers, body = env.hooks[-1]
    [ev] = conn.parse_webhook(headers, body)
    doc_id = f"slack:C0PUB:{out['thread_ts']}"
    assert ev.type == "content.changed" and ev.object_ids == (doc_id,)
    doc = conn.fetch(doc_id)
    assert doc is not None and "jira:DBMIG-12" in doc.links and doc.author == "jdoe"


@pytest.mark.filterwarnings(_TC_TIMEOUT)
def test_mock_rate_limit_denies_gate2_but_ingest_waits(mock_env):
    env, conn = mock_env
    env.admin_post("/admin/ratelimit", {"platform": "slack", "on": True})
    r = conn.check_access(env.directory.get("priya.sre"), PRIV)
    assert r.verdict == "forbidden" and r.reason_code == "source_rate_limited"
    from databridge_ib.connectors.base import ConnectorError
    with pytest.raises(ConnectorError):
        conn.list_changes(None)
    env.admin_post("/admin/ratelimit", {"platform": "slack", "on": False})
    assert conn.check_access(env.directory.get("priya.sre"), PRIV).allowed


# ---------------------------------------------------------------- Codex security review r1, track 3
@pytest.mark.parametrize("flag", ["is_private", "is_ext_shared", "is_im", "is_mpim"])
def test_missing_classification_flag_is_unsupported_not_public(env, flag):   # sec3a #2
    d, fake, conn, _ = env
    fake.channels["C0PUB"].pop(flag)
    e = conn.acl(PUB)
    assert isinstance(e, AclNobody)
    assert not any(A.allows(e, u) for u in d.all())
    r = conn.check_access(d.get("leo.ops"), PUB)
    assert r.verdict == "forbidden" and not r.allowed
    assert conn.fetch(PUB) is None
    assert not any(x.doc_id.startswith("slack:C0PUB:") for x in conn.list_changes(None).docs)


def test_non_boolean_classification_flag_is_unsupported(env):
    d, fake, conn, _ = env
    fake.channels["C0PUB"]["is_private"] = "false"
    assert isinstance(conn.acl(PUB), AclNobody)
    assert not conn.check_access(d.get("leo.ops"), PUB).allowed


@pytest.mark.parametrize("flag", ["deleted", "is_restricted", "is_ultra_restricted", "is_stranger"])
def test_gate2_missing_user_flag_denies(env, flag):                          # sec3a #2
    d, fake, conn, _ = env
    fake.users[d.get("leo.ops").platform_ids["slack"]].pop(flag)
    r = conn.check_access(d.get("leo.ops"), PUB)
    assert r.verdict == "forbidden" and r.reason_code == "source_bad_payload" and not r.allowed


def test_gate2_empty_user_object_denies(env):
    d, fake, conn, _ = env
    native = d.get("leo.ops").platform_ids["slack"]
    fake.users[native] = {}
    assert not conn.check_access(d.get("leo.ops"), PUB).allowed


def test_gate2_users_info_about_someone_else_denies(env):
    d, fake, conn, _ = env
    native = d.get("leo.ops").platform_ids["slack"]
    fake.users[native] = dict(fake.users[native], id="U0SOMEONEELSE")
    r = conn.check_access(d.get("leo.ops"), PUB)
    assert r.verdict == "forbidden" and r.reason_code == "source_ambiguous"


@pytest.mark.parametrize("flag", ["is_mpim", "is_im"])
def test_dm_flags_win_over_private_and_shared(env, flag):                   # sec3a #3
    d, fake, conn, _ = env
    fake.channels["C0PRIV"][flag] = True                    # a private-flagged group DM
    assert fake.channels["C0PRIV"]["is_private"] is True
    e = conn.acl(PRIV)
    assert isinstance(e, AclNobody) and not A.allows(e, d.get("priya.sre"))
    r = conn.check_access(d.get("priya.sre"), PRIV)
    assert r.verdict == "forbidden" and r.reason_code == "unsupported_dm"
    assert conn.fetch(PRIV) is None
    fake.channels["C0VEND"][flag] = True                    # and a shared one
    assert conn.check_access(d.get("ken.ext"), VEND).reason_code == "unsupported_dm"
    assert not any(x.doc_id.startswith(("slack:C0PRIV:", "slack:C0VEND:"))
                   for x in conn.list_changes(None).docs)


def test_deleting_a_middle_reply_changes_the_version(env):                  # sec3a #6
    d, fake, conn, _ = env
    key = ("C0PUB", "1728370000.000100")
    fake.reply(*key, "jdoe", "third, newest")
    b1 = conn.list_changes(None)
    v1 = conn.fetch(PUB).version
    deleted_text = fake.threads[key][1]["text"]
    del fake.threads[key][1]                                # B goes; C is still the newest
    v2 = conn.fetch(PUB).version
    assert version_ts(v2) == version_ts(v1) and v2 != v1
    r = conn.check_access(d.get("jdoe"), PUB, known_version=v1)
    assert r.verdict == "drift_material" and r.current_version == v2
    b2 = conn.list_changes(b1.cursor)                       # crawl sees reply_count drop
    [doc] = [x for x in b2.docs if x.doc_id == PUB]
    assert doc.version == v2 and deleted_text not in doc.text


def test_editing_an_older_reply_changes_the_version(env):                   # sec3a #6
    d, fake, conn, _ = env
    key = ("C0PUB", "1728370000.000100")
    fake.reply(*key, "jdoe", "newest")
    v1 = conn.fetch(PUB).version
    fake.threads[key][0]["text"] = "rewritten parent"        # no new ts anywhere
    r = conn.check_access(d.get("jdoe"), PUB, known_version=v1)
    assert r.verdict == "drift_material" and r.current_version != v1


def test_unchanged_thread_keeps_its_version(env):
    _, _, conn, _ = env
    assert conn.fetch(PUB).version == conn.fetch(PUB).version


def test_pagination_cap_denies_gate2_and_fails_ingest(env, monkeypatch):   # sec3a #12
    from databridge_ib.connectors import _http
    from databridge_ib.connectors.base import ConnectorError
    d, fake, conn, _ = env
    fake.add_thread("C0PRIV", "1728370900.000100", [("priya.sre", "a"), ("jdoe", "b"),
                                                    ("priya.sre", "c")])        # 2 pages of replies
    monkeypatch.setattr(_http, "MAX_PAGES", 1)
    r = conn.check_access(d.get("priya.sre"), "slack:C0PRIV:1728370900.000100")
    assert r.verdict == "forbidden" and r.reason_code == "source_incomplete"
    with pytest.raises(ConnectorError):
        conn.list_changes(None)


def test_repeating_cursor_is_rejected(env):
    from databridge_ib.connectors.base import ConnectorError
    d, fake, conn, _ = env
    inner = conn.client._transport.handler                   # wrap the fake: members loop forever
    import httpx

    def looping(request):
        if request.url.path.endswith("conversations.members"):
            return httpx.Response(200, json={"ok": True, "members": [],
                                             "response_metadata": {"next_cursor": "same"}})
        return inner(request)

    conn.client._transport.handler = looping
    r = conn.check_access(d.get("priya.sre"), PRIV)
    assert r.verdict == "forbidden" and r.reason_code == "source_incomplete"
    with pytest.raises(ConnectorError):
        conn.acl(PRIV)


def test_event_user_resolves_native_first_and_never_guesses(env):          # sec3a #10
    from fixtures_connectors import user
    d, fake, conn, _ = env
    jdoe_native = d.get("jdoe").platform_ids["slack"]
    # an unrelated IB user whose id equals jdoe's NATIVE Slack id
    d2 = Directory(people() + [user(jdoe_native, slack="U0OTHER")])
    assert d2.get(jdoe_native) is not None and d2.get(jdoe_native).id != "jdoe"
    conn2 = SlackConnector(conn.client, d2, SECRET)
    assert conn2._ib_user(jdoe_native) == "jdoe"            # native mapping wins
    assert conn2._ib_user("U0NOBODY") is None                # native-shaped, unmapped: unresolved
    assert conn2._ib_user("jdoe") == "jdoe"                  # a known IB id
    assert conn2._ib_user("mallory") is None                 # unknown: not passed through
    body = _body(type="membership.changed", container="C0PRIV", user="U0NOBODY")
    [ev] = conn2.parse_webhook(signed_headers(body), body)
    assert ev.user is None


def test_webhook_delivery_id_is_inside_the_signature(env):                  # sec3a #8
    _, _, conn, _ = env
    body = _body(object_ids=["C0PUB:1728370000.000100"])
    h = signed_headers(body)
    conn.parse_webhook(h, body)
    swapped = dict(h, **{"X-IB-Delivery": "a-fresh-id"})     # same signature, new delivery id
    with pytest.raises(WebhookRejected, match="bad signature"):
        conn.parse_webhook(swapped, body)

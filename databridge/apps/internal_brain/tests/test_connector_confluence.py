"""Confluence connector (A3): space grant ∧ narrowing restrictions, trashed ⇒ vanished, admin-only
permission check at Gate 2 (fail closed), crawl cursor, webhooks."""
from __future__ import annotations

import json
import time

import pytest

from databridge_ib import acl as A
from databridge_ib.connectors.base import ConnectorError, SourceConnector, WebhookRejected
from databridge_ib.connectors.confluence import ConfluenceConnector, storage_to_text
from databridge_ib.models import AclNobody

from fixtures_connectors import (SECRET, Directory, FakeConfluence, client_for, people,
                                 signed_headers)


@pytest.fixture()
def env():
    d = Directory(people())
    fake = FakeConfluence(d)
    sleeps: list[float] = []
    conn = ConfluenceConnector(client_for(fake, "svc-test"), d, SECRET,
                               admin_client=client_for(fake, "admin-test"), sleep=sleeps.append)
    return d, fake, conn, sleeps


def test_protocol(env):
    assert isinstance(env[2], SourceConnector)


# ---------------------------------------------------------------- documents
def test_fetch_page(env):
    _, fake, conn, _ = env
    doc = conn.fetch("confluence:cf-1001")
    p = fake.pages["cf-1001"]
    assert doc.kind == "page" and doc.container == "payment-gateway"
    assert doc.container_name == "Payment Gateway" and doc.title == p["title"]
    assert doc.version == str(p["version"])
    assert doc.deep_link == "https://companya.atlassian.net/wiki/spaces/payment-gateway/pages/cf-1001"
    assert doc.text == storage_to_text(p["body"]) and "<" not in doc.text
    assert doc.links == ("drive:df-7",)
    assert doc.author == "jdoe"


def test_storage_to_text_unescapes():
    assert storage_to_text("<p>a &amp; b</p><p>c</p>") == "a & b\nc"


def test_trashed_and_missing_fetch_none(env):
    _, fake, conn, _ = env
    assert fake.pages["cf-1004"]["status"] == "trashed"
    assert conn.fetch("confluence:cf-1004") is None
    assert conn.fetch("confluence:cf-9999") is None
    assert conn.fetch("jira:PAY-1") is None
    fake.pages["cf-1001"]["status"] = "archived"
    assert conn.fetch("confluence:cf-1001") is None


# ---------------------------------------------------------------- ACL compile
def _expected(fake, d, pid):
    return {u.id for u in d.all() if fake.can_read(u.id, pid)}


@pytest.mark.parametrize("pid", ["cf-1001", "cf-1002", "cf-1003", "cf-2001"])
def test_acl_matches_native_semantics(env, pid):
    d, fake, conn, _ = env
    e = conn.acl(f"confluence:{pid}")
    got = {u.id for u in d.all() if A.allows(e, u)}
    assert got == _expected(fake, d, pid)


def test_nested_restrictions_intersect(env):
    """cf-1003 sits under cf-1002 (users jdoe, priya) and adds grp-sre: only priya survives."""
    d, _, conn, _ = env
    e = conn.acl("confluence:cf-1003")
    readers = {u.id for u in d.all() if A.allows(e, u)}
    assert readers == {"priya.sre"}
    assert A.allows(conn.acl("confluence:cf-1002"), d.get("jdoe"))       # parent alone allows jdoe
    assert not A.allows(e, d.get("jdoe"))                                  # …the child narrows him out


def test_space_grant_still_required_under_restriction(env):
    d, fake, conn, _ = env
    fake.pages["cf-1002"]["restriction"]["users"].append("sara.sec")       # sara not in space grant
    assert not A.allows(conn.acl("confluence:cf-1002"), d.get("sara.sec"))


def test_anonymous_space_is_nobody(env):
    _, _, conn, _ = env
    e = conn.acl("confluence:cf-3001")
    assert isinstance(e, AclNobody)


def test_unreadable_restriction_or_ancestors_fail_closed(env):
    d, fake, conn, _ = env
    fake.error.add("restriction")
    with pytest.raises(ConnectorError):                      # transport failure: caller retries
        conn.acl("confluence:cf-1002")
    fake.error.clear()

    class Gone404(FakeConfluence):
        def __call__(self, request):
            if request.url.path.endswith("/restriction/byOperation/read") and "cf-1001" in request.url.path:
                import httpx
                return httpx.Response(404, json={})
            return super().__call__(request)

    g = Gone404(d)
    c2 = ConfluenceConnector(client_for(g, "svc-test"), d, SECRET)
    assert not any(A.allows(c2.acl("confluence:cf-1002"), u) for u in d.all())


def test_trashed_or_bad_id_is_nobody(env):
    _, _, conn, _ = env
    assert isinstance(conn.acl("confluence:cf-1004"), AclNobody)
    assert isinstance(conn.acl("confluence:../../x"), AclNobody)


def test_group_resolver_maps_names(env):
    d, fake, _, _ = env
    fake.pages["cf-1003"]["restriction"] = {"users": [], "groups": ["sre-team"]}
    conn = ConfluenceConnector(client_for(fake, "svc-test"), d, SECRET,
                               group_resolver=lambda g: {"sre-team": "grp-sre"}.get(g, g))
    assert A.allows(conn.acl("confluence:cf-1003"), d.get("priya.sre"))
    # a resolver that does not know a group DROPS it (never grants by raw name)
    strict = ConfluenceConnector(client_for(fake, "svc-test"), d, SECRET, group_resolver=lambda g: None)
    assert not any(A.allows(strict.acl("confluence:cf-1001"), u) for u in d.all())


# ---------------------------------------------------------------- Gate 2
def test_gate2_permitted_and_not(env):
    d, fake, conn, _ = env
    r = conn.check_access(d.get("priya.sre"), "confluence:cf-1003")
    assert r.verdict == "verified" and r.current_version == str(fake.pages["cf-1003"]["version"])
    r = conn.check_access(d.get("jdoe"), "confluence:cf-1003")
    assert r.verdict == "forbidden" and r.reason_code == "not_permitted"


def test_gate2_uses_admin_client_only(env):
    d, fake, conn, _ = env
    no_admin = ConfluenceConnector(conn.client, d, SECRET)
    r = no_admin.check_access(d.get("priya.sre"), "confluence:cf-1003")
    assert not r.allowed and r.verdict == "unverifiable"
    # a non-admin token as "admin" gets 403 → deny
    svc_as_admin = ConfluenceConnector(conn.client, d, SECRET, admin_client=conn.client)
    r = svc_as_admin.check_access(d.get("priya.sre"), "confluence:cf-1003")
    assert r.verdict == "forbidden" and r.reason_code == "source_error"


def test_gate2_missing_page_vanished(env):
    d, _, conn, _ = env
    assert conn.check_access(d.get("jdoe"), "confluence:cf-9999").verdict == "vanished"


def test_gate2_trashed_page_vanished(env):
    d, fake, conn, _ = env
    fake.pages["cf-1001"]["status"] = "trashed"
    assert conn.check_access(d.get("jdoe"), "confluence:cf-1001").verdict == "vanished"


@pytest.mark.parametrize("fault,endpoint,reason", [
    ("rate_limited", "permission/check", "source_rate_limited"),
    ("timeout", "permission/check", "source_timeout"),
    ("error", "permission/check", "source_error"),
    ("rate_limited", "page", "source_rate_limited"),
])
def test_gate2_failures_deny_without_retry(env, fault, endpoint, reason):
    d, fake, conn, sleeps = env
    getattr(fake, fault).add(endpoint)
    r = conn.check_access(d.get("jdoe"), "confluence:cf-1001")
    assert r.verdict == "forbidden" and r.reason_code == reason
    assert fake.calls.count(endpoint) == 1 and sleeps == []


def test_gate2_drift(env):
    d, fake, conn, _ = env
    old = conn.fetch("confluence:cf-1001").version
    fake.edit("cf-1001", "<p>v2</p>")
    r = conn.check_access(d.get("jdoe"), "confluence:cf-1001", known_version=old)
    assert r.verdict == "drift_material" and r.current_version == str(fake.pages["cf-1001"]["version"])
    assert conn.check_access(d.get("jdoe"), "confluence:cf-1001",
                             known_version=r.current_version).verdict == "verified"


def test_gate2_inactive_or_unmapped_user(env):
    d, _, conn, _ = env
    u = d.get("jdoe")
    assert conn.check_access(u.model_copy(update={"active": False}), "confluence:cf-1001").verdict == "forbidden"
    assert conn.check_access(u.model_copy(update={"platform_ids": {}}), "confluence:cf-1001").verdict == "forbidden"


# ---------------------------------------------------------------- crawl
def _current(fake):
    return {pid for pid, p in fake.pages.items() if p["status"] == "current"}


def test_crawl_full_and_incremental(env):
    _, fake, conn, _ = env
    b1 = conn.list_changes(None)
    assert {x.doc_id for x in b1.docs} == {f"confluence:{p}" for p in _current(fake)}
    assert b1.deleted == () and b1.acl_changed == ()
    b2 = conn.list_changes(b1.cursor)
    assert b2.docs == () and b2.deleted == () and b2.acl_changed == ()
    fake.edit("cf-2001", "<p>updated</p>")
    fake.pages["cf-1001"]["status"] = "trashed"
    fake.add_page("cf-1005", "sp-1", None, "New page", "<p>new</p>")
    b3 = conn.list_changes(b2.cursor)
    assert {x.doc_id for x in b3.docs} == {"confluence:cf-2001", "confluence:cf-1005"}
    assert b3.deleted == ("confluence:cf-1001",)
    assert json.loads(b3.cursor)["pages"]["cf-2001"] == str(fake.pages["cf-2001"]["version"])


def test_crawl_restriction_change_marks_subtree(env):
    _, fake, conn, _ = env
    b1 = conn.list_changes(None)
    fake.pages["cf-1002"]["restriction"] = {"users": ["priya.sre"], "groups": []}
    b2 = conn.list_changes(b1.cursor)
    assert set(b2.acl_changed) == {"confluence:cf-1002", "confluence:cf-1003"}   # page + descendant
    assert b2.docs == ()


def test_crawl_space_grant_change_marks_space(env):
    _, fake, conn, _ = env
    b1 = conn.list_changes(None)
    fake.spaces["sp-2"]["read"]["groups"].append("grp-eng")
    b2 = conn.list_changes(b1.cursor)
    assert set(b2.acl_changed) == {f"confluence:{p}" for p in _current(fake)
                                   if fake.pages[p]["space"] == "sp-2"}


def test_crawl_retries_429(env):
    _, fake, conn, sleeps = env
    fake.rate_limit_once.add("pages")
    assert conn.list_changes(None).docs
    assert sleeps == [2.0]


def test_crawl_bad_cursor(env):
    with pytest.raises(ConnectorError):
        env[2].list_changes('{"v": 99}')


# ---------------------------------------------------------------- webhooks
def _body(**kw):
    base = {"platform": "confluence", "type": "content.changed", "object_ids": ["cf-1001"],
            "container": None, "user": None, "at": "2026-10-13T01:00:00Z"}
    base.update(kw)
    return json.dumps(base).encode()


def test_webhook_maps_page_ids(env):
    _, _, conn, _ = env
    body = _body(type="acl.changed", object_ids=["cf-1002"], container="payment-gateway")
    [ev] = conn.parse_webhook(signed_headers(body), body)
    assert ev.object_ids == ("confluence:cf-1002",) and ev.container == "payment-gateway"
    assert ev.at.tzinfo is not None


def test_webhook_user_event_translates_account(env):
    _, _, conn, _ = env
    body = _body(type="user.changed", object_ids=[], user="acc-jdoe")
    [ev] = conn.parse_webhook(signed_headers(body), body)
    assert ev.user == "jdoe"


def test_webhook_rejections(env):
    _, _, conn, _ = env
    body = _body()
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(signed_headers(body, secret="nope"), body)
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(signed_headers(body, ts=int(time.time()) - 400), body)
    h = signed_headers(body)
    conn.parse_webhook(h, body)
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(h, body)
    slack_body = _body(platform="slack")
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(signed_headers(slack_body), slack_body)


# ---------------------------------------------------------------- integration: apps/mock_platforms
_TC_TIMEOUT = "ignore:You should not use the 'timeout' argument"


@pytest.fixture()
def mock_env(tmp_path):
    mod = pytest.importorskip("mock_platforms.app", reason="mock_platforms (A2) not available")
    from fixtures_connectors import MockEnv
    env = MockEnv(tmp_path, mod.create_app)
    conn = ConfluenceConnector(env.svc, env.directory, SECRET, admin_client=env.admin,
                               sleep=lambda s: None)
    return env, conn


@pytest.mark.filterwarnings(_TC_TIMEOUT)
def test_mock_crawl_acl_and_gate2_agree(mock_env):
    env, conn = mock_env
    batch = conn.list_changes(None)
    current = {f"confluence:{p['id']}" for p in env.world["confluence"]["pages"] if p["status"] == "current"}
    assert {d.doc_id for d in batch.docs} == current
    allowed_pairs = 0
    for doc in batch.docs:
        expr = conn.acl(doc.doc_id)
        for u in env.directory.all():
            g2 = conn.check_access(u, doc.doc_id)
            assert A.allows(expr, u) == g2.allowed, (doc.doc_id, u.id, g2.reason_code)
            allowed_pairs += g2.allowed
    assert allowed_pairs > 0
    d = env.directory
    assert {u.id for u in d.all() if A.allows(conn.acl("confluence:cf-1003"), u)} == {"priya.sre"}
    assert conn.fetch("confluence:cf-1004") is None
    assert conn.check_access(d.get("jdoe"), "confluence:cf-1004").verdict == "vanished"


@pytest.mark.filterwarnings(_TC_TIMEOUT)
def test_mock_restrict_webhook_and_recrawl(mock_env):
    env, conn = mock_env
    d = env.directory
    b1 = conn.list_changes(None)
    env.admin_post("/admin/confluence/pages/cf-1001/restrict", {"users": ["priya.sre"]})
    _, headers, body = env.hooks[-1]
    [ev] = conn.parse_webhook(headers, body)
    assert ev.type == "acl.changed" and ev.container == "payment-gateway"
    b2 = conn.list_changes(b1.cursor)
    live = {f"confluence:{p['id']}" for p in env.world["confluence"]["pages"] if p["status"] == "current"}
    assert set(b2.acl_changed) == set(ev.object_ids) & live               # page + its live subtree
    assert {"confluence:cf-1001", "confluence:cf-1002", "confluence:cf-1003"} <= set(b2.acl_changed)
    assert not conn.check_access(d.get("jdoe"), "confluence:cf-1001").allowed
    assert not A.allows(conn.acl("confluence:cf-1002"), d.get("jdoe"))   # inherited narrowing


@pytest.mark.filterwarnings(_TC_TIMEOUT)
def test_mock_edit_drift_and_trash(mock_env):
    env, conn = mock_env
    d = env.directory
    b1 = conn.list_changes(None)
    v0 = conn.fetch("confluence:cf-1001").version
    env.admin_post("/admin/confluence/pages/cf-1001/edit", {"body": "Overview v2"})
    r = conn.check_access(d.get("jdoe"), "confluence:cf-1001", known_version=v0)
    assert r.verdict == "drift_material" and r.current_version == str(int(v0) + 1)
    env.admin_post("/admin/confluence/pages/cf-2001/trash")
    b2 = conn.list_changes(b1.cursor)
    assert {x.doc_id for x in b2.docs} == {"confluence:cf-1001"}
    assert b2.deleted == ("confluence:cf-2001",)
    assert conn.check_access(d.get("leo.ops"), "confluence:cf-2001").verdict == "vanished"


@pytest.mark.filterwarnings(_TC_TIMEOUT)
def test_mock_permission_check_needs_admin_and_429_denies(mock_env):
    env, conn = mock_env
    d = env.directory
    as_svc = ConfluenceConnector(env.svc, d, SECRET, admin_client=env.svc)
    assert as_svc.check_access(d.get("jdoe"), "confluence:cf-1001").verdict == "forbidden"
    env.admin_post("/admin/ratelimit", {"platform": "confluence", "on": True})
    r = conn.check_access(d.get("jdoe"), "confluence:cf-1001")
    assert r.verdict == "forbidden" and r.reason_code == "source_rate_limited"

"""Jira connector (B1): browse(project) ∧ security level, reporter/assignee holders, role actors via
groups, unsupported holders fail closed, admin-only permission check at Gate 2 (fail closed, no
retry), JQL crawl with fingerprints, webhooks, and Gate 1 == Gate 2 against apps/mock_platforms."""
from __future__ import annotations

import json
import time

import pytest

from databridge_ib import acl as A
from databridge_ib.connectors.base import ConnectorError, SourceConnector, WebhookRejected
from databridge_ib.connectors.jira import JiraConnector, adf_to_text, issue_key_of
from databridge_ib.models import AclNobody

from fixtures_connectors import SECRET, Directory, client_for, signed_headers
from fixtures_connectors.jira_drive import FakeJira, jd_people


class Clock:
    def __init__(self) -> None:
        self.t = time.time()

    def __call__(self) -> float:
        return self.t


@pytest.fixture()
def env():
    d = Directory(jd_people())
    fake = FakeJira(d)
    sleeps: list[float] = []
    clock = Clock()
    conn = JiraConnector(client_for(fake, "svc-test"), d, SECRET,
                         admin_client=client_for(fake, "admin-test"), sleep=sleeps.append,
                         page_size=2, now=clock)
    return d, fake, conn, sleeps, clock


def readers(conn, d, doc_id):
    e = conn.acl(doc_id)
    return {u.id for u in d.all() if A.allows(e, u)}


def expected(fake, d, key):
    return {u.id for u in d.all() if fake.can_browse(u.id, key)}


def test_protocol(env):
    assert isinstance(env[2], SourceConnector)


def test_issue_key_of():
    assert issue_key_of("jira:PAY-201") == "PAY-201"
    for bad in ("jira:pay-1", "jira:PAY", "confluence:PAY-1", "jira:../PAY-1", "jira:PAY-1/x"):
        assert issue_key_of(bad) is None


# ---------------------------------------------------------------- documents
def test_fetch_issue(env):
    _, fake, conn, _, _ = env
    doc = conn.fetch("jira:PAY-1")
    i = fake.issues["PAY-1"]
    assert doc.doc_id == "jira:PAY-1" and doc.kind == "issue" and doc.platform == "jira"
    assert doc.container == "PAY" and doc.container_name == "Payments"
    assert doc.title == f"PAY-1: {i['summary']}"
    assert doc.version == i["updated"]
    assert doc.deep_link == "https://companya.atlassian.net/browse/PAY-1"
    assert doc.author == "jdoe"
    for part in i["description"].split("\n\n"):
        assert part in doc.text                                # ADF flattened to plain text
    assert f"Status: {i['status']}" in doc.text
    assert "(jdoe)" in doc.text and "(priya.sre)" in doc.text  # reporter / assignee
    assert "priya.sre: Replica lag is fine." in doc.text       # "<ib user>: body"
    assert set(doc.links) == {"confluence:cf-1001", "jira:SEC-1", "jira:PAY-3"}
    assert "jira:PAY-1" not in doc.links


def test_fetch_missing_or_bad_id(env):
    _, _, conn, _, _ = env
    assert conn.fetch("jira:PAY-999") is None
    assert conn.fetch("jira:not a key") is None
    assert conn.fetch("drive:df-1") is None


def test_adf_to_text():
    doc = {"type": "doc", "content": [
        {"type": "heading", "content": [{"type": "text", "text": "Title"}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "a "},
                                          {"type": "mention", "attrs": {"text": "@Jo"}},
                                          {"type": "hardBreak"}, {"type": "text", "text": "b"}]},
        {"type": "bulletList", "content": [{"type": "listItem", "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "item"}]}]}]},
        {"type": "paragraph", "content": [{"type": "inlineCard", "attrs": {"url": "https://x/y"}}]}]}
    assert adf_to_text(doc) == "Title\na @Jo\nb\nitem\nhttps://x/y"
    assert adf_to_text("plain") == "plain" and adf_to_text(None) == ""


# ---------------------------------------------------------------- ACL compile (Gate 1)
@pytest.mark.parametrize("key", ["PAY-1", "PAY-2", "PAY-3", "PAY-4", "SEC-1"])
def test_acl_matches_native_semantics(env, key):
    d, fake, conn, _, _ = env
    got = readers(conn, d, f"jira:{key}")
    assert got == expected(fake, d, key)


def test_acl_non_vacuous(env):
    d, fake, conn, _, _ = env
    sizes = [len(readers(conn, d, f"jira:{k}")) for k in fake.issues]
    assert all(s < len(d.all()) for s in sizes) and any(s > 0 for s in sizes)


def test_reporter_on_security_level_issue(env):
    """PAY-2: level 'Security only' lists the reporter; mia.pm (reporter, no role) may read, the
    assignee jdoe (Developer, but the level does not list assignees) may not, and sara.sec (level
    group member but no Browse) may not either — both gates must pass."""
    d, _, conn, _, _ = env
    assert readers(conn, d, "jira:PAY-2") == {"mia.pm"}


def test_reporter_holder_removed_denies(env):
    d, fake, conn, _, _ = env
    fake.levels["10100"]["members"]["reporter"] = False
    assert "mia.pm" not in readers(conn, d, "jira:PAY-2")
    fake.projects["PAY"]["browse"]["reporter"] = False
    assert "ken.ext" not in readers(conn, d, "jira:PAY-4")      # reporter-only browse grant gone


def test_role_actor_via_group(env):
    """PAY-3: level lists the Developers ROLE whose actor is GROUP grp-payments-eng → jdoe."""
    d, fake, conn, _, _ = env
    r = readers(conn, d, "jira:PAY-3")
    assert "jdoe" in r and "leo.ops" not in r                   # leo: Viewer + reporter, not a Developer
    fake.projects["PAY"]["roles"]["Developers"]["groups"] = []
    assert "jdoe" not in readers(conn, d, "jira:PAY-3")


def test_assignee_not_holder_in_sec(env):
    d, _, conn, _, _ = env
    r = readers(conn, d, "jira:SEC-1")
    assert r == {"sara.sec"} and "jdoe" not in r                 # assignee is not a Browse holder


@pytest.mark.parametrize("holder", [
    {"type": "applicationRole", "parameter": "jira-software"},
    {"type": "userCustomField", "parameter": "customfield_10010"},
    {"type": "groupCustomField", "parameter": "customfield_10011"},
    {"type": "projectLead"},
    {"type": "anyone"},
    {"type": "somethingNew"},
])
def test_unsupported_browse_holder_grants_nobody(env, holder):
    d, fake, conn, _, _ = env
    before = readers(conn, d, "jira:PAY-1")
    fake.projects["PAY"]["browse"]["extra"].append(holder)
    e = conn.acl("jira:PAY-1")
    assert {u.id for u in d.all() if A.allows(e, u)} == before  # never widens
    assert any(isinstance(x, AclNobody) for x in e.of[0].of)    # the branch is an explicit nobody
    # an issue browsable ONLY through the unsupported holder is readable by nobody
    fake.projects["SEC"]["browse"]["roles"] = []
    fake.projects["SEC"]["browse"]["extra"].append(holder)
    assert readers(conn, d, "jira:SEC-1") == set()


def test_unsupported_level_holder_and_role_actor(env):
    d, fake, conn, _, _ = env
    fake.levels["10100"]["members"]["groups"] = []
    fake.levels["10100"]["members"]["reporter"] = False
    fake.levels["10100"]["members"]["extra"].append({"type": "userCustomField", "parameter": "cf"})
    assert readers(conn, d, "jira:PAY-2") == set()
    fake.projects["PAY"]["roles"]["Viewers"]["extra"] = [{"type": "atlassian-app-role-actor"}]
    assert "leo.ops" in readers(conn, d, "jira:PAY-1")          # supported actors still count


def test_empty_or_unknown_level_is_nobody(env):
    d, fake, conn, _, _ = env
    fake.issues["PAY-1"]["security_level"] = "10999"
    fake.levels["10999"] = {"project": "PAY", "name": "ghost",
                            "members": {"users": [], "groups": [], "roles": [], "reporter": False,
                                        "assignee": False, "extra": []}}
    assert readers(conn, d, "jira:PAY-1") == set()
    del fake.levels["10999"]                                    # level members: empty listing
    assert readers(conn, d, "jira:PAY-1") == set()


def test_unreadable_role_or_scheme_fail_closed(env):
    d, fake, conn, _, _ = env
    del fake.projects["PAY"]["roles"]["Viewers"]                # scheme names a role that 404s
    r = readers(conn, d, "jira:PAY-1")
    assert "leo.ops" not in r and "jdoe" in r
    fake.error.add("scheme")
    with pytest.raises(ConnectorError):                         # transport failure: ingest retries
        conn.acl("jira:PAY-1")


def test_acl_missing_issue_or_bad_id(env):
    _, _, conn, _, _ = env
    assert isinstance(conn.acl("jira:PAY-999"), AclNobody)
    assert isinstance(conn.acl("jira:bad"), AclNobody)


def conn_default(fake, d):
    return JiraConnector(client_for(fake, "svc-test"), d, SECRET)


def test_group_resolver(env):
    d, fake, _, _, _ = env
    strict = JiraConnector(client_for(fake, "svc-test"), d, SECRET, group_resolver=lambda g: None)
    assert "jdoe" in {u.id for u in d.all() if A.allows(conn_default(fake, d).acl("jira:PAY-3"), u)}
    assert "jdoe" not in {u.id for u in d.all() if A.allows(strict.acl("jira:PAY-3"), u)}


# ---------------------------------------------------------------- Gate 2
def test_gate2_matches_native(env):
    d, fake, conn, _, _ = env
    allowed = 0
    for key in fake.issues:
        for u in d.all():
            r = conn.check_access(u, f"jira:{key}")
            assert r.allowed == fake.can_browse(u.id, key), (key, u.id, r.reason_code)
            allowed += r.allowed
            if r.allowed:
                assert r.verdict == "verified" and r.current_version == fake.issues[key]["updated"]
            else:
                assert r.reason_code == "not_permitted"
    assert 0 < allowed < len(fake.issues) * len(d.all())


def test_gate2_needs_admin(env):
    d, fake, conn, _, _ = env
    no_admin = JiraConnector(conn.client, d, SECRET)
    r = no_admin.check_access(d.get("jdoe"), "jira:PAY-1")
    assert r.verdict == "unverifiable" and not r.allowed
    svc_as_admin = JiraConnector(conn.client, d, SECRET, admin_client=conn.client)
    r = svc_as_admin.check_access(d.get("jdoe"), "jira:PAY-1")
    assert r.verdict == "forbidden" and r.reason_code == "source_error"


def test_gate2_vanished(env):
    d, fake, conn, _, _ = env
    assert conn.check_access(d.get("jdoe"), "jira:PAY-999").verdict == "vanished"


@pytest.mark.parametrize("fault,endpoint,reason", [
    ("rate_limited", "check", "source_rate_limited"),
    ("timeout", "check", "source_timeout"),
    ("error", "check", "source_error"),
    ("rate_limited", "issue", "source_rate_limited"),
    ("timeout", "issue", "source_timeout"),
])
def test_gate2_failures_deny_without_retry(env, fault, endpoint, reason):
    d, fake, conn, sleeps, _ = env
    getattr(fake, fault).add(endpoint)
    r = conn.check_access(d.get("jdoe"), "jira:PAY-1")
    assert r.verdict == "forbidden" and r.reason_code == reason
    assert fake.calls.count(endpoint) == 1 and sleeps == []


def test_gate2_bad_or_ambiguous_payload(env):
    d, fake, conn, _, _ = env
    import httpx

    class Weird(FakeJira):
        mode = "none"

        def __call__(self, request):
            if request.url.path.endswith("permissions/check"):
                if self.mode == "none":
                    return httpx.Response(200, json={"nope": 1})
                if self.mode == "two":
                    return httpx.Response(200, json={"projectPermissions": [
                        {"permission": "BROWSE_PROJECTS", "issues": [20001]},
                        {"permission": "BROWSE_PROJECTS", "issues": []}]})
                return httpx.Response(200, text="not json")
            return super().__call__(request)

    w = Weird(d)
    c = JiraConnector(client_for(w, "svc-test"), d, SECRET, admin_client=client_for(w, "admin-test"))
    assert c.check_access(d.get("jdoe"), "jira:PAY-1").reason_code == "source_bad_payload"
    w.mode = "two"
    assert c.check_access(d.get("jdoe"), "jira:PAY-1").reason_code == "source_ambiguous"
    w.mode = "text"
    assert c.check_access(d.get("jdoe"), "jira:PAY-1").reason_code == "source_bad_payload"


def test_gate2_drift(env):
    d, fake, conn, _, _ = env
    old = conn.fetch("jira:PAY-1").version
    fake.edit("PAY-1", summary="Migrate ledger DB (v2)")
    r = conn.check_access(d.get("jdoe"), "jira:PAY-1", known_version=old)
    assert r.verdict == "drift_material" and r.allowed
    assert r.current_version == fake.issues["PAY-1"]["updated"] != old
    assert conn.check_access(d.get("jdoe"), "jira:PAY-1",
                             known_version=r.current_version).verdict == "verified"


def test_gate2_inactive_or_unmapped(env):
    d, _, conn, _, _ = env
    u = d.get("jdoe")
    assert conn.check_access(u.model_copy(update={"active": False}), "jira:PAY-1").verdict == "forbidden"
    r = conn.check_access(u.model_copy(update={"platform_ids": {}}), "jira:PAY-1")
    assert r.verdict == "forbidden" and r.reason_code == "no_platform_identity"
    assert conn.check_access(u, "jira:bad").reason_code == "bad_doc_id"


# ---------------------------------------------------------------- crawl
def test_crawl_full_then_incremental(env):
    _, fake, conn, _, _ = env
    b1 = conn.list_changes(None)
    assert {x.doc_id for x in b1.docs} == {f"jira:{k}" for k in fake.issues}
    assert b1.deleted == () and b1.acl_changed == ()
    b2 = conn.list_changes(b1.cursor)
    assert b2.docs == () and b2.deleted == () and b2.acl_changed == ()   # overlap re-reads are skipped
    fake.edit("PAY-4", summary="Vendor SLA v2")
    fake.add_issue("PAY-5", "20005", "New thing", "new", reporter="jdoe", assignee=None)
    b3 = conn.list_changes(b2.cursor)
    assert {x.doc_id for x in b3.docs} == {"jira:PAY-4", "jira:PAY-5"}
    cur = json.loads(b3.cursor)
    assert cur["issues"]["PAY-4"]["u"] == fake.issues["PAY-4"]["updated"]
    assert cur["hw"] == max(i["updated"] for i in fake.issues.values())


def test_crawl_jql_uses_overlap_window(env):
    _, fake, conn, _, _ = env
    b1 = conn.list_changes(None)
    seen: list[str] = []
    orig = fake.__call__


    def spy(request):
        if request.url.path.endswith("search/jql"):
            seen.append(request.url.params["jql"])
        return orig(request)

    conn.client = client_for(spy, "svc-test")
    conn.list_changes(b1.cursor)
    hw = json.loads(b1.cursor)["hw"]
    from datetime import datetime, timedelta
    want = (datetime.fromisoformat(hw) - timedelta(minutes=2)).isoformat(timespec="seconds")
    assert seen and seen[0] == f'updated >= "{want}" ORDER BY updated'


def test_crawl_deletions_need_full_listing(env):
    _, fake, conn, _, clock = env
    conn.full_every_s = 3600
    b1 = conn.list_changes(None)
    del fake.issues["PAY-4"]
    b2 = conn.list_changes(b1.cursor)
    assert b2.deleted == ()                                     # incremental JQL cannot see deletes
    clock.t += 3601
    b3 = conn.list_changes(b2.cursor)
    assert b3.deleted == ("jira:PAY-4",)
    assert "PAY-4" not in json.loads(b3.cursor)["issues"]


def test_crawl_full_listing_catches_change_outside_window(env):
    _, fake, conn, _, clock = env
    b1 = conn.list_changes(None)
    fake.issues["PAY-1"]["summary"] = "backdated edit"
    fake.issues["PAY-1"]["updated"] = "2026-09-01T00:00:00+08:00"   # older than hw - 2 min
    clock.t += 4000
    b2 = conn.list_changes(b1.cursor)
    assert [x.doc_id for x in b2.docs] == ["jira:PAY-1"] and "backdated edit" in b2.docs[0].title


def test_crawl_security_level_change_marks_issue(env):
    _, fake, conn, _, _ = env
    b1 = conn.list_changes(None)
    fake.edit("PAY-1", security_level="10100")
    b2 = conn.list_changes(b1.cursor)
    assert b2.acl_changed == ("jira:PAY-1",)
    assert [x.doc_id for x in b2.docs] == ["jira:PAY-1"]


def test_crawl_role_actor_change_marks_project_without_updated_bump(env):
    _, fake, conn, _, _ = env
    b1 = conn.list_changes(None)
    fake.projects["PAY"]["roles"]["Viewers"]["users"].append("mia.pm")
    b2 = conn.list_changes(b1.cursor)
    assert set(b2.acl_changed) == {f"jira:{k}" for k, i in fake.issues.items() if i["project"] == "PAY"}
    assert b2.docs == ()


def test_crawl_browse_holder_and_level_member_change(env):
    _, fake, conn, _, _ = env
    b1 = conn.list_changes(None)
    fake.projects["SEC"]["browse"]["assignee"] = True
    b2 = conn.list_changes(b1.cursor)
    assert b2.acl_changed == ("jira:SEC-1",)
    fake.levels["10101"]["members"]["users"].append("leo.ops")
    b3 = conn.list_changes(b2.cursor)
    assert b3.acl_changed == ("jira:PAY-3",)


def test_crawl_retries_429(env):
    _, fake, conn, sleeps, _ = env
    fake.rate_limit_once.add("search")
    assert conn.list_changes(None).docs
    assert sleeps == [2.0]


def test_crawl_bad_cursor(env):
    with pytest.raises(ConnectorError):
        env[2].list_changes('{"v": 99}')
    with pytest.raises(ConnectorError):
        env[2].list_changes("not json")


# ---------------------------------------------------------------- webhooks
def _body(**kw):
    base = {"platform": "jira", "type": "content.changed", "object_ids": ["PAY-1"],
            "container": None, "user": None, "at": "2026-10-13T01:00:00Z"}
    base.update(kw)
    return json.dumps(base).encode()


def test_webhook_maps_issue_keys(env):
    _, _, conn, _, _ = env
    body = _body(type="acl.changed", object_ids=["PAY-1", "jira:PAY-2"], container="PAY")
    [ev] = conn.parse_webhook(signed_headers(body), body)
    assert ev.object_ids == ("jira:PAY-1", "jira:PAY-2") and ev.container == "PAY"
    assert ev.type == "acl.changed" and ev.at.tzinfo is not None


def test_webhook_user_event(env):
    _, _, conn, _, _ = env
    body = _body(type="user.changed", object_ids=["acc-jdoe"], user="acc-jdoe")
    [ev] = conn.parse_webhook(signed_headers(body), body)
    assert ev.user == "jdoe" and ev.object_ids == ("acc-jdoe",)


def test_webhook_rejections(env):
    _, _, conn, _, _ = env
    body = _body()
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(signed_headers(body, secret="nope"), body)
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(signed_headers(body, ts=int(time.time()) - 400), body)
    h = signed_headers(body)
    conn.parse_webhook(h, body)
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(h, body)                              # replay
    other = _body(platform="drive")
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(signed_headers(other), other)
    bad_type = _body(type="issue.exploded")
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(signed_headers(bad_type), bad_type)
    with pytest.raises(WebhookRejected):
        conn.parse_webhook({}, body)
    tampered = _body(object_ids=["SEC-1"])
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(signed_headers(body), tampered)


# ---------------------------------------------------------------- integration: apps/mock_platforms
_TC_TIMEOUT = "ignore:You should not use the 'timeout' argument"


@pytest.fixture()
def mock_env(tmp_path):
    mod = pytest.importorskip("mock_platforms.app", reason="mock_platforms not available")
    from fixtures_connectors.jira_drive import JiraDriveMockEnv
    env = JiraDriveMockEnv(tmp_path, mod.create_app)
    conn = JiraConnector(env.svc, env.directory, SECRET, admin_client=env.admin,
                         sleep=lambda s: None, page_size=2, full_every_s=0)
    return env, conn


@pytest.mark.filterwarnings(_TC_TIMEOUT)
def test_mock_gate1_equals_gate2_for_every_pair(mock_env):
    env, conn = mock_env
    batch = conn.list_changes(None)
    keys = {f"jira:{i['key']}" for i in env.world["jira"]["issues"]}
    assert {x.doc_id for x in batch.docs} == keys
    allowed = 0
    for doc in batch.docs:
        expr = conn.acl(doc.doc_id)
        for u in env.directory.all():
            g2 = conn.check_access(u, doc.doc_id)
            assert A.allows(expr, u) == g2.allowed, (doc.doc_id, u.id, g2.reason_code)
            allowed += g2.allowed
    assert 0 < allowed < len(keys) * len(env.directory.all())
    d = env.directory
    assert {u.id for u in d.all() if A.allows(conn.acl("jira:PAY-2"), u)} == {"mia.pm"}
    pay1 = conn.fetch("jira:PAY-1")
    assert "Second paragraph for PAY-1." in pay1.text and "jira:PAY-3" in pay1.links
    assert "priya.sre: Replica lag is fine." in pay1.text


@pytest.mark.filterwarnings(_TC_TIMEOUT)
def test_mock_security_level_edit_webhook_recrawl_and_drift(mock_env):
    env, conn = mock_env
    d = env.directory
    b1 = conn.list_changes(None)
    v0 = conn.fetch("jira:PAY-1").version
    assert conn.check_access(d.get("jdoe"), "jira:PAY-1").allowed
    env.admin_post("/admin/jira/issues/PAY-1/edit", {"security_level": "10100"})
    events = [conn.parse_webhook(h, b)[0] for _, h, b in env.hooks[-2:]]
    assert {e.type for e in events} == {"content.changed", "acl.changed"}
    acl_ev = next(e for e in events if e.type == "acl.changed")
    assert acl_ev.object_ids == ("jira:PAY-1",) and acl_ev.container == "PAY"
    b2 = conn.list_changes(b1.cursor)
    assert "jira:PAY-1" in b2.acl_changed and [x.doc_id for x in b2.docs] == ["jira:PAY-1"]
    r = conn.check_access(d.get("priya.sre"), "jira:PAY-1", known_version=v0)
    assert r.verdict == "forbidden" and r.reason_code == "not_permitted"   # assignee: not a level holder
    assert not A.allows(conn.acl("jira:PAY-1"), d.get("priya.sre"))
    r = conn.check_access(d.get("jdoe"), "jira:PAY-1", known_version=v0)
    assert r.verdict == "drift_material"                          # the reporter IS a level holder
    assert A.allows(conn.acl("jira:PAY-1"), d.get("jdoe"))
    env.admin_post("/admin/jira/issues/PAY-4/edit", {"summary": "SLA v2"})
    r = conn.check_access(d.get("ken.ext"), "jira:PAY-4",
                          known_version=b1.docs[[x.doc_id for x in b1.docs].index("jira:PAY-4")].version)
    assert r.verdict == "drift_material"


@pytest.mark.filterwarnings(_TC_TIMEOUT)
def test_mock_permission_check_needs_admin_and_429_denies(mock_env):
    env, conn = mock_env
    d = env.directory
    as_svc = JiraConnector(env.svc, d, SECRET, admin_client=env.svc)
    assert as_svc.check_access(d.get("jdoe"), "jira:PAY-1").verdict == "forbidden"
    assert conn.check_access(d.get("jdoe"), "jira:PAY-404").verdict == "vanished"
    env.admin_post("/admin/ratelimit", {"platform": "jira", "on": True})
    r = conn.check_access(d.get("jdoe"), "jira:PAY-1")
    assert r.verdict == "forbidden" and r.reason_code == "source_rate_limited"


# ---------------------------------------------------------------- Codex security review r1, track 3
def _wrap(conn, fn):
    """Post-process service-client responses: ``fn(request, response) -> response``."""
    inner = conn.client._transport.handler
    conn.client._transport.handler = lambda request: fn(request, inner(request))


def _rewrite_issue(conn, key, mutate):
    import httpx

    def fn(request, resp):
        if request.url.path.endswith(f"/issue/{key}") and resp.status_code == 200:
            data = resp.json()
            mutate(data["fields"])
            return httpx.Response(200, json=data)
        return resp

    _wrap(conn, fn)


def test_restricted_comment_is_never_indexed(env):                         # sec3b #1
    d, fake, conn, _, _ = env
    fake.issues["PAY-1"]["comments"].append(
        {"author": "sara.sec", "body": "ROLE-ONLY-REMARK incident key", "created": "2026-10-10T10:00:00+08:00",
         "visibility": {"type": "role", "value": "Administrators"}})
    fake.issues["PAY-1"]["comments"].append(
        {"author": "sara.sec", "body": "GROUP-ONLY-REMARK", "created": "2026-10-10T10:00:00+08:00",
         "visibility": {"type": "group", "value": "grp-security", "identifier": "grp-security"}})
    doc = conn.fetch("jira:PAY-1")
    assert "Replica lag is fine." in doc.text                 # the unrestricted comment stays
    assert "ROLE-ONLY-REMARK" not in doc.text and "GROUP-ONLY-REMARK" not in doc.text
    crawled = {x.doc_id: x for x in conn.list_changes(None).docs}
    assert "ROLE-ONLY-REMARK" not in crawled["jira:PAY-1"].text


@pytest.mark.parametrize("mutate", [
    lambda f: f.pop("security", None),                        # requested but absent
    lambda f: f.__setitem__("security", {"name": "no id"}),
    lambda f: f.__setitem__("security", "10100"),
    lambda f: f.__setitem__("security", {"id": None}),
    lambda f: f.__setitem__("security", {"id": True}),
    lambda f: f.__setitem__("security", []),
])
def test_absent_or_malformed_security_field_is_nobody(env, mutate):        # sec3b #4
    d, fake, conn, _, _ = env
    assert fake.issues["PAY-2"]["security_level"] is not None
    _rewrite_issue(conn, "PAY-2", mutate)
    e = conn.acl("jira:PAY-2")
    assert isinstance(e, AclNobody) and e.reason == "jira-security-field-unreadable"
    assert not any(A.allows(e, u) for u in d.all())


def test_explicit_null_security_is_browse_only(env):
    d, fake, conn, _, _ = env
    assert fake.issues["PAY-1"]["security_level"] is None
    assert readers(conn, d, "jira:PAY-1") == expected(fake, d, "PAY-1") != set()


def test_crawl_with_absent_security_field_fingerprints_and_fails_closed(env):
    import httpx
    d, fake, conn, _, _ = env

    def strip(request, resp):
        if request.url.path.endswith("/search/jql") and resp.status_code == 200:
            data = resp.json()
            for i in data["issues"]:
                if i["key"] == "PAY-2":
                    i["fields"].pop("security", None)
            return httpx.Response(200, json=data)
        return resp

    _wrap(conn, strip)
    b = conn.list_changes(None)
    assert json.loads(b.cursor)["issues"]["PAY-2"]["l"] != fake.issues["PAY-2"]["security_level"]


def test_search_pagination_cap_raises(env, monkeypatch):                   # sec3b #6 pattern
    from databridge_ib.connectors import _http
    _, _, conn, _, _ = env
    monkeypatch.setattr(_http, "MAX_PAGES", 1)                # page_size 2, five issues
    with pytest.raises(ConnectorError):
        conn.list_changes(None)


def test_search_token_cycle_raises(env):
    import httpx
    _, _, conn, _, _ = env

    def loop(request, resp):
        if request.url.path.endswith("/search/jql"):
            data = resp.json()
            data["nextPageToken"], data["isLast"] = "2", False       # the same token forever
            return httpx.Response(200, json=data)
        return resp

    _wrap(conn, loop)
    with pytest.raises(ConnectorError):
        conn.list_changes(None)


def test_event_user_native_first_and_unresolved_stays_none(env):           # sec3b #13
    d, _, conn, _, _ = env
    assert conn._ib_user(d.get("jdoe").platform_ids["jira"]) == "jdoe"
    assert conn._ib_user("acc-nobody") is None
    assert conn._ib_user("557058:0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b") is None
    assert conn._ib_user("jdoe") == "jdoe"
    assert conn._ib_user("mallory") is None

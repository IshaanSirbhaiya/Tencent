"""Drive connector (B1): permissions incl. inherited entries (widening), limited-access folders,
shared-drive members, company-domain grants only, anyone-with-link ⇒ nobody, expiring ⇒ nobody,
text-like files only, Gate 2 re-read (fail closed, no retry), changes-feed crawl with folder ACL
expansion, webhooks, and Gate 1 == Gate 2 against apps/mock_platforms."""
from __future__ import annotations

import json
import time

import pytest

from databridge_ib import acl as A
from databridge_ib.connectors.base import ConnectorError, SourceConnector, WebhookRejected
from databridge_ib.connectors.drive import DriveConnector, file_id_of
from databridge_ib.models import AclNobody

from fixtures_connectors import SECRET, Directory, client_for, signed_headers
from fixtures_connectors.jira_drive import COMPANY, GDOC, GSHEET, FakeDrive, jd_people

TEXTLIKE = {"text/plain", "text/markdown", "text/csv", GDOC, GSHEET}


@pytest.fixture()
def env():
    d = Directory(jd_people())
    fake = FakeDrive(d)
    sleeps: list[float] = []
    conn = DriveConnector(client_for(fake, "svc-test"), d, SECRET, sleep=sleeps.append, page_size=2)
    return d, fake, conn, sleeps


def readers(conn, d, doc_id):
    e = conn.acl(doc_id)
    return {u.id for u in d.all() if A.allows(e, u)}


def files(fake, *, indexable=True):
    return sorted(i for i, it in fake.items.items()
                  if it["mime"] != "application/vnd.google-apps.folder"
                  and (not indexable or it["mime"] in TEXTLIKE) and not it["trashed"])


def test_protocol(env):
    assert isinstance(env[2], SourceConnector)


def test_file_id_of():
    assert file_id_of("drive:df-1") == "df-1"
    assert file_id_of("drive:../x") is None and file_id_of("jira:PAY-1") is None


# ---------------------------------------------------------------- documents
def test_fetch_text_file(env):
    _, fake, conn, _ = env
    doc = conn.fetch("drive:df-1")
    it = fake.items["df-1"]
    assert doc.kind == "file" and doc.platform == "drive" and doc.doc_id == "drive:df-1"
    assert doc.container == "0AEng" and doc.container_name == "Engineering"
    assert doc.title == it["name"] and doc.text == it["content"] and doc.version == str(it["version"])
    assert doc.deep_link == "https://docs.google.com/document/d/df-1/edit"
    assert doc.author == "jdoe"
    assert set(doc.links) == {"jira:PAY-1", "drive:df-2"}


def test_google_doc_and_sheet_are_exported(env):
    _, fake, conn, _ = env
    assert conn.fetch("drive:df-2").text == fake.items["df-2"]["content"]
    assert conn.fetch("drive:df-3").text == fake.items["df-3"]["content"]
    assert "export" in fake.calls and "media" not in fake.calls   # alt=media would 403 on Google docs


def test_markdown_and_csv(env):
    _, fake, conn, _ = env
    assert conn.fetch("drive:df-4").text == fake.items["df-4"]["content"]
    assert conn.fetch("drive:df-7").text == fake.items["df-7"]["content"]


def test_unsupported_mime_folder_missing_trashed_skip(env):
    _, fake, conn, _ = env
    assert conn.fetch("drive:df-6") is None                     # image/png: never indexed
    assert conn.fetch("drive:fd-1") is None                     # folders are not documents
    assert conn.fetch("drive:nope") is None
    fake.trash("df-1")
    assert conn.fetch("drive:df-1") is None


def test_export_fallback_to_media(env):
    """A source without the export route (404) still yields text through alt=media."""
    d, fake, _, _ = env
    import httpx

    class NoExport(FakeDrive):
        def __call__(self, request):
            if request.url.path.endswith("/export"):
                return httpx.Response(404, json={"detail": "Not Found"})
            if request.url.params.get("alt") == "media":
                it = self.items[request.url.path.split("/")[4]]
                return httpx.Response(200, text=it["content"])
            return super().__call__(request)

    ne = NoExport(d)
    c = DriveConnector(client_for(ne, "svc-test"), d, SECRET)
    assert c.fetch("drive:df-2").text == ne.items["df-2"]["content"]


def test_drive_names_fallback(env):
    d, fake, _, _ = env
    import httpx

    class NoDrives(FakeDrive):
        def __call__(self, request):
            if "/drives/" in request.url.path:
                return httpx.Response(404, json={"detail": "Not Found"})
            return super().__call__(request)

    nd = NoDrives(d)
    assert DriveConnector(client_for(nd, "svc-test"), d, SECRET).fetch("drive:df-1").container_name == "0AEng"
    named = DriveConnector(client_for(nd, "svc-test"), d, SECRET, drive_names={"0AEng": "Eng"})
    assert named.fetch("drive:df-1").container_name == "Eng"


# ---------------------------------------------------------------- ACL compile (Gate 1)
@pytest.mark.parametrize("fid", ["df-1", "df-2", "df-3", "df-4", "df-5", "df-7", "df-8"])
def test_acl_matches_ib_policy(env, fid):
    d, fake, conn, _ = env
    assert readers(conn, d, f"drive:{fid}") == {u.id for u in d.all() if fake.ib_can_read(u.id, fid)}


def test_acl_non_vacuous(env):
    d, fake, conn, _ = env
    sizes = {f: len(readers(conn, d, f"drive:{f}")) for f in files(fake)}
    assert any(s > 0 for s in sizes.values()) and all(s < len(d.all()) for s in sizes.values())


def test_shared_drive_members_inherit(env):
    d, _, conn, _ = env
    assert readers(conn, d, "drive:df-1") == {u.id for u in d.all() if "grp-eng" in u.groups}


def test_limited_access_folder_stops_inheritance(env):
    """df-2 sits in limited folder fd-2: grp-security (folder) + priya (file) — NOT grp-eng from the
    shared drive, so jdoe (grp-eng) is out even though fd-1 / the drive would grant him."""
    d, fake, conn, _ = env
    r = readers(conn, d, "drive:df-2")
    assert r == {"sara.sec", "priya.sre"} and "jdoe" not in r
    fake.items["fd-2"]["limited"] = False
    assert "jdoe" in readers(conn, d, "drive:df-2")             # the stop is what excluded him


def test_inheritance_widens(env):
    d, _, conn, _ = env
    assert readers(conn, d, "drive:df-4") >= readers(conn, d, "drive:df-3") | {"leo.ops"}


def test_domain_grant_only_company_domain_users(env):
    d, _, conn, _ = env
    r = readers(conn, d, "drive:df-3")
    assert r == {u.id for u in d.all() if u.domain == COMPANY}
    assert "ken.ext" not in r                                   # partner.example
    assert "ken.ext" not in readers(conn, d, "drive:df-8")      # a FOREIGN domain grant grants nothing
    assert readers(conn, d, "drive:df-8") == {"mia.pm"}         # …the folder's group still does


def test_anyone_with_link_is_nobody(env):
    d, fake, conn, _ = env
    e = conn.acl("drive:df-5")
    assert isinstance(e, AclNobody) and e.reason == "drive-anyone-with-link"
    assert fake.ib_can_read("jdoe", "df-5") is False
    fake.items["fd-1"]["perms"].append(FakeDrive._perm(("pa", "anyone", None)))
    assert isinstance(conn.acl("drive:df-1"), AclNobody)        # inherited anyone-link also denies


def test_expiring_and_unknown_types_are_nobody(env):
    d, fake, conn, _ = env
    assert isinstance(conn.acl("drive:df-7"), AclNobody)        # mia's grant expires
    from databridge_ib.connectors.drive import DriveConnector as DC
    c = DC(conn.client, d, SECRET)
    assert isinstance(c.compile_acl([{"type": "user", "role": "reader", "emailAddress": f"jdoe@{COMPANY}"},
                                     {"type": "audience", "role": "reader"}]), AclNobody)
    assert isinstance(c.compile_acl([{"type": "user", "role": "secretRole",
                                      "emailAddress": f"jdoe@{COMPANY}"}]), AclNobody)
    assert isinstance(c.compile_acl([]), AclNobody)
    ok = c.compile_acl([{"type": "user", "role": "commenter", "emailAddress": f"JDOE@{COMPANY}".lower()},
                        {"type": "user", "role": "reader", "emailAddress": "stranger@x.example"},
                        {"type": "user", "role": "reader", "emailAddress": f"sara.sec@{COMPANY}",
                         "deleted": True}])
    assert {u.id for u in d.all() if A.allows(ok, u)} == {"jdoe"}


def test_group_email_mapping(env):
    d, _, conn, _ = env
    e = conn.compile_acl([{"type": "group", "role": "reader", "emailAddress": "grp-sre@evil.example"}])
    assert not any(A.allows(e, u) for u in d.all())             # foreign group email never maps
    c = DriveConnector(conn.client, d, SECRET, group_resolver={"sre@lists.example": "grp-sre"}.get)
    e = c.compile_acl([{"type": "group", "role": "reader", "emailAddress": "sre@lists.example"}])
    assert {u.id for u in d.all() if A.allows(e, u)} == {"priya.sre"}


def test_acl_gone_or_folder(env):
    _, fake, conn, _ = env
    assert isinstance(conn.acl("drive:nope"), AclNobody)
    assert isinstance(conn.acl("drive:fd-1"), AclNobody)
    fake.trash("df-1")
    assert isinstance(conn.acl("drive:df-1"), AclNobody)
    fake.error.add("permissions")
    with pytest.raises(ConnectorError):
        conn.acl("drive:df-4")


# ---------------------------------------------------------------- Gate 2
def test_gate2_matches_policy(env):
    d, fake, conn, _ = env
    allowed = 0
    for fid in files(fake):
        for u in d.all():
            r = conn.check_access(u, f"drive:{fid}")
            assert r.allowed == fake.ib_can_read(u.id, fid), (fid, u.id, r.reason_code)
            allowed += r.allowed
            if r.allowed:
                assert r.current_version == str(fake.items[fid]["version"])
    assert allowed > 0
    r = conn.check_access(d.get("jdoe"), "drive:df-5")
    assert r.verdict == "forbidden" and r.reason_code == "unsupported_acl"
    r = conn.check_access(d.get("leo.ops"), "drive:df-1")
    assert r.verdict == "forbidden" and r.reason_code == "not_permitted"


def test_gate2_reads_source_now(env):
    d, fake, conn, _ = env
    assert conn.check_access(d.get("priya.sre"), "drive:df-2").allowed
    fake.remove_perm("df-2", "p-2")
    assert not conn.check_access(d.get("priya.sre"), "drive:df-2").allowed


def test_gate2_vanished(env):
    d, fake, conn, _ = env
    assert conn.check_access(d.get("jdoe"), "drive:nope").verdict == "vanished"
    fake.trash("df-1")
    assert conn.check_access(d.get("jdoe"), "drive:df-1").verdict == "vanished"
    assert conn.check_access(d.get("jdoe"), "drive:fd-1").verdict == "forbidden"


@pytest.mark.parametrize("fault,endpoint,reason", [
    ("rate_limited", "get", "source_rate_limited"),
    ("timeout", "get", "source_timeout"),
    ("error", "get", "source_error"),
    ("rate_limited", "permissions", "source_rate_limited"),
    ("timeout", "permissions", "source_timeout"),
])
def test_gate2_failures_deny_without_retry(env, fault, endpoint, reason):
    d, fake, conn, sleeps = env
    getattr(fake, fault).add(endpoint)
    r = conn.check_access(d.get("jdoe"), "drive:df-1")
    assert r.verdict == "forbidden" and r.reason_code == reason
    assert fake.calls.count(endpoint) == 1 and sleeps == []


def test_gate2_drift(env):
    d, fake, conn, _ = env
    old = conn.fetch("drive:df-1").version
    fake.edit("df-1", "v2 text")
    r = conn.check_access(d.get("jdoe"), "drive:df-1", known_version=old)
    assert r.verdict == "drift_material" and r.current_version == str(fake.items["df-1"]["version"])
    assert conn.check_access(d.get("jdoe"), "drive:df-1",
                             known_version=r.current_version).verdict == "verified"


def test_gate2_inactive_or_unmapped(env):
    d, _, conn, _ = env
    u = d.get("jdoe")
    assert conn.check_access(u.model_copy(update={"active": False}), "drive:df-1").verdict == "forbidden"
    r = conn.check_access(u.model_copy(update={"platform_ids": {}}), "drive:df-1")
    assert r.verdict == "forbidden" and r.reason_code == "no_platform_identity"
    assert conn.check_access(u, "drive:..").reason_code == "bad_doc_id"


# ---------------------------------------------------------------- crawl
def test_crawl_full_then_quiet(env):
    _, fake, conn, _ = env
    b1 = conn.list_changes(None)
    assert {x.doc_id for x in b1.docs} == {f"drive:{f}" for f in files(fake)}
    assert "drive:df-6" not in {x.doc_id for x in b1.docs}      # image skipped
    b2 = conn.list_changes(b1.cursor)
    assert b2.docs == () and b2.deleted == () and b2.acl_changed == ()


def test_crawl_edit_new_and_trash(env):
    _, fake, conn, _ = env
    b1 = conn.list_changes(None)
    fake.edit("df-4", "new runbook")
    fake.file("df-9", "fd-1", "Fresh", "text/plain", "fresh", record=True)
    fake.file("df-10", "fd-1", "Photo", "image/jpeg", "jpg", record=True)
    fake.trash("df-3")
    b2 = conn.list_changes(b1.cursor)
    assert {x.doc_id for x in b2.docs} == {"drive:df-4", "drive:df-9"}
    assert b2.deleted == ("drive:df-3",) and b2.acl_changed == ()
    cur = json.loads(b2.cursor)
    assert cur["files"]["df-4"]["v"] == str(fake.items["df-4"]["version"])
    assert "df-3" not in cur["files"] and "df-10" not in cur["files"]


def test_crawl_file_permission_change(env):
    _, fake, conn, _ = env
    b1 = conn.list_changes(None)
    fake.remove_perm("df-2", "p-2")
    b2 = conn.list_changes(b1.cursor)
    assert b2.acl_changed == ("drive:df-2",) and b2.docs == ()


def test_crawl_folder_acl_change_marks_every_descendant(env):
    """The feed reports only the folder (fake, like real Drive); the connector expands it."""
    _, fake, conn, _ = env
    b1 = conn.list_changes(None)
    fake.remove_perm("fd-3", "pf-3")
    assert [c["fileId"] for c in fake.changes] == ["fd-3"]
    b2 = conn.list_changes(b1.cursor)
    under = {f"drive:{f}" for f in ("df-3", "df-4")}           # fd-3 and its subfolder fd-4
    assert set(b2.acl_changed) == under and b2.docs == ()


def test_crawl_folder_move_marks_descendants(env):
    _, fake, conn, _ = env
    b1 = conn.list_changes(None)
    fake.items["fd-4"]["parent"] = "fd-5"
    fake.record("fd-4")
    b2 = conn.list_changes(b1.cursor)
    assert b2.acl_changed == ("drive:df-4",)
    assert json.loads(b2.cursor)["tree"]["fd-4"]["p"] == "fd-5"


def test_crawl_trashed_folder_removes_children(env):
    _, fake, conn, _ = env
    b1 = conn.list_changes(None)
    fake.items["fd-4"]["trashed"] = True
    fake.items["df-4"]["trashed"] = True
    fake.record("fd-4")                                           # only the folder is reported
    b2 = conn.list_changes(b1.cursor)
    assert b2.deleted == ("drive:df-4",)
    assert "fd-4" not in json.loads(b2.cursor)["tree"]


def test_crawl_drive_change_marks_drive(env):
    _, fake, conn, _ = env
    b1 = conn.list_changes(None)
    fake.changes.append({"fileId": None, "removed": False})       # malformed: ignored
    orig = fake.__call__
    import httpx

    def with_drive_change(request):
        resp = orig(request)
        if request.url.path.endswith("/changes") and "startPageToken" not in request.url.path:
            body = resp.json()
            body["changes"].append({"changeType": "drive", "driveId": "0AEng"})
            return httpx.Response(200, json=body)
        return resp

    conn.client = client_for(with_drive_change, "svc-test")
    b2 = conn.list_changes(b1.cursor)
    assert set(b2.acl_changed) == {f"drive:{f}" for f in files(fake) if fake.items[f]["drive"] == "0AEng"}


def test_crawl_retries_429_and_bad_cursor(env):
    _, fake, conn, sleeps = env
    fake.rate_limit_once.add("list")
    assert conn.list_changes(None).docs and sleeps == [2.0]
    with pytest.raises(ConnectorError):
        conn.list_changes('{"v": 1}')
    with pytest.raises(ConnectorError):
        conn.list_changes("garbage")


# ---------------------------------------------------------------- webhooks
def _body(**kw):
    base = {"platform": "drive", "type": "content.changed", "object_ids": ["df-1"],
            "container": None, "user": None, "at": "2026-10-13T01:00:00Z"}
    base.update(kw)
    return json.dumps(base).encode()


def test_webhook_content_ids(env):
    _, _, conn, _ = env
    body = _body(object_ids=["df-1", "drive:df-2"])
    [ev] = conn.parse_webhook(signed_headers(body), body)
    assert ev.object_ids == ("drive:df-1", "drive:df-2") and ev.type == "content.changed"


def test_webhook_acl_folder_expands_to_descendant_files(env):
    _, _, conn, _ = env
    body = _body(type="acl.changed", object_ids=["fd-3", "df-1"], container="0ACo")
    [ev] = conn.parse_webhook(signed_headers(body), body)
    assert set(ev.object_ids) == {"drive:df-3", "drive:df-4", "drive:df-1"}
    assert "drive:fd-3" not in ev.object_ids and "drive:fd-4" not in ev.object_ids
    assert ev.container == "0ACo"


def test_webhook_expansion_failure_keeps_raw_id(env):
    _, fake, conn, _ = env
    fake.error.add("get")
    body = _body(type="acl.changed", object_ids=["fd-3"], container="0ACo")
    [ev] = conn.parse_webhook(signed_headers(body), body)
    assert ev.object_ids == ("drive:fd-3",) and ev.container == "0ACo"   # never drop an invalidation


def test_webhook_user_event(env):
    _, _, conn, _ = env
    body = _body(type="user.changed", object_ids=[f"jdoe@{COMPANY}"], user="jdoe")
    [ev] = conn.parse_webhook(signed_headers(body), body)
    assert ev.user == "jdoe" and ev.object_ids == (f"jdoe@{COMPANY}",)
    body = _body(type="user.changed", object_ids=[], user=f"priya.sre@{COMPANY}")
    assert conn.parse_webhook(signed_headers(body), body)[0].user == "priya.sre"


def test_webhook_rejections(env):
    _, _, conn, _ = env
    body = _body()
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(signed_headers(body, secret="nope"), body)
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(signed_headers(body, ts=int(time.time()) + 400), body)
    h = signed_headers(body)
    conn.parse_webhook(h, body)
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(h, body)
    other = _body(platform="jira")
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(signed_headers(other), other)
    bad_ids = _body(object_ids=[1, 2])
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(signed_headers(bad_ids), bad_ids)
    with pytest.raises(WebhookRejected):
        conn.parse_webhook(signed_headers(b"[]"), b"[]")


# ---------------------------------------------------------------- integration: apps/mock_platforms
_TC_TIMEOUT = "ignore:You should not use the 'timeout' argument"


@pytest.fixture()
def mock_env(tmp_path):
    mod = pytest.importorskip("mock_platforms.app", reason="mock_platforms not available")
    from fixtures_connectors.jira_drive import JiraDriveMockEnv
    env = JiraDriveMockEnv(tmp_path, mod.create_app)
    conn = DriveConnector(env.svc, env.directory, SECRET, admin_client=env.admin,
                          sleep=lambda s: None, page_size=2,
                          drive_names={x["id"]: x["name"] for x in env.world["drive"]["drives"]})
    return env, conn


def _world_files(env):
    return {f"drive:{f['id']}" for f in env.world["drive"]["files"] if f["mimeType"] in TEXTLIKE}


@pytest.mark.filterwarnings(_TC_TIMEOUT)
def test_mock_gate1_equals_gate2_for_every_pair(mock_env):
    env, conn = mock_env
    d = env.directory
    batch = conn.list_changes(None)
    assert {x.doc_id for x in batch.docs} == _world_files(env)
    allowed = 0
    for doc in batch.docs:
        expr = conn.acl(doc.doc_id)
        for u in d.all():
            g2 = conn.check_access(u, doc.doc_id)
            assert A.allows(expr, u) == g2.allowed, (doc.doc_id, u.id, g2.reason_code)
            allowed += g2.allowed
    assert 0 < allowed < len(batch.docs) * len(d.all())

    def r(fid):
        return {u.id for u in d.all() if A.allows(conn.acl(f"drive:{fid}"), u)}

    eng = {u.id for u in d.all() if "grp-eng" in u.groups}
    assert r("df-1") == eng                                      # shared-drive members
    assert r("df-2") == {"sara.sec", "priya.sre"}                # limited folder: no drive members
    assert r("df-5") == set()                                    # anyone-with-link
    assert r("df-3") == {u.id for u in d.all() if u.domain == COMPANY}
    assert r("df-4") == r("df-3")                                # inherits domain (+ leo, already in)
    assert r("df-6") == {"mia.pm"}
    doc = next(x for x in batch.docs if x.doc_id == "drive:df-2")
    assert doc.text == "Evidence CNRY-2222" and doc.container_name == "Engineering"


@pytest.mark.filterwarnings(_TC_TIMEOUT)
def test_mock_folder_permission_removal_webhook_and_recrawl(mock_env):
    env, conn = mock_env
    d = env.directory
    b1 = conn.list_changes(None)
    assert conn.check_access(d.get("sara.sec"), "drive:df-2").allowed
    env.admin_post("/admin/drive/files/fd-2/permissions/remove", {"permission_id": "pf-2"})
    _, headers, body = env.hooks[-1]
    [ev] = conn.parse_webhook(headers, body)
    assert ev.type == "acl.changed" and set(ev.object_ids) == {"drive:df-2"}
    b2 = conn.list_changes(b1.cursor)
    assert b2.acl_changed == ("drive:df-2",) and b2.docs == () and b2.deleted == ()
    assert not conn.check_access(d.get("sara.sec"), "drive:df-2").allowed
    assert not A.allows(conn.acl("drive:df-2"), d.get("sara.sec"))
    assert conn.check_access(d.get("priya.sre"), "drive:df-2").allowed


@pytest.mark.filterwarnings(_TC_TIMEOUT)
def test_mock_edit_drift_and_429(mock_env):
    env, conn = mock_env
    d = env.directory
    b1 = conn.list_changes(None)
    v0 = conn.fetch("drive:df-1").version
    env.admin_post("/admin/drive/files/df-1/edit", {"content": "Root cause v2"})
    _, headers, body = env.hooks[-1]
    assert conn.parse_webhook(headers, body)[0].object_ids == ("drive:df-1",)
    r = conn.check_access(d.get("jdoe"), "drive:df-1", known_version=v0)
    assert r.verdict == "drift_material" and r.current_version == str(int(v0) + 1)
    b2 = conn.list_changes(b1.cursor)
    assert [x.doc_id for x in b2.docs] == ["drive:df-1"] and b2.docs[0].text == "Root cause v2"
    env.admin_post("/admin/ratelimit", {"platform": "drive", "on": True})
    r = conn.check_access(d.get("jdoe"), "drive:df-1")
    assert r.verdict == "forbidden" and r.reason_code == "source_rate_limited"

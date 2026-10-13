"""Auth (401/403) and the per-platform rate-limit toggle (429 + Retry-After: 2)."""
from __future__ import annotations

import pytest

READS = {
    "slack": "/slack/api/conversations.list",
    "confluence": "/confluence/wiki/api/v2/spaces",
    "jira": "/jira/rest/api/3/search/jql?jql=project%20%3D%20PAY",
    "drive": "/drive/v3/files",
}
CF_CHECK = ("/confluence/wiki/rest/api/content/cf-1001/permission/check",
            {"subject": {"type": "user", "identifier": "acc-jdoe"}, "operation": "read"})
JIRA_CHECK = ("/jira/rest/api/3/permissions/check",
              {"accountId": "acc-jdoe",
               "projectPermissions": [{"issues": [20001], "permissions": ["BROWSE_PROJECTS"]}]})


@pytest.mark.parametrize("platform", list(READS))
def test_missing_or_wrong_token_is_401(client, platform):
    url = READS[platform]
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get(url, headers={"Authorization": "Basic abc"}).status_code == 401


@pytest.mark.parametrize("platform", list(READS))
def test_svc_and_admin_tokens_read(client, svc, admin, platform):
    assert client.get(READS[platform], headers=svc).status_code == 200
    assert client.get(READS[platform], headers=admin).status_code == 200


def test_slack_401_body_is_slack_shaped(client):
    r = client.get(READS["slack"])
    assert r.json() == {"ok": False, "error": "not_authed"}


@pytest.mark.parametrize("url,body", [CF_CHECK, JIRA_CHECK])
def test_permission_checks_need_admin(client, svc, admin, url, body):
    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json=body, headers=svc).status_code == 403
    assert client.post(url, json=body, headers=admin).status_code == 200


def test_admin_endpoints_need_admin_token(client, svc, admin):
    url = "/admin/ratelimit"
    body = {"platform": "slack", "on": False}
    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json=body, headers=svc).status_code == 403
    assert client.post(url, json=body, headers=admin).status_code == 200


def test_health_is_open(client):
    assert client.get("/health").json()["ok"] is True


@pytest.mark.parametrize("platform", list(READS))
def test_ratelimit_toggle(client, svc, admin, platform):
    assert client.post("/admin/ratelimit", json={"platform": platform, "on": True},
                       headers=admin).status_code == 200
    r = client.get(READS[platform], headers=svc)
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "2"
    # admin token is rate-limited too on platform routes; /admin/* is not
    assert client.get(READS[platform], headers=admin).status_code == 429
    for other, url in READS.items():
        if other != platform:
            assert client.get(url, headers=svc).status_code == 200
    assert client.post("/admin/ratelimit", json={"platform": platform, "on": False},
                       headers=admin).status_code == 200
    assert client.get(READS[platform], headers=svc).status_code == 200


def test_ratelimit_covers_permission_checks(client, admin):
    client.post("/admin/ratelimit", json={"platform": "confluence", "on": True}, headers=admin)
    r = client.post(CF_CHECK[0], json=CF_CHECK[1], headers=admin)
    assert r.status_code == 429 and r.headers["Retry-After"] == "2"
    client.post("/admin/ratelimit", json={"platform": "jira", "on": True}, headers=admin)
    r = client.post(JIRA_CHECK[0], json=JIRA_CHECK[1], headers=admin)
    assert r.status_code == 429 and r.headers["Retry-After"] == "2"


def test_ratelimit_rejects_unknown_platform(client, admin):
    assert client.post("/admin/ratelimit", json={"platform": "teams", "on": True},
                       headers=admin).status_code == 400

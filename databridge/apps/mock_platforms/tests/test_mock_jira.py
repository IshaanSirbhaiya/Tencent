"""Jira subset: enhanced-search pagination, JQL subset, shapes, and permissions/check
(Browse via roles/users/groups/reporter/assignee AND security-level membership)."""
from __future__ import annotations

from datetime import datetime

import pytest

J = "/jira/rest/api/3"


def _search_all(client, headers, jql, max_results):
    out, token, pages = [], None, 0
    while True:
        params = {"jql": jql, "maxResults": max_results, "fields": "*all"}
        if token:
            params["nextPageToken"] = token
        body = client.get(f"{J}/search/jql", params=params, headers=headers).json()
        out.extend(body["issues"])
        pages += 1
        if body["isLast"]:
            assert "nextPageToken" not in body
            return out, pages
        token = body["nextPageToken"]


def _adf_text(doc):
    return "\n\n".join(n["text"] for p in doc["content"] for n in p["content"])


def test_search_pagination_round_trip(client, svc, mini):
    issues, pages = _search_all(client, svc, "project = PAY", 1)
    want = [i["key"] for i in mini["jira"]["issues"] if i["project"] == "PAY"]
    assert sorted(i["key"] for i in issues) == sorted(want)
    assert pages == len(want)


def test_search_post_matches_get(client, svc):
    r = client.post(f"{J}/search/jql", json={"jql": "project = PAY", "maxResults": 10,
                                             "fields": ["summary"]}, headers=svc).json()
    assert {i["key"] for i in r["issues"]} == {"PAY-1", "PAY-2", "PAY-3"}
    assert set(r["issues"][0]["fields"]) == {"summary"}


def test_jql_updated_filter_and_order(client, svc, mini):
    cutoff = "2026-10-05T00:00:00+08:00"
    issues, _ = _search_all(client, svc, f'updated >= "{cutoff}" ORDER BY updated DESC', 50)
    want = [i for i in mini["jira"]["issues"]
            if datetime.fromisoformat(i["updated"]) >= datetime.fromisoformat(cutoff)]
    want.sort(key=lambda i: i["updated"], reverse=True)
    assert [i["key"] for i in issues] == [i["key"] for i in want]
    # Jira's "yyyy-MM-dd HH:mm" form is accepted too
    issues2, _ = _search_all(client, svc, 'project = PAY AND updated >= "2026-10-05 00:00" '
                                          'ORDER BY updated', 50)
    assert [i["key"] for i in issues2] == sorted(
        [i["key"] for i in want if i["project"] == "PAY"],
        key=lambda k: next(x["updated"] for x in mini["jira"]["issues"] if x["key"] == k))


def test_jql_project_in(client, svc):
    issues, _ = _search_all(client, svc, "project in (PAY, SEC)", 50)
    assert len(issues) == 4


def test_jql_unsupported_is_400(client, svc):
    r = client.get(f"{J}/search/jql", params={"jql": "assignee = currentUser()"}, headers=svc)
    assert r.status_code == 400 and r.json()["errorMessages"]


def test_issue_shape(client, svc, mini):
    src = next(i for i in mini["jira"]["issues"] if i["key"] == "PAY-1")
    by_key = client.get(f"{J}/issue/PAY-1", headers=svc).json()
    by_id = client.get(f"{J}/issue/{src['id']}", headers=svc).json()
    assert by_key == by_id
    f = by_key["fields"]
    assert by_key["id"] == src["id"] and by_key["key"] == "PAY-1"
    assert f["summary"] == src["summary"]
    assert _adf_text(f["description"]) == src["description"]
    assert f["reporter"]["accountId"] == "acc-jdoe"
    assert f["assignee"]["accountId"] == "acc-priya"
    assert f["security"] is None
    assert f["status"]["name"] == src["status"]
    assert f["updated"] == src["updated"]
    assert [_adf_text(c["body"]) for c in f["comment"]["comments"]] == \
        [c["body"] for c in src["comments"]]
    assert [l["outwardIssue"]["key"] for l in f["issuelinks"]] == src["links"]
    sec = client.get(f"{J}/issue/PAY-2", headers=svc).json()["fields"]["security"]
    assert sec["id"] == "10100" and sec["name"] == "Security only"
    assert client.get(f"{J}/issue/PAY-999", headers=svc).status_code == 404


def test_project_role_actors(client, svc):
    roles = client.get(f"{J}/project/PAY/role", headers=svc).json()
    assert set(roles) == {"Developers", "Viewers"}
    dev = client.get(f"{J}/project/PAY/role/Developers", headers=svc).json()
    assert [(a["type"], a["actorGroup"]["name"], a["actorGroup"]["groupId"])
            for a in dev["actors"]] == [("atlassian-group-role-actor", "payments-eng",
                                         "grp-payments-eng")]
    viewers = client.get(f"{J}/project/PAY/role/{roles['Viewers'].rsplit('/', 1)[-1]}",
                         headers=svc).json()
    assert [a["actorUser"]["accountId"] for a in viewers["actors"]] == ["acc-leo"]
    assert client.get(f"{J}/project/PAY/role/Nope", headers=svc).status_code == 404


def test_security_level_members(client, svc):
    r = client.get(f"{J}/issuesecurityschemes/level/member", params={"levelId": "10100"},
                   headers=svc).json()
    holders = [v["holder"] for v in r["values"]]
    assert {"type": "group", "parameter": "security", "value": "grp-security"} in holders
    assert {"type": "reporter"} in holders
    assert {"type": "assignee"} not in holders
    assert all(v["issueSecurityLevelId"] == "10100" for v in r["values"])


def test_permission_scheme_browse_holders(client, svc):
    r = client.get(f"{J}/project/PAY/permissionscheme", params={"expand": "permissions"},
                   headers=svc).json()
    types = sorted(p["holder"]["type"] for p in r["permissions"])
    assert types == ["assignee", "projectRole", "projectRole", "reporter"]
    assert all(p["permission"] == "BROWSE_PROJECTS" for p in r["permissions"])


def _allowed(client, admin, account, issue_ids):
    r = client.post(f"{J}/permissions/check", headers=admin, json={
        "accountId": account,
        "projectPermissions": [{"issues": issue_ids, "permissions": ["BROWSE_PROJECTS"]}]})
    assert r.status_code == 200, r.text
    pp = r.json()["projectPermissions"]
    assert pp[0]["permission"] == "BROWSE_PROJECTS"
    return set(pp[0]["issues"])


ALL = [20001, 20002, 20003, 20004]


@pytest.mark.parametrize("account,expected,why", [
    ("acc-jdoe", {20001, 20002, 20003},
     "Developers via group; PAY-2 security level via REPORTER; no SEC browse"),
    ("acc-priya", {20001}, "assignee holder grants Browse on PAY-1 only; not in PAY-2 level"),
    ("acc-leo", {20001, 20003}, "Viewers role; PAY-2 level excludes assignee"),
    ("acc-sara", {20004}, "level group member but no PAY Browse; SEC role"),
    ("acc-old", set(), "deactivated"),
    ("acc-nobody", set(), "unknown account"),
])
def test_permissions_check_matrix(client, admin, account, expected, why):
    assert _allowed(client, admin, account, ALL) == expected, why


def test_permissions_check_projects_and_string_ids(client, admin):
    r = client.post(f"{J}/permissions/check", headers=admin, json={
        "accountId": "acc-leo",
        "projectPermissions": [{"issues": ["20003"], "projects": [10001, 10002],
                                "permissions": ["BROWSE_PROJECTS"]}]}).json()
    entry = r["projectPermissions"][0]
    assert entry["issues"] == ["20003"]
    assert entry["projects"] == [10001]


def test_unsupported_permission_is_denied(client, admin):
    r = client.post(f"{J}/permissions/check", headers=admin, json={
        "accountId": "acc-jdoe",
        "projectPermissions": [{"issues": ALL, "permissions": ["EDIT_ISSUES"]}]}).json()
    assert r["projectPermissions"][0]["issues"] == []

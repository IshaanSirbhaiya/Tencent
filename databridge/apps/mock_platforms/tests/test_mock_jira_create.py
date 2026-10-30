"""Jira "Create issues": the CREATE_ISSUES permission check and ``POST /rest/api/3/issue``
(the Internal Brain's follow-up ticket action, AGENTIC_ROADMAP.md §1).

Mini world: PAY's Developers role is grp-payments-eng (jdoe, old.timer deactivated); leo.ops is a PAY
Viewer (browse, not create); SEC has no Developers role, so nobody may create there by default."""
from __future__ import annotations

import json

import pytest

J = "/jira/rest/api/3"


def _create_ok(client, admin, account, projects=(10001, 10002)):
    r = client.post(f"{J}/permissions/check", headers=admin, json={
        "accountId": account,
        "projectPermissions": [{"projects": list(projects), "permissions": ["CREATE_ISSUES"]}]})
    assert r.status_code == 200, r.text
    entry = r.json()["projectPermissions"][0]
    assert entry["permission"] == "CREATE_ISSUES"
    return entry["projects"]


@pytest.mark.parametrize("account,expected", [
    ("acc-jdoe", [10001]),          # PAY Developer (via group); SEC has no Developers role
    ("acc-leo", []),                # PAY Viewer: browse only
    ("acc-old", []),                # deactivated
    ("acc-nobody", []),             # unknown account
])
def test_create_permission_check(client, admin, account, expected):
    assert _create_ok(client, admin, account) == expected


def test_create_permission_check_needs_the_admin_token(client, svc):
    r = client.post(f"{J}/permissions/check", headers=svc, json={
        "accountId": "acc-jdoe",
        "projectPermissions": [{"projects": [10001], "permissions": ["CREATE_ISSUES"]}]})
    assert r.status_code == 403


def _fields(**over):
    f = {"project": {"key": "PAY"}, "summary": "Follow-up: replication lag",
         "issuetype": {"name": "Task"}, "reporter": {"accountId": "acc-jdoe"},
         "labels": ["internal-brain"],
         "description": {"type": "doc", "version": 1, "content": [
             {"type": "paragraph", "content": [{"type": "text", "text": "First paragraph."}]},
             {"type": "paragraph", "content": [{"type": "text", "text": "Sources: https://x/1"}]}]}}
    f.update(over)
    return {"fields": f}


def test_create_issue_round_trip_and_webhook(client, svc, sink):
    r = client.post(f"{J}/issue", headers=svc, json=_fields())
    assert r.status_code == 201, r.text
    out = r.json()
    assert set(out) == {"id", "key", "self"}
    assert out["key"] == "PAY-4"                    # PAY-1..3 exist in the mini world
    got = client.get(f"{J}/issue/{out['key']}", headers=svc).json()
    f = got["fields"]
    assert f["summary"] == "Follow-up: replication lag"
    assert f["reporter"]["accountId"] == "acc-jdoe"
    assert f["status"]["name"] == "To Do" and f["labels"] == ["internal-brain"]
    assert [n["content"][0]["text"] for n in f["description"]["content"]] == [
        "First paragraph.", "Sources: https://x/1"]
    hooks = [json.loads(b) for p, _, b in sink.calls if p == "jira"]
    assert hooks[-1]["type"] == "content.changed" and hooks[-1]["object_ids"] == [out["key"]]
    # the creator can browse it (reporter holder), a second create gets the next key
    r2 = client.post(f"{J}/issue", headers=svc, json=_fields(summary="Another"))
    assert r2.json()["key"] == "PAY-5" and int(r2.json()["id"]) == int(out["id"]) + 1


@pytest.mark.parametrize("over,field", [
    ({"summary": ""}, "summary"),
    ({"summary": "x" * 256}, "summary"),
    ({"summary": "two\nlines"}, "summary"),
    ({"issuetype": {"name": "Epic"}}, "issuetype"),
    ({"project": {"key": "NOPE"}}, "project"),
    ({"labels": ["has space"]}, "labels"),
])
def test_create_issue_validation_is_jira_shaped(client, svc, over, field):
    r = client.post(f"{J}/issue", headers=svc, json=_fields(**over))
    assert r.status_code == 400
    assert r.json()["errorMessages"] == [] and field in r.json()["errors"]


@pytest.mark.parametrize("project,account", [("PAY", "acc-leo"), ("SEC", "acc-jdoe"),
                                             ("PAY", "acc-old"), ("PAY", None)])
def test_create_issue_without_create_permission_is_403_and_changes_nothing(client, svc, sink,
                                                                           project, account):
    before = client.get(f"{J}/search/jql", headers=svc,
                        params={"jql": f"project = {project}", "maxResults": 100}).json()
    rep = {"reporter": {"accountId": account}} if account else {"reporter": None}
    r = client.post(f"{J}/issue", headers=svc, json=_fields(project={"key": project}, **rep))
    assert r.status_code == 403
    after = client.get(f"{J}/search/jql", headers=svc,
                       params={"jql": f"project = {project}", "maxResults": 100}).json()
    assert len(after["issues"]) == len(before["issues"])
    assert not sink.calls


def test_create_issue_needs_a_token(client):
    assert client.post(f"{J}/issue", json=_fields()).status_code == 401

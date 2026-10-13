"""Confluence subset: v2 cursor pagination via _links.next, shapes, and the permission check
(space grant AND restrictions on the page and every ancestor; nested groups; deactivated; trashed)."""
from __future__ import annotations

import pytest

C = "/confluence/wiki"
CHECK = C + "/rest/api/content/{}/permission/check"


def _follow(client, headers, url, params=None):
    """Follow ``_links.next`` exactly as a connector would with base URL ``<mock>/confluence``."""
    out, pages = [], 0
    r = client.get(url, params=params, headers=headers)
    while True:
        assert r.status_code == 200, r.text
        body = r.json()
        out.extend(body["results"])
        pages += 1
        nxt = body["_links"].get("next")
        if not nxt:
            assert "Link" not in r.headers
            return out, pages
        assert nxt.startswith("/wiki/api/v2/")
        assert r.headers["Link"].startswith(f"<{nxt}>")
        r = client.get("/confluence" + nxt, headers=headers)


def _check(client, headers, page_id, account):
    return client.post(CHECK.format(page_id), headers=headers,
                       json={"subject": {"type": "user", "identifier": account},
                             "operation": "read"})


def test_spaces_pagination_round_trip(client, svc, mini):
    spaces, pages = _follow(client, svc, f"{C}/api/v2/spaces", {"limit": 1})
    assert sorted(s["key"] for s in spaces) == sorted(s["key"] for s in mini["confluence"]["spaces"])
    assert pages == len(mini["confluence"]["spaces"])


def test_pages_pagination_by_space_excludes_trashed(client, svc, mini):
    got, _ = _follow(client, svc, f"{C}/api/v2/pages",
                     {"space-id": "sp-1", "limit": 2, "body-format": "storage"})
    want = [p for p in mini["confluence"]["pages"]
            if p["space"] == "payment-gateway" and p["status"] != "trashed"]
    assert sorted(p["id"] for p in got) == sorted(p["id"] for p in want)
    assert all(p["body"]["storage"]["value"] for p in got)
    trashed, _ = _follow(client, svc, f"{C}/api/v2/pages", {"status": "trashed"})
    assert [p["id"] for p in trashed] == [p["id"] for p in mini["confluence"]["pages"]
                                          if p["status"] == "trashed"]


def test_page_shape(client, svc, mini):
    src = next(p for p in mini["confluence"]["pages"] if p["id"] == "cf-1002")
    r = client.get(f"{C}/api/v2/pages/cf-1002", params={"body-format": "storage"}, headers=svc)
    p = r.json()
    assert p["id"] == "cf-1002" and p["title"] == src["title"] and p["status"] == "current"
    assert p["spaceId"] == "sp-1" and p["parentId"] == src["parent"]
    assert p["version"]["number"] == src["version"]
    assert p["body"]["storage"]["value"] == src["body"]
    assert p["authorId"] == "acc-priya"
    no_body = client.get(f"{C}/api/v2/pages/cf-1002", headers=svc).json()
    assert "body" not in no_body


def test_trashed_page_is_not_found_and_archived_is_returned(client, svc):
    assert client.get(f"{C}/api/v2/pages/cf-1004", headers=svc).status_code == 404
    assert client.get(f"{C}/api/v2/pages/cf-9999", headers=svc).status_code == 404
    arch = client.get(f"{C}/api/v2/pages/cf-1005", headers=svc)
    assert arch.status_code == 200 and arch.json()["status"] == "archived"


def test_history_versions(client, svc):
    vers = client.get(f"{C}/api/v2/pages/cf-1001/versions", headers=svc).json()["results"]
    assert [v["number"] for v in vers] == [2, 1]
    old = client.get(f"{C}/api/v2/pages/cf-1001", params={"version": 1, "body-format": "storage"},
                     headers=svc).json()
    assert old["version"]["number"] == 1 and old["body"]["storage"]["value"] == "Draft gateway overview."


def test_ancestors_root_first(client, svc):
    r = client.get(f"{C}/api/v2/pages/cf-1003/ancestors", headers=svc)
    assert [a["id"] for a in r.json()["results"]] == ["cf-1001", "cf-1002"]
    r = client.get(f"{C}/api/v2/pages/cf-1001/ancestors", headers=svc)
    assert r.json()["results"] == []


def test_read_restriction_shape(client, svc):
    r = client.get(f"{C}/rest/api/content/cf-1002/restriction/byOperation/read", headers=svc).json()
    assert r["operation"] == "read"
    assert [u["accountId"] for u in r["restrictions"]["user"]["results"]] == ["acc-priya"]
    assert [(g["name"], g["id"]) for g in r["restrictions"]["group"]["results"]] == \
        [("security", "grp-security")]
    empty = client.get(f"{C}/rest/api/content/cf-1001/restriction/byOperation/read",
                       headers=svc).json()
    assert empty["restrictions"]["user"]["results"] == []
    assert empty["restrictions"]["group"]["results"] == []


def test_space_permissions_shape(client, svc):
    r = client.get(f"{C}/api/v2/spaces/sp-1/permissions", headers=svc).json()["results"]
    principals = {(p["principal"]["type"], p["principal"]["id"]) for p in r}
    assert principals == {("user", "acc-sara"), ("group", "grp-eng")}
    assert all(p["operation"] == {"key": "read", "targetType": "space"} for p in r)


@pytest.mark.parametrize("page,account,expected,why", [
    ("cf-1001", "acc-jdoe", True, "space grant via NESTED group payments-eng -> engineering"),
    ("cf-1001", "acc-priya", True, "space grant via direct group"),
    ("cf-1001", "acc-sara", True, "space grant via user"),
    ("cf-1001", "acc-leo", False, "no space grant"),
    ("cf-1001", "acc-old", False, "deactivated user (would pass via nested group)"),
    ("cf-1002", "acc-priya", True, "restriction user"),
    ("cf-1002", "acc-sara", True, "restriction group"),
    ("cf-1002", "acc-jdoe", False, "space ok but not in restriction"),
    ("cf-1003", "acc-priya", True, "passes own AND parent restriction"),
    ("cf-1003", "acc-jdoe", False, "in own restriction but NOT in ancestor's (intersection)"),
    ("cf-1003", "acc-sara", False, "in ancestor's restriction but NOT in own"),
    ("cf-1005", "acc-jdoe", True, "archived page still readable"),
    ("cf-2001", "acc-sara", True, "SEC space group"),
    ("cf-2001", "acc-jdoe", False, "not in SEC space"),
    ("cf-1001", "acc-nobody", False, "unknown account"),
])
def test_permission_check_matrix(client, admin, page, account, expected, why):
    r = _check(client, admin, page, account)
    assert r.status_code == 200, r.text
    assert r.json()["hasPermission"] is expected, why


def test_permission_check_trashed_is_404(client, admin):
    assert _check(client, admin, "cf-1004", "acc-jdoe").status_code == 404
    assert _check(client, admin, "cf-0000", "acc-jdoe").status_code == 404


def test_permission_check_validates_body(client, admin):
    r = client.post(CHECK.format("cf-1001"), headers=admin,
                    json={"subject": {"type": "user", "identifier": "acc-jdoe"},
                          "operation": "delete"})
    assert r.status_code == 400

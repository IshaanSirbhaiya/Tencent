"""Drive subset: nextPageToken pagination, q subset, metadata/media, and permissions.list with
inherited entries that STOP at a limited-access folder; shared-drive membership inherited."""
from __future__ import annotations

D = "/drive/v3"
FOLDER = "application/vnd.google-apps.folder"


def _list_all(client, headers, **params):
    out, token, pages = [], None, 0
    while True:
        p = dict(params)
        if token:
            p["pageToken"] = token
        body = client.get(f"{D}/files", params=p, headers=headers).json()
        out.extend(body["files"])
        pages += 1
        token = body.get("nextPageToken")
        if not token:
            return out, pages


def _perms(client, headers, item_id):
    r = client.get(f"{D}/files/{item_id}/permissions", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["permissions"]


def _by_principal(perms):
    return {(p["type"], p.get("emailAddress") or p.get("domain")): p for p in perms}


def test_files_pagination_round_trip(client, svc, mini):
    files, pages = _list_all(client, svc, pageSize=2, driveId="0AEng",
                             includeItemsFromAllDrives="true", supportsAllDrives="true")
    want = [f["id"] for f in mini["drive"]["files"]] + [f["id"] for f in mini["drive"]["folders"]]
    assert sorted(f["id"] for f in files) == sorted(want)
    assert pages == -(-len(want) // 2)


def test_q_subset(client, svc, mini):
    files, _ = _list_all(client, svc, q="'fd-1' in parents and trashed = false")
    want = {f["id"] for f in mini["drive"]["files"] if f["folder"] == "fd-1"} | \
        {f["id"] for f in mini["drive"]["folders"] if f["parent"] == "fd-1"}
    assert {f["id"] for f in files} == want
    files, _ = _list_all(client, svc, q=f"mimeType != '{FOLDER}'")
    assert {f["id"] for f in files} == {f["id"] for f in mini["drive"]["files"]}
    files, _ = _list_all(client, svc, q="modifiedTime > '2026-09-22T00:00:00+08:00' "
                                        f"and mimeType != '{FOLDER}'")
    assert {f["id"] for f in files} == {"df-3", "df-4", "df-5"}
    r = client.get(f"{D}/files", params={"q": "starred = true"}, headers=svc)
    assert r.status_code == 400 and r.json()["error"]["code"] == 400


def test_file_metadata_and_media(client, svc, mini):
    src = next(f for f in mini["drive"]["files"] if f["id"] == "df-1")
    m = client.get(f"{D}/files/df-1", params={"fields": "*", "supportsAllDrives": "true"},
                   headers=svc).json()
    assert m["id"] == "df-1" and m["name"] == src["name"] and m["mimeType"] == src["mimeType"]
    assert m["parents"] == ["fd-1"] and m["driveId"] == "0AEng"
    assert m["version"] == str(src["version"]) and m["modifiedTime"] == src["modified"]
    assert m["webViewLink"] and m["inheritedPermissionsDisabled"] is False
    root = client.get(f"{D}/files/df-4", headers=svc).json()
    assert root["parents"] == ["0AEng"]
    folder = client.get(f"{D}/files/fd-2", headers=svc).json()
    assert folder["mimeType"] == FOLDER and folder["inheritedPermissionsDisabled"] is True
    media = client.get(f"{D}/files/df-1", params={"alt": "media"}, headers=svc)
    assert media.status_code == 200 and media.text == src["content"]
    assert client.get(f"{D}/files/fd-1", params={"alt": "media"}, headers=svc).status_code == 403
    assert client.get(f"{D}/files/nope", headers=svc).status_code == 404


def test_permissions_direct_folder_inherited_and_drive_member(client, svc):
    p = _by_principal(_perms(client, svc, "df-1"))
    leo = p[("user", "leo.ops@companya.example")]
    assert leo["id"] == "p9"
    assert leo["permissionDetails"] == [{"permissionType": "file", "role": "reader",
                                         "inherited": False}]
    sec = p[("group", "grp-security@companya.example")]
    assert sec["permissionDetails"] == [{"permissionType": "file", "role": "reader",
                                         "inherited": True, "inheritedFrom": "fd-1"}]
    eng = p[("group", "grp-eng@companya.example")]
    assert eng["permissionDetails"][0]["permissionType"] == "member"
    assert eng["permissionDetails"][0]["inheritedFrom"] == "0AEng"
    assert len(p) == 3


def test_limited_access_folder_stops_inheritance(client, svc):
    p = _by_principal(_perms(client, svc, "df-2"))
    assert set(p) == {("user", "leo.ops@companya.example"), ("user", "sara.sec@companya.example")}
    assert p[("user", "leo.ops@companya.example")]["permissionDetails"][0]["inheritedFrom"] == "fd-3"
    assert p[("user", "sara.sec@companya.example")]["permissionDetails"][0]["inheritedFrom"] == "fd-2"
    # negative: neither fd-1's group nor the shared-drive members reach below fd-2
    assert ("group", "grp-security@companya.example") not in p
    assert ("group", "grp-eng@companya.example") not in p
    fd2 = _by_principal(_perms(client, svc, "fd-2"))
    assert set(fd2) == {("user", "sara.sec@companya.example")}
    assert fd2[("user", "sara.sec@companya.example")]["permissionDetails"][0]["inherited"] is False


def test_anyone_and_domain_entries(client, svc):
    anyone = [x for x in _perms(client, svc, "df-3") if x["type"] == "anyone"]
    assert len(anyone) == 1 and anyone[0]["id"] == "p10"
    assert "emailAddress" not in anyone[0]
    dom = [x for x in _perms(client, svc, "df-4") if x["type"] == "domain"]
    assert dom[0]["domain"] == "companya.example"


def test_direct_and_inherited_merge_per_principal(client, svc):
    p = _by_principal(_perms(client, svc, "df-5"))
    sec = p[("group", "grp-security@companya.example")]
    assert sec["id"] == "p12" and sec["role"] == "writer"
    assert [(d["inherited"], d.get("inheritedFrom")) for d in sec["permissionDetails"]] == \
        [(False, None), (True, "fd-1")]


def test_permissions_pagination(client, svc):
    first = client.get(f"{D}/files/df-1/permissions", params={"pageSize": 1}, headers=svc).json()
    ids = [x["id"] for x in first["permissions"]]
    token = first.get("nextPageToken")
    while token:
        page = client.get(f"{D}/files/df-1/permissions", params={"pageSize": 1, "pageToken": token},
                          headers=svc).json()
        ids += [x["id"] for x in page["permissions"]]
        token = page.get("nextPageToken")
    assert ids == [x["id"] for x in _perms(client, svc, "df-1")]


def test_changes_feed(client, svc, admin):
    start = client.get(f"{D}/changes/startPageToken", headers=svc).json()["startPageToken"]
    empty = client.get(f"{D}/changes", params={"pageToken": start}, headers=svc).json()
    assert empty["changes"] == [] and empty["newStartPageToken"] == start
    client.post("/admin/drive/files/df-1/edit", json={"content": "v4"}, headers=admin)
    r = client.get(f"{D}/changes", params={"pageToken": start}, headers=svc).json()
    assert [c["fileId"] for c in r["changes"]] == ["df-1"]
    assert r["changes"][0]["file"]["version"] == "4" and r["changes"][0]["removed"] is False
    nxt = r["newStartPageToken"]
    assert client.get(f"{D}/changes", params={"pageToken": nxt}, headers=svc).json()["changes"] == []
    assert client.get(f"{D}/changes", params={"pageToken": "zzz"}, headers=svc).status_code == 400


def test_export_plain_text(client, svc, mini):
    src = next(f for f in mini["drive"]["files"] if f["id"] == "df-4")
    r = client.get(f"{D}/files/df-4/export", params={"mimeType": "text/plain"}, headers=svc)
    assert r.status_code == 200 and r.text == src["content"]
    assert client.get(f"{D}/files/df-4/export", params={"mimeType": "application/pdf"},
                      headers=svc).status_code == 400

"""``POST /admin/drive/files/upload``: an upload lands in Drive like an employee's file.

The file is served back byte-exact by ``alt=media``, inherits exactly the folder's permissions
(no direct entries), appears in ``files`` and the ``changes`` feed, fires ONE signed
``content.changed`` webhook, and is gone after ``/admin/reset``."""
from __future__ import annotations

import base64
import io
import json
import zipfile

import pytest

from mockplat_helpers import verify_signature

UP = "/admin/drive/files/upload"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _office_bytes() -> bytes:
    """Arbitrary binary bytes (a tiny zip with non-UTF-8 content), so byte-exactness is real."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", bytes(range(256)) * 4)
    return buf.getvalue()


def _body(mini, **over) -> dict:
    folder = mini["drive"]["folders"][0]
    body = {"folder": folder["id"], "name": "Quarterly plan.docx", "mime": DOCX,
            "content_b64": base64.b64encode(_office_bytes()).decode(), "owner": "jdoe"}
    body.update(over)
    return body


def _perm_view(perms):
    return sorted((p["type"], p.get("emailAddress") or p.get("domain"), p["role"]) for p in perms)


def test_upload_is_served_byte_exact_with_the_folders_permissions(client, svc, admin, mini, sink):
    folder = mini["drive"]["folders"][0]
    r = client.post(UP, headers=admin, json=_body(mini))
    assert r.status_code == 200, r.text
    out = r.json()
    fid = out["id"]
    assert fid.startswith("up-") and out["folder"] == folder["id"]
    assert out["drive"] == folder["drive"] and out["mimeType"] == DOCX

    meta = client.get(f"/drive/v3/files/{fid}", headers=svc).json()
    assert meta["mimeType"] == DOCX and meta["parents"] == [folder["id"]]
    assert meta["driveId"] == folder["drive"] and meta["name"] == "Quarterly plan.docx"
    assert meta["size"] == str(len(_office_bytes()))
    assert meta["lastModifyingUser"]["emailAddress"] == "jdoe@companya.example"

    media = client.get(f"/drive/v3/files/{fid}", params={"alt": "media"}, headers=svc)
    assert media.status_code == 200 and media.content == _office_bytes()

    # no direct permission: exactly what the folder grants (inherited), nothing more
    fperms = client.get(f"/drive/v3/files/{fid}/permissions", headers=svc).json()["permissions"]
    folder_perms = client.get(f"/drive/v3/files/{folder['id']}/permissions",
                              headers=svc).json()["permissions"]
    assert _perm_view(fperms) == _perm_view(folder_perms)
    assert all(d["inherited"] for p in fperms for d in p["permissionDetails"])

    listed = client.get("/drive/v3/files", headers=svc,
                        params={"q": f"'{folder['id']}' in parents"}).json()["files"]
    assert fid in {f["id"] for f in listed}


def test_upload_fires_one_signed_content_changed_and_enters_the_changes_feed(client, svc, admin,
                                                                             mini, sink):
    tok = client.get("/drive/v3/changes/startPageToken", headers=svc).json()["startPageToken"]
    sink.clear()
    fid = client.post(UP, headers=admin, json=_body(mini)).json()["id"]
    assert len(sink.calls) == 1
    platform, headers, body = sink.calls[0]
    assert platform == "drive" and verify_signature(headers, body)
    p = json.loads(body)
    assert p["type"] == "content.changed" and p["object_ids"] == [fid] and p["user"] == "jdoe"
    ch = client.get("/drive/v3/changes", headers=svc, params={"pageToken": tok}).json()
    assert [c["fileId"] for c in ch["changes"]] == [fid]
    assert ch["changes"][0]["removed"] is False


def test_text_upload_is_stored_as_text(client, svc, admin, mini):
    chat = "12/10/2026, 21:04 - Priya: shard 4 rehearsal moved\n"
    r = client.post(UP, headers=admin, json=_body(
        mini, name="chat.txt", mime="text/plain",
        content_b64=base64.b64encode(chat.encode()).decode()))
    assert r.status_code == 200, r.text
    media = client.get(f"/drive/v3/files/{r.json()['id']}", params={"alt": "media"}, headers=svc)
    assert media.text == chat


def test_silent_upload_sends_no_webhook(client, admin, mini, sink):
    sink.clear()
    r = client.post(UP + "?webhook=0", headers=admin, json=_body(mini))
    assert r.status_code == 200 and r.json()["webhook"] is None and sink.calls == []


def test_reset_removes_the_upload(client, svc, admin, mini):
    fid = client.post(UP, headers=admin, json=_body(mini)).json()["id"]
    assert client.post("/admin/reset", headers=admin, json={}).status_code == 200
    assert client.get(f"/drive/v3/files/{fid}", headers=svc).status_code == 404


def test_service_token_cannot_upload(client, svc, mini):
    assert client.post(UP, headers=svc, json=_body(mini)).status_code == 403
    assert client.post(UP, json=_body(mini)).status_code == 401


@pytest.mark.parametrize("over,status", [
    ({"mime": "application/x-msdownload"}, 400),
    ({"mime": "application/vnd.ms-word.document.macroEnabled.12"}, 400),
    ({"name": "../../etc/passwd"}, 400),
    ({"name": "a\\b.docx"}, 400),
    ({"name": ""}, 400),
    ({"name": "x" * 201}, 400),
    ({"content_b64": "not base64!"}, 400),
    ({"content_b64": ""}, 400),
    ({"content_b64": base64.b64encode(b"\0" * (5 * 1024 * 1024 + 1)).decode()}, 400),
    ({"mime": "text/plain", "content_b64": base64.b64encode(b"\xff\xfe").decode()}, 400),
    ({"folder": "fd-nope"}, 404),
    ({"folder": "df-1"}, 404),                      # a file is not a folder
    ({"owner": "nobody.here"}, 404),
])
def test_bad_uploads_are_refused_and_change_nothing(client, svc, admin, mini, sink, over, status):
    before = client.get("/drive/v3/files", headers=svc).json()["files"]
    sink.clear()
    assert client.post(UP, headers=admin, json=_body(mini, **over)).status_code == status
    assert client.get("/drive/v3/files", headers=svc).json()["files"] == before
    assert sink.calls == []

"""``GET /brain/export/sheets`` — standalone tests.

`export_routes.py` is not wired into `create_app()` (that one-line registration is left for the
gateway owner — see the module docstring), so a tiny FastAPI app is built here over the SAME
real clinic seed the running gateway loads at boot.

What these assert is that the download IS the evidence surface and not a copy of it: a real zip,
whose entries are exactly the manifest it carries, whose sheets are byte-for-byte what
`node_markdown` renders, and which omits — rather than empties — anything that did not exist at a
requested `as_of`.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from databridge_gateway.export_routes import register_export_routes
from databridge_graph.clinic_seed import fresh_clinic_store
from databridge_graph.node_sheet import node_markdown
from databridge_graph.sheet_export import DEFAULT_ROOT

_UTC = dt.timezone.utc
_BEFORE_THE_DOCUMENTS = dt.datetime(2026, 5, 1, tzinfo=_UTC)


@pytest.fixture(scope="module")
def store():
    return fresh_clinic_store()


@pytest.fixture()
def client(store):
    app = FastAPI()
    app.state.brain = SimpleNamespace(store=store)
    register_export_routes(app)
    return TestClient(app)


def _archive(response) -> zipfile.ZipFile:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(response.content))


def _manifest(archive: zipfile.ZipFile) -> dict:
    return json.loads(archive.read(f"{DEFAULT_ROOT}/manifest.json"))


# --- the archive --------------------------------------------------------------------------------


def test_the_route_returns_a_real_zip_whose_entries_match_the_manifest(client):
    archive = _archive(client.get("/brain/export/sheets"))
    assert archive.testzip() is None, "the archive is corrupt"

    manifest = _manifest(archive)
    expected = {f"{DEFAULT_ROOT}/{f['filename']}" for f in manifest["files"]}
    expected |= {f"{DEFAULT_ROOT}/index.md", f"{DEFAULT_ROOT}/manifest.json"}
    assert set(archive.namelist()) == expected
    assert manifest["count"] == len(manifest["files"]) > 0
    assert len(set(archive.namelist())) == len(archive.namelist()), "a name appears twice"


def test_every_entry_is_byte_for_byte_the_node_sheet(client, store):
    """The download is the same document the app shows and the assistant reads. Anything else
    would be a second renderer, and a folder that cannot be checked against the screen."""
    archive = _archive(client.get("/brain/export/sheets"))
    for record in _manifest(archive)["files"]:
        entry = archive.read(f"{DEFAULT_ROOT}/{record['filename']}")
        assert entry == node_markdown(store, record["entity_id"]).encode("utf-8")


def test_the_export_covers_every_open_node(client, store):
    archive = _archive(client.get("/brain/export/sheets"))
    exported = {f["entity_id"] for f in _manifest(archive)["files"]}
    assert exported == {row["id"] for row in store.open_nodes()}
    assert not _manifest(archive)["skipped"]


def test_entries_are_confined_to_one_directory(client):
    """A zip entry name is a path the extractor will follow. None of these may traverse."""
    for name in _archive(client.get("/brain/export/sheets")).namelist():
        assert name.startswith(f"{DEFAULT_ROOT}/")
        tail = name[len(DEFAULT_ROOT) + 1:]
        assert "/" not in tail and "\\" not in tail
        assert ".." not in name and not name.startswith("/")


def test_the_download_is_named_and_not_cached(client):
    response = client.get("/brain/export/sheets")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="clinic-evidence-sheets-')
    assert disposition.endswith('.zip"')
    assert "\n" not in disposition and "\r" not in disposition
    assert response.headers["cache-control"] == "no-store"


# --- rewinding ----------------------------------------------------------------------------------


def test_an_as_of_export_omits_what_did_not_exist_then(client, store):
    archive = _archive(client.get("/brain/export/sheets",
                                 params={"as_of": _BEFORE_THE_DOCUMENTS.isoformat()}))
    manifest = _manifest(archive)
    documents = {row["id"] for row in store.open_nodes() if row["kind"] == "document"}
    assert documents, "the seed has no documents to rewind past"

    exported = {f["entity_id"] for f in manifest["files"]}
    assert not (documents & exported), "a document that did not exist yet was exported"
    assert documents <= {s["entity_id"] for s in manifest["skipped"]}
    for record in manifest["files"]:
        entry = archive.read(f"{DEFAULT_ROOT}/{record['filename']}")
        assert entry == node_markdown(
            store, record["entity_id"], as_of=_BEFORE_THE_DOCUMENTS).encode("utf-8")
        assert entry, "a node was written empty rather than omitted"


def test_the_as_of_is_recorded_in_the_archive_and_its_name(client):
    response = client.get("/brain/export/sheets",
                          params={"as_of": _BEFORE_THE_DOCUMENTS.isoformat()})
    assert "as-of-20260501" in response.headers["content-disposition"]
    archive = _archive(response)
    assert _manifest(archive)["as_of"] == _BEFORE_THE_DOCUMENTS.isoformat()
    index = archive.read(f"{DEFAULT_ROOT}/index.md").decode("utf-8")
    assert "2026-05-01" in index and "valid time" in index


def test_a_naive_as_of_is_read_as_utc_rather_than_500ing(client):
    """The browser sends `toISOString()`, but a hand-typed `?as_of=2026-05-01` has no zone, and
    comparing it with the store's aware instants raises deep inside the renderer."""
    response = client.get("/brain/export/sheets", params={"as_of": "2026-05-01"})
    assert _manifest(_archive(response))["as_of"] == _BEFORE_THE_DOCUMENTS.isoformat()


# --- refusals -----------------------------------------------------------------------------------


def test_a_malformed_as_of_is_refused_not_ignored(client):
    """Silently exporting "now" for an unparseable instant would hand back a file that looks like
    the answer to the question that was asked."""
    response = client.get("/brain/export/sheets", params={"as_of": "last Tuesday"})
    assert response.status_code == 422
    assert response.json()["refusal"]["code"] == "BAD_AS_OF"


def test_an_uninitialised_graph_refuses_rather_than_serving_an_empty_zip():
    app = FastAPI()
    app.state.brain = None
    register_export_routes(app)
    response = TestClient(app).get("/brain/export/sheets")
    assert response.status_code == 503
    assert response.json()["refusal"]["code"] == "EXPORT_UNAVAILABLE"

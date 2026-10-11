"""Codex R-029 round 1, #9: a document ingested through `brain_ingest` is identified by its
source-qualified path, hashed — never by the literal path, and never by its content."""
from __future__ import annotations

import hashlib

from test_brain_server import FIXTURE_TEXT, _tools


def _doc_ids(store) -> set[str]:
    import json
    return {json.loads(r["source_pointer_json"]).get("document_id")
            for r in store.fetchall("SELECT source_pointer_json FROM sources")} - {None}


def test_r1_9_document_identity_is_the_hashed_path_and_content_is_kept_apart():
    t = _tools()
    before = _doc_ids(t.store)
    a = t.brain_ingest("inbox/a/letter.txt", text=FIXTURE_TEXT)
    b = t.brain_ingest("inbox/b/letter.txt", text=FIXTURE_TEXT)
    assert a["entities_added"] >= 1                                       # non-vacuous
    assert a["document_id"] != b["document_id"]           # same name, same text, two documents
    assert a["document_id"] == hashlib.sha256(b"brain://ingest/inbox/a/letter.txt").hexdigest()
    assert a["content_sha256"] == b["content_sha256"]     # the version identity is the content
    new = _doc_ids(t.store) - before
    assert a["document_id"] in new and "inbox/a/letter.txt" not in new

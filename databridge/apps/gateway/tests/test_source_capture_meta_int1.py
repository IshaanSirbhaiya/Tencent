"""INT1 item 7 — the gateway reads capture metadata only through `capture.get_meta`.

`source_capture.read_capture` used to `json.loads` the capture's `.json` itself, so metadata swapped
between captures or edited in place was published as the pointer's `source` and fed the
source-binding check. Now a corrupt file is reported (`meta_problem`), none of it is trusted, and
`snippet_and_verify` answers a refusal status instead of a snippet — never a crash.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

capture = pytest.importorskip("databridge_mcp_ingest.capture")

from databridge_gateway.source_capture import read_capture, snippet_and_verify  # noqa: E402

TEXT = "Invoice 4471 was paid in full on 2026-09-30 by Northwind Traders."
META = {"source": "crm", "uri": "file:///ledger/inv-4471.txt", "mime": "text/plain",
        "title": "inv-4471", "retrieved_at": "2026-10-01T09:00:00Z"}


def _sealed(root) -> str:
    sha = capture.put_bytes(TEXT.encode("utf-8"), root=root)
    capture.put_meta(sha, META, root=root)
    assert capture.get_meta(sha, root=root)["meta_digest"]              # non-vacuous: sealed
    return sha


def _tamper(root, sha, **changes) -> None:
    p = capture.meta_path(sha, root)
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw.update(changes)
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(capture.CaptureCorrupt):
        capture.get_meta(sha, root=root)


def _pointer(sha: str, value: str) -> SimpleNamespace:
    start = TEXT.index(value)
    return SimpleNamespace(capture_digest=f"sha256:{sha}", char_start=start,
                           char_end=start + len(value))


def test_a_sealed_capture_reads_and_verifies(tmp_path):
    sha = _sealed(tmp_path)
    cap = read_capture(sha, tmp_path)
    assert cap.meta_problem is None and cap.source_meta()["uri"] == META["uri"]
    out = snippet_and_verify(_pointer(sha, "Northwind Traders"), value="Northwind Traders",
                             root=tmp_path)
    assert out["verify"]["status"] == "verified"
    assert out["source"]["source"] == META["source"]


def test_tampered_metadata_is_reported_and_nothing_from_it_is_trusted(tmp_path):
    sha = _sealed(tmp_path)
    _tamper(tmp_path, sha, source="another-source", uri="file:///elsewhere.txt")
    cap = read_capture(sha, tmp_path)                                     # no crash
    assert cap.meta == {} and cap.source_meta() == {}
    assert cap.meta_problem
    out = snippet_and_verify(_pointer(sha, "Northwind Traders"), value="Northwind Traders",
                             root=tmp_path)
    assert out["verify"]["status"] == "capture_meta_corrupt"
    assert out["source"] == {} and out["snippet"] is None
    assert "another-source" not in json.dumps(out)


def test_legacy_metadata_without_a_digest_still_reads(tmp_path):
    sha = capture.put_bytes(TEXT.encode("utf-8"), root=tmp_path)
    capture.meta_path(sha, tmp_path).write_text(json.dumps(META), encoding="utf-8")
    cap = read_capture(sha, tmp_path)
    assert cap.meta_problem is None and cap.source_meta()["source"] == META["source"]

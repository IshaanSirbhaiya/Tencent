"""W1 (R-019-real-connections deferred #5): a capture's `.json` metadata is bound to its content
hash by a digest, so metadata swapped between captures — or edited in place — is detected."""
from __future__ import annotations

import json

import pytest

from databridge_mcp_ingest import capture

from conftest import fs_connection


def _put(tmp_path, data: bytes, **meta):
    sha = capture.put_bytes(data, tmp_path)
    base = {"source": "src", "uri": f"file:///{sha[:6]}", "mime": "text/plain",
            "title": sha[:6], "retrieved_at": "2026-10-01T00:00:00+00:00"}
    capture.put_meta(sha, {**base, **meta}, tmp_path)
    return sha


def test_the_metadata_carries_a_digest_bound_to_the_content_hash(tmp_path):
    sha = _put(tmp_path, b"alpha")
    raw = json.loads(capture.meta_path(sha, tmp_path).read_text(encoding="utf-8"))
    assert raw["meta_digest"] == capture.meta_digest(sha, raw)
    # the digest covers the content address: the same record under another sha does not match
    other = capture.sha256_bytes(b"beta")
    assert capture.meta_digest(other, raw) != raw["meta_digest"]
    assert capture.get_meta(sha, tmp_path)["uri"] == raw["uri"]


def test_metadata_swapped_between_two_captures_is_refused(tmp_path):
    a, b = _put(tmp_path, b"alpha"), _put(tmp_path, b"beta")
    pa, pb = capture.meta_path(a, tmp_path), capture.meta_path(b, tmp_path)
    ja, jb = pa.read_bytes(), pb.read_bytes()
    pa.write_bytes(jb)
    pb.write_bytes(ja)
    for sha in (a, b):
        with pytest.raises(capture.CaptureCorrupt):
            capture.get_meta(sha, tmp_path)


def test_metadata_edited_in_place_is_refused(tmp_path):
    sha = _put(tmp_path, b"alpha")
    p = capture.meta_path(sha, tmp_path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["uri"] = "file:///somewhere/else"
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(capture.CaptureCorrupt):
        capture.get_meta(sha, tmp_path)


def test_stripping_the_digest_is_caught_against_the_ledger_anchor(tmp_path):
    """The runner records the digest in `captured.jsonl`; a reader holding it refuses a file whose
    digest was removed (a bare file without one reads as legacy only when nothing expects one)."""
    sha = _put(tmp_path, b"alpha")
    want = capture.stored_meta_digest(sha, tmp_path)
    assert want
    p = capture.meta_path(sha, tmp_path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw.pop("meta_digest")
    raw["title"] = "forged"
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert capture.get_meta(sha, tmp_path)["title"] == "forged"          # legacy read
    with pytest.raises(capture.CaptureCorrupt):
        capture.get_meta(sha, tmp_path, expect_digest=want)


def test_a_second_sighting_keeps_the_first_digest_valid(tmp_path):
    sha = _put(tmp_path, b"alpha")
    first = capture.stored_meta_digest(sha, tmp_path)
    capture.put_meta(sha, {"source": "other", "uri": "file:///b", "mime": "text/plain",
                           "title": "b", "retrieved_at": "2026-10-02T00:00:00+00:00"}, tmp_path)
    meta = capture.get_meta(sha, tmp_path, expect_digest=first)
    assert meta["sightings"] == [{"source": "other", "uri": "file:///b"}]
    assert capture.stored_meta_digest(sha, tmp_path) == first


def test_a_real_run_anchors_every_capture_digest_in_its_ledger(walnut_env, corpus, common_vocab,
                                                             fake_harness):
    """Derived from the run: every captured line names the digest of its capture's metadata."""
    from pathlib import Path

    import databridge_mcp_ingest as mi
    from databridge_graph.store import GraphStore

    root, _ = corpus
    mi.register_connection(fs_connection("docs", root))
    mi.propose_plan("docs")
    path = walnut_env["WALNUT_GRAPH_DB_PATH"]
    path.parent.mkdir(parents=True, exist_ok=True)
    run_dir = Path(mi.run_ingest("docs", store=GraphStore(str(path)),
                                 harness=fake_harness).run_dir)
    lines = [json.loads(x) for x in (run_dir / "captured.jsonl").read_text(
        encoding="utf-8").splitlines() if x.strip()]
    assert lines, "the run captured nothing — the check would be vacuous"
    for rec in lines:
        assert rec["meta_digest"] == capture.stored_meta_digest(rec["sha256"])
        capture.get_meta(rec["sha256"], expect_digest=rec["meta_digest"])

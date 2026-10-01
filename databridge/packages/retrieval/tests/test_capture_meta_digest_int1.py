"""INT1 item 7 — retrieval reads capture metadata only through `capture.get_meta`.

Capture metadata now carries a `meta_digest` binding it to the content address, and
`databridge_mcp_ingest.capture.get_meta` raises `CaptureCorrupt` on a file that was swapped or
edited. The indexer used to `json.loads` the `.json` itself, so tampered metadata (another path, so
another document identity) was trusted. A corrupt capture must become a recorded refusal or a
recorded exclusion — never a crash, never a trusted value.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from retrieval_testkit import prose, write_capture

_INGEST = Path(__file__).resolve().parents[2] / "mcp_ingest"
if str(_INGEST) not in sys.path:
    sys.path.append(str(_INGEST))            # appended: never shadow this package's modules
capture = pytest.importorskip("databridge_mcp_ingest.capture")


def _sealed_capture(cap: Path, text: str, meta: dict) -> str:
    """A capture whose `.json` carries its `meta_digest`, as `capture.put_meta` writes it."""
    sha = capture.put_bytes(text.encode("utf-8"), root=cap)
    capture.text_path(sha, cap).write_text(text, encoding="utf-8", newline="")
    capture.put_meta(sha, meta, root=cap)
    assert capture.get_meta(sha, root=cap)["meta_digest"]              # non-vacuous: sealed
    return sha


def _tamper(cap: Path, sha: str, **changes) -> None:
    p = cap / sha[:2] / f"{sha}.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw.update(changes)
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(capture.CaptureCorrupt):                          # the reader agrees
        capture.get_meta(sha, root=cap)


def _events(pas: Path, source: str) -> list[tuple]:
    from databridge_retrieval import store as st
    from databridge_retrieval.store import store_filename

    conn = st.connect(pas / store_filename(source, "default"), readonly=True)
    try:
        return [tuple(r) for r in conn.execute(
            "SELECT event, doc_id, capture_sha256 FROM exclusion_events")]
    finally:
        conn.close()


def test_tampered_metadata_is_refused_and_recorded_not_indexed(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession

    cap, pas = dirs
    h, _ = fake_harness()
    good = _sealed_capture(cap, prose(1500, "g"), {"source": "cm", "uri": "file:///good.md"})
    bad = _sealed_capture(cap, prose(1500, "b"), {"source": "cm", "uri": "file:///bad.md"})
    _tamper(cap, bad, uri="file:///somewhere-else.md")
    with BuildSession("cm", harness=h, screen=screen) as b:
        ok = b.index_capture(good)
        r = b.index_capture(bad)
    assert ok.status == "indexed"
    assert r.status == "capture_corrupt" and r.chunks == 0 and r.detail
    # the tampered path never became an identity
    assert "somewhere-else" not in r.doc_id
    assert ("capture_corrupt", r.doc_id, bad) in _events(pas, "cm")


def test_legacy_metadata_without_a_digest_still_indexes(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession

    cap, _pas = dirs
    h, _ = fake_harness()
    sha = write_capture(cap, prose(1500), {"source": "cm", "uri": "file:///legacy.md"})
    assert "meta_digest" not in json.loads((cap / sha[:2] / f"{sha}.json").read_text("utf-8"))
    with BuildSession("cm", harness=h, screen=screen) as b:
        assert b.index_capture(sha).status == "indexed"


def test_metadata_tampered_after_indexing_drops_the_document_at_publish(dirs, fake_harness,
                                                                        screen):
    from databridge_retrieval import BuildSession

    cap, pas = dirs
    h, _ = fake_harness()
    a = _sealed_capture(cap, prose(1500, "a"), {"source": "cm", "uri": "file:///a.md"})
    z = _sealed_capture(cap, prose(1500, "z"), {"source": "cm", "uri": "file:///z.md"})
    with BuildSession("cm", harness=h, screen=screen) as b:
        b.index_capture(a)
        doc_z = b.index_capture(z).doc_id
    _tamper(cap, z, source="another-source")
    b = BuildSession("cm", harness=h, screen=screen).open()
    rep = b.publish()
    assert [d["doc_id"] for d in rep.dropped] == [doc_z]
    assert "metadata" in rep.dropped[0]["reason"]
    assert ("document_unresolvable", doc_z, z) in _events(pas, "cm")


def test_entry_point_returns_a_refusal_for_a_corrupt_capture(dirs, fake_harness):
    from databridge_retrieval import configure, index_capture

    cap, _pas = dirs
    h, _ = fake_harness()
    configure(harness=h)
    sha = _sealed_capture(cap, prose(900), {"source": "entry", "uri": "file:///e.md"})
    _tamper(cap, sha, title="swapped")
    r = index_capture(sha)                                               # no crash
    assert r.status == "capture_corrupt"


def test_read_capture_never_returns_tampered_metadata(dirs):
    from databridge_retrieval import read_capture

    cap, _pas = dirs
    sha = _sealed_capture(cap, prose(600), {"source": "cm", "uri": "file:///r.md"})
    _tamper(cap, sha, uri="file:///forged.md")
    text, meta = read_capture(sha, cap)                                  # no crash
    assert text is not None and meta == {}                               # nothing trusted

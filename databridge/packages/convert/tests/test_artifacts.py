"""PipelineArtifact: checksum, store (no pickle), lifecycles, drift/tamper detection."""
from __future__ import annotations

import pytest

from databridge_convert import ArtifactStore, Frame, TransformSpec, build_s2s_artifact, infer
from databridge_convert.artifacts import PipelineArtifact, audit, preview, replay
from databridge_convert.rules.spec import CoercionUse

SPEC = TransformSpec(language="jsonata", expression='$.{ "x": a }',
                     coercions=[CoercionUse(name="id_cast", columns=["x"])])


def _artifact() -> PipelineArtifact:
    return build_s2s_artifact(SPEC, artifact_id="A1", source_adapter="json",
                              target_adapter="csv", approved_by="alice")


def test_checksum_roundtrips():
    art = _artifact()
    assert art.checksum != ""
    assert art.verify_checksum() is True


def test_store_save_load_no_pickle(tmp_path):
    art = _artifact()
    store = ArtifactStore()
    path = store.save(art, tmp_path)
    assert path.suffix == ".json"                      # JSON, never pickle
    assert "pickle" not in path.read_text(encoding="utf-8").lower()
    loaded = store.load(tmp_path)
    assert loaded == art


def test_tamper_detection(tmp_path):
    art = _artifact()
    store = ArtifactStore()
    store.save(art, tmp_path)
    p = tmp_path / "artifact.json"
    p.write_text(p.read_text(encoding="utf-8").replace('"alice"', '"mallory"'), encoding="utf-8")
    with pytest.raises(ValueError):                    # checksum mismatch → refuse to load
        store.load(tmp_path)


def test_immutable_versioning():
    art = _artifact()
    v2 = art.model_copy(update={"version": 2, "supersedes": art.artifact_id}).with_checksum()
    assert v2.version == 2 and v2.supersedes == "A1"
    with pytest.raises(Exception):                     # frozen — no in-place mutation
        art.version = 9


def test_lifecycles():
    art = _artifact()
    frame = Frame(rows=[{"a": 1}, {"a": 2}])
    assert preview(art, frame, n=1).rows == [{"x": "1"}]         # id_cast → "1"
    assert replay(art, frame).rows == [{"x": "1"}, {"x": "2"}]
    assert infer(art, {"a": 7}) == {"x": "7"}
    assert audit(art)["checksum_ok"] is True

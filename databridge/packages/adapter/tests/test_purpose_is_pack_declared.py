"""Purpose is part of the principal, and its vocabulary belongs to the pack (spine D5).

Two failures this file is written to catch, both of which look like conveniences:

1. someone adds a fallback purpose (``"unspecified"``, or an empty-string default) so that a
   caller who did not think about it can still read. GDPR 5(1)(b) / PDPA purpose limitation is not
   retrofittable after an audit has begun, and a purpose that can be omitted will be;
2. someone moves the vocabulary into engine code as an enum, at which point the engine has an
   opinion about a domain question and the pack layering is gone.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from databridge_adapter import (
    Principal,
    PurposeError,
    PurposeNotDeclared,
    active_purposes,
    load_pack_purposes,
    purpose_vocabulary,
    use_purposes,
)


def _write_pack(root: Path, name: str, body: str) -> Path:
    (root / name).mkdir(parents=True, exist_ok=True)
    path = root / name / "purposes.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# --- the vocabulary is the pack's ---------------------------------------------------

def test_every_shipped_pack_declares_its_purposes(all_packs, packs_root):
    """Each pack on disk declares a purpose vocabulary the loader accepts."""
    for pack in all_packs:
        declared = load_pack_purposes(pack, packs_root)
        assert declared, f"pack {pack!r} declares no purposes"
        assert all(d.pack == pack for d in declared.values())


def test_purpose_ids_are_unique_across_the_enabled_packs(all_packs, packs_root):
    """Two packs sharing one id would carry two lawful bases behind one name."""
    vocab = purpose_vocabulary(all_packs, packs_root)
    total = sum(len(load_pack_purposes(p, packs_root)) for p in all_packs)
    assert len(vocab.purposes) == total


def test_a_collision_between_packs_is_refused(tmp_path):
    _write_pack(tmp_path, "a", "pack: a\npurposes:\n  - id: x.read\n    subject_scoped: false\n")
    _write_pack(tmp_path, "b", "pack: b\npurposes:\n  - id: x.read\n    subject_scoped: false\n")
    with pytest.raises(PurposeError, match="declared by both"):
        purpose_vocabulary(("a", "b"), tmp_path, use_cache=False)


def test_the_default_vocabulary_comes_from_the_deployment_binding():
    """No explicit binding: the vocabulary is the ENABLED packs', read through the graph loader."""
    from databridge_graph.packs import active_vocabulary

    vocab = active_purposes()
    assert vocab.packs == tuple(active_vocabulary().packs)
    assert vocab.purposes, "the enabled packs declare no purposes at all"


# --- it fails closed ----------------------------------------------------------------

def test_an_undeclared_purpose_is_refused(packs_purposes):
    with pytest.raises(PurposeNotDeclared) as exc:
        Principal(actor="orchestrator@walnut", on_behalf_of=None, purpose="just.because")
    # The refusal has to say what to do about it, or it will be routed around.
    assert "purposes.yaml" in str(exc.value)


def test_there_is_no_fallback_purpose(packs_purposes):
    """The three shapes a fallback usually takes."""
    for bogus in ("", "unspecified", "general"):
        with pytest.raises((PurposeNotDeclared, ValueError)):
            Principal(actor="a@b", on_behalf_of=None, purpose=bogus)


def test_purpose_cannot_be_omitted(packs_purposes):
    with pytest.raises(Exception):
        Principal(actor="a@b", on_behalf_of=None)          # type: ignore[call-arg]


def test_on_behalf_of_cannot_be_omitted(packs_purposes):
    """None means 'no human asked'. That is a decision, so the caller states it."""
    with pytest.raises(Exception):
        Principal(actor="a@b", purpose="finance.graph_build")   # type: ignore[call-arg]


def test_an_empty_vocabulary_refuses_everything(tmp_path):
    with use_purposes(purpose_vocabulary((), tmp_path, use_cache=False)):
        with pytest.raises(PurposeNotDeclared):
            Principal(actor="a@b", on_behalf_of=None, purpose="finance.graph_build")


# --- subject scope drives the grant requirement (ADAPTER_AGENT_SPEC §1) -------------

def test_a_subject_scoped_read_without_a_grant_is_refused(packs_purposes):
    with pytest.raises(ValueError, match="subject_scoped"):
        Principal(actor="orchestrator@walnut", on_behalf_of="rm-0042",
                  purpose="lending.underwriting")


def test_a_subject_scoped_read_with_a_grant_is_accepted(underwriter):
    assert underwriter.is_subject_scoped is True
    assert underwriter.grant_id == "grant-abc"


def test_a_build_time_crawl_is_not_subject_scoped_and_needs_no_grant(crawler):
    assert crawler.is_subject_scoped is False
    assert crawler.grant_id is None
    assert crawler.on_behalf_of is None


def test_a_blank_grant_does_not_satisfy_the_requirement(packs_purposes):
    with pytest.raises(ValueError, match="subject_scoped"):
        Principal(actor="a@b", on_behalf_of="h", purpose="lending.underwriting", grant_id="   ")


# --- the loader refuses malformed declarations --------------------------------------

def test_subject_scoped_must_be_declared_explicitly(tmp_path):
    """Defaulting it would silently decide whether a grant is required."""
    _write_pack(tmp_path, "p", "pack: p\npurposes:\n  - id: a.b\n")
    with pytest.raises(PurposeError, match="subject_scoped"):
        load_pack_purposes("p", tmp_path)


def test_a_single_word_purpose_is_refused(tmp_path):
    _write_pack(tmp_path, "p", "pack: p\npurposes:\n  - id: billing\n    subject_scoped: false\n")
    with pytest.raises(PurposeError, match="two\n?\\s*segments|two segments"):
        load_pack_purposes("p", tmp_path)


def test_a_pack_declaring_someone_elses_name_is_refused(tmp_path):
    _write_pack(tmp_path, "p", "pack: q\npurposes: []\n")
    with pytest.raises(PurposeError, match="directory"):
        load_pack_purposes("p", tmp_path)


def test_a_pack_with_no_purposes_file_declares_nothing(tmp_path):
    (tmp_path / "p").mkdir()
    assert load_pack_purposes("p", tmp_path) == {}


def test_the_vocabulary_is_sealed_against_mutation(packs_purposes):
    """A mutable vocabulary is not a closed one; this object is cached process-wide."""
    with pytest.raises(TypeError):
        packs_purposes.purposes["invented.purpose"] = None      # type: ignore[index]

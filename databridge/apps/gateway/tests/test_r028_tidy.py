"""R-028 TIDY: the gateway's link review uses the SUBJECT predicate (CONS_BRIEF standard 7).

A kind the pack declares a protected subject but NOT a person (a vocabulary exposing `is_person`
that answers False for it) is still never a link target, never approvable, and never shows sample
keys. Expectations come from `mapping.is_subject_kind`, the rule the code uses.
"""
from __future__ import annotations

import pytest


class _Vocab:
    """A vocabulary that splits subjects from persons: `org` is a subject, not a person."""

    node_kinds = {"org": None, "order": None}

    def is_subject(self, kind):
        return kind == "org"

    def is_person(self, kind):
        return False


def _link(kind):
    return {"column": "org_id", "source": "other", "table": "orgs", "key": "id",
            "target_kind": kind, "status": "proposed", "edge": {"kind": "belongs_to"},
            "matched": 3, "with_value": 3}


@pytest.fixture()
def patched(monkeypatch):
    from databridge_mcp_ingest import mapping as mapping_mod
    from databridge_mcp_ingest.mapping import SourceMapping, TableMapping

    def make(kind):
        m = SourceMapping(source_id="src", tables={"orders": TableMapping(
            status="proposed", links=[_link(kind)])})
        monkeypatch.setattr(mapping_mod, "load_mapping", lambda *_a, **_k: m)
        monkeypatch.setattr(mapping_mod, "logged_approvals", lambda *_a, **_k: {})
        monkeypatch.setattr(mapping_mod, "captured_tables", lambda *_a, **_k: {})
        return m
    return make


@pytest.mark.parametrize("kind", ["org", "order"])
def test_a_subject_kind_that_is_not_a_person_is_never_a_link_target(patched, kind):
    from databridge_mcp_ingest.mapping import is_person_kind, is_subject_kind

    from databridge_gateway import mapping_review

    vocab = _Vocab()
    patched(kind)
    subject = is_subject_kind(vocab, kind)
    assert not is_person_kind(vocab, kind)               # the case the person predicate misses
    [row] = mapping_review.review("src", vocab)["links"]
    assert row["subject_target"] is subject
    assert row["approvable"] is (not subject)
    refusal = mapping_review.person_target_refusal("src", "orders", "org_id", None, vocab)
    assert (refusal is not None) is subject


def test_subject_evidence_never_shows_sample_keys(patched):
    from databridge_gateway import mapping_review

    patched("org")
    ev = mapping_review.link_evidence("src", "orders", _link("org"), _Vocab(), {})
    assert ev["samples"] is None and "subject" in (ev["samples_note"] or "")

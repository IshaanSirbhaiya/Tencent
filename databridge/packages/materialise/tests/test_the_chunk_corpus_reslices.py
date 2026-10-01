"""The RAG shape — SYSTEM_DESIGN §6.6(c) — over the estate's free text.

Two properties carry the shape, and both are checked against the estate rather than asserted:

* **membership on system time**, so retrieval replays what was on file at the corpus instant;
* **every chunk re-slices byte-identically** from its own `(row_id, char_start, char_end)`, which
  is the grounding wall one level up. Citation hallucination rates of 11–57% persist across
  commercially deployed models, so this has to be structural rather than a prompt instruction.

`facility.notes` is the corpus source because it is the one free-text column in `core_banking`
that is *not* also declared sensitive — a sensitive free-text column would be a transformed
disclosure and refused under O1b before it got here, which is itself worth knowing.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from databridge_materialise import materialise
from databridge_materialise.shapes.chunked import _boundaries

from plans import facility_notes_corpus


@pytest.fixture
def corpus(compliance_reader):
    return materialise(facility_notes_corpus(), compliance_reader)


def test_a_chunk_corpus_is_produced(corpus):
    assert corpus.dataset.n_rows > 0
    assert corpus.dataset.columns == ("chunk_id", "document_id", "char_start", "char_end", "text")
    assert corpus.receipt.cells.grounded == corpus.dataset.n_rows * 5


def test_every_chunk_reslices_from_its_own_offsets(corpus, finance_engine):
    """The check that makes a chunk a POINTER rather than a copy with a citation attached.

    Re-derived here from the estate's own bytes: read the note back out of SQL, slice it at the
    offsets the chunk carries, and require the result to be byte-identical.
    """
    with finance_engine.begin() as conn:
        notes = {r.facility_id: r.notes for r in conn.execute(text(
            "select facility_id, notes from core_banking.facility where notes is not null"))}

    checked = 0
    for row in corpus.dataset.rows:
        doc, start, end, chunk = row[1].value, row[2].value, row[3].value, row[4].value
        assert notes[doc][start:end] == chunk
        assert row[4].pointer.kind == "derived"
        assert row[4].pointer.derivation == f"text_slice@v1(start={start},end={end})"
        assert row[4].pointer.derived_from, (
            "a chunk that does not cite the cell it was sliced from is an unsourced string")
        checked += 1
    assert checked > 50


def test_chunks_cover_their_document_exactly_once(corpus, finance_engine):
    """Late chunking with no overlap partitions the text. A gap loses evidence; a duplicated span
    double-counts it in a retriever's scoring."""
    with finance_engine.begin() as conn:
        lengths = {r.facility_id: len(r.notes) for r in conn.execute(text(
            "select facility_id, notes from core_banking.facility where notes is not null"))}
    spans: dict[str, list[tuple[int, int]]] = {}
    for row in corpus.dataset.rows:
        spans.setdefault(row[1].value, []).append((row[2].value, row[3].value))
    for doc, ranges in spans.items():
        ranges.sort()
        assert ranges[0][0] == 0
        assert ranges[-1][1] == lengths[doc]
        for (_, prev_end), (next_start, _) in zip(ranges, ranges[1:]):
            assert prev_end == next_start


def test_membership_is_cut_on_SYSTEM_time_not_on_the_present(compliance_reader, finance_engine):
    """A corpus instant before the estate's own `updated_at` values must exclude those documents.

    This is what "retrieval replays what the agency had on file" means operationally: move the
    instant back and the corpus shrinks, because those notes had not been written yet.
    """
    plan = facility_notes_corpus()
    with finance_engine.begin() as conn:
        earliest = conn.execute(text(
            "select min(updated_at) from core_banking.facility where notes is not null")).scalar()
    before_everything = datetime(2020, 1, 1, tzinfo=timezone.utc)

    full = materialise(plan, compliance_reader)
    empty = materialise(facility_notes_corpus(temporal=plan.temporal.model_copy(
        update={"corpus_as_of": before_everything})), compliance_reader)

    assert full.dataset.n_rows > 0
    assert empty.dataset.n_rows == 0, (
        f"no facility note existed before 2020 (earliest {earliest}), so a corpus cut at that "
        f"instant must be empty — otherwise membership is being read off the present")


def test_the_corpus_says_what_it_does_not_have(corpus):
    """Two honest absences, stated in the receipt rather than left for a consumer to discover."""
    notes = " ".join(corpus.receipt.notes)
    assert "no embedding model is pinned" in notes
    assert "generated_claim" in notes and "never citable as evidence" in notes


def test_a_free_text_column_is_unreadable_to_a_principal_without_the_exception(reader):
    """The corpus depends on a named policy exception, and the refusal path is worth pinning.

    `free_text_policy` in this estate withholds every free-text column from every role by default;
    exactly one role holds an exception. A credit analyst therefore builds an EMPTY corpus rather
    than a partial one, and the receipt counts the documents as policy-rejected rather than as
    missing.
    """
    built = materialise(facility_notes_corpus(), reader)
    assert built.dataset.n_rows == 0
    assert built.receipt.cells.grounded == 0


def test_the_boundary_function_is_total_and_terminating():
    """The chunker itself. An off-by-one here is a corpus with a missing character in every
    document, and it would pass every re-slice check because both sides share the bug."""
    assert _boundaries(0, 10, 0) == []
    assert _boundaries(10, 10, 0) == [(0, 10)]
    assert _boundaries(25, 10, 0) == [(0, 10), (10, 20), (20, 25)]
    assert _boundaries(25, 10, 3) == [(0, 10), (7, 17), (14, 24), (21, 25)]


def test_a_chunk_whose_offsets_do_not_reproduce_is_refused():
    """The failure mode the re-slice check exists for, provoked directly through the rule."""
    from databridge_adapter import DerivationError, parse_derivation, replay_derivation

    call = parse_derivation("text_slice@v1(start=0,end=99)")
    with pytest.raises(DerivationError, match="range the source does not have"):
        replay_derivation(call, ["short text"])

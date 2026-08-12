"""Node evidence sheets — the document a person reads, and the grounding unit the model reads.

The sheet exists so that a click on a node yields something auditable, and so that
``build_context`` can hand the LLM the *same* text a human was shown. That second purpose is why
these tests are strict about two things: nothing may appear in a sheet that the graph does not
hold, and the unresolved-identity caveat must survive into it. A sheet is fed to a model; anything
ungrounded in it is ungrounded in the model's context (CLAUDE.md §Hard constraints 2), and an
identity claim that loses its caveat there is one the rest of the system refuses to make
(§Hard constraints 1).
"""
from __future__ import annotations

import datetime as dt

import pytest

from databridge_graph.clinic_seed import fresh_clinic_store
from databridge_graph.node_sheet import IDENTITY_CAVEAT, node_markdown


@pytest.fixture(scope="module")
def store():
    return fresh_clinic_store()


@pytest.fixture(scope="module")
def node_ids(store) -> list[str]:
    return [row["id"] for row in store.open_nodes()]


def test_every_node_renders_a_sheet(store, node_ids):
    assert node_ids, "the seed produced no nodes"
    for node_id in node_ids:
        sheet = node_markdown(store, node_id)
        assert sheet, f"{node_id} rendered nothing"
        assert sheet.startswith("# "), f"{node_id} has no title"
        for heading in ("## Attributes", "## Relationships", "## Sources"):
            assert heading in sheet, f"{node_id} is missing {heading}"


def test_an_unknown_node_returns_none_rather_than_an_empty_sheet(store):
    """An empty sheet for a node that does not exist would render as "we know nothing about this
    entity", which is a different and much weaker statement than "there is no such entity"."""
    assert node_markdown(store, "patient:does-not-exist") is None


def test_a_claimed_patient_link_keeps_its_unverified_caveat(store, node_ids):
    """`FOR_PATIENT` is a CLAIM the document makes, resolved into candidates a human chooses
    between. Every rendering of it must say so — including this one, because this text goes into
    the model's context and `PLAN.md` §6.6 flags exactly this failure mode.
    """
    documents = [n for n in node_ids if n.startswith("document:")]
    assert documents, "no document nodes in the seed"

    caveated = 0
    for node_id in documents:
        sheet = node_markdown(store, node_id)
        if "**for_patient**" in sheet:
            assert IDENTITY_CAVEAT in sheet, (
                f"{node_id} renders a for_patient edge WITHOUT the unverified caveat")
            caveated += 1
    assert caveated > 0, "no document sheet exercised the for_patient path"


def test_a_sheet_never_claims_identity_was_verified(store, node_ids):
    """The wording check. The repo has already shipped "verified in person" in green with a tick;
    the record is that a named person SAID they checked."""
    forbidden = ("identity verified", "verified identity", "identity confirmed",
                 "confirmed identity")
    for node_id in node_ids:
        blob = node_markdown(store, node_id).lower()
        for phrase in forbidden:
            assert phrase not in blob, f"{node_id} asserts {phrase!r}"


def test_every_citation_in_a_sheet_is_a_real_stored_pointer(store, node_ids):
    """A sheet is assembled, never generated. Its Sources section must be exactly what the store
    holds — no more, and no invented locations."""
    for node_id in node_ids:
        sheet = node_markdown(store, node_id)
        stored = store.sources_for(node_id)
        sources_section = sheet.split("## Sources", 1)[1]
        assert f"{len(stored)} citation(s)" in sources_section
        for pointer in stored:
            assert pointer.kind in sources_section, (
                f"{node_id}: stored pointer kind {pointer.kind!r} missing from the sheet")


def test_as_of_hides_relationships_that_had_not_begun(store, node_ids):
    """The sheet answers the same question the time slider does, in prose.

    This is only meaningful because the bi-temporal data was fixed: 63 of 111 edges used to carry
    the standing-config date rather than the date the relationship became true, so an `as_of` in
    the past returned relationships that had not happened yet.
    """
    documents = [n for n in node_ids if n.startswith("document:")]
    before_anything = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)

    for node_id in documents:
        early = node_markdown(store, node_id, as_of=before_anything)
        assert "_No relationships recorded at this point in time._" in early, (
            f"{node_id} shows relationships at a date before the document existed")
        now = node_markdown(store, node_id)
        assert "_No relationships recorded" not in now, f"{node_id} shows none at all today"

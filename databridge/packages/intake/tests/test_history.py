"""The clinic-history seam, from the intake side.

Everything here uses a FAKE history — this package must never import the graph, and these tests
prove the seam holds without one. The graph adapter is tested in `packages/graph`.

The load-bearing test is `test_history_never_changes_a_candidate_score`. It is the guardrail for
the one way this feature could quietly become identity resolution.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re

import pytest

from databridge_core import SourcePointer
from databridge_intake.eligibility import FLAG_CONFLICTING_DOCUMENT
from databridge_intake.history import ClinicHistory, NoHistory, PriorIntake, PriorTest
from databridge_intake.paths import fixtures_dir
from databridge_intake.pipeline import run_intake

PTR = SourcePointer(source_id="chit_x.txt", kind="doc_span", document_id="chit_x.txt",
                    char_start=0, char_end=5)


class FakeHistory:
    """A hand-built history. No graph, no traversal — just the answers."""

    def __init__(self, intakes=(), tests=()):
        self._intakes = list(intakes)
        self._tests = list(tests)

    def prior_intakes_for(self, patient_id):
        return [i for i in self._intakes if i.patient_id == patient_id]

    def documents_naming(self, patient_id, *, exclude_document_id=None):
        return [i for i in self._intakes
                if i.patient_id == patient_id and i.document_id != exclude_document_id]

    def tests_authorised_for(self, patient_id, *, since=None):
        return list(self._tests)


class ExplodingHistory:
    """Every method raises. The pipeline must not care."""

    def prior_intakes_for(self, patient_id):
        raise RuntimeError("graph unavailable")

    def documents_naming(self, patient_id, *, exclude_document_id=None):
        raise RuntimeError("graph unavailable")

    def tests_authorised_for(self, patient_id, *, since=None):
        raise RuntimeError("graph unavailable")


def _draft(name="chit_01_meridian_referral.txt", **kw):
    path = fixtures_dir() / "chits" / name
    return run_intake(path.read_text(encoding="utf-8"), document_id=name, **kw)


# --- THE SEAM ---------------------------------------------------------------------------


def test_packages_intake_never_imports_the_graph():
    """`packages/graph` imports `packages/intake` (clinic_seed uses the registry, resolver and
    rules). Importing back would be circular — the whole reason the port is a Protocol here and the
    implementation lives there. A direct import "just this once" is what this test exists to stop."""
    root = pathlib.Path(__file__).resolve().parents[2] / "databridge_intake"
    offenders = []
    for py in root.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if re.search(r"^\s*(from|import)\s+databridge_graph", text, re.M):
            offenders.append(py.name)
    assert offenders == [], (
        f"{offenders} import databridge_graph — use the ClinicHistory port instead "
        f"(PLAN.md §5 Phase 6.0)")


def test_no_history_is_the_default():
    """The pipeline must produce a valid card with no graph attached. If a feature only works with
    a graph wired in, that shows up here rather than as a surprise in production."""
    draft = _draft()
    assert FLAG_CONFLICTING_DOCUMENT not in draft.coverage.eligibility_flags
    assert draft.coverage.status.value == "determined"


def test_no_history_answers_empty_for_everything():
    h = NoHistory()
    assert h.prior_intakes_for("pat:anything") == []
    assert h.documents_naming("pat:anything") == []
    assert h.tests_authorised_for("pat:anything") == []
    assert isinstance(h, ClinicHistory)          # runtime_checkable Protocol


def test_a_broken_history_does_not_block_an_intake():
    """Fail open on enrichment, never on safety. A graph that is down loses flags; it must not stop
    the counter reading a document."""
    draft = _draft(history=ExplodingHistory())
    assert draft.coverage.status.value == "determined"
    assert FLAG_CONFLICTING_DOCUMENT not in draft.coverage.eligibility_flags
    assert draft.resolution.candidates


def test_a_naive_timestamp_from_the_port_does_not_blow_up_the_intake():
    """The guard used to wrap only the port CALL, not the loop that reads its result.

    `seen_at` has no timezone contract in the port, so a perfectly valid non-graph implementation
    returning a naive datetime made the window comparison raise `TypeError` — which escaped
    `determine_coverage` and surfaced as an `EXTRACTION_FAILED` refusal. A document became unreadable
    because an enrichment had the wrong tzinfo.
    """
    draft = _draft(history=NoHistory())
    patient_id = draft.resolution.candidates[0].patient_id
    naive = FakeHistory(intakes=[
        PriorIntake(document_id="chit_naive.txt", patient_id=patient_id, issuer_code="NSTNBU",
                    seen_at=dt.datetime(2026, 8, 9), sources=[PTR])])       # no tzinfo
    result = _draft(history=naive)
    assert result.coverage.status.value in {"determined", "partial"}
    # And the finding is still produced, rather than being lost to a swallowed exception.
    assert FLAG_CONFLICTING_DOCUMENT in result.coverage.eligibility_flags


def test_codes_are_compared_the_way_the_rules_table_compares_them():
    """The traversal is handed the RAW extracted values so it still works when the issuer cannot be
    resolved — but the graph's values are canonical. Comparing literally made a chit printing
    "mrdeb" disagree with itself."""
    draft = _draft(history=NoHistory())
    patient_id = draft.resolution.candidates[0].patient_id
    same_but_scruffy = FakeHistory(intakes=[
        PriorIntake(document_id="chit_other.txt", patient_id=patient_id,
                    issuer_code="  mrdeb ", sources=[PTR])])
    assert FLAG_CONFLICTING_DOCUMENT not in _draft(
        history=same_but_scruffy).coverage.eligibility_flags


def test_a_finding_carries_the_pointers_it_was_given():
    draft = _draft(history=NoHistory())
    patient_id = draft.resolution.candidates[0].patient_id
    conflicting = FakeHistory(intakes=[
        PriorIntake(document_id="chit_other.txt", patient_id=patient_id,
                    issuer_code="NSTNBU", sources=[PTR])])
    findings = _draft(history=conflicting).coverage.history_findings
    assert len(findings) == 1
    assert findings[0].sources == [PTR]
    assert findings[0].document_id == "chit_other.txt"


def test_an_ambiguous_prior_document_is_described_as_such():
    """A prior document that matched three patients did not identify this one. Saying it "also
    names" them overstates what the graph knows."""
    draft = _draft(history=NoHistory())
    patient_id = draft.resolution.candidates[0].patient_id
    ambiguous = FakeHistory(intakes=[
        PriorIntake(document_id="chit_other.txt", patient_id=patient_id, issuer_code="NSTNBU",
                    candidate_count=3, sources=[PTR])])
    reasoning = _draft(history=ambiguous).coverage.reasoning
    assert any("may also concern" in r and "1 of 3 candidates" in r for r in reasoning)
    assert not any("also names" in r for r in reasoning)


def test_a_prior_document_with_no_recorded_candidate_count_is_described_as_unknown():
    """The bug this pins: `PriorIntake.candidate_count` defaults to `None` for a history
    implementation that never recorded one (e.g. a graph FOR_PATIENT edge minted by
    `extract_graph`'s brain_ingest path, which builds edges with empty attrs). `None` is FALSY in
    Python, so `other.candidate_count and other.candidate_count > 1` used to fall straight to the
    unqualified "also names" wording — exactly the same text produced for a CONFIRMED single
    candidate — overstating a link that was never resolved against the registry
    (CLAUDE.md §Hard constraints 1: never overstate an identity link). An unknown count must read
    as unknown, not as "count == 1"."""
    draft = _draft(history=NoHistory())
    patient_id = draft.resolution.candidates[0].patient_id
    unknown_count = FakeHistory(intakes=[
        PriorIntake(document_id="chit_other.txt", patient_id=patient_id, issuer_code="NSTNBU",
                    sources=[PTR])])                       # candidate_count intentionally omitted
    reasoning = _draft(history=unknown_count).coverage.reasoning
    assert any("may also concern" in r and "candidate count on that document is unknown" in r
              for r in reasoning), reasoning
    assert not any("also names" in r for r in reasoning)


def test_an_ungrounded_prior_intake_cannot_be_constructed():
    """The port's docstring promises every value is grounded. A default of `[]` made that promise
    unenforceable — a non-graph implementation could raise the flag with no pointer behind it."""
    with pytest.raises(Exception, match="SourcePointer"):
        PriorIntake(document_id="d", patient_id="p")


# --- THE GUARDRAIL ----------------------------------------------------------------------


def test_history_never_changes_a_candidate_score():
    """The one way this feature could become identity resolution by the back door.

    "Candidate A has been here before" is information a human should have and a score must not
    absorb — folding it in would make the machine lean toward one identity on a prior. Scores and
    ordering must be byte-identical with and without history.
    """
    without = _draft(history=NoHistory())
    patient_id = without.resolution.candidates[0].patient_id
    rich = FakeHistory(intakes=[
        PriorIntake(document_id="chit_other.txt", patient_id=patient_id,
                    issuer_code="ZZOTHER", seen_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
                    candidate_count=1, sources=[PTR])
        for _ in range(5)])
    with_history = _draft(history=rich)

    assert [(c.patient_id, c.score, c.matched_on, c.conflicts)
            for c in without.resolution.candidates] == \
           [(c.patient_id, c.score, c.matched_on, c.conflicts)
            for c in with_history.resolution.candidates]


def test_history_does_not_reach_the_resolver_at_all():
    """Source inspection, because the property above could be satisfied by accident."""
    import inspect

    from databridge_intake import resolve

    source = inspect.getsource(resolve)
    for forbidden in ("history", "prior_intake", "ClinicHistory"):
        assert forbidden not in source, f"resolve.py mentions {forbidden!r}"


def test_an_ambiguous_document_with_history_still_returns_both_candidates():
    """Chit 3 has two candidates. Giving one of them a rich history must not narrow the list."""
    draft = _draft("chit_03_everwell_group_invitation.txt", history=NoHistory())
    first = draft.resolution.candidates[0].patient_id
    rich = FakeHistory(intakes=[
        PriorIntake(document_id="chit_prior.txt", patient_id=first, issuer_code="EVWME",
                    sources=[PTR])])
    with_history = _draft("chit_03_everwell_group_invitation.txt", history=rich)
    assert len(with_history.resolution.candidates) == 2
    assert with_history.resolution.status.value == "multiple_candidates"


# --- THE FLAG ---------------------------------------------------------------------------


def test_a_document_with_a_different_issuer_on_file_raises_the_conflict_flag():
    draft = _draft(history=NoHistory())
    patient_id = draft.resolution.candidates[0].patient_id
    conflicting = FakeHistory(intakes=[
        PriorIntake(document_id="chit_other.txt", patient_id=patient_id,
                    issuer_code="NSTNBU", sources=[PTR])])
    flagged = _draft(history=conflicting)
    assert FLAG_CONFLICTING_DOCUMENT in flagged.coverage.eligibility_flags
    assert any("another document on file" in r for r in flagged.coverage.reasoning)
    assert any("chit_other.txt" in r for r in flagged.coverage.reasoning)


def test_the_same_document_is_not_a_conflict_with_itself():
    """The current document is in the graph too. Without the exclusion every intake would flag."""
    draft = _draft(history=NoHistory())
    patient_id = draft.resolution.candidates[0].patient_id
    itself = FakeHistory(intakes=[
        PriorIntake(document_id="chit_01_meridian_referral.txt", patient_id=patient_id,
                    issuer_code="ZZOTHER", sources=[PTR])])
    assert FLAG_CONFLICTING_DOCUMENT not in _draft(history=itself).coverage.eligibility_flags


def test_a_matching_issuer_on_file_is_not_a_conflict():
    """A second document from the SAME insurer for the same package is ordinary, not a conflict."""
    draft = _draft(history=NoHistory())
    patient_id = draft.resolution.candidates[0].patient_id
    agreeing = FakeHistory(intakes=[
        PriorIntake(document_id="chit_other.txt", patient_id=patient_id,
                    issuer_code="MRDEB", sources=[PTR])])
    assert FLAG_CONFLICTING_DOCUMENT not in _draft(history=agreeing).coverage.eligibility_flags


def test_a_conflict_makes_the_coverage_partial_not_determined():
    """A flag means something still needs a human, and `status` must say so."""
    draft = _draft(history=NoHistory())
    patient_id = draft.resolution.candidates[0].patient_id
    conflicting = FakeHistory(intakes=[
        PriorIntake(document_id="chit_other.txt", patient_id=patient_id,
                    issuer_code="NSTNBU", sources=[PTR])])
    assert _draft(history=conflicting).coverage.status.value == "partial"


def test_prior_intake_and_prior_test_require_grounding():
    """An enrichment on the card must be clickable-through exactly like an extracted field."""
    with pytest.raises(Exception):
        PriorIntake(document_id="d", patient_id="p", sources=[{"not": "a pointer"}])
    ok = PriorTest(canonical="chest_xray", as_printed="Chest X-Ray", document_id="d",
                   sources=[PTR])
    assert ok.sources[0].source_id == "chit_x.txt"

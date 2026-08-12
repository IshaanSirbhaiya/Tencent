"""The graph adapter for the clinic-history port — the thing that makes the graph load-bearing.

Before Phase 6 the graph was queryable and deletable: nothing in the front-desk decision path
touched a traversal. These tests assert the traversals are correct, grounded, and that the flag they
produce genuinely comes from the graph rather than from the document being read.
"""
from __future__ import annotations

import pytest

from databridge_graph.clinic_history import GraphClinicHistory
from databridge_graph.clinic_seed import fresh_clinic_store, patient_node_id
from databridge_intake.eligibility import FLAG_CONFLICTING_DOCUMENT
from databridge_intake.history import ClinicHistory, NoHistory
from databridge_intake.paths import fixtures_dir
from databridge_intake.pipeline import run_intake
from databridge_intake.resolve import resolve

# Loh Wei Ming — named by BOTH chit 1 (Meridian) and chit 10 (Northstar).
TWO_DOCUMENT_PATIENT_NRIC = "S8010946C"


@pytest.fixture(scope="module")
def history():
    return GraphClinicHistory(fresh_clinic_store())


@pytest.fixture(scope="module")
def two_document_patient_id():
    from databridge_intake.registry import registry

    record = next(r for r in registry() if r.id_number.value == TWO_DOCUMENT_PATIENT_NRIC)
    return record.patient_id


def _text(name: str) -> str:
    return (fixtures_dir() / "chits" / name).read_text(encoding="utf-8")


def test_the_adapter_satisfies_the_port(history):
    assert isinstance(history, ClinicHistory)


# --- the traversals ---------------------------------------------------------------------


def test_prior_intakes_finds_both_documents_naming_the_patient(history, two_document_patient_id):
    found = history.prior_intakes_for(two_document_patient_id)
    documents = {p.document_id for p in found}
    assert {"chit_01_meridian_referral.txt",
            "chit_10_northstar_second_referral.txt"} <= documents


def test_the_traversal_resolves_each_documents_issuer(history, two_document_patient_id):
    """Issuer comes from the `ISSUED` edge, not from the document node's own attributes — the
    document does not carry it."""
    by_doc = {p.document_id: p for p in history.prior_intakes_for(two_document_patient_id)}
    assert by_doc["chit_01_meridian_referral.txt"].issuer_code == "MRDEB"
    assert by_doc["chit_10_northstar_second_referral.txt"].issuer_code == "NSTNBU"


def test_documents_naming_excludes_the_document_being_read(history, two_document_patient_id):
    others = history.documents_naming(two_document_patient_id,
                                      exclude_document_id="chit_01_meridian_referral.txt")
    assert "chit_01_meridian_referral.txt" not in {p.document_id for p in others}
    assert "chit_10_northstar_second_referral.txt" in {p.document_id for p in others}


def test_an_unknown_patient_returns_nothing_rather_than_raising(history):
    assert history.prior_intakes_for("pat:000000000000") == []
    assert history.tests_authorised_for("pat:000000000000") == []


def test_tests_authorised_spans_both_documents(history, two_document_patient_id):
    """Chit 1 orders a chest X-ray, HIV antibody and treadmill ECG; chit 10 orders a UFEME. All four
    are tests this patient was sent for, whichever letter carried them."""
    canonicals = {t.canonical for t in history.tests_authorised_for(two_document_patient_id)}
    assert {"chest_xray", "hiv_antibody", "treadmill_ecg", "ufeme"} <= canonicals


def test_a_since_bound_can_exclude_older_documents(history, two_document_patient_id):
    """The bi-temporal store is what makes "recently" answerable at all."""
    import datetime as dt

    future = dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc)
    assert history.tests_authorised_for(two_document_patient_id, since=future) == []


def test_every_answer_carries_the_pointers_of_the_path_it_walked(history,
                                                                 two_document_patient_id):
    """An enrichment must be clickable-through exactly like an extracted field. A traversal that
    cannot cite its path is not evidence."""
    for prior in history.prior_intakes_for(two_document_patient_id):
        assert prior.sources, prior.document_id
        for pointer in prior.sources:
            assert pointer.source_id
            assert pointer.kind in {"doc_span", "table_cell", "derived"}
    for test in history.tests_authorised_for(two_document_patient_id):
        assert test.sources, test.canonical


def test_results_are_deterministically_ordered(history, two_document_patient_id):
    """Two calls must agree — the card renders them in order and staff learn positions."""
    first = [p.document_id for p in history.prior_intakes_for(two_document_patient_id)]
    for _ in range(3):
        assert [p.document_id for p in history.prior_intakes_for(two_document_patient_id)] == first


# --- THE POINT: a front-desk flag that only a traversal can produce ---------------------


def test_the_conflict_flag_comes_from_the_graph_and_not_the_document(history):
    """The whole Phase 6 success criterion.

    Chit 1 alone says nothing about Northstar. Only the graph knows that the same patient also has a
    Northstar referral on file — so with no history the flag is absent, and with the graph it fires.
    Delete the graph and the card reverts to Phase 5 behaviour, which is exactly the property
    `NoHistory` is the default to guarantee.
    """
    text = _text("chit_01_meridian_referral.txt")
    without = run_intake(text, document_id="chit_01_meridian_referral.txt", history=NoHistory())
    with_graph = run_intake(text, document_id="chit_01_meridian_referral.txt", history=history)

    assert FLAG_CONFLICTING_DOCUMENT not in without.coverage.eligibility_flags
    assert FLAG_CONFLICTING_DOCUMENT in with_graph.coverage.eligibility_flags
    assert any("chit_10_northstar_second_referral.txt" in r
               for r in with_graph.coverage.reasoning)
    # Northstar is not mentioned anywhere on chit 1 — proof the flag is not derived from the text.
    assert "Northstar" not in text


def test_the_graph_does_not_change_identity_resolution(history):
    """Scores and ordering must be byte-identical with the graph attached."""
    text = _text("chit_01_meridian_referral.txt")
    bare = resolve(run_intake(text, document_id="c1.txt", history=NoHistory()).record.subject)
    rich = resolve(run_intake(text, document_id="c1.txt", history=history).record.subject)
    assert [(c.patient_id, c.score, c.matched_on) for c in bare.candidates] == \
           [(c.patient_id, c.score, c.matched_on) for c in rich.candidates]


def test_an_unrelated_document_is_not_flagged(history):
    """Chit 2's patient has exactly one document on file, so there is nothing to conflict with."""
    text = _text("chit_02_everwell_voucher.txt")
    draft = run_intake(text, document_id="chit_02_everwell_voucher.txt", history=history)
    assert FLAG_CONFLICTING_DOCUMENT not in draft.coverage.eligibility_flags


def test_the_flag_carries_the_pointers_of_the_traversal(history):
    """A graph-derived claim must be clickable-through exactly like an extracted field. Reducing the
    traversal to a prose string discarded the FOR_PATIENT / ISSUED / AUTHORISES pointers that are its
    only evidence, and the card then told staff "document X conflicts" with nothing behind it."""
    text = _text("chit_01_meridian_referral.txt")
    draft = run_intake(text, document_id="chit_01_meridian_referral.txt", history=history)
    findings = draft.coverage.history_findings
    assert findings, "the flag fired but carried no finding"
    for finding in findings:
        assert finding.kind == FLAG_CONFLICTING_DOCUMENT
        assert finding.document_id == "chit_10_northstar_second_referral.txt"
        assert finding.sources, finding.message
        # The ISSUED edge is what proves the OTHER document came from Northstar. Citing only the
        # issuer node would cite a row of coverage_rules.yaml, which proves only that Northstar
        # exists.
        assert any(p.kind == "doc_span" for p in finding.sources)


def test_a_document_older_than_the_window_is_not_a_live_conflict(history):
    """Two referrals from different insurers a year apart are two episodes, not a contradiction.
    Flagging every historical difference would put a permanent warning on every returning patient,
    which is how a flag stops being read."""
    import datetime as dt

    from databridge_intake.eligibility import CONFLICT_WINDOW_DAYS, determine_coverage
    from databridge_intake.resolve import resolve

    text = _text("chit_01_meridian_referral.txt")
    draft = run_intake(text, document_id="chit_01_meridian_referral.txt", history=history)
    assert FLAG_CONFLICTING_DOCUMENT in draft.coverage.eligibility_flags

    # Re-determine as if today were long after both documents.
    far_future = (dt.date(2026, 8, 11) + dt.timedelta(days=CONFLICT_WINDOW_DAYS + 30))
    later = determine_coverage(draft.record,
                               candidates=resolve(draft.record.subject).candidates,
                               today=far_future, history=history)
    assert FLAG_CONFLICTING_DOCUMENT not in later.eligibility_flags


def test_documents_carry_their_own_date_not_a_shared_epoch(history, two_document_patient_id):
    """Every document node used to share one `valid_from`, which made the bi-temporal axis
    meaningless for documents and made every seeded referral look seven months old."""
    dates = {p.document_id: p.seen_at for p in history.prior_intakes_for(two_document_patient_id)}
    chit_01 = dates["chit_01_meridian_referral.txt"]
    chit_10 = dates["chit_10_northstar_second_referral.txt"]
    assert chit_01 is not None and chit_10 is not None
    # Chit 1 is dated 4 August 2026, chit 10 is dated 9 August 2026.
    assert chit_01 < chit_10


def test_an_undetermined_coverage_still_reports_a_conflict(history):
    """The check used to sit after the early returns, so an unknown issuer skipped it entirely —
    and "we could not determine this document AND another disagrees" is strictly more useful than
    either half."""
    from databridge_intake.eligibility import determine_coverage
    from databridge_intake.resolve import resolve

    text = _text("chit_01_meridian_referral.txt")
    record = run_intake(text, document_id="chit_01_meridian_referral.txt").record
    stripped = record.model_copy(update={
        "issuer": record.issuer.model_copy(update={
            "issuer_code": record.issuer.issuer_code.model_copy(update={"value": "NOSUCH1"})})})
    coverage = determine_coverage(stripped, candidates=resolve(record.subject).candidates,
                                 history=history)
    assert coverage.status.value == "undetermined"
    assert FLAG_CONFLICTING_DOCUMENT in coverage.eligibility_flags
    assert coverage.history_findings


def test_the_snapshot_is_read_once_per_graph_generation(history, two_document_patient_id):
    """The conflict check calls in once per candidate, so a full node+edge scan per call was
    O(candidates x graph) — and worse, nodes and edges were separate reads, so different candidates
    could be evaluated against different graph versions."""
    calls = {"n": 0}
    original = history.store.open_nodes

    def counting():
        calls["n"] += 1
        return original()

    history.store.open_nodes = counting                      # type: ignore[method-assign]
    try:
        history.prior_intakes_for(two_document_patient_id)
        after_first = calls["n"]
        for _ in range(5):
            history.prior_intakes_for(two_document_patient_id)
        assert calls["n"] == after_first, "the snapshot was re-read despite no graph change"
    finally:
        history.store.open_nodes = original                  # type: ignore[method-assign]

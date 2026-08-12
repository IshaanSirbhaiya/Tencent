"""Phase 1 — the document classifier, with emphasis on when it should refuse to answer.

A misclassified document selects the wrong issuer's coverage rules; an abstention costs one staff
click. So the tests below care as much about `UNRECOGNISED` being reachable as about the nine
fixtures landing in the right bucket.
"""
from __future__ import annotations

import pytest

from databridge_intake import DocumentType, chits_dir
from databridge_intake.classify import MIN_CONFIDENCE, SIGNALS, classify


def _text(name: str) -> str:
    return (chits_dir() / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name,expected", [
    ("chit_01_meridian_referral.txt", DocumentType.INSURER_REFERRAL_LETTER),
    ("chit_02_everwell_voucher.txt", DocumentType.HEALTH_CHECKUP_VOUCHER),
    ("chit_03_everwell_group_invitation.txt", DocumentType.GROUP_INSURANCE_INVITATION),
    ("chit_04_bluepeak_underwriting.txt", DocumentType.UNDERWRITING_FOLLOWUP),
    ("chit_05_bluepeak_wellness_voucher.txt", DocumentType.HEALTH_CHECKUP_VOUCHER),
    ("chit_06_northstar_further_req.txt", DocumentType.UNDERWRITING_FOLLOWUP),
    ("chit_07_mol_authorisation_pee226.txt", DocumentType.TPA_AUTHORISATION_FORM),
    ("chit_08_mol_authorisation_vital.txt", DocumentType.TPA_AUTHORISATION_FORM),
    ("chit_09_coralharbour_hr_email.eml", DocumentType.EMPLOYER_HR_EMAIL),
])
def test_all_nine_fixtures_classify_correctly(name, expected):
    verdict = classify(_text(name))
    assert verdict.doc_type is expected, f"scores: {verdict.scores}"
    assert verdict.confidence >= MIN_CONFIDENCE


def test_empty_input_is_the_walk_in_path_not_an_error():
    for blank in ("", "   ", "\n\n"):
        assert classify(blank).doc_type is DocumentType.NO_DOCUMENT


def test_unrelated_prose_abstains():
    for junk in ("Dear Sir, the weather is fine. Regards.",
                 "Invoice 4402 for office stationery. Payment due in 30 days.",
                 "Minutes of the quarterly safety committee meeting."):
        assert classify(junk).doc_type is DocumentType.UNRECOGNISED


def test_a_near_tie_abstains_rather_than_guessing():
    """A document carrying equally strong markers of two families is when a human should look."""
    hybrid = ("Invitation for medical tests\n"
              "Please present this Voucher at the clinic.\n")
    verdict = classify(hybrid)
    assert verdict.doc_type is DocumentType.UNRECOGNISED
    assert verdict.runner_up is not None


def test_a_negative_marker_settles_what_would_otherwise_be_a_tie():
    """"Authorisation form" argues actively against "referral letter", so this is not ambiguous
    — the negative signal is evidence, not just an absence of evidence."""
    both = ("AUTHORISATION FORM FOR MEDICAL EXAMINATION\n"
            "Medical Referral Letter\n"
            "To : The Medical Examiner\n")
    assert classify(both).doc_type is DocumentType.TPA_AUTHORISATION_FORM


@pytest.mark.parametrize("thin", [
    "Please record the expiry date.",          # one weak voucher word
    "The employee spoke to HR.",               # one weak HR word
    "Subject: quarterly newsletter",           # a header line, but no header BLOCK
    "From: someone@example.com",
])
def test_thin_evidence_abstains_even_when_only_one_type_scores(thin):
    """Share-of-total alone reads 100% confidence when exactly one weak marker fires anywhere on
    the page. The absolute evidence floor is what stops that presenting as certainty."""
    assert classify(thin).doc_type is DocumentType.UNRECOGNISED


def test_an_email_needs_a_header_block_not_a_single_header_line():
    """From: and Subject: are required together — either alone appears in ordinary letters."""
    assert classify("From: a@b.c\nSubject: Medical appointment\n\nDetails follow."
                    ).doc_type is DocumentType.EMPLOYER_HR_EMAIL


def test_overlapping_patterns_are_not_double_counted():
    """"Medical Referral Letter" once matched both a specific and a general referral pattern,
    inflating its score and shifting tie-breaking without adding independent evidence."""
    once = classify("Medical Referral Letter\nTo : The Medical Examiner\n")
    assert once.scores["insurer_referral_letter"] <= 1.0 + 4 * 0.28


def test_classification_keys_on_layout_not_on_known_issuer_names():
    """A brand-new insurer's referral letter must classify on the strength of looking like a
    referral letter. Enumerating issuers here would be the same mistake as hardcoding them in
    the coverage rules."""
    novel = ("Someone Entirely New Assurance Ltd\n"
             "Code: QQ9\n"
             "Medical Referral Letter\n"
             "To : The Medical Examiner\n"
             "Examinee : A Person (S1234567A)\n"
             "Please conduct the following examination on A Person:\nChest X-Ray\n")
    assert classify(novel).doc_type is DocumentType.INSURER_REFERRAL_LETTER


def test_no_signal_pattern_mentions_a_configured_issuer_code():
    """The classifier's evidence must be layout markers, not a list of the insurers we happen to
    have onboarded — otherwise document type silently stops working for new ones."""
    from databridge_intake import rules

    codes = {i.code.upper() for i in rules().issuers}
    for sig in SIGNALS:
        for pat in (*sig.strong, *sig.weak, *sig.negative):
            assert not any(c in pat.upper() for c in codes), \
                f"{sig.doc_type.value} keys on an issuer code: {pat!r}"


def test_a_voucher_is_not_read_as_an_underwriting_memo():
    """chit_05 carries underwriting-ish words but is a voucher; the negative marker settles it."""
    verdict = classify(_text("chit_05_bluepeak_wellness_voucher.txt"))
    assert verdict.doc_type is DocumentType.HEALTH_CHECKUP_VOUCHER


def test_scores_are_exposed_for_inspection():
    """A staff-facing "why did you think that?" needs the numbers, not just the verdict."""
    verdict = classify(_text("chit_01_meridian_referral.txt"))
    assert verdict.scores and verdict.matched
    assert set(verdict.scores) == {s.doc_type.value for s in SIGNALS}

"""The identity-document refusal (T-36) — both directions of error.

`/intake` promises the system never reads identity documents or insurance e-cards. Before the file
upload path existed that promise needed no mechanism; now it does, and this file is where the
mechanism is held to it.

Two failure modes, and the fixture corpus proves both are real:

* **Too narrow** lets a photographed NRIC through. Every rule here is checked against a synthetic
  identity document that a careless implementation would miss.
* **Too broad** refuses the legitimate corpus. `chit_09` tells the patient to bring "Your
  identification card (NRIC/FIN card)", three chits print the field label "NRIC/Passport No.",
  `chit_07` offers "Work Permit / S Pass" as a checkbox option, and `chit_02` prints both "Date of
  issue" and "Policy No.". A guard that refuses those is worse than no guard: staff route around
  it, and the demo's most-inspected promise becomes a thing that gets disabled.

Every document below is synthetic. No real NRIC, FIN or passport number appears in this repo.
"""
from __future__ import annotations

import pytest

from databridge_intake.id_document_guard import (REFUSAL_CODE, screen_extracted_text,
                                                 screen_upload)
from databridge_intake.paths import fixtures_dir


# --- layer 1: what the caller declared, before a byte is extracted ----------------
@pytest.mark.parametrize("filename", [
    "nric.jpg",
    "NRIC_front.png",
    "nric-back.jpeg",
    "passport.pdf",
    "passport_bio_page.png",
    "ecard.png",
    "e-card.png",
    "insurance card.jpg",
    "member_card.png",
    "identity_card_scan.pdf",
    "id-photo.jpg",
    "medisave_card.png",
    "fin card.jpg",
])
def test_a_filename_that_names_an_identity_document_is_refused(filename):
    result = screen_upload(filename=filename, document_id=filename)
    assert result.refused, f"{filename!r} should have been refused"
    assert result.code == REFUSAL_CODE
    assert result.reason, "a refusal must name the rule that fired"


@pytest.mark.parametrize("filename", [
    "chit_01_meridian_referral.txt",
    "chit_07_mol_authorisation_pee226.pdf",
    "chit_09_coralharbour_hr_email.eml",
    "referral letter.pdf",
    "health screening voucher.png",
    "authorisation form scan.pdf",
    "underwriting_memo_2026.pdf",
    "generic_referral.pdf",
    "electronic_referral.pdf",
])
def test_an_ordinary_referring_document_filename_is_not_refused(filename):
    assert not screen_upload(filename=filename, document_id=filename).refused


def test_the_document_id_is_screened_as_well_as_the_filename():
    # The document id is what lands on the card and in the ledger. A caller that renames the file
    # to something bland while keeping the honest id is still uploading an NRIC.
    assert screen_upload(filename="scan001.jpg", document_id="nric_front").refused
    assert screen_upload(filename="nric_front.jpg", document_id="scan001").refused


def test_every_fixture_in_the_demo_corpus_passes_the_filename_guard():
    names = [p.name for p in sorted(fixtures_dir().rglob("*")) if p.is_file()]
    assert names, "the fixture corpus should not be empty"
    refused = [n for n in names if screen_upload(filename=n, document_id=n).refused]
    assert refused == []


# --- layer 2: what the extraction actually says -----------------------------------
PASSPORT_DATA_PAGE = """PASSPORT
Type/Code: P   Issuing State: SGP
Surname: TAN
Given names: WEI LING
Passport No.: E1234567X
Nationality: SINGAPORE CITIZEN
Date of birth: 12 MAR 1988
Place of birth: SINGAPORE
Date of issue: 04 JAN 2021
Date of expiry: 03 JAN 2031
Authority: IMMIGRATION & CHECKPOINTS AUTHORITY
P<SGPTAN<<WEI<LING<<<<<<<<<<<<<<<<<<<<<<<<<<
E1234567X8SGP8803129F3101034<<<<<<<<<<<<<<06
"""

NRIC_CARD_FACE = """REPUBLIC OF SINGAPORE
IDENTITY CARD
No.: S8010946C
Name: LOH WEI MING
Race: CHINESE
Date of birth: 15-08-1980
Sex: M
Country of birth: SINGAPORE
"""

INSURANCE_E_CARD = """Bluepeak Prosperity Life
MEDICAL CARD
Member Name: LOH AMIR
Card No.: 4213 9987 0011
Policy No.: BLP301158
Valid Thru: 12/28
"""


@pytest.mark.parametrize("text,expected_rule", [
    (PASSPORT_DATA_PAGE, "machine_readable_zone"),
    (NRIC_CARD_FACE, "nric_card_face"),
    (INSURANCE_E_CARD, "insurance_e_card"),
])
def test_an_identity_document_is_refused_from_its_extracted_text(text, expected_rule):
    result = screen_extracted_text(text)
    assert result.refused
    assert result.code == REFUSAL_CODE
    assert result.reason == expected_rule


def test_a_passport_page_is_refused_even_without_its_mrz():
    # A cropped or badly-lit photo often loses the MRZ. The data page's own field labels are the
    # backstop, and they need two corroborating markers so "NRIC/Passport No." on a form cannot
    # fire the rule.
    without_mrz = "\n".join(ln for ln in PASSPORT_DATA_PAGE.splitlines()
                            if not ln.startswith(("P<SGP", "E1234567X8SGP")))
    result = screen_extracted_text(without_mrz)
    assert result.refused and result.reason == "passport_data_page"


def test_ocr_that_loses_the_spacing_is_still_refused():
    # The bundled OCR engine renders the sample form's letterhead as `SYNTHETIC/FICTIONALDOCUMENT`.
    # A rule written with spaces in it would be defeated by the very engine it polices — the same
    # failure the question guardrail had with newlines.
    squashed = NRIC_CARD_FACE.replace(" ", "")
    assert screen_extracted_text(squashed).refused


@pytest.mark.parametrize("name", [p.name for p in sorted(
    (fixtures_dir() / "chits").glob("*")) if p.is_file()])
def test_the_whole_chit_corpus_survives_the_text_guard(name):
    text = (fixtures_dir() / "chits" / name).read_text(encoding="utf-8")
    result = screen_extracted_text(text)
    assert not result.refused, f"{name} was refused by rule {result.reason!r}"


def test_the_phrases_that_make_the_corpus_dangerous_are_not_on_their_own_enough():
    # Each of these appears verbatim in a legitimate fixture. Named individually so a future
    # tightening of the rules fails on the specific phrase it over-matched.
    for phrase in ("Your identification card (NRIC/FIN card)",
                   "NRIC/Passport No. : S8001691D",
                   "Work Permit / S Pass: ___",
                   "Policy No.: EVW563298",
                   "Date of issue: 16 July 2026"):
        assert not screen_extracted_text(phrase).refused, phrase


def test_empty_input_is_not_an_accusation():
    assert not screen_extracted_text("").refused
    assert not screen_extracted_text("   \n\n ").refused
    assert not screen_upload().refused

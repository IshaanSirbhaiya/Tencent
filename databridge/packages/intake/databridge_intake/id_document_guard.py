"""Deterministic refusal of identity-document uploads — **before** anything is read or stored.

`/intake` carries a banner promising that this system never reads identity documents or insurance
e-cards. Text upload could not break that promise: there was no way to hand it a photograph. A
file-upload path can, so the promise now needs a mechanism, and this is it.

Modelled on ``guardrails.check_question``, which refuses identity-verification *questions* before
the model is called: pure deterministic matching, no model in the loop, a fixed refusal code, and a
named rule for every refusal so a miss can be traced to the rule that missed it rather than to a
prompt's mood.

**Two layers, because one cannot do the job.**

* ``screen_upload`` runs before a single character is extracted. All it can honestly see is what
  the caller *named* the thing — the filename and the document id. That catches the common,
  careless case (`nric_front.jpg`, `ecard.png`) at zero cost and with zero extraction. The declared
  media type is deliberately not consulted: "image/jpeg" is what most legitimate scans are.
* ``screen_extracted_text`` runs on the extracted text, before it is stored, before it reaches a
  model, and before any ``IntakeRecord`` exists. This is the layer that actually recognises a
  photographed NRIC or a screenshotted e-card. On a hit the caller drops the text on the floor:
  nothing is persisted, nothing is returned, no record is built. "Extracts nothing" is a statement
  about what survives the call, and layer 2 is where it is made true.

**Why image geometry is NOT used.** An ID-1 card is 85.6 × 54 mm — aspect 1.586. Refusing images
near that ratio also refuses 3:2 (1.500) and 16:10 (1.600) captures, which is most phone photos
and every laptop screenshot; a front desk photographing a paper referral in landscape would be
turned away. Aspect ratio cannot separate a card from a landscape capture of a letter, so it is
not used at all rather than used badly. Layer 2 is the control that catches a photographed card.

**Both directions of error are real, and the fixtures prove it.** A rule keyed on the words
"identity card", "passport" or "card" refuses the legitimate corpus: `chit_09` tells the patient to
bring "Your identification card (NRIC/FIN card)", three chits print the field label
"NRIC/Passport No.", `chit_07` offers "Work Permit / S Pass" as a checkbox option, and `chit_02`
prints both "Date of issue" and "Policy No.". Every rule below therefore keys on a marker that
appears on the *face of an identity document* and requires corroboration — never on a mention.
``test_id_document_guard.py`` asserts the whole fixture corpus passes.

**OCR mangles spacing**, so matching happens against a whitespace-stripped view: RapidOCR renders
the sample form's letterhead as ``SYNTHETIC/FICTIONALDOCUMENT``. A rule written with spaces in it
would be defeated by the very engine it is meant to police — the same failure the question
guardrail had with newlines.
"""
from __future__ import annotations

import re
from typing import NamedTuple, Optional

REFUSAL_CODE = "IDENTITY_DOCUMENT_REFUSED"

REFUSAL_TEXT = (
    "This looks like an identity document or an insurance e-card, and reading those is outside "
    "what this system does. Nothing was extracted from it and nothing has been stored.\n\n"
    "Identity is checked in person by clinic staff against the physical NRIC, FIN or passport. "
    "Upload the referring document instead — the referral letter, voucher, authorisation form or "
    "HR email that says what the patient is here for."
)


class GuardResult(NamedTuple):
    refused: bool
    code: str = ""
    reason: str = ""       # which rule fired — for the log and the tests, not for staff
    message: str = ""


# --- layer 1: what the caller declared -------------------------------------------
# Whole filename tokens that name an identity document on their own. Deliberately short: each of
# these is a word nobody puts in the name of a referral letter.
_FILENAME_TOKENS = frozenset({
    "nric", "passport", "ecard", "idcard", "identitycard", "identificationcard",
    "photoid", "medisave", "workpermit", "employmentpass",
})

# Token pairs — either adjacent in the filename or written as one word. "insurance card",
# "id photo" and "nric_scan" all name the same upload; the separator is not the signal.
_QUALIFIERS = frozenset({"id", "ic", "fin", "identity", "identification", "insurance", "medical",
                         "member", "membership", "health", "policy", "e"})
_ARTEFACTS = frozenset({"card", "scan", "photo", "front", "back", "selfie"})
_SUBJECTS = frozenset({"nric", "fin", "passport", "ic", "identity", "identification"})
_CAPTURES = frozenset({"scan", "photo", "front", "back", "copy", "pic", "img", "selfie"})

_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+")


def _tokens(name: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split((name or "").lower()) if t]


def _filename_names_an_identity_document(name: str) -> Optional[str]:
    tokens = _tokens(name)
    if not tokens:
        return None
    joined = "".join(tokens)
    for token in _FILENAME_TOKENS:
        if token in tokens or token in joined:
            return f"filename token {token!r}"
    for a, b in zip(tokens, tokens[1:]):
        if a in _QUALIFIERS and b in _ARTEFACTS:
            return f"filename names a {a} {b}"
        if a in _SUBJECTS and b in _CAPTURES:
            return f"filename names a {b} of a {a}"
    return None


def screen_upload(*, filename: str = "", document_id: str = "") -> GuardResult:
    """Screen an upload BEFORE extraction, on what the caller declared.

    Both the filename and the caller-supplied ``document_id`` are screened: the document id is what
    ends up on the card and in the ledger, so a caller that launders `nric_front.jpg` into a bland
    filename while keeping the honest id is still refused.
    """
    for label, value in (("filename", filename), ("document_id", document_id)):
        reason = _filename_names_an_identity_document(value)
        if reason is not None:
            return GuardResult(True, REFUSAL_CODE, f"{label}: {reason}", REFUSAL_TEXT)
    return GuardResult(False)


# --- layer 2: what the extraction actually says ----------------------------------
_FLAT = re.compile(r"\s+")


def _flatten(text: str) -> str:
    """Uppercase with ALL whitespace removed — the view every phrase rule matches against."""
    return _FLAT.sub("", text or "").upper()


# The machine-readable zone of a passport or ID card: two or three long lines drawn from
# ``[A-Z0-9<]`` with filler chevrons. Nothing else in a clinic's paperwork looks like this, so it
# is the one rule that needs no corroboration.
_MRZ_LINE = re.compile(r"^[A-Z0-9<]{28,50}$")
_MRZ_TYPE = re.compile(r"[IPAC]<[A-Z]{3}")

# Markers printed on the face of a Singapore identity card, and on nothing a clinic receives.
_ID_CARD_CORROBORATION = ("REPUBLICOFSINGAPORE", "NATIONALREGISTRATION", "COUNTRYOFBIRTH",
                          "DATEOFISSUE", "RACE:", "SEX:")

# A passport data page names its own fields. "PASSPORT" alone is a field LABEL on three of the ten
# chit fixtures ("NRIC/Passport No."), so two corroborating markers are required — none of the
# fixtures carries even one.
_PASSPORT_CORROBORATION = ("DATEOFEXPIRY", "PLACEOFBIRTH", "COUNTRYOFBIRTH", "GIVENNAMES",
                           "SURNAME", "ISSUINGSTATE", "TYPE/CODE", "HOLDERSSIGNATURE",
                           "HOLDER'SSIGNATURE", "AUTHORITY")

# An insurance card names itself AND carries a card-level identifier. "Policy No." alone appears on
# two legitimate vouchers, and "card" alone appears in an HR email telling the patient what to
# bring, so neither half is sufficient on its own.
_CARD_FACE = ("MEDICALCARD", "MEMBERCARD", "MEMBERSHIPCARD", "HEALTHCARD", "INSURANCECARD",
              "E-CARD", "ECARD", "POLICYCARD", "CARDHOLDER", "MEDISAVECARD", "PANELCARD")
_CARD_IDENTIFIER = ("VALIDTHRU", "VALIDTHROUGH", "CARDNO", "CARDNUMBER", "MEMBERNO",
                    "MEMBERSHIPNO", "POLICYNO", "GROUPNO", "PLANCODE", "CERTIFICATENO")


def _count(flat: str, markers: tuple[str, ...]) -> int:
    return sum(1 for m in markers if m in flat)


def screen_extracted_text(text: str) -> GuardResult:
    """Screen extracted text BEFORE it is stored or fed to anything.

    The caller must discard the text on a refusal. Returning ``refused=True`` and then storing the
    text anyway would make this function decoration: the refusal is not the response code, it is
    the fact that nothing survives.
    """
    flat = _flatten(text)
    if not flat:
        return GuardResult(False)

    for line in (text or "").splitlines():
        stripped = _FLAT.sub("", line).upper()
        if _MRZ_LINE.match(stripped) and stripped.count("<") >= 3:
            return GuardResult(True, REFUSAL_CODE, "machine_readable_zone", REFUSAL_TEXT)
    if _MRZ_TYPE.search(flat):
        return GuardResult(True, REFUSAL_CODE, "machine_readable_zone", REFUSAL_TEXT)

    if "NATIONALREGISTRATIONIDENTITYCARD" in flat:
        return GuardResult(True, REFUSAL_CODE, "nric_card_face", REFUSAL_TEXT)
    if "IDENTITYCARD" in flat and _count(flat, _ID_CARD_CORROBORATION) >= 1:
        return GuardResult(True, REFUSAL_CODE, "nric_card_face", REFUSAL_TEXT)

    if "PASSPORT" in flat and _count(flat, _PASSPORT_CORROBORATION) >= 2:
        return GuardResult(True, REFUSAL_CODE, "passport_data_page", REFUSAL_TEXT)

    if _count(flat, _CARD_FACE) >= 1 and _count(flat, _CARD_IDENTIFIER) >= 1:
        return GuardResult(True, REFUSAL_CODE, "insurance_e_card", REFUSAL_TEXT)

    return GuardResult(False)

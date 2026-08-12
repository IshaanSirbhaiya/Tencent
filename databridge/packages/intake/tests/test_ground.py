"""Phase 1 — the grounding wall.

This is the anti-hallucination check the Innovation and Governance criteria rest on, so the tests
are weighted toward what must be REFUSED. A wall that passes everything real but also passes
plausible fabrications is not a wall.
"""
from __future__ import annotations

import pytest

from databridge_intake import chits_dir
from databridge_intake.ground import (
    FUZZY_THRESHOLD,
    SHORT_TOKEN_LEN,
    TOKEN_FLOOR,
    ground,
    locate,
)

CHIT1 = "chit_01_meridian_referral.txt"
CHIT2 = "chit_02_everwell_voucher.txt"


def _text(name: str = CHIT1) -> str:
    return (chits_dir() / name).read_text(encoding="utf-8")


# --- values that are genuinely on the page -------------------------------------------
@pytest.mark.parametrize("value,kind", [
    ("MRDEB", "id"),
    ("MRD707314", "id"),
    ("S8010946C", "id"),
    ("Loh Wei Ming", "name"),
    ("Coral Harbour Pte Ltd", "name"),
    ("Meridian Life Assurance Pte Ltd", "name"),
    ("Chest X-Ray", "text"),
    ("Treadmill ECG", "text"),
    ("Meridian Life# 10 - HIV Antibody Test", "text"),
    ("4 August 2026", "date"),
    ("The Medical Examiner", "text"),
])
def test_real_values_ground(value, kind):
    r = locate(_text(), value, kind)
    assert r.grounded, f"{value!r} should ground ({r.reason})"
    start, end = r.span
    assert 0 <= start < end <= len(_text())


def test_the_span_actually_contains_the_value():
    """A pointer that does not resolve is decoration. This is the property the whole story rests
    on, so it is asserted directly rather than inferred from the score."""
    text = _text()
    for value in ["MRDEB", "MRD707314", "S8010946C", "Loh Wei Ming", "Chest X-Ray"]:
        r = locate(text, value, "text" if " " in value else "id")
        sliced = text[r.span[0]:r.span[1]]
        assert sliced.lower().replace(" ", "") == value.lower().replace(" ", ""), \
            f"span for {value!r} sliced to {sliced!r}"


# --- plausible fabrications that must be refused --------------------------------------
@pytest.mark.parametrize("value,kind,why", [
    ("MRD707315", "id", "one digit off the real policy number"),
    ("S8010946D", "id", "one letter off the real NRIC"),
    ("T2221854H", "id", "a real NRIC, but from a different document"),
    ("Loh Wei Mingus", "name", "surname extended — mean score 0.93 hides one bad token"),
    ("Loh Wei Min", "name", "surname truncated — a different person in this registry"),
    ("Loh Wai Ming", "name", "one particle changed"),
    ("Tang Wei Ming", "name", "family name changed"),
    ("Full Blood Count", "text", "a real test, not on this letter"),
    ("Colonoscopy", "text", "not mentioned at all"),
    ("5 August 2026", "date", "one day off the real date"),
    ("man", "name", "a fragment that occurs inside longer words"),
])
def test_fabrications_are_refused(value, kind, why):
    r = locate(_text(), value, kind)
    assert not r.grounded, f"{value!r} leaked (score {r.score:.2f}) — {why}"
    assert r.reason


def test_substring_containment_is_not_grounding():
    """`"man" in "management"` is True. Grounding by containment would make the wall useless."""
    text = "The Employee Benefit Department manages underwriting."
    assert not locate(text, "man", "name").grounded
    assert not locate(text, "age", "name").grounded
    assert locate(text, "manages", "name").grounded


def test_short_tokens_must_match_exactly():
    """Edit-distance ratios are meaningless on short tokens: ratio('min','ming') is 0.86, over any
    sane floor. Singaporean name particles are exactly this short, and Min/Ming are two people."""
    text = "Patient: Loh Wei Ming"
    assert locate(text, "Loh Wei Ming", "name").grounded
    for near in ("Loh Wei Min", "Loh Wai Ming", "Lah Wei Ming"):
        assert not locate(text, near, "name").grounded, f"{near!r} leaked"


def test_a_single_bad_token_sinks_an_otherwise_perfect_name():
    text = "Examinee : Kumar Sheng Yang (S8001691D)"
    assert locate(text, "Kumar Sheng Yang", "name").grounded
    assert not locate(text, "Kumar Sheng Yangzi", "name").grounded


def test_numbers_do_not_collide_with_dates():
    # 2026 appears inside the date; a fabricated quantity of 2026 must not ground against it.
    text = "Date of Issue : 02 Jun 2026. Amount payable: 340."
    assert locate(text, "340", "number").grounded
    assert not locate(text, "2026", "number").grounded


def test_dates_match_across_printed_formats():
    """The voucher prints its appointment date inside a form box as '30 / 07 / 2026'."""
    text = _text(CHIT2)
    assert locate(text, "30 / 07 / 2026", "date").grounded
    assert locate(text, "30/07/2026", "date").grounded
    assert locate(text, "2026-07-30", "date").grounded
    assert not locate(text, "31/07/2026", "date").grounded


def test_id_matching_tolerates_spacing_but_not_content():
    text = "NRIC : S8010946C"
    assert locate(text, "S 8010946 C", "id").grounded      # same id, spaced
    assert locate(text, "s8010946c", "id").grounded        # same id, lowercased
    assert not locate(text, "S8010946G", "id").grounded    # different checksum letter


@pytest.mark.parametrize("value", ["", "   ", "x"])
def test_degenerate_values_are_refused(value):
    assert not locate(_text(), value, "name").grounded


# --- the GroundedValue wrapper --------------------------------------------------------
def test_ground_returns_a_resolvable_pointer():
    text = _text()
    gv, reason = ground(text, "MRD707314", document_id=CHIT1, kind="id")
    assert reason is None and gv is not None
    p = gv.pointer
    assert p.kind == "doc_span" and p.document_id == CHIT1
    assert text[p.char_start:p.char_end] == "MRD707314"


def test_ground_returns_a_reason_naming_the_field_on_refusal():
    gv, reason = ground(_text(), "MRD999999", document_id=CHIT1, kind="id",
                        field_path="issuer.policy_number")
    assert gv is None
    assert reason.startswith("issuer.policy_number:")
    assert "MRD999999" in reason


def test_the_thresholds_actually_gate_behaviour():
    """Asserting the constants are in range proves nothing. These assert they DECIDE something:
    a text value just under the bar is refused and one just over it grounds."""
    assert 0 < TOKEN_FLOOR <= FUZZY_THRESHOLD <= 1 and SHORT_TOKEN_LEN >= 3
    src = "Complete Physical Examination scheduled"
    assert locate(src, "Complete Physical Examination", "text").grounded        # exact
    assert locate(src, "Complete Physical Examinaton", "text").grounded         # 1 typo, over bar
    assert not locate(src, "Complete Spinal Examination", "text").grounded      # 1 word, under bar
    # short tokens are governed by SHORT_TOKEN_LEN, not by the ratio thresholds
    assert not locate("Loh Wei Ming", "Loh Wei Ling", "name").grounded


# --- names get no fuzzy tolerance at all ----------------------------------------------
# Review finding: "Lim Arjuna" grounded against a voucher reading "Lim Arjun" at score 0.96.
# Any token-based fuzzy rule lets a one-character name change through, and in a 300-patient
# registry of overlapping name particles that is a different person on the screen.
@pytest.mark.parametrize("near", [
    "Lim Arjuna",       # one character added
    "Lim Arju",         # one character removed
    "Lin Arjun",        # one character changed in the family name
    "Arjun Lim",        # right tokens, wrong order -> not contiguous
])
def test_near_miss_names_are_refused(near):
    text = (chits_dir() / CHIT2).read_text(encoding="utf-8")
    r = locate(text, near, "name")
    assert not r.grounded, f"{near!r} leaked (score {r.score:.2f})"


def test_exact_names_still_ground():
    text = (chits_dir() / CHIT2).read_text(encoding="utf-8")
    for real in ("Lim Arjun", "Everwell Insurance Group"):
        assert locate(text, real, "name").grounded


def test_name_tokens_must_be_contiguous():
    """Both tokens appearing somewhere on the page does not mean the page names that person."""
    text = "Loh Wei Ming attended. Separately, Ang Kai Ming was scheduled."
    assert locate(text, "Loh Wei Ming", "name").grounded
    assert not locate(text, "Loh Ming", "name").grounded
    assert not locate(text, "Loh Kai Ming", "name").grounded


def test_free_text_keeps_fuzzy_tolerance():
    """The strictness is scoped to names — descriptive strings may still drift slightly, which is
    what the `text` kind is for."""
    text = "Please arrange a Complete Physical Examination for the candidate."
    assert locate(text, "Complete Physical Examination", "text").grounded
    assert locate(text, "Complete Physical Examinaton", "text").grounded   # one typo
    assert not locate(text, "Complete Mental Examination", "text").grounded

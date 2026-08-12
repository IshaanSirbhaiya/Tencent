"""Phase 0 — the fixture corpus exists, is verbatim, and keeps the signals we extract from.

The chit letters are the eval set. If a fixture drifts from the source document, the grounding
wall stops being able to relocate values in it and Phase 1's anti-fabrication proof quietly
becomes vacuous — so these checks guard the corpus itself, not just its presence.
"""
from __future__ import annotations

import json

import pytest

from databridge_intake import chits_dir, fixtures_dir

CHITS = [
    "chit_01_meridian_referral.txt",
    "chit_02_everwell_voucher.txt",
    "chit_03_everwell_group_invitation.txt",
    "chit_04_bluepeak_underwriting.txt",
    "chit_05_bluepeak_wellness_voucher.txt",
    "chit_06_northstar_further_req.txt",
    "chit_07_mol_authorisation_pee226.txt",
    "chit_08_mol_authorisation_vital.txt",
    "chit_09_coralharbour_hr_email.eml",
    # Phase 6: a SECOND document for Loh Wei Ming (already on chit 1) from a different insurer.
    # It exists so the cross-document conflict flag has a real case — the pipeline reads one
    # document at a time and cannot see this; only a graph traversal can.
    "chit_10_northstar_second_referral.txt",
]

TICKED, EMPTY = "☒", "☐"


def _text(name: str) -> str:
    return (chits_dir() / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", CHITS)
def test_chit_exists_and_is_non_trivial(name):
    path = chits_dir() / name
    assert path.exists(), f"missing fixture {name}"
    assert len(path.read_text(encoding="utf-8")) > 500


def test_walkin_fixture_is_a_first_class_path():
    data = json.loads((fixtures_dir() / "walkin" / "walkin_01.json").read_text(encoding="utf-8"))
    assert data["channel"] == "walk_in"
    assert data["document_id"] == "walkin_01"


@pytest.mark.parametrize("name,ticked,empty", [
    ("chit_05_bluepeak_wellness_voucher.txt", 1, 2),    # WELL2 of WELL1/2/3
    ("chit_07_mol_authorisation_pee226.txt", 4, 23),
    ("chit_08_mol_authorisation_vital.txt", 1, 3),
])
def test_ballot_boxes_survive_extraction(name, ticked, empty):
    """The checkbox state IS the package signal — losing it would silently gut chits 5/7/8."""
    text = _text(name)
    assert text.count(TICKED) == ticked
    assert text.count(EMPTY) == empty


def test_chit_05_ticks_well2_and_not_the_other_tiers():
    text = _text("chit_05_bluepeak_wellness_voucher.txt")
    assert f"{TICKED}  WELL2" in text
    assert f"{EMPTY}  WELL1" in text
    assert f"{EMPTY}  WELL3" in text


def test_chit_07_ticks_pee226_and_not_pee224_or_225():
    text = _text("chit_07_mol_authorisation_pee226.txt")
    assert f"{TICKED}  PEE226" in text
    assert f"{EMPTY}  PEE224" in text
    assert f"{EMPTY}  PEE225" in text


def test_chit_08_has_no_test_code_section():
    """Its Section B is absent entirely — coverage must come back undetermined, not guessed."""
    text = _text("chit_08_mol_authorisation_vital.txt")
    assert "PEE224" not in text and "PEE225" not in text and "PEE226" not in text


@pytest.mark.parametrize("name,code", [
    ("chit_01_meridian_referral.txt", "MRDEB"),
    ("chit_02_everwell_voucher.txt", "EVWPA"),
    ("chit_03_everwell_group_invitation.txt", "EVWME"),
    ("chit_04_bluepeak_underwriting.txt", "BLPDE"),
    ("chit_05_bluepeak_wellness_voucher.txt", "BLPHS"),
    ("chit_06_northstar_further_req.txt", "NSTNBU"),
    ("chit_07_mol_authorisation_pee226.txt", "MOL0199VME"),
    ("chit_08_mol_authorisation_vital.txt", "MOL0199VME"),
    ("chit_09_coralharbour_hr_email.eml", "MRDEB"),
])
def test_each_chit_carries_its_issuer_code_verbatim(name, code):
    assert code in _text(name)


@pytest.mark.parametrize("name,nric", [
    ("chit_01_meridian_referral.txt", "S8010946C"),
    ("chit_02_everwell_voucher.txt", "T2221854H"),
    ("chit_04_bluepeak_underwriting.txt", "T1800429G"),
    ("chit_06_northstar_further_req.txt", "S2915369H"),
    ("chit_07_mol_authorisation_pee226.txt", "S8001691D"),
    ("chit_08_mol_authorisation_vital.txt", "T7605473J"),
])
def test_chits_that_carry_an_nric_carry_it_verbatim(name, nric):
    assert nric in _text(name)


@pytest.mark.parametrize("name", [
    "chit_03_everwell_group_invitation.txt",
    "chit_05_bluepeak_wellness_voucher.txt",
    "chit_09_coralharbour_hr_email.eml",
])
def test_chits_without_an_nric_really_have_none(name):
    """These three drive the ambiguous-resolution cases — if an NRIC crept in, the eval is fake."""
    import re
    assert not re.search(r"\b[STFGM]\d{7}[A-Z]\b", _text(name))


def test_every_chit_is_marked_synthetic():
    for name in CHITS:
        assert "SYNTHETIC" in _text(name) or "Synthetic sample document" in _text(name)


def test_no_stray_extra_chits():
    found = sorted(p.name for p in chits_dir().iterdir() if p.is_file())
    assert found == sorted(CHITS)

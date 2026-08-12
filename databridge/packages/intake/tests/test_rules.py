"""Phase 0 — the coverage rules table loads, validates, and matches printed test names."""
from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from databridge_intake import RulesError, load_rules, rules
from databridge_intake.rules import CoverageRules, _norm, _squash

CHIT_ISSUER_CODES = ["MRDEB", "EVWPA", "EVWME", "BLPDE", "BLPHS", "NSTNBU", "MOL0199VME"]


# --- loading & validation ---------------------------------------------------------
def test_rules_load_and_cache():
    assert rules() is rules()
    assert rules().version == 1


def test_all_seven_chit_issuer_codes_resolve():
    for code in CHIT_ISSUER_CODES:
        assert rules().issuer(code) is not None, f"{code} has no rule"


def test_issuer_lookup_is_case_insensitive_and_whitespace_tolerant():
    assert rules().issuer(" mrdeb ").code == "MRDEB"
    assert rules().issuer(None) is None
    assert rules().issuer("NOPE") is None


def test_missing_rules_file_raises(tmp_path):
    with pytest.raises(RulesError, match="not found"):
        load_rules(tmp_path / "nope.yaml")


def _minimal(**overrides) -> dict:
    base = {
        "version": 1,
        "issuers": [{"code": "AAA", "name": "A", "kind": "insurer", "billing_party": "insurer"}],
        "packages": [{"id": "r1", "issuer_code": "AAA", "package_code": "P1", "name": "P",
                      "billing": {"covered_by": "insurer"}, "includes_tests": ["t_one"]}],
        "self_pay": {"id": "sp", "name": "Self pay", "billing": {"covered_by": "patient"}},
        "test_catalogue": [{"canonical": "t_one", "category": "laboratory", "aliases": ["Test One"]}],
    }
    base.update(overrides)
    return base


def _write(tmp_path, data) -> object:
    p = tmp_path / "rules.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def test_package_referencing_unknown_issuer_is_rejected(tmp_path):
    data = _minimal()
    data["packages"][0]["issuer_code"] = "ZZZ"
    with pytest.raises(RulesError, match="unknown issuer"):
        load_rules(_write(tmp_path, data))


def test_package_referencing_unknown_test_is_rejected(tmp_path):
    data = _minimal()
    data["packages"][0]["includes_tests"] = ["ghost_test"]
    with pytest.raises(RulesError, match="unknown test"):
        load_rules(_write(tmp_path, data))


def test_unselectable_package_is_rejected(tmp_path):
    # no package_code and not document-defined => nothing could ever match it
    data = _minimal()
    data["packages"][0]["package_code"] = None
    with pytest.raises((RulesError, ValidationError), match="never be selected"):
        load_rules(_write(tmp_path, data))


def test_package_cannot_be_both_coded_and_document_defined(tmp_path):
    # Two selection paths into one rule; which wins would depend on lookup order.
    data = _minimal()
    data["packages"][0]["tests_from_document"] = True
    with pytest.raises((RulesError, ValidationError), match="one way or the other"):
        load_rules(_write(tmp_path, data))


def test_duplicate_package_id_is_rejected(tmp_path):
    data = _minimal()
    data["packages"].append(dict(data["packages"][0]))
    with pytest.raises(RulesError, match="duplicate package rule id"):
        load_rules(_write(tmp_path, data))


def test_ambiguous_alias_across_two_tests_is_rejected(tmp_path):
    # An alias claimed by two canonicals would resolve by dict order — that is not a rule.
    data = _minimal()
    data["test_catalogue"].append(
        {"canonical": "t_two", "category": "laboratory", "aliases": ["Test One"]})
    with pytest.raises(RulesError, match="ambiguous alias|claimed by both"):
        load_rules(_write(tmp_path, data))


def test_shipped_rules_pass_their_own_validation():
    # the real file, not a fixture — a typo in production config is a silent wrong answer
    assert isinstance(load_rules(), CoverageRules)


# --- package resolution -----------------------------------------------------------
def test_named_package_lookup():
    pkg = rules().package("BLPHS", "WELL2")
    assert pkg is not None and pkg.id == "rule_bluepeak_well2"
    assert pkg.eligibility.age_min == 40 and pkg.eligibility.age_max == 59
    assert "resting_ecg" in pkg.includes_tests


def test_unknown_package_code_for_a_known_issuer_returns_none():
    # Better to return nothing (=> "undetermined", ask a human) than to fall back to a default.
    assert rules().package("BLPHS", "WELL9") is None


def test_no_package_code_falls_back_to_the_document_defined_rule():
    pkg = rules().package("MRDEB", None)
    assert pkg is not None and pkg.tests_from_document is True


def test_issuer_without_a_document_defined_rule_returns_none():
    # MOL0199VME only has coded packages; a form with no code ticked must be undetermined.
    assert rules().package("MOL0199VME", None) is None


@pytest.mark.parametrize("age,expected", [(39, False), (40, True), (59, True), (60, False)])
def test_well2_age_band_boundaries(age, expected):
    assert rules().package("BLPHS", "WELL2").eligibility.covers_age(age) is expected


@pytest.mark.parametrize("age,expected", [(24, False), (25, True), (49, True), (50, False)])
def test_pee226_age_band_boundaries(age, expected):
    assert rules().package("MOL0199VME", "PEE226").eligibility.covers_age(age) is expected


def test_open_ended_bands():
    assert rules().package("BLPHS", "WELL1").eligibility.covers_age(1) is True     # age_max only
    assert rules().package("BLPHS", "WELL3").eligibility.covers_age(99) is True    # age_min only


def test_eligibility_describe_is_human_readable():
    assert rules().package("BLPHS", "WELL2").eligibility.describe() == "ages 40-59"
    assert rules().package("BLPHS", "WELL3").eligibility.describe() == "ages 60 and above"


# --- test-name matching -----------------------------------------------------------
@pytest.mark.parametrize("printed,canonical", [
    # chit 1 — including the awkward vendor-prefixed name
    ("Chest X-Ray", "chest_xray"),
    ("Meridian Life# 10 - HIV Antibody Test", "hiv_antibody"),
    ("Treadmill ECG", "treadmill_ecg"),
    # chit 2
    ("Medical Examination", "medical_examination"),
    ("Lipid Profile", "lipid_profile"),
    ("UFEME", "ufeme"),
    ("Resting ECG", "resting_ecg"),
    # chit 4
    ("Full Medical Examination", "medical_examination"),
    ("Anti-HIV Test (BP#HIV)", "hiv_antibody"),
    # chit 5
    ("Complete History Taking", "history_taking"),
    ("BMI and Fat Composition", "bmi_fat_composition"),
    ("Cholesterol screening", "lipid_profile"),
    ("Medical Report Consultation", "medical_report_consultation"),
    # chit 7
    ("Electrocardiogram (ECG)", "resting_ecg"),
    ("MMR Dose 1 Vaccination (if applicable)", "vacc_mmr_dose_1"),
    # formatting drift
    ("  chest xray  ", "chest_xray"),
    ("CXR", "chest_xray"),
])
def test_printed_test_names_map_to_the_catalogue(printed, canonical):
    m = rules().match_test(printed)
    assert m.matched, f"{printed!r} did not match (best score {m.score})"
    assert m.canonical == canonical


def test_unmatched_test_is_reported_not_guessed():
    # Chit 4 lists administrative requirements that are not clinical tests. They must surface
    # to staff as unmatched, not be coerced into the nearest catalogue entry.
    m = rules().match_test("Documentary proof of residential address")
    assert not m.matched and m.canonical is None
    assert m.as_printed == "Documentary proof of residential address"


def test_empty_test_name_is_unmatched():
    assert not rules().match_test("   ").matched


def test_match_tests_preserves_order_and_length():
    printed = ["Chest X-Ray", "Nonsense Test", "UFEME"]
    out = rules().match_tests(printed)
    assert [m.as_printed for m in out] == printed
    assert [m.matched for m in out] == [True, False, True]


def test_every_test_printed_across_the_chits_is_in_the_catalogue():
    """The catalogue must cover the real corpus — this is the coverage bar for Phase 0."""
    printed = [
        "Chest X-Ray", "Meridian Life# 10 - HIV Antibody Test", "Treadmill ECG",
        "Medical Examination", "Lipid Profile", "UFEME", "Resting ECG",
        "Adult Medical Examination", "Full Medical Examination", "Anti-HIV Test (BP#HIV)",
        "Complete History Taking", "Complete Physical Examination", "BMI and Fat Composition",
        "Blood pressure measurement", "Full Blood Count", "Cholesterol screening",
        "Liver function screening", "Kidney function screening", "Thyroid function screening",
        "Medical Report Consultation", "Liver Cancer Marker",
        "Urine Examination and Microscopy", "Electrocardiogram (ECG)",
        "MMR Dose 1 Vaccination", "GHQ 60 Questionnaire",
    ]
    unmatched = [p for p in printed if not rules().match_test(p).matched]
    assert not unmatched, f"catalogue is missing: {unmatched}"


def test_norm_preserves_word_boundaries_for_fuzzy_scoring():
    assert _norm("Chest X-Ray") == "chest x ray"
    assert _norm("  CHEST   x-ray!! ") == "chest x ray"


def test_squash_is_hyphen_blind():
    assert _squash("Chest X-Ray") == _squash("chest xray") == _squash("CHEST X RAY") == "chestxray"


@pytest.mark.parametrize("spelling", ["Chest X-Ray", "Chest Xray", "Chest X Ray", "chest x-ray"])
def test_separator_variants_all_match_without_needing_an_alias_each(spelling):
    # Documents spell the same test several ways; matching must not depend on somebody having
    # listed every spelling in the catalogue.
    assert rules().match_test(spelling).canonical == "chest_xray"


def test_squash_index_drops_ambiguous_keys_rather_than_guessing(tmp_path):
    """Two canonicals whose names collide only under squashing must NOT resolve by dict order.

    'AlphaBeta' and 'Alpha Beta' have distinct `_norm` keys (so validation lets them through) but
    the same `_squash` key. The hyphen-blind index must drop that key entirely and let the lookup
    fall through to fuzzy scoring, rather than silently returning whichever was inserted first.
    """
    data = _minimal()
    data["test_catalogue"] = [
        {"canonical": "t_one", "category": "laboratory", "aliases": ["AlphaBeta"]},
        {"canonical": "t_two", "category": "laboratory", "aliases": ["Alpha Beta"]},
    ]
    data["packages"][0]["includes_tests"] = ["t_one"]
    loaded = load_rules(_write(tmp_path, data))

    assert _norm("AlphaBeta") != _norm("Alpha Beta")          # validation legitimately passes
    assert _squash("AlphaBeta") == _squash("Alpha Beta")      # but they squash together
    assert "alphabeta" not in loaded._squash_index            # so the key is withheld

    # Each still resolves via its own exact `_norm` key — precision is preserved, not lost.
    assert loaded.match_test("AlphaBeta").canonical == "t_one"
    assert loaded.match_test("Alpha Beta").canonical == "t_two"


# --- self-pay ---------------------------------------------------------------------
def test_self_pay_is_a_rule_not_a_special_case():
    sp = rules().self_pay
    assert sp.billing.covered_by == "patient"
    assert sp.questionnaire == "none"

"""Resolver tests — the eval matrix in PLAN.md §5 Phase 2 is the centrepiece.

That matrix is derived from a real cross-match of the 9 chit letters against
``patient_registration_synthetic.csv``: three patients named Loh Wei Ming, two named Devi Hui Min,
a name-only HR email, and a walk-in with nothing at all. It is the most valuable test asset in the
build and must not be weakened to make a change pass — if a code change breaks a row here, the code
is wrong, not the row.
"""
from __future__ import annotations

import datetime as dt
import inspect
import re

import pytest

from databridge_intake import resolve as resolve_mod
from databridge_intake.extract import extract_intake
from databridge_intake.gold import gold_harness
from databridge_intake.models import (PatientRecord, ResolutionResult, ResolutionStatus,
                                      SubjectBlock)
from databridge_intake.paths import fixtures_dir
from databridge_intake.registry import DATASET_TODAY, registry
from databridge_intake.resolve import HIGH_CONFIDENCE_SCORE, parse_claimed_dob, resolve


@pytest.fixture(scope="module")
def records() -> tuple[PatientRecord, ...]:
    return registry(DATASET_TODAY)


@pytest.fixture(scope="module")
def resolved() -> dict[str, ResolutionResult]:
    """Every chit fixture, extracted then resolved, keyed by filename."""
    harness = gold_harness()
    out = {}
    for path in sorted((fixtures_dir() / "chits").glob("*")):
        record = extract_intake(path.read_text(encoding="utf-8"),
                                document_id=path.name, harness=harness)
        out[path.name] = resolve(record.subject, today=DATASET_TODAY)
    return out


def _only(result: ResolutionResult):
    assert len(result.candidates) == 1, f"expected exactly one candidate, got {result.candidates}"
    return result.candidates[0]


# --- THE EVAL MATRIX (PLAN.md §5 Phase 2) -----------------------------------------------
# chit → (expected status, expected number of candidates, id of the row that should win)
MATRIX = [
    ("chit_01_meridian_referral.txt",       ResolutionStatus.SINGLE_CANDIDATE,    1, "S8010946C"),
    ("chit_02_everwell_voucher.txt",        ResolutionStatus.SINGLE_CANDIDATE,    1, "T2221854H"),
    ("chit_03_everwell_group_invitation.txt", ResolutionStatus.MULTIPLE_CANDIDATES, 2, None),
    ("chit_04_bluepeak_underwriting.txt",   ResolutionStatus.SINGLE_CANDIDATE,    1, "T1800429G"),
    ("chit_05_bluepeak_wellness_voucher.txt", ResolutionStatus.MULTIPLE_CANDIDATES, 2, None),
    ("chit_06_northstar_further_req.txt",   ResolutionStatus.SINGLE_CANDIDATE,    1, "S2915369H"),
    ("chit_07_mol_authorisation_pee226.txt", ResolutionStatus.SINGLE_CANDIDATE,   1, "S8001691D"),
    ("chit_08_mol_authorisation_vital.txt", ResolutionStatus.SINGLE_CANDIDATE,    1, "T7605473J"),
    ("chit_09_coralharbour_hr_email.eml",   ResolutionStatus.SINGLE_CANDIDATE,    1, "T9665502I"),
]


@pytest.mark.parametrize("fixture,status,count,winning_id", MATRIX)
def test_the_resolver_eval_matrix(resolved, fixture, status, count, winning_id):
    result = resolved[fixture]
    assert result.status is status
    assert len(result.candidates) == count
    if winning_id is not None:
        assert result.candidates[0].record.id_number.value == winning_id


def test_a_walk_in_with_no_document_is_insufficient_identifiers():
    """Row 10 of the matrix. Not an error — it routes to registration at the counter."""
    result = resolve(SubjectBlock(), today=DATASET_TODAY)
    assert result.status is ResolutionStatus.INSUFFICIENT_IDENTIFIERS
    assert result.candidates == ()
    assert any("register this patient" in n for n in result.notes)


# --- the specific properties each matrix row exists to prove -----------------------------


def test_an_identifier_disambiguates_a_three_way_name_collision(resolved, records):
    """Chit 1. Three patients are named Loh Wei Ming; only the NRIC on the letter picks one.

    The two same-name rows must be *excluded*, not merely out-ranked — leaving them in would make
    this `multiple_candidates` and put two strangers on the card next to the real patient.
    """
    same_name = [r for r in records if r.full_name.value == "Loh Wei Ming"]
    assert len(same_name) == 3, "fixture drift: the registry no longer has 3 Loh Wei Mings"

    result = resolved["chit_01_meridian_referral.txt"]
    assert result.status is ResolutionStatus.SINGLE_CANDIDATE
    candidate = _only(result)
    assert candidate.record.id_number.value == "S8010946C"
    assert "id_number" in candidate.matched_on
    assert any("narrowed it to 1" in n for n in result.notes)


def test_a_document_with_no_identifier_must_not_auto_pick(resolved):
    """Chit 3. Two patients named Devi Hui Min, nothing on the invitation to tell them apart.

    Both come back, equally scored, with a note that says so in words. This is the case where a
    "helpful" system picks the more recently registered one and is wrong half the time.
    """
    result = resolved["chit_03_everwell_group_invitation.txt"]
    assert result.status is ResolutionStatus.MULTIPLE_CANDIDATES
    assert len(result.candidates) == 2
    assert len({c.score for c in result.candidates}) == 1, "no signal separates these two"
    assert {c.record.id_number.value for c in result.candidates} == {"S5389135J", "S9733844J"}
    assert any("staff must select" in n for n in result.notes)


def test_a_name_only_match_scores_below_the_high_confidence_band(resolved):
    """Chit 9. An HR email naming a patient and nothing else.

    It resolves to exactly one person, which is the tempting case: one candidate looks settled.
    The score must stay below the identifier band and the result must say why in words the UI can
    show, or the card presents a name collision waiting to happen as a confident match.
    """
    result = resolved["chit_09_coralharbour_hr_email.eml"]
    candidate = _only(result)
    assert candidate.matched_on == ["name"]
    assert candidate.score < HIGH_CONFIDENCE_SCORE
    assert any("low-confidence match" in n for n in result.notes)


def test_a_corroborating_dob_and_sex_raise_the_score(resolved):
    """Chit 7 prints name, NRIC, DOB and sex — everything but nationality.

    The score stops just short of 1.0 for exactly that reason: 1.0 means every signal the scorer
    knows about was corroborated, and this document does not state nationality. A document that
    withholds a field must not score as though it had supplied one.
    """
    from databridge_intake.resolve import W_DEMOGRAPHIC, W_DOB, W_ID_NUMBER, W_NAME

    candidate = _only(resolved["chit_07_mol_authorisation_pee226.txt"])
    assert set(candidate.matched_on) == {"id_number", "name", "dob", "sex"}
    assert candidate.score == pytest.approx(W_ID_NUMBER + W_NAME + W_DOB + W_DEMOGRAPHIC / 2)
    assert candidate.score < 1.0
    assert candidate.conflicts == []


def test_scores_never_exceed_one_despite_float_addition(resolved):
    """The weights sum to exactly 1.0 in decimal but overshoot in binary floating point, which
    would trip `ResolutionCandidate.score`'s `le=1.0` bound on any fully-corroborated match."""
    for result in resolved.values():
        for candidate in result.candidates:
            assert 0.0 <= candidate.score <= 1.0


# --- the identity constraint (CLAUDE.md §Hard constraints 1) ------------------------------


def test_every_result_requires_human_selection(resolved):
    for name, result in resolved.items():
        assert result.requires_human_selection is True, name


def test_no_code_path_sets_requires_human_selection_false():
    """Source inspection, as PLAN.md §5 Phase 2 requires under "Done when".

    The schema already makes `False` unconstructible (`Literal[True]`), so this guards the other
    direction: someone loosening the schema later would still have to get past a test that reads
    the resolver's own source.
    """
    source = inspect.getsource(resolve_mod)
    offenders = re.findall(r"requires_human_selection\s*=\s*(?!True)\w+", source)
    assert not offenders, f"resolve.py assigns requires_human_selection to {offenders}"


def test_the_resolver_never_selects_a_patient(resolved):
    """A ResolutionResult has no field that could carry a selection, and `top` is display order
    only. Assert the API surface stays that way."""
    assert not hasattr(ResolutionResult, "selected_patient_id")
    for result in resolved.values():
        if result.candidates:
            assert result.top is result.candidates[0]


# --- scoring behaviour ---------------------------------------------------------------


def _subject(**kw) -> SubjectBlock:
    """Build a SubjectBlock from plain strings, bypassing grounding.

    These tests exercise the *scoring*, not the wall; Phase 1 already proves values are grounded.
    """
    from databridge_core import GroundedValue, SourcePointer

    ptr = SourcePointer(source_id="t", kind="doc_span", document_id="t",
                        char_start=0, char_end=1)
    return SubjectBlock(**{k: GroundedValue(value=v, pointer=ptr, confidence=1.0)
                           for k, v in kw.items() if v is not None})


def test_a_name_that_is_merely_similar_does_not_match(records):
    """Names are exact-after-normalisation with NO fuzzy tolerance — the Phase 1 rule, carried
    forward. 'Lim Arjuna' scores ~0.96 against 'Lim Arjun' under any fuzzy metric, and they are
    different people; a near-miss here puts a stranger's record in front of staff."""
    assert resolve(_subject(claimed_name="Lim Arjuna"), today=DATASET_TODAY).status is ResolutionStatus.NO_CANDIDATE
    assert resolve(_subject(claimed_name="Lim Arjun"), today=DATASET_TODAY).status is ResolutionStatus.SINGLE_CANDIDATE


def test_name_matching_ignores_case_and_punctuation_spacing():
    """Normalisation is not tolerance: a printed letter and a CSV cell differ in spacing and case
    without meaning two different people."""
    assert resolve(_subject(claimed_name="  lim   ARJUN "), today=DATASET_TODAY).status is (
        ResolutionStatus.SINGLE_CANDIDATE)


def test_an_identifier_matching_nobody_falls_back_to_name_and_flags_the_mismatch():
    """A typo'd or stale NRIC must not produce an empty result for a patient who IS registered —
    but the candidate has to carry the mismatch, or staff see a clean match to the wrong basis."""
    result = resolve(_subject(claimed_name="Lim Arjun", claimed_id_number="S0000000A"), today=DATASET_TODAY)
    candidate = _only(result)
    assert candidate.matched_on == ["name"]
    assert "id_number_mismatch" in candidate.conflicts
    assert any("matches no registry patient" in n for n in result.notes)


def test_a_conflicting_dob_is_flagged_not_dropped():
    """An ID match with a disagreeing date of birth is exactly what staff need to see. Dropping
    the candidate would hide the conflict; keeping it unflagged would hide it just as well."""
    result = resolve(_subject(claimed_name="Lim Arjun", claimed_id_number="T2221854H",
                              claimed_dob="01/01/99"), today=DATASET_TODAY)
    candidate = _only(result)
    assert "dob_mismatch" in candidate.conflicts
    assert "id_number" in candidate.matched_on
    assert candidate.score < 1.0


def test_demographics_alone_are_never_a_search_key():
    """'Male, Singaporean' describes a third of the registry. Returning 100 candidates is
    indistinguishable from returning none, so a subject with no name and no id is insufficient."""
    result = resolve(_subject(claimed_sex="Male", claimed_nationality="Singaporean"), today=DATASET_TODAY)
    assert result.status is ResolutionStatus.INSUFFICIENT_IDENTIFIERS
    assert result.candidates == ()


def test_the_demographic_weight_is_split_across_whichever_fields_are_stated():
    """A form carrying only sex must not earn the same corroboration as one carrying sex AND
    nationality — otherwise the weakest possible demographic signal scores full marks."""
    sex_only = _only(resolve(_subject(claimed_name="Lim Arjun", claimed_sex="M"), today=DATASET_TODAY))
    both = _only(resolve(_subject(claimed_name="Lim Arjun", claimed_sex="M",
                                  claimed_nationality="Singaporean"), today=DATASET_TODAY))
    assert sex_only.score < both.score


def test_an_unparseable_dob_is_silent_rather_than_a_conflict():
    """Absence of information is not evidence of disagreement. A spurious conflict badge trains
    staff to ignore the real ones."""
    candidate = _only(resolve(_subject(claimed_name="Lim Arjun", claimed_id_number="T2221854H",
                                       claimed_dob="not a date"), today=DATASET_TODAY))
    assert "dob_mismatch" not in candidate.conflicts
    assert "dob" not in candidate.matched_on


def test_ranking_is_deterministic_for_equally_scored_candidates(resolved):
    """Two candidates at the same score must not swap places between runs — the UI presents them
    in order and staff learn positions."""
    first = [c.patient_id for c in resolved["chit_03_everwell_group_invitation.txt"].candidates]
    for _ in range(3):
        again = resolve(_subject(claimed_name="Devi Hui Min"), today=DATASET_TODAY)
        assert [c.patient_id for c in again.candidates] == first


# --- the document-date parser --------------------------------------------------------


@pytest.mark.parametrize("printed,expected", [
    ("20/05/77", dt.date(1977, 5, 20)),      # as printed on chit 7
    ("23/02/79", dt.date(1979, 2, 23)),      # as printed on chit 8
    ("07/10/1990", dt.date(1990, 10, 7)),    # four-digit years appear on other forms
    ("1990-10-07", dt.date(1990, 10, 7)),
    ("07 Oct 1990", dt.date(1990, 10, 7)),
])
def test_claimed_dob_parses_the_formats_the_documents_print(printed, expected):
    assert parse_claimed_dob(printed, today=DATASET_TODAY) == expected


@pytest.mark.parametrize("bad", ["", "   ", "not a date", "31/02/90", "99/99/99", None])
def test_an_unreadable_claimed_dob_returns_none_rather_than_guessing(bad):
    assert parse_claimed_dob(bad, today=DATASET_TODAY) is None


def test_the_claimed_dob_pivot_matches_the_registry_pivot():
    """One two-digit-year rule, one implementation. Two would drift, and the drift would show up
    as a `dob_mismatch` between a document and the registry row it actually describes."""
    from databridge_intake.registry import parse_dob_two_digit_year

    for yy in ("05", "26", "27", "99"):
        assert parse_claimed_dob(f"01/01/{yy}", today=DATASET_TODAY) == parse_dob_two_digit_year(f"01/01/{yy}", today=DATASET_TODAY)


def test_a_claimed_dob_is_never_in_the_future():
    """The pivot resolves toward the most recent non-future reading; a future DOB would produce a
    negative age and, downstream, a nonsense eligibility decision."""
    for yy in range(0, 100):
        parsed = parse_claimed_dob(f"01/01/{yy:02d}", today=DATASET_TODAY)
        assert parsed is not None and parsed <= DATASET_TODAY

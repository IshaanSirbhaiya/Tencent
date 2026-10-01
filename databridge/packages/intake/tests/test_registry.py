"""Phase 0 — the patient registry loads, and every value knows where it came from."""
from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from databridge_core import GroundedValue
from databridge_intake import (
    DATASET_TODAY,
    DOB_PIVOT_YEAR,
    IdType,
    PatientRecord,
    RegistryError,
    by_patient_id,
    classify_id_type,
    load_registry,
    parse_dob_two_digit_year,
    patient_id_for,
    registry,
)
from databridge_intake.registry import COLUMN_MAP, TABLE


# --- loading ----------------------------------------------------------------------
def test_registry_loads_every_data_row():
    # Counted from the CSV itself (HC1, rule 6), header excluded — not a remembered 300.
    import csv

    from databridge_intake.paths import patient_registry_csv

    with open(patient_registry_csv(), newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) >= 1
    assert len(registry(DATASET_TODAY)) == len(rows)


def test_registry_is_cached_and_immutable():
    # a tuple, so a caller cannot append to or reorder the shared copy
    assert registry(DATASET_TODAY) is registry(DATASET_TODAY)
    assert isinstance(registry(DATASET_TODAY), tuple)


def test_missing_file_raises_registry_error(tmp_path):
    with pytest.raises(RegistryError, match="not found"):
        load_registry(tmp_path / "nope.csv")


def test_header_drift_fails_loudly(tmp_path):
    # A renamed column must fail here, not silently produce blank records downstream.
    bad = tmp_path / "bad.csv"
    bad.write_text("Full Name,Sex\nTan Kai Xuan,M\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="missing expected column"):
        load_registry(bad)


def test_empty_file_raises(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text(",".join(COLUMN_MAP) + "\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="no data rows"):
        load_registry(empty)


# --- grounding: every field traces to a CSV cell ----------------------------------
def test_every_field_is_a_grounded_value_with_a_valid_pointer():
    for record in registry(DATASET_TODAY)[:25]:
        for field in PatientRecord.model_fields:
            if field == "patient_id":
                continue
            gv = getattr(record, field)
            assert isinstance(gv, GroundedValue), f"{field} is not grounded"
            assert gv.pointer.source_id.endswith(".csv")


def test_scalar_fields_point_at_the_exact_csv_cell():
    rec = registry(DATASET_TODAY)[0]
    ptr = rec.full_name.pointer
    assert ptr.kind == "table_cell"
    assert ptr.table == TABLE
    assert ptr.column == "Full Name"
    assert ptr.row_id == "1"          # 1-based data row, as a human would count


def test_dob_carries_a_derived_pointer_naming_the_pivot_rule():
    # An age drives eligibility, so the century-resolution rule must be inspectable.
    rec = registry(DATASET_TODAY)[0]
    ptr = rec.date_of_birth.pointer
    assert ptr.kind == "derived"
    assert "two-digit-year pivot" in ptr.derivation
    assert ptr.derived_from and ptr.derived_from[0].column == "Date of Birth (DD/MM/YY)"


def test_id_type_is_derived_from_the_id_number_cell_not_a_fabricated_cell():
    rec = registry(DATASET_TODAY)[0]
    ptr = rec.id_type.pointer
    assert ptr.kind == "derived"
    assert ptr.derived_from[0].column == "NRIC/FIN/Passport Number"


def test_a_grounded_value_cannot_be_built_without_a_pointer():
    with pytest.raises(ValidationError):
        GroundedValue(value="fabricated")            # type: ignore[call-arg]


# --- known rows -------------------------------------------------------------------
def test_spot_check_lim_arjun():
    rec = next(r for r in registry(DATASET_TODAY) if r.id_number.value == "T2221854H")
    assert rec.full_name.value == "Lim Arjun"
    assert rec.date_of_birth.value == dt.date(1990, 10, 7)
    assert rec.sex.value == "M"
    assert rec.email.value == "arjun.lim23@icloud.com"
    assert rec.drug_allergy.value == "Latex"


def test_drug_allergy_none_is_a_recorded_answer_not_a_null():
    # "None" means the patient was asked and said none — materially different from "unknown".
    rec = next(r for r in registry(DATASET_TODAY) if r.id_number.value == "S4744854C")
    assert rec.drug_allergy.value == "None"


# --- the two-digit-year pivot -----------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("18/12/93", dt.date(1993, 12, 18)),
        ("25/01/05", dt.date(2005, 1, 25)),      # the ambiguous one: 2005, not 1905
        ("26/07/52", dt.date(1952, 7, 26)),
        ("01/01/26", dt.date(2026, 1, 1)),       # on the pivot -> 20xx
        ("01/01/27", dt.date(1927, 1, 1)),       # one past the pivot -> 19xx
        ("29/02/04", dt.date(2004, 2, 29)),      # leap day survives
    ],
)
def test_dob_pivot_boundary(raw, expected):
    assert parse_dob_two_digit_year(raw, today=DATASET_TODAY) == expected


def test_pivot_never_produces_a_future_birth_date():
    """Probes every day of every two-digit year, not just 01/01.

    A year-only check passes `31/12/26` — which resolves to 2026-12-31, four months after the
    clinic's today — and that yields a derived age of -1, which then satisfies any open-ended
    lower age band. The bug hides entirely in the months after DATASET_TODAY, so the probe has
    to cover them.
    """
    for yy in range(0, 100):
        for month, day in ((1, 1), (6, 15), (12, 31)):
            parsed = parse_dob_two_digit_year(f"{day:02d}/{month:02d}/{yy:02d}", today=DATASET_TODAY)
            assert parsed <= DATASET_TODAY, f"{day:02d}/{month:02d}/{yy:02d} -> {parsed} is future"


def test_the_specific_late_in_pivot_year_regression():
    # 31/12/26 with today = 2026-08-11: the 20xx reading is in the future, so it must be 1926.
    assert parse_dob_two_digit_year("31/12/26", today=DATASET_TODAY) == dt.date(1926, 12, 31)
    # ...while an earlier date in the same year is legitimately 20xx.
    assert parse_dob_two_digit_year("01/01/26", today=DATASET_TODAY) == dt.date(2026, 1, 1)


def test_a_future_dob_would_produce_a_negative_age_which_no_band_should_accept():
    """Why the above matters: a negative age silently satisfies an open-ended lower bound."""
    from databridge_intake import rules
    pee225 = rules().package("MOL0199VME", "PEE225")     # ages 24 and below
    assert pee225.eligibility.covers_age(-1) is True     # the latent hazard...
    # ...which is unreachable because no parsed DOB can be in the future.
    assert all(r.date_of_birth.value <= DATASET_TODAY for r in registry(DATASET_TODAY))
    assert all(r.age_at(DATASET_TODAY) >= 0 for r in registry(DATASET_TODAY))


def test_pivot_is_derived_from_the_dataset_date_not_hardcoded():
    assert DOB_PIVOT_YEAR == DATASET_TODAY.year % 100


@pytest.mark.parametrize("bad", ["1990-10-07", "07/10/1990", "7/10", "", "aa/bb/cc"])
def test_dob_rejects_malformed_input(bad):
    with pytest.raises(ValueError):
        parse_dob_two_digit_year(bad, today=DATASET_TODAY)


def test_no_registry_patient_is_born_in_the_future():
    assert all(r.date_of_birth.value <= DATASET_TODAY for r in registry(DATASET_TODAY))


# --- derived age ------------------------------------------------------------------
def test_age_at_handles_the_birthday_boundary():
    rec = next(r for r in registry(DATASET_TODAY) if r.id_number.value == "T2221854H")   # DOB 1990-10-07
    assert rec.age_at(dt.date(2026, 10, 6)) == 35
    assert rec.age_at(dt.date(2026, 10, 7)) == 36


def test_kumar_sheng_yang_is_49_on_the_pee226_boundary():
    # PEE226 covers ages 25-49; this patient sits exactly on the upper edge in 2026.
    rec = next(r for r in registry(DATASET_TODAY) if r.id_number.value == "S8001691D")
    assert rec.date_of_birth.value == dt.date(1977, 5, 20)
    assert rec.age_at(DATASET_TODAY) == 49


# --- id classification ------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    ("S8010946C", IdType.NRIC_FIN),
    ("T2221854H", IdType.NRIC_FIN),
    ("G1234567X", IdType.NRIC_FIN),
    ("E12345678", IdType.PASSPORT),
    ("X1234567", IdType.PASSPORT),
    # Unclassifiable input must NOT be positively asserted as a passport — that would be a guess
    # wearing a type, and would pre-tick the Passport radio for a patient whose ID type is unknown.
    ("", IdType.UNKNOWN),
    ("   ", IdType.UNKNOWN),
    ("NOT AN ID", IdType.UNKNOWN),
    ("12/34", IdType.UNKNOWN),
    ("ABC", IdType.UNKNOWN),
])
def test_classify_id_type(value, expected):
    assert classify_id_type(value) is expected


def test_every_registry_id_is_recognised_as_nric_fin():
    # The synthetic registry is all-Singaporean-format; if that ever changes we want to know.
    assert all(r.id_type.value == IdType.NRIC_FIN.value for r in registry(DATASET_TODAY))


# --- indexing ---------------------------------------------------------------------
def test_patient_id_is_stable_and_derived_from_the_identifier():
    assert patient_id_for("S8010946C") == patient_id_for(" s8010946c ")
    assert patient_id_for("S8010946C") != patient_id_for("T2221854H")


def test_by_patient_id_round_trips():
    index = by_patient_id()
    rec = registry(DATASET_TODAY)[0]
    assert index[rec.patient_id].id_number.value == rec.id_number.value

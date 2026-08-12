"""Patient registry loader — ``patient_registration_synthetic.csv`` → grounded ``PatientRecord``s.

This is the clinic's existing master data: the 300 patients the front desk would otherwise re-key
by hand at ~2-3 minutes each. Every field carries a ``table_cell`` pointer back to the exact CSV
cell, so any value the system later pre-fills into a questionnaire can be traced to its origin in
one click.

The one genuinely tricky bit is the date of birth — see ``parse_dob_two_digit_year``.
"""
from __future__ import annotations

import csv
import datetime as dt
import functools
import hashlib
import re
from typing import Iterable, Optional

from databridge_core import GroundedValue, SourcePointer

from .models import IdType, PatientRecord
from .paths import PATIENT_REGISTRY_CSV, patient_registry_csv

TABLE = "patient_registry"

# CSV header → PatientRecord field. Kept explicit (not derived) so a header change in the source
# data fails loudly here rather than silently producing empty records.
COLUMN_MAP: dict[str, str] = {
    "Full Name": "full_name",
    "NRIC/FIN/Passport Number": "id_number",
    "Sex": "sex",
    "Nationality": "nationality",
    "Date of Birth (DD/MM/YY)": "date_of_birth",
    "Address": "address",
    "Postal Code": "postal_code",
    "Contact - Home": "contact_home",
    "Contact - Office": "contact_office",
    "Contact - Mobile": "contact_mobile",
    "Email": "email",
    "Drug Allergy": "drug_allergy",
}

# Singapore NRIC/FIN: one letter prefix, 7 digits, one checksum letter (e.g. S8010946C).
_NRIC_FIN = re.compile(r"^[STFGM]\d{7}[A-Z]$", re.IGNORECASE)

# A plausible passport number: alphanumeric, no separators, 6-12 chars. Deliberately permissive
# (formats vary by country) but not a catch-all — see classify_id_type.
_PASSPORT_SHAPED = re.compile(r"^[A-Z0-9]{6,12}$", re.IGNORECASE)

# The clinic "today" every derived age is computed against. Fixed rather than read from the system
# clock so ages, eligibility flags and the demo's numbers are reproducible on any machine and in
# CI. The sample chit letters and questionnaires are dated across mid-2026; this sits just after
# the latest of them. Bump it only alongside the fixture data.
DATASET_TODAY = dt.date(2026, 8, 11)

# Pivot for two-digit years: `YY <= DOB_PIVOT_YEAR` means 20xx, otherwise 19xx.
DOB_PIVOT_YEAR = DATASET_TODAY.year % 100


class RegistryError(RuntimeError):
    """The registry CSV is missing, malformed, or has drifted from COLUMN_MAP."""


def parse_dob_two_digit_year(raw: str, *, today: dt.date = DATASET_TODAY) -> dt.date:
    """Parse ``DD/MM/YY`` into a real date, resolving the century so the result is never future.

    The source CSV stores two-digit years, so ``25/01/05`` is genuinely ambiguous: 1905 or 2005.
    The rule is "the most recent reading that is not in the future": try 20xx first and fall back
    to 19xx if that lands after ``today``. So ``05`` → 2005 (a 21-year-old) and ``52`` → 1952.

    Pivoting on the *year alone* is not enough, and that bug is easy to miss. With today =
    2026-08-11, ``31/12/26`` passes a ``YY <= 26`` check and yields 2026-12-31 — four months in
    the future, giving a derived age of **-1**, which then satisfies any open-ended lower age band
    (``age_max: 24`` happily accepts -1) and silently makes a nonexistent patient eligible for a
    package instead of raising a flag. Comparing the whole date closes that.

    This matters because **age drives eligibility** (WELL2 is ages 40-59, PEE226 is 25-49). A
    coverage decision resting on a silently-guessed century is not defensible, which is why this is
    a named, tested function and why its output carries a ``derived`` SourcePointer naming the rule.
    """
    parts = raw.strip().split("/")
    if len(parts) != 3:
        raise ValueError(f"expected DD/MM/YY, got {raw!r}")
    day_s, month_s, year_s = parts
    if len(year_s) != 2 or not year_s.isdigit():
        raise ValueError(f"expected a two-digit year in {raw!r}, got {year_s!r}")
    yy = int(year_s)
    candidate = dt.date(2000 + yy, int(month_s), int(day_s))
    if candidate > today:
        candidate = candidate.replace(year=1900 + yy)
    return candidate


def classify_id_type(id_number: str) -> IdType:
    """NRIC/FIN vs passport, by format. Structural only — this says nothing about validity.

    This is a *format* classification used to pick the right questionnaire radio button. It is
    explicitly NOT a validity check: we never assert that an identity document is genuine, current,
    or belongs to the person presenting it (CLAUDE.md §Hard constraints 1).

    Blank or structurally implausible input returns ``UNKNOWN`` rather than defaulting to
    ``PASSPORT``. "Everything I don't recognise is a passport" is a guess wearing a type, and it
    would pre-tick the Passport radio button for a patient whose ID type we simply do not know.
    """
    cleaned = id_number.strip()
    if not cleaned:
        return IdType.UNKNOWN
    if _NRIC_FIN.match(cleaned):
        return IdType.NRIC_FIN
    # Passport numbers vary worldwide, so we can only require that it plausibly *is* one:
    # alphanumeric, no separators, a sane length. Anything else is unclassifiable, not a passport.
    if _PASSPORT_SHAPED.match(cleaned):
        return IdType.PASSPORT
    return IdType.UNKNOWN


def patient_id_for(id_number: str) -> str:
    """Stable synthetic key for a registry row, derived from the identifier."""
    digest = hashlib.sha256(id_number.strip().upper().encode()).hexdigest()[:12]
    return f"pat:{digest}"


def _cell_pointer(column: str, row_id: str, source_id: str) -> SourcePointer:
    return SourcePointer(
        source_id=source_id, kind="table_cell", table=TABLE, column=column, row_id=row_id)


def _derived_pointer(base: SourcePointer, derivation: str, source_id: str) -> SourcePointer:
    return SourcePointer(
        source_id=source_id, kind="derived", derivation=derivation, derived_from=[base])


def _row_to_record(row: dict[str, str], row_id: str, source_id: str) -> PatientRecord:
    values: dict[str, GroundedValue] = {}
    for column, field in COLUMN_MAP.items():
        raw = (row.get(column) or "").strip()
        ptr = _cell_pointer(column, row_id, source_id)
        if field == "date_of_birth":
            parsed = parse_dob_two_digit_year(raw)
            values[field] = GroundedValue(
                value=parsed,
                pointer=_derived_pointer(
                    ptr, f"two-digit-year pivot at {DOB_PIVOT_YEAR}: {raw!r} -> {parsed.isoformat()}",
                    source_id),
                confidence=1.0)
        else:
            values[field] = GroundedValue(value=raw, pointer=ptr, confidence=1.0)

    id_number = values["id_number"].value
    if not id_number:
        raise ValueError("NRIC/FIN/Passport Number is blank — a patient record with no identifier "
                         "cannot be matched against and must not enter the registry")
    # id_type is not a CSV column — it is derived from the identifier's format, so it gets a
    # `derived` pointer chaining back to the cell it was inferred from rather than a fake cell ref.
    # Confidence is below 1.0 for the passport case: an NRIC/FIN match is a positive pattern hit,
    # but "passport" is inferred by exclusion, and the difference should be visible downstream.
    id_type = classify_id_type(id_number)
    values["id_type"] = GroundedValue(
        value=id_type.value,
        pointer=_derived_pointer(
            values["id_number"].pointer,
            f"id format classification -> {id_type.value} (NRIC/FIN pattern match, else shape)",
            source_id),
        confidence=1.0 if id_type is IdType.NRIC_FIN else 0.6)

    return PatientRecord(patient_id=patient_id_for(id_number), **values)


def load_registry(path=None) -> list[PatientRecord]:
    """Load every patient in the registry CSV, fully grounded.

    ``row_id`` is the 1-based **data** row, header excluded — i.e. "record 3 of 300", which is one
    less than the spreadsheet line number. Error messages spell out both so a pointer stays
    followable by a person, not just by a program.
    """
    csv_path = path or patient_registry_csv()
    if not csv_path.exists():
        raise RegistryError(f"patient registry not found at {csv_path}")
    source_id = getattr(csv_path, "name", PATIENT_REGISTRY_CSV)

    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        missing = [c for c in COLUMN_MAP if c not in headers]
        if missing:
            raise RegistryError(
                f"{source_id} is missing expected column(s): {missing}. "
                f"Found: {headers}. Update COLUMN_MAP if the source data legitimately changed.")
        records = []
        for i, row in enumerate(reader, start=1):
            try:
                records.append(_row_to_record(row, str(i), source_id))
            except Exception as exc:  # noqa: BLE001 — re-raised with location below
                # One bad cell in 300 rows should say WHICH row and WHICH file. A bare
                # "expected a two-digit year in '07/10/1990'" is unactionable against a CSV.
                raise RegistryError(
                    f"{source_id} row {i} (spreadsheet line {i + 1}): {exc}") from exc

    if not records:
        raise RegistryError(f"{source_id} contained no data rows")
    return records


@functools.lru_cache(maxsize=1)
def registry() -> tuple[PatientRecord, ...]:
    """Process-wide cached registry. Immutable tuple so callers cannot mutate the shared copy."""
    return tuple(load_registry())


def by_patient_id(records: Optional[Iterable[PatientRecord]] = None) -> dict[str, PatientRecord]:
    """Index by ``patient_id``.

    Note ``patient_id`` is derived from the identifier, so two rows sharing an NRIC would collapse.
    That is correct — it would mean the same person recorded twice — but callers that need every
    physical row (e.g. a duplicate audit) should iterate the list instead of this index.
    """
    return {r.patient_id: r for r in (records if records is not None else registry())}

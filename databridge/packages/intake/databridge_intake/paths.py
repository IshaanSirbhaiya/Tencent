"""Canonical filesystem locations for the Hack4Health data and fixtures.

Resolved relative to the repo root so every caller (tests, gateway, CLI) agrees on where the
data lives. The competition CSVs are read from ``Hack4Health/`` — the untouched originals — rather
than copied into ``fixtures/``: a second copy of a 300-row master file is a drift hazard, and the
originals are never edited (CLAUDE.md). Fixtures that we *derive* (the split chit letters, the
walk-in stub) do live under ``databridge/fixtures/hackathon/``.
"""
from __future__ import annotations

import functools
import pathlib


@functools.lru_cache(maxsize=1)
def repo_root() -> pathlib.Path:
    """The git root — the directory containing both ``Hack4Health/`` and ``databridge/``.

    Walks up from this file rather than trusting the cwd, so pytest, uvicorn and the CLI all
    resolve identically no matter where they were launched from.
    """
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "Hack4Health").is_dir() and (parent / "databridge").is_dir():
            return parent
    raise RuntimeError(
        f"repo root not found above {here}: expected an ancestor containing both "
        f"'Hack4Health/' and 'databridge/'")


def source_data_dir() -> pathlib.Path:
    """The competition-provided originals. Read-only — never write here."""
    return repo_root() / "Hack4Health"


def fixtures_dir() -> pathlib.Path:
    """Derived test fixtures (split chit letters, walk-in stub)."""
    return repo_root() / "databridge" / "fixtures" / "hackathon"


def chits_dir() -> pathlib.Path:
    return fixtures_dir() / "chits"


def config_dir() -> pathlib.Path:
    """``packages/intake/databridge_intake/config`` — the rules/schema tables."""
    return pathlib.Path(__file__).resolve().parent / "config"


# --- the three competition CSVs ---------------------------------------------------
PATIENT_REGISTRY_CSV = "patient_registration_synthetic.csv"
GENERAL_HEALTH_CSV = "general_health_questionnaire_mock_patients.csv"
OCCUPATIONAL_HEALTH_CSV = "occupational_health_questionnaire_mock_patients.csv"


def patient_registry_csv() -> pathlib.Path:
    return source_data_dir() / PATIENT_REGISTRY_CSV


def general_health_csv() -> pathlib.Path:
    return source_data_dir() / GENERAL_HEALTH_CSV


def occupational_health_csv() -> pathlib.Path:
    return source_data_dir() / OCCUPATIONAL_HEALTH_CSV

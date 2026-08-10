"""Exhaustive tests for the C17 canonical formatters (§12.1) — the contract claim_check matches.

Treated with grounding-wall rigour: determinism, half-up rounding, float-drift immunity, sign
handling, locale-free dates, case-preserving names. If any of these forms shift, every downstream
factuality check silently changes — so they are pinned hard here.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from databridge_convert.generate import (
    render_date,
    render_decimal,
    render_int,
    render_money,
    render_name,
    render_percent,
)


# --- render_decimal ---------------------------------------------------------------
@pytest.mark.parametrize("value,places,expected", [
    (1234.5, 2, "1,234.50"),
    ("1234.567", 2, "1,234.57"),       # half-up
    ("1234.565", 2, "1,234.57"),       # .5 rounds away from zero (not banker's → would be .56)
    (1000, 0, "1,000"),
    (0, 2, "0.00"),
    (-0.005, 2, "-0.01"),              # half-up away from zero
    (-0.004, 2, "0.00"),               # never "-0.00"
    (1234567.891, 2, "1,234,567.89"),
    (Decimal("1234.567"), 2, "1,234.57"),
    (0.1, 2, "0.10"),                  # float-drift immunity (not 0.10000000000000001)
    (19.99, 2, "19.99"),
    (-1234.5, 2, "-1,234.50"),
    (5, 0, "5"),
    (999, 0, "999"),
    (1000000, 0, "1,000,000"),
])
def test_render_decimal(value, places, expected):
    assert render_decimal(value, places) == expected


def test_render_decimal_float_drift_immune():
    # the classic 0.1+0.2 trap must NOT leak into the rendered token
    assert render_decimal(0.1 + 0.2, 2) == "0.30"


def test_render_decimal_negative_places_rejected():
    with pytest.raises(ValueError):
        render_decimal(5, -1)


# --- render_int -------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    (1234567, "1,234,567"),
    (0, "0"),
    (-42, "-42"),
    (1000, "1,000"),
    ("2500", "2,500"),
    (Decimal("9999"), "9,999"),
])
def test_render_int(value, expected):
    assert render_int(value) == expected


def test_render_int_rejects_non_integer():
    with pytest.raises(ValueError):
        render_int(12.5)


# --- render_money -----------------------------------------------------------------
@pytest.mark.parametrize("value,currency,expected", [
    (1350, "USD", "$1,350.00"),
    (1234.5, "EUR", "€1,234.50"),
    (5000, "JPY", "¥5,000"),            # zero-decimal currency
    (99.5, "CHF", "99.50 CHF"),         # unknown symbol → ISO suffix
    (-20, "USD", "-$20.00"),
    (0, "USD", "$0.00"),
    (1234567.895, "USD", "$1,234,567.90"),  # half-up at the cent
    (1234.5, "gbp", "£1,234.50"),       # case-insensitive currency
])
def test_render_money(value, currency, expected):
    assert render_money(value, currency) == expected


def test_render_money_symbol_off():
    assert render_money(1350, "USD", symbol=False) == "1,350.00 USD"


def test_render_money_deterministic():
    assert render_money(1350.0) == render_money(Decimal("1350")) == render_money("1350") == "$1,350.00"


# --- render_percent ---------------------------------------------------------------
@pytest.mark.parametrize("value,places,frac,expected", [
    (8, 1, False, "8.0%"),
    (8.25, 2, False, "8.25%"),
    (0.08, 1, True, "8.0%"),
    (33.3333, 1, False, "33.3%"),
    (100, 0, False, "100%"),
    (0.005, 2, True, "0.50%"),
])
def test_render_percent(value, places, frac, expected):
    assert render_percent(value, places, as_fraction=frac) == expected


# --- render_date ------------------------------------------------------------------
@pytest.mark.parametrize("value,fmt,expected", [
    (dt.date(2024, 3, 15), "iso", "2024-03-15"),
    (dt.datetime(2024, 3, 15, 9, 30), "iso", "2024-03-15"),   # time dropped
    ("2024-03-15", "iso", "2024-03-15"),
    ("2024-03-15T09:30:00", "iso", "2024-03-15"),
    (dt.date(2024, 3, 15), "long", "15 March 2024"),
    (dt.date(2024, 1, 1), "long", "1 January 2024"),
])
def test_render_date(value, fmt, expected):
    assert render_date(value, fmt) == expected


def test_render_date_rejects_garbage():
    with pytest.raises((ValueError, TypeError)):
        render_date("not a date")


# --- render_name ------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    ("  Acme   Industries ", "Acme Industries"),
    ("john\tsmith", "john smith"),
    ("McDonald", "McDonald"),                 # case preserved
    ("van der Berg", "van der Berg"),
    ("IBM", "IBM"),
    ("Stark Industries", "Stark Industries"),
])
def test_render_name(value, expected):
    assert render_name(value) == expected


# --- cross-cutting: determinism (the whole point) ---------------------------------
def test_all_formatters_deterministic_across_input_types():
    # the SAME value via int / float / str / Decimal must render to the SAME token
    forms = {render_money(1234.5), render_money("1234.50"), render_money(Decimal("1234.5"))}
    assert forms == {"$1,234.50"}
    pcts = {render_percent(8), render_percent(8.0), render_percent("8")}
    assert pcts == {"8.0%"}


def test_bool_rejected_as_number():
    # a bool sneaking in as a number is a bug, not "1" — reject loudly
    with pytest.raises(TypeError):
        render_money(True)

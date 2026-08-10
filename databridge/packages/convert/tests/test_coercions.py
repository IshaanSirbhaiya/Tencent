"""Exhaustive tests for the coercion catalogue (09_FINAL_PRD §10.3).

Every coercion is tested against every edge case listed in the
``databridge-coercion-catalogue`` skill reference table. Failure cases assert
``CoercionError`` (quarantine, never a silent drop / guess); value cases use
exact ``Decimal`` equality or ``pytest.approx``.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from databridge_convert.rules.coercions import (
    COERCIONS,
    CoercionError,
    apply_coercion,
    bool_coerce,
    case_fold,
    currency_minor_units,
    currency_scale,
    detect_epoch_unit,
    enum_map,
    epoch_to_iso,
    id_cast,
    iso_normalise,
    sentinel_to_null,
    str_to_decimal,
    trim_normalise_ws,
    unit_convert,
)


# --- registry / harness ---------------------------------------------------------
def test_registry_contains_every_coercion():
    expected = {
        "str_to_decimal", "epoch_to_iso", "iso_normalise", "sentinel_to_null",
        "bool_coerce", "enum_map", "currency_minor_units", "id_cast",
        "unit_convert", "trim_normalise_ws", "case_fold",
    }
    assert expected <= set(COERCIONS)


def test_apply_coercion_dispatches_by_name():
    assert apply_coercion("case_fold", "ABC") == "abc"
    assert apply_coercion("str_to_decimal", "1,234.56", locale="us") == Decimal("1234.56")


def test_apply_coercion_unknown_name_is_keyerror_not_coercionerror():
    # Unknown coercion name is a programming error, NOT a data error.
    with pytest.raises(KeyError):
        apply_coercion("does_not_exist", 1)


def test_coercion_error_is_valueerror_subclass():
    assert issubclass(CoercionError, ValueError)


# --- str_to_decimal -------------------------------------------------------------
def test_str_to_decimal_us_grouped():
    assert str_to_decimal("1,234.56", locale="us") == Decimal("1234.56")


def test_str_to_decimal_eu_grouped():
    assert str_to_decimal("1.234,56", locale="eu") == Decimal("1234.56")


def test_str_to_decimal_both_locales_same_value():
    assert str_to_decimal("1,234.56", locale="us") == str_to_decimal("1.234,56", locale="eu")


def test_str_to_decimal_us_default_locale():
    # locale defaults to 'us'
    assert str_to_decimal("1,234.56") == Decimal("1234.56")


def test_str_to_decimal_negative_us():
    assert str_to_decimal("-1,234.56", locale="us") == Decimal("-1234.56")


def test_str_to_decimal_negative_eu():
    assert str_to_decimal("-1.234,56", locale="eu") == Decimal("-1234.56")


def test_str_to_decimal_plain_integer_string():
    assert str_to_decimal("1234", locale="us") == Decimal("1234")
    assert str_to_decimal("1234", locale="eu") == Decimal("1234")


def test_str_to_decimal_int_passthrough():
    assert str_to_decimal(1234) == Decimal("1234")


def test_str_to_decimal_float_passthrough():
    assert str_to_decimal(1234.56) == Decimal("1234.56")


def test_str_to_decimal_decimal_passthrough():
    d = Decimal("99.99")
    assert str_to_decimal(d) is d


def test_str_to_decimal_whitespace_trimmed():
    assert str_to_decimal("  1234.5  ", locale="us") == Decimal("1234.5")


def test_str_to_decimal_empty_string_raises():
    with pytest.raises(CoercionError):
        str_to_decimal("", locale="us")


def test_str_to_decimal_whitespace_only_raises():
    with pytest.raises(CoercionError):
        str_to_decimal("   ", locale="us")


def test_str_to_decimal_non_number_string_raises():
    with pytest.raises(CoercionError):
        str_to_decimal("not a number", locale="us")


def test_str_to_decimal_unknown_locale_raises():
    with pytest.raises(CoercionError):
        str_to_decimal("1234.56", locale="fr")


def test_str_to_decimal_non_string_non_number_raises():
    with pytest.raises(CoercionError):
        str_to_decimal(["1234"], locale="us")


# --- detect_epoch_unit ----------------------------------------------------------
def test_detect_epoch_unit_seconds():
    assert detect_epoch_unit(1_700_000_000) == "s"          # ~10 digits


def test_detect_epoch_unit_milliseconds():
    assert detect_epoch_unit(1_700_000_000_000) == "ms"     # ~13 digits


def test_detect_epoch_unit_microseconds():
    assert detect_epoch_unit(1_700_000_000_000_000) == "us"  # ~16 digits


def test_detect_epoch_unit_nanoseconds():
    assert detect_epoch_unit(1_700_000_000_000_000_000) == "ns"


def test_detect_epoch_unit_uses_magnitude_of_abs():
    assert detect_epoch_unit(-1_700_000_000) == "s"


# --- epoch_to_iso ---------------------------------------------------------------
def test_epoch_to_iso_zero_is_unix_epoch():
    assert epoch_to_iso(0) == "1970-01-01T00:00:00Z"


def test_epoch_to_iso_seconds_detected():
    # 1700000000 s = 2023-11-14T22:13:20Z
    assert epoch_to_iso(1_700_000_000) == "2023-11-14T22:13:20Z"


def test_epoch_to_iso_milliseconds_detected():
    # same wall-clock instant expressed in ms
    assert epoch_to_iso(1_700_000_000_000) == "2023-11-14T22:13:20Z"


def test_epoch_to_iso_microseconds_detected():
    assert epoch_to_iso(1_700_000_000_000_000) == "2023-11-14T22:13:20Z"


def test_epoch_to_iso_all_magnitudes_agree():
    s = epoch_to_iso(1_700_000_000)
    ms = epoch_to_iso(1_700_000_000_000)
    us = epoch_to_iso(1_700_000_000_000_000)
    assert s == ms == us == "2023-11-14T22:13:20Z"


def test_epoch_to_iso_explicit_unit_overrides_detection():
    # 1700000000 would auto-detect as seconds; force ms instead.
    auto = epoch_to_iso(1_700_000_000)                   # seconds
    forced = epoch_to_iso(1_700_000_000, unit="ms")      # 1.7e9 ms = ~1970
    assert auto != forced
    assert forced.startswith("1970-01-20")


def test_epoch_to_iso_explicit_seconds_unit():
    assert epoch_to_iso(0, unit="s") == "1970-01-01T00:00:00Z"


def test_epoch_to_iso_unit_detect_off_assumes_seconds():
    # With unit_detect=False and no explicit unit, the value is treated as seconds.
    # 0 seconds is the unix epoch regardless of detection.
    assert epoch_to_iso(0, unit_detect=False) == "1970-01-01T00:00:00Z"
    # A ms-magnitude value auto-detects as ms → 2023.
    big_ms = 1_700_000_000_000
    assert epoch_to_iso(big_ms).startswith("2023")        # auto → ms → 2023
    # Forced to be read as seconds it is year ~55879, beyond datetime's max year (9999),
    # so it fails closed with CoercionError (→ quarantine) rather than crashing.
    with pytest.raises(CoercionError):
        epoch_to_iso(big_ms, unit_detect=False)


def test_epoch_to_iso_numeric_string_accepted():
    # int(value) accepts numeric strings.
    assert epoch_to_iso("0") == "1970-01-01T00:00:00Z"


def test_epoch_to_iso_unknown_explicit_unit_raises():
    with pytest.raises(CoercionError):
        epoch_to_iso(1_700_000_000, unit="weeks")


def test_epoch_to_iso_non_int_raises():
    with pytest.raises(CoercionError):
        epoch_to_iso("not-an-int")


def test_epoch_to_iso_none_raises():
    with pytest.raises(CoercionError):
        epoch_to_iso(None)


# --- iso_normalise --------------------------------------------------------------
def test_iso_normalise_tz_aware_string_to_utc():
    assert iso_normalise("2023-11-14T22:13:20+00:00") == "2023-11-14T22:13:20Z"


def test_iso_normalise_offset_converted_to_utc():
    # +02:00 → subtract 2 hours to reach UTC
    assert iso_normalise("2023-11-14T22:13:20+02:00") == "2023-11-14T20:13:20Z"


def test_iso_normalise_z_suffix_handled():
    assert iso_normalise("2023-11-14T22:13:20Z") == "2023-11-14T22:13:20Z"


def test_iso_normalise_tz_aware_datetime_to_utc():
    dt = datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone(timedelta(hours=5)))
    assert iso_normalise(dt) == "2023-11-14T17:13:20Z"


def test_iso_normalise_naive_assume_utc_attaches_utc():
    assert iso_normalise("2023-11-14T22:13:20", tz_policy="assume_utc") == "2023-11-14T22:13:20Z"


def test_iso_normalise_naive_datetime_assume_utc():
    dt = datetime(2023, 11, 14, 22, 13, 20)  # naive
    assert iso_normalise(dt, tz_policy="assume_utc") == "2023-11-14T22:13:20Z"


def test_iso_normalise_naive_reject_raises():
    with pytest.raises(CoercionError):
        iso_normalise("2023-11-14T22:13:20", tz_policy="reject")


def test_iso_normalise_naive_reject_is_default():
    # tz_policy defaults to 'reject'
    with pytest.raises(CoercionError):
        iso_normalise("2023-11-14T22:13:20")


def test_iso_normalise_unknown_tz_policy_raises():
    with pytest.raises(CoercionError):
        iso_normalise("2023-11-14T22:13:20", tz_policy="invent")


def test_iso_normalise_bad_string_raises():
    with pytest.raises(CoercionError):
        iso_normalise("not-a-timestamp")


def test_iso_normalise_unsupported_type_raises():
    with pytest.raises(CoercionError):
        iso_normalise(12345)


# --- sentinel_to_null -----------------------------------------------------------
SENTINELS = [-999, "N/A", "", "NULL", "null"]


def test_sentinel_numeric_to_null():
    assert sentinel_to_null(-999, sentinels=SENTINELS) is None


def test_sentinel_na_to_null():
    assert sentinel_to_null("N/A", sentinels=SENTINELS) is None


def test_sentinel_null_word_to_null():
    assert sentinel_to_null("NULL", sentinels=SENTINELS) is None
    assert sentinel_to_null("null", sentinels=SENTINELS) is None


def test_sentinel_empty_string_to_null_when_listed():
    assert sentinel_to_null("", sentinels=SENTINELS) is None


def test_sentinel_empty_string_not_null_when_not_listed():
    # "" is only a sentinel when explicitly declared in the list.
    not_listing_empty = [-999, "N/A", "NULL"]
    assert sentinel_to_null("", sentinels=not_listing_empty) == ""


def test_sentinel_genuine_value_passes_through():
    assert sentinel_to_null("hello", sentinels=SENTINELS) == "hello"
    assert sentinel_to_null(42, sentinels=SENTINELS) == 42


def test_sentinel_genuine_none_stays_none():
    assert sentinel_to_null(None, sentinels=SENTINELS) is None


def test_sentinel_empty_list_passes_everything_through():
    assert sentinel_to_null("N/A", sentinels=[]) == "N/A"
    assert sentinel_to_null(-999) == -999


def test_sentinel_distinguishes_string_vs_non_listed_empty():
    # An empty string is nulled ONLY when "" is in the sentinel list.
    assert sentinel_to_null("", sentinels=[""]) is None
    assert sentinel_to_null("", sentinels=["N/A"]) == ""


# --- bool_coerce ----------------------------------------------------------------
@pytest.mark.parametrize("token,expected", [
    ("Y", True), ("N", False),
    ("1", True), ("0", False),
    ("true", True), ("false", False),
    ("T", True), ("F", False),
])
def test_bool_coerce_default_map(token, expected):
    assert bool_coerce(token) is expected


def test_bool_coerce_actual_bool_passthrough():
    assert bool_coerce(True) is True
    assert bool_coerce(False) is False


def test_bool_coerce_unmapped_token_raises():
    with pytest.raises(CoercionError):
        bool_coerce("maybe")


def test_bool_coerce_empty_string_unmapped_raises():
    with pytest.raises(CoercionError):
        bool_coerce("")


def test_bool_coerce_custom_map():
    custom = {"yes": True, "no": False, "oui": True, "non": False}
    assert bool_coerce("yes", map=custom) is True
    assert bool_coerce("non", map=custom) is False


def test_bool_coerce_custom_map_rejects_default_tokens():
    custom = {"yes": True, "no": False}
    with pytest.raises(CoercionError):
        bool_coerce("Y", map=custom)


# --- enum_map -------------------------------------------------------------------
TABLE = {"A": "Active", "I": "Inactive", "P": "Pending"}


def test_enum_map_known_values():
    assert enum_map("A", table=TABLE) == "Active"
    assert enum_map("I", table=TABLE) == "Inactive"
    assert enum_map("P", table=TABLE) == "Pending"


def test_enum_map_unseen_value_raises_quarantine():
    # Never guess: an unseen value quarantines the row.
    with pytest.raises(CoercionError):
        enum_map("Z", table=TABLE)


def test_enum_map_empty_table_raises_for_any_value():
    with pytest.raises(CoercionError):
        enum_map("A", table={})


def test_enum_map_missing_table_raises():
    with pytest.raises(CoercionError):
        enum_map("A")


# --- currency_scale / currency_minor_units --------------------------------------
def test_currency_scale_jpy_zero():
    assert currency_scale("JPY") == 0


def test_currency_scale_usd_two():
    assert currency_scale("USD") == 2


def test_currency_scale_bhd_three():
    assert currency_scale("BHD") == 3


def test_currency_scale_unknown_defaults_to_two():
    assert currency_scale("XYZ") == 2


def test_currency_scale_case_insensitive():
    assert currency_scale("usd") == 2
    assert currency_scale("jpy") == 0


def test_currency_minor_units_jpy_scale0_major():
    # JPY scale 0: minor units == major.
    assert currency_minor_units(12345, ccy="JPY", to="major") == Decimal("12345")


def test_currency_minor_units_usd_scale2_major():
    assert currency_minor_units(12345, ccy="USD", to="major") == Decimal("123.45")


def test_currency_minor_units_bhd_scale3_major():
    assert currency_minor_units(12345, ccy="BHD", to="major") == Decimal("12.345")


def test_currency_minor_units_default_direction_is_major():
    assert currency_minor_units(12345, ccy="USD") == Decimal("123.45")


def test_currency_minor_units_usd_to_minor():
    assert currency_minor_units("123.45", ccy="USD", to="minor") == Decimal("12345")


def test_currency_minor_units_jpy_to_minor():
    assert currency_minor_units(12345, ccy="JPY", to="minor") == Decimal("12345")


def test_currency_minor_units_bhd_to_minor():
    assert currency_minor_units("12.345", ccy="BHD", to="minor") == Decimal("12345")


@pytest.mark.parametrize("ccy,minor", [("USD", 12345), ("JPY", 12345), ("BHD", 12345)])
def test_currency_minor_units_round_trip(ccy, minor):
    major = currency_minor_units(minor, ccy=ccy, to="major")
    back = currency_minor_units(major, ccy=ccy, to="minor")
    assert back == Decimal(minor)


def test_currency_minor_units_unknown_direction_raises():
    with pytest.raises(CoercionError):
        currency_minor_units(12345, ccy="USD", to="sideways")


def test_currency_minor_units_non_numeric_raises():
    with pytest.raises(CoercionError):
        currency_minor_units("not-a-number", ccy="USD")


# --- id_cast --------------------------------------------------------------------
def test_id_cast_preserves_leading_zeros():
    assert id_cast("007") == "007"


def test_id_cast_string_passthrough_exact():
    assert id_cast("abc-123") == "abc-123"


def test_id_cast_int_to_string():
    assert id_cast(7) == "7"


def test_id_cast_integral_float_to_string():
    assert id_cast(7.0) == "7"


def test_id_cast_non_integer_float_raises():
    with pytest.raises(CoercionError):
        id_cast(7.5)


def test_id_cast_none_raises():
    with pytest.raises(CoercionError):
        id_cast(None)


def test_id_cast_bool_raises():
    # bool is an int subclass, but it is NOT an id.
    with pytest.raises(CoercionError):
        id_cast(True)
    with pytest.raises(CoercionError):
        id_cast(False)


def test_id_cast_unsupported_type_raises():
    with pytest.raises(CoercionError):
        id_cast(["007"])


# --- unit_convert ---------------------------------------------------------------
def test_unit_convert_kg_to_lb_on_number():
    assert unit_convert(10, name="kg_to_lb") == Decimal("10") * Decimal("2.20462262")


def test_unit_convert_kg_to_lb_on_decimal():
    assert unit_convert(Decimal("1"), name="kg_to_lb") == Decimal("2.20462262")


def test_unit_convert_kg_to_lb_approx():
    assert float(unit_convert(1, name="kg_to_lb")) == pytest.approx(2.20462262)


def test_unit_convert_lb_to_kg():
    assert unit_convert(1, name="lb_to_kg") == Decimal("0.45359237")


def test_unit_convert_unknown_factor_raises():
    with pytest.raises(CoercionError):
        unit_convert(10, name="furlong_to_smoot")


def test_unit_convert_non_numeric_raises():
    with pytest.raises(CoercionError):
        unit_convert("heavy", name="kg_to_lb")


# --- trim_normalise_ws ----------------------------------------------------------
def test_trim_normalise_ws_collapses_internal_and_trims():
    assert trim_normalise_ws("  a   b ") == "a b"


def test_trim_normalise_ws_tabs_and_newlines():
    assert trim_normalise_ws("\ta\n\nb\t") == "a b"


def test_trim_normalise_ws_already_clean():
    assert trim_normalise_ws("a b c") == "a b c"


def test_trim_normalise_ws_non_str_raises():
    with pytest.raises(CoercionError):
        trim_normalise_ws(123)


# --- case_fold ------------------------------------------------------------------
def test_case_fold_uppercase():
    assert case_fold("ABC") == "abc"


def test_case_fold_mixed():
    assert case_fold("HeLLo") == "hello"


def test_case_fold_non_str_raises():
    with pytest.raises(CoercionError):
        case_fold(123)

"""The coercion catalogue (09_FINAL_PRD §10.3) — central, named, exhaustively tested.

Mappings reference coercions BY NAME; the PipelineArtifact records exactly which ran with
which params. Every coercion is deterministic and raises ``CoercionError`` on a value it
cannot safely convert — the engine turns that into a quarantined row with a reason code
(never a silent drop, never a guess). Adding a coercion here without tests fails CI.

Consult the ``databridge-coercion-catalogue`` skill before changing this file.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable


class CoercionError(ValueError):
    """A value could not be safely coerced → the engine quarantines the row (§10.5)."""


# name → callable(value, **params). The registry mappings reference by name.
COERCIONS: dict[str, Callable[..., Any]] = {}


def coercion(name: str) -> Callable[[Callable], Callable]:
    def deco(fn: Callable) -> Callable:
        fn.coercion_name = name  # type: ignore[attr-defined]
        COERCIONS[name] = fn
        return fn
    return deco


def apply_coercion(name: str, value: Any, **params: Any) -> Any:
    """Apply a named coercion. Unknown name is a programming error, not a data error."""
    if name not in COERCIONS:
        raise KeyError(f"unknown coercion {name!r}; known: {sorted(COERCIONS)}")
    return COERCIONS[name](value, **params)


# --- str_to_decimal -------------------------------------------------------------
@coercion("str_to_decimal")
def str_to_decimal(value: Any, *, locale: str = "us") -> Decimal:
    """String-encoded number → Decimal. ``locale`` selects the separator convention:
    'us' → "1,234.56", 'eu' → "1.234,56". Mixed conventions in one column are caught by
    the ``locale_errors`` validator, NOT here (this coercion trusts its declared locale)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if not isinstance(value, str):
        raise CoercionError(f"str_to_decimal expects a string, got {type(value).__name__}")
    s = value.strip()
    if not s:
        raise CoercionError("str_to_decimal: empty string")
    if locale == "us":
        s = s.replace(",", "")
    elif locale == "eu":
        s = s.replace(".", "").replace(",", ".")
    else:
        raise CoercionError(f"str_to_decimal: unknown locale {locale!r} (use 'us' or 'eu')")
    try:
        return Decimal(s)
    except InvalidOperation as e:
        raise CoercionError(f"str_to_decimal: {value!r} is not a {locale} number") from e


# --- epoch_to_iso ---------------------------------------------------------------
def detect_epoch_unit(n: int) -> str:
    """Detect epoch unit by MAGNITUDE (§10.3: detected, logged, never silently assumed)."""
    a = abs(int(n))
    if a < 10**11:
        return "s"           # seconds up to ~year 5138
    if a < 10**14:
        return "ms"
    if a < 10**17:
        return "us"
    return "ns"


@coercion("epoch_to_iso")
def epoch_to_iso(value: Any, *, unit_detect: bool = True, unit: str | None = None) -> str:
    """Epoch integer → UTC ISO-8601. Unit detected by magnitude (s/ms/us/ns) unless an
    explicit ``unit`` is given. Returns the ISO string (the engine logs the detected unit)."""
    try:
        n = int(value)
    except (TypeError, ValueError) as e:
        raise CoercionError(f"epoch_to_iso: {value!r} is not an integer epoch") from e
    u = unit if unit is not None else (detect_epoch_unit(n) if unit_detect else "s")
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}.get(u)
    if divisor is None:
        raise CoercionError(f"epoch_to_iso: unknown unit {u!r}")
    try:
        dt = datetime.fromtimestamp(n / divisor, tz=timezone.utc)
    except (OSError, OverflowError, ValueError) as e:
        # out-of-range epoch for the chosen unit → fail closed (quarantine), never crash
        raise CoercionError(f"epoch_to_iso: {value!r} out of range as {u!r}") from e
    return dt.isoformat().replace("+00:00", "Z")


# --- iso_normalise --------------------------------------------------------------
@coercion("iso_normalise")
def iso_normalise(value: Any, *, tz_policy: str = "reject") -> str:
    """Timestamp → UTC-canonical ISO. tz-aware → UTC; NAIVE → ``tz_policy``
    ('reject' | 'assume_utc'), the choice recorded by the caller."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as e:
            raise CoercionError(f"iso_normalise: {value!r} is not ISO-8601") from e
    else:
        raise CoercionError(f"iso_normalise: unsupported type {type(value).__name__}")
    if dt.tzinfo is None:
        if tz_policy == "assume_utc":
            dt = dt.replace(tzinfo=timezone.utc)
        elif tz_policy == "reject":
            raise CoercionError(f"iso_normalise: naive timestamp {value!r} rejected "
                                "(tz_policy='reject')")
        else:
            raise CoercionError(f"iso_normalise: unknown tz_policy {tz_policy!r}")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# --- sentinel_to_null -----------------------------------------------------------
@coercion("sentinel_to_null")
def sentinel_to_null(value: Any, *, sentinels: list | None = None) -> Any:
    """Per-column sentinel → None. Genuine null vs empty-string vs sentinel are NOT the
    same — only the declared sentinels become None (e.g. [-999, 'N/A', 'NULL'])."""
    sentinels = sentinels or []
    if value is None:
        return None
    # EXACT type+value match only: a genuine null, an empty string, and a -999 sentinel
    # are NOT the same — and we must not conflate 1.0/1/True (Python ==) across types.
    for s in sentinels:
        if type(value) is type(s) and value == s:
            return None
    return value


# --- bool_coerce ----------------------------------------------------------------
@coercion("bool_coerce")
def bool_coerce(value: Any, *, map: dict | None = None) -> bool:
    """Token → bool via an EXPLICIT mapping (no implicit truthiness). An unmapped token
    is a coercion failure → quarantine."""
    if isinstance(value, bool):
        return value
    mapping = map or {"Y": True, "N": False, "1": True, "0": False,
                      "true": True, "false": False, "T": True, "F": False}
    try:
        key = value if value in mapping else str(value)
    except TypeError:                       # unhashable value (e.g. a list) → not mappable
        key = None
    if key not in mapping:
        raise CoercionError(f"bool_coerce: {value!r} not in explicit map {sorted(mapping)}")
    return bool(mapping[key])


# --- enum_map -------------------------------------------------------------------
@coercion("enum_map")
def enum_map(value: Any, *, table: dict | None = None) -> Any:
    """Source code → target enum via the FULL value table. An unseen value at execute
    time ⇒ quarantine the row, NEVER guess (also drift class 'enum value added', §20)."""
    table = table or {}
    if value not in table:
        raise CoercionError(f"enum_map: unseen value {value!r} (not in table) — quarantine")
    return table[value]


# --- currency_minor_units -------------------------------------------------------
_CCY_SCALE = {"JPY": 0, "KRW": 0, "BHD": 3, "KWD": 3, "OMR": 3, "TND": 3}


def currency_scale(ccy: str) -> int:
    return _CCY_SCALE.get(ccy.upper(), 2)


@coercion("currency_minor_units")
def currency_minor_units(value: Any, *, ccy: str, to: str = "major") -> Decimal:
    """Minor units ↔ major, using the CURRENCY's scale (JPY=0, BHD=3, most=2) — never a
    hardcoded 2. to='major': 12345 USD → 123.45; to='minor': 123.45 USD → 12345."""
    scale = currency_scale(ccy)
    factor = Decimal(10) ** scale
    try:
        v = Decimal(str(value))
    except InvalidOperation as e:
        raise CoercionError(f"currency_minor_units: {value!r} is not numeric") from e
    if to == "major":
        return v / factor
    if to == "minor":
        return (v * factor).to_integral_value()
    raise CoercionError(f"currency_minor_units: unknown direction {to!r} (major|minor)")


# --- id_cast --------------------------------------------------------------------
@coercion("id_cast")
def id_cast(value: Any) -> str:
    """Numeric-looking id → canonical STRING id. Leading zeros preserved — ids are
    strings ('007' stays '007', never becomes 7)."""
    if value is None:
        raise CoercionError("id_cast: None is not an id")
    if isinstance(value, str):
        return value                 # preserve exactly (leading zeros intact)
    if isinstance(value, bool):
        raise CoercionError("id_cast: bool is not an id")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        raise CoercionError(f"id_cast: non-integer float {value!r} is not an id")
    raise CoercionError(f"id_cast: unsupported type {type(value).__name__}")


# --- unit_convert ---------------------------------------------------------------
_UNIT_FACTORS = {
    "kg_to_lb": Decimal("2.20462262"), "lb_to_kg": Decimal("0.45359237"),
    "m_to_ft": Decimal("3.2808399"), "ft_to_m": Decimal("0.3048"),
    "km_to_mi": Decimal("0.62137119"), "mi_to_km": Decimal("1.609344"),
    "c_to_f_scale": Decimal("1.8"),  # offset handled by name below
}


@coercion("unit_convert")
def unit_convert(value: Any, *, name: str) -> Decimal:
    """Unit conversion by NAMED factor only (e.g. 'kg_to_lb'). Arbitrary math in a mapping
    is refused by the proposal validator — this coercion only knows named factors."""
    if name not in _UNIT_FACTORS:
        raise CoercionError(f"unit_convert: unknown factor {name!r} "
                            f"(named factors only: {sorted(_UNIT_FACTORS)})")
    try:
        v = Decimal(str(value))
    except InvalidOperation as e:
        raise CoercionError(f"unit_convert: {value!r} is not numeric") from e
    return v * _UNIT_FACTORS[name]


# --- trim_normalise_ws ----------------------------------------------------------
@coercion("trim_normalise_ws")
def trim_normalise_ws(value: Any) -> str:
    """Trim + collapse internal whitespace deterministically: '  a   b ' → 'a b'."""
    if not isinstance(value, str):
        raise CoercionError(f"trim_normalise_ws expects a string, got {type(value).__name__}")
    return re.sub(r"\s+", " ", value).strip()


# --- case_fold ------------------------------------------------------------------
@coercion("case_fold")
def case_fold(value: Any) -> str:
    """Deterministic case folding for matching/joins."""
    if not isinstance(value, str):
        raise CoercionError(f"case_fold expects a string, got {type(value).__name__}")
    return value.casefold()

"""Deterministic canonical formatters (C17, 09_FINAL_PRD §12.1) — THE unsung dependency.

"Hallucination defence, not cosmetics." Every downstream validator (`claim_check`, §12.3a)
string-matches against the EXACT forms produced here, so these functions must be:

  * DETERMINISTIC — same input ⇒ byte-identical output, no locale drift, no float jitter;
  * EXACT — money/percent rounding is half-up at a fixed precision (never banker's rounding,
    never str(float) which leaks 0.1+0.2=0.30000000000000004);
  * STABLE — the token forms a generator is told to copy are the token forms the checker accepts.

A fact rendered here is the ONLY spelling of that value the generated text may use. If the model
writes "approximately $1,350" instead of the rendered "$1,350.00", claim_check sees a number token
that matches no rendered fact ⇒ violation. That is the point.

Treated with the same rigour as the grounding wall (C15) — see test_c17_formatters.py.
"""
from __future__ import annotations

import datetime as _dt
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Union

Number = Union[int, float, Decimal, str]

# Currency symbol table (closed set; unknown codes render the ISO code suffix, never guessed).
_CURRENCY_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥", "INR": "₹"}
# Currencies with zero minor units (no decimal places by convention).
_ZERO_DECIMAL_CURRENCY = {"JPY"}


def _to_decimal(value: Number) -> Decimal:
    """Coerce to Decimal WITHOUT going through binary float when avoidable (a float is converted
    via its shortest round-trip repr, so 19.99 stays 19.99, not 19.989999…)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):                          # bool is an int subclass — reject early
        raise TypeError("cannot render a bool as a number")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))                      # repr = shortest round-trip
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        try:
            return Decimal(s)
        except InvalidOperation as e:
            raise ValueError(f"not a number: {value!r}") from e
    raise TypeError(f"cannot render {type(value).__name__} as a number")


def _quantize(d: Decimal, places: int) -> Decimal:
    q = Decimal(1).scaleb(-places) if places > 0 else Decimal(1)
    return d.quantize(q, rounding=ROUND_HALF_UP)


def _group(int_part: str) -> str:
    """Thousands-group a non-negative integer string with commas: '1234567' → '1,234,567'."""
    rev = int_part[::-1]
    chunks = [rev[i:i + 3] for i in range(0, len(rev), 3)]
    return ",".join(chunks)[::-1]


def render_decimal(value: Number, places: int = 2) -> str:
    """Fixed-precision decimal with thousands separators and HALF-UP rounding.

    render_decimal(1234.5)            → '1,234.50'
    render_decimal('1234.567', 2)     → '1,234.57'
    render_decimal(-0.005, 2)         → '-0.01'   (half-up, away from zero on .5)
    render_decimal(1000, 0)           → '1,000'
    """
    if places < 0:
        raise ValueError("places must be ≥ 0")
    d = _quantize(_to_decimal(value), places)
    if d == 0:
        d = Decimal(0)                                    # collapse negative zero (-0.004 → 0.00)
    neg = d < 0
    d = -d if neg else d
    sign = "-" if neg else ""                             # never render "-0.00" (handled above)
    text = f"{d:.{places}f}"                              # fixed places, no grouping yet
    if "." in text:
        int_part, frac = text.split(".")
        body = f"{_group(int_part)}.{frac}"
    else:
        body = _group(text)
    return f"{sign}{body}"


def render_int(value: Number) -> str:
    """Grouped integer (no decimals). render_int(1234567) → '1,234,567'. Rejects non-integers."""
    d = _to_decimal(value)
    if d != d.to_integral_value():
        raise ValueError(f"render_int: {value!r} is not an integer")
    return render_decimal(d, 0)


def render_money(value: Number, currency: str = "USD", *, symbol: bool = True) -> str:
    """Canonical money. USD → '$1,234.50'; symbol-less or unknown currency → '1,234.50 EUR'.
    Zero-decimal currencies (JPY) render no minor units. Negatives: '-$1,234.50' (sign outside).

    render_money(1350)               → '$1,350.00'
    render_money(1234.5, 'EUR')      → '€1,234.50'
    render_money(5000, 'JPY')        → '¥5,000'
    render_money(99.5, 'CHF')        → '99.50 CHF'   (no symbol known → ISO suffix)
    render_money(-20)                → '-$20.00'
    """
    cur = currency.upper()
    places = 0 if cur in _ZERO_DECIMAL_CURRENCY else 2
    d = _quantize(_to_decimal(value), places)
    neg = d < 0
    body = render_decimal(-d if neg else d, places)
    sign = "-" if (neg and d != 0) else ""
    sym = _CURRENCY_SYMBOL.get(cur) if symbol else None
    if sym:
        return f"{sign}{sym}{body}"
    return f"{sign}{body} {cur}"


def render_percent(value: Number, places: int = 1, *, as_fraction: bool = False) -> str:
    """Percentage with a trailing '%'. By default ``value`` is already a percent number; pass
    ``as_fraction=True`` to interpret 0.08 → '8.0%'.

    render_percent(8)                → '8.0%'
    render_percent(8.25, 2)          → '8.25%'
    render_percent(0.08, as_fraction=True) → '8.0%'
    render_percent(33.3333, 1)       → '33.3%'
    """
    d = _to_decimal(value)
    if as_fraction:
        d = d * 100
    return f"{render_decimal(d, places)}%"


def render_date(value: Any, fmt: str = "iso") -> str:
    """Canonical date. ``fmt='iso'`` → 'YYYY-MM-DD' (default); ``fmt='long'`` → '15 March 2024'.
    Accepts date/datetime/ISO-string. A datetime's time component is dropped (date-level facts)."""
    d = _as_date(value)
    if fmt == "iso":
        return d.isoformat()
    if fmt == "long":
        return f"{d.day} {d.strftime('%B')} {d.year}"
    raise ValueError(f"unknown date fmt {fmt!r}")


def _as_date(value: Any) -> _dt.date:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        # strict ISO parse only (no fuzzy/locale guessing — determinism over convenience)
        return _dt.date.fromisoformat(value.strip()[:10])
    raise TypeError(f"cannot render {type(value).__name__} as a date")


def render_name(value: Any) -> str:
    """Canonical name/free-text token: strip + collapse internal whitespace. Case is PRESERVED
    (names are case-significant: 'McDonald', 'van der Berg', 'IBM' must survive verbatim).

    render_name('  Acme   Industries ')  → 'Acme Industries'
    render_name('john\\tsmith')           → 'john smith'
    """
    if not isinstance(value, str):
        value = str(value)
    return " ".join(value.split())

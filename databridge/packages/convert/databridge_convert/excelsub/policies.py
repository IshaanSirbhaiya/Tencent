"""Excel read policies + typed error cells + refusal codes (09_FINAL_PRD §14.9/§14.12/
§14.18).

Policies are explicit and recorded — the reader never silently fills, drops, or
auto-converts. Defaults follow §14:
  * merged cells  → ``fill_down`` for header context, ``anchor_only`` for data (§14.9)
  * error cells   → ``null_warn`` (the cell becomes None, a warning is recorded) (§14.12)
  * hidden content → EXCLUDED unless explicitly opted in (audit honesty, §14.10)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from ..adapters._util import AdapterError

# --- refusal codes (§14.18) -----------------------------------------------------
ENCRYPTED_WORKBOOK = "ENCRYPTED_WORKBOOK"
REQUIRE_RECALCULATED = "REQUIRE_RECALCULATED"


class ExcelRefusal(AdapterError):
    """A typed Excel-subsystem refusal (§5 rule: refuse, name the reason, never fabricate).

    Carries a stable ``code`` (e.g. ``ENCRYPTED_WORKBOOK``) so the router/frontend can render
    it verbatim and tests can assert on it. Subclasses AdapterError so existing adapter
    error handling catches it too."""

    def __init__(self, code: str, message: str, nearest_alternative: Optional[str] = None):
        self.code = code
        self.nearest_alternative = nearest_alternative
        super().__init__(f"{code}: {message}")


# --- typed error cell (§14.12) --------------------------------------------------
@dataclass(frozen=True)
class ExcelError:
    """A spreadsheet error cell value, typed (NOT a string). ``code`` is the literal
    Excel error token, e.g. ``#N/A`` / ``#VALUE!`` / ``#DIV/0!``."""

    code: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.code


# The closed set of Excel error tokens we recognise (others kept verbatim under "#?!").
EXCEL_ERROR_CODES = frozenset({
    "#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A",
    "#GETTING_DATA", "#SPILL!", "#CALC!", "#FIELD!", "#UNKNOWN!",
})


MergedPolicy = Literal["fill_down", "anchor_only", "refuse"]
ErrorPolicy = Literal["null_warn", "keep", "quarantine_row", "hard_fail"]


@dataclass(frozen=True)
class ReadOptions:
    """Options governing an Excel read. All defaults are §14-conformant and audit-honest."""

    # §14.1 sheet selection: "visible" (default), "all", or an explicit list of names.
    sheets: Literal["visible", "all"] | list[str] = "visible"
    # §14.10 hidden rows/cols inclusion — default EXCLUDED (hidden often means stale).
    include_hidden: bool = False
    # §14.9 merged-cell policy. Header-context ranges fill down; data ranges anchor-only.
    merged_header: MergedPolicy = "fill_down"
    merged_data: MergedPolicy = "anchor_only"
    # §14.12 error-cell policy.
    errors: ErrorPolicy = "null_warn"
    # §14.5 recalculation is OUT of scope; if the caller demands it we REFUSE.
    require_recalculated: bool = False
    # §14.11 workbook locale hint for downstream str_to_decimal (does not change reads).
    locale_hint: str | None = None
    # The set of merged ranges (cellref like "A1:B1") the caller declares as header context.
    # Anything not listed is treated as data (anchor_only). The reader also auto-treats a
    # merge that overlaps the detected header row as header context.
    header_merge_ranges: frozenset[str] = field(default_factory=frozenset)

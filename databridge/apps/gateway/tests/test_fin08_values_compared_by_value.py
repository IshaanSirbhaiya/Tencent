"""FIN-08 (finance dry run, 2026-10-02): `/brain/query`'s answer checks compare values by VALUE.

The dry run dropped correct model answers with violations `['33','600','000.00']` (a total written
with thousands separators against evidence holding `33600000.00`) and `['-0029']` (the hyphen in
`CM-2026-0029` read as a minus sign). Both checks — `main.unsupported_value_tokens` /
`main.narration_claim_violations` and `llm.ground_narration` — now read values through
`databridge_core.value_forms`.

Every case is derived from fixture DATA in two estates: the finance core-banking facility seed and
the workspace (Larkspur) invoice table. The evidence line carries the value as stored; the answer
writes it the way a person (or a model) does — grouped, two decimals, currency code flush against
it, an identifier with a typographic hyphen. A different value or a different identifier is still
refused.
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from databridge_gateway.llm import ground_narration
from databridge_gateway.main import narration_claim_violations, unsupported_value_tokens

REPO = Path(__file__).resolve().parents[3]


def _finance_row() -> tuple[str, str, str]:
    """(facility id, limit_amount as stored, currency) — the first facility row of the seed."""
    sql = (REPO / "fixtures/packs/finance/core_banking/seed/05_facility_01.sql").read_text(
        encoding="utf-8")
    m = re.search(r"\('(CB-F-\d+)', '[^']*', '[^']*', ([\d.]+), [\d.]+, '([A-Z]{3})'", sql)
    assert m, "no facility row in the finance seed"
    return m.group(1), m.group(2), m.group(3)


def _workspace_row() -> tuple[str, str, str]:
    """(invoice no, total as stored, currency from the column name) — the first invoice row."""
    sql = (REPO / "fixtures/workspace_demo/db/01_schema_and_data.sql").read_text(encoding="utf-8")
    head = re.search(r"INSERT INTO invoices \(([^)]*)\) VALUES\s*\(([^)]*)\)", sql)
    assert head, "no invoice row in the workspace fixture"
    cols = [c.strip() for c in head.group(1).split(",")]
    vals = [v.strip().strip("'") for v in head.group(2).split(",")]
    total = next(c for c in cols if c.startswith("total_"))
    return vals[cols.index(cols[0])], vals[cols.index(total)], total.split("_", 1)[1].upper()


ESTATES = {"finance": _finance_row, "workspace": _workspace_row}


def _case(estate: str):
    ident, stored, cur = ESTATES[estate]()
    value = Decimal(stored)
    assert value > 1000, "the fixture value must be large enough to carry a thousands separator"
    evidence = f"- [C1] record {ident}: amount: {stored}"
    grouped = f"{value:,.2f}"
    assert "," in grouped and grouped != stored
    return ident, value, cur, evidence, grouped


def _bump_identifier(ident: str) -> str:
    """The same identifier with its last digit changed — a DIFFERENT record."""
    m = re.search(r"(\d)(?!.*\d)", ident)
    d = (int(m.group(1)) + 1) % 10
    return ident[:m.start()] + str(d) + ident[m.end():]


def _all_violations(answer: str, evidence: str) -> list[str]:
    return (unsupported_value_tokens(answer, evidence)
            + narration_claim_violations(answer, evidence)
            + ground_narration(answer, evidence)["unbound"])


@pytest.mark.parametrize("estate", sorted(ESTATES))
def test_a_grouped_amount_with_a_flush_currency_code_is_supported(estate):
    ident, _value, cur, evidence, grouped = _case(estate)
    for answer in (f"The amount for {ident} is {cur}{grouped}.",
                   f"The amount for {ident} is {grouped} {cur}.",
                   f"The amount for {ident} is {grouped.rstrip('0').rstrip('.')}."):
        assert _all_violations(answer, evidence) == [], answer
        bound = ground_narration(answer, evidence)["bound"]
        assert {b["token"] for b in bound} >= {ident}


@pytest.mark.parametrize("estate", sorted(ESTATES))
def test_an_identifier_is_one_token_never_signed_numbers(estate):
    ident, _value, _cur, evidence, _grouped = _case(estate)
    typographic = ident.replace("-", "‑")
    for answer in (f"Record {ident} is on file.", f"Record {typographic} is on file."):
        assert _all_violations(answer, evidence) == [], answer
        assert not any(v.startswith("-") for v in narration_claim_violations(answer, evidence))


@pytest.mark.parametrize("estate", sorted(ESTATES))
def test_a_different_value_is_still_refused(estate):
    ident, value, cur, evidence, _grouped = _case(estate)
    wrong = f"{value + 100:,.2f}"
    answer = f"The amount for {ident} is {cur}{wrong}."
    assert wrong in unsupported_value_tokens(answer, evidence)
    assert wrong in narration_claim_violations(answer, evidence)
    assert wrong in ground_narration(answer, evidence)["unbound"]


@pytest.mark.parametrize("estate", sorted(ESTATES))
def test_a_different_identifier_is_still_refused(estate):
    ident, _value, _cur, evidence, _grouped = _case(estate)
    other = _bump_identifier(ident)
    assert other != ident
    answer = f"Record {other} is on file."
    assert other in unsupported_value_tokens(answer, evidence)
    assert other in ground_narration(answer, evidence)["unbound"]
    # A prefix of the real identifier is a different identifier, not a substring hit.
    prefix = ident[:-1]
    if re.search(r"\d", prefix) and re.search(r"[A-Za-z]", prefix):
        assert prefix in unsupported_value_tokens(f"Record {prefix} is on file.", evidence)


def test_the_dry_run_memo_identifier_from_the_finance_fixtures():
    """The exact FIN-08 shape: a credit memo id from the fixture tree, in the evidence and the
    answer, used to produce the violation ``-<last group>``."""
    memo = sorted((REPO / "fixtures/packs/finance/credit_memos").rglob("CM-*.md"))[0]
    memo_id = re.match(r"(CM-\d{4}-\d{4})", memo.name).group(1)
    evidence = f"- [C1] credit_memo {memo_id}: recommendation: approve"
    answer = f"Credit memo {memo_id} recommends approval."
    assert narration_claim_violations(answer, evidence) == []
    assert unsupported_value_tokens(answer, evidence) == []


@pytest.mark.parametrize("estate", sorted(ESTATES))
def test_the_comparison_is_by_value_in_both_directions(estate):
    """Evidence written grouped, answer written plain: no spelling of one is a substring of the
    other, so only a VALUE comparison binds them."""
    ident, value, _cur, _evidence, grouped = _case(estate)
    evidence = f"- [C1] record {ident}: amount: {grouped}"
    plain = f"{value:.3f}"
    assert plain not in evidence and "," in grouped
    answer = f"The amount for {ident} is {plain}."
    assert _all_violations(answer, evidence) == [], answer
    assert {b["token"] for b in ground_narration(answer, evidence)["bound"]} == {ident, plain}

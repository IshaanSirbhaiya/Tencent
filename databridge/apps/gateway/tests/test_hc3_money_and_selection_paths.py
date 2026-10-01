"""HC3 (2026-10-01): the two items HC2 deferred (SWEEP hardcode audit #15 and #16).

**#15 currency.** The billing API baked the currency into field names
(`estimated_patient_payable_sgd`). The card now also carries `billing.currency`, read from the
active pack's `coverage_rules.yaml` and cited by a pointer into it, and a currency-NEUTRAL alias
for every amount key (`estimated_patient_payable`, `known_subtotal`, `list_price`). The old
`_<ccy>` keys stay for one release so a client built before this change keeps working.

**#16 selection paths.** `/intake/upload-file` sends `extraction.selection_paths`, the field
paths the grounding step treats as tick-box selections (`extract.CHECKBOX_SENSITIVE`, the one
definition the masking itself uses). The frontend drops its copied list and still shows the
"take these from the original" safety notice. If the gateway sends no paths, it shows the notice
conservatively for every unresolved field.

Every expectation below is read from the data (the YAML, the engine's own set, the response) and
never restated. Each has a non-vacuous guard.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import AUTH_HEADERS

from databridge_gateway import create_app

GATEWAY_SRC = Path(__file__).resolve().parents[1] / "databridge_gateway"
INTAKE_ROUTES = GATEWAY_SRC / "intake_routes.py"
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
CARD = FRONTEND / "components" / "frontdesk" / "card-sections.tsx"
REPORT = FRONTEND / "components" / "intake" / "extraction-report.tsx"
TYPES = FRONTEND / "lib" / "intake-types.ts"


@pytest.fixture(scope="module", autouse=True)
def force_gold():
    """No test may depend on a live model (CLAUDE.md)."""
    previous = os.environ.get("DATABRIDGE_FORCE_GOLD")
    os.environ["DATABRIDGE_FORCE_GOLD"] = "1"
    yield
    if previous is None:
        os.environ.pop("DATABRIDGE_FORCE_GOLD", None)
    else:
        os.environ["DATABRIDGE_FORCE_GOLD"] = previous


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        yield c


def _rules_yaml() -> tuple[Path, dict]:
    from databridge_intake.paths import config_dir

    path = config_dir() / "coverage_rules.yaml"
    return path, yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cards(client):
    """Every chit in the corpus, read through the API exactly as the counter reads it."""
    from databridge_intake.paths import fixtures_dir

    out = []
    for path in sorted((fixtures_dir() / "chits").glob("*")):
        r = client.post("/intake/upload", json={"document_id": f"hc3__{path.name}",
                                                "text": path.read_text(encoding="utf-8")})
        assert r.status_code == 200, r.text
        out.append(r.json())
    assert out, "no chit fixtures: the corpus moved"
    return out


def _code(path: Path) -> str:
    """Source with comments removed, so a sentence ABOUT a removed literal does not count."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"\{/\*.*?\*/\}", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    text = re.sub(r"(?m)\s//\s.*$", "", text)
    return text


def _python_code(path: Path) -> str:
    """Python source without comments and docstrings."""
    import ast
    import io
    import tokenize

    src = path.read_text(encoding="utf-8")
    doc_lines: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and \
                isinstance(getattr(body[0], "value", None), ast.Constant) and \
                isinstance(body[0].value.value, str):
            doc_lines.update(range(body[0].lineno, body[0].end_lineno + 1))
    kept = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT or tok.start[0] in doc_lines:
            continue
        kept.append(tok.string)
    return " ".join(kept)


# --- #15: currency ---------------------------------------------------------------------------

def test_the_card_names_the_currency_the_pack_declares(cards):
    path, data = _rules_yaml()
    declared = data.get("currency")
    assert isinstance(declared, str) and declared, f"{path} declares no currency"
    for card in cards:
        billing = card["billing"]
        assert billing["currency"] == declared
        pointer = billing["field_sources"]["currency"]
        # The pointer resolves: it names the rules file and the key that holds the value.
        assert pointer["source_id"] == path.name
        assert pointer["kind"] == "table_cell"
        assert data[pointer["column"]] == billing["currency"]


def test_every_amount_has_a_currency_neutral_alias_and_the_old_key_stays(cards):
    _, data = _rules_yaml()
    suffix = "_" + data["currency"].lower()
    amounts_seen = 0

    def walk(node):
        nonlocal amounts_seen
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str) and key.endswith(suffix) and len(key) > len(suffix):
                    neutral = key[: -len(suffix)]
                    assert neutral in node, f"{key} has no neutral alias {neutral}"
                    assert node[neutral] == value
                    if value is not None and not isinstance(value, dict):
                        amounts_seen += 1
                walk(value)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for card in cards:
        billing = card["billing"]
        walk(billing)
        walk(card["coverage"]["authorised_tests"])
        # Backward compatible: the deprecated keys are still sent.
        for legacy in ("estimated_patient_payable", "known_subtotal"):
            assert legacy + suffix in billing and legacy in billing
        # A neutral amount carries the same pointer its legacy key does.
        for key, ptr in list(billing["field_sources"].items()):
            if key.endswith(suffix):
                assert billing["field_sources"][key[: -len(suffix)]] == ptr
    assert amounts_seen > 0, "no fixture priced anything: the alias check is vacuous"


def test_no_currency_is_written_into_the_route_code():
    _, data = _rules_yaml()
    code = _python_code(INTAKE_ROUTES)
    assert "currency" in code, "the route does not emit a currency at all"
    assert not re.search(re.escape(data["currency"]), code, re.I)


def test_the_frontend_reads_neutral_amounts_and_the_currency():
    code = _code(CARD)
    # Read BY the neutral name, not only through the deprecated `<name>_<ccy>` suffix fallback.
    assert "rec.estimated_patient_payable" in code and "rec.known_subtotal" in code
    assert "rec.currency" in code
    assert not re.search(r"\bSGD\b", code) and "_sgd" not in code
    types = _code(TYPES)
    for field in ("currency", "estimated_patient_payable", "known_subtotal"):
        assert re.search(rf"\b{field}\?:", types), f"BillingDraft lacks {field}"


# --- #16: selection paths --------------------------------------------------------------------

def test_upload_file_sends_the_engines_selection_paths(client):
    from databridge_intake.extract import CHECKBOX_SENSITIVE
    from databridge_intake.paths import fixtures_dir

    assert CHECKBOX_SENSITIVE, "the engine's selection set is empty: the check is vacuous"
    chit = sorted((fixtures_dir() / "chits").glob("*"))[0]
    r = client.post("/intake/upload-file",
                    files={"file": (chit.name, chit.read_bytes(), "text/plain")},
                    data={"document_id": f"hc3_paths__{chit.name}"})
    assert r.status_code == 200, r.text
    assert r.json()["extraction"]["selection_paths"] == sorted(CHECKBOX_SENSITIVE)


def test_no_selection_path_is_written_into_route_or_frontend_code():
    from databridge_intake.extract import CHECKBOX_SENSITIVE

    assert CHECKBOX_SENSITIVE
    route = _python_code(INTAKE_ROUTES)
    report = _code(REPORT)
    for path in CHECKBOX_SENSITIVE:
        assert path not in route, f"{path} is restated in intake_routes"
        assert path not in report, f"{path} is restated in extraction-report.tsx"
    assert "LEGACY_SELECTION_PATHS" not in report


def test_the_tick_box_notice_is_kept_and_is_conservative_without_paths():
    report = _code(REPORT)
    assert "x?.selection_paths" in report
    assert 'data-testid="withheld-fields"' in report
    # With no paths from the gateway, every unresolved field is listed rather than none.
    assert 'data-testid="selection-paths-unknown"' in report
    assert re.search(r":\s*card\.unresolved\b", report), \
        "with no selection_paths the notice must fall back to every unresolved field"

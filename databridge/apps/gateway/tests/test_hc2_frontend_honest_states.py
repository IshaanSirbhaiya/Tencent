"""HC2 (2026-10-01), SWEEP hardcode audit — the frontend's honest states.

* The intake card, the extraction report and the queue say when values are an OFFLINE SAMPLE
  (`offline_gold`), and only `offline_gold === false` is ever presented as a live read.
* `/impact` labels its minutes from the response's `saving_basis`, never as "measured".
* #14 identity-panel, #15 card-sections currency, #16 extraction-report labels, #18 stats-panel,
  #19 filter-bar, #22 the queue h1, #23 platform example prompts: no recalled domain literal.

Source-level, because the frontend has no unit runner in this repo (`npx tsc --noEmit` is the only
frontend gate the fix brief allows) — the same approach as `test_frontend_registry_is_live.py`.
Each assertion names the construct that made the state dishonest, so restoring it fails here.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
REPO = FRONTEND.parents[1]

CARD = FRONTEND / "components" / "frontdesk" / "card-sections.tsx"
CARD_PAGE = FRONTEND / "app" / "frontdesk" / "[id]" / "page.tsx"
QUEUE_PAGE = FRONTEND / "app" / "frontdesk" / "page.tsx"
REPORT = FRONTEND / "components" / "intake" / "extraction-report.tsx"
IDENTITY = FRONTEND / "components" / "frontdesk" / "identity-panel.tsx"
STATS = FRONTEND / "components" / "brain" / "stats-panel.tsx"
FILTER = FRONTEND / "components" / "graph" / "filter-bar.tsx"
AGENTS = FRONTEND / "components" / "agents" / "agents-panel.tsx"
DATA_AGENT = FRONTEND / "components" / "data" / "data-agent.tsx"
IMPACT = FRONTEND / "app" / "impact" / "page.tsx"


def _code(path: Path) -> str:
    """The file with comments removed, so a sentence ABOUT a removed literal does not count."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"\{/\*.*?\*/\}", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?<![:\"'])//[^\n]*", "", text)


def _domain_word() -> re.Pattern:
    path = REPO / "packages" / "graph" / "tests" / "test_no_domain_branching.py"
    spec = importlib.util.spec_from_file_location("_ndb_terms_hc2_fe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._WORD


# --- offline_gold is visible -------------------------------------------------------------------

def _banner() -> str:
    code = _code(CARD)
    start = code.index("export function ReadSourceBanner")
    return code[start:code.index("\n}\n", start) + 3]


def test_the_banner_says_offline_sample_and_only_false_reads_as_live():
    body = _banner()
    assert "Offline sample, not a live read" in body
    # Only an explicit `false` is a live read. `!card.offline_gold` would render null/absent
    # (an UNRECORDED read) as live — the silent default the audit found.
    assert "card.offline_gold === false" in body
    assert "card.offline_gold === true" in body
    assert "!card.offline_gold" not in body and "offline_gold ?? false" not in body
    assert '"unrecorded"' in body                       # the third state has its own words


@pytest.mark.parametrize("path", (CARD_PAGE, REPORT), ids=lambda p: p.parent.name + "/" + p.name)
def test_every_card_surface_renders_the_banner(path):
    assert "<ReadSourceBanner card=" in _code(path)


def test_the_queue_row_labels_an_offline_or_unrecorded_read():
    code = _code(QUEUE_PAGE)
    assert "row.offline_gold !== false" in code         # null/absent is labelled too
    assert "offline sample" in code


def test_the_types_carry_offline_gold_on_card_and_queue_row():
    types = _code(FRONTEND / "lib" / "intake-types.ts")
    assert types.count("offline_gold?: boolean | null;") >= 2


# --- /impact says "estimated" -------------------------------------------------------------------

def test_impact_labels_minutes_from_saving_basis():
    code = _code(IMPACT)
    assert "saving_basis" in code and "basis.note" in code
    # The word shown is computed from the basis; with no basis it is NOT "measured".
    assert 'basis?.minutes === "estimate" ? "estimated"' in code
    for claim in ("Saved (measured)", "Measured over", "Measuring…", "No intakes measured"):
        assert claim not in code, claim
    assert code.count("{minutesAre}") >= 4               # header, tiles, column


# --- per-item literals ---------------------------------------------------------------------------

def test_identity_panel_names_the_packs_identifier_not_nric():                       # #14
    code = _code(IDENTITY)
    assert "{nouns.identifier}" in code
    assert not re.search(r"NRIC|passport", code, re.I)


def test_billing_reads_its_currency_from_the_api_not_a_literal():                    # #15
    code = _code(CARD)
    assert not re.search(r"\bSGD\b", code)
    assert "billingAmounts(b)" in code
    assert "_sgd" not in code                             # no field read by its baked-in name


def test_extraction_report_computes_labels_and_prefers_the_servers_paths():          # #16
    code = _code(REPORT)
    assert "FIELD_LABELS" not in code and "CHECKBOX_DERIVED" not in code
    # HC3 completed the deferral: the paths come only from the server (no transitional copy);
    # `test_hc3_money_and_selection_paths.py` pins the conservative notice when none are sent.
    assert "x?.selection_paths" in code and "LEGACY_SELECTION_PATHS" not in code


def test_stats_panel_has_no_fixture_filename_table():                                # #18
    code = _code(STATS)
    assert not re.search(r"_\d{3}\.(eml|txt|pdf)", code)
    assert "SOURCE_ICON" not in code


def test_filter_bar_placeholder_names_no_synthetic_entity():                         # #19
    code = _code(FILTER)
    m = re.search(r'placeholder="([^"]*)"', code)
    assert m and m.group(1)                               # non-vacuous: the placeholder exists
    assert "Meridian" not in m.group(1)


def test_queue_heading_is_built_from_pack_nouns():                                   # #22
    code = _code(QUEUE_PAGE)
    assert "Front desk queue" not in code
    assert "{nouns.operator_surface} {nouns.queue.toLowerCase()}" in code


@pytest.mark.parametrize("path", (AGENTS, DATA_AGENT), ids=lambda p: p.name)
def test_platform_examples_name_no_domain(path):                                     # #23
    code = _code(path)
    word = re.compile(_domain_word().pattern + r"|invoice", re.I)
    block = code[code.index("EXAMPLES"):]
    block = block[:block.index("];") + 2]
    strings = re.findall(r'"([^"]{8,})"', block)
    assert len(strings) >= 2                              # non-vacuous: label + text at least
    assert [s for s in strings if word.search(s)] == []

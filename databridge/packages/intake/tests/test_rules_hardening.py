"""Regression tests from the Phase 0 review gate.

Each block below corresponds to a defect the reviewers found in the first cut of `rules.py`.
They are kept together, and named for the failure rather than the feature, so that a future
change that reintroduces one fails with an explanatory message rather than a bare assert.
"""
from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from databridge_intake import RulesError, load_rules, rules
from databridge_intake.rules import CoverageRules


def _minimal(**overrides) -> dict:
    base = {
        "version": 1,
        "issuers": [{"code": "AAA", "name": "A", "kind": "insurer", "billing_party": "insurer"}],
        "packages": [{"id": "r1", "issuer_code": "AAA", "package_code": "P1", "name": "P",
                      "billing": {"covered_by": "insurer"}, "includes_tests": ["t_one"]}],
        "self_pay": {"id": "sp", "name": "Self pay", "billing": {"covered_by": "patient"}},
        "test_catalogue": [{"canonical": "t_one", "category": "laboratory",
                            "aliases": ["Test One"]}],
    }
    base.update(overrides)
    return base


def _write(tmp_path, data):
    p = tmp_path / "rules.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


# --- the token_set_ratio subset hole -------------------------------------------------
# fuzz.token_set_ratio scores 100 whenever either side's tokens are a subset of the other's.
# Against this catalogue that matched the TPA form's *unticked* options and its Section A
# prose as authorised tests — it would have ordered and billed tests nobody asked for.
@pytest.mark.parametrize("prose", [
    # An UNTICKED option on chit_07. Matching it would authorise a test the form declined.
    "Outward Bound Medical Examination (OBS) (without Tetanus Vaccination)",
    # chit_07 Section A purpose text — prose that merely contains test words.
    "Pre-employment medical examination for appointment into the Public Service "
    "as Assistant Learning Officer",
    # chit_04 administrative requirements, not clinical tests.
    "Manager approval for the recommendation in BluePlanner/FNA.",
    "Documentary proof of residential address, dated within the last 12 months.",
    # Bare fragments that are subsets of real alias token sets.
    "Blood", "Liver", "History", "Screening", "Dose 1", "Examination", "Test",
])
def test_prose_and_fragments_never_match_a_test(prose):
    m = rules().match_test(prose)
    assert not m.matched, (
        f"{prose!r} leaked to {m.canonical!r} @ {m.score} — a fragment or an unticked option "
        f"must never become an authorised, billable test")


@pytest.mark.parametrize("typo,canonical", [
    ("Restng ECG", "resting_ecg"),
    ("Cholestrol screening", "lipid_profile"),
    ("Full Blood Cont", "full_blood_count"),
])
def test_genuine_misspellings_still_match(typo, canonical):
    """Tightening the matcher must not cost real-world tolerance."""
    assert rules().match_test(typo).canonical == canonical


# --- tie abstention ------------------------------------------------------------------
def test_ambiguous_name_abstains_instead_of_picking_by_catalogue_order():
    """'Hepatitis B' is equally the screening and the vaccination. Resolving that by YAML row
    order would let a config reordering change which procedure happens and what is billed."""
    m = rules().match_test("Hepatitis B")
    assert not m.matched and m.canonical is None


def test_ambiguity_is_reported_so_staff_can_disambiguate(tmp_path):
    data = _minimal()
    data["test_catalogue"] = [
        {"canonical": "t_alpha", "category": "laboratory", "aliases": ["Widget Panel A"]},
        {"canonical": "t_beta", "category": "laboratory", "aliases": ["Widget Panel B"]},
    ]
    data["packages"][0]["includes_tests"] = ["t_alpha"]
    loaded = load_rules(_write(tmp_path, data))
    m = loaded.match_test("Widget Panel C")
    assert not m.matched
    assert m.ambiguous and set(m.ambiguous_with) == {"t_alpha", "t_beta"}


def test_reordering_the_catalogue_changes_no_match():
    """The property tie-abstention buys: config file order is not semantics."""
    forward = rules()
    dumped = forward.model_dump()
    backward = CoverageRules.model_validate(
        {**dumped, "test_catalogue": list(reversed(dumped["test_catalogue"]))})
    probes = ["Chest X-Ray", "Hepatitis B", "Resting ECG", "Dose 1", "UFEME",
              "Cholesterol screening", "Blood", "Medical Examination", "Liver function screening"]
    assert ([forward.match_test(p).canonical for p in probes]
            == [backward.match_test(p).canonical for p in probes])


def test_internal_canonical_ids_are_not_surfaced_as_the_matched_alias():
    # A snake_case id is not text any document prints; showing it to staff as "what this
    # matched" is meaningless, and letting it score widens the match surface.
    for probe in ["Chest X-Ray", "Resting ECG", "UFEME", "Cholesterol screening"]:
        m = rules().match_test(probe)
        assert m.matched and m.matched_alias is not None
        assert "_" not in m.matched_alias, f"internal id leaked as alias: {m.matched_alias}"


def test_canonical_id_still_resolves_on_an_exact_lookup():
    assert rules().match_test("chest_xray").canonical == "chest_xray"


# --- config validation hardening ------------------------------------------------------
def test_duplicate_issuer_code_is_rejected(tmp_path):
    # Silently ignoring the second entry means an edit to billing_party has no effect.
    data = _minimal()
    data["issuers"].append({"code": "AAA", "name": "A again", "kind": "tpa",
                            "billing_party": "tpa"})
    with pytest.raises(RulesError, match="duplicate issuer code"):
        load_rules(_write(tmp_path, data))


def test_duplicate_test_canonical_is_rejected(tmp_path):
    data = _minimal()
    data["test_catalogue"].append({"canonical": "t_one", "category": "laboratory",
                                   "list_price_sgd": 999, "aliases": ["Other Name"]})
    with pytest.raises(RulesError, match="duplicate test canonical"):
        load_rules(_write(tmp_path, data))


def test_duplicate_issuer_package_selector_is_rejected(tmp_path):
    data = _minimal()
    dup = dict(data["packages"][0])
    dup["id"] = "r2"
    data["packages"].append(dup)
    with pytest.raises(RulesError, match="duplicate.*selector"):
        load_rules(_write(tmp_path, data))


def test_two_document_defined_fallbacks_for_one_issuer_are_rejected(tmp_path):
    data = _minimal()
    base = {"issuer_code": "AAA", "package_code": None, "tests_from_document": True,
            "name": "doc-defined", "billing": {"covered_by": "insurer"}}
    data["packages"] += [{**base, "id": "d1"}, {**base, "id": "d2"}]
    with pytest.raises(RulesError, match="more than one tests_from_document"):
        load_rules(_write(tmp_path, data))


def test_package_listing_the_same_test_twice_is_rejected(tmp_path):
    data = _minimal()
    data["packages"][0]["includes_tests"] = ["t_one", "t_one"]
    with pytest.raises(RulesError, match="more than once"):
        load_rules(_write(tmp_path, data))


@pytest.mark.parametrize("band", [
    {"age_min": 60, "age_max": 40},     # empty band — nobody could ever match
    {"age_min": -1},
    {"age_max": -5},
])
def test_incoherent_age_bands_are_rejected(tmp_path, band):
    data = _minimal()
    data["packages"][0]["eligibility"] = band
    with pytest.raises((RulesError, ValidationError)):
        load_rules(_write(tmp_path, data))


def test_unknown_yaml_key_is_rejected_not_silently_discarded(tmp_path):
    """`questionaire:` would otherwise be dropped, leaving the package with no questionnaire —
    a typo silently changing patient-facing behaviour."""
    data = _minimal()
    data["packages"][0]["questionaire"] = "general_health"
    with pytest.raises((RulesError, ValidationError)):
        load_rules(_write(tmp_path, data))


@pytest.mark.parametrize("field,bad", [
    ("kind", "insuurer"),
    ("billing_party", "insuror"),
    ("default_questionnaire", "generalhealth"),
    ("default_visit_type", "gp"),
])
def test_closed_vocabulary_typos_in_yaml_fail_at_load(tmp_path, field, bad):
    data = _minimal()
    data["issuers"][0][field] = bad
    with pytest.raises((RulesError, ValidationError)):
        load_rules(_write(tmp_path, data))


# --- remaining age-band boundaries ----------------------------------------------------
@pytest.mark.parametrize("code,age,expected", [
    ("WELL1", 39, True), ("WELL1", 40, False),
    ("WELL3", 59, False), ("WELL3", 60, True),
    ("PEE225", 24, True), ("PEE225", 25, False),
    ("PEE224", 49, False), ("PEE224", 50, True),
])
def test_all_package_age_band_edges(code, age, expected):
    issuer = "BLPHS" if code.startswith("WELL") else "MOL0199VME"
    assert rules().package(issuer, code).eligibility.covers_age(age) is expected


def test_package_age_bands_tile_without_gaps_or_overlaps():
    """Every age 0-120 must fall in exactly one tier of each family, else the config has a hole
    and a real patient would come back either uncovered or doubly covered."""
    for issuer, codes in [("BLPHS", ["WELL1", "WELL2", "WELL3"]),
                          ("MOL0199VME", ["PEE225", "PEE226", "PEE224"])]:
        bands = [rules().package(issuer, c).eligibility for c in codes]
        for age in range(0, 121):
            hits = [c for c, b in zip(codes, bands) if b.covers_age(age)]
            assert len(hits) == 1, f"{issuer} age {age} matches {hits}"


# --- the anti-hardcoding guarantee ----------------------------------------------------
def _executable_strings(source: str) -> list[str]:
    """Every string literal in ``source`` that is NOT a docstring.

    Scanning raw text would flag a comment like "WELL2 is ages 40-59", which is documentation
    worth keeping — the invariant is about *behaviour*, not about never naming a code in prose.
    A hardcoded branch, by contrast, always shows up as a live string constant or an identifier,
    so that is what this looks at. Comments never reach the AST at all.
    """
    import ast

    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


# `gold.py` is a declarative fixture table: it quotes issuer codes because it stands in for a
# model reading them off the documents. Quoting a code as DATA is not branching on it. It is
# still covered by `test_no_issuer_code_appears_in_a_branch_condition`, which is the real
# invariant and admits no exclusions.
_DECLARATIVE_FIXTURE_MODULES = {"gold.py"}


def _codes() -> set[str]:
    return ({i.code for i in rules().issuers}
            | {p.package_code for p in rules().packages if p.package_code})


def _condition_strings(source: str) -> list[str]:
    """String literals appearing in a branch or comparison — where an `if code == "X"` would live."""
    import ast

    tree = ast.parse(source)
    out: list[str] = []
    for node in ast.walk(tree):
        tests = []
        if isinstance(node, (ast.If, ast.IfExp, ast.While)):
            tests = [node.test]
        elif isinstance(node, ast.Compare):
            tests = [node.left, *node.comparators]
        elif isinstance(node, ast.Match):
            tests = [node.subject] + [c.pattern for c in node.cases]
        for t in tests:
            out += [n.value for n in ast.walk(t)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    return out


def test_no_issuer_code_appears_in_a_branch_condition():
    """The literal invariant: nowhere in the package does control flow test an issuer code.

    This is what "no `if code == 'MRDEB'`" actually means, and unlike a raw-text scan it holds
    even for modules that legitimately quote codes as data (CLAUDE.md §Hard constraints 3).
    """
    from databridge_intake.paths import package_root

    pkg_root = package_root()
    offenders = [f"{py.relative_to(pkg_root)}: {tok}"
                 for py in pkg_root.rglob("*.py")
                 for tok in _codes()
                 if any(tok in s for s in _condition_strings(py.read_text(encoding="utf-8")))]
    assert not offenders, (
        "control flow branches on an issuer/package code — behaviour must come from "
        "coverage_rules.yaml: " + "; ".join(sorted(offenders)))


def test_no_issuer_branching():
    """Belt and braces: logic modules do not mention a code at all, even as data.

    Stricter than the branch check and easier to reason about — if a code is nowhere in the
    module, it certainly is not being special-cased. Declarative fixture tables are exempt and
    listed explicitly above.
    """
    from databridge_intake.paths import package_root

    pkg_root = package_root()
    offenders: list[str] = []
    for py in pkg_root.rglob("*.py"):
        if py.name in _DECLARATIVE_FIXTURE_MODULES:
            continue
        source = py.read_text(encoding="utf-8")
        haystack = _executable_strings(source)
        idents = _identifiers(source)
        for tok in _codes():
            if any(tok in s for s in haystack) or tok in idents:
                offenders.append(f"{py.relative_to(pkg_root)}: {tok}")
    assert not offenders, (
        "issuer/package codes must live only in coverage_rules.yaml; found in executable code: "
        + "; ".join(sorted(offenders)))


def test_gold_fixture_module_is_data_not_logic():
    """Guards the exemption above: `gold.py` may quote codes only inside literal data, never in
    a comparison. Without this, the exclusion list would be a loophole."""
    from databridge_intake.paths import package_root

    gold = (package_root() / "gold.py").read_text(encoding="utf-8")
    conditions = _condition_strings(gold)
    leaked = [t for t in _codes() if any(t in s for s in conditions)]
    assert not leaked, f"gold.py branches on {leaked} — it must remain a declarative table"


def _identifiers(source: str) -> set[str]:
    import ast
    tree = ast.parse(source)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    return names


def test_the_branching_detector_actually_detects_branching(tmp_path):
    """A guard test is only worth its own runtime if it fails on the thing it claims to catch."""
    offending = tmp_path / "bad.py"
    offending.write_text(
        '"""Docstring mentioning MRDEB is fine."""\n'
        "# a comment mentioning MRDEB is fine too\n"
        "def pick(code):\n"
        '    if code == "MRDEB":\n'
        '        return "special"\n',
        encoding="utf-8")
    source = offending.read_text(encoding="utf-8")
    strings = _executable_strings(source)
    assert "MRDEB" in strings                      # the live branch is seen
    assert not any("Docstring mentioning" in s for s in strings)   # prose is not

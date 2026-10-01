"""Loader + typed view over the ACTIVE PACK's ``config/coverage_rules.yaml`` (PLAN.md §3).

The file lives at ``packs/<pack>/config/coverage_rules.yaml`` — beside the ``ontology.yaml``
that declares the vocabulary it is written in — and is located through
``databridge_intake.paths.config_dir()``, which resolves it from the deployment binding. No
module here names a pack.

This module is the *only* thing that knows an issuer code exists. Everything downstream asks it
questions ("what package does BLPHS/WELL2 authorise?") instead of branching on codes. That is what
makes "add an insurer without changing code" true rather than aspirational, and it is enforced by
``test_no_issuer_branching``.

Test-name matching is three-stage — exact after normalisation, exact ignoring separators, then a
LENGTH-SENSITIVE fuzzy pass — because the sample documents print the same test half a dozen ways
("Resting ECG", "Electrocardiogram (ECG)", "ECG"). A name that matches nothing, or that matches two
catalogue entries equally well, is returned unmatched and shown to staff; it is never coerced into
the nearest entry.

**Why not ``token_set_ratio``.** The obvious choice is wrong here and dangerously so: it scores 100
whenever either side's tokens are a subset of the other's. Against this catalogue that made the
*unticked* option "Outward Bound Medical Examination (OBS)" on the TPA form match
``medical_examination`` at 100, and made bare words like "Blood" and "History" match real tests —
i.e. it would have authorised and billed tests nobody ordered. ``fuzz.ratio`` compares whole
normalised strings, so a fragment scores low, which is the behaviour a clinical instruction needs.
``packages/convert/.../extract/grounding.py`` avoids token-set for the same reason (its "B1 hole").
Note its threshold is on a 0-1 scale; ``rapidfuzz`` here returns 0-100, so the constants differ in
scale while meaning the same thing.
"""
from __future__ import annotations

import functools
import re
from decimal import Decimal
from typing import Iterable, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from rapidfuzz import fuzz

from .models import BillingParty, IssuerKind, QuestionnaireKind, VisitType
from .paths import config_dir

# rapidfuzz returns 0-100. The convert package's grounding wall uses the same 0.90 idea on a 0-1
# scale; same intent, different scale.
FUZZY_THRESHOLD = 90.0

# Two catalogue entries scoring within this of each other are treated as a TIE and abstained on,
# rather than resolved by whichever happens to appear first in the YAML. Reordering two config
# rows must never silently change a clinical instruction or an invoice line.
TIE_MARGIN = 2.0

_NORMALISE = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    """Casefold, punctuation → single spaces. Preserves word boundaries for fuzzy scoring.

    'Chest X-Ray' → 'chest x ray'. token_set_ratio needs the boundaries, so this is the key used
    for fuzzy comparison and for the primary exact index.
    """
    return _NORMALISE.sub(" ", s.lower()).strip()


def _squash(s: str) -> str:
    """Alphanumerics only — the hyphen-blind key. 'Chest X-Ray' and 'Chest Xray' both → 'chestxray'.

    Documents spell the same test with and without separators ('X-Ray'/'Xray'/'X Ray', 'Anti-HIV'/
    'Anti HIV'). Matching those shouldn't depend on somebody having listed every spelling as an
    alias, so the exact lookup tries this second key before falling back to fuzzy.
    """
    return _NORMALISE.sub("", s.lower())


class RulesError(RuntimeError):
    """The coverage rules file is missing, malformed, or internally inconsistent."""


# Every model below sets extra="forbid". This file is the sole authority on coverage, so an
# unrecognised key is a typo that would otherwise be discarded silently — `questionaire:` would
# leave the package defaulting to "none" and route the patient to no questionnaire at all.
_STRICT = ConfigDict(frozen=True, extra="forbid")


class Issuer(BaseModel):
    model_config = _STRICT

    code: str
    name: str
    # Typed as enums rather than str: a `kind: insuurer` typo must fail at load, not surface later
    # as a mis-routed bill (repo invariant P3, closed vocabularies).
    kind: IssuerKind
    billing_party: BillingParty
    tpa_claim_required: bool = False
    default_visit_type: VisitType = VisitType.UNKNOWN
    default_questionnaire: QuestionnaireKind = QuestionnaireKind.NONE


class Eligibility(BaseModel):
    model_config = _STRICT

    age_min: Optional[int] = None
    age_max: Optional[int] = None

    @model_validator(mode="after")
    def _band_is_coherent(self) -> "Eligibility":
        if self.age_min is not None and self.age_min < 0:
            raise ValueError(f"age_min must be >= 0, got {self.age_min}")
        if self.age_max is not None and self.age_max < 0:
            raise ValueError(f"age_max must be >= 0, got {self.age_max}")
        if self.age_min is not None and self.age_max is not None and self.age_min > self.age_max:
            raise ValueError(
                f"empty age band: age_min={self.age_min} > age_max={self.age_max} — no patient "
                f"could ever match this package")
        return self

    def covers_age(self, age: int) -> bool:
        if self.age_min is not None and age < self.age_min:
            return False
        if self.age_max is not None and age > self.age_max:
            return False
        return True

    def describe(self) -> str:
        if self.age_min is not None and self.age_max is not None:
            return f"ages {self.age_min}-{self.age_max}"
        if self.age_min is not None:
            return f"ages {self.age_min} and above"
        if self.age_max is not None:
            return f"ages {self.age_max} and below"
        return "no age restriction"


class Billing(BaseModel):
    model_config = _STRICT

    covered_by: BillingParty
    patient_payable_sgd: Optional[Decimal] = None


class Package(BaseModel):
    model_config = _STRICT

    id: str
    issuer_code: str
    package_code: Optional[str] = None
    name: str
    eligibility: Eligibility = Field(default_factory=Eligibility)
    questionnaire: QuestionnaireKind = QuestionnaireKind.NONE
    billing: Billing
    includes_tests: list[str] = Field(default_factory=list)
    tests_from_document: bool = False
    # The Occupational Health questionnaire checkbox this package implies, if any. Data rather
    # than a code-level inference: deriving it would guess "Pre/Re Employment" for every
    # occupational rule, including a hazard-specific one. Absent ⇒ the patient answers it.
    screening_type: Optional[str] = None

    @model_validator(mode="after")
    def _selector_is_unambiguous(self) -> "Package":
        # A rule that is both coded AND document-defined has two different selection paths into
        # it; which one wins would depend on lookup order rather than on the document.
        if self.package_code and self.tests_from_document:
            raise ValueError(
                f"package {self.id!r} sets both package_code={self.package_code!r} and "
                f"tests_from_document=True — a rule must be selected one way or the other")
        if not self.package_code and not self.tests_from_document:
            raise ValueError(
                f"package {self.id!r} has neither a package_code nor tests_from_document — it can "
                f"never be selected")
        return self


class SelfPay(BaseModel):
    model_config = _STRICT

    id: str
    name: str
    billing: Billing
    questionnaire: QuestionnaireKind = QuestionnaireKind.NONE
    visit_type: VisitType = VisitType.GP_CONSULT


class CatalogueTest(BaseModel):
    model_config = _STRICT

    canonical: str
    category: str
    list_price_sgd: Optional[Decimal] = None
    aliases: list[str] = Field(default_factory=list)


class TestMatch(BaseModel):
    """Result of looking a printed test name up in the catalogue.

    ``ambiguous_with`` is populated when two or more catalogue entries scored within ``TIE_MARGIN``
    of each other: the match is refused, but the near-misses are carried so the front desk can be
    told *why* ("this could be Hepatitis B screening or the Hepatitis B vaccination — please pick")
    instead of just "unrecognised".
    """

    model_config = ConfigDict(frozen=True)

    as_printed: str
    canonical: Optional[str] = None
    score: float = 0.0
    matched_alias: Optional[str] = None
    ambiguous_with: tuple[str, ...] = ()

    @property
    def matched(self) -> bool:
        return self.canonical is not None

    @property
    def ambiguous(self) -> bool:
        return bool(self.ambiguous_with)


class CoverageRules(BaseModel):
    """The whole rules table, indexed for lookup."""

    model_config = ConfigDict(frozen=True)

    version: int
    # ISO 4217 code of every price in this table, declared by the pack (SWEEP #15). Optional so a
    # table that predates it still loads; the API then reports `currency: null`, never a guess.
    currency: Optional[str] = Field(default=None, pattern=r"^[A-Z]{3}$")
    issuers: list[Issuer]
    packages: list[Package]
    self_pay: SelfPay
    test_catalogue: list[CatalogueTest]

    # -- issuer lookup ------------------------------------------------------------
    def issuer(self, code: Optional[str]) -> Optional[Issuer]:
        if not code:
            return None
        want = code.strip().upper()
        return next((i for i in self.issuers if i.code.upper() == want), None)

    def issuer_codes(self) -> list[str]:
        return [i.code for i in self.issuers]

    # -- package lookup -----------------------------------------------------------
    def package(self, issuer_code: Optional[str], package_code: Optional[str]) -> Optional[Package]:
        """The rule for (issuer, package). With no package code, fall back to the issuer's
        document-defined rule — a referral letter names tests directly instead of a package."""
        issuer = self.issuer(issuer_code)
        if issuer is None:
            return None
        code_u = issuer.code.upper()
        if package_code:
            want = package_code.strip().upper()
            hit = next((p for p in self.packages
                        if p.issuer_code.upper() == code_u
                        and (p.package_code or "").upper() == want), None)
            if hit is not None:
                return hit
            return None
        return next((p for p in self.packages
                     if p.issuer_code.upper() == code_u and p.tests_from_document), None)

    def packages_for(self, issuer_code: str) -> list[Package]:
        want = issuer_code.strip().upper()
        return [p for p in self.packages if p.issuer_code.upper() == want]

    # -- test catalogue -----------------------------------------------------------
    def _index_by(self, keyfn) -> dict[str, tuple[str, str]]:
        """Build ``key → (canonical, display alias)`` under ``keyfn``.

        Human aliases are inserted BEFORE canonical ids, so when both collapse to the same key the
        entry keeps the wording a document would actually print. Insert the other way round and
        ``match_test("UFEME")`` reports its ``matched_alias`` as the internal ``ufeme`` — the front
        desk is then shown a snake_case identifier as "the text this matched".

        Keys claimed by two DIFFERENT canonicals are dropped entirely rather than resolved by
        insertion order; the lookup falls through to fuzzy scoring, which abstains on ties.
        """
        index: dict[str, tuple[str, str]] = {}
        collisions: set[str] = set()
        for pick in (lambda t: t.aliases, lambda t: [t.canonical]):
            for t in self.test_catalogue:
                for alias in pick(t):
                    key = keyfn(alias)
                    existing = index.get(key)
                    if existing is not None and existing[0] != t.canonical:
                        collisions.add(key)
                    index.setdefault(key, (t.canonical, alias))
        for key in collisions:
            index.pop(key, None)
        return index

    @functools.cached_property
    def _alias_index(self) -> dict[str, tuple[str, str]]:
        """Normalised exact index. Canonical ids are included so an internal id still resolves on
        an exact lookup, but they are excluded from the FUZZY surface (``_fuzzy_aliases``) — a
        snake_case id is not something a document prints, and letting it score widens the match
        surface for no benefit."""
        return self._index_by(_norm)

    @functools.cached_property
    def _fuzzy_aliases(self) -> tuple[tuple[str, str, str], ...]:
        """(normalised alias, canonical, display alias) for human-facing aliases only."""
        return tuple((_norm(a), t.canonical, a) for t in self.test_catalogue for a in t.aliases)

    @functools.cached_property
    def _squash_index(self) -> dict[str, tuple[str, str]]:
        """Hyphen-blind exact index (see ``_squash``)."""
        return self._index_by(_squash)

    def display_name(self, canonical: str) -> str:
        """The wording a document would print for a catalogue entry.

        Used for tests that come from a package definition and so have no printed line of their
        own: a claim line reading `ufeme` is an internal id shown to a payer, which is neither
        what the clinic ordered nor what an insurer's portal expects.
        """
        entry = self.test(canonical)
        if entry is None:
            return canonical
        return entry.aliases[0] if entry.aliases else entry.canonical

    def test(self, canonical: str) -> Optional[CatalogueTest]:
        return next((t for t in self.test_catalogue if t.canonical == canonical), None)

    def match_test(self, printed: str) -> TestMatch:
        """Map a printed test name to a catalogue entry, or refuse.

        Three stages: exact after normalisation → exact ignoring separators → length-sensitive
        fuzzy with tie abstention. Returns an unmatched ``TestMatch`` rather than raising or
        guessing — an unrecognised test on a document is information for staff ("this insurer asked
        for something we have no code for"), not an error and not something to quietly discard.

        A fragment of a longer phrase must NOT match: the TPA form's unticked options and its
        Section A purpose text are ordinary prose containing test words, and matching them would
        authorise and bill tests nobody ordered.
        """
        cleaned = printed.strip()
        if not cleaned:
            return TestMatch(as_printed=printed)
        key = _norm(cleaned)
        exact = self._alias_index.get(key) or self._squash_index.get(_squash(cleaned))
        if exact is not None:
            return TestMatch(as_printed=cleaned, canonical=exact[0], score=100.0,
                             matched_alias=exact[1])

        # fuzz.ratio compares whole strings, so a short fragment of a long alias (or vice versa)
        # scores low — unlike token_set_ratio, which returns 100 for any subset.
        scored: list[tuple[float, str, str]] = [
            (float(fuzz.ratio(key, alias_key)), canonical, alias)
            for alias_key, canonical, alias in self._fuzzy_aliases]
        if not scored:
            return TestMatch(as_printed=cleaned)
        scored.sort(key=lambda s: -s[0])
        best_score, best_canonical, best_alias = scored[0]
        if best_score < FUZZY_THRESHOLD:
            return TestMatch(as_printed=cleaned, score=best_score)

        # Abstain when two DIFFERENT canonicals are within the tie margin — resolving that by
        # catalogue order would let a YAML reordering change which test gets performed.
        contenders = {c for s, c, _ in scored if s >= best_score - TIE_MARGIN}
        if len(contenders) > 1:
            return TestMatch(as_printed=cleaned, score=best_score,
                             ambiguous_with=tuple(sorted(contenders)))
        return TestMatch(as_printed=cleaned, canonical=best_canonical, score=best_score,
                         matched_alias=best_alias)

    def match_tests(self, printed: Iterable[str]) -> list[TestMatch]:
        return [self.match_test(p) for p in printed]


def _dupes(values: Iterable[str]) -> list[str]:
    seen, dupes = set(), set()
    for v in values:
        (dupes if v in seen else seen).add(v)
    return sorted(dupes)


def _validate(rules: CoverageRules) -> None:
    """Fail loudly on an internally inconsistent table — a typo here is a silent wrong answer.

    Every check below exists because the alternative is a lookup silently resolving by YAML order:
    a duplicated issuer code means an edit to the payer has no effect, a duplicated canonical means
    a price change is ignored, a second document-defined fallback means the first one always wins.
    None of those raise anything at runtime; they just quietly produce the wrong bill.
    """
    codes = {i.code.upper() for i in rules.issuers}
    canonicals = {t.canonical for t in rules.test_catalogue}

    # -- uniqueness of every lookup key -------------------------------------------
    if dup := _dupes(i.code.upper() for i in rules.issuers):
        raise RulesError(f"duplicate issuer code(s) {dup}: the later entry is silently ignored")
    if dup := _dupes(p.id for p in rules.packages):
        raise RulesError(f"duplicate package rule id(s): {dup}")
    if dup := _dupes(t.canonical for t in rules.test_catalogue):
        raise RulesError(f"duplicate test canonical(s) {dup}: the later entry is silently ignored")

    selectors = [f"{p.issuer_code.upper()}/{(p.package_code or '').upper()}"
                 for p in rules.packages if p.package_code]
    if dup := _dupes(selectors):
        raise RulesError(f"duplicate (issuer, package_code) selector(s) {dup}: lookup would "
                         f"resolve by file order")

    fallbacks = [p.issuer_code.upper() for p in rules.packages if p.tests_from_document]
    if dup := _dupes(fallbacks):
        raise RulesError(f"issuer(s) {dup} have more than one tests_from_document rule: the "
                         f"no-package-code fallback would resolve by file order")

    # -- referential integrity ----------------------------------------------------
    for p in rules.packages:
        if p.issuer_code.upper() not in codes:
            raise RulesError(f"package {p.id!r} references unknown issuer {p.issuer_code!r}")
        if unknown := [t for t in p.includes_tests if t not in canonicals]:
            raise RulesError(f"package {p.id!r} references unknown test(s) {unknown}")
        if dup := _dupes(p.includes_tests):
            raise RulesError(f"package {p.id!r} lists test(s) {dup} more than once (double-billed)")

    # -- alias uniqueness (both normalisations) -----------------------------------
    seen: dict[str, str] = {}
    for t in rules.test_catalogue:
        for alias in [t.canonical, *t.aliases]:
            key = _norm(alias)
            if key in seen and seen[key] != t.canonical:
                raise RulesError(
                    f"alias {alias!r} is claimed by both {seen[key]!r} and {t.canonical!r} — "
                    f"an ambiguous alias would resolve by dict order, which is not a rule")
            seen[key] = t.canonical


def load_rules(path=None) -> CoverageRules:
    """Parse and validate the coverage rules table."""
    rules_path = path or (config_dir() / "coverage_rules.yaml")
    if not rules_path.exists():
        raise RulesError(f"coverage rules not found at {rules_path}")
    data = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RulesError(f"{rules_path} did not parse to a mapping")
    rules = CoverageRules.model_validate(data)
    _validate(rules)
    return rules


@functools.lru_cache(maxsize=1)
def rules() -> CoverageRules:
    """Process-wide cached rules table."""
    return load_rules()

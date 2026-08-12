"""Document classifier — which of the referring-document types is this?

Deliberately a *closed* classification with an abstention. The taxonomy comes from the brief;
anything the classifier is not confident about becomes ``UNRECOGNISED`` and routes to a human,
which is always the safe direction: a misfiled document produces the wrong coverage rule, whereas
an abstention produces a front-desk prompt.

The signal set is intentionally boring — layout markers that these document families genuinely
carry ("Voucher", "Underwriting", "AUTHORISATION FORM", an email header block). It is not a list
of issuer names: a new insurer's referral letter must classify correctly on the strength of
looking like a referral letter, not because we enumerated the insurer. That is the same
anti-hardcoding rule the coverage table follows, applied to layout instead of codes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import DocumentType

# Confidence below this becomes UNRECOGNISED — a human decides rather than the machine guessing.
MIN_CONFIDENCE = 0.35
# Absolute evidence floor: one strong marker (1.0), or at least two independent weak ones.
# Share-of-total alone cannot distinguish "clearly a voucher" from "the only word we recognised
# anywhere on this page was 'expiry date'" — both give the winner 100% of the total.
MIN_EVIDENCE = 2 * 0.28


@dataclass(frozen=True)
class TypeSignals:
    """Evidence for one document type. ``strong`` markers are near-decisive; ``weak`` ones nudge."""

    doc_type: DocumentType
    strong: tuple[str, ...] = ()
    weak: tuple[str, ...] = ()
    negative: tuple[str, ...] = ()      # markers that argue AGAINST this type


# Ordered only for readability; scoring is order-independent (ties abstain — see `classify`).
SIGNALS: tuple[TypeSignals, ...] = (
    TypeSignals(
        DocumentType.EMPLOYER_HR_EMAIL,
        # A full header BLOCK is the giveaway, regardless of who sent it. From: and Subject: are
        # required TOGETHER: either alone appears in plenty of ordinary letters, and as separate
        # strong markers a bare "Subject: newsletter" scored a decisive HR-email classification.
        strong=(r"^\s*From\s*:[\s\S]{0,400}?^\s*Subject\s*:",),
        weak=(r"^\s*Sent\s*:", r"^\s*To\s*:", r"\bHR\b", r"human resources",
              r"show this email"),
    ),
    TypeSignals(
        DocumentType.TPA_AUTHORISATION_FORM,
        strong=(r"authoris?z?ation form",),
        weak=(r"for billing purpose", r"purpose of medical examination", r"scheme of service",
              r"authorising department", r"particulars of candidate", r"[☒☐]"),
    ),
    TypeSignals(
        DocumentType.HEALTH_CHECKUP_VOUCHER,
        strong=(r"\bvoucher\b",),
        weak=(r"check-?up", r"screening package", r"expiry date", r"date of issue",
              r"present this voucher", r"valid till"),
    ),
    TypeSignals(
        DocumentType.UNDERWRITING_FOLLOWUP,
        strong=(r"underwriting follow[- ]?up", r"further requirement"),
        weak=(r"proposal number", r"new business", r"underwriting/administrative requirements",
              r"contract number", r"life assured"),
        negative=(r"\bvoucher\b",),
    ),
    TypeSignals(
        DocumentType.GROUP_INSURANCE_INVITATION,
        strong=(r"invitation for medical tests",),
        weak=(r"group life insurance", r"group insurance", r"certificate no", r"vol life",
              r"medical underwriting section"),
    ),
    TypeSignals(
        DocumentType.INSURER_REFERRAL_LETTER,
        strong=(r"referral letter",),
        weak=(r"the medical examiner", r"please conduct the following examination",
              r"policyholder", r"examinee"),
        negative=(r"\bvoucher\b", r"authorisation form"),
    ),
)

STRONG_WEIGHT = 1.0
WEAK_WEIGHT = 0.28
NEGATIVE_WEIGHT = 1.2


@dataclass(frozen=True)
class Classification:
    doc_type: DocumentType
    confidence: float
    matched: tuple[str, ...] = ()
    runner_up: DocumentType | None = None
    scores: dict = field(default_factory=dict)
    evidence_span: tuple[int, int] | None = None    # where the best marker was found


def _score(text: str, sig: TypeSignals) -> tuple[float, list[str], tuple[int, int] | None]:
    """Score one type, and return the span of its strongest marker for provenance.

    Overlapping patterns are counted ONCE. "Medical Referral Letter" matches both the specific
    and the general referral pattern; counting both inflates confidence and shifts tie-breaking
    without adding any independent evidence.
    """
    hits: list[str] = []
    seen_spans: list[tuple[int, int]] = []
    total = 0.0
    best_span: tuple[int, int] | None = None

    def _fire(pat: str, weight: float) -> None:
        nonlocal total, best_span
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if not m:
            return
        span = (m.start(), m.end())
        # same stretch of text already counted by a broader/narrower sibling pattern
        if any(span[0] < e and s < span[1] for s, e in seen_spans):
            return
        seen_spans.append(span)
        total += weight
        hits.append(pat)
        if weight == STRONG_WEIGHT and best_span is None:
            best_span = span

    for pat in sig.strong:
        _fire(pat, STRONG_WEIGHT)
    for pat in sig.weak:
        _fire(pat, WEAK_WEIGHT)
    for pat in sig.negative:
        if re.search(pat, text, re.IGNORECASE | re.MULTILINE):
            total -= NEGATIVE_WEIGHT
    if best_span is None and seen_spans:
        best_span = seen_spans[0]
    return max(total, 0.0), hits, best_span


def classify(text: str) -> Classification:
    """Classify a document's text, or abstain.

    Returns ``NO_DOCUMENT`` for empty input and ``UNRECOGNISED`` when the evidence is weak,
    ambiguous, or split between two families.

    Confidence is the winner's *share* of the total score, which measures how cleanly one family
    won — but share alone is not enough. A page containing only the words "expiry date" scores
    0.28 for exactly one type and therefore takes 100% of the total, which would present as
    complete certainty about a document we have almost no evidence for. So an absolute evidence
    floor is required as well: one strong marker, or at least two independent weak ones.
    """
    if not text or not text.strip():
        return Classification(DocumentType.NO_DOCUMENT, 1.0)

    scored = [(sig.doc_type, *_score(text, sig)) for sig in SIGNALS]
    scored.sort(key=lambda s: -s[1])
    table = {t.value: round(s, 3) for t, s, _, _ in scored}
    best_type, best_score, best_hits, best_span = scored[0]

    if best_score <= 0:
        return Classification(DocumentType.UNRECOGNISED, 0.0, scores=table)

    runner_type, runner_score = (scored[1][0], scored[1][1]) if len(scored) > 1 else (None, 0.0)
    total = sum(s for _, s, _, _ in scored) or best_score
    confidence = best_score / total

    def _abstain() -> Classification:
        return Classification(DocumentType.UNRECOGNISED, round(confidence, 3),
                              tuple(best_hits), runner_type, table, best_span)

    # Absolute evidence floor — see docstring.
    if best_score < MIN_EVIDENCE:
        return _abstain()
    # A near-tie is not a classification. Abstaining costs one staff click; guessing wrong
    # selects a different issuer's coverage rules.
    if runner_score and best_score - runner_score < 0.3 * best_score:
        return _abstain()
    if confidence < MIN_CONFIDENCE:
        return _abstain()
    return Classification(best_type, round(confidence, 3), tuple(best_hits), runner_type,
                          table, best_span)

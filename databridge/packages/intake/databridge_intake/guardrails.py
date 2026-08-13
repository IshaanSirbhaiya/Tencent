"""Deterministic refusal of identity-verification questions — **before** the model is called.

The system prompt in ``config/assistant.yaml`` also forbids these answers, but a prompt is a
request, not a guarantee: it can be argued with, out-competed by a persuasive question, or lost in
a long context. This module is the guarantee. It is pure string matching over the question, it runs
before ``Harness`` is touched, and its refusal text is fixed — so "does the assistant ever confirm
a patient's identity?" has a testable answer that does not depend on a model's mood
(CLAUDE.md §Hard constraints 1; PLAN.md §5 Phase 4).

The rule it encodes: the assistant may say what a **document claims** and what the **registry
holds**. It may never say that a person *is* who they claim to be, or that an identity document or
insurance e-card is valid, current, or theirs.

**Both directions of error are real**, and review found both:

* Too NARROW let "Has this person passed the identity check?" reach the model. It also let a
  single newline defeat everything — every pattern uses ``.{0,N}`` and ``.`` does not cross a line
  break, so the question was normalised to one line before matching.
* Too BROAD refused "Which package matches this patient?". Refusing every question that mentions a
  patient would make the assistant useless and teach staff to route around it, which costs more
  than it protects.

So the *objects* are scoped: an identity DOCUMENT, or the person AT THE COUNTER — never any
mention of a patient as a record.
"""
from __future__ import annotations

import re
from typing import NamedTuple

REFUSAL_CODE = "IDENTITY_VERIFICATION_OUT_OF_SCOPE"

REFUSAL_TEXT = (
    "I can't confirm anyone's identity, and I can't tell you whether an NRIC, FIN, passport or "
    "insurance e-card is valid, current, or belongs to the person in front of you. Identity is "
    "verified in person by clinic staff, and that check is outside what I do.\n\n"
    "What I can do: show you the registry candidates a document matches, with the score and the "
    "reason each one matched, and show you the exact text on the document that each value came "
    "from. You make the call."
)

# An identity DOCUMENT, as opposed to a package or a test.
_ID_DOC = r"(?:nric|fin|passport|ic|identity card|id card|e-?card|insurance card|photo ?id)"

# The person physically at the counter — as opposed to "the patient" as a RECORD, which ordinary
# clinic questions talk about all day ("which package matches this patient?").
_PERSON_HERE = (
    r"(?:him|her|them|"
    r"this (?:person|man|woman|guy|lady)|"
    r"the (?:person|man|woman|guy|lady|visitor|patient) (?:standing|in front|here|waiting|"
    r"at the counter)|"
    r"the person (?:here|present)|the visitor)"
)

# Each pattern is one WAY of asking the forbidden question, kept separate so a failing case can be
# traced to the rule that missed it rather than to one unreadable mega-regex.
_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    # "is this the right / same person" — but not "the right patient questionnaire".
    ("same_person", re.compile(
        r"\b(?:is|are)\b.{0,40}?\b(?:the )?(?:right|correct|same|real) "
        r"(?:person|guy|man|woman|patient)\b"
        r"(?! (?:questionnaire|form|package|record|file|details))", re.I)),
    ("really_them", re.compile(
        r"\b(?:is|are) (?:that|this|it) (?:really|actually|truly) "
        r"(?:him|her|them|he|she|they|the patient)\b", re.I)),
    # "This is actually him, right?"
    ("tag_question", re.compile(
        r"\bthis is (?:actually |really )?(?:him|her|them|the (?:right )?(?:person|patient))\b"
        r"[^.?!]{0,15}\bright\b", re.I)),
    # "confirm/verify <identity>" — but NOT "confirm who this is billed to", and not a question
    # about whether an identity FIELD was transcribed correctly.
    ("verify_identity", re.compile(
        r"\b(?:confirm|verify|validate|authenticate|establish)\b.{0,40}?"
        r"\b(?:identity|identification)\b"
        r"(?! (?:field|column|value|was copied|is copied))", re.I)),
    # The subject must be a PERSON. A bare "who this is" also appears in "confirm who this is
    # billed to" — an ordinary billing question, and refusing it is the over-broad failure mode.
    ("verify_who", re.compile(
        r"\b(?:confirm|verify|validate|prove|establish|tell me)\b.{0,30}?"
        r"\bwho (?:he|she|they|this (?:patient|person)|that (?:patient|person)|the patient) "
        r"(?:is|are)\b", re.I)),
    # An identity document's VALIDITY or OWNERSHIP — not its transcription.
    ("document_valid", re.compile(
        r"\b" + _ID_DOC + r"\b.{0,40}?"
        r"\b(?:valid|genuine|authentic|current|expired|legit\w*|belongs? to|the holder)\b", re.I)),
    ("verify_document", re.compile(
        r"\b(?:confirm|verify|validate|authenticate)\b.{0,40}?\b" + _ID_DOC + r"\b.{0,40}?"
        r"\b(?:valid|genuine|real|authentic|legit\w*|expired|belongs?)\b", re.I)),
    # "Is the woman standing here the passport holder?"
    ("holder_is_person", re.compile(
        r"\b(?:is|are)\b.{0,40}?" + _PERSON_HERE + r".{0,25}?"
        r"\bthe (?:" + _ID_DOC + r" )?(?:holder|owner|named (?:person|patient))\b", re.I)),
    # "does this NRIC belong to / line up with this person" — scoped to the person at the counter.
    ("belongs_to", re.compile(
        r"\b(?:belongs? to|match(?:es)?|line up with|tally with)\b.{0,40}?" + _PERSON_HERE + r"\b",
        re.I)),
    ("id_matches", re.compile(
        r"\b(?:do|does|is|are)\b.{0,20}?\b" + _ID_DOC + r"\b.{0,30}?\b(?:match|belong)", re.I)),
    # "should I accept this e-card as ID" / "can I let her proceed under this NRIC"
    ("trust_id", re.compile(
        r"\b(?:can|should|may)\s+(?:i|we)\b.{0,45}?"
        r"\b(?:trust|accept|rely on|let \w+ proceed|go ahead)\b.{0,45}?"
        r"\b(?:" + _ID_DOC + r"|identity|id)\b", re.I)),
    ("prove_identity", re.compile(
        r"\b(?:prove|proof)\b.{0,30}?\b(?:identity|who (?:he|she|they) (?:is|are))\b", re.I)),
    # "has this person passed the identity check?"
    ("check_passed", re.compile(
        r"\b(?:has|have|did|was|is)\b.{0,40}?\b(?:passed|pass|cleared|clear|completed|done)\b"
        r".{0,25}?\b(?:the )?(?:identity|id) (?:check|verification)\b", re.I)),
    # A verification VERB aimed straight at an identity document, with no claim about the
    # document trailing it. `verify_document` above required a validity word within 40 characters
    # ("verify the passport is genuine"), so the barest and most natural phrasing of the forbidden
    # request — "validate the insurance e-card" — walked through the screen and reached the model.
    # Probing nine phrasings found five misses of this shape; "is this e-card valid?" was caught
    # while "validate the insurance e-card" was not, which is not a distinction anyone asking the
    # question would recognise. Validating an e-card IS the forbidden act; nothing needs to follow.
    # The trailing lookahead keeps the transcription questions allowed — checking that an NRIC
    # NUMBER was copied correctly from a letter is proofreading, not identity verification.
    ("verify_id_document_bare", re.compile(
        r"\b(?:verify|validate|authenticate)\b.{0,30}?\b" + _ID_DOC + r"\b"
        r"(?! (?:number|field|column|value)\b)", re.I)),
    # "skip / bypass / auto-confirm the identity check"
    ("bypass_check", re.compile(
        r"\b(?:skip|bypass|waive|override|auto[- ]?(?:confirm|approve|verify))\b.{0,40}?"
        r"\b(?:identity|id check|verification|attestation)\b", re.I)),
)


class GuardrailResult(NamedTuple):
    refused: bool
    code: str = ""
    reason: str = ""       # which rule fired — for logs and tests, not for the staff-facing text
    answer: str = ""


def check_question(question: str) -> GuardrailResult:
    """Screen a question before it reaches any model.

    Returns ``refused=True`` with the fixed refusal text for identity-verification questions, and
    ``refused=False`` for everything else — including questions that merely *name* a patient, which
    are ordinary clinic questions the assistant is meant to answer.
    """
    # Collapse ALL whitespace to single spaces first. Every pattern uses `.{0,N}` and `.` does not
    # cross a newline, so without this a single line break defeated the entire guardrail:
    # "Can you confirm\nthis patient's identity?" went straight through while the same words on
    # one line were refused. For a module documented as "the guarantee", that is the difference
    # between a control and a decoration.
    text = re.sub(r"\s+", " ", question or "").strip()
    if not text:
        return GuardrailResult(refused=False)
    for name, pattern in _PATTERNS:
        if pattern.search(text):
            return GuardrailResult(refused=True, code=REFUSAL_CODE, reason=name,
                                   answer=REFUSAL_TEXT)
    return GuardrailResult(refused=False)

"""Deterministic refusal of identity-verification questions — **before** any model is called.

C-5 (finance dry run, 2026-10-02). This mechanism used to be ``databridge_intake.guardrails``, and
its refusal text named one vertical's identity documents and staff under EVERY pack: a finance
deployment was told its question was out of scope because "identity is verified in person by
[that vertical's] staff". The mechanism is the engine's; the WORDS are the pack's. So:

* **The detection rule lives here and is not weakened.** Pure string matching over the question,
  run before ``Harness`` is touched. The rule: the assistant may say what a *document claims* and
  what a *registry holds*; it may never say that a person *is* who they claim to be, or that an
  identity document or credential is valid, current, or theirs.
* **The nouns the rule matches** are the engine's own generic floor (passport, identity card, …)
  **plus every noun any pack on disk declares** in the ``identity`` section of its
  ``agent_wording.yaml``. The union, not the active pack's alone: a guarantee must not get weaker
  because a different pack is bound, so a question naming one pack's identity document is refused
  under every binding — exactly as strict as it was when the nouns were literals in code.
* **The refusal text** is the voice pack's ``identity.refusal`` (``pack_binding.voice_pack``), or
  :data:`NEUTRAL_REFUSAL_TEXT` when no single pack is the voice or the pack declares none.

Both directions of error are real, and review found both: too NARROW let "Has this person passed the
identity check?" reach the model (and a single newline defeated every pattern, so the question is
normalised to one line first); too BROAD refused "Which package matches this <subject>?". So the
*objects* are scoped: an identity DOCUMENT, or the person AT THE COUNTER — never any mention of a
subject as a record.
"""
from __future__ import annotations

import functools
import pathlib
import re
from dataclasses import dataclass
from typing import Any, NamedTuple, Optional

import yaml

from .pack_binding import default_packs_root, packs_on_disk, packs_root, voice_pack

REFUSAL_CODE = "IDENTITY_VERIFICATION_OUT_OF_SCOPE"

#: The file a pack declares its identity nouns and refusal wording in (beside ``ontology.yaml``).
WORDING_FILENAME = "agent_wording.yaml"
#: Its section, and that section's closed key set.
IDENTITY_SECTION = "identity"
IDENTITY_KEYS = ("refusal", "document_nouns", "subject_nouns", "record_nouns")

#: The engine's neutral refusal — names no domain, no national document and no organisation.
NEUTRAL_REFUSAL_TEXT = (
    "I can't confirm anyone's identity, and I can't tell you whether an identity document or "
    "credential is valid, current, or belongs to the person in front of you. Identity is "
    "confirmed by a named human, and that check is outside what I do.\n\n"
    "What I can do: show you the registry candidates a record matches, with the reason each one "
    "matched, and show you the exact source text each value came from. You make the call."
)

# The engine's floor of identity-DOCUMENT words — regex fragments, generic across domains. Pack
# nouns are added to these, never substituted for them.
_FLOOR_DOCUMENTS = (r"fin", r"passport", r"ic", r"identity card", r"id card", r"e-?card",
                    r"insurance card", r"photo ?id")

# Words that, after "the right <subject>", make the question about a RECORD, not a person.
_FLOOR_RECORDS = ("form", "package", "record", "file", "details")

#: A pack noun: lowercase words, digits, spaces and hyphens. Anything else is refused so a pack
#: cannot smuggle regex into the detection rule.
_NOUN_SHAPE = re.compile(r"^[a-z0-9][a-z0-9 \-]*[a-z0-9]$|^[a-z0-9]$")


class IdentityWordingError(RuntimeError):
    """A pack's ``identity`` section is malformed. Fail loud: a guardrail must not half-load."""


class GuardrailResult(NamedTuple):
    refused: bool
    code: str = ""
    reason: str = ""       # which rule fired — for logs and tests, not for the user-facing text
    answer: str = ""


@dataclass(frozen=True)
class IdentityWording:
    """What one pack says about identity: the refusal sentence and the nouns it adds."""

    pack: Optional[str]
    refusal: str
    document_nouns: tuple[str, ...] = ()
    subject_nouns: tuple[str, ...] = ()
    #: Words that, right after a subject noun, make it a question about a RECORD ("the right
    #: <subject> <record noun>") rather than about a person. They narrow `same_person` only.
    record_nouns: tuple[str, ...] = ()


def _nouns(value: Any, key: str, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise IdentityWordingError(f"{where}: {IDENTITY_SECTION}.{key} must be a list of nouns")
    out: list[str] = []
    for raw in value:
        noun = " ".join(str(raw or "").lower().split())
        if not _NOUN_SHAPE.match(noun):
            raise IdentityWordingError(
                f"{where}: {IDENTITY_SECTION}.{key} entry {raw!r} is not a plain noun "
                f"(lowercase letters, digits, spaces, hyphens)")
        if noun not in out:
            out.append(noun)
    return tuple(out)


def parse_identity_section(data: Any, *, pack: Optional[str], where: str) -> Optional[IdentityWording]:
    """The ``identity`` section of a parsed wording file, validated; ``None`` when absent."""
    if not isinstance(data, dict) or data.get(IDENTITY_SECTION) is None:
        return None
    section = data[IDENTITY_SECTION]
    if not isinstance(section, dict):
        raise IdentityWordingError(f"{where}: {IDENTITY_SECTION} must be a mapping")
    extra = sorted(k for k in section if k not in IDENTITY_KEYS)
    if extra:
        raise IdentityWordingError(
            f"{where}: unknown {IDENTITY_SECTION} key(s) {extra}; the keys are {list(IDENTITY_KEYS)}")
    refusal = str(section.get("refusal") or "").strip()
    if "refusal" in section and not refusal:
        raise IdentityWordingError(f"{where}: {IDENTITY_SECTION}.refusal is empty")
    # Paragraph breaks are kept; runs of whitespace inside a paragraph are folded.
    refusal = "\n\n".join(" ".join(p.split()) for p in re.split(r"\n\s*\n", refusal) if p.strip())
    return IdentityWording(pack=pack, refusal=refusal or NEUTRAL_REFUSAL_TEXT,
                           document_nouns=_nouns(section.get("document_nouns"), "document_nouns", where),
                           subject_nouns=_nouns(section.get("subject_nouns"), "subject_nouns", where),
                           record_nouns=_nouns(section.get("record_nouns"), "record_nouns", where))


@functools.lru_cache(maxsize=64)
def _load(path: str, mtime_ns: int, pack: str) -> Optional[IdentityWording]:   # noqa: ARG001
    p = pathlib.Path(path)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise IdentityWordingError(f"{p}: {exc}") from exc
    return parse_identity_section(data, pack=pack, where=str(p))


def _pack_wording(root: pathlib.Path, name: str) -> Optional[IdentityWording]:
    path = root / name / WORDING_FILENAME
    if not path.is_file():
        return None
    return _load(str(path), path.stat().st_mtime_ns, name)


def identity_wording(pack: Optional[str] = None,
                     root: Optional[pathlib.Path] = None) -> IdentityWording:
    """The identity wording in force: ``pack``'s if given, else the voice pack's, else neutral."""
    r = pathlib.Path(root) if root is not None else packs_root()
    name = pack if pack is not None else voice_pack(WORDING_FILENAME, r)
    found = _pack_wording(r, name) if name else None
    return found or IdentityWording(pack=None, refusal=NEUTRAL_REFUSAL_TEXT)


def refusal_text(pack: Optional[str] = None, root: Optional[pathlib.Path] = None) -> str:
    return identity_wording(pack, root).refusal


def detection_nouns(root: Optional[pathlib.Path] = None
                    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """``(document_nouns, subject_nouns, record_nouns)`` declared by EVERY pack on disk — under ``root`` (or the
    configured packs root) and under this checkout's own packs root, so pointing the packs root at
    a partial directory cannot drop a noun the checkout's packs declare."""
    roots: list[pathlib.Path] = []
    for r in (pathlib.Path(root) if root is not None else packs_root(), default_packs_root()):
        if r.resolve() not in [x.resolve() for x in roots]:
            roots.append(r)
    docs: list[str] = []
    subjects: list[str] = []
    records: list[str] = []
    for r in roots:
        for name in packs_on_disk(r):
            w = _pack_wording(r, name)
            if w is None:
                continue
            docs += [n for n in w.document_nouns if n not in docs]
            subjects += [n for n in w.subject_nouns if n not in subjects]
            records += [n for n in w.record_nouns if n not in records]
    return tuple(sorted(docs)), tuple(sorted(subjects)), tuple(sorted(records))


def _alt(nouns: tuple[str, ...]) -> str:
    # Longest first, so "national id card" is tried before "national id".
    return "|".join(re.escape(n) for n in sorted(nouns, key=lambda n: (-len(n), n)))


@functools.lru_cache(maxsize=32)
def compile_patterns(document_nouns: tuple[str, ...], subject_nouns: tuple[str, ...],
                     record_nouns: tuple[str, ...] = ()) -> tuple[tuple[str, re.Pattern], ...]:
    """The detection rule, with the pack-declared nouns slotted in beside the engine's floor."""
    extra_docs = [re.escape(n) for n in document_nouns if n not in _FLOOR_DOCUMENTS]
    id_doc = r"(?:" + "|".join([*sorted(extra_docs, key=len, reverse=True),
                                 *_FLOOR_DOCUMENTS]) + r")"
    subj = _alt(subject_nouns)
    s = ("|" + subj) if subj else ""                      # appended to a person alternation
    the_s = (r"|the (?:" + subj + r")") if subj else ""   # "the <subject>"
    records = "|".join([*(re.escape(n) for n in sorted(set(record_nouns) - set(_FLOOR_RECORDS),
                                                       key=lambda n: (-len(n), n))),
                        *_FLOOR_RECORDS])

    # The person physically at the counter — as opposed to a subject as a RECORD, which ordinary
    # questions talk about all day ("which package matches this <subject>?").
    person_here = (
        r"(?:him|her|them|"
        r"this (?:person|man|woman|guy|lady)|"
        r"the (?:person|man|woman|guy|lady|visitor" + s + r") (?:standing|in front|here|waiting|"
        r"at the counter)|"
        r"the person (?:here|present)|the visitor)"
    )

    # Each pattern is one WAY of asking the forbidden question, kept separate so a failing case can
    # be traced to the rule that missed it rather than to one unreadable mega-regex.
    return (
        # "is this the right / same person" — but not "the right <subject> form/record".
        ("same_person", re.compile(
            r"\b(?:is|are)\b.{0,40}?\b(?:the )?(?:right|correct|same|real) "
            r"(?:person|guy|man|woman" + s + r")\b"
            r"(?! (?:" + records + r"))", re.I)),
        ("really_them", re.compile(
            r"\b(?:is|are) (?:that|this|it) (?:really|actually|truly) "
            r"(?:him|her|them|he|she|they" + the_s + r")\b", re.I)),
        # "This is actually him, right?"
        ("tag_question", re.compile(
            r"\bthis is (?:actually |really )?(?:him|her|them|the (?:right )?(?:person" + s + r"))\b"
            r"[^.?!]{0,15}\bright\b", re.I)),
        # "confirm/verify <identity>" — but NOT a question about whether an identity FIELD was
        # transcribed correctly.
        ("verify_identity", re.compile(
            r"\b(?:confirm|verify|validate|authenticate|establish)\b.{0,40}?"
            r"\b(?:identity|identification)\b"
            r"(?! (?:field|column|value|was copied|is copied))", re.I)),
        # The subject must be a PERSON: "confirm who this is billed to" is an ordinary question.
        ("verify_who", re.compile(
            r"\b(?:confirm|verify|validate|prove|establish|tell me)\b.{0,30}?"
            r"\bwho (?:he|she|they|this (?:person" + s + r")|that (?:person" + s + r")" + the_s
            + r") (?:is|are)\b", re.I)),
        # An identity document's VALIDITY or OWNERSHIP — not its transcription.
        ("document_valid", re.compile(
            r"\b" + id_doc + r"\b.{0,40}?"
            r"\b(?:valid|genuine|authentic|current|expired|legit\w*|belongs? to|the holder)\b",
            re.I)),
        ("verify_document", re.compile(
            r"\b(?:confirm|verify|validate|authenticate)\b.{0,40}?\b" + id_doc + r"\b.{0,40}?"
            r"\b(?:valid|genuine|real|authentic|legit\w*|expired|belongs?)\b", re.I)),
        # "Is the woman standing here the passport holder?"
        ("holder_is_person", re.compile(
            r"\b(?:is|are)\b.{0,40}?" + person_here + r".{0,25}?"
            r"\bthe (?:" + id_doc + r" )?(?:holder|owner|named (?:person" + s + r"))\b", re.I)),
        # "does this document belong to / line up with this person" — scoped to the person here.
        ("belongs_to", re.compile(
            r"\b(?:belongs? to|match(?:es)?|line up with|tally with)\b.{0,40}?" + person_here
            + r"\b", re.I)),
        ("id_matches", re.compile(
            r"\b(?:do|does|is|are)\b.{0,20}?\b" + id_doc + r"\b.{0,30}?\b(?:match|belong)", re.I)),
        # "should I accept this card as ID" / "can I let her proceed under this document"
        ("trust_id", re.compile(
            r"\b(?:can|should|may)\s+(?:i|we)\b.{0,45}?"
            r"\b(?:trust|accept|rely on|let \w+ proceed|go ahead)\b.{0,45}?"
            r"\b(?:" + id_doc + r"|identity|id)\b", re.I)),
        ("prove_identity", re.compile(
            r"\b(?:prove|proof)\b.{0,30}?\b(?:identity|who (?:he|she|they) (?:is|are))\b", re.I)),
        # "has this person passed the identity check?"
        ("check_passed", re.compile(
            r"\b(?:has|have|did|was|is)\b.{0,40}?\b(?:passed|pass|cleared|clear|completed|done)\b"
            r".{0,25}?\b(?:the )?(?:identity|id) (?:check|verification)\b", re.I)),
        # A verification VERB aimed straight at an identity document, with no claim trailing it:
        # "validate the e-card" IS the forbidden act. The lookahead keeps transcription questions
        # allowed — checking that a document NUMBER was copied correctly is proofreading.
        ("verify_id_document_bare", re.compile(
            r"\b(?:verify|validate|authenticate)\b.{0,30}?\b" + id_doc + r"\b"
            r"(?! (?:number|field|column|value)\b)", re.I)),
        # "skip / bypass / auto-confirm the identity check"
        ("bypass_check", re.compile(
            r"\b(?:skip|bypass|waive|override|auto[- ]?(?:confirm|approve|verify))\b.{0,40}?"
            r"\b(?:identity|id check|verification|attestation)\b", re.I)),
    )


def detect(question: str, root: Optional[pathlib.Path] = None) -> str:
    """The name of the rule ``question`` trips, or ``""``. Pure detection — no wording."""
    # Collapse ALL whitespace first: every pattern uses `.{0,N}` and `.` does not cross a newline,
    # so without this one line break defeated the whole guardrail.
    text = re.sub(r"\s+", " ", question or "").strip()
    if not text:
        return ""
    for name, pattern in compile_patterns(*detection_nouns(root)):
        if pattern.search(text):
            return name
    return ""


def check_question(question: str, *, pack: Optional[str] = None,
                   root: Optional[pathlib.Path] = None) -> GuardrailResult:
    """Screen a question before it reaches any model.

    ``refused=True`` with the pack's (or the neutral) refusal text for identity-verification
    questions; ``refused=False`` for everything else — including questions that merely *name* a
    subject, which are ordinary questions the assistant is meant to answer. ``pack`` chooses only
    the WORDING; detection is the same under every binding.
    """
    rule = detect(question, root)
    if not rule:
        return GuardrailResult(refused=False)
    return GuardrailResult(refused=True, code=REFUSAL_CODE, reason=rule,
                           answer=refusal_text(pack, root))


__all__ = ["GuardrailResult", "IDENTITY_KEYS", "IDENTITY_SECTION", "IdentityWording",
           "IdentityWordingError", "NEUTRAL_REFUSAL_TEXT", "REFUSAL_CODE", "WORDING_FILENAME",
           "check_question", "compile_patterns", "detect", "detection_nouns", "identity_wording",
           "parse_identity_section", "refusal_text"]

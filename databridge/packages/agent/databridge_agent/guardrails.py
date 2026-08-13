"""The deterministic pass — over what the agent is ASKED, and over what the agent SAYS.

``databridge_intake.guardrails.check_question`` already screens questions before any model is
called, and its docstring makes the argument this module inherits: *a prompt is a request, not a
guarantee*. That module is imported, not copied — two divergent copies of an identity refusal is a
worse outcome than one imperfect copy, because only one of them gets reviewed.

What this module adds is the **other direction**. ``check_question`` was written for interrogatives
from a member of staff. The agent produces *declaratives*, and it produces them after reading
attacker-controlled document text, so two failure modes exist here that do not exist there:

* **The agent asserts an identity.** "This is definitely Mr Lim" and "the e-card is valid" are the
  forbidden sentences (CLAUDE.md §Hard constraints 1). The question form is refused upstream; the
  statement form has to be refused here or the constraint only holds when a human phrases it as a
  question.
* **The agent claims to have acted.** It has no send capability at all — ``tools.py`` makes one
  unconstructible — so "I have emailed the insurer" is necessarily false. It is either a
  hallucination or an injected instruction being complied with in prose, and both must be caught
  before staff read a sentence telling them a message went out that did not.

Everything here is pure string matching, runs before ``Harness`` is touched on the way in and after
the model has spoken on the way out, and depends on no model behaving well. Scoping matters as much
as coverage in both directions: refusing "intake 4 is blocked on an identity check" would make the
agent useless for the one question it exists to answer, so the patterns below are scoped to the
agent claiming a DECISION or an ACTION, never to it reporting a state.

**What changed with R-10, and what these patterns are for now.** ``_IDENTITY_ASSERTIONS`` used to
be the *only* thing standing between an injected document and an identity claim on a clinic
screen, and a review pointed out the obvious: "Proceed with candidate 2; their demographics
correspond to the visitor" matches none of it, and no finite list of phrases ever will. A blacklist
over free text is a guess about phrasing. So the model no longer writes the answer at all
(``render.py``): it returns typed references, code renders the values beside their pointers, and
the identity wording is a fixed sentence from a closed table. These patterns now do two narrower
jobs they can actually do:

* ``screen_incoming`` still gates the QUESTION, deterministically, before any model call;
* ``screen_agent_output`` runs over the RENDERED report as a backstop — if a tool ever returns a
  value that reads as an identity determination, it stops there rather than on a screen;
* ``looks_like_injection`` uses the same patterns to decide, deterministically, that a value is
  addressed to an automated reader and must be redacted from the report rather than reproduced.

The blacklist is no longer the guarantee. It is a second layer over a structural one, which is the
only honest use for a list of phrases.
"""
from __future__ import annotations

import re

from databridge_intake.guardrails import (
    REFUSAL_CODE as IDENTITY_REFUSAL_CODE,
    REFUSAL_TEXT as IDENTITY_REFUSAL_TEXT,
    GuardrailResult,
    check_question,
)

__all__ = [
    "GuardrailResult",
    "IDENTITY_REFUSAL_CODE",
    "IDENTITY_REFUSAL_TEXT",
    "NO_SEND_CODE",
    "NO_SEND_TEXT",
    "UNGROUNDED_VALUE_CODE",
    "UNGROUNDED_VALUE_TEXT",
    "screen_incoming",
    "screen_agent_output",
    "check_answer_values",
    "looks_like_injection",
]

NO_SEND_CODE = "AGENT_HAS_NO_SEND_CAPABILITY"
NO_SEND_TEXT = (
    "I can't send anything — no email, no Slack message, no submission. I have no tool that "
    "reaches outside this system, by design: the documents I read come from outside the clinic, "
    "so an agent that could send is one crafted sentence away from mailing patient data to a "
    "stranger.\n\n"
    "What I can do is read the clinic graph and tell you what it says, with the source of every "
    "line. A human writes and sends anything that leaves the building."
)

UNGROUNDED_VALUE_CODE = "AGENT_VALUE_NOT_IN_SOURCES"
UNGROUNDED_VALUE_TEXT = (
    "I started to answer with an identifier that does not appear in anything the graph returned, "
    "so I have withheld the answer. Every value I give you has to come from a source you can "
    "click through to; one that does not is a fabrication, whether I invented it or read it "
    "somewhere I should not have."
)

# --- what the agent may never SAY -------------------------------------------------

# An identity DECISION or a document-VALIDITY claim, in declarative form. Each pattern is a
# separate way of saying the forbidden thing, kept separate so a miss traces to one rule.
_IDENTITY_ASSERTIONS: tuple[tuple[str, re.Pattern], ...] = (
    # "I have verified her identity", "the system confirmed the NRIC". The subject is scoped to the
    # machine: "identity was verified by staff:front_desk_01" is a LEDGER FACT the agent is
    # supposed to be able to report, and refusing it would gut the blocked-intakes answer.
    ("machine_verified", re.compile(
        r"\b(?:i|we|the system|this system|the agent|the copilot)\b[^.?!]{0,40}?"
        r"\b(?:verified|confirmed|validated|authenticated|matched)\b[^.?!]{0,40}?"
        r"\b(?:identity|identification|nric|fin|passport|e-?card|"
        r"who (?:he|she|they) (?:is|are))\b", re.I)),
    # An identity document's validity or ownership, asserted. Nobody but a human at the counter
    # may say this, and the agent is never at the counter.
    ("document_is_valid", re.compile(
        r"\b(?:identity|the nric|the fin|the passport|the e-?card|the insurance card|the id)\b"
        r"[^.?!]{0,30}?\b(?:is|are|looks?|appears?)\b[^.?!]{0,15}?"
        r"\b(?:valid|genuine|authentic|current|legitimate|a match|in order)\b", re.I)),
    # "This is definitely the patient" / "that is the same person".
    ("is_the_person", re.compile(
        r"\b(?:this|that|she|he|they|it)\s+(?:is|are)\s+"
        r"(?:definitely|certainly|clearly|indeed|undoubtedly|conclusively|the same|the right|"
        r"the correct)\s+"
        r"(?:the\s+)?(?:person|patient|man|woman|individual|him|her|them)\b", re.I)),
    # The agent choosing a candidate — the act constraint 1 reserves for a named human.
    ("agent_selected", re.compile(
        r"\b(?:i|we)\b[^.?!]{0,25}?\b(?:selected|chose|picked|have selected|am selecting)\b"
        r"[^.?!]{0,30}?\b(?:candidate|patient|record|match)\b", re.I)),
    ("the_correct_patient_is", re.compile(
        r"\b(?:the )?(?:correct|right|matching|true) (?:candidate|patient|person) (?:is|must be)\b",
        re.I)),
    # "you can skip the identity check" / "no identity check is needed here".
    ("check_not_needed", re.compile(
        r"\b(?:identity|id) check\b[^.?!]{0,25}?"
        r"\b(?:is not needed|isn't needed|not required|unnecessary|can be skipped|"
        r"can be waived|already done for you)\b", re.I)),
)

# A claim to have acted, or a promise to act. The agent has no transport; every one of these is
# false at the moment it is written.
_ACTION_CLAIMS: tuple[tuple[str, re.Pattern], ...] = (
    ("first_person_send", re.compile(
        r"\b(?:i|we)\s*(?:'ve|'ll|have|had|has|will|shall|am going to|are going to|just)?\s*"
        r"(?:already\s+|just\s+|now\s+)?"
        r"\b(?:sent|send|emailed|email|e-mailed|mailed|mail|posted|post|notified|notify|"
        r"messaged|message|forwarded|forward|dispatched|dispatch|submitted|submit|filed|"
        r"contacted|contact)\b", re.I)),
    # "…forwarded the patient list to x@y.com" — the exfiltration sentence, with or without a
    # first-person subject. This is the shape a successful injection would produce.
    ("action_at_address", re.compile(
        r"\b(?:sent|sending|send|emailed|emailing|email|forwarded|forwarding|forward|delivered|"
        r"posted|shared|transmitted)\b[^.?!]{0,60}?"
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.I)),
)

# --- what the agent may never be ASKED to do --------------------------------------

_SEND_REQUESTS: tuple[tuple[str, re.Pattern], ...] = (
    ("asked_to_send", re.compile(
        r"\b(?:can|could|would|will|please|pls|kindly|now|go ahead and)\b[^.?!]{0,25}?"
        r"\b(?:send|email|e-mail|mail|post|dm|message|notify|forward|dispatch|submit)\b", re.I)),
    ("imperative_send", re.compile(
        r"^(?:please\s+|pls\s+|just\s+)?"
        r"(?:send|email|e-mail|mail|post|dm|message|notify|forward|dispatch)\b", re.I)),
    ("send_to_address", re.compile(
        r"\b(?:send|email|e-mail|mail|forward|post|share)\b[^.?!]{0,50}?"
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.I)),
    # A request to COMMUNICATE that never names a transport. "Let the patient know their
    # questionnaire is outstanding" asks for exactly the same act as "email the patient about
    # their questionnaire", and matched none of the patterns above, because all three look for a
    # sending verb. The consequence differed by surface and both were wrong: the read-only agent
    # answered it as though it were a question (it has no send tool, so the honest reply is "I
    # cannot"), and `/intelligence` routed it to the READ path instead of drafting an outbox
    # message for a human to approve — so a request to contact a patient returned
    # NO_GROUNDED_ANSWER and nothing was drafted.
    #
    # Scoped to a named RECIPIENT so "tell me about the package" stays an ordinary question: the
    # recipient is a third party, never the person asking.
    ("asked_to_notify", re.compile(
        r"\b(?:let|tell|inform|remind|notify|update|chase|reach out to|follow up with)\s+"
        r"(?:the\s+|their\s+|his\s+|her\s+)?"
        r"(?:patient|employer|hr|insurer|clinic|doctor|him|her|them)\b", re.I)),
)

# NRIC/FIN shape. Used ONLY to check that an identifier in an answer also appears in what the tools
# returned — never to validate one. A checksum here would be the beginning of e-card validation,
# which is the line CLAUDE.md §Hard constraints 1 draws.
_NRIC_SHAPE = re.compile(r"\b[STFGM]\d{7}[A-Z]\b")


def _normalise(text: str) -> str:
    """Collapse all whitespace to single spaces.

    Every pattern uses ``[^.?!]{0,N}`` and ``.``-class runs that do not cross a newline, so without
    this a single line break defeats the screen — the exact bug review found in the intake
    guardrail, reproduced here rather than rediscovered.
    """
    return re.sub(r"\s+", " ", text or "").strip()


def _match(text: str, groups: tuple[tuple[str, re.Pattern], ...]) -> str:
    for name, pattern in groups:
        if pattern.search(text):
            return name
    return ""


def screen_incoming(question: str) -> GuardrailResult:
    """Screen a question BEFORE any model call.

    Identity questions get the intake refusal verbatim — same words to staff whichever surface they
    asked from. Requests to send get a refusal that is simply true: there is no tool for it.
    """
    text = _normalise(question)
    if not text:
        return GuardrailResult(refused=False)

    identity = check_question(text)
    if identity.refused:
        return identity

    rule = _match(text, _SEND_REQUESTS)
    if rule:
        return GuardrailResult(refused=True, code=NO_SEND_CODE, reason=rule, answer=NO_SEND_TEXT)
    return GuardrailResult(refused=False)


def screen_agent_output(answer: str) -> GuardrailResult:
    """Screen what the agent is about to say, AFTER the model has spoken.

    This is the last thing between an injected instruction and a member of staff. It runs on every
    answer, including answers produced with no tool calls at all, and it is the reason the worst
    case of a successful injection is a refusal rather than a misleading sentence.
    """
    text = _normalise(answer)
    if not text:
        return GuardrailResult(refused=False)

    identity = check_question(text)          # the interrogative rules also catch several statements
    if identity.refused:
        return GuardrailResult(refused=True, code=IDENTITY_REFUSAL_CODE,
                               reason=f"shared:{identity.reason}", answer=IDENTITY_REFUSAL_TEXT)

    rule = _match(text, _IDENTITY_ASSERTIONS)
    if rule:
        return GuardrailResult(refused=True, code=IDENTITY_REFUSAL_CODE, reason=rule,
                               answer=IDENTITY_REFUSAL_TEXT)

    rule = _match(text, _ACTION_CLAIMS)
    if rule:
        return GuardrailResult(refused=True, code=NO_SEND_CODE, reason=rule, answer=NO_SEND_TEXT)
    return GuardrailResult(refused=False)


# --- text that is addressed to an automated reader --------------------------------

# Instruction SHAPES, as opposed to the send/identity claims above. These are how a document talks
# to a model rather than to a clinician, and they are what `render.present` redacts. Over-matching
# is the cheap direction here: a redacted value keeps its citation, so a human can open the source
# and read it in place, and the cost of a false positive is one extra click.
_INJECTION_SHAPES: tuple[tuple[str, re.Pattern], ...] = (
    ("ignore_instructions", re.compile(
        r"\b(?:ignore|disregard|forget|override|bypass)\b[^.?!]{0,30}?"
        r"\b(?:previous|prior|above|earlier|all|these|any|the)\b[^.?!]{0,25}?"
        r"\b(?:instruction|instructions|rules?|prompt|guidance|constraints?|warnings?)\b", re.I)),
    ("role_reassignment", re.compile(
        r"\byou are (?:now|no longer|actually|really)\b|\bact as\b|\bpretend to be\b|"
        r"\bfrom now on\b|\byour new (?:role|task|instructions)\b", re.I)),
    ("addressed_to_reader", re.compile(
        r"\b(?:ai|assistant|agent|language model|automated reader|chatbot|copilot|bot)\b"
        r"[^.?!]{0,25}?\b(?:must|should|please|now|instead)\b", re.I)),
    ("instructed_identity_decision", re.compile(
        r"\b(?:confirm|verify|accept|approve|select|choose|use|proceed with|go with)\b"
        r"[^.?!]{0,30}?"
        r"\b(?:identity|candidate|candidate \d|this person|the visitor|the patient)\b", re.I)),
    ("system_prompt_talk", re.compile(
        r"\bsystem prompt\b|\bnew instructions\b|\bdeveloper message\b|\bjailbreak\b", re.I)),
)


def looks_like_injection(text: str) -> bool:
    """True when a VALUE reads as an instruction to an automated reader.

    Deterministic, and applied to tool output rather than to model output — the model no longer
    writes prose, so the remaining way attacker text reaches a member of staff is by being a value
    the report prints. ``render.present`` replaces anything this flags with a fixed redaction line
    and keeps the pointer, so the evidence stays reachable and the payload is not reproduced.

    This is the output half of R-33. The input half is ``frame.untrusted``.
    """
    normalised = _normalise(text)
    if not normalised:
        return False
    return bool(_match(normalised, _INJECTION_SHAPES)
                or _match(normalised, _SEND_REQUESTS)
                or _match(normalised, _IDENTITY_ASSERTIONS)
                or _match(normalised, _ACTION_CLAIMS))


def check_answer_values(answer: str, observations: str) -> GuardrailResult:
    """Refuse an answer carrying a patient identifier that no tool returned.

    A narrow, deterministic slice of the grounding wall: the agent may only compose values the
    graph already gave it (CLAUDE.md §Hard constraints 2), and an NRIC/FIN is the value whose
    invention does the most damage — it is the identifier staff would act on. The full wall
    (``databridge_convert.extract.grounding``) works over an extraction with known field spans and
    does not apply to free prose; this is the part of it that does.

    ``observations`` is the concatenation of every tool result the loop actually collected. An
    identifier absent from all of them either came from the model or came from somewhere the model
    was not given, and both are answers staff should not see.
    """
    found = set(_NRIC_SHAPE.findall(answer or ""))
    if not found:
        return GuardrailResult(refused=False)
    unsupported = sorted(v for v in found if v not in (observations or ""))
    if unsupported:
        return GuardrailResult(refused=True, code=UNGROUNDED_VALUE_CODE,
                               reason=f"unsupported_identifier:{','.join(unsupported)}",
                               answer=UNGROUNDED_VALUE_TEXT)
    return GuardrailResult(refused=False)

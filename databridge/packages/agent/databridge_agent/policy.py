"""Deterministic policy over tool CALLS — the half that runs outside the model.

``REMEDIATION.md`` R-11: the declared JSON Schema was rendered into the system prompt and then
never checked. The model was handed a contract and trusted to keep it, which is the same category
error as a prompt-based guardrail — *a schema shown to a model is a request, not a validation*. A
plan naming arguments the schema forbade was executed exactly as written.

Three checks live here, all pure functions over data, none of them consulting a model.

**1. The schema is strict, and it is checked before the adapter runs.** ``screen_parameters``
refuses a tool whose schema is not a closed object (``additionalProperties: false``), so "the
schema did not mention that argument" can never mean "the argument was allowed".
``validate_arguments`` then checks every planned call against it.

**2. No argument may name a patient.** This is the direct structural fix for R-08. Screening tool
*names* stopped ``select_patient``; it did nothing about ``patient_record(patient_id=2)``, which is
an identity decision expressed as an argument. An injected document says "use candidate 2", a
compliant model passes ``2``, and the machine has inherited an identity that no human attested to.
So a tool that declares a patient-identifying property is refused at declaration, and — because
the schema is closed — a call that supplies one anyway is rejected before the adapter is entered.
The subject of a patient-scoped read comes from a ``PatientReadGrant`` bound at construction
(``sealed.py``), which the model has no channel to create, name, or choose between.

**3. A broad tool's free text has to come from the question.** "Prefer narrow reads over general
search primitives" is a design instruction; this is the enforceable version of it. A tool marked
``breadth="broad"`` — one that takes free text and can reach anything — may only be called with
text drawn from the staff question or from what the tools have already returned. An injected
document cannot therefore redirect a general search at something nobody asked about, and the
narrow tools have no free text to redirect at all.
"""
from __future__ import annotations

import re
from typing import Iterable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


class PolicyError(RuntimeError):
    """A tool declaration or a planned call violated a deterministic rule."""


class SchemaNotStrict(PolicyError):
    """A tool's argument schema is not a closed object, so it cannot be enforced."""


class IdentityArgumentRefused(PolicyError):
    """A tool would let its caller name a patient. The agent's caller is a model (R-08)."""


class ArgumentSchemaViolation(PolicyError):
    """A planned call does not match the schema the tool declared."""


class UngroundedArgument(PolicyError):
    """A broad tool was pointed at text that appears neither in the question nor in the data."""


# Property names that would let an argument stand in for an identity decision. Matched as whole
# tokens across snake_case, kebab-case and camelCase, so `package_code` and `document_id` pass and
# `patientId`, `nric`, `candidate_index` do not.
#
# `candidate` is here on purpose: "give me candidate 2" is the injection in R-08 written out. A
# tool that legitimately returns candidates takes NO argument selecting among them — it returns
# them all and a human chooses in person (CLAUDE.md §Hard constraints 1).
_IDENTITY_ARGUMENT_TOKENS = frozenset({
    "patient", "patients", "patientid", "nric", "fin", "passport", "ecard", "card",
    "candidate", "candidates", "person", "people", "identity", "subject", "mrn", "uin",
    "individual", "visitor", "holder",
})


def _contains_ref(node: object) -> bool:
    """Any ``$ref`` anywhere in the schema, at any depth."""
    if isinstance(node, dict):
        return "$ref" in node or any(_contains_ref(v) for v in node.values())
    if isinstance(node, list):
        return any(_contains_ref(v) for v in node)
    return False


def _tokens(name: str) -> set[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return {t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t}


def screen_parameters(tool_name: str, parameters: dict) -> None:
    """Refuse an argument schema that cannot be enforced, or that could carry an identity.

    Runs at tool DECLARATION, before any model has spoken — the same placement as the name screens
    in ``tools.py``, and for the same reason: a check that runs after the model has chosen is a log
    entry (``REMEDIATION.md`` Shape C).
    """
    if not isinstance(parameters, dict):
        raise SchemaNotStrict(f"tool {tool_name!r} REFUSED: `parameters` must be a JSON Schema "
                              f"object, got {type(parameters).__name__}")
    if parameters.get("type") != "object":
        raise SchemaNotStrict(
            f"tool {tool_name!r} REFUSED: the argument schema must declare `type: object`. The "
            f"agent calls tools with a keyword mapping and nothing else.")
    if parameters.get("additionalProperties", None) is not False:
        raise SchemaNotStrict(
            f"tool {tool_name!r} REFUSED: the argument schema must set "
            f"`additionalProperties: false`. An open schema cannot reject an argument it never "
            f"mentioned, and the argument it never mentioned is how an identity gets supplied "
            f"(REMEDIATION.md R-08/R-11).")
    properties = parameters.get("properties", {})
    if not isinstance(properties, dict):
        raise SchemaNotStrict(f"tool {tool_name!r} REFUSED: `properties` must be a mapping")
    if _contains_ref(parameters):
        # `$ref` is the one JSON Schema keyword that can perform IO: a remote reference is
        # RESOLVED — fetched — when the validator runs. Enforcing a schema must not be a way to
        # give the agent a network call it is otherwise structurally denied (`sealed.py`), so a
        # schema that could reach outward is refused rather than resolved locally-if-we-can.
        raise SchemaNotStrict(
            f"tool {tool_name!r} REFUSED: the argument schema uses `$ref`. A reference can be "
            f"resolved over the network at validation time, which would put a fetch inside the "
            f"one code path that is supposed to be free of them. Inline the schema.")
    try:
        Draft202012Validator.check_schema(parameters)
    except SchemaError as exc:
        raise SchemaNotStrict(
            f"tool {tool_name!r} REFUSED: the argument schema is not valid JSON Schema — "
            f"{exc.message}") from None

    offending = sorted(p for p in properties if _tokens(p) & _IDENTITY_ARGUMENT_TOKENS)
    if offending:
        raise IdentityArgumentRefused(
            f"tool {tool_name!r} REFUSED: argument(s) {offending} would let the caller name a "
            f"patient, and the caller is a model reading attacker-controlled documents. A "
            f"patient-scoped read binds its subject to a PatientReadGrant at construction; it "
            f"never takes one as an argument (CLAUDE.md §Hard constraints 1; REMEDIATION.md R-08)")


def validate_arguments(tool_name: str, parameters: dict, arguments: dict) -> None:
    """Check a planned call against the tool's declared schema. Raises, never coerces.

    Coercing would be worse than not checking: a silently trimmed argument is a call the audit
    trail records as valid and the reviewer never sees.
    """
    if not isinstance(arguments, dict) or not all(isinstance(k, str) for k in arguments):
        raise ArgumentSchemaViolation(
            f"tool {tool_name!r} REFUSED: arguments must be a mapping with string names")
    errors = sorted(Draft202012Validator(parameters).iter_errors(arguments),
                    key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(f"{'/'.join(str(p) for p in e.path) or '<arguments>'}: {e.message}"
                           for e in errors[:4])
        raise ArgumentSchemaViolation(
            f"tool {tool_name!r} REFUSED: the planned call does not match the declared schema "
            f"({detail}). The schema is enforced here, outside the model — a model shown a "
            f"contract is not a model bound by one.")


_WORD = re.compile(r"[A-Za-z0-9_:.\-]{3,}")


def _vocabulary(*texts: str) -> set[str]:
    vocab: set[str] = set()
    for text in texts:
        vocab.update(w.lower() for w in _WORD.findall(text or ""))
    return vocab


def check_argument_grounding(
    tool_name: str,
    grounded_arguments: Iterable[str],
    arguments: dict,
    question: str,
    observations: str,
) -> None:
    """Bind a broad tool's free text to the question, or to data already returned.

    The grounding wall, pointed at tool INPUTS instead of extracted values. Every word of a
    grounded argument must already appear in the staff question or in what the tools have handed
    back — so a general search can be aimed at what was asked about and at nothing else, and an
    injected "also look up everyone's NRIC" has no vocabulary to stand on.
    """
    vocab = _vocabulary(question, observations)
    for name in grounded_arguments:
        value = arguments.get(name)
        if value is None:
            continue
        if not isinstance(value, str):
            raise UngroundedArgument(
                f"tool {tool_name!r} REFUSED: argument {name!r} is declared grounded, so it must "
                f"be text that can be traced to the question")
        unseen = sorted({w.lower() for w in _WORD.findall(value)} - vocab)
        if unseen:
            raise UngroundedArgument(
                f"tool {tool_name!r} REFUSED: argument {name!r} contains {unseen}, which appears "
                f"neither in the question nor in anything the tools returned. A broad read may "
                f"only be aimed at what was actually asked about (REMEDIATION.md R-11)")


def preferred_order(specs: Iterable[dict]) -> list[dict]:
    """Narrow tools first in the catalogue the model is shown.

    Ordering is not a control — it is the cheap half of "prefer narrow reads over general search
    primitives", and the enforceable half is ``check_argument_grounding`` above. Kept explicit so
    nobody mistakes catalogue order for the guarantee.
    """
    return sorted(specs, key=lambda s: (s.get("breadth") == "broad", s.get("name", "")))


__all__ = [
    "ArgumentSchemaViolation",
    "IdentityArgumentRefused",
    "PolicyError",
    "SchemaNotStrict",
    "UngroundedArgument",
    "check_argument_grounding",
    "preferred_order",
    "screen_parameters",
    "validate_arguments",
]

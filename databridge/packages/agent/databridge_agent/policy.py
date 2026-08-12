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

**2. A patient- or candidate-scoped tool takes NO model-authored arguments at all.** This is the
direct structural fix for R-08, in the form the second review demanded. The first attempt was a
*denylist of property names* — ``patient``, ``nric``, ``candidate`` — and a denylist over names is
the same shape of guard R-10 already failed with: ``{"filters": {"properties": {"patient_id": …}}}``
never reached it (it looked one level deep), and ``id``, ``key``, ``record`` and ``query`` were
never on it in the first place. So the rule is now an allowlist expressed as zero: a tool whose
capability is ``candidates`` or ``patient`` may declare **no properties whatsoever**. There is no
argument to smuggle a selector into, whatever it is called and however deeply it is nested. The
subject of a patient-scoped read comes from a ``PatientReadGrant`` bound at construction
(``sealed.py``), which the model has no channel to create, name, or choose between; a candidate
read returns every candidate and takes nothing.

The name screen survives for ``clinic_structural`` tools, where arguments are legitimate — but it
now walks the **whole** schema tree (nested ``properties``, ``items``, ``$defs``,
``allOf``/``anyOf``/``oneOf``, …), refuses ``patternProperties`` outright (a pattern can match a
name no denylist can see), and requires every object level to be closed rather than only the top
one. It is a second layer, not the guarantee.

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


class SelectorArgumentRefused(IdentityArgumentRefused):
    """A subject-bound tool declared an argument. Those tools take none, whatever it is called."""


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

# Property names that select a row without saying what kind of row. `intake_id` names its domain;
# a bare `id` does not, and "give me record 2" is the R-08 injection with the noun filed off.
# Matched against the WHOLE property name, never as a token, so `intake_id` and `document_id` stay
# legal — over-refusing here would push people to loosen the screen, which costs more than it saves.
_GENERIC_SELECTOR_NAMES = frozenset({
    "id", "ids", "key", "keys", "ref", "refs", "record", "records", "row", "rows", "index",
    "idx", "uid", "uuid", "target", "who", "which", "selector", "select", "choice", "pick",
})

# JSON Schema keywords whose value is a MAPPING of name -> subschema. The names are property names
# and are screened; the subschemas are walked.
_NAMED_SUBSCHEMAS = ("properties", "patternProperties", "dependentSchemas", "$defs", "definitions")
# Keywords whose value is a single subschema.
_SINGLE_SUBSCHEMA = ("items", "contains", "additionalItems", "additionalProperties",
                     "unevaluatedItems", "unevaluatedProperties", "propertyNames",
                     "not", "if", "then", "else")
# Keywords whose value is a LIST of subschemas.
_SUBSCHEMA_LISTS = ("allOf", "anyOf", "oneOf", "prefixItems")


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


def walk_schema(schema: object, path: str = "<arguments>"):
    """Yield ``(path, subschema)`` for the schema and every subschema below it, at any depth.

    Public because the point of it is to be testable on its own. The previous screen read
    ``parameters["properties"]`` and stopped, so a ``filters`` object with a ``patient_id`` inside
    it was screened as one harmless property called ``filters``. A guard that inspects one level of
    a recursive structure is a guard against the example it was written from.
    """
    if not isinstance(schema, dict):
        return
    yield path, schema
    for keyword in _NAMED_SUBSCHEMAS:
        block = schema.get(keyword)
        if isinstance(block, dict):
            for name, sub in block.items():
                yield from walk_schema(sub, f"{path}.{keyword}[{name}]")
    for keyword in _SINGLE_SUBSCHEMA:
        sub = schema.get(keyword)
        if isinstance(sub, dict):
            yield from walk_schema(sub, f"{path}.{keyword}")
    for keyword in _SUBSCHEMA_LISTS:
        block = schema.get(keyword)
        if isinstance(block, list):
            for i, sub in enumerate(block):
                yield from walk_schema(sub, f"{path}.{keyword}[{i}]")


def _declared_property_names(parameters: dict) -> list[tuple[str, str]]:
    """Every property name declared anywhere in the tree, with the path it was declared at."""
    found: list[tuple[str, str]] = []
    for path, node in walk_schema(parameters):
        block = node.get("properties")
        if isinstance(block, dict):
            found.extend((name, path) for name in block)
    return found


def screen_parameters(tool_name: str, parameters: dict, *, scope: str = "") -> None:
    """Refuse an argument schema that cannot be enforced, or that could carry a selector.

    Runs at tool DECLARATION and at adapter CONSTRUCTION, before any model has spoken — the same
    placement as the name screens in ``tools.py``, and for the same reason: a check that runs after
    the model has chosen is a log entry (``REMEDIATION.md`` Shape C).

    ``scope`` is the adapter's capability scope. For ``candidates`` and ``patient`` the rule is not
    "no patient-shaped argument" but **no argument at all** — see the module docstring.
    """
    if not isinstance(parameters, dict):
        raise SchemaNotStrict(f"tool {tool_name!r} REFUSED: `parameters` must be a JSON Schema "
                              f"object, got {type(parameters).__name__}")
    if parameters.get("type") != "object":
        raise SchemaNotStrict(
            f"tool {tool_name!r} REFUSED: the argument schema must declare `type: object`. The "
            f"agent calls tools with a keyword mapping and nothing else.")
    if not isinstance(parameters.get("properties", {}), dict):
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

    # -- every level of the tree, not only the top one --------------------------------
    for path, node in walk_schema(parameters):
        if "patternProperties" in node:
            raise SchemaNotStrict(
                f"tool {tool_name!r} REFUSED: `patternProperties` at {path}. A pattern admits "
                f"property names nobody has written down, so no screen over names can see what it "
                f"lets through. Declare the properties.")
        looks_like_object = node.get("type") == "object" or "properties" in node
        if looks_like_object and node.get("additionalProperties", None) is not False:
            raise SchemaNotStrict(
                f"tool {tool_name!r} REFUSED: the object schema at {path} does not set "
                f"`additionalProperties: false`. An open schema cannot reject an argument it never "
                f"mentioned, and the argument it never mentioned is how an identity gets supplied "
                f"(REMEDIATION.md R-08/R-11). Nested objects count: closing only the top level "
                f"leaves a `filters` object carrying `patient_id` wide open.")

    declared = _declared_property_names(parameters)

    # -- subject-bound tools take nothing ---------------------------------------------
    if scope in ("candidates", "patient") and declared:
        raise SelectorArgumentRefused(
            f"tool {tool_name!r} REFUSED: a {scope}-scoped tool declares "
            f"{sorted({n for n, _ in declared})}, and a tool bound to a human's choice takes NO "
            f"model-authored arguments at all. Not 'no patient-shaped argument' — none: a "
            f"denylist of names is the guard R-10 already failed with, and any argument on this "
            f"tool is a channel through which the model, reading attacker-controlled documents, "
            f"could narrow or redirect a subject a human attested to "
            f"(CLAUDE.md §Hard constraints 1; REMEDIATION.md R-08).")

    offending = sorted({name for name, _ in declared if _tokens(name) & _IDENTITY_ARGUMENT_TOKENS})
    if offending:
        raise IdentityArgumentRefused(
            f"tool {tool_name!r} REFUSED: argument(s) {offending} would let the caller name a "
            f"patient, and the caller is a model reading attacker-controlled documents. A "
            f"patient-scoped read binds its subject to a PatientReadGrant at construction; it "
            f"never takes one as an argument (CLAUDE.md §Hard constraints 1; REMEDIATION.md R-08)")

    generic = sorted({n for n, _ in declared if n.strip().lower() in _GENERIC_SELECTOR_NAMES})
    if generic:
        raise IdentityArgumentRefused(
            f"tool {tool_name!r} REFUSED: argument(s) {generic} select a row without naming what "
            f"kind of row, so 'use record 2' needs no patient vocabulary to arrive. Name the "
            f"domain — `intake_id`, `document_id` — or take no argument.")


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
    "SelectorArgumentRefused",
    "UngroundedArgument",
    "check_argument_grounding",
    "preferred_order",
    "screen_parameters",
    "validate_arguments",
    "walk_schema",
]

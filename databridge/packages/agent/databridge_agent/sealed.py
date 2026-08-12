"""Sealed read adapters — the structural reason "read-only" is now more than a label.

``REMEDIATION.md`` R-07: ``effect: Literal["read"]`` annotated an **arbitrary callable**. A tool
honestly declared ``read`` and named ``clinic_lookup`` could open a socket, POST somewhere, or call
``EvidenceLedger.commit``, and nothing in this package would notice — the label described the
author's intention, not the code. A label a reviewer has to trust is not a control.

So the callable a ``Tool`` may hold is no longer arbitrary. It must be a ``SealedReadAdapter``, and
that base class imposes four things at CLASS-DEFINITION time and at CALL time, all deterministic
and none of them dependent on a model or an author behaving well:

1. **A closed capability.** Every adapter declares one of exactly three capabilities
   (``ClinicStructuralRead``, ``CandidateRead``, ``PatientScopedRead``). There is no write member
   and no way to add one without a schema change a reviewer sees.
2. **No transport, no writer, in the module that defines it.** ``__init_subclass__`` reads the
   source file the subclass was defined in and refuses it if it imports a transport or a vendor
   SDK, or if it contains a write-shaped call (``upsert_*``, ``.commit(``, SQL DML, a write-mode
   ``open``). Source unavailable ⇒ refused: a guard that cannot see the code fails closed.
   This is the same scan ``tests/test_no_send_capability.py`` runs over this package, pointed at
   the code that arrives from outside it — the composition root's adapters are exactly the code
   this package cannot otherwise review.
3. **The subject is bound at CONSTRUCTION, never supplied by the model.** A patient-scoped adapter
   holds a ``PatientReadGrant``, and the grant is created by the composition root at the moment a
   named human attests to an in-person selection. The model's only channel into this package is a
   JSON object parsed into ``ToolPlan``; it has no field that can carry a grant, and a
   patient- or candidate-scoped adapter may declare **no arguments at all**
   (``policy.screen_parameters``, called from ``__init__``). That is R-08: the model cannot make an
   identity decision through tool ARGUMENTS because such a tool has no arguments.
4. **Arguments are validated, and the result is checked, on the way out.** ``__call__`` validates
   every call against an IMMUTABLE snapshot of the schema taken at construction, then refuses
   anything that is not a ``ToolResult`` for this tool — so an adapter cannot hand back a bare dict
   that skipped the per-value grounding wall. Validation lives here, at the final callable
   boundary, rather than in the agent loop: ``Tool.call()`` and a direct ``adapter(**args)`` reach
   the same check, which is what the second review found missing.

**What this does NOT claim — read this before relying on any of it.**

* *The scan is textual, and it covers exactly one file: the module the subclass is defined in.* It
  does not follow imports, so an adapter that calls ``helpers.fetch()`` in a neighbouring module is
  not examined at all. ``getattr``/``importlib``-assembled module names, ``eval``, a connection
  handle captured before the class was defined, a callable injected through ``__init__``
  (``StaticReadAdapter``'s ``log``), a C extension — all get through. The scan is a wall against the
  ordinary way this goes wrong (somebody adds ``import httpx`` to a tool module on a deadline), and
  it is loud when it fires. **It is not a sandbox, it is not an authorization boundary, and this
  package must never be described as one.** Do not run this process with network egress, mail
  credentials or a writable ledger handle on the assumption that the scan prevents their use.
* *A ``PatientReadGrant`` is an assertion by the code that constructed it, not proof that a human
  attested to anything.* Any caller in-process can write ``attested_by="Alice"``. What this package
  guarantees is only that the fields exist and are not machine-shaped. The binding to a real person
  is the composition root's job — see ``PatientReadGrant``'s own docstring for the rule the route
  must follow.
"""
from __future__ import annotations

import abc
import copy
import inspect
import pathlib
import re
import sys
import weakref
from datetime import datetime
from typing import Any, ClassVar, Literal, Union

from pydantic import BaseModel, ConfigDict

from . import policy
from .results import ToolResult


class AdapterRefused(RuntimeError):
    """An adapter class or instance was refused. Subclasses map to stable causes."""


class TransportInAdapter(AdapterRefused):
    """The module defining this adapter can reach the outside world."""


class WriterInAdapter(AdapterRefused):
    """The module defining this adapter can write — to the ledger, the graph or the disk."""


class AdapterSourceUnavailable(AdapterRefused):
    """The scan could not read the adapter's source. Refused: an unreadable guard is no guard."""


class SealBroken(AdapterRefused):
    """An adapter returned something the seal does not permit."""


class AdapterSealMutated(AdapterRefused):
    """Something tried to change a declaration after it was screened. It is fixed at construction."""


class SubjectMismatch(SealBroken):
    """A patient-scoped adapter returned a row labelled with a different subject than its grant."""


class GrantRefused(RuntimeError):
    """A patient-scoped capability was constructed without a human attestation behind it."""


# --- the module scan --------------------------------------------------------------
# Anything that can open a connection, send mail, spawn a process, or speak a third-party API.
# Written as string data, never as imports: this package imports none of them, and
# `test_no_send_capability.py` asserts that against the source.
_FORBIDDEN_MODULES = (
    "smtplib", "imaplib", "poplib", "socket", "ssl", "http", "httpx", "requests", "urllib",
    "aiohttp", "websockets", "ftplib", "telnetlib", "slack_sdk", "slack_bolt", "slackclient",
    "googleapiclient", "google_auth_oauthlib", "boto3", "botocore", "paramiko", "smtpd",
    "subprocess", "webbrowser", "xmlrpc", "anthropic", "openai", "litellm", "cohere", "mistralai",
    "ollama",
)

_FORBIDDEN_IMPORT = re.compile(
    r"^[ \t]*(?:import|from)[ \t]+(" + "|".join(_FORBIDDEN_MODULES) + r")\b", re.M)

# Write-shaped calls. Deliberately a short, specific list: SQL keywords are matched CASE-SENSITIVE
# in their uppercase form so prose about updating a record does not trip it, and the graph writers
# are named exactly (`databridge_graph.store`). If you land here, the adapter is doing something a
# read adapter must not — the fix is to move that code to the composition root, never to shorten
# this list.
_WRITE_SHAPED: tuple[tuple[str, re.Pattern], ...] = (
    ("graph_writer", re.compile(r"\b(?:upsert_node|upsert_edge|replace_node|append_sources)\s*\(")),
    ("ledger_writer", re.compile(r"\.commit\s*\(")),
    ("sql_dml", re.compile(
        r"\b(?:INSERT\s+INTO|UPDATE\s+\w|DELETE\s+FROM|DROP\s+TABLE|CREATE\s+TABLE|"
        r"REPLACE\s+INTO|ALTER\s+TABLE)\b")),
    ("executescript", re.compile(r"\.executescript\s*\(")),
    ("write_mode_open", re.compile(r"\bopen\s*\([^)]*,\s*[\"'][^\"']*[wax+]")),
    ("path_write", re.compile(r"\.(?:write_text|write_bytes|mkdir|unlink|rmdir|rename)\s*\(")),
)


def scan_source(source: str, what: str) -> None:
    """Refuse ``source`` if it can reach outward or write. Public so a test can plant a violation.

    A test that only asserts "the clean case passes" would stay green if the patterns were wrong,
    which is the failure mode this repo has hit before.
    """
    outward = sorted({m.group(1) for m in _FORBIDDEN_IMPORT.finditer(source)})
    if outward:
        raise TransportInAdapter(
            f"{what} REFUSED: the module defining it imports {outward}. A read adapter with a "
            f"transport is a send capability with a read name — the exact thing R-07 found "
            f"(GRAPH_AGENT_AND_MCP.md §3: the agent never holds send credentials)")
    for rule, pattern in _WRITE_SHAPED:
        hit = pattern.search(source)
        if hit:
            raise WriterInAdapter(
                f"{what} REFUSED: the module defining it contains a write-shaped call "
                f"({rule}: {hit.group(0)!r}). Reads and writes live in different processes; a "
                f"writer reachable from the agent collapses that boundary into a naming "
                f"convention (REMEDIATION.md R-07, R-34)")


def _module_source(cls: type) -> tuple[str, str]:
    path = None
    try:
        path = inspect.getsourcefile(cls)
    except TypeError:                                  # pragma: no cover - builtins only
        path = None
    if path is None:
        module = sys.modules.get(cls.__module__)
        path = getattr(module, "__file__", None)
    if not path:
        raise AdapterSourceUnavailable(
            f"adapter {cls.__name__!r} REFUSED: its source is not readable, so the read-only scan "
            f"cannot run. A guard that cannot see the code fails closed.")
    try:
        return pathlib.Path(path).read_text(encoding="utf-8"), str(path)
    except OSError as exc:
        raise AdapterSourceUnavailable(
            f"adapter {cls.__name__!r} REFUSED: {exc}") from None


#: Classes this process has actually scanned. A module-level set rather than a class attribute,
#: because a class attribute is settable from the class body — ``scanned_source = "trust me"``
#: would be the same one-line off switch as ``_abstract_base``, which is the hole this round of
#: review was about. A class cannot put itself in here while it is being defined.
_SCANNED: "weakref.WeakSet[type]" = weakref.WeakSet()


def _scan_class(cls: type) -> None:
    """Scan the module ``cls`` was defined in, unless this exact class has already been scanned.

    Membership is by class IDENTITY, so a subclass never inherits its parent's clearance: an
    inherited scan record describes a different file than the one the subclass lives in, and the
    subclass's file is the one that will run.
    """
    if cls in _SCANNED:
        return
    source, path = _module_source(cls)
    scan_source(source, f"adapter {cls.__name__!r}")
    # Informational only — the audit trail says which file was read. Nothing branches on it.
    cls.scanned_source = path
    _SCANNED.add(cls)


# --- the closed capability set ----------------------------------------------------


class PatientReadGrant(BaseModel):
    """Permission to read ONE patient's record, created by a human, bound to its subject.

    This is the whole of R-08's fix in one type. Before it exists the agent has no patient-scoped
    tool at all; after it exists the tool reads exactly the row named here and takes no argument
    that could redirect it.

    ``selection_made_in_person`` is ``Literal[True]`` — the device from
    ``ResolutionResult.requires_human_selection`` (CLAUDE.md §Hard constraints 1). There is no
    value that means "the machine decided", so a grant asserting anything else is unconstructible.

    ``subject_fingerprint`` binds the grant to the choice the human was actually offered — the
    document revision, the selected registry row and a digest of the candidate set (the design in
    ``REMEDIATION.md`` §1 Shape B). It is required, and it is opaque to this package: computing it
    is the composition root's job because only the route sees the resolution. What this package
    guarantees is that a grant cannot exist without one.

    **What this type is NOT.** It is an *assertion made by the code that constructed it*, not proof
    that a named human stood at a counter. Any in-process caller can write
    ``attested_by="Alice"``, invent a fingerprint and set ``selection_made_in_person=True``; the
    class cannot tell the difference, and nothing here binds the grant to an authenticated session
    or to the candidate set a human was actually shown. This package screens the *shape* of the
    claim and nothing more.

    So the rule for whoever builds the route, stated here because this is where it will be read:

    * **Never deserialize a grant from a request.** Not from a body, not from a header, not from a
      cookie. A grant arriving over HTTP is an identity decision made by whoever sent the request,
      which for this system includes a model reading an attacker's document.
    * Construct it **server-side only**, from the staff session that is already authenticated, at
      the moment the confirmation endpoint records the in-person attestation, with the fingerprint
      computed from the resolution the route itself performed.
    * A grant should not outlive that attestation. Re-attesting is cheap; a stale grant is an
      identity nobody checked.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    patient_id: str
    subject_fingerprint: str
    attested_by: str                       # a staff id from the shift session — never "system"
    attested_at: datetime
    selection_made_in_person: Literal[True] = True

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        # Checked after validation rather than as a field constraint so the message can say why.
        if not self.attested_by.strip() or self.attested_by.strip().lower() in _NOT_A_HUMAN:
            raise GrantRefused(
                f"PatientReadGrant REFUSED: attested_by={self.attested_by!r} is not a named "
                f"person. Identity is attested by a human at the counter, in person "
                f"(CLAUDE.md §Hard constraints 1)")
        if not self.subject_fingerprint.strip():
            raise GrantRefused(
                "PatientReadGrant REFUSED: no subject_fingerprint. An attestation not bound to "
                "the document revision and candidate set it was made against can authorise a "
                "record it was never about (REMEDIATION.md Shape B)")


_NOT_A_HUMAN = frozenset({
    "system", "agent", "the agent", "copilot", "model", "llm", "auto", "automatic", "machine",
    "walnut", "databridge", "none", "null", "unknown", "n/a",
})


class ClinicStructuralRead(BaseModel):
    """Counts, statuses and structure across the clinic. Names no patient and returns no NRIC.

    The capability ``/intelligence`` needs: "which issuers sent the most documents this week, and
    what is still blocked?" is answerable entirely from here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    scope: Literal["clinic_structural"] = "clinic_structural"


class CandidateRead(BaseModel):
    """The ONLY identity-adjacent capability available before an attestation exists.

    It returns registry candidates and carries ``requires_human_selection: Literal[True]``, which
    ``render.py`` turns into fixed, code-authored wording on every answer built from it. The agent
    may say "three candidates, a human chooses"; there is no capability through which it could
    choose, and no argument through which the model could name one (``policy.screen_parameters``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    scope: Literal["candidates"] = "candidates"
    requires_human_selection: Literal[True] = True


class PatientScopedRead(BaseModel):
    """Reads one patient's record. Constructible only from a ``PatientReadGrant``."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    scope: Literal["patient"] = "patient"
    grant: PatientReadGrant

    @property
    def patient_id(self) -> str:
        """The subject — taken from the human's attestation, never from a tool argument."""
        return self.grant.patient_id


ReadCapability = Union[ClinicStructuralRead, CandidateRead, PatientScopedRead]


# --- the adapter ------------------------------------------------------------------


class _FrozenDict(dict):
    """A dict that refuses mutation. Used for the schema snapshot the seal enforces against.

    A dict SUBCLASS rather than ``MappingProxyType`` for one practical reason: ``jsonschema``'s
    type checker asks ``isinstance(instance, dict)``, and a mapping proxy is not one — a frozen
    schema that fails its own metaschema check would be worse than no freezing at all.
    """

    def _refuse(self, *args: Any, **kwargs: Any):
        raise AdapterSealMutated(
            "the argument schema is sealed at construction: it was screened once, and the screen "
            "is only a control if the thing it screened is the thing that runs. Declare the "
            "schema on the class or pass it to __init__ (sealed.py; REMEDIATION.md R-11)")

    __setitem__ = __delitem__ = _refuse
    setdefault = update = pop = popitem = clear = _refuse   # type: ignore[assignment]

    def __copy__(self) -> dict:
        return dict(self)

    def __deepcopy__(self, memo: dict) -> dict:
        return {k: copy.deepcopy(v, memo) for k, v in self.items()}


class _FrozenList(list):
    """The list half of the same freeze. A ``list`` subclass, for the reason above."""

    def _refuse(self, *args: Any, **kwargs: Any):
        raise AdapterSealMutated("the argument schema is sealed at construction")

    append = extend = insert = remove = pop = clear = sort = reverse = _refuse  # type: ignore[assignment]
    __setitem__ = __delitem__ = __iadd__ = __imul__ = _refuse                   # type: ignore[assignment]

    def __copy__(self) -> list:
        return list(self)

    def __deepcopy__(self, memo: dict) -> list:
        return [copy.deepcopy(v, memo) for v in self]


def freeze(node: Any) -> Any:
    """A deep, immutable copy of a JSON-shaped structure. Public so a test can prove it is frozen."""
    if isinstance(node, dict):
        return _FrozenDict({k: freeze(v) for k, v in node.items()})
    if isinstance(node, list):
        return _FrozenList(freeze(v) for v in node)
    return node


# Row keys that LABEL a subject. Matched as whole tokens, so `registry_row` and `document_id` are
# not subject labels and an ordinary read is not second-guessed. Narrow on purpose: this is a check
# on rows that name a patient, not a claim that every returned row was scoped to one.
_SUBJECT_KEY_TOKENS = frozenset({"patient", "patientid", "subject", "mrn"})


def _name_tokens(name: str) -> set[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return {t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t}


class SealedReadAdapter(abc.ABC):
    """The only thing a ``Tool`` may call.

    Subclasses declare ``name``, ``description``, ``parameters`` (a strict JSON Schema) and
    implement ``read``. They are scanned at class definition; they are called only through
    ``__call__``, which validates the arguments against the sealed schema and checks what comes
    back.

    **Two things are fixed at construction and cannot be changed afterwards**: ``parameters`` — a
    deep-frozen snapshot, so mutating it later cannot widen the schema past the screen it already
    passed — and ``name``, which is what every citation in the answer is attributed to.
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    # A STRICT object schema: `additionalProperties: false` at every level, no `patternProperties`,
    # no selector-shaped property names, and — for a candidate- or patient-scoped adapter — no
    # properties at all. Screened by `policy.screen_parameters` in `__init__` and enforced against
    # every call in `__call__` (R-08/R-11).
    parameters: ClassVar[dict] = {"type": "object", "properties": {}, "additionalProperties": False}
    # "broad" marks a general search primitive — one that takes free text and can reach anything.
    # `policy.check_argument_grounding` binds its arguments to the question that was asked, so it
    # cannot be pointed at whatever an injected document suggests (R-11, second half). That check
    # needs the question, which only the loop has, so it runs there and not here.
    breadth: ClassVar[Literal["narrow", "broad"]] = "narrow"
    # Argument names whose value must appear in the question or in what the tools already returned.
    grounded_arguments: ClassVar[frozenset] = frozenset()

    #: Set by the scan — the file it actually read, for the audit trail. **Informational.** Nothing
    #: branches on it; whether a class has been scanned is tracked in ``_SCANNED``, which a class
    #: body cannot write to.
    scanned_source: ClassVar[str] = ""

    #: Only ``sealed.py`` itself may declare a base class that is not scanned. A concrete subclass
    #: in another module setting this has no effect — see ``__init_subclass__``.
    _abstract_base: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # `_abstract_base` used to be honoured wherever it was set, which meant any concrete
        # adapter could opt out of the scan with one line — the guard's own off switch, reachable
        # by the code it guards. It is now honoured only for classes defined in THIS module, which
        # is code that ships with the package and is reviewed with it. Everything else is scanned,
        # abstract or not: an abstract intermediate's module is still code that will run.
        if cls.__module__ == __name__ and cls.__dict__.get("_abstract_base", False):
            return
        _scan_class(cls)

    def __init__(self, capability: ReadCapability) -> None:
        if not isinstance(capability, (ClinicStructuralRead, CandidateRead, PatientScopedRead)):
            raise AdapterRefused(
                f"adapter {type(self).__name__!r} REFUSED: {type(capability).__name__} is not one "
                f"of the three read capabilities. The set is closed and contains no writer.")
        if not self.name:
            raise AdapterRefused(f"adapter {type(self).__name__!r} REFUSED: no tool name")
        # A class that reached here without being scanned — built by `type()`, or inheriting a
        # parent's `scanned_source` — is scanned now. `cls.__dict__` rather than `getattr`, because
        # an inherited scan record is a record of a DIFFERENT file.
        _scan_class(type(self))

        # Screened, then snapshotted, in that order: the thing that runs must be the thing that
        # was screened. `dict(...)` first so a subclass that assigned a plain dict is normalised.
        declared = copy.deepcopy(dict(self.parameters))
        policy.screen_parameters(self.name, declared, scope=capability.scope)

        object.__setattr__(self, "_capability", capability)
        object.__setattr__(self, "parameters", freeze(declared))
        object.__setattr__(self, "_sealed", True)

    #: Declarations fixed at construction. Rebinding them would change what the model is shown, or
    #: what a call is validated against, after the screens have run.
    _FIXED_AFTER_INIT = ("parameters", "name", "_capability", "_sealed")

    def __setattr__(self, key: str, value: Any) -> None:
        if getattr(self, "_sealed", False) and key in self._FIXED_AFTER_INIT:
            raise AdapterSealMutated(
                f"adapter {self.name!r} REFUSED: {key!r} is fixed at construction. It was screened "
                f"once — `policy.screen_parameters` for the schema, `tools.screen_declaration` for "
                f"the name — and a declaration that can change afterwards is a declaration nobody "
                f"has screened (REMEDIATION.md R-08/R-11).")
        object.__setattr__(self, key, value)

    @property
    def capability(self) -> ReadCapability:
        return self._capability

    @property
    def requires_human_selection(self) -> bool:
        """True when anything this adapter returns is a candidate list, not a determination."""
        return isinstance(self._capability, CandidateRead)

    @property
    def subject(self) -> str:
        """The patient this adapter is bound to, or "" if it is not patient-scoped."""
        cap = self._capability
        return cap.patient_id if isinstance(cap, PatientScopedRead) else ""

    @abc.abstractmethod
    def read(self, **arguments: Any) -> ToolResult:
        """Do the read. Arguments have already been validated against ``parameters``."""

    def __call__(self, **arguments: Any) -> ToolResult:
        """Validate, read, check. THE call boundary — every path into ``read`` comes through here.

        Validation was previously done by the agent loop, which left ``Tool.call()`` and a direct
        ``adapter(**args)`` unvalidated. It is here now, against the frozen snapshot, so there is
        one place to review rather than one place per caller.
        """
        policy.validate_arguments(self.name, self.parameters, arguments)
        result = self.read(**arguments)
        if not isinstance(result, ToolResult):
            raise SealBroken(
                f"adapter {self.name!r} returned {type(result).__name__}, not ToolResult. A bare "
                f"dict has skipped the per-value grounding wall, so its values carry no pointers "
                f"and an answer built on them would look cited and not be.")
        if result.tool != self.name:
            raise SealBroken(
                f"adapter {self.name!r} returned a result labelled {result.tool!r}. The label is "
                f"what the answer attributes each value to; a mismatch misattributes evidence.")
        self._check_subject(result)
        return result

    def _check_subject(self, result: ToolResult) -> None:
        """A patient-scoped read must not return a row LABELLED with a different subject.

        Honest about its reach: this catches a row that names a patient other than the one the
        human attested to. It cannot prove the underlying query was scoped — that has to be
        enforced where the query is written, in the composition root, because only that code knows
        what it asked the datastore for. It is a tripwire on the way out, not the boundary.
        """
        cap = self._capability
        if not isinstance(cap, PatientScopedRead):
            return
        for index, row in enumerate(result.rows):
            for key, value in row.values.items():
                if not (_name_tokens(key) & _SUBJECT_KEY_TOKENS):
                    continue
                if isinstance(value, str) and value and value != cap.patient_id:
                    raise SubjectMismatch(
                        f"adapter {self.name!r} REFUSED: row {index} is labelled "
                        f"{key}={value!r}, but this adapter is bound to {cap.patient_id!r} by a "
                        f"human's attestation. A patient-scoped read returning another patient's "
                        f"row is the identity the grant exists to prevent "
                        f"(CLAUDE.md §Hard constraints 1).")


__all__ = [
    "AdapterRefused",
    "AdapterSealMutated",
    "AdapterSourceUnavailable",
    "CandidateRead",
    "ClinicStructuralRead",
    "GrantRefused",
    "PatientReadGrant",
    "PatientScopedRead",
    "ReadCapability",
    "SealBroken",
    "SealedReadAdapter",
    "SubjectMismatch",
    "TransportInAdapter",
    "freeze",
    "WriterInAdapter",
    "scan_source",
]

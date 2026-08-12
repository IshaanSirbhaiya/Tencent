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
   JSON object parsed into ``ToolPlan``; it has no field that can carry a grant, and
   ``policy.screen_parameters`` refuses any tool whose schema would let arguments carry a patient
   id. That is R-08: the model cannot make an identity decision through tool ARGUMENTS because no
   argument can name a patient.
4. **The result is checked on the way out.** ``__call__`` refuses anything that is not a
   ``ToolResult`` for this tool, so an adapter cannot hand back a bare dict that skipped the
   per-value grounding wall.

What this does NOT claim: that a determined author cannot defeat it. ``getattr``-assembled module
names, a connection handle captured before the class was defined, a C extension — all get through.
The scan is a wall against the ordinary way this goes wrong (somebody adds ``import httpx`` to a
tool module on a deadline), and it is loud when it fires. It is not a sandbox, and this package
should never be described as one.
"""
from __future__ import annotations

import abc
import inspect
import pathlib
import re
import sys
from datetime import datetime
from typing import Any, ClassVar, Literal, Union

from pydantic import BaseModel, ConfigDict

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


class SealedReadAdapter(abc.ABC):
    """The only thing a ``Tool`` may call.

    Subclasses declare ``name``, ``description``, ``parameters`` (a strict JSON Schema) and
    implement ``read``. They are scanned at class definition; they are called only through
    ``__call__``, which checks what comes back.
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    # A STRICT object schema: `additionalProperties: false`, enforced by `policy.screen_parameters`
    # when the tool is declared, and validated against every call before the adapter runs (R-11).
    parameters: ClassVar[dict] = {"type": "object", "properties": {}, "additionalProperties": False}
    # "broad" marks a general search primitive — one that takes free text and can reach anything.
    # `policy.check_argument_grounding` binds its arguments to the question that was asked, so it
    # cannot be pointed at whatever an injected document suggests (R-11, second half).
    breadth: ClassVar[Literal["narrow", "broad"]] = "narrow"
    # Argument names whose value must appear in the question or in what the tools already returned.
    grounded_arguments: ClassVar[frozenset] = frozenset()

    #: Set by ``__init_subclass__`` — the file the scan actually read, for the audit trail.
    scanned_source: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if inspect.isabstract(cls) or getattr(cls, "_abstract_base", False):
            return
        source, path = _module_source(cls)
        scan_source(source, f"adapter {cls.__name__!r}")
        cls.scanned_source = path

    def __init__(self, capability: ReadCapability) -> None:
        if not isinstance(capability, (ClinicStructuralRead, CandidateRead, PatientScopedRead)):
            raise AdapterRefused(
                f"adapter {type(self).__name__!r} REFUSED: {type(capability).__name__} is not one "
                f"of the three read capabilities. The set is closed and contains no writer.")
        if not self.name:
            raise AdapterRefused(f"adapter {type(self).__name__!r} REFUSED: no tool name")
        self._capability = capability

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
        return result


__all__ = [
    "AdapterRefused",
    "AdapterSourceUnavailable",
    "CandidateRead",
    "ClinicStructuralRead",
    "GrantRefused",
    "PatientReadGrant",
    "PatientScopedRead",
    "ReadCapability",
    "SealBroken",
    "SealedReadAdapter",
    "TransportInAdapter",
    "WriterInAdapter",
    "scan_source",
]

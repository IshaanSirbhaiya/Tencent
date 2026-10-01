"""**No ambient authority** (AGENT_PLANE §3) - the runtime handle, and what it refuses.

    "a capability not listed here cannot be requested at runtime, and there is no escalation
     path that adds one without a new pack version."

That sentence is only a control if the runtime has no other way to obtain one. So this module
offers exactly one object, `GrantedAuthority`, and it is built from a `CompiledAgent` - which
cannot exist without all four artefacts, which in turn cannot be produced without every check in
`compiler.py` passing.

## What the class deliberately does not have

No `add`, `grant`, `escalate`, `with_capability`, `elevate`, `request`, `extend` or `widen`. Not
"present but raising" - *absent*. A method that exists and refuses is a method somebody will
eventually make succeed under a flag; a method that does not exist has to be written, reviewed and
merged first, which is the point.

The only way to widen an agent's authority is to edit its declaration, bump `version`, and
recompile. `tests/test_no_ambient_authority.py` asserts the absence over the class's whole public
surface rather than over the eight names listed above, so a widening method added later fails the
test even if nobody thought to add it to a list.

## The one word that must not be conflated

`CapabilityGrant.grant_id` and `Principal.grant_id` are **different things**, and this module
keeps them apart on purpose:

* a **capability grant** is what this package mints - authority for an agent version to ask an
  adapter for a capability, under a purpose;
* a **subject-read grant** (`Principal.grant_id`) is minted *after a named human attests to a
  candidate in person*, and is required when the pack declares the purpose subject-scoped.

Passing the first where the second belongs would let a compiled artefact stand in for a human's
attestation, which is the failure CLAUDE.md's first hard constraint exists to prevent. `principal`
therefore names its parameter `subject_grant_id` and refuses a value that is one of this agent's
own capability grant ids.
"""
from __future__ import annotations

from typing import Optional

from databridge_adapter import Principal

from .artefacts import BudgetAdmission, CapabilityGrant, CompiledAgent, EvalBinding, SinkPolicy
from .compiler import CompilationDecision, Outcome
from .errors import AgentDefError, AmbientAuthorityRefused, CompilationRefused


class GrantedAuthority:
    """Everything a compiled agent may do, and nothing else. Sealed at construction.

    ``__slots__`` and the absent mutators are the whole implementation. There is no state to
    change: the authority IS the compiled artefact, and a different authority is a different
    compilation.
    """

    __slots__ = ("_compiled",)

    def __init__(self, compiled: CompiledAgent) -> None:
        if not isinstance(compiled, CompiledAgent):
            raise AgentDefError(
                f"a GrantedAuthority is built from a CompiledAgent, not from "
                f"{type(compiled).__name__}. There is no path from a declaration to authority "
                f"that skips the compiler, because the compiler is where the refusals live")
        object.__setattr__(self, "_compiled", compiled)

    # --- construction ---
    @classmethod
    def from_decision(cls, decision: CompilationDecision) -> "GrantedAuthority":
        """Authority from a compilation decision, or `CompilationRefused` carrying the reasoning.

        The refused branch raises rather than returning `None`: a caller that forgot to check a
        `None` would carry on with no authority object and no error, and "the agent ran without a
        grant" must not be reachable by forgetting an `if`.
        """
        if decision.outcome is not Outcome.COMPILED or decision.compiled is None:
            raise CompilationRefused(decision)
        return cls(decision.compiled)

    # --- what it is ---
    @property
    def compiled(self) -> CompiledAgent:
        return self._compiled

    @property
    def agent(self) -> str:
        return self._compiled.agent

    @property
    def version(self) -> int:
        return self._compiled.version

    @property
    def pack(self) -> str:
        return self._compiled.pack

    @property
    def purpose(self) -> str:
        """The single purpose this agent version reads under (spine D5).

        One purpose per declaration, not a list. A purpose is the lawful basis recorded on every
        read; an agent that could choose among several at runtime would be choosing its own legal
        justification, and the choice would not be in any reviewed file.
        """
        return self._compiled.purpose

    @property
    def capabilities(self) -> frozenset[str]:
        return self._compiled.grants.capabilities

    @property
    def sink_policy(self) -> SinkPolicy:
        return self._compiled.sink_policy

    @property
    def budget(self) -> BudgetAdmission:
        return self._compiled.budget

    @property
    def evals(self) -> EvalBinding:
        return self._compiled.evals

    # --- what it permits ---
    def holds(self, capability: str) -> bool:
        return capability in self.capabilities

    def require(self, capability: str, purpose: Optional[str] = None) -> CapabilityGrant:
        """The grant for ``capability``, or `AmbientAuthorityRefused`.

        ``purpose`` defaults to the declared one. Passing a different purpose is refused rather
        than silently ignored: a caller that names a purpose means it, and quietly substituting
        the declaration's would put a lawful basis into the ledger that the caller did not intend.
        """
        want = purpose if purpose is not None else self.purpose
        if want != self.purpose:
            raise AmbientAuthorityRefused(
                f"{self.agent}@v{self.version} reads under purpose {self.purpose!r} and was asked "
                f"for {want!r}. A purpose is not a parameter of a request - it is a field of the "
                f"principal, declared once per agent and reviewed (spine D5). To read under "
                f"another purpose, declare another agent.")
        return self._compiled.grants.require(capability, want)

    def principal(self, *, actor: str, on_behalf_of: Optional[str],
                  subject_grant_id: Optional[str] = None) -> Principal:
        """A `Principal` for this agent's reads: actor, the human if any, and the purpose.

        ``on_behalf_of`` has no default, deliberately - `None` means *no human asked*, which is a
        decision the caller states rather than one that happens by omission.

        ``subject_grant_id`` is the **subject-read** grant, minted after a named human attests to
        a candidate in person. It is not one of this agent's capability grants, and passing one is
        refused: a compiled artefact must never be able to stand in for an attestation.
        """
        if subject_grant_id is not None:
            own = {g.grant_id for g in self._compiled.grants}
            if subject_grant_id in own:
                raise AgentDefError(
                    f"subject_grant_id {subject_grant_id!r} is one of this agent's CAPABILITY "
                    f"grants. Those are different things: a capability grant is compiled from a "
                    f"declaration, and a subject-read grant is minted after a named human attests "
                    f"to a candidate in person. Passing the first here would let a compiled "
                    f"artefact stand in for an attestation, which is the one thing this system "
                    f"never automates (CLAUDE.md hard constraint 1)")
        return Principal(actor=actor, on_behalf_of=on_behalf_of, purpose=self.purpose,
                         grant_id=subject_grant_id)

    def forbids_sink(self, sink: str) -> bool:
        """Whether an untrusted value held by this agent is barred from ``sink``.

        True for every position in the trusted-sink inventory, by construction - see `SinkPolicy`.
        The method exists so a caller asks the policy rather than reimplementing the list.
        """
        return sink in self._compiled.sink_policy.forbidden_sinks

    def __repr__(self) -> str:                                  # pragma: no cover - debug aid
        return (f"GrantedAuthority({self._compiled.ref}, "
                f"{len(self._compiled.grants)} grant(s), purpose={self.purpose!r})")

"""The **four artefacts** a declaration compiles to (AGENT_PLANE §3), and nothing else.

    1. a capability grant set   - each grant bound to a specific adapter AND purpose
    2. a sink policy            - which TRUSTED_SINKS positions this agent's untrusted values
                                  may not reach
    3. a budget admission record
    4. an eval binding          - a golden set that must pass before the agent may run

`CompiledAgent` is the four of them together, and it **cannot be constructed with any of them
missing**. That is the structural half of "an agent that will not compile will not run": there is
no partially-compiled object for a caller to hold, so there is nothing for a later code path to
find and use. The other half is in `compiler.py`, which has no waiver parameter.

## Why grants carry the purpose

A grant is `(adapter, capability, purpose)`, never `(adapter, capability)`. Purpose is a field of
the *principal* (spine D5) and it selects which policy runs and what the ledger records as the
lawful basis, so a grant that did not name one would authorise a read whose legal justification is
decided later, by whoever calls it. `CapabilityGrantSet.require` therefore takes both, and there
is no single-argument convenience overload.

## What is NOT here

**Nothing writes to the graph.** D10 makes an agent a bi-temporal node with a `SourcePointer` to
the file that declares it, and that is out of scope for this package: `CompiledAgent` carries
`declared_in` and `declaration_digest`, which is what such a pointer would be built from, and
`__init__.py` states exactly what persistence would still need.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator, Mapping, Optional, Sequence

from databridge_adapter import ADAPTER_SINKS, TRUSTED_SINKS

from .declaration import ActionDecl
from .errors import AgentDefError, AmbientAuthorityRefused
from .feasibility import FeasibilityReport


def _digest(payload: dict, prefix: str) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"{prefix}_{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16]}"


#: Finding 1 (R-013): `CompiledAgent.__post_init__` used to check only that all four artefacts
#: were present and belonged to the same agent/version - a caller could satisfy that by
#: constructing the artefacts from the PUBLIC constructors by hand and skip every check in
#: `compiler.py` entirely. This sentinel closes the actual bypass the finding demonstrated:
#: `CompiledAgent` now refuses construction unless it is handed this exact object, and nothing
#: outside `compiler.compile_agent` holds it - `__init__.py`'s `__all__` never names it, and this
#: module is not documented as public API.
#:
#: **The honest limit, stated once so it does not need restating at every call site:** this is
#: Python, not a sandbox. A caller willing to `from databridge_agentdef.artefacts import
#: _COMPILER_MINT` gets the real token and can still forge a `CompiledAgent`. No in-process
#: object can stop a caller who chooses to read this module's source and use its private name -
#: that is the same category of limit this codebase already accepts for in-process trust
#: elsewhere (R-012's disposition on `GrowthLedger.record`; `attestation.py`'s stated inability to
#: verify an attestor was actually present). What this closes is the realistic failure mode the
#: finding actually describes: assembling a `CompiledAgent` from the PUBLIC surface
#: (`from databridge_agentdef import CompiledAgent, CapabilityGrant, ...`) because nothing stopped
#: it. That path is refused now, unconditionally, with no parameter that reaches around it.
_COMPILER_MINT: object = object()


# --------------------------------------------------------------------------------------
# 1. the capability grant set
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class CapabilityGrant:
    """One grant: this agent version may ask THIS adapter for THIS capability, for THIS purpose.

    `max_trust` rides along because it is a property of the grant rather than of the request: the
    ceiling was declared once, reviewed once, and compiled into every grant the declaration
    produced. A caller cannot raise it by constructing a different request.
    """

    agent: str
    version: int
    pack: str
    source_id: str
    capability: str
    purpose: str
    max_trust: str
    declaration_digest: str
    resolved_through: str
    """Where the adapter's advertisement was read from - AGENT_PLANE's ``RESOLVED_THROUGH`` edge,
    as a recorded path until the graph carries the edge itself."""

    def __post_init__(self) -> None:
        for field, value in (("source_id", self.source_id), ("capability", self.capability),
                             ("purpose", self.purpose)):
            if not str(value).strip():
                raise AgentDefError(f"a capability grant must name a non-blank {field}")

    @property
    def ref(self) -> str:
        """``<source_id>.<capability>`` - the form the declaration uses."""
        return f"{self.source_id}.{self.capability}"

    @property
    def grant_id(self) -> str:
        # High 11 (R-013): `resolved_through` names WHICH advertisement the grant was screened
        # against. Two catalogues that offer the same (source_id, capability, purpose, max_trust)
        # but resolve it through different estate files are different authority decisions - the
        # identity has to be able to tell them apart, not just the record.
        return _digest({
            "agent": self.agent, "version": self.version, "pack": self.pack,
            "source_id": self.source_id, "capability": self.capability,
            "purpose": self.purpose, "max_trust": self.max_trust,
            "resolved_through": self.resolved_through,
            "declaration_digest": self.declaration_digest,
        }, "cg")

    def as_record(self) -> dict:
        return {"grant_id": self.grant_id, "agent": self.agent, "version": self.version,
                "pack": self.pack, "source_id": self.source_id, "capability": self.capability,
                "purpose": self.purpose, "max_trust": self.max_trust,
                "resolved_through": self.resolved_through}


@dataclass(frozen=True)
class CapabilityGrantSet:
    """Everything this agent version may ask for. **Closed**: there is no `add`.

    Widening is a new declaration version, reviewed and recompiled. That is not a convention the
    caller is asked to respect - this object exposes no mutator, `grants` is a tuple, and
    `tests/test_no_ambient_authority.py` asserts over every public attribute that none of them
    widens the set.
    """

    agent: str
    version: int
    grants: tuple[CapabilityGrant, ...]

    def __post_init__(self) -> None:
        if not self.grants:
            raise AgentDefError(
                f"agent {self.agent}@v{self.version} compiled to an EMPTY grant set. An agent "
                f"that may ask for nothing cannot do anything, and an empty set would make the "
                f"runtime screen vacuous: `require` would refuse everything, so nothing would "
                f"ever discover that the declaration listed no capabilities")
        seen: set[tuple[str, str, str]] = set()
        for g in self.grants:
            if g.agent != self.agent or g.version != self.version:
                raise AgentDefError(
                    f"grant set for {self.agent}@v{self.version} carries a grant belonging to "
                    f"{g.agent}@v{g.version}. A grant set is one agent version's authority; "
                    f"mixing two makes 'what could this agent do in March?' unanswerable (D10)")
            key = (g.source_id, g.capability, g.purpose)
            if key in seen:
                raise AgentDefError(f"duplicate grant {key} in {self.agent}@v{self.version}")
            seen.add(key)

    @property
    def capabilities(self) -> frozenset[str]:
        """``{'<source_id>.<capability>', ...}`` - what the declaration asked for and got."""
        return frozenset(g.ref for g in self.grants)

    @property
    def purposes(self) -> frozenset[str]:
        return frozenset(g.purpose for g in self.grants)

    def __iter__(self) -> Iterator[CapabilityGrant]:
        return iter(self.grants)

    def __len__(self) -> int:
        return len(self.grants)

    def __contains__(self, ref: object) -> bool:
        return ref in self.capabilities

    def require(self, capability: str, purpose: str) -> CapabilityGrant:
        """The grant covering ``(capability, purpose)``, or `AmbientAuthorityRefused`.

        Both arguments, always. A capability granted for underwriting is not granted for
        portfolio reporting: the purpose is what the ledger records as the lawful basis, so a
        lookup that ignored it would hand back a grant that justifies the wrong read.
        """
        for g in self.grants:
            if g.ref == capability and g.purpose == purpose:
                return g
        held = ", ".join(sorted(f"{g.ref} for {g.purpose}" for g in self.grants))
        raise AmbientAuthorityRefused(
            f"{self.agent}@v{self.version} holds no grant for capability {capability!r} under "
            f"purpose {purpose!r}. It holds: {held}. There is no escalation path that adds one: "
            f"a capability not in the declaration cannot be requested at runtime, and widening "
            f"means editing packs/<pack>/agents/{self.agent}.yaml, bumping `version`, and "
            f"recompiling under review (AGENT_PLANE §3)")

    def as_record(self) -> dict:
        return {"agent": self.agent, "version": self.version,
                "grants": [g.as_record() for g in self.grants]}


# --------------------------------------------------------------------------------------
# 2. the sink policy
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class SinkPolicy:
    """Which `TRUSTED_SINKS` positions this agent's untrusted values may not reach (spine D3).

    **The forbidden set is the whole inventory, always, and that is the honest answer.** An
    untrusted value may be stored, cited and displayed; it may never decide what happens next. So
    there is no agent for which some subset of the sinks is acceptable, and a policy that named a
    subset would be recording an exception nobody argued for. `__post_init__` asserts equality
    with `TRUSTED_SINKS` rather than containment, which means a sink added to the adapter package
    lands in every compiled policy automatically - and a test fails on any build that let a policy
    fall behind.

    What IS agent-specific is the second half:

    * `action_sinks` - one ``action.<name>.arguments`` position per declared action. These are the
      business-plane sinks AGENT_PLANE §7 property 4 is about, and they exist only because this
      agent declared those actions.
    * `requires_field_level_filter` - the adapters this agent may read whose worst declared trust
      is `untrusted`. Under a `trusted` read ceiling that is not a contradiction (the agent may
      still read the trusted fields), it is a *filter obligation*, and naming it is the difference
      between a ceiling that is enforced and one that is aspirational.

    `orchestrator_enforced` records what this package cannot check. `screen_sinks` inspects a
    `CapabilityRequest`, and no `CapabilityRequest` carries `action.arguments` or
    `prompt.instruction`; `taint.py` says so about itself and this policy repeats it rather than
    presenting a uniform guarantee that half of the positions do not have.
    """

    agent: str
    version: int
    read_ceiling: str
    forbidden_sinks: tuple[str, ...]
    adapter_enforced: tuple[str, ...]
    orchestrator_enforced: tuple[str, ...]
    action_sinks: tuple[str, ...]
    requires_field_level_filter: tuple[str, ...]
    reasoning: str

    def __post_init__(self) -> None:
        if tuple(self.forbidden_sinks) != tuple(TRUSTED_SINKS):
            raise AgentDefError(
                f"sink policy for {self.agent}@v{self.version} names {list(self.forbidden_sinks)} "
                f"but the trusted-sink inventory is {list(TRUSTED_SINKS)}. An untrusted value may "
                f"reach NO sink, so the forbidden set is the whole inventory by construction; a "
                f"policy that named a subset would be an exception nobody wrote down. If a sink "
                f"was added to databridge_adapter, every compiled policy must carry it")
        split = set(self.adapter_enforced) | set(self.orchestrator_enforced)
        if split != set(self.forbidden_sinks):
            raise AgentDefError(
                "the enforcement split must partition the forbidden set: a sink in neither half "
                "is one nobody enforces, and a sink in both is one two layers each assume the "
                "other checks")
        if set(self.adapter_enforced) & set(self.orchestrator_enforced):
            raise AgentDefError("a sink cannot be both adapter-enforced and orchestrator-enforced")

    @property
    def policy_id(self) -> str:
        """A content id, for the same reason the other three artefacts have one (High 11, R-013).

        `SinkPolicy` was the odd one out with no id of its own, which is how a trust flip that
        changed nothing about `forbidden_sinks` (always the whole inventory) could leave
        `CompiledAgent.compiled_id` unable to see that the policy underneath had changed.
        `reasoning` is excluded on purpose: it is prose derived from the fields below, not a fact
        an identity needs to track separately from them.
        """
        return _digest({
            "agent": self.agent, "version": self.version, "read_ceiling": self.read_ceiling,
            "forbidden_sinks": list(self.forbidden_sinks),
            "adapter_enforced": list(self.adapter_enforced),
            "orchestrator_enforced": list(self.orchestrator_enforced),
            "action_sinks": list(self.action_sinks),
            "requires_field_level_filter": list(self.requires_field_level_filter),
        }, "sp")

    def as_record(self) -> dict:
        return {"policy_id": self.policy_id, "agent": self.agent, "version": self.version,
                "read_ceiling": self.read_ceiling,
                "forbidden_sinks": list(self.forbidden_sinks),
                "adapter_enforced": list(self.adapter_enforced),
                "orchestrator_enforced": list(self.orchestrator_enforced),
                "action_sinks": list(self.action_sinks),
                "requires_field_level_filter": list(self.requires_field_level_filter),
                "reasoning": self.reasoning}


def build_sink_policy(*, agent: str, version: int, read_ceiling: str,
                      action_names: Sequence[str],
                      untrusted_capable_sources: Sequence[str]) -> SinkPolicy:
    """Derive the sink policy. Pure; the only agent-specific inputs are the last two."""
    adapter_enforced = tuple(s for s in TRUSTED_SINKS if s in ADAPTER_SINKS)
    orchestrator_enforced = tuple(s for s in TRUSTED_SINKS if s not in ADAPTER_SINKS)
    action_sinks = tuple(f"action.{name}.arguments" for name in sorted(set(action_names)))
    filters = tuple(sorted(set(untrusted_capable_sources)))
    ceiling_note = (
        "The read ceiling is `untrusted`: this agent MAY receive untrusted cells, so every value "
        "it holds is a candidate for the taint map and none of them may reach a sink."
        if read_ceiling == "untrusted" else
        "The read ceiling is `trusted`: this agent may not act on untrusted cells at all. That is "
        "a narrower ceiling, not a licence - trust is per field, so the ceiling is only real if "
        "the untrusted fields of the adapters below are filtered at resolution.")
    filter_note = (
        f" Adapters whose worst declared trust is `untrusted`, and which therefore need a "
        f"field-level filter under this ceiling: {list(filters)}." if filters else
        " Every adapter this agent may read declares `trusted` as its worst field.")
    return SinkPolicy(
        agent=agent, version=version, read_ceiling=read_ceiling,
        forbidden_sinks=tuple(TRUSTED_SINKS),
        adapter_enforced=adapter_enforced, orchestrator_enforced=orchestrator_enforced,
        action_sinks=action_sinks, requires_field_level_filter=filters,
        reasoning=(
            f"{ceiling_note}{filter_note} "
            f"{len(adapter_enforced)} of {len(TRUSTED_SINKS)} positions are visible on a "
            f"CapabilityRequest and are screened by databridge_adapter.screen_sinks; "
            f"{list(orchestrator_enforced)} are business-plane positions that no "
            f"CapabilityRequest carries, so the orchestrator enforces them or nothing does. "
            f"Per-action argument positions for this agent: {list(action_sinks) or 'none'}."))


# --------------------------------------------------------------------------------------
# 3. the budget admission record
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class BudgetAdmission:
    """The three ceilings, admitted. Enforced by the harness, not by convention (§3).

    `enforced_by` is recorded rather than assumed because this package does not enforce anything:
    it produces the record the harness must read. Saying so on the artefact is the difference
    between a budget and a comment.
    """

    agent: str
    version: int
    tokens_per_run: int
    escalations_per_day: int
    cost_ceiling_usd_per_day: float
    enforced_by: str = "databridge_harness"

    def __post_init__(self) -> None:
        if self.tokens_per_run < 1 or self.escalations_per_day < 1:
            raise AgentDefError(
                f"budget admission for {self.agent}@v{self.version} carries a non-positive "
                f"ceiling. A ceiling of zero is a disabled agent, and a disabled agent should be "
                f"an absent declaration so the record says so")
        if not (self.cost_ceiling_usd_per_day > 0):
            raise AgentDefError(
                f"budget admission for {self.agent}@v{self.version} has a non-positive daily cost "
                f"ceiling")

    @property
    def admission_id(self) -> str:
        return _digest({"agent": self.agent, "version": self.version,
                        "tokens_per_run": self.tokens_per_run,
                        "escalations_per_day": self.escalations_per_day,
                        "cost_ceiling_usd_per_day": self.cost_ceiling_usd_per_day}, "ba")

    def as_record(self) -> dict:
        return {"admission_id": self.admission_id, "agent": self.agent, "version": self.version,
                "tokens_per_run": self.tokens_per_run,
                "escalations_per_day": self.escalations_per_day,
                "cost_ceiling_usd_per_day": self.cost_ceiling_usd_per_day,
                "enforced_by": self.enforced_by}


# --------------------------------------------------------------------------------------
# 4. the eval binding
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class EvalBinding:
    """The golden set that must pass before the agent may run, pinned by digest.

    Pinned, because "the evals passed" is a claim about a *specific* set of examples. A golden set
    that can be edited after the binding was made is a gate that can be walked around by deleting
    the cases that fail, and nobody would see it in the record.

    The same digest appears on the feasibility report, which is the point: the base risk the
    abstention budget was certified against was measured on the set this binding names, and the
    compiler refuses the pair when the two digests disagree.
    """

    agent: str
    version: int
    golden_set: str
    golden_set_digest: str
    records: int
    gates: tuple[tuple[str, float], ...]
    feasibility: Optional[FeasibilityReport]

    def __post_init__(self) -> None:
        if self.records < 1:
            raise AgentDefError(
                f"eval binding for {self.agent}@v{self.version} points at a golden set with no "
                f"records. An empty golden set passes every gate, which is worse than no gate: it "
                f"reads as evidence")
        if not self.gates:
            raise AgentDefError(
                f"eval binding for {self.agent}@v{self.version} declares no gates. A golden set "
                f"with no threshold is a file, not a gate")

    @property
    def gate_map(self) -> Mapping[str, float]:
        return dict(self.gates)

    @property
    def binding_id(self) -> str:
        return _digest({"agent": self.agent, "version": self.version,
                        "golden_set": self.golden_set,
                        "golden_set_digest": self.golden_set_digest,
                        "records": self.records, "gates": [list(g) for g in self.gates]}, "eb")

    def check(self, results: Mapping[str, float]) -> tuple[bool, tuple[str, ...]]:
        """Whether an eval run clears every gate, and the human-readable reasons it did not.

        A gate with **no reported result fails**. An eval suite that silently stopped emitting a
        metric would otherwise turn a gate off by omission, which is the same failure as an empty
        golden set arriving one step later.

        A gate with a **non-finite reported result also fails** (finding 4, R-013). `NaN >=
        threshold` and `NaN < threshold` are BOTH `False` in IEEE 754 - whichever way this method
        wrote the comparison, a `NaN` result would clear the gate by comparing false in the
        failing direction. A boolean or an out-of-range score is rejected the same way: `results`
        is a mapping an eval RUN supplies, not something this method's own input validation
        already covers, so it is untrusted in the same sense a request body is.
        """
        failures: list[str] = []
        for name, threshold in self.gates:
            if name not in results:
                failures.append(
                    f"gate {name!r} requires >= {threshold:.4g} and the run reported no value for "
                    f"it. A missing metric is a failed gate: otherwise a suite that stopped "
                    f"emitting one would switch the gate off without saying so")
                continue
            got = results[name]
            if isinstance(got, bool) or not isinstance(got, (int, float)) or not math.isfinite(
                    got):
                failures.append(
                    f"gate {name!r} reported {got!r}, which is not a finite score. NaN and +/-inf "
                    f"compare false against a threshold in BOTH directions, so a malformed result "
                    f"must be refused explicitly rather than compared - a silent pass here would "
                    f"be the gate clearing itself")
                continue
            got = float(got)
            if not (0.0 <= got <= 1.0):
                failures.append(
                    f"gate {name!r} reported {got:.4g}, outside the [0, 1] range every gate here "
                    f"scores in")
                continue
            if got < threshold:
                failures.append(f"gate {name!r} requires >= {threshold:.4g}, run scored "
                                f"{got:.4g} on {self.golden_set} ({self.records} records)")
        return (not failures, tuple(failures))

    def as_record(self) -> dict:
        return {"binding_id": self.binding_id, "agent": self.agent, "version": self.version,
                "golden_set": self.golden_set, "golden_set_digest": self.golden_set_digest,
                "records": self.records, "gates": [list(g) for g in self.gates],
                "feasibility": (self.feasibility.as_record() if self.feasibility else None)}


# --------------------------------------------------------------------------------------
# the four, together
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class CompiledAgent:
    """A declaration that produced all four artefacts. There is no partial form of this object.

    Construction refuses a missing artefact and refuses artefacts belonging to another agent
    version. That is deliberate belt-and-braces beside `compiler.compile_agent`: the compiler is
    the only thing that builds one, but a future caller assembling one by hand must hit the same
    wall, or "compiled" stops meaning what the type says it means.

    ``citation`` and ``actions`` (finding 3, finding 6, R-013) are the declaration's evidence rule
    and its per-action enforcement metadata, carried forward rather than dropped at the door. A
    citation rule the compiler CHECKED but did not RECORD is a claim nothing downstream can hold
    the agent to at runtime; an `automation.mode`/`touches_subject`/`reversal` the compiler
    ENFORCED but did not RECORD is exactly the gap D12's future commit-path enforcement would find
    nothing to check against - the compiler would have "verified" a rule it then declined to write
    down.

    ``mint`` (finding 1, R-013) is the compiler-only construction token - see `_COMPILER_MINT`'s
    docstring. It is excluded from equality/repr because it is not content: every legitimately
    compiled agent carries the same sentinel, and two decisions that otherwise agree must still
    compare equal.
    """

    agent: str
    version: int
    pack: str
    purpose: str
    grants: CapabilityGrantSet
    sink_policy: SinkPolicy
    budget: BudgetAdmission
    evals: EvalBinding
    citation: str
    actions: tuple[ActionDecl, ...]
    declared_in: str
    declaration_digest: str
    compiled_at: datetime
    mint: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        missing = [name for name, value in (("capability grant set", self.grants),
                                            ("sink policy", self.sink_policy),
                                            ("budget admission record", self.budget),
                                            ("eval binding", self.evals)) if value is None]
        if missing:
            raise AgentDefError(
                f"{self.agent}@v{self.version} cannot be compiled: {missing} could not be "
                f"produced. Compilation produces four artefacts and refuses if any cannot be "
                f"produced (AGENT_PLANE §3). There is no partially-compiled agent, because a "
                f"partially-compiled agent is one a later code path finds and runs")
        for name, obj in (("grants", self.grants), ("sink_policy", self.sink_policy),
                          ("budget", self.budget), ("evals", self.evals)):
            if obj.agent != self.agent or obj.version != self.version:
                raise AgentDefError(
                    f"{name} belongs to {obj.agent}@v{obj.version}, not {self.agent}@"
                    f"v{self.version}. Four artefacts from two declarations is not a compilation")
        if self.mint is not _COMPILER_MINT:
            # Finding 1 (R-013). Checked LAST, after the two structural checks above, so a test
            # exercising "an artefact is missing" or "the artefacts disagree on agent/version"
            # still reports THAT reason rather than being masked by this one.
            raise AgentDefError(
                f"{self.agent}@v{self.version} was not constructed by "
                f"databridge_agentdef.compiler.compile_agent. There is no public path that builds "
                f"a CompiledAgent directly - assembling one from the four artefacts by hand, even "
                f"from genuinely valid ones, is exactly the compiler-refusal bypass this check "
                f"exists to close")

    @property
    def ref(self) -> str:
        return f"{self.pack}/{self.agent}@v{self.version}"

    @property
    def compiled_id(self) -> str:
        # High 11 (R-013): now covers every artefact whose content can change independently of
        # the others - including the sink policy (via its own `policy_id`) and the citation rule
        # and action metadata this class carries alongside the four artefacts - so two compiles
        # that differ in any of them no longer collide under one id.
        return _digest({"agent": self.agent, "version": self.version, "pack": self.pack,
                        "purpose": self.purpose, "citation": self.citation,
                        "grants": [g.grant_id for g in self.grants],
                        "sink_policy": self.sink_policy.policy_id,
                        "budget": self.budget.admission_id,
                        "evals": self.evals.binding_id,
                        "actions": [a.as_record() for a in self.actions],
                        "declaration_digest": self.declaration_digest}, "ca")

    def action(self, name: str) -> ActionDecl:
        """The compiled record for one declared action - what D12's commit path would enforce."""
        for a in self.actions:
            if a.name == name:
                return a
        raise KeyError(f"agent {self.ref} declares no action {name!r}")

    def as_record(self) -> dict:
        return {"compiled_id": self.compiled_id, "ref": self.ref, "agent": self.agent,
                "version": self.version, "pack": self.pack, "purpose": self.purpose,
                "citation": self.citation,
                "declared_in": self.declared_in,
                "declaration_digest": self.declaration_digest,
                "compiled_at": self.compiled_at.isoformat(),
                "grants": self.grants.as_record(),
                "sink_policy": self.sink_policy.as_record(),
                "budget": self.budget.as_record(),
                "evals": self.evals.as_record(),
                "actions": [a.as_record() for a in self.actions]}

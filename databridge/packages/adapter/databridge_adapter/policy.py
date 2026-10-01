"""The Policy→Core decision surface — **a named stub** (`ADAPTER_AGENT_SPEC` Appendix A).

Appendix A lists two things this module would need and neither exists:

> **Policy→Core authorisation protocol** — *needed before the first adapter* — "a caller must not
> be able to invoke Core directly and skip Policy; today this is stated, not enforced."

and, from §7's worked example, the compile-down protocol:

> "Row-level decisions need raw rows, which never leave Core — so Policy compiles a decision table
> for this (principal, purpose, projection) and hands it down at admission; Core evaluates it
> locally and cannot widen it. That compile-down protocol is undefined (Appendix A) and is a
> Stage-1 blocker."

What is defined here is only the part that is genuinely settled: the SHAPE of a per-cell decision,
because `Withheld` already needs `policy_id` and `reason_code` and those cannot be invented later
without moving stored records. Everything else is `NotYetSpecifiedDecisionTable`, which raises.

**This module is not load-bearing and a test asserts it.** The stub exists so that a reader who
goes looking for the enforcement path finds a refusal that names the missing design, rather than
finding nothing and assuming the path is elsewhere. Workstream 3C builds the real one.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, model_validator

from databridge_core import WithholdingRecord

from .contract import Principal


class PolicyDecision(BaseModel):
    """One cell-level decision. Deny carries its own attribution or it is not a decision.

    ``policy_id`` and ``reason_code`` are mandatory on a denial because `WithholdingRecord`
    requires them, and it requires them because a denial nobody can attribute to a policy and a
    principal is not an audit record - it is just a missing value with extra steps.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    withheld: bool
    policy_id: Optional[str] = None
    reason_code: Optional[str] = None
    #: Could a DIFFERENT principal or purpose see this? What lets a UI say "ask the data owner"
    #: instead of "nobody can see this".
    appealable: Optional[bool] = None

    #: A deterministic transformation the Core must apply on DISCLOSURE, named as a registered
    #: derivation rule (`derivations.py`). ``None`` discloses verbatim.
    #:
    #: This is a PERMIT that narrows, not a denial: the value reaches the caller, but as a value
    #: the source did not literally hold - so §4a applies in full and the Core emits it under a
    #: `derived` pointer whose derivation replays to exactly this transformation. The raw cell
    #: stays cited and stays resolvable by a principal entitled to see it.
    #:
    #: Distinct from a MASK, which the estate declares as `cell_type: Withheld` and which
    #: therefore produces a denial rather than a narrowed value.
    transform: Optional[str] = None

    @model_validator(mode="after")
    def _denial_is_attributable(self) -> "PolicyDecision":
        if self.withheld and not ((self.policy_id or "").strip()
                                  and (self.reason_code or "").strip()):
            raise ValueError(
                "a withholding decision must name the policy that made it and a machine-readable "
                "reason code from that policy's vocabulary")
        if not self.withheld and (self.policy_id or self.reason_code):
            raise ValueError(
                "a permit decision carries no policy_id/reason_code: those fields exist to "
                "attribute a DENIAL, and populating them on a permit would put a denial reason "
                "beside a disclosed value")
        if self.withheld and self.transform:
            raise ValueError(
                "a withholding decision carries no transform: nothing is disclosed, so there is "
                "nothing to transform, and a transformation named beside a denial reads as a "
                "partial disclosure that did not happen")
        return self

    def to_record(self, *, field_path: str, principal: Principal,
                  policy_version: Optional[str] = None) -> WithholdingRecord:
        """Turn a denial into the record a `Withheld` cell carries."""
        if not self.withheld:
            raise ValueError("only a withholding decision produces a WithholdingRecord")
        return WithholdingRecord(
            field_path=field_path,
            policy_id=str(self.policy_id),
            reason_code=str(self.reason_code),
            principal=principal.actor,
            policy_version=policy_version,
            purpose=principal.purpose,
            appealable=self.appealable,
        )


@runtime_checkable
class DecisionTable(Protocol):
    """What Policy compiles down and hands to Core at admission.

    The shape is from §7: ``decide(entity, column, row, principal)``. Row-level policy needs raw
    rows, and raw rows never leave Core - so the decision function goes down to the data rather
    than the data coming up to the decision. Core evaluates it locally and **cannot widen it**;
    how that non-widening is enforced across a process boundary is the undefined part.
    """

    def decide(self, entity: str, column: str, row: Mapping[str, Any],
               principal: Principal) -> PolicyDecision:
        ...


class NotYetSpecifiedDecisionTable:
    """The honest placeholder. Every call raises.

    Deliberately not a permissive default. A `DecisionTable` that answered "not withheld" would be
    a policy engine that permits everything, wearing the name of one that does not - and it would
    pass every test written against the happy path.
    """

    #: Named so a reader grepping for the blocker finds it.
    BLOCKER = "ADAPTER_AGENT_SPEC Appendix A: Policy->Core authorisation + compile-down protocol"

    def decide(self, entity: str, column: str, row: Mapping[str, Any],
               principal: Principal) -> PolicyDecision:
        raise NotImplementedError(
            f"{self.BLOCKER}. Phase 3A defines the CONTRACT only; the policy layer is workstream "
            f"3C. There is deliberately no permissive default: a decision table that answered "
            f"'not withheld' would be a policy engine that permits everything wearing the name of "
            f"one that does not, and it would pass every happy-path test ever written against it. "
            f"(asked about {entity}.{column} for principal {principal.actor!r})")


# =======================================================================================
# Workstream 3C — the real Policy layer.
#
# The stub above is kept, and kept raising, because it names a hole that is only PARTLY
# filled: what follows is the in-process compile-down protocol, not the cross-process
# authorisation Appendix A also lists. Deleting the stub would erase the record of which
# half is still missing.
#
# Five screens, in this order, all deterministic and none of them reachable by a model:
#
#   1. CAPABILITY — is this in the adapter's advertised closed set? (§10: reject, log, and
#      never escalate to a model to reconsider.)
#   2. GRANT — a subject-scoped purpose without a grant is refused, never defaulted (§1);
#      an expired role is refused on the day it expires.
#   3. PURPOSE — the principal's pack purpose is bound to an estate purpose, and at least
#      one of the principal's roles may act under it. Holding data for one purpose is not
#      permission to use it for another.
#   4. BUDGET — admission control refuses BEFORE the call. After the call the cost is
#      incurred and, for an egress-classed source, the rows have already left.
#   5. EGRESS — the source's class against the role's ceiling.
#
#   ...and then the SINK SCREEN, because an untrusted value that has reached one of the
#   request's control-flow positions is a bug in the caller and must not be executed.
#
# What comes out is an `Admission` carrying a `CompiledDecisionTable`: the per-column
# decisions and per-row rules for exactly this (principal, purpose, projection). Row-level
# decisions need raw rows, raw rows never leave Core, so the DECISION goes down to the data
# instead of the data coming up to the decision.
# =======================================================================================

from datetime import date, datetime, timezone
from types import MappingProxyType
from typing import Optional as _Optional, Sequence

from .canonical import canonical_json, sha256_of
from .contract import CapabilityRequest, Entitlement
from .describe import SourceDescription
from .errors import (
    CapabilityNotAdvertised,
    EgressCeilingExceeded,
    PolicyNotApplied,
    PurposeNotPermitted,
    RowRuleNotCompilable,
)
from .results import Budget, Cost
from .rolemodel import (
    EGRESS_RANK,
    PurposeBinding,
    Role,
    RoleModel,
    RowRule,
    applicable_roles,
)
from .taint import RegistryTaintMap, TaintMap, require_trusted_scope_column, screen_sinks


class ColumnRule(BaseModel):
    """The compiled decision for ONE column of ONE projection, for one principal.

    Compiled once at admission rather than decided per cell, because a column-level decision
    cannot depend on the row: whether `party.notes` is denied to a credit analyst is a fact about
    the analyst and the column. Only the ROW rules need the raw row, and they are separate.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    column: str
    withheld: bool
    policy_id: _Optional[str] = None
    reason_code: _Optional[str] = None
    appealable: _Optional[bool] = None
    transform: _Optional[str] = None
    #: Which role's rule decided. Recorded so a denial can be traced to a line in `roles.yaml`
    #: rather than to "the policy engine".
    decided_by: str = ""

    def as_decision(self) -> PolicyDecision:
        return PolicyDecision(withheld=self.withheld, policy_id=self.policy_id,
                              reason_code=self.reason_code, appealable=self.appealable,
                              transform=self.transform)


class CompiledDecisionTable(BaseModel):
    """What Policy hands Core at admission (§7). Frozen, digested, and impossible to widen.

    > "Row-level decisions need raw rows, which never leave Core — so Policy compiles a decision
    > table for this (principal, purpose, projection) and hands it down at admission; Core
    > evaluates it locally and cannot widen it."

    **How "cannot widen" is actually enforced here, precisely.** The table is a frozen Pydantic
    model whose only decision surfaces are `decide` and `row_verdict`; there is no method that
    grants anything, and every path through both returns either a compiled `ColumnRule` or a
    denial. Its `digest` covers every rule, the `Admission` carries that digest independently, and
    the Core re-checks the two agree before it emits a cell — so swapping the table for a wider
    one between admission and emission is caught rather than trusted.

    That is a real in-process guarantee and it is not the whole of Appendix A's item: across a
    process boundary the table would have to be signed, and that protocol is still undefined.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    source_id: str
    entity: str
    purpose: str
    estate_purpose: str
    roles: tuple[str, ...]
    columns: tuple[ColumnRule, ...]
    #: The row-level rules the Core evaluates locally against raw rows. Compiled, never raw text:
    #: the Core renders each through the driver's own parameter binding and can run nothing else.
    rules: tuple[RowRule, ...] = ()
    policy_id: str = ""
    policy_version: str = ""

    @model_validator(mode="after")
    def _one_rule_per_column(self) -> "CompiledDecisionTable":
        names = [c.column for c in self.columns]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(
                f"compiled table has two rules for column(s) {dupes}: a cell could then be both "
                f"disclosed and withheld and the answer would depend on iteration order")
        return self

    @property
    def digest(self) -> str:
        """A content address for the whole table. What the `Admission` commits to."""
        return sha256_of(canonical_json({
            "source_id": self.source_id, "entity": self.entity, "purpose": self.purpose,
            "estate_purpose": self.estate_purpose, "roles": list(self.roles),
            "policy_id": self.policy_id, "policy_version": self.policy_version,
            "row_rules": sorted(f"{r.rule_id}: {r.describe()}" for r in self.rules),
            "columns": [
                {"column": c.column, "withheld": c.withheld, "policy_id": c.policy_id,
                 "reason_code": c.reason_code, "appealable": c.appealable,
                 "transform": c.transform, "decided_by": c.decided_by}
                for c in sorted(self.columns, key=lambda c: c.column)],
        }))

    def rule_for(self, column: str) -> ColumnRule:
        for rule in self.columns:
            if rule.column == column:
                return rule
        # A column with no compiled rule is not a permit. The table was compiled for exactly one
        # projection; a request for a column outside it means the Core is reading something the
        # admission never covered.
        raise PolicyNotApplied(
            f"no compiled decision for {self.entity}.{column}: this table covers "
            f"{[c.column for c in self.columns]}. A column the admission never saw has not been "
            f"permitted — it has been missed, and defaulting it to disclosed is how a policy "
            f"engine quietly stops applying")

    def decide(self, entity: str, column: str, row: Mapping[str, Any],
               principal: Principal) -> PolicyDecision:
        """The `DecisionTable` protocol. ``row`` is accepted and unused for COLUMN decisions.

        It is in the signature because §7 puts it there and because row-scoped column rules (mask
        this column only for rows the principal does not own) are the obvious next extension. It
        is deliberately not consulted today, so that a reader cannot conclude from a passing test
        that per-row column policy is implemented.
        """
        if entity != self.entity:
            raise PolicyNotApplied(
                f"this table was compiled for entity {self.entity!r} and was asked about "
                f"{entity!r}: a decision made for one entity is not a decision about another")
        return self.rule_for(column).as_decision()

    # --- row-level ---
    def row_verdict(self, row: Mapping[str, Any], *, attributes: Mapping[str, str],
                    subquery_sets: Mapping[str, frozenset]) -> _Optional[PolicyDecision]:
        """``None`` when the row is visible; a denial naming the rule when it is not.

        Evaluated in the Core against the raw row, which is the only place the raw row exists.
        An uncompilable rule never reaches here — admission refuses first — so there is no branch
        that treats "we could not compile this" as "no restriction".
        """
        for rule in self.rules:
            if not self._row_passes(rule, row, attributes, subquery_sets):
                return PolicyDecision(
                    withheld=True, policy_id=rule.rule_id, reason_code="ROW_NOT_IN_SCOPE",
                    appealable=True)
        return None

    @staticmethod
    def _row_passes(rule: RowRule, row: Mapping[str, Any], attributes: Mapping[str, str],
                    subquery_sets: Mapping[str, frozenset]) -> bool:
        if rule.kind == "eq":
            expected = attributes.get(str(rule.principal_attr))
            if expected is None:
                # A principal attribute the directory did not supply. Fail CLOSED: an unbound
                # `:principal.rm_code` compared against every row would match none or all
                # depending on the comparison, and "all" is the direction that leaks.
                return False
            return row.get(str(rule.column)) == expected
        if rule.kind == "in_subquery":
            allowed = subquery_sets.get(rule.rule_id)
            if allowed is None:
                return False
            return row.get(str(rule.column)) in allowed
        return False                                             # pragma: no cover - refused above


class PrincipalBinding(BaseModel):
    """What the identity provider says about a principal: its roles and its attributes.

    Kept OUT of `Principal` on purpose. `Principal` is what the orchestrator sends and what a
    pointer's audit trail records; roles and attributes are what the deployment's directory
    asserts about that principal at this instant, and they change without the principal changing
    (a role is removed, an `rm_code` is reassigned). Mixing them would let a caller name its own
    entitlements, which is the shape of every privilege-escalation bug.

    ``attributes`` are the `:principal.<attr>` bindings a row filter needs — `rm_code`, `team`,
    `email`. They are pairs rather than a mapping so the binding is hashable and frozen.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    roles: tuple[str, ...]
    attributes: tuple[tuple[str, str], ...] = ()
    #: The ONE role context this principal is acting under. `roles.yaml` v2 is explicit:
    #: "A request activates EXACTLY ONE role context... There is no implicit union, no 'best
    #: matching role', and no fallback to a second role when the first refuses." It is also
    #: explicit that the context is "chosen by the CALLER and validated, never inferred from what
    #: would return the most data" — so leaving it `None` is legal only when the principal holds
    #: exactly one role, and anything else is `POLICY_NO_ACTIVE_CONTEXT`.
    #:
    #: It lives here rather than on `CapabilityRequest` because `CapabilityRequest` has no field
    #: for it and adding one would put the role inside `read_hash`, which is principal-independent
    #: by design. That is a real gap between the contract and this policy model, recorded rather
    #: than papered over.
    active_role: Optional[str] = None

    @model_validator(mode="after")
    def _check(self) -> "PrincipalBinding":
        if self.active_role is not None and self.active_role not in self.roles:
            raise ValueError(
                f"active_role {self.active_role!r} is not among the roles this principal holds "
                f"{self.roles}: a context nobody granted is not a context")
        if not self.roles:
            raise ValueError(
                "a principal binding with no roles grants nothing and must say so by being "
                "absent: an empty role tuple reads as 'we looked it up and it has none', which "
                "is indistinguishable from 'the directory was down'")
        names = [k for k, _ in self.attributes]
        if len(set(names)) != len(names):
            raise ValueError("duplicate principal attribute binding")
        return self

    @property
    def attribute_map(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self.attributes))

    @property
    def digest(self) -> str:
        return sha256_of(canonical_json({"roles": sorted(self.roles),
                                         "attributes": dict(self.attributes)}))


class Admission(BaseModel):
    """Policy's answer: this read may proceed, under exactly these decisions.

    Constructed only by `PolicyEngine.admit`. The Core takes no admission from a caller — it asks
    its own engine — so there is no argument through which a wider table could be supplied.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    read_hash: str
    source_id: str
    decisions: CompiledDecisionTable
    #: Committed independently of `decisions` so the Core can detect a swapped table. See
    #: `CompiledDecisionTable` on what this does and does not prove.
    decisions_digest: str
    entitlement: Entitlement
    budget: Budget
    egress_class: str
    roles: tuple[str, ...]
    attributes: tuple[tuple[str, str], ...] = ()
    bundle_version: str = ""
    #: Non-fatal, declared consequences of the policy — e.g. that a row rule narrows the read.
    #: Surfaced on the result so a filtered answer is never silently partial.
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _committed(self) -> "Admission":
        if self.decisions_digest != self.decisions.digest:
            raise PolicyNotApplied(
                f"admission commits to table {self.decisions_digest[:12]}… but carries "
                f"{self.decisions.digest[:12]}…: the decisions were changed after they were "
                f"admitted")
        return self

    @property
    def attribute_map(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self.attributes))

    def check(self) -> "Admission":
        """Re-verify the commitment. The Core calls this before emitting a single cell."""
        if self.decisions_digest != self.decisions.digest:            # pragma: no cover - frozen
            raise PolicyNotApplied("the compiled decision table no longer matches its admission")
        return self


class PolicyEngine:
    """Deterministic. No LLM, ever. Holds no driver handle and no credentials.

    It holds the estate's role model, the pack→estate purpose binding, and the deployment's
    principal directory. Everything it decides is a pure function of those three plus the request,
    which is what makes a denial reproducible eighteen months later from the same inputs.
    """

    #: Reason codes. A closed vocabulary, because `WithholdingRecord.reason_code` is described as
    #: coming from "that policy's vocabulary" and a free-text reason cannot be aggregated,
    #: appealed against, or counted.
    REASONS = MappingProxyType({
        "TABLE_NOT_GRANTED": "the role's allow-list does not include this entity",
        "TABLE_DENIED": "the role denies this entity explicitly",
        "SOURCE_DENIED": "the role denies this source explicitly",
        "COLUMN_DENIED": "the role denies this column explicitly",
        "METADATA_ONLY": "the role may see schema, counts and digests, never a value",
        "POINTERS_ONLY": "the role may see citations, never the values they cite",
        "ROW_NOT_IN_SCOPE": "the row is outside the role's row-level scope",
    })

    def __init__(self, *, role_model: RoleModel, purpose_binding: PurposeBinding,
                 directory: Mapping[str, PrincipalBinding],
                 default_budget: _Optional[Budget] = None,
                 taint: _Optional[TaintMap] = None,
                 today: _Optional[date] = None) -> None:
        self._roles = role_model
        self._binding = purpose_binding
        self._directory = MappingProxyType(dict(directory))
        self._default_budget = default_budget or Budget(max_rows=10_000)
        self._taint = taint if taint is not None else RegistryTaintMap()
        self._today = today
        self._free_text_exceptions = role_model.free_text_exceptions
        if purpose_binding.estate != role_model.estate:
            raise PolicyNotApplied(
                f"purpose bindings are for estate {purpose_binding.estate!r} and the role model "
                f"is {role_model.estate!r}: binding one estate's purposes onto another's roles "
                f"would grant access under a lawful basis that estate never declared")

    # --- reading ---
    @property
    def role_model(self) -> RoleModel:
        return self._roles

    @property
    def taint(self) -> TaintMap:
        return self._taint

    @property
    def bundle_version(self) -> str:
        return self._roles.bundle_version

    def binding_for(self, principal: Principal) -> PrincipalBinding:
        """The directory entry for a principal, keyed by the human if there is one.

        A principal the directory does not know is REFUSED, not treated as role-less. "Unknown"
        and "no roles" look identical downstream and only one of them is safe to guess at.
        """
        for key in (principal.on_behalf_of, principal.actor):
            if key and key in self._directory:
                return self._directory[key]
        raise PurposeNotPermitted(
            f"no directory entry for principal (on_behalf_of={principal.on_behalf_of!r}, "
            f"actor={principal.actor!r}). An unknown principal is refused rather than treated as "
            f"role-less: 'we could not look them up' and 'they hold no roles' are different "
            f"facts, and only one of them is safe to act on")

    # --- the screens, in the estate's own declared order --------------------------------
    #
    # `roles.yaml` v2 ships an `evaluation_order` block with nine numbered stages and an
    # `on_failure` verdict and reason code for each. That file is the policy authority, so the
    # order below is ITS order rather than a reading of the spec, and each stage names the code it
    # emits. Where the two disagree the file wins - it is the thing a data owner reviews.
    #
    #   1 role context      2 principal attributes   3 purpose        4 expiry
    #   5 deny              6 object                 7 egress         8 row scope
    #   9 column projection
    #
    # Stages 1-8 fail at REQUEST level (`result_model.dispositions` says FORBIDDEN, and "an empty
    # set is a claim about the data"). Stage 9 fails per CELL, as a `Withheld`.

    def admit(self, req: CapabilityRequest, *, description: SourceDescription,
              budget: _Optional[Budget] = None) -> Admission:
        """Run every stage and compile the decision table, or refuse with a named error."""
        source_id, entity = req.source_id, req.projection.entity
        binding = self.binding_for(req.principal)

        # 0. CAPABILITY. Not one of the nine - it is the port's own screen (§10), and it runs
        #    first because an unadvertised capability is not a policy question at all.
        if req.capability not in description.capabilities:
            raise CapabilityNotAdvertised(
                f"source {description.source_id!r} does not advertise capability "
                f"{req.capability!r}; advertised: {sorted(description.capabilities)}. The "
                f"rejection is final - a capability screen that feeds its refusal back to a model "
                f"until something passes is a screen that eventually passes anything (§10)")
        if source_id != description.source_id:
            raise PolicyNotApplied(
                f"request targets source {source_id!r} and the description is for "
                f"{description.source_id!r}: one request reaches one adapter")

        # 1. ROLE CONTEXT - exactly one. POLICY_NO_ACTIVE_CONTEXT.
        role = self._active_role(binding)
        held = [self._roles.require_role(name) for name in binding.roles]

        # 2. PRINCIPAL ATTRIBUTES - bound from the credential, never from the request.
        #    "A request that CARRIES a `:principal.*` value is FORBIDDEN... rather than having it
        #    ignored - ignoring it makes an attempted escalation invisible."
        smuggled = sorted(name for name, _ in req.params if name.startswith("principal"))
        if smuggled:
            raise PurposeNotPermitted(
                f"POLICY_UNATTESTED_PRINCIPAL_ATTRIBUTE: the request carries {smuggled}. Every "
                f"`:principal.*` value is bound from the authenticated workforce identity, never "
                f"from a request field a calling process controls. Refused rather than ignored - "
                f"ignoring it would make an attempted escalation invisible")

        # 3. PURPOSE - against the role's list AND against `purpose_scoped_grants` for THIS
        #    object. POLICY_PURPOSE_MISMATCH.
        estate_purpose = self._binding.require(req.principal.purpose)
        if estate_purpose not in role.purposes:
            raise PurposeNotPermitted(
                f"POLICY_PURPOSE_MISMATCH: role {role.name!r} may act under "
                f"{sorted(role.purposes)}, not {estate_purpose!r} (pack purpose "
                f"{req.principal.purpose!r}). Holding this data for one purpose is not permission "
                f"to use it for another (D5)")
        if not role.purpose_reaches(estate_purpose, source_id, entity):
            reachable = sorted(role.purpose_grants.get(estate_purpose, ()))
            raise PurposeNotPermitted(
                f"POLICY_PURPOSE_MISMATCH: role {role.name!r} under purpose {estate_purpose!r} "
                f"reaches {reachable or '(nothing)'}, not {source_id}.{entity}. The role-level "
                f"`purposes` list is a union, not a grant - `purpose_scoped_grants` is what a "
                f"purpose actually reaches, and absence there is a refusal, not a wildcard")

        # 4. EXPIRY. POLICY_GRANT_EXPIRED.
        today = self._today or datetime.now(timezone.utc).date()
        if role.is_expired(today):
            raise PurposeNotPermitted(
                f"POLICY_GRANT_EXPIRED: role {role.name!r} expired on "
                f"{role.expires_on.isoformat()} and today is {today.isoformat()}. An expiry that "
                f"is not enforced on the day is a grant with a comment on it")

        # 5. DENY - source, then table. "DENY WINS, always, over every allow and every mask, in
        #    this role and in EVERY OTHER ROLE the principal holds." So this reads `held`, not
        #    `role`: it is the one place roles combine, and they combine only restrictively.
        path = f"{source_id}.{entity}"
        for other in held:
            for denied in other.deny_sources:
                if denied == source_id or denied == path or path.startswith(f"{denied}."):
                    raise PurposeNotPermitted(
                        f"POLICY_SOURCE_DENIED: role {other.name!r} denies {denied!r}. Deny beats "
                        f"allow unconditionally, in the active context and in every other role "
                        f"the principal holds")
            if path in other.deny_tables:
                raise PurposeNotPermitted(
                    f"POLICY_TABLE_DENIED: role {other.name!r} denies {path!r}")

        # 6. OBJECT - inside the ACTIVE role's allow list. POLICY_ROLE_SCOPE.
        if not role.grants_entity(source_id, entity):
            raise PurposeNotPermitted(
                f"POLICY_ROLE_SCOPE: {path} is outside role {role.name!r}'s allow list")

        # 7. EGRESS - the OBJECT's class against the role's ceiling. POLICY_EGRESS_CEILING.
        #    Deliberately not softened to "only when a value would be disclosed": the estate puts
        #    this at stage 7, before the column projection at stage 9, and an object-level ceiling
        #    that a sufficiently redacted projection could slip past is not a ceiling.
        #
        #    **A KNOWN GAP, failed closed rather than worked around.** `egress_ceiling` is a single
        #    scalar, so the policy language cannot express "may read this source, but only in
        #    transformed form". `auditor` and `platform_operator` are exactly that shape - both
        #    carry `column_masks: {"*": redact}` and an INTERNAL ceiling over a RESTRICTED_INTERNAL
        #    source - and both therefore reach nothing here.
        #
        #    This is the SAME missing concept as the fourth cell state (a transformed disclosure,
        #    spine §9 O1b), found independently by both the finance and government estates and
        #    recorded as must-fix-before-Phase-6. A local fix - a second ceiling, or a rule that
        #    exempts a fully-masked projection - would fragment the vocabulary per pack, which is
        #    worse than the gap. So this refuses, and says why.
        ceiling = EGRESS_RANK[role.egress_ceiling]
        if EGRESS_RANK[str(description.egress_class)] > ceiling:
            allowed = sorted(n for n, rank in EGRESS_RANK.items() if rank <= ceiling)
            raise EgressCeilingExceeded(
                f"POLICY_EGRESS_CEILING: source {description.source_id!r} is "
                f"{description.egress_class} and role {role.name!r} may receive at most "
                f"{allowed}. Refused before the read: afterwards the rows have already left (§10)")

        # An aggregate-only role may not receive rows at all. Its own refusal, because the remedy
        # differs: "ask for an aggregate", not "ask a data owner".
        if role.aggregate_only and req.capability != "structural_read":
            raise EgressCeilingExceeded(
                f"role {role.name!r} is declared aggregate_only with minimum_group_size "
                f"{role.minimum_group_size}; a row read emits named rows and no rounding or "
                f"perturbation makes it an aggregate. `roles.yaml` says on_small_group: refuse")

        # 8. BUDGET. Not in the estate's list - it is the port's admission control (§10) - and it
        #    runs before the compile-down so a refused read costs nothing.
        effective = budget or self._default_budget
        effective.admit(Cost(rows=req.limit), source_id=source_id)

        # THE SINK SCREEN. An untrusted value in a control-flow position is a BUG IN THE CALLER,
        # not an operational condition, and it is worth reaching even when the read would have
        # been refused anyway.
        screen_sinks(req, self._taint)

        # 8 (estate) ROW SCOPE and 9 COLUMN PROJECTION, both in the compile-down.
        table, notes = self._compile(req, description, role, estate_purpose)
        entitlement = Entitlement(
            grant_id=req.principal.grant_id,
            roles=(role.name,),
            # The compiled table's digest IS the policy in force for this principal: it is derived
            # from the bundle version, the active role, the purpose, the projection AND the
            # principal's attributes, so two RMs with the same role and different `rm_code` get
            # different cache generations. Folding the attributes in here rather than adding a
            # field to `Entitlement` keeps that type's meaning - "everything that shapes the
            # withheld set" - exactly true.
            policy_bundle_version=f"{self._roles.bundle_version}+{table.digest[:16]}")
        return Admission(
            read_hash=req.read_hash, source_id=source_id, decisions=table,
            decisions_digest=table.digest, entitlement=entitlement, budget=effective,
            egress_class=str(description.egress_class), roles=(role.name,),
            attributes=binding.attributes, bundle_version=self._roles.bundle_version,
            notes=tuple(notes))

    def _active_role(self, binding: PrincipalBinding) -> Role:
        """Stage 1. Exactly one context, chosen by the caller, never inferred."""
        if binding.active_role is not None:
            return self._roles.require_role(binding.active_role)
        if len(binding.roles) == 1:
            return self._roles.require_role(binding.roles[0])
        raise PurposeNotPermitted(
            f"POLICY_NO_ACTIVE_CONTEXT: this principal holds {sorted(binding.roles)} and named no "
            f"active role. A request activates exactly ONE role context and the caller chooses it; "
            f"there is no implicit union, no best-matching role, and no fallback to a second role "
            f"when the first refuses. Inferring it from what would return the most data is "
            f"precisely the thing the rule forbids")

    # --- the compile-down -------------------------------------------------------------
    def _compile(self, req: CapabilityRequest, description: SourceDescription,
                 role: Role, estate_purpose: str) -> tuple[CompiledDecisionTable, list[str]]:
        source_id, entity = req.source_id, req.projection.entity
        notes: list[str] = []

        # STAGE 8 - ROW SCOPE. "EVERY object in a role's `allow` must appear in EITHER that role's
        # `row_filters` OR its `unscoped_objects` list. An object in neither... fails closed with
        # POLICY_ROW_SCOPE_UNDECLARED. There is no default scope, and 'the client will join
        # through a filtered parent' is not a policy."
        rules: list[RowRule] = []
        rule = role.row_rule_for(source_id, entity)
        if rule is None:
            if not role.is_unscoped(source_id, entity):
                raise RowRuleNotCompilable(
                    f"POLICY_ROW_SCOPE_UNDECLARED: role {role.name!r} allows "
                    f"{source_id}.{entity} and declares neither a row filter nor an entry in "
                    f"`unscoped_objects` for it. Fails closed: there is no default scope, and an "
                    f"object read with no declared scope is an object read with none")
            notes.append(
                f"{source_id}.{entity} is read WITHOUT a row scope, which role {role.name!r} "
                f"declares deliberately in `unscoped_objects`")
        elif not rule.compilable:
            raise RowRuleNotCompilable(
                f"POLICY_ROW_SCOPE_UNCOMPILABLE: role {role.name!r} scopes {source_id}.{entity} "
                f"with {rule.raw!r}, which this build's compiler cannot express - the estate's own "
                f"`row_scope_compilation` lists the two shapes that compile and calls a two-hop "
                f"scope out as one that does not. REFUSED, never dropped: 'a row rule that "
                f"silently does not run is the exact shape of a policy that permits everything "
                f"while appearing not to'. Unblocked by the §6 predicate grammar, or by a bank-"
                f"side scoping view that turns a two-hop scope into a one-hop one")
        else:
            if rule.subquery is not None and rule.subquery.schema not in (None, source_id):
                raise RowRuleNotCompilable(
                    f"POLICY_ROW_SCOPE_UNCOMPILABLE: role {role.name!r} scopes "
                    f"{source_id}.{entity} through {rule.subquery.qualified}, which is in another "
                    f"source. One request reaches one adapter and one adapter holds one source's "
                    f"credentials; resolving this needs the cross-source join to happen in the "
                    f"graph, not in a Core")

            # UNTRUSTED SCOPE COLUMN (spine D3; five independent reviews converged on this gap -
            # see `taint.require_trusted_scope_column`). Both "eq" and "in_subquery" compare
            # `rule.column` on the RAW ROW being read, so it is checked regardless of kind; a
            # subquery additionally reads `select_column`/`where_column` on ITS OWN table, which
            # this build never treats as `entity` because a subquery is free to name a different
            # table in the same source.
            require_trusted_scope_column(
                description, entity=entity, column=str(rule.column), rule_id=rule.rule_id,
                position="row_filters comparison column")
            if rule.subquery is not None:
                sub = rule.subquery
                require_trusted_scope_column(
                    description, entity=sub.table, column=sub.select_column,
                    rule_id=rule.rule_id, position="subquery select_column")
                if sub.where_column:
                    require_trusted_scope_column(
                        description, entity=sub.table, column=sub.where_column,
                        rule_id=rule.rule_id, position="subquery where_column")

            rules.append(rule)
            # A CONSTANT scope declaration, and deliberately no count. `existence_disclosure`:
            # "a response... carries a CONSTANT scope declaration... and NEVER a count of what was
            # excluded. A count is cardinality and cardinality is the leak."
            notes.append(f"this response is limited to your scope under {rule.rule_id}")

        # STAGE 9 - COLUMN PROJECTION.
        columns = [self._column_rule(source_id, entity, column, role, estate_purpose, description)
                   for column in req.projection.fields]

        table = CompiledDecisionTable(
            source_id=source_id, entity=entity, purpose=req.principal.purpose,
            estate_purpose=estate_purpose, roles=(role.name,), columns=tuple(columns),
            rules=tuple(rules), policy_id=f"{self._roles.estate}:roles:{role.name}",
            policy_version=self._roles.bundle_version)
        return table, notes

    def _column_rule(self, source_id: str, entity: str, column: str, role: Role,
                     estate_purpose: str, description: SourceDescription) -> ColumnRule:
        """One column under the ONE active role. Most restrictive outcome wins.

        `combination_semantics.within_one_context`: "Where two rules in one role cover one cell,
        the MOST RESTRICTIVE outcome wins: withheld > transformed > raw. Compared per cell, not
        per rule."
        """
        rid = f"{self._roles.estate}:roles:{role.name}"

        def denial(code: str) -> ColumnRule:
            return ColumnRule(column=column, withheld=True, policy_id=rid,
                              reason_code=self._roles.require_reason(code), appealable=True,
                              decided_by=role.name)

        # DENY WINS - over the allow list, over the mask, over everything.
        if role.denies_column(source_id, entity, column):
            return denial("POLICY_COLUMN_DENIED")

        # FREE TEXT is withheld from EVERY role by default, "regardless of any grant over the
        # table that contains it, and regardless of any sensitivity label beside it". One named
        # exception in this estate, and it is checked against the PURPOSE as well as the role.
        if self._roles.is_free_text(source_id, entity, column):
            if not self._free_text_permitted(role, estate_purpose):
                return denial("POLICY_FREE_TEXT_UNREVIEWED")

        # The MASK is checked before the role-level output constraints, because for the two roles
        # that carry `metadata_only` / `pointers_only` the estate declares `column_masks: {"*":
        # redact}` as the mechanism - and the mask's own declared reason code is the one a reader
        # can look up. The flags stay as a backstop for a role that carries one and no mask.
        mask = role.mask_for(source_id, entity, column)
        if mask is None and (role.metadata_only or role.pointers_only):
            return denial("POLICY_COLUMN_DENIED")
        if mask is not None:
            # Every mask in `roles.yaml` declares `cell_type: Withheld`. Honoured rather than
            # reinterpreted: a masked cell is a DENIAL carrying the mask's declared reason code,
            # not a partial disclosure. The fourth cell state a partial disclosure would need
            # (O1b) does not exist, and inventing it would put an uncitable value behind a pointer.
            return denial(self._roles.require_mask(mask).reason_code)

        # Disclosed. Whether verbatim or transformed is a fact about the COLUMN - the SOURCE
        # contract says it holds personal data - not about the role.
        sensitive = f"{source_id}.{entity}.{column}" in _sensitive_columns(description)
        return ColumnRule(column=column, withheld=False, decided_by=role.name,
                          transform=(f"redact_pii@v1(column={column})" if sensitive else None))

    def _free_text_permitted(self, role: Role, estate_purpose: str) -> bool:
        """`free_text_policy.exceptions` - one role, one purpose, in this estate.

        Read from the file rather than hard-coded, so adding a second exception is a YAML change.
        """
        for exception in self._free_text_exceptions:
            if (str(exception.get("role")) == role.name
                    and estate_purpose in {str(p) for p in (exception.get("purposes") or ())}):
                return True
        return False


def _sensitive_columns(description: SourceDescription) -> frozenset[str]:
    """Columns the SOURCE CONTRACT declares as personal data, as fully-qualified paths.

    Read from the description (which reads `sources.yaml`), never inferred from a value: inferring
    sensitivity from content is how a column full of national identifiers stays unredacted for the
    six months before one of them happens to match a regex.
    """
    return frozenset(
        f"{description.source_id}.{e.name}.{f.name}"
        for e in description.entities for f in e.fields if f.sensitive)

